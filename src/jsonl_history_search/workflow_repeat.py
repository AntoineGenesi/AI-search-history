from __future__ import annotations

import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable

from .agent_contract import estimate_tokens, reduction_ratio
from .clustering import session_key
from .masking import MaskContext
from .records import Record, compact_json, parse_record
from .sources import SourcePlan, enumerate_jsonl_files
from .tool_events import ToolEvent, normalize_command, scan_tool_events


SCHEMA_VERSION = 1
INLINE_CLUSTER_LIMIT = 12
INLINE_SUGGESTION_LIMIT = 20
SNIPPET_LIMIT = 220

USER_ASK_MARKERS = {
    "again",
    "automate",
    "can you automate",
    "every time",
    "manual",
    "make a skill",
    "pipeline",
    "repeat",
    "same process",
    "workflow",
    "write a script",
}
SUGGESTION_MARKERS = {
    "add a command",
    "automate",
    "could automate",
    "create a workflow",
    "make a skill",
    "should use",
    "use a tool",
    "write a script",
}
CORRECTION_MARKERS = {
    "failed",
    "missed",
    "not what",
    "over-reading",
    "partially_verified",
    "redo",
    "review failed",
    "too many tool calls",
    "wrong",
}
COMPLETION_MARKERS = {"committed", "merged", "passes", "report written", "review artifact", "verified"}
AUTOMATION_NOUNS_RE = re.compile(
    r"\b(?:skill|tool|script|command|workflow|automation)\b(?:\s+called|\s+named)?\s+[`$'\"]?([a-zA-Z][\w.-]{2,})[`'\"]?",
    re.I,
)


@dataclass(frozen=True)
class ProseRecord:
    record: Record
    text: str
    lower: str
    timestamp_utc: str | None
    sort_key: tuple[str, str, int]


def build_workflow_repeat_payload(
    *,
    source_plan: dict,
    plan: SourcePlan,
    default_tz,
    query: str = "",
    min_instances: int = 2,
    window_records: int = 8,
    cluster_limit: int = INLINE_CLUSTER_LIMIT,
    include_suggestions: bool = True,
    mask_context: MaskContext | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict:
    min_instances = max(1, min_instances)
    window_records = max(0, window_records)
    cluster_limit = max(1, cluster_limit)
    tool_scan = scan_tool_events(plan, default_tz=default_tz, mask_context=mask_context)
    prose_scan = scan_prose_records(plan, default_tz=default_tz, mask_context=mask_context)
    events = tool_scan["events"]
    records = prose_scan["records"]
    record_index = records_by_session(records)
    suggestions = find_automation_suggestions(records) if include_suggestions else []
    clusters, one_offs = build_clusters(
        events,
        records_by_session=record_index,
        suggestions=suggestions,
        min_instances=min_instances,
        window_records=window_records,
        now_utc=now_from_source_plan(source_plan),
    )
    clusters = sorted(clusters, key=lambda row: (-float(row["score"]), row["cluster_id"]))
    for rank, cluster in enumerate(clusters, 1):
        cluster["rank"] = rank
    suggestions = classify_suggestions(suggestions, events, clusters)
    for cluster in clusters:
        related = related_suggestions(cluster, suggestions)
        cluster["prior_suggestions"] = related[:5]
        cluster["actual_use_evidence"] = actual_use_evidence(cluster, related, events)[:5]
        cluster["use_classification"] = cluster_use_classification(cluster, related)
        if cluster["use_classification"] in {"suggested_not_used", "suggested_and_contradicted"}:
            cluster["score"] = round(float(cluster["score"]) + 4.0, 2)
            cluster["ranking_metrics"]["prior_unused_or_bad_suggestion"] = True
    clusters = sorted(clusters, key=lambda row: (-float(row["score"]), row["cluster_id"]))
    for rank, cluster in enumerate(clusters, 1):
        cluster["rank"] = rank
        cluster.pop("_match_terms", None)

    inline_clusters = clusters[:cluster_limit]
    inline_suggestions = suggestions[:INLINE_SUGGESTION_LIMIT]
    inline_bytes = len(json.dumps(inline_clusters + inline_suggestions + one_offs[:10], ensure_ascii=False).encode("utf-8"))
    searched_files = max(int(tool_scan["searched_files"]), int(prose_scan["searched_files"]))
    searched_bytes = max(int(tool_scan["searched_bytes"]), int(prose_scan["searched_bytes"]))
    status = "found" if clusters else "no_repeated_workflows"
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "query": query,
        "command_surface": {
            "workflow-repeat-finder": "Returns deterministic source-backed repeated workflow clusters from structured tool events plus bounded prose annotations for asks, suggestions, corrections, and uncertainty."
        },
        "signal_definitions": signal_definitions(),
        "workflow_clusters": inline_clusters,
        "automation_suggestions": inline_suggestions,
        "rejected_one_offs": one_offs[:20],
        "ai_handoff_packet": compact_ai_handoff(clusters, suggestions, one_offs),
        "context_budget": {
            "payload_bytes": inline_bytes,
            "estimated_tokens": estimate_tokens(inline_bytes),
            "raw_baseline_bytes": searched_bytes,
            "estimated_reduction_ratio": reduction_ratio(inline_bytes, searched_bytes),
            "report_recommended": len(clusters) > cluster_limit or len(suggestions) > INLINE_SUGGESTION_LIMIT,
            "report_written": report_output,
            "format": output_format,
        },
        "token_efficiency": {
            "cluster_count": len(clusters),
            "suggestion_count": len(suggestions),
            "rejected_one_off_count": len(one_offs),
            "structured_event_count": len(events),
            "inline_cluster_limit": cluster_limit,
            "inline_context_bytes": inline_bytes,
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
        },
        "scan_contract": {
            "execution_support": "Workflow clusters require structured events from tool_events.py; prose-only suggestions or keyword hits can annotate but never create executed-event claims.",
            "bounded_prose_join": f"Nearby user asks, suggestions, corrections, and completion signals are joined within {window_records} records in the same session.",
            "on_demand_search": "This command uses bounded root enumeration and scans on demand; no persistent index or embeddings are required for this deterministic first slice.",
            "indexing_guidance": "Use indexing or embeddings only after benchmark evidence shows lower token/tool cost without weakening source refs, event/prose separation, and uncertainty labels.",
            "private_data": "Raw private logs are not committed; fixtures should stay synthetic or sanitized.",
        },
        "warnings": sorted(set(tool_scan["warnings"] + prose_scan["warnings"] + source_plan.get("sources", {}).get("warnings", []))),
    }
    payload = {
        "workflow_repeat_finder": report,
        "source_plan": source_plan,
        "warnings": report["warnings"],
        "blind_spots": sorted(set(tool_scan["blind_spots"] + prose_scan["blind_spots"] + source_plan.get("sources", {}).get("blind_spots", []))),
    }
    if "masking" in tool_scan:
        payload["masking"] = tool_scan["masking"]
    elif "masking" in prose_scan:
        payload["masking"] = prose_scan["masking"]
    return payload


def scan_prose_records(plan: SourcePlan, *, default_tz, mask_context: MaskContext | None) -> dict:
    started = time.monotonic()
    records: list[ProseRecord] = []
    searched_files = 0
    searched_bytes = 0
    warnings = list(plan.warnings)
    stopped = False
    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = enum_warnings
        searched_files += 1
        searched_bytes += size
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"Could not open {path}: {exc}")
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
                text = record.text or ""
                if mask_context is not None:
                    text = mask_context.mask_text(text, source_path=record.path, source_index=record.line)
                stamp = timestamp_utc(record.timestamp)
                records.append(
                    ProseRecord(
                        record=record,
                        text=text,
                        lower=text.lower(),
                        timestamp_utc=stamp,
                        sort_key=(stamp or "9999-12-31T23:59:59+00:00", record.path, record.line),
                    )
                )
            if stopped:
                break
    records.sort(key=lambda row: row.sort_key)
    payload = {
        "records": records,
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(plan.blind_spots)),
    }
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload


def build_clusters(
    events: list[ToolEvent],
    *,
    records_by_session: dict[str, list[ProseRecord]],
    suggestions: list[dict],
    min_instances: int,
    window_records: int,
    now_utc: datetime | None,
) -> tuple[list[dict], list[dict]]:
    grouped: dict[tuple[str, str], list[ToolEvent]] = defaultdict(list)
    for event in events:
        key = cluster_key(event)
        if key:
            grouped[key].append(event)
    clusters: list[dict] = []
    one_offs: list[dict] = []
    for index, (key, key_events) in enumerate(sorted(grouped.items()), 1):
        deduped = dedupe_instances(key_events)
        if len(deduped) < min_instances:
            one_offs.append(one_off_row(key, deduped))
            continue
        cluster = cluster_payload(
            index=index,
            key=key,
            events=deduped,
            all_events=key_events,
            records_by_session=records_by_session,
            suggestions=suggestions,
            window_records=window_records,
            now_utc=now_utc,
        )
        clusters.append(cluster)
    return clusters, sorted(one_offs, key=lambda row: (-row["structured_event_count"], row["cluster_key"]))[:50]


def cluster_key(event: ToolEvent) -> tuple[str, str] | None:
    if event.event_type == "command_sequence" and event.sequence_id:
        return ("command_sequence", event.sequence_id)
    if event.event_type in {"command_run", "test_run", "git_event"} and event.command:
        return (event.event_type, command_family(event.command))
    if event.event_type in {"artifact_write", "review_event", "changed_path"}:
        family = first_path_family(event)
        return (event.event_type, family) if family else None
    if event.event_type in {"skill_file_read", "skill_helper_invocation"}:
        skill = skill_from_event(event)
        return ("skill_or_helper_usage", skill) if skill else (event.event_type, "unknown_skill")
    if event.event_type == "file_search" and event.command:
        return ("file_search", command_family(event.command))
    if event.event_type == "tool_call" and event.tool_name:
        lower_name = event.tool_name.lower()
        if any(token in lower_name for token in ("exec_command", "bash", "shell", "apply_patch")):
            return None
        return ("tool_call", lower_name)
    return None


def cluster_payload(
    *,
    index: int,
    key: tuple[str, str],
    events: list[ToolEvent],
    all_events: list[ToolEvent],
    records_by_session: dict[str, list[ProseRecord]],
    suggestions: list[dict],
    window_records: int,
    now_utc: datetime | None,
) -> dict:
    kind = workflow_kind_for(key, events)
    first_seen, last_seen = first_last_seen(events)
    recency_days = recency_days_for(last_seen, now_utc)
    sessions = sorted({str(event.source_ref.get("session") or "") for event in events if event.source_ref})
    projects = sorted({event.project or event.cwd or "" for event in events if event.project or event.cwd})
    nearby = nearby_signal_bundle(events, records_by_session, window_records=window_records)
    event_counts = Counter(event.event_type for event in all_events)
    command_counts = Counter(command_family(event.command or "") for event in all_events if event.command)
    path_counts = Counter(path_family(target.get("value", "")) for event in event_dicts(all_events) for target in event.get("targets", []))
    artifact_count = sum(event_counts[event_type] for event_type in ("artifact_write", "review_event", "changed_path"))
    frustration = len(nearby["corrections"])
    repeated_corrections = min(frustration, 5)
    feasibility = feasibility_for(kind, events)
    metrics = {
        "frequency": len(events),
        "recency_days": recency_days,
        "session_spread": len(sessions),
        "project_spread": len(projects),
        "frustration": frustration,
        "tool_call_cost": len(all_events),
        "elapsed_time_proxy_minutes": elapsed_minutes(first_seen, last_seen),
        "repeated_corrections": repeated_corrections,
        "artifact_count": artifact_count,
        "automation_feasibility": feasibility,
        "manual_repetition": manual_repetition_for(kind, events),
    }
    score = score_for(metrics, suggestions, key)
    confidence = confidence_for(events, nearby)
    reason_codes = reason_codes_for(metrics, kind, confidence)
    label = label_for(kind, key, events)
    evidence_against = evidence_against_for(kind, nearby, suggestions, key)
    uncertainty = uncertainty_for(kind, nearby, suggestions, key, confidence)
    return {
        "cluster_id": f"wf-{index:03d}",
        "label": label,
        "confidence": confidence,
        "rank": 0,
        "score": score,
        "workflow_kind": kind,
        "instance_count": len(events),
        "session_count": len(sessions),
        "project_count": len(projects),
        "first_seen_utc": first_seen.isoformat() if first_seen else None,
        "last_seen_utc": last_seen.isoformat() if last_seen else None,
        "recency_days": recency_days,
        "representative_instances": representative_instances(events, nearby, limit=5),
        "event_summary": {
            "counts_by_event_type": dict(sorted(event_counts.items())),
            "top_normalized_commands": counter_rows(command_counts),
            "top_path_families": counter_rows(path_counts),
            "nearby_signal_counts": {
                "user_asks": len(nearby["user_asks"]),
                "corrections": len(nearby["corrections"]),
                "completion_or_proof": len(nearby["completions"]),
            },
            "completion_or_proof_refs": [row["source_ref"] for row in nearby["completions"][:5]],
        },
        "prior_suggestions": [],
        "actual_use_evidence": [],
        "use_classification": "automation_candidate",
        "ranking_metrics": metrics,
        "reason_codes": reason_codes,
        "evidence_for": evidence_for_for(kind, metrics, nearby),
        "evidence_against": evidence_against,
        "uncertainty": uncertainty,
        "next_validation_queries": next_validation_queries(label, key, uncertainty),
        "_match_terms": match_terms_for(key, label, events),
    }


def representative_instances(events: list[ToolEvent], nearby: dict, *, limit: int) -> list[dict]:
    rows = []
    asks_by_ref = {(item["source_ref"]["path"], item["source_ref"]["line"]): item for item in nearby["user_asks"]}
    for event in events[:limit]:
        ref = event.source_ref
        ask_refs = [
            ask
            for ask in nearby["user_asks"]
            if ask["source_ref"].get("session") == ref.get("session")
        ][:2]
        rows.append(
            {
                "source_ref": ref,
                "timestamp_utc": event.timestamp_utc,
                "cwd": event.cwd,
                "project": event.project,
                "event_ids": [event.event_id] + event.related_event_ids,
                "event_type": event.event_type,
                "command": event.command,
                "targets": [target.to_dict() for target in event.targets][:5],
                "nearby_user_ask_refs": [ask["source_ref"] for ask in ask_refs],
                "nearby_user_asks": ask_refs,
                "payload_excerpt": event.payload_excerpt,
            }
        )
    return rows


def one_off_row(key: tuple[str, str], events: list[ToolEvent]) -> dict:
    event = events[0] if events else None
    return {
        "cluster_key": f"{key[0]}:{key[1]}",
        "reason": "below_min_instances_or_weak_repetition",
        "structured_event_count": len(events),
        "first_source_ref": event.source_ref if event else None,
        "event_type": event.event_type if event else None,
        "command": event.command if event else None,
        "targets": [target.to_dict() for target in event.targets][:3] if event else [],
    }


def find_automation_suggestions(records: list[ProseRecord]) -> list[dict]:
    suggestions: list[dict] = []
    for item in records:
        if not is_suggestion(item):
            continue
        terms = suggestion_terms(item.text)
        suggestions.append(
            {
                "suggestion_id": f"s{len(suggestions) + 1:03d}",
                "source_ref": source_ref(item.record),
                "timestamp_utc": item.timestamp_utc,
                "role": item.record.role,
                "classification": "unclassified",
                "suggestion_type": suggestion_type(item.lower),
                "suggested_terms": terms,
                "snippet": snippet(item.text),
                "evidence_basis": ["bounded_prose_suggestion"],
                "uncertainty": ["suggestion_is_prose_not_execution_evidence"],
            }
        )
    return suggestions


def classify_suggestions(suggestions: list[dict], events: list[ToolEvent], clusters: list[dict]) -> list[dict]:
    event_dict_rows = event_dicts(events)
    for suggestion in suggestions:
        terms = set(suggestion.get("suggested_terms") or [])
        later_events = [
            event
            for event in event_dict_rows
            if terms and event_after(event.get("timestamp_utc"), suggestion.get("timestamp_utc")) and terms_overlap(terms, event_text_terms(event))
        ]
        related_clusters = [cluster for cluster in clusters if terms_overlap(terms, set(cluster.get("_match_terms", [])))]
        contradicted = any(
            item
            for cluster in related_clusters
            for item in cluster.get("evidence_against", [])
            if "correction" in item.get("basis", "")
        )
        if contradicted:
            suggestion["classification"] = "contradicted"
            suggestion["actual_use_refs"] = [row["source_ref"] for row in later_events[:5]]
        elif later_events:
            suggestion["classification"] = "used"
            suggestion["actual_use_refs"] = [row["source_ref"] for row in later_events[:5]]
        elif related_clusters:
            suggestion["classification"] = "ignored"
            suggestion["actual_use_refs"] = []
        else:
            suggestion["classification"] = "never_triggered"
            suggestion["actual_use_refs"] = []
        suggestion["related_cluster_ids"] = [cluster["cluster_id"] for cluster in related_clusters[:5]]
    return suggestions


def related_suggestions(cluster: dict, suggestions: list[dict]) -> list[dict]:
    terms = set(cluster.get("_match_terms", []))
    related = [suggestion for suggestion in suggestions if terms_overlap(terms, set(suggestion.get("suggested_terms") or []))]
    if related:
        return suggestion_public_rows(related)
    if cluster.get("ranking_metrics", {}).get("frustration", 0) and suggestions:
        return suggestion_public_rows(suggestions[:2])
    return []


def actual_use_evidence(cluster: dict, suggestions: list[dict], events: list[ToolEvent]) -> list[dict]:
    terms = {term for suggestion in suggestions for term in suggestion.get("suggested_terms", [])}
    if not terms:
        return []
    rows = []
    for event in event_dicts(events):
        if event["event_type"] not in {"skill_file_read", "skill_helper_invocation", "command_run", "tool_call"}:
            continue
        if terms_overlap(terms, event_text_terms(event)):
            rows.append(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "source_ref": event["source_ref"],
                    "timestamp_utc": event.get("timestamp_utc"),
                    "command": event.get("command"),
                    "tool_name": event.get("tool_name"),
                    "basis": "structured_tool_event_matching_suggestion_term",
                }
            )
    return rows


def cluster_use_classification(cluster: dict, suggestions: list[dict]) -> str:
    if cluster["workflow_kind"] in {"skill_or_helper_usage"}:
        return "already_automated"
    classes = {suggestion.get("classification") for suggestion in suggestions}
    if "used" in classes:
        return "used_later"
    if "contradicted" in classes:
        return "suggested_and_contradicted"
    if classes & {"ignored", "never_triggered"}:
        return "suggested_not_used"
    if cluster.get("confidence") == "low":
        return "insufficient_evidence"
    return "automation_candidate"


def nearby_signal_bundle(events: list[ToolEvent], records_by_session: dict[str, list[ProseRecord]], *, window_records: int) -> dict:
    user_asks: list[dict] = []
    corrections: list[dict] = []
    completions: list[dict] = []
    seen: set[tuple[str, int, str]] = set()
    for event in events:
        ref = event.source_ref
        session = str(ref.get("session") or "")
        line = int(ref.get("line") or 0)
        for item in nearby_records(records_by_session.get(session, []), ref.get("path"), line, window_records):
            if has_any(item.lower, USER_ASK_MARKERS) and item.record.role.lower() in {"user", "user_message"}:
                append_unique(user_asks, context_row(item, "user_ask"), seen)
            if has_any(item.lower, CORRECTION_MARKERS):
                append_unique(corrections, context_row(item, "correction_or_friction"), seen)
            if has_any(item.lower, COMPLETION_MARKERS):
                append_unique(completions, context_row(item, "completion_or_proof"), seen)
    return {"user_asks": user_asks[:8], "corrections": corrections[:8], "completions": completions[:8]}


def nearby_records(records: list[ProseRecord], path: str | None, line: int, window_records: int) -> Iterable[ProseRecord]:
    if not records:
        return []
    candidates = [idx for idx, item in enumerate(records) if item.record.path == path and item.record.line == line]
    if not candidates:
        return []
    index = candidates[0]
    start = max(0, index - window_records)
    end = min(len(records), index + window_records + 1)
    return records[start:end]


def records_by_session(records: list[ProseRecord]) -> dict[str, list[ProseRecord]]:
    grouped: dict[str, list[ProseRecord]] = defaultdict(list)
    for item in records:
        grouped[session_key(item.record.path)].append(item)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item.record.path, item.record.line))
    return grouped


def signal_definitions() -> dict:
    return {
        "structured_execution": "Repeated workflow clusters are created only from `scan_tool_events` structured events.",
        "command_sequences": "Adjacent command runs reuse jsh-014 deterministic `sequence_id` fingerprints.",
        "path_families": "Touched files and artifact writes are grouped by stable families such as tasks/*, reviews/*, docs/*, src/*, tests/*, scripts/*, and reports/*.",
        "prose_annotations": "User asks, prior AI suggestions, corrections, and completion/proof text are bounded context signals only.",
        "suggestion_use": "Suggested tools/skills/scripts are classified by later structured use, related repeated work without use, or correction evidence.",
        "one_off_guard": "Clusters below `--min-instances` stay in `rejected_one_offs` even if prose looks important.",
    }


def compact_ai_handoff(clusters: list[dict], suggestions: list[dict], one_offs: list[dict]) -> dict:
    return {
        "top_workflow_clusters": [
            {
                "cluster_id": cluster["cluster_id"],
                "label": cluster["label"],
                "workflow_kind": cluster["workflow_kind"],
                "score": cluster["score"],
                "confidence": cluster["confidence"],
                "instance_count": cluster["instance_count"],
                "first_source_ref": (cluster.get("representative_instances") or [{}])[0].get("source_ref"),
                "candidate_automation": candidate_automation_for(cluster),
                "evidence_for": cluster.get("evidence_for", [])[:3],
                "evidence_against": cluster.get("evidence_against", [])[:2],
                "next_validation_query": (cluster.get("next_validation_queries") or [None])[0],
            }
            for cluster in clusters[:5]
        ],
        "automation_suggestions": suggestion_public_rows(suggestions[:5]),
        "rejected_one_off_count": len(one_offs),
    }


def candidate_automation_for(cluster: dict) -> str:
    kind = cluster.get("workflow_kind")
    label = cluster.get("label")
    if kind == "verification_loop":
        return f"Create a reusable verification command for {label}."
    if kind == "task_delivery_loop":
        return f"Create or refine a task-delivery helper/skill for {label}."
    if kind == "report_generation":
        return f"Wrap repeated report generation for {label} in a script or command."
    if kind == "review_merge_gate":
        return f"Automate review artifact collection and merge-gate checks for {label}."
    if kind == "search_retrieval_loop":
        return f"Add a retrieval shortcut for the repeated search pattern {label}."
    return f"Validate whether {label} can become a script, command, or skill."


def event_dicts(events: Iterable[ToolEvent]) -> list[dict]:
    return [event.to_dict() for event in events]


def dedupe_instances(events: list[ToolEvent]) -> list[ToolEvent]:
    seen: set[tuple[str, int, str]] = set()
    rows: list[ToolEvent] = []
    for event in sorted(events, key=lambda item: (item.timestamp_utc or "9999", item.source_ref.get("path") or "", item.source_ref.get("line") or 0, item.event_id)):
        key = (str(event.source_ref.get("session") or event.source_ref.get("path") or ""), int(event.source_ref.get("line") or 0), event.event_type)
        if key in seen:
            continue
        seen.add(key)
        rows.append(event)
    return rows


def workflow_kind_for(key: tuple[str, str], events: list[ToolEvent]) -> str:
    key_type, value = key
    commands = " ".join(event.command or "" for event in events).lower()
    paths = " ".join(target.value for event in events for target in event.targets).lower()
    if key_type in {"test_run"} or "pytest" in commands or "validate-install" in commands or "diff --check" in commands:
        return "verification_loop"
    if "tasks/" in paths or "task" in value:
        return "task_delivery_loop"
    if key_type in {"review_event"} or "reviews/" in paths:
        return "review_merge_gate"
    if key_type in {"artifact_write"} or "report" in paths or "--report-output" in commands:
        return "report_generation"
    if key_type in {"skill_or_helper_usage"}:
        return "skill_or_helper_usage"
    if key_type in {"file_search"} or commands.startswith("rg ") or commands.startswith("grep "):
        return "search_retrieval_loop"
    if key_type in {"git_event"} or commands.startswith("git "):
        return "git_forensics"
    if any(token in commands for token in ("ffmpeg", "kdenlive", "whisper", "tts", "rvc")):
        return "video/media_pipeline"
    if key_type == "command_sequence":
        return "unknown_repeated_tool_chain"
    return "unknown_repeated_tool_chain"


def command_family(command: str) -> str:
    normalized = normalize_command(command)
    parts = normalized.split()
    if not parts:
        return "unknown_command"
    while parts and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", parts[0]):
        parts = parts[1:]
    if not parts:
        return "environment_assignment"
    first = PurePosixPath(parts[0]).name
    if first == "python3" and "-m" in parts:
        idx = parts.index("-m")
        module = parts[idx + 1] if idx + 1 < len(parts) else "python_module"
        if "pytest" in module or "pytest" in parts:
            return "python3 -m pytest"
        if module.startswith("jsonl_history_search"):
            command_name = next((part for part in parts[idx + 2 :] if not part.startswith("-")), module)
            return f"python3 -m {module} {command_name}"
        return f"python3 -m {module}"
    if first in {"pytest", "rg", "grep", "find"}:
        return first
    if first == "git" and len(parts) > 1:
        return f"git {parts[1]}"
    if first in {"sed", "cat", "nl", "head", "tail"}:
        return first
    return first


def first_path_family(event: ToolEvent) -> str | None:
    values = [target.value for target in event.targets] + event.output_paths
    for value in values:
        family = path_family(value)
        if family:
            return family
    return None


def path_family(path: str) -> str:
    path = path.strip()
    if not path:
        return ""
    normalized = path.replace("\\", "/")
    anchored = normalized.lstrip("/")
    for prefix in ("tasks/", "reviews/", "codex/state/reviews/", "docs/", "src/", "tests/", "scripts/", "benchmarks/", "reports/"):
        if prefix in anchored:
            return prefix.rstrip("/") + "/*"
    name = PurePosixPath(anchored).name
    if name.endswith(".md"):
        return "*.md"
    if name.endswith(".py"):
        return "*.py"
    if "." in name:
        return f"*{PurePosixPath(name).suffix}"
    return anchored.split("/")[0] + "/*" if "/" in anchored else anchored


def skill_from_event(event: ToolEvent) -> str | None:
    values = [target.value for target in event.targets]
    text = " ".join(values + [event.command or "", event.payload_excerpt or ""])
    match = re.search(r"(?:^|/)\.codex/skills/([^/]+)/|(?:^|/)skills/([^/]+)/", text)
    if match:
        return (match.group(1) or match.group(2) or "").lower()
    if values and values[0].lower().endswith("skill.md"):
        parts = PurePosixPath(values[0]).parts
        return parts[-2].lower() if len(parts) >= 2 else "unknown_skill"
    return None


def first_last_seen(events: list[ToolEvent]) -> tuple[datetime | None, datetime | None]:
    stamps = [parse_utc(event.timestamp_utc) for event in events if event.timestamp_utc]
    stamps = [stamp for stamp in stamps if stamp is not None]
    return (min(stamps), max(stamps)) if stamps else (None, None)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def now_from_source_plan(source_plan: dict) -> datetime | None:
    return parse_utc(str(source_plan.get("now") or "")) if source_plan.get("now") else None


def timestamp_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def recency_days_for(last_seen: datetime | None, now_utc: datetime | None) -> int | None:
    if not last_seen or not now_utc:
        return None
    return max(0, (now_utc - last_seen).days)


def elapsed_minutes(first_seen: datetime | None, last_seen: datetime | None) -> int | None:
    if not first_seen or not last_seen:
        return None
    return max(0, math.ceil((last_seen - first_seen).total_seconds() / 60))


def feasibility_for(kind: str, events: list[ToolEvent]) -> str:
    if kind in {"verification_loop", "search_retrieval_loop", "git_forensics"}:
        return "high"
    if kind in {"report_generation", "review_merge_gate", "task_delivery_loop", "skill_or_helper_usage"}:
        return "medium"
    if any(event.event_type == "command_sequence" for event in events):
        return "medium"
    return "low"


def manual_repetition_for(kind: str, events: list[ToolEvent]) -> bool:
    return kind != "skill_or_helper_usage" and not any(event.event_type == "skill_helper_invocation" for event in events)


def score_for(metrics: dict, suggestions: list[dict], key: tuple[str, str]) -> float:
    recency = metrics.get("recency_days")
    recency_score = 8 if recency is None else max(0, 8 - min(8, recency // 7))
    feasibility_score = {"high": 8, "medium": 5, "low": 1}.get(metrics["automation_feasibility"], 0)
    score = (
        metrics["frequency"] * 8
        + metrics["session_spread"] * 5
        + metrics["project_spread"] * 2
        + min(metrics["tool_call_cost"], 12)
        + metrics["frustration"] * 5
        + metrics["artifact_count"] * 2
        + feasibility_score
        + recency_score
    )
    if metrics["manual_repetition"]:
        score += 5
    terms = {key[1], *key[1].split()}
    if any(terms_overlap(terms, set(suggestion.get("suggested_terms") or [])) for suggestion in suggestions):
        score += 3
    return round(float(score), 2)


def confidence_for(events: list[ToolEvent], nearby: dict) -> str:
    sessions = {event.source_ref.get("session") for event in events}
    high_events = [event for event in events if event.confidence == "high"]
    if len(events) >= 2 and len(high_events) >= 2 and sessions:
        return "high"
    if len(events) >= 2 and (nearby["user_asks"] or nearby["corrections"]):
        return "medium"
    return "low"


def reason_codes_for(metrics: dict, kind: str, confidence: str) -> list[str]:
    reasons = [f"kind:{kind}", f"confidence:{confidence}"]
    if metrics["frequency"] >= 2:
        reasons.append("repeated_structured_events")
    if metrics["session_spread"] >= 2:
        reasons.append("cross_session_repeat")
    if metrics["frustration"]:
        reasons.append("nearby_correction_or_friction")
    if metrics["artifact_count"]:
        reasons.append("repeated_artifact_or_path_work")
    if metrics["manual_repetition"]:
        reasons.append("manual_repetition_without_helper_evidence")
    if metrics["automation_feasibility"] == "high":
        reasons.append("high_feasibility_command_pattern")
    return reasons


def label_for(kind: str, key: tuple[str, str], events: list[ToolEvent]) -> str:
    if key[0] == "command_sequence":
        sample = events[0].payload_excerpt or events[0].command or key[1]
        return f"repeated command chain: {sample}"
    if key[0] in {"command_run", "test_run", "git_event", "file_search"}:
        return f"repeated {command_family(events[0].command or key[1])}"
    if key[0] in {"artifact_write", "review_event", "changed_path"}:
        return f"repeated {key[1]} work"
    if key[0] == "skill_or_helper_usage":
        return f"repeated {key[1]} skill/helper use"
    return f"{kind}: {key[1]}"


def evidence_for_for(kind: str, metrics: dict, nearby: dict) -> list[dict]:
    evidence = [
        {"basis": "structured_repeat", "detail": f"{metrics['frequency']} structured instances in {metrics['session_spread']} session(s)."},
        {"basis": "automation_feasibility", "detail": f"{metrics['automation_feasibility']} feasibility for {kind}."},
    ]
    if nearby["user_asks"]:
        evidence.append({"basis": "nearby_user_asks", "detail": f"{len(nearby['user_asks'])} nearby repeated-work ask(s)."})
    if metrics["frustration"]:
        evidence.append({"basis": "friction", "detail": f"{metrics['frustration']} nearby correction/friction signal(s)."})
    if nearby["completions"]:
        evidence.append({"basis": "completion_or_proof", "detail": f"{len(nearby['completions'])} nearby completion/proof signal(s)."})
    return evidence


def evidence_against_for(kind: str, nearby: dict, suggestions: list[dict], key: tuple[str, str]) -> list[dict]:
    evidence = []
    if not nearby["user_asks"]:
        evidence.append({"basis": "no_nearby_user_ask", "detail": "No repeated-work user ask was found near the structured events."})
    for row in nearby["corrections"][:3]:
        evidence.append({"basis": "nearby_correction", "detail": row["snippet"], "source_ref": row["source_ref"]})
    if kind == "unknown_repeated_tool_chain":
        evidence.append({"basis": "ambiguous_kind", "detail": "The repeated structured events do not map cleanly to a known workflow type."})
    terms = {key[1], *key[1].split()}
    if suggestions and not any(terms_overlap(terms, set(suggestion.get("suggested_terms") or [])) for suggestion in suggestions):
        evidence.append({"basis": "no_matching_prior_suggestion", "detail": "Automation suggestions were found, but none clearly match this cluster."})
    return evidence


def uncertainty_for(kind: str, nearby: dict, suggestions: list[dict], key: tuple[str, str], confidence: str) -> list[str]:
    uncertainty = []
    if confidence != "high":
        uncertainty.append("structured_support_is_repeated_but_context_is_limited")
    if kind == "unknown_repeated_tool_chain":
        uncertainty.append("workflow_kind_ambiguous_from_structured_events")
    if not nearby["user_asks"]:
        uncertainty.append("no_nearby_user_repeat_ask_found")
    terms = {key[1], *key[1].split()}
    if suggestions and not any(terms_overlap(terms, set(suggestion.get("suggested_terms") or [])) for suggestion in suggestions):
        uncertainty.append("prior_suggestions_do_not_clearly_match_cluster")
    return uncertainty


def next_validation_queries(label: str, key: tuple[str, str], uncertainty: list[str]) -> list[str]:
    queries = [f"{label} corrections review failed redo", f"{key[1]} automate script skill"]
    if uncertainty:
        queries.append(f"{key[1]} verified merged report")
    return queries


def match_terms_for(key: tuple[str, str], label: str, events: list[ToolEvent]) -> list[str]:
    terms = set(tokenize(f"{key[0]} {key[1]} {label}"))
    for event in events[:5]:
        terms.update(tokenize(event.command or ""))
        terms.update(tokenize(event.tool_name or ""))
        for target in event.targets:
            terms.update(tokenize(path_family(target.value)))
            terms.update(tokenize(target.value))
    return sorted(term for term in terms if len(term) > 2)


def source_ref(record: Record) -> dict:
    return {"path": record.path, "line": record.line, "session": session_key(record.path)}


def context_row(item: ProseRecord, classification: str) -> dict:
    return {
        "classification": classification,
        "source_ref": source_ref(item.record),
        "timestamp_utc": item.timestamp_utc,
        "role": item.record.role,
        "snippet": snippet(item.text),
    }


def append_unique(rows: list[dict], row: dict, seen: set[tuple[str, int, str]]) -> None:
    ref = row["source_ref"]
    key = (str(ref.get("path") or ""), int(ref.get("line") or 0), row["classification"])
    if key in seen:
        return
    seen.add(key)
    rows.append(row)


def is_suggestion(item: ProseRecord) -> bool:
    if item.record.role.lower() not in {"assistant", "assistant_message", "agent_message"}:
        return False
    return has_any(item.lower, SUGGESTION_MARKERS)


def suggestion_terms(text: str) -> list[str]:
    lower = text.lower()
    terms = set(tokenize(lower))
    terms.update(match.group(1).lower().strip(".") for match in AUTOMATION_NOUNS_RE.finditer(text))
    terms.update(re.findall(r"\$([a-zA-Z][\w.-]+)", text))
    terms.update(re.findall(r"`([a-zA-Z][\w.-]+)`", text))
    for phrase in ("skill-audit", "workflow-repeat-finder", "jsonl-history-search", "validate-install"):
        if phrase in lower:
            terms.add(phrase)
    return sorted(term for term in terms if len(term) > 2 and term not in STOP_TERMS)


def suggestion_type(lower: str) -> str:
    if "skill" in lower:
        return "skill"
    if "script" in lower:
        return "script"
    if "command" in lower:
        return "command"
    if "tool" in lower:
        return "tool"
    return "automation"


def suggestion_public_rows(suggestions: list[dict]) -> list[dict]:
    return [
        {
            key: value
            for key, value in suggestion.items()
            if key in {"suggestion_id", "source_ref", "timestamp_utc", "role", "classification", "suggestion_type", "suggested_terms", "snippet", "evidence_basis", "uncertainty", "actual_use_refs", "related_cluster_ids"}
        }
        for suggestion in suggestions
    ]


def event_text_terms(event: dict) -> set[str]:
    text = " ".join(
        [
            str(event.get("event_type") or ""),
            str(event.get("tool_name") or ""),
            str(event.get("command") or ""),
            compact_json(event.get("targets") or []),
            str(event.get("payload_excerpt") or ""),
        ]
    )
    return set(tokenize(text))


def terms_overlap(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    expanded_left = set(left)
    expanded_right = set(right)
    for term in list(left):
        expanded_left.update(term.split("-"))
        expanded_left.update(term.split("_"))
    for term in list(right):
        expanded_right.update(term.split("-"))
        expanded_right.update(term.split("_"))
    return bool((expanded_left - STOP_TERMS) & (expanded_right - STOP_TERMS))


def event_after(event_stamp: str | None, suggestion_stamp: str | None) -> bool:
    if not suggestion_stamp:
        return True
    if not event_stamp:
        return False
    event_time = parse_utc(event_stamp)
    suggestion_time = parse_utc(suggestion_stamp)
    if not event_time or not suggestion_time:
        return False
    return event_time >= suggestion_time


def has_any(lower: str, markers: Iterable[str]) -> bool:
    return any(marker in lower for marker in markers)


def snippet(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) <= SNIPPET_LIMIT:
        return text
    return text[: SNIPPET_LIMIT - 3] + "..."


def counter_rows(counter: Counter) -> list[dict]:
    return [{"value": value, "count": count} for value, count in counter.most_common(8) if value]


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[a-zA-Z][\w.-]{2,}", text)]


STOP_TERMS = {
    "and",
    "are",
    "but",
    "can",
    "cmd",
    "command",
    "could",
    "for",
    "from",
    "later",
    "make",
    "now",
    "script",
    "should",
    "skill",
    "the",
    "this",
    "tool",
    "use",
    "workflow",
    "write",
}
