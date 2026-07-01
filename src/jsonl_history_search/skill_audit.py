from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .agent_contract import estimate_tokens, reduction_ratio
from .masking import MaskContext
from .adapters import AdapterReadError, iter_records_for_file
from .records import Record
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path


INLINE_INSTANCE_LIMIT = 8
INLINE_MENTION_LIMIT = 12
SNIPPET_LIMIT = 220

CORRECTION_WORDS = {
    "actually",
    "correction",
    "failed",
    "failure",
    "missed",
    "not what",
    "partially_verified",
    "redo",
    "retry",
    "review failed",
    "wrong",
}
ACCEPTANCE_WORDS = {"accepted", "complete", "completed", "merged", "passes", "verified"}
ARTIFACT_WORDS = {"artifact", "committed", "docs/", "review artifact", "scratch", "task progress"}
SUGGESTION_WORDS = {"could use", "install", "mention", "roadmap", "should use", "suggest", "suggested"}
AGENT_ACTION_ROLES = {
    "assistant",
    "assistant_message",
    "agent_message",
    "function_call",
    "function_call_output",
    "tool",
    "tool_call",
    "tool_output",
}


@dataclass(frozen=True)
class AuditRecord:
    record: Record
    text: str
    timestamp_utc: str | None
    sort_key: tuple[str, str, int]


def build_skill_audit_payload(
    *,
    skill: str,
    source_plan: dict,
    plan: SourcePlan,
    default_tz,
    pre_window: int = 3,
    post_window: int = 6,
    correction_window: int = 12,
    mask_context: MaskContext | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict:
    scan = scan_records(plan, default_tz=default_tz, mask_context=mask_context)
    records = scan["records"]
    usage_instances, used_indices = build_usage_instances(
        skill,
        records,
        pre_window=max(0, pre_window),
        post_window=max(0, post_window),
        correction_window=max(0, correction_window),
    )
    non_use_mentions = build_non_use_mentions(skill, records, used_indices, used_refs_for(usage_instances))
    workflow_summary = workflow_classification_summary(usage_instances, non_use_mentions)
    status = "found" if usage_instances else "mentions_only" if non_use_mentions else "no_evidence"
    instance_dicts = [instance for instance in usage_instances]
    inline_context_bytes = len(str(instance_dicts[:INLINE_INSTANCE_LIMIT] + non_use_mentions[:INLINE_MENTION_LIMIT]).encode("utf-8"))
    raw_baseline_bytes = int(scan["searched_bytes"])
    payload = {
        "skill_audit": {
            "schema_version": 1,
            "status": status,
            "skill": skill,
            "command_surface": {
                "skill-audit": "Returns deterministic source-referenced skill usage evidence, pre/post windows, correction/outcome labels, non-use mentions, and a compact AI handoff packet."
            },
            "signal_definitions": signal_definitions(),
            "usage_instances": instance_dicts[:INLINE_INSTANCE_LIMIT],
            "non_use_mentions": non_use_mentions[:INLINE_MENTION_LIMIT],
            "workflow_classification_summary": workflow_summary,
            "ai_handoff_packet": compact_ai_handoff(skill, instance_dicts, non_use_mentions),
            "context_budget": {
                "payload_bytes": inline_context_bytes,
                "estimated_tokens": estimate_tokens(inline_context_bytes),
                "raw_baseline_bytes": raw_baseline_bytes,
                "estimated_reduction_ratio": reduction_ratio(inline_context_bytes, raw_baseline_bytes),
                "report_recommended": len(instance_dicts) > INLINE_INSTANCE_LIMIT or len(non_use_mentions) > INLINE_MENTION_LIMIT,
                "report_written": report_output,
                "format": output_format,
            },
            "token_efficiency": {
                "usage_instance_count": len(instance_dicts),
                "non_use_mention_count": len(non_use_mentions),
                "workflow_classification_counts": workflow_summary["classification_counts"],
                "inline_instance_limit": INLINE_INSTANCE_LIMIT,
                "inline_context_bytes": inline_context_bytes,
                "searched_files": scan["searched_files"],
                "searched_bytes": scan["searched_bytes"],
            },
            "scan_contract": {
                "ordering": "parsed timestamp ascending in UTC, then path and line; missing timestamps sort last",
                "pre_window_records": max(0, pre_window),
                "post_window_records": max(0, post_window),
                "correction_window_records": max(0, correction_window),
                "actual_use_rule": "keyword mentions alone are never actual use; actual use requires a skill-file/helper signal or an explicit agent use statement adjacent to workflow behavior",
                "deep_audit_classifications": [
                    "properly_applied",
                    "partially_applied",
                    "missed_trigger",
                    "misapplied",
                    "skill_read_without_required_reference_followthrough",
                    "not_enough_evidence",
                ],
            },
            "warnings": sorted(set(scan["warnings"] + source_plan.get("sources", {}).get("warnings", []))),
        },
        "source_plan": source_plan,
        "warnings": sorted(set(scan["warnings"] + source_plan.get("sources", {}).get("warnings", []))),
        "blind_spots": sorted(set(scan["blind_spots"] + source_plan.get("sources", {}).get("blind_spots", []))),
    }
    if "masking" in scan:
        payload["masking"] = scan["masking"]
    return payload


def scan_records(plan: SourcePlan, *, default_tz, mask_context: MaskContext | None) -> dict:
    started = time.monotonic()
    records: list[AuditRecord] = []
    searched_files = 0
    searched_bytes = 0
    warnings = list(plan.warnings)
    stopped = False
    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = enum_warnings
        searched_files += 1
        searched_bytes += size
        source_kind = source_kind_for_path(path, plan)
        try:
            for record in iter_records_for_file(path, source_kind, default_tz):
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                copied_text = (
                    mask_context.mask_text(record.text, source_path=record.path, source_index=record.line)
                    if mask_context is not None
                    else record.text
                )
                stamp = timestamp_utc(record.timestamp)
                records.append(
                    AuditRecord(
                        record=record,
                        text=copied_text,
                        timestamp_utc=stamp,
                        sort_key=(stamp or "9999-12-31T23:59:59+00:00", record.path, record.line),
                    )
                )
        except AdapterReadError as exc:
            warnings.append(str(exc))
            continue
        if stopped:
            break
    records.sort(key=lambda item: item.sort_key)
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


def build_usage_instances(
    skill: str,
    records: list[AuditRecord],
    *,
    pre_window: int,
    post_window: int,
    correction_window: int,
) -> tuple[list[dict], set[int]]:
    instances: list[dict] = []
    used_indices: set[int] = set()
    last_instance_at = -999999
    for index, item in enumerate(records):
        signals = actual_use_signals(skill, item, records[index + 1 : index + 1 + post_window])
        if not signals:
            continue
        if index - last_instance_at <= max(1, post_window):
            instances[-1]["skill_evidence"].extend(signals)
            instances[-1]["signal_types"] = sorted(set(instances[-1]["signal_types"] + [signal["signal_type"] for signal in signals]))
            instances[-1]["confidence"] = confidence_for(instances[-1]["signal_types"])
            used_indices.add(index)
            continue
        pre = window_records(records[max(0, index - pre_window) : index], exclude_empty=True)
        post = window_records(records[index + 1 : index + 1 + post_window], exclude_empty=True)
        correction_records = records[index + 1 : index + 1 + correction_window]
        corrections = [context_row(row) for row in correction_records if is_correction(row)]
        labels = outcome_labels(item, post, corrections)
        instance = {
            "usage_id": f"u{len(instances) + 1:03d}",
            "confidence": confidence_for([signal["signal_type"] for signal in signals]),
            "signal_types": sorted({signal["signal_type"] for signal in signals}),
            "trigger": trigger_for(records, index),
            "skill_evidence": signals,
            "pre_window": pre,
            "post_window": post,
            "user_corrections": corrections,
            "outcome_labels": labels,
            "improvement_opportunities": improvement_opportunities(labels, corrections),
            "uncertainty": uncertainty_for(signals, corrections),
            "next_read_refs": next_read_refs(pre, signals, post, corrections),
        }
        instance["workflow_classification"] = classify_usage_workflow(instance)
        instances.append(instance)
        used_indices.add(index)
        last_instance_at = index
    return instances, used_indices


def build_non_use_mentions(skill: str, records: list[AuditRecord], used_indices: set[int], used_refs: set[tuple[str, int]]) -> list[dict]:
    mentions: list[dict] = []
    for index, item in enumerate(records):
        ref_key = (item.record.path, item.record.line)
        if index in used_indices or ref_key in used_refs or not mentions_skill(skill, item.text):
            continue
        signals = mention_only_signals(skill, item)
        mentions.append(
            {
                "mention_id": f"m{len(mentions) + 1:03d}",
                "classification": classify_non_use_mention(skill, item),
                "reason": "mention_or_suggestion_without_actual_use_signal",
                "signal_types": signals,
                **context_row(item),
            }
        )
    return mentions


def used_refs_for(instances: list[dict]) -> set[tuple[str, int]]:
    refs: set[tuple[str, int]] = set()
    for instance in instances:
        groups = [
            [instance.get("trigger")] if instance.get("trigger") else [],
            instance.get("skill_evidence", []),
            instance.get("pre_window", []),
            instance.get("post_window", []),
            instance.get("user_corrections", []),
        ]
        for group in groups:
            for item in group or []:
                ref = item.get("source_ref") if isinstance(item, dict) else None
                if ref:
                    refs.add((str(ref.get("path") or ""), int(ref.get("line") or 0)))
    return refs


def actual_use_signals(skill: str, item: AuditRecord, following: list[AuditRecord]) -> list[dict]:
    text = item.text
    lower = text.lower()
    slug = skill_slug(skill)
    signals: list[dict] = []
    if mentions_skill(skill, text) and is_agent_action_record(item) and looks_like_skill_file_use(skill, item):
        signals.append(signal(item, "skill_file_read", "matching skill name and SKILL.md path/content"))
    if mentions_skill(skill, text) and is_agent_action_record(item) and ("/scripts/" in lower or "\\scripts\\" in lower or f"{slug}/scripts" in lower):
        signals.append(signal(item, "skill_helper_invocation", "matching skill helper or script path"))
    if (
        item.record.role.lower() in {"assistant", "agent_message", "assistant_message"}
        and mentions_skill(skill, text)
        and re.search(r"\b(use|using|used|invoke|trigger|triggered)\b.{0,80}\bskill\b", lower)
        and workflow_behavior_nearby(following)
    ):
        signals.append(signal(item, "explicit_agent_use_statement", "agent stated skill use with adjacent workflow behavior"))
    return dedupe_signals(signals)


def classify_usage_workflow(instance: dict) -> str:
    signal_types = set(instance.get("signal_types") or [])
    outcome_labels_set = set(instance.get("outcome_labels") or [])
    if "needed_correction" in outcome_labels_set or "over_applied_skill" in outcome_labels_set:
        return "misapplied"
    if "skill_file_read" in signal_types and _missing_required_reference_followthrough(instance):
        return "skill_read_without_required_reference_followthrough"
    if "skill_file_read" in signal_types or "skill_helper_invocation" in signal_types:
        if "completed_smoothly" in outcome_labels_set or "produced_useful_durable_artifact" in outcome_labels_set:
            return "properly_applied"
        return "partially_applied"
    if "explicit_agent_use_statement" in signal_types:
        return "partially_applied"
    return "not_enough_evidence"


def _missing_required_reference_followthrough(instance: dict) -> bool:
    evidence_text = " ".join(str(row.get("snippet") or "") for row in instance.get("skill_evidence", [])).lower()
    if not any(marker in evidence_text for marker in ("references/", "reference file", "referenced file")):
        return False
    followthrough_text = " ".join(
        str(row.get("snippet") or "")
        for group in (instance.get("skill_evidence", []), instance.get("post_window", []))
        for row in group
    ).lower()
    followthrough_markers = (
        "opened references/",
        "read references/",
        "reading references/",
        "loaded references/",
        "sed -n",
        "cat references/",
    )
    return not any(marker in followthrough_text for marker in followthrough_markers)


def classify_non_use_mention(skill: str, item: AuditRecord) -> str:
    lower = item.text.lower()
    if any(marker in lower for marker in ("maybe", "later", "should use")):
        return "non_use_mention"
    if item.record.role.lower() in {"user", "user_message"} and (
        re.search(rf"\${re.escape(skill_slug(skill))}\b", lower)
        or re.search(r"\b(use|invoke|trigger|run)\b.{0,80}\b(skill|audit)\b", lower)
    ):
        return "missed_trigger"
    return "non_use_mention"


def workflow_classification_summary(usage_instances: list[dict], non_use_mentions: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for instance in usage_instances:
        label = str(instance.get("workflow_classification") or "not_enough_evidence")
        counts[label] = counts.get(label, 0) + 1
    for mention in non_use_mentions:
        label = str(mention.get("classification") or "non_use_mention")
        counts[label] = counts.get(label, 0) + 1
    return {
        "classification_counts": dict(sorted(counts.items())),
        "classification_contract": (
            "Skill audit distinguishes actual SKILL.md/helper use from mentions, missed triggers, "
            "misapplication, and missing required referenced-file follow-through."
        ),
    }


def is_agent_action_record(item: AuditRecord) -> bool:
    return item.record.role.lower() in AGENT_ACTION_ROLES


def looks_like_skill_file_use(skill: str, item: AuditRecord) -> bool:
    lower_text = item.text.lower()
    if "skill.md" not in lower_text:
        return False
    if item.record.role.lower() in {"function_call", "tool_call"}:
        return True
    slug = skill_slug(skill)
    path_patterns = (
        f"/{slug}/skill.md",
        f"\\{slug}\\skill.md",
        f"/{slug.lower()}/skill.md",
        f"\\{slug.lower()}\\skill.md",
    )
    has_matching_path = any(pattern in lower_text for pattern in path_patterns)
    has_read_action = bool(
        re.search(
            r"\b(read|reading|loaded|loading|opened|opening|cat|sed|less|view|viewed)\b.{0,120}\bskill\.md\b",
            lower_text,
        )
    )
    if has_matching_path and has_read_action and not looks_like_skill_non_use_context(lower_text):
        return True
    return has_read_action and not looks_like_skill_non_use_context(lower_text)


def looks_like_skill_non_use_context(lower_text: str) -> bool:
    return any(
        marker in lower_text
        for marker in (
            "not using",
            "do not use",
            "don't use",
            "without loading",
            "docs",
            "documentation",
            "install",
            "roadmap",
            "planning",
            "later",
        )
    )


def mention_only_signals(skill: str, item: AuditRecord) -> list[str]:
    signals = ["keyword_mention"]
    lower = item.text.lower()
    if any(word in lower for word in SUGGESTION_WORDS):
        signals.append("suggestion_or_documentation")
    if re.search(rf"\${re.escape(skill_slug(skill))}\b", lower):
        signals.append("explicit_skill_token")
    if "skill.md" in lower:
        signals.append("skill_doc_reference_without_matching_use")
    return sorted(set(signals))


def mentions_skill(skill: str, text: str) -> bool:
    lower = text.lower()
    slug = skill_slug(skill)
    variants = {skill.lower(), slug, slug.replace("-", " "), slug.replace("-", "_")}
    return any(variant and variant in lower for variant in variants)


def workflow_behavior_nearby(records: Iterable[AuditRecord]) -> bool:
    for item in records:
        lower = item.text.lower()
        if any(token in lower for token in ("apply_patch", "pytest", "progress log", "scratch", "artifact", "report", "commit")):
            return True
    return False


def is_correction(item: AuditRecord) -> bool:
    lower = item.text.lower()
    if item.record.role.lower() not in {"user", "user_message"} and "partially_verified" not in lower:
        return False
    return any(word in lower for word in CORRECTION_WORDS)


def outcome_labels(evidence: AuditRecord, post_window: list[dict], corrections: list[dict]) -> list[str]:
    text = " ".join([evidence.text, *(item.get("snippet") or "" for item in post_window)]).lower()
    labels: set[str] = set()
    if corrections:
        labels.add("needed_correction")
    if any(word in text for word in ACCEPTANCE_WORDS) and not corrections:
        labels.add("completed_smoothly")
    if any(word in text for word in ARTIFACT_WORDS):
        labels.add("produced_useful_durable_artifact")
    if "ignored" in text and "skill" in text:
        labels.add("ignored_skill_guidance")
    if "over" in text and ("skill" in text or "trigger" in text):
        labels.add("over_applied_skill")
    if "too many tool" in text or "extra tool" in text:
        labels.add("caused_extra_tool_calls")
    if not labels:
        labels.add("outcome_unclear")
    return sorted(labels)


def improvement_opportunities(labels: list[str], corrections: list[dict]) -> list[dict]:
    opportunities: list[dict] = []
    if "needed_correction" in labels:
        opportunities.append(
            {
                "label": "clarify_correction_handling",
                "basis": "later correction language appears in the bounded post-use window",
                "source_refs": [item["source_ref"] for item in corrections[:3]],
            }
        )
    if "ignored_skill_guidance" in labels:
        opportunities.append({"label": "make_required_steps_more_auditable", "basis": "post-use text says skill guidance was ignored"})
    if "over_applied_skill" in labels:
        opportunities.append({"label": "tighten_trigger_conditions", "basis": "post-use text suggests over-application"})
    if "caused_extra_tool_calls" in labels:
        opportunities.append({"label": "reduce_unnecessary_tools", "basis": "post-use text mentions extra tool calls"})
    return opportunities


def uncertainty_for(signals: list[dict], corrections: list[dict]) -> list[str]:
    signal_types = {item["signal_type"] for item in signals}
    uncertainty: list[str] = []
    if "skill_file_read" not in signal_types and "skill_helper_invocation" not in signal_types:
        uncertainty.append("No matching SKILL.md/helper read was found; actual use relies on explicit statement plus nearby workflow behavior.")
    if not corrections:
        uncertainty.append("No later correction found inside the bounded correction window; absence is not proof of success.")
    return uncertainty


def trigger_for(records: list[AuditRecord], index: int) -> dict | None:
    for item in reversed(records[max(0, index - 8) : index + 1]):
        if item.record.role.lower() in {"user", "user_message"}:
            return context_row(item)
    return None


def window_records(records: Iterable[AuditRecord], *, exclude_empty: bool) -> list[dict]:
    rows = []
    for item in records:
        if exclude_empty and not item.text.strip():
            continue
        rows.append(context_row(item))
    return rows


def context_row(item: AuditRecord) -> dict:
    return {
        "source_ref": {"path": item.record.path, "line": item.record.line},
        "timestamp": item.record.raw_timestamp,
        "timestamp_utc": item.timestamp_utc,
        "role": item.record.role,
        "cwd": item.record.cwd,
        "snippet": snippet(item.text),
    }


def signal(item: AuditRecord, signal_type: str, basis: str) -> dict:
    row = context_row(item)
    return {
        "signal_type": signal_type,
        "basis": basis,
        **row,
    }


def dedupe_signals(signals: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, int]] = set()
    result = []
    for item in signals:
        ref = item["source_ref"]
        key = (item["signal_type"], ref["path"], int(ref["line"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def next_read_refs(*groups) -> list[dict]:
    refs = []
    seen: set[tuple[str, int]] = set()
    for group in groups:
        for item in group or []:
            ref = item.get("source_ref")
            if not ref:
                continue
            key = (ref.get("path", ""), int(ref.get("line") or 0))
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
            if len(refs) >= 8:
                return refs
    return refs


def confidence_for(signal_types: list[str]) -> str:
    types = set(signal_types)
    if "skill_file_read" in types or "skill_helper_invocation" in types:
        return "high"
    if "explicit_agent_use_statement" in types:
        return "medium"
    return "low"


def compact_ai_handoff(skill: str, instances: list[dict], non_use_mentions: list[dict]) -> dict:
    return {
        "skill": skill,
        "status_hint": "interpret the deterministic packet; do not infer actual use from non_use_mentions",
        "usage_instances": [
            {
                "usage_id": item["usage_id"],
                "confidence": item["confidence"],
                "signal_types": item["signal_types"],
                "trigger_ref": (item.get("trigger") or {}).get("source_ref"),
                "evidence_refs": [signal["source_ref"] for signal in item.get("skill_evidence", [])[:4]],
                "correction_refs": [row["source_ref"] for row in item.get("user_corrections", [])[:4]],
                "outcome_labels": item["outcome_labels"],
                "workflow_classification": item.get("workflow_classification"),
                "improvement_labels": [opp["label"] for opp in item.get("improvement_opportunities", [])],
                "uncertainty": item["uncertainty"],
            }
            for item in instances[:INLINE_INSTANCE_LIMIT]
        ],
        "non_use_mention_refs": [item["source_ref"] for item in non_use_mentions[:INLINE_MENTION_LIMIT]],
        "missed_trigger_refs": [
            item["source_ref"]
            for item in non_use_mentions[:INLINE_MENTION_LIMIT]
            if item.get("classification") == "missed_trigger"
        ],
        "recommended_next_read": [
            "Open high-confidence skill evidence rows first.",
            "Inspect correction refs before suggesting skill changes.",
            "Treat non-use mentions as exclusion evidence, not proof of skill execution.",
            "Treat missed_trigger and missing-reference-follow-through classifications as workflow failures to inspect.",
        ],
    }


def signal_definitions() -> dict:
    return {
        "high_confidence_actual_use": [
            "matching SKILL.md read",
            "matching helper/script invocation under the skill",
        ],
        "medium_confidence_actual_use": [
            "explicit agent statement that it is using the skill plus nearby workflow behavior",
        ],
        "non_use": [
            "discussion, documentation, installation, roadmap, or suggestion mentions without actual-use evidence",
        ],
        "correction_labels": sorted(CORRECTION_WORDS),
        "outcome_labels": [
            "completed_smoothly",
            "needed_correction",
            "ignored_skill_guidance",
            "over_applied_skill",
            "caused_extra_tool_calls",
            "produced_useful_durable_artifact",
            "outcome_unclear",
        ],
    }


def snippet(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= SNIPPET_LIMIT:
        return compact
    return compact[: SNIPPET_LIMIT - 3].rstrip() + "..."


def skill_slug(skill: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", skill.lower()).strip("-")


def timestamp_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()
