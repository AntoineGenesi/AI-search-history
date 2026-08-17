from __future__ import annotations

import json
import hashlib
import io
import os
import re
import shlex
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
from .agent_contract import source_ref_target
from .sources import PlannedRoot, SourcePlan, path_is_inside, source_kind_for_path


DEFAULT_WINDOW_BEFORE = 2
DEFAULT_WINDOW_AFTER = 2
DEFAULT_NEXT_COUNT = 3
DEFAULT_MAX_RECORDS = 20
LIVE_TAIL_MAX_AGE_SECONDS = 300
LIVE_TAIL_MAX_LINE_DISTANCE = 8
TERMINAL_TAIL_FOLLOWUP_WINDOW = 24
TERMINAL_CLAIM_CUE_RE = re.compile(
    r"\b(?:closeout|completion|completed|failed|failure|final answer|final response|final result|"
    r"final rerun|focused checks?|missing final|no closeout|session ended|terminal state|commit(?:ted)?)\b",
    re.IGNORECASE,
)
SUPPORTED_FOLLOW_NEXT_READ_KINDS = {"recovery", "required_primary_proof"}
DEEP_AUDIT_CONTINUATION_ALLOWED_VALUE_FLAGS = {
    "--role",
    "--audit-depth",
    "--root",
    "--codex-home",
    "--claude-home",
    "--cwd",
    "--since",
    "--until",
    "--path",
    "--max-files",
    "--max-bytes",
    "--max-matches",
    "--max-elapsed",
    "--hard-cap-files",
    "--hard-cap-bytes",
    "--deep-audit-target-contract",
    "--format",
}
DEEP_AUDIT_CONTINUATION_ALLOWED_SWITCHES = {
    "--allow-arbitrary-root",
    "--deep-audit-continuation-step",
}
RECOVERY_USABILITY_STATUSES = {
    "private_reviewed_usable",
    "reviewed_not_useful",
    "still_unreviewed",
    "review_gap",
}
REVIEWED_FOLLOWED_RECOVERY_TARGET = "answer_only_for_reviewed_usable_facets_and_state_remaining_gaps"
GEOMETRY_CAUSALITY_ORIGINAL_ROLES = {
    "original_decision_rationale",
    "original_decision",
    "original_rationale",
    "decision_rationale",
    "causal_decision",
}
GEOMETRY_CAUSALITY_LATER_ROLES = {
    "later_radius_adjustment",
    "radius_adjustment",
    "later_consequence",
    "consequence",
}
APPROVED_HISTORY_ORCHESTRATION_ROLES = {"parent_or_coordinator", "worker", "reviewer"}
APPROVED_HISTORY_ORCHESTRATION_MAX_FILES = 120
APPROVED_HISTORY_ORCHESTRATION_MAX_BYTES = 5 * 1024 * 1024
APPROVED_HISTORY_ORCHESTRATION_HANDLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+$")


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
    projection: str = "full"
    compact_text_limit: int = 1200


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

    packet_info = detect_follow_packet(report, kind=kind)
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

    reads = prioritize_follow_next_reads(
        collect_follow_next_reads(packet_info["packet"], packet_info["packet_type"], kind=kind)
    )
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
    diagnostic_only_optional_count = int(packet_info.get("diagnostic_only_optional_count") or 0)
    diagnostic_only_message = None
    if not reads and diagnostic_only_optional_count:
        diagnostic_only_message = (
            "source_followthrough_plan contains only optional_diagnostic refs; follow-next-reads "
            "does not expand optional diagnostics by default. Open them only after explicitly "
            "naming a diagnostic/completeness gap."
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
                "diagnostic_only_optional_count": diagnostic_only_optional_count,
                "diagnostic_only_message": diagnostic_only_message,
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
                "diagnostic_only_message": diagnostic_only_message,
            },
            "diagnostic_only_message": diagnostic_only_message,
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


def build_execute_continuation_payload(
    *,
    report_path: Path,
    packet_id: str | None,
    prior_receipt_path: Path | None,
    max_steps: int,
    max_output_chars: int,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
    receipt_output_path: Path | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "from_report": str(report_path),
        "packet_id": packet_id,
        "limits": {
            "max_steps": max(0, int(max_steps)),
            "max_output_chars": max(0, int(max_output_chars)),
        },
        "claim_boundaries": {
            "broad_search_rerun": False,
            "fresh_ask_allowed": False,
            "fresh_semantic_debug_allowed": False,
            "bulk_line_match_allowed": False,
            "shell_archaeology_allowed": False,
            "guessed_anchor_allowed": False,
            "answer_generation_included": False,
            "all_history_completeness_claim_supported": False,
        },
        "privacy_flags": {
            "raw_text_included": True,
            "raw_paths_included": True,
            "committed_safe_without_sanitization": False,
        },
    }
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "execute_continuation": {
                **base,
                "status": "report_not_found",
                "successor_state": "terminal_report_not_found",
                "remaining_named_gap": "saved_report_missing",
                "failure_reason": "from-report path does not exist",
            }
        }
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "execute_continuation": {
                **base,
                "status": "report_read_error",
                "successor_state": "terminal_report_read_error",
                "remaining_named_gap": "saved_report_unreadable",
                "failure_reason": str(exc),
            }
        }
    except json.JSONDecodeError as exc:
        return {
            "execute_continuation": {
                **base,
                "status": "invalid_report_json",
                "successor_state": "terminal_invalid_report_json",
                "remaining_named_gap": "saved_report_invalid_json",
                "failure_reason": str(exc),
            }
        }

    packet_info = select_continuation_packet(report, packet_id=packet_id)
    if packet_info["status"] != "selected":
        return {
            "execute_continuation": {
                **base,
                "status": packet_info["status"],
                "successor_state": "terminal_no_supported_continuation_packet",
                "remaining_named_gap": "canonical_continuation_packet_missing",
                "failure_reason": packet_info.get("failure_reason"),
                "available_packet_ids": packet_info.get("available_packet_ids", []),
            }
        }

    packet = packet_info["packet"]
    reads = prioritize_follow_next_reads(continuation_packet_follow_reads(packet))
    packet_binding = continuation_packet_binding(packet_info, packet=packet, reads=reads)
    prior_summary = validate_prior_continuation_receipt_binding(
        load_prior_continuation_receipt_summary(prior_receipt_path),
        expected_binding=packet_binding,
    )
    prior_step_identities = set(prior_summary.get("executed_step_identities") or [])
    prior_step_states = [
        item for item in prior_summary.get("executed_step_states") or [] if isinstance(item, dict)
    ]
    pending_reads = [
        read
        for read in reads
        if continuation_step_identity(read) not in prior_step_identities
    ]
    selected_reads = pending_reads[: max(0, int(max_steps))]
    approved_history_budget = approved_history_budget_from_step_states(prior_step_states)
    expanded_reads = []
    for index, read in enumerate(selected_reads):
        expanded = execute_continuation_step(
            read=read,
            rank=index + 1,
            plan=plan,
            default_tz=default_tz,
            cwd=cwd,
            max_output_chars=max(0, int(max_output_chars)),
            approved_history_budget=approved_history_budget,
        )
        expanded_reads.append(expanded)
        approved_history_budget = approved_history_budget_after_step(approved_history_budget, expanded)
    counts = follow_next_read_counts(selected_reads, expanded_reads)
    selected_step_identities = [continuation_step_identity(read) for read in selected_reads]
    new_executed_step_states = continuation_executed_step_states(expanded_reads)
    executed_step_states = prior_step_states + new_executed_step_states
    executed_step_identities = list(
        dict.fromkeys(list(prior_summary.get("executed_step_identities") or []) + selected_step_identities)
    )
    remaining_after_count = max(0, len(reads) - len(executed_step_identities))
    geometry_gate = build_geometry_causality_gate(
        expanded_reads,
        prior_evidence_states=prior_geometry_causality_states(reads, prior_summary),
    )
    remaining_named_gap = continuation_remaining_named_gap(
        packet=packet,
        selected_reads=selected_reads,
        expanded_reads=expanded_reads,
        geometry_gate=geometry_gate,
    )
    repeated_exhaustion = bool(
        prior_summary.get("status") == "loaded"
        and prior_summary.get("remaining_named_gap") == remaining_named_gap
        and not selected_reads
        and len(reads) > 0
        and len(prior_step_identities) >= len(reads)
    )
    successor_state = continuation_successor_state(
        available_count=len(reads),
        selected_count=len(selected_reads),
        counts=counts,
        geometry_gate=geometry_gate,
        repeated_exhaustion=repeated_exhaustion,
        remaining_after_count=remaining_after_count,
    )
    receipt = {
        **base,
        "status": "executed",
        "successor_state": successor_state,
        "remaining_named_gap": remaining_named_gap,
        "packet": {
            "packet_id": packet_info.get("packet_id"),
            "canonical_packet_fingerprint": packet_binding.get("canonical_packet_fingerprint"),
            "surface": packet_info.get("surface"),
            "status": packet.get("status"),
            "declared_named_gap": packet.get("named_gap") or packet.get("remaining_named_gap") or packet.get("gap_name"),
        },
        "packet_binding": packet_binding,
        "selection": {
            "available_structured_step_count": len(reads),
            "prior_executed_step_count": len(prior_step_identities),
            "pending_structured_step_count": len(pending_reads),
            "selected_count": len(selected_reads),
            "expanded_count": counts["expanded_count"],
            "failed_count": counts["failed_count"],
            "limit_applied": max(0, int(max_steps)),
            "unselected_step_count": max(0, len(pending_reads) - len(selected_reads)),
            "remaining_after_run_count": remaining_after_count,
            "priority_order": [
                "geometry_original_decision_rationale",
                "required_primary_proof",
                "conditional_required",
                "promoted_continuation_recovery_clr",
                "omitted_window_recovery",
                "ordinary_next_read",
            ],
        },
        "packet_progress": {
            "schema_version": 1,
            "packet_binding": packet_binding,
            "status": "advanced" if selected_reads else "exhausted",
            "total_step_count": len(reads),
            "prior_executed_step_count": len(prior_step_identities),
            "newly_selected_step_identities": selected_step_identities,
            "executed_step_identities": executed_step_identities,
            "executed_step_states": executed_step_states,
            "remaining_step_count": remaining_after_count,
            "prior_receipt_consumed": prior_summary.get("status") == "loaded",
            "approved_history_cumulative_budget": approved_history_budget,
        },
        "geometry_causality_gate": geometry_gate,
        "reviewed_follow_through_answer_gate": build_reviewed_follow_through_answer_gate(counts, {**base, **counts}),
        "searched_scope": continuation_searched_scope(reads, selected_reads, expanded_reads, plan),
        "residual_unknowns": continuation_residual_unknowns(
            available_count=len(reads),
            selected_count=len(selected_reads),
            counts=counts,
            remaining_named_gap=remaining_named_gap,
            geometry_gate=geometry_gate,
            failed_reads=failed_follow_next_read_summaries(expanded_reads),
        ),
        "failed_reads": failed_follow_next_read_summaries(expanded_reads),
        "executed_steps": expanded_reads,
        "prior_receipt": prior_summary,
        "agent_instruction": {
            "required_next_action": "answer_only_if_geometry_gate_and_reviewed_gate_allow_named_gap_closure",
            "do_not_do": [
                "do_not_rerun_ask",
                "do_not_rerun_semantic_debug",
                "do_not_run_bulk_line_match",
                "do_not_search_shell_history",
                "do_not_guess_new_anchors",
            ],
            "terminal_rule": (
                "If selected structured steps are exhausted, name searched_scope and residual_unknowns; "
                "do not widen beyond the saved packet without a new explicit packet."
            ),
        },
    }
    if receipt_output_path is not None:
        receipt["report_output"] = str(receipt_output_path)
    return {"execute_continuation": receipt}


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
    now_utc: datetime | None = None,
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
            "compact_text_limit": max(0, int(request.compact_text_limit)),
        },
        "projection": request.projection,
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
    if request.projection not in {"full", "compact"}:
        return {"source_expansion": {**base_payload, "status": "invalid_projection", "records": []}}
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
                "canonical_ref_fallback": canonical_ref_fallback(parsed, plan=plan, cwd=cwd),
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
        "live_tail_caveat": build_source_expansion_live_tail_caveat(
            source_path=source_path,
            records=records,
            observed_range_end=range_end,
            now_utc=now_utc,
        ),
        "source_continuation": build_source_expansion_terminal_tail_followup(
            source_path=source_path,
            selected_records=selected,
            observed_range_end=range_end,
            total_records=total_records,
        ),
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
        "projection_summary": source_expansion_projection_summary(
            selected,
            projection=request.projection,
            compact_text_limit=max(0, int(request.compact_text_limit)),
        ),
        "records": [
            expanded_record(
                record,
                projection=request.projection,
                compact_text_limit=max(0, int(request.compact_text_limit)),
            )
            for record in selected
        ],
        "scope": source_scope(plan),
    }
    live_tail_caveat = payload["live_tail_caveat"]
    if live_tail_caveat.get("applies"):
        payload["claim_boundaries"] = {
            **payload["claim_boundaries"],
            **live_tail_caveat["claim_boundaries"],
            "in_flight_or_live_tail": True,
            "terminal_state_unproven": True,
            "terminal_session_conclusion": "not_allowed_from_inspected_live_tail_window",
        }
    source_continuation = payload["source_continuation"]
    if source_continuation.get("terminal_claim_requires_tail_followup"):
        payload["claim_boundaries"] = {
            **payload["claim_boundaries"],
            **source_continuation["claim_boundaries"],
            "source_continues_beyond_opened_window": True,
            "terminal_claim_requires_tail_followup": True,
            "terminal_session_conclusion": "not_allowed_until_current_tail_is_inspected",
        }
    return {"source_expansion": payload}


def build_source_expansion_terminal_tail_followup(
    *,
    source_path: Path,
    selected_records: list[Any],
    observed_range_end: int | None,
    total_records: int,
) -> dict[str, Any]:
    lines_beyond_window = (
        max(0, total_records - observed_range_end) if observed_range_end is not None else 0
    )
    source_continues = lines_beyond_window > LIVE_TAIL_MAX_LINE_DISTANCE
    terminal_contract = source_expansion_window_has_terminal_claim_contract(selected_records)
    requires_followup = bool(source_continues and terminal_contract)
    tail_target = f"{source_path}:{total_records}" if total_records else None
    command = None
    if requires_followup and tail_target:
        before = min(TERMINAL_TAIL_FOLLOWUP_WINDOW, max(0, total_records - 1))
        try:
            hard_cap_bytes = max(20_000, source_path.stat().st_size + 1024)
        except OSError:
            hard_cap_bytes = 20_000
        command = (
            "jsonl-history-search read-more "
            f"{shlex.quote(tail_target)} --mode window --before {before} --after 0 "
            f"--max-records {before + 1} --projection compact --format text "
            f"--root {shlex.quote(str(source_path.parent))} --allow-arbitrary-root "
            f"--hard-cap-files 1 --hard-cap-bytes {hard_cap_bytes}"
        )
    return {
        "schema_version": 1,
        "applies": requires_followup,
        "status": (
            "source_continues_beyond_opened_window"
            if requires_followup
            else "no_terminal_tail_followup_required"
        ),
        "terminal_claim_status": (
            "terminal_claim_requires_tail_followup" if requires_followup else "not_flagged"
        ),
        "terminal_claim_requires_tail_followup": requires_followup,
        "terminal_claim_contract_detected": terminal_contract,
        "opened_window_end_line": observed_range_end,
        "observed_current_tail_line": total_records,
        "lines_beyond_opened_window": lines_beyond_window,
        "tail_distance_threshold": LIVE_TAIL_MAX_LINE_DISTANCE,
        "exact_next_read_more_command": command,
        "tail_target": tail_target if requires_followup else None,
        "required_before_terminal_claim": (
            "Open the current transcript tail with exact_next_read_more_command before using absence of a "
            "closeout/final response to support a terminal-session or actual-failure conclusion."
            if requires_followup
            else None
        ),
        "claim_boundaries": {
            "absence_of_closeout_or_final_response": (
                "not_proof_while_source_continues_beyond_opened_window"
                if requires_followup
                else "not_assessed"
            ),
            "session_ended": "not_proven" if requires_followup else "not_assessed",
            "missing_closeout_is_actual_failure": "not_proven" if requires_followup else "not_assessed",
            "positive_facts_in_inspected_window": "allowed_with_source_support",
        },
        "signal_committed_safe": True,
        "raw_text_included": False,
        "local_runtime_action_contains_source_ref": bool(command),
    }


def source_expansion_window_has_terminal_claim_contract(records: list[Any]) -> bool:
    return any(
        TERMINAL_CLAIM_CUE_RE.search(str(getattr(record, "text", "") or ""))
        for record in records
    )


def build_source_expansion_live_tail_caveat(
    *,
    source_path: Path,
    records: list[Any],
    observed_range_end: int | None,
    now_utc: datetime | None,
) -> dict[str, Any]:
    now = _source_expansion_aware_utc(now_utc or datetime.now(timezone.utc))
    last_record = records[-1] if records else None
    total_records = len(records)
    lines_from_tail = (
        max(0, total_records - observed_range_end) if observed_range_end is not None else None
    )
    near_tail = lines_from_tail is not None and lines_from_tail <= LIVE_TAIL_MAX_LINE_DISTANCE
    try:
        mtime = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        mtime = None
    last_timestamp = (
        _source_expansion_aware_utc(last_record.timestamp)
        if last_record is not None and last_record.timestamp
        else None
    )
    mtime_age_seconds = round((now - mtime).total_seconds(), 3) if mtime else None
    last_record_age_seconds = (
        round((now - last_timestamp).total_seconds(), 3) if last_timestamp else None
    )
    mtime_near_query = _source_expansion_age_is_near_query(mtime_age_seconds)
    last_record_near_query = _source_expansion_age_is_near_query(last_record_age_seconds)
    applies = bool(near_tail and (mtime_near_query or last_record_near_query))
    required_caveat = (
        "LIVE-TAIL CAVEAT: absence of a closeout/final response proves only that it was not present in this "
        "inspected window at query time. The transcript may still have been in flight; this does not prove "
        "the session ended or that missing closeout was the actual failure."
    )
    return {
        "schema_version": 1,
        "applies": applies,
        "status": "in_flight_or_live_tail" if applies else "no_live_tail_detected",
        "terminal_state_status": "terminal_state_unproven" if applies else "not_flagged",
        "query_time_utc": now.isoformat(),
        "freshness_threshold_seconds": LIVE_TAIL_MAX_AGE_SECONDS,
        "tail_line_distance_threshold": LIVE_TAIL_MAX_LINE_DISTANCE,
        "lines_from_observed_tail": lines_from_tail,
        "near_observed_tail": near_tail,
        "file_mtime_utc": mtime.isoformat() if mtime else None,
        "file_mtime_age_seconds": mtime_age_seconds,
        "last_record_timestamp_utc": last_timestamp.isoformat() if last_timestamp else None,
        "last_record_age_seconds": last_record_age_seconds,
        "mtime_near_query_time": mtime_near_query,
        "last_record_timestamp_near_query_time": last_record_near_query,
        "required_caveat": required_caveat if applies else None,
        "claim_boundaries": {
            "absence_of_closeout_or_final_response": (
                "not_present_in_inspected_window_at_query_time_only" if applies else "not_assessed"
            ),
            "session_ended": "not_proven" if applies else "not_assessed",
            "missing_closeout_is_actual_failure": "not_proven" if applies else "not_assessed",
            "positive_facts_in_inspected_window": "allowed_with_source_support",
        },
        "committed_safe": True,
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def _source_expansion_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_expansion_age_is_near_query(age_seconds: float | None) -> bool:
    return age_seconds is not None and -60 <= age_seconds <= LIVE_TAIL_MAX_AGE_SECONDS


def detect_follow_packet(report: Any, *, kind: str = "recovery") -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"status": "unsupported_report_shape", "failure_reason": "report JSON must be an object"}
    candidates: list[tuple[str, Any]] = []
    if "semantic_debug" in report:
        candidates.append(("semantic_debug", report.get("semantic_debug")))
    if "agent_find" in report:
        candidates.append(("agent_find", report.get("agent_find")))
    if kind == "required_primary_proof" and "semantic_wrapup" in report:
        candidates.append(("semantic_wrapup", report.get("semantic_wrapup")))
    for packet_type, packet in candidates:
        if not isinstance(packet, dict):
            continue
        surfaces = follow_next_read_surfaces(packet, packet_type, kind=kind)
        if surfaces:
            return {
                "status": "supported",
                "packet_type": packet_type,
                "packet": packet,
                "schema_version": packet.get("schema_version"),
                "candidate_surfaces": [surface for surface, _reads in surfaces],
                "diagnostic_only_optional_count": (
                    source_followthrough_plan_optional_diagnostic_count(
                        packet.get("source_followthrough_plan") or {}
                    )
                    if packet_type == "semantic_debug"
                    else 0
                ),
            }
    if candidates:
        return {
            "status": "stale_or_missing_next_reads",
            "failure_reason": "recognized packet type does not include current next_reads surfaces",
        }
    expected = "semantic_wrapup" if kind == "required_primary_proof" else "semantic_debug or agent_find"
    return {
        "status": "unsupported_report_shape",
        "failure_reason": f"expected top-level {expected} packet",
    }


def follow_next_read_surfaces(
    packet: dict[str, Any],
    packet_type: str,
    *,
    kind: str = "recovery",
) -> list[tuple[str, list[Any]]]:
    surfaces: list[tuple[str, list[Any]]] = []
    if packet_type == "semantic_debug":
        source_followthrough_plan = packet.get("source_followthrough_plan")
        if isinstance(source_followthrough_plan, dict):
            planned_reads = source_followthrough_plan_follow_reads(source_followthrough_plan)
            surfaces.append(("semantic_debug.source_followthrough_plan", planned_reads))
            return surfaces
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
    elif packet_type == "semantic_wrapup" and kind == "required_primary_proof":
        plan = packet.get("source_followthrough_plan") or {}
        reads = [
            {**item, "_source_followthrough_group": "required_primary_proof"}
            for item in list_or_empty(plan.get("required_primary_proof"))
            if isinstance(item, dict)
            and item.get("read_role") == "required_primary_proof"
            and item.get("read_more_actionable") is not False
            and item.get("diagnostic_only") is not True
        ]
        surfaces.append(("semantic_wrapup.source_followthrough_plan.required_primary_proof", reads))
    return surfaces


def source_followthrough_plan_follow_reads(plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    required = [
        {**item, "_source_followthrough_group": "required_primary_proof"}
        for item in list_or_empty(plan.get("required_primary_proof"))
        if isinstance(item, dict)
    ]
    conditional = [
        {**item, "_source_followthrough_group": "conditional_required"}
        for item in list_or_empty(plan.get("conditional_required"))
        if isinstance(item, dict)
    ]
    return required + conditional


def source_followthrough_plan_optional_diagnostic_count(plan: dict[str, Any]) -> int:
    if not isinstance(plan, dict):
        return 0
    return sum(1 for item in list_or_empty(plan.get("optional_diagnostic")) if isinstance(item, dict))


def append_next_read_surface(surfaces: list[tuple[str, list[Any]]], label: str, container: Any) -> None:
    if isinstance(container, dict) and "next_reads" in container:
        surfaces.append((label, list_or_empty(container.get("next_reads"))))


def collect_follow_next_reads(
    packet: dict[str, Any],
    packet_type: str,
    *,
    kind: str = "recovery",
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    by_key: dict[str, int] = {}
    for surface, surface_reads in follow_next_read_surfaces(packet, packet_type, kind=kind):
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
    read_role = str(read.get("read_role") or read.get("_source_followthrough_group") or "")
    default_actionable = read.get("default_actionable") is True
    if read_role in GEOMETRY_CAUSALITY_ORIGINAL_ROLES:
        role_tier = 0
    elif read_role == "required_primary_proof" and default_actionable:
        role_tier = 0
    elif read_role in GEOMETRY_CAUSALITY_LATER_ROLES:
        role_tier = 3
    elif read_role == "required_primary_proof":
        role_tier = 1
    elif read_role == "conditional_required":
        role_tier = 2
    elif read_role == "optional_diagnostic":
        role_tier = 9
    else:
        role_tier = 4
    raw_recovery = read.get("omitted_window_recovery")
    recovery = raw_recovery if isinstance(raw_recovery, dict) else {}
    recovery_id = str(recovery.get("recovery_id") or "")
    if recovery_id.startswith("clr"):
        tier = min(role_tier, 2)
    elif raw_recovery:
        tier = min(role_tier, 3)
    else:
        tier = role_tier
    return (tier, 0 if read.get("target") else 1, str(read.get("target") or ""))


def select_continuation_packet(report: Any, *, packet_id: str | None = None) -> dict[str, Any]:
    packets = find_continuation_packets(report)
    if not packets:
        return {
            "status": "no_continuation_packet",
            "failure_reason": "expected canonical_continuation_packet, next_proof_step, or continuation_packet with structured reads",
        }
    available_ids = [str(item.get("packet_id")) for item in packets if item.get("packet_id")]
    if packet_id:
        for item in packets:
            if str(item.get("packet_id") or "") == packet_id:
                return {"status": "selected", **item}
        return {
            "status": "packet_id_not_found",
            "failure_reason": f"packet_id not found: {packet_id}",
            "available_packet_ids": available_ids,
        }
    return {"status": "selected", **packets[0], "available_packet_ids": available_ids}


def continuation_packet_binding(packet_info: dict[str, Any], *, packet: dict[str, Any], reads: list[dict[str, Any]]) -> dict[str, Any]:
    packet_id_value = packet_info.get("packet_id")
    packet_id = str(packet_id_value).strip() if packet_id_value is not None else ""
    canonical = canonical_continuation_packet_for_fingerprint(packet, reads)
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "canonicalization": "jsonl-history-search.continuation-packet.v1",
        "packet_id": packet_id or None,
        "canonical_packet_fingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
    }


def canonical_continuation_packet_for_fingerprint(
    packet: dict[str, Any],
    reads: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "packet": {
            "packet_id": packet.get("packet_id"),
            "packet_kind": packet.get("packet_kind"),
            "status": packet.get("status"),
            "named_gap": packet.get("named_gap"),
            "remaining_named_gap": packet.get("remaining_named_gap"),
            "gap_name": packet.get("gap_name"),
            "continuation_kind": packet.get("continuation_kind"),
            "task_handle": packet.get("task_handle"),
            "approved_roots": packet.get("approved_roots"),
            "approved_source_plan_fingerprint": packet.get("approved_source_plan_fingerprint"),
            "inherited_caps": packet.get("inherited_caps"),
        },
        "structured_steps": [
            {
                "canonical_order": index,
                "candidate_id": read.get("candidate_id"),
                "step_id": read.get("step_id"),
                "step_identity": read.get("step_identity"),
                "read_role": read.get("read_role") or read.get("role") or read.get("target_source_role"),
                "target": read.get("target"),
                "source_ref": read.get("source_ref"),
                "mode": read.get("mode"),
                "scope": read.get("scope"),
                "before": read.get("before"),
                "after": read.get("after"),
                "next_count": read.get("next_count"),
                "max_records": read.get("max_records"),
                "projection": read.get("projection"),
                "named_gap": read.get("named_gap"),
                "remaining_named_gap": read.get("remaining_named_gap"),
                "command": read.get("command"),
                "argv": read.get("argv"),
                "step_kind": read.get("step_kind"),
                "task_handle": read.get("task_handle"),
                "required_roles": read.get("required_roles"),
                "approved_root": read.get("approved_root"),
                "approved_source_plan_fingerprint": read.get("approved_source_plan_fingerprint"),
                "inherited_caps": read.get("inherited_caps"),
                "source_followthrough_group": read.get("_source_followthrough_group"),
                "follow_surface": read.get("_follow_surface") or read.get("surface"),
            }
            for index, read in enumerate(reads, 1)
        ],
    }


def validate_prior_continuation_receipt_binding(
    prior_summary: dict[str, Any],
    *,
    expected_binding: dict[str, Any],
) -> dict[str, Any]:
    if prior_summary.get("status") != "loaded":
        return {
            **prior_summary,
            "packet_binding_validation": {
                "status": "not_applicable",
                "reason": f"prior_receipt_{prior_summary.get('status')}",
            },
        }

    expected_packet_id = str(expected_binding.get("packet_id") or "").strip()
    expected_fingerprint = str(expected_binding.get("canonical_packet_fingerprint") or "").strip()
    if not expected_packet_id or not expected_fingerprint:
        return rejected_prior_continuation_receipt_summary(
            prior_summary,
            status="binding_malformed",
            reason="selected continuation packet has no valid packet_id or canonical fingerprint",
            expected_binding=expected_binding,
        )

    actual_binding = prior_summary.get("packet_binding")
    if not isinstance(actual_binding, dict):
        return rejected_prior_continuation_receipt_summary(
            prior_summary,
            status="binding_missing",
            reason="prior receipt does not include packet_binding",
            expected_binding=expected_binding,
            actual_binding=actual_binding,
        )
    actual_packet_id = str(actual_binding.get("packet_id") or "").strip()
    actual_fingerprint = str(actual_binding.get("canonical_packet_fingerprint") or "").strip()
    if not actual_packet_id or not actual_fingerprint:
        return rejected_prior_continuation_receipt_summary(
            prior_summary,
            status="binding_malformed",
            reason="prior receipt packet_binding is missing packet_id or canonical_packet_fingerprint",
            expected_binding=expected_binding,
            actual_binding=actual_binding,
        )
    if actual_packet_id != expected_packet_id:
        return rejected_prior_continuation_receipt_summary(
            prior_summary,
            status="binding_packet_id_mismatch",
            reason="prior receipt packet_id does not match selected continuation packet",
            expected_binding=expected_binding,
            actual_binding=actual_binding,
        )
    if actual_fingerprint != expected_fingerprint:
        return rejected_prior_continuation_receipt_summary(
            prior_summary,
            status="binding_fingerprint_mismatch",
            reason="prior receipt canonical packet fingerprint does not match selected continuation packet",
            expected_binding=expected_binding,
            actual_binding=actual_binding,
        )
    return {
        **prior_summary,
        "packet_binding_validation": {
            "status": "valid",
            "packet_id": expected_packet_id,
            "canonical_packet_fingerprint": expected_fingerprint,
        },
    }


def rejected_prior_continuation_receipt_summary(
    prior_summary: dict[str, Any],
    *,
    status: str,
    reason: str,
    expected_binding: dict[str, Any],
    actual_binding: Any | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "failure_reason": reason,
        "successor_state": prior_summary.get("successor_state"),
        "remaining_named_gap": prior_summary.get("remaining_named_gap"),
        "executed_step_identities": [],
        "executed_step_states": [],
        "packet_binding_validation": {
            "status": status,
            "reason": reason,
            "expected_packet_id": expected_binding.get("packet_id"),
            "expected_canonical_packet_fingerprint": expected_binding.get("canonical_packet_fingerprint"),
            "actual_packet_id": actual_binding.get("packet_id") if isinstance(actual_binding, dict) else None,
            "actual_canonical_packet_fingerprint": (
                actual_binding.get("canonical_packet_fingerprint") if isinstance(actual_binding, dict) else None
            ),
            "prior_proof_consumed": False,
        },
    }


def find_continuation_packets(node: Any, *, surface: str = "$") -> list[dict[str, Any]]:
    packets: list[dict[str, Any]] = []
    if isinstance(node, dict):
        for key in ("canonical_continuation_packet", "next_proof_step", "continuation_packet"):
            value = node.get(key)
            if isinstance(value, dict) and continuation_packet_follow_reads(value):
                packets.append(
                    {
                        "packet": value,
                        "packet_id": value.get("packet_id"),
                        "surface": f"{surface}.{key}",
                    }
                )
        if continuation_packet_follow_reads(node) and (
            str(node.get("packet_kind") or "").startswith("canonical_continuation")
            or node.get("steps")
            or node.get("continuation_steps")
        ):
            packets.append(
                {
                    "packet": node,
                    "packet_id": node.get("packet_id"),
                    "surface": surface,
                }
            )
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                packets.extend(find_continuation_packets(value, surface=f"{surface}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, (dict, list)):
                packets.extend(find_continuation_packets(value, surface=f"{surface}[{index}]"))
    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in packets:
        identity = id(item["packet"])
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(item)
    return deduped


def continuation_packet_follow_reads(packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(packet, dict):
        return []
    reads: list[dict[str, Any]] = []

    def add_group(group: Any, group_name: str) -> None:
        for index, item in enumerate(list_or_empty(group), 1):
            read = continuation_step_to_follow_read(item, group_name=group_name, index=index, packet=packet)
            if read:
                reads.append(read)

    add_group(packet.get("required_primary_proof"), "required_primary_proof")
    add_group(packet.get("conditional_required"), "conditional_required")
    add_group(packet.get("next_reads"), "next_reads")
    add_group(packet.get("required_next_reads"), "required_next_reads")
    add_group(packet.get("read"), "read")
    add_group(packet.get("steps"), "steps")
    add_group(packet.get("continuation_steps"), "continuation_steps")
    by_key: dict[str, dict[str, Any]] = {}
    for read in reads:
        key = follow_next_read_key(read)
        if not key:
            continue
        if key in by_key:
            by_key[key] = merge_duplicate_follow_next_read(by_key[key], read)
        else:
            by_key[key] = read
    ordered_reads = list(by_key.values())
    for read in ordered_reads:
        read["step_identity"] = continuation_step_identity(read)
    return ordered_reads


def continuation_step_to_follow_read(
    item: Any,
    *,
    group_name: str,
    index: int,
    packet: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if item.get("diagnostic_only") is True or item.get("optional") is True:
        return None
    target = item.get("target") or item.get("openable_target")
    source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else None
    argv = item.get("argv") if isinstance(item.get("argv"), list) else []
    command = str(item.get("command") or "")
    approved_history_validation = approved_history_orchestration_step_validation(item, packet=packet)
    if approved_history_validation["status"] != "not_approved_history_orchestration":
        return {
            **item,
            "target": None,
            "source_ref": None,
            "read_role": "approved_history_orchestration_roles",
            "_continuation_step_kind": "approved_history_orchestration_roles",
            "_approved_history_validation": approved_history_validation,
            "_packet": packet,
            "_source_followthrough_group": group_name,
            "_follow_surface": f"canonical_continuation_packet.{group_name}",
            "candidate_id": item.get("step_id") or f"{group_name}-{index}",
            "named_gap": item.get("named_gap") or packet.get("named_gap"),
        }
    semantic_debug_validation = deep_audit_continuation_argv_validation(argv)
    if semantic_debug_validation["status"] != "not_deep_audit_semantic_debug":
        read_role = str(item.get("read_role") or item.get("role") or item.get("target_source_role") or group_name)
        return {
            **item,
            "target": None,
            "source_ref": None,
            "read_role": read_role,
            "_continuation_step_kind": "deep_audit_semantic_debug",
            "_deep_audit_argv_validation": semantic_debug_validation,
            "_source_followthrough_group": group_name,
            "_follow_surface": f"canonical_continuation_packet.{group_name}",
            "candidate_id": item.get("candidate_id") or item.get("step_id") or item.get("command_id") or f"{group_name}-{index}",
            "named_gap": item.get("named_gap")
            or item.get("remaining_named_gap")
            or packet.get("named_gap")
            or packet.get("remaining_named_gap")
            or packet.get("gap_name"),
        }
    if not target and not source_ref and argv:
        target = continuation_target_from_argv(argv)
    if not target and not source_ref and command:
        target = continuation_target_from_command(command)
    if not target and not source_ref:
        return None
    read_role = str(item.get("read_role") or item.get("role") or item.get("target_source_role") or group_name)
    return {
        **item,
        "target": target,
        "source_ref": source_ref,
        "read_role": read_role,
        "_source_followthrough_group": group_name,
        "_follow_surface": f"canonical_continuation_packet.{group_name}",
        "candidate_id": item.get("candidate_id") or item.get("step_id") or f"{group_name}-{index}",
        "named_gap": item.get("named_gap")
        or item.get("remaining_named_gap")
        or packet.get("named_gap")
        or packet.get("remaining_named_gap")
        or packet.get("gap_name"),
    }


def approved_history_orchestration_step_validation(item: dict[str, Any], *, packet: dict[str, Any]) -> dict[str, Any]:
    """Allow only the generated, root-bound historical-role packet shape."""
    if str(item.get("step_kind") or "") != "approved_history_orchestration_roles":
        return {"status": "not_approved_history_orchestration"}
    if packet.get("packet_kind") != "approved_history_orchestration_roles.v1" or packet.get("continuation_kind") != "approved_history_orchestration_roles":
        return {"status": "rejected", "reason": "approved-history continuation kind is unsupported"}
    if item.get("argv") or item.get("command"):
        return {"status": "rejected", "reason": "approved-history continuation cannot carry argv or shell command text"}
    task_handle = str(item.get("task_handle") or "")
    if not APPROVED_HISTORY_ORCHESTRATION_HANDLE_RE.fullmatch(task_handle) or task_handle != str(packet.get("task_handle") or ""):
        return {"status": "rejected", "reason": "approved-history continuation task handle is invalid or unbound"}
    roles = item.get("required_roles")
    if not isinstance(roles, list) or not roles or any(role not in APPROVED_HISTORY_ORCHESTRATION_ROLES for role in roles) or roles != packet.get("required_roles"):
        return {"status": "rejected", "reason": "approved-history continuation roles are invalid or unbound"}
    roots = packet.get("approved_roots")
    root = item.get("approved_root")
    if not isinstance(roots, list) or not roots or not isinstance(root, str):
        return {"status": "rejected", "reason": "approved-history continuation requires packet-bound roots"}
    normalized_roots = [str(Path(str(value)).expanduser().resolve(strict=False)) for value in roots if isinstance(value, str)]
    normalized_root = str(Path(root).expanduser().resolve(strict=False))
    if len(normalized_roots) != len(roots) or len(set(normalized_roots)) != len(normalized_roots) or normalized_root != root or normalized_root not in normalized_roots:
        return {"status": "rejected", "reason": "approved-history continuation root is malformed or widened"}
    fingerprint = hashlib.sha256(json.dumps(normalized_roots, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if item.get("approved_source_plan_fingerprint") != fingerprint or packet.get("approved_source_plan_fingerprint") != fingerprint:
        return {"status": "rejected", "reason": "approved-history continuation source-plan fingerprint does not match roots"}
    caps = item.get("inherited_caps")
    if not isinstance(caps, dict) or caps != packet.get("inherited_caps"):
        return {"status": "rejected", "reason": "approved-history continuation caps are invalid or unbound"}
    try:
        max_files, max_bytes = int(caps["max_files"]), int(caps["max_bytes"])
    except (KeyError, TypeError, ValueError):
        return {"status": "rejected", "reason": "approved-history continuation caps are malformed"}
    if not (0 < max_files <= APPROVED_HISTORY_ORCHESTRATION_MAX_FILES and 0 < max_bytes <= APPROVED_HISTORY_ORCHESTRATION_MAX_BYTES):
        return {"status": "rejected", "reason": "approved-history continuation attempts to widen inherited caps"}
    return {"status": "allowed", "execution_contract": "packet_owned_approved_history_orchestration_roles"}


def deep_audit_continuation_argv_validation(argv: list[Any]) -> dict[str, Any]:
    """Accept only the serialized deep-audit semantic-debug step contract.

    A canonical continuation packet is untrusted input at the CLI boundary.  The
    packet binding protects resumptions, while this parser limits first use to
    the one semantic-debug shape produced by the deep-audit packet builder.
    """
    parts = [str(part) for part in argv]
    if len(parts) < 5 or parts[:2] != ["jsonl-history-search", "semantic-debug"]:
        return {"status": "not_deep_audit_semantic_debug"}
    query = parts[2]
    if not query or query.startswith("-"):
        return {"status": "rejected", "reason": "semantic-debug continuation requires one non-option query"}

    index = 3
    seen_deep_step = 0
    seen_audit_depth = 0
    seen_target_contract = 0
    seen_roots = 0
    seen_format = 0
    format_value = None
    while index < len(parts):
        token = parts[index]
        if token in DEEP_AUDIT_CONTINUATION_ALLOWED_SWITCHES:
            if token == "--deep-audit-continuation-step":
                seen_deep_step += 1
            index += 1
            continue
        if token not in DEEP_AUDIT_CONTINUATION_ALLOWED_VALUE_FLAGS:
            return {"status": "rejected", "reason": f"unsupported deep-audit continuation option: {token}"}
        if index + 1 >= len(parts) or parts[index + 1].startswith("--"):
            return {"status": "rejected", "reason": f"missing value for deep-audit continuation option: {token}"}
        value = parts[index + 1]
        if token == "--format":
            seen_format += 1
            format_value = value
        if token == "--audit-depth":
            seen_audit_depth += 1
            if value not in {"in-depth", "exhaustive"}:
                return {"status": "rejected", "reason": "deep-audit continuation audit depth must be in-depth or exhaustive"}
        if token == "--root":
            seen_roots += 1
        if token == "--deep-audit-target-contract":
            seen_target_contract += 1
            try:
                if not isinstance(json.loads(value), dict):
                    raise ValueError("target contract is not an object")
            except (TypeError, ValueError, json.JSONDecodeError):
                return {"status": "rejected", "reason": "deep-audit target contract must be a JSON object"}
        index += 2
    if seen_deep_step != 1:
        return {"status": "rejected", "reason": "deep-audit continuation marker must appear exactly once"}
    if seen_audit_depth != 1:
        return {"status": "rejected", "reason": "deep-audit continuation requires exactly one audit-depth"}
    if seen_target_contract != 1:
        return {"status": "rejected", "reason": "deep-audit continuation requires exactly one preserved target contract"}
    if seen_roots < 1:
        return {"status": "rejected", "reason": "deep-audit continuation requires at least one serialized root"}
    if seen_format != 1 or format_value != "json":
        return {"status": "rejected", "reason": "deep-audit continuation output must be JSON"}
    return {
        "status": "allowed",
        "command_family": "semantic-debug",
        "execution_contract": "packet_owned_deep_audit_continuation_step",
    }


def continuation_target_from_argv(argv: list[Any]) -> str | None:
    parts = [str(part) for part in argv]
    if len(parts) >= 3 and parts[0] == "jsonl-history-search" and parts[1] == "read-more":
        return parts[2]
    if len(parts) >= 2 and parts[0] == "read-more":
        return parts[1]
    return None


def continuation_target_from_command(command: str) -> str | None:
    try:
        return continuation_target_from_argv(shlex.split(command))
    except ValueError:
        return None


def continuation_step_identity(read: dict[str, Any]) -> str:
    explicit = read.get("step_identity")
    if explicit:
        return str(explicit)
    identity_packet = {
        "candidate_id": read.get("candidate_id"),
        "step_id": read.get("step_id"),
        "target": read.get("target"),
        "source_ref": read.get("source_ref"),
        "argv": read.get("argv") if read.get("_continuation_step_kind") else None,
        "continuation_step_kind": read.get("_continuation_step_kind"),
        "read_role": read.get("read_role") or read.get("role") or read.get("target_source_role"),
        "surface": read.get("_follow_surface") or read.get("surface"),
    }
    return hashed_handle("continuation-step", json.dumps(identity_packet, sort_keys=True, default=str))


def continuation_executed_step_states(expanded_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for item in expanded_reads:
        states.append(
            {
                "step_identity": item.get("step_identity"),
                "candidate_id": item.get("candidate_id"),
                "read_role": item.get("read_role"),
                "continuation_step_kind": item.get("continuation_step_kind"),
                "status": item.get("status"),
                "remaining_named_gap": item.get("named_gap"),
                "target_handle": hashed_handle("target", item.get("target")),
                "source_handle": hashed_handle(
                    "source",
                    json.dumps(item.get("source_ref"), sort_keys=True, default=str)
                    if isinstance(item.get("source_ref"), dict)
                    else None,
                ),
                "approved_history_packet_budget": (
                    {
                        "files_examined": (item.get("searched_scope") or {}).get("files_examined", 0),
                        "bytes_examined": (item.get("searched_scope") or {}).get("bytes_examined", 0),
                    }
                    if item.get("continuation_step_kind") == "approved_history_orchestration_roles"
                    else None
                ),
            }
        )
    return states


def prior_geometry_causality_states(
    packet_reads: list[dict[str, Any]],
    prior_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    if prior_summary.get("status") != "loaded":
        return []
    expected_by_identity = {
        continuation_step_identity(read): {
            "read_role": read.get("read_role") or read.get("role") or read.get("target_source_role"),
            "target_handle": hashed_handle("target", read.get("target")),
            "source_handle": hashed_handle(
                "source",
                json.dumps(read.get("source_ref"), sort_keys=True, default=str)
                if isinstance(read.get("source_ref"), dict)
                else None,
            ),
        }
        for read in packet_reads
    }
    valid_states: list[dict[str, Any]] = []
    for state in prior_summary.get("executed_step_states") or []:
        if not isinstance(state, dict) or state.get("status") != "expanded":
            continue
        step_identity = str(state.get("step_identity") or "")
        expected = expected_by_identity.get(step_identity)
        if not expected:
            continue
        if str(state.get("read_role") or "") != str(expected.get("read_role") or ""):
            continue
        if state.get("target_handle") != expected.get("target_handle"):
            continue
        expected_source_handle = expected.get("source_handle")
        if expected_source_handle is not None and state.get("source_handle") != expected_source_handle:
            continue
        valid_states.append(
            {
                "rank": None,
                "candidate_id": state.get("candidate_id"),
                "status": state.get("status"),
                "role": state.get("read_role"),
                "target_handle": state.get("target_handle"),
                "source_handle": state.get("source_handle"),
                "step_identity": step_identity,
                "evidence_source": "prior_receipt",
            }
        )
    return valid_states


def build_geometry_causality_gate(
    expanded_reads: list[dict[str, Any]],
    *,
    prior_evidence_states: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    original = []
    later = []
    for item in prior_evidence_states or []:
        role = str(item.get("role") or item.get("read_role") or "")
        row = {
            "rank": item.get("rank"),
            "candidate_id": item.get("candidate_id"),
            "status": item.get("status"),
            "role": role,
            "target_handle": item.get("target_handle"),
            "source_handle": item.get("source_handle"),
            "step_identity": item.get("step_identity"),
            "evidence_source": "prior_receipt",
        }
        if role in GEOMETRY_CAUSALITY_ORIGINAL_ROLES:
            original.append(row)
        elif role in GEOMETRY_CAUSALITY_LATER_ROLES:
            later.append(row)
    for item in expanded_reads:
        role = str(item.get("read_role") or item.get("target_source_role") or "")
        row = {
            "rank": item.get("rank"),
            "candidate_id": item.get("candidate_id"),
            "status": item.get("status"),
            "role": role,
            "target_handle": hashed_handle("target", item.get("target")),
            "source_handle": hashed_handle(
                "source",
                json.dumps(item.get("source_ref"), sort_keys=True, default=str)
                if isinstance(item.get("source_ref"), dict)
                else None,
            ),
            "step_identity": item.get("step_identity"),
            "evidence_source": "current_invocation",
        }
        if role in GEOMETRY_CAUSALITY_ORIGINAL_ROLES:
            original.append(row)
        elif role in GEOMETRY_CAUSALITY_LATER_ROLES:
            later.append(row)
    original_expanded = any(item.get("status") == "expanded" for item in original)
    later_expanded = any(item.get("status") == "expanded" for item in later)
    if original_expanded:
        status = "original_decision_rationale_observed"
    elif later_expanded:
        status = "blocked_waiting_original_decision_rationale"
    else:
        status = "not_applicable_or_not_observed"
    return {
        "schema_version": 1,
        "status": status,
        "required_before_closure": ["original_decision_rationale"],
        "original_decision_rationale_reads": original,
        "later_radius_adjustment_or_consequence_reads": later,
        "later_radius_evidence_cannot_close_answer": bool(later_expanded and not original_expanded),
        "answer_closure_allowed_for_geometry_causality": bool(original_expanded),
    }


def continuation_remaining_named_gap(
    *,
    packet: dict[str, Any],
    selected_reads: list[dict[str, Any]],
    expanded_reads: list[dict[str, Any]],
    geometry_gate: dict[str, Any],
) -> str:
    if geometry_gate.get("later_radius_evidence_cannot_close_answer"):
        return "geometry_original_decision_rationale"
    for read in selected_reads + expanded_reads:
        gap = read.get("named_gap") or read.get("remaining_named_gap")
        if gap:
            return str(gap)
    return str(packet.get("remaining_named_gap") or packet.get("named_gap") or packet.get("gap_name") or "continuation_gap")


def continuation_successor_state(
    *,
    available_count: int,
    selected_count: int,
    counts: dict[str, int],
    geometry_gate: dict[str, Any],
    repeated_exhaustion: bool,
    remaining_after_count: int,
) -> str:
    if repeated_exhaustion:
        return "terminal_repeated_bounded_exhaustion"
    if available_count <= 0 or selected_count <= 0:
        return "terminal_no_structured_steps"
    if geometry_gate.get("later_radius_evidence_cannot_close_answer"):
        return "advanced_geometry_causality_blocked"
    if counts["expanded_count"] and counts["failed_count"]:
        return "advanced_with_read_failures_remaining"
    if counts["expanded_count"] and remaining_after_count > 0:
        return "advanced_with_unselected_bounded_steps"
    if counts["expanded_count"]:
        return "bounded_scope_exhausted_with_named_gap"
    return "bounded_steps_failed"


def continuation_searched_scope(
    reads: list[dict[str, Any]],
    selected_reads: list[dict[str, Any]],
    expanded_reads: list[dict[str, Any]],
    plan: SourcePlan,
) -> dict[str, Any]:
    return {
        "available_structured_step_count": len(reads),
        "selected_count": len(selected_reads),
        "expanded_count": sum(1 for item in expanded_reads if item.get("status") == "expanded"),
        "failed_count": sum(1 for item in expanded_reads if item.get("status") != "expanded"),
        "selected_target_handles": [hashed_handle("target", item.get("target")) for item in selected_reads[:20]],
        "root_count": len(plan.roots),
        "root_kinds": sorted({str(getattr(root, "kind", "unknown")) for root in plan.roots}),
        "scope_boundary": (
            "Only exact source refs serialized in the saved continuation packet were expanded; "
            "no broad retrieval, ask routing, shell search, or guessed anchors were run."
        ),
    }


def continuation_residual_unknowns(
    *,
    available_count: int,
    selected_count: int,
    counts: dict[str, int],
    remaining_named_gap: str,
    geometry_gate: dict[str, Any],
    failed_reads: list[dict[str, Any]],
) -> list[str]:
    unknowns = [f"remaining_named_gap:{remaining_named_gap}"]
    if selected_count < available_count:
        unknowns.append(f"unselected_structured_steps:{available_count - selected_count}")
    if counts["failed_count"]:
        unknowns.append(f"failed_structured_reads:{counts['failed_count']}")
    if counts["reviewed_usable_recovery_evidence_count"] == 0:
        unknowns.append("no_reviewed_usable_recovery_evidence")
    if geometry_gate.get("later_radius_evidence_cannot_close_answer"):
        unknowns.append("geometry_original_decision_rationale_not_observed")
    for failure in failed_reads[:3]:
        reason = failure.get("failure_reason")
        if reason:
            unknowns.append(f"failed_read_reason:{reason}")
    return list(dict.fromkeys(unknowns))


def load_prior_continuation_receipt_summary(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "not_supplied"}
    try:
        loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "not_found"}
    except (OSError, UnicodeDecodeError):
        return {"status": "read_error"}
    except json.JSONDecodeError:
        return {"status": "invalid_json"}
    packet = loaded.get("execute_continuation") if isinstance(loaded, dict) else None
    if not isinstance(packet, dict):
        return {"status": "unsupported_shape"}
    progress = packet.get("packet_progress") if isinstance(packet.get("packet_progress"), dict) else {}
    packet_binding = packet.get("packet_binding") if isinstance(packet.get("packet_binding"), dict) else None
    if packet_binding is None and isinstance(progress.get("packet_binding"), dict):
        packet_binding = progress.get("packet_binding")
    raw_step_states = progress.get("executed_step_states") if isinstance(progress.get("executed_step_states"), list) else []
    executed_step_states = [item for item in raw_step_states if isinstance(item, dict)]
    executed_step_identities = [
        str(item)
        for item in progress.get("executed_step_identities") or []
        if str(item).strip()
    ]
    if not executed_step_identities:
        executed_step_identities = [
            str(item.get("step_identity"))
            for item in packet.get("executed_steps") or []
            if isinstance(item, dict) and item.get("step_identity")
        ]
    return {
        "status": "loaded",
        "successor_state": packet.get("successor_state"),
        "remaining_named_gap": packet.get("remaining_named_gap"),
        "searched_scope": packet.get("searched_scope"),
        "packet_binding": packet_binding,
        "executed_step_identities": list(dict.fromkeys(executed_step_identities)),
        "executed_step_states": executed_step_states,
    }


def execute_continuation_step(
    *,
    read: dict[str, Any],
    rank: int,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
    max_output_chars: int,
    approved_history_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    if read.get("_continuation_step_kind") == "deep_audit_semantic_debug":
        return execute_deep_audit_semantic_debug_step(
            read=read,
            rank=rank,
            max_output_chars=max_output_chars,
        )
    if read.get("_continuation_step_kind") == "approved_history_orchestration_roles":
        return execute_approved_history_orchestration_roles_step(
            read=read,
            rank=rank,
            prior_budget=approved_history_budget,
        )
    return expand_follow_next_read(
        read=read,
        rank=rank,
        plan=plan,
        default_tz=default_tz,
        cwd=cwd,
        usability_decisions=None,
    )


def execute_approved_history_orchestration_roles_step(
    *,
    read: dict[str, Any],
    rank: int,
    prior_budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    validation = approved_history_orchestration_step_validation(read, packet=read.get("_packet") or {})
    base = {
        "rank": rank,
        "target": None,
        "source_ref": None,
        "surface": read.get("_follow_surface"),
        "step_identity": read.get("step_identity") or continuation_step_identity(read),
        "read_role": "approved_history_orchestration_roles",
        "named_gap": read.get("named_gap"),
        "candidate_id": read.get("candidate_id"),
        "continuation_step_kind": "approved_history_orchestration_roles",
        "serialized_packet_validation": validation,
        "metadata_only": True,
    }
    if validation.get("status") != "allowed":
        return {**base, "status": "rejected_continuation_packet", "failure_reason": validation.get("reason")}
    root = Path(str(read["approved_root"])).expanduser()
    if not root.exists() or root.is_symlink():
        return {
            **base,
            "status": "expanded",
            "continuation_result_status": "approved_history_root_unavailable",
            "failure_reason": "packet-approved transcript root is missing, unreadable, or a symlink",
            "role_scoped_raw_refs": [],
            "searched_scope": {"approved_root": str(root), "files_examined": 0, "bytes_examined": 0, "caps_hit": [], "current_workspace_searched": False},
        }
    caps = read["inherited_caps"]
    prior_budget = prior_budget or {}
    prior_files = max(0, int(prior_budget.get("files_examined") or 0))
    prior_bytes = max(0, int(prior_budget.get("bytes_examined") or 0))
    files, bytes_examined, cap_hits, refs = 0, 0, [], []
    paths = [root] if root.is_file() and root.suffix == ".jsonl" else []
    if root.is_dir():
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if not (Path(directory) / name).is_symlink())
            paths.extend(Path(directory) / name for name in sorted(filenames) if name.endswith(".jsonl"))
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if not path_is_inside(resolved, root):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if prior_files + files >= int(caps["max_files"]):
            cap_hits.append("max_files")
            break
        if prior_bytes + bytes_examined + size > int(caps["max_bytes"]):
            cap_hits.append("max_bytes")
            break
        files += 1
        bytes_examined += size
        refs.extend(approved_history_role_refs_in_file(path, task_handle=str(read["task_handle"]), roles=read["required_roles"]))
    deduped_refs, seen_roles = [], set()
    for ref in refs:
        if ref["role"] not in seen_roles:
            seen_roles.add(ref["role"])
            deduped_refs.append(ref)
    missing_roles = [role for role in read["required_roles"] if role not in seen_roles]
    return {
        **base,
        "status": "expanded",
        "continuation_result_status": "approved_history_roles_found" if not missing_roles else "approved_history_capped" if cap_hits else "not_found_within_approved_corpus_and_budget",
        "role_scoped_raw_refs": deduped_refs,
        "missing_roles": missing_roles,
        "searched_scope": {
            "approved_root": str(root),
            "files_examined": files,
            "bytes_examined": bytes_examined,
            "caps_hit": cap_hits,
            "current_workspace_searched": False,
            "packet_budget": {
                "max_files": int(caps["max_files"]),
                "max_bytes": int(caps["max_bytes"]),
                "prior_files_examined": prior_files,
                "prior_bytes_examined": prior_bytes,
                "cumulative_files_examined": prior_files + files,
                "cumulative_bytes_examined": prior_bytes + bytes_examined,
            },
        },
        "claim_boundaries": {"generic_shell_recovery_allowed": False, "root_widening_allowed": False, "answer_generation_included": False},
    }


def approved_history_budget_from_step_states(step_states: list[dict[str, Any]]) -> dict[str, int]:
    files, bytes_examined = 0, 0
    for state in step_states:
        if state.get("continuation_step_kind") != "approved_history_orchestration_roles":
            continue
        budget = state.get("approved_history_packet_budget")
        if not isinstance(budget, dict):
            continue
        files += max(0, int(budget.get("files_examined") or 0))
        bytes_examined += max(0, int(budget.get("bytes_examined") or 0))
    return {"files_examined": files, "bytes_examined": bytes_examined}


def approved_history_budget_after_step(prior_budget: dict[str, int], step: dict[str, Any]) -> dict[str, int]:
    files = max(0, int(prior_budget.get("files_examined") or 0))
    bytes_examined = max(0, int(prior_budget.get("bytes_examined") or 0))
    scope = step.get("searched_scope") if isinstance(step.get("searched_scope"), dict) else {}
    if step.get("continuation_step_kind") == "approved_history_orchestration_roles":
        files += max(0, int(scope.get("files_examined") or 0))
        bytes_examined += max(0, int(scope.get("bytes_examined") or 0))
    return {"files_examined": files, "bytes_examined": bytes_examined}


def approved_history_role_refs_in_file(path: Path, *, task_handle: str, roles: list[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return refs
    for line_number, line in enumerate(lines, 1):
        lowered = line.casefold()
        if task_handle.casefold() not in lowered:
            continue
        for role, terms in {
            "parent_or_coordinator": ("coordinator", "parent", "spawned", "launched"),
            "worker": ("worker", "implementer", "subagent"),
            "reviewer": ("reviewer", "rereviewer", "verified"),
        }.items():
            # A coordinator line that merely names a worker/reviewer is parent
            # evidence, not the child's own raw-role session ref.
            child_role_on_parent_line = role in {"worker", "reviewer"} and any(
                marker in lowered for marker in ("coordinator", "parent", "spawned", "launched")
            )
            if role in roles and not child_role_on_parent_line and any(term in lowered for term in terms):
                refs.append({"role": role, "source_ref": {"path": str(path), "line": line_number}, "target": f"{path}:{line_number}", "raw_text_included": False})
    return refs


def execute_deep_audit_semantic_debug_step(
    *,
    read: dict[str, Any],
    rank: int,
    max_output_chars: int,
) -> dict[str, Any]:
    argv = [str(part) for part in list_or_empty(read.get("argv"))]
    validation = deep_audit_continuation_argv_validation(argv)
    base = {
        "rank": rank,
        "target": None,
        "source_ref": None,
        "surface": read.get("_follow_surface"),
        "step_identity": read.get("step_identity") or continuation_step_identity(read),
        "read_role": read.get("read_role") or read.get("role") or read.get("target_source_role"),
        "named_gap": read.get("named_gap") or read.get("remaining_named_gap"),
        "duplicate_surfaces": list_or_empty(read.get("_follow_duplicate_surfaces")),
        "duplicate_candidate_ids": list_or_empty(read.get("_follow_duplicate_candidate_ids")),
        "discarded_duplicate_reads": list_or_empty(read.get("_follow_discarded_duplicates")),
        "candidate_id": read.get("candidate_id"),
        "continuation_step_kind": "deep_audit_semantic_debug",
        "serialized_argv_validation": validation,
        "metadata_only": True,
    }
    if validation.get("status") != "allowed":
        return {
            **base,
            "status": "rejected_continuation_argv",
            "failure_reason": validation.get("reason") or "serialized command is not an allowed deep-audit continuation",
        }

    # Importing CLI at module import time would create a cycle.  The local
    # import executes the exact saved argv in-process only after the package is
    # fully initialized; no shell, ask, find, bulk matcher, or fresh command is
    # involved.
    from .cli import main

    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            exit_code = main(argv[1:])
    except Exception as exc:  # pragma: no cover - defensive boundary for saved packets
        return {
            **base,
            "status": "semantic_debug_execution_error",
            "failure_reason": f"{type(exc).__name__}: {exc}",
        }
    raw_output = stdout.getvalue()
    try:
        nested_payload = json.loads(raw_output)
    except json.JSONDecodeError:
        return {
            **base,
            "status": "semantic_debug_invalid_json_output",
            "failure_reason": "serialized semantic-debug continuation did not return one JSON payload",
            "nested_exit_code": exit_code,
            "nested_output_char_count": len(raw_output),
        }
    nested = nested_payload.get("semantic_debug") if isinstance(nested_payload, dict) else None
    if not isinstance(nested, dict):
        return {
            **base,
            "status": "semantic_debug_invalid_payload",
            "failure_reason": "serialized semantic-debug continuation did not return semantic_debug metadata",
            "nested_exit_code": exit_code,
            "nested_output_char_count": len(raw_output),
        }

    step_contract = nested.get("continuation_step_contract") if isinstance(nested.get("continuation_step_contract"), dict) else {}
    workflow = nested.get("audit_continuation_workflow") if isinstance(nested.get("audit_continuation_workflow"), dict) else {}
    followthrough = nested.get("in_depth_audit_followthrough") if isinstance(nested.get("in_depth_audit_followthrough"), dict) else {}
    disposition = workflow.get("cap_hit_disposition") if isinstance(workflow.get("cap_hit_disposition"), dict) else followthrough.get("cap_hit_disposition") if isinstance(followthrough.get("cap_hit_disposition"), dict) else {}
    nested_packet = disposition.get("canonical_continuation_packet") if isinstance(disposition.get("canonical_continuation_packet"), dict) else None
    if exit_code != 0:
        return {
            **base,
            "status": "semantic_debug_nonzero_exit",
            "failure_reason": "serialized semantic-debug continuation returned nonzero",
            "nested_exit_code": exit_code,
            "nested_output_char_count": len(raw_output),
        }
    return {
        **base,
        "status": "expanded",
        "nested_exit_code": exit_code,
        "nested_output_char_count": len(raw_output),
        "nested_output_within_receipt_limit": not max_output_chars or len(raw_output) <= max_output_chars,
        "receipt_output_char_limit": max_output_chars,
        "nested_status": nested.get("status"),
        "nested_continuation_step_status": step_contract.get("status"),
        "nested_answer_ready": step_contract.get("answer_ready"),
        "nested_cap_hit_status": disposition.get("status"),
        "nested_canonical_packet_id": nested_packet.get("packet_id") if nested_packet else None,
        "claim_boundaries": {
            "nested_evidence_projection": step_contract.get("evidence_projection"),
            "nested_answer_ready": step_contract.get("answer_ready"),
            "answer_generation_included": False,
            "raw_nested_output_included": False,
            "nested_output_exceeded_receipt_limit_but_was_not_retained": bool(
                max_output_chars and len(raw_output) > max_output_chars
            ),
        },
    }


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
        "step_identity": read.get("step_identity") or continuation_step_identity(read),
        "read_role": read.get("read_role") or read.get("role") or read.get("target_source_role"),
        "named_gap": read.get("named_gap") or read.get("remaining_named_gap"),
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
    if read.get("_continuation_step_kind") == "deep_audit_semantic_debug":
        argv = read.get("argv")
        if isinstance(argv, list):
            return f"deep_audit_semantic_debug:{json.dumps(argv, ensure_ascii=False)}"
    if read.get("_continuation_step_kind") == "approved_history_orchestration_roles":
        return "approved_history_orchestration_roles:" + str(read.get("step_id") or read.get("candidate_id") or "")
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
    candidates.extend(canonical_alias_path_candidates(path_text, plan=plan))
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


def canonical_alias_path_candidates(path_text: str, *, plan: SourcePlan) -> list[Path]:
    if path_text == "codex_history:history.jsonl":
        return [root.path.expanduser() for root in plan.roots if root.kind == "codex_history"]
    if not path_text.startswith("codex_sessions:"):
        return []
    alias = path_text[len("codex_sessions:") :].strip("/")
    if not alias:
        return []
    matches: list[Path] = []
    for root in plan.roots:
        if root.kind != "codex_sessions":
            continue
        base = root.path.expanduser()
        direct = base / alias
        if direct.suffix != ".jsonl":
            direct_with_suffix = direct.with_suffix(".jsonl")
        else:
            direct_with_suffix = direct
        for candidate in (direct, direct_with_suffix):
            if candidate.is_file():
                matches.append(candidate)
        if matches:
            continue
        if not base.is_dir():
            continue
        for candidate in base.rglob("*.jsonl"):
            if _codex_session_alias_for_path(candidate, base) == alias:
                matches.append(candidate)
                break
    return matches


def _codex_session_alias_for_path(path: Path, base: Path) -> str:
    try:
        rel = path.resolve().relative_to(base.resolve())
        raw = str(rel)
    except (OSError, ValueError):
        rel = Path(path.name)
        raw = path.name
    parts = rel.parts
    if len(parts) >= 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        date_label = "/".join(parts[:3])
    else:
        date_label = "unknown-date"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{date_label}/session-{digest}"


def canonical_ref_fallback(parsed: ParsedTarget, *, plan: SourcePlan, cwd: Path) -> dict[str, Any] | None:
    path_text = parsed.path_text or ""
    if not (path_text.startswith("codex_history:") or path_text.startswith("codex_sessions:")):
        return None
    if path_text.startswith("codex_history:"):
        openable_targets = [
            f"{root.path.expanduser()}:{parsed.line}" if parsed.line is not None else str(root.path.expanduser())
            for root in plan.roots
            if root.kind == "codex_history"
        ]
    else:
        openable_targets = []
        for root in plan.roots:
            if root.kind == "codex_sessions":
                openable_targets.append(f"<session-jsonl-under-{root.path.expanduser()}>:{parsed.line}")
    target = openable_targets[0] if openable_targets else None
    command = None
    if target:
        command = (
            "jsonl-history-search read-more "
            f"{shlex.quote(target)} --cwd {shlex.quote(str(cwd))} --format json --mode window --before 2 --after 2"
        )
    return {
        "status": "canonical_alias_unresolved",
        "requested_canonical_ref": parsed.raw_target,
        "openable_target": target,
        "read_more_command": command,
        "guidance": (
            "Use the openable_target/read_more_command if present. For unresolved codex_sessions aliases, "
            "rerun the status route and use the row's openable_target."
        ),
    }


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


COMPACT_SUPPRESSED_ROLES = {"token_count"}
COMPACT_SUMMARY_ROLES = {"exec_command_end", "function_call_output", "tool_result", "tool_output"}


def source_expansion_projection_summary(
    records: list[Any],
    *,
    projection: str,
    compact_text_limit: int,
) -> dict[str, Any]:
    raw_text_bytes = sum(len(str(getattr(record, "text", "") or "").encode("utf-8")) for record in records)
    if projection != "compact":
        return {
            "schema_version": 1,
            "projection": "full",
            "raw_text_bytes": raw_text_bytes,
            "projected_text_bytes": raw_text_bytes,
            "suppressed_record_count": 0,
            "truncated_record_count": 0,
        }
    projected = [compact_record_text(record, compact_text_limit=compact_text_limit) for record in records]
    return {
        "schema_version": 1,
        "projection": "compact",
        "raw_text_bytes": raw_text_bytes,
        "projected_text_bytes": sum(len(str(item["text"]).encode("utf-8")) for item in projected),
        "suppressed_record_count": sum(1 for item in projected if item["suppressed"]),
        "truncated_record_count": sum(1 for item in projected if item["truncated"]),
        "compact_text_limit": compact_text_limit,
        "suppressed_roles": sorted(COMPACT_SUPPRESSED_ROLES),
        "summary_roles": sorted(COMPACT_SUMMARY_ROLES),
        "claim_boundary": "compact projection preserves source refs and bounded text previews; it is not a full raw transcript dump",
    }


def compact_record_text(record: Any, *, compact_text_limit: int) -> dict[str, Any]:
    role = str(getattr(record, "role", "") or "")
    text = str(getattr(record, "text", "") or "")
    raw_bytes = len(text.encode("utf-8"))
    if role in COMPACT_SUPPRESSED_ROLES:
        return {
            "text": f"[suppressed {role} record; raw_text_bytes={raw_bytes}]",
            "raw_text_bytes": raw_bytes,
            "projected_text_bytes": 0,
            "suppressed": True,
            "truncated": False,
            "projection_reason": "low_signal_runtime_metadata",
        }
    limit = max(0, int(compact_text_limit))
    if role in COMPACT_SUMMARY_ROLES and raw_bytes > limit:
        preview = text[:limit].rstrip()
        omitted = max(0, raw_bytes - len(preview.encode("utf-8")))
        return {
            "text": f"{preview}\n[truncated {role} record; raw_text_bytes={raw_bytes}; omitted_bytes~={omitted}]",
            "raw_text_bytes": raw_bytes,
            "projected_text_bytes": len(preview.encode("utf-8")),
            "suppressed": False,
            "truncated": True,
            "projection_reason": "large_tool_output_preview",
        }
    return {
        "text": text,
        "raw_text_bytes": raw_bytes,
        "projected_text_bytes": raw_bytes,
        "suppressed": False,
        "truncated": False,
        "projection_reason": "kept_full_text",
    }


def expanded_record(
    record: Any,
    *,
    projection: str = "full",
    compact_text_limit: int = 1200,
) -> dict[str, Any]:
    projected = (
        compact_record_text(record, compact_text_limit=compact_text_limit)
        if projection == "compact"
        else {
            "text": record.text,
            "raw_text_bytes": len(str(record.text or "").encode("utf-8")),
            "projected_text_bytes": len(str(record.text or "").encode("utf-8")),
            "suppressed": False,
            "truncated": False,
            "projection_reason": "full_projection",
        }
    )
    row = {
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
        "text": projected["text"],
        "text_included": not projected["suppressed"],
        "projection": projection,
        "projection_reason": projected["projection_reason"],
        "raw_text_bytes": projected["raw_text_bytes"],
        "projected_text_bytes": projected["projected_text_bytes"],
        "text_truncated": projected["truncated"],
        "text_suppressed": projected["suppressed"],
    }
    return row


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
