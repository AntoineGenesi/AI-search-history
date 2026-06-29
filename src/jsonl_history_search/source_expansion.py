from __future__ import annotations

import json
import hashlib
import shlex
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
from .agent_contract import source_ref_target
from .sources import PlannedRoot, SourcePlan, path_is_inside, source_kind_for_path


DEFAULT_WINDOW_BEFORE = 2
DEFAULT_WINDOW_AFTER = 2
DEFAULT_NEXT_COUNT = 3
DEFAULT_MAX_RECORDS = 20
SUPPORTED_FOLLOW_NEXT_READ_KINDS = {"recovery"}
RECOVERY_USABILITY_STATUSES = {
    "private_reviewed_usable",
    "reviewed_not_useful",
    "still_unreviewed",
    "review_gap",
}
REVIEWED_FOLLOWED_RECOVERY_TARGET = "answer_only_for_reviewed_usable_facets_and_state_remaining_gaps"


@dataclass(frozen=True)
class ExpansionRequest:
    target: str | None = None
    source_ref: dict[str, Any] | None = None
    mode: str = "record"
    scope: str = "source"
    before: int = DEFAULT_WINDOW_BEFORE
    after: int = DEFAULT_WINDOW_AFTER
    next_count: int = DEFAULT_NEXT_COUNT
    max_records: int = DEFAULT_MAX_RECORDS


@dataclass(frozen=True)
class ParsedTarget:
    path_text: str | None
    line: int | None
    raw_target: str | None


def build_follow_next_reads_payload(
    *,
    report_path: Path,
    kind: str,
    limit: int,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
    reviewed_usability_decisions_path: Path | None = None,
    recovery_report_output_path: Path | None = None,
) -> dict[str, Any]:
    decisions_packet = load_recovery_usability_decisions(reviewed_usability_decisions_path)
    base: dict[str, Any] = {
        "schema_version": 1,
        "kind": kind,
        "limit": max(0, int(limit)),
        "report_path": str(report_path),
        "privacy_flags": {
            "raw_text_included": True,
            "raw_paths_included": True,
            "source_refs_may_point_to_local_transcripts": True,
            "vectors_indexes_or_persistent_cache_used": False,
        },
        "reviewed_usability_decisions": decisions_packet["summary"],
        "claim_boundaries": {
            "broad_search_rerun": False,
            "ranking_or_cap_changed": False,
            "retrieval_defaults_changed": False,
            "answer_generation_included": False,
            "final_answer_claim_supported": False,
            "all_history_completeness_claim_supported": False,
        },
        "expanded_reads": [],
    }
    if kind not in SUPPORTED_FOLLOW_NEXT_READ_KINDS:
        return {
            "follow_next_reads": {
                **base,
                "status": "unsupported_kind",
                **follow_next_read_counts([], []),
                "failure_reason": f"unsupported kind: {kind}",
                "supported_kinds": sorted(SUPPORTED_FOLLOW_NEXT_READ_KINDS),
                "source_packet": {"status": "not_read"},
            }
        }
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "follow_next_reads": {
                **base,
                "status": "report_not_found",
                **follow_next_read_counts([], []),
                "failure_reason": "from-report path does not exist",
                "source_packet": {"status": "not_read"},
            }
        }
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "follow_next_reads": {
                **base,
                "status": "report_read_error",
                **follow_next_read_counts([], []),
                "failure_reason": str(exc),
                "source_packet": {"status": "not_read"},
            }
        }
    except json.JSONDecodeError as exc:
        return {
            "follow_next_reads": {
                **base,
                "status": "invalid_report_json",
                **follow_next_read_counts([], []),
                "failure_reason": str(exc),
                "source_packet": {"status": "invalid_json"},
            }
        }

    packet_info = detect_follow_packet(report)
    if packet_info["status"] != "supported":
        return {
            "follow_next_reads": {
                **base,
                "status": packet_info["status"],
                **follow_next_read_counts([], []),
                "failure_reason": packet_info["failure_reason"],
                "source_packet": packet_info,
            }
        }

    reads = prioritize_follow_next_reads(collect_follow_next_reads(packet_info["packet"], packet_info["packet_type"]))
    selected_reads = reads[: max(0, int(limit))]
    expanded_reads = [
        expand_follow_next_read(
            read=read,
            rank=index + 1,
            plan=plan,
            default_tz=default_tz,
            cwd=cwd,
            usability_decisions=decisions_packet["decisions"],
        )
        for index, read in enumerate(selected_reads)
    ]
    counts = follow_next_read_counts(selected_reads, expanded_reads)
    reviewed_usability_summary = {
        **decisions_packet["summary"],
        "matched_count": sum(1 for item in expanded_reads if (item.get("recovery_usability") or {}).get("matched")),
        "status_counts": recovery_usability_status_counts(expanded_reads),
    }
    answer_gate = build_reviewed_follow_through_answer_gate(counts, {**base, **counts})
    workflow_step_summary = recovery_workflow_step_summary(
        saved_follow_next_reads_report=recovery_report_output_path is not None,
        expanded_count=counts["expanded_count"],
        reviewed_counts_available=True,
    )
    return {
        "follow_next_reads": {
            **base,
            "reviewed_usability_decisions": reviewed_usability_summary,
            "status": follow_next_reads_status(counts),
            **counts,
            "recovery_bundle": build_recovery_bundle_summary(
                reads=reads,
                selected_reads=selected_reads,
                expanded_reads=expanded_reads,
                limit=max(0, int(limit)),
                counts=counts,
            ),
            "reviewed_follow_through_answer_gate": answer_gate,
            "workflow_step_summary": workflow_step_summary,
            "source_packet": {
                "status": "supported",
                "packet_type": packet_info["packet_type"],
                "schema_version": packet_info["schema_version"],
                "candidate_surfaces": packet_info["candidate_surfaces"],
            },
            "selection": {
                "available_next_read_count": len(reads),
                "selected_count": len(selected_reads),
                "expanded_count": counts["expanded_count"],
                "failed_count": counts["failed_count"],
                "usable_recovery_evidence_count": counts["usable_recovery_evidence_count"],
                "reviewed_usable_recovery_evidence_count": counts["reviewed_usable_recovery_evidence_count"],
                "reviewed_not_useful_recovery_count": counts["reviewed_not_useful_recovery_count"],
                "still_unreviewed_recovery_count": counts["still_unreviewed_recovery_count"],
                "review_gap_recovery_count": counts["review_gap_recovery_count"],
                "limit_applied": max(0, int(limit)),
                "priority_order": [
                    "promoted_continuation_recovery_clr",
                    "omitted_window_recovery",
                    "ordinary_next_read",
                ],
                "deduplicated_by": "target_or_source_ref",
            },
            "failed_reads": failed_follow_next_read_summaries(expanded_reads),
            "expanded_reads": expanded_reads,
            "agent_instruction": build_follow_next_reads_agent_instruction(
                report_path=report_path,
                recovery_report_output_path=recovery_report_output_path,
                status=follow_next_reads_status(counts),
                counts=counts,
                reviewed_follow_through_answer_gate=answer_gate,
                workflow_step_summary=workflow_step_summary,
            ),
        }
    }


def build_follow_next_reads_agent_instruction(
    *,
    report_path: Path,
    recovery_report_output_path: Path | None,
    status: str,
    counts: dict[str, int],
    reviewed_follow_through_answer_gate: dict[str, Any] | None = None,
    workflow_step_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status in {"expanded", "partial_expanded"} and counts.get("expanded_count", 0):
        decision = "needs_investigation"
        required_next_action = "run_failure_analysis_with_recovery_report"
    elif status == "no_next_reads":
        decision = "answer_now"
        required_next_action = "answer_with_explicit_no_next_reads_boundary"
    else:
        decision = "gather_more_evidence"
        required_next_action = "fix_or_rerun_recovery_reads_before_synthesis"
    follow_report = str(recovery_report_output_path) if recovery_report_output_path else "<saved-follow-next-reads-report.json>"
    command_copy_pasteable = recovery_report_output_path is not None
    failure_argv = [
        "jsonl-history-search",
        "failure-analysis",
        "--semantic-debug-report",
        str(report_path),
        "--follow-next-reads-report",
        follow_report,
        "--story-id",
        "investigation-brief",
        "--format",
        "json",
    ]
    commands = [
        {
            "command_id": "build_investigation_brief",
            "argv": failure_argv,
            "command": " ".join(shlex.quote(str(part)) for part in failure_argv),
            "requires_saved_follow_next_reads_report": follow_report,
            "command_copy_pasteable": command_copy_pasteable,
            "handoff_layer": "investigation_brief",
            "purpose": "Use this recovery packet with the original semantic-debug report to produce failure_analysis.devlog_brief.",
        }
    ]
    if recovery_report_output_path is not None:
        eval_argv = [
            "jsonl-history-search",
            "eval-decision-loop",
            "--semantic-debug-report",
            str(report_path),
            "--follow-next-reads-report",
            follow_report,
            "--format",
            "json",
        ]
        commands.append(
            {
                "command_id": "answer_eval_gate",
                "argv": eval_argv,
                "command": " ".join(shlex.quote(str(part)) for part in eval_argv),
                "requires_saved_follow_next_reads_report": follow_report,
                "command_copy_pasteable": True,
                "handoff_layer": "answer_eval_gate",
                "optional_when_embedded_gate_available": bool(reviewed_follow_through_answer_gate),
                "purpose": (
                    "Use this saved semantic-debug report plus saved follow-next-reads report "
                    "to recheck whether followed recovery is reviewed enough to answer."
                ),
            }
        )
    embedded_gate_available = bool(command_copy_pasteable and reviewed_follow_through_answer_gate)
    if command_copy_pasteable and decision == "needs_investigation" and embedded_gate_available:
        next_action = "use_embedded_answer_gate_before_answer"
    elif command_copy_pasteable and decision == "needs_investigation":
        next_action = "run_eval_decision_gate_before_answer"
    else:
        next_action = required_next_action
    return {
        "schema_version": 1,
        "decision": decision,
        "required_next_action": next_action,
        "reviewed_follow_through_answer_gate": reviewed_follow_through_answer_gate,
        "workflow_step_summary": workflow_step_summary or {},
        "commands": commands,
        "do_not_answer_until": [
            "Use reviewed_follow_through_answer_gate before answering from followed recovery; run answer_eval_gate only when an independent recheck is needed.",
            "Do not synthesize devlog-style conclusions until expanded recovery reads have been reviewed or marked unusable.",
            "Use failure_analysis.devlog_brief for supported claims, timeline/play-by-play, non-claims, gaps, and next reads.",
            "Do not claim root cause, all-history completeness, final-answer quality, token savings, speed, or product value.",
        ],
        "workflow_layers": [
            {"layer": "evidence_packet", "surface": "semantic_debug", "status": "already_saved"},
            {"layer": "recovery_packet", "surface": "follow_next_reads", "status": status},
            {
                "layer": "answer_eval_gate",
                "surface": "eval_decision_loop.reviewed_follow_through_answer_gate",
                "status": "embedded" if embedded_gate_available else "next" if command_copy_pasteable else "requires_saved_follow_next_reads_report",
            },
            {"layer": "investigation_brief", "surface": "failure_analysis.devlog_brief", "status": "available"},
        ],
        "privacy_boundaries": {
            "raw_text_included_in_this_packet": True,
            "raw_paths_included_in_this_packet": True,
            "committed_safe_without_sanitization": False,
        },
    }


def build_recovery_bundle_summary(
    *,
    reads: list[dict[str, Any]],
    selected_reads: list[dict[str, Any]],
    expanded_reads: list[dict[str, Any]],
    limit: int,
    counts: dict[str, int],
) -> dict[str, Any]:
    categories: dict[str, dict[str, Any]] = {}
    for read in selected_reads:
        kind = recovery_metadata(read).get("kind") or "ordinary_next_read"
        categories.setdefault(kind, recovery_category_row(kind))["selected_count"] += 1
    for item in expanded_reads:
        recovery = item.get("recovery") if isinstance(item.get("recovery"), dict) else {}
        kind = str(recovery.get("kind") or "ordinary_next_read")
        category = categories.setdefault(kind, recovery_category_row(kind))
        if item.get("status") == "expanded":
            category["expanded_count"] += 1
        else:
            category["failed_count"] += 1
        usability = item.get("recovery_usability") if isinstance(item.get("recovery_usability"), dict) else {}
        usability_status = str(usability.get("status") or "unknown")
        category["reviewed_status_counts"][usability_status] = (
            category["reviewed_status_counts"].get(usability_status, 0) + 1
        )
        category["evidence_handles"].append(recovery_bundle_evidence_handle(item))

    available_count = len(reads)
    selected_count = len(selected_reads)
    expanded_count = counts.get("expanded_count", 0)
    failed_count = counts.get("failed_count", 0)
    return {
        "schema_version": 1,
        "status": "bounded_recovery_bundle",
        "expanded_evidence_categories": [
            {
                **row,
                "reviewed_status_counts": dict(sorted(row["reviewed_status_counts"].items())),
                "evidence_handles": row["evidence_handles"][:5],
            }
            for _kind, row in sorted(categories.items())
        ],
        "category_counts": {
            key: {
                "selected_count": row["selected_count"],
                "expanded_count": row["expanded_count"],
                "failed_count": row["failed_count"],
            }
            for key, row in sorted(categories.items())
        },
        "omitted_or_recovery_boundaries": {
            "available_next_read_count": available_count,
            "selected_count": selected_count,
            "expanded_count": expanded_count,
            "failed_count": failed_count,
            "limit_applied": limit,
            "unselected_recovery_count": max(0, available_count - selected_count),
            "unexpanded_selected_count": max(0, selected_count - expanded_count - failed_count),
            "all_history_completeness_claim_supported": False,
            "answer_generation_included": False,
            "ranking_or_cap_changed": False,
            "broad_search_rerun": False,
        },
        "privacy_boundaries": {
            "raw_text_included_in_bundle_summary": False,
            "raw_paths_included_in_bundle_summary": False,
            "source_refs_or_targets_replaced_by_handles": True,
            "expanded_reads_may_include_raw_text_and_paths": True,
        },
    }


def recovery_category_row(kind: str) -> dict[str, Any]:
    return {
        "category": kind,
        "selected_count": 0,
        "expanded_count": 0,
        "failed_count": 0,
        "reviewed_status_counts": {},
        "evidence_handles": [],
    }


def recovery_bundle_evidence_handle(item: dict[str, Any]) -> dict[str, Any]:
    recovery = item.get("recovery") if isinstance(item.get("recovery"), dict) else {}
    usability = item.get("recovery_usability") if isinstance(item.get("recovery_usability"), dict) else {}
    source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    return {
        "rank": item.get("rank"),
        "candidate_id": item.get("candidate_id"),
        "recovery_id": recovery.get("recovery_id"),
        "status": item.get("status"),
        "reviewed_usability_status": usability.get("status"),
        "target_handle": hashed_handle("target", item.get("target")),
        "source_handle": hashed_handle("source", json.dumps(source_ref, sort_keys=True) if source_ref else None),
    }


def recovery_workflow_step_summary(
    *,
    saved_follow_next_reads_report: bool,
    expanded_count: int,
    reviewed_counts_available: bool,
) -> dict[str, Any]:
    bundled_ready = bool(saved_follow_next_reads_report and expanded_count and reviewed_counts_available)
    previous_required = ["expand_recovery_context", "answer_eval_gate"]
    bundled_required = ["expand_recovery_context"] if bundled_ready else previous_required
    return {
        "schema_version": 1,
        "comparison_scope": "same reviewed follow-next-reads categories",
        "previous_split_required_command_ids": previous_required,
        "bundled_required_command_ids": bundled_required,
        "separate_recovery_command_steps_avoided": max(0, len(previous_required) - len(bundled_required)),
        "answer_gate_metadata_embedded": bundled_ready,
        "claim_boundary": (
            "counts only separate package command steps in the reviewed follow-through path; "
            "does not claim elapsed time, token savings, cost, final-answer quality, or release readiness"
        ),
    }


def build_reviewed_follow_through_answer_gate(
    counts: dict[str, int],
    packet: dict[str, Any],
) -> dict[str, Any]:
    reviewed_usable = counts["reviewed_usable_recovery_evidence_count"]
    reviewed_negative = counts["reviewed_not_useful_recovery_count"]
    still_unreviewed = counts["still_unreviewed_recovery_count"]
    review_gap = counts["review_gap_recovery_count"]
    expanded = int_or_default(packet.get("expanded_count"), 0)
    failed = int_or_default(packet.get("failed_count"), 0)
    if expanded and reviewed_usable and not still_unreviewed and not review_gap and not reviewed_negative:
        status = "reviewed_recovery_answer_usable_with_boundaries"
        decision = "answer_with_reviewed_recovery_and_source_discipline_boundaries"
    elif expanded and reviewed_usable:
        status = "reviewed_recovery_partially_answer_usable"
        decision = REVIEWED_FOLLOWED_RECOVERY_TARGET
    elif expanded and (reviewed_negative or review_gap):
        status = "reviewed_recovery_not_answer_ready"
        decision = "do_not_answer_from_recovery_without_more_source_discipline"
    elif expanded and still_unreviewed:
        status = "followthrough_opened_but_recovery_unreviewed"
        decision = "review_opened_recovery_before_answering"
    elif failed:
        status = "followthrough_attempted_but_reads_failed"
        decision = "fix_or_rerun_recovery_reads_before_answering"
    else:
        status = "followthrough_no_recovery_opened"
        decision = "do_not_answer_without_gap_boundary"

    blockers: list[str] = []
    if still_unreviewed:
        blockers.append("opened_recovery_not_reviewed_for_usability")
    if review_gap:
        blockers.append("reviewed_recovery_has_source_discipline_gap")
    if reviewed_negative:
        blockers.append("reviewed_recovery_contains_explicit_negative_evidence")
    if failed:
        blockers.append("follow_next_reads_failures_present")

    return {
        "status": status,
        "decision": decision,
        "answer_ready_without_boundaries": False,
        "source_discipline_blockers": blockers,
        "basis": {
            **counts,
            "expanded_count": expanded,
            "failed_count": failed,
            "usable_recovery_evidence_count": int_or_default(packet.get("usable_recovery_evidence_count"), 0),
        },
        "claim_boundary": (
            "derived from sanitized follow-next-reads reviewed usability counters; "
            "not a final-answer quality or all-history completeness claim"
        ),
    }


def recovery_usability_status_counts(expanded_reads: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in expanded_reads:
        status = str((item.get("recovery_usability") or {}).get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def follow_next_read_counts(selected_reads: list[dict[str, Any]], expanded_reads: list[dict[str, Any]]) -> dict[str, int]:
    expanded_count = sum(1 for item in expanded_reads if item.get("status") == "expanded")
    failed_count = sum(1 for item in expanded_reads if item.get("status") != "expanded")
    reviewed_usable_count = sum(
        1
        for item in expanded_reads
        if item.get("status") == "expanded"
        and (item.get("recovery_usability") or {}).get("status") == "private_reviewed_usable"
    )
    usable_recovery_evidence_count = sum(
        1
        for item in expanded_reads
        if item.get("status") == "expanded"
        and (
            (
                (item.get("recovery") or {}).get("kind") != "ordinary_next_read"
                and bool(item.get("selected_records"))
            )
            or (item.get("recovery_usability") or {}).get("status") == "private_reviewed_usable"
        )
    )
    return {
        "selected_count": len(selected_reads),
        "expanded_count": expanded_count,
        "failed_count": failed_count,
        "usable_recovery_evidence_count": usable_recovery_evidence_count,
        "reviewed_usable_recovery_evidence_count": reviewed_usable_count,
        "reviewed_not_useful_recovery_count": sum(
            1
            for item in expanded_reads
            if item.get("status") == "expanded"
            and (item.get("recovery_usability") or {}).get("status") == "reviewed_not_useful"
        ),
        "still_unreviewed_recovery_count": sum(
            1
            for item in expanded_reads
            if item.get("status") == "expanded"
            and (item.get("recovery_usability") or {}).get("status") == "still_unreviewed"
        ),
        "review_gap_recovery_count": sum(
            1
            for item in expanded_reads
            if item.get("status") == "expanded"
            and (item.get("recovery_usability") or {}).get("status") == "review_gap"
        ),
    }


def follow_next_reads_status(counts: dict[str, int]) -> str:
    selected_count = counts.get("selected_count", 0)
    expanded_count = counts.get("expanded_count", 0)
    failed_count = counts.get("failed_count", 0)
    if selected_count <= 0:
        return "no_next_reads"
    if expanded_count > 0 and failed_count > 0:
        return "partial_expanded"
    if expanded_count > 0:
        return "expanded"
    return "selected_reads_failed"


def failed_follow_next_read_summaries(expanded_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in expanded_reads:
        if item.get("status") == "expanded":
            continue
        failures.append(
            {
                "rank": item.get("rank"),
                "target": item.get("target"),
                "surface": item.get("surface"),
                "candidate_id": item.get("candidate_id"),
                "recovery": item.get("recovery"),
                "status": item.get("status"),
                "failure_reason": item.get("failure_reason") or item.get("status"),
            }
        )
    return failures[:20]


def build_source_expansion_payload(
    *,
    request: ExpansionRequest,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
) -> dict[str, Any]:
    parsed = parse_expansion_target(request.target, request.source_ref)
    warnings = list(plan.warnings)
    base_payload: dict[str, Any] = {
        "schema_version": 1,
        "mode": request.mode,
        "requested_target": parsed.raw_target,
        "anchor": {
            "target": parsed.raw_target,
            "source_ref": request.source_ref or source_ref_from_parsed_target(parsed),
            "line": parsed.line,
            "line_is_one_based": True,
        },
        "limits": {
            "scope": request.scope,
            "before": max(0, int(request.before)),
            "after": max(0, int(request.after)),
            "next_count": max(0, int(request.next_count)),
            "max_records": max(0, int(request.max_records)),
        },
        "privacy_flags": {
            "raw_text_included": True,
            "raw_paths_included": True,
            "source_refs_may_point_to_local_transcripts": True,
            "vectors_indexes_or_persistent_cache_used": False,
        },
        "claim_boundaries": {
            "broad_search_rerun": False,
            "ranking_or_cap_changed": False,
            "all_history_completeness_claim_supported": False,
        },
        "warnings": warnings,
    }
    if request.mode not in {"record", "window", "next", "before", "neighborhood"}:
        return {"source_expansion": {**base_payload, "status": "invalid_mode", "records": []}}
    if request.scope not in {"source", "session", "day"}:
        return {"source_expansion": {**base_payload, "status": "invalid_scope", "records": []}}
    if not parsed.path_text:
        return {"source_expansion": {**base_payload, "status": "missing_path", "records": []}}
    if parsed.line is None:
        return {"source_expansion": {**base_payload, "status": "missing_line", "records": []}}
    if parsed.line < 1:
        return {"source_expansion": {**base_payload, "status": "invalid_line", "records": []}}

    source_path = resolve_source_path(parsed.path_text, plan=plan, cwd=cwd)
    if source_path is None:
        return {
            "source_expansion": {
                **base_payload,
                "status": "source_not_found_or_out_of_scope",
                "records": [],
                "scope": source_scope(plan),
            }
        }
    source_kind = source_kind_for_path(source_path, plan)
    try:
        records = list(iter_records_for_file(source_path, source_kind, default_tz))
    except (AdapterReadError, OSError) as exc:
        return {
            "source_expansion": {
                **base_payload,
                "status": "read_error",
                "records": [],
                "source_path": str(source_path),
                "error": str(exc),
                "scope": source_scope(plan),
            }
        }

    total_records = len(records)
    if parsed.line > total_records:
        return {
            "source_expansion": {
                **base_payload,
                "status": "anchor_line_not_found",
                "records": [],
                "source_path": str(source_path),
                "source_kind": source_kind,
                "total_records_in_source": total_records,
                "scope": source_scope(plan),
            }
        }

    selection = select_records(
        records,
        anchor_line=parsed.line,
        mode=request.mode,
        scope=request.scope,
        before=max(0, int(request.before)),
        after=max(0, int(request.after)),
        next_count=max(0, int(request.next_count)),
        max_records=max(0, int(request.max_records)),
    )
    selected = selection["records"]
    requested_count = int(selection["requested_count"])
    range_start = selection["range_start"]
    range_end = selection["range_end"]
    included_count = len(selected)
    omitted_count = max(0, requested_count - included_count)
    start_of_source_reached = bool(
        range_start == 1
        or (
            request.mode == "before"
            and requested_count == 0
            and parsed.line == 1
            and selection["candidate_start"] is None
            and selection["candidate_end"] is None
        )
    )
    payload = {
        **base_payload,
        "status": "expanded",
        "source_path": str(source_path),
        "source_kind": source_kind,
        "total_records_in_source": total_records,
        "line_range": {
            "start": range_start,
            "end": range_end,
            "anchor": parsed.line,
            "line_is_one_based": True,
        },
        "bounds": {
            "mode": request.mode,
            "scope": request.scope,
            "before": max(0, int(request.before)),
            "after": max(0, int(request.after)),
            "next_count": max(0, int(request.next_count)),
            "max_records": max(0, int(request.max_records)),
            "candidate_line_range": {
                "start": selection["candidate_start"],
                "end": selection["candidate_end"],
                "line_is_one_based": True,
            },
        },
        "counts": {
            "requested_count": requested_count,
            "included_count": included_count,
            "omitted_count": omitted_count,
            "omitted_or_truncated_count": omitted_count,
            "scope_filtered_count": int(selection["scope_filtered_count"]),
            "before_available": max(0, parsed.line - 1),
            "after_available": max(0, total_records - parsed.line),
        },
        "truncated": omitted_count > 0,
        "boundary_flags": {
            "start_of_source_reached": start_of_source_reached,
            "end_of_source_reached": bool(range_end == total_records or (request.mode == "next" and included_count < request.next_count)),
            "start_of_candidate_range_reached": bool(range_start == selection["candidate_start"]),
            "end_of_candidate_range_reached": bool(range_end == selection["candidate_end"]),
        },
        "continuation": build_expansion_continuation(
            source_path=str(source_path),
            anchor_line=parsed.line,
            range_start=range_start,
            range_end=range_end,
            total_records=total_records,
            mode=request.mode,
            scope=request.scope,
            before=max(0, int(request.before)),
            after=max(0, int(request.after)),
            next_count=max(0, int(request.next_count)),
            max_records=max(0, int(request.max_records)),
        ),
        "records": [expanded_record(record) for record in selected],
        "scope": source_scope(plan),
    }
    return {"source_expansion": payload}


def detect_follow_packet(report: Any) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"status": "unsupported_report_shape", "failure_reason": "report JSON must be an object"}
    candidates: list[tuple[str, Any]] = []
    if "semantic_debug" in report:
        candidates.append(("semantic_debug", report.get("semantic_debug")))
    if "agent_find" in report:
        candidates.append(("agent_find", report.get("agent_find")))
    for packet_type, packet in candidates:
        if not isinstance(packet, dict):
            continue
        surfaces = follow_next_read_surfaces(packet, packet_type)
        if surfaces:
            return {
                "status": "supported",
                "packet_type": packet_type,
                "packet": packet,
                "schema_version": packet.get("schema_version"),
                "candidate_surfaces": [surface for surface, reads in surfaces if reads],
            }
    if candidates:
        return {
            "status": "stale_or_missing_next_reads",
            "failure_reason": "recognized packet type does not include current next_reads surfaces",
        }
    return {
        "status": "unsupported_report_shape",
        "failure_reason": "expected top-level semantic_debug or agent_find packet",
    }


def follow_next_read_surfaces(packet: dict[str, Any], packet_type: str) -> list[tuple[str, list[Any]]]:
    surfaces: list[tuple[str, list[Any]]] = []
    if packet_type == "semantic_debug":
        append_next_read_surface(surfaces, "semantic_debug.next_reads", packet)
        append_next_read_surface(
            surfaces,
            "semantic_debug.source_locator_handoff.next_reads",
            packet.get("source_locator_handoff") or {},
        )
        append_next_read_surface(
            surfaces,
            "semantic_debug.compact_provenance_handoff.next_reads",
            packet.get("compact_provenance_handoff") or {},
        )
    elif packet_type == "agent_find":
        append_next_read_surface(
            surfaces,
            "agent_find.source_locator_handoff.next_reads",
            packet.get("source_locator_handoff") or {},
        )
        append_next_read_surface(
            surfaces,
            "agent_find.compact_provenance_handoff.next_reads",
            packet.get("compact_provenance_handoff") or {},
        )
    return surfaces


def append_next_read_surface(surfaces: list[tuple[str, list[Any]]], label: str, container: Any) -> None:
    if isinstance(container, dict) and "next_reads" in container:
        surfaces.append((label, list_or_empty(container.get("next_reads"))))


def collect_follow_next_reads(packet: dict[str, Any], packet_type: str) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    by_key: dict[str, int] = {}
    for surface, surface_reads in follow_next_read_surfaces(packet, packet_type):
        for item in surface_reads:
            if not isinstance(item, dict):
                continue
            key = follow_next_read_key(item)
            if not key:
                continue
            candidate = {**item, "_follow_surface": surface}
            if key in by_key:
                index = by_key[key]
                reads[index] = merge_duplicate_follow_next_read(reads[index], candidate)
                continue
            by_key[key] = len(reads)
            reads.append(candidate)
    return reads


def merge_duplicate_follow_next_read(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    keep, discard = (incoming, existing) if follow_next_read_priority_key(incoming) < follow_next_read_priority_key(existing) else (existing, incoming)
    duplicate_surfaces = list(dict.fromkeys(list_or_empty(existing.get("_follow_duplicate_surfaces")) + list_or_empty(incoming.get("_follow_duplicate_surfaces"))))
    for read in (existing, incoming):
        surface = read.get("_follow_surface")
        if surface:
            duplicate_surfaces.append(str(surface))
    duplicate_surfaces = list(dict.fromkeys(duplicate_surfaces))
    duplicate_candidate_ids = list(
        dict.fromkeys(
            [
                str(item)
                for item in (
                    list_or_empty(existing.get("_follow_duplicate_candidate_ids"))
                    + list_or_empty(incoming.get("_follow_duplicate_candidate_ids"))
                    + [existing.get("candidate_id"), incoming.get("candidate_id")]
                )
                if item
            ]
        )
    )
    discarded = list_or_empty(existing.get("_follow_discarded_duplicates")) + list_or_empty(
        incoming.get("_follow_discarded_duplicates")
    )
    discarded.append(
        {
            "surface": discard.get("_follow_surface"),
            "candidate_id": discard.get("candidate_id"),
            "recovery": recovery_metadata(discard),
        }
    )
    return {
        **keep,
        "_follow_duplicate_surfaces": duplicate_surfaces,
        "_follow_duplicate_candidate_ids": duplicate_candidate_ids,
        "_follow_discarded_duplicates": discarded[:20],
    }


def prioritize_follow_next_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(reads, key=follow_next_read_priority_key)


def follow_next_read_priority_key(read: dict[str, Any]) -> tuple[int, int, str]:
    recovery_id = str((read.get("omitted_window_recovery") or {}).get("recovery_id") or "")
    if recovery_id.startswith("clr"):
        tier = 0
    elif read.get("omitted_window_recovery"):
        tier = 1
    else:
        tier = 2
    return (tier, 0 if read.get("target") else 1, str(read.get("target") or ""))


def expand_follow_next_read(
    *,
    read: dict[str, Any],
    rank: int,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
    usability_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request = expansion_request_from_next_read(read)
    expansion_cwd = derived_follow_read_cwd(read, fallback_cwd=cwd)
    expansion_plan, derived_roots = plan_with_follow_read_roots(read, plan=plan, cwd=expansion_cwd)
    payload = build_source_expansion_payload(
        request=request,
        plan=expansion_plan,
        default_tz=default_tz,
        cwd=expansion_cwd,
    )["source_expansion"]
    response: dict[str, Any] = {
        "rank": rank,
        "target": read.get("target") or request.target,
        "source_ref": read.get("source_ref"),
        "surface": read.get("_follow_surface"),
        "duplicate_surfaces": list_or_empty(read.get("_follow_duplicate_surfaces")),
        "duplicate_candidate_ids": list_or_empty(read.get("_follow_duplicate_candidate_ids")),
        "discarded_duplicate_reads": list_or_empty(read.get("_follow_discarded_duplicates")),
        "candidate_id": read.get("candidate_id"),
        "recovery": recovery_metadata(read),
        "request": {
            "mode": request.mode,
            "scope": request.scope,
            "before": request.before,
            "after": request.after,
            "next_count": request.next_count,
            "max_records": request.max_records,
        },
        "source_context": {
            "cwd": str(expansion_cwd),
            "derived_root_count": len(derived_roots),
            "derived_roots": [str(root) for root in derived_roots],
        },
        "status": payload.get("status"),
        "selected_records": payload.get("records", []),
        "truncated": payload.get("truncated", False),
        "counts": payload.get("counts", {}),
        "line_range": payload.get("line_range"),
        "continuation": payload.get("continuation"),
        "privacy_flags": payload.get("privacy_flags", {}),
        "claim_boundaries": payload.get("claim_boundaries", {}),
    }
    response["recovery_usability"] = recovery_usability_for_read(
        read=response,
        original_read=read,
        decisions=usability_decisions or [],
    )
    if payload.get("status") != "expanded":
        response["failure_reason"] = payload.get("error") or payload.get("status")
    return response


def load_recovery_usability_decisions(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "decisions": [],
            "summary": {
                "status": "not_supplied",
                "decision_count": 0,
                "matched_count": 0,
                "invalid_count": 0,
                "privacy_boundary": "optional decisions file not supplied",
            },
        }
    try:
        loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _usability_decision_load_error("not_found", "reviewed usability decisions path does not exist")
    except (OSError, UnicodeDecodeError):
        return _usability_decision_load_error("read_error", "reviewed usability decisions path could not be read")
    except json.JSONDecodeError:
        return _usability_decision_load_error("invalid_json", "reviewed usability decisions file is not valid JSON")

    raw_decisions = loaded.get("decisions") if isinstance(loaded, dict) else None
    if raw_decisions is None and isinstance(loaded, dict):
        raw_decisions = loaded.get("recovery_usability_decisions")
    if raw_decisions is None and isinstance(loaded, list):
        raw_decisions = loaded
    if not isinstance(raw_decisions, list):
        return _usability_decision_load_error("unsupported_shape", "expected decisions list")

    decisions: list[dict[str, Any]] = []
    invalid_count = 0
    for index, item in enumerate(raw_decisions, 1):
        decision = sanitize_recovery_usability_decision(item, index)
        if decision is None:
            invalid_count += 1
            continue
        decisions.append(decision)
    return {
        "decisions": decisions,
        "summary": {
            "status": "loaded",
            "decision_count": len(decisions),
            "matched_count": 0,
            "invalid_count": invalid_count,
            "allowed_statuses": sorted(RECOVERY_USABILITY_STATUSES),
            "privacy_boundary": "only sanitized handles/status labels/counts are propagated; raw review notes and transcript payloads are ignored",
        },
    }


def _usability_decision_load_error(status: str, reason: str) -> dict[str, Any]:
    return {
        "decisions": [],
        "summary": {
            "status": status,
            "decision_count": 0,
            "matched_count": 0,
            "invalid_count": 0,
            "failure_reason": reason,
            "privacy_boundary": "decisions file was not consumed",
        },
    }


def sanitize_recovery_usability_decision(item: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    status = str(item.get("status") or item.get("usability") or "").strip()
    if status not in RECOVERY_USABILITY_STATUSES:
        return None
    decision: dict[str, Any] = {
        "status": status,
        "decision_index": index,
        "match_keys": {},
    }
    for key in ("decision_id", "candidate_id", "recovery_id", "evidence_label", "reason_label", "reviewer_label"):
        value = sanitized_decision_label(item.get(key))
        if value is not None:
            decision[key] = value
    if item.get("rank") is not None:
        rank = int_or_default(item.get("rank"), -1)
        if rank > 0:
            decision["rank"] = rank
    for key in ("target_handle", "source_handle"):
        value = sanitized_decision_label(item.get(key))
        if value is not None:
            decision["match_keys"][key] = value
    for key in ("reviewed_record_count", "usable_record_count"):
        value = int_or_default(item.get(key), -1)
        if value >= 0:
            decision[key] = value
    if item.get("committed_safe") is not None:
        decision["committed_safe"] = bool(item.get("committed_safe"))
    return decision


def sanitized_decision_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    cleaned = "".join(ch for ch in text if ch in allowed)
    return cleaned[:120] if cleaned else None


def recovery_usability_for_read(
    *,
    read: dict[str, Any],
    original_read: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    match_context = recovery_usability_match_context(read, original_read)
    matched = None
    matched_by = None
    for decision in decisions:
        matched_by = recovery_usability_match_key(match_context, decision)
        if matched_by:
            matched = decision
            break
    if matched is None:
        return {
            "status": "still_unreviewed",
            "decision_source": "default_unreviewed",
            "matched": False,
            "match_context": match_context,
            "privacy_boundary": "no reviewed usability decision matched this opened recovery row",
        }
    result = {
        "status": matched["status"],
        "decision_source": "reviewed_usability_decisions",
        "matched": True,
        "matched_by": matched_by,
        "match_context": match_context,
        "privacy_boundary": "sanitized reviewed-usability metadata only; raw private review text is not propagated",
    }
    for key in (
        "decision_id",
        "candidate_id",
        "recovery_id",
        "evidence_label",
        "reason_label",
        "reviewer_label",
        "reviewed_record_count",
        "usable_record_count",
        "committed_safe",
    ):
        if key in matched:
            result[key] = matched[key]
    return result


def recovery_usability_match_context(read: dict[str, Any], original_read: dict[str, Any]) -> dict[str, Any]:
    recovery = read.get("recovery") if isinstance(read.get("recovery"), dict) else {}
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    return {
        "rank": read.get("rank"),
        "candidate_id": read.get("candidate_id") or recovery.get("candidate_id"),
        "recovery_id": recovery.get("recovery_id"),
        "recovery_kind": recovery.get("kind"),
        "target_handle": hashed_handle("target", read.get("target")),
        "source_handle": hashed_handle("source", json.dumps(source_ref, sort_keys=True) if source_ref else None),
        "original_target_handle": hashed_handle("target", original_read.get("target")),
    }


def recovery_usability_match_key(context: dict[str, Any], decision: dict[str, Any]) -> str | None:
    for key in ("candidate_id", "recovery_id"):
        if decision.get(key) and context.get(key) and str(decision.get(key)) == str(context.get(key)):
            return key
    if decision.get("rank") and context.get("rank") and int(decision["rank"]) == int(context["rank"]):
        return "rank"
    match_keys = decision.get("match_keys") if isinstance(decision.get("match_keys"), dict) else {}
    for key in ("target_handle", "source_handle"):
        if match_keys.get(key) and context.get(key) and match_keys.get(key) == context.get(key):
            return key
    return None


def hashed_handle(prefix: str, value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def derived_follow_read_cwd(read: dict[str, Any], *, fallback_cwd: Path) -> Path:
    action = first_read_more_action(read)
    candidates = [fallback_cwd]
    if action:
        argv = [str(item) for item in list_or_empty(action.get("argv"))]
        action_cwd = argv_option_value(argv, "--cwd")
        if action_cwd:
            candidates.append(Path(action_cwd).expanduser())
    parsed = parse_expansion_target(read.get("target"), read.get("source_ref") if isinstance(read.get("source_ref"), dict) else None)
    for candidate in candidates:
        base = candidate.expanduser()
        if parsed.path_text and (base / parsed.path_text).exists():
            return base.resolve()
    return fallback_cwd


def plan_with_follow_read_roots(
    read: dict[str, Any],
    *,
    plan: SourcePlan,
    cwd: Path,
) -> tuple[SourcePlan, list[Path]]:
    parsed = parse_expansion_target(read.get("target"), read.get("source_ref") if isinstance(read.get("source_ref"), dict) else None)
    target_path = resolved_existing_path(parsed.path_text, cwd=cwd)
    derived_roots: list[Path] = []
    for root in follow_read_root_candidates(read, cwd=cwd):
        resolved = root.expanduser().resolve()
        if not resolved.exists():
            continue
        if target_path and not path_is_inside(target_path, resolved) and target_path != resolved:
            continue
        if any(existing.path.expanduser().resolve() == resolved for existing in plan.roots):
            continue
        if resolved in derived_roots:
            continue
        derived_roots.append(resolved)
    if not derived_roots and target_path:
        parent = target_path.parent.resolve()
        if not any(existing.path.expanduser().resolve() == parent for existing in plan.roots):
            derived_roots.append(parent)
    if not derived_roots:
        return plan, []
    augmented = SourcePlan(
        roots=list(plan.roots),
        rejected_roots=list(plan.rejected_roots),
        warnings=list(plan.warnings),
        blind_spots=list(plan.blind_spots),
        limits=plan.limits,
    )
    for root in derived_roots:
        augmented.roots.append(
            PlannedRoot(
                kind="arbitrary_jsonl_root",
                path=root,
                reason="derived from saved follow-next-reads read-more action",
                recursive=root.suffix not in {".jsonl", ".json"},
                known=False,
            )
        )
    augmented.warnings.append("follow-next-reads derived source scope from saved read-more action metadata.")
    return augmented, derived_roots


def follow_read_root_candidates(read: dict[str, Any], *, cwd: Path) -> list[Path]:
    action = first_read_more_action(read)
    if not action:
        return []
    argv = [str(item) for item in list_or_empty(action.get("argv"))]
    roots = argv_option_values(argv, "--root")
    action_cwd_value = argv_option_value(argv, "--cwd")
    action_cwd = Path(action_cwd_value).expanduser() if action_cwd_value else None
    candidates: list[Path] = []
    for root_text in roots:
        root = Path(root_text).expanduser()
        if root.is_absolute():
            candidates.append(root)
            continue
        candidates.append((cwd / root).resolve())
        if action_cwd is not None:
            candidates.append((action_cwd / root).resolve())
        candidates.append(root)
    return candidates


def resolved_existing_path(path_text: str | None, *, cwd: Path) -> Path | None:
    if not path_text:
        return None
    raw = Path(path_text).expanduser()
    candidates = [raw if raw.is_absolute() else cwd / raw]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return None


def expansion_request_from_next_read(read: dict[str, Any]) -> ExpansionRequest:
    action = first_read_more_action(read)
    kwargs: dict[str, Any] = {
        "target": read.get("target"),
        "source_ref": read.get("source_ref") if isinstance(read.get("source_ref"), dict) else None,
        "mode": "record",
        "scope": "source",
        "before": DEFAULT_WINDOW_BEFORE,
        "after": DEFAULT_WINDOW_AFTER,
        "next_count": DEFAULT_NEXT_COUNT,
        "max_records": DEFAULT_MAX_RECORDS,
    }
    if action:
        argv = [str(item) for item in list_or_empty(action.get("argv"))]
        if len(argv) >= 2 and argv[0] == "read-more":
            kwargs["target"] = argv[1]
        for option, key in (
            ("--mode", "mode"),
            ("--scope", "scope"),
            ("--before", "before"),
            ("--after", "after"),
            ("--next-count", "next_count"),
            ("--max-records", "max_records"),
        ):
            value = argv_option_value(argv, option)
            if value is not None:
                kwargs[key] = value if key in {"mode", "scope"} else int_or_default(value, kwargs[key])
        for key in ("mode", "scope", "before", "after", "next_count", "max_records"):
            if key in action:
                kwargs[key] = action[key] if key in {"mode", "scope"} else int_or_default(action[key], kwargs[key])
    return ExpansionRequest(**kwargs)


def first_read_more_action(read: dict[str, Any]) -> dict[str, Any] | None:
    for action in list_or_empty(read.get("expansion_actions")):
        if not isinstance(action, dict):
            continue
        command = str(action.get("command") or "")
        argv = list_or_empty(action.get("argv"))
        if command.endswith("read-more") or (argv and argv[0] == "read-more"):
            return action
    return None


def recovery_metadata(read: dict[str, Any]) -> dict[str, Any]:
    recovery = read.get("omitted_window_recovery") if isinstance(read.get("omitted_window_recovery"), dict) else {}
    recovery_id = str(recovery.get("recovery_id") or "")
    return {
        "kind": (
            "promoted_continuation_recovery"
            if recovery_id.startswith("clr")
            else "omitted_window_recovery"
            if recovery
            else "ordinary_next_read"
        ),
        "recovery_id": recovery.get("recovery_id"),
        "omission_reason": recovery.get("omission_reason"),
        "candidate_id": read.get("candidate_id") or recovery.get("candidate_id"),
        "metadata": recovery,
    }


def follow_next_read_key(read: dict[str, Any]) -> str | None:
    target = read.get("target")
    if target:
        return f"target:{target}"
    source_ref = read.get("source_ref")
    if isinstance(source_ref, dict):
        return f"source_ref:{json.dumps(source_ref, sort_keys=True)}"
    return None


def list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def argv_option_value(argv: list[str], option: str) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def argv_option_values(argv: list[str], option: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == option and index + 1 < len(argv):
            values.append(argv[index + 1])
            index += 2
            continue
        index += 1
    return values


def int_or_default(value: Any, default: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def parse_expansion_target(target: str | None, source_ref: dict[str, Any] | None = None) -> ParsedTarget:
    if source_ref:
        ref_target = source_ref_target(source_ref)
        line = _positive_int_or_none(source_ref.get("line"))
        return ParsedTarget(path_text=str(source_ref.get("path") or "") or None, line=line, raw_target=ref_target)
    if not target:
        return ParsedTarget(path_text=None, line=None, raw_target=None)
    path_text = str(target)
    line: int | None = None
    head, sep, tail = path_text.rpartition(":")
    if sep and tail.strip().isdigit():
        path_text = head
        line = int(tail.strip())
    return ParsedTarget(path_text=path_text or None, line=line, raw_target=target)


def source_ref_from_parsed_target(parsed: ParsedTarget) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    if parsed.path_text:
        ref["path"] = parsed.path_text
    if parsed.line is not None:
        ref["line"] = parsed.line
    return ref


def resolve_source_path(path_text: str, *, plan: SourcePlan, cwd: Path) -> Path | None:
    candidates: list[Path] = []
    raw_path = Path(path_text).expanduser()
    candidates.append(raw_path if raw_path.is_absolute() else cwd / raw_path)
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_dir() and not raw_path.is_absolute():
            candidates.append(base / raw_path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            continue
        if source_path_allowed(resolved, plan):
            return resolved
    return None


def source_path_allowed(path: Path, plan: SourcePlan) -> bool:
    for root in plan.roots:
        base = root.path.expanduser().resolve()
        if base.is_file() and path == base:
            return True
        if base.is_dir() and path_is_inside(path, base):
            return True
    return False


def select_records(
    records: list[Any],
    *,
    anchor_line: int,
    mode: str,
    scope: str,
    before: int,
    after: int,
    next_count: int,
    max_records: int,
) -> dict[str, Any]:
    total_records = len(records)
    if mode == "record":
        start = anchor_line
        end = anchor_line
    elif mode in {"window", "neighborhood"}:
        start = max(1, anchor_line - before)
        end = min(total_records, anchor_line + after)
    elif mode == "before":
        start = max(1, anchor_line - before)
        end = max(0, anchor_line - 1)
    else:
        start = min(total_records + 1, anchor_line + 1)
        end = min(total_records, anchor_line + next_count)
    requested_count = max(0, end - start + 1)
    candidate_records = records[start - 1 : end] if requested_count else []
    scoped_records = filter_records_by_scope(candidate_records, records[anchor_line - 1], scope=scope)
    scope_filtered_count = max(0, requested_count - len(scoped_records))
    if max_records > 0:
        selected = scoped_records[-max_records:] if mode == "before" else scoped_records[:max_records]
    else:
        selected = []
    if not selected:
        return {
            "records": [],
            "requested_count": requested_count,
            "range_start": None,
            "range_end": None,
            "candidate_start": start if requested_count else None,
            "candidate_end": end if requested_count else None,
            "scope_filtered_count": scope_filtered_count,
        }
    return {
        "records": selected,
        "requested_count": requested_count,
        "range_start": int(selected[0].line),
        "range_end": int(selected[-1].line),
        "candidate_start": start,
        "candidate_end": end,
        "scope_filtered_count": scope_filtered_count,
    }


def filter_records_by_scope(records: list[Any], anchor: Any, *, scope: str) -> list[Any]:
    if scope == "source":
        return list(records)
    if scope == "day":
        anchor_day = record_day(anchor)
        return [record for record in records if record_day(record) == anchor_day] if anchor_day else anchor_records_only(records, anchor)
    if scope == "session":
        anchor_session = record_session(anchor)
        return [record for record in records if record_session(record) == anchor_session] if anchor_session else anchor_records_only(records, anchor)
    return list(records)


def anchor_records_only(records: list[Any], anchor: Any) -> list[Any]:
    for record in records:
        if int(record.line) == int(anchor.line):
            return [record]
    return []


def record_day(record: Any) -> str | None:
    if record.timestamp:
        return record.timestamp.date().isoformat()
    if record.raw_timestamp:
        value = str(record.raw_timestamp)
        return value[:10] if len(value) >= 10 else None
    return None


def record_session(record: Any) -> str | None:
    raw = record.raw if isinstance(getattr(record, "raw", None), dict) else {}
    for key in ("session_id", "session"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for nested_key in ("id", "session_id"):
                nested = value.get(nested_key)
                if nested:
                    return str(nested)
    payload = raw.get("payload")
    if isinstance(payload, dict):
        for key in ("session_id", "session"):
            value = payload.get(key)
            if value:
                return str(value)
    return getattr(record, "conversation_id", None)


def build_expansion_continuation(
    *,
    source_path: str,
    anchor_line: int,
    range_start: int | None,
    range_end: int | None,
    total_records: int,
    mode: str,
    scope: str,
    before: int,
    after: int,
    next_count: int,
    max_records: int,
) -> dict[str, Any]:
    more_before = bool(range_start and range_start > 1)
    more_after = bool(range_end and range_end < total_records)
    return {
        "semantics": "Continue with bounded read-more calls from returned boundary targets; no broad search or ranking is rerun.",
        "more_before_available": more_before,
        "more_after_available": more_after,
        "scope": scope,
        "next_steps": [
            step
            for step in (
                {
                    "direction": "before",
                    "target": f"{source_path}:{range_start}",
                    "mode": "before",
                    "before": before or max_records,
                    "max_records": max_records,
                    "scope": scope,
                }
                if more_before and range_start
                else None,
                {
                    "direction": "after",
                    "target": f"{source_path}:{range_end}",
                    "mode": "next",
                    "next_count": next_count or max_records,
                    "max_records": max_records,
                    "scope": scope,
                }
                if more_after and range_end
                else None,
            )
            if step is not None
        ],
        "anchor": {"target": f"{source_path}:{anchor_line}", "line": anchor_line},
    }


def expanded_record(record: Any) -> dict[str, Any]:
    return {
        "source_ref": {
            "path": str(record.path),
            "line": int(record.line),
        },
        "target": f"{record.path}:{record.line}",
        "line": int(record.line),
        "role": record.role,
        "timestamp": record.raw_timestamp,
        "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
        "cwd": record.cwd,
        "text": record.text,
        "text_included": True,
    }


def source_scope(plan: SourcePlan) -> dict[str, Any]:
    return {
        "selected_roots": [root.to_dict() for root in plan.roots],
        "rejected_roots": [root.to_dict() for root in plan.rejected_roots],
        "root_count": len(plan.roots),
        "rejected_root_count": len(plan.rejected_roots),
    }


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed
