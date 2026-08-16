from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath

from .agent_contract import source_ref_target
from .metric_contract import TOKEN_FORMULA
from .harness_loop import render_harness_minimum_loop_report, render_harness_minimum_loop_text
from .eval_decision_loop import render_eval_decision_loop_markdown
from .real_history_benchmark_drift import (
    render_real_history_benchmark_drift_markdown,
    render_real_history_benchmark_drift_text,
)
from .real_history_post_jsh763_date_timeline_diagnostic import (
    render_post_jsh763_date_timeline_diagnostic_markdown,
    render_post_jsh763_date_timeline_diagnostic_text,
)
from .real_run_gate import render_real_run_gate_report, render_real_run_gate_text
from .status_anchor import parent_thread_interval_continuation_packet


SOURCE_EXPANSION_TEXT_RECORD_HEADER_RE = re.compile(
    r"(?i)^-\s+[^\n]+\.jsonl:\d+:\s+[a-z_][a-z0-9_]*\s*$"
)


def render_json(payload: dict) -> str:
    safe_payload = _semantic_debug_cap_hit_json_projection(payload)
    return json.dumps(safe_payload or payload, indent=2, ensure_ascii=False, sort_keys=True)


def project_semantic_debug_continuation_step(payload: dict) -> dict:
    report = payload.get("semantic_debug")
    if not isinstance(report, dict):
        return payload
    followthrough = report.get("in_depth_audit_followthrough") or {}
    workflow = report.get("audit_continuation_workflow") or followthrough.get(
        "audit_continuation_workflow"
    ) or {}
    if workflow.get("answer_ready") is True:
        report["continuation_step_contract"] = {
            "status": "answer_ready",
            "answer_ready": True,
            "evidence_projection": "normal_answer_ready_output",
        }
        return payload

    projected = _semantic_debug_cap_hit_json_projection(payload)
    if projected is None:
        scoped_fail_closed = {
            "schema_version": 1,
            "status": "scoped_fail_closed",
            "answer_ready": False,
            "canonical_continuation": None,
            "canonical_continuation_packet": None,
            "scoped_fail_closed": {
                "reason": (
                    "Continuation step remains unresolved without a canonical nested continuation; "
                    "partial evidence is withheld."
                ),
                "searched_scope": (followthrough.get("coverage_state") or {}).get("searched_scope") or {},
                "not_searched_scope": (followthrough.get("coverage_state") or {}).get("not_searched_scope") or {},
                "source_boundary": "serialized selected history roots and reported searched windows only",
            },
            "partial_evidence_rendering_allowed": False,
            "manual_history_fallback_allowed": False,
        }
        synthetic_followthrough = {**followthrough, "cap_hit_disposition": scoped_fail_closed}
        synthetic_workflow = {
            **workflow,
            "applies": True,
            "answer_ready": False,
            "hard_stop": True,
            "cap_hit_disposition": scoped_fail_closed,
        }
        synthetic_followthrough["audit_continuation_workflow"] = synthetic_workflow
        projected = _semantic_debug_cap_hit_json_projection(
            {
                "semantic_debug": {
                    **report,
                    "in_depth_audit_followthrough": synthetic_followthrough,
                    "audit_continuation_workflow": synthetic_workflow,
                }
            }
        )
    if projected is None:
        return {
            "semantic_debug": {
                "schema_version": report.get("schema_version"),
                "status": "scoped_fail_closed",
                "answer_gate": {
                    "status": "not_answerable_yet",
                    "answer_ready": False,
                    "hard_stop": True,
                    "partial_evidence_rendering_allowed": False,
                },
            }
        }
    safe_report = projected["semantic_debug"]
    safe_report["continuation_step_contract"] = {
        "status": "unresolved_evidence_withheld",
        "answer_ready": False,
        "evidence_projection": "metadata_only",
        "forbidden_evidence_fields": [
            "strongest_candidates",
            "timeline",
            "next_reads",
            "snippet",
            "text",
            "read",
        ],
    }
    safe_report["message"] = (
        "Continuation-step evidence is withheld until this step is answer-ready; follow only a nested "
        "canonical continuation or return the reported scoped blocker."
    )
    return projected


def _semantic_debug_cap_hit_json_projection(payload: dict) -> dict | None:
    report = payload.get("semantic_debug")
    if not isinstance(report, dict):
        return None
    followthrough = report.get("in_depth_audit_followthrough") or {}
    disposition = followthrough.get("cap_hit_disposition") or {}
    if disposition.get("status") not in {
        "canonical_continuation",
        "canonical_continuation_packet",
        "scoped_fail_closed",
    }:
        return None
    if disposition.get("answer_ready") is not False:
        return None

    canonical = disposition.get("canonical_continuation")
    safe_canonical = _semantic_debug_safe_continuation(canonical) if isinstance(canonical, dict) else None
    continuation_packet = disposition.get("canonical_continuation_packet")
    safe_packet = (
        _semantic_debug_safe_continuation_packet(continuation_packet)
        if isinstance(continuation_packet, dict)
        else None
    )
    safe_disposition = {
        "schema_version": disposition.get("schema_version"),
        "status": disposition.get("status"),
        "answer_ready": False,
        "canonical_continuation": safe_canonical,
        "canonical_continuation_packet": safe_packet,
        "scoped_fail_closed": disposition.get("scoped_fail_closed"),
        "partial_evidence_rendering_allowed": False,
        "manual_history_fallback_allowed": False,
        "pending_continuation_candidate_count": disposition.get("pending_continuation_candidate_count", 0),
        "cap_continuation_count": disposition.get("cap_continuation_count", 0),
        "tail_followup_count": disposition.get("tail_followup_count", 0),
        "unresolved_cap_slice_count": disposition.get("unresolved_cap_slice_count", 0),
        "covered_unresolved_cap_slice_count": disposition.get(
            "covered_unresolved_cap_slice_count", 0
        ),
        "canonical_scope_coverage_complete": bool(
            disposition.get("canonical_scope_coverage_complete")
        ),
    }
    workflow = report.get("audit_continuation_workflow") or followthrough.get(
        "audit_continuation_workflow"
    ) or {}
    safe_workflow = {
        "schema_version": workflow.get("schema_version"),
        "applies": bool(workflow.get("applies")),
        "answer_ready": False,
        "hard_stop": True,
        "answer_status": workflow.get("answer_status") or "not_answerable_yet",
        "hard_stop_semantics": workflow.get("hard_stop_semantics"),
        "pending_slices": list(workflow.get("pending_slices") or []),
        "continuation_commands": [safe_canonical] if safe_canonical else [],
        "continuation_packet": safe_packet,
        "cap_hit_disposition": safe_disposition,
        "do_not_abandon_jsonl_guidance": workflow.get("do_not_abandon_jsonl_guidance"),
    }
    return {
        "semantic_debug": {
            "schema_version": report.get("schema_version"),
            "status": report.get("status"),
            "audit_depth": report.get("audit_depth"),
            "question_family": report.get("question_family"),
            "answer_gate": {
                "status": "not_answerable_yet",
                "answer_ready": False,
                "hard_stop": True,
                "partial_evidence_rendering_allowed": False,
                "manual_history_fallback_allowed": False,
            },
            "cap_hit_disposition": safe_disposition,
            "coverage_state": followthrough.get("coverage_state") or {},
            "audit_continuation_workflow": safe_workflow,
            "suppressed_evidence": {
                "strongest_candidate_count": len(report.get("strongest_candidates") or []),
                "timeline_event_count": len(report.get("timeline") or []),
                "next_read_count": len(report.get("next_reads") or []),
                "fields": ["strongest_candidates", "timeline", "next_reads"],
                "snippet_material_included": False,
            },
            "message": (
                "Cap-hit evidence is withheld until the exact continuation covers all unresolved "
                "capped scope, or the scoped failure is resolved."
            ),
        }
    }


def _semantic_debug_safe_continuation(action: dict) -> dict:
    safe_keys = (
        "command_id",
        "slice",
        "missing_or_capped_window",
        "status",
        "coverage_label",
        "cap_labels",
        "retry_guidance",
        "target_fingerprint_12",
        "target_persistence_required",
        "evidence_output_contract",
        "argv",
        "command",
    )
    return {key: action.get(key) for key in safe_keys if key in action}


def _semantic_debug_safe_continuation_packet(packet: dict) -> dict:
    return {
        "schema_version": packet.get("schema_version"),
        "packet_id": packet.get("packet_id"),
        "status": packet.get("status"),
        "answer_ready": False,
        "execution_mode": packet.get("execution_mode"),
        "source_scope": packet.get("source_scope") or {},
        "omitted_ranges": list(packet.get("omitted_ranges") or []),
        "caps": packet.get("caps") or {},
        "target_contract": packet.get("target_contract") or {},
        "pending_slices": list(packet.get("pending_slices") or []),
        "steps": [
            _semantic_debug_safe_continuation(step)
            for step in packet.get("steps") or []
            if isinstance(step, dict)
        ],
        "blockers": list(packet.get("blockers") or []),
        "completion_contract": packet.get("completion_contract") or {},
        "partial_evidence_rendering_allowed": False,
        "manual_history_fallback_allowed": False,
    }


def render_text(payload: dict) -> str:
    if "retained_source_read" in payload:
        return render_retained_source_read_text(payload)
    if "ask_answer" in payload:
        return render_ask_answer_text(payload)
    if "ask_execute" in payload:
        return render_ask_execute_text(payload)
    if "ask_router" in payload:
        return render_ask_router_text(payload)
    if "bulk_line_match" in payload:
        return render_bulk_line_match_text(payload)
    if "failure_analysis" in payload:
        return render_failure_analysis_text(payload)
    if "latest_user_messages" in payload:
        return render_latest_user_messages_text(payload)
    if "status_anchor" in payload:
        return render_status_anchor_text(payload)
    if "artifact_currentness" in payload:
        return render_artifact_currentness_text(payload)
    if "local_evidence_diagnostic" in payload:
        return render_local_evidence_diagnostic_text(payload)
    if "semantic_wrapup" in payload:
        return render_semantic_wrapup_text(payload)
    if "source_expansion" in payload:
        return render_source_expansion_text(payload)
    if "follow_next_reads" in payload:
        return render_follow_next_reads_text(payload)
    if "execute_continuation" in payload:
        return render_execute_continuation_text(payload)
    if "jsonl_candidate_discovery" in payload:
        return render_jsonl_candidate_discovery_text(payload)
    if "capability_diagnostic" in payload:
        return render_capability_diagnostic_text(payload)
    if "install_validation" in payload:
        return render_install_validation_text(payload)
    if "forensics" in payload:
        return render_forensics_text(payload)
    if "manifest_report" in payload:
        return render_manifest_text(payload)
    if "generated_metadata_discovery" in payload:
        return render_generated_metadata_discovery_text(payload)
    if "audit_coverage_ledger" in payload:
        return render_audit_coverage_ledger_text(payload)
    if "expansion_family_probe" in payload:
        return render_expansion_family_probe_text(payload)
    if "expansion_family_audit" in payload:
        return render_expansion_family_audit_text(payload)
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_text(payload)
    if "agent_find" in payload:
        return render_agent_find_text(payload)
    if "agent_loop_cost_contract" in payload:
        return render_agent_loop_cost_contract_text(payload)
    if "retained_trace_counters" in payload:
        return render_retained_trace_counters_text(payload)
    if "project_lineage" in payload:
        return render_project_lineage_text(payload)
    if "skill_audit" in payload:
        return render_skill_audit_text(payload)
    if "structured_tool_events" in payload:
        return render_structured_tool_events_text(payload)
    if "workflow_repeat_finder" in payload:
        return render_workflow_repeat_text(payload)
    if "semantic_debug" in payload:
        return render_semantic_debug_text(payload)
    if "private_benchmark_mine" in payload:
        return render_private_benchmark_mine_text(payload)
    if "measured_benchmark" in payload:
        return render_measured_benchmark_text(payload)
    if "impact_benchmark" in payload:
        return render_impact_benchmark_text(payload)
    if "mechanism_eval" in payload:
        return render_mechanism_eval_text(payload)
    if "eval_decision_loop" in payload:
        return render_eval_decision_loop_markdown(payload)
    if "real_history_impact_benchmark" in payload:
        return render_real_history_impact_text(payload)
    if "real_history_timestamp_seek_benchmark" in payload:
        return render_real_history_timestamp_seek_text(payload)
    if "real_history_embedding_benchmark" in payload:
        return render_real_history_embedding_text(payload)
    if "real_history_package_search_miss_audit" in payload:
        return render_real_history_package_miss_audit_text(payload)
    if "real_history_remaining_miss_loss_point_diagnostic" in payload:
        return render_real_history_remaining_miss_diagnostic_text(payload)
    if "real_history_opened_file_progress_displacement_diagnostic" in payload:
        return render_real_history_opened_file_progress_diagnostic_text(payload)
    if "real_history_benchmark_drift_comparator" in payload:
        return render_real_history_benchmark_drift_text(payload)
    if "post_jsh763_date_timeline_miss_depth_diagnostic" in payload:
        return render_post_jsh763_date_timeline_diagnostic_text(payload)
    if "paired_agent_benchmark_protocol" in payload:
        return render_paired_agent_benchmark_protocol_text(payload)
    if "harness_artifact_validation" in payload:
        return render_harness_artifact_validation_text(payload)
    if "harness_dry_run_artifacts" in payload:
        return render_harness_dry_run_artifacts_text(payload)
    if "harness_offline_scorer_artifacts" in payload:
        return render_harness_offline_scorer_artifacts_text(payload)
    if "harness_minimum_user_facing_loop" in payload:
        return render_harness_minimum_loop_text(payload)
    if "harness_real_run_gate" in payload:
        return render_real_run_gate_text(payload)
    if "live_terminal_protocol" in payload:
        return render_live_terminal_protocol_text(payload)
    if "embedding_experiment" in payload:
        return render_embedding_experiment_text(payload)
    if "archive_snapshot_proof" in payload:
        return render_archive_snapshot_proof_text(payload)
    plan = payload["source_plan"]
    query = plan["query"]
    lines = [
        f"Query: {query['original']}",
        f"Terms: {', '.join(query['terms']) if query['terms'] else '(none)'}",
        f"Role: {query['role'] or '(any)'}",
    ]
    window = query["date_window"]
    if window["start"] or window["end"]:
        lines.append(f"Date window: {window['start']} to {window['end']} ({window['reason']})")
    lines.append(f"Roots: {len(plan['sources']['roots'])} selected, {len(plan['sources']['rejected_roots'])} rejected")
    for root in plan["sources"]["roots"]:
        lines.append(f"- {root['kind']}: {root['path']} ({root['reason']})")
    for rejected in plan["sources"]["rejected_roots"]:
        lines.append(f"- rejected {rejected['path']}: {rejected['reason']}")
    lines.append(
        f"Searched: {payload.get('searched_files', 0)} files, {payload.get('searched_bytes', 0)} bytes"
    )
    for warning in payload.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    masking = payload.get("masking")
    if masking:
        redaction = masking.get("redaction") or {}
        lines.append(
            "Masking: "
            f"{masking.get('mode')} "
            f"masked={masking.get('active_context_masked', 0)} "
            f"collapsed={masking.get('active_context_collapsed', 0)} "
            f"debug_visible={masking.get('debug_visible', 0)} "
            f"transcript_retained={masking.get('transcript_retained', 0)} "
            f"redacted={redaction.get('redacted_total', 0)}"
        )
    if payload.get("groups"):
        lines.append("Matches:")
        for group in payload["groups"]:
            lines.append(f"- {group['path']} (score {group['top_score']})")
            for match in group["matches"][:3]:
                where = f"line {match['line']}"
                stamp = f", {match['timestamp']}" if match["timestamp"] else ""
                role = f", {match['role']}" if match["role"] else ""
                lines.append(f"  {where}{stamp}{role}: {match['snippet']}")
    return "\n".join(lines)


def render_markdown(payload: dict) -> str:
    if "retained_source_read" in payload:
        return render_retained_source_read_markdown(payload)
    if "ask_answer" in payload:
        return render_ask_answer_markdown(payload)
    if "ask_execute" in payload:
        return render_ask_execute_markdown(payload)
    if "ask_router" in payload:
        return render_ask_router_markdown(payload)
    if "bulk_line_match" in payload:
        return render_bulk_line_match_markdown(payload)
    if "failure_analysis" in payload:
        return render_failure_analysis_markdown(payload)
    if "latest_user_messages" in payload:
        return render_latest_user_messages_markdown(payload)
    if "status_anchor" in payload:
        return render_status_anchor_markdown(payload)
    if "artifact_currentness" in payload:
        return render_artifact_currentness_markdown(payload)
    if "local_evidence_diagnostic" in payload:
        return render_local_evidence_diagnostic_markdown(payload)
    if "semantic_wrapup" in payload:
        return render_semantic_wrapup_markdown(payload)
    if "source_expansion" in payload:
        return render_source_expansion_markdown(payload)
    if "follow_next_reads" in payload:
        return render_follow_next_reads_markdown(payload)
    if "execute_continuation" in payload:
        return render_execute_continuation_markdown(payload)
    if "jsonl_candidate_discovery" in payload:
        return render_jsonl_candidate_discovery_markdown(payload)
    if "capability_diagnostic" in payload:
        return render_capability_diagnostic_markdown(payload)
    if "install_validation" in payload:
        return render_install_validation_markdown(payload)
    if "forensics" in payload:
        return render_forensics_markdown(payload)
    if "manifest_report" in payload:
        return render_manifest_markdown(payload)
    if "generated_metadata_discovery" in payload:
        return render_generated_metadata_discovery_markdown(payload)
    if "expansion_family_probe" in payload:
        return render_expansion_family_probe_markdown(payload)
    if "expansion_family_audit" in payload:
        return render_expansion_family_audit_markdown(payload)
    if "retrieval_efficiency_eval" in payload:
        return render_efficiency_eval_markdown(payload)
    if "agent_find" in payload:
        return render_agent_find_markdown(payload)
    if "agent_loop_cost_contract" in payload:
        return render_agent_loop_cost_contract_markdown(payload)
    if "retained_trace_counters" in payload:
        return render_retained_trace_counters_markdown(payload)
    if "project_lineage" in payload:
        return render_project_lineage_markdown(payload)
    if "skill_audit" in payload:
        return render_skill_audit_markdown(payload)
    if "structured_tool_events" in payload:
        return render_structured_tool_events_markdown(payload)
    if "workflow_repeat_finder" in payload:
        return render_workflow_repeat_markdown(payload)
    if "semantic_debug" in payload:
        return render_semantic_debug_markdown(payload)
    if "private_benchmark_mine" in payload:
        return render_private_benchmark_mine_markdown(payload)
    if "measured_benchmark" in payload:
        return render_measured_benchmark_markdown(payload)
    if "impact_benchmark" in payload:
        return render_impact_benchmark_markdown(payload)
    if "mechanism_eval" in payload:
        return render_mechanism_eval_markdown(payload)
    if "eval_decision_loop" in payload:
        return render_eval_decision_loop_markdown(payload)
    if "real_history_impact_benchmark" in payload:
        return render_real_history_impact_markdown(payload)
    if "real_history_timestamp_seek_benchmark" in payload:
        return render_real_history_timestamp_seek_markdown(payload)
    if "real_history_embedding_benchmark" in payload:
        return render_real_history_embedding_markdown(payload)
    if "real_history_package_search_miss_audit" in payload:
        return render_real_history_package_miss_audit_markdown(payload)
    if "real_history_remaining_miss_loss_point_diagnostic" in payload:
        return render_real_history_remaining_miss_diagnostic_markdown(payload)
    if "real_history_opened_file_progress_displacement_diagnostic" in payload:
        return render_real_history_opened_file_progress_diagnostic_markdown(payload)
    if "real_history_benchmark_drift_comparator" in payload:
        return render_real_history_benchmark_drift_markdown(payload)
    if "post_jsh763_date_timeline_miss_depth_diagnostic" in payload:
        return render_post_jsh763_date_timeline_diagnostic_markdown(payload)
    if "paired_agent_benchmark_protocol" in payload:
        return render_paired_agent_benchmark_protocol_markdown(payload)
    if "harness_artifact_validation" in payload:
        return render_harness_artifact_validation_markdown(payload)
    if "harness_dry_run_artifacts" in payload:
        return render_harness_dry_run_artifacts_markdown(payload)
    if "harness_offline_scorer_artifacts" in payload:
        return render_harness_offline_scorer_artifacts_markdown(payload)
    if "harness_minimum_user_facing_loop" in payload:
        return render_harness_minimum_loop_report(payload)
    if "harness_real_run_gate" in payload:
        return render_real_run_gate_report(payload)
    if "live_terminal_protocol" in payload:
        return render_live_terminal_protocol_markdown(payload)
    if "embedding_experiment" in payload:
        return render_embedding_experiment_markdown(payload)
    if "archive_snapshot_proof" in payload:
        return render_archive_snapshot_proof_markdown(payload)
    text = render_text(payload)
    lines = ["# JSONL", ""]
    for line in text.splitlines():
        if line.startswith("- "):
            lines.append(line)
        elif line.startswith("  "):
            lines.append(line)
        else:
            key, sep, value = line.partition(": ")
            if sep and key in {"Query", "Terms", "Role", "Date window", "Roots", "Searched"}:
                lines.append(f"**{key}:** {value}")
            else:
                lines.append(line)
    return "\n".join(lines)


def render(payload: dict, output_format: str) -> str:
    if output_format == "json":
        return render_json(payload)
    if output_format == "markdown":
        return render_markdown(payload)
    if output_format == "answer":
        return render_answer(payload)
    return render_text(payload)


def render_answer(payload: dict) -> str:
    if "status_anchor" in payload:
        return render_status_anchor_answer(payload)
    return render_text(payload)


def render_audit_coverage_ledger_text(payload: dict) -> str:
    ledger = payload["audit_coverage_ledger"]
    summary = ledger.get("summary") or {}
    lines = [
        "Audit Coverage Ledger",
        f"Status: {ledger.get('status')}",
        f"Privacy boundary: {ledger.get('privacy_boundary')}",
        f"Candidate traces: {summary.get('candidate_trace_count', 0)}",
        f"Traces reported: {summary.get('traces_reported', 0)}",
        "Classification counts:",
    ]
    for key, value in (summary.get("classification_counts") or {}).items():
        lines.append(f"- {key}: {value}")
    lines.append("Included selected-model counts:")
    selected = summary.get("included_selected_model_counts") or {}
    if selected:
        for key, value in selected.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.append("Slices:")
    for slice_row in ledger.get("slices") or []:
        lines.append(
            f"- {slice_row.get('slice')}: traces={slice_row.get('candidate_trace_count', 0)} "
            f"cap_status={slice_row.get('cap_status')}"
        )
    return "\n".join(lines)


def render_retained_source_read_text(payload: dict) -> str:
    report = payload["retained_source_read"]
    lines = [
        "Retained Source Read",
        f"Status: {report.get('status')}",
        f"Task: {report.get('task_id')}",
        f"Handle: {report.get('handle')}",
        f"Reason: {report.get('reason')}",
        f"Proof boundary: {report.get('proof_boundary')}",
        f"Candidate files opened: {report.get('candidate_files_opened', 0)}",
        f"Bytes read: {report.get('bytes_read', 0)}",
        "Opened role-valid source-session refs:",
    ]
    refs = report.get("opened_refs") or []
    if not refs:
        lines.append("- none")
    for ref in refs:
        lines.append(f"- {ref.get('target')}: {ref.get('summary')}")
    stop = report.get("missing_source_session_stop_contract") or {}
    if stop:
        policy = stop.get("secondary_artifact_policy") or {}
        lines.extend(
            [
                f"Source-session facet: {stop.get('status')}",
                f"Source-session answer allowed: {str(bool(stop.get('answer_allowed'))).lower()}",
                "Summary artifact policy: "
                + ", ".join(f"{name}={value}" for name, value in policy.items()),
                "Preserved separate fact facets: "
                + ", ".join(stop.get("preserved_fact_facets") or []),
                f"Stop: {stop.get('stop')}",
                f"Unblock: {stop.get('unblock')}",
            ]
        )
    return "\n".join(lines)


def render_retained_source_read_markdown(payload: dict) -> str:
    report = payload["retained_source_read"]
    lines = [
        "# Retained Source Read",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Task: `{report.get('task_id')}`",
        f"- Handle: `{report.get('handle')}`",
        f"- Reason: `{report.get('reason')}`",
        f"- Proof boundary: {report.get('proof_boundary')}",
        f"- Candidate files opened: `{report.get('candidate_files_opened', 0)}`",
        f"- Bytes read: `{report.get('bytes_read', 0)}`",
        "",
        "## Opened Role-Valid Source-Session Refs",
        "",
    ]
    refs = report.get("opened_refs") or []
    if not refs:
        lines.append("- none")
    for ref in refs:
        lines.append(f"- `{ref.get('target')}`: {ref.get('summary')}")
    stop = report.get("missing_source_session_stop_contract") or {}
    if stop:
        policy = stop.get("secondary_artifact_policy") or {}
        lines.extend(
            [
                "",
                "## Missing Source-Session Stop",
                "",
                f"- Status: `{stop.get('status')}`",
                f"- Source-session answer allowed: `{str(bool(stop.get('answer_allowed'))).lower()}`",
                "- Summary artifact policy: "
                + ", ".join(f"`{name}={value}`" for name, value in policy.items()),
                "- Preserved separate fact facets: "
                + ", ".join(f"`{item}`" for item in stop.get("preserved_fact_facets") or []),
                f"- Stop: {stop.get('stop')}",
                f"- Unblock: {stop.get('unblock')}",
            ]
        )
    return "\n".join(lines)


def _original_prompt_identity_lines(identity: dict, *, markdown: bool) -> list[str]:
    if not identity:
        return []
    source = identity.get("original_prompt_source") or {}
    status = identity.get("status")
    satisfied = str(bool(identity.get("original_wording_contract_satisfied"))).lower()
    source_ref = source.get("source_reference") or source.get("kind")
    byte_count = source.get("byte_count")
    sha256 = source.get("sha256")
    reread_required = str(bool(identity.get("identity_audit_read_required"))).lower()
    if markdown:
        return [
            f"- Original prompt identity: `{status}`",
            f"- Original wording contract satisfied: `{satisfied}`",
            f"- Original prompt source: `{source_ref}` bytes=`{byte_count}` sha256=`{sha256}`",
            f"- Original prompt reread required: `{reread_required}`",
        ]
    return [
        f"Original prompt identity: {status}",
        f"Original wording contract satisfied: {satisfied}",
        f"Original prompt source: {source_ref} bytes={byte_count} sha256={sha256}",
        f"Original prompt reread required: {reread_required}",
    ]


def render_ask_router_text(payload: dict) -> str:
    report = payload["ask_router"]
    resolution = report.get("command_resolution") or {}
    lines = [
        "Ask Route",
        "Answer status: not-ready/needs-more-proof",
        "Open these source refs first: none yet; run the first command to produce source refs.",
        f"Run this first: {report.get('first_command')}",
        f"First command: {report.get('first_command')}",
        f"Stop condition: {report.get('stop_guidance')}",
        f"Only continue if: {report.get('manual_search_guidance')}",
        f"Chosen route: {report.get('chosen_route')}",
        f"Audit depth: {(report.get('audit_depth') or {}).get('label', 'quick')}",
        f"Why: {report.get('why_it_applies')}",
        f"Read: {report.get('read_specialization')}",
    ]
    lines.extend(_original_prompt_identity_lines(report.get("original_prompt_identity") or {}, markdown=False))
    if resolution.get("mode") != "not_applicable":
        lines.extend(
            [
                f"Command resolution: {resolution.get('mode')}",
                f"Source fallback: {resolution.get('source_fallback_command') or '(not available from this cwd)'}",
                f"Command guidance: {resolution.get('guidance')}",
            ]
        )
    alias = report.get("explicit_history_tool_alias") or {}
    if alias:
        lines.extend(
            [
                f"Explicit history-tool alias: {alias.get('matched_alias')}",
                f"Canonical history command: {alias.get('canonical_command')}",
                f"Alias guidance: {alias.get('guidance')}",
            ]
        )
    handoff = report.get("answer_ready_handoff") or {}
    if handoff:
        lines.extend(
            [
                f"Answer-ready after: {', '.join(handoff.get('required_evidence', []))}",
                f"Answer facets: {'; '.join(handoff.get('answer_facets', []))}",
                f"Required-read stop: {handoff.get('stop')}",
                f"Answer boundary: {handoff.get('answer_boundary')}",
                f"Optional deeper reads only if: {handoff.get('only_continue_if')}",
            ]
        )
    packet = report.get("method_selection_packet") or {}
    if packet:
        lines.extend(_render_method_selection_packet_text(packet))
    lines.append("Next commands:")
    for item in report.get("next_commands", []):
        lines.append(f"- {item.get('purpose')}: {item.get('command')}")
        lines.append(f"  Inspect: {item.get('inspect_next')}")
    cookbook = report.get("command_cookbook", [])
    if cookbook:
        lines.append("Agent command cookbook:")
        for item in cookbook[:6]:
            lines.append(f"- {item.get('question_family')}: {item.get('first_command')}")
            lines.append(f"  Inspect: {item.get('inspect_next')}")
    lines.append("Inspect after:")
    for item in report.get("inspect_after", []):
        lines.append(f"- {item}")
    lines.extend(
        [
            f"Stop guidance: {report.get('stop_guidance')}",
            f"Manual search guidance: {report.get('manual_search_guidance')}",
            f"Boundary: {report.get('boundary')}",
        ]
    )
    return "\n".join(lines)


def _render_method_selection_packet_text(packet: dict) -> list[str]:
    lines = [
        "Method selection packet:",
        f"- schema_version: {packet.get('schema_version')}",
        f"- route_family: {packet.get('route_family')}",
        f"- method_id: {packet.get('method_id')}",
        f"- cap_language_required: {packet.get('cap_language_required')}",
        f"- single_flight_policy: {packet.get('single_flight_policy')}",
        f"- secondary_policy: {packet.get('secondary_policy')}",
        f"- stop_rule: {packet.get('stop_rule')}",
        f"- continue_only_if: {packet.get('continue_only_if')}",
    ]
    if packet.get("first_command"):
        lines.insert(4, f"- first_command: {packet.get('first_command')}")
    for field in ("forbidden_first_moves", "required_surfaces", "read_order"):
        values = packet.get(field) or []
        lines.append(f"- {field}:")
        for value in values:
            lines.append(f"  - {value}")
    return lines


def render_bulk_line_match_text(payload: dict) -> str:
    report = payload["bulk_line_match"]
    scope = report.get("searched_scope") or {}
    lines = [
        "Bulk Line Match",
        f"Status: {report.get('status')}",
        f"Candidate role: {report.get('candidate_role')}",
        f"Validation: {report.get('validation_guidance')}",
        f"Phrases: {report.get('phrase_count', 0)}",
        (
            "Searched: "
            f"{scope.get('searched_files', 0)} files, "
            f"{scope.get('searched_lines', 0)} lines, "
            f"{scope.get('searched_bytes', 0)} bytes"
        ),
        f"Caps: {', '.join(scope.get('cap_labels') or []) or '(none)'}",
        f"Candidates: {report.get('candidate_count', 0)}",
    ]
    for candidate in report.get("candidates", [])[:20]:
        lines.append(
            "- "
            f"{candidate.get('phrase_id')} {candidate.get('match_status')} {candidate.get('match_strength')} "
            f"{candidate.get('target')} fields={','.join(candidate.get('matched_fields') or [])}"
        )
        if candidate.get("snippet"):
            lines.append(f"  {candidate.get('snippet')}")
    if report.get("validation_commands"):
        lines.append("Validation commands:")
        for command in report.get("validation_commands", []):
            lines.append(f"- {command}")
    for warning in scope.get("warnings") or []:
        lines.append(f"Warning: {warning}")
    for blind in scope.get("blind_spots") or []:
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_ask_answer_text(payload: dict) -> str:
    report = payload["ask_answer"]
    route = report.get("selected_route") or {}
    lines = [
        "Ask Answer",
        f"Query: {report.get('query')}",
        f"Route family: {route.get('route_family')}",
        f"Answer status: {report.get('answer_status')}",
    ]
    if report.get("answer"):
        lines.append(f"Answer: {report.get('answer')}")
    if report.get("fail_closed_reason"):
        lines.append(f"Cannot answer directly yet: {report.get('fail_closed_reason')}")
    refs = report.get("refs_used") or []
    if refs:
        lines.append("Refs used:")
        for item in refs:
            bits = [str(item.get("target") or "(unknown target)")]
            if item.get("kind"):
                bits.append(f"kind={item.get('kind')}")
            if item.get("proof_surface"):
                bits.append(f"proof_surface={item.get('proof_surface')}")
            if item.get("target_source_role"):
                bits.append(f"target_source_role={item.get('target_source_role')}")
            lines.append(f"- {'; '.join(bits)}")
    proof = report.get("proof_summary") or []
    if proof:
        lines.append("Proof summary:")
        for item in proof[:6]:
            lines.append(f"- {item}")
    if report.get("next_proof_step"):
        lines.append(f"Next proof step: {report.get('next_proof_step')}")
    source_contract = report.get("source_contract") or {}
    if source_contract:
        lines.append(
            "Source contract: "
            f"{source_contract.get('built_from')} "
            f"answer_action_status={source_contract.get('answer_action_status')} "
            f"promotion_status={source_contract.get('promotion_status')}"
        )
    return "\n".join(lines)


def render_ask_execute_text(payload: dict) -> str:
    report = payload["ask_execute"]
    route = report.get("selected_route") or {}
    resolution = route.get("command_resolution") or {}
    cli_prefix = resolution.get("command_prefix") or "jsonl-history-search"
    execution = report.get("execution") or {}
    action = report.get("answer_action_contract") or {}
    terminal_answer_and_stop = action.get("terminal_answer_and_stop_contract") or {}
    latest_status_contract = _ask_execute_latest_status_contract(
        action, report.get("executed_output") or {}
    )
    first_command = route.get("first_command")
    single_flight = _ask_execute_single_flight_contract(action)
    if (
        execution.get("status") == "report_output_unavailable"
        and action.get("answer_status") == "not-ready/missing-source-session-proof"
    ):
        first_command = "(not needed; run only the retained-source-read actions below)"
    lines = [
        "Ask Execute",
        f"Query: {report.get('query')}",
        f"Chosen route: {route.get('chosen_route')}",
        f"Route family: {route.get('route_family')}",
        f"Execution status: {execution.get('status')}",
        f"Command family: {execution.get('command_family') or '(unsupported)'}",
    ]
    lines.extend(_original_prompt_identity_lines(report.get("original_prompt_identity") or {}, markdown=False))
    if single_flight:
        lines.append(
            "Route already executed: selected semantic-debug route completed by ask --execute; "
            "do not run it again by default."
        )
    elif first_command and terminal_answer_and_stop.get("status") != "terminal_answer_and_stop":
        lines.append(f"First command: {first_command}")
    lines.append(f"Command resolution: {resolution.get('mode') or 'unresolved'}")
    packet = route.get("method_selection_packet") or {}
    if packet:
        if single_flight:
            packet = {key: value for key, value in packet.items() if key != "first_command"}
        lines.extend(_render_method_selection_packet_text(packet))
    if execution.get("report_written"):
        lines.append(f"Report written: {execution.get('report_written')}")
    if execution.get("report_output_status") == "unavailable":
        lines.append(f"Report output unavailable: {execution.get('reason')}")
    if execution.get("status") == "report_output_unavailable":
        lines.append(f"Report output unavailable: {execution.get('reason')}")
        if execution.get("fallback_command") and first_command == route.get("first_command"):
            lines.append(f"Fallback command: {execution.get('fallback_command')}")
    if execution.get("status") == "unsupported_route":
        lines.append(f"Unsupported route: {execution.get('reason')}")
        lines.append(f"Stop condition: {route.get('stop_guidance')}")
        handoff = route.get("answer_ready_handoff") or {}
        if handoff:
            lines.append(f"Answer-ready after: {', '.join(handoff.get('required_evidence', []))}")
            lines.append(f"Required-read stop: {handoff.get('stop')}")
            lines.append(f"Answer boundary: {handoff.get('answer_boundary')}")
            lines.append(f"Optional deeper reads only if: {handoff.get('only_continue_if')}")
        if route.get("next_commands"):
            lines.append("Bounded next commands:")
            for item in route["next_commands"]:
                lines.append(f"- {item.get('purpose')}: {item.get('command')}")
                lines.append(f"  Inspect: {item.get('inspect_next')}")
        return "\n".join(lines)
    lines.extend(
        [
            f"Answer status: {action.get('answer_status')}",
            f"Promotion status: {action.get('promotion_status')}",
        ]
    )
    lines.extend(render_investigation_finalization_state_text(action.get("investigation_finalization_state") or {}))
    workflow = action.get("receiving_agent_workflow") or {}
    if workflow:
        lines.append(f"Receiving-agent workflow: {workflow.get('mode')}")
        lines.append(f"Answer/continue boundary: {workflow.get('answer_or_continue')}")
        for item in workflow.get("prohibited_actions") or []:
            lines.append(f"- Prohibited: {item}")
    continuation_first = action.get("continuation_first_contract") or {}
    if continuation_first:
        lines.append(f"Continuation-first status: {continuation_first.get('status')}")
        lines.append(f"Ask user now: {str(bool(continuation_first.get('ask_user_now'))).lower()}")
        lines.append(f"Missing facet: {continuation_first.get('missing_facet')}")
    continuation_packet = action.get("canonical_continuation_packet") or {}
    if continuation_packet:
        lines.append(f"Canonical continuation packet: {continuation_packet.get('packet_id')}")
        lines.append("Run every packet step in order; partial step results are not answer material.")
        for index, step in enumerate(continuation_packet.get("steps") or [], 1):
            command = step.get("command") if isinstance(step, dict) else None
            if command:
                lines.append(f"Packet step {index}: {command}")
        blockers = list(continuation_packet.get("blockers") or [])
        if blockers:
            lines.append(f"Packet blockers: {json.dumps(blockers, sort_keys=True)}")
    _append_latest_status_followthrough_contract(
        lines,
        latest_status_contract,
    )
    if action.get("stop") != single_flight.get("stop_rule"):
        lines.append(f"Stop condition: {action.get('stop')}")
    if action.get("promotion_reason"):
        lines.append(f"Promotion reason: {action.get('promotion_reason')}")
    _append_ask_execute_single_flight_contract(lines, single_flight)
    _append_provenance_missing_facets(lines, action)
    _append_answer_handoff_text(lines, report.get("answer_handoff"))
    if not _append_remembered_user_missing_evidence_contract(lines, action):
        _append_scoped_no_evidence_contract(lines, action, report.get("executed_output") or {})
    lines.append("Read/action contract:")
    if latest_status_contract.get("navigation_only"):
        lines.append(
            "- Canonical follow-through path: "
            f"{latest_status_contract.get('canonical_follow_through_path')}"
        )
    else:
        _append_compact_read_contract(lines, action.get("read"), cli_prefix=cli_prefix)
    if action.get("conditional_required") and route.get("route_family") != "semantic_wrapup":
        lines.append(
            "Conditional required (secondary; run only if bounded reads leave required coverage incomplete):"
        )
        _append_compact_read_contract(
            lines,
            action.get("conditional_required"),
            max_items=None,
            cli_prefix=cli_prefix,
        )
    only_continue = action.get("only_continue_if")
    if only_continue:
        lines.append("Only continue if:")
        _append_compact_read_contract(lines, only_continue, cli_prefix=cli_prefix)
    executed = report.get("executed_output") or {}
    if route.get("route_family") == "devlog_failure_analysis":
        output_contract = executed.get("output_contract") or {}
        if output_contract:
            lines.append(
                "Output contract: stdout is readable text; report-output and linked reports are "
                f"{output_contract.get('report_output_format') or 'JSON'} sidecars."
            )
            lines.append(f"jq rule: {output_contract.get('jq_rule')}")
    if executed.get("status"):
        lines.append(f"Executed output status: {executed.get('status')}")
    if executed.get("message"):
        lines.append(f"Executed output: {executed.get('message')}")
    if route.get("route_family") == "semantic_wrapup":
        _append_semantic_wrapup_execute_text(lines, executed, action, cli_prefix=cli_prefix)
    if route.get("route_family") == "coordinator_context":
        _append_coordinator_context_text(lines, executed, action)
    if route.get("route_family") == "artifact_currentness":
        _append_artifact_currentness_execute_text(lines, executed)
    return "\n".join(lines)


def render_ask_answer_markdown(payload: dict) -> str:
    report = payload["ask_answer"]
    route = report.get("selected_route") or {}
    lines = [
        "# JSONL Ask Answer",
        "",
        f"- Query: `{report.get('query')}`",
        f"- Route family: `{route.get('route_family')}`",
        f"- Answer status: `{report.get('answer_status')}`",
    ]
    if report.get("answer"):
        lines.extend(["", "## Answer", "", str(report.get("answer"))])
    if report.get("fail_closed_reason"):
        lines.extend(["", "## Cannot Answer Directly Yet", "", str(report.get("fail_closed_reason"))])
    refs = report.get("refs_used") or []
    if refs:
        lines.extend(["", "## Refs Used"])
        for item in refs:
            target = item.get("target") or "(unknown target)"
            details = []
            for key in ("kind", "proof_surface", "target_source_role"):
                if item.get(key):
                    details.append(f"{key}=`{item.get(key)}`")
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"- `{target}`{suffix}")
    proof = report.get("proof_summary") or []
    if proof:
        lines.extend(["", "## Proof Summary"])
        for item in proof[:6]:
            lines.append(f"- {item}")
    if report.get("next_proof_step"):
        lines.extend(["", "## Next Proof Step", "", f"`{report.get('next_proof_step')}`"])
    return "\n".join(lines)


def render_ask_execute_markdown(payload: dict) -> str:
    report = payload["ask_execute"]
    route = report.get("selected_route") or {}
    resolution = route.get("command_resolution") or {}
    cli_prefix = resolution.get("command_prefix") or "jsonl-history-search"
    execution = report.get("execution") or {}
    action = report.get("answer_action_contract") or {}
    terminal_answer_and_stop = action.get("terminal_answer_and_stop_contract") or {}
    latest_status_contract = _ask_execute_latest_status_contract(
        action, report.get("executed_output") or {}
    )
    first_command = route.get("first_command")
    single_flight = _ask_execute_single_flight_contract(action)
    if (
        execution.get("status") == "report_output_unavailable"
        and action.get("answer_status") == "not-ready/missing-source-session-proof"
    ):
        first_command = "(not needed; run only the retained-source-read actions below)"
    lines = [
        "# JSONL Ask Execute",
        "",
        f"- Query: `{report.get('query')}`",
        f"- Chosen route: `{route.get('chosen_route')}`",
        f"- Route family: `{route.get('route_family')}`",
        f"- Execution status: `{execution.get('status')}`",
        f"- Command family: `{execution.get('command_family') or '(unsupported)'}`",
    ]
    lines.extend(_original_prompt_identity_lines(report.get("original_prompt_identity") or {}, markdown=True))
    if single_flight:
        lines.append(
            "- Route already executed: selected `semantic-debug` route completed by `ask --execute`; "
            "do not run it again by default."
        )
    elif first_command and terminal_answer_and_stop.get("status") != "terminal_answer_and_stop":
        lines.append(f"- First command: `{first_command}`")
    lines.append(f"- Command resolution: `{resolution.get('mode') or 'unresolved'}`")
    packet = route.get("method_selection_packet") or {}
    if packet:
        if single_flight:
            packet = {key: value for key, value in packet.items() if key != "first_command"}
        lines.extend(_render_method_selection_packet_markdown(packet))
    if execution.get("report_written"):
        lines.append(f"- Report written: `{execution.get('report_written')}`")
    if execution.get("report_output_status") == "unavailable":
        lines.append(f"- Report output unavailable: {execution.get('reason')}")
    if execution.get("status") == "report_output_unavailable":
        lines.append(f"- Report output unavailable: {execution.get('reason')}")
        if execution.get("fallback_command") and first_command == route.get("first_command"):
            lines.append(f"- Fallback command: `{execution.get('fallback_command')}`")
    lines.extend(
        [
            "",
            "## Answer Action Contract",
            "",
            f"- Answer status: `{action.get('answer_status')}`",
            f"- Promotion status: `{action.get('promotion_status')}`",
        ]
    )
    lines.extend(render_investigation_finalization_state_markdown(action.get("investigation_finalization_state") or {}))
    workflow = action.get("receiving_agent_workflow") or {}
    if workflow:
        lines.append(f"- Receiving-agent workflow: `{workflow.get('mode')}`")
        lines.append(f"- Answer/continue boundary: {workflow.get('answer_or_continue')}")
        for item in workflow.get("prohibited_actions") or []:
            lines.append(f"- Prohibited: {item}")
    continuation_first = action.get("continuation_first_contract") or {}
    if continuation_first:
        lines.append(f"- Continuation-first status: `{continuation_first.get('status')}`")
        lines.append(
            f"- Ask user now: `{str(bool(continuation_first.get('ask_user_now'))).lower()}`"
        )
        lines.append(f"- Missing facet: {continuation_first.get('missing_facet')}")
    continuation_packet = action.get("canonical_continuation_packet") or {}
    if continuation_packet:
        lines.extend(
            [
                f"- Canonical continuation packet: `{continuation_packet.get('packet_id')}`",
                "- Run every packet step in order; partial step results are not answer material.",
            ]
        )
        for index, step in enumerate(continuation_packet.get("steps") or [], 1):
            command = step.get("command") if isinstance(step, dict) else None
            if command:
                lines.append(f"- Packet step {index}: `{command}`")
        blockers = list(continuation_packet.get("blockers") or [])
        if blockers:
            lines.append(f"- Packet blockers: `{json.dumps(blockers, sort_keys=True)}`")
    _append_latest_status_followthrough_contract(
        lines,
        latest_status_contract,
        markdown=True,
    )
    if action.get("stop") != single_flight.get("stop_rule"):
        lines.append(f"- Stop condition: {action.get('stop')}")
    if action.get("promotion_reason"):
        lines.append(f"- Promotion reason: {action.get('promotion_reason')}")
    _append_ask_execute_single_flight_contract(lines, single_flight, markdown=True)
    _append_provenance_missing_facets(lines, action, markdown=True)
    _append_answer_handoff_markdown(lines, report.get("answer_handoff"))
    if not _append_remembered_user_missing_evidence_contract(lines, action, markdown=True):
        _append_scoped_no_evidence_contract(
            lines,
            action,
            report.get("executed_output") or {},
            markdown=True,
        )
    if execution.get("status") == "unsupported_route":
        lines.append(f"- Unsupported route: {execution.get('reason')}")
        handoff = route.get("answer_ready_handoff") or {}
        if handoff:
            lines.extend(
                [
                    f"- Answer-ready after: {', '.join(handoff.get('required_evidence', []))}",
                    f"- Required-read stop: {handoff.get('stop')}",
                    f"- Answer boundary: {handoff.get('answer_boundary')}",
                    f"- Optional deeper reads only if: {handoff.get('only_continue_if')}",
                ]
            )
        if route.get("next_commands"):
            lines.extend(["", "## Bounded Next Commands", ""])
            for item in route["next_commands"]:
                lines.append(f"- **{item.get('purpose')}**: `{item.get('command')}`")
                lines.append(f"  Inspect: {item.get('inspect_next')}")
    else:
        lines.append("- Read/action:")
        if latest_status_contract.get("navigation_only"):
            lines.append(
                "- Canonical follow-through path: "
                f"`{latest_status_contract.get('canonical_follow_through_path')}`"
            )
        else:
            _append_compact_read_contract(lines, action.get("read"), markdown=True, cli_prefix=cli_prefix)
        if action.get("conditional_required") and route.get("route_family") != "semantic_wrapup":
            lines.append(
                "- Conditional required (secondary; run only if bounded reads leave required coverage incomplete):"
            )
            _append_compact_read_contract(
                lines,
                action.get("conditional_required"),
                markdown=True,
                max_items=None,
                cli_prefix=cli_prefix,
            )
    if route.get("route_family") == "semantic_wrapup":
        _append_semantic_wrapup_execute_markdown(
            lines,
            report.get("executed_output") or {},
            action,
            cli_prefix=cli_prefix,
        )
    if route.get("route_family") == "coordinator_context":
        _append_coordinator_context_markdown(lines, report.get("executed_output") or {}, action)
    if route.get("route_family") == "artifact_currentness":
        _append_artifact_currentness_execute_markdown(lines, report.get("executed_output") or {})
    return "\n".join(lines)


def _ask_execute_single_flight_contract(action: dict) -> dict:
    contract = action.get("single_flight_contract") if isinstance(action, dict) else None
    if not isinstance(contract, dict):
        return {}
    if contract.get("route_already_executed") is not True:
        return {}
    if (
        action.get("answer_status") != "answer-ready-after-source-inspection"
        and action.get("promotion_status") != "directional_history_reads_required"
    ):
        return {}
    if not action.get("read"):
        return {}
    return contract


def _ask_execute_latest_status_contract(action: dict, executed: dict) -> dict:
    contract = action.get("latest_status_followthrough_contract")
    if isinstance(contract, dict) and contract:
        return contract
    contract = executed.get("latest_status_followthrough_contract")
    if isinstance(contract, dict):
        return contract
    return {}


def _append_latest_status_followthrough_contract(
    lines: list[str],
    contract: dict,
    *,
    markdown: bool = False,
) -> None:
    if not contract:
        return
    guidance = contract.get("next_read_guidance") if isinstance(contract.get("next_read_guidance"), dict) else {}
    interval = (
        contract.get("post_anchor_interval_reconstruction")
        if isinstance(contract.get("post_anchor_interval_reconstruction"), dict)
        else {}
    )
    coverage = interval.get("coverage") if isinstance(interval.get("coverage"), dict) else {}
    read_order = " -> ".join(str(item) for item in contract.get("required_read_order") or [])
    if markdown:
        lines.extend(
            [
                "",
                "## Latest-status follow-through",
                "",
                f"- Contract: `{contract.get('schema_version')}`",
                f"- Status: `{contract.get('status')}`",
                (
                    "- Post-anchor interval coverage: "
                    f"`status={contract.get('coverage_status') or coverage.get('status')}` "
                    f"`complete={contract.get('coverage_complete') if contract.get('coverage_complete') is not None else coverage.get('complete')}` "
                    f"`omitted={contract.get('omitted_activity_entry_count', 0)}`"
                ),
                f"- Required read order: {read_order}",
                (
                    "- Required post-anchor interval refs: "
                    f"`{contract.get('post_anchor_interval_read_count', 0)}`"
                ),
                f"- Navigation only: `{contract.get('navigation_only')}`",
                f"- Answer from report allowed: `{contract.get('answer_from_report_allowed')}`",
                (
                    "- Required primary windows opened: "
                    f"`{contract.get('required_primary_windows_opened')}` "
                    f"(`{contract.get('opened_required_primary_window_count', 0)}`/"
                    f"`{contract.get('required_primary_window_count', 0)}`)"
                ),
            ]
        )
        if contract.get("canonical_follow_through_path"):
            lines.append(
                "- Canonical follow-through path: "
                f"`{contract.get('canonical_follow_through_path')}`"
            )
        if guidance:
            lines.append(
                "- `post_anchor_interval_reconstruction.next_read_guidance`: "
                f"`status={guidance.get('status')}` {guidance.get('policy')}"
            )
        lines.append(f"- Stop before answer: {contract.get('stop')}")
        return
    lines.append("Latest-status follow-through:")
    lines.append(f"Contract: {contract.get('schema_version')} status={contract.get('status')}")
    lines.append(
        "Post-anchor interval coverage: "
        f"status={contract.get('coverage_status') or coverage.get('status')} "
        f"complete={contract.get('coverage_complete') if contract.get('coverage_complete') is not None else coverage.get('complete')} "
        f"omitted={contract.get('omitted_activity_entry_count', 0)}"
    )
    if read_order:
        lines.append(f"Required read order: {read_order}")
    lines.append(f"Required post-anchor interval refs: {contract.get('post_anchor_interval_read_count', 0)}")
    lines.append(
        "Generated report boundary: "
        f"navigation_only={contract.get('navigation_only')} "
        f"answer_from_report_allowed={contract.get('answer_from_report_allowed')} "
        f"required_primary_windows_opened={contract.get('required_primary_windows_opened')} "
        f"opened={contract.get('opened_required_primary_window_count', 0)}/"
        f"{contract.get('required_primary_window_count', 0)}"
    )
    if contract.get("canonical_follow_through_path"):
        lines.append(
            "Canonical follow-through path: "
            f"{contract.get('canonical_follow_through_path')}"
        )
    if guidance:
        lines.append(
            "post_anchor_interval_reconstruction.next_read_guidance: "
            f"status={guidance.get('status')} policy={guidance.get('policy')}"
        )
    lines.append(f"Stop before answer: {contract.get('stop')}")


def _append_ask_execute_single_flight_contract(
    lines: list[str],
    contract: dict,
    *,
    markdown: bool = False,
) -> None:
    if not contract:
        return
    do_not_rerun = ", ".join(str(item) for item in contract.get("do_not_rerun") or [])
    continue_only_if = [
        str(item)
        for item in contract.get("continue_only_if") or []
        if str(item).strip()
    ]
    if markdown:
        lines.extend(
            [
                f"- Single-flight status: `{contract.get('status')}`",
                (
                    "- Default action: read the selected primary refs first, then answer if the "
                    "required facets are supported."
                ),
            ]
        )
        if do_not_rerun:
            lines.append(f"- Do not rerun by default: {do_not_rerun}.")
        lines.append(
            "- Diagnostics: secondary/current/post-hoc refs stay in JSON/report for named gaps or review; "
            "they are not default follow-through."
        )
        if contract.get("stop_rule"):
            lines.append(f"- Stop rule: {contract.get('stop_rule')}")
        if continue_only_if:
            lines.append(f"- Continue only if: {'; '.join(continue_only_if)}.")
        return
    lines.append(f"Single-flight status: {contract.get('status')}")
    lines.append(
        "Default action: read the selected primary refs first, then answer if the required facets are supported."
    )
    if do_not_rerun:
        lines.append(f"Do not rerun by default: {do_not_rerun}.")
    lines.append(
        "Diagnostics: secondary/current/post-hoc refs stay in JSON/report for named gaps or review; "
        "they are not default follow-through."
    )
    if contract.get("stop_rule"):
        lines.append(f"Stop rule: {contract.get('stop_rule')}")
    if continue_only_if:
        lines.append(f"Continue only if: {'; '.join(continue_only_if)}.")


def _append_provenance_missing_facets(
    lines: list[str],
    action: dict,
    *,
    markdown: bool = False,
) -> None:
    handoff = action.get("provenance_complete_proof_handoff") or {}
    missing = list(handoff.get("missing_required_facets") or [])
    if not handoff.get("applies") or not missing:
        return
    prefix = "- Missing required facets:" if markdown else "Missing required facets:"
    lines.append(f"{prefix} {', '.join(str(facet) for facet in missing)}")


def _append_answer_handoff_text(lines: list[str], handoff: object) -> None:
    if not isinstance(handoff, dict):
        return
    lines.extend(
        [
            "Answer handoff:",
            f"- status: {handoff.get('answer_status')}",
        ]
    )
    if handoff.get("answer"):
        lines.append(f"- answer: {handoff.get('answer')}")
    source_root = _answer_handoff_source_root(handoff)
    if source_root:
        lines.append(f"- validated source root: {source_root}")
    refs = handoff.get("refs_used") or []
    if refs:
        lines.append("- validated refs:")
        for item in refs[:4]:
            lines.append(f"  - {_answer_handoff_openable_target(item, source_root)}")


def _append_answer_handoff_markdown(lines: list[str], handoff: object) -> None:
    if not isinstance(handoff, dict):
        return
    lines.extend(
        [
            "",
            "## Answer Handoff",
            "",
            f"- Status: `{handoff.get('answer_status')}`",
        ]
    )
    if handoff.get("answer"):
        lines.extend(["", str(handoff.get("answer"))])
    source_root = _answer_handoff_source_root(handoff)
    if source_root:
        lines.extend(["", f"Validated source root: `{source_root}`"])
    refs = handoff.get("refs_used") or []
    if refs:
        lines.extend(["", "Validated refs:"])
        for item in refs[:4]:
            target = _answer_handoff_openable_target(item, source_root)
            lines.append(f"- [`{item.get('target')}`](<{target}>)")


def _answer_handoff_source_root(handoff: dict) -> str:
    value = str(handoff.get("cwd") or "").strip()
    return str(Path(value).expanduser()) if value else ""


def _answer_handoff_openable_target(item: object, source_root: str) -> str:
    if not isinstance(item, dict):
        return ""
    target = str(item.get("openable_target") or item.get("target") or "").strip()
    if not target or not source_root:
        return target
    match = re.match(r"^(.+):(\d+)$", target)
    path_text = match.group(1) if match else target
    suffix = f":{match.group(2)}" if match else ""
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path(source_root) / path
    return f"{path.resolve()}{suffix}"


def _append_artifact_currentness_execute_text(lines: list[str], executed: dict) -> None:
    cleanup = executed.get("cleanup_causality") or {}
    lines.append("Worktree cleanup causality:")
    lines.append(f"- debt_status: {cleanup.get('debt_status')}")
    lines.append(f"- summary: {cleanup.get('causal_summary')}")
    for record in cleanup.get("operational_records") or []:
        lines.append(
            f"- {record.get('causal_source_role') or record.get('source_role')}: "
            f"{record.get('source_ref')} - {record.get('relationship')} "
            f"[{record.get('source_authority') or 'unclassified'}]"
        )
    roles = executed.get("source_roles_present") or []
    if roles:
        lines.append(f"- source_roles_present: {', '.join(roles)}")
    for item in executed.get("worktree_classifications") or []:
        lines.append(
            f"- worktree: {item.get('identifier')} {item.get('classification')} "
            f"path={item.get('path')}"
        )


def _append_artifact_currentness_execute_markdown(lines: list[str], executed: dict) -> None:
    cleanup = executed.get("cleanup_causality") or {}
    lines.extend(
        [
            "",
            "## Worktree Cleanup Causality",
            "",
            f"- Debt status: `{cleanup.get('debt_status')}`",
            f"- Summary: {cleanup.get('causal_summary')}",
        ]
    )
    for record in cleanup.get("operational_records") or []:
        lines.append(
            f"- `{record.get('causal_source_role') or record.get('source_role')}` `{record.get('source_ref')}`: "
            f"{record.get('relationship')} [`{record.get('source_authority') or 'unclassified'}`]"
        )
    roles = executed.get("source_roles_present") or []
    if roles:
        lines.append(f"- Source roles present: `{', '.join(roles)}`")
    for item in executed.get("worktree_classifications") or []:
        lines.append(
            f"- Worktree `{item.get('identifier')}`: `{item.get('classification')}` "
            f"(`{item.get('path')}`)"
        )


def _append_semantic_wrapup_execute_text(
    lines: list[str],
    executed: dict,
    action: dict,
    *,
    cli_prefix: str = "jsonl-history-search",
) -> None:
    lines.append("Semantic-wrapup evidence handoff:")
    lines.append(f"- saved_report: {action.get('saved_report') or executed.get('report_written')}")
    boundary = action.get("source_inspection_boundary") or executed.get("source_inspection_boundary") or {}
    if boundary:
        lines.append(
            "- source_inspection_boundary: "
            f"inspect_required_primary_proof_before_answer="
            f"{boundary.get('inspect_required_primary_proof_before_answer')} "
            f"optional_diagnostics_default_actionable={boundary.get('optional_diagnostics_default_actionable')}"
        )
    read_summary = executed.get("read_role_summary") or action.get("read_role_summary") or {}
    if read_summary:
        lines.append(
            "- read_role_summary: "
            f"required_primary_proof={read_summary.get('required_primary_proof_count')} "
            f"optional_diagnostic={read_summary.get('optional_diagnostic_count')} "
            f"conditional_required={read_summary.get('conditional_required_count')}"
        )
    _append_semantic_wrapup_failure_modes(lines, action.get("failure_modes") or executed.get("failure_modes"))
    _append_semantic_wrapup_fix_suggestions(lines, action.get("fix_suggestions") or executed.get("fix_suggestions"))
    lines.append("- required_primary_proof:")
    required_primary_proof = executed.get("required_primary_proof")
    if required_primary_proof is None:
        required_primary_proof = action.get("read")
    _append_compact_read_contract(
        lines,
        required_primary_proof,
        cli_prefix=cli_prefix,
    )
    conditional_required = action.get("conditional_required") or executed.get("conditional_required")
    if conditional_required:
        lines.append("- conditional_required:")
        _append_compact_read_contract(lines, conditional_required, max_items=None, cli_prefix=cli_prefix)
    diagnostics = action.get("diagnostics_after")
    if diagnostics:
        lines.append("- diagnostics_after:")
        _append_compact_read_contract(lines, diagnostics, cli_prefix=cli_prefix)


def _append_semantic_wrapup_execute_markdown(
    lines: list[str],
    executed: dict,
    action: dict,
    *,
    cli_prefix: str = "jsonl-history-search",
) -> None:
    lines.extend(["", "## Semantic-Wrapup Evidence Handoff", ""])
    lines.append(f"- Saved report: `{action.get('saved_report') or executed.get('report_written')}`")
    boundary = action.get("source_inspection_boundary") or executed.get("source_inspection_boundary") or {}
    if boundary:
        lines.append(
            "- Source inspection boundary: "
            f"`inspect_required_primary_proof_before_answer="
            f"{boundary.get('inspect_required_primary_proof_before_answer')}`, "
            f"`optional_diagnostics_default_actionable={boundary.get('optional_diagnostics_default_actionable')}`"
        )
    read_summary = executed.get("read_role_summary") or action.get("read_role_summary") or {}
    if read_summary:
        lines.append(
            "- Read role summary: "
            f"`required_primary_proof={read_summary.get('required_primary_proof_count')}`, "
            f"`optional_diagnostic={read_summary.get('optional_diagnostic_count')}`, "
            f"`conditional_required={read_summary.get('conditional_required_count')}`"
        )
    _append_semantic_wrapup_failure_modes(lines, action.get("failure_modes") or executed.get("failure_modes"))
    _append_semantic_wrapup_fix_suggestions(lines, action.get("fix_suggestions") or executed.get("fix_suggestions"))
    lines.append("- Required primary proof:")
    required_primary_proof = executed.get("required_primary_proof")
    if required_primary_proof is None:
        required_primary_proof = action.get("read")
    _append_compact_read_contract(
        lines,
        required_primary_proof,
        markdown=True,
        cli_prefix=cli_prefix,
    )
    conditional_required = action.get("conditional_required") or executed.get("conditional_required")
    if conditional_required:
        lines.append("- Conditional required:")
        _append_compact_read_contract(
            lines,
            conditional_required,
            markdown=True,
            max_items=None,
            cli_prefix=cli_prefix,
        )
    diagnostics = action.get("diagnostics_after")
    if diagnostics:
        lines.append("- Diagnostics after:")
        _append_compact_read_contract(lines, diagnostics, markdown=True, cli_prefix=cli_prefix)


def _append_semantic_wrapup_failure_modes(lines: list[str], modes: object) -> None:
    lines.append("- failure_modes:")
    if not isinstance(modes, list) or not modes:
        lines.append("  - none")
        return
    for mode in modes[:5]:
        if not isinstance(mode, dict):
            lines.append(f"  - {mode}")
            continue
        label = mode.get("failure_mode") or mode.get("title") or "(unknown)"
        title = mode.get("title")
        count = mode.get("evidence_count")
        rendered = f"{label}"
        if title and title != label:
            rendered = f"{rendered} - {title}"
        if count is not None:
            rendered = f"{rendered} (evidence_count={count})"
        lines.append(f"  - {rendered}")


def _append_semantic_wrapup_fix_suggestions(lines: list[str], suggestions: object) -> None:
    lines.append("- fix_suggestions:")
    if not isinstance(suggestions, list) or not suggestions:
        lines.append("  - none")
        return
    for suggestion in suggestions[:5]:
        if not isinstance(suggestion, dict):
            lines.append(f"  - {suggestion}")
            continue
        suggestion_id = suggestion.get("suggestion_id") or "(suggestion)"
        failure_mode = suggestion.get("failure_mode") or suggestion.get("issue_family") or "(unknown)"
        action = suggestion.get("proposed_action") or suggestion.get("doable_suggested_fix") or ""
        suffix = f" - {action}" if action else ""
        lines.append(f"  - {suggestion_id}: {failure_mode}{suffix}")


def _append_coordinator_context_text(lines: list[str], executed: dict, action: dict) -> None:
    lines.append("Coordinator context package:")
    active = executed.get("active_task_handles") or []
    lines.append(f"- active_task_handles: {', '.join(active[:6]) if active else '(none found)'}")
    lines.append("- required_next_reads:")
    _append_path_reason_rows(lines, executed.get("required_next_reads"), limit=8)
    current_state = executed.get("current_state") if isinstance(executed.get("current_state"), dict) else {}
    summary_lines = current_state.get("summary_lines") if isinstance(current_state.get("summary_lines"), list) else []
    if summary_lines:
        lines.append("- current_state:")
        _append_path_reason_rows(lines, summary_lines, limit=4)
    why = executed.get("why_goals_guiding_principles")
    if isinstance(why, dict):
        goal_rows = why.get("why_and_goals") if isinstance(why.get("why_and_goals"), list) else []
        principle_rows = why.get("guiding_principles") if isinstance(why.get("guiding_principles"), list) else []
        if goal_rows:
            lines.append("- why/goals:")
            _append_path_reason_rows(lines, goal_rows, limit=3)
        if principle_rows:
            lines.append("- guiding_principles:")
            _append_path_reason_rows(lines, principle_rows, limit=3)
    learnings = executed.get("learnings_failure_modes")
    if isinstance(learnings, dict):
        failure_rows = (
            learnings.get("failure_modes_to_watch")
            if isinstance(learnings.get("failure_modes_to_watch"), list)
            else []
        )
        if failure_rows:
            lines.append("- learnings/failure_modes_to_watch:")
            _append_path_reason_rows(lines, failure_rows, limit=5)
    boundaries = action.get("claim_boundaries") or executed.get("claim_boundaries") or []
    if boundaries:
        lines.append("- claim_boundaries:")
        for boundary in boundaries[:5]:
            lines.append(f"  - {boundary}")


def _append_coordinator_context_markdown(lines: list[str], executed: dict, action: dict) -> None:
    lines.extend(["", "## Coordinator Context Package", ""])
    active = executed.get("active_task_handles") or []
    lines.append(f"- Active handles: `{', '.join(active[:6]) if active else '(none found)'}`")
    lines.append("- Required next reads:")
    _append_path_reason_rows(lines, executed.get("required_next_reads"), limit=8, markdown=True)
    current_state = executed.get("current_state") if isinstance(executed.get("current_state"), dict) else {}
    summary_lines = current_state.get("summary_lines") if isinstance(current_state.get("summary_lines"), list) else []
    if summary_lines:
        lines.append("- Current state:")
        _append_path_reason_rows(lines, summary_lines, limit=4, markdown=True)
    why = executed.get("why_goals_guiding_principles")
    if isinstance(why, dict):
        goal_rows = why.get("why_and_goals") if isinstance(why.get("why_and_goals"), list) else []
        principle_rows = why.get("guiding_principles") if isinstance(why.get("guiding_principles"), list) else []
        if goal_rows:
            lines.append("- Why/goals:")
            _append_path_reason_rows(lines, goal_rows, limit=3, markdown=True)
        if principle_rows:
            lines.append("- Guiding principles:")
            _append_path_reason_rows(lines, principle_rows, limit=3, markdown=True)
    learnings = executed.get("learnings_failure_modes")
    if isinstance(learnings, dict):
        failure_rows = (
            learnings.get("failure_modes_to_watch")
            if isinstance(learnings.get("failure_modes_to_watch"), list)
            else []
        )
        if failure_rows:
            lines.append("- Learnings/failure modes to watch:")
            _append_path_reason_rows(lines, failure_rows, limit=5, markdown=True)
    boundaries = action.get("claim_boundaries") or executed.get("claim_boundaries") or []
    if boundaries:
        lines.append("- Claim boundaries:")
        for boundary in boundaries[:5]:
            lines.append(f"  - {boundary}")


def _append_path_reason_rows(
    lines: list[str],
    rows: object,
    *,
    limit: int,
    markdown: bool = False,
) -> None:
    if not isinstance(rows, list) or not rows:
        lines.append("  - none")
        return
    for row in rows[:limit]:
        if not isinstance(row, dict):
            lines.append(f"  - {row}")
            continue
        path = row.get("path") or row.get("target") or "(unknown)"
        line = f":{row.get('line')}" if row.get("line") else ""
        reason = row.get("reason") or row.get("text") or row.get("read_role") or ""
        exists = ""
        if "exists" in row:
            exists = f" exists={str(row.get('exists')).lower()}"
        if markdown:
            lines.append(f"  - `{path}{line}`{exists} - {reason}")
        else:
            lines.append(f"  - {path}{line}{exists} - {reason}")


def _append_scoped_no_evidence_contract(
    lines: list[str],
    action: dict,
    executed: dict,
    *,
    markdown: bool = False,
) -> None:
    contract = action.get("scoped_no_evidence_contract")
    if not isinstance(contract, dict):
        no_evidence = executed.get("no_evidence") if isinstance(executed.get("no_evidence"), dict) else {}
        if not no_evidence:
            return
        searched_scope = executed.get("searched_scope") if isinstance(executed.get("searched_scope"), dict) else {}
        scope_counts = executed.get("scope_counts") if isinstance(executed.get("scope_counts"), dict) else {}
        cap_hit = executed.get("cap_hit") if isinstance(executed.get("cap_hit"), dict) else {}
        read_more_policy = (
            no_evidence.get("read_more_policy") if isinstance(no_evidence.get("read_more_policy"), dict) else {}
        )
        contract = {
            "answer_scope": no_evidence.get("answer_scope"),
            "searched_files": searched_scope.get("searched_files"),
            "searched_bytes": searched_scope.get("searched_bytes"),
            "selected_root_count": scope_counts.get("selected_root_count"),
            "excluded_root_count": scope_counts.get("excluded_root_count"),
            "missing_root_count": scope_counts.get("missing_root_count"),
            "cap_hit": cap_hit.get("hit"),
            "caps_hit": cap_hit.get("caps_hit", []),
            "not_all_history_absence": no_evidence.get("not_all_history_absence"),
            "read_more_required_before_scoped_no_evidence_answer": read_more_policy.get(
                "required_before_scoped_no_evidence_answer"
            ),
            "read_more_actionability": read_more_policy.get("actionability"),
            "saved_report": executed.get("report_written"),
        }
    answer_now = action.get("answer_status") == "answer-scoped-no-evidence-now"
    terminal = action.get("terminal_answer_and_stop_contract") or {}
    cap_disposition = action.get("cap_hit_disposition") or {}
    if markdown:
        lines.extend(
            [
                "",
                "## Scoped No-Evidence Contract",
                "",
                f"- Answer now from searched scope: `{str(answer_now).lower()}`",
                f"- Answer scope: `{contract.get('answer_scope')}`",
                (
                    f"- Searched scope: files=`{contract.get('searched_files')}`, "
                    f"bytes=`{contract.get('searched_bytes')}`"
                ),
                (
                    f"- Missing/excluded scope: selected_roots=`{contract.get('selected_root_count')}`, "
                    f"excluded_roots=`{contract.get('excluded_root_count')}`, "
                    f"missing_roots=`{contract.get('missing_root_count')}`"
                ),
                f"- Cap status: hit=`{contract.get('cap_hit')}`, caps_hit=`{contract.get('caps_hit')}`",
                f"- not_all_history_absence: `{contract.get('not_all_history_absence')}`",
                f"- Source boundary: {contract.get('source_boundary')}",
                f"- Blind spots: `{contract.get('blind_spots')}`",
                (
                    "- read_more_required_before_scoped_no_evidence_answer: "
                    f"`{contract.get('read_more_required_before_scoped_no_evidence_answer')}`"
                ),
                f"- Saved report: `{contract.get('saved_report') or action.get('saved_report')}`",
            ]
        )
        if terminal.get("status") == "terminal_answer_and_stop":
            lines.extend(
                [
                    "- **TERMINAL ANSWER-AND-STOP:** answer from this searched scope now.",
                    "- Do not open weak candidates, rerun retrieval, or fall back to manual search.",
                ]
            )
        elif cap_disposition.get("status") == "scoped_fail_closed":
            lines.extend(
                [
                    "- **CAP DISPOSITION: scoped_fail_closed**",
                    "- Partial candidates and snippets are withheld because hidden evidence may remain.",
                    "- No canonical continuation is available; do not improvise with manual history search.",
                ]
            )
        return
    lines.extend(
        [
            "Scoped no-evidence contract:",
            f"- answer_now_from_searched_scope: {str(answer_now).lower()}",
            f"- answer_scope: {contract.get('answer_scope')}",
            (
                f"- searched_scope: searched_files={contract.get('searched_files')} "
                f"searched_bytes={contract.get('searched_bytes')}"
            ),
            (
                f"- missing/excluded scope: selected_root_count={contract.get('selected_root_count')} "
                f"excluded_root_count={contract.get('excluded_root_count')} "
                f"missing_root_count={contract.get('missing_root_count')}"
            ),
            f"- cap_status: hit={contract.get('cap_hit')} caps_hit={contract.get('caps_hit')}",
            f"- not_all_history_absence: {contract.get('not_all_history_absence')}",
            f"- source_boundary: {contract.get('source_boundary')}",
            f"- blind_spots: {contract.get('blind_spots')}",
            (
                "- read_more_required_before_scoped_no_evidence_answer: "
                f"{contract.get('read_more_required_before_scoped_no_evidence_answer')}"
            ),
            f"- saved_report: {contract.get('saved_report') or action.get('saved_report')}",
        ]
    )
    if terminal.get("status") == "terminal_answer_and_stop":
        lines.extend(
            [
                "TERMINAL ANSWER-AND-STOP: answer from this searched scope now.",
                "Do not open weak candidates, rerun retrieval, or fall back to manual search.",
            ]
        )
    elif cap_disposition.get("status") == "scoped_fail_closed":
        lines.extend(
            [
                "CAP DISPOSITION: scoped_fail_closed",
                "Partial candidates and snippets are withheld because hidden evidence may remain.",
                "No canonical continuation is available; do not improvise with manual history search.",
            ]
        )


def _append_remembered_user_missing_evidence_contract(
    lines: list[str],
    action: dict,
    *,
    markdown: bool = False,
) -> bool:
    contract = action.get("missing_evidence_stop_contract")
    if not isinstance(contract, dict):
        return False
    checked = (
        contract.get("bounded_evidence_checked")
        if isinstance(contract.get("bounded_evidence_checked"), dict)
        else {}
    )
    checked_kinds = _root_scope_kinds(checked.get("roots"))
    searched_kinds = _root_scope_kinds(checked.get("searched_roots"))
    missing_kinds = _root_scope_kinds(checked.get("missing_roots"))
    searched_files = int(checked.get("searched_files") or 0)
    searched_bytes = int(checked.get("searched_bytes") or 0)
    selected_count = int(checked.get("selected_root_count") or 0)
    searched_count = int(checked.get("searched_root_count") or 0)
    excluded_count = int(checked.get("excluded_root_count") or 0)
    missing_count = int(checked.get("missing_root_count") or 0)
    if markdown:
        lines.extend(
            [
                "",
                "## Remembered-User Missing-Evidence Scope",
                "",
                f"- Searched primary history: files=`{searched_files}`, bytes=`{searched_bytes}`",
                f"- Checked root kinds: `{', '.join(checked_kinds) or 'none'}`",
                f"- Searched root kinds: `{', '.join(searched_kinds) or 'none'}`",
                f"- Missing root kinds: `{', '.join(missing_kinds) or 'none'}`",
                (
                    f"- Root counts: selected=`{selected_count}`, searched=`{searched_count}`, "
                    f"excluded=`{excluded_count}`, missing=`{missing_count}`"
                ),
                f"- Broad repo search allowed: `{str(bool(contract.get('broad_repo_search_allowed'))).lower()}`",
                (
                    "- Secondary substitution allowed: "
                    f"`{str(bool(contract.get('secondary_substitution_allowed'))).lower()}`"
                ),
            ]
        )
        return True
    lines.extend(
        [
            "Remembered-user missing-evidence scope:",
            f"- searched_primary_history: searched_files={searched_files} searched_bytes={searched_bytes}",
            f"- checked_root_kinds: {', '.join(checked_kinds) or 'none'}",
            f"- searched_root_kinds: {', '.join(searched_kinds) or 'none'}",
            f"- missing_root_kinds: {', '.join(missing_kinds) or 'none'}",
            (
                f"- root_counts: selected={selected_count} searched={searched_count} "
                f"excluded={excluded_count} missing={missing_count}"
            ),
            f"- broad_repo_search_allowed: {str(bool(contract.get('broad_repo_search_allowed'))).lower()}",
            (
                "- secondary_substitution_allowed: "
                f"{str(bool(contract.get('secondary_substitution_allowed'))).lower()}"
            ),
        ]
    )
    return True


def _root_scope_kinds(rows: object) -> list[str]:
    kinds: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip()
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def _append_compact_read_contract(
    lines: list[str],
    value: object,
    *,
    markdown: bool = False,
    max_items: int | None = 5,
    cli_prefix: str = "jsonl-history-search",
) -> None:
    bullet = "- " if markdown else "- "
    if not value:
        lines.append(f"{bullet}none")
        return
    if isinstance(value, dict):
        command = value.get("command") or value.get("first_command")
        if isinstance(command, dict):
            command = command.get("command")
        if command:
            lines.append(f"{bullet}{_with_cli_prefix(str(command), cli_prefix)}")
            return
        appended = False
        for key in ("decision", "actionability", "required_before_scoped_no_evidence_answer"):
            if key in value:
                lines.append(f"{bullet}{key}: {value.get(key)}")
                appended = True
        if not appended:
            lines.append(f"{bullet}{value}")
        return
    if isinstance(value, list):
        items = value if max_items is None else value[:max_items]
        for item in items:
            if isinstance(item, dict):
                command = item.get("command")
                target = item.get("target")
                source_target = _source_target(item)
                reason = item.get("reason") or item.get("purpose")
                rendered = _with_cli_prefix(str(command), cli_prefix) if command else target or source_target or reason or str(item)
                lines.append(f"{bullet}{rendered}")
                for source_ref_id in _reviewed_source_ref_ids(item):
                    if markdown:
                        lines.append(f"  - Required final provenance: `{source_ref_id}`")
                    else:
                        lines.append(f"  required final provenance: {source_ref_id}")
                inspection_command = (
                    None
                    if command
                    else _ask_execute_source_inspection_command(item, cli_prefix=cli_prefix)
                )
                if inspection_command:
                    lines.append(f"  read-more: {inspection_command}")
            else:
                lines.append(f"{bullet}{item}")
        return
    lines.append(f"{bullet}{value}")


def _ask_execute_source_inspection_command(
    item: dict,
    *,
    cli_prefix: str = "jsonl-history-search",
) -> str | None:
    if _read_marked_diagnostic_or_nonactionable(item):
        return None
    target = _source_target(item)
    if not _is_jsonl_read_target(target):
        return None
    return (
        f"{cli_prefix} read-more {shlex.quote(target)} "
        '--mode window --before 2 --after 2 --cwd "$PWD" --format text'
    )


def _with_cli_prefix(command: str, cli_prefix: str) -> str:
    if command.startswith("jsonl-history-search "):
        return f"{cli_prefix}{command[len('jsonl-history-search'):]}"
    return command


def render_ask_router_markdown(payload: dict) -> str:
    report = payload["ask_router"]
    resolution = report.get("command_resolution") or {}
    lines = [
        "# JSONL Ask Route",
        "",
        "- Answer status: `not-ready/needs-more-proof`",
        "- Open these source refs first: none yet; run the first command to produce source refs.",
        f"- Run this first: `{report.get('first_command')}`",
        f"- First command: `{report.get('first_command')}`",
        f"- Stop condition: {report.get('stop_guidance')}",
        f"- Only continue if: {report.get('manual_search_guidance')}",
        "",
        f"- Chosen route: `{report.get('chosen_route')}`",
        f"- Audit depth: `{(report.get('audit_depth') or {}).get('label', 'quick')}`",
        f"- Why: {report.get('why_it_applies')}",
        f"- Read: `{report.get('read_specialization')}`",
    ]
    lines.extend(_original_prompt_identity_lines(report.get("original_prompt_identity") or {}, markdown=True))
    if resolution.get("mode") != "not_applicable":
        lines.extend(
            [
                f"- Command resolution: `{resolution.get('mode')}`",
                f"- Source fallback: `{resolution.get('source_fallback_command') or '(not available from this cwd)'}`",
                f"- Command guidance: {resolution.get('guidance')}",
            ]
        )
    alias = report.get("explicit_history_tool_alias") or {}
    if alias:
        lines.extend(
            [
                f"- Explicit history-tool alias: `{alias.get('matched_alias')}`",
                f"- Canonical history command: `{alias.get('canonical_command')}`",
                f"- Alias guidance: {alias.get('guidance')}",
            ]
        )
    handoff = report.get("answer_ready_handoff") or {}
    if handoff:
        lines.extend(
            [
                "",
                "## Answer-Ready Stop",
                "",
                f"- Required evidence: {', '.join(handoff.get('required_evidence', []))}",
                f"- Answer facets: {'; '.join(handoff.get('answer_facets', []))}",
                f"- Stop: {handoff.get('stop')}",
                f"- Answer boundary: {handoff.get('answer_boundary')}",
                f"- Optional deeper reads only if: {handoff.get('only_continue_if')}",
            ]
        )
    packet = report.get("method_selection_packet") or {}
    if packet:
        lines.extend(_render_method_selection_packet_markdown(packet))
    lines.extend(["", "## Next Commands", ""])
    for item in report.get("next_commands", []):
        lines.append(f"- **{item.get('purpose')}**: `{item.get('command')}`")
        lines.append(f"  Inspect: {item.get('inspect_next')}")
    cookbook = report.get("command_cookbook") or []
    if cookbook:
        lines.extend(["", "## Agent Command Cookbook", ""])
        for item in cookbook[:6]:
            lines.append(f"- **{item.get('question_family')}**: `{item.get('first_command')}`")
            lines.append(f"  Inspect: {item.get('inspect_next')}")
    lines.extend(["", "## Inspect After", ""])
    for item in report.get("inspect_after", []):
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
            "## Search Boundary",
            "",
            f"- Stop: {report.get('stop_guidance')}",
            f"- Manual search: {report.get('manual_search_guidance')}",
            f"- Boundary: {report.get('boundary')}",
        ]
    )
    return "\n".join(lines)


def _render_method_selection_packet_markdown(packet: dict) -> list[str]:
    lines = [
        "",
        "## Method Selection Packet",
        "",
        f"- `schema_version`: `{packet.get('schema_version')}`",
        f"- `route_family`: `{packet.get('route_family')}`",
        f"- `method_id`: `{packet.get('method_id')}`",
        f"- `cap_language_required`: `{packet.get('cap_language_required')}`",
        f"- `single_flight_policy`: {packet.get('single_flight_policy')}",
        f"- `secondary_policy`: {packet.get('secondary_policy')}",
        f"- `stop_rule`: {packet.get('stop_rule')}",
        f"- `continue_only_if`: {packet.get('continue_only_if')}",
    ]
    if packet.get("first_command"):
        lines.insert(6, f"- `first_command`: `{packet.get('first_command')}`")
    for field in ("forbidden_first_moves", "required_surfaces", "read_order"):
        values = packet.get(field) or []
        lines.append(f"- `{field}`:")
        for value in values:
            lines.append(f"  - {value}")
    return lines


def render_bulk_line_match_markdown(payload: dict) -> str:
    report = payload["bulk_line_match"]
    scope = report.get("searched_scope") or {}
    lines = [
        "# Bulk Line Match",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Candidate role: `{report.get('candidate_role')}`",
        f"- Validation: {report.get('validation_guidance')}",
        f"- Phrases: `{report.get('phrase_count', 0)}`",
        (
            "- Searched: "
            f"`{scope.get('searched_files', 0)}` files, "
            f"`{scope.get('searched_lines', 0)}` lines, "
            f"`{scope.get('searched_bytes', 0)}` bytes"
        ),
        f"- Caps: `{', '.join(scope.get('cap_labels') or []) or '(none)'}`",
        f"- Candidates: `{report.get('candidate_count', 0)}`",
        "",
        "## Candidates",
        "",
    ]
    for candidate in report.get("candidates", [])[:20]:
        lines.append(
            "- "
            f"`{candidate.get('phrase_id')}` `{candidate.get('match_status')}` `{candidate.get('match_strength')}` "
            f"`{candidate.get('target')}` fields=`{','.join(candidate.get('matched_fields') or [])}`"
        )
        if candidate.get("snippet"):
            lines.append(f"  {candidate.get('snippet')}")
    lines.extend(["", "## Validation Commands", ""])
    for command in report.get("validation_commands", []):
        lines.append(f"- `{command}`")
    return "\n".join(lines)


def _source_target(item: dict) -> str:
    target = item.get("target") or item.get("openable_target")
    if target:
        return str(target)
    source_ref = item.get("source_ref")
    if isinstance(source_ref, dict):
        return source_ref_target(source_ref) or ""
    if source_ref:
        return str(source_ref)
    return ""


def _reviewed_source_ref_ids(item: dict) -> list[str]:
    pointer = (
        item.get("reviewed_source_packet_pointer")
        if isinstance(item.get("reviewed_source_packet_pointer"), dict)
        else {}
    )
    values = list(item.get("required_final_source_ref_ids") or [])
    values.extend([item.get("source_ref_id"), pointer.get("source_ref_id")])
    return list(dict.fromkeys(str(value) for value in values if value))


def _command_from_argv(argv: list | tuple | None) -> str | None:
    if not argv:
        return None
    parts = [str(part) for part in argv]
    if parts[0] == "jsonl-history-search":
        command = parts
    elif parts[0] == "read-more":
        command = ["jsonl-history-search", *parts]
    else:
        command = parts
    return " ".join(shlex.quote(part) for part in command)


def _action_command(action: dict) -> str | None:
    command = str(action.get("command") or "")
    argv = action.get("argv")
    if argv:
        return _command_from_argv(argv)
    if command.startswith("jsonl-history-search "):
        return command
    return None


def _read_marked_diagnostic_or_nonactionable(item: dict) -> bool:
    selection = item.get("historical_provenance_selection") if isinstance(item.get("historical_provenance_selection"), dict) else {}
    return (
        item.get("diagnostic_only") is True
        or item.get("read_more_actionable") is False
        or item.get("followthrough_actionable") is False
        or selection.get("diagnostic_only") is True
        or selection.get("read_more_actionable") is False
        or selection.get("final_support_allowed") is False
    )


def _required_surface_reads(reads: list[dict] | tuple[dict, ...] | None) -> list[dict]:
    return [read for read in list(reads or []) if isinstance(read, dict) and not _read_marked_diagnostic_or_nonactionable(read)]


def _read_more_command(item: dict, command_by_target: dict[str, str] | None = None) -> str | None:
    if _read_marked_diagnostic_or_nonactionable(item):
        return None
    if item.get("read_more_command"):
        return str(item["read_more_command"])
    target = _source_target(item)
    if target and command_by_target and target in command_by_target:
        return command_by_target[target]
    actions = list(item.get("expansion_actions") or [])
    preferred = [action for action in actions if action.get("mode") == "window"] or actions
    for action in preferred:
        command = _action_command(action)
        if command:
            return command
    command = item.get("command")
    if isinstance(command, str) and command.startswith("jsonl-history-search read-more "):
        return command
    if _is_jsonl_read_target(target):
        return _command_from_argv(["jsonl-history-search", "read-more", target, "--mode", "window", "--format", "json"])
    return _repo_artifact_open_command(target)


def _repo_artifact_open_command(target: str | None) -> str | None:
    parsed = _parse_repo_artifact_target(target)
    if not parsed:
        return None
    path, line = parsed
    if line is None:
        start, end = 1, 160
    else:
        start, end = max(1, line - 5), line + 20
    return f"sed -n '{start},{end}p' {shlex.quote(path)}"


def _parse_repo_artifact_target(target: str | None) -> tuple[str, int | None] | None:
    if not target:
        return None
    if "\x00" in target or "\n" in target or "\r" in target:
        return None
    path = target
    line = None
    if ":" in target:
        maybe_path, maybe_line = target.rsplit(":", 1)
        if maybe_line.isdigit():
            path = maybe_path
            line = int(maybe_line)
        else:
            return None
    if _is_jsonl_read_target(path):
        return None
    if path.startswith(("/", "~")) or "://" in path or ":" in path:
        return None
    parts = PurePosixPath(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    if "/" not in path and "." not in PurePosixPath(path).name:
        return None
    return path, line


def _read_more_commands_by_target(reads: list[dict]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for read in reads:
        if _read_marked_diagnostic_or_nonactionable(read):
            continue
        target = _source_target(read)
        command = _read_more_command(read)
        if target and command and target not in commands:
            commands[target] = command
    return commands


def _bounded_text_read_more_commands_by_target(reads: list[dict]) -> dict[str, str]:
    commands: dict[str, str] = {}
    for read in reads:
        if _read_marked_diagnostic_or_nonactionable(read):
            continue
        target = _source_target(read)
        if target and _is_jsonl_read_target(target) and target not in commands:
            commands[target] = (
                f"jsonl-history-search read-more {shlex.quote(target)} "
                '--mode window --before 2 --after 2 --cwd "$PWD" --format text'
            )
    return commands


COMPACT_MULTIFACET_PROVENANCE_FOLLOWTHROUGH_REASON = (
    "reviewed_source_multifacet_provenance_window_covers_task_review_branch_commit_and_notes"
)


def _is_compact_multifacet_provenance_followthrough(followthrough: dict) -> bool:
    if not followthrough.get("applies"):
        return False
    command = followthrough.get("first_command") if isinstance(followthrough.get("first_command"), dict) else {}
    window = command.get("window") if isinstance(command.get("window"), dict) else {}
    return window.get("reason") == COMPACT_MULTIFACET_PROVENANCE_FOLLOWTHROUGH_REASON


def _direct_provenance_evidence_complete(report: dict) -> bool:
    ledger = report.get("provenance_direct_evidence_facet_ledger")
    if not isinstance(ledger, dict):
        ledger = ((report.get("answer_ready_handoff") or {}).get("provenance_direct_evidence_facet_ledger") or {})
    return bool(isinstance(ledger, dict) and ledger.get("applies") and ledger.get("evidence_complete"))


def _bounded_projection_value(value: object, limit: int = 600) -> str:
    text = str(value if value is not None else "")
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 18)]}... [value capped]"


def _cap_utf8_projection(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    suffix = "\n[projection capped at configured UTF-8 byte budget]"
    suffix_bytes = suffix.encode("utf-8")
    prefix = encoded[: max(0, limit - len(suffix_bytes))].decode("utf-8", errors="ignore")
    return prefix + suffix


def _render_provenance_direct_proof_projection(report: dict, *, markdown: bool) -> str | None:
    handoff = report.get("provenance_complete_proof_handoff")
    if not isinstance(handoff, dict) or not handoff.get("applies"):
        return None

    complete = bool(handoff.get("active"))
    title = "Provenance Complete Proof" if complete else "Provenance Direct Proof Incomplete"
    lines = [f"# {title}" if markdown else title]
    if markdown:
        lines.extend(
            [
                "",
                f"- **State:** `{handoff.get('state')}`",
                f"- **Answer status:** `{handoff.get('answer_status')}`",
                f"- **Stop instruction:** {_bounded_projection_value(handoff.get('stop_instruction'))}",
            ]
        )
    else:
        lines.extend(
            [
                f"State: {handoff.get('state')}",
                f"Answer status: {handoff.get('answer_status')}",
                f"STOP: {_bounded_projection_value(handoff.get('stop_instruction'))}",
            ]
        )

    required = ", ".join(handoff.get("required_facets") or []) or "none"
    covered = ", ".join(handoff.get("covered_direct_facets") or []) or "none"
    missing = ", ".join(handoff.get("missing_required_facets") or []) or "none"
    if markdown:
        lines.extend(
            [
                f"- **Required facets:** `{required}`",
                f"- **Covered by direct proof:** `{covered}`",
                f"- **Missing facets:** `{missing}`",
                "",
                "## Selected Direct Reads",
                "",
            ]
        )
    else:
        lines.extend(
            [
                f"Required facets: {required}",
                f"Covered by direct proof: {covered}",
                f"Missing facets: {missing}",
                "Selected direct reads:",
            ]
        )
    selected_reads = list(handoff.get("selected_direct_reads") or [])[:4]
    if not selected_reads:
        lines.append("- none")
    for index, read in enumerate(selected_reads, start=1):
        target = _bounded_projection_value(read.get("target"), 1_200)
        facets = ", ".join(read.get("covered_facets") or []) or "none"
        proof_kind = _bounded_projection_value(read.get("proof_kind") or "unknown")
        source_role = _bounded_projection_value(read.get("source_role") or "unknown")
        command = _bounded_projection_value(read.get("command"), 2_000)
        output_ref = source_ref_target(read.get("output_source_ref") or {})
        if markdown:
            lines.extend(
                [
                    f"{index}. `{target}`",
                    f"   - Facets: `{facets}`",
                    f"   - Proof kind: `{proof_kind}`",
                    f"   - Source role: `{source_role}`",
                    (
                        f"   - Correlated output ref: `{_bounded_projection_value(output_ref, 1_200)}`"
                        if output_ref
                        else "   - Correlated output ref: `not applicable to direct-human proof`"
                    ),
                    f"   - Bounded command: `{command}`",
                ]
            )
        else:
            lines.extend(
                [
                    f"{index}. ref: {target}",
                    f"   facets: {facets}",
                    f"   proof kind: {proof_kind}",
                    f"   source role: {source_role}",
                    (
                        f"   correlated output ref: {_bounded_projection_value(output_ref, 1_200)}"
                        if output_ref
                        else "   correlated output ref: not applicable to direct-human proof"
                    ),
                    f"   bounded command: {command}",
                ]
            )

    continuations = list(handoff.get("continuation_commands") or [])[:1]
    if not complete:
        heading = "## Single Bounded Missing-Facet Continuation" if markdown else "Single bounded missing-facet continuation:"
        lines.extend(["", heading, ""] if markdown else [heading])
        if not continuations:
            lines.append("- unavailable; state the unsupported facets and stop")
        for continuation in continuations:
            facets = ", ".join(continuation.get("missing_facets") or []) or "unspecified"
            command = _bounded_projection_value(continuation.get("command"), 2_000)
            lines.append(f"- Missing facets `{facets}`: `{command}`" if markdown else f"- missing facets {facets}: {command}")

    boundaries = handoff.get("searched_scope_boundaries") or {}
    searched = boundaries.get("searched_scope") or {}
    not_searched = boundaries.get("not_searched_scope") or {}
    excluded = boundaries.get("excluded_scope") or {}
    caps = boundaries.get("caps") or {}
    heading = "## Searched-Scope Boundary" if markdown else "Searched-scope boundary:"
    lines.extend(["", heading, ""] if markdown else [heading])
    boundary_rows = [
        (
            "searched",
            f"roots={searched.get('searched_root_count', 0)}/{searched.get('selected_root_count', 0)} "
            f"files={searched.get('searched_files', 0)} bytes={searched.get('searched_bytes', 0)} "
            f"meaning={_bounded_projection_value(searched.get('result_meaning'))}",
        ),
        (
            "not searched/excluded",
            f"outside_selected_roots={_bounded_projection_value(not_searched.get('outside_selected_roots'))} "
            f"missing_roots={not_searched.get('missing_root_count', 0)} "
            f"deferred_files={not_searched.get('source_plan_deferred_files', 0)} "
            f"pruned_files={not_searched.get('source_plan_pruned_files', 0)} "
            f"rejected_roots={excluded.get('rejected_root_count', 0)}",
        ),
        (
            "caps",
            f"status={caps.get('status')} hit={caps.get('hit', False)} "
            f"caps_hit={','.join(caps.get('caps_hit') or []) or 'none'}",
        ),
        ("claim boundary", _bounded_projection_value(boundaries.get("claim_boundary"), 1_000)),
    ]
    for label, value in boundary_rows:
        lines.append(f"- **{label}:** {value}" if markdown else f"- {label}: {value}")

    suppression = handoff.get("suppression_counts") or {}
    heading = "## Suppressed Continuation Surface" if markdown else "Suppressed continuation surface:"
    lines.extend(["", heading, ""] if markdown else [heading])
    for key in (
        "assistant_recap_targets",
        "report_writing_targets",
        "compacted_summary_targets",
        "specification_context_targets",
        "optional_diagnostics_preserved_non_actionable",
        "secondary_executable_targets_exposed",
        "generic_continuation_commands_exposed",
    ):
        lines.append(f"- `{key}`: `{suppression.get(key, 0)}`" if markdown else f"- {key}: {suppression.get(key, 0)}")
    lines.append(
        "- Diagnostics remain in structured/debug data and are non-actionable."
        if markdown
        else "Diagnostics remain in structured/debug data and are non-actionable."
    )
    if complete:
        lines.append(
            "- **Answer now from the selected direct evidence and stop.**"
            if markdown
            else "ANSWER NOW from the selected direct evidence and stop."
        )
    return _cap_utf8_projection("\n".join(lines), int(handoff.get("output_budget_bytes") or 12_288))


def render_in_depth_audit_followthrough_text(
    plan: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> list[str]:
    if not plan.get("applies"):
        return []
    workflow = plan.get("audit_continuation_workflow") or {}
    coverage_state = plan.get("coverage_state") or {}
    lines = [
        "AUDIT CONTINUATION WORKFLOW:",
        (
            f"- answer_ready={workflow.get('answer_ready', plan.get('answer_ready'))} "
            f"hard_stop={workflow.get('hard_stop', plan.get('hard_stop'))} "
            f"answer_status={workflow.get('answer_status', plan.get('answer_status'))}"
        ),
        f"- hard-stop semantics: {workflow.get('hard_stop_semantics') or 'Open ordered primary refs before answering.'}",
        (
            f"- status: {plan.get('status')} "
            f"ordered_primary_refs={plan.get('ordered_primary_ref_count', 0)} "
            f"continuations={len(plan.get('continuation_commands') or [])}"
        ),
        (
            f"- coverage: status={coverage_state.get('status')} "
            f"promotion_allowed={coverage_state.get('answer_promotion_allowed')} "
            f"slice_status_counts={coverage_state.get('slice_status_counts')}"
        ),
    ]
    caps = coverage_state.get("caps") or {}
    search_cap = coverage_state.get("search_cap_hit") or {}
    lines.append(
        f"- cap/omitted-window state: hit={search_cap.get('hit', caps.get('hit', False))} "
        f"caps_hit={','.join(search_cap.get('caps_hit') or caps.get('caps_hit') or []) or 'none'} "
        f"omitted_or_capped_windows={len(coverage_state.get('omitted_or_capped_windows') or [])}"
    )
    searched = coverage_state.get("searched_scope") or {}
    not_searched = coverage_state.get("not_searched_scope") or {}
    if searched or not_searched:
        lines.append(
            f"- searched scope: files={searched.get('searched_files', 0)} "
            f"bytes={searched.get('searched_bytes', 0)} "
            f"missing_roots={not_searched.get('missing_root_count', 0)} "
            f"deferred_files={not_searched.get('source_plan_deferred_files', 0)}"
        )
    evidence_boundary = plan.get("artifact_and_local_evidence_boundary") or {}
    if evidence_boundary:
        lines.append(
            "- artifact/local evidence boundary: committed/public/export artifacts use refs/commands without raw transcript text; "
            "local runtime opens selected read-more/source windows when transcript evidence is useful"
        )
        lines.append("- metadata-only handoff is an efficiency/public-artifact mode, not the core retrieval principle")
    if workflow.get("do_not_abandon_jsonl_guidance"):
        lines.append(f"- do not abandon JSONL: {workflow.get('do_not_abandon_jsonl_guidance')}")
    lines.append(
        "- continuation protocol: manual_history_fallback_allowed=False; use only emitted "
        "jsonl-history-search read-more, semantic-debug, follow-next-reads, or audit-coverage commands"
    )
    lines.append(
        "- command failure: do not open transcript homes with sed/rg/jq; run an emitted scoped "
        "JSONL continuation, or report the exact blocker and fail closed"
    )
    target_contract = plan.get("target_persistence_contract") or workflow.get("target_persistence_contract") or {}
    target_block = plan.get("target_mismatch_answer_block") or workflow.get("target_mismatch_answer_block") or {}
    if target_contract:
        requested_failure_class = target_contract.get("requested_failure_class") or {}
        time_scope_intent = target_contract.get("time_scope_intent") or {}
        lines.append(
            f"- target persistence: required={target_contract.get('target_persistence_required')} "
            f"ready={target_contract.get('target_persistence_ready')} "
            f"fingerprint={target_contract.get('target_fingerprint_12')} "
            f"target_matched_reads={target_contract.get('target_matched_read_count', 0)} "
            f"drift_risk_reads={target_contract.get('target_drift_risk_count', 0)}"
        )
        lines.append(
            f"- target contract: failure_class={requested_failure_class.get('normalized_label') or 'unspecified'} "
            f"scope={time_scope_intent.get('scope') or 'unspecified'} "
            f"required_evidence={','.join(target_contract.get('required_evidence_classes') or []) or 'none'}"
        )
        lines.append(
            "- forbidden nearby substitutes: "
            + (",".join(target_contract.get("forbidden_nearby_substitutes") or []) or "none")
        )
        missing_evidence = list(target_contract.get("missing_required_evidence_classes") or [])
        if missing_evidence:
            lines.append(f"- missing required evidence: {','.join(missing_evidence)}")
        if target_block.get("blocked"):
            lines.append(
                "- answer blocked: target_mismatch_detected; primary_answer_material_allowed=False; "
                "previous_lane_or_eval_primary_material_allowed=False"
            )
        if target_contract.get("answer_ready_blocked_by_target_drift"):
            lines.append("- target mismatch: target_mismatch_detected; fail closed and rerun the scoped continuation for the original target")
        else:
            lines.append("- target drift policy: previous-lane/eval artifacts remain diagnostic-only unless explicitly requested")
        drift_reads = list(target_contract.get("target_drift_risk_reads") or [])
        if drift_reads:
            lines.append(f"- quarantined drift-risk refs: {len(drift_reads)} diagnostic-only hashes retained in JSON")
    live_tail_caveat = plan.get("live_tail_caveat") or workflow.get("live_tail_caveat") or {}
    if live_tail_caveat.get("applies"):
        lines.append(
            f"- LIVE-TAIL CAVEAT: status={live_tail_caveat.get('status')} "
            f"terminal_state={live_tail_caveat.get('terminal_state_status')} "
            f"live_tail_sources={live_tail_caveat.get('live_tail_source_count', 0)} "
            "answer_ready_for_terminal_state_claims=False"
        )
        lines.append(f"- terminal claim boundary: {live_tail_caveat.get('required_caveat')}")
        lines.append("- positive facts present in the inspected window remain usable with source support")
    source_continuation = plan.get("source_continuation") or workflow.get("source_continuation") or {}
    if source_continuation.get("terminal_claim_requires_tail_followup"):
        lines.append(
            f"- SOURCE CONTINUES: status={source_continuation.get('status')} "
            f"terminal_claim={source_continuation.get('terminal_claim_status')} "
            f"sources={source_continuation.get('source_count', 0)} "
            "answer_ready_for_terminal_state_claims=False"
        )
        lines.append(
            f"- required current-tail read: {source_continuation.get('exact_next_read_more_command')}"
        )
        lines.append("- positive facts present in the inspected window remain usable with source support")
    post_hoc_count = plan.get("post_hoc_diagnostic_source_count", 0)
    if post_hoc_count:
        lines.append(
            f"- post-hoc diagnostic sources: {post_hoc_count}; final_support_allowed=False; "
            "may guide target-primary reads only"
        )
    pending_slices = list(workflow.get("pending_slices") or [])[:8]
    if pending_slices:
        lines.append("- pending coverage slices:")
        for slice_row in pending_slices:
            caps = ",".join(str(item) for item in slice_row.get("cap_labels") or []) or "none"
            lines.append(
                f"  - {slice_row.get('slice')}: coverage_label={slice_row.get('coverage_label')} "
                f"cap_labels={caps} window={slice_row.get('window')}"
            )
    lines.append("- ordered primary refs to open first:")
    reads = list(plan.get("ordered_primary_refs_to_open_first") or [])
    if not reads:
        lines.append("  - none surfaced; state the coverage gap instead of broad searching")
    for read in reads:
        _append_text_read(lines, read, command_by_target=command_by_target)
    lines.append(f"- stop rule: {plan.get('stop_rule')}")
    continuations = list(plan.get("continuation_commands") or [])[:12]
    if continuations:
        lines.append("- scoped continuation commands if selected refs leave coverage incomplete:")
        for continuation in continuations:
            caps = ",".join(str(item) for item in continuation.get("cap_labels") or []) or "none"
            lines.append(
                f"  - [{continuation.get('coverage_label')}; caps={caps}] {continuation.get('command')}"
            )
    else:
        lines.append("- continuation: none emitted; do not invent broad rg/find/ls recovery")
    for item in list(plan.get("do_not_do") or [])[:5]:
        lines.append(f"- do not: {item}")
    return lines


def render_in_depth_audit_followthrough_markdown(
    plan: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> list[str]:
    if not plan.get("applies"):
        return []
    workflow = plan.get("audit_continuation_workflow") or {}
    coverage_state = plan.get("coverage_state") or {}
    lines = [
        "",
        "## Audit Continuation Workflow",
        "",
        f"- `answer_ready`: `{workflow.get('answer_ready', plan.get('answer_ready'))}`",
        f"- `hard_stop`: `{workflow.get('hard_stop', plan.get('hard_stop'))}`",
        f"- `answer_status`: `{workflow.get('answer_status', plan.get('answer_status'))}`",
        f"- `hard_stop_semantics`: {workflow.get('hard_stop_semantics') or 'Open ordered primary refs before answering.'}",
        f"- `status`: `{plan.get('status')}`",
        f"- `ordered_primary_refs`: `{plan.get('ordered_primary_ref_count', 0)}`",
        f"- `continuations`: `{len(plan.get('continuation_commands') or [])}`",
        (
            f"- `coverage`: `status={coverage_state.get('status')}` "
            f"`promotion_allowed={coverage_state.get('answer_promotion_allowed')}` "
            f"`slice_status_counts={coverage_state.get('slice_status_counts')}`"
        ),
    ]
    caps = coverage_state.get("caps") or {}
    search_cap = coverage_state.get("search_cap_hit") or {}
    lines.append(
        f"- `cap/omitted-window state`: `hit={search_cap.get('hit', caps.get('hit', False))}` "
        f"`caps_hit={','.join(search_cap.get('caps_hit') or caps.get('caps_hit') or []) or 'none'}` "
        f"`omitted_or_capped_windows={len(coverage_state.get('omitted_or_capped_windows') or [])}`"
    )
    searched = coverage_state.get("searched_scope") or {}
    not_searched = coverage_state.get("not_searched_scope") or {}
    if searched or not_searched:
        lines.append(
            f"- `searched_scope`: `files={searched.get('searched_files', 0)}` "
            f"`bytes={searched.get('searched_bytes', 0)}` "
            f"`missing_roots={not_searched.get('missing_root_count', 0)}` "
            f"`deferred_files={not_searched.get('source_plan_deferred_files', 0)}`"
        )
    evidence_boundary = plan.get("artifact_and_local_evidence_boundary") or {}
    if evidence_boundary:
        lines.append(
            "- `artifact/local evidence boundary`: committed/public/export artifacts use refs/commands without raw transcript text; "
            "local runtime opens selected `read-more`/source windows when transcript evidence is useful"
        )
        lines.append("- Metadata-only handoff is an efficiency/public-artifact mode, not the core retrieval principle.")
    if workflow.get("do_not_abandon_jsonl_guidance"):
        lines.append(f"- `do_not_abandon_jsonl`: {workflow.get('do_not_abandon_jsonl_guidance')}")
    lines.append(
        "- `continuation_protocol`: `manual_history_fallback_allowed=False`; use only emitted "
        "`jsonl-history-search read-more`, `semantic-debug`, `follow-next-reads`, or `audit-coverage` commands."
    )
    lines.append(
        "- `command_failure`: do not open transcript homes with `sed`/`rg`/`jq`; run an emitted scoped "
        "JSONL continuation, or report the exact blocker and fail closed."
    )
    target_contract = plan.get("target_persistence_contract") or workflow.get("target_persistence_contract") or {}
    target_block = plan.get("target_mismatch_answer_block") or workflow.get("target_mismatch_answer_block") or {}
    if target_contract:
        requested_failure_class = target_contract.get("requested_failure_class") or {}
        time_scope_intent = target_contract.get("time_scope_intent") or {}
        lines.append(
            f"- `target_persistence`: `required={target_contract.get('target_persistence_required')}` "
            f"`ready={target_contract.get('target_persistence_ready')}` "
            f"`fingerprint={target_contract.get('target_fingerprint_12')}` "
            f"`target_matched_reads={target_contract.get('target_matched_read_count', 0)}` "
            f"`drift_risk_reads={target_contract.get('target_drift_risk_count', 0)}`"
        )
        lines.append(
            f"- `target_contract`: `failure_class={requested_failure_class.get('normalized_label') or 'unspecified'}` "
            f"`scope={time_scope_intent.get('scope') or 'unspecified'}` "
            f"`required_evidence={','.join(target_contract.get('required_evidence_classes') or []) or 'none'}`"
        )
        lines.append(
            "- `forbidden_nearby_substitutes`: `"
            + (",".join(target_contract.get("forbidden_nearby_substitutes") or []) or "none")
            + "`"
        )
        missing_evidence = list(target_contract.get("missing_required_evidence_classes") or [])
        if missing_evidence:
            lines.append(f"- `missing_required_evidence`: `{','.join(missing_evidence)}`")
        if target_block.get("blocked"):
            lines.append(
                "- `answer_blocked`: `target_mismatch_detected` `primary_answer_material_allowed=False` "
                "`previous_lane_or_eval_primary_material_allowed=False`"
            )
        if target_contract.get("answer_ready_blocked_by_target_drift"):
            lines.append("- `target_mismatch`: `target_mismatch_detected`; fail closed and rerun the scoped continuation for the original target")
        else:
            lines.append("- `target_drift_policy`: previous-lane/eval artifacts remain diagnostic-only unless explicitly requested")
        drift_reads = list(target_contract.get("target_drift_risk_reads") or [])
        if drift_reads:
            lines.append(f"- `quarantined_drift_risk_refs`: `{len(drift_reads)}` diagnostic-only hashes retained in JSON")
    live_tail_caveat = plan.get("live_tail_caveat") or workflow.get("live_tail_caveat") or {}
    if live_tail_caveat.get("applies"):
        lines.append(
            f"- **LIVE-TAIL CAVEAT:** `status={live_tail_caveat.get('status')}` "
            f"`terminal_state={live_tail_caveat.get('terminal_state_status')}` "
            f"`live_tail_sources={live_tail_caveat.get('live_tail_source_count', 0)}` "
            "`answer_ready_for_terminal_state_claims=False`"
        )
        lines.append(f"- **Terminal claim boundary:** {live_tail_caveat.get('required_caveat')}")
        lines.append("- Positive facts present in the inspected window remain usable with source support.")
    source_continuation = plan.get("source_continuation") or workflow.get("source_continuation") or {}
    if source_continuation.get("terminal_claim_requires_tail_followup"):
        lines.append(
            f"- **SOURCE CONTINUES:** `status={source_continuation.get('status')}` "
            f"`terminal_claim={source_continuation.get('terminal_claim_status')}` "
            f"`sources={source_continuation.get('source_count', 0)}` "
            "`answer_ready_for_terminal_state_claims=False`"
        )
        lines.append(
            f"- **Required current-tail read:** `{source_continuation.get('exact_next_read_more_command')}`"
        )
        lines.append("- Positive facts present in the inspected window remain usable with source support.")
    post_hoc_count = plan.get("post_hoc_diagnostic_source_count", 0)
    if post_hoc_count:
        lines.append(
            f"- `post_hoc_diagnostic_sources`: `{post_hoc_count}`; "
            "`final_support_allowed=False`; may guide target-primary reads only."
        )
    pending_slices = list(workflow.get("pending_slices") or [])[:8]
    if pending_slices:
        lines.extend(["", "### Pending Coverage Slices", ""])
        for slice_row in pending_slices:
            caps = ",".join(str(item) for item in slice_row.get("cap_labels") or []) or "none"
            lines.append(
                f"- `{slice_row.get('slice')}` `coverage_label={slice_row.get('coverage_label')}` "
                f"`cap_labels={caps}` `window={slice_row.get('window')}`"
            )
    lines.extend(["", "### Ordered Primary Refs To Open First", ""])
    reads = list(plan.get("ordered_primary_refs_to_open_first") or [])
    if not reads:
        lines.append("- None surfaced; state the coverage gap instead of broad searching.")
    for read in reads:
        _append_markdown_read(lines, read, command_by_target=command_by_target)
    lines.extend(["", "### Stop / Continue Rule", "", f"- Stop rule: {plan.get('stop_rule')}"])
    continuations = list(plan.get("continuation_commands") or [])[:12]
    if continuations:
        lines.append("- Scoped continuation commands if selected refs leave coverage incomplete:")
        for continuation in continuations:
            caps = ",".join(str(item) for item in continuation.get("cap_labels") or []) or "none"
            lines.append(
                f"  - `[{continuation.get('coverage_label')}; caps={caps}]` `{continuation.get('command')}`"
            )
    else:
        lines.append("- Continuation: none emitted; do not invent broad `rg`/`find`/`ls` recovery.")
    for item in list(plan.get("do_not_do") or [])[:5]:
        lines.append(f"- Do not: {item}")
    return lines


def _source_followthrough_direct_evidence_complete(plan: dict) -> bool:
    if not plan:
        return False
    counts = plan.get("counts") or {}
    return (
        counts.get("required_primary_proof_count", 0) > 0
        and counts.get("conditional_required_count", 0) == 0
        and "selected correlated direct-event reads" in str(plan.get("stop_condition") or "")
    )


def render_source_followthrough_plan_text(plan: dict, *, command_by_target: dict[str, str] | None = None) -> list[str]:
    if not plan:
        return []
    counts = plan.get("counts") or {}
    lines = [
        "First source-followthrough plan:",
        f"- status: {plan.get('status')}",
        (
            "- counts: "
            f"required_primary_proof={counts.get('required_primary_proof_count', 0)} "
            f"conditional_required={counts.get('conditional_required_count', 0)} "
            f"optional_diagnostic={counts.get('optional_diagnostic_count', 0)}"
        ),
        f"- policy: {plan.get('default_read_policy')}",
        "required_primary_proof:",
    ]
    required = plan.get("required_primary_proof") or []
    if required:
        for read in required[:5]:
            prefix = "default" if read.get("default_actionable") else "non-default"
            lines.append(f"- {prefix}: {_source_target(read) or '(no source ref)'}")
            for source_ref_id in _reviewed_source_ref_ids(read):
                lines.append(f"  required final source ref: {source_ref_id}")
            reason = read.get("reason")
            if reason:
                lines.append(f"  reason: {reason}")
            if read.get("default_actionable"):
                command = _read_more_command(read, command_by_target)
                if command:
                    lines.append(f"  command: {command}")
    else:
        lines.append("- none")
    conditional = plan.get("conditional_required") or []
    lines.append("conditional_required:")
    if conditional:
        for read in conditional[:4]:
            lines.append(f"- {_source_target(read) or '(no source ref)'}: {read.get('reason') or 'conditional proof recovery'}")
    else:
        lines.append("- none")
    optional = plan.get("optional_diagnostic") or []
    lines.append("optional_diagnostic:")
    if optional:
        lines.append(f"- {len(optional)} diagnostic refs preserved in JSON/report; not default read-more.")
        lines.append("- diagnostic targets are listed later only under optional diagnostics / next_reads.")
    else:
        lines.append("- none")
    lines.append(f"Stop condition: {plan.get('stop_condition')}")
    for item in list(plan.get("only_continue_if") or [])[:4]:
        lines.append(f"Only continue if: {item}")
    return lines


def render_source_followthrough_plan_markdown(plan: dict, *, command_by_target: dict[str, str] | None = None) -> list[str]:
    if not plan:
        return []
    counts = plan.get("counts") or {}
    lines = [
        "",
        "## First Source-Followthrough Plan",
        "",
        f"- `status`: `{plan.get('status')}`",
        (
            f"- `required_primary_proof`: `{counts.get('required_primary_proof_count', 0)}`; "
            f"`conditional_required`: `{counts.get('conditional_required_count', 0)}`; "
            f"`optional_diagnostic`: `{counts.get('optional_diagnostic_count', 0)}`"
        ),
        f"- Policy: {plan.get('default_read_policy')}",
        "",
        "### required_primary_proof",
    ]
    required = plan.get("required_primary_proof") or []
    if required:
        for read in required[:5]:
            prefix = "default" if read.get("default_actionable") else "non-default"
            lines.append(f"- `{prefix}` `{_source_target(read) or '(no source ref)'}`")
            for source_ref_id in _reviewed_source_ref_ids(read):
                lines.append(f"  - Required final source ref: `{source_ref_id}`")
            if read.get("reason"):
                lines.append(f"  - Reason: {read.get('reason')}")
            if read.get("default_actionable"):
                command = _read_more_command(read, command_by_target)
                if command:
                    lines.append(f"  - Command: `{command}`")
    else:
        lines.append("- None.")
    lines.extend(["", "### conditional_required"])
    conditional = plan.get("conditional_required") or []
    if conditional:
        for read in conditional[:4]:
            lines.append(f"- `{_source_target(read) or '(no source ref)'}`: {read.get('reason') or 'conditional proof recovery'}")
    else:
        lines.append("- None.")
    lines.extend(["", "### optional_diagnostic"])
    optional = plan.get("optional_diagnostic") or []
    if optional:
        lines.append(f"- `{len(optional)}` diagnostic refs preserved in JSON/report; not default read-more.")
        lines.append("- Diagnostic targets are listed later only under optional diagnostics / `next_reads`.")
    else:
        lines.append("- None.")
    lines.extend(["", "### Stop / Continue", f"- Stop condition: {plan.get('stop_condition')}"])
    for item in list(plan.get("only_continue_if") or [])[:4]:
        lines.append(f"- Only continue if: {item}")
    return lines


def _is_jsonl_read_target(target: str | None) -> bool:
    if not target:
        return False
    path = target.split(":", 1)[0]
    return path.endswith(".jsonl") or path.endswith(".json")


def _append_text_read(lines: list[str], item: dict, *, command_by_target: dict[str, str] | None = None) -> None:
    target = _source_target(item) or "(no source ref)"
    reason = item.get("reason") or item.get("instruction") or item.get("proof_role") or item.get("relationship")
    lines.append(f"- {target}{f': {reason}' if reason else ''}")
    for source_ref_id in _reviewed_source_ref_ids(item):
        lines.append(f"  required final source ref: {source_ref_id}")
    if item.get("verification_or_rerun_only"):
        policy = item.get("diagnostic_open_policy") or "verification/rerun material only; not answer support"
        lines.append(f"  verification/rerun-only: {policy}")
    if item.get("read_role") == "conditional_required" or item.get("conditional_required") is True:
        policy = item.get("diagnostic_open_policy") or "open only when a named proof/completeness condition applies"
        lines.append(f"  conditional-required: {policy}")
        return
    if item.get("diagnostic_only") or item.get("read_more_actionable") is False:
        policy = item.get("diagnostic_open_policy") or "diagnostic-only; do not open as answer evidence"
        lines.append(f"  diagnostic-only: {policy}")
        return
    command = _read_more_command(item, command_by_target)
    if command:
        lines.append(f"  command: {command}")


def _append_text_read_summary(lines: list[str], item: dict) -> None:
    target = _source_target(item) or "(no source ref)"
    reason = item.get("reason") or item.get("instruction") or item.get("proof_role") or item.get("relationship")
    lines.append(f"- {target}{f': {reason}' if reason else ''}")


def _append_markdown_read(lines: list[str], item: dict, *, command_by_target: dict[str, str] | None = None) -> None:
    target = _source_target(item) or "(no source ref)"
    reason = item.get("reason") or item.get("instruction") or item.get("proof_role") or item.get("relationship")
    lines.append(f"- `{target}`{f': {reason}' if reason else ''}")
    for source_ref_id in _reviewed_source_ref_ids(item):
        lines.append(f"  - Required final source ref: `{source_ref_id}`")
    if item.get("verification_or_rerun_only"):
        policy = item.get("diagnostic_open_policy") or "verification/rerun material only; not answer support"
        lines.append(f"  - Verification/rerun-only: {policy}")
    if item.get("read_role") == "conditional_required" or item.get("conditional_required") is True:
        policy = item.get("diagnostic_open_policy") or "open only when a named proof/completeness condition applies"
        lines.append(f"  - Conditional-required: {policy}")
        return
    if item.get("diagnostic_only") or item.get("read_more_actionable") is False:
        policy = item.get("diagnostic_open_policy") or "diagnostic-only; do not open as answer evidence"
        lines.append(f"  - Diagnostic-only: {policy}")
        return
    command = _read_more_command(item, command_by_target)
    if command:
        lines.append(f"  - Command: `{command}`")


def _append_markdown_read_summary(lines: list[str], item: dict) -> None:
    target = _source_target(item) or "(no source ref)"
    reason = item.get("reason") or item.get("instruction") or item.get("proof_role") or item.get("relationship")
    lines.append(f"- `{target}`{f': {reason}' if reason else ''}")


def _append_process_actual_use_read_plan_text(
    lines: list[str],
    plan: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> None:
    if not plan:
        return
    guidance = plan.get("guidance") or {}
    lines.append("Process actual-use final-support read plan:")
    lines.append(
        "- guidance: open required primary raw reads first; secondary leads and post-hoc answer-key candidates are not final support"
    )
    lines.append(
        "- excluded candidate policy: post-hoc answer-key candidates are diagnostic-only; open only for contamination audit or explicit prior-answer/report recovery"
    )
    lines.append(
        "- current-run self-echo policy: current prompt/tool-output/same-lane evidence is diagnostic-only for historical actual-use prompts unless the prompt asks about the current lane/tool/run"
    )
    lines.append(
        "- prior final prose support: "
        + (
            "allowed only because the prompt explicitly asks for a prior answer/report"
            if guidance.get("prior_final_prose_counts_as_support")
            else "do not count prior final prose as support"
        )
    )
    required = list(plan.get("required_primary_raw_final_support_reads") or [])
    if required:
        lines.append("- required_primary_raw_final_support_reads:")
        for item in required[:4]:
            _append_text_read(lines, item, command_by_target=command_by_target)
    secondary = list(plan.get("secondary_leads") or [])
    if secondary:
        lines.append("- secondary_leads_not_final_support:")
        for item in secondary[:3]:
            _append_text_read(lines, item, command_by_target=command_by_target)
    answer_key = list(plan.get("post_hoc_answer_key_candidates") or [])
    if answer_key:
        lines.append("- post_hoc_answer_key_candidates_excluded_from_required_reads:")
        lines.append("  diagnostic-only: do not open these as answer evidence or normal follow-through")
        for item in answer_key[:3]:
            _append_text_read(lines, item, command_by_target=command_by_target)
    diagnostic = list(plan.get("diagnostic_only_candidates") or [])
    if diagnostic:
        lines.append("- diagnostic_only_current_run_self_echo_candidates_excluded_from_required_reads:")
        lines.append(
            "  diagnostic-only: current-run prompt echoes/tool output are not historical final support or normal follow-through"
        )
        for item in diagnostic[:3]:
            _append_text_read(lines, item, command_by_target=command_by_target)
    _append_process_actual_use_bucket_bundle_text(
        lines,
        plan.get("process_actual_use_bucket_evidence_bundle") or {},
        command_by_target=command_by_target,
    )


def _append_process_actual_use_read_plan_markdown(
    lines: list[str],
    plan: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> None:
    if not plan:
        return
    guidance = plan.get("guidance") or {}
    lines.extend(
        [
            "",
            "## Process Actual-Use Final-Support Read Plan",
            "",
            "- Open required primary raw reads first; secondary leads and post-hoc answer-key candidates are not final support.",
            "- Excluded candidate policy: post-hoc answer-key candidates are diagnostic-only; open only for contamination audit or explicit prior-answer/report recovery.",
            "- Current-run self-echo policy: current prompt/tool-output/same-lane evidence is diagnostic-only for historical actual-use prompts unless the prompt asks about the current lane/tool/run.",
            (
                "- Prior final prose support: allowed only because the prompt explicitly asks for a prior answer/report."
                if guidance.get("prior_final_prose_counts_as_support")
                else "- Prior final prose support: do not count prior final prose as support."
            ),
        ]
    )
    required = list(plan.get("required_primary_raw_final_support_reads") or [])
    if required:
        lines.extend(["", "### Required Primary Raw Reads", ""])
        for item in required[:4]:
            _append_markdown_read(lines, item, command_by_target=command_by_target)
    secondary = list(plan.get("secondary_leads") or [])
    if secondary:
        lines.extend(["", "### Secondary Leads", ""])
        for item in secondary[:3]:
            _append_markdown_read(lines, item, command_by_target=command_by_target)
    answer_key = list(plan.get("post_hoc_answer_key_candidates") or [])
    if answer_key:
        lines.extend(["", "### Post-Hoc Answer-Key Candidates", ""])
        lines.append(
            "- Excluded from required final-support reads and normal read-more follow-through unless the prompt asks for that prior answer/report."
        )
        lines.append("- Diagnostic-only: open only for contamination audit or explicit prior-answer/report recovery.")
        for item in answer_key[:3]:
            _append_markdown_read(lines, item, command_by_target=command_by_target)
    diagnostic = list(plan.get("diagnostic_only_candidates") or [])
    if diagnostic:
        lines.extend(["", "### Diagnostic-Only Current-Run Self-Echo Candidates", ""])
        lines.append(
            "- Excluded from required final-support reads and normal read-more follow-through for historical actual-use prompts."
        )
        lines.append("- Diagnostic-only: open only for current lane/tool/run behavior audit.")
        for item in diagnostic[:3]:
            _append_markdown_read(lines, item, command_by_target=command_by_target)
    _append_process_actual_use_bucket_bundle_markdown(
        lines,
        plan.get("process_actual_use_bucket_evidence_bundle") or {},
        command_by_target=command_by_target,
    )


def _append_process_actual_use_bucket_bundle_text(
    lines: list[str],
    bundle: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> None:
    if not bundle:
        return
    summary = bundle.get("summary") or {}
    lines.append("Process actual-use bucket evidence bundle:")
    lines.append(
        "- summary: "
        f"primary_supported={summary.get('primary_supported_bucket_count', 0)} "
        f"secondary_only={summary.get('secondary_only_bucket_count', 0)} "
        f"missing_or_excluded={summary.get('missing_or_excluded_bucket_count', 0)}"
    )
    buckets = bundle.get("buckets") or {}
    for bucket_name in bundle.get("bucket_order") or list(buckets):
        bucket = buckets.get(bucket_name) or {}
        if not bucket:
            continue
        lines.append(f"- bucket {bucket_name}: {bucket.get('status')} - {bucket.get('answer_use')}")
        primary = list(bucket.get("primary_raw_evidence") or [])
        secondary = list(bucket.get("secondary_leads") or [])
        excluded = list(bucket.get("excluded_post_hoc_answer_key_candidates") or [])
        if primary:
            _append_text_read(lines, primary[0], command_by_target=command_by_target)
        elif secondary:
            _append_text_read(lines, secondary[0], command_by_target=command_by_target)
        elif excluded:
            _append_text_read(lines, excluded[0], command_by_target=command_by_target)


def _append_process_actual_use_bucket_bundle_markdown(
    lines: list[str],
    bundle: dict,
    *,
    command_by_target: dict[str, str] | None = None,
) -> None:
    if not bundle:
        return
    summary = bundle.get("summary") or {}
    lines.extend(
        [
            "",
            "## Process Actual-Use Bucket Evidence Bundle",
            "",
            (
                f"- Primary-supported buckets: `{summary.get('primary_supported_bucket_count', 0)}`; "
                f"secondary-only buckets: `{summary.get('secondary_only_bucket_count', 0)}`; "
                f"missing/excluded buckets: `{summary.get('missing_or_excluded_bucket_count', 0)}`."
            ),
            "- Primary raw evidence remains preferred for timing and behavior claims; secondary leads do not displace it.",
            "",
            "| Bucket | Status | Answer use | First evidence/lead |",
            "| --- | --- | --- | --- |",
        ]
    )
    buckets = bundle.get("buckets") or {}
    for bucket_name in bundle.get("bucket_order") or list(buckets):
        bucket = buckets.get(bucket_name) or {}
        if not bucket:
            continue
        primary = list(bucket.get("primary_raw_evidence") or [])
        secondary = list(bucket.get("secondary_leads") or [])
        excluded = list(bucket.get("excluded_post_hoc_answer_key_candidates") or [])
        first = (primary or secondary or excluded or [{}])[0]
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(bucket_name))}` | "
            f"`{_escape_markdown_table(str(bucket.get('status') or ''))}` | "
            f"{_escape_markdown_table(str(bucket.get('answer_use') or ''))} | "
            f"`{_escape_markdown_table(str(_source_target(first) or 'gap'))}` |"
        )
        command = _read_more_command(first, command_by_target)
        if command:
            lines.append(f"- `{bucket_name}` command: `{command}`")


def _source_use_answer_status(report_status: str | None, *, has_required_reads: bool, has_refs: bool) -> str:
    if not has_refs:
        return "not-ready/needs-more-proof"
    if has_required_reads or report_status not in {None, "ok", "found", "candidates_found"}:
        return "answer-after-required-reads"
    return "answer-after-required-reads"


def render_artifact_currentness_text(payload: dict) -> str:
    report = payload["artifact_currentness"]
    if _is_worktree_cleanup_causality_report(report):
        return _render_worktree_cleanup_causality_text(report)
    selected = report.get("selected_artifact_or_topic") or {}
    trust = _artifact_trust_label(report.get("currentness_verdict"))
    reads = report.get("next_reads") or []
    answer_status = _source_use_answer_status(
        report.get("status"),
        has_required_reads=bool(reads),
        has_refs=bool(reads or selected.get("source_ref") or (report.get("evidence_chain") or [])),
    )
    lines = [
        f"Should the agent trust this artifact/topic? {trust}",
        f"Answer status: {answer_status}",
        "Open these source refs first:",
    ]
    if reads:
        for read in reads[:5]:
            _append_text_read(lines, read)
    elif selected.get("source_ref"):
        _append_text_read(lines, selected)
    else:
        lines.append("- (no source refs surfaced)")
    lines.extend(
        [
            "Stop condition: stop after the selected artifact/topic and current front-door refs are inspected and the currentness verdict is source-backed.",
            "Only continue if: the listed refs conflict, the front door is missing, or a required currentness source is absent.",
            "Required proof reads:",
        ]
    )
    if reads:
        for read in reads[:8]:
            _append_text_read(lines, read)
    else:
        lines.append("- none listed")
    lines.extend([
        "Optional diagnostics/adjacent evidence: status fields, evidence chain, worktree classifications, blind spots, and warnings below.",
        f"Verdict: {report.get('currentness_verdict')}",
        f"Why: {report.get('why')}",
        f"Query: {report.get('query')}",
        f"Detected intent: {report.get('detected_intent')}",
    ])
    if selected:
        selected_ref = selected.get("source_ref") or selected.get("path") or selected.get("topic")
        lines.append(f"Selected: {selected_ref}")
        if selected.get("kind") or selected.get("artifact_type"):
            lines.append(f"Artifact type: {selected.get('artifact_type') or selected.get('kind')}")
        if selected.get("role_label"):
            lines.append(f"Selected role: {selected.get('role_label')}")
    worktree = report.get("worktree_currentness") or {}
    if worktree:
        cleanup = worktree.get("cleanup_causality") or {}
        if cleanup.get("is_cleanup_causality_query"):
            lines.append("Worktree cleanup causality:")
            lines.append(f"- debt_status: {cleanup.get('debt_status')}")
            if cleanup.get("causal_summary"):
                lines.append(f"- summary: {cleanup.get('causal_summary')}")
            for ref in cleanup.get("operational_records", [])[:6]:
                lines.append(
                    f"- {ref.get('causal_source_role') or ref.get('source_role')}: {ref.get('source_ref')} - "
                    f"{ref.get('relationship')} [{ref.get('source_authority') or 'unclassified'}] - {ref.get('snippet')}"
                )
            if cleanup.get("target_selection_note"):
                lines.append(f"- target selection: {cleanup.get('target_selection_note')}")
        if worktree.get("source_roles_present"):
            lines.append(f"Source roles present: {', '.join(worktree.get('source_roles_present') or [])}")
        lines.append("Worktree currentness:")
        for item in worktree.get("classifications", [])[:12]:
            lines.append(
                f"- {item.get('identifier')}: {item.get('classification')} - {item.get('why')}"
            )
            if item.get("path"):
                lines.append(f"  path: {item.get('path')}")
            if item.get("branch"):
                lines.append(f"  branch: {item.get('branch')}")
            for ref in item.get("source_refs", [])[:3]:
                role = ref.get("source_role") or "operational_record"
                lines.append(f"  evidence: {role} {ref.get('source_ref')} - {ref.get('snippet')}")
            for command in item.get("next_commands", [])[:5]:
                lines.append(f"  command: {command.get('command')}")
            if item.get("preservation_guidance"):
                lines.append(f"  preservation: {item.get('preservation_guidance')}")
        if worktree.get("preservation_policy"):
            lines.append(f"Preservation policy: {worktree.get('preservation_policy')}")
        if worktree.get("boundary"):
            lines.append(f"Worktree boundary: {worktree.get('boundary')}")
    status = report.get("status_field_assessment") or {}
    lines.append(f"Status fields: {status.get('assessment')}")
    if status.get("claim_source"):
        lines.append(f"Claim source: {status.get('claim_source')}")
    for field in status.get("status_fields", [])[:3]:
        lines.append(f"- {field.get('source_ref')}: {field.get('raw')}")
    front = report.get("current_front_door") or {}
    if front.get("source_refs"):
        lines.append("Current front door:")
        for ref in front["source_refs"][:4]:
            role = ref.get("role_label") or ref.get("currentness_role") or "front-door evidence"
            lines.append(f"- {ref.get('source_ref')}: {role} - {ref.get('snippet')}")
    if front.get("active_current") or front.get("supporting_context") or front.get("provenance"):
        lines.append("Plan hierarchy:")
        for label, key in (
            ("active/current", "active_current"),
            ("supporting/context", "supporting_context"),
            ("provenance/historical", "provenance"),
        ):
            items = front.get(key) or []
            if not items:
                continue
            lines.append(f"- {label}:")
            for item in items[:4]:
                lines.append(f"  - {item.get('source_ref')}: {item.get('snippet')}")
    chain = report.get("evidence_chain") or []
    if chain:
        lines.append("Evidence chain:")
        for item in chain[:10]:
            role = f"{item.get('source_role')} " if item.get("source_role") else ""
            lines.append(f"- {role}{item.get('source_ref')}: {item.get('relationship')} - {item.get('snippet')}")
    if reads:
        lines.append("Next reads:")
        for read in reads[:8]:
            _append_text_read(lines, read)
    for blind in report.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_artifact_currentness_markdown(payload: dict) -> str:
    report = payload["artifact_currentness"]
    if _is_worktree_cleanup_causality_report(report):
        return _render_worktree_cleanup_causality_markdown(report)
    selected = report.get("selected_artifact_or_topic") or {}
    trust = _artifact_trust_label(report.get("currentness_verdict"))
    reads = report.get("next_reads") or []
    answer_status = _source_use_answer_status(
        report.get("status"),
        has_required_reads=bool(reads),
        has_refs=bool(reads or selected.get("source_ref") or (report.get("evidence_chain") or [])),
    )
    lines = [
        "# Artifact Currentness",
        "",
        f"**Should the agent trust this artifact/topic?** {trust}",
        "",
        f"**Answer status:** `{answer_status}`",
        "",
        "## Open These Source Refs First",
        "",
    ]
    if reads:
        for read in reads[:5]:
            _append_markdown_read(lines, read)
    elif selected.get("source_ref"):
        _append_markdown_read(lines, selected)
    else:
        lines.append("- No source refs surfaced.")
    lines.extend([
        "",
        "## Stop / Continue",
        "",
        "- Stop condition: stop after the selected artifact/topic and current front-door refs are inspected and the currentness verdict is source-backed.",
        "- Only continue if: the listed refs conflict, the front door is missing, or a required currentness source is absent.",
        "",
        "## Required Proof Reads",
        "",
    ])
    if reads:
        for read in reads[:8]:
            _append_markdown_read(lines, read)
    else:
        lines.append("- None listed.")
    lines.extend([
        "",
        "## Optional Diagnostics / Adjacent Evidence",
        "",
        f"**Verdict:** `{report.get('currentness_verdict')}`",
        f"**Why:** {report.get('why')}",
        f"**Query:** {report.get('query')}",
        f"**Detected intent:** `{report.get('detected_intent')}`",
    ])
    selected_ref = selected.get("source_ref") or selected.get("path") or selected.get("topic")
    if selected_ref:
        lines.append(f"**Selected:** `{selected_ref}`")
    if selected.get("kind") or selected.get("artifact_type"):
        lines.append(f"**Artifact type:** `{selected.get('artifact_type') or selected.get('kind')}`")
    if selected.get("role_label"):
        lines.append(f"**Selected role:** {selected.get('role_label')}")
    worktree = report.get("worktree_currentness") or {}
    if worktree:
        cleanup = worktree.get("cleanup_causality") or {}
        if cleanup.get("is_cleanup_causality_query"):
            lines.extend(["", "## Worktree Cleanup Causality", ""])
            lines.append(f"- Debt status: `{cleanup.get('debt_status')}`")
            if cleanup.get("causal_summary"):
                lines.append(f"- Summary: {cleanup.get('causal_summary')}")
            for ref in cleanup.get("operational_records", [])[:6]:
                lines.append(
                    f"- `{ref.get('source_role')}` `{ref.get('source_ref')}`: `{ref.get('relationship')}` - {ref.get('snippet')}"
                )
            if cleanup.get("target_selection_note"):
                lines.append(f"- Target selection: {cleanup.get('target_selection_note')}")
        if worktree.get("source_roles_present"):
            lines.extend(["", f"**Source roles present:** `{', '.join(worktree.get('source_roles_present') or [])}`"])
        lines.extend(["", "## Worktree Currentness", ""])
        for item in worktree.get("classifications", [])[:12]:
            lines.append(f"- `{item.get('identifier')}`: `{item.get('classification')}` - {item.get('why')}")
            if item.get("path"):
                lines.append(f"  - Path: `{item.get('path')}`")
            if item.get("branch"):
                lines.append(f"  - Branch: `{item.get('branch')}`")
            for ref in item.get("source_refs", [])[:3]:
                role = ref.get("source_role") or "operational_record"
                lines.append(f"  - Evidence `{role}` `{ref.get('source_ref')}`: {ref.get('snippet')}")
            for command in item.get("next_commands", [])[:5]:
                lines.append(f"  - Command: `{command.get('command')}`")
            if item.get("preservation_guidance"):
                lines.append(f"  - Preservation: {item.get('preservation_guidance')}")
        lines.append(f"**Preservation policy:** {worktree.get('preservation_policy')}")
        lines.append(f"**Boundary:** {worktree.get('boundary')}")
    status = report.get("status_field_assessment") or {}
    lines.extend(["", "## Status Field Assessment", "", f"`{status.get('assessment')}`"])
    if status.get("claim_source"):
        lines.append(f"- Claim source: `{status.get('claim_source')}`")
    for field in status.get("status_fields", [])[:3]:
        lines.append(f"- `{field.get('source_ref')}`: {field.get('raw')}")
    front = report.get("current_front_door") or {}
    if front.get("source_refs"):
        lines.extend(["", "## Current Front Door", ""])
        for ref in front["source_refs"][:4]:
            role = ref.get("role_label") or ref.get("currentness_role") or "front-door evidence"
            lines.append(f"- `{ref.get('source_ref')}`: **{role}** - {ref.get('snippet')}")
    if front.get("active_current") or front.get("supporting_context") or front.get("provenance"):
        lines.extend(["", "## Plan Hierarchy", ""])
        for label, key in (
            ("Active/current", "active_current"),
            ("Supporting/context", "supporting_context"),
            ("Provenance/historical", "provenance"),
        ):
            items = front.get(key) or []
            if not items:
                continue
            lines.append(f"### {label}")
            for item in items[:4]:
                lines.append(f"- `{item.get('source_ref')}`: {item.get('snippet')}")
    chain = report.get("evidence_chain") or []
    if chain:
        lines.extend(["", "## Evidence Chain", ""])
        for item in chain[:10]:
            role = f"`{item.get('source_role')}` " if item.get("source_role") else ""
            lines.append(f"- {role}`{item.get('source_ref')}`: `{item.get('relationship')}` - {item.get('snippet')}")
    if reads:
        lines.extend(["", "## Next Reads", ""])
        for read in reads[:8]:
            _append_markdown_read(lines, read)
    if report.get("blind_spots"):
        lines.extend(["", "## Blind Spots", ""])
        for blind in report["blind_spots"]:
            lines.append(f"- {blind}")
    return "\n".join(lines)


def _is_worktree_cleanup_causality_report(report: dict) -> bool:
    cleanup = ((report.get("worktree_currentness") or {}).get("cleanup_causality") or {})
    return bool(cleanup.get("is_cleanup_causality_query"))


def _render_worktree_cleanup_causality_text(report: dict) -> str:
    worktree = report.get("worktree_currentness") or {}
    cleanup = worktree.get("cleanup_causality") or {}
    lines = [
        "Worktree cleanup causality",
        f"Debt status: {cleanup.get('debt_status')}",
        f"Summary: {cleanup.get('causal_summary')}",
        "Operational records:",
    ]
    for ref in cleanup.get("operational_records", [])[:6]:
        lines.append(
            f"- {ref.get('causal_source_role') or ref.get('source_role')} {ref.get('source_ref')}: "
            f"{ref.get('relationship')} [{ref.get('source_authority') or 'unclassified'}] - {ref.get('snippet')}"
        )
    roles = worktree.get("source_roles_present") or []
    if "private_retention_evidence" in roles:
        lines.append(f"Source roles present: {', '.join(roles)}")
    classifications = worktree.get("classifications") or []
    if classifications:
        lines.append("Worktree classifications:")
        for item in classifications[:8]:
            lines.append(f"- {item.get('identifier')}: {item.get('classification')} - {item.get('why')}")
            for command in item.get("next_commands", [])[:3]:
                lines.append(f"  command: {command.get('command')}")
    else:
        lines.append("Worktree classifications: none selected; operational records are the main answer.")
    if cleanup.get("target_selection_note"):
        lines.append(f"Target selection: {cleanup.get('target_selection_note')}")
    if worktree.get("boundary"):
        lines.append("Boundary: no cleanup execution here; no branch deletion/pruning unless explicitly asked.")
    return "\n".join(lines)


def _render_worktree_cleanup_causality_markdown(report: dict) -> str:
    worktree = report.get("worktree_currentness") or {}
    cleanup = worktree.get("cleanup_causality") or {}
    lines = [
        "# Worktree Cleanup Causality",
        "",
        f"- Debt status: `{cleanup.get('debt_status')}`",
        f"- Summary: {cleanup.get('causal_summary')}",
        "",
        "## Operational Records",
        "",
    ]
    for ref in cleanup.get("operational_records", [])[:6]:
        lines.append(
            f"- `{ref.get('causal_source_role') or ref.get('source_role')}` `{ref.get('source_ref')}`: "
            f"`{ref.get('relationship')}` [`{ref.get('source_authority') or 'unclassified'}`] - {ref.get('snippet')}"
        )
    roles = worktree.get("source_roles_present") or []
    if "private_retention_evidence" in roles:
        lines.extend(["", f"**Source roles present:** `{', '.join(roles)}`"])
    classifications = worktree.get("classifications") or []
    lines.extend(["", "## Worktree Currentness", ""])
    if classifications:
        for item in classifications[:8]:
            lines.append(f"- `{item.get('identifier')}`: `{item.get('classification')}` - {item.get('why')}")
            for command in item.get("next_commands", [])[:3]:
                lines.append(f"  - Command: `{command.get('command')}`")
    else:
        lines.append("- No specific physical target selected; operational records are the main answer.")
    if cleanup.get("target_selection_note"):
        lines.extend(["", f"**Target selection:** {cleanup.get('target_selection_note')}"])
    if worktree.get("boundary"):
        lines.extend(["", "**Boundary:** no cleanup execution here; no branch deletion/pruning unless explicitly asked."])
    return "\n".join(lines)


def render_local_evidence_diagnostic_text(payload: dict) -> str:
    report = payload["local_evidence_diagnostic"]
    summary = report.get("summary") or {}
    lines = [
        f"Local evidence diagnostic: {report.get('evidence_mode')}",
        f"Mode: {report.get('mode_label')}",
        f"Rows: {summary.get('row_count', 0)}",
        f"Rows with source refs: {summary.get('rows_with_source_refs', 0)}",
        f"Rows with commands: {summary.get('rows_with_commands', 0)}",
    ]
    contract = report.get("privacy_contract") or {}
    lines.append(
        "Raw detail included: "
        f"paths={contract.get('raw_private_paths_included')} "
        f"lines={contract.get('exact_line_numbers_included')} "
        f"commands={contract.get('raw_command_text_included')} "
        f"answers={contract.get('final_answer_text_included')}"
    )
    for row in report.get("rows", [])[:5]:
        lines.append(f"- {row.get('case_id')}: refs={row.get('diagnosis_inputs', {}).get('source_ref_count', 0)}")
        for ref in row.get("source_refs", [])[:3]:
            lines.append(f"  source: {ref.get('target')}")
        for command in row.get("command_family_trace", [])[:3]:
            lines.append(f"  command family: {command.get('family')}")
    return "\n".join(lines)


def render_local_evidence_diagnostic_markdown(payload: dict) -> str:
    report = payload["local_evidence_diagnostic"]
    summary = report.get("summary") or {}
    lines = [
        "# Local Evidence Diagnostic",
        "",
        f"- Evidence mode: `{report.get('evidence_mode')}`",
        f"- Mode label: {report.get('mode_label')}",
        f"- Rows: {summary.get('row_count', 0)}",
        f"- Rows with source refs: {summary.get('rows_with_source_refs', 0)}",
        f"- Rows with commands: {summary.get('rows_with_commands', 0)}",
        "",
        "## Boundary",
        "",
        f"- Internal mode: {report.get('boundary', {}).get('internal_mode')}",
        f"- Public mode: {report.get('boundary', {}).get('public_mode')}",
        "",
        "## Rows",
        "",
    ]
    for row in report.get("rows", [])[:10]:
        lines.append(f"### {row.get('case_id')}")
        lines.append("")
        for ref in row.get("source_refs", [])[:5]:
            lines.append(f"- Source: `{ref.get('target')}`")
        for command in row.get("command_family_trace", [])[:5]:
            lines.append(f"- Command family: `{command.get('family')}`")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_semantic_wrapup_text(payload: dict) -> str:
    report = payload["semantic_wrapup"]
    summary = report.get("summary") or {}
    reads = report.get("next_reads") or []
    required_reads = _required_surface_reads(reads)
    diagnostic_reads = [read for read in reads if isinstance(read, dict) and read not in required_reads]
    command_by_target = _bounded_text_read_more_commands_by_target(required_reads)
    read_role_summary = report.get("read_role_summary") or {}
    source_followthrough_plan = report.get("source_followthrough_plan") or {}
    answer_status = _source_use_answer_status(
        report.get("status"),
        has_required_reads=bool(required_reads),
        has_refs=bool(reads),
    )
    lines = [
        "Semantic wrap-up",
        f"Status: {report.get('status')}",
        f"Answer status: {answer_status}",
    ]
    lines.extend(render_source_followthrough_plan_text(source_followthrough_plan, command_by_target=command_by_target))
    lines.append("Required primary proof reads:")
    if required_reads:
        for read in required_reads[:8]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- (no required primary proof reads surfaced)")
    contract = report.get("answer_use_contract") or {}
    if contract:
        lines.append(
            "Answer-use contract: "
            f"{contract.get('decision')} "
            f"source_use_required={contract.get('source_use_required')}"
        )
        if contract.get("answer_ready") is True:
            lines.append("Answer-ready: yes after inspecting required_primary_proof refs.")
        if contract.get("stop_broad_search") is True:
            lines.append("Stop broad search: yes.")
        if contract.get("required_next_action"):
            lines.append(f"Required next action: {contract.get('required_next_action')}")
        if contract.get("stop_condition"):
            lines.append(f"Contract stop condition: {contract.get('stop_condition')}")
    answer_ready_contract = report.get("answer_ready_contract") or {}
    cap_gate = answer_ready_contract.get("cap_readiness_gate") or {}
    if cap_gate:
        lines.append(
            "Answer-ready gate: "
            f"answer_ready={answer_ready_contract.get('answer_ready')} "
            f"cap_changed_conclusion={cap_gate.get('cap_changed_conclusion')} "
            f"reason={cap_gate.get('reason')}"
        )
        if cap_gate.get("bounded_source_plan"):
            lines.append(f"Bounded source plan count: {len(cap_gate.get('bounded_source_plan') or [])}")
    lines.extend([
        "Stop condition: stop once failure modes are supported by inspected required_primary_proof refs; do not auto-apply fixes.",
        "Only continue if: a failure mode lacks source proof, caps/omitted windows block completeness, the behavior-system destination is ambiguous, the user asks for exhaustive diagnostics, or the user selects a fix to implement.",
        (
            "Read-role summary: "
            f"required_primary_proof={read_role_summary.get('required_primary_proof_count', len(required_reads))}, "
            f"optional_diagnostic={read_role_summary.get('optional_diagnostic_count', 0)}, "
            f"conditional_required={read_role_summary.get('conditional_required_count', 0)}, "
            f"default_actionable={read_role_summary.get('default_actionable_read_count', len(required_reads))}"
        ),
        "Optional/conditional diagnostics:",
    ])
    if diagnostic_reads:
        for read in diagnostic_reads[:8]:
            _append_text_read(lines, read)
    else:
        lines.append("- none listed")
    lines.extend([
        "Additional adjacent evidence: behavior-system destinations and fix suggestions below.",
        (
            "Summary: "
            f"{summary.get('session_message_count', 0)} session messages, "
            f"{summary.get('user_message_count', 0)} user messages, "
            f"{summary.get('complaint_signal_count', 0)} complaint signals, "
            f"{summary.get('substantive_signal_count', 0)} substantive, "
            f"{summary.get('setup_echo_signal_count', 0)} setup/prompt echo, "
            f"{summary.get('failure_mode_returned_count', summary.get('failure_mode_count', 0))}/"
            f"{summary.get('failure_mode_total_count', summary.get('failure_mode_count', 0))} failure modes returned, "
            f"{summary.get('fix_suggestion_returned_count', summary.get('fix_suggestion_count', 0))}/"
            f"{summary.get('fix_suggestion_total_count', summary.get('fix_suggestion_count', 0))} fix suggestions returned"
        ),
        "Selection required: yes; no fixes were auto-applied.",
    ])
    if summary.get("failure_mode_truncated") or summary.get("fix_suggestion_truncated"):
        lines.append(f"Truncation: inline arrays are limited to {summary.get('inline_limit')} items; totals are shown above.")
    coverage = report.get("rolling_window_coverage") or {}
    window_accounting = coverage.get("window_accounting") or {}
    cap_accounting = coverage.get("cap_accounting") or {}
    if coverage:
        lines.append(
            "Whole-session coverage: "
            f"windows_inspected={window_accounting.get('windows_inspected', 0)}, "
            f"windows_omitted={window_accounting.get('windows_omitted', 0)}, "
            f"assistant_tool_runtime_errors={coverage.get('assistant_tool_runtime_error_count', 0)}, "
            f"cap_changed_conclusion={cap_accounting.get('cap_changed_conclusion')}"
        )
        if coverage.get("omitted_or_uncertain_next_reads"):
            lines.append("Omitted/uncertain rolling-window reads are listed above as conditional_required diagnostics.")
    scope_resolution = report.get("scope_resolution") or {}
    if scope_resolution.get("status"):
        lines.append(f"Scope: {scope_resolution.get('status')} ({scope_resolution.get('strategy')})")
    if scope_resolution.get("first_action"):
        lines.append(f"First action: {scope_resolution.get('first_action')}")
    root_handoff = report.get("root_handoff_contract") or {}
    if root_handoff:
        lines.append(
            "Root handoff: "
            f"{root_handoff.get('status')} "
            f"candidate_count={root_handoff.get('candidate_count')}"
        )
        if root_handoff.get("recommended_rerun_command"):
            lines.append(f"Root handoff rerun: {root_handoff.get('recommended_rerun_command')}")
        elif root_handoff.get("missing_root_guidance"):
            lines.append(f"Root handoff guidance: {root_handoff.get('missing_root_guidance')}")
    currentness = report.get("currentness_boundary") or {}
    if currentness:
        lines.append(f"Currentness boundary: {currentness.get('status')}: {currentness.get('boundary')}")
        if currentness.get("first_action_for_current_session"):
            lines.append(f"Current-session anchoring action: {currentness.get('first_action_for_current_session')}")
    skill_check = report.get("skill_check_recommendation") or {}
    if skill_check:
        lines.extend([
            "Skill-check recommendation:",
            f"- Route: {skill_check.get('route')}",
            f"- Skill: {skill_check.get('skill')}",
            f"- First command: {skill_check.get('first_command')}",
            f"- Boundary: {skill_check.get('semantic_wrapup_boundary')}",
        ])
    signal_classification = report.get("signal_classification") or {}
    if signal_classification:
        lines.append(
            "Signal classification: "
            f"substantive={signal_classification.get('substantive_signal_count', 0)}, "
            f"setup_echo={signal_classification.get('setup_echo_signal_count', 0)}, "
            f"failure_modes_exclude_setup_echoes={signal_classification.get('failure_modes_exclude_setup_echoes')}"
        )
    user_first = report.get("user_message_first_evidence") or {}
    if user_first:
        counts = user_first.get("classification_counts") or {}
        lines.append(
            "User-message-first evidence: "
            f"user_messages={user_first.get('total_user_messages', 0)}, "
            f"candidates={user_first.get('candidate_count', 0)}, "
            f"clusters={user_first.get('cluster_count', 0)}, "
            f"clustered_before_fix_suggestions={user_first.get('clustered_before_fix_suggestions')}"
        )
        lines.append(
            "User-message classifications: "
            f"frustration/correction={counts.get('frustration/correction', 0)}, "
            f"directive={counts.get('directive', 0)}, "
            f"factual_clarification={counts.get('factual_clarification', 0)}, "
            f"launch_criterion={counts.get('launch_criterion', 0)}, "
            f"non_complaint_context={counts.get('non_complaint_context', 0)}"
        )
        for cluster in user_first.get("clusters", [])[:6]:
            lines.append(
                f"- cluster {cluster.get('label')} candidates={cluster.get('candidate_count')} "
                f"context_windows={cluster.get('nearby_context_window_count')}"
            )
    late_signals = report.get("late_signal_handling") or {}
    if late_signals.get("items"):
        lines.append(
            "Late signal handling: "
            f"{late_signals.get('returned_count', 0)} returned; "
            "promoted signals are failure proof, conditional_non_promoted signals are visible diagnostics."
        )
        for item in late_signals.get("items", [])[:6]:
            ref = item.get("source_ref") or {}
            lines.append(
                f"- {ref.get('path')}:{ref.get('line')} role={item.get('source_role')} "
                f"status={item.get('status')} kind={item.get('scope_signal_kind')} reason={item.get('reason')}"
            )
            related = item.get("related_mirror_source_refs") or []
            if related:
                refs = ", ".join(f"{mirror.get('path')}:{mirror.get('line')}" for mirror in related[:3])
                lines.append(f"  related collapsed mirrors: {refs}")
    factual_context = report.get("factual_context") or {}
    if factual_context.get("entries"):
        lines.append(f"Factual context: {factual_context.get('count', 0)} preserved separately from complaint evidence")
        for entry in factual_context.get("entries", [])[:4]:
            ref = entry.get("source_ref") or {}
            lines.append(f"- {ref.get('path')}:{ref.get('line')} {entry.get('safe_summary')}")
    current_sources = report.get("current_session_source_selection") or {}
    if current_sources.get("selected_sources"):
        lines.append(
            "Current-session source selection: "
            f"{current_sources.get('returned_count', 0)}/{current_sources.get('total_count', 0)} returned"
        )
        for source in current_sources.get("selected_sources", [])[:5]:
            ref = source.get("source_ref") or {}
            facets = ", ".join(source.get("facets") or [])
            lines.append(
                f"- {ref.get('path')}:{ref.get('line')} "
                f"[{source.get('source_kind')}; facets={facets}]: {source.get('snippet')}"
            )
    orchestration = report.get("orchestration_evidence") or {}
    if orchestration.get("items"):
        lines.append(
            "Orchestration evidence: "
            f"{orchestration.get('returned_count', 0)}/{orchestration.get('total_count', 0)} returned; "
            f"substantive={orchestration.get('substantive_count', 0)}, "
            f"setup_echo={orchestration.get('setup_echo_count', 0)}"
        )
        for item in orchestration.get("items", [])[:8]:
            ref = item.get("source_ref") or {}
            categories = ", ".join(item.get("categories") or [])
            lines.append(
                f"- {item.get('actor_label')} {ref.get('path')}:{ref.get('line')} "
                f"[{categories}] setup_echo={item.get('setup_echo')}: {item.get('snippet')}"
            )
    if report.get("failure_modes"):
        lines.append("Failure modes:")
        for mode in report["failure_modes"][:8]:
            lines.append(f"- {mode.get('failure_mode')}: {mode.get('title')} evidence={mode.get('evidence_count')}")
            if mode.get("issue_axis"):
                lines.append(f"  axis: {mode.get('issue_axis')}")
            if mode.get("why_it_matters"):
                lines.append(f"  why it matters: {mode.get('why_it_matters')}")
            for example in (mode.get("examples") or [])[:2]:
                ref = example.get("source_ref") or {}
                lines.append(f"  example: {ref.get('path')}:{ref.get('line')} {example.get('snippet')}")
            for ref in mode.get("source_refs", [])[:3]:
                lines.append(f"  ref: {ref.get('path')}:{ref.get('line')}")
            cause_chain = mode.get("cause_chain") or {}
            inferred_steps = cause_chain.get("inferred_steps") or []
            if inferred_steps:
                lines.append(f"  inferred: {inferred_steps[0]}")
    if report.get("fix_suggestions"):
        lines.append("Fix suggestions:")
        for suggestion in report["fix_suggestions"][:8]:
            dest = suggestion.get("proposed_destination") or {}
            lines.append(
                f"- {suggestion.get('suggestion_id')} {suggestion.get('fix_type')} -> "
                f"{dest.get('path')}: {suggestion.get('proposed_action')}"
            )
            if suggestion.get("issue_axis"):
                lines.append(f"  axis: {suggestion.get('issue_axis')}")
            if suggestion.get("why_it_matters"):
                lines.append(f"  why it matters: {suggestion.get('why_it_matters')}")
            if suggestion.get("doable_suggested_fix"):
                lines.append(f"  doable fix: {suggestion.get('doable_suggested_fix')}")
            lines.append(f"  destination rationale: {suggestion.get('destination_rationale')}")
            criteria = suggestion.get("acceptance_criteria") or []
            if criteria:
                lines.append(f"  acceptance criteria: {'; '.join(criteria[:3])}")
            for example in (suggestion.get("examples") or [])[:2]:
                ref = example.get("source_ref") or {}
                lines.append(f"  example: {ref.get('path')}:{ref.get('line')} {example.get('snippet')}")
            decisions = [
                option.get("decision")
                for option in (suggestion.get("selection_shape") or {}).get("options", [])
                if option.get("decision")
            ] or list(suggestion.get("selection_options") or [])
            if decisions:
                lines.append(f"  options: {', '.join(decisions)}")
            if suggestion.get("suggestion_only_contract"):
                lines.append(f"  contract: {suggestion.get('suggestion_only_contract')}")
    if reads:
        lines.append("Next reads:")
        for read in reads[:8]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    return "\n".join(lines)


def render_semantic_wrapup_markdown(payload: dict) -> str:
    report = payload["semantic_wrapup"]
    summary = report.get("summary") or {}
    reads = report.get("next_reads") or []
    required_reads = _required_surface_reads(reads)
    diagnostic_reads = [read for read in reads if isinstance(read, dict) and read not in required_reads]
    command_by_target = _read_more_commands_by_target(required_reads)
    read_role_summary = report.get("read_role_summary") or {}
    source_followthrough_plan = report.get("source_followthrough_plan") or {}
    answer_status = _source_use_answer_status(
        report.get("status"),
        has_required_reads=bool(required_reads),
        has_refs=bool(reads),
    )
    lines = [
        "# Semantic Wrap-Up",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Answer status:** `{answer_status}`",
    ]
    lines.extend(render_source_followthrough_plan_markdown(source_followthrough_plan, command_by_target=command_by_target))
    lines.extend([
        "",
        "## Required Primary Proof Reads",
        "",
    ])
    if required_reads:
        for read in required_reads[:8]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- No required primary proof reads surfaced.")
    contract = report.get("answer_use_contract") or {}
    if contract:
        lines.extend([
            "",
            "## Answer-Use Contract",
            "",
            f"- Decision: `{contract.get('decision')}`",
            f"- Source use required: `{contract.get('source_use_required')}`",
        ])
        if contract.get("required_next_action"):
            lines.append(f"- Required next action: {contract.get('required_next_action')}")
        if contract.get("answer_ready") is True:
            lines.append("- Answer-ready: `true` after inspecting `required_primary_proof` refs")
        if contract.get("stop_broad_search") is True:
            lines.append("- Stop broad search: `true`")
        if contract.get("stop_condition"):
            lines.append(f"- Stop condition: {contract.get('stop_condition')}")
        for item in contract.get("continue_only_if") or []:
            lines.append(f"- Continue only if: {item}")
        if contract.get("setup_echo_boundary"):
            lines.append(f"- Setup-echo boundary: {contract.get('setup_echo_boundary')}")
    answer_ready_contract = report.get("answer_ready_contract") or {}
    cap_gate = answer_ready_contract.get("cap_readiness_gate") or {}
    if cap_gate:
        lines.extend([
            "",
            "## Answer-Ready Gate",
            "",
            f"- Answer ready: `{answer_ready_contract.get('answer_ready')}`",
            f"- Cap changed conclusion: `{cap_gate.get('cap_changed_conclusion')}`",
            f"- Reason: {cap_gate.get('reason')}",
            f"- Bounded source plan count: `{len(cap_gate.get('bounded_source_plan') or [])}`",
        ])
    skill_check = report.get("skill_check_recommendation") or {}
    if skill_check:
        lines.extend([
            "",
            "## Skill-Check Recommendation",
            "",
            f"- Route: `{skill_check.get('route')}`",
            f"- Skill: `{skill_check.get('skill')}`",
            f"- First command: `{skill_check.get('first_command')}`",
            f"- Boundary: {skill_check.get('semantic_wrapup_boundary')}",
        ])
    lines.extend([
        "",
        "## Stop / Continue",
        "",
        "- Stop condition: stop once failure modes are supported by inspected `required_primary_proof` refs; do not auto-apply fixes.",
        "- Only continue if: a failure mode lacks source proof, caps/omitted windows block completeness, the behavior-system destination is ambiguous, the user asks for exhaustive diagnostics, or the user selects a fix to implement.",
        "",
        "## Read Roles",
        "",
        f"- `required_primary_proof`: `{read_role_summary.get('required_primary_proof_count', len(required_reads))}`",
        f"- `optional_diagnostic`: `{read_role_summary.get('optional_diagnostic_count', 0)}`",
        f"- `conditional_required`: `{read_role_summary.get('conditional_required_count', 0)}`",
        f"- `default_actionable`: `{read_role_summary.get('default_actionable_read_count', len(required_reads))}`",
        "",
        "## Optional / Conditional Diagnostics",
        "",
    ])
    if diagnostic_reads:
        for read in diagnostic_reads[:8]:
            _append_markdown_read(lines, read)
    else:
        lines.append("- None listed.")
    lines.extend([
        "",
        "## Optional Diagnostics / Adjacent Evidence",
        "",
        (
            f"**Summary:** {summary.get('session_message_count', 0)} session messages, "
            f"{summary.get('user_message_count', 0)} user messages, "
            f"{summary.get('complaint_signal_count', 0)} complaint signals, "
            f"{summary.get('substantive_signal_count', 0)} substantive, "
            f"{summary.get('setup_echo_signal_count', 0)} setup/prompt echo, "
            f"{summary.get('failure_mode_returned_count', summary.get('failure_mode_count', 0))}/"
            f"{summary.get('failure_mode_total_count', summary.get('failure_mode_count', 0))} failure modes returned, "
            f"{summary.get('fix_suggestion_returned_count', summary.get('fix_suggestion_count', 0))}/"
            f"{summary.get('fix_suggestion_total_count', summary.get('fix_suggestion_count', 0))} fix suggestions returned"
        ),
        "",
        "> Selection required: no fixes were auto-applied.",
        "",
    ])
    if summary.get("failure_mode_truncated") or summary.get("fix_suggestion_truncated"):
        lines.extend([
            f"**Truncation:** inline arrays are limited to `{summary.get('inline_limit')}` items; totals are shown above.",
            "",
        ])
    coverage = report.get("rolling_window_coverage") or {}
    if coverage:
        session_accounting = coverage.get("session_accounting") or {}
        window_accounting = coverage.get("window_accounting") or {}
        cap_accounting = coverage.get("cap_accounting") or {}
        lines.extend([
            "## Whole-Session Rolling Coverage",
            "",
            f"- Session messages: `{session_accounting.get('session_message_count', 0)}`",
            f"- User messages: `{session_accounting.get('user_message_count', 0)}`",
            f"- User complaint/correction candidates: `{session_accounting.get('user_complaint_candidate_count', 0)}`",
            f"- Windows inspected: `{window_accounting.get('windows_inspected', 0)}`",
            f"- Windows omitted from inline output: `{window_accounting.get('windows_omitted', 0)}`",
            f"- Assistant/tool/runtime errors found around windows: `{coverage.get('assistant_tool_runtime_error_count', 0)}`",
            f"- Cap changed conclusion: `{cap_accounting.get('cap_changed_conclusion')}`",
            f"- Boundary: {cap_accounting.get('cap_boundary')}",
            "",
        ])
        if coverage.get("omitted_or_uncertain_next_reads"):
            lines.extend([
                "Omitted or uncertain rolling-window reads are listed above as `conditional_required` diagnostics.",
                "",
            ])
        if coverage.get("assistant_tool_runtime_errors"):
            lines.extend(["### Assistant / Tool / Runtime Errors", ""])
            for item in coverage.get("assistant_tool_runtime_errors", [])[:10]:
                ref = item.get("source_ref") or {}
                labels = ", ".join(item.get("labels") or [])
                lines.append(f"- `{ref.get('path')}:{ref.get('line')}` `{labels}`: {item.get('snippet')}")
            lines.append("")
    scope_resolution = report.get("scope_resolution") or {}
    if scope_resolution.get("status"):
        lines.extend([
            f"**Scope:** `{scope_resolution.get('status')}` via `{scope_resolution.get('strategy')}`",
            "",
        ])
    if scope_resolution.get("first_action"):
        lines.extend([
            f"**First action:** `{scope_resolution.get('first_action')}`",
            "",
        ])
    root_handoff = report.get("root_handoff_contract") or {}
    if root_handoff:
        lines.extend([
            "## Root Handoff",
            "",
            f"- Status: `{root_handoff.get('status')}`",
            f"- Candidate roots: `{root_handoff.get('candidate_count')}`",
        ])
        if root_handoff.get("recommended_rerun_command"):
            lines.append(f"- Rerun: `{root_handoff.get('recommended_rerun_command')}`")
        elif root_handoff.get("missing_root_guidance"):
            lines.append(f"- Guidance: `{root_handoff.get('missing_root_guidance')}`")
        lines.append("")
    currentness = report.get("currentness_boundary") or {}
    if currentness:
        lines.extend([
            "## Currentness Boundary",
            "",
            f"- Status: `{currentness.get('status')}`",
            f"- Boundary: {currentness.get('boundary')}",
        ])
        if currentness.get("first_action_for_current_session"):
            lines.append(f"- Current-session anchoring action: `{currentness.get('first_action_for_current_session')}`")
        lines.append("")
    user_first = report.get("user_message_first_evidence") or {}
    if user_first:
        counts = user_first.get("classification_counts") or {}
        lines.extend([
            "## User-Message-First Evidence",
            "",
            f"- User messages read: `{user_first.get('total_user_messages', 0)}`",
            f"- Frustration/correction candidates: `{user_first.get('candidate_count', 0)}`",
            f"- Clusters: `{user_first.get('cluster_count', 0)}`",
            f"- Clustered before fix suggestions: `{user_first.get('clustered_before_fix_suggestions')}`",
            f"- Frustration/correction: `{counts.get('frustration/correction', 0)}`",
            f"- Directive: `{counts.get('directive', 0)}`",
            f"- Factual clarification: `{counts.get('factual_clarification', 0)}`",
            f"- Launch criterion: `{counts.get('launch_criterion', 0)}`",
            f"- Non-complaint context: `{counts.get('non_complaint_context', 0)}`",
            "",
        ])
        for cluster in user_first.get("clusters", [])[:6]:
            lines.append(
                f"- `{cluster.get('label')}` candidates=`{cluster.get('candidate_count')}` "
                f"context_windows=`{cluster.get('nearby_context_window_count')}`"
            )
        lines.append("")
    late_signals = report.get("late_signal_handling") or {}
    if late_signals.get("items"):
        lines.extend([
            "## Late Signal Handling",
            "",
            f"- Returned: `{late_signals.get('returned_count', 0)}`",
            "- Boundary: promoted signals are failure proof; `conditional_non_promoted` signals are visible diagnostics with reasons.",
            "",
        ])
        for item in late_signals.get("items", [])[:8]:
            ref = item.get("source_ref") or {}
            lines.append(
                f"- `{ref.get('path')}:{ref.get('line')}` role=`{item.get('source_role')}` "
                f"status=`{item.get('status')}` kind=`{item.get('scope_signal_kind')}`"
            )
            lines.append(f"  - Reason: {item.get('reason')}")
            related = item.get("related_mirror_source_refs") or []
            if related:
                refs = ", ".join(f"`{mirror.get('path')}:{mirror.get('line')}`" for mirror in related[:3])
                lines.append(f"  - Related collapsed mirrors: {refs}")
        lines.append("")
    factual_context = report.get("factual_context") or {}
    if factual_context.get("entries"):
        lines.extend([
            "## Factual Context",
            "",
            f"- Preserved separately from complaint evidence: `{factual_context.get('count', 0)}`",
        ])
        for entry in factual_context.get("entries", [])[:4]:
            ref = entry.get("source_ref") or {}
            lines.append(f"- `{ref.get('path')}:{ref.get('line')}` {entry.get('safe_summary')}")
        lines.append("")
    current_sources = report.get("current_session_source_selection") or {}
    if current_sources.get("selected_sources"):
        lines.extend([
            "## Current-Session Source Selection",
            "",
            f"- Returned: `{current_sources.get('returned_count', 0)}/{current_sources.get('total_count', 0)}`",
            f"- Policy: {current_sources.get('selection_policy')}",
            "",
        ])
        for source in current_sources.get("selected_sources", [])[:5]:
            ref = source.get("source_ref") or {}
            facets = ", ".join(source.get("facets") or [])
            lines.append(f"- `{ref.get('path')}:{ref.get('line')}` `{source.get('source_kind')}` facets=`{facets}`")
            if source.get("snippet"):
                lines.append(f"  - {source.get('snippet')}")
        lines.append("")
    signal_classification = report.get("signal_classification") or {}
    if signal_classification:
        lines.extend([
            "## Signal Classification",
            "",
            f"- Substantive signals: `{signal_classification.get('substantive_signal_count', 0)}`",
            f"- Setup/prompt echo signals: `{signal_classification.get('setup_echo_signal_count', 0)}`",
            f"- Failure modes exclude setup echoes: `{signal_classification.get('failure_modes_exclude_setup_echoes')}`",
            "",
        ])
    orchestration = report.get("orchestration_evidence") or {}
    if orchestration.get("items"):
        lines.extend([
            "## Orchestration Evidence",
            "",
            f"- Returned: `{orchestration.get('returned_count', 0)}/{orchestration.get('total_count', 0)}`",
            f"- Substantive: `{orchestration.get('substantive_count', 0)}`",
            f"- Setup/prompt echo: `{orchestration.get('setup_echo_count', 0)}`",
            f"- Claim boundary: {orchestration.get('claim_boundary')}",
            "",
        ])
        for item in orchestration.get("items", [])[:10]:
            ref = item.get("source_ref") or {}
            categories = ", ".join(item.get("categories") or [])
            lines.append(f"- `{item.get('actor_label')}` `{ref.get('path')}:{ref.get('line')}` `{categories}` setup_echo=`{item.get('setup_echo')}`")
            if item.get("snippet"):
                lines.append(f"  - {item.get('snippet')}")
        lines.append("")
    if report.get("failure_modes"):
        lines.extend(["## Failure Modes", ""])
        for mode in report["failure_modes"][:10]:
            lines.append(f"### {mode.get('title')}")
            lines.append("")
            lines.append(f"- Label: `{mode.get('failure_mode')}`")
            if mode.get("issue_axis"):
                lines.append(f"- Issue axis: `{mode.get('issue_axis')}`")
            if mode.get("why_it_matters"):
                lines.append(f"- Why it matters: {mode.get('why_it_matters')}")
            lines.append(f"- Evidence count: `{mode.get('evidence_count')}`")
            lines.append(f"- Confidence: `{mode.get('confidence')}`")
            if mode.get("examples"):
                lines.append("- Examples:")
                for example in mode.get("examples", [])[:3]:
                    ref = example.get("source_ref") or {}
                    lines.append(f"  - `{ref.get('path')}:{ref.get('line')}` {example.get('snippet')}")
            lines.append("- Source refs:")
            for ref in mode.get("source_refs", [])[:5]:
                lines.append(f"  - `{ref.get('path')}:{ref.get('line')}`")
            cause_chain = mode.get("cause_chain") or {}
            lines.append("- Cause-chain evidence refs:")
            for ref in cause_chain.get("evidence_refs", [])[:5]:
                lines.append(f"  - `{ref.get('path')}:{ref.get('line')}`")
            lines.append("- Inferred steps:")
            for step in cause_chain.get("inferred_steps", []):
                lines.append(f"  - {step}")
            if cause_chain.get("claim_boundary"):
                lines.append(f"- Claim boundary: {cause_chain.get('claim_boundary')}")
            lines.append("")
    if report.get("fix_suggestions"):
        lines.extend(["## User-Selectable Fix Suggestions", ""])
        for suggestion in report["fix_suggestions"][:10]:
            dest = suggestion.get("proposed_destination") or {}
            lines.append(f"- `{suggestion.get('suggestion_id')}` `{suggestion.get('fix_type')}` -> `{dest.get('path')}`")
            lines.append(f"  - Issue family: `{suggestion.get('issue_family') or suggestion.get('failure_mode')}`")
            if suggestion.get("issue_axis"):
                lines.append(f"  - Issue axis: `{suggestion.get('issue_axis')}`")
            if suggestion.get("why_it_matters"):
                lines.append(f"  - Why it matters: {suggestion.get('why_it_matters')}")
            lines.append(f"  - {suggestion.get('proposed_action')}")
            if suggestion.get("doable_suggested_fix"):
                lines.append(f"  - Doable suggested fix: {suggestion.get('doable_suggested_fix')}")
            lines.append(f"  - Destination rationale: {suggestion.get('destination_rationale')}")
            if suggestion.get("acceptance_criteria"):
                lines.append("  - Acceptance criteria:")
                for criterion in suggestion.get("acceptance_criteria", [])[:5]:
                    lines.append(f"    - {criterion}")
            if suggestion.get("examples"):
                lines.append("  - Examples:")
                for example in suggestion.get("examples", [])[:3]:
                    ref = example.get("source_ref") or {}
                    lines.append(f"    - `{ref.get('path')}:{ref.get('line')}` {example.get('snippet')}")
            for ref in (suggestion.get("destination_source_refs") or suggestion.get("destination_refs") or [])[:3]:
                lines.append(f"  - Destination ref: `{ref.get('path')}:{ref.get('line')}`")
            lines.append(f"  - Auto-apply: `{suggestion.get('auto_apply')}`")
            decisions = [
                option.get("decision")
                for option in (suggestion.get("selection_shape") or {}).get("options", [])
                if option.get("decision")
            ] or list(suggestion.get("selection_options") or [])
            if decisions:
                lines.append(f"  - Selection options: `{', '.join(decisions)}`")
            if suggestion.get("suggestion_only_contract"):
                lines.append(f"  - Contract: {suggestion.get('suggestion_only_contract')}")
    if reads:
        lines.extend(["", "## Next Reads", ""])
        for read in reads[:8]:
            _append_markdown_read(lines, read)
    return "\n".join(lines).rstrip()


def _artifact_trust_label(verdict: str | None) -> str:
    if verdict == "current":
        return "Yes, but only after checking the cited front-door source."
    if verdict in {"stale", "superseded", "contradicted", "historical_provenance"}:
        return "No, not as the current source of truth."
    return "Unknown from the inspected local evidence."


def render_follow_next_reads_text(payload: dict) -> str:
    report = payload["follow_next_reads"]
    instruction = report.get("agent_instruction") or {}
    reviewed = report.get("reviewed_usability_decisions") or {}
    status_counts = reviewed.get("status_counts") or {}
    gate = report.get("reviewed_follow_through_answer_gate") or {}
    bundle = report.get("recovery_bundle") or {}
    step_summary = report.get("workflow_step_summary") or {}
    lines = [
        "Follow Next Reads",
        f"Status: {report.get('status')}",
        f"Selected: {report.get('selected_count', 0)}",
        f"Expanded: {report.get('expanded_count', 0)}",
        f"Failed: {report.get('failed_count', 0)}",
        f"Usable recovery evidence: {report.get('usable_recovery_evidence_count', 0)}",
        f"Reviewed usable recovery evidence: {report.get('reviewed_usable_recovery_evidence_count', 0)}",
    ]
    if report.get("diagnostic_only_message"):
        lines.append(f"Diagnostic-only default skip: {report.get('diagnostic_only_message')}")
    if status_counts:
        lines.append(
            "Reviewed usability statuses: "
            + ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items()))
        )
    if bundle:
        boundaries = bundle.get("omitted_or_recovery_boundaries") or {}
        category_counts = bundle.get("category_counts") or {}
        lines.extend(
            [
                "Recovery Bundle:",
                f"- category_count: {len(category_counts)}",
                f"- unselected_recovery_count: {boundaries.get('unselected_recovery_count', 0)}",
                f"- failed_recovery_count: {boundaries.get('failed_count', 0)}",
                "- evidence_handles: hashed in JSON; raw targets and paths omitted from text output",
            ]
        )
    if gate:
        lines.extend(
            [
                "Reviewed Answer Gate:",
                f"- status: {gate.get('status')}",
                f"- decision: {gate.get('decision')}",
                f"- answer_ready_without_boundaries: {gate.get('answer_ready_without_boundaries')}",
            ]
        )
    if step_summary:
        lines.append(
            "Workflow steps: bundled_required_command_ids="
            + ", ".join(step_summary.get("bundled_required_command_ids") or [])
            + f"; avoided={step_summary.get('separate_recovery_command_steps_avoided', 0)}"
        )
    if instruction:
        commands = instruction.get("commands") or []
        command_ids = [str(command.get("command_id")) for command in commands if command.get("command_id")]
        lines.extend(
            [
                "Agent Instruction:",
                f"- decision: {instruction.get('decision')}",
                f"- required_next_action: {instruction.get('required_next_action')}",
                f"- command_count: {len(commands)}",
                f"- command_ids: {', '.join(command_ids) if command_ids else 'none'}",
                "- commands: omitted from text output; use JSON for executable saved-report commands",
            ]
        )
        for item in (instruction.get("do_not_answer_until") or [])[:4]:
            lines.append(f"- gate: {item}")
    lines.append("Claim boundaries:")
    for key, value in sorted((report.get("claim_boundaries") or {}).items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def render_follow_next_reads_markdown(payload: dict) -> str:
    report = payload["follow_next_reads"]
    instruction = report.get("agent_instruction") or {}
    reviewed = report.get("reviewed_usability_decisions") or {}
    status_counts = reviewed.get("status_counts") or {}
    gate = report.get("reviewed_follow_through_answer_gate") or {}
    bundle = report.get("recovery_bundle") or {}
    step_summary = report.get("workflow_step_summary") or {}
    lines = [
        "# Follow Next Reads",
        "",
        f"- `status`: `{report.get('status')}`",
        f"- `selected_count`: `{report.get('selected_count', 0)}`",
        f"- `expanded_count`: `{report.get('expanded_count', 0)}`",
        f"- `failed_count`: `{report.get('failed_count', 0)}`",
        f"- `usable_recovery_evidence_count`: `{report.get('usable_recovery_evidence_count', 0)}`",
        f"- `reviewed_usable_recovery_evidence_count`: `{report.get('reviewed_usable_recovery_evidence_count', 0)}`",
    ]
    if status_counts:
        lines.append(
            f"- `reviewed_usability_statuses`: `{', '.join(f'{key}={value}' for key, value in sorted(status_counts.items()))}`"
        )
    if bundle:
        boundaries = bundle.get("omitted_or_recovery_boundaries") or {}
        category_counts = bundle.get("category_counts") or {}
        lines.extend(
            [
                "",
                "## Recovery Bundle",
                "",
                f"- `category_count`: `{len(category_counts)}`",
                f"- `unselected_recovery_count`: `{boundaries.get('unselected_recovery_count', 0)}`",
                f"- `failed_recovery_count`: `{boundaries.get('failed_count', 0)}`",
                "- `evidence_handles`: hashed in JSON; raw targets and paths omitted from Markdown output",
            ]
        )
    if gate:
        lines.extend(
            [
                "",
                "## Reviewed Answer Gate",
                "",
                f"- `status`: `{gate.get('status')}`",
                f"- `decision`: `{gate.get('decision')}`",
                f"- `answer_ready_without_boundaries`: `{gate.get('answer_ready_without_boundaries')}`",
            ]
        )
    if step_summary:
        lines.append(
            f"- `bundled_required_command_ids`: `{', '.join(step_summary.get('bundled_required_command_ids') or [])}`"
        )
        lines.append(
            f"- `separate_recovery_command_steps_avoided`: `{step_summary.get('separate_recovery_command_steps_avoided', 0)}`"
        )
    if instruction:
        commands = instruction.get("commands") or []
        command_ids = [str(command.get("command_id")) for command in commands if command.get("command_id")]
        lines.extend(
            [
                "",
                "## Agent Instruction",
                "",
                f"- `decision`: `{instruction.get('decision')}`",
                f"- `required_next_action`: `{instruction.get('required_next_action')}`",
                f"- `command_count`: `{len(commands)}`",
                f"- `command_ids`: `{', '.join(command_ids) if command_ids else 'none'}`",
                "- `commands`: omitted from Markdown output; use JSON for executable saved-report commands",
            ]
        )
        for item in (instruction.get("do_not_answer_until") or [])[:4]:
            lines.append(f"- Gate: {item}")
    lines.extend(["", "## Claim Boundaries", ""])
    for key, value in sorted((report.get("claim_boundaries") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines)


def render_execute_continuation_text(payload: dict) -> str:
    receipt = payload["execute_continuation"]
    selection = receipt.get("selection") or {}
    binding = receipt.get("packet_binding") or {}
    geometry_gate = receipt.get("geometry_causality_gate") or {}
    claim_boundaries = receipt.get("claim_boundaries") or {}
    lines = [
        "Execute Continuation",
        f"Status: {receipt.get('status')}",
        f"Successor state: {receipt.get('successor_state')}",
        f"Remaining named gap: {receipt.get('remaining_named_gap')}",
        f"Packet id: {binding.get('packet_id')}",
        f"Selected/expanded steps: {selection.get('selected_count')} / {selection.get('expanded_count')}",
    ]
    if binding.get("canonical_packet_fingerprint"):
        lines.append(f"Canonical packet fingerprint: {binding.get('canonical_packet_fingerprint')}")
    if geometry_gate:
        lines.append(
            "Geometry causality gate: "
            f"original_expanded={geometry_gate.get('original_decision_rationale_expanded')} "
            f"later_radius_cannot_close={geometry_gate.get('later_radius_evidence_cannot_close_answer')} "
            f"answer_closure_allowed={geometry_gate.get('answer_closure_allowed_for_geometry_causality')}"
        )
    if receipt.get("residual_unknowns"):
        lines.append("Residual unknowns:")
        for item in list(receipt.get("residual_unknowns") or [])[:8]:
            lines.append(f"- {item}")
    if claim_boundaries:
        lines.append(
            "Claim boundaries: "
            f"fresh_ask_allowed={claim_boundaries.get('fresh_ask_allowed')} "
            f"broad_search_rerun={claim_boundaries.get('broad_search_rerun')} "
            f"bulk_line_match_allowed={claim_boundaries.get('bulk_line_match_allowed')}"
        )
    searched_scope = receipt.get("searched_scope") or {}
    if searched_scope:
        lines.append(
            "Searched scope: "
            f"expanded_count={searched_scope.get('expanded_count')} "
            f"scope_boundary={searched_scope.get('scope_boundary')}"
        )
    lines.append(
        "Receiver rule: answer only if the successor state and required gate allow closure; "
        "otherwise report the remaining named gap and do not run fresh broad search."
    )
    return "\n".join(lines)


def render_execute_continuation_markdown(payload: dict) -> str:
    receipt = payload["execute_continuation"]
    selection = receipt.get("selection") or {}
    binding = receipt.get("packet_binding") or {}
    geometry_gate = receipt.get("geometry_causality_gate") or {}
    claim_boundaries = receipt.get("claim_boundaries") or {}
    lines = [
        "# Execute Continuation",
        "",
        f"- `status`: `{receipt.get('status')}`",
        f"- `successor_state`: `{receipt.get('successor_state')}`",
        f"- `remaining_named_gap`: `{receipt.get('remaining_named_gap')}`",
        f"- `packet_id`: `{binding.get('packet_id')}`",
        f"- `selected_steps`: `{selection.get('selected_count')}`",
        f"- `expanded_steps`: `{selection.get('expanded_count')}`",
    ]
    if binding.get("canonical_packet_fingerprint"):
        lines.append(f"- `canonical_packet_fingerprint`: `{binding.get('canonical_packet_fingerprint')}`")
    if geometry_gate:
        lines.extend(
            [
                "",
                "## Geometry Causality Gate",
                "",
                f"- `original_decision_rationale_expanded`: `{geometry_gate.get('original_decision_rationale_expanded')}`",
                f"- `later_radius_evidence_cannot_close_answer`: `{geometry_gate.get('later_radius_evidence_cannot_close_answer')}`",
                f"- `answer_closure_allowed_for_geometry_causality`: `{geometry_gate.get('answer_closure_allowed_for_geometry_causality')}`",
            ]
        )
    if receipt.get("residual_unknowns"):
        lines.extend(["", "## Residual Unknowns", ""])
        for item in list(receipt.get("residual_unknowns") or [])[:8]:
            lines.append(f"- `{item}`")
    if claim_boundaries:
        lines.extend(
            [
                "",
                "## Claim Boundaries",
                "",
                f"- `fresh_ask_allowed`: `{claim_boundaries.get('fresh_ask_allowed')}`",
                f"- `broad_search_rerun`: `{claim_boundaries.get('broad_search_rerun')}`",
                f"- `bulk_line_match_allowed`: `{claim_boundaries.get('bulk_line_match_allowed')}`",
            ]
        )
    searched_scope = receipt.get("searched_scope") or {}
    if searched_scope:
        lines.extend(
            [
                "",
                "## Searched Scope",
                "",
                f"- `expanded_count`: `{searched_scope.get('expanded_count')}`",
                f"- `scope_boundary`: {searched_scope.get('scope_boundary')}",
            ]
        )
    lines.extend(
        [
            "",
            "## Receiver Rule",
            "",
            "- Answer only if the successor state and required gate allow closure; otherwise report the remaining named gap and do not run fresh broad search.",
        ]
    )
    return "\n".join(lines)


_RECOVERY_REVIEW_COUNT_KEYS = (
    "recovery_opened",
    "reviewed_usable",
    "reviewed_not_useful",
    "still_unreviewed",
    "review_gap",
    "review_debt",
)


def _failure_analysis_recovery_review_text_lines(story_packet: dict) -> list[str]:
    handoff = story_packet.get("recovery_review_handoff") or {}
    summary = story_packet.get("reviewed_recovery_summary") or {}
    if not handoff and not summary:
        return []

    summary_counts = summary.get("counts") or {}
    handoff_counts = handoff.get("counts") or {}
    summary_count_text = ", ".join(
        f"{key}={summary_counts.get(key, 0)}" for key in _RECOVERY_REVIEW_COUNT_KEYS
    )
    handoff_count_text = ", ".join(
        f"{key}={handoff_counts.get(key, 0)}"
        for key in (
            "review_debt",
            "safe_review_item_count",
            "not_rendered_due_to_limit",
        )
        if key in handoff_counts
    ) or "none"
    no_open_debt = "yes" if not summary.get("review_debt_remaining") else "no"
    lines = [
        "Recovery review:",
        (
            f"- summary: {summary.get('status')} handoff={summary.get('recovery_review_handoff_status')} "
            f"story_packet={summary.get('story_packet_status')} no_open_review_debt={no_open_debt}"
        ),
        f"- counts: {summary_count_text}",
        f"- handoff counts: {handoff_count_text}",
    ]

    remaining_gaps = summary.get("remaining_review_gaps") or []
    if remaining_gaps:
        for gap in remaining_gaps[:6]:
            lines.append(
                f"- remaining review gap {gap.get('gap_id')}: "
                f"{gap.get('reason')} count={gap.get('count', 0)}"
            )
    else:
        lines.append("- remaining review gaps: none")

    blocked_claims = [
        row for row in (handoff.get("blocked_claims_until_review") or []) if row.get("blocked")
    ]
    if blocked_claims:
        claim_ids = ", ".join(str(row.get("claim_id")) for row in blocked_claims[:8])
        lines.append(f"- blocked claims until review: {claim_ids}")
    else:
        lines.append("- blocked claims until review: none")

    for item in (handoff.get("review_items") or [])[:6]:
        target = item.get("target_handle") or {}
        source = item.get("source_handle") or {}
        line_range = item.get("line_range") or {}
        lines.append(
            f"- review item {item.get('review_item_id')}: status={item.get('status')} "
            f"target={target.get('target_handle')} source={source.get('path_handle')} "
            f"lines={line_range.get('start')}..{line_range.get('end')} "
            f"reason={item.get('reason') or item.get('reason_label')}"
        )

    return lines


def _failure_analysis_recovery_review_markdown_lines(story_packet: dict) -> list[str]:
    handoff = story_packet.get("recovery_review_handoff") or {}
    summary = story_packet.get("reviewed_recovery_summary") or {}
    if not handoff and not summary:
        return []

    summary_counts = summary.get("counts") or {}
    handoff_counts = handoff.get("counts") or {}
    lines = [
        "",
        "### Recovery Review",
        "",
        f"- Summary status: `{summary.get('status')}`",
        f"- Handoff status: `{summary.get('recovery_review_handoff_status')}`",
        f"- Story packet status: `{summary.get('story_packet_status')}`",
        f"- No open review debt: `{'yes' if not summary.get('review_debt_remaining') else 'no'}`",
        (
            "- Review counts: "
            + ", ".join(
                f"`{key}={summary_counts.get(key, 0)}`" for key in _RECOVERY_REVIEW_COUNT_KEYS
            )
        ),
    ]
    if handoff_counts:
        lines.append(
            "- Handoff counts: "
            + ", ".join(
                f"`{key}={handoff_counts.get(key, 0)}`"
                for key in (
                    "review_debt",
                    "safe_review_item_count",
                    "not_rendered_due_to_limit",
                )
                if key in handoff_counts
            )
        )

    remaining_gaps = summary.get("remaining_review_gaps") or []
    if remaining_gaps:
        lines.append("- Remaining review gaps:")
        for gap in remaining_gaps[:6]:
            lines.append(
                f"  - `{gap.get('gap_id')}`: `{gap.get('reason')}` count `{gap.get('count', 0)}`"
            )
    else:
        lines.append("- Remaining review gaps: `none`")

    blocked_claims = [
        row for row in (handoff.get("blocked_claims_until_review") or []) if row.get("blocked")
    ]
    if blocked_claims:
        lines.append(
            "- Blocked claims until review: "
            + ", ".join(f"`{row.get('claim_id')}`" for row in blocked_claims[:8])
        )
    else:
        lines.append("- Blocked claims until review: `none`")

    review_items = handoff.get("review_items") or []
    if review_items:
        lines.append("- Safe review items:")
        for item in review_items[:6]:
            target = item.get("target_handle") or {}
            source = item.get("source_handle") or {}
            line_range = item.get("line_range") or {}
            lines.append(
                f"  - `{item.get('review_item_id')}` status `{item.get('status')}`; "
                f"target `{target.get('target_handle')}`; source `{source.get('path_handle')}`; "
                f"lines `{line_range.get('start')}..{line_range.get('end')}`; "
                f"reason `{item.get('reason') or item.get('reason_label')}`"
            )

    return lines


def render_failure_analysis_text(payload: dict) -> str:
    report = payload["failure_analysis"]
    scope = report.get("story_scope") or {}
    next_reads = report.get("next_reads") or {}
    narrative = report.get("narrative_readiness") or {}
    story_phase_summary = report.get("story_phase_summary") or {}
    story_packet = report.get("devlog_story_packet") or {}
    devlog_brief = report.get("devlog_brief") or {}
    semantic_debugging_categories = story_packet.get("semantic_debugging_categories") or []
    behavior_quality_labels = story_packet.get("behavior_quality_labels") or []
    narrative_ledger = report.get("committed_safe_narrative_ledger") or []
    repo_chronology = report.get("repo_handle_chronology") or {}
    repo_counts = repo_chronology.get("handle_type_counts") or {}
    repo_date_range = repo_chronology.get("date_range") or {}
    lines = [
        "Failure Analysis",
        f"Status: {report.get('status')}",
        f"Story: {scope.get('story_id')}",
        f"Question family: {scope.get('primary_question_family')}",
        f"Date range: {(report.get('date_range') or {}).get('start')} to {(report.get('date_range') or {}).get('end')}",
        f"Play-by-play rows: {len(report.get('play_by_play') or [])}",
        f"Failure ledger rows: {len(report.get('failure_mode_ledger') or [])}",
        f"Narrative readiness: {narrative.get('status')} ({narrative.get('ledger_row_count', len(narrative_ledger))} ledger rows)",
        f"Story phase summary: {story_phase_summary.get('status')} ({story_phase_summary.get('usable_phase_count', 0)}/{story_phase_summary.get('phase_count', 0)} usable)",
        f"Devlog story packet: {story_packet.get('status')} ({len(story_packet.get('supported_claims') or [])} supported claims, {len(story_packet.get('explicit_non_claims') or [])} non-claims, {len(semantic_debugging_categories)} semantic-debugging categories, {len(behavior_quality_labels)} compatibility case labels)",
        f"Repo handle chronology: {repo_chronology.get('status')} ({repo_chronology.get('phase_row_count', 0)} rows)",
        f"Repo handle date range: {repo_date_range.get('start')} to {repo_date_range.get('end')}",
        f"Repo handle counts: {', '.join(f'{key}={value}' for key, value in sorted(repo_counts.items())) or 'none'}",
        f"Next reads: {next_reads.get('status')} ({next_reads.get('count', 0)})",
        f"Story phase gaps: {', '.join(str(item) for item in story_phase_summary.get('gap_reasons') or []) or 'none'}",
        f"Story phase next review: {', '.join(str(item) for item in story_phase_summary.get('next_review_needs') or []) or 'none'}",
        f"Devlog brief: {devlog_brief.get('status')} ({len(devlog_brief.get('short_take') or [])} short-take lines, {len(devlog_brief.get('timeline_highlights') or [])} timeline highlights)",
        "Devlog brief short take:",
    ]
    for item in (devlog_brief.get("short_take") or [])[:6]:
        support = ", ".join(str(surface) for surface in item.get("support") or []) or "no support surface"
        lines.append(f"- {item.get('take_id')}: {item.get('line')} Support: {support}")
    lines.append("Devlog brief evidence-backed summary:")
    for item in (devlog_brief.get("evidence_backed_summary") or [])[:6]:
        support = ", ".join(str(surface) for surface in item.get("support") or []) or "no support surface"
        lines.append(f"- {item.get('summary_id')}: {item.get('bullet')} Support: {support}")
    lines.append("Devlog brief category framing:")
    for category in (devlog_brief.get("semantic_debugging_category_framing") or [])[:6]:
        case_labels = ", ".join(str(item) for item in category.get("case_labels") or []) or "none"
        handles = ", ".join(str(item) for item in category.get("representative_safe_handles") or []) or "no representative handle"
        lines.append(
            f"- category {category.get('category_id')} ({category.get('classification_status')}; "
            f"case labels={case_labels}) Handles: {handles}"
        )
    lines.append("Devlog brief failure modes:")
    for item in (devlog_brief.get("failure_mode_framing") or [])[:6]:
        lines.append(
            f"- {item.get('failure_id')}: {item.get('failure_mode')} "
            f"status={item.get('status')} surface={item.get('evidence_surface')}"
        )
    non_claims_and_gaps = devlog_brief.get("non_claims_and_gaps") or {}
    lines.append("Devlog brief non-claims and gaps:")
    for item in (non_claims_and_gaps.get("non_claims") or [])[:6]:
        lines.append(f"- non-claim {item.get('non_claim')}: {item.get('boundary')}")
    for item in (non_claims_and_gaps.get("gaps") or [])[:6]:
        lines.append(f"- gap {item.get('gap_id')}: {item.get('status')} via {item.get('reason')}")
    brief_next_reads = devlog_brief.get("next_reads") or {}
    lines.append(f"Devlog brief next reads: {brief_next_reads.get('status')} ({brief_next_reads.get('count', 0)})")
    for read in (brief_next_reads.get("reads") or [])[:6]:
        lines.append(f"- next-read {read.get('read_id')}: {read.get('kind')} {read.get('source_handle')}")
    instruction = report.get("agent_instruction") or {}
    if instruction:
        lines.append(
            f"Agent instruction: {instruction.get('decision')} -> {instruction.get('required_next_action')}"
        )
        for command in (instruction.get("commands") or [])[:3]:
            if command.get("command"):
                lines.append(f"- {command.get('command_id')}: {command.get('command')}")
    brief_critique = devlog_brief.get("tool_critique") or {}
    packaged = brief_critique.get("what_the_tool_could_package") or {}
    lines.append(
        "Devlog brief tool critique: "
        f"{', '.join(f'{key}={value}' for key, value in sorted(packaged.items())) or 'none'}"
    )
    for gap in (brief_critique.get("gaps_and_limitations") or [])[:6]:
        lines.append(f"- limitation: {gap}")
    readiness = devlog_brief.get("readiness") or {}
    lines.append(
        "Devlog brief readiness: "
        f"packet={readiness.get('story_packet_status')} claims={readiness.get('supported_claim_count')} "
        f"categories={readiness.get('category_count')} gaps={readiness.get('gap_count')} "
        f"final_prose={readiness.get('brief_is_final_prose')}"
    )
    lines.append("Devlog brief claim boundaries:")
    for boundary in (devlog_brief.get("claim_boundaries") or [])[:6]:
        lines.append(f"- {boundary}")
    lines.extend([
        "Devlog brief timeline highlights:",
    ])
    for row in (devlog_brief.get("timeline_highlights") or [])[:6]:
        dates = row.get("start") or row.get("timestamp_utc") or "undated"
        if row.get("end") and row.get("end") != row.get("start"):
            dates = f"{dates}..{row.get('end')}"
        lines.append(f"- {row.get('timeline_id')}: {dates}; {row.get('source')} status={row.get('status')}")
    lines.extend([
        "Story phase rows:",
    ]
    )
    for row in (story_phase_summary.get("rows") or [])[:10]:
        details = [f"status={row.get('status')}"]
        if row.get("task_id"):
            details.append(f"task={row.get('task_id')}")
        row_date_range = row.get("date_range") or {}
        if row_date_range.get("start") or row_date_range.get("end"):
            details.append(f"dates={row_date_range.get('start')}..{row_date_range.get('end')}")
        if row.get("gap_reasons"):
            details.append(f"gaps={','.join(str(item) for item in row.get('gap_reasons') or [])}")
        lines.append(f"- {row.get('phase_id')}: {'; '.join(details)}")
    lines.extend([
        "Devlog story packet:",
    ])
    for item in (story_packet.get("take_candidates") or [])[:5]:
        lines.append(f"- take {item.get('take_id')}: {item.get('take')}")
    for claim in (story_packet.get("supported_claims") or [])[:6]:
        lines.append(f"- supported {claim.get('claim_id')}: {claim.get('claim')}")
    for category in semantic_debugging_categories[:8]:
        strength = category.get("evidence_strength") or {}
        handles = ", ".join(str(item) for item in category.get("representative_safe_handles") or []) or "no representative handle"
        case_labels = ", ".join(str(item) for item in category.get("case_labels") or []) or "none"
        lines.append(
            f"- semantic-debug category {category.get('category_id')} "
            f"({category.get('classification_status')}, scope={category.get('scope')}; case labels={case_labels}): "
            f"rows={strength.get('matched_row_count', 0)} handles={strength.get('representative_handle_count', 0)}; "
            f"{category.get('source_boundary')} Handles: {handles}"
        )
    for non_claim in (story_packet.get("explicit_non_claims") or [])[:8]:
        lines.append(f"- non-claim {non_claim.get('claim_id')}: {non_claim.get('non_claim')}")
    for gap in (story_packet.get("gaps") or [])[:6]:
        lines.append(f"- gap {gap.get('gap_id')}: {gap.get('status')} ({gap.get('reason')})")
    lines.extend(_failure_analysis_recovery_review_text_lines(story_packet))
    lines.extend([
        "Repo handle chronology rows:",
    ])
    for row in (repo_chronology.get("rows") or [])[:10]:
        details = [f"{row.get('handle_type')}={row.get('handle')}"]
        if row.get("date"):
            details.append(f"date={row.get('date')}")
        if row.get("status"):
            details.append(f"status={row.get('status')}")
        lines.append(f"- {row.get('phase_id')}: {'; '.join(details)}")
    lines.extend([
        "Committed-safe narrative ledger:",
    ])
    for row in narrative_ledger[:10]:
        details = []
        if row.get("candidate_id"):
            details.append(f"candidate={row.get('candidate_id')}")
        if row.get("recovery_kind"):
            details.append(f"recovery={row.get('recovery_kind')}")
        if row.get("reason_codes"):
            details.append(f"reasons={','.join(str(item) for item in row.get('reason_codes') or [])}")
        suffix = f" ({'; '.join(details)})" if details else ""
        lines.append(f"- {row.get('ledger_kind')}: {row.get('status') or row.get('timestamp_utc') or 'recorded'}{suffix}")
    lines.append(
        "Claim boundaries:",
    )
    for key, value in sorted((report.get("claim_boundaries") or {}).items()):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def render_failure_analysis_markdown(payload: dict) -> str:
    report = payload["failure_analysis"]
    scope = report.get("story_scope") or {}
    date_range = report.get("date_range") or {}
    next_reads = report.get("next_reads") or {}
    narrative = report.get("narrative_readiness") or {}
    story_phase_summary = report.get("story_phase_summary") or {}
    story_packet = report.get("devlog_story_packet") or {}
    devlog_brief = report.get("devlog_brief") or {}
    semantic_debugging_categories = story_packet.get("semantic_debugging_categories") or []
    behavior_quality_labels = story_packet.get("behavior_quality_labels") or []
    narrative_ledger = report.get("committed_safe_narrative_ledger") or []
    repo_chronology = report.get("repo_handle_chronology") or {}
    repo_counts = repo_chronology.get("handle_type_counts") or {}
    repo_date_range = repo_chronology.get("date_range") or {}
    lines = [
        "# Failure Analysis",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Story: `{scope.get('story_id')}`",
        f"- Question family: `{scope.get('primary_question_family')}`",
        f"- Date range: `{date_range.get('start')}` to `{date_range.get('end')}`",
        f"- Play-by-play rows: `{len(report.get('play_by_play') or [])}`",
        f"- Failure ledger rows: `{len(report.get('failure_mode_ledger') or [])}`",
        f"- Narrative readiness: `{narrative.get('status')}`",
        f"- Story phase summary: `{story_phase_summary.get('status')}` (`{story_phase_summary.get('usable_phase_count', 0)}`/`{story_phase_summary.get('phase_count', 0)}` usable)",
        f"- Devlog story packet: `{story_packet.get('status')}` (`{len(story_packet.get('supported_claims') or [])}` supported claims, `{len(story_packet.get('explicit_non_claims') or [])}` non-claims, `{len(semantic_debugging_categories)}` semantic-debugging categories, `{len(behavior_quality_labels)}` compatibility case labels)",
        f"- Repo handle chronology: `{repo_chronology.get('status')}` (`{repo_chronology.get('phase_row_count', 0)}` rows)",
        f"- Repo handle date range: `{repo_date_range.get('start')}` to `{repo_date_range.get('end')}`",
        f"- Committed-safe narrative ledger rows: `{narrative.get('ledger_row_count', len(narrative_ledger))}`",
        f"- Next reads: `{next_reads.get('status')}` (`{next_reads.get('count', 0)}`)",
        "",
        "## Devlog Brief",
        "",
        f"- Status: `{devlog_brief.get('status')}`",
        f"- Mode: `{devlog_brief.get('mode')}`",
        f"- Short-take lines: `{len(devlog_brief.get('short_take') or [])}`",
        f"- Timeline highlights: `{len(devlog_brief.get('timeline_highlights') or [])}`",
        f"- Semantic-debugging category frames: `{len(devlog_brief.get('semantic_debugging_category_framing') or [])}`",
        "",
        "### Short Take",
        "",
    ]
    for item in devlog_brief.get("short_take") or []:
        support = ", ".join(str(surface) for surface in item.get("support") or [])
        lines.append(f"- `{item.get('take_id')}`: {item.get('line')} Support: `{support or 'none'}`")
    lines.extend(["", "### Evidence-Backed Summary", ""])
    for item in devlog_brief.get("evidence_backed_summary") or []:
        support = ", ".join(str(surface) for surface in item.get("support") or [])
        lines.append(f"- {item.get('bullet')} Support: `{support or 'none'}`")
    lines.extend(["", "### Timeline Highlights", ""])
    for row in devlog_brief.get("timeline_highlights") or []:
        bits = [
            f"`{row.get('timeline_id')}`",
            f"source `{row.get('source')}`",
            f"status `{row.get('status')}`",
        ]
        if row.get("start") or row.get("end"):
            bits.append(f"dates `{row.get('start')}..{row.get('end')}`")
        if row.get("timestamp_utc"):
            bits.append(f"at `{row.get('timestamp_utc')}`")
        if row.get("task_id"):
            bits.append(f"task `{row.get('task_id')}`")
        lines.append(f"- {'; '.join(bits)}")
    lines.extend(["", "### Semantic-Debugging Category Framing", ""])
    for category in devlog_brief.get("semantic_debugging_category_framing") or []:
        handles = ", ".join(str(item) for item in category.get("representative_safe_handles") or []) or "none"
        surfaces = ", ".join(str(item) for item in category.get("support_surfaces") or []) or "none"
        case_labels = ", ".join(str(item) for item in category.get("case_labels") or []) or "none"
        lines.append(
            f"- `{category.get('category_id')}` (`{category.get('classification_status')}`): "
            f"case labels `{case_labels}`; handles `{handles}`; support `{surfaces}`"
        )
    lines.extend(["", "### Failure-Mode Framing", ""])
    for item in devlog_brief.get("failure_mode_framing") or []:
        lines.append(
            f"- `{item.get('failure_id')}`: `{item.get('failure_mode')}` "
            f"status `{item.get('status')}` via `{item.get('evidence_surface')}`. "
            f"Boundary: {item.get('boundary')}"
        )
    non_claims_and_gaps = devlog_brief.get("non_claims_and_gaps") or {}
    lines.extend(["", "### Non-Claims And Gaps", ""])
    for item in non_claims_and_gaps.get("non_claims") or []:
        lines.append(f"- non-claim `{item.get('non_claim')}` via `{item.get('support_surface')}`")
    for item in non_claims_and_gaps.get("gaps") or []:
        lines.append(f"- gap `{item.get('gap_id')}`: `{item.get('status')}` via `{item.get('reason')}`")
    brief_next_reads = devlog_brief.get("next_reads") or {}
    lines.extend(["", "### Next Reads And Tool Critique", ""])
    lines.append(f"- Next reads: `{brief_next_reads.get('status')}` (`{brief_next_reads.get('count', 0)}`)")
    for read in brief_next_reads.get("reads") or []:
        lines.append(f"- `{read.get('read_id')}`: `{read.get('kind')}` `{read.get('source_handle')}`")
    instruction = report.get("agent_instruction") or {}
    if instruction:
        lines.append(f"- Agent instruction: `{instruction.get('decision')}` -> `{instruction.get('required_next_action')}`")
        for command in (instruction.get("commands") or [])[:3]:
            if command.get("command"):
                lines.append(f"- `{command.get('command_id')}`: `{command.get('command')}`")
    brief_critique = devlog_brief.get("tool_critique") or {}
    lines.append(
        f"- Tool could package: `{', '.join(f'{key}={value}' for key, value in sorted((brief_critique.get('what_the_tool_could_package') or {}).items())) or 'none'}`"
    )
    for gap in brief_critique.get("gaps_and_limitations") or []:
        lines.append(f"- limitation: {gap}")
    readiness = devlog_brief.get("readiness") or {}
    lines.extend(["", "### Readiness And Claim Boundaries", ""])
    lines.append(f"- Story packet status: `{readiness.get('story_packet_status')}`")
    lines.append(f"- Supported claims: `{readiness.get('supported_claim_count')}`")
    lines.append(f"- Semantic-debugging categories: `{readiness.get('category_count')}`")
    lines.append(f"- Gaps: `{readiness.get('gap_count')}`")
    lines.append(f"- Brief is final prose: `{readiness.get('brief_is_final_prose')}`")
    for boundary in devlog_brief.get("claim_boundaries") or []:
        lines.append(f"- {boundary}")
    lines.extend(
        [
        "",
        "## Narrative Readiness",
        "",
        f"- Status: `{narrative.get('status')}`",
        f"- Candidates: `{narrative.get('candidate_count', 0)}`",
        f"- Recovery opened: `{narrative.get('recovery_opened_count', 0)}`",
        f"- Usable recovery evidence: `{narrative.get('usable_recovery_evidence_count', 0)}`",
        f"- Reason codes: `{', '.join(str(item) for item in narrative.get('reason_codes') or []) or 'none'}`",
        "",
        "## Story Phase Summary",
        "",
        f"- Status: `{story_phase_summary.get('status')}`",
        f"- Phases: `{story_phase_summary.get('phase_count', 0)}`",
        f"- Usable phases: `{story_phase_summary.get('usable_phase_count', 0)}`",
        f"- Gap reasons: `{', '.join(str(item) for item in story_phase_summary.get('gap_reasons') or []) or 'none'}`",
        f"- Next review needs: `{', '.join(str(item) for item in story_phase_summary.get('next_review_needs') or []) or 'none'}`",
        "",
        ]
    )
    for row in story_phase_summary.get("rows") or []:
        row_date_range = row.get("date_range") or {}
        bits = [
            f"`{row.get('phase_id')}`",
            f"status `{row.get('status')}`",
            f"usable `{row.get('usable')}`",
        ]
        if row.get("task_id"):
            bits.append(f"task `{row.get('task_id')}`")
        if row_date_range.get("start") or row_date_range.get("end"):
            bits.append(f"dates `{row_date_range.get('start')}..{row_date_range.get('end')}`")
        if row.get("handle_types"):
            bits.append(f"handles `{', '.join(str(item) for item in row.get('handle_types') or [])}`")
        if row.get("gap_reasons"):
            bits.append(f"gaps `{', '.join(str(item) for item in row.get('gap_reasons') or [])}`")
        lines.append(f"- {'; '.join(bits)}")
    lines.extend([
        "",
        "## Devlog Story Packet",
        "",
        f"- Status: `{story_packet.get('status')}`",
        f"- Mode: `{story_packet.get('mode')}`",
        f"- Dated arc rows: `{len(story_packet.get('dated_arc_rows') or [])}`",
        f"- Claim/support ledger rows: `{len(story_packet.get('claim_support_ledger') or [])}`",
        f"- Semantic-debugging categories: `{len(semantic_debugging_categories)}`",
        f"- Compatibility case labels: `{len(behavior_quality_labels)}`",
    ])
    lines.extend(_failure_analysis_recovery_review_markdown_lines(story_packet))
    lines.extend([
        "",
        "### Take Candidates",
        "",
    ])
    for item in story_packet.get("take_candidates") or []:
        lines.append(f"- `{item.get('take_id')}`: {item.get('take')}")
    lines.extend(["", "### Evidence-Backed Summary Bullets", ""])
    for item in story_packet.get("evidence_backed_summary_bullets") or []:
        support = ", ".join(str(surface) for surface in item.get("support") or [])
        lines.append(f"- {item.get('bullet')} Support: `{support or 'none'}`")
    lines.extend(["", "### Dated Arc Rows", ""])
    for row in (story_packet.get("dated_arc_rows") or [])[:12]:
        bits = [
            f"`{row.get('arc_id')}`",
            f"source `{row.get('source')}`",
            f"status `{row.get('status')}`",
        ]
        if row.get("task_id"):
            bits.append(f"task `{row.get('task_id')}`")
        if row.get("start") or row.get("end"):
            bits.append(f"dates `{row.get('start')}..{row.get('end')}`")
        if row.get("handle_types"):
            bits.append(f"handles `{', '.join(str(item) for item in row.get('handle_types') or [])}`")
        lines.append(f"- {'; '.join(bits)}")
    lines.extend(["", "### Supported Claims", ""])
    for claim in story_packet.get("supported_claims") or []:
        surfaces = ", ".join(str(surface) for surface in claim.get("support_surfaces") or [])
        lines.append(f"- `{claim.get('claim_id')}`: {claim.get('claim')} Support: `{surfaces or 'none'}`")
    lines.extend(["", "### Semantic-Debugging Categories", ""])
    for category in semantic_debugging_categories:
        strength = category.get("evidence_strength") or {}
        handles = ", ".join(str(item) for item in category.get("representative_safe_handles") or []) or "none"
        surfaces = ", ".join(str(surface) for surface in category.get("support_surfaces") or []) or "none"
        case_labels = ", ".join(str(item) for item in category.get("case_labels") or []) or "none"
        boundaries = "; ".join(str(item) for item in category.get("boundaries_non_claims") or [])
        lines.append(
            f"- `{category.get('category_id')}` (`{category.get('classification_status')}`, scope `{category.get('scope')}`): "
            f"case labels `{case_labels}`; `{strength.get('matched_row_count', 0)}` matched rows, "
            f"`{strength.get('representative_handle_count', 0)}` representative handles. "
            f"Handles: `{handles}`. Boundary: {category.get('source_boundary')} "
            f"Support: `{surfaces}`. Boundaries: {boundaries}"
        )
    lines.extend(["", "### Explicit Non-Claims", ""])
    for non_claim in story_packet.get("explicit_non_claims") or []:
        lines.append(f"- `{non_claim.get('claim_id')}`: `{non_claim.get('non_claim')}`")
    lines.extend(["", "### Story Gaps And Next Reads", ""])
    for gap in story_packet.get("gaps") or []:
        lines.append(f"- `{gap.get('gap_id')}`: `{gap.get('status')}` via `{gap.get('reason')}`")
    story_next_reads = story_packet.get("next_reads") or {}
    lines.append(f"- Next reads: `{story_next_reads.get('status')}` (`{story_next_reads.get('count', 0)}`)")
    lines.extend([
        "",
        "## Repo Handle Chronology",
        "",
        f"- Status: `{repo_chronology.get('status')}`",
        f"- Phase rows: `{repo_chronology.get('phase_row_count', 0)}`",
        f"- Handle counts: `{', '.join(f'{key}={value}' for key, value in sorted(repo_counts.items())) or 'none'}`",
        f"- Date range: `{repo_date_range.get('start')}` to `{repo_date_range.get('end')}`",
        f"- Gaps: `{', '.join(str(item) for item in repo_chronology.get('gaps') or []) or 'none'}`",
        "",
    ])
    for row in repo_chronology.get("rows") or []:
        bits = [
            f"`{row.get('phase_id')}`",
            f"`{row.get('handle_type')}` `{row.get('handle')}`",
            f"source `{row.get('source')}`",
        ]
        if row.get("date"):
            bits.append(f"date `{row.get('date')}`")
        if row.get("status"):
            bits.append(f"status `{row.get('status')}`")
        if row.get("confidence"):
            bits.append(f"confidence `{row.get('confidence')}`")
        lines.append(f"- {'; '.join(bits)}")
    lines.extend([
        "",
        "## Committed-Safe Narrative Ledger",
        "",
    ])
    for row in narrative_ledger:
        bits = [
            f"`{row.get('ledger_kind')}`",
            f"source `{row.get('source')}`",
        ]
        if row.get("candidate_id"):
            bits.append(f"candidate `{row.get('candidate_id')}`")
        if row.get("recovery_kind"):
            bits.append(f"recovery `{row.get('recovery_kind')}`")
        if row.get("timestamp_utc"):
            bits.append(f"at `{row.get('timestamp_utc')}`")
        if row.get("line_range"):
            line_range = row.get("line_range") or {}
            bits.append(f"lines `{line_range.get('start')}..{line_range.get('end')}`")
        if row.get("reason_codes"):
            bits.append(f"reasons `{', '.join(str(item) for item in row.get('reason_codes') or [])}`")
        lines.append(f"- {'; '.join(bits)}")
    lines.extend(
        [
        "",
        "## Failure Mode Ledger",
        "",
        ]
    )
    for row in report.get("failure_mode_ledger") or []:
        lines.append(
            f"- `{row.get('failure_mode')}`: `{row.get('status')}` via `{row.get('evidence_surface')}`"
        )
    lines.extend(["", "## Claim Boundaries", ""])
    for key, value in sorted((report.get("claim_boundaries") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines)


def _escape_source_expansion_record_headers(text: str) -> str:
    return "\n".join(
        f"  {line}" if SOURCE_EXPANSION_TEXT_RECORD_HEADER_RE.fullmatch(line) else line
        for line in text.split("\n")
    )


def render_source_expansion_text(payload: dict) -> str:
    report = payload["source_expansion"]
    lines = [
        f"Status: {report.get('status')}",
        f"Mode: {report.get('mode')}",
        f"Target: {report.get('requested_target')}",
    ]
    line_range = report.get("line_range") or {}
    if line_range:
        lines.append(f"Lines: {line_range.get('start')}..{line_range.get('end')} (anchor {line_range.get('anchor')})")
    counts = report.get("counts") or {}
    if counts:
        lines.append(
            "Records: "
            f"{counts.get('included_count', 0)} included, "
            f"{counts.get('omitted_count', 0)} omitted"
        )
    live_tail_caveat = report.get("live_tail_caveat") or {}
    if live_tail_caveat.get("applies"):
        lines.append(
            f"LIVE-TAIL CAVEAT: status={live_tail_caveat.get('status')} "
            f"terminal_state={live_tail_caveat.get('terminal_state_status')}"
        )
        lines.append(f"Terminal claim boundary: {live_tail_caveat.get('required_caveat')}")
    source_continuation = report.get("source_continuation") or {}
    if source_continuation.get("terminal_claim_requires_tail_followup"):
        lines.append(
            "SOURCE CONTINUES: "
            f"status={source_continuation.get('status')} "
            f"terminal_claim={source_continuation.get('terminal_claim_status')} "
            f"opened_end={source_continuation.get('opened_window_end_line')} "
            f"current_tail={source_continuation.get('observed_current_tail_line')}"
        )
        lines.append(
            "Required tail read: "
            f"{source_continuation.get('exact_next_read_more_command')}"
        )
    for record in report.get("records", []):
        lines.append(f"- {record.get('target')}: {record.get('role') or '(role unknown)'}")
        text = str(record.get("text") or "")
        if text:
            lines.append(_escape_source_expansion_record_headers(text))
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_source_expansion_markdown(payload: dict) -> str:
    report = payload["source_expansion"]
    lines = [
        "# Source Expansion",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Mode: `{report.get('mode')}`",
        f"- Target: `{report.get('requested_target')}`",
    ]
    line_range = report.get("line_range") or {}
    if line_range:
        lines.append(f"- Lines: `{line_range.get('start')}..{line_range.get('end')}` anchored at `{line_range.get('anchor')}`")
    counts = report.get("counts") or {}
    if counts:
        lines.append(f"- Records: `{counts.get('included_count', 0)}` included, `{counts.get('omitted_count', 0)}` omitted")
    live_tail_caveat = report.get("live_tail_caveat") or {}
    if live_tail_caveat.get("applies"):
        lines.append(
            f"- **LIVE-TAIL CAVEAT:** `status={live_tail_caveat.get('status')}` "
            f"`terminal_state={live_tail_caveat.get('terminal_state_status')}`"
        )
        lines.append(f"- **Terminal claim boundary:** {live_tail_caveat.get('required_caveat')}")
    source_continuation = report.get("source_continuation") or {}
    if source_continuation.get("terminal_claim_requires_tail_followup"):
        lines.append(
            f"- **SOURCE CONTINUES:** `status={source_continuation.get('status')}` "
            f"`terminal_claim={source_continuation.get('terminal_claim_status')}` "
            f"`opened_end={source_continuation.get('opened_window_end_line')}` "
            f"`current_tail={source_continuation.get('observed_current_tail_line')}`"
        )
        lines.append(
            f"- **Required tail read:** `{source_continuation.get('exact_next_read_more_command')}`"
        )
    for record in report.get("records", []):
        lines.extend(["", f"## {record.get('target')}", "", str(record.get("text") or "")])
    return "\n".join(lines)


def render_expansion_family_audit_text(payload: dict) -> str:
    report = payload["expansion_family_audit"]
    summary = report["summary"]
    lines = [
        "Expansion Family Audit",
        f"Status: {report['status']}",
        f"Verdict: {report['verdict']}",
        f"Process detour lesson policy: {report['decision']['process_detour_lesson_policy']}",
        f"Families: {summary['family_count']}",
        f"Workflow-specific families: {summary['workflow_specific_family_count']}",
        f"Candidate-supplement families: {summary['candidate_supplement_family_count']}",
        (
            "Workflow-specific candidate-supplement families: "
            f"{summary['workflow_specific_candidate_supplement_family_count']}"
        ),
        f"High-risk families: {', '.join(summary['high_risk_families']) or '(none)'}",
        (
            "Candidate-replacement families: "
            f"{', '.join(summary['candidate_replacement_families']) or '(none)'}"
        ),
        (
            "Flagged local/proper registry labels: "
            f"{', '.join(summary['flagged_local_or_proper_terms']) or '(none)'}"
        ),
        f"Generated metadata: {report['generated_metadata']['status']}",
        f"Next action: {report['next_direct_tool_action']['action']}",
        "Evidence basis:",
    ]
    for item in report["evidence_basis"]:
        lines.append(f"- {item['source']}: {item['role']}; {item['summary']}")
    lines.append("Families:")
    for row in report["families"]:
        flagged = ", ".join(row["flagged_local_or_proper_terms"]) or "-"
        lines.append(
            "- "
            f"{row['name']} | scope={row['core_scope']} | risk={row['overfit_risk']} | "
            f"metadata_role={row['generated_metadata_role']} | flagged={flagged}"
        )
    return "\n".join(lines)


def render_expansion_family_probe_text(payload: dict) -> str:
    report = payload["expansion_family_probe"]
    bridge = report["bridge"]
    counters = report["counters"]
    lines = [
        "Expansion Family Probe",
        f"Status: {report['status']}",
        f"Family: {report['family']}",
        f"Verdict: {report['verdict']}",
        f"Bridge status: {bridge['status']}",
        f"Common accepted cases: {bridge['accepted_common_case_count']}",
        f"Missing from bridge: {bridge['missing_from_bridge_count']}",
        f"Family triggers: {counters['family_trigger_count']}",
        f"Recommendation: {report['recommendation']['action']}",
        f"Runtime retrieval behavior changed: {report['runtime_retrieval_behavior_changed']}",
    ]
    term_status = counters["term_count_comparison"]["status"]
    lines.append(f"Term-count comparison: {term_status}")
    lines.append("Privacy: aggregate committed-safe counters only")
    return "\n".join(lines)


def render_expansion_family_probe_markdown(payload: dict) -> str:
    report = payload["expansion_family_probe"]
    bridge = report["bridge"]
    counters = report["counters"]
    metadata = report["family_metadata"]
    lines = [
        "# JSH-799 Process Detour Expansion Diagnostic Counters",
        "",
        "## Verdict",
        "",
        (
            "Current retained real-history evidence does not exercise "
            "`process_detour_lesson`; behavior comparison or removal is not "
            "justified yet."
            if counters["family_trigger_count"] == 0
            else (
                "Current retained real-history evidence exercises the target family; "
                "a default-off behavior comparison is required before any default change."
            )
        ),
        "",
        "Defaults are unchanged. This report does not suppress, replace, opt-in-gate, "
        "or promote generated metadata for the target family.",
        "",
        "## Aggregate Counters",
        "",
        f"- Family: `{report['family']}`",
        f"- Scope: `{metadata['core_scope']}`",
        f"- Overfit risk: `{metadata['overfit_risk']}`",
        f"- Generated-metadata role: `{metadata['generated_metadata_role']}`",
        f"- Bridge status: `{bridge['status']}`",
        f"- Validator status: `{bridge['validator_status']}`",
        f"- Manifest cases: `{bridge['manifest_case_count']}`",
        f"- Bridge cases: `{bridge['bridge_case_count']}`",
        f"- Common cases: `{bridge['common_case_count']}`",
        f"- Accepted common cases: `{bridge['accepted_common_case_count']}`",
        f"- Missing from newer manifest/bridge intersection: `{bridge['missing_from_bridge_count']}`",
        f"- Extra bridge cases: `{bridge['extra_bridge_case_count']}`",
        f"- Family trigger count: `{counters['family_trigger_count']}`",
        "",
        "## Planner Aggregate Check",
        "",
        (
            "- Common accepted cases are `18`: "
            f"`{bridge['planner_aggregate_match']['common_accepted_cases_18']}`"
        ),
        (
            "- Missing from newer manifest is `2`: "
            f"`{bridge['planner_aggregate_match']['missing_from_newer_manifest_2']}`"
        ),
        (
            "- Process-detour triggers are `0`: "
            f"`{bridge['planner_aggregate_match']['process_detour_lesson_triggers_0']}`"
        ),
        "",
        "## Family And Label Buckets",
        "",
        "Accepted common case family buckets:",
    ]
    lines.extend(_markdown_count_lines(counters["accepted_case_family_buckets"]))
    lines.append("")
    lines.append("Accepted common case label buckets:")
    lines.extend(_markdown_count_lines(counters["accepted_case_label_buckets"]))
    lines.append("")
    lines.append("Triggering case family buckets:")
    lines.extend(_markdown_count_lines(counters["triggering_case_family_buckets"]))
    lines.append("")
    lines.append("Triggering case label buckets:")
    lines.extend(_markdown_count_lines(counters["triggering_case_label_buckets"]))
    lines.extend(["", "## Term Count Comparison", ""])
    term_counts = counters["term_count_comparison"]
    lines.append(f"- Status: `{term_counts['status']}`")
    lines.append(
        "- Current-vs-family-excluded term-count buckets included: "
        f"`{term_counts['current_vs_family_excluded_term_count_buckets_included']}`"
    )
    if term_counts.get("current_vs_family_excluded_term_count_buckets_included"):
        lines.append("- Current term-count buckets:")
        lines.extend(_markdown_count_lines(term_counts["current_term_count_buckets"]))
        lines.append("- Family-excluded term-count buckets:")
        lines.extend(_markdown_count_lines(term_counts["family_excluded_term_count_buckets"]))
        lines.append("- Term delta buckets:")
        lines.extend(_markdown_count_lines(term_counts["term_delta_buckets"]))
    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            f"`{report['recommendation']['action']}`",
            "",
            report["recommendation"]["reason"],
            "",
            "Next direct tool action should be selected from measured tool evidence, "
            "not harness/live recovery.",
            "",
            "## Privacy Boundary",
            "",
            "This report includes aggregate counters, sanitized family names, and "
            "sanitized manifest labels only. It omits raw query strings, case IDs, "
            "expected refs, paths, target lines, session IDs, local usernames, "
            "per-row expansions, current benchmark miss IDs, and user-specific "
            "runtime phrasing.",
        ]
    )
    return "\n".join(lines)


def _markdown_count_lines(counts: dict) -> list[str]:
    if not counts:
        return ["- `(none)`: `0`"]
    return [f"- `{label}`: `{count}`" for label, count in sorted(counts.items())]


def render_expansion_family_audit_markdown(payload: dict) -> str:
    report = payload["expansion_family_audit"]
    summary = report["summary"]
    lines = [
        "# JSH-798 Expansion Family Hygiene Audit",
        "",
        "## Verdict",
        "",
        "Do not suppress, replace, or opt-in-gate `process_detour_lesson` yet. "
        "The evidence supports diagnostic visibility only for now.",
        "",
        "Defaults are unchanged. Generated metadata remains unpromoted and is not "
        "consumed by runtime search or replacement behavior.",
        "",
        "## Summary",
        "",
        f"- Families: `{summary['family_count']}`",
        f"- Workflow-specific families: `{summary['workflow_specific_family_count']}`",
        f"- Candidate-supplement families: `{summary['candidate_supplement_family_count']}`",
        (
            "- Workflow-specific candidate-supplement families: "
            f"`{summary['workflow_specific_candidate_supplement_family_count']}`"
        ),
        f"- High-risk families: `{', '.join(summary['high_risk_families']) or 'none'}`",
        (
            "- Candidate-replacement families: "
            f"`{', '.join(summary['candidate_replacement_families']) or 'none'}`"
        ),
        (
            "- Flagged local/proper terms: "
            f"`{', '.join(summary['flagged_local_or_proper_terms']) or 'none'}` "
            "(sanitized labels from the code registry only)"
        ),
        "",
        "## Counts",
        "",
        "| Family | Count |",
        "| --- | ---: |",
    ]
    for label, count in summary["core_scope_counts"].items():
        lines.append(f"| core_scope `{label}` | {count} |")
    for label, count in summary["overfit_risk_counts"].items():
        lines.append(f"| overfit_risk `{label}` | {count} |")
    for label, count in summary["generated_metadata_role_counts"].items():
        lines.append(f"| generated_metadata_role `{label}` | {count} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Process-detour policy: `{report['decision']['process_detour_lesson_policy']}`",
            f"- Runtime retrieval behavior changed: `{report['runtime_retrieval_behavior_changed']}`",
            f"- Generated metadata status: `{report['generated_metadata']['status']}`",
            "",
            "## Evidence Basis",
            "",
        ]
    )
    for item in report["evidence_basis"]:
        lines.append(f"- `{item['source']}`: {item['summary']}")
    lines.extend(
        [
            "",
            "## Next Direct Tool Action",
            "",
            f"`{report['next_direct_tool_action']['action']}`",
            "",
            report["next_direct_tool_action"]["description"],
            "",
            "This is not a harness/live recovery task and does not authorize "
            "auth/send/process route work.",
            "",
            "## Family Rows",
            "",
            "| Family | Scope | Risk | Generated metadata role | Flagged labels |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["families"]:
        flagged = ", ".join(row["flagged_local_or_proper_terms"]) or "-"
        lines.append(
            f"| `{row['name']}` | `{row['core_scope']}` | `{row['overfit_risk']}` | "
            f"`{row['generated_metadata_role']}` | `{flagged}` |"
        )
    lines.extend(
        [
            "",
            "## Privacy And Oracle Boundary",
            "",
            "The report includes aggregate counts, sanitized family names, risk labels, "
            "generated-metadata role labels, and sanitized flagged registry labels only.",
        ]
    )
    return "\n".join(lines)


def render_latest_user_messages_text(payload: dict) -> str:
    report = payload["latest_user_messages"]
    summary = report["summary"]
    lines = [
        "Latest User Messages",
        f"Status: {report['status']}",
        f"Lookup: {report['lookup']}",
        f"Sort: {report['sort_strategy']}",
        (
            "Searched: "
            f"{summary['searched_files']} files, {summary['records_read']} records, "
            f"{summary['candidate_user_message_count']} user candidates, "
            f"{summary['deduped_mirror_count']} deduped mirrors"
        ),
    ]
    for warning in report["sources"].get("warnings", []):
        lines.append(f"Warning: {warning}")
    for message in report["messages"]:
        lines.append(
            f"- {message['local_timestamp']} {message['source_kind']} "
            f"{message['source_identifier']} {message['source_ref']}: {message['snippet']}"
        )
    return "\n".join(lines)


def render_latest_user_messages_markdown(payload: dict) -> str:
    report = payload["latest_user_messages"]
    summary = report["summary"]
    lines = [
        "# Latest User Messages",
        "",
        f"**Status:** {report['status']}",
        f"**Lookup:** {report['lookup']}",
        f"**Sort:** {report['sort_strategy']}",
        (
            f"**Searched:** {summary['searched_files']} files, "
            f"{summary['records_read']} records, "
            f"{summary['candidate_user_message_count']} user candidates, "
            f"{summary['deduped_mirror_count']} deduped mirrors"
        ),
        "",
    ]
    warnings = report["sources"].get("warnings", [])
    if warnings:
        lines.append("## Warnings")
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.append("## Messages")
    for message in report["messages"]:
        lines.append(
            f"- `{message['local_timestamp']}` `{message['source_kind']}` "
            f"`{message['source_identifier']}` `{message['source_ref']}`: {message['snippet']}"
        )
    return "\n".join(lines)


def _status_anchor_navigation_only(report: dict) -> bool:
    contract = report.get("generated_report_contract") or {}
    return bool(
        contract.get("navigation_only")
        and contract.get("answer_from_report_allowed") is False
    )


def _render_status_anchor_navigation_only_text(report: dict) -> str:
    contract = report.get("generated_report_contract") or {}
    selected = report.get("selected_anchor") or {}
    readiness = report.get("answer_readiness") or {}
    freshness = readiness.get("parent_main_anchor_freshness") or {}
    interval = report.get("post_anchor_interval_reconstruction") or {}
    coverage = interval.get("coverage") or {}
    summary = report.get("summary") or {}
    return "\n".join(
        [
            "Status Anchor",
            f"Status: {report.get('status')}",
            "Answer status: not-ready/required-primary-followthrough",
            "Navigation only: true",
            "Answer from report allowed: false",
            "Required primary windows opened: "
            f"false (0/{contract.get('required_primary_window_count', 0)})",
            (
                "Selected parent history row: "
                f"{selected.get('timestamp_utc')} {selected.get('source_ref')}"
            ),
            (
                "Parent/main anchor freshness: "
                f"status={freshness.get('status')} current={freshness.get('current')}"
            ),
            (
                "Scanner coverage: "
                f"status={coverage.get('status')} complete={coverage.get('complete')} "
                f"records_after_anchor={coverage.get('records_after_anchor')} "
                f"retained_entries={summary.get('post_anchor_interval_entry_count', 0)} "
                f"omitted_entries={summary.get('post_anchor_interval_omitted_entry_count', 0)}"
            ),
            "Canonical follow-through path: "
            f"{contract.get('canonical_follow_through_path')}",
            f"STOP: {contract.get('stop')}",
        ]
    )


def _render_status_anchor_navigation_only_markdown(report: dict) -> str:
    contract = report.get("generated_report_contract") or {}
    selected = report.get("selected_anchor") or {}
    readiness = report.get("answer_readiness") or {}
    freshness = readiness.get("parent_main_anchor_freshness") or {}
    interval = report.get("post_anchor_interval_reconstruction") or {}
    coverage = interval.get("coverage") or {}
    summary = report.get("summary") or {}
    return "\n".join(
        [
            "# Status Anchor",
            "",
            f"- Status: `{report.get('status')}`",
            "- Answer status: `not-ready/required-primary-followthrough`",
            "- Navigation only: `true`",
            "- Answer from report allowed: `false`",
            "- Required primary windows opened: "
            f"`false` (`0/{contract.get('required_primary_window_count', 0)}`)",
            "- Selected parent history row: "
            f"`{selected.get('timestamp_utc')}` `{selected.get('source_ref')}`",
            "- Parent/main anchor freshness: "
            f"`status={freshness.get('status')}` `current={freshness.get('current')}`",
            "- Scanner coverage: "
            f"`status={coverage.get('status')}` `complete={coverage.get('complete')}` "
            f"`records_after_anchor={coverage.get('records_after_anchor')}` "
            f"`retained_entries={summary.get('post_anchor_interval_entry_count', 0)}` "
            f"`omitted_entries={summary.get('post_anchor_interval_omitted_entry_count', 0)}`",
            "- Canonical follow-through path: "
            f"`{contract.get('canonical_follow_through_path')}`",
            f"- **STOP:** {contract.get('stop')}",
        ]
    )


def _status_anchor_needs_clarification(report: dict) -> bool:
    if parent_thread_interval_continuation_packet(report):
        return False
    source_tiers = report.get("source_tiers") or {}
    structured_continuation_available = any(
        read.get("read_more_command")
        for read in source_tiers.get("required_primary") or []
    )
    return (
        report.get("status") == "anchor_ambiguous"
        and not report.get("selected_anchor")
        and not structured_continuation_available
    )


def _render_status_anchor_clarification_text(report: dict) -> str:
    clarification = report.get("clarification_request") or {}
    request = clarification.get("request") or (
        "I cannot safely identify the intended anchor. Please provide a quoted message, "
        "an exact timestamp, or a thread/session boundary."
    )
    return "\n".join(
        [
            "NOT ANSWERABLE YET",
            "Status: anchor_ambiguous",
            f"Clarification required: {request}",
            "STOP: Do not inspect transcript files or synthesize an answer until the user supplies the anchor.",
        ]
    )


def _render_status_anchor_clarification_markdown(report: dict) -> str:
    clarification = report.get("clarification_request") or {}
    request = clarification.get("request") or (
        "I cannot safely identify the intended anchor. Please provide a quoted message, "
        "an exact timestamp, or a thread/session boundary."
    )
    return "\n".join(
        [
            "# Not Answerable Yet",
            "",
            "- Status: `anchor_ambiguous`",
            f"- Clarification required: {request}",
            "- **STOP:** Do not inspect transcript files or synthesize an answer until the user supplies the anchor.",
        ]
    )


def render_status_anchor_text(payload: dict) -> str:
    report = payload["status_anchor"]
    if _status_anchor_needs_clarification(report):
        return _render_status_anchor_clarification_text(report)
    if _status_anchor_navigation_only(report):
        return _render_status_anchor_navigation_only_text(report)
    summary = report["summary"]
    reads = report.get("next_reads") or []
    source_tiers = report.get("source_tiers") or {}
    follow_through_plan = report.get("follow_through_plan") or {}
    selected = report.get("selected_anchor")
    instruction = report.get("agent_instruction") or {}
    session_activity = report.get("session_activity") or {}
    fail_closed = report.get("fail_closed_guidance") or {}
    clarification = report.get("clarification_request") or {}
    answer_readiness = report.get("answer_readiness") or {}
    parent_main_shortlist = report.get("parent_main_anchor_shortlist") or {}
    status_boundary = report.get("status_as_of_boundary") or {}
    post_anchor_interval = report.get("post_anchor_interval_reconstruction") or {}
    open_first_reads = [read for read in reads if read.get("source_tier") != "diagnostic_optional"]
    answer_status = _status_anchor_answer_status(report, reads=reads, selected=selected)
    if _is_answer_ready_parent_main_status(report):
        return _render_answer_ready_parent_main_status_text(report, answer_status=answer_status)
    lines = [
        "Status Anchor",
        f"Status: {report['status']}",
        f"Answer status: {answer_status}",
        "Evidence contract: raw history/session JSONL is primary; commits, merges, and repo state are secondary only.",
    ]
    if clarification.get("request"):
        lines.append(f"Clarification request: {clarification.get('request')}")
        if clarification.get("candidate_refs_policy"):
            lines.append(f"Clarification boundary: {clarification.get('candidate_refs_policy')}")
    if fail_closed.get("guidance"):
        lines.append(
            "Fail-closed guidance: "
            f"{fail_closed.get('guidance')} "
            f"answer_allowed={fail_closed.get('answer_allowed')}"
        )
    if _status_anchor_parent_main_complete_interval_recovery_required(report):
        lines.append(
            "Complete-interval answer command: "
            f"{_status_anchor_rerun_answer_command(report)}"
        )
        lines.append(
            "Combined-history boundary: combined Codex history.jsonl is not an unrecoverable blocker; "
            "use the canonical parent history row/session linkage or fail closed with this exact command/read."
        )
    if answer_readiness:
        lines.append(
            "Answer readiness: "
            f"status={answer_readiness.get('status')} "
            f"answer_allowed={answer_readiness.get('answer_allowed')} "
            f"selected_anchor_answer_ready={answer_readiness.get('selected_anchor_answer_ready')}"
        )
        facets = answer_readiness.get("required_answer_facets") or []
        if facets:
            lines.append(f"Required answer facets: {', '.join(facets)}")
        missing = answer_readiness.get("missing_required_answer_facets") or []
        if missing:
            lines.append(f"Missing required answer facets: {', '.join(missing)}")
        if answer_readiness.get("stop_condition"):
            lines.append(f"Answer-readiness stop condition: {answer_readiness.get('stop_condition')}")
    if status_boundary:
        lines.append(
            "Status as-of boundary: "
            f"{status_boundary.get('timestamp_utc')} {status_boundary.get('source_ref')} "
            f"audit_run_requested={report.get('routing', {}).get('audit_run_requested')}"
        )
    _append_parent_main_anchor_shortlist_text(lines, parent_main_shortlist)
    _append_status_follow_through_plan_text(lines, follow_through_plan)
    _append_status_post_anchor_interval_text(lines, post_anchor_interval)
    lines.append("Open these source refs first:")
    if selected:
        _append_text_read(lines, selected)
    for read in open_first_reads[:4]:
        if not selected or read.get("source_ref") != selected.get("source_ref"):
            _append_text_read(lines, read)
    if not selected and not open_first_reads:
        lines.append("- (no source refs surfaced)")
    if instruction.get("stop_condition"):
        lines.append(f"Stop condition: {instruction.get('stop_condition')}")
    lines.append("Only continue if: direct anchor/artifact reads fail, conflict, or leave a named status gap.")
    lines.append("Optional diagnostics/adjacent evidence: rejected anchors, broad work products, blind spots, and warnings below.")
    lines.extend([
        f"Route: {report['routing']['route']}",
        f"JSONL-first: {report['routing']['jsonl_first']}",
        f"Raw session trace: {session_activity.get('status')}",
        f"Commit-only answers allowed: {report.get('evidence_contract', {}).get('commit_only_answers_allowed')}",
        (
            "Summary: "
            f"{summary['anchor_candidate_count']} anchors, "
            f"{summary.get('current_window_work_product_count', 0)} current-window work products, "
            f"{summary.get('diagnostic_optional_work_product_count', 0)} diagnostic optional work products, "
            f"{summary['behavior_delta_count']} behavior deltas, "
            f"{summary['non_delta_count']} non-deltas, "
            f"{summary['unresolved_blocker_count']} blockers"
        ),
    ])
    if selected:
        lines.append(
            "Anchor decision: "
            f"context_boundary={selected.get('context_boundary')} "
            f"anchor_scope={selected.get('anchor_scope')} "
            f"anchor_reason={selected.get('anchor_reason')} "
            f"anchor_ref={selected.get('anchor_ref')}"
        )
        lines.append(
            "Selected anchor: "
            f"{selected.get('timestamp_utc')} {selected.get('source_ref')}: "
            f"{selected.get('snippet')}"
        )
        if selected.get("openable_target"):
            lines.append(f"Selected anchor openable target: {selected.get('openable_target')}")
        if selected.get("read_more_command"):
            lines.append(f"Selected anchor read-more: {selected.get('read_more_command')}")
        lines.append(f"Anchor rationale: {selected.get('rationale')}")
    rejected = (report.get("anchor_decision") or {}).get("rejected_anchor_alternatives") or []
    if rejected:
        lines.append("Rejected anchor alternatives:")
        for item in rejected[:5]:
            lines.append(
                "- "
                f"{item.get('source_ref')} "
                f"classification={item.get('classification')} "
                f"reason={item.get('rationale')}: "
                f"{item.get('snippet')}"
            )
    if session_activity:
        activity = session_activity.get("activity_after_anchor") or {}
        lines.append(
            "Raw session activity after anchor: "
            f"records={activity.get('records_after_anchor')} "
            f"conversation_actions={activity.get('conversation_actions')} "
            f"tool_calls={activity.get('tool_calls')} "
            f"tool_outputs={activity.get('tool_outputs')} "
            f"spawned_sessions={activity.get('spawned_session_calls')} "
            f"compactions={activity.get('compacted_summaries')}"
        )
        lines.append(
            "Secondary evidence boundary: commits/merges and current repo state can support the answer only after raw JSONL source refs are inspected."
        )
    lines.append("Answer brief:")
    answer_brief = report.get("answer_brief", [])
    if answer_brief:
        for item in answer_brief:
            lines.append(f"- {item}")
    elif answer_readiness.get("answer_allowed") is False:
        lines.append("- (suppressed until required_answer_facets are proven; missing facets are a stop condition)")
    if source_tiers:
        lines.append("Source tiers:")
        for tier in source_tiers.get("tier_order") or ["required_primary", "required_secondary", "diagnostic_optional"]:
            tier_reads = source_tiers.get(tier) or []
            lines.append(f"{tier}: {len(tier_reads)}")
            for read in tier_reads[:5]:
                _append_text_read(lines, read)
    if report.get("unresolved_blockers"):
        lines.append("Unresolved blockers:")
        for blocker in report["unresolved_blockers"]:
            lines.append(f"- {blocker['blocker']}: {blocker['summary']}")
    if reads:
        lines.append("Next reads:")
        for read in reads[:10]:
            _append_text_read(lines, read)
    for blind in report.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_status_anchor_answer(payload: dict) -> str:
    report = payload["status_anchor"]
    continuation_packet = parent_thread_interval_continuation_packet(report)
    if continuation_packet:
        return "\n".join(
            [
                "NOT ANSWERABLE YET",
                "CANONICAL CONTINUATION PACKET",
                json.dumps(continuation_packet, ensure_ascii=True, sort_keys=True),
                "answer_ready=false",
            ]
        )
    if _status_anchor_needs_clarification(report):
        return _render_status_anchor_clarification_text(report)
    if not _status_anchor_answer_format_ready(report):
        lines = ["NOT ANSWERABLE YET", _status_anchor_canonical_continuation_command(report)]
        freshness = (report.get("answer_readiness") or {}).get("parent_main_anchor_freshness") or {}
        if freshness.get("status") == "stale_selected_anchor":
            newest = freshness.get("newest_candidate") or {}
            lines.append(
                "parent_main_anchor_freshness: "
                "status=stale_selected_anchor "
                "selected_anchor_freshness=false "
                f"newest_ref={newest.get('source_ref') or '(unknown)'} "
                f"newest_timestamp={newest.get('timestamp_utc') or '(unknown)'}"
            )
        lines.append("answer_ready=false")
        return "\n".join(lines)

    selected = report.get("selected_anchor") or {}
    routing = report.get("routing") or {}
    interval = report.get("post_anchor_interval_reconstruction") or {}
    coverage = interval.get("coverage") or {}
    boundary = interval.get("boundary") or {}
    session_activity = report.get("session_activity") or {}
    followups = session_activity.get("session_followups") or []
    joins = interval.get("secondary_evidence_joins") or {}
    entries = interval.get("chronological_activity") or []
    parent_identity = selected.get("source_identifier") or selected.get("source_ref")
    parent_hash = selected.get("session_id_hash") or selected.get("source_identifier")
    lines = [
        "ANSWER READY",
        "answer_ready=true",
        "terminal_answer_and_stop=true",
        (
            "Selected parent history row: "
            f"{selected.get('timestamp_utc')} {selected.get('source_ref')} "
            f"identity={parent_identity} hash={parent_hash}"
        ),
        (
            "Parent-thread boundary: "
            f"scope={routing.get('anchor_scope')} reason={routing.get('anchor_reason')} "
            f"ref={routing.get('anchor_ref')}"
        ),
        (
            "Interval bounds: "
            f"anchor={selected.get('timestamp_utc')} "
            f"boundary={boundary.get('timestamp_utc') or 'now/latest'} "
            f"boundary_ref={boundary.get('source_ref') or '(none)'}"
        ),
        (
            "Coverage: "
            f"status={coverage.get('status')} complete={coverage.get('complete')} "
            f"records_after_anchor={coverage.get('records_after_anchor')} "
            f"entries={interval.get('chronological_entry_count')} "
            f"omitted={coverage.get('omitted_activity_entry_count', 0)}"
        ),
        "Touched session files:",
    ]
    if followups:
        for item in followups[:12]:
            lines.append(
                "- "
                f"role={item.get('role')} session_hash={item.get('session_id_hash')} "
                f"records_after_anchor={item.get('records_after_anchor')} "
                f"file={item.get('session_file')}"
            )
    else:
        lines.append("- (none surfaced)")
    lines.append("Timeline:")
    if entries:
        timeline_entries = _status_anchor_answer_timeline_entries(entries, limit=16)
        for entry in timeline_entries:
            if entry.get("activity_kind") == "summary_gap":
                lines.append(
                    "- "
                    f"... {entry.get('omitted_count')} compact timeline entries summarized between "
                    f"{entry.get('after_source_ref')} and {entry.get('before_source_ref')}"
                )
                continue
            lines.append(
                "- "
                f"{entry.get('timestamp_utc')} {entry.get('source_ref')} "
                f"{entry.get('activity_kind')}: {entry.get('snippet')}"
            )
    else:
        lines.append("- (no chronological activity refs surfaced)")
    lines.append("Secondary joins (capped):")
    for key in ("tasks", "reports", "reviews", "git_commits"):
        items = joins.get(key) or []
        lines.append(f"- {key}: {len(items)}")
        for item in items[:3]:
            lines.append(
                "  - "
                f"{item.get('source_ref')} {item.get('evidence_timestamp_utc') or item.get('committed_at')}: "
                f"{item.get('snippet')}"
            )
    return "\n".join(lines)


def _status_anchor_answer_timeline_entries(entries: list[dict], *, limit: int) -> list[dict]:
    if len(entries) <= limit:
        return entries
    head_count = max(1, limit // 2)
    tail_count = max(1, limit - head_count - 1)
    head = entries[:head_count]
    tail = entries[-tail_count:]
    gap = {
        "activity_kind": "summary_gap",
        "omitted_count": max(0, len(entries) - len(head) - len(tail)),
        "after_source_ref": head[-1].get("source_ref") if head else None,
        "before_source_ref": tail[0].get("source_ref") if tail else None,
    }
    return head + [gap] + tail


def _status_anchor_answer_format_ready(report: dict) -> bool:
    readiness = report.get("answer_readiness") or {}
    if readiness.get("answer_from_report_allowed") is False:
        return False
    interval = report.get("post_anchor_interval_reconstruction") or {}
    coverage = interval.get("coverage") or {}
    selected = report.get("selected_anchor") or {}
    routing = report.get("routing") or {}
    command_options = report.get("command_options") or {}
    if not selected:
        return False
    if readiness.get("answer_allowed") is not True:
        return False
    if coverage.get("complete") is not True:
        return False
    if command_options.get("parent_thread") and routing.get("anchor_scope") != "parent_thread":
        return False
    return True


def _status_anchor_canonical_continuation_command(report: dict) -> str:
    freshness = ((report.get("answer_readiness") or {}).get("parent_main_anchor_freshness") or {})
    if freshness.get("current") is False and freshness.get("canonical_continuation_command"):
        return str(freshness["canonical_continuation_command"])
    if _status_anchor_parent_main_complete_interval_recovery_required(report):
        return _status_anchor_rerun_answer_command(report)
    source_tiers = report.get("source_tiers") or {}
    for read in source_tiers.get("required_primary") or []:
        if read.get("read_more_command"):
            return str(read["read_more_command"])
    generated_contract = report.get("generated_report_contract") or {}
    if generated_contract.get("canonical_follow_through_path"):
        return str(generated_contract["canonical_follow_through_path"])
    interval = report.get("post_anchor_interval_reconstruction") or {}
    guidance = interval.get("next_read_guidance") or {}
    commands = [str(command) for command in guidance.get("commands") or [] if command]
    if commands:
        return commands[0]
    for item in interval.get("omitted_ranges") or []:
        if item.get("read_next_command"):
            return str(item["read_next_command"])
    selected = report.get("selected_anchor") or {}
    if selected.get("read_more_command"):
        return str(selected["read_more_command"])
    return _status_anchor_rerun_answer_command(report)


def _status_anchor_rerun_answer_command(report: dict) -> str:
    command_options = report.get("command_options") or {}
    parent_thread = bool(command_options.get("parent_thread")) or _status_anchor_parent_main_complete_interval_recovery_required(
        report
    )
    parts = [
        "jsonl-history-search",
        "what-happened-since",
        str(report.get("query") or "what happened since my last message?"),
        "--cwd",
        str(command_options.get("cwd") or "."),
    ]
    if command_options.get("codex_home"):
        parts.extend(["--codex-home", str(command_options["codex_home"])])
    if parent_thread:
        parts.append("--parent-thread")
    if command_options.get("anchor_ref"):
        parts.extend(["--anchor-ref", str(command_options["anchor_ref"])])
    parts.extend(["--complete-interval", "--format", "answer"])
    if command_options.get("max_matches"):
        parts.extend(["--max-matches", str(command_options["max_matches"])])
    return " ".join(shlex.quote(part) for part in parts)


def _status_anchor_parent_main_complete_interval_recovery_required(report: dict) -> bool:
    command_options = report.get("command_options") or {}
    routing = report.get("routing") or {}
    selected = report.get("selected_anchor") or {}
    if command_options.get("complete_interval") and command_options.get("parent_thread"):
        return False
    query = " ".join(str(report.get("query") or "").casefold().replace("?", " ").replace("!", " ").split())
    parent_main = (
        bool(command_options.get("parent_thread"))
        or (routing.get("anchor_scope") == "parent_thread" and not selected)
        or any(
            marker in query
            for marker in (
                "parent thread",
                "main thread",
                "parent/main",
                "main/parent",
            )
        )
    )
    if not parent_main:
        return False
    since_or_after = any(
        marker in query
        for marker in (
            "what happened since",
            "what has happened since",
            "what happened after",
            "what has happened after",
            "since my last",
            "since my latest",
            "after my last",
            "after my latest",
        )
    )
    message_anchor = any(
        marker in query
        for marker in (
            "last message",
            "latest message",
            "last user message",
            "latest user message",
            "last actual message",
            "latest actual message",
            "last substantive instruction",
            "latest substantive instruction",
            "last substantive message",
            "last real instruction",
        )
    )
    return since_or_after and message_anchor


def _status_anchor_answer_status(report: dict, *, reads: list[dict], selected: dict | None) -> str:
    fail_closed = report.get("fail_closed_guidance") or {}
    if fail_closed.get("answer_allowed") is False:
        return "not-ready/needs-more-proof"
    return _source_use_answer_status(report.get("status"), has_required_reads=bool(reads), has_refs=bool(reads or selected))


def _is_answer_ready_parent_main_status(report: dict) -> bool:
    shortlist = report.get("parent_main_anchor_shortlist") or {}
    readiness = report.get("answer_readiness") or {}
    return (
        shortlist.get("status") == "selected"
        and shortlist.get("answer_allowed") is True
        and readiness.get("answer_allowed") is True
    )


def _answer_ready_parent_main_reads(report: dict) -> tuple[dict, list[dict], list[dict]]:
    selected = report.get("selected_anchor") or {}
    selected_ref = selected.get("source_ref")
    plan = report.get("follow_through_plan") or {}
    primary = list(plan.get("open_first") or [])
    canonical = next((read for read in primary if read.get("source_ref") == selected_ref), selected)
    raw_activity = [read for read in primary if read.get("source_ref") != selected_ref]

    secondary = []
    seen = set()
    for group in plan.get("then_confirm") or []:
        for read in group.get("refs") or []:
            ref = str(read.get("source_ref") or read.get("openable_target") or "")
            if not ref or ref in seen:
                continue
            secondary.append(read)
            seen.add(ref)
            if len(secondary) >= 8:
                return canonical, raw_activity, secondary
    return canonical, raw_activity, secondary


def _bounded_commit_confirmation_command(reads: list[dict]) -> str | None:
    commit_ids = []
    for read in reads:
        source_ref = str(read.get("source_ref") or "")
        if not source_ref.startswith("git_commit:"):
            return None
        commit_id = source_ref.split(":", 1)[1].strip()
        if not commit_id:
            return None
        commit_ids.append(shlex.quote(commit_id))
    if not commit_ids:
        return None
    refs = " ".join(commit_ids)
    return (
        f"for ref in {refs}; do "
        "git show --no-patch --format='%H%n%aI%n%s%n%b' \"$ref\"; "
        "git show --stat --oneline --format='' \"$ref\"; "
        "done"
    )


def _render_answer_ready_parent_main_status_text(report: dict, *, answer_status: str) -> str:
    selected = report.get("selected_anchor") or {}
    readiness = report.get("answer_readiness") or {}
    activity = (report.get("session_activity") or {}).get("activity_after_anchor") or {}
    post_anchor_interval = report.get("post_anchor_interval_reconstruction") or {}
    canonical, raw_activity, secondary = _answer_ready_parent_main_reads(report)
    activity_location = selected.get("session_activity_location") or {}
    required_facets = readiness.get("required_answer_facets") or []
    lines = [
        "Status Anchor",
        f"Status: {report.get('status')}",
        f"Answer status: {answer_status}",
        "Answer-ready parent/main path: run only these bounded reads, in order.",
        "1. Read the selected canonical anchor:",
        f"- Authority: {selected.get('source_ref')}",
    ]
    _append_text_read(lines, canonical)
    if activity_location.get("source_ref"):
        lines.append(
            "- Activity boundary: "
            f"{activity_location.get('source_ref')} (required session reads start strictly after this logical row)."
        )
    lines.append("2. Read the required raw post-anchor session windows:")
    if raw_activity:
        for read in raw_activity:
            _append_text_read(lines, read)
    else:
        lines.append("- No additional raw session window is required.")
    lines.append(
        "- Trace coverage: "
        f"records={activity.get('records_after_anchor')} "
        f"tool_calls={activity.get('tool_calls')} "
        f"spawned_sessions={activity.get('spawned_session_calls')}"
    )
    _append_status_post_anchor_interval_text(lines, post_anchor_interval)
    lines.append("3. Run one bounded secondary commit/status confirmation batch if listed:")
    if secondary:
        lines.append("- Exact refs: " + ", ".join(str(read.get("source_ref")) for read in secondary))
        batch_command = _bounded_commit_confirmation_command(secondary)
        if batch_command:
            lines.append(f"  command: {batch_command}")
        else:
            for read in secondary:
                _append_text_read(lines, read)
    else:
        lines.append("- None required; do not add a repo, task, state, or Git-history scan.")
    lines.extend(
        [
            "4. Answer the user and stop:",
            "- After the reads above succeed without contradiction, answer now and stop.",
            (
                "- Continue only when a named required facet is still missing or contradictory: "
                + ", ".join(required_facets)
                + ". Name that facet before one targeted follow-up read."
            ),
            (
                "- Do not run broad rg over sessions, state files, old task families, reports, or Git history; "
                "do not open rejected, diagnostic, or optional refs after the required reads are complete."
            ),
        ]
    )
    return "\n".join(lines)


def _append_parent_main_anchor_shortlist_text(lines: list[str], shortlist: dict) -> None:
    if not shortlist or shortlist.get("status") == "not_applicable":
        return
    lines.append("Parent/main anchor shortlist:")
    lines.append(
        "Shortlist status: "
        f"{shortlist.get('status')} "
        f"answer_allowed={shortlist.get('answer_allowed')} "
        f"selected_anchor={shortlist.get('selected_anchor')}"
    )
    lines.append(
        "Shortlist candidates: "
        f"{shortlist.get('candidate_count', 0)} "
        f"high_confidence={shortlist.get('canonical_high_confidence_count', 0)}"
    )
    if shortlist.get("stop_condition"):
        lines.append(f"Shortlist stop_condition: {shortlist.get('stop_condition')}")
    for candidate in (shortlist.get("candidates") or [])[:6]:
        lines.append(
            "- "
            f"{candidate.get('source_ref')} "
            f"scope={candidate.get('source_scope')} "
            f"kind={candidate.get('source_kind')} "
            f"timestamp={candidate.get('timestamp_utc')} "
            f"classification={candidate.get('classification')} "
            f"decision={candidate.get('decision')} "
            f"confidence={candidate.get('confidence_label')} "
            f"ambiguity={candidate.get('ambiguity_label')}: "
            f"{candidate.get('rationale')}"
        )
        if candidate.get("read_more_command"):
            lines.append(f"  read-more: {candidate.get('read_more_command')}")
    recovery = shortlist.get("recovery") or {}
    if recovery.get("rerun_instruction"):
        lines.append(f"Shortlist recovery: {recovery.get('rerun_instruction')}")


def render_status_anchor_markdown(payload: dict) -> str:
    report = payload["status_anchor"]
    if _status_anchor_needs_clarification(report):
        return _render_status_anchor_clarification_markdown(report)
    if _status_anchor_navigation_only(report):
        return _render_status_anchor_navigation_only_markdown(report)
    summary = report["summary"]
    reads = report.get("next_reads") or []
    source_tiers = report.get("source_tiers") or {}
    follow_through_plan = report.get("follow_through_plan") or {}
    selected = report.get("selected_anchor")
    instruction = report.get("agent_instruction") or {}
    session_activity = report.get("session_activity") or {}
    fail_closed = report.get("fail_closed_guidance") or {}
    clarification = report.get("clarification_request") or {}
    answer_readiness = report.get("answer_readiness") or {}
    parent_main_shortlist = report.get("parent_main_anchor_shortlist") or {}
    status_boundary = report.get("status_as_of_boundary") or {}
    post_anchor_interval = report.get("post_anchor_interval_reconstruction") or {}
    open_first_reads = [read for read in reads if read.get("source_tier") != "diagnostic_optional"]
    answer_status = _status_anchor_answer_status(report, reads=reads, selected=selected)
    if _is_answer_ready_parent_main_status(report):
        return _render_answer_ready_parent_main_status_markdown(report, answer_status=answer_status)
    lines = [
        "# Status Anchor",
        "",
        f"**Status:** {report['status']}",
        f"**Answer status:** `{answer_status}`",
        "**Evidence contract:** raw history/session JSONL is primary; commits, merges, and repo state are secondary only.",
        "",
    ]
    if clarification.get("request"):
        lines.extend(
            [
                "## Clarification Request",
                "",
                f"- {clarification.get('request')}",
            ]
        )
        if clarification.get("candidate_refs_policy"):
            lines.append(f"- Boundary: {clarification.get('candidate_refs_policy')}")
        lines.append("")
    if fail_closed.get("guidance"):
        lines.extend(["## Fail-Closed Guidance", "", f"- {fail_closed.get('guidance')}"])
        lines.append(f"- Answer allowed: `{fail_closed.get('answer_allowed')}`")
        if _status_anchor_parent_main_complete_interval_recovery_required(report):
            lines.append(f"- Complete-interval answer command: `{_status_anchor_rerun_answer_command(report)}`")
            lines.append(
                "- Combined-history boundary: combined Codex `history.jsonl` is not an unrecoverable blocker; "
                "use the canonical parent history row/session linkage or fail closed with this exact command/read."
            )
        lines.append("")
    if answer_readiness:
        lines.extend(
            [
                "## Answer Readiness",
                "",
                f"- Status: `{answer_readiness.get('status')}`",
                f"- Answer allowed: `{answer_readiness.get('answer_allowed')}`",
                f"- Selected anchor answer-ready: `{answer_readiness.get('selected_anchor_answer_ready')}`",
            ]
        )
        facets = answer_readiness.get("required_answer_facets") or []
        if facets:
            lines.append(f"- Required answer facets: `{', '.join(facets)}`")
        missing = answer_readiness.get("missing_required_answer_facets") or []
        if missing:
            lines.append(f"- Missing required answer facets: `{', '.join(missing)}`")
        if answer_readiness.get("stop_condition"):
            lines.append(f"- Stop condition: {answer_readiness.get('stop_condition')}")
        lines.append("")
    if status_boundary:
        lines.extend(
            [
                "## Status As-Of Boundary",
                "",
                (
                    f"- `{status_boundary.get('timestamp_utc')}` `{status_boundary.get('source_ref')}`; "
                    f"audit_run_requested=`{report.get('routing', {}).get('audit_run_requested')}`"
                ),
                "",
            ]
        )
    _append_parent_main_anchor_shortlist_markdown(lines, parent_main_shortlist)
    lines.extend(["## Follow-Through Plan", ""])
    _append_status_follow_through_plan_markdown(lines, follow_through_plan)
    lines.extend(["", "## Post-Anchor Interval Reconstruction", ""])
    _append_status_post_anchor_interval_markdown(lines, post_anchor_interval)
    lines.extend([
        "",
        "## Open These Source Refs First",
        "",
    ])
    if selected:
        _append_markdown_read(lines, selected)
    for read in open_first_reads[:4]:
        if not selected or read.get("source_ref") != selected.get("source_ref"):
            _append_markdown_read(lines, read)
    if not selected and not open_first_reads:
        lines.append("- No source refs surfaced.")
    lines.extend(["", "## Stop / Continue", ""])
    if instruction.get("stop_condition"):
        lines.append(f"- Stop condition: {instruction.get('stop_condition')}")
    lines.extend([
        "- Only continue if: direct anchor/artifact reads fail, conflict, or leave a named status gap.",
        "",
        "## Optional Diagnostics / Adjacent Evidence",
        "",
        f"**Route:** `{report['routing']['route']}`",
        f"**JSONL-first:** `{report['routing']['jsonl_first']}`",
        f"**Raw session trace:** `{session_activity.get('status')}`",
        f"**Commit-only answers allowed:** `{report.get('evidence_contract', {}).get('commit_only_answers_allowed')}`",
        (
            f"**Summary:** {summary['anchor_candidate_count']} anchors, "
            f"{summary.get('current_window_work_product_count', 0)} current-window work products, "
            f"{summary.get('diagnostic_optional_work_product_count', 0)} diagnostic optional work products, "
            f"{summary['behavior_delta_count']} behavior deltas, "
            f"{summary['non_delta_count']} non-deltas, "
            f"{summary['unresolved_blocker_count']} blockers"
        ),
        "",
    ])
    if selected:
        lines.extend(
            [
                "## Selected Anchor",
                "",
                (
                    f"- Decision: `context_boundary={selected.get('context_boundary')}` "
                    f"`anchor_scope={selected.get('anchor_scope')}` "
                    f"`anchor_reason={selected.get('anchor_reason')}` "
                    f"`anchor_ref={selected.get('anchor_ref')}`"
                ),
                (
                    f"- `{selected.get('timestamp_utc')}` `{selected.get('source_ref')}`: "
                    f"{selected.get('snippet')}"
                ),
            ]
        )
        if selected.get("openable_target"):
            lines.append(f"- Openable target: `{selected.get('openable_target')}`")
        if selected.get("read_more_command"):
            lines.append(f"- Read more: `{selected.get('read_more_command')}`")
        lines.extend([f"- Rationale: {selected.get('rationale')}", ""])
    rejected = (report.get("anchor_decision") or {}).get("rejected_anchor_alternatives") or []
    if rejected:
        lines.extend(["## Rejected Anchor Alternatives", ""])
        for item in rejected[:5]:
            lines.append(
                f"- `{item.get('source_ref')}` `{item.get('classification')}`: "
                f"{item.get('rationale')} - {item.get('snippet')}"
            )
        lines.append("")
    if session_activity:
        activity = session_activity.get("activity_after_anchor") or {}
        lines.extend(
            [
                "## Raw Session Activity After Anchor",
                "",
                (
                    f"- Records: `{activity.get('records_after_anchor')}`; "
                    f"conversation actions: `{activity.get('conversation_actions')}`; "
                    f"tool calls: `{activity.get('tool_calls')}`; "
                    f"tool outputs: `{activity.get('tool_outputs')}`; "
                    f"spawned sessions: `{activity.get('spawned_session_calls')}`; "
                    f"compactions: `{activity.get('compacted_summaries')}`"
                ),
                "- Secondary evidence boundary: commits/merges and current repo state can support the answer only after raw JSONL source refs are inspected.",
                "",
            ]
        )
    lines.extend(["## Answer Brief", ""])
    answer_brief = report.get("answer_brief", [])
    if answer_brief:
        lines.extend(f"- {item}" for item in answer_brief)
    elif answer_readiness.get("answer_allowed") is False:
        lines.append("- Suppressed until `required_answer_facets` are proven; missing facets are a stop condition.")
    lines.append("")
    if source_tiers:
        lines.extend(["## Source Tiers", ""])
        for tier in source_tiers.get("tier_order") or ["required_primary", "required_secondary", "diagnostic_optional"]:
            tier_reads = source_tiers.get(tier) or []
            lines.extend([f"### `{tier}`", ""])
            if not tier_reads:
                lines.append("- No refs surfaced.")
            for read in tier_reads[:5]:
                _append_markdown_read(lines, read)
            lines.append("")
    if reads:
        lines.extend(["## Next Reads", ""])
        for read in reads[:10]:
            _append_markdown_read(lines, read)
        lines.append("")
    if report.get("blind_spots"):
        lines.extend(["## Blind Spots", ""])
        lines.extend(f"- {blind}" for blind in report["blind_spots"])
    return "\n".join(lines)


def _render_answer_ready_parent_main_status_markdown(report: dict, *, answer_status: str) -> str:
    selected = report.get("selected_anchor") or {}
    readiness = report.get("answer_readiness") or {}
    activity = (report.get("session_activity") or {}).get("activity_after_anchor") or {}
    post_anchor_interval = report.get("post_anchor_interval_reconstruction") or {}
    canonical, raw_activity, secondary = _answer_ready_parent_main_reads(report)
    activity_location = selected.get("session_activity_location") or {}
    required_facets = readiness.get("required_answer_facets") or []
    lines = [
        "# Status Anchor",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Answer status: `{answer_status}`",
        "- Answer-ready parent/main path: run only these bounded reads, in order.",
        "",
        "## 1. Selected Canonical Anchor",
        "",
        f"- Authority: `{selected.get('source_ref')}`",
    ]
    _append_markdown_read(lines, canonical)
    if activity_location.get("source_ref"):
        lines.append(
            f"- Activity boundary: `{activity_location.get('source_ref')}`; required session reads start "
            "strictly after this logical row."
        )
    lines.extend(["", "## 2. Required Raw Session Reads", ""])
    if raw_activity:
        for read in raw_activity:
            _append_markdown_read(lines, read)
    else:
        lines.append("- No additional raw session window is required.")
    lines.append(
        "- Trace coverage: "
        f"records=`{activity.get('records_after_anchor')}`, "
        f"tool calls=`{activity.get('tool_calls')}`, "
        f"spawned sessions=`{activity.get('spawned_session_calls')}`."
    )
    lines.extend(["", "## Post-Anchor Interval Reconstruction", ""])
    _append_status_post_anchor_interval_markdown(lines, post_anchor_interval)
    lines.extend(["", "## 3. Bounded Secondary Confirmation", ""])
    if secondary:
        lines.append("- Exact refs: " + ", ".join(f"`{read.get('source_ref')}`" for read in secondary))
        batch_command = _bounded_commit_confirmation_command(secondary)
        if batch_command:
            lines.append(f"- Command: `{batch_command}`")
        else:
            for read in secondary:
                _append_markdown_read(lines, read)
    else:
        lines.append("- None required; do not add a repo, task, state, or Git-history scan.")
    lines.extend(
        [
            "",
            "## 4. Answer And Stop",
            "",
            "- After the reads above succeed without contradiction, answer now and stop.",
            (
                "- Continue only when a named required facet is still missing or contradictory: `"
                + ", ".join(required_facets)
                + "`. Name that facet before one targeted follow-up read."
            ),
            (
                "- Do not run broad `rg` over sessions, state files, old task families, reports, or Git history; "
                "do not open rejected, diagnostic, or optional refs after the required reads are complete."
            ),
        ]
    )
    return "\n".join(lines)


def _append_parent_main_anchor_shortlist_markdown(lines: list[str], shortlist: dict) -> None:
    if not shortlist or shortlist.get("status") == "not_applicable":
        return
    lines.extend(
        [
            "## Parent/Main Anchor Shortlist",
            "",
            f"- Status: `{shortlist.get('status')}`",
            f"- Answer allowed: `{shortlist.get('answer_allowed')}`",
            f"- Selected anchor: `{shortlist.get('selected_anchor')}`",
            (
                f"- Candidates: `{shortlist.get('candidate_count', 0)}`; "
                f"high confidence: `{shortlist.get('canonical_high_confidence_count', 0)}`"
            ),
        ]
    )
    if shortlist.get("stop_condition"):
        lines.append(f"- Stop condition: {shortlist.get('stop_condition')}")
    lines.append("")
    for candidate in (shortlist.get("candidates") or [])[:6]:
        lines.append(
            f"- `{candidate.get('source_ref')}` "
            f"`scope={candidate.get('source_scope')}` "
            f"`kind={candidate.get('source_kind')}` "
            f"`timestamp={candidate.get('timestamp_utc')}` "
            f"`classification={candidate.get('classification')}` "
            f"`decision={candidate.get('decision')}` "
            f"`confidence={candidate.get('confidence_label')}` "
            f"`ambiguity={candidate.get('ambiguity_label')}`: "
            f"{candidate.get('rationale')}"
        )
        if candidate.get("read_more_command"):
            lines.append(f"  - Read more: `{candidate.get('read_more_command')}`")
    recovery = shortlist.get("recovery") or {}
    if recovery.get("rerun_instruction"):
        lines.append(f"- Recovery: {recovery.get('rerun_instruction')}")
    lines.append("")


def render_generated_metadata_discovery_text(payload: dict) -> str:
    report = payload["generated_metadata_discovery"]
    summary = report["summary"]
    lines = [
        "Generated Metadata Discovery",
        f"Status: {report['status']}",
        f"Mode: {report['mode']}",
        f"Candidates: {summary['candidate_count']}",
        "Promotion: report-only prototype; no default search, sidecar, watcher, ranking, or token-efficiency claim.",
    ]
    for candidate in report["candidates"][:10]:
        evidence = candidate["evidence"]
        lines.append(
            f"- {candidate['kind']} {candidate['label']} "
            f"({candidate['confidence']}, entries={evidence['entry_count']}, raw_values={evidence['raw_value_count']})"
        )
    return "\n".join(lines)


def render_agent_loop_cost_contract_text(payload: dict) -> str:
    report = payload["agent_loop_cost_contract"]
    lanes = ", ".join(lane["lane_id"] for lane in report["strategy_lanes"])
    return "\n".join(
        [
            "Agent Loop Route/Counter Contract",
            f"Status: {report['status']}",
            f"Cases: {report['held_out_case_count']}",
            f"Lanes: {lanes}",
            f"Prompt materializations: {report['prompt_materialization']['materialization_count']}",
            f"Contract hash: {report['prompt_hash_stability']['contract_hash_sha256']}",
            "No-send: live_model_sends_performed=false; adjudication_performed=false",
        ]
    )


def render_agent_loop_cost_contract_markdown(payload: dict) -> str:
    report = payload["agent_loop_cost_contract"]
    lines = [
        "# Agent Loop Route/Counter Contract",
        "",
        f"**Status:** {report['status']}",
        f"**Cases:** {report['held_out_case_count']}",
        f"**Prompt materializations:** {report['prompt_materialization']['materialization_count']}",
        f"**Contract hash:** `{report['prompt_hash_stability']['contract_hash_sha256']}`",
        "",
        "## Lanes",
    ]
    for lane in report["strategy_lanes"]:
        package = "live JSONL required" if lane["jsonl_package_available"] else "JSONL unavailable"
        lines.append(f"- `{lane['lane_id']}`: {lane['strategy_family']}; {package}")
    lines.extend(
        [
            "",
            "## Counter Buckets",
            "- `local_tool_work`",
            "- `model_facing_context_input`",
            "- `full_agent_loop_cost`",
            "",
            "Missing counters are literal `unavailable`; this artifact performs no live model sends and no adjudication.",
        ]
    )
    return "\n".join(lines)


def _append_status_follow_through_plan_text(lines: list[str], plan: dict) -> None:
    if not plan:
        return
    lines.append("Follow-through plan:")
    lines.append(f"Plan status: {plan.get('status')}")
    open_first = plan.get("open_first") or []
    lines.append("Plan open_first:")
    if open_first:
        for read in open_first[:4]:
            _append_text_read(lines, read)
    else:
        lines.append("- (no required primary refs surfaced)")
    then_confirm = plan.get("then_confirm") or []
    lines.append("Plan then_confirm:")
    if then_confirm:
        for group in then_confirm:
            label = group.get("task_id") or group.get("label") or "secondary_support"
            lines.append(f"- {label}: {group.get('purpose')}")
            for read in (group.get("refs") or [])[:4]:
                _append_text_read(lines, read)
    else:
        lines.append("- (no required secondary refs surfaced)")
    diagnostics = plan.get("do_not_open_by_default") or []
    lines.append("Plan do_not_open_by_default:")
    if diagnostics:
        for read in diagnostics[:5]:
            _append_text_read(lines, read)
    else:
        lines.append("- (no diagnostic refs surfaced)")
    facets = plan.get("expected_status_answer_facets") or []
    if facets:
        lines.append(f"Expected status-answer facets: {', '.join(facets)}")
    required_facets = plan.get("required_answer_facets") or []
    if required_facets:
        lines.append(f"Required answer facets: {', '.join(required_facets)}")
    if plan.get("required_facets_stop_condition"):
        lines.append(f"Required-facets stop_condition: {plan.get('required_facets_stop_condition')}")
    if plan.get("stop_condition"):
        lines.append(f"Plan stop_condition: {plan.get('stop_condition')}")
    if plan.get("read_more_boundary_warning"):
        lines.append(f"Read-more boundary warning: {plan.get('read_more_boundary_warning')}")
    if plan.get("rerun_policy"):
        lines.append(f"Rerun policy: {plan.get('rerun_policy')}")


def _append_status_follow_through_plan_markdown(lines: list[str], plan: dict) -> None:
    if not plan:
        lines.append("- No follow-through plan surfaced.")
        return
    lines.append(f"- Plan status: `{plan.get('status')}`")
    lines.extend(["", "### `open_first`", ""])
    open_first = plan.get("open_first") or []
    if open_first:
        for read in open_first[:4]:
            _append_markdown_read(lines, read)
    else:
        lines.append("- No required primary refs surfaced.")
    lines.extend(["", "### `then_confirm`", ""])
    then_confirm = plan.get("then_confirm") or []
    if then_confirm:
        for group in then_confirm:
            label = group.get("task_id") or group.get("label") or "secondary_support"
            lines.append(f"- `{label}`: {group.get('purpose')}")
            for read in (group.get("refs") or [])[:4]:
                _append_markdown_read(lines, read)
    else:
        lines.append("- No required secondary refs surfaced.")
    lines.extend(["", "### `do_not_open_by_default`", ""])
    diagnostics = plan.get("do_not_open_by_default") or []
    if diagnostics:
        for read in diagnostics[:5]:
            _append_markdown_read(lines, read)
    else:
        lines.append("- No diagnostic refs surfaced.")
    facets = plan.get("expected_status_answer_facets") or []
    if facets:
        lines.extend(["", f"- Expected status-answer facets: `{', '.join(facets)}`"])
    required_facets = plan.get("required_answer_facets") or []
    if required_facets:
        lines.append(f"- Required answer facets: `{', '.join(required_facets)}`")
    if plan.get("required_facets_stop_condition"):
        lines.append(f"- Required-facets stop condition: {plan.get('required_facets_stop_condition')}")
    if plan.get("stop_condition"):
        lines.append(f"- Stop condition: {plan.get('stop_condition')}")
    if plan.get("read_more_boundary_warning"):
        lines.append(f"- Read-more boundary warning: {plan.get('read_more_boundary_warning')}")
    if plan.get("rerun_policy"):
        lines.append(f"- Rerun policy: {plan.get('rerun_policy')}")


def _append_status_post_anchor_interval_text(lines: list[str], interval: dict) -> None:
    if not interval:
        return
    coverage = interval.get("coverage") or {}
    anchor = interval.get("anchor") or {}
    boundary = interval.get("boundary") or {}
    lines.append("Post-anchor interval reconstruction:")
    lines.append(
        "Interval coverage: "
        f"status={coverage.get('status')} complete={coverage.get('complete')} "
        f"records_after_anchor={coverage.get('records_after_anchor')} "
        f"entries={interval.get('chronological_entry_count')} "
        f"omitted={coverage.get('omitted_activity_entry_count', 0)}"
    )
    if anchor:
        lines.append(f"Interval anchor: {anchor.get('timestamp_utc')} {anchor.get('source_ref')}")
    if boundary:
        lines.append(
            "Interval boundary: "
            f"{boundary.get('kind')} {boundary.get('timestamp_utc')} {boundary.get('source_ref')}"
        )
    entries = interval.get("chronological_activity") or []
    lines.append("Interval chronological activity:")
    if entries:
        for entry in entries[:8]:
            lines.append(
                "- "
                f"{entry.get('timestamp_utc')} {entry.get('source_ref')} "
                f"{entry.get('activity_kind')}: {entry.get('snippet')}"
            )
            if entry.get("read_more_command"):
                lines.append(f"  read-more: {entry.get('read_more_command')}")
    else:
        lines.append("- (no chronological activity refs surfaced)")
    spawned_refs = interval.get("spawned_session_launch_refs") or []
    if spawned_refs:
        lines.append("Interval spawned/worker/reviewer launch refs: " + ", ".join(str(ref) for ref in spawned_refs[:8]))
    joins = interval.get("secondary_evidence_joins") or {}
    join_counts = {
        key: len(joins.get(key) or [])
        for key in ("tasks", "reports", "reviews", "git_commits")
    }
    lines.append(
        "Interval secondary joins: "
        f"tasks={join_counts['tasks']} reports={join_counts['reports']} "
        f"reviews={join_counts['reviews']} git_commits={join_counts['git_commits']}"
    )
    omitted_ranges = interval.get("omitted_ranges") or []
    if omitted_ranges:
        lines.append("Interval omitted ranges:")
        for item in omitted_ranges:
            lines.append(
                "- "
                f"after={item.get('after_ref')} last={item.get('last_ref')} "
                f"omitted={item.get('omitted_count')}: {item.get('guidance')}"
            )
            if item.get("read_next_command"):
                lines.append(f"  next-read: {item.get('read_next_command')}")
    guidance = interval.get("next_read_guidance") or {}
    if guidance:
        lines.append(
            f"Interval next-read guidance: status={guidance.get('status')} policy={guidance.get('policy')}"
        )


def _append_status_post_anchor_interval_markdown(lines: list[str], interval: dict) -> None:
    if not interval:
        lines.append("- No post-anchor interval reconstruction surfaced.")
        return
    coverage = interval.get("coverage") or {}
    anchor = interval.get("anchor") or {}
    boundary = interval.get("boundary") or {}
    lines.append(
        f"- Coverage: `status={coverage.get('status')}` `complete={coverage.get('complete')}` "
        f"`records_after_anchor={coverage.get('records_after_anchor')}` "
        f"`entries={interval.get('chronological_entry_count')}` "
        f"`omitted={coverage.get('omitted_activity_entry_count', 0)}`"
    )
    if anchor:
        lines.append(f"- Anchor: `{anchor.get('timestamp_utc')}` `{anchor.get('source_ref')}`")
    if boundary:
        lines.append(
            f"- Boundary: `{boundary.get('kind')}` `{boundary.get('timestamp_utc')}` `{boundary.get('source_ref')}`"
        )
    entries = interval.get("chronological_activity") or []
    lines.extend(["", "### Chronological Activity", ""])
    if entries:
        for entry in entries[:8]:
            lines.append(
                f"- `{entry.get('timestamp_utc')}` `{entry.get('source_ref')}` "
                f"`{entry.get('activity_kind')}`: {entry.get('snippet')}"
            )
            if entry.get("read_more_command"):
                lines.append(f"  - Read more: `{entry.get('read_more_command')}`")
    else:
        lines.append("- No chronological activity refs surfaced.")
    spawned_refs = interval.get("spawned_session_launch_refs") or []
    if spawned_refs:
        lines.append("")
        lines.append("- Spawned/worker/reviewer launch refs: " + ", ".join(f"`{ref}`" for ref in spawned_refs[:8]))
    joins = interval.get("secondary_evidence_joins") or {}
    join_counts = {
        key: len(joins.get(key) or [])
        for key in ("tasks", "reports", "reviews", "git_commits")
    }
    lines.append("")
    lines.append(
        f"- Secondary joins: `tasks={join_counts['tasks']}` `reports={join_counts['reports']}` "
        f"`reviews={join_counts['reviews']}` `git_commits={join_counts['git_commits']}`"
    )
    omitted_ranges = interval.get("omitted_ranges") or []
    if omitted_ranges:
        lines.extend(["", "### Omitted Ranges", ""])
        for item in omitted_ranges:
            lines.append(
                f"- `after={item.get('after_ref')}` `last={item.get('last_ref')}` "
                f"`omitted={item.get('omitted_count')}`: {item.get('guidance')}"
            )
            if item.get("read_next_command"):
                lines.append(f"  - Next read: `{item.get('read_next_command')}`")
    guidance = interval.get("next_read_guidance") or {}
    if guidance:
        lines.append("")
        lines.append(f"- Next-read guidance: `status={guidance.get('status')}` {guidance.get('policy')}")


def render_retained_trace_counters_text(payload: dict) -> str:
    report = payload["retained_trace_counters"]
    buckets = report["counter_buckets"]
    local = buckets["local_tool_work"]
    model = buckets["model_facing_context_input"]
    loop = buckets["full_agent_loop_cost"]
    return "\n".join(
        [
            f"Row: {report['scope']['row_id']}",
            f"Tool calls: {local['tool_calls']['value']}",
            f"Command count: {local['command_count']['value']}",
            f"Codex exec events: {loop['codex_exec_event_count']['value']}",
            f"Prompt bytes: {model['prompt_input_bytes']['value']}",
            f"Tool stdout bytes shown: {model['tool_stdout_bytes_shown']['value']}",
            f"Final answer bytes: {loop['final_answer_bytes']['value']}",
            f"Model input tokens: {loop['model_input_tokens']['value']}",
            f"Runtime JSONL lines: {local['retained_runtime_jsonl_line_count']['value']}",
            f"Next: {report['selected_next_value_producing_action']}",
        ]
    )


def render_retained_trace_counters_markdown(payload: dict) -> str:
    report = payload["retained_trace_counters"]
    buckets = report["counter_buckets"]
    local = buckets["local_tool_work"]
    model = buckets["model_facing_context_input"]
    loop = buckets["full_agent_loop_cost"]
    source = buckets["source_ref_retention_quality"]
    lines = [
        "# JSH-756 Offline Retained-Trace Counter Extractor",
        "",
        "## Value Status",
        "",
        "- Tool behavior improved: no.",
        "- Harness capability improved: yes; reusable offline retained-trace counter extraction is now available.",
        "- Benchmark/live eval run: no live eval or send-capable run.",
        "- New numbers produced: yes; sanitized counters for one reviewed held-out row.",
        "- Current blocker: only one clean held-out final-answer row exists, and cross-lane held-out comparison remains unavailable.",
        f"- Next value-producing action: `{report['selected_next_value_producing_action']}`.",
        "",
        "## Scope",
        "",
        f"- Row: `{report['scope']['row_id']}`.",
        "- Extraction type: offline retained local artifacts only.",
        "- No live sends, send-capable `codex exec`, private upload, JSH-748b/c/d resumption, scanner/ranking/rg/sidecar/embedding/generated-metadata tuning, or final-answer readjudication.",
        "",
        "## Extracted Counters",
        "",
        "| Bucket | Counter | Value | Source |",
        "| --- | --- | ---: | --- |",
    ]
    rows = [
        ("local_tool_work", "tool_calls", local["tool_calls"]),
        ("local_tool_work", "subprocess_calls", local["subprocess_calls"]),
        ("local_tool_work", "command_count", local["command_count"]),
        ("local_tool_work", "stdout_bytes", local["stdout_bytes"]),
        ("local_tool_work", "stderr_bytes", local["stderr_bytes"]),
        ("local_tool_work", "elapsed_wall_time_seconds", local["elapsed_wall_time_seconds"]),
        ("model_facing_context_input", "prompt_input_bytes", model["prompt_input_bytes"]),
        ("model_facing_context_input", "tool_stdout_bytes_shown", model["tool_stdout_bytes_shown"]),
        ("model_facing_context_input", "opened_context_bytes_shown", model["opened_context_bytes_shown"]),
        ("model_facing_context_input", "total_model_facing_input_bytes", model["total_model_facing_input_bytes"]),
        ("full_agent_loop_cost", "codex_exec_prompt_sends_performed", loop["codex_exec_prompt_sends_performed"]),
        ("full_agent_loop_cost", "live_model_sends_performed", loop["live_model_sends_performed"]),
        ("full_agent_loop_cost", "retries_performed", loop["retries_performed"]),
        ("full_agent_loop_cost", "final_answer_bytes", loop["final_answer_bytes"]),
        ("full_agent_loop_cost", "model_input_tokens", loop["model_input_tokens"]),
        ("full_agent_loop_cost", "cached_input_tokens", loop["cached_input_tokens"]),
        ("full_agent_loop_cost", "model_output_tokens", loop["model_output_tokens"]),
        ("full_agent_loop_cost", "reasoning_output_tokens", loop["reasoning_output_tokens"]),
        ("full_agent_loop_cost", "observable_turn_context_events", loop["observable_turn_context_events"]),
        ("full_agent_loop_cost", "observable_search_attempt_proxy_count", loop["observable_search_attempt_proxy_count"]),
    ]
    for bucket, name, counter in rows:
        lines.append(f"| `{bucket}` | `{name}` | `{counter['value']}` | {counter['source']} |")
    lines.extend(
        [
            "",
            "## Runtime JSONL Retention",
            "",
            f"- Manifest runtime JSONL count: `{report['input_evidence']['runtime_jsonl_evidence']['manifest_runtime_session_jsonl_count']}`.",
            f"- Locally readable runtime JSONL line count: `{local['retained_runtime_jsonl_line_count']['value']}`.",
            "- Runtime session IDs and private paths are not committed.",
            "",
            "## Source-Ref Retention Quality",
            "",
            f"- Durable committed-safe source IDs in final answer: `{source['durable_committed_safe_source_id_refs']['value']}`.",
            f"- Retained private trace mapping: `{source['retained_private_trace_mapping']['value']}`.",
            f"- Temporary sandbox refs present: `{source['temporary_sandbox_refs']['value']}`.",
            "",
            "## Privacy Boundary",
            "",
            "- The packet/report commit only source IDs, counts, byte counts, hashes, availability labels, and provenance labels.",
            "- They do not commit raw private transcript text, raw prompt/final-answer text, raw source snippets, raw private paths, session IDs, source windows, answer keys, expected refs, private bridge payloads, or evaluator-only raw material.",
        ]
    )
    return "\n".join(lines)


def render_generated_metadata_discovery_markdown(payload: dict) -> str:
    report = payload["generated_metadata_discovery"]
    lines = [
        "# Generated Metadata Discovery",
        "",
        f"**Status:** {report['status']}",
        f"**Mode:** {report['mode']}",
        "",
        "This is a report-only prototype. It does not enable default generated metadata, sidecars, watchers, ranking changes, or token-efficiency claims.",
        "",
        "## Candidates",
    ]
    if not report["candidates"]:
        lines.append("- None above the selected threshold.")
    for candidate in report["candidates"]:
        evidence = candidate["evidence"]
        shapes = ", ".join(evidence.get("example_shapes", [])[:3]) or "none"
        lines.append(
            f"- `{candidate['kind']}` `{candidate['label']}`: "
            f"{candidate['confidence']}; entries={evidence['entry_count']}; "
            f"raw_values={evidence['raw_value_count']}; shapes={shapes}"
        )
    privacy = report["privacy_contract"]
    promotion = report["promotion_boundary"]
    lines.extend(
        [
            "",
            "## Privacy And Promotion Boundary",
            f"- Raw private text committed: `{privacy['raw_private_text_committed']}`",
            f"- Raw source paths committed: `{privacy['raw_source_paths_committed']}`",
            f"- Raw values exposed: `{privacy['raw_values_exposed']}`",
            f"- Default search uses generated metadata: `{promotion['default_search_uses_generated_metadata']}`",
            f"- Sidecar or index created: `{promotion['sidecar_or_index_created']}`",
            f"- Token-efficiency claim: `{promotion['token_efficiency_claimed']}`",
            f"- Ranking or product-value claim: `{promotion['ranking_or_product_value_claimed']}`",
        ]
    )
    assessment = report["mechanism_assessment"]
    lines.extend(["", "## Learning", f"- Status: `{assessment['status']}`"])
    for item in assessment.get("what_worked", []):
        lines.append(f"- Worked: {item}")
    for item in assessment.get("what_failed_or_is_unknown", []):
        lines.append(f"- Unknown: {item}")
    return "\n".join(lines)


def render_scope_boundary_summary_text(summary: dict) -> list[str]:
    if not summary:
        return []
    searched = summary.get("searched_scope") or {}
    not_searched = summary.get("not_searched_scope") or {}
    excluded = summary.get("excluded_scope") or {}
    caps = summary.get("caps") or {}
    duplicate = summary.get("duplicate_mirror_risk") or {}
    lines = [
        "Scope boundary summary:",
        (
            "- searched: "
            f"roots={searched.get('searched_root_count', 0)}/{searched.get('selected_root_count', 0)} "
            f"files={searched.get('searched_files', 0)} bytes={searched.get('searched_bytes', 0)} "
            f"meaning={searched.get('result_meaning')}"
        ),
        (
            "- not searched/excluded: "
            f"outside_selected_roots={not_searched.get('outside_selected_roots')} "
            f"missing_roots={not_searched.get('missing_root_count', 0)} "
            f"deferred_files={not_searched.get('source_plan_deferred_files', 0)} "
            f"pruned_files={not_searched.get('source_plan_pruned_files', 0)} "
            f"rejected_roots={excluded.get('rejected_root_count', 0)}"
        ),
        (
            "- caps: "
            f"{caps.get('status')} hit={caps.get('hit', False)} "
            f"caps_hit={', '.join(caps.get('caps_hit') or []) or 'none'}"
        ),
        (
            "- duplicate/mirror risk: "
            f"{duplicate.get('status')} "
            f"collapsed_candidates={duplicate.get('collapsed_candidate_count', 0)} "
            f"duplicate_or_collapsed_rows={duplicate.get('duplicate_or_collapsed_row_count', 0)} "
            f"duplicate_file_overlap={duplicate.get('duplicate_file_overlap_count', 0)}"
        ),
        f"- not-all-history boundary: {summary.get('claim_boundary')}",
        f"- escalation condition: {summary.get('escalation_condition')}",
    ]
    return lines


def render_scope_boundary_summary_markdown(summary: dict) -> list[str]:
    if not summary:
        return []
    searched = summary.get("searched_scope") or {}
    not_searched = summary.get("not_searched_scope") or {}
    excluded = summary.get("excluded_scope") or {}
    caps = summary.get("caps") or {}
    duplicate = summary.get("duplicate_mirror_risk") or {}
    return [
        "",
        "## Scope Boundary Summary",
        "",
        (
            f"- `searched`: roots `{searched.get('searched_root_count', 0)}/{searched.get('selected_root_count', 0)}`, "
            f"files `{searched.get('searched_files', 0)}`, bytes `{searched.get('searched_bytes', 0)}`, "
            f"meaning `{searched.get('result_meaning')}`"
        ),
        (
            f"- `not_searched_or_excluded`: outside selected roots `{not_searched.get('outside_selected_roots')}`, "
            f"missing roots `{not_searched.get('missing_root_count', 0)}`, "
            f"deferred files `{not_searched.get('source_plan_deferred_files', 0)}`, "
            f"pruned files `{not_searched.get('source_plan_pruned_files', 0)}`, "
            f"rejected roots `{excluded.get('rejected_root_count', 0)}`"
        ),
        (
            f"- `caps`: `{caps.get('status')}`, hit `{caps.get('hit', False)}`, "
            f"caps hit `{', '.join(caps.get('caps_hit') or []) or 'none'}`"
        ),
        (
            f"- `duplicate_mirror_risk`: `{duplicate.get('status')}`, "
            f"collapsed candidates `{duplicate.get('collapsed_candidate_count', 0)}`, "
            f"duplicate/collapsed rows `{duplicate.get('duplicate_or_collapsed_row_count', 0)}`, "
            f"duplicate file overlap `{duplicate.get('duplicate_file_overlap_count', 0)}`"
        ),
        f"- `not_all_history_boundary`: {summary.get('claim_boundary')}",
        f"- `escalation_condition`: {summary.get('escalation_condition')}",
    ]


def _render_semantic_debug_missing_primary_evidence(
    report: dict, *, markdown: bool
) -> str | None:
    handoff = report.get("answer_ready_handoff") or {}
    target_mismatch_block = handoff.get("target_mismatch_answer_block") or {}
    target_mismatch = (
        handoff.get("answer_readiness_status") == "target_mismatch_detected"
        and target_mismatch_block.get("blocked") is True
    )
    if handoff.get("decision") != "missing_primary_evidence" and not target_mismatch:
        return None
    guidance = handoff.get("source_action_guidance") or {}
    followthrough = report.get("in_depth_audit_followthrough") or {}
    target_contract = followthrough.get("target_persistence_contract") or {}
    commands = []
    command_items = guidance.get("exact_jsonl_continuation_commands") or []
    if target_mismatch:
        command_items = (
            (report.get("in_depth_audit_followthrough") or {}).get("continuation_commands")
            or command_items
        )
    for item in command_items:
        command = item.get("command") or item.get("shell") if isinstance(item, dict) else None
        if command:
            commands.append(str(command))
    blocker = (
        "Target-matching primary conversation-history evidence was not found in the searched scope, "
        "so the historical/provenance question cannot be answered."
    )
    requested_failure_class = target_contract.get("requested_failure_class") or {}
    time_scope_intent = target_contract.get("time_scope_intent") or {}
    target_contract_summary = (
        f"Target contract: failure_class={requested_failure_class.get('normalized_label') or 'unspecified'}; "
        f"scope={time_scope_intent.get('scope') or 'unspecified'}; "
        f"required_evidence={','.join(target_contract.get('required_evidence_classes') or []) or 'none'}; "
        f"forbidden_substitutes={','.join(target_contract.get('forbidden_nearby_substitutes') or []) or 'none'}."
        if target_mismatch and target_contract
        else None
    )
    prohibition = (
        "STOP: Repository task, report, review, documentation, and learning artifacts are "
        "post-hoc context, not substitutes for missing primary history evidence. Do not search "
        "or cite them to answer this question, and do not use broad repository or manual transcript "
        + (
            "searches outside the exact JSONL continuation below."
            if commands
            else "searches. Return the bounded fail-closed packet below instead of asking the user in this turn."
        )
    )
    status = (
        "target_mismatch_detected / missing_primary_evidence"
        if target_mismatch
        else "missing_primary_evidence"
    )
    if markdown:
        lines = [
            "# Not Answerable",
            "",
            f"- Status: `{status}`",
            f"- {blocker}",
            f"- **{prohibition}**",
        ]
        if target_contract_summary:
            lines.append(f"- {target_contract_summary}")
        if commands:
            lines.extend(["", "## Exact JSONL Continuation", ""])
            for command in commands[:1]:
                lines.extend(["```bash", command, "```"])
        else:
            lines.extend(
                [
                    "",
                    (
                        "- FAIL-CLOSED PACKET: no bounded target-preserving continuation is available. "
                        "Report searched scope and the missing target facet; do not ask the user in this turn."
                        if target_mismatch
                        else "- FAIL-CLOSED PACKET: no bounded continuation is available. Report searched scope "
                        "and the missing primary facet; do not ask the user in this turn."
                    ),
                ]
            )
        return "\n".join(lines)
    lines = [
        "NOT ANSWERABLE",
        f"Status: {status}",
        blocker,
        prohibition,
    ]
    if target_contract_summary:
        lines.append(target_contract_summary)
    if commands:
        lines.extend(["Exact JSONL continuation:", commands[0]])
    else:
        lines.append(
            "FAIL-CLOSED PACKET: no bounded target-preserving continuation is available. Report searched scope "
            "and the missing target facet; do not ask the user in this turn."
            if target_mismatch
            else "FAIL-CLOSED PACKET: no bounded continuation is available. Report searched scope and the "
            "missing primary facet; do not ask the user in this turn."
        )
    return "\n".join(lines)


def _render_semantic_debug_cap_hit_disposition(
    report: dict, *, markdown: bool
) -> str | None:
    followthrough = report.get("in_depth_audit_followthrough") or {}
    disposition = followthrough.get("cap_hit_disposition") or {}
    status = disposition.get("status")
    if status not in {
        "canonical_continuation",
        "canonical_continuation_packet",
        "scoped_fail_closed",
    }:
        return None

    coverage = followthrough.get("coverage_state") or {}
    searched = coverage.get("searched_scope") or {}
    not_searched = coverage.get("not_searched_scope") or {}
    caps = coverage.get("caps") or coverage.get("search_cap_hit") or {}
    cap_labels = ", ".join(str(item) for item in caps.get("caps_hit") or []) or "unspecified"
    boundary = "selected history roots and reported searched windows only; hidden evidence may remain"
    canonical = disposition.get("canonical_continuation") or {}
    command = canonical.get("command") if isinstance(canonical, dict) else None
    continuation_packet = disposition.get("canonical_continuation_packet") or {}
    packet_steps = continuation_packet.get("steps") if isinstance(continuation_packet, dict) else []
    packet_steps = [step for step in packet_steps or [] if isinstance(step, dict) and step.get("command")]

    if markdown:
        lines = [
            "# Cap-Hit Disposition",
            "",
            f"- Status: `{status}`",
            "- Answer ready: `false`",
            f"- Searched scope: files=`{searched.get('searched_files', 0)}`, bytes=`{searched.get('searched_bytes', 0)}`",
            (
                f"- Blind spots: caps_hit=`{cap_labels}`, missing_roots=`{not_searched.get('missing_root_count', 0)}`, "
                f"deferred_files=`{not_searched.get('source_plan_deferred_files', 0)}`"
            ),
            f"- Source boundary: {boundary}.",
            "- Partial candidates and snippets are withheld until coverage is complete.",
            "- Manual history fallback: `false`",
        ]
        if status == "canonical_continuation" and command:
            lines.extend(["", "## Canonical Continuation", "", "```bash", str(command), "```"])
        elif status == "canonical_continuation_packet" and packet_steps:
            lines.extend(
                [
                    "",
                    "## Canonical Continuation Packet",
                    "",
                    f"- Packet ID: `{continuation_packet.get('packet_id')}`",
                    f"- Execution mode: `{continuation_packet.get('execution_mode')}`",
                    "- Run every step in order. Partial step results are not answer material.",
                ]
            )
            for index, step in enumerate(packet_steps, 1):
                lines.extend(["", f"### Step {index}", "", "```bash", str(step["command"]), "```"])
            blockers = list(continuation_packet.get("blockers") or [])
            if blockers:
                lines.append(f"- Remaining blockers after bounded steps: `{json.dumps(blockers, sort_keys=True)}`")
        else:
            reason = (disposition.get("scoped_fail_closed") or {}).get("reason")
            lines.extend(["", f"- **FAIL CLOSED:** {reason or 'No safe canonical continuation is available.'}"])
        return "\n".join(lines)

    lines = [
        "CAP-HIT DISPOSITION",
        f"Status: {status}",
        "Answer ready: false",
        f"Searched scope: files={searched.get('searched_files', 0)} bytes={searched.get('searched_bytes', 0)}",
        (
            f"Blind spots: caps_hit={cap_labels} missing_roots={not_searched.get('missing_root_count', 0)} "
            f"deferred_files={not_searched.get('source_plan_deferred_files', 0)}"
        ),
        f"Source boundary: {boundary}.",
        "Partial candidates and snippets are withheld until coverage is complete.",
        "Manual history fallback: false",
    ]
    if status == "canonical_continuation" and command:
        lines.extend(["Canonical continuation:", str(command)])
    elif status == "canonical_continuation_packet" and packet_steps:
        lines.extend(
            [
                "CANONICAL CONTINUATION PACKET",
                f"Packet ID: {continuation_packet.get('packet_id')}",
                f"Execution mode: {continuation_packet.get('execution_mode')}",
                "Run every step in order. Partial step results are not answer material.",
            ]
        )
        for index, step in enumerate(packet_steps, 1):
            lines.extend([f"Step {index}:", str(step["command"])])
        blockers = list(continuation_packet.get("blockers") or [])
        if blockers:
            lines.append(f"Remaining blockers after bounded steps: {json.dumps(blockers, sort_keys=True)}")
    else:
        reason = (disposition.get("scoped_fail_closed") or {}).get("reason")
        lines.append(f"FAIL CLOSED: {reason or 'No safe canonical continuation is available.'}")
    return "\n".join(lines)


def render_semantic_debug_text(payload: dict) -> str:
    report = payload["semantic_debug"]
    missing_primary = _render_semantic_debug_missing_primary_evidence(report, markdown=False)
    if missing_primary is not None:
        return missing_primary
    cap_hit_projection = _render_semantic_debug_cap_hit_disposition(report, markdown=False)
    if cap_hit_projection is not None:
        return cap_hit_projection
    direct_projection = _render_provenance_direct_proof_projection(report, markdown=False)
    if direct_projection is not None:
        return direct_projection
    family = report.get("question_family", {})
    tradeoffs = report.get("token_byte_tradeoffs", {})
    action_proof_plan = report.get("action_proof_plan") or {}
    handoff = report.get("answer_ready_handoff") or {}
    source_action_guidance = handoff.get("source_action_guidance") or {}
    scoped_no_evidence = (
        handoff.get("decision") == "scoped_no_evidence"
        or source_action_guidance.get("decision") == "answer_scoped_no_evidence_now"
    )
    next_reads_for_required_surfaces = (
        [] if scoped_no_evidence else _required_surface_reads(report.get("next_reads", []) or [])
    )
    command_by_target = _read_more_commands_by_target(next_reads_for_required_surfaces)
    readiness = report.get("answer_readiness") or {}
    followthrough = handoff.get("primary_source_followthrough") or {}
    direct_evidence_complete = _direct_provenance_evidence_complete(report)
    suppress_action_required_reads = _is_compact_multifacet_provenance_followthrough(followthrough)
    required_reads = (
        []
        if suppress_action_required_reads
        else _required_surface_reads(action_proof_plan.get("required_proof_reads") or [])
    )
    process_read_plan = report.get("process_actual_use_final_support_read_plan") or (
        report.get("answer_ready_handoff") or {}
    ).get("process_actual_use_final_support_read_plan") or (
        report.get("source_locator_handoff") or {}
    ).get("process_actual_use_final_support_read_plan")
    source_followthrough_plan = report.get("source_followthrough_plan") or {}
    spawned_session_resolution = (
        report.get("spawned_session_resolution")
        or handoff.get("spawned_session_resolution")
        or (report.get("source_locator_handoff") or {}).get("spawned_session_resolution")
        or {}
    )
    in_depth_coverage = report.get("in_depth_audit_coverage") or handoff.get("in_depth_audit_coverage") or {}
    in_depth_followthrough = (
        report.get("in_depth_audit_followthrough")
        or handoff.get("in_depth_audit_followthrough")
        or (report.get("source_locator_handoff") or {}).get("in_depth_audit_followthrough")
        or {}
    )
    deep_audit_followthrough_active = bool(in_depth_followthrough.get("applies"))
    coverage_blocks_answer = (
        isinstance(in_depth_coverage, dict)
        and in_depth_coverage.get("applies")
        and not in_depth_coverage.get("answer_promotion_allowed")
    )
    answer_status = (
        "not-ready/needs-more-proof"
        if coverage_blocks_answer
        else "answer-scoped-no-evidence-now"
        if scoped_no_evidence
        else "answer-after-required-reads"
        if required_reads or next_reads_for_required_surfaces
        else "not-ready/needs-more-proof"
    )
    stop_condition = (
        source_action_guidance.get("stop_condition")
        if scoped_no_evidence
        else in_depth_followthrough.get("stop_rule")
        if deep_audit_followthrough_active
        else followthrough.get("stop_after")
        if suppress_action_required_reads
        else action_proof_plan.get("stop_condition")
        or followthrough.get("stop_after")
        or "Stop when inspected source refs support the answer within the searched-scope boundaries."
    )
    primary_command = (followthrough.get("first_command") or {}).get("command") if followthrough.get("applies") else None
    followthrough_required_reads = list(
        source_followthrough_plan.get("default_required_primary_proof")
        or source_followthrough_plan.get("required_primary_proof")
        or []
    )
    orchestration_role_bundle = (
        report.get("historical_orchestration_role_evidence_bundle")
        or handoff.get("historical_orchestration_role_evidence_bundle")
        or (report.get("source_locator_handoff") or {}).get("historical_orchestration_role_evidence_bundle")
    )
    orchestration_role_plan = (
        report.get("historical_orchestration_role_coverage_source_plan")
        or report.get("historical_orchestration_provenance_final_support_plan")
        or handoff.get("historical_orchestration_role_coverage_source_plan")
        or handoff.get("historical_orchestration_provenance_final_support_plan")
        or (report.get("source_locator_handoff") or {}).get("historical_orchestration_role_coverage_source_plan")
        or (report.get("source_locator_handoff") or {}).get("historical_orchestration_provenance_final_support_plan")
    )
    longitudinal_plan = (
        report.get("longitudinal_phase_source_plan")
        or handoff.get("longitudinal_phase_source_plan")
        or (report.get("source_locator_handoff") or {}).get("longitudinal_phase_source_plan")
    )
    lines = [
        "Semantic Debug",
        f"Status: {report.get('status')}",
        f"Answer status: {answer_status}",
        f"Audit depth: {(report.get('audit_depth') or {}).get('label', 'quick')}",
    ]
    if isinstance(in_depth_coverage, dict) and in_depth_coverage.get("applies"):
        lines.append(
            "In-depth coverage: "
            f"status={in_depth_coverage.get('status')} "
            f"promotion_allowed={in_depth_coverage.get('answer_promotion_allowed')} "
            f"slice_status_counts={in_depth_coverage.get('slice_status_counts')}"
        )
    lines.extend(render_in_depth_audit_followthrough_text(in_depth_followthrough, command_by_target=command_by_target))
    if not deep_audit_followthrough_active:
        lines.extend(render_source_followthrough_plan_text(source_followthrough_plan, command_by_target=command_by_target))
    lines.extend(render_scope_boundary_summary_text(report.get("scope_boundary_summary") or handoff.get("scope_boundary_summary") or {}))
    lines.extend(render_investigation_finalization_state_text(report.get("investigation_finalization_state") or {}))
    if report.get("cap_next_action"):
        action = report["cap_next_action"]
        reasons = ", ".join(str(item) for item in action.get("trigger_reasons", []))
        lines.append(f"Cap next action: {action.get('selected_action')} ({reasons})")
        if action.get("rationale"):
            lines.append(f"Cap action rationale: {action.get('rationale')}")
    if report.get("bm25_escalation"):
        escalation = report["bm25_escalation"]
        command = (escalation.get("command") or {}).get("shell")
        reasons = ", ".join(str(item) for item in escalation.get("trigger_reasons", []))
        lines.append(f"BM25 escalation: {escalation.get('status')} ({reasons})")
        if command:
            lines.append(f"BM25 retry command: {command}")
    if spawned_session_resolution and spawned_session_resolution.get("applies"):
        lines.extend(render_spawned_session_resolution_text(spawned_session_resolution))
    if orchestration_role_bundle:
        lines.extend(render_historical_orchestration_role_evidence_bundle_text(orchestration_role_bundle))
    if orchestration_role_plan:
        lines.extend(render_historical_orchestration_role_coverage_source_plan_text(orchestration_role_plan))
    if longitudinal_plan and longitudinal_plan.get("applies"):
        lines.extend(render_longitudinal_phase_source_plan_text(longitudinal_plan))
    lines.append("Open these source refs first:")
    if deep_audit_followthrough_active:
        lines.append("- see AUDIT CONTINUATION WORKFLOW.ordered_primary_refs_to_open_first above")
    elif scoped_no_evidence:
        lines.append("- no required primary source read; answer `not_found_in_searched_scope` with searched/not-searched/cap boundaries")
        if source_action_guidance.get("diagnostic_policy"):
            lines.append(f"- diagnostic policy: {source_action_guidance.get('diagnostic_policy')}")
    elif direct_evidence_complete and followthrough_required_reads:
        for read in followthrough_required_reads[:2]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    elif process_read_plan and process_read_plan.get("required_primary_raw_final_support_reads"):
        for read in process_read_plan.get("required_primary_raw_final_support_reads", [])[:4]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    elif primary_command:
        lines.append(f"- primary source window command: {primary_command}")
        required_final_source_ref_ids = list(followthrough.get("required_final_source_ref_ids") or [])
        for source_ref_id in required_final_source_ref_ids[:3]:
            lines.append(f"- required final source ref: {source_ref_id}")
        for facet in list(followthrough.get("required_answer_facets") or [])[:6]:
            lines.append(f"- required answer facet: {facet}")
        lines.append("- fallback individual refs are listed later only if this window leaves a named answer facet missing")
    elif required_reads:
        for read in required_reads[:5]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    elif next_reads_for_required_surfaces:
        for read in next_reads_for_required_surfaces[:5]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- (no source refs surfaced)")
    lines.append(f"Stop condition: {stop_condition}")
    only_continue = (
        []
        if deep_audit_followthrough_active
        else ((report.get("answer_ready_handoff") or {}).get("primary_source_followthrough") or {}).get("only_continue_if")
        or readiness.get("required_before_answer")
        or []
    )
    for item in only_continue[:4]:
        lines.append(f"Only continue if: {item}")
    lines.append("Required proof reads:")
    if required_reads:
        for read in required_reads[:8]:
            _append_text_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- none listed")
    lines.extend([
        "Optional diagnostics/adjacent evidence: source locator handoff, strongest candidates, uncertainty, and retrieval mode diagnostics below.",
        f"Query: {report.get('query')}",
        f"Primary family: {family.get('primary')}",
        f"Candidates: {len(report.get('strongest_candidates', []))}",
        f"Structured events: {report.get('tool_action_proof', {}).get('event_count', 0)}",
        f"Packet bytes/tokens: {tradeoffs.get('packet_basis_bytes', 0)} / {tradeoffs.get('packet_basis_estimated_tokens', 0)}",
        "Root cause: not claimed",
    ])
    workflow_summary = report.get("forensics_workflow_summary") or (report.get("forensics_workflow") or {}).get("summary")
    if workflow_summary:
        basis_counts = workflow_summary.get("join_basis_counts") or {}
        provenance = workflow_summary.get("provenance") or {}
        support_summary = workflow_summary.get("support_summary") or workflow_summary
        lines.append(
            "Forensics workflow: "
            f"phases={workflow_summary.get('phase_count', 0)} "
            f"joins={workflow_summary.get('join_count', 0)} "
            f"review_gate={workflow_summary.get('review_gate_status') or 'unknown'} "
            f"support={_format_support_counts(support_summary)} "
            f"join_basis={_format_count_map(basis_counts)} "
            f"provenance_commits={provenance.get('commit_count', 0)}"
        )
    decision_context = report.get("decision_context") or report.get("decision_context_summary")
    if decision_context:
        lines.append(
            "Decision context: "
            f"project_time={decision_context.get('project_time_reconstruction_needed')} "
            f"routes={','.join(decision_context.get('selected_routes', []))}"
        )
    audit_plan = report.get("audit_plan") or report.get("audit_plan_summary")
    if audit_plan:
        lines.append(
            "Audit plan: "
            f"routes={','.join(audit_plan.get('route_order', []))} "
            f"watchlist={len(audit_plan.get('watchlist', []))}"
        )
    investigation_state = report.get("investigation_state")
    if investigation_state:
        lines.extend(render_investigation_state_text(investigation_state))
    if action_proof_plan and action_proof_plan.get("applies") and not suppress_action_required_reads:
        lines.extend(render_action_proof_plan_text(action_proof_plan, command_by_target=command_by_target))
    elif action_proof_plan and action_proof_plan.get("applies") and suppress_action_required_reads:
        lines.append(
            "Action-proof plan: fallback diagnostics suppressed because primary source followthrough is active for this provenance prompt."
        )
    if report.get("report_written"):
        lines.append(f"Report written: {report.get('report_written')}")
    if report.get("source_locator_handoff"):
        lines.extend(render_source_locator_handoff_text(report["source_locator_handoff"]))
    handoff = report.get("answer_ready_handoff")
    if handoff:
        lines.append(
            "Answer-ready handoff: "
            f"decision={handoff.get('decision')} "
            f"answer_readiness_status={handoff.get('answer_readiness_status')} "
            f"gate={handoff.get('answer_gate_status')} "
            f"next={handoff.get('do_next')}"
        )
        source_action = handoff.get("source_action_guidance") or {}
        if source_action:
            lines.append(
                "Source action guidance: "
                f"decision={source_action.get('decision')} "
                f"required_primary={len(source_action.get('required_primary_source_actions') or [])} "
                f"optional_diagnostics={len(source_action.get('optional_diagnostic_reads') or [])}"
            )
            if source_action.get("retrieval_escalation_condition"):
                lines.append(f"- retrieval_escalation_condition: {source_action.get('retrieval_escalation_condition')}")
        followthrough = handoff.get("primary_source_followthrough") or {}
        if followthrough.get("applies"):
            command = followthrough.get("first_command") or {}
            lines.append(
                "Primary source followthrough: "
                f"do_first={followthrough.get('do_first')} "
                f"primary_reads={followthrough.get('primary_read_count')} "
                f"command_id={command.get('command_id')} "
                f"target={command.get('target')}"
            )
            if command.get("target_absolute_path_like") or not command.get("target_local_path_like"):
                lines.append(f"- command: {command.get('command')}")
            else:
                lines.append("- command: omitted from text output for relative/local target; use JSON field first_command.command")
            lines.append(f"- stop_after: {followthrough.get('stop_after')}")
            for facet in list(followthrough.get("required_answer_facets") or [])[:6]:
                lines.append(f"- required_answer_facet: {facet}")
            for item in (followthrough.get("only_continue_if") or [])[:3]:
                lines.append(f"- only_continue_if: {item}")
    if process_read_plan:
        _append_process_actual_use_read_plan_text(
            lines,
            process_read_plan,
            command_by_target=command_by_target,
        )
    contract = report.get("final_answer_source_use_contract")
    if contract:
        lines.append("Final Answer Contract:")
        lines.append(f"- answer_scope: {contract.get('answer_scope')}")
        lines.append(f"- source_use_required: {contract.get('source_use_required')}")
        reviewed_provenance = contract.get("required_final_provenance") or {}
        for source_ref_id in reviewed_provenance.get("required_source_ref_ids") or []:
            lines.append(f"- required final provenance: {source_ref_id}")
        if reviewed_provenance.get("fail_closed_when_missing"):
            lines.append("- final answer validation: fail closed when required reviewed provenance is missing")
        for action in contract.get("required_actions", [])[:5]:
            lines.append(f"- {action}")
        process_contract = contract.get("process_actual_use_contract") or {}
        if process_contract:
            lines.append("- process_actual_use: primary raw evidence is required for behavior claims")
            hierarchy = process_contract.get("source_hierarchy") or {}
            if hierarchy.get("secondary_artifact_rule"):
                lines.append(f"- {hierarchy.get('secondary_artifact_rule')}")
            for bucket in list(process_contract.get("bucket_requirements") or [])[:6]:
                lines.append(
                    f"- actual_use_bucket: {bucket.get('bucket')} ({bucket.get('evidence_status')})"
                )
    if readiness:
        lines.append("Answer Readiness:")
        lines.append(f"- status: {readiness.get('status')}")
        lines.append(f"- decision: {readiness.get('decision')}")
        lines.append(f"- allowed_answer_scope: {readiness.get('allowed_answer_scope')}")
        followthrough = (report.get("answer_ready_handoff") or {}).get("primary_source_followthrough") or {}
        if followthrough.get("applies"):
            lines.append("- effective_next_action: run_primary_source_window_command_then_answer_if_supported")
            lines.append("- older recovery-readiness actions are secondary unless the primary source window is insufficient")
        else:
            for action in readiness.get("required_before_answer", [])[:4]:
                lines.append(f"- {action}")
    instruction = report.get("agent_instruction")
    if instruction:
        lines.append("Agent Instruction:")
        lines.append(f"- decision: {instruction.get('decision')}")
        lines.append(f"- required_next_action: {instruction.get('required_next_action')}")
        commands = instruction.get("commands", [])
        lines.append(f"- command_count: {len(commands)}")
        for command in commands[:5]:
            rendered = _action_command(command)
            if rendered:
                lines.append(f"- command: {rendered}")
    if report.get("next_reads"):
        followthrough = (report.get("answer_ready_handoff") or {}).get("primary_source_followthrough") or {}
        if scoped_no_evidence:
            lines.append("Optional diagnostic next reads (not required before scoped no-evidence answer):")
            for item in report.get("next_reads", [])[:8]:
                _append_text_read_summary(lines, item)
        elif deep_audit_followthrough_active:
            lines.append("Fallback next reads (suppressed for deep-history audit follow-through):")
            lines.append("- open ordered_primary_refs_to_open_first first; do not use broad search or extra read-more unless a named facet remains missing")
            lines.append(
                f"- additional next_reads preserved in JSON/report: {len(report.get('next_reads', []) or [])}"
            )
        elif followthrough.get("applies"):
            lines.append("Fallback next reads (only if the primary source window leaves a named facet missing):")
            lines.append("- executable fallback commands suppressed in text output; use JSON next_reads only after a named facet gap")
            if direct_evidence_complete:
                secondary_count = sum(
                    1
                    for item in report.get("next_reads", []) or []
                    if isinstance(item, dict) and item.get("secondary_context_role")
                )
                lines.append(
                    f"- secondary recap/summary/specification targets suppressed after complete direct evidence: {secondary_count}"
                )
            else:
                for item in report.get("next_reads", [])[:8]:
                    _append_text_read_summary(lines, item)
        else:
            lines.append("Next reads:")
            for item in report.get("next_reads", [])[:8]:
                _append_text_read(lines, item, command_by_target=command_by_target)
    for item in report.get("uncertainty", [])[:8]:
        lines.append(f"Uncertainty: {item.get('label')} - {item.get('meaning')}")
    return "\n".join(lines)


def render_semantic_debug_markdown(payload: dict) -> str:
    report = payload["semantic_debug"]
    missing_primary = _render_semantic_debug_missing_primary_evidence(report, markdown=True)
    if missing_primary is not None:
        return missing_primary
    cap_hit_projection = _render_semantic_debug_cap_hit_disposition(report, markdown=True)
    if cap_hit_projection is not None:
        return cap_hit_projection
    direct_projection = _render_provenance_direct_proof_projection(report, markdown=True)
    if direct_projection is not None:
        return direct_projection
    family = report.get("question_family", {})
    tradeoffs = report.get("token_byte_tradeoffs", {})
    action_proof_plan = report.get("action_proof_plan") or {}
    handoff = report.get("answer_ready_handoff") or {}
    source_action_guidance = handoff.get("source_action_guidance") or {}
    scoped_no_evidence = (
        handoff.get("decision") == "scoped_no_evidence"
        or source_action_guidance.get("decision") == "answer_scoped_no_evidence_now"
    )
    next_reads_for_required_surfaces = (
        [] if scoped_no_evidence else _required_surface_reads(report.get("next_reads", []) or [])
    )
    command_by_target = _read_more_commands_by_target(next_reads_for_required_surfaces)
    readiness = report.get("answer_readiness") or {}
    followthrough = handoff.get("primary_source_followthrough") or {}
    direct_evidence_complete = _direct_provenance_evidence_complete(report)
    suppress_action_required_reads = _is_compact_multifacet_provenance_followthrough(followthrough)
    required_reads = (
        []
        if suppress_action_required_reads
        else _required_surface_reads(action_proof_plan.get("required_proof_reads") or [])
    )
    process_read_plan = report.get("process_actual_use_final_support_read_plan") or (
        report.get("answer_ready_handoff") or {}
    ).get("process_actual_use_final_support_read_plan") or (
        report.get("source_locator_handoff") or {}
    ).get("process_actual_use_final_support_read_plan")
    source_followthrough_plan = report.get("source_followthrough_plan") or {}
    spawned_session_resolution = (
        report.get("spawned_session_resolution")
        or handoff.get("spawned_session_resolution")
        or (report.get("source_locator_handoff") or {}).get("spawned_session_resolution")
        or {}
    )
    in_depth_coverage = report.get("in_depth_audit_coverage") or handoff.get("in_depth_audit_coverage") or {}
    in_depth_followthrough = (
        report.get("in_depth_audit_followthrough")
        or handoff.get("in_depth_audit_followthrough")
        or (report.get("source_locator_handoff") or {}).get("in_depth_audit_followthrough")
        or {}
    )
    deep_audit_followthrough_active = bool(in_depth_followthrough.get("applies"))
    coverage_blocks_answer = (
        isinstance(in_depth_coverage, dict)
        and in_depth_coverage.get("applies")
        and not in_depth_coverage.get("answer_promotion_allowed")
    )
    answer_status = (
        "not-ready/needs-more-proof"
        if coverage_blocks_answer
        else "answer-scoped-no-evidence-now"
        if scoped_no_evidence
        else "answer-after-required-reads"
        if required_reads or next_reads_for_required_surfaces
        else "not-ready/needs-more-proof"
    )
    stop_condition = (
        source_action_guidance.get("stop_condition")
        if scoped_no_evidence
        else in_depth_followthrough.get("stop_rule")
        if deep_audit_followthrough_active
        else followthrough.get("stop_after")
        if suppress_action_required_reads
        else action_proof_plan.get("stop_condition")
        or followthrough.get("stop_after")
        or "Stop when inspected source refs support the answer within the searched-scope boundaries."
    )
    primary_command = (followthrough.get("first_command") or {}).get("command") if followthrough.get("applies") else None
    followthrough_required_reads = list(
        source_followthrough_plan.get("default_required_primary_proof")
        or source_followthrough_plan.get("required_primary_proof")
        or []
    )
    orchestration_role_bundle = (
        report.get("historical_orchestration_role_evidence_bundle")
        or handoff.get("historical_orchestration_role_evidence_bundle")
        or (report.get("source_locator_handoff") or {}).get("historical_orchestration_role_evidence_bundle")
    )
    longitudinal_plan = (
        report.get("longitudinal_phase_source_plan")
        or handoff.get("longitudinal_phase_source_plan")
        or (report.get("source_locator_handoff") or {}).get("longitudinal_phase_source_plan")
    )
    lines = [
        "# Semantic Debug",
        "",
        f"**Status:** {report.get('status')}",
        f"**Answer status:** `{answer_status}`",
        f"**Audit depth:** `{(report.get('audit_depth') or {}).get('label', 'quick')}`",
    ]
    if isinstance(in_depth_coverage, dict) and in_depth_coverage.get("applies"):
        lines.append(
            f"**In-depth coverage:** `status={in_depth_coverage.get('status')}` "
            f"`promotion_allowed={in_depth_coverage.get('answer_promotion_allowed')}` "
            f"`slice_status_counts={in_depth_coverage.get('slice_status_counts')}`"
        )
    lines.extend(render_in_depth_audit_followthrough_markdown(in_depth_followthrough, command_by_target=command_by_target))
    if not deep_audit_followthrough_active:
        lines.extend(render_source_followthrough_plan_markdown(source_followthrough_plan, command_by_target=command_by_target))
    lines.extend(render_scope_boundary_summary_markdown(report.get("scope_boundary_summary") or handoff.get("scope_boundary_summary") or {}))
    lines.extend(render_investigation_finalization_state_markdown(report.get("investigation_finalization_state") or {}))
    if report.get("cap_next_action"):
        action = report["cap_next_action"]
        reasons = ", ".join(str(item) for item in action.get("trigger_reasons", []))
        lines.extend(
            [
                "",
                "## Cap Next Action",
                "",
                f"- Selected action: `{action.get('selected_action')}`",
                f"- Reasons: `{reasons}`",
            ]
        )
        if action.get("rationale"):
            lines.append(f"- Rationale: {action.get('rationale')}")
    if report.get("bm25_escalation"):
        escalation = report["bm25_escalation"]
        command = (escalation.get("command") or {}).get("shell")
        reasons = ", ".join(str(item) for item in escalation.get("trigger_reasons", []))
        lines.extend(["", "## BM25 Escalation", "", f"- Status: `{escalation.get('status')}`", f"- Reasons: `{reasons}`"])
        if command:
            lines.extend(["", "```bash", command, "```"])
    if spawned_session_resolution and spawned_session_resolution.get("applies"):
        lines.extend(render_spawned_session_resolution_markdown(spawned_session_resolution))
    if orchestration_role_bundle:
        lines.extend(render_historical_orchestration_role_evidence_bundle_markdown(orchestration_role_bundle))
    if longitudinal_plan and longitudinal_plan.get("applies"):
        lines.extend(render_longitudinal_phase_source_plan_markdown(longitudinal_plan))
    lines.extend(["", "## Open These Source Refs First", ""])
    if deep_audit_followthrough_active:
        lines.append("- See `Audit Continuation Workflow.ordered_primary_refs_to_open_first` above.")
    elif scoped_no_evidence:
        lines.append("- No required primary source read; answer `not_found_in_searched_scope` with searched/not-searched/cap boundaries.")
        if source_action_guidance.get("diagnostic_policy"):
            lines.append(f"- Diagnostic policy: {source_action_guidance.get('diagnostic_policy')}")
    elif direct_evidence_complete and followthrough_required_reads:
        for read in followthrough_required_reads[:2]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    elif process_read_plan and process_read_plan.get("required_primary_raw_final_support_reads"):
        for read in process_read_plan.get("required_primary_raw_final_support_reads", [])[:4]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    elif primary_command:
        lines.append(f"- Primary source window command: `{primary_command}`")
        for source_ref_id in list(followthrough.get("required_final_source_ref_ids") or [])[:3]:
            lines.append(f"- Required final source ref: `{source_ref_id}`")
        for facet in list(followthrough.get("required_answer_facets") or [])[:6]:
            lines.append(f"- Required answer facet: {facet}")
        lines.append("- Fallback individual refs are listed later only if this window leaves a named answer facet missing.")
    elif required_reads:
        for read in required_reads[:5]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    elif next_reads_for_required_surfaces:
        for read in next_reads_for_required_surfaces[:5]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- No source refs surfaced.")
    lines.extend(["", "## Stop / Continue", "", f"- Stop condition: {stop_condition}"])
    only_continue = (
        []
        if deep_audit_followthrough_active
        else ((report.get("answer_ready_handoff") or {}).get("primary_source_followthrough") or {}).get("only_continue_if")
        or readiness.get("required_before_answer")
        or []
    )
    for item in only_continue[:4]:
        lines.append(f"- Only continue if: {item}")
    if scoped_no_evidence and source_action_guidance.get("retrieval_escalation_condition"):
        lines.append(f"- Retrieval escalation condition: {source_action_guidance.get('retrieval_escalation_condition')}")
    lines.extend(["", "## Required Proof Reads", ""])
    if required_reads:
        for read in required_reads[:8]:
            _append_markdown_read(lines, read, command_by_target=command_by_target)
    else:
        lines.append("- None listed.")
    lines.extend([
        "",
        "## Optional Diagnostics / Adjacent Evidence",
        "",
        f"**Query:** {report.get('query')}",
        f"**Primary family:** `{family.get('primary')}`",
        f"**Root cause:** not claimed",
        f"**Packet bytes/tokens:** {tradeoffs.get('packet_basis_bytes', 0)} / {tradeoffs.get('packet_basis_estimated_tokens', 0)}",
        "",
        "## Source Locator Handoff",
        "",
    ])
    workflow_summary = report.get("forensics_workflow_summary") or (report.get("forensics_workflow") or {}).get("summary")
    if workflow_summary:
        basis_counts = workflow_summary.get("join_basis_counts") or {}
        provenance = workflow_summary.get("provenance") or {}
        support_summary = workflow_summary.get("support_summary") or workflow_summary
        lines[4:4] = [
            f"**Forensics workflow:** phases={workflow_summary.get('phase_count', 0)} "
            f"joins={workflow_summary.get('join_count', 0)} "
            f"review_gate={workflow_summary.get('review_gate_status') or 'unknown'} "
            f"support={_format_support_counts(support_summary)} "
            f"join_basis={_format_count_map(basis_counts)} "
            f"provenance_commits={provenance.get('commit_count', 0)}",
            "",
        ]
    decision_context = report.get("decision_context") or report.get("decision_context_summary")
    if decision_context:
        lines.extend(
            [
                "## Decision Context",
                "",
                f"- `primary_question_family`: `{decision_context.get('primary_question_family')}`",
                f"- `project_time_reconstruction_needed`: `{decision_context.get('project_time_reconstruction_needed')}`",
                f"- `selected_routes`: `{', '.join(decision_context.get('selected_routes', []))}`",
            ]
        )
        confidence = decision_context.get("confidence") or {}
        if confidence:
            lines.append(f"- `confidence`: `{confidence.get('level')}`")
        lines.append("")
    audit_plan = report.get("audit_plan") or report.get("audit_plan_summary")
    if audit_plan:
        lines.extend(
            [
                "## Audit Plan",
                "",
                f"- `route_order`: `{', '.join(audit_plan.get('route_order', []))}`",
                f"- `watchlist_count`: `{len(audit_plan.get('watchlist', []))}`",
            ]
        )
        resume = audit_plan.get("resume_from") or {}
        if resume:
            lines.append(f"- `resume_from`: `{resume.get('next_read_target') or resume.get('route')}`")
        lines.append("")
    investigation_state = report.get("investigation_state")
    if investigation_state:
        lines.extend(render_investigation_state_markdown(investigation_state))
    if action_proof_plan and action_proof_plan.get("applies") and not suppress_action_required_reads:
        lines.extend(render_action_proof_plan_markdown(action_proof_plan, command_by_target=command_by_target))
    elif action_proof_plan and action_proof_plan.get("applies") and suppress_action_required_reads:
        lines.extend(
            [
                "## Action-Proof Plan",
                "",
                "- Fallback diagnostics suppressed because primary source followthrough is active for this provenance prompt.",
                "",
            ]
        )
    if report.get("source_locator_handoff"):
        lines.extend(render_source_locator_handoff_markdown(report["source_locator_handoff"]))
    if process_read_plan:
        _append_process_actual_use_read_plan_markdown(
            lines,
            process_read_plan,
            command_by_target=command_by_target,
        )
    handoff = report.get("answer_ready_handoff")
    if handoff:
        lines.extend([
            "",
            "## Answer-Ready Handoff",
            "",
            f"- `decision`: `{handoff.get('decision')}`",
            f"- `answer_readiness_status`: `{handoff.get('answer_readiness_status')}`",
            f"- `answer_gate_status`: `{handoff.get('answer_gate_status')}`",
            f"- `do_next`: `{handoff.get('do_next')}`",
        ])
        source_action = handoff.get("source_action_guidance") or {}
        if source_action:
            lines.extend(
                [
                    "",
                    "### Source Action Guidance",
                    "",
                    f"- `decision`: `{source_action.get('decision')}`",
                    f"- `required_primary_source_actions`: `{len(source_action.get('required_primary_source_actions') or [])}`",
                    f"- `optional_diagnostic_reads`: `{len(source_action.get('optional_diagnostic_reads') or [])}`",
                ]
            )
            if source_action.get("retrieval_escalation_condition"):
                lines.append(f"- `retrieval_escalation_condition`: {source_action.get('retrieval_escalation_condition')}")
        followthrough = handoff.get("primary_source_followthrough") or {}
        if followthrough.get("applies"):
            command = followthrough.get("first_command") or {}
            lines.extend(
                [
                    "",
                    "### Primary Source Followthrough",
                    "",
                    f"- `do_first`: `{followthrough.get('do_first')}`",
                    f"- `primary_read_count`: `{followthrough.get('primary_read_count')}`",
                    f"- `command_id`: `{command.get('command_id')}`",
                    f"- `target`: `{command.get('target')}`",
                    f"- `stop_after`: {followthrough.get('stop_after')}",
                ]
            )
            if command.get("target_absolute_path_like") or not command.get("target_local_path_like"):
                lines.append(f"- `command`: `{command.get('command')}`")
            else:
                lines.append("- `command`: omitted from Markdown output for relative/local target; use JSON field `first_command.command`")
            for facet in list(followthrough.get("required_answer_facets") or [])[:6]:
                lines.append(f"- `required_answer_facet`: {facet}")
            for item in (followthrough.get("only_continue_if") or [])[:3]:
                lines.append(f"- `only_continue_if`: {item}")
    contract = report.get("final_answer_source_use_contract")
    if contract:
        lines.extend([
            "",
            "## Final Answer Contract",
            "",
            f"- `answer_scope`: `{contract.get('answer_scope')}`",
            f"- `source_use_required`: `{contract.get('source_use_required')}`",
        ])
        reviewed_provenance = contract.get("required_final_provenance") or {}
        for source_ref_id in reviewed_provenance.get("required_source_ref_ids") or []:
            lines.append(f"- Required final provenance: `{source_ref_id}`")
        if reviewed_provenance.get("fail_closed_when_missing"):
            lines.append("- Final answer validation: fail closed when required reviewed provenance is missing.")
        for action in contract.get("required_actions", [])[:6]:
            lines.append(f"- {action}")
        process_contract = contract.get("process_actual_use_contract") or {}
        if process_contract:
            lines.append("- `process_actual_use`: primary raw evidence is required for behavior claims.")
            hierarchy = process_contract.get("source_hierarchy") or {}
            if hierarchy.get("secondary_artifact_rule"):
                lines.append(f"- {hierarchy.get('secondary_artifact_rule')}")
            for bucket in list(process_contract.get("bucket_requirements") or [])[:6]:
                lines.append(
                    f"- `actual_use_bucket`: `{bucket.get('bucket')}` (`{bucket.get('evidence_status')}`)"
                )
    if readiness:
        lines.extend([
            "",
            "## Answer Readiness",
            "",
            f"- `status`: `{readiness.get('status')}`",
            f"- `decision`: `{readiness.get('decision')}`",
            f"- `allowed_answer_scope`: `{readiness.get('allowed_answer_scope')}`",
        ])
        for action in readiness.get("required_before_answer", [])[:6]:
            lines.append(f"- {action}")
    instruction = report.get("agent_instruction")
    if instruction:
        lines.extend([
            "",
            "## Agent Instruction",
            "",
            f"- `decision`: `{instruction.get('decision')}`",
            f"- `required_next_action`: `{instruction.get('required_next_action')}`",
            f"- `command_count`: `{len(instruction.get('commands', []))}`",
        ])
        for command in instruction.get("commands", [])[:5]:
            rendered = _action_command(command)
            if rendered:
                lines.append(f"- `command`: `{rendered}`")
    lines.extend([
        "",
        "## Strongest Candidates",
    ])
    for item in report.get("strongest_candidates", [])[:8]:
        ref = item.get("source_ref") or {}
        classification = item.get("post_hoc_source_classification") or {}
        evidence_role = (
            " evidence_role=diagnostic_post_hoc_not_target_proof final_support_allowed=False"
            if classification.get("likely_post_hoc_diagnostic")
            else ""
        )
        lines.append(
            f"- `{item.get('candidate_id')}` {source_ref_target(ref) or ''} "
            f"confidence={item.get('confidence')} stages={','.join(item.get('stages', []))}"
            f"{evidence_role}"
        )
    lines.append("")
    lines.append("## Next Reads")
    followthrough = (report.get("answer_ready_handoff") or {}).get("primary_source_followthrough") or {}
    if scoped_no_evidence:
        lines.append("These are optional diagnostics, not required before the scoped no-evidence answer.")
        for item in report.get("next_reads", [])[:8]:
            _append_markdown_read_summary(lines, item)
    elif deep_audit_followthrough_active:
        lines.append("- Suppressed for deep-history audit follow-through.")
        lines.append(
            "- Open `ordered_primary_refs_to_open_first` first; do not use broad search or extra `read-more` unless a named facet remains missing."
        )
        lines.append(f"- Additional `next_reads` preserved in JSON/report: `{len(report.get('next_reads', []) or [])}`")
    elif followthrough.get("applies"):
        lines.append(
            "- Executable fallback commands suppressed in Markdown output; use JSON `next_reads` only after a named facet gap."
        )
        if direct_evidence_complete:
            secondary_count = sum(
                1
                for item in report.get("next_reads", []) or []
                if isinstance(item, dict) and item.get("secondary_context_role")
            )
            lines.append(
                f"- Secondary recap/summary/specification targets suppressed after complete direct evidence: `{secondary_count}`."
            )
        else:
            for item in report.get("next_reads", [])[:8]:
                _append_markdown_read_summary(lines, item)
    else:
        for item in report.get("next_reads", [])[:8]:
            _append_markdown_read(lines, item, command_by_target=command_by_target)
    lines.append("")
    lines.append("## Uncertainty")
    for item in report.get("uncertainty", [])[:8]:
        lines.append(f"- `{item.get('label')}` {item.get('meaning')}")
    return "\n".join(lines)


def render_investigation_state_text(investigation_state: dict) -> list[str]:
    question_intent = investigation_state.get("question_intent") or {}
    retrieval = investigation_state.get("retrieval") or {}
    recommendation = retrieval.get("conservative_recommendation") or {}
    actionable_next = investigation_state.get("actionable_next") or {}
    unresolved_gaps = investigation_state.get("unresolved_gaps") or {}
    answer_readiness = investigation_state.get("answer_readiness") or {}
    claim_boundaries = retrieval.get("claim_boundaries") or {}
    source_boundaries = investigation_state.get("source_boundary_reminders") or {}

    lines = [
        "Investigation state: "
        f"status={investigation_state.get('status')} "
        f"family={question_intent.get('primary_question_family')} "
        f"intent={question_intent.get('primary_intent')} "
        f"mode_used={retrieval.get('mode_used')} "
        f"recommended_mode={recommendation.get('mode')} "
        f"next={answer_readiness.get('recommended_next_step')} "
        f"read_next={actionable_next.get('read_next_count', 0)} "
        f"gaps={unresolved_gaps.get('gap_count', 0)}",
    ]
    gap_labels = _investigation_gap_labels(unresolved_gaps)
    if gap_labels:
        lines.append(f"- gaps: {', '.join(gap_labels)}")
    lines.append(
        "Source/claim boundaries: "
        f"searched_scope_only={_investigation_nested(source_boundaries, 'no_evidence', 'searched_scope_only')} "
        f"adjacent_artifacts_present={_investigation_nested(source_boundaries, 'adjacent_artifacts', 'present')} "
        f"primary_source_rules={len(source_boundaries.get('primary_sources') or [])} "
        f"forbidden_claims={len(source_boundaries.get('forbidden_claims') or [])} "
        f"ranking_changed={claim_boundaries.get('ranking_changed_by_investigation_state')} "
        f"rrf_default_promoted={claim_boundaries.get('rrf_default_promoted')} "
        f"rrf_default_allowed={recommendation.get('rrf_default_allowed')}"
    )
    return lines


def render_investigation_finalization_state_text(finalization_state: dict) -> list[str]:
    if not isinstance(finalization_state, dict) or not finalization_state.get("state"):
        return []
    state = str(finalization_state.get("state") or "")
    canonical_next = finalization_state.get("canonical_next_action") or {}
    command = canonical_next.get("command")
    progress = finalization_state.get("evidence_progress") or {}
    boundedness = finalization_state.get("boundedness") or {}
    lines = [
        "Investigation finalization: "
        f"state={state} "
        f"answer_ready={finalization_state.get('answer_ready')} "
        f"terminal={finalization_state.get('terminal')} "
        f"receiver_action={finalization_state.get('required_receiver_action')}",
    ]
    if state == "answer_ready" and finalization_state.get("answer_promotion_allowed") is True:
        lines.append("- Receiver action: answer-ready; stop broad searching and answer only from opened primary proof.")
        return lines
    if state == "terminal_scoped_no_evidence" and finalization_state.get("answer_promotion_allowed") is True:
        lines.append(
            "- Receiver action: terminal scoped stop; answer `not_found_in_searched_scope` with searched/not-searched/cap boundaries."
        )
        return lines
    if finalization_state.get("terminal"):
        lines.append("- Receiver action: terminal blocker; report the scoped blocker instead of running fresh broad search.")
        for blocker in list(finalization_state.get("blockers") or [])[:3]:
            if isinstance(blocker, dict):
                lines.append(f"- blocker: {blocker.get('kind') or blocker}")
        return lines

    lines.append("- Receiver action: not answer-ready; do not synthesize; continue with the canonical bounded action.")
    if command:
        lines.append(f"- Canonical next command: {command}")
    else:
        lines.append(f"- Canonical next action: {canonical_next.get('kind') or '(none emitted)'}")
    lines.append(
        "- Continue boundary: do not rerun broad ask/search or use optional diagnostics as answer material; "
        "continue only until this state becomes answer_ready or terminal."
    )
    if progress:
        lines.append(
            "Evidence progress: "
            f"required={progress.get('required_primary_total')} "
            f"uninspected={progress.get('required_primary_uninspected_count')} "
            f"exhausted={progress.get('required_primary_exhausted_count')}"
        )
    if boundedness:
        lines.append(
            "Finalization controls: "
            f"broad_search_rerun_allowed={boundedness.get('broad_search_rerun_allowed')} "
            f"optional_diagnostics_default_actionable={boundedness.get('optional_diagnostics_default_actionable')}"
        )
    return lines


def render_investigation_finalization_state_markdown(finalization_state: dict) -> list[str]:
    if not isinstance(finalization_state, dict) or not finalization_state.get("state"):
        return []
    state = str(finalization_state.get("state") or "")
    canonical_next = finalization_state.get("canonical_next_action") or {}
    command = canonical_next.get("command")
    progress = finalization_state.get("evidence_progress") or {}
    boundedness = finalization_state.get("boundedness") or {}
    lines = [
        "",
        "## Investigation Finalization",
        "",
        f"- `state`: `{state}`",
        f"- `answer_ready`: `{finalization_state.get('answer_ready')}`",
        f"- `terminal`: `{finalization_state.get('terminal')}`",
        f"- `required_receiver_action`: `{finalization_state.get('required_receiver_action')}`",
    ]
    if state == "answer_ready" and finalization_state.get("answer_promotion_allowed") is True:
        lines.append("- Receiver action: answer-ready; stop broad searching and answer only from opened primary proof.")
        return lines
    if state == "terminal_scoped_no_evidence" and finalization_state.get("answer_promotion_allowed") is True:
        lines.append(
            "- Receiver action: terminal scoped stop; answer `not_found_in_searched_scope` with searched/not-searched/cap boundaries."
        )
        return lines
    if finalization_state.get("terminal"):
        lines.append("- Receiver action: terminal blocker; report the scoped blocker instead of running fresh broad search.")
        for blocker in list(finalization_state.get("blockers") or [])[:3]:
            if isinstance(blocker, dict):
                lines.append(f"- `blocker`: `{blocker.get('kind') or blocker}`")
        return lines

    lines.append("- Receiver action: not answer-ready; do not synthesize; continue with the canonical bounded action.")
    if command:
        lines.extend(["", "```bash", str(command), "```"])
    else:
        lines.append(f"- `canonical_next_action`: `{canonical_next.get('kind') or '(none emitted)'}`")
    lines.append(
        "- Continue boundary: do not rerun broad `ask`/search or use optional diagnostics as answer material; "
        "continue only until this state becomes `answer_ready` or terminal."
    )
    if progress:
        lines.append(
            "- `evidence_progress`: "
            f"required=`{progress.get('required_primary_total')}` "
            f"uninspected=`{progress.get('required_primary_uninspected_count')}` "
            f"exhausted=`{progress.get('required_primary_exhausted_count')}`"
        )
    if boundedness:
        lines.append(
            "- `finalization_controls`: "
            f"broad_search_rerun_allowed=`{boundedness.get('broad_search_rerun_allowed')}`; "
            f"optional_diagnostics_default_actionable=`{boundedness.get('optional_diagnostics_default_actionable')}`"
        )
    return lines


def render_investigation_state_markdown(investigation_state: dict) -> list[str]:
    question_intent = investigation_state.get("question_intent") or {}
    retrieval = investigation_state.get("retrieval") or {}
    recommendation = retrieval.get("conservative_recommendation") or {}
    actionable_next = investigation_state.get("actionable_next") or {}
    unresolved_gaps = investigation_state.get("unresolved_gaps") or {}
    answer_readiness = investigation_state.get("answer_readiness") or {}
    claim_boundaries = retrieval.get("claim_boundaries") or {}
    source_boundaries = investigation_state.get("source_boundary_reminders") or {}

    lines = [
        "## Investigation State",
        "",
        f"- `status`: `{investigation_state.get('status')}`",
        f"- `primary_question_family`: `{question_intent.get('primary_question_family')}`",
        f"- `primary_intent`: `{question_intent.get('primary_intent')}`",
        f"- `mode_used`: `{retrieval.get('mode_used')}`",
        f"- `recommended_mode`: `{recommendation.get('mode')}`",
        f"- `recommended_next_step`: `{answer_readiness.get('recommended_next_step')}`",
        f"- `read_next_count`: `{actionable_next.get('read_next_count', 0)}`",
        f"- `gap_count`: `{unresolved_gaps.get('gap_count', 0)}`",
    ]
    gap_labels = _investigation_gap_labels(unresolved_gaps)
    if gap_labels:
        lines.append(f"- `gap_labels`: `{', '.join(gap_labels)}`")
    lines.extend(
        [
            (
                "- `source_boundaries`: "
                f"searched_scope_only=`{_investigation_nested(source_boundaries, 'no_evidence', 'searched_scope_only')}`; "
                f"adjacent_artifacts_present=`{_investigation_nested(source_boundaries, 'adjacent_artifacts', 'present')}`; "
                f"primary_source_rules=`{len(source_boundaries.get('primary_sources') or [])}`; "
                f"forbidden_claims=`{len(source_boundaries.get('forbidden_claims') or [])}`"
            ),
            (
                "- `claim_boundaries`: "
                f"ranking_changed=`{claim_boundaries.get('ranking_changed_by_investigation_state')}`; "
                f"rrf_default_promoted=`{claim_boundaries.get('rrf_default_promoted')}`; "
                f"rrf_default_allowed=`{recommendation.get('rrf_default_allowed')}`"
            ),
            "",
        ]
    )
    return lines


def render_action_proof_plan_text(plan: dict, *, command_by_target: dict[str, str] | None = None) -> list[str]:
    retrieval = plan.get("retrieval_mode_guidance") or {}
    package = retrieval.get("package") or {}
    fielded = retrieval.get("fielded_bm25") or {}
    rrf = retrieval.get("rrf_fusion") or {}
    primary_refs = _required_surface_reads(plan.get("primary_proof_refs") or [])
    required_reads = _required_surface_reads(plan.get("required_proof_reads") or [])
    optional = plan.get("optional_diagnostics") or []
    lines = [
        "Action-proof plan: "
        f"status={plan.get('status')} "
        f"primary_refs={len(primary_refs)} "
        f"required_reads={len(required_reads)} "
        f"optional_diagnostics={len(optional)} "
        f"current_mode={retrieval.get('current_mode')} "
        f"recommended_mode={retrieval.get('recommended_mode')}",
        f"- do_first: {plan.get('do_first')}",
    ]
    for ref in primary_refs[:3]:
        lines.append(
            "- primary proof ref: "
            f"{ref.get('target') or source_ref_target(ref.get('source_ref') or {})} "
            f"role={ref.get('proof_role')} required={ref.get('required')}"
        )
    for read in required_reads[:3]:
        lines.append(
            "- required proof read: "
            f"{read.get('target') or source_ref_target(read.get('source_ref') or {})} "
            f"type={read.get('read_type')}"
        )
        command = _read_more_command(read, command_by_target)
        if command:
            lines.append(f"  command: {command}")
    if optional:
        lines.append(f"- optional diagnostics: {len(optional)} after primary proof is thin/gapped or timeline synthesis is required")
    lines.append(f"- stop broad search: {plan.get('stop_condition')}")
    lines.append(
        "- retrieval modes: "
        f"package_default_ok={package.get('default_ok')} "
        f"fielded_bm25_required_now={fielded.get('required_now')} "
        f"fielded_command_available={bool(fielded.get('rerun_command'))} "
        f"rrf_explicit_opt_in_only={rrf.get('explicit_opt_in_only')}"
    )
    return lines


def render_action_proof_plan_markdown(plan: dict, *, command_by_target: dict[str, str] | None = None) -> list[str]:
    retrieval = plan.get("retrieval_mode_guidance") or {}
    package = retrieval.get("package") or {}
    fielded = retrieval.get("fielded_bm25") or {}
    rrf = retrieval.get("rrf_fusion") or {}
    primary_refs = _required_surface_reads(plan.get("primary_proof_refs") or [])
    required_reads = _required_surface_reads(plan.get("required_proof_reads") or [])
    optional = plan.get("optional_diagnostics") or []
    lines = [
        "## Action-Proof Plan",
        "",
        f"- `status`: `{plan.get('status')}`",
        f"- `do_first`: `{plan.get('do_first')}`",
        f"- `primary_proof_ref_count`: `{len(primary_refs)}`",
        f"- `required_proof_read_count`: `{len(required_reads)}`",
        f"- `optional_diagnostic_count`: `{len(optional)}`",
        f"- `stop_broad_search`: {plan.get('stop_condition')}",
        (
            "- `retrieval_modes`: "
            f"package_default_ok=`{package.get('default_ok')}`; "
            f"fielded_bm25_required_now=`{fielded.get('required_now')}`; "
            f"fielded_command_available=`{bool(fielded.get('rerun_command'))}`; "
            f"rrf_explicit_opt_in_only=`{rrf.get('explicit_opt_in_only')}`"
        ),
    ]
    if primary_refs:
        lines.append("- `primary_proof_refs`:")
        for ref in primary_refs[:3]:
            target = ref.get("target") or source_ref_target(ref.get("source_ref") or {})
            lines.append(f"  - `{target}` proof_role=`{ref.get('proof_role')}` required=`{ref.get('required')}`")
    if required_reads:
        lines.append("- `required_proof_reads`:")
        for read in required_reads[:3]:
            target = read.get("target") or source_ref_target(read.get("source_ref") or {})
            lines.append(f"  - `{target}` read_type=`{read.get('read_type')}`")
            command = _read_more_command(read, command_by_target)
            if command:
                lines.append(f"    - Command: `{command}`")
    return lines + [""]


def _investigation_gap_labels(unresolved_gaps: dict) -> list[str]:
    labels: list[str] = []
    for item in (unresolved_gaps.get("items") or [])[:4]:
        label = item.get("label")
        if label:
            labels.append(str(label))
    return labels


def _investigation_nested(source: dict, first: str, second: str) -> object:
    value = source.get(first) or {}
    if not isinstance(value, dict):
        return None
    return value.get(second)


def render_jsonl_candidate_discovery_text(payload: dict) -> str:
    report = payload["jsonl_candidate_discovery"]
    summary = report.get("summary", {})
    cap_hits = summary.get("cap_hits", {})
    lines = [
        "JSONL Candidate Discovery",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{summary.get('selected_root_count', 0)} selected roots, "
            f"{summary.get('rejected_root_count', 0)} rejected roots"
        ),
        (
            "Candidates: "
            f"{summary.get('candidate_count', 0)} files, "
            f"{summary.get('candidate_bytes', 0)} bytes"
        ),
        (
            "Cap hits: "
            f"file={cap_hits.get('file', False)} "
            f"byte={cap_hits.get('byte', False)} "
            f"elapsed={cap_hits.get('elapsed', False)}"
        ),
    ]
    for root in report.get("scope", {}).get("selected_roots", []):
        lines.append(
            "- selected "
            f"{root.get('root_label')} {root.get('kind')} "
            f"known={root.get('known')} recursive={root.get('recursive')} "
            f"hash={root.get('root_hash')}"
        )
    for rejected in report.get("scope", {}).get("rejected_roots", []):
        lines.append(f"- rejected {rejected.get('root_label')}: {rejected.get('reason')}")
    for candidate in report.get("candidates", [])[:12]:
        lines.append(
            "- "
            f"{candidate.get('candidate_id')} {candidate.get('root_label')} "
            f"{candidate.get('source_kind')} {candidate.get('suffix')} "
            f"size={candidate.get('file_size_bytes')} "
            f"mtime={candidate.get('mtime_utc')} "
            f"{candidate.get('path_label')}"
        )
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for warning in report.get("privacy_warnings", []):
        lines.append(f"Privacy: {warning}")
    for item in report.get("scope", {}).get("not_searched_scope", []):
        lines.append(f"Not searched: {item}")
    return "\n".join(lines)


def render_jsonl_candidate_discovery_markdown(payload: dict) -> str:
    report = payload["jsonl_candidate_discovery"]
    summary = report.get("summary", {})
    cap_hits = summary.get("cap_hits", {})
    lines = [
        "# JSONL Candidate Discovery",
        "",
        f"**Status:** `{report.get('status')}`",
        (
            "**Scope:** "
            f"{summary.get('selected_root_count', 0)} selected roots, "
            f"{summary.get('rejected_root_count', 0)} rejected roots"
        ),
        (
            "**Candidates:** "
            f"{summary.get('candidate_count', 0)} files, "
            f"{summary.get('candidate_bytes', 0)} bytes"
        ),
        (
            "**Cap hits:** "
            f"file=`{cap_hits.get('file', False)}`, "
            f"byte=`{cap_hits.get('byte', False)}`, "
            f"elapsed=`{cap_hits.get('elapsed', False)}`"
        ),
        "",
        "## Roots",
        "",
    ]
    selected_roots = report.get("scope", {}).get("selected_roots", [])
    rejected_roots = report.get("scope", {}).get("rejected_roots", [])
    if selected_roots:
        lines.extend(["| Label | Kind | Known | Recursive | Hash |", "| --- | --- | --- | --- | --- |"])
        for root in selected_roots:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(root.get('root_label')))} | "
                f"{_escape_markdown_table(str(root.get('kind')))} | "
                f"{root.get('known')} | "
                f"{root.get('recursive')} | "
                f"`{_escape_markdown_table(str(root.get('root_hash')))}` |"
            )
    else:
        lines.append("- No selected roots.")
    if rejected_roots:
        lines.extend(["", "### Rejected Roots", ""])
        for rejected in rejected_roots:
            lines.append(f"- `{rejected.get('root_label')}`: {rejected.get('reason')}")
    lines.extend(["", "## Candidates", ""])
    candidates = report.get("candidates", [])
    if candidates:
        lines.extend(
            [
                "| ID | Root | Source | Type | Size | Mtime UTC | Path label |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for candidate in candidates[:50]:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(candidate.get('candidate_id')))} | "
                f"{_escape_markdown_table(str(candidate.get('root_label')))} | "
                f"{_escape_markdown_table(str(candidate.get('source_kind')))} | "
                f"{_escape_markdown_table(str(candidate.get('file_type')))} | "
                f"{candidate.get('file_size_bytes')} | "
                f"{_escape_markdown_table(str(candidate.get('mtime_utc')))} | "
                f"`{_escape_markdown_table(str(candidate.get('path_label')))}` |"
            )
    else:
        lines.append("- No candidate files found in selected roots.")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in report.get("warnings", []))
    lines.extend(["", "## Privacy And Limits", ""])
    lines.extend(f"- {warning}" for warning in report.get("privacy_warnings", []))
    lines.extend(f"- {item}" for item in report.get("scope", {}).get("not_searched_scope", []))
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    return "\n".join(lines)


def render_capability_diagnostic_text(payload: dict) -> str:
    report = payload["capability_diagnostic"]
    summary = report.get("summary", {})
    lines = [
        "Capability Diagnostic",
        f"Backend: {report.get('backend_selected')} (tier {report.get('fallback_tier')})",
        "Summary: "
        f"available={summary.get('available', 0)} "
        f"degraded={summary.get('degraded', 0)} "
        f"missing={summary.get('missing', 0)} "
        f"blocked={summary.get('blocked', 0)} "
        f"not_implemented={summary.get('not_implemented', 0)}",
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    cookbook = report.get("agent_command_cookbook") or []
    if cookbook:
        lines.append("Agent command cookbook:")
        for item in cookbook:
            lines.append(f"- {item.get('question_family')}: {item.get('first_command')}")
            lines.append(f"  Inspect: {item.get('inspect_next')}")
    source_plan = report.get("source_plan", {})
    lines.append(
        "Source plan: "
        f"{source_plan.get('selected_root_count', 0)} selected, "
        f"{source_plan.get('rejected_root_count', 0)} rejected, "
        f"{source_plan.get('missing_root_count', 0)} missing"
    )
    for root in source_plan.get("roots", [])[:5]:
        state = "readable" if root.get("readable") else "missing/unreadable"
        lines.append(f"- root {root.get('kind')}: {root.get('label')} ({state})")
    for rejected in source_plan.get("rejected_roots", [])[:5]:
        lines.append(f"- rejected {rejected.get('label')}: {rejected.get('reason')}")
    archive = report.get("archive_protection", {})
    if archive:
        cap_status = archive.get("cap_status", {})
        lines.append("Archive protection:")
        lines.append(
            "- "
            f"status={archive.get('status')} "
            f"protection={archive.get('protection_status')} "
            f"configured={archive.get('archive_configured')}"
        )
        lines.append(
            "- "
            f"selected_labels={', '.join(archive.get('selected_source_labels', [])) or '(none)'} "
            f"estimated_files={archive.get('estimated_selected_file_count', 0)} "
            f"estimated_bytes={archive.get('estimated_selected_bytes', 0)}"
        )
        lines.append(
            "- "
            f"caps file={cap_status.get('file', False)} "
            f"byte={cap_status.get('byte', False)} "
            f"elapsed={cap_status.get('elapsed', False)}"
        )
        for warning in archive.get("mounted_drive_warnings", []):
            lines.append(f"  Mounted-drive warning: {warning}")
        for warning in archive.get("privacy_warnings", []):
            lines.append(f"  Privacy: {warning}")
        for note in archive.get("duplicate_risk_notes", []):
            lines.append(f"  Duplicate risk: {note}")

    capabilities = report.get("capabilities", {})
    for status in ("available", "degraded", "missing", "blocked", "not_implemented"):
        items = [item for item in capabilities.values() if item.get("status") == status]
        if not items:
            continue
        heading = "Missing/not implemented" if status == "not_implemented" else status.replace("_", " ").title()
        lines.append(f"{heading}:")
        for item in items:
            lines.append(
                "- "
                f"{item.get('id')}: {item.get('name')} | "
                f"fallback={item.get('fallback')} | "
                f"next={item.get('suggested_next_action')}"
            )
            loss = item.get("expected_slowdown_or_lost_feature")
            if loss and status != "available":
                lines.append(f"  Impact: {loss}")
    gates = report.get("benchmark_gates", {})
    if gates:
        lines.append(f"Benchmark gates: default_promotion_ready={gates.get('default_promotion_ready')}")
        lines.append(f"Gate reason: {gates.get('reason')}")
    archive_gate = report.get("archive_protection_gate", {})
    if archive_gate:
        lines.append(
            "Archive protection gate: "
            f"{archive_gate.get('label')} status={archive_gate.get('status')} "
            f"promotion_ready={archive_gate.get('promotion_ready')}"
        )
    return "\n".join(lines)


def render_capability_diagnostic_markdown(payload: dict) -> str:
    report = payload["capability_diagnostic"]
    summary = report.get("summary", {})
    lines = [
        "# Capability Diagnostic",
        "",
        f"**Backend:** {report.get('backend_selected')} (tier {report.get('fallback_tier')})",
        (
            "**Summary:** "
            f"available={summary.get('available', 0)}, "
            f"degraded={summary.get('degraded', 0)}, "
            f"missing={summary.get('missing', 0)}, "
            f"blocked={summary.get('blocked', 0)}, "
            f"not_implemented={summary.get('not_implemented', 0)}"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"**Report written:** {report['report_written']}")
    cookbook = report.get("agent_command_cookbook") or []
    if cookbook:
        lines.extend(["", "## Agent Command Cookbook", ""])
        for item in cookbook[:6]:
            lines.append(f"- **{item.get('question_family')}**: `{item.get('first_command')}`")
            lines.append(f"  Inspect: {item.get('inspect_next')}")
    lines.extend(["", "## Capabilities", ""])
    for item in report.get("capabilities", {}).values():
        lines.append(
            f"- **{item.get('id')}**: `{item.get('status')}`; "
            f"fallback `{item.get('fallback')}`; {item.get('suggested_next_action')}"
        )
    lines.extend(["", "## Source Plan", ""])
    source_plan = report.get("source_plan", {})
    lines.append(
        f"- selected={source_plan.get('selected_root_count', 0)}, "
        f"rejected={source_plan.get('rejected_root_count', 0)}, "
        f"missing={source_plan.get('missing_root_count', 0)}"
    )
    for root in source_plan.get("roots", [])[:5]:
        lines.append(f"- {root.get('kind')}: `{root.get('label')}` readable={root.get('readable')}")
    for rejected in source_plan.get("rejected_roots", [])[:5]:
        lines.append(f"- rejected `{rejected.get('label')}`: {rejected.get('reason')}")
    archive = report.get("archive_protection", {})
    if archive:
        cap_status = archive.get("cap_status", {})
        lines.extend(
            [
                "",
                "## Archive Protection",
                "",
                f"- status=`{archive.get('status')}`; protection=`{archive.get('protection_status')}`; configured=`{archive.get('archive_configured')}`",
                f"- selected source labels: {', '.join(archive.get('selected_source_labels', [])) or '(none)'}",
                f"- estimated selected files={archive.get('estimated_selected_file_count', 0)}; estimated selected bytes={archive.get('estimated_selected_bytes', 0)}",
                f"- caps: file=`{cap_status.get('file', False)}`, byte=`{cap_status.get('byte', False)}`, elapsed=`{cap_status.get('elapsed', False)}`",
            ]
        )
        for warning in archive.get("mounted_drive_warnings", []):
            lines.append(f"- mounted-drive warning: {warning}")
        for warning in archive.get("privacy_warnings", []):
            lines.append(f"- privacy: {warning}")
        for note in archive.get("duplicate_risk_notes", []):
            lines.append(f"- duplicate risk: {note}")
    archive_gate = report.get("archive_protection_gate", {})
    if archive_gate:
        lines.extend(
            [
                "",
                "## Archive Protection Gate",
                "",
                f"- label=`{archive_gate.get('label')}`; status=`{archive_gate.get('status')}`; promotion_ready=`{archive_gate.get('promotion_ready')}`",
                f"- restore/export smoke: {archive_gate.get('restore_export_acceptance_test')}",
            ]
        )
    return "\n".join(lines)


def render_archive_snapshot_proof_text(payload: dict) -> str:
    report = payload["archive_snapshot_proof"]
    snapshot = report.get("snapshot", {})
    gate = report.get("archive_protection_gate", {})
    duplicate = report.get("duplicate_detection", {})
    export = report.get("export_restore_smoke", {})
    hash_verification = report.get("hash_verification", {})
    lines = [
        "Archive Snapshot Proof",
        f"Status: {report.get('status')}",
        f"Scope: {report.get('scope')}",
        (
            "Snapshot: "
            f"files={snapshot.get('source_file_count', 0)} "
            f"bytes={snapshot.get('source_bytes', 0)} "
            f"objects={snapshot.get('archive_object_count', 0)} "
            f"index_bytes={snapshot.get('index_bytes', 0)}"
        ),
        (
            "Verification: "
            f"hash={hash_verification.get('status')} "
            f"export={export.get('status')} "
            f"duplicates={duplicate.get('status')}"
        ),
        (
            "Gate: "
            f"{gate.get('label')} status={gate.get('status')} "
            f"promotion_ready={gate.get('promotion_ready')} "
            f"claim_allowed={gate.get('claim_allowed')}"
        ),
    ]
    for group in duplicate.get("groups", []):
        lines.append(f"- duplicate {group.get('group_id')}: {', '.join(group.get('source_ids', []))}")
    for status, count in sorted((hash_verification.get("status_counts") or {}).items()):
        lines.append(f"- status {status}: {count}")
    status_handling = report.get("status_handling", {})
    for probe in status_handling.get("probes", []):
        lines.append(
            "- probe "
            f"{probe.get('probe')}: expected={probe.get('expected_status')} "
            f"observed={probe.get('status')} matched={probe.get('matched')}"
        )
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for error in report.get("errors", []):
        lines.append(f"Error: {error}")
    return "\n".join(lines)


def render_archive_snapshot_proof_markdown(payload: dict) -> str:
    report = payload["archive_snapshot_proof"]
    snapshot = report.get("snapshot", {})
    gate = report.get("archive_protection_gate", {})
    duplicate = report.get("duplicate_detection", {})
    export = report.get("export_restore_smoke", {})
    hash_verification = report.get("hash_verification", {})
    lines = [
        "# Archive Snapshot Proof",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Scope:** `{report.get('scope')}`",
        f"**Promotion ready:** `{report.get('promotion_ready')}`",
        "",
        "## Snapshot",
        "",
        f"- files=`{snapshot.get('source_file_count', 0)}`; bytes=`{snapshot.get('source_bytes', 0)}`; objects=`{snapshot.get('archive_object_count', 0)}`",
        f"- bytes copied=`{snapshot.get('bytes_copied', 0)}`; bytes compressed=`{snapshot.get('bytes_compressed', 0)}`; index bytes=`{snapshot.get('index_bytes', 0)}`",
        "",
        "## Verification",
        "",
        f"- hash verification=`{hash_verification.get('status')}`",
        f"- export/restore smoke=`{export.get('status')}`",
        f"- duplicate detection=`{duplicate.get('status')}` groups=`{duplicate.get('duplicate_group_count', 0)}`",
        "",
        "## Status Handling",
        "",
    ]
    probes = report.get("status_handling", {}).get("probes", [])
    if probes:
        lines.extend(["| Probe | Expected | Observed | Matched |", "| --- | --- | --- | --- |"])
        for probe in probes:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(probe.get('probe')))} | "
                f"{_escape_markdown_table(str(probe.get('expected_status')))} | "
                f"{_escape_markdown_table(str(probe.get('status')))} | "
                f"{probe.get('matched')} |"
            )
    else:
        lines.append("- Status probes were not enabled.")
    lines.extend(
        [
            "",
            "## Archive Protection Gate",
            "",
            f"- label=`{gate.get('label')}`; status=`{gate.get('status')}`; promotion_ready=`{gate.get('promotion_ready')}`; claim_allowed=`{gate.get('claim_allowed')}`",
            f"- evidence basis=`{gate.get('evidence_basis')}`",
        ]
    )
    if gate.get("remaining_blockers"):
        for blocker in gate["remaining_blockers"]:
            lines.append(f"- blocker: `{blocker}`")
    for error in report.get("errors", []):
        lines.append(f"- error: {error}")
    return "\n".join(lines)


def render_forensics_text(payload: dict) -> str:
    forensic = payload["forensics"]
    git = payload.get("git", {})
    jsonl = payload.get("jsonl_evidence", {})
    lines = [
        "Forensics",
        f"Query: {forensic.get('query') or '(none)'}",
        f"Confidence: {forensic.get('confidence')} ({', '.join(forensic.get('confidence_reasons', []))})",
    ]
    hints = forensic.get("hints", {})
    hint_text = ", ".join(f"{key}={value}" for key, value in hints.items() if value)
    lines.append(f"Hints: {hint_text or '(none)'}")
    if forensic.get("around_window_minutes") is not None:
        lines.append(f"Around window: +/- {forensic['around_window_minutes']} minutes")

    provenance = forensic.get("provenance_lifecycle") or {}
    provenance_summary = provenance.get("summary") or {}
    if provenance_summary:
        lines.append(
            "Provenance lifecycle: "
            f"commits={provenance_summary.get('commit_count', 0)} "
            f"branches={provenance_summary.get('branch_count', 0)} "
            f"tasks={provenance_summary.get('task_artifact_count', 0)} "
            f"reviews={provenance_summary.get('review_artifact_count', 0)} "
            f"artifacts={provenance_summary.get('artifact_count', 0)} "
            f"worktrees={provenance_summary.get('worktree_count', 0)} "
            f"review_gate={provenance_summary.get('review_gate_status') or 'unknown'}"
        )
        basis_counts = provenance_summary.get("join_basis_counts") or {}
        if basis_counts:
            lines.append(f"Join basis: {_format_count_map(basis_counts)}")
        support_summary = provenance_summary.get("support_summary") or forensic.get("support_summary") or {}
        if support_summary:
            lines.append(f"Support precision: {_format_support_counts(support_summary)}")
    git_lifecycle = provenance.get("git_lifecycle") or {}
    lifecycle_commits = git_lifecycle.get("commits") or []
    if lifecycle_commits:
        order = git_lifecycle.get("commit_order") or "newest_first"
        lines.append(
            "Git lifecycle commits: "
            f"{len(lifecycle_commits)} shown order={order} bounded={git_lifecycle.get('bounded')}"
        )
        for item in lifecycle_commits[:5]:
            commit_ref = str(item.get("commit") or "")[:12]
            lines.append(f"- lifecycle commit {commit_ref} {item.get('commit_time')} {item.get('subject')}")

    review_gate = forensic.get("review_gate") or {}
    if review_gate:
        lines.append(
            "Review gate: "
            f"{review_gate.get('status')} "
            f"verdict={review_gate.get('verdict') or 'unknown'} "
            f"confidence={review_gate.get('confidence')}"
        )
        if review_gate.get("merge_commit"):
            lines.append(f"Review gate merge commit: {review_gate['merge_commit']}")
        for blocker in review_gate.get("blockers", [])[:5]:
            lines.append(f"- review blocker {blocker.get('path')}: {blocker.get('verdict')}")

    phases = forensic.get("phase_timeline") or []
    if phases:
        lines.append("Phase timeline:")
        lines.append("  id | phase | evidence | action | support | confidence | source")
        for phase in phases[:12]:
            source = _compact_phase_source(phase.get("source_ref") or {})
            lines.append(
                "  "
                f"{phase.get('phase_id')} | "
                f"{phase.get('phase_kind')} | "
                f"{phase.get('evidence_kind')} | "
                f"{phase.get('claim_action')} | "
                f"{phase.get('support_status') or 'unknown'} | "
                f"{phase.get('confidence')} | "
                f"{source}"
            )
        if len(phases) > 12:
            lines.append(f"  ... {len(phases) - 12} more phases omitted from text output")

    diagnostics = forensic.get("diagnostics") or {}
    if diagnostics:
        lines.append(
            "Forensics diagnostics: "
            f"phases={sum((diagnostics.get('phase_counts') or {}).values())} "
            f"joins={diagnostics.get('join_count', 0)} "
            f"support={_format_support_counts(diagnostics.get('support_summary') or diagnostics)} "
            f"unsupported_claims={diagnostics.get('unsupported_claim_count', 0)} "
            f"missing={','.join(diagnostics.get('missing_evidence_flags', [])) or 'none'}"
        )

    lines.append(f"Git repo: {git.get('repo') or '(none)'}")
    if git.get("current_branch"):
        lines.append(f"Git current branch: {git['current_branch']}")
    commit = git.get("commit")
    if commit:
        lines.append(f"Commit: {commit['commit']} {commit['commit_time']} {commit['subject']}")
        for changed in commit.get("changed_files", []):
            lines.append(f"- changed {changed.get('status')}: {changed.get('path')}")
    if git.get("path_last_change"):
        change = git["path_last_change"]
        lines.append(f"Path last change: {change['commit']} {change['commit_time']} {change['path']} {change['subject']}")
    if git.get("branch"):
        branch = git["branch"]
        lines.append(f"Branch: {branch['name']} -> {branch['head']}")

    lines.append(
        f"JSONL searched: {jsonl.get('searched_files', 0)} files, {jsonl.get('searched_bytes', 0)} bytes"
    )
    for group in jsonl.get("groups", []):
        lines.append(f"- JSONL {group['cluster_key']} score {group['top_score']}")
        for match in group.get("matches", [])[:3]:
            action = (match.get("claim_action") or {}).get("action", "unknown")
            stamp = f", {match['timestamp']}" if match.get("timestamp") else ""
            role = f", {match['role']}" if match.get("role") else ""
            lines.append(f"  line {match['line']}{stamp}{role}, action={action}: {match['snippet']}")

    if payload.get("artifact_checks"):
        lines.append("Artifact checks:")
        for item in payload["artifact_checks"]:
            state = "exists" if item.get("exists") else "missing"
            reason = f", {item['reason']}" if item.get("reason") else ""
            lines.append(f"- {item['path']}: {state}{reason}")

    if payload.get("review_evidence"):
        lines.append("Review evidence:")
        for item in payload["review_evidence"]:
            verdict = item.get("review_verdict") or {}
            action = (item.get("claim_action") or {}).get("action", "unknown")
            label = verdict.get("action") or action
            lines.append(f"- {item['path']}: {label}")

    joins = forensic.get("claim_action_review_joins") or []
    if joins:
        counts: dict[str, int] = {}
        basis_counts: dict[str, int] = {}
        for join in joins:
            counts[join.get("relation", "unknown")] = counts.get(join.get("relation", "unknown"), 0) + 1
            basis = join.get("basis_kind", "unknown")
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
        lines.append(
            "Claim/action/review joins: "
            + _format_count_map(counts)
        )
        lines.append("Claim/action/review join basis: " + _format_count_map(basis_counts))

    for warning in payload.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_forensics_markdown(payload: dict) -> str:
    text = render_forensics_text(payload)
    lines = ["# JSONL Forensics", ""]
    for line in text.splitlines()[1:]:
        if line.startswith("- ") or line.startswith("  "):
            lines.append(line)
            continue
        key, sep, value = line.partition(": ")
        if sep:
            lines.append(f"**{key}:** {value}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _compact_phase_source(source_ref: dict) -> str:
    if source_ref.get("path") and source_ref.get("line"):
        return f"{source_ref.get('path')}:{source_ref.get('line')}"
    if source_ref.get("path"):
        return str(source_ref.get("path"))
    if source_ref.get("commit"):
        return str(source_ref.get("commit"))
    return "(unknown)"


def _format_count_map(counts: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) or "none"


def _format_support_counts(summary: dict) -> str:
    return (
        f"exact={int(summary.get('exact_support_count') or 0)},"
        f"proximity={int(summary.get('proximity_support_count') or 0)},"
        f"inferred={int(summary.get('inferred_support_count') or 0)},"
        f"unsupported={int(summary.get('unsupported_support_count') or 0)}"
    )


def render_project_lineage_text(payload: dict) -> str:
    lineage = payload["project_lineage"]
    subject = lineage.get("subject_project", {})
    lines = [
        "Project Lineage",
        f"Status: {lineage.get('status')}",
        f"Subject: {subject.get('name')} ({subject.get('path')})",
        f"Query: {lineage.get('query') or '(none)'}",
        f"Candidates: {len(lineage.get('candidate_projects', []))}",
    ]
    if lineage.get("report_written"):
        lines.append(f"Report written: {lineage['report_written']}")
    for candidate in lineage.get("candidate_projects", [])[:8]:
        lines.append(
            "- "
            f"{candidate.get('candidate_id') or '?'} {candidate.get('name')} "
            f"score={candidate.get('score')} "
            f"confidence={candidate.get('confidence')} "
            f"relationship={candidate.get('relationship_hypothesis')}"
        )
        reason_codes = ", ".join(candidate.get("reason_codes", []))
        if reason_codes:
            lines.append(f"  Reasons: {reason_codes}")
        if candidate.get("jsonl_evidence_refs"):
            ref = candidate["jsonl_evidence_refs"][0]
            lines.append(f"  First JSONL ref: {ref.get('path')}:{ref.get('line')} {ref.get('timestamp')}")
    if lineage.get("timeline"):
        lines.append("Timeline:")
        for item in lineage["timeline"][:10]:
            lines.append(
                f"- {item.get('timestamp_utc') or item.get('timestamp') or '(no timestamp)'} "
                f"{item.get('path')}:{item.get('line')} {item.get('snippet_or_summary')}"
            )
    for warning in payload.get("warnings", []) + lineage.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    for item in lineage.get("next_read", [])[:5]:
        lines.append(f"Next read: {item}")
    return "\n".join(lines)


def render_project_lineage_markdown(payload: dict) -> str:
    text = render_project_lineage_text(payload)
    lines = ["# Project Lineage", ""]
    for line in text.splitlines()[1:]:
        if line.startswith("- ") or line.startswith("  "):
            lines.append(line)
            continue
        key, sep, value = line.partition(": ")
        if sep:
            lines.append(f"**{key}:** {value}")
        else:
            lines.append(line)
    return "\n".join(lines)


def render_skill_audit_text(payload: dict) -> str:
    report = payload["skill_audit"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Skill Audit",
        f"Status: {report.get('status')}",
        f"Skill: {report.get('skill')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('usage_instance_count', 0)} actual-use instances, "
            f"{efficiency.get('non_use_mention_count', 0)} non-use mentions"
        ),
    ]
    workflow_summary = report.get("workflow_classification_summary") or {}
    if workflow_summary.get("classification_counts"):
        counts = ", ".join(
            f"{key}={value}"
            for key, value in sorted((workflow_summary.get("classification_counts") or {}).items())
        )
        lines.append(f"Workflow classifications: {counts}")
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for instance in report.get("usage_instances", [])[:8]:
        evidence = instance.get("skill_evidence", [{}])[0]
        ref = evidence.get("source_ref", {})
        lines.append(
            "- "
            f"{instance.get('usage_id')} {instance.get('confidence')} "
            f"{instance.get('workflow_classification')} "
            f"{','.join(instance.get('signal_types', []))} "
            f"{ref.get('path')}:{ref.get('line')} "
            f"labels={','.join(instance.get('outcome_labels', []))}"
        )
    for mention in report.get("non_use_mentions", [])[:5]:
        ref = mention.get("source_ref", {})
        lines.append(f"{mention.get('classification')}: {ref.get('path')}:{ref.get('line')} {mention.get('snippet')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_skill_audit_markdown(payload: dict) -> str:
    report = payload["skill_audit"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Skill Audit",
        "",
        f"**Status:** {report.get('status')}",
        f"**Skill:** {report.get('skill')}",
        (
            f"**Scope:** {efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Evidence:** {efficiency.get('usage_instance_count', 0)} actual-use instances, "
            f"{efficiency.get('non_use_mention_count', 0)} non-use mentions"
        ),
        "",
        "## Workflow Classification",
        "",
    ]
    workflow_summary = report.get("workflow_classification_summary") or {}
    counts = workflow_summary.get("classification_counts") or {}
    if counts:
        for key, value in sorted(counts.items()):
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- No workflow classifications found.")
    if workflow_summary.get("classification_contract"):
        lines.append(f"- Contract: {workflow_summary.get('classification_contract')}")
    lines.extend([
        "",
        "## Usage Instances",
        "",
        "| ID | Classification | Confidence | Signals | Source | Labels |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for instance in report.get("usage_instances", []):
        evidence = instance.get("skill_evidence", [{}])[0]
        ref = evidence.get("source_ref", {})
        source = f"{ref.get('path')}:{ref.get('line')}"
        lines.append(
            "| "
            f"{_escape_markdown_table(str(instance.get('usage_id', '')))} | "
            f"{_escape_markdown_table(str(instance.get('workflow_classification', '')))} | "
            f"{_escape_markdown_table(str(instance.get('confidence', '')))} | "
            f"{_escape_markdown_table(', '.join(instance.get('signal_types', [])))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(', '.join(instance.get('outcome_labels', [])))} |"
        )
    if report.get("non_use_mentions"):
        lines.extend(["", "## Non-Use Mentions", ""])
        for mention in report["non_use_mentions"][:12]:
            ref = mention.get("source_ref", {})
            lines.append(
                f"- `{mention.get('classification')}` `{ref.get('path')}:{ref.get('line')}` "
                f"{mention.get('snippet')}"
            )
    return "\n".join(lines)


def render_workflow_repeat_text(payload: dict) -> str:
    report = payload["workflow_repeat_finder"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Workflow Repeat Finder",
        f"Status: {report.get('status')}",
        f"Query: {report.get('query') or '(none)'}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('cluster_count', 0)} clusters, "
            f"{efficiency.get('suggestion_count', 0)} suggestions, "
            f"{efficiency.get('rejected_one_off_count', 0)} one-offs rejected"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for cluster in report.get("workflow_clusters", [])[:8]:
        first = (cluster.get("representative_instances") or [{}])[0].get("source_ref", {})
        lines.append(
            "- "
            f"{cluster.get('cluster_id')} rank={cluster.get('rank')} "
            f"score={cluster.get('score')} confidence={cluster.get('confidence')} "
            f"instances={cluster.get('instance_count')} kind={cluster.get('workflow_kind')} "
            f"{first.get('path')}:{first.get('line')} {cluster.get('label')}"
        )
        reasons = ", ".join(cluster.get("reason_codes", [])[:5])
        if reasons:
            lines.append(f"  Reasons: {reasons}")
    for suggestion in report.get("automation_suggestions", [])[:5]:
        ref = suggestion.get("source_ref", {})
        lines.append(
            f"Suggestion: {suggestion.get('classification')} "
            f"{ref.get('path')}:{ref.get('line')} {suggestion.get('snippet')}"
        )
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_workflow_repeat_markdown(payload: dict) -> str:
    report = payload["workflow_repeat_finder"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Workflow Repeat Finder",
        "",
        f"**Status:** {report.get('status')}",
        f"**Query:** {report.get('query') or '(none)'}",
        (
            f"**Scope:** {efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Evidence:** {efficiency.get('cluster_count', 0)} clusters, "
            f"{efficiency.get('suggestion_count', 0)} suggestions, "
            f"{efficiency.get('rejected_one_off_count', 0)} one-offs rejected"
        ),
        "",
        "## Workflow Clusters",
        "",
        "| Rank | ID | Score | Confidence | Kind | Instances | First Source | Label |",
        "| ---: | --- | ---: | --- | --- | ---: | --- | --- |",
    ]
    for cluster in report.get("workflow_clusters", []):
        first = (cluster.get("representative_instances") or [{}])[0].get("source_ref", {})
        source = f"{first.get('path')}:{first.get('line')}"
        lines.append(
            "| "
            f"{cluster.get('rank')} | "
            f"{_escape_markdown_table(str(cluster.get('cluster_id', '')))} | "
            f"{cluster.get('score')} | "
            f"{_escape_markdown_table(str(cluster.get('confidence', '')))} | "
            f"{_escape_markdown_table(str(cluster.get('workflow_kind', '')))} | "
            f"{cluster.get('instance_count')} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(cluster.get('label', '')))} |"
        )
    if report.get("automation_suggestions"):
        lines.extend(["", "## Automation Suggestions", ""])
        for suggestion in report["automation_suggestions"][:12]:
            ref = suggestion.get("source_ref", {})
            lines.append(
                f"- `{suggestion.get('classification')}` `{ref.get('path')}:{ref.get('line')}` "
                f"{suggestion.get('snippet')}"
            )
    if report.get("rejected_one_offs"):
        lines.extend(["", "## Rejected One-Offs", ""])
        for one_off in report["rejected_one_offs"][:8]:
            ref = one_off.get("first_source_ref") or {}
            lines.append(f"- `{one_off.get('cluster_key')}` `{ref.get('path')}:{ref.get('line')}` {one_off.get('reason')}")
    return "\n".join(lines)


def render_private_benchmark_mine_text(payload: dict) -> str:
    report = payload["private_benchmark_mine"]
    summary = report.get("candidate_summary", {})
    coverage = report.get("coverage_gap_report", {})
    suggestions = report.get("sanitized_fixture_suggestions", {})
    efficiency = report.get("token_efficiency", {})
    files = report.get("output_files", {})
    lines = [
        "Private Benchmark Mine",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        f"Candidates: {summary.get('candidate_count', 0)}",
        f"Suggestions: {suggestions.get('suggested_case_count', 0)}",
        f"Private triage CSV: {files.get('private_triage_csv')}",
        f"Coverage gap report: {files.get('coverage_gap_report')}",
        f"Private report: {files.get('private_report')}",
    ]
    if coverage.get("missing_stages"):
        lines.append(f"Missing stages: {', '.join(coverage['missing_stages'])}")
    if coverage.get("missing_failure_modes"):
        lines.append(f"Missing failure modes: {', '.join(coverage['missing_failure_modes'])}")
    checker = report.get("checker", {})
    if checker.get("command"):
        lines.append(f"Checker: {checker['command']}")
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_private_benchmark_mine_markdown(payload: dict) -> str:
    report = payload["private_benchmark_mine"]
    summary = report.get("candidate_summary", {})
    coverage = report.get("coverage_gap_report", {})
    files = report.get("output_files", {})
    lines = [
        "# Private Benchmark Mine",
        "",
        f"**Status:** {report.get('status')}",
        f"**Candidates:** {summary.get('candidate_count', 0)}",
        f"**Suggestions:** {report.get('sanitized_fixture_suggestions', {}).get('suggested_case_count', 0)}",
        f"**Private triage CSV:** `{files.get('private_triage_csv')}`",
        f"**Coverage gap report:** `{files.get('coverage_gap_report')}`",
        f"**Private report:** `{files.get('private_report')}`",
        "",
        "## Coverage Gaps",
        "",
    ]
    for label, values in (
        ("Missing categories", coverage.get("missing_categories", [])),
        ("Missing stages", coverage.get("missing_stages", [])),
        ("Missing failure modes", coverage.get("missing_failure_modes", [])),
        ("Weak categories", coverage.get("weak_categories", [])),
    ):
        lines.append(f"- **{label}:** {', '.join(values) if values else '(none)'}")
    checker = report.get("checker", {})
    if checker.get("command"):
        lines.extend(["", "## Checker", "", f"`{checker['command']}`"])
    return "\n".join(lines)


def render_install_validation_text(payload: dict) -> str:
    validation = payload["install_validation"]
    summary = validation.get("summary", {})
    lines = [
        f"Install validation: {str(validation.get('overall_status', 'unknown')).upper()}",
        (
            "Checks: "
            f"{summary.get('pass', 0)} pass, "
            f"{summary.get('warn', 0)} warn, "
            f"{summary.get('fail', 0)} fail"
        ),
        f"Package root: {validation.get('package_root')}",
        f"Read-only: {validation.get('read_only')}",
    ]
    if validation.get("codex_home"):
        lines.append(f"Codex home: {validation['codex_home']}")
    if validation.get("claude_home"):
        lines.append(f"Claude home: {validation['claude_home']}")
    preflight = validation.get("agent_preflight") or {}
    layers = preflight.get("install_layers") or {}
    if layers:
        cli_layer = layers.get("python_cli") or {}
        codex_layer = layers.get("codex_skill") or {}
        claude_layer = layers.get("claude_skill") or {}
        lines.append("Install layers:")
        lines.append(f"- Python CLI (required): {cli_layer.get('status', 'unknown')}")
        lines.append(f"  Install: {cli_layer.get('install_command')}")
        lines.append(f"- Codex skill (optional): {codex_layer.get('status', 'unknown')}")
        lines.append(f"  Install: {codex_layer.get('install_command')}")
        lines.append(f"- Claude skill (optional): {claude_layer.get('status', 'unknown')}")
        lines.append(f"  Install: {claude_layer.get('install_command')}")
    currentness = validation.get("currentness") or {}
    if currentness:
        local = currentness.get("local_install_freshness") or {}
        remote = currentness.get("git_remote") or {}
        describe = currentness.get("git_describe") or {}
        version = currentness.get("version_metadata") or {}
        lines.append("Currentness:")
        lines.append(f"- Local package root: {local.get('package_root')}")
        lines.append(f"- Editable project root: {local.get('editable_project_root') or '(not a source checkout)'}")
        lines.append(f"- Local global skill freshness: {local.get('global_skill_freshness', 'unknown')}")
        lines.append(f"- Git remote availability: {remote.get('status', 'unknown')}")
        if remote.get("remote_names"):
            lines.append(f"  Remotes: {', '.join(str(item) for item in remote.get('remote_names', []))}")
        lines.append(f"- Git describe/tag distance: {describe.get('status', 'unknown')}")
        if describe.get("describe"):
            lines.append(f"  Describe: {describe.get('describe')}")
        if describe.get("nearest_tag") is not None:
            lines.append(
                f"  Nearest tag: {describe.get('nearest_tag')} "
                f"({describe.get('commits_after_tag')} commits after tag, dirty={describe.get('dirty')})"
            )
        lines.append(
            "- Version metadata: "
            f"pyproject={version.get('pyproject_version')!r}, "
            f"module __version__={version.get('module_version')!r} "
            f"({version.get('status', 'unknown')})"
        )
        if currentness.get("warning_check_ids"):
            lines.append("- Currentness warnings: " + ", ".join(str(item) for item in currentness["warning_check_ids"]))
        lines.append(
            "- Latest upstream/release currentness: "
            f"{currentness.get('upstream_latest_release_currentness', 'unproven')}"
        )
        lines.append(f"- Currentness claim boundary: {currentness.get('claim_boundary')}")
    if preflight:
        lines.append("Agent preflight:")
        agent_freshness = preflight.get("agent_skill_freshness") or {}
        lines.append(f"- Codex skill freshness: {agent_freshness.get('codex', 'not_checked')}")
        lines.append(f"- Claude skill freshness: {agent_freshness.get('claude', 'not_checked')}")
        lines.append(f"- Global skill freshness: {preflight.get('global_skill_freshness', 'unknown')}")
        lines.append(f"- Must refresh global skill: {preflight.get('must_refresh_global_skill')}")
        if preflight.get("stale_or_missing_global_checks"):
            lines.append(
                "- Stale/missing global checks: "
                + ", ".join(str(item) for item in preflight.get("stale_or_missing_global_checks", []))
            )
            lines.append(f"- Refresh command: {preflight.get('refresh_command')}")
        lines.append(f"- Validate global command: {preflight.get('validate_global_command')}")
        for agent, command in (preflight.get("validate_agent_commands") or {}).items():
            lines.append(f"- Validate {agent}: {command}")
        cookbook = preflight.get("agent_question_cookbook") or []
        if cookbook:
            lines.append("- Agent command cookbook:")
            for item in cookbook:
                lines.append(f"  - {item.get('question_family')}: {item.get('first_command')}")
                lines.append(f"    Inspect: {item.get('inspect_next')}")
        lines.append("- JSH-1098 proven route demos:")
        for demo in preflight.get("jsh1098_route_demos", []):
            lines.append(f"  - {demo.get('label')}: {demo.get('first_move')}")
        lines.append(f"- Claim boundary: {preflight.get('claim_boundary')}")
    lines.append("Details:")
    for check in validation.get("checks", []):
        lines.append(f"- {str(check.get('status', '')).upper()} {check.get('name')}: {check.get('detail')}")
        for recovery in check.get("recovery", []):
            lines.append(f"  Recovery: {recovery}")
    return "\n".join(lines)


def render_install_validation_markdown(payload: dict) -> str:
    validation = payload["install_validation"]
    summary = validation.get("summary", {})
    lines = [
        "# JSONL Install Validation",
        "",
        f"**Status:** {str(validation.get('overall_status', 'unknown')).upper()}",
        (
            f"**Checks:** {summary.get('pass', 0)} pass, "
            f"{summary.get('warn', 0)} warn, {summary.get('fail', 0)} fail"
        ),
        f"**Package root:** `{validation.get('package_root')}`",
        f"**Read-only:** {validation.get('read_only')}",
    ]
    preflight = validation.get("agent_preflight") or {}
    layers = preflight.get("install_layers") or {}
    if layers:
        cli_layer = layers.get("python_cli") or {}
        codex_layer = layers.get("codex_skill") or {}
        claude_layer = layers.get("claude_skill") or {}
        lines.extend(
            [
                "",
                "## Install Layers",
                "",
                f"- Python CLI (required): `{cli_layer.get('status', 'unknown')}`",
                f"  Install: `{cli_layer.get('install_command')}`",
                f"- Codex skill (optional): `{codex_layer.get('status', 'unknown')}`",
                f"  Install: `{codex_layer.get('install_command')}`",
                f"- Claude skill (optional): `{claude_layer.get('status', 'unknown')}`",
                f"  Install: `{claude_layer.get('install_command')}`",
            ]
        )
    currentness = validation.get("currentness") or {}
    if currentness:
        local = currentness.get("local_install_freshness") or {}
        remote = currentness.get("git_remote") or {}
        describe = currentness.get("git_describe") or {}
        version = currentness.get("version_metadata") or {}
        lines.extend(
            [
                "",
                "## Currentness",
                "",
                f"- Local package root: `{local.get('package_root')}`",
                f"- Editable project root: `{local.get('editable_project_root') or '(not a source checkout)'}`",
                f"- Local global skill freshness: `{local.get('global_skill_freshness', 'unknown')}`",
                f"- Git remote availability: `{remote.get('status', 'unknown')}`",
                f"- Git describe/tag distance: `{describe.get('status', 'unknown')}`",
                f"- Git describe: `{describe.get('describe')}`",
                (
                    f"- Nearest tag: `{describe.get('nearest_tag')}`; "
                    f"commits after tag: `{describe.get('commits_after_tag')}`; "
                    f"dirty: `{describe.get('dirty')}`"
                ),
                (
                    f"- Version metadata: pyproject `{version.get('pyproject_version')}`, "
                    f"module `__version__` `{version.get('module_version')}` "
                    f"({version.get('status', 'unknown')})"
                ),
                (
                    "- Currentness warnings: `"
                    + ", ".join(str(item) for item in currentness.get("warning_check_ids", []))
                    + "`"
                ),
                (
                    "- Latest upstream/release currentness: `"
                    f"{currentness.get('upstream_latest_release_currentness', 'unproven')}`"
                ),
                f"- Currentness claim boundary: {currentness.get('claim_boundary')}",
            ]
        )
    lines.extend(["", "| Status | Check | Detail |", "| --- | --- | --- |"])
    for check in validation.get("checks", []):
        lines.append(
            "| "
            f"{str(check.get('status', '')).upper()} | "
            f"{_escape_markdown_table(str(check.get('name', '')))} | "
            f"{_escape_markdown_table(str(check.get('detail', '')))} |"
        )
    suggestions = validation.get("recovery_suggestions") or []
    if suggestions:
        lines.extend(["", "## Recovery Suggestions", ""])
        lines.extend(f"- {item}" for item in suggestions)
    if preflight:
        agent_freshness = preflight.get("agent_skill_freshness") or {}
        lines.extend(
            [
                "",
                "## Agent Preflight",
                "",
                f"- Codex skill freshness: `{agent_freshness.get('codex', 'not_checked')}`",
                f"- Claude skill freshness: `{agent_freshness.get('claude', 'not_checked')}`",
                f"- Global skill freshness: `{preflight.get('global_skill_freshness', 'unknown')}`",
                f"- Must refresh global skill: `{preflight.get('must_refresh_global_skill')}`",
                f"- Validate global command: `{preflight.get('validate_global_command')}`",
            ]
        )
        cookbook = preflight.get("agent_question_cookbook") or []
        if cookbook:
            lines.extend(["", "### Agent Command Cookbook", ""])
            for item in cookbook[:6]:
                lines.append(f"- **{item.get('question_family')}**: `{item.get('first_command')}`")
                lines.append(f"  Inspect: {item.get('inspect_next')}")
        stale = preflight.get("stale_or_missing_global_checks") or []
        if stale:
            lines.append(f"- Stale/missing global checks: `{', '.join(str(item) for item in stale)}`")
            lines.append(f"- Refresh command: `{preflight.get('refresh_command')}`")
        lines.extend(["", "### JSH-1098 Proven Route Demos", ""])
        for demo in preflight.get("jsh1098_route_demos", []):
            lines.append(
                f"- **{demo.get('label')}**: `{demo.get('first_move')}` "
                f"for {demo.get('prompt_shape')}"
            )
        lines.extend(["", f"Claim boundary: {preflight.get('claim_boundary')}"])
    return "\n".join(lines)


def _escape_markdown_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _format_handoff_source_ref(candidate: dict) -> str:
    ref = candidate.get("source_ref") or {}
    path = ref.get("path")
    line = ref.get("line")
    if path and line is not None:
        return f"{path}:{line}"
    if path:
        return str(path)
    if line is not None:
        return f"line {line}"
    return ""


def _format_handoff_reason_codes(candidate: dict) -> str:
    reason_codes = candidate.get("reason_codes") or []
    return ", ".join(str(item) for item in reason_codes if item is not None)


def _format_handoff_score(candidate: dict) -> str:
    score = candidate.get("score")
    if isinstance(score, float):
        return f"{score:.3f}".rstrip("0").rstrip(".")
    if score is None:
        return ""
    return str(score)


def _format_deep_audit_target_use(candidate: dict) -> str:
    target_match = candidate.get("deep_audit_target_match") or {}
    if not target_match:
        return ""
    if target_match.get("eligible_as_prompt_history_lead"):
        return "eligible-prompt-history-lead"
    return "diagnostic-only/non-actionable"


def render_source_locator_handoff_text(handoff: dict) -> list[str]:
    not_searched = handoff.get("not_searched_scope", {})
    candidate_ordering = handoff.get("candidate_ordering", {})
    caps = handoff.get("caps", {})
    no_evidence = handoff.get("no_evidence_meaning", {})
    lines = [
        "Source locator handoff:",
        (
            "- roots: "
            f"{len(handoff.get('selected_roots', []))} selected, "
            f"{len(handoff.get('rejected_roots', []))} rejected, "
            f"{not_searched.get('missing_root_count', 0)} missing"
        ),
        f"- candidates: {candidate_ordering.get('candidate_count', 0)} ordered by {candidate_ordering.get('strategy')}",
        f"- no-evidence means: {no_evidence.get('answer_scope', 'not_found_in_searched_scope')}",
    ]
    if caps.get("hit"):
        lines.append(f"- caps hit: {', '.join(caps.get('caps_hit', []))}")
    if no_evidence.get("candidate_interpretation") == "weak_context_only":
        lines.append("- no-evidence candidate interpretation: weak/context-only candidates; read-more is optional diagnostic context")
    provenance_policy = handoff.get("historical_provenance_self_echo_policy") or {}
    if provenance_policy:
        lines.append(
            "- historical provenance self-echo policy: "
            f"embedded_refs_promoted={provenance_policy.get('embedded_source_ref_promoted_count', 0)} "
            f"diagnostic_only_reads={provenance_policy.get('diagnostic_only_next_read_count', 0)}"
        )
    followthrough_plan = handoff.get("source_followthrough_plan") or {}
    if followthrough_plan:
        counts = followthrough_plan.get("counts") or {}
        lines.append(
            "- source_followthrough_plan: "
            f"status={followthrough_plan.get('status')} "
            f"required_primary_proof={counts.get('required_primary_proof_count', 0)} "
            f"conditional_required={counts.get('conditional_required_count', 0)} "
            f"optional_diagnostic={counts.get('optional_diagnostic_count', 0)}"
        )
        lines.append(f"- source_followthrough_stop: {followthrough_plan.get('stop_condition')}")
    top_candidates = candidate_ordering.get("top_candidates") or []
    if top_candidates and _source_followthrough_direct_evidence_complete(followthrough_plan):
        lines.append(
            f"- top candidates: {len(top_candidates[:5])} diagnostic candidates suppressed after complete direct evidence"
        )
    else:
        for candidate in top_candidates[:5]:
            bits = [
                f"id={candidate.get('candidate_id') or ''}",
                f"source={_format_handoff_source_ref(candidate)}",
                f"confidence={candidate.get('confidence') or ''}",
                f"stage={candidate.get('stage') or ''}",
                f"score={_format_handoff_score(candidate)}",
                f"reasons={_format_handoff_reason_codes(candidate)}",
            ]
            classification = candidate.get("post_hoc_source_classification") or {}
            if classification.get("likely_post_hoc_diagnostic"):
                bits.extend(
                    [
                        "evidence_role=diagnostic_post_hoc_not_target_proof",
                        "final_support_allowed=False",
                    ]
                )
            target_use = _format_deep_audit_target_use(candidate)
            if target_use:
                bits.append(f"target_use={target_use}")
            lines.append(f"- top candidate: {' '.join(bits)}")
    if followthrough_plan:
        lines.append("- next_reads: preserved in JSON/report; default open order is semantic_debug.source_followthrough_plan")
        optional_count = (followthrough_plan.get("counts") or {}).get("optional_diagnostic_count", 0)
        if optional_count:
            lines.append(f"- next read (diagnostic-only): {optional_count} refs preserved; targets listed only in JSON next_reads")
    else:
        for item in handoff.get("next_reads", [])[:5]:
            actionability = "diagnostic-only" if item.get("diagnostic_only") or item.get("read_more_actionable") is False else "actionable"
            reason = f" reason={item.get('reason')}" if item.get("reason") else ""
            lines.append(f"- next read ({actionability}): {item.get('kind')} {item.get('target')}{reason}")
    directional = handoff.get("directional_history_reconstruction") or {}
    if directional.get("applies"):
        lines.append(
            "- directional reconstruction: "
            f"route={directional.get('route_family')} traversal={directional.get('traversal')} "
            f"status={directional.get('status')}"
        )
        for stage in directional.get("stages") or []:
            lines.append(
                f"- directional stage {stage.get('phase')}: {stage.get('action')} command={stage.get('command')}"
            )
    if handoff.get("process_actual_use_bucket_evidence_bundle"):
        _append_process_actual_use_bucket_bundle_text(
            lines,
            handoff.get("process_actual_use_bucket_evidence_bundle") or {},
        )
    return lines


HISTORICAL_ORCHESTRATION_ROLE_LABELS = {
    "parent_or_coordinator": "parent/coordinator",
    "worker": "worker/subagent",
    "reviewer": "reviewer/rereviewer",
    "secondary_task_review_commit_support": "secondary task/review/commit support",
}


def render_spawned_session_resolution_text(resolution: dict) -> list[str]:
    diagnostics = resolution.get("diagnostics") or {}
    caps = resolution.get("caps") or {}
    observed = resolution.get("observed") or {}
    lines = [
        "Spawned session transcript resolution:",
        f"- status: {resolution.get('status')}",
        (
            "- diagnostics: "
            f"spawn_detected={diagnostics.get('spawn_detected', 0)} "
            f"child_transcript_inspected={diagnostics.get('child_transcript_inspected', 0)} "
            f"child_transcript_missing={diagnostics.get('child_transcript_missing', 0)} "
            f"child_scope_omitted={diagnostics.get('child_scope_omitted', 0)}"
        ),
        (
            "- traversal: "
            f"depth={observed.get('max_depth_seen', 0)} "
            f"child_refs={observed.get('child_ref_count', 0)} "
            f"caps_hit={','.join(caps.get('caps_hit') or []) or 'none'}"
        ),
    ]
    for read in list(resolution.get("next_reads") or [])[:4]:
        lines.append(f"- child transcript ref: {read.get('target')}")
    if resolution.get("missing_children"):
        lines.append(f"- missing child transcripts: {len(resolution.get('missing_children') or [])}")
    if caps.get("omitted_reasons"):
        lines.append(f"- omitted child scope: {', '.join(caps.get('omitted_reasons') or [])}")
    return lines


def render_spawned_session_resolution_markdown(resolution: dict) -> list[str]:
    diagnostics = resolution.get("diagnostics") or {}
    caps = resolution.get("caps") or {}
    observed = resolution.get("observed") or {}
    lines = [
        "",
        "## Spawned Session Transcript Resolution",
        "",
        f"- `status`: `{resolution.get('status')}`",
        (
            "- `diagnostics`: "
            f"`spawn_detected={diagnostics.get('spawn_detected', 0)}` "
            f"`child_transcript_inspected={diagnostics.get('child_transcript_inspected', 0)}` "
            f"`child_transcript_missing={diagnostics.get('child_transcript_missing', 0)}` "
            f"`child_scope_omitted={diagnostics.get('child_scope_omitted', 0)}`"
        ),
        (
            "- `traversal`: "
            f"`depth={observed.get('max_depth_seen', 0)}` "
            f"`child_refs={observed.get('child_ref_count', 0)}` "
            f"`caps_hit={','.join(caps.get('caps_hit') or []) or 'none'}`"
        ),
    ]
    for read in list(resolution.get("next_reads") or [])[:4]:
        lines.append(f"- Child transcript ref: `{read.get('target')}`")
    if resolution.get("missing_children"):
        lines.append(f"- Missing child transcripts: `{len(resolution.get('missing_children') or [])}`")
    if caps.get("omitted_reasons"):
        lines.append(f"- Omitted child scope: `{', '.join(caps.get('omitted_reasons') or [])}`")
    return lines


def render_historical_orchestration_role_evidence_bundle_text(bundle: dict) -> list[str]:
    labels = HISTORICAL_ORCHESTRATION_ROLE_LABELS
    lines = [
        "Historical orchestration role evidence bundle:",
        f"- bundle_name: {bundle.get('bundle_name') or 'historical_orchestration_role_evidence_bundle'}",
        f"- status: {bundle.get('status')}",
        f"- required_role_read_count: {bundle.get('required_role_read_count', 0)} / {bundle.get('required_role_read_cap', 5)}",
        (
            "- claim_boundaries: raw JSONL supports primary role claims; "
            "task/review/commit refs are secondary support; current-run/current-task/same-loop echoes do not count"
        ),
    ]
    for group in bundle.get("role_groups") or []:
        label = group.get("label") or labels.get(group.get("role"), group.get("role"))
        lines.append(
            f"- role_group: {label} status={group.get('status')} "
            f"tier={group.get('support_tier')} sources={group.get('source_count', 0)}"
        )
        for read in list(group.get("source_refs") or [])[:2]:
            lines.append(f"  source: {read.get('target') or '(no source ref)'}")
    required_reads = bundle.get("required_role_reads") or []
    if required_reads:
        lines.append("Required role reads:")
        for read in required_reads[:5]:
            _append_historical_orchestration_plan_read(lines, read, labels=labels)
    if bundle.get("missing_roles"):
        missing = [labels.get(role, role) for role in bundle.get("missing_roles") or []]
        lines.append(f"- missing_roles: {', '.join(missing)}")
        lines.append(
            f"- missing_role_recovery_ref_count: {bundle.get('missing_role_recovery_ref_count', 0)}"
        )
        for item in list(bundle.get("missing_role_recovery_refs") or [])[:5]:
            role = labels.get(item.get("role"), item.get("role"))
            lines.append(f"- recovery for {role}: {item.get('target') or '(no target)'}")
            if item.get("command"):
                lines.append(f"  command: {item.get('command')}")
    return lines


def render_historical_orchestration_role_evidence_bundle_markdown(bundle: dict) -> list[str]:
    labels = HISTORICAL_ORCHESTRATION_ROLE_LABELS
    lines = [
        "",
        "## Historical Orchestration Role Evidence Bundle",
        "",
        f"- `bundle_name`: `{bundle.get('bundle_name') or 'historical_orchestration_role_evidence_bundle'}`",
        f"- `status`: `{bundle.get('status')}`",
        f"- `required_role_read_count`: `{bundle.get('required_role_read_count', 0)} / {bundle.get('required_role_read_cap', 5)}`",
        "- `claim_boundaries`: raw JSONL supports primary role claims; task/review/commit refs are secondary support; current-run/current-task/same-loop echoes do not count.",
    ]
    for group in bundle.get("role_groups") or []:
        label = group.get("label") or labels.get(group.get("role"), group.get("role"))
        lines.append(
            f"- `{label}`: `{group.get('status')}` "
            f"tier=`{group.get('support_tier')}` sources=`{group.get('source_count', 0)}`"
        )
        for read in list(group.get("source_refs") or [])[:2]:
            lines.append(f"  - source: `{read.get('target') or '(no source ref)'}`")
    required_reads = bundle.get("required_role_reads") or []
    if required_reads:
        lines.extend(["", "Required role reads:"])
        for read in required_reads[:5]:
            lines.append(f"- `{read.get('target') or '(no source ref)'}`")
    if bundle.get("missing_roles"):
        missing = [labels.get(role, role) for role in bundle.get("missing_roles") or []]
        lines.append(f"- `missing_roles`: `{', '.join(missing)}`")
        lines.append(
            f"- `missing_role_recovery_ref_count`: `{bundle.get('missing_role_recovery_ref_count', 0)}`"
        )
        for item in list(bundle.get("missing_role_recovery_refs") or [])[:5]:
            role = labels.get(item.get("role"), item.get("role"))
            lines.append(f"- recovery for `{role}`: `{item.get('target') or '(no target)'}`")
            if item.get("command"):
                lines.append(f"  - command: `{item.get('command')}`")
    return lines


def render_historical_orchestration_role_coverage_source_plan_text(plan: dict) -> list[str]:
    labels = HISTORICAL_ORCHESTRATION_ROLE_LABELS
    missing = [labels.get(role, role) for role in plan.get("missing_roles") or []]
    required = [labels.get(role, role) for role in plan.get("required_roles") or []]
    covered = [labels.get(role, role) for role in plan.get("covered_roles") or []]
    lines = [
        "Historical orchestration role-coverage source plan:",
        f"- source_plan_name: {plan.get('source_plan_name') or 'historical_orchestration_role_coverage_source_plan'}",
        f"- status: {plan.get('status')}",
        f"- required_roles: {', '.join(required) if required else '(none)'}",
        f"- covered_roles: {', '.join(covered) if covered else '(none)'}",
        f"- missing_roles: {', '.join(missing) if missing else '(none)'}",
        (
            "- final_support_policy: pre-existing raw JSONL refs first; "
            "current-run/current-task/same-loop echoes do not count as final support"
        ),
    ]
    role_coverage = plan.get("role_coverage") or []
    if role_coverage:
        lines.append("- role coverage:")
        for row in role_coverage:
            label = labels.get(row.get("role"), row.get("role"))
            lines.append(
                "  "
                f"- {label}: {row.get('status')} "
                f"tier={row.get('tier')} sources={row.get('source_count', 0)}"
            )
    primary_reads = plan.get("primary_historical_final_support_reads") or []
    lines.append("Primary pre-existing raw JSONL refs:")
    if primary_reads:
        for read in primary_reads[:4]:
            _append_historical_orchestration_plan_read(lines, read, labels=labels)
    else:
        lines.append("- (missing)")
    secondary_reads = plan.get("secondary_task_review_commit_support_reads") or []
    lines.append("Secondary task/review/commit support refs:")
    if secondary_reads:
        for read in secondary_reads[:3]:
            _append_historical_orchestration_plan_read(lines, read, labels=labels)
    else:
        lines.append("- (missing)")
    non_final_reads = plan.get("bounded_non_final_required_reads") or []
    recovery_refs = plan.get("missing_role_recovery_refs") or []
    if missing or recovery_refs or non_final_reads:
        lines.append("Fail-closed gap checks:")
        if missing:
            lines.append(f"- state missing roles before any answer: {', '.join(missing)}")
        if recovery_refs:
            lines.append(
                "- missing_role_recovery_ref_count: "
                f"{plan.get('missing_role_recovery_ref_count', len(recovery_refs))}"
            )
            for item in recovery_refs[:3]:
                role = labels.get(item.get("role"), item.get("role"))
                command = item.get("command")
                target = item.get("target") or "(no target)"
                lines.append(f"- recovery for {role}: {target}")
                if command:
                    lines.append(f"  command: {command}")
        lines.append(
            "- bounded_non_final_required_read_count: "
            f"{plan.get('bounded_non_final_required_read_count', len(non_final_reads))}"
        )
        if plan.get("current_run_self_echo_candidate_count"):
            lines.append(
                "- current_run_current_task_same_loop_echo_count: "
                f"{plan.get('current_run_self_echo_candidate_count')}; listed later only as diagnostics"
            )
    return lines


def render_longitudinal_phase_source_plan_text(plan: dict) -> list[str]:
    lines = [
        "Longitudinal phase/source plan:",
        f"- status: {plan.get('status')}",
        f"- phase_balance_status: {plan.get('phase_balance_status')}",
        f"- represented phases: {', '.join(plan.get('represented_phase_ids') or []) or 'none'}",
        f"- missing phases: {', '.join(plan.get('missing_phase_ids') or []) or 'none'}",
    ]
    echoes = plan.get("same_wave_current_echoes") or {}
    lines.append(
        f"- current/same-wave echoes: {echoes.get('count', 0)} "
        f"({echoes.get('policy') or 'diagnostic_only'})"
    )
    lines.append("Planned phases:")
    for phase in list(plan.get("planned_phases") or [])[:6]:
        families = ", ".join(phase.get("source_families") or []) or "none"
        lines.append(
            f"- {phase.get('phase_id')}: {phase.get('status')}; "
            f"reads={phase.get('actionable_read_count', 0)}; source_families={families}"
        )
    lines.append("Source-set families:")
    for family in list(plan.get("source_set_families") or [])[:8]:
        lines.append(
            f"- {family.get('family')}: surfaced_reads={family.get('surfaced_read_count', 0)}; "
            f"selected_root={family.get('selected_root')}"
        )
    for action in list(plan.get("required_before_synthesis") or [])[:4]:
        lines.append(f"- before synthesis: {action}")
    return lines


def render_longitudinal_phase_source_plan_markdown(plan: dict) -> list[str]:
    lines = [
        "",
        "## Longitudinal Phase/Source Plan",
        "",
        f"- `status`: `{plan.get('status')}`",
        f"- `phase_balance_status`: `{plan.get('phase_balance_status')}`",
        f"- Represented phases: {', '.join(plan.get('represented_phase_ids') or []) or 'none'}",
        f"- Missing phases: {', '.join(plan.get('missing_phase_ids') or []) or 'none'}",
    ]
    echoes = plan.get("same_wave_current_echoes") or {}
    lines.append(
        f"- Current/same-wave echoes: {echoes.get('count', 0)} "
        f"({echoes.get('policy') or 'diagnostic_only'})"
    )
    lines.extend(["", "Planned phases:"])
    for phase in list(plan.get("planned_phases") or [])[:6]:
        families = ", ".join(phase.get("source_families") or []) or "none"
        lines.append(
            f"- `{phase.get('phase_id')}`: `{phase.get('status')}`; "
            f"reads={phase.get('actionable_read_count', 0)}; source_families={families}"
        )
    lines.extend(["", "Required before synthesis:"])
    for action in list(plan.get("required_before_synthesis") or [])[:4]:
        lines.append(f"- {action}")
    return lines


def _append_historical_orchestration_plan_read(
    lines: list[str],
    read: dict,
    *,
    labels: dict[str, str],
    diagnostic: bool = False,
) -> None:
    roles = [labels.get(role, role) for role in read.get("orchestration_roles") or []]
    role_text = f" roles={','.join(roles)}" if roles else ""
    chronology = read.get("source_chronology_role") or "unknown_chronology"
    reason = f" reason={read.get('reason')}" if read.get("reason") else ""
    prefix = "diagnostic/non-final" if diagnostic or read.get("diagnostic_only") else "source"
    lines.append(
        f"- {prefix}: {read.get('target') or '(no source ref)'}"
        f"{role_text} chronology={chronology}{reason}"
    )


def render_source_locator_handoff_markdown(handoff: dict) -> list[str]:
    not_searched = handoff.get("not_searched_scope", {})
    candidate_ordering = handoff.get("candidate_ordering", {})
    caps = handoff.get("caps", {})
    no_evidence = handoff.get("no_evidence_meaning", {})
    lines = [
        (
            f"- Roots: `{len(handoff.get('selected_roots', []))}` selected, "
            f"`{len(handoff.get('rejected_roots', []))}` rejected, "
            f"`{not_searched.get('missing_root_count', 0)}` missing"
        ),
        f"- Candidate ordering: `{candidate_ordering.get('strategy')}`",
        f"- Candidate count: `{candidate_ordering.get('candidate_count', 0)}`",
        f"- No-evidence meaning: `{no_evidence.get('answer_scope', 'not_found_in_searched_scope')}`",
    ]
    if caps.get("hit"):
        lines.append(f"- Caps hit: `{', '.join(caps.get('caps_hit', []))}`")
    if no_evidence.get("candidate_interpretation") == "weak_context_only":
        lines.append("- No-evidence candidate interpretation: `weak_context_only`; read-more is optional diagnostic context.")
    provenance_policy = handoff.get("historical_provenance_self_echo_policy") or {}
    if provenance_policy:
        lines.append(
            "- Historical provenance self-echo policy: "
            f"`embedded_refs_promoted={provenance_policy.get('embedded_source_ref_promoted_count', 0)}`; "
            f"`diagnostic_only_reads={provenance_policy.get('diagnostic_only_next_read_count', 0)}`."
        )
    followthrough_plan = handoff.get("source_followthrough_plan") or {}
    if followthrough_plan:
        counts = followthrough_plan.get("counts") or {}
        lines.append(
            "- Source followthrough plan: "
            f"`status={followthrough_plan.get('status')}`; "
            f"`required_primary_proof={counts.get('required_primary_proof_count', 0)}`; "
            f"`conditional_required={counts.get('conditional_required_count', 0)}`; "
            f"`optional_diagnostic={counts.get('optional_diagnostic_count', 0)}`."
        )
        lines.append(f"- Source followthrough stop: {followthrough_plan.get('stop_condition')}")
    top_candidates = candidate_ordering.get("top_candidates") or []
    if top_candidates and _source_followthrough_direct_evidence_complete(followthrough_plan):
        lines.append(f"- Top candidates: `{len(top_candidates[:5])}` diagnostic candidates suppressed after complete direct evidence.")
    elif top_candidates:
        include_target_use = any(_format_deep_audit_target_use(candidate) for candidate in top_candidates[:5])
        if include_target_use:
            lines.extend(
                [
                    "",
                    "| ID | Source | Confidence | Stage | Score | Target use | Reason codes |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "| ID | Source | Confidence | Stage | Score | Reason codes |",
                    "| --- | --- | --- | --- | --- | --- |",
                ]
            )
        for candidate in top_candidates[:5]:
            target_use_cell = ""
            if include_target_use:
                target_use_cell = f"{_escape_markdown_table(_format_deep_audit_target_use(candidate))} | "
            lines.append(
                "| "
                f"{_escape_markdown_table(str(candidate.get('candidate_id') or ''))} | "
                f"{_escape_markdown_table(_format_handoff_source_ref(candidate))} | "
                f"{_escape_markdown_table(str(candidate.get('confidence') or ''))} | "
                f"{_escape_markdown_table(str(candidate.get('stage') or ''))} | "
                f"{_escape_markdown_table(_format_handoff_score(candidate))} | "
                f"{target_use_cell}"
                f"{_escape_markdown_table(_format_handoff_reason_codes(candidate))} |"
            )
    next_reads = [] if followthrough_plan else handoff.get("next_reads", [])[:5]
    if followthrough_plan:
        lines.append("- `next_reads`: preserved in JSON/report; default open order is `semantic_debug.source_followthrough_plan`.")
    elif next_reads:
        lines.extend(["", "| Kind | Target | Actionability | Reason |", "| --- | --- | --- | --- |"])
        for item in next_reads:
            actionability = (
                "diagnostic-only"
                if item.get("diagnostic_only") or item.get("read_more_actionable") is False
                else "actionable"
            )
            lines.append(
                "| "
                f"{_escape_markdown_table(str(item.get('kind', '')))} | "
                f"{_escape_markdown_table(str(item.get('target', '')))} | "
                f"{_escape_markdown_table(actionability)} | "
                f"{_escape_markdown_table(str(item.get('reason', '')))} |"
            )
    directional = handoff.get("directional_history_reconstruction") or {}
    if directional.get("applies"):
        lines.append(
            "- Directional reconstruction: "
            f"`route={directional.get('route_family')}`; `traversal={directional.get('traversal')}`; "
            f"`status={directional.get('status')}`."
        )
        for stage in directional.get("stages") or []:
            lines.append(
                f"- Directional stage `{stage.get('phase')}` (`{stage.get('action')}`): `{stage.get('command')}`"
            )
    if handoff.get("process_actual_use_bucket_evidence_bundle"):
        _append_process_actual_use_bucket_bundle_markdown(
            lines,
            handoff.get("process_actual_use_bucket_evidence_bundle") or {},
        )
    return lines


def render_agent_find_text(payload: dict) -> str:
    report = payload["agent_find"]
    plan_sources = report.get("plan_sources", {})
    source_scope = plan_sources.get("source_scope", {})
    cap_hit = plan_sources.get("cap_hit", {})
    lines = [
        "JSONL Find",
        f"Status: {report.get('status')}",
        f"Query: {report.get('query', {}).get('original', '')}",
        (
            "Scope: "
            f"{plan_sources.get('searched_files', 0)} files, "
            f"{plan_sources.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Candidates: "
            f"{report.get('token_efficiency', {}).get('candidate_count', 0)} "
            f"({report.get('context_budget', {}).get('payload_bytes', 0)} bytes inline)"
        ),
    ]
    if source_scope:
        lines.insert(
            4,
            "Source roots: "
            f"{len(source_scope.get('selected_roots', []))} selected/private, "
            f"{source_scope.get('excluded_scope', {}).get('rejected_root_count', 0)} excluded, "
            f"{source_scope.get('not_searched_scope', {}).get('missing_root_count', 0)} missing",
        )
    if cap_hit.get("hit"):
        lines.append(f"Cap hit: {', '.join(cap_hit.get('caps_hit', []))}")
    if report.get("cap_next_action"):
        action = report["cap_next_action"]
        reasons = ", ".join(str(item) for item in action.get("trigger_reasons", []))
        lines.append(f"Cap next action: {action.get('selected_action')} ({reasons})")
        if action.get("rationale"):
            lines.append(f"Cap action rationale: {action.get('rationale')}")
    if report.get("bm25_escalation"):
        escalation = report["bm25_escalation"]
        command = (escalation.get("command") or {}).get("shell")
        reasons = ", ".join(str(item) for item in escalation.get("trigger_reasons", []))
        lines.append(f"BM25 escalation: {escalation.get('status')} ({reasons})")
        if command:
            lines.append(f"BM25 retry command: {command}")
    if report.get("source_locator_handoff"):
        lines.extend(render_source_locator_handoff_text(report["source_locator_handoff"]))
    for candidate in report.get("find_candidates", [])[:8]:
        ref = candidate.get("source_ref", {})
        target_use = _format_deep_audit_target_use(candidate)
        target_use_text = f" target_use={target_use}" if target_use else ""
        lines.append(
            "- "
            f"{candidate.get('candidate_id')} {candidate.get('confidence')} "
            f"{source_ref_target(ref) or ''} "
            f"{candidate.get('timestamp') or '(no timestamp)'} "
            f"{candidate.get('role') or ''}{target_use_text}: {candidate.get('snippet')}"
        )
    if report.get("status") == "no_evidence" or report.get("no_evidence_report", {}).get("needed"):
        no_evidence = report.get("no_evidence_report", {})
        not_searched = no_evidence.get("not_searched_scope", {})
        searched = no_evidence.get("searched_scope", {})
        lines.append(
            "No-evidence scope: "
            f"not found in searched scope only ({searched.get('searched_files', 0)} searched files); "
            f"{not_searched.get('missing_root_count', 0)} missing selected roots; "
            f"{len(no_evidence.get('excluded_scope', []))} excluded roots"
        )
        interpretation = no_evidence.get("candidate_interpretation")
        if interpretation:
            lines.append(f"No-evidence candidate interpretation: {interpretation}")
        coverage = no_evidence.get("query_term_coverage") or {}
        missing_terms = coverage.get("missing_terms_any_candidate") or []
        if missing_terms:
            lines.append(f"Missing query terms in surfaced candidates: {', '.join(str(term) for term in missing_terms)}")
        guidance = no_evidence.get("read_more_guidance") or {}
        if guidance:
            required = "required" if guidance.get("required_before_scoped_no_evidence_answer") else "optional diagnostic"
            lines.append(f"Read-more before scoped no-evidence answer: {required}")
        lines.append("No-evidence boundary: do not treat this as all-history, global, or permanent absence without more search.")
        lines.append(f"No evidence: {no_evidence.get('why_not_confident')}")
    suggestions = report.get("next_search_suggestions") or []
    if suggestions:
        lines.append("Next searches:")
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def render_agent_find_markdown(payload: dict) -> str:
    report = payload["agent_find"]
    plan_sources = report.get("plan_sources", {})
    source_scope = plan_sources.get("source_scope", {})
    cap_hit = plan_sources.get("cap_hit", {})
    candidates = list(report.get("find_candidates") or [])
    include_target_use = any(_format_deep_audit_target_use(candidate) for candidate in candidates)
    lines = [
        "# JSONL Find",
        "",
        f"**Status:** {report.get('status')}",
        f"**Query:** {report.get('query', {}).get('original', '')}",
        (
            f"**Scope:** {plan_sources.get('searched_files', 0)} files, "
            f"{plan_sources.get('searched_bytes', 0)} bytes searched"
        ),
        (
            f"**Context budget:** {report.get('context_budget', {}).get('payload_bytes', 0)} bytes, "
            f"~{report.get('context_budget', {}).get('estimated_tokens', 0)} tokens"
        ),
        "",
        "## Candidates",
        "",
        (
            "| ID | Confidence | Source | Timestamp | Role | Stage | Target use | Snippet |"
            if include_target_use
            else "| ID | Confidence | Source | Timestamp | Role | Stage | Snippet |"
        ),
        (
            "| --- | --- | --- | --- | --- | --- | --- | --- |"
            if include_target_use
            else "| --- | --- | --- | --- | --- | --- | --- |"
        ),
    ]
    if source_scope:
        lines.insert(
            5,
            f"**Source roots:** {len(source_scope.get('selected_roots', []))} selected/private, "
            f"{source_scope.get('excluded_scope', {}).get('rejected_root_count', 0)} excluded, "
            f"{source_scope.get('not_searched_scope', {}).get('missing_root_count', 0)} missing",
        )
    if cap_hit.get("hit"):
        lines.extend(["", f"**Cap hit:** {', '.join(cap_hit.get('caps_hit', []))}"])
    if report.get("cap_next_action"):
        action = report["cap_next_action"]
        reasons = ", ".join(str(item) for item in action.get("trigger_reasons", []))
        lines.extend(
            [
                "",
                f"**Cap next action:** {action.get('selected_action')} ({reasons})",
            ]
        )
        if action.get("rationale"):
            lines.append(f"**Cap action rationale:** {action.get('rationale')}")
    if report.get("bm25_escalation"):
        escalation = report["bm25_escalation"]
        command = (escalation.get("command") or {}).get("shell")
        reasons = ", ".join(str(item) for item in escalation.get("trigger_reasons", []))
        lines.extend(["", f"**BM25 escalation:** {escalation.get('status')} ({reasons})"])
        if command:
            lines.extend(["", "```bash", command, "```"])
    if report.get("source_locator_handoff"):
        lines.extend(["", "## Source Locator Handoff", ""])
        lines.extend(render_source_locator_handoff_markdown(report["source_locator_handoff"]))
    for candidate in candidates:
        ref = candidate.get("source_ref", {})
        source = source_ref_target(ref) or ""
        target_use = _format_deep_audit_target_use(candidate)
        target_use_cell = f"{_escape_markdown_table(target_use)} | " if include_target_use else ""
        lines.append(
            "| "
            f"{_escape_markdown_table(str(candidate.get('candidate_id', '')))} | "
            f"{_escape_markdown_table(str(candidate.get('confidence', '')))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(candidate.get('timestamp') or ''))} | "
            f"{_escape_markdown_table(str(candidate.get('role') or ''))} | "
            f"{_escape_markdown_table(str(candidate.get('stage') or ''))} | "
            f"{target_use_cell}"
            f"{_escape_markdown_table(str(candidate.get('snippet') or ''))} |"
        )
    if report.get("status") == "no_evidence" or report.get("no_evidence_report", {}).get("needed"):
        no_evidence = report.get("no_evidence_report", {})
        not_searched = no_evidence.get("not_searched_scope", {})
        searched = no_evidence.get("searched_scope", {})
        coverage = no_evidence.get("query_term_coverage") or {}
        missing_terms = coverage.get("missing_terms_any_candidate") or []
        guidance = no_evidence.get("read_more_guidance") or {}
        lines.extend(
            [
                "",
                "## No Evidence",
                "",
                (
                    f"Not found in searched scope only ({searched.get('searched_files', 0)} searched files). "
                    f"Missing selected roots: {not_searched.get('missing_root_count', 0)}. "
                    f"Excluded roots: {len(no_evidence.get('excluded_scope', []))}."
                ),
                "Do not treat this as all-history, global, or permanent absence without more search.",
                "",
                no_evidence.get("why_not_confident", ""),
            ]
        )
        if missing_terms:
            lines.append("")
            lines.append("Missing query terms in surfaced candidates: `" + "`, `".join(str(term) for term in missing_terms) + "`.")
        interpretation = no_evidence.get("candidate_interpretation")
        if interpretation:
            lines.append(f"Candidate interpretation: `{interpretation}`.")
        if guidance:
            required = "required" if guidance.get("required_before_scoped_no_evidence_answer") else "optional diagnostic"
            lines.append(f"Read-more before scoped no-evidence answer: `{required}`.")
    suggestions = report.get("next_search_suggestions") or []
    if suggestions:
        lines.extend(["", "## Next Searches", ""])
        lines.extend(f"- {item}" for item in suggestions)
    return "\n".join(lines)


def render_manifest_text(payload: dict) -> str:
    report = payload["manifest_report"]
    manifest = report["manifest"]
    shortlist = report["shortlist"]
    cost = report["cost_model"]
    metrics = shortlist["metrics"]
    lines = [
        "JSONL Manifest",
        f"Entries: {manifest['entry_count']}",
        f"Build: {cost['build_time_ms']} ms, {manifest['build_metrics'].get('bytes_parsed', 0)} bytes parsed",
        f"Refresh: {cost['refresh_time_ms']} ms, {manifest['no_change_refresh_metrics'].get('files_reused', 0)} files reused",
        f"Estimated manifest size: {cost['manifest_disk_bytes']} bytes",
        (
            "Shortlist: "
            f"{metrics['selected_files']}/{metrics['total_files']} files, "
            f"{metrics['selected_bytes']}/{metrics['total_bytes']} bytes, "
            f"file reduction={metrics['file_reduction_ratio']}, "
            f"byte reduction={metrics['byte_reduction_ratio']}"
        ),
    ]
    duplicate_risk = report.get("duplicate_risk", {})
    duplicate_summary = duplicate_risk.get("summary", {})
    lines.append(
        "Duplicate risk: "
        f"{duplicate_summary.get('group_count', 0)} groups "
        f"({duplicate_summary.get('exact_duplicate_groups', 0)} exact, "
        f"{duplicate_summary.get('likely_mirror_groups', 0)} likely mirror)"
    )
    for group in duplicate_risk.get("groups", [])[:5]:
        labels = ", ".join(group.get("path_labels", [])[:4])
        lines.append(
            "- "
            f"{group.get('risk_type')} {group.get('confidence')} "
            f"members={group.get('member_count')} paths={labels}"
        )
    for entry in shortlist.get("selected_entries", [])[:10]:
        path = entry["entry"]["path"]
        reasons = ", ".join(entry.get("reasons") or ["weak-candidate"])
        lines.append(f"- {path} score={entry['score']} reasons={reasons}")
    for warning in manifest.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_manifest_markdown(payload: dict) -> str:
    report = payload["manifest_report"]
    manifest = report["manifest"]
    shortlist = report["shortlist"]
    cost = report["cost_model"]
    metrics = shortlist["metrics"]
    lines = [
        "# JSONL Manifest",
        "",
        f"**Entries:** {manifest['entry_count']}",
        f"**Build:** {cost['build_time_ms']} ms, {manifest['build_metrics'].get('bytes_parsed', 0)} bytes parsed",
        f"**Refresh:** {cost['refresh_time_ms']} ms, {manifest['no_change_refresh_metrics'].get('files_reused', 0)} files reused",
        f"**Estimated manifest size:** {cost['manifest_disk_bytes']} bytes",
        (
            f"**Shortlist:** {metrics['selected_files']}/{metrics['total_files']} files, "
            f"{metrics['selected_bytes']}/{metrics['total_bytes']} bytes, "
            f"file reduction {metrics['file_reduction_ratio']}, "
            f"byte reduction {metrics['byte_reduction_ratio']}"
        ),
        "",
        "## Cache Policy",
        "",
    ]
    policy = report.get("cache_policy", {})
    for key in ("local_only", "optional", "default_search_uses_cache", "invalidation", "privacy"):
        lines.append(f"- **{key}:** {policy.get(key)}")

    duplicate_risk = report.get("duplicate_risk", {})
    duplicate_summary = duplicate_risk.get("summary", {})
    lines.extend(
        [
            "",
            "## Duplicate Risk",
            "",
            (
                f"- **Groups:** {duplicate_summary.get('group_count', 0)} "
                f"({duplicate_summary.get('exact_duplicate_groups', 0)} exact, "
                f"{duplicate_summary.get('likely_mirror_groups', 0)} likely mirror)"
            ),
        ]
    )
    for group in duplicate_risk.get("groups", [])[:10]:
        labels = ", ".join(group.get("path_labels", [])[:6])
        lines.append(
            f"- **{group.get('risk_type')}** `{group.get('confidence')}`; "
            f"members {group.get('member_count')}; paths {labels}"
        )
    for limitation in duplicate_risk.get("limitations", []):
        lines.append(f"- Limitation: {limitation}")

    lines.extend(
        [
            "",
            "## Selected Entries",
            "",
            "| Score | Path | Date span | Roles | Reasons |",
            "| ---: | --- | --- | --- | --- |",
        ]
    )
    for item in shortlist.get("selected_entries", [])[:25]:
        entry = item["entry"]
        date_span = " to ".join(part for part in [entry.get("date_start"), entry.get("date_end")] if part) or "(unknown)"
        lines.append(
            "| "
            f"{item.get('score')} | "
            f"{_escape_markdown_table(entry.get('path', ''))} | "
            f"{_escape_markdown_table(date_span)} | "
            f"{_escape_markdown_table(', '.join(entry.get('roles') or []) or '(none)')} | "
            f"{_escape_markdown_table(', '.join(item.get('reasons') or []) or 'weak-candidate')} |"
        )

    guidance = report.get("guidance", {})
    lines.extend(["", "## Guidance", ""])
    for item in guidance.get("ultra_valuable_when", []):
        lines.append(f"- Ultra valuable: {item}")
    for item in guidance.get("unnecessary_when", []):
        lines.append(f"- Usually unnecessary: {item}")
    if guidance.get("shortlist_note"):
        lines.append(f"- Note: {guidance['shortlist_note']}")
    return "\n".join(lines)


def render_efficiency_eval_text(payload: dict) -> str:
    report = payload["retrieval_efficiency_eval"]
    if report.get("status") == "invalid_fixture":
        lines = [
            "Retrieval efficiency benchmark: INVALID FIXTURE",
            f"Fixture: {report.get('fixture')}",
        ]
        lines.extend(f"ERROR: {error}" for error in report.get("fixture_errors", []))
        return "\n".join(lines)

    thresholds = report.get("large_impact_thresholds", {})
    lines = [
        "Retrieval efficiency benchmark",
        f"Fixture: {report.get('fixture')}",
        f"Data source: {report.get('data_source')}",
        f"Threshold status: {str(thresholds.get('status', 'unknown')).upper()}",
        f"Note: {report.get('measurement_note')}",
        "Summary:",
    ]
    for name, summary in report.get("summary", {}).items():
        lines.append(
            "- "
            f"{name}: hit_rate={summary.get('first_pass_hit_rate')} "
            f"context_bytes={summary.get('median_prompt_context_bytes')} "
            f"files={summary.get('median_files_opened')} "
            f"tool_calls={summary.get('median_tool_calls')} "
            f"recall@10={summary.get('mean_recall_at_10')}"
        )
    if thresholds.get("items"):
        lines.append("Thresholds:")
        for item in thresholds["items"]:
            status = "PASS" if item.get("passed") else "FAIL"
            lines.append(
                f"- {status} {item.get('metric')} {item.get('operator')} "
                f"{item.get('target')} (actual {item.get('actual')})"
            )
    followup = report.get("coverage_needs_followup") or {}
    stages = followup.get("candidate_generation_stages") or []
    modes = followup.get("failure_modes") or []
    if stages or modes:
        lines.append(f"Follow-up stages: {', '.join(stages) if stages else '(none)'}")
        lines.append(f"Follow-up failure modes: {', '.join(modes) if modes else '(none)'}")
    embedding_spike = report.get("embedding_spike")
    if embedding_spike:
        lines.extend(render_embedding_spike_text_lines(embedding_spike))
    return "\n".join(lines)


def render_measured_benchmark_text(payload: dict) -> str:
    report = payload["measured_benchmark"]
    aggregate = report.get("aggregate", {})
    lines = [
        "Measured local benchmark",
        f"Status: {report.get('status')}",
        f"Run: {report.get('run_id')}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        (
            "Totals: "
            f"elapsed_ms={aggregate.get('total_elapsed_ms', 0)} "
            f"searched_files={aggregate.get('searched_files', 0)} "
            f"searched_bytes={aggregate.get('searched_bytes', 0)} "
            f"candidates={aggregate.get('candidate_count', 0)} "
            f"output_context_bytes={aggregate.get('output_context_bytes', 0)}"
        ),
    ]
    statuses = aggregate.get("statuses", {})
    if statuses:
        lines.append("Statuses: " + ", ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    lines.extend(_performance_metric_text_lines(report))
    for gate_name in ("ranking_quality_gate", "rg_prefilter_gate", "manifest_shortlist_gate", "timestamp_sidecar_gate"):
        gate = report.get(gate_name) or {}
        if gate:
            lines.append(
                f"{gate_name}: status={gate.get('status')} "
                f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
            )
    prefilter = aggregate.get("rg_prefilter") or {}
    if prefilter.get("enabled_rows"):
        lines.append(
            "rg_prefilter: "
            f"files_reduced={prefilter.get('files_reduced', 0)} "
            f"bytes_reduced={prefilter.get('bytes_reduced', 0)} "
            f"statuses={prefilter.get('statuses', {})}"
        )
    manifest = aggregate.get("manifest_shortlist") or {}
    if manifest.get("enabled_rows"):
        lines.append(
            "manifest_shortlist: "
            f"planned_rows={manifest.get('planned_rows', 0)} "
            f"fallback_rows={manifest.get('fallback_rows', 0)} "
            f"files_reduction_ratio={manifest.get('file_reduction_ratio', 0.0)} "
            f"bytes_reduction_ratio={manifest.get('byte_reduction_ratio', 0.0)} "
            f"python_opened_files={manifest.get('python_opened_files', 0)} "
            f"parsed_bytes={manifest.get('parsed_bytes', 0)} "
            f"source_ref_equivalence={_compact_counter_map(manifest.get('source_ref_equivalence') or {})} "
            f"no_evidence_scope_equivalence={_compact_counter_map(manifest.get('no_evidence_scope_equivalence') or {})}"
        )
    timestamp_seek = aggregate.get("timestamp_seek_prototype") or {}
    if timestamp_seek.get("enabled_rows"):
        lines.append(
            "timestamp_seek_prototype: "
            f"planned_rows={timestamp_seek.get('planned_rows', 0)} "
            f"fallback_rows={timestamp_seek.get('fallback_rows', 0)} "
            f"parsed_bytes={timestamp_seek.get('parsed_bytes', 0)} "
            f"records_considered={timestamp_seek.get('records_considered', 0)} "
            f"sidecar_hits={timestamp_seek.get('sidecar_hits', 0)} "
            f"sidecar_misses={timestamp_seek.get('sidecar_misses', 0)} "
            f"ordering_equivalence={_compact_counter_map(timestamp_seek.get('ordering_equivalence') or {})} "
            f"source_ref_equivalence={_compact_counter_map(timestamp_seek.get('source_ref_equivalence') or {})}"
        )
    for warning in aggregate.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for row in report.get("rows", []):
        row_id = row.get("case_id") or "(unknown)"
        lines.append(
            "- "
            f"{row_id}: {row.get('result_status')} "
            f"elapsed_ms={row.get('elapsed_ms')} "
            f"files={row.get('searched_files')} "
            f"bytes={row.get('searched_bytes')} "
            f"candidates={row.get('candidate_count')} "
            f"context_bytes={row.get('output_context_bytes')}"
        )
        manifest_row = row.get("manifest_shortlist") or {}
        if manifest_row.get("enabled"):
            lines[-1] += (
                f" manifest_source_ref_equivalence={manifest_row.get('source_ref_equivalence')} "
                f"manifest_no_evidence_scope_equivalence={manifest_row.get('no_evidence_scope_equivalence')}"
            )
        timestamp_row = row.get("timestamp_seek_prototype") or {}
        if timestamp_row.get("enabled"):
            lines[-1] += (
                f" timestamp_seek_status={timestamp_row.get('status')} "
                f"timestamp_ordering_equivalence={timestamp_row.get('ordering_equivalence')}"
            )
    return "\n".join(lines)


def render_measured_benchmark_markdown(payload: dict) -> str:
    report = payload["measured_benchmark"]
    aggregate = report.get("aggregate", {})
    lines = [
        "# Measured Local Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Run:** `{report.get('run_id')}`",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        "",
        "## Aggregate",
        "",
        f"- Total elapsed ms: {aggregate.get('total_elapsed_ms', 0)}",
        f"- Searched files: {aggregate.get('searched_files', 0)}",
        f"- Searched bytes: {aggregate.get('searched_bytes', 0)}",
        f"- Candidate count: {aggregate.get('candidate_count', 0)}",
        f"- Output context bytes: {aggregate.get('output_context_bytes', 0)}",
        f"- Statuses: {', '.join(f'{key}={value}' for key, value in sorted((aggregate.get('statuses') or {}).items())) or '(none)'}",
    ]
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(_benchmark_gate_markdown_lines(report))
    prefilter = aggregate.get("rg_prefilter") or {}
    if prefilter.get("enabled_rows"):
        lines.extend(
            [
                f"- rg prefilter files reduced: {prefilter.get('files_reduced', 0)}",
                f"- rg prefilter bytes reduced: {prefilter.get('bytes_reduced', 0)}",
                f"- rg prefilter statuses: {', '.join(f'{key}={value}' for key, value in sorted((prefilter.get('statuses') or {}).items())) or '(none)'}",
            ]
        )
    manifest = aggregate.get("manifest_shortlist") or {}
    if manifest.get("enabled_rows"):
        lines.extend(
            [
                f"- manifest shortlist planned rows: {manifest.get('planned_rows', 0)}",
                f"- manifest shortlist fallback rows: {manifest.get('fallback_rows', 0)}",
                f"- manifest shortlist files reduction ratio: {manifest.get('file_reduction_ratio', 0.0)}",
                f"- manifest shortlist bytes reduction ratio: {manifest.get('byte_reduction_ratio', 0.0)}",
                f"- manifest shortlist Python-opened files: {manifest.get('python_opened_files', 0)}",
                f"- manifest shortlist parsed bytes: {manifest.get('parsed_bytes', 0)}",
                f"- manifest shortlist source-ref equivalence: `{_compact_counter_map(manifest.get('source_ref_equivalence') or {})}`",
                f"- manifest shortlist no-evidence scope equivalence: `{_compact_counter_map(manifest.get('no_evidence_scope_equivalence') or {})}`",
            ]
        )
    timestamp_seek = aggregate.get("timestamp_seek_prototype") or {}
    if timestamp_seek.get("enabled_rows"):
        lines.extend(
            [
                f"- timestamp seek planned rows: {timestamp_seek.get('planned_rows', 0)}",
                f"- timestamp seek fallback rows: {timestamp_seek.get('fallback_rows', 0)}",
                f"- timestamp seek parsed bytes: {timestamp_seek.get('parsed_bytes', 0)}",
                f"- timestamp seek records considered: {timestamp_seek.get('records_considered', 0)}",
                f"- timestamp seek hits/misses: {timestamp_seek.get('sidecar_hits', 0)}/{timestamp_seek.get('sidecar_misses', 0)}",
                f"- timestamp seek median parsed bytes: {timestamp_seek.get('parsed_bytes_median', 0)}",
                f"- timestamp seek p90 elapsed ms: {timestamp_seek.get('elapsed_ms_p90', 0)}",
                f"- timestamp seek ordering equivalence: `{_compact_counter_map(timestamp_seek.get('ordering_equivalence') or {})}`",
                f"- timestamp seek source-ref equivalence: `{_compact_counter_map(timestamp_seek.get('source_ref_equivalence') or {})}`",
            ]
        )
    if aggregate.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in aggregate.get("warnings", []))
    if report.get("rows"):
        include_timestamp_columns = "timestamp_seek_prototype" in aggregate
        lines.extend(["", "## Runs", ""])
        if include_timestamp_columns:
            lines.extend(
                [
                    "| Probe | Source | Status | Elapsed ms | Files | Bytes | Candidates | Context bytes | Manifest source refs | Manifest no-evidence scope | Timestamp status | Timestamp ordering | Warnings |",
                    "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |",
                ]
            )
        else:
            lines.extend(
                [
                    "| Probe | Source | Status | Elapsed ms | Files | Bytes | Candidates | Context bytes | Manifest source refs | Manifest no-evidence scope | Warnings |",
                    "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
                ]
            )
        for row in report.get("rows", []):
            row_id = row.get("case_id") or ""
            manifest_row = row.get("manifest_shortlist") or {}
            timestamp_row = row.get("timestamp_seek_prototype") or {}
            line = (
                "| "
                f"{_escape_markdown_table(str(row_id))} | "
                f"{_escape_markdown_table(str(row.get('query_source', '')))} | "
                f"{_escape_markdown_table(str(row.get('result_status', '')))} | "
                f"{row.get('elapsed_ms', 0)} | "
                f"{row.get('searched_files', 0)} | "
                f"{row.get('searched_bytes', 0)} | "
                f"{row.get('candidate_count', 0)} | "
                f"{row.get('output_context_bytes', 0)} | "
                f"{_escape_markdown_table(str(manifest_row.get('source_ref_equivalence', '')))} | "
                f"{_escape_markdown_table(str(manifest_row.get('no_evidence_scope_equivalence', '')))} | "
            )
            if include_timestamp_columns:
                line += (
                    f"{_escape_markdown_table(str(timestamp_row.get('status', '')))} | "
                    f"{_escape_markdown_table(str(timestamp_row.get('ordering_equivalence', '')))} | "
                )
            line += f"{_escape_markdown_table(', '.join(row.get('warnings') or []))} |"
            lines.append(line)
    return "\n".join(lines)


def _compact_counter_map(values: dict) -> str:
    if not values:
        return "(none)"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def _performance_metric_text_lines(report: dict) -> list[str]:
    metrics = report.get("performance_metrics") or {}
    if not metrics:
        return []
    local_work = metrics.get("local_tool_work") or {}
    handoff = metrics.get("agent_model_handoff") or {}
    memory = metrics.get("memory") or {}
    local_tokens = local_work.get("approximate_token_estimates") or {}
    handoff_tokens = handoff.get("approximate_token_estimates") or {}
    lines = [
        (
            "Metric contract: "
            f"approximate_tokens={TOKEN_FORMULA}, tokenizer=none; "
            "parsed bytes are tool-side work proxies, not direct model token savings"
        ),
        (
            "Performance metrics: "
            f"wall_time_ms={(metrics.get('timing') or {}).get('wall_time_ms', 0)} "
            f"bytes_parsed={local_work.get('bytes_parsed', 0)} "
            f"approx_parsed_tokens={local_tokens.get('bytes_parsed_approx_tokens', 0)} "
            f"handoff_bytes={handoff.get('output_context_bytes', 0)} "
            f"approx_handoff_tokens={handoff_tokens.get('output_context_bytes_approx_tokens', 0)}"
        ),
    ]
    if memory.get("status") == "measured":
        lines.append(
            "Memory: "
            f"measured peak_rss_bytes={memory.get('peak_rss_bytes', 0)} "
            f"observed_duration_ms={memory.get('observed_duration_ms', 0)} "
            f"rss_duration_above_thresholds_ms={memory.get('rss_duration_above_thresholds_ms', {})}"
        )
    else:
        lines.append(f"Memory: not_measured reason={memory.get('reason', 'rss_not_sampled')}")
    for tradeoff in metrics.get("tradeoffs") or []:
        lines.append(f"Tradeoff: {tradeoff}")
    return lines


def _performance_metric_markdown_lines(report: dict) -> list[str]:
    metrics = report.get("performance_metrics") or {}
    if not metrics:
        return []
    local_work = metrics.get("local_tool_work") or {}
    handoff = metrics.get("agent_model_handoff") or {}
    memory = metrics.get("memory") or {}
    local_tokens = local_work.get("approximate_token_estimates") or {}
    handoff_tokens = handoff.get("approximate_token_estimates") or {}
    lines = [
        "",
        "## Metric Contract",
        "",
        f"- Approximate tokens: `{TOKEN_FORMULA}`, tokenizer=`none`.",
        "- Parsed/scanned bytes are tool-side work proxies, not direct model token savings.",
        (
            "- Local work: "
            f"bytes parsed `{local_work.get('bytes_parsed', 0)}`, "
            f"approx parsed tokens `{local_tokens.get('bytes_parsed_approx_tokens', 0)}`, "
            f"files opened `{local_work.get('files_opened', 0)}`."
        ),
        (
            "- Agent/model handoff: "
            f"output context bytes `{handoff.get('output_context_bytes', 0)}`, "
            f"approx handoff tokens `{handoff_tokens.get('output_context_bytes_approx_tokens', 0)}`."
        ),
    ]
    if memory.get("status") == "measured":
        lines.extend(
            [
                (
                    "- Memory: "
                    f"`measured`, peak RSS `{memory.get('peak_rss_bytes', 0)}`, "
                    f"observed duration ms `{memory.get('observed_duration_ms', 0)}`."
                ),
                f"- RSS duration above thresholds ms: `{memory.get('rss_duration_above_thresholds_ms', {})}`.",
            ]
        )
    else:
        lines.append(f"- Memory: `not_measured`, reason `{memory.get('reason', 'rss_not_sampled')}`.")
    tradeoffs = metrics.get("tradeoffs") or []
    if tradeoffs:
        lines.append("- Tradeoff context: " + " ".join(tradeoffs))
    return lines


def render_embedding_experiment_text(payload: dict) -> str:
    report = payload["embedding_experiment"]
    aggregate = report.get("aggregate", {})
    index_metrics = report.get("index_metrics") or aggregate.get("index_metrics", {})
    query_metrics = report.get("query_metrics") or aggregate.get("query_metrics", {})
    backend = report.get("backend", {})
    gate = report.get("embedding_recall_gate", {})
    storage = report.get("storage_metrics") or {}
    batching = report.get("batching") or {}
    baseline_comparison = report.get("baseline_comparison") or {}
    streaming = report.get("streaming_enumeration") or {}
    lines = [
        "Embedding experiment",
        f"Status: {report.get('status')}",
        f"Mode: {report.get('experiment_mode')}",
        f"Backend: {backend.get('selected')} requested={backend.get('requested')} fallback={backend.get('fallback_used')}",
        f"Production status: {report.get('production_status')}",
        (
            "Index: "
            f"documents={index_metrics.get('document_count', 0)} "
            f"build_ms={index_metrics.get('index_build_time_ms', 0)} "
            f"peak_memory_bytes={index_metrics.get('peak_memory_bytes', 0)} "
            f"disk_bytes={index_metrics.get('disk_index_size_bytes', 0)} "
            f"refresh={index_metrics.get('refresh_status')}"
        ),
        (
            "Queries: "
            f"cases={query_metrics.get('case_count', 0)} "
            f"median_ms={query_metrics.get('median_query_latency_ms', 0)} "
            f"p90_ms={query_metrics.get('p90_query_latency_ms', 0)} "
            f"embedding_candidates={query_metrics.get('candidate_count_total', 0)} "
            f"baseline_candidates={query_metrics.get('baseline_candidate_count_total', 0)}"
        ),
        f"embedding_recall_gate: status={gate.get('status')} reasons={', '.join(gate.get('reasons') or []) or '(none)'}",
        (
            "Storage: "
            f"shards={storage.get('shard_count', 0)} "
            f"vector_bytes={storage.get('vector_bytes', 0)} "
            f"metadata_bytes={storage.get('metadata_bytes', 0)} "
            f"manifest_complete={storage.get('manifest_complete')}"
        ),
        (
            "Batching: "
            f"fastembed_batch_size={batching.get('fastembed_batch_size')} "
            f"shard_size={batching.get('shard_size')} "
            f"documents_embedded={batching.get('documents_embedded', 0)} "
            f"documents_reused={batching.get('documents_reused', 0)}"
        ),
        (
            "Baseline comparison: "
            f"mode={baseline_comparison.get('mode')} "
            f"status={baseline_comparison.get('status')} "
            f"correctness_available={baseline_comparison.get('correctness_comparison_available')}"
        ),
        (
            "Streaming enumeration: "
            f"enabled={streaming.get('enabled')} "
            f"mode={streaming.get('mode')} "
            f"documents_retained_for_index_build={streaming.get('documents_retained_for_index_build')}"
        ),
    ]
    lines.extend(_performance_metric_text_lines(report))
    if aggregate.get("cap_hit_labels"):
        lines.append("Cap hits: " + ", ".join(aggregate.get("cap_hit_labels", [])))
    memory_profile = report.get("memory_profile") or {}
    if memory_profile.get("enabled"):
        metric_memory = ((report.get("performance_metrics") or {}).get("memory") or {})
        lines.append(
            "Memory profile: "
            f"samples={memory_profile.get('sample_count', 0)} "
            f"peak_rss_bytes={memory_profile.get('peak_observed_rss_bytes', 0)} "
            f"observed_duration_ms={metric_memory.get('observed_duration_ms', 0)} "
            f"rss_duration_above_thresholds_ms={metric_memory.get('rss_duration_above_thresholds_ms', {})} "
            f"process_max_rss_bytes={memory_profile.get('process_max_rss_bytes', 0)} "
            f"tracemalloc_python_peak_bytes={memory_profile.get('tracemalloc_python_peak_bytes', 0)}"
        )
    onnx_profile = report.get("onnx_profile") or {}
    if onnx_profile.get("requested"):
        lines.append(f"ONNX profile: status={onnx_profile.get('status')}")
    for failure in aggregate.get("failure_modes", []):
        lines.append(f"Failure mode: {failure}")
    for row in report.get("rows", []):
        lines.append(
            "- "
            f"{row.get('case_id')}: {row.get('case_family')} "
            f"embedding_candidates={row.get('candidate_count')} "
            f"baseline_candidates={row.get('baseline_candidate_count')} "
            f"latency_ms={row.get('query_latency_ms')} "
            f"protected_regression={row.get('protected_regression')} "
            f"baseline_status={(row.get('baseline_comparison') or {}).get('status')}"
        )
    return "\n".join(lines)


def render_embedding_experiment_markdown(payload: dict) -> str:
    report = payload["embedding_experiment"]
    aggregate = report.get("aggregate", {})
    index_metrics = report.get("index_metrics") or aggregate.get("index_metrics", {})
    query_metrics = report.get("query_metrics") or aggregate.get("query_metrics", {})
    backend = report.get("backend", {})
    gate = report.get("embedding_recall_gate", {})
    storage = report.get("storage_metrics") or {}
    batching = report.get("batching") or {}
    chunking = report.get("chunking") or {}
    baseline_comparison = report.get("baseline_comparison") or {}
    streaming = report.get("streaming_enumeration") or {}
    lines = [
        "# Embedding Experiment",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Mode:** `{report.get('experiment_mode')}`",
        f"**Backend:** `{backend.get('selected')}` requested=`{backend.get('requested')}` fallback=`{backend.get('fallback_used')}`",
        f"**Production status:** `{report.get('production_status')}`",
        f"**Default live search changed:** `{report.get('default_live_search_changed')}`",
        "",
        "## Metrics",
        "",
        f"- Documents indexed: {index_metrics.get('document_count', 0)}",
        f"- Index build ms: {index_metrics.get('index_build_time_ms', 0)}",
        f"- Peak memory bytes: {index_metrics.get('peak_memory_bytes', 0)}",
        f"- Disk index bytes: {index_metrics.get('disk_index_size_bytes', 0)}",
        f"- Vector shard count: {storage.get('shard_count', 0)}",
        f"- Vector shard bytes: {storage.get('vector_bytes', 0)}",
        f"- Shard metadata bytes: {storage.get('metadata_bytes', 0)}",
        f"- FastEmbed batch size: {batching.get('fastembed_batch_size')}",
        f"- Shard size: {batching.get('shard_size')}",
        f"- Documents embedded this run: {batching.get('documents_embedded', 0)}",
        f"- Documents reused from shards: {batching.get('documents_reused', 0)}",
        f"- Chunking enabled: `{chunking.get('enabled')}`",
        f"- Refresh status: `{index_metrics.get('refresh_status')}`",
        f"- Refresh/no-change cost ms: {index_metrics.get('refresh_no_change_cost_ms', 0)}",
        f"- Query cases: {query_metrics.get('case_count', 0)}",
        f"- Median query ms: {query_metrics.get('median_query_latency_ms', 0)}",
        f"- P90 query ms: {query_metrics.get('p90_query_latency_ms', 0)}",
        f"- Embedding candidate count: {query_metrics.get('candidate_count_total', 0)}",
        f"- Baseline candidate count: {query_metrics.get('baseline_candidate_count_total', 0)}",
        f"- Protected regressions: {query_metrics.get('protected_regressions', 0)}",
        f"- Baseline comparison mode: `{baseline_comparison.get('mode')}`",
        f"- Baseline comparison status: `{baseline_comparison.get('status')}`",
        f"- Correctness comparison available: `{baseline_comparison.get('correctness_comparison_available')}`",
        f"- Streaming enumeration enabled: `{streaming.get('enabled')}`",
        f"- Streaming enumeration mode: `{streaming.get('mode')}`",
        f"- Documents retained for index build: `{streaming.get('documents_retained_for_index_build')}`",
    ]
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(
        [
            "",
            "## Gate Status",
            "",
            "| Gate | Status | Reasons |",
            "| --- | --- | --- |",
            (
                "| `embedding_recall_gate` | "
                f"`{gate.get('status')}` | "
                f"{_escape_markdown_table(', '.join(gate.get('reasons') or [])) or '(none)'} |"
            ),
        ]
    )
    if aggregate.get("cap_hit_labels"):
        lines.extend(["", "## Caps", ""])
        lines.extend(f"- `{label}`" for label in aggregate.get("cap_hit_labels", []))
    if aggregate.get("failure_modes"):
        lines.extend(["", "## Failure Modes", ""])
        lines.extend(f"- `{failure}`" for failure in aggregate.get("failure_modes", []))
    memory_profile = report.get("memory_profile") or {}
    if memory_profile.get("enabled"):
        metric_memory = ((report.get("performance_metrics") or {}).get("memory") or {})
        lines.extend(
            [
                "",
                "## Memory Profile",
                "",
                f"- Samples: {memory_profile.get('sample_count', 0)}",
                (
                    "- RSS summary: "
                    f"peak observed bytes `{memory_profile.get('peak_observed_rss_bytes', 0)}`, "
                    f"observed duration ms `{metric_memory.get('observed_duration_ms', 0)}`, "
                    f"duration above thresholds ms `{metric_memory.get('rss_duration_above_thresholds_ms', {})}`."
                ),
                f"- Process max RSS bytes: {memory_profile.get('process_max_rss_bytes', 0)}",
                f"- Tracemalloc Python peak bytes: {memory_profile.get('tracemalloc_python_peak_bytes', 0)}",
                f"- Tracemalloc scope: `{memory_profile.get('tracemalloc_scope')}`",
                f"- Native memory note: {memory_profile.get('native_memory_note')}",
                "",
                "| Phase | Event | Elapsed ms | RSS bytes | Max RSS bytes | Counters |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for sample in memory_profile.get("samples", [])[:80]:
            counters = sample.get("counters") or {}
            counter_text = ", ".join(f"{key}={value}" for key, value in sorted(counters.items()))
            lines.append(
                "| "
                f"{_escape_markdown_table(str(sample.get('phase', '')))} | "
                f"{_escape_markdown_table(str(sample.get('event', '')))} | "
                f"{sample.get('elapsed_ms', 0)} | "
                f"{sample.get('rss_bytes', 0)} | "
                f"{sample.get('max_rss_bytes', 0)} | "
                f"{_escape_markdown_table(counter_text)} |"
            )
    root_cause_profile = report.get("root_cause_profile") or {}
    hypotheses = root_cause_profile.get("hypotheses_ranked") or []
    if hypotheses:
        lines.extend(
            [
                "",
                "## Root Cause Profile",
                "",
                "| Rank | Hypothesis | Confidence | Evidence | Follow-up |",
                "| ---: | --- | --- | --- | --- |",
            ]
        )
        for item in hypotheses:
            lines.append(
                "| "
                f"{item.get('rank', '')} | "
                f"{_escape_markdown_table(str(item.get('hypothesis', '')))} | "
                f"{_escape_markdown_table(str(item.get('confidence', '')))} | "
                f"{_escape_markdown_table(str(item.get('evidence', '')))} | "
                f"{_escape_markdown_table(str(item.get('recommended_follow_up', '')))} |"
            )
    private_artifact_audit = report.get("private_artifact_audit") or {}
    audit_items = private_artifact_audit.get("items") or []
    if audit_items:
        lines.extend(
            [
                "",
                "## Private Artifact Audit",
                "",
                f"- Scope: `{private_artifact_audit.get('scope')}`",
                f"- Deleted anything: `{private_artifact_audit.get('deleted_anything')}`",
                f"- Raw transcript paths committed: `{private_artifact_audit.get('raw_transcript_paths_committed')}`",
                f"- Artifact paths included: `{private_artifact_audit.get('artifact_paths_included')}`",
                "",
                "| Label | Path | Size bytes approx | Classification | Reason |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for item in audit_items:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(item.get('label', '')))} | "
                f"{_escape_markdown_table(str(item.get('path', '')))} | "
                f"{item.get('size_bytes_approx', 0)} | "
                f"{_escape_markdown_table(str(item.get('classification', '')))} | "
                f"{_escape_markdown_table(str(item.get('reason', '')))} |"
            )
    onnx_profile = report.get("onnx_profile") or {}
    if onnx_profile.get("requested"):
        lines.extend(
            [
                "",
                "## ONNX Profile",
                "",
                f"- Status: `{onnx_profile.get('status')}`",
                f"- Raw profile files committed: `{onnx_profile.get('raw_profile_files_committed')}`",
            ]
        )
        if onnx_profile.get("reason"):
            lines.append(f"- Reason: {onnx_profile.get('reason')}")
        if onnx_profile.get("next_step"):
            lines.append(f"- Next step: {onnx_profile.get('next_step')}")
    selected_roots = report.get("scope", {}).get("selected_roots") or []
    if selected_roots:
        lines.extend(["", "## Scope", ""])
        lines.extend(
            f"- `{root.get('root_label')}` `{root.get('kind')}` known=`{root.get('known')}`"
            for root in selected_roots
        )
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Rows",
                "",
                "| Case | Family | Source | Embedding candidates | Baseline candidates | Latency ms | Protected regression |",
                "| --- | --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in report.get("rows", []):
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('case_family', '')))} | "
                f"{_escape_markdown_table(str(row.get('query_source', '')))} | "
                f"{row.get('candidate_count', 0)} | "
                f"{row.get('baseline_candidate_count', 0)} | "
                f"{row.get('query_latency_ms', 0)} | "
                f"`{row.get('protected_regression')}` |"
            )
    lines.extend(["", "## Privacy", ""])
    privacy = report.get("privacy_contract", {})
    for key in (
        "raw_private_text_committed",
        "private_vectors_committed",
        "private_indexes_committed",
        "raw_query_text_committed",
        "raw_transcript_paths_committed",
        "exact_artifact_paths_may_be_committed",
        "remote_upload",
        "default_search_promotion",
    ):
        lines.append(f"- {key}: `{privacy.get(key)}`")
    return "\n".join(lines)


def render_impact_benchmark_text(payload: dict) -> str:
    report = payload["impact_benchmark"]
    lines = [
        "Impact benchmark",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Run: {report.get('run_id', '(none)')}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        f"Total wall time ms: {report.get('total_wall_time_ms', 0)}",
    ]
    lines.extend(_performance_metric_text_lines(report))
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    if report.get("fixture_errors"):
        lines.append("Fixture errors:")
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
    for gate_name in ("ranking_quality_gate", "rg_prefilter_gate"):
        gate = report.get(gate_name) or {}
        if gate:
            lines.append(
                f"{gate_name}: status={gate.get('status')} "
                f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
            )
    summary = report.get("summary", {})
    if summary:
        lines.append("Summary:")
        for name, row in summary.items():
            lines.append(
                "- "
                f"{name}: hit_rate={row.get('first_pass_hit_rate')} "
                f"recall@10={row.get('mean_recall_at_10')} "
                f"no_evidence={row.get('no_evidence_correct')}/{row.get('no_evidence_cases')} "
                f"tool_calls={row.get('median_tool_calls')} "
                f"subprocess_calls={row.get('median_subprocess_calls')} "
                f"files={row.get('median_files_opened')} "
                f"prefilter_files_reduced={row.get('median_prefilter_files_reduced', 0)} "
                f"context_bytes={row.get('median_output_context_bytes')}"
            )
    for row in report.get("rows", []):
        lines.append(
            "- "
            f"{row.get('case_id')}/{row.get('baseline')}: "
            f"first_pass={row.get('first_pass_hit')} "
            f"wall_ms={row.get('wall_time_ms')} "
            f"subprocess_calls={row.get('subprocess_calls')} "
            f"recall@10={row.get('recall_at_10')} "
            f"no_evidence_correct={row.get('no_evidence_correct')} "
            f"candidates={row.get('candidate_count')} "
            f"context_bytes={row.get('output_context_bytes')}"
        )
    return "\n".join(lines)


def render_mechanism_eval_text(payload: dict) -> str:
    report = payload["mechanism_eval"]
    gate = report.get("mechanism_eval_gate", {})
    metadata = report.get("metadata_discovery", {})
    lines = [
        "Mechanism eval",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Gate: {gate.get('status')} ({', '.join(gate.get('reasons') or [])})",
        f"Metadata candidates: {metadata.get('candidate_count', 0)}",
        "Promotion: unpromoted tiny synthetic slice; no default search, sidecar, ranking, token-efficiency, final-answer-quality, or product-value claim.",
    ]
    for row in report.get("rows", []):
        prefilter = row.get("rg_prefilter") or {}
        supplement = row.get("metadata_supplement") or {}
        lines.append(
            "- "
            f"{row.get('case_id')}/{row.get('baseline')}: "
            f"status={row.get('case_status')} "
            f"source_ref={row.get('source_ref_status')} "
            f"files={row.get('searched_files')} "
            f"bytes={row.get('searched_bytes')} "
            f"needle_policy={prefilter.get('needle_policy')} "
            f"needle_count={prefilter.get('needle_count')} "
            f"metadata_candidates={row.get('metadata_candidate_count')} "
            f"supplement_terms={supplement.get('term_count', 0)} "
            f"supplement_outcome={row.get('metadata_supplement_outcome')}"
        )
    return "\n".join(lines)


def render_mechanism_eval_markdown(payload: dict) -> str:
    report = payload["mechanism_eval"]
    gate = report.get("mechanism_eval_gate", {})
    lines = [
        "# Tiny Retrieval Mechanism Eval",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Gate:** `{gate.get('status')}`",
        "",
        "This is an unpromoted synthetic slice. It does not enable default `rg`, generated metadata, sidecars, indexes, ranking changes, token-efficiency claims, final-answer-quality claims, or product-value claims.",
        "",
        "## Gate Reasons",
    ]
    lines.extend(f"- `{reason}`" for reason in gate.get("reasons", []))
    metadata = report.get("metadata_discovery", {})
    lines.extend(
        [
            "",
            "## Metadata Discovery",
            f"- Status: `{metadata.get('status')}`",
            f"- Candidate count: `{metadata.get('candidate_count', 0)}`",
            f"- Candidate kinds: {', '.join(f'`{kind}`' for kind in metadata.get('candidate_kinds', [])) or '(none)'}",
            "",
            "## Rows",
            "| case | baseline | case status | source-ref status | searched files | searched bytes | needle policy | needle count | metadata candidates | supplement terms | supplement outcome |",
            "| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in report.get("rows", []):
        prefilter = row.get("rg_prefilter") or {}
        supplement = row.get("metadata_supplement") or {}
        lines.append(
            "| "
            f"`{row.get('case_id')}` | `{row.get('baseline')}` | `{row.get('case_status')}` | "
            f"`{row.get('source_ref_status')}` | {row.get('searched_files')} | {row.get('searched_bytes')} | "
            f"`{prefilter.get('needle_policy')}` | {prefilter.get('needle_count')} | {row.get('metadata_candidate_count')} | "
            f"{supplement.get('term_count', 0)} | `{row.get('metadata_supplement_outcome')}` |"
        )
    return "\n".join(lines)


def render_impact_benchmark_markdown(payload: dict) -> str:
    report = payload["impact_benchmark"]
    lines = [
        "# JSH-026 Impact Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Run:** `{report.get('run_id', '(none)')}`",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        f"**Total wall time ms:** {report.get('total_wall_time_ms', 0)}",
    ]
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(["", "## Privacy Contract", ""])
    privacy = report.get("privacy_contract", {})
    for key, value in privacy.items():
        lines.append(f"- `{key}`: `{value}`")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in report.get("warnings", []))
    if report.get("fixture_errors"):
        lines.extend(["", "## Fixture Errors", ""])
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
    lines.extend(_benchmark_gate_markdown_lines(report))
    if report.get("summary"):
        lines.extend(
            [
                "",
                "## Baseline Summary",
                "",
                "| Baseline | Hit rate | Recall@1 | Recall@3 | Recall@10 | No-evidence | Wall ms | Tool calls | Subprocess calls | Files | Bytes scanned | Bytes parsed | Prefilter files reduced | Prefilter bytes reduced | Context bytes | Candidates |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, row in report.get("summary", {}).items():
            no_evidence = f"{row.get('no_evidence_correct')}/{row.get('no_evidence_cases')}"
            lines.append(
                "| "
                f"{_escape_markdown_table(str(name))} | "
                f"{row.get('first_pass_hit_rate')} | "
                f"{row.get('mean_recall_at_1')} | "
                f"{row.get('mean_recall_at_3')} | "
                f"{row.get('mean_recall_at_10')} | "
                f"{no_evidence} | "
                f"{row.get('median_wall_time_ms')} | "
                f"{row.get('median_tool_calls')} | "
                f"{row.get('median_subprocess_calls')} | "
                f"{row.get('median_files_opened')} | "
                f"{row.get('median_bytes_scanned')} | "
                f"{row.get('median_bytes_parsed')} | "
                f"{row.get('median_prefilter_files_reduced', 0)} | "
                f"{row.get('median_prefilter_bytes_reduced', 0)} | "
                f"{row.get('median_output_context_bytes')} | "
                f"{row.get('median_candidate_count')} |"
            )
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Case Runs",
                "",
                "| Case | Family | Baseline | Outcome | First pass | Recall@1 | Recall@3 | Recall@10 | No-evidence correct | Wall ms | Tool calls | Subprocess calls | Files | Context bytes | Candidates |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in report.get("rows", []):
            no_evidence = row.get("no_evidence_correct")
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('protected_feature_family', '')))} | "
                f"{_escape_markdown_table(str(row.get('baseline', '')))} | "
                f"{_escape_markdown_table(str(row.get('expected_outcome', '')))} | "
                f"{row.get('first_pass_hit')} | "
                f"{row.get('recall_at_1')} | "
                f"{row.get('recall_at_3')} | "
                f"{row.get('recall_at_10')} | "
                f"{'' if no_evidence is None else no_evidence} | "
                f"{row.get('wall_time_ms')} | "
                f"{row.get('tool_calls')} | "
                f"{row.get('subprocess_calls')} | "
                f"{row.get('files_opened')} | "
                f"{row.get('output_context_bytes')} | "
                f"{row.get('candidate_count')} |"
            )
    return "\n".join(lines)


def render_real_history_impact_text(payload: dict) -> str:
    report = payload["real_history_impact_benchmark"]
    lines = [
        "Real-history impact benchmark",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Run: {report.get('run_id', '(none)')}",
        f"Cases: {report.get('case_count', 0)}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        f"Total wall time ms: {report.get('total_wall_time_ms', 0)}",
    ]
    lines.extend(_performance_metric_text_lines(report))
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    if report.get("cap_hit_labels"):
        lines.append(f"Cap hits: {', '.join(report.get('cap_hit_labels', []))}")
        cap_counts = (report.get("aggregate") or {}).get("cap_hit_rows_by_label") or {}
        if cap_counts:
            lines.append(
                "Cap-hit rows by label: "
                + ", ".join(f"{label}={count}" for label, count in sorted(cap_counts.items()))
            )
    pre_parse = (report.get("aggregate") or {}).get("pre_parse") or {}
    if pre_parse:
        lines.append(
            "pre_parse: "
            f"status={pre_parse.get('status')} "
            f"runs={pre_parse.get('runs')} "
            f"active={pre_parse.get('active_runs')} "
            f"statuses={pre_parse.get('status_counts')} "
            f"anchors={pre_parse.get('anchor_kind_counts')} "
            f"raw_examined={pre_parse.get('raw_lines_examined')} "
            f"skipped_before_parse={pre_parse.get('raw_lines_skipped_before_full_json_parse')} "
            f"full_json_records_parsed={pre_parse.get('full_json_records_parsed')}"
        )
    for gate_name in ("ranking_quality_gate", "rg_prefilter_gate"):
        gate = report.get(gate_name) or {}
        if gate:
            lines.append(
                f"{gate_name}: status={gate.get('status')} "
                f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
            )
    coverage = report.get("rg_prefilter_coverage") or {}
    if coverage:
        lines.append(
            "rg_prefilter_coverage: "
            f"runs={coverage.get('runs')} "
            f"used={coverage.get('used_runs')} "
            f"eligible={coverage.get('eligible_runs')} "
            f"attempted={coverage.get('attempted_runs')} "
            f"anchors={coverage.get('anchor_kind_counts')} "
            f"fallbacks={coverage.get('fallback_reason_counts')}"
        )
    decision = report.get("rg_prefilter_workflow_decision") or {}
    if decision:
        lines.append(
            "rg_prefilter_workflow_decision: "
            f"{decision.get('decision')} "
            f"default_changed={decision.get('default_search_behavior_changed')}"
        )
    compact = report.get("compact_package_handoff") or {}
    if compact:
        equivalence = compact.get("equivalence") or {}
        lines.append(
            "compact_package_handoff: "
            f"status={compact.get('status')} "
            f"runs={compact.get('runs')} "
            f"full_bytes={compact.get('full_result_output_context_bytes')} "
            f"compact_bytes={compact.get('compact_context_bytes')} "
            f"ratio={compact.get('compact_to_full_result_byte_ratio')} "
            f"ref_equiv={equivalence.get('top_candidate_ref_ids_equivalent_runs')}/"
            f"{equivalence.get('compared_runs')} "
            f"window_ref_equiv={equivalence.get('source_window_ref_ids_equivalent_runs')}/"
            f"{equivalence.get('compared_runs')}"
        )
    compact_decision = report.get("compact_package_handoff_decision") or {}
    if compact_decision:
        lines.append(
            "compact_package_handoff_decision: "
            f"{compact_decision.get('decision')} "
            f"default_changed={compact_decision.get('default_search_behavior_changed')}"
        )
    agent_compact = report.get("agent_compact_provenance_handoff") or {}
    if agent_compact:
        no_evidence = agent_compact.get("no_evidence") or {}
        lines.append(
            "agent_compact_provenance_handoff: "
            f"status={agent_compact.get('status')} "
            f"runs={agent_compact.get('runs')} "
            f"full_manifest_20={agent_compact.get('full_manifest_20_case_run')} "
            f"full_bytes={agent_compact.get('full_result_output_context_bytes')} "
            f"compact_bytes={agent_compact.get('compact_context_bytes')} "
            f"ratio={agent_compact.get('compact_to_full_result_byte_ratio')} "
            f"source_refs={agent_compact.get('source_ref_count')} "
            f"next_reads={agent_compact.get('next_read_count')} "
            f"no_evidence_scope_rows={no_evidence.get('scope_limited_semantics_preserved_rows')}"
        )
    agent_compact_decision = report.get("agent_compact_provenance_handoff_decision") or {}
    if agent_compact_decision:
        lines.append(
            "agent_compact_provenance_handoff_decision: "
            f"{agent_compact_decision.get('decision')} "
            f"default_changed={agent_compact_decision.get('default_search_behavior_changed')}"
        )
    summary = report.get("summary", {})
    if summary:
        lines.append("Summary:")
        for name, row in summary.items():
            lines.append(
                "- "
                f"{name}: hit_rate={row.get('first_pass_hit_rate')} "
                f"recall@10={row.get('mean_recall_at_10')} "
                f"mrr={row.get('mean_mrr')} "
                f"tool_calls={row.get('median_tool_calls')} "
                f"subprocess_calls={row.get('median_subprocess_calls')} "
                f"files={row.get('median_files_opened')} "
                f"records={row.get('median_records_parsed')} "
                f"bytes_parsed={row.get('median_bytes_parsed')} "
                f"context_bytes={row.get('median_output_context_bytes')}"
            )
    diagnostic = report.get("structural_sidecar_diagnostic") or {}
    if diagnostic:
        decision = diagnostic.get("decision") or {}
        lines.append(
            "structural_sidecar_diagnostic: "
            f"misses={diagnostic.get('structural_misses')} "
            f"stages={diagnostic.get('miss_stage_counts')} "
            f"next={decision.get('next_value_action')}"
        )
    return "\n".join(lines)


def render_real_history_impact_markdown(payload: dict) -> str:
    report = payload["real_history_impact_benchmark"]
    lines = [
        "# Real-History Impact Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Run:** `{report.get('run_id', '(none)')}`",
        f"**Cases:** `{report.get('case_count', 0)}` of `{report.get('manifest_case_count', 0)}` manifest cases",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        f"**Total wall time ms:** `{report.get('total_wall_time_ms', 0)}`",
        f"**Evaluated-case floor status:** `{report.get('evaluated_case_count_floor_status', report.get('case_count_floor_status'))}`",
        f"**Manifest-case floor status:** `{report.get('manifest_case_count_floor_status', report.get('case_count_floor_status'))}`",
    ]
    subset = report.get("subset_selection") or {}
    if subset:
        lines.extend(
            [
                "",
                "## Subset Selection",
                "",
                f"- Mode: `{subset.get('mode')}`",
                f"- Requested family labels: `{subset.get('requested_family_labels')}`",
                f"- Selected case count: `{subset.get('selected_case_count')}`",
                f"- Selected family counts: `{subset.get('selected_family_counts')}`",
                f"- Selection basis: {subset.get('selection_basis')}",
                f"- Full-manifest prevalence claim: `{subset.get('full_manifest_prevalence_claim')}`",
                f"- Subset prevalence claim: `{subset.get('subset_prevalence_claim')}`",
            ]
        )
    raw = report.get("raw_private_evidence") or {}
    if report.get("headline_findings"):
        lines.extend(["", "## Headline Findings", ""])
        lines.extend(f"- {finding}" for finding in report.get("headline_findings", []))
    lines.extend(
        [
            "",
            "## Private Evidence",
            "",
            f"- Raw per-case evidence stored: `{raw.get('stored')}`",
            f"- Storage: `{raw.get('storage', 'ignored_local_raw_run_artifacts')}`",
            f"- Run file label: `{raw.get('run_file_label', '(none)')}`",
            f"- Committed: `{raw.get('committed')}`",
        ]
    )
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(["", "## Privacy Contract", ""])
    for key, value in (report.get("privacy_contract") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in report.get("warnings", []))
    if report.get("cap_hit_labels"):
        aggregate = report.get("aggregate") or {}
        lines.extend(["", "## Caps", ""])
        lines.append("Labels: " + ", ".join(f"`{label}`" for label in report.get("cap_hit_labels", [])))
        label_counts = aggregate.get("cap_hit_rows_by_label") or {}
        if label_counts:
            lines.extend(
                [
                    "",
                    "| Cap label | Rows hit |",
                    "| --- | ---: |",
                ]
            )
            for label, count in sorted(label_counts.items()):
                lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
        baseline_counts = aggregate.get("cap_hit_rows_by_baseline") or {}
        if baseline_counts:
            lines.extend(
                [
                    "",
                    "| Baseline | Cap rows by label |",
                    "| --- | --- |",
                ]
            )
            for baseline, counts in sorted(baseline_counts.items()):
                count_text = ", ".join(f"{label}={count}" for label, count in sorted((counts or {}).items()))
                lines.append(
                    "| "
                    f"`{_escape_markdown_table(str(baseline))}` | "
                    f"{_escape_markdown_table(count_text or '(none)')} |"
                )
    pre_parse = (report.get("aggregate") or {}).get("pre_parse") or {}
    if pre_parse:
        privacy = pre_parse.get("privacy") or {}
        lines.extend(
            [
                "",
                "## Pre-Parse Raw-Line Gate",
                "",
                f"- Status: `{pre_parse.get('status')}`",
                f"- Runs: `{pre_parse.get('runs')}`",
                f"- Active runs: `{pre_parse.get('active_runs')}`",
                f"- Status counts: `{pre_parse.get('status_counts')}`",
                f"- Anchor-kind counts: `{pre_parse.get('anchor_kind_counts')}`",
                f"- Raw JSONL lines examined: `{pre_parse.get('raw_lines_examined')}`",
                (
                    "- Raw JSONL lines skipped before full JSON parse: "
                    f"`{pre_parse.get('raw_lines_skipped_before_full_json_parse')}`"
                ),
                f"- Full JSON records parsed: `{pre_parse.get('full_json_records_parsed')}`",
                f"- Raw anchor needles included: `{privacy.get('raw_anchor_needles_included')}`",
            ]
        )
    lines.extend(_fielded_bm25_query_stream_pressure_markdown_lines(report))
    lines.extend(_fielded_bm25_source_grouping_markdown_lines(report))
    lines.extend(_benchmark_gate_markdown_lines(report))
    scope = report.get("report_scope") or {}
    if scope:
        lines.extend(
            [
                "",
                "## Report Scope",
                "",
                f"- Mode: `{scope.get('mode')}`",
                f"- Requested case limit: `{scope.get('requested_case_limit')}`",
                f"- Evaluated case count: `{scope.get('evaluated_case_count')}`",
                f"- Limitation: {scope.get('limitation')}",
            ]
        )
    coverage = report.get("rg_prefilter_coverage") or {}
    decision = report.get("rg_prefilter_workflow_decision") or {}
    if coverage:
        lines.extend(
            [
                "",
                "## Rg Prefilter Anchor Coverage",
                "",
                f"- Status: `{coverage.get('status')}`",
                f"- Basis: `{coverage.get('basis')}`",
                f"- Runs: `{coverage.get('runs')}`",
                f"- Cases: `{coverage.get('cases')}`",
                f"- Eligible runs: `{coverage.get('eligible_runs')}`",
                f"- Used runs: `{coverage.get('used_runs')}`",
                f"- Attempted runs: `{coverage.get('attempted_runs')}`",
                f"- Anchor-kind counts: `{coverage.get('anchor_kind_counts')}`",
                f"- Fallback reason counts: `{coverage.get('fallback_reason_counts')}`",
                f"- Status counts: `{coverage.get('status_counts')}`",
                f"- Needle policy counts: `{coverage.get('needle_policy_counts')}`",
                f"- Median files reduction ratio: `{coverage.get('median_files_reduction_ratio')}`",
                f"- Median bytes reduction ratio: `{coverage.get('median_bytes_reduction_ratio')}`",
            ]
        )
        privacy = coverage.get("privacy") or {}
        if privacy:
            lines.extend(["", "### Coverage Privacy", ""])
            for key, value in privacy.items():
                lines.append(f"- `{key}`: `{value}`")
    if decision:
        lines.extend(
            [
                "",
                "## Rg Prefilter Workflow Decision",
                "",
                f"- Decision: `{decision.get('decision')}`",
                f"- Rationale: {decision.get('rationale')}",
                f"- Next action: `{decision.get('next_action')}`",
                f"- Default search behavior changed: `{decision.get('default_search_behavior_changed')}`",
                f"- Default rg promotion supported: `{decision.get('rg_default_promotion_supported')}`",
                f"- Universal rg supported: `{decision.get('universal_rg_supported')}`",
                f"- Token-savings claim supported: `{decision.get('token_savings_claim_supported')}`",
                f"- Final-answer quality claim supported: `{decision.get('final_answer_quality_claim_supported')}`",
                f"- Product-value claim supported: `{decision.get('product_value_claim_supported')}`",
            ]
        )
    compact = report.get("compact_package_handoff") or {}
    compact_decision = report.get("compact_package_handoff_decision") or {}
    if compact:
        equivalence = compact.get("equivalence") or {}
        lines.extend(
            [
                "",
                "## Compact Package Handoff",
                "",
                f"- Status: `{compact.get('status')}`",
                f"- Basis: `{compact.get('basis')}`",
                f"- Runs: `{compact.get('runs')}`",
                f"- Cases: `{compact.get('cases')}`",
                f"- Baseline counts: `{compact.get('baseline_counts')}`",
                f"- Full scanner-result output context bytes: `{compact.get('full_result_output_context_bytes')}`",
                f"- Compact package context bytes: `{compact.get('compact_context_bytes')}`",
                f"- Locator context bytes: `{compact.get('locator_context_bytes')}`",
                f"- Source-window text bytes: `{compact.get('source_window_text_bytes')}`",
                f"- Compact/full byte ratio: `{compact.get('compact_to_full_result_byte_ratio')}`",
                f"- Median full scanner-result bytes: `{compact.get('median_full_result_output_context_bytes')}`",
                f"- Median compact package bytes: `{compact.get('median_compact_context_bytes')}`",
                f"- Top candidate count: `{compact.get('top_candidate_count')}`",
                f"- Source window count: `{compact.get('source_window_count')}`",
                f"- Equivalence comparison basis: `{equivalence.get('comparison_basis')}`",
                f"- Top-candidate scope: `{equivalence.get('top_candidate_scope')}`",
                f"- Top-candidate limit: `{equivalence.get('top_candidate_limit')}`",
                f"- Source-window scope: `{equivalence.get('source_window_scope')}`",
                f"- Source-window limit: `{equivalence.get('source_window_limit')}`",
                f"- Compared runs: `{equivalence.get('compared_runs')}`",
                f"- Top-candidate ref-id equivalent runs: `{equivalence.get('top_candidate_ref_ids_equivalent_runs')}`",
                f"- Top-candidate source-ref equivalent runs: `{equivalence.get('top_candidate_source_refs_equivalent_runs')}`",
                f"- Source-window ref-id equivalent runs: `{equivalence.get('source_window_ref_ids_equivalent_runs')}`",
                f"- Source-window source-ref equivalent runs: `{equivalence.get('source_window_source_refs_equivalent_runs')}`",
                f"- All top-candidate ref IDs equivalent: `{equivalence.get('all_top_candidate_ref_ids_equivalent')}`",
                f"- All top-candidate source refs equivalent: `{equivalence.get('all_top_candidate_source_refs_equivalent')}`",
                f"- All source-window ref IDs equivalent: `{equivalence.get('all_source_window_ref_ids_equivalent')}`",
                f"- All source-window source refs equivalent: `{equivalence.get('all_source_window_source_refs_equivalent')}`",
                f"- Legacy output context bytes preserved: `{compact.get('legacy_output_context_bytes_preserved')}`",
                f"- Compact bytes measured separately: `{compact.get('compact_bytes_measured_separately')}`",
            ]
        )
        privacy = compact.get("privacy") or {}
        if privacy:
            lines.extend(["", "### Compact Package Privacy", ""])
            for key, value in privacy.items():
                lines.append(f"- `{key}`: `{value}`")
    if compact_decision:
        lines.extend(
            [
                "",
                "## Compact Package Decision",
                "",
                f"- Decision: `{compact_decision.get('decision')}`",
                f"- Rationale: {compact_decision.get('rationale')}",
                f"- Next action: `{compact_decision.get('next_action')}`",
                f"- Default search behavior changed: `{compact_decision.get('default_search_behavior_changed')}`",
                f"- Scanner/ranking/source selection changed: `{compact_decision.get('scanner_ranking_source_selection_changed')}`",
                f"- Legacy output context bytes redefined: `{compact_decision.get('legacy_output_context_bytes_redefined')}`",
                f"- Token-savings claim supported: `{compact_decision.get('token_savings_claim_supported')}`",
                f"- Final-answer quality claim supported: `{compact_decision.get('final_answer_quality_claim_supported')}`",
                f"- Product-value claim supported: `{compact_decision.get('product_value_claim_supported')}`",
                f"- Default promotion supported: `{compact_decision.get('default_promotion_supported')}`",
            ]
        )
    agent_compact = report.get("agent_compact_provenance_handoff") or {}
    agent_compact_decision = report.get("agent_compact_provenance_handoff_decision") or {}
    if agent_compact:
        no_evidence = agent_compact.get("no_evidence") or {}
        cap_hit = agent_compact.get("cap_hit") or {}
        lines.extend(
            [
                "",
                "## Agent Compact Provenance Handoff",
                "",
                f"- Status: `{agent_compact.get('status')}`",
                f"- Basis: `{agent_compact.get('basis')}`",
                f"- Runs: `{agent_compact.get('runs')}`",
                f"- Cases: `{agent_compact.get('cases')}`",
                f"- Manifest case count: `{agent_compact.get('manifest_case_count')}`",
                f"- Evaluated case count: `{agent_compact.get('evaluated_case_count')}`",
                f"- Requested case limit: `{agent_compact.get('requested_case_limit')}`",
                f"- Full manifest requested: `{agent_compact.get('full_manifest_requested')}`",
                f"- Full manifest run: `{agent_compact.get('full_manifest_run')}`",
                f"- Full 20-case manifest run: `{agent_compact.get('full_manifest_20_case_run')}`",
                f"- Baseline counts: `{agent_compact.get('baseline_counts')}`",
                f"- Status counts: `{agent_compact.get('status_counts')}`",
                f"- Full scanner-result output context bytes: `{agent_compact.get('full_result_output_context_bytes')}`",
                f"- Agent compact handoff context bytes: `{agent_compact.get('compact_context_bytes')}`",
                f"- Compact/full byte ratio: `{agent_compact.get('compact_to_full_result_byte_ratio')}`",
                f"- Median full scanner-result bytes: `{agent_compact.get('median_full_result_output_context_bytes')}`",
                f"- Median agent compact handoff bytes: `{agent_compact.get('median_compact_context_bytes')}`",
                f"- Candidate count: `{agent_compact.get('candidate_count')}`",
                f"- Compact candidate count: `{agent_compact.get('compact_candidate_count')}`",
                f"- Source-ref count: `{agent_compact.get('source_ref_count')}`",
                f"- Source-window count: `{agent_compact.get('source_window_count')}`",
                f"- Next-read count: `{agent_compact.get('next_read_count')}`",
                f"- No-evidence rows: `{no_evidence.get('rows')}`",
                f"- No-evidence needed rows: `{no_evidence.get('needed_rows')}`",
                f"- No-evidence scope-limited semantics preserved rows: `{no_evidence.get('scope_limited_semantics_preserved_rows')}`",
                f"- No-evidence not-all-history absence rows: `{no_evidence.get('not_all_history_absence_rows')}`",
                f"- No-evidence answer-scope counts: `{no_evidence.get('answer_scope_counts')}`",
                f"- Cap-hit rows: `{cap_hit.get('rows')}`",
                f"- Cap-hit rows by label: `{cap_hit.get('rows_by_label')}`",
                f"- Legacy output context bytes preserved: `{agent_compact.get('legacy_output_context_bytes_preserved')}`",
                f"- Compact bytes measured separately: `{agent_compact.get('compact_bytes_measured_separately')}`",
            ]
        )
        privacy = agent_compact.get("privacy") or {}
        if privacy:
            lines.extend(["", "### Agent Compact Privacy", ""])
            for key, value in privacy.items():
                lines.append(f"- `{key}`: `{value}`")
        claims = agent_compact.get("claim_boundaries") or {}
        if claims:
            lines.extend(["", "### Agent Compact Claim Boundaries", ""])
            for key, value in claims.items():
                lines.append(f"- `{key}`: `{value}`")
    if agent_compact_decision:
        lines.extend(
            [
                "",
                "## Agent Compact Decision",
                "",
                f"- Decision: `{agent_compact_decision.get('decision')}`",
                f"- Rationale: {agent_compact_decision.get('rationale')}",
                f"- Next action: `{agent_compact_decision.get('next_action')}`",
                f"- Measured packet: `{agent_compact_decision.get('measured_packet')}`",
                f"- Measured builder: `{agent_compact_decision.get('measured_builder')}`",
                f"- Old compact package diagnostic used as measured packet: `{agent_compact_decision.get('old_compact_package_diagnostic_used_as_measured_packet')}`",
                f"- Default search behavior changed: `{agent_compact_decision.get('default_search_behavior_changed')}`",
                f"- Scanner/ranking/source selection changed: `{agent_compact_decision.get('scanner_ranking_source_selection_changed')}`",
                f"- Legacy output context bytes redefined: `{agent_compact_decision.get('legacy_output_context_bytes_redefined')}`",
                f"- Token-savings claim supported: `{agent_compact_decision.get('token_savings_claim_supported')}`",
                f"- Final-answer quality claim supported: `{agent_compact_decision.get('final_answer_quality_claim_supported')}`",
                f"- Product-value claim supported: `{agent_compact_decision.get('product_value_claim_supported')}`",
                f"- Default promotion supported: `{agent_compact_decision.get('default_promotion_supported')}`",
            ]
        )
    source_tier_pressure = report.get("source_tier_answerability_pressure") or {}
    source_tier_decision = report.get("source_tier_answerability_pressure_decision") or {}
    large_history_stage = report.get("large_history_stage_attribution_pressure") or {}
    if source_tier_pressure:
        source_catalog = source_tier_pressure.get("source_catalog") or {}
        source_plan = source_tier_pressure.get("source_plan") or {}
        stage_bottlenecks = source_tier_pressure.get("stage_bottlenecks") or {}
        compact_budget = source_tier_pressure.get("compact_context_budget") or {}
        cap_hit = source_tier_pressure.get("cap_hit") or {}
        no_evidence = source_tier_pressure.get("no_evidence") or {}
        next_read_recovery = source_tier_pressure.get("next_read_recovery") or {}
        lines.extend(
            [
                "",
                "## Source-Tier Answerability Pressure",
                "",
                f"- Status: `{source_tier_pressure.get('status')}`",
                f"- Basis: `{source_tier_pressure.get('basis')}`",
                f"- Runs: `{source_tier_pressure.get('runs')}`",
                f"- Cases: `{source_tier_pressure.get('cases')}`",
                f"- Manifest case count: `{source_tier_pressure.get('manifest_case_count')}`",
                f"- Evaluated case count: `{source_tier_pressure.get('evaluated_case_count')}`",
                f"- Requested case limit: `{source_tier_pressure.get('requested_case_limit')}`",
                f"- Full manifest requested: `{source_tier_pressure.get('full_manifest_requested')}`",
                f"- Full manifest run: `{source_tier_pressure.get('full_manifest_run')}`",
                f"- Full 20-case manifest run: `{source_tier_pressure.get('full_manifest_20_case_run')}`",
                f"- Baseline counts: `{source_tier_pressure.get('baseline_counts')}`",
                f"- Answerability status counts: `{source_tier_pressure.get('answerability_status_counts')}`",
                f"- Answerable-from-packet rows: `{source_tier_pressure.get('answerable_from_packet_rows')}`",
                (
                    "- Candidate-presence-implies-answerability rows: "
                    f"`{source_tier_pressure.get('candidate_presence_implies_answerability_rows')}`"
                ),
                f"- Diagnostic candidate rows: `{source_tier_pressure.get('diagnostic_candidate_rows')}`",
                f"- Weak candidate diagnostic rows: `{source_tier_pressure.get('weak_candidate_diagnostic_rows')}`",
                f"- Next-read recoverable rows: `{next_read_recovery.get('recoverable_rows')}`",
                f"- Next-read recovery kind counts: `{next_read_recovery.get('recovery_kind_counts')}`",
                f"- Next-read count total: `{next_read_recovery.get('next_read_count_total')}`",
                f"- Source-window count total: `{next_read_recovery.get('candidate_window_count_total')}`",
                (
                    "- Source-grouping selection changed rows: "
                    f"`{next_read_recovery.get('source_grouping_selection_changed_rows')}`"
                ),
                f"- Cap-hit rows: `{cap_hit.get('rows')}`",
                f"- Cap-hit rows by label: `{cap_hit.get('rows_by_label')}`",
                f"- No-evidence needed rows: `{no_evidence.get('needed_rows')}`",
                f"- No-evidence answer-scope counts: `{no_evidence.get('answer_scope_counts')}`",
            ]
        )
        lines.extend(
            [
                "",
                "### Source Catalog and Plan",
                "",
                f"- Source catalog present rows: `{source_catalog.get('present_rows')}`",
                f"- Median source catalog file count: `{source_catalog.get('median_file_count')}`",
                f"- Median selected file count: `{source_catalog.get('median_selected_file_count')}`",
                f"- Source catalog cap label counts: `{source_catalog.get('cap_label_counts')}`",
                f"- Source catalog raw paths included: `{source_catalog.get('raw_paths_included')}`",
                f"- Source catalog raw text included: `{source_catalog.get('raw_text_included')}`",
                (
                    "- Persistent index or cache used: "
                    f"`{source_catalog.get('persistent_index_or_cache_used')}`"
                ),
                f"- Source plan present rows: `{source_plan.get('present_rows')}`",
                f"- Median sources total: `{source_plan.get('median_sources_total')}`",
                f"- Median sources selected: `{source_plan.get('median_sources_selected')}`",
                f"- Sources selected total: `{source_plan.get('sources_selected_total')}`",
                f"- Sources pruned hard total: `{source_plan.get('sources_pruned_hard_total')}`",
                f"- Sources deferred by budget total: `{source_plan.get('sources_deferred_budget_total')}`",
                f"- Deferred rows: `{source_plan.get('deferred_rows')}`",
                f"- Retry guidance rows: `{source_plan.get('retry_guidance_rows')}`",
                f"- Retry guidance present rows: `{source_plan.get('retry_guidance_present_rows')}`",
                f"- Selected tier counts: `{source_plan.get('selected_tier_counts')}`",
                f"- Deferred tier counts: `{source_plan.get('deferred_tier_counts')}`",
                f"- Deferred reason counts: `{source_plan.get('deferred_reason_counts')}`",
                f"- Uses scoring weights rows: `{source_plan.get('uses_scoring_weights_rows')}`",
                f"- Source plan raw paths included: `{source_plan.get('raw_paths_included')}`",
                f"- Source plan raw text included: `{source_plan.get('raw_text_included')}`",
            ]
        )
        lines.extend(
            [
                "",
                "### Stage Bottlenecks",
                "",
                (
                    "- Dominant pressure stage counts: "
                    f"`{stage_bottlenecks.get('dominant_pressure_stage_counts')}`"
                ),
                f"- Stage status counts: `{stage_bottlenecks.get('stage_status_counts')}`",
                f"- Stage next-fix hint counts: `{stage_bottlenecks.get('stage_next_fix_hint_counts')}`",
            ]
        )
        lines.extend(
            [
                "",
                "### Compact Budget",
                "",
                f"- Compact context bytes: `{compact_budget.get('compact_context_bytes')}`",
                f"- Compact context tokens approx: `{compact_budget.get('compact_context_tokens_approx')}`",
                (
                    "- Full result output context bytes: "
                    f"`{compact_budget.get('full_result_output_context_bytes')}`"
                ),
                f"- Median compact context bytes: `{compact_budget.get('median_compact_context_bytes')}`",
                (
                    "- Median full result output context bytes: "
                    f"`{compact_budget.get('median_full_result_output_context_bytes')}`"
                ),
                f"- Measurement basis counts: `{compact_budget.get('measurement_basis_counts')}`",
                f"- Output count totals: `{compact_budget.get('output_count_totals')}`",
                f"- Output limit maxima: `{compact_budget.get('output_limit_maxima')}`",
            ]
        )
        privacy = source_tier_pressure.get("privacy") or {}
        if privacy:
            lines.extend(["", "### Source-Tier Privacy", ""])
            for key, value in privacy.items():
                lines.append(f"- `{key}`: `{value}`")
        claims = source_tier_pressure.get("claim_boundaries") or {}
        if claims:
            lines.extend(["", "### Source-Tier Claim Boundaries", ""])
            for key, value in claims.items():
                lines.append(f"- `{key}`: `{value}`")
    if source_tier_decision:
        lines.extend(
            [
                "",
                "## Source-Tier Answerability Decision",
                "",
                f"- Decision: `{source_tier_decision.get('decision')}`",
                f"- Selected next product decision: `{source_tier_decision.get('selected_next_product_decision')}`",
                f"- Rationale: {source_tier_decision.get('rationale')}",
                f"- Next action: `{source_tier_decision.get('next_action')}`",
                f"- Measured packet: `{source_tier_decision.get('measured_packet')}`",
                f"- Measured builder: `{source_tier_decision.get('measured_builder')}`",
                f"- Measured baseline: `{source_tier_decision.get('measured_baseline')}`",
                f"- Uses live fielded BM25 result: `{source_tier_decision.get('uses_live_fielded_bm25_result')}`",
                f"- Uses handcrafted packets: `{source_tier_decision.get('uses_handcrafted_packets')}`",
                f"- Default search behavior changed: `{source_tier_decision.get('default_search_behavior_changed')}`",
                (
                    "- Scanner/ranking/source selection changed: "
                    f"`{source_tier_decision.get('scanner_ranking_source_selection_changed')}`"
                ),
                (
                    "- Legacy output context bytes redefined: "
                    f"`{source_tier_decision.get('legacy_output_context_bytes_redefined')}`"
                ),
                f"- Token-savings claim supported: `{source_tier_decision.get('token_savings_claim_supported')}`",
                (
                    "- Final-answer quality claim supported: "
                    f"`{source_tier_decision.get('final_answer_quality_claim_supported')}`"
                ),
                f"- Product-value claim supported: `{source_tier_decision.get('product_value_claim_supported')}`",
                f"- Default promotion supported: `{source_tier_decision.get('default_promotion_supported')}`",
            ]
        )
    if large_history_stage:
        stage_summary = large_history_stage.get("stage_bottleneck_summary") or {}
        tool_delta = large_history_stage.get("selected_next_tool_delta") or {}
        latency_boundary = large_history_stage.get("latency_indexing_boundary") or {}
        lines.extend(
            [
                "",
                "## Large-History Stage Attribution Pressure",
                "",
                f"- Status: `{large_history_stage.get('status')}`",
                f"- Basis: `{large_history_stage.get('basis')}`",
                f"- Allowed bottleneck labels: `{large_history_stage.get('allowed_bottleneck_labels')}`",
                f"- Selected next bottleneck: `{large_history_stage.get('selected_next_bottleneck')}`",
                f"- Bottleneck stage counts: `{stage_summary.get('bottleneck_stage_counts')}`",
                f"- Signal counts: `{stage_summary.get('signal_counts')}`",
                f"- Selection basis: `{stage_summary.get('selection_basis')}`",
                f"- No decision reason: `{large_history_stage.get('no_decision_reason')}`",
                f"- Selected next tool delta: `{tool_delta.get('next_tool_delta')}`",
                f"- Tool delta implemented in this task: `{tool_delta.get('implementation_included')}`",
            ]
        )
        lines.extend(
            [
                "",
                "### Latency Indexing Boundary",
                "",
                f"- Selection rule: `{latency_boundary.get('selection_rule')}`",
                f"- Cap-hit rows: `{latency_boundary.get('cap_hit_rows')}`",
                f"- Supported or deferred rows: `{latency_boundary.get('supported_or_deferred_rows')}`",
                f"- Case count: `{latency_boundary.get('case_count')}`",
                (
                    "- Retrieval usefulness acceptable: "
                    f"`{latency_boundary.get('retrieval_usefulness_acceptable')}`"
                ),
                (
                    "- Cap or latency prevents stage reach: "
                    f"`{latency_boundary.get('cap_or_latency_prevents_stage_reach')}`"
                ),
                f"- Fielded BM25 slow phase count: `{latency_boundary.get('fielded_bm25_slow_phase_count')}`",
                (
                    "- Persistent index or cache used: "
                    f"`{latency_boundary.get('persistent_index_or_cache_used')}`"
                ),
                (
                    "- Indexing latency selection supported: "
                    f"`{latency_boundary.get('indexing_latency_selection_supported')}`"
                ),
                (
                    "- Indexing necessity claim supported: "
                    f"`{latency_boundary.get('indexing_necessity_claim_supported')}`"
                ),
            ]
        )
        privacy = large_history_stage.get("privacy") or {}
        if privacy:
            lines.extend(["", "### Large-History Stage Privacy", ""])
            for key, value in privacy.items():
                lines.append(f"- `{key}`: `{value}`")
        claims = large_history_stage.get("claim_boundaries") or {}
        if claims:
            lines.extend(["", "### Large-History Stage Claim Boundaries", ""])
            for key, value in claims.items():
                lines.append(f"- `{key}`: `{value}`")
    omitted = report.get("rows_omitted") or {}
    if omitted:
        lines.extend(
            [
                "",
                "## Omitted Per-Case Rows",
                "",
                f"- Omitted: `{omitted.get('omitted')}`",
                f"- Row count: `{omitted.get('row_count')}`",
                f"- Reason: `{omitted.get('reason')}`",
                f"- Case IDs committed: `{omitted.get('case_ids_committed')}`",
                f"- Raw queries committed: `{omitted.get('raw_queries_committed')}`",
                f"- Raw private paths committed: `{omitted.get('raw_private_paths_committed')}`",
                f"- Raw anchor needles committed: `{omitted.get('raw_anchor_needles_committed')}`",
                f"- Raw compact handoff detail committed: `{omitted.get('raw_compact_handoff_detail_committed')}`",
                f"- Raw agent compact handoff detail committed: `{omitted.get('raw_agent_compact_handoff_detail_committed')}`",
                f"- Raw source windows committed: `{omitted.get('raw_source_windows_committed')}`",
            ]
        )
    diagnostic = report.get("structural_sidecar_diagnostic") or {}
    if diagnostic:
        decision = diagnostic.get("decision") or {}
        lines.extend(
            [
                "",
                "## Structural Sidecar Diagnostic",
                "",
                f"- Basis: `{diagnostic.get('basis')}`",
                f"- Structural cases: `{diagnostic.get('structural_cases')}`",
                f"- Structural misses: `{diagnostic.get('structural_misses')}`",
                f"- Comparison counts: `{diagnostic.get('comparison_counts')}`",
                f"- Miss stage counts: `{diagnostic.get('miss_stage_counts')}`",
                f"- Repair family: `{decision.get('repair_family')}`",
                f"- Next value action: `{decision.get('next_value_action')}`",
                f"- Default promotion supported: `{decision.get('default_promotion_supported')}`",
                f"- Token savings claim supported: `{decision.get('token_savings_claim_supported')}`",
                f"- Full agent-loop cost: `{(diagnostic.get('cost_buckets') or {}).get('full_agent_loop_cost')}`",
                "",
                "### Structural Miss/Comparison Rows",
                "",
                "| Case | Class | Stage | Indexed | Candidate file | Candidate entry | Opened | Line reached | Candidate | Pre-final | Final | Structural bytes | Package bytes | Structural context | Package context |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in diagnostic.get("rows", []):
            structural_ref = row.get("structural_expected_ref") or {}
            cost = row.get("cost") or {}
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('comparison_class', '')))} | "
                f"{_escape_markdown_table(str(row.get('stage', '')))} | "
                f"{structural_ref.get('indexed_record')} | "
                f"{structural_ref.get('candidate_file_selected')} | "
                f"{structural_ref.get('candidate_entry_selected')} | "
                f"{structural_ref.get('opened')} | "
                f"{structural_ref.get('record_line_reached')} | "
                f"{structural_ref.get('candidate_created')} | "
                f"{structural_ref.get('retained_pre_final')} | "
                f"{structural_ref.get('final_kept')} | "
                f"{cost.get('structural_bytes_parsed')} | "
                f"{cost.get('package_bytes_parsed')} | "
                f"{cost.get('structural_output_context_bytes')} | "
                f"{cost.get('package_output_context_bytes')} |"
            )
    if report.get("summary"):
        lines.extend(
            [
                "",
                "## Baseline Summary",
                "",
                "| Baseline | Hit rate | Recall@1 | Recall@3 | Recall@10 | MRR | Wall ms | Tool calls | Subprocess calls | Files | Records parsed | Bytes scanned | Bytes parsed | Context bytes | Context tokens | Candidates | Cap rows | Cap labels |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for name, row in report.get("summary", {}).items():
            cap_labels = ", ".join(row.get("cap_hit_labels") or [])
            lines.append(
                "| "
                f"{_escape_markdown_table(str(name))} | "
                f"{row.get('first_pass_hit_rate')} | "
                f"{row.get('mean_recall_at_1')} | "
                f"{row.get('mean_recall_at_3')} | "
                f"{row.get('mean_recall_at_10')} | "
                f"{row.get('mean_mrr')} | "
                f"{row.get('median_wall_time_ms')} | "
                f"{row.get('median_tool_calls')} | "
                f"{row.get('median_subprocess_calls')} | "
                f"{row.get('median_files_opened')} | "
                f"{row.get('median_records_parsed')} | "
                f"{row.get('median_bytes_scanned')} | "
                f"{row.get('median_bytes_parsed')} | "
                f"{row.get('median_output_context_bytes')} | "
                f"{row.get('median_output_context_tokens_approx')} | "
                f"{row.get('median_candidate_count')} | "
                f"{row.get('caps_hit_runs')} | "
                f"{_escape_markdown_table(cap_labels)} |"
            )
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Case Runs",
                "",
                "| Case | Family | Baseline | Outcome | First pass | Recall@1 | Recall@3 | Recall@10 | MRR | Wall ms | Tool calls | Files | Records | Bytes parsed | Context bytes | Candidates | Caps |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in report.get("rows", []):
            row_caps = ", ".join(row.get("cap_hit_labels") or [])
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('family', '')))} | "
                f"{_escape_markdown_table(str(row.get('baseline', '')))} | "
                f"{_escape_markdown_table(str(row.get('expected_outcome', '')))} | "
                f"{row.get('first_pass_hit')} | "
                f"{row.get('recall_at_1')} | "
                f"{row.get('recall_at_3')} | "
                f"{row.get('recall_at_10')} | "
                f"{row.get('mrr')} | "
                f"{row.get('wall_time_ms')} | "
                f"{row.get('tool_calls')} | "
                f"{row.get('files_opened')} | "
                f"{row.get('records_parsed')} | "
                f"{row.get('bytes_parsed')} | "
                f"{row.get('output_context_bytes')} | "
                f"{row.get('candidate_count')} | "
                f"{_escape_markdown_table(row_caps)} |"
            )
    return "\n".join(lines)


def _fielded_bm25_query_stream_pressure_markdown_lines(report: dict) -> list[str]:
    query_streams = ((report.get("aggregate") or {}).get("fielded_bm25") or {}).get("query_streams") or {}
    if not isinstance(query_streams, dict):
        return []
    if query_streams.get("status") == "not_evaluated" and not int(query_streams.get("runs") or 0):
        return []
    privacy = query_streams.get("privacy") or {}
    lines = [
        "",
        "## Fielded BM25 Query-Stream Pressure",
        "",
        f"- Status: `{query_streams.get('status')}`",
        f"- Runs: `{query_streams.get('runs')}`",
        f"- Stream hit-limit runs: `{query_streams.get('stream_hit_limit_runs')}`",
        f"- Saturated stream total: `{query_streams.get('saturated_stream_total')}`",
        (
            "- Saturated stream label counts: "
            f"`{_compact_counter_map(query_streams.get('saturated_stream_label_counts') or {})}`"
        ),
        f"- Row count before dedupe: `{query_streams.get('row_count_before_dedupe')}`",
        f"- Row count after dedupe: `{query_streams.get('row_count_after_dedupe')}`",
        f"- Duplicate row count: `{query_streams.get('duplicate_row_count')}`",
        (
            "- Selected rows by stream: "
            f"`{_compact_counter_map(query_streams.get('selected_rows_by_stream') or {})}`"
        ),
        (
            "- Rows dropped by stream cap: "
            f"`{_compact_counter_map(query_streams.get('rows_dropped_by_stream_cap') or {})}`"
        ),
        f"- Rows dropped by stream cap total: `{query_streams.get('rows_dropped_by_stream_cap_total')}`",
    ]
    if privacy:
        lines.extend(["", "### Fielded BM25 Query-Stream Privacy", ""])
        for key, value in privacy.items():
            lines.append(f"- `{key}`: `{value}`")
    return lines


def _fielded_bm25_source_grouping_markdown_lines(report: dict) -> list[str]:
    source_grouping = ((report.get("aggregate") or {}).get("fielded_bm25") or {}).get("source_grouping") or {}
    if not isinstance(source_grouping, dict):
        return []
    if source_grouping.get("status") == "not_evaluated" and not int(source_grouping.get("runs") or 0):
        return []
    privacy = source_grouping.get("privacy") or {}
    lines = [
        "",
        "## Fielded BM25 Source Grouping / Adjacent Collapse",
        "",
        f"- Status: `{source_grouping.get('status')}`",
        f"- Runs: `{source_grouping.get('runs')}`",
        f"- Candidate rows considered: `{source_grouping.get('candidate_rows_considered')}`",
        f"- Candidate rows selected: `{source_grouping.get('candidate_rows_selected')}`",
        f"- Collapsed adjacent run count: `{source_grouping.get('collapsed_adjacent_run_count')}`",
        f"- Suppressed adjacent row count: `{source_grouping.get('suppressed_adjacent_row_count')}`",
        f"- Max adjacent run size: `{source_grouping.get('max_adjacent_run_size')}`",
        f"- Group count considered total: `{source_grouping.get('group_count_considered_total')}`",
        f"- Group count selected total: `{source_grouping.get('group_count_selected_total')}`",
        f"- Group-interleaved rows selected: `{source_grouping.get('group_interleaved_rows_selected')}`",
        f"- Source-window count considered total: `{source_grouping.get('window_count_considered_total')}`",
        f"- Source-window count selected total: `{source_grouping.get('window_count_selected_total')}`",
        f"- Source-window count omitted total: `{source_grouping.get('window_count_omitted_total')}`",
        f"- Selected window-head count total: `{source_grouping.get('selected_window_head_count_total')}`",
        f"- Selected inner-hit count total: `{source_grouping.get('selected_inner_hit_count_total')}`",
        f"- Omitted inner-hit count total: `{source_grouping.get('omitted_inner_hit_count_total')}`",
        f"- Merged adjacent window count total: `{source_grouping.get('merged_adjacent_window_count_total')}`",
        f"- Distinct windows selected total: `{source_grouping.get('distinct_windows_selected_total')}`",
        f"- Distinct sources selected total: `{source_grouping.get('distinct_sources_selected_total')}`",
        f"- Distinct sessions selected total: `{source_grouping.get('distinct_sessions_selected_total')}`",
        f"- Distinct days selected total: `{source_grouping.get('distinct_days_selected_total')}`",
        f"- Source count considered total: `{source_grouping.get('source_count_considered_total')}`",
        f"- Source count selected total: `{source_grouping.get('source_count_selected_total')}`",
        f"- Rows dropped by source cap: `{source_grouping.get('rows_dropped_by_source_cap')}`",
    ]
    if privacy:
        lines.extend(["", "### Fielded BM25 Source Grouping Privacy", ""])
        for key, value in privacy.items():
            lines.append(f"- `{key}`: `{value}`")
    return lines


def render_real_history_package_miss_audit_text(payload: dict) -> str:
    report = payload["real_history_package_search_miss_audit"]
    lines = [
        "Real-history package search miss audit",
        f"Status: {report.get('status')}",
        f"Raw run: {report.get('raw_run_label')}",
        f"Cases: {report.get('case_count', 0)}",
        f"Baseline: {report.get('audited_baseline')}",
    ]
    cap = report.get("cap_impact") or {}
    for label in ("enumeration_byte_cap", "elapsed_cap", "match_cap", "weak_reservoir_cap"):
        lines.append(f"{label}: {(cap.get(label) or {}).get('case_count', 0)} cases")
    not_searched = cap.get("not_searched_scope") or {}
    if not_searched:
        lines.append(
            "Not-searched scope: "
            + ", ".join(f"{label}={count}" for label, count in sorted(not_searched.items()))
        )
    lines.append("Top failure classifications:")
    for label, count in list((report.get("failure_classification_counts") or {}).items())[:12]:
        lines.append(f"- {label}: {count}")
    lines.append("Rows:")
    for row in report.get("rows", []):
        lines.append(
            "- "
            f"{row.get('case_id')}: "
            f"{', '.join(row.get('failure_classifications') or [])}"
        )
    return "\n".join(lines)


def render_real_history_package_miss_audit_markdown(payload: dict) -> str:
    report = payload["real_history_package_search_miss_audit"]
    lines = [
        "# JSH-058A Real-History Package Search Miss Audit",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Raw run label:** `{report.get('raw_run_label')}`",
        f"**Raw run id:** `{report.get('raw_run_id')}`",
        f"**Cases:** `{report.get('case_count', 0)}` of `{report.get('manifest_case_count', 0)}` manifest cases",
        f"**Audited baseline:** `{report.get('audited_baseline')}`",
        "",
        "## Evidence Limits",
        "",
    ]
    for key, value in (report.get("evidence_limits") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Privacy Contract", ""])
    for key, value in (report.get("privacy_contract") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    cap = report.get("cap_impact") or {}
    lines.extend(
        [
            "",
            "## Cap Impact",
            "",
            "| Cap / scope | Case count |",
            "| --- | ---: |",
        ]
    )
    for label in ("enumeration_byte_cap", "elapsed_cap", "match_cap", "weak_reservoir_cap"):
        lines.append(f"| `{label}` | {(cap.get(label) or {}).get('case_count', 0)} |")
    for label, count in sorted((cap.get("not_searched_scope") or {}).items()):
        lines.append(f"| `not_searched_scope:{_escape_markdown_table(str(label))}` | {count} |")
    query = report.get("query_basis_summary") or {}
    lines.extend(["", "## Query Basis", ""])
    lines.append(f"- Max private query chars: `{query.get('max_private_query_char_count', 0)}`")
    lines.append(f"- Max base terms: `{query.get('max_base_term_count', 0)}`")
    lines.append(f"- Max expanded terms: `{query.get('max_expanded_term_count', 0)}`")
    if query.get("pathology_counts"):
        lines.extend(["", "| Query pathology | Cases |", "| --- | ---: |"])
        for label, count in sorted(query.get("pathology_counts", {}).items()):
            lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
    lines.extend(["", "## Failure Counts", "", "| Classification | Cases |", "| --- | ---: |"])
    for label, count in (report.get("failure_classification_counts") or {}).items():
        lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
    lines.extend(
        [
            "",
            "## Case Ledger",
            "",
            "| Case | Family | Candidates | Scope | Should consider | Rank if reached | Caps | Query pathologies | Failure classifications |",
            "| --- | --- | ---: | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in report.get("rows", []):
        scope = (row.get("scope_probe") or {}).get("not_searched_scope")
        should = (row.get("should_consider_probe") or {}).get("would_pass_should_consider")
        rank = (row.get("ranking_probe") or {}).get("rank_against_returned_candidates")
        caps = ", ".join((row.get("cap_probe") or {}).get("cap_hit_labels") or [])
        query_pathologies = ", ".join((row.get("query_basis") or {}).get("pathologies") or [])
        classifications = ", ".join(row.get("failure_classifications") or [])
        lines.append(
            "| "
            f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
            f"{_escape_markdown_table(str(row.get('family', '')))} | "
            f"{row.get('package_candidate_count', 0)} | "
            f"`{_escape_markdown_table(str(scope))}` | "
            f"`{should}` | "
            f"{rank if rank is not None else ''} | "
            f"{_escape_markdown_table(caps)} | "
            f"{_escape_markdown_table(query_pathologies)} | "
            f"{_escape_markdown_table(classifications)} |"
        )
    return "\n".join(lines)


def render_real_history_remaining_miss_diagnostic_text(payload: dict) -> str:
    report = payload["real_history_remaining_miss_loss_point_diagnostic"]
    summary = report.get("summary") or {}
    decision = report.get("decision") or {}
    lines = [
        "Real-history remaining miss loss-point diagnostic",
        f"Status: {report.get('status')}",
        f"Baseline run: {(report.get('canonical_baseline') or {}).get('benchmark_run_id')}",
        f"Absent final refs: {summary.get('absent_final_ref_case_count', 0)}",
        f"Date-timeline only: {summary.get('all_absent_refs_are_date_timeline_user_ask')}",
        f"Dedupe measured cause count: {summary.get('dedupe_measured_cause_count', 0)}",
        f"Would pass should_consider: {summary.get('would_pass_should_consider_count', 0)}",
        f"Rank-if-reached 1: {summary.get('rank_if_reached_1_count', 0)}",
        f"Instrumented expected-ref evidence: {summary.get('instrumented_expected_ref_available_count', 0)}",
        f"Line reached before stop: {summary.get('expected_record_line_reached_before_elapsed_stop_count', 0)}",
        f"Pre-final present: {summary.get('expected_record_present_in_pre_final_candidate_state_count', 0)}",
        f"Final surfacing loss: {summary.get('final_output_ref_surfacing_loss_count', 0)}",
        f"Repair supported: {decision.get('repair_supported_by_jsh702')}",
        f"Headline: {decision.get('headline')}",
    ]
    for label, count in (summary.get("path_safe_loss_point_counts") or {}).items():
        lines.append(f"- {label}: {count}")
    return "\n".join(lines)


def render_real_history_remaining_miss_diagnostic_markdown(payload: dict) -> str:
    report = payload["real_history_remaining_miss_loss_point_diagnostic"]
    summary = report.get("summary") or {}
    baseline = report.get("canonical_baseline") or {}
    decision = report.get("decision") or {}
    lines = [
        "# Remaining Miss Loss-Point Diagnostic",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Canonical benchmark run:** `{baseline.get('benchmark_run_id')}`",
        f"**Audited baseline:** `{baseline.get('audited_baseline')}`",
        f"**Package search result:** `{baseline.get('package_search_first_pass_hits')}/{baseline.get('package_search_first_pass_cases')}` first-pass hits",
        "",
        "## Headline",
        "",
        decision.get("headline") or "",
        "",
        f"- Repair supported by JSH-702 alone: `{decision.get('repair_supported_by_jsh702')}`",
        f"- Reason: {decision.get('reason')}",
        f"- Next step: {decision.get('recommended_next_step')}",
        "",
        "## Summary Counters",
        "",
        "| Counter | Value |",
        "| --- | ---: |",
        f"| absent final expected refs | {summary.get('absent_final_ref_case_count', 0)} |",
        f"| date-timeline family only | {summary.get('all_absent_refs_are_date_timeline_user_ask')} |",
        f"| candidate_records_deduped == 0 | {summary.get('candidate_records_deduped_zero_count', 0)} |",
        f"| dedupe measured cause | {summary.get('dedupe_measured_cause_count', 0)} |",
        f"| would pass should_consider | {summary.get('would_pass_should_consider_count', 0)} |",
        f"| rank-if-reached = 1 | {summary.get('rank_if_reached_1_count', 0)} |",
        f"| pre-final internal candidate lists available | {summary.get('pre_final_internal_candidate_list_available_count', 0)} |",
        f"| instrumented expected-ref evidence available | {summary.get('instrumented_expected_ref_available_count', 0)} |",
        f"| expected file opened before scan stop | {summary.get('expected_file_opened_before_scan_stop_instrumented_count', 0)} |",
        f"| expected record line reached before elapsed stop | {summary.get('expected_record_line_reached_before_elapsed_stop_count', 0)} |",
        f"| expected record present before reservoir retention | {summary.get('expected_record_present_before_reservoir_retention_count', 0)} |",
        f"| expected record present in pre-final candidate state | {summary.get('expected_record_present_in_pre_final_candidate_state_count', 0)} |",
        f"| expected record present in final candidate groups | {summary.get('expected_record_present_in_final_candidate_groups_count', 0)} |",
        f"| expected ref present in sanitized top refs | {summary.get('sanitized_top_candidate_refs_present_count', 0)} |",
        f"| pre-final retention loss | {summary.get('pre_final_retention_loss_count', 0)} |",
        f"| final output/ref surfacing loss | {summary.get('final_output_ref_surfacing_loss_count', 0)} |",
        f"| elapsed cap | {summary.get('elapsed_cap_count', 0)} |",
        f"| match cap | {summary.get('match_cap_count', 0)} |",
        f"| weak reservoir cap | {summary.get('weak_reservoir_cap_count', 0)} |",
        "",
        "## Loss-Point Buckets",
        "",
        "| Bucket | Cases |",
        "| --- | ---: |",
    ]
    for label, count in (summary.get("path_safe_loss_point_counts") or {}).items():
        lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
    lines.extend(["", "## Next Repair Candidate Counts", "", "| Candidate | Cases |", "| --- | ---: |"])
    for label, count in (summary.get("next_repair_candidate_counts") or {}).items():
        lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
    if not summary.get("next_repair_candidate_counts"):
        lines.append("| `(none)` | 0 |")
    lines.extend(["", "## Query/Source-Planning Signals", "", "| Signal | Cases |", "| --- | ---: |"])
    for label, count in (summary.get("query_pathology_counts") or {}).items():
        lines.append(f"| `{_escape_markdown_table(str(label))}` | {count} |")
    if not summary.get("query_pathology_counts"):
        lines.append("| `(none)` | 0 |")
    lines.extend(["", "## Evidence Limits", ""])
    for key, value in (report.get("evidence_limits") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Behavior Boundary", ""])
    for key, value in (report.get("behavior_boundary") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Privacy Contract", ""])
    for key, value in (report.get("privacy_contract") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    auxiliary = report.get("auxiliary_private_evidence") or {}
    lines.extend(
        [
            "",
            "## Auxiliary Evidence",
            "",
            f"- Raw run label: `{auxiliary.get('raw_run_label')}`",
            f"- Used: `{auxiliary.get('used')}`",
            f"- Reason: {auxiliary.get('reason')}",
            "",
            "## Case Ledger",
            "",
            "| Case | Family | Loss bucket | Selected | Queued | Opened | Line reached | Candidate path | Pre-final status | Final surfaced | Files opened | Bytes parsed | Handoff bytes | Next repair candidate | Query signals |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report.get("rows", []):
        scope = row.get("expected_file_scope") or {}
        query = row.get("query_source_planning_signal") or {}
        inst = row.get("instrumented_expected_ref_evidence") or {}
        cost = row.get("cost_evidence") or {}
        lines.append(
            "| "
            f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
            f"{_escape_markdown_table(str(row.get('family', '')))} | "
            f"`{_escape_markdown_table(str(row.get('path_safe_loss_point_bucket', '')))}` | "
            f"`{inst.get('expected_file_selected_by_source_plan')}` | "
            f"`{inst.get('expected_file_queued_for_scan')}` | "
            f"`{inst.get('expected_file_opened_before_scan_stop')}` | "
            f"`{inst.get('expected_record_line_reached_before_elapsed_stop')}` | "
            f"`{_escape_markdown_table(str(inst.get('candidate_path_label')))}` | "
            f"`{_escape_markdown_table(str(inst.get('kept_or_dropped_before_pre_final')))}` | "
            f"`{inst.get('sanitized_top_candidate_refs_present') or inst.get('expected_record_present_in_final_candidate_groups')}` | "
            f"{cost.get('files_opened', 0)} | "
            f"{cost.get('bytes_parsed', 0)} | "
            f"{cost.get('handoff_bytes', 0)} | "
            f"`{_escape_markdown_table(str(row.get('next_repair_candidate')))}` | "
            f"{_escape_markdown_table(', '.join(query.get('pathology_labels') or []))} |"
        )
    return "\n".join(lines)


def render_real_history_opened_file_progress_diagnostic_text(payload: dict) -> str:
    report = payload["real_history_opened_file_progress_displacement_diagnostic"]
    summary = report.get("summary") or {}
    decision = report.get("decision") or {}
    compared = report.get("compared_runs") or {}
    lines = [
        "Real-history opened-file progress displacement diagnostic",
        f"Status: {report.get('status')}",
        f"JSH-706 package_search: {summary.get('jsh706_package_search_first_pass_hits')}/18",
        f"JSH-707 failed package_search: {summary.get('jsh707_failed_package_search_first_pass_hits')}/18",
        f"Delta: {summary.get('package_search_first_pass_hit_delta')}",
        f"Regressed cases: {summary.get('regressed_case_count')}",
        f"Opened date-timeline misses: {summary.get('canonical_opened_date_timeline_miss_count')}",
        f"Headline: {decision.get('headline')}",
    ]
    if "jsh709_fresh" in compared:
        lines.append(
            "JSH-709 fresh package_search: "
            f"{summary.get('jsh709_fresh_package_search_first_pass_hits')}/18; "
            f"Recall@10={summary.get('jsh709_fresh_package_search_recall_at_10')}; "
            f"MRR={summary.get('jsh709_fresh_package_search_mrr')}"
        )
    for row in report.get("rows", []):
        displacement = row.get("candidate_displacement") or {}
        fresh = row.get("jsh709_outcome") or {}
        lines.append(
            "- "
            f"{row.get('case_id')}: "
            f"{', '.join(displacement.get('displacement_labels') or [])}; "
            f"records_delta={displacement.get('records_read_delta')}; "
            f"probe_records={displacement.get('line_window_probe_records_added_in_jsh707')}; "
            f"jsh709={', '.join(fresh.get('outcome_labels') or [])}"
        )
    return "\n".join(lines)


def render_real_history_opened_file_progress_diagnostic_markdown(payload: dict) -> str:
    report = payload["real_history_opened_file_progress_displacement_diagnostic"]
    summary = report.get("summary") or {}
    decision = report.get("decision") or {}
    compared = report.get("compared_runs") or {}
    lines = [
        "# Opened-File Progress And Candidate Displacement Diagnostic",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        "",
        "## Compared Runs",
        "",
        "| Run | ID | Package search | Recall@10 | MRR | Caps elapsed/match/weak | Audit opened/not-reached/absent-final | Median opened/bytes/handoff |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    run_labels = ["jsh706", "jsh707_failed"] + (["jsh709_fresh"] if "jsh709_fresh" in compared else [])
    for label in run_labels:
        run = compared.get(label) or {}
        lines.append(
            "| "
            f"`{label}` | "
            f"`{_escape_markdown_table(str(run.get('run_id')))}` | "
            f"{run.get('package_search_first_pass_hits')}/{run.get('package_search_cases')} | "
            f"{run.get('package_search_recall_at_10')} | "
            f"{run.get('package_search_mrr', run.get('package_search_recall_mrr'))} | "
            f"{run.get('elapsed_cap_rows')}/{run.get('match_cap_rows')}/{run.get('weak_reservoir_cap_rows')} | "
            f"{run.get('audit_expected_file_opened_rows')}/{run.get('audit_expected_record_line_not_reached_rows')}/{run.get('audit_expected_ref_absent_final_rows')} | "
            f"{run.get('median_files_opened')}/{run.get('median_bytes_parsed')}/{run.get('median_handoff_bytes')} |"
        )
    lines.extend(
        [
            "",
            "## Headline",
            "",
            decision.get("headline") or "",
            "",
            f"- Repair supported by this diagnostic: `{decision.get('repair_supported_by_this_diagnostic')}`",
            f"- Reject retrying blind midpoint/positional probing: `{decision.get('reject_retrying_blind_midpoint_or_positional_line_window_probe')}`",
            f"- Next step: {decision.get('recommended_next_step')}",
            f"- Recommended next repair task: `{(decision.get('recommended_next_repair_task') or {}).get('title')}`",
            "",
            "## Summary Counters",
            "",
            "| Counter | Value |",
            "| --- | ---: |",
            f"| JSH-706 package_search first-pass hits | {summary.get('jsh706_package_search_first_pass_hits', 0)} |",
            f"| JSH-707 failed package_search first-pass hits | {summary.get('jsh707_failed_package_search_first_pass_hits', 0)} |",
            f"| package_search first-pass hit delta | {summary.get('package_search_first_pass_hit_delta', 0)} |",
            f"| regressed case count | {summary.get('regressed_case_count', 0)} |",
            f"| regressed cases opened but not reached in JSH-707 | {summary.get('regressed_cases_opened_but_not_reached_in_jsh707', 0)} |",
            f"| regressed cases with JSH-707 midpoint probe records | {summary.get('regressed_cases_with_jsh707_midpoint_probe_records', 0)} |",
            f"| canonical opened date-timeline miss count | {summary.get('canonical_opened_date_timeline_miss_count', 0)} |",
            f"| canonical opened date-timeline misses opened-not-reached in both runs | {summary.get('canonical_opened_date_timeline_misses_opened_not_reached_in_both_runs', 0)} |",
        ]
    )
    if "jsh709_fresh" in compared:
        lines.extend(
            [
                f"| JSH-709 fresh package_search first-pass hits | {summary.get('jsh709_fresh_package_search_first_pass_hits', 0)} |",
                f"| JSH-709 fresh Recall@10 | {summary.get('jsh709_fresh_package_search_recall_at_10', 0)} |",
                f"| JSH-709 fresh MRR | {summary.get('jsh709_fresh_package_search_mrr', 0)} |",
                f"| JSH-709 elapsed/match/weak-reservoir cap rows | {summary.get('jsh709_elapsed_cap_rows', 0)}/{summary.get('jsh709_match_cap_rows', 0)}/{summary.get('jsh709_weak_reservoir_cap_rows', 0)} |",
                f"| JSH-709 audit opened/not-reached/absent-final rows | {summary.get('jsh709_audit_expected_file_opened_rows', 0)}/{summary.get('jsh709_audit_expected_record_line_not_reached_rows', 0)}/{summary.get('jsh709_audit_expected_ref_absent_final_rows', 0)} |",
                f"| JSH-709 median opened/bytes/handoff | {summary.get('jsh709_median_files_opened', 0)}/{summary.get('jsh709_median_bytes_parsed', 0)}/{summary.get('jsh709_median_handoff_bytes', 0)} |",
                f"| JSH-709 selected/opened/reached/final expected refs | {summary.get('jsh709_selected_expected_file_count', 0)}/{summary.get('jsh709_opened_expected_file_count', 0)}/{summary.get('jsh709_reached_expected_record_line_count', 0)}/{summary.get('jsh709_final_output_present_count', 0)} |",
                f"| JSH-709 measured progress cases/records | {summary.get('jsh709_cases_with_measured_opened_file_progress', 0)}/{summary.get('jsh709_opened_file_progress_record_count', 0)} |",
                f"| JSH-709 opened-but-not-reached rows | {summary.get('jsh709_opened_but_not_reached_count', 0)} |",
            ]
        )
    lines.extend(
        [
            "",
            "## Evidence Limits",
            "",
        ]
    )
    for key, value in (report.get("evidence_limits") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Behavior Boundary", ""])
    for key, value in (report.get("behavior_boundary") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Privacy Contract", ""])
    for key, value in (report.get("privacy_contract") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Case Comparison",
            "",
            "| Case | Role | 706 hit | 707 hit | 709 hit | 706 opened/reached/final | 707 opened/reached/final | 709 opened/reached/final | 706 opened files | 707 opened files | 709 opened files | 706 records | 707 records | 709 records | 707 probe records/files | 706 phase | 707 phase | 709 phase | Displacement labels | JSH-709 labels |",
            "| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report.get("rows", []):
        j706 = row.get("jsh706") or {}
        j707 = row.get("jsh707_failed") or {}
        j709 = row.get("jsh709_fresh") or {}
        ref706 = j706.get("expected_ref") or {}
        ref707 = j707.get("expected_ref") or {}
        ref709 = j709.get("expected_ref") or {}
        displacement = row.get("candidate_displacement") or {}
        labels = ", ".join(displacement.get("displacement_labels") or [])
        outcome = row.get("jsh709_outcome") or {}
        outcome_labels = ", ".join(outcome.get("outcome_labels") or [])
        phase706 = _phase_summary(ref706)
        phase707 = _phase_summary(ref707)
        phase709 = _phase_summary(ref709)
        probe = j707.get("line_window_probe") or {}
        lines.append(
            "| "
            f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
            f"`{_escape_markdown_table(str(row.get('case_role', '')))}` | "
            f"{j706.get('first_pass_hit')} | "
            f"{j707.get('first_pass_hit')} | "
            f"{j709.get('first_pass_hit', '')} | "
            f"{_bool_triplet(ref706)} | "
            f"{_bool_triplet(ref707)} | "
            f"{_bool_triplet(ref709) if j709 else ''} | "
            f"{j706.get('opened_files_count', 0)} | "
            f"{j707.get('opened_files_count', 0)} | "
            f"{j709.get('opened_files_count', '')} | "
            f"{j706.get('records_read_total', 0)} | "
            f"{j707.get('records_read_total', 0)} | "
            f"{j709.get('records_read_total', '')} | "
            f"{probe.get('probe_records', 0)}/{probe.get('probe_files', 0)} | "
            f"{_escape_markdown_table(phase706)} | "
            f"{_escape_markdown_table(phase707)} | "
            f"{_escape_markdown_table(phase709) if j709 else ''} | "
            f"{_escape_markdown_table(labels)} |"
            f" {_escape_markdown_table(outcome_labels)} |"
        )
    lines.extend(
        [
            "",
            "## Opened-File Progress Ledger",
            "",
            "| Case | Run | File label | Order | Records read | Bucket | Expected ref file | Counter status |",
            "| --- | --- | --- | ---: | ---: | --- | --- | --- |",
        ]
    )
    for row in report.get("rows", []):
        row_run_keys = ["jsh706", "jsh707_failed"] + (["jsh709_fresh"] if "jsh709_fresh" in row else [])
        for run_key in row_run_keys:
            run = row.get(run_key) or {}
            for item in run.get("opened_file_progress") or []:
                records = item.get("records_read")
                lines.append(
                    "| "
                    f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                    f"`{run_key}` | "
                    f"`{_escape_markdown_table(str(item.get('file_label', '')))}` | "
                    f"{item.get('opened_order', 0)} | "
                    f"{records if records is not None else ''} | "
                    f"`{_escape_markdown_table(str(item.get('records_read_bucket', '')))}` | "
                    f"`{item.get('expected_ref_file')}` | "
                    f"`{_escape_markdown_table(str(item.get('counter_status', '')))}` |"
                )
    lines.extend(["", "## Measured Explanations", ""])
    for row in report.get("rows", []):
        displacement = row.get("candidate_displacement") or {}
        lines.append(
            f"- `{_escape_markdown_table(str(row.get('case_id', '')))}`: "
            f"{displacement.get('measured_explanation')}"
        )
    return "\n".join(lines)


def _bool_triplet(ref: dict) -> str:
    return (
        f"opened={ref.get('opened')}, "
        f"reached={ref.get('reached')}, "
        f"final={ref.get('final_output_status')}"
    )


def _phase_summary(ref: dict) -> str:
    return (
        f"strength={ref.get('candidate_strength')}; "
        f"pre_reservoir={ref.get('pre_reservoir_status')}; "
        f"pre_final={ref.get('pre_final_status')}; "
        f"final={ref.get('final_candidate_status')}; "
        f"reservoir={ref.get('reservoir_phase_label')}; "
        f"final_cap={ref.get('final_cap_phase_label')}"
    )


def render_real_history_timestamp_seek_text(payload: dict) -> str:
    report = payload["real_history_timestamp_seek_benchmark"]
    gate = report.get("timestamp_sidecar_gate") or {}
    summary = report.get("summary") or {}
    lines = [
        "Real-history timestamp seek benchmark",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Run: {report.get('run_id', '(none)')}",
        f"Cases: {report.get('case_count', 0)}",
        f"Roots: {', '.join(report.get('root_labels') or []) or '(none)'}",
        f"Total wall time ms: {report.get('total_wall_time_ms', 0)}",
        (
            "timestamp_sidecar_gate: "
            f"status={gate.get('status')} "
            f"reasons={', '.join(gate.get('reasons') or []) or '(none)'}"
        ),
    ]
    lines.extend(_performance_metric_text_lines(report))
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    if report.get("headline_findings"):
        lines.append("Findings:")
        lines.extend(f"- {finding}" for finding in report.get("headline_findings", []))
    for mode in ("baseline_full_scan", "manifest_planned_scan", "timestamp_seek"):
        row = summary.get(mode) or {}
        if not row:
            continue
        lines.append(
            "- "
            f"{mode}: runs={row.get('runs')} "
            f"elapsed_median_ms={row.get('median_elapsed_ms')} "
            f"elapsed_p90_ms={row.get('p90_elapsed_ms')} "
            f"files={row.get('median_files_opened')} "
            f"records={row.get('median_records_parsed')} "
            f"bytes_parsed={row.get('median_bytes_parsed')} "
            f"context_bytes={row.get('median_output_context_bytes')} "
            f"context_tokens={row.get('median_output_context_tokens_approx')} "
            f"expected_ref_order={row.get('expected_ref_order_status')}"
        )
    return "\n".join(lines)


def render_real_history_embedding_text(payload: dict) -> str:
    report = payload["real_history_embedding_benchmark"]
    gate = report.get("embedding_recall_gate") or {}
    query_metrics = report.get("query_metrics") or {}
    index_metrics = report.get("index_metrics") or {}
    case_selection = report.get("case_selection") or {}
    baseline = report.get("baseline_comparison") or {}
    lines = [
        "Real-history embedding benchmark",
        f"Status: {report.get('status')}",
        f"Data source: {report.get('data_source')}",
        f"Run: {report.get('run_id', '(none)')}",
        f"Cases: {case_selection.get('selected_case_count', 0)}",
        f"Expected embedding cases: {case_selection.get('expected_embedding_recall_case_count', 0)}",
        f"Backend: {(report.get('backend') or {}).get('selected')}",
        (
            "Index: "
            f"documents={index_metrics.get('document_count', 0)} "
            f"build_ms={index_metrics.get('index_build_time_ms', 0)} "
            f"disk_bytes={index_metrics.get('disk_index_size_bytes', 0)}"
        ),
        (
            "Queries: "
            f"gold_cases={query_metrics.get('gold_case_count', 0)} "
            f"embedding_first_pass={query_metrics.get('embedding_first_pass_hits', 0)} "
            f"baseline_first_pass={query_metrics.get('baseline_first_pass_hits', 0)} "
            f"embedding_recall_at_3={query_metrics.get('embedding_mean_recall_at_3')} "
            f"baseline_recall_at_3={query_metrics.get('baseline_mean_recall_at_3')}"
        ),
        (
            "Baseline comparison: "
            f"mode={baseline.get('mode')} "
            f"status={baseline.get('status')} "
            f"correctness_available={baseline.get('correctness_comparison_available')}"
        ),
        f"embedding_recall_gate: status={gate.get('status')} reasons={', '.join(gate.get('reasons') or []) or '(none)'}",
    ]
    lines.extend(_performance_metric_text_lines(report))
    for warning in report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    return "\n".join(lines)


def render_real_history_embedding_markdown(payload: dict) -> str:
    report = payload["real_history_embedding_benchmark"]
    gate = report.get("embedding_recall_gate") or {}
    query_metrics = report.get("query_metrics") or {}
    index_metrics = report.get("index_metrics") or {}
    case_selection = report.get("case_selection") or {}
    baseline = report.get("baseline_comparison") or {}
    storage = report.get("storage_metrics") or {}
    lines = [
        "# Real-History Embedding Benchmark",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Run:** `{report.get('run_id', '(none)')}`",
        f"**Selected cases:** `{case_selection.get('selected_case_count', 0)}`",
        f"**Expected embedding cases:** `{case_selection.get('expected_embedding_recall_case_count', 0)}`",
        f"**Caveat:** {case_selection.get('below_floor_case_count_caveat')}",
        f"**Backend:** `{(report.get('backend') or {}).get('selected')}`",
        "",
        "## Metrics",
        "",
        f"- Documents indexed: {index_metrics.get('document_count', 0)}",
        f"- Index build ms: {index_metrics.get('index_build_time_ms', 0)}",
        f"- Disk index bytes: {index_metrics.get('disk_index_size_bytes', 0)}",
        f"- Vector bytes: {storage.get('vector_bytes', 0)}",
        f"- Query cases: {query_metrics.get('case_count', 0)}",
        f"- Gold cases: {query_metrics.get('gold_case_count', 0)}",
        f"- Embedding first-pass hits: {query_metrics.get('embedding_first_pass_hits', 0)}",
        f"- Baseline first-pass hits: {query_metrics.get('baseline_first_pass_hits', 0)}",
        f"- Embedding recall@3: {query_metrics.get('embedding_mean_recall_at_3')}",
        f"- Baseline recall@3: {query_metrics.get('baseline_mean_recall_at_3')}",
        f"- Protected regressions: {query_metrics.get('protected_regressions', 0)}",
        f"- Baseline comparison mode: `{baseline.get('mode')}`",
        f"- Baseline comparison status: `{baseline.get('status')}`",
        f"- Correctness comparison available: `{baseline.get('correctness_comparison_available')}`",
    ]
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(
        [
            "",
            "## Gate Status",
            "",
            "| Gate | Status | Reasons |",
            "| --- | --- | --- |",
            (
                "| `embedding_recall_gate` | "
                f"`{gate.get('status')}` | "
                f"{_escape_markdown_table(', '.join(gate.get('reasons') or [])) or '(none)'} |"
            ),
        ]
    )
    families = case_selection.get("families") or {}
    if families:
        lines.extend(["", "## Case Families", ""])
        for family, count in families.items():
            lines.append(f"- `{family}`: `{count}`")
    rows = report.get("rows") or []
    if rows:
        lines.extend(
            [
                "",
                "## Rows",
                "",
                "| Case | Family | Embedding first pass | Baseline first pass | Candidates | Baseline candidates |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in rows:
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{_escape_markdown_table(str(row.get('case_family', '')))} | "
                f"{row.get('embedding', {}).get('first_pass_hit')} | "
                f"{row.get('baseline', {}).get('first_pass_hit')} | "
                f"{row.get('candidate_count')} | "
                f"{row.get('baseline_candidate_count')} |"
            )
    return "\n".join(lines)


def render_real_history_timestamp_seek_markdown(payload: dict) -> str:
    report = payload["real_history_timestamp_seek_benchmark"]
    gate = report.get("timestamp_sidecar_gate") or {}
    raw = report.get("raw_private_evidence") or {}
    summary = report.get("summary") or {}
    aggregate = report.get("aggregate") or {}
    lines = [
        "# JSH-050 Real-History Timestamp Seek Gate",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Run:** `{report.get('run_id', '(none)')}`",
        f"**Cases:** `{report.get('case_count', 0)}` of `{report.get('manifest_case_count', 0)}` manifest cases",
        f"**Roots:** {', '.join(f'`{label}`' for label in report.get('root_labels') or []) or '(none)'}",
        f"**Total wall time ms:** `{report.get('total_wall_time_ms', 0)}`",
        f"**Caveat:** {report.get('below_floor_case_count_caveat')}",
    ]
    if report.get("headline_findings"):
        lines.extend(["", "## Headline Findings", ""])
        lines.extend(f"- {finding}" for finding in report.get("headline_findings", []))
    lines.extend(
        [
            "",
            "## Gate Decision",
            "",
            "| Gate | Status | Reasons |",
            "| --- | --- | --- |",
            (
                "| `timestamp_sidecar_gate` | "
                f"`{gate.get('status')}` | "
                f"{_escape_markdown_table(', '.join(gate.get('reasons') or [])) or '(none)'} |"
            ),
        ]
    )
    lines.extend(
        [
            "",
            "## Private Evidence",
            "",
            f"- Raw per-case evidence stored: `{raw.get('stored')}`",
            f"- Storage: `{raw.get('storage', 'ignored_local_raw_run_artifacts')}`",
            f"- Run file label: `{raw.get('run_file_label', '(none)')}`",
            f"- Committed: `{raw.get('committed')}`",
        ]
    )
    lines.extend(_performance_metric_markdown_lines(report))
    lines.extend(
        [
            "",
            "## Mode Summary",
            "",
            "| Mode | Runs | Statuses | Elapsed median ms | Elapsed p90 ms | Files median | Records median | Bytes parsed median | Bytes parsed p90 | Context bytes median | Context tokens approx | Expected-ref order | Caps |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for mode in ("baseline_full_scan", "manifest_planned_scan", "timestamp_seek"):
        row = summary.get(mode) or {}
        if not row:
            continue
        lines.append(
            "| "
            f"`{mode}` | "
            f"{row.get('runs')} | "
            f"{_escape_markdown_table(_compact_counter_map(row.get('status_counts') or {}))} | "
            f"{row.get('median_elapsed_ms')} | "
            f"{row.get('p90_elapsed_ms')} | "
            f"{row.get('median_files_opened')} | "
            f"{row.get('median_records_parsed')} | "
            f"{row.get('median_bytes_parsed')} | "
            f"{row.get('p90_bytes_parsed')} | "
            f"{row.get('median_output_context_bytes')} | "
            f"{row.get('median_output_context_tokens_approx')} | "
            f"{_escape_markdown_table(_compact_counter_map(row.get('expected_ref_order_status') or {}))} | "
            f"{_escape_markdown_table(', '.join(row.get('cap_hit_labels') or []))} |"
        )
    timestamp = summary.get("timestamp_seek") or {}
    if timestamp:
        lines.extend(
            [
                "",
                "## Timestamp Seek Reductions",
                "",
                f"- Median parsed-byte reduction vs baseline: `{timestamp.get('median_parsed_byte_reduction_ratio_vs_baseline')}`",
                f"- P90 parsed-byte reduction vs baseline: `{timestamp.get('p90_parsed_byte_reduction_ratio_vs_baseline')}`",
                f"- Median elapsed reduction vs baseline: `{timestamp.get('median_elapsed_reduction_ratio_vs_baseline')}`",
                f"- P90 elapsed reduction vs baseline: `{timestamp.get('p90_elapsed_reduction_ratio_vs_baseline')}`",
                f"- Median parsed-byte reduction vs manifest: `{timestamp.get('median_parsed_byte_reduction_ratio_vs_manifest')}`",
                f"- P90 parsed-byte reduction vs manifest: `{timestamp.get('p90_parsed_byte_reduction_ratio_vs_manifest')}`",
                f"- Sidecar hits/misses: `{timestamp.get('sidecar_hits')}` / `{timestamp.get('sidecar_misses')}`",
                f"- Refresh counts: total `{timestamp.get('refresh_count')}`, no-change `{timestamp.get('no_change_refresh_count')}`, append `{timestamp.get('append_refresh_count')}`, stale `{timestamp.get('stale_refresh_count')}`",
                f"- Fallback reasons: `{_compact_counter_map(timestamp.get('fallback_reasons') or {})}`",
                f"- Planning sources: `{_compact_counter_map(timestamp.get('planning_sources') or {})}`",
            ]
        )
    lines.extend(["", "## Equivalence", ""])
    equivalence = aggregate.get("source_ref_order_equivalence") or {}
    if equivalence:
        lines.extend(["| Comparison | Counts |", "| --- | --- |"])
        for name, counts in equivalence.items():
            lines.append(f"| `{name}` | `{_escape_markdown_table(_compact_counter_map(counts or {}))}` |")
    else:
        lines.append("- No equivalence counters available.")
    no_evidence = aggregate.get("no_evidence_scope_equivalence") or {}
    lines.extend(
        [
            "",
            "## No-Evidence Scope",
            "",
            f"- Status: `{no_evidence.get('status', 'not_evaluated')}`",
            f"- Reason: `{no_evidence.get('reason', '(none)')}`",
            f"- Evaluated rows: `{no_evidence.get('evaluated_rows', 0)}`",
        ]
    )
    lines.extend(["", "## Privacy Contract", ""])
    for key, value in (report.get("privacy_contract") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if report.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in report.get("warnings", []))
    if report.get("rows"):
        lines.extend(
            [
                "",
                "## Case Runs",
                "",
                "| Case | Repeat | Date window | Baseline expected | Timestamp expected | Timestamp vs baseline | Timestamp status | Files | Records | Bytes parsed | Elapsed ms | Context bytes | Caps |",
                "| --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in report.get("rows", []):
            timestamp_row = row.get("timestamp_seek") or {}
            baseline_row = row.get("baseline_full_scan") or {}
            eq = row.get("source_ref_order_equivalence") or {}
            lines.append(
                "| "
                f"{_escape_markdown_table(str(row.get('case_id', '')))} | "
                f"{row.get('repeat_index', 0)} | "
                f"{_escape_markdown_table(str(row.get('date_window_status', '')))} | "
                f"{_escape_markdown_table(str(baseline_row.get('expected_ref_order_status', '')))} | "
                f"{_escape_markdown_table(str(timestamp_row.get('expected_ref_order_status', '')))} | "
                f"{_escape_markdown_table(str(eq.get('timestamp_vs_baseline', '')))} | "
                f"{_escape_markdown_table(str(timestamp_row.get('status', '')))} | "
                f"{timestamp_row.get('files_opened', 0)} | "
                f"{timestamp_row.get('records_parsed', 0)} | "
                f"{timestamp_row.get('bytes_parsed', 0)} | "
                f"{timestamp_row.get('elapsed_ms', 0)} | "
                f"{timestamp_row.get('output_context_bytes', 0)} | "
                f"{_escape_markdown_table(', '.join(row.get('cap_hit_labels') or []))} |"
            )
    return "\n".join(lines)


def render_live_terminal_protocol_text(payload: dict) -> str:
    protocol = payload["live_terminal_protocol"]
    decision = protocol.get("decision", {})
    caps = protocol.get("caps", {})
    lines = [
        "Live terminal simulation protocol",
        f"Status: {protocol.get('status')}",
        f"Data source: {protocol.get('data_source')}",
        f"Command inert: {protocol.get('command_inert')}",
        f"Live run performed: {protocol.get('live_run_performed')}",
        f"Codex spawned: {protocol.get('codex_spawned')}",
        f"Raw trace parsed: {protocol.get('raw_trace_parsed')}",
        f"Private logs inspected: {protocol.get('private_logs_inspected')}",
        f"Decision: {decision.get('live_terminal_simulation_value')}",
        f"Gate promotion: {decision.get('promotion_rule')}",
        "Caps:",
    ]
    for key in sorted(caps):
        lines.append(f"- {key}: {caps[key]}")
    lines.append("Allowlist:")
    for item in protocol.get("allowlist", []):
        lines.append(f"- {item.get('command')}: {', '.join(item.get('constraints') or [])}")
    lines.append("Denylist:")
    for item in protocol.get("denylist", []):
        lines.append(f"- {item.get('command')}: {item.get('reason')}")
    raw_trace = protocol.get("raw_trace_rule", {})
    lines.append(f"Raw trace directory: {raw_trace.get('directory')}")
    lines.append(f"Committed raw trace allowed: {raw_trace.get('committed_raw_trace_allowed')}")
    contract = protocol.get("committed_output_contract", {})
    lines.append("Allowed committed fields: " + ", ".join(contract.get("allowed_fields") or []))
    lines.append("Forbidden committed fields: " + ", ".join(contract.get("forbidden_fields") or []))
    return "\n".join(lines)


def render_paired_agent_benchmark_protocol_text(payload: dict) -> str:
    protocol = payload["paired_agent_benchmark_protocol"]
    identity = protocol.get("protocol_identity", {})
    complement = protocol.get("complements_live_terminal_protocol", {})
    trace = protocol.get("trace_contract", {})
    lines = [
        "Paired agent benchmark protocol",
        f"Status: {protocol.get('status')}",
        f"Command inert: {protocol.get('command_inert')}",
        f"Dry validation only: {protocol.get('dry_validation_only')}",
        f"Live paired benchmark run performed: {protocol.get('live_paired_benchmark_run_performed')}",
        f"Live agents spawned: {protocol.get('live_agents_spawned')}",
        f"Command: {identity.get('command')}",
        f"Pilot task: {identity.get('pilot_task')}",
        f"Case manifest dependency: {identity.get('case_manifest_dependency')}",
        f"Complements: {complement.get('related_command')} ({complement.get('related_command_role')})",
        f"Private trace root: {trace.get('private_root')}",
        "Default/manual prompt template:",
    ]
    manual = ((protocol.get("prompt_views") or {}).get("default_manual") or {}).get("prompt_template") or []
    for item in manual:
        lines.append(f"- {item}")
    lines.append("Required side metrics:")
    for key in ((protocol.get("result_schema") or {}).get("required_side_metrics") or []):
        lines.append(f"- {key}")
    lines.append("Missing counter rule: unavailable, never zero")
    return "\n".join(lines)


def render_harness_dry_run_artifacts_text(payload: dict) -> str:
    report = payload["harness_dry_run_artifacts"]
    summary = report.get("validation_summary", {})
    lines = [
        f"Harness dry-run artifacts: {str(report.get('status', 'unknown')).upper()}",
        f"Mode: {report.get('mode')}",
        f"Case: {report.get('case_id')}",
        f"Output dir: {report.get('output_dir')}",
        f"Report output: {report.get('report_output') or '(none)'}",
        f"Validation: {report.get('validation_status')} ({summary.get('checked_files', 0)} files checked)",
        f"Live benchmark run performed: {report.get('live_benchmark_run_performed')}",
        f"Model send performed: {report.get('model_send_performed')}",
        f"Missing live counter value: {report.get('missing_live_counter_value')}",
        "Artifacts:",
    ]
    for key, path in report.get("artifact_paths", {}).items():
        lines.append(f"- {key}: {path}")
    return "\n".join(lines)


def render_harness_offline_scorer_artifacts_text(payload: dict) -> str:
    report = payload["harness_offline_scorer_artifacts"]
    summary = report.get("validation_summary", {})
    lines = [
        f"Harness offline scorer artifacts: {str(report.get('status', 'unknown')).upper()}",
        f"Mode: {report.get('mode')}",
        f"Run ID: {report.get('run_id')}",
        f"Output dir: {report.get('output_dir')}",
        f"Report output: {report.get('report_output') or '(none)'}",
        f"Fixtures: {report.get('fixture_count')}",
        f"Validation: {report.get('validation_status')} ({summary.get('checked_files', 0)} files checked)",
        f"Deterministic score status: {report.get('deterministic_score_status')}",
        f"Live counter status: {report.get('live_counter_status')}",
        f"Live benchmark run performed: {report.get('live_benchmark_run_performed')}",
        f"Six-case run performed: {report.get('six_case_run_performed')}",
        f"Scenario B execution performed: {report.get('scenario_b_execution_performed')}",
        f"Winner claim created: {report.get('winner_claim_created')}",
        f"Value claim created: {report.get('value_claim_created')}",
        f"JSONL superiority claim created: {report.get('jsonl_superiority_claim_created')}",
        "Artifacts:",
    ]
    for key, path in report.get("artifact_paths", {}).items():
        lines.append(f"- {key}: {path}")
    return "\n".join(lines)


def render_harness_artifact_validation_text(payload: dict) -> str:
    report = payload["harness_artifact_validation"]
    summary = report.get("summary", {})
    lines = [
        f"Harness artifact validation: {str(report.get('status', 'unknown')).upper()}",
        f"Mode: {report.get('mode')}",
        f"Schema path: {report.get('schema_path')}",
        (
            "Files: "
            f"{summary.get('checked_files', 0)} checked, "
            f"{summary.get('pass', 0)} pass, "
            f"{summary.get('fail', 0)} fail"
        ),
        f"Families: {', '.join(report.get('checked_families', []))}",
        f"Live benchmark run performed: {report.get('live_benchmark_run_performed')}",
        f"Missing live counter value: {report.get('missing_live_counter_value')}",
    ]
    for result in report.get("results", []):
        if result.get("errors"):
            lines.append(f"Errors in {result.get('source_label')}:")
            lines.extend(f"- {item}" for item in result.get("errors", []))
    return "\n".join(lines)


def render_harness_artifact_validation_markdown(payload: dict) -> str:
    report = payload["harness_artifact_validation"]
    summary = report.get("summary", {})
    lines = [
        "# JSH-080a Harness Artifact Validation",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Mode:** `{report.get('mode')}`",
        f"**Schema path:** `{report.get('schema_path')}`",
        f"**Live benchmark run performed:** `{report.get('live_benchmark_run_performed')}`",
        f"**Model send performed:** `{report.get('model_send_performed')}`",
        f"**Codex exec performed:** `{report.get('codex_exec_performed')}`",
        f"**Docker action performed:** `{report.get('docker_action_performed')}`",
        f"**Private corpus materialized:** `{report.get('private_corpus_materialized')}`",
        f"**Claim boundary enforced:** `{report.get('claim_boundary_enforced')}`",
        f"**Missing live counter value:** `{report.get('missing_live_counter_value')}`",
        "",
        "## Summary",
        "",
        "| Checked Files | Pass | Fail | Errors | Warnings | Families |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {summary.get('checked_files', 0)} | "
            f"{summary.get('pass', 0)} | "
            f"{summary.get('fail', 0)} | "
            f"{summary.get('error_count', 0)} | "
            f"{summary.get('warning_count', 0)} | "
            f"{report.get('checked_family_count', 0)} |"
        ),
        "",
        "## Families",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report.get("checked_families", []))
    lines.extend(["", "## Results", "", "| Status | Source | Errors |", "| --- | --- | ---: |"])
    for result in report.get("results", []):
        lines.append(
            "| "
            f"{str(result.get('status', '')).upper()} | "
            f"`{_escape_markdown_table(str(result.get('source_label', '')))}` | "
            f"{len(result.get('errors', []))} |"
        )
    for result in report.get("results", []):
        if result.get("errors"):
            lines.extend(["", f"### {result.get('source_label')}", ""])
            lines.extend(f"- {item}" for item in result.get("errors", []))
    return "\n".join(lines)


def render_harness_dry_run_artifacts_markdown(payload: dict) -> str:
    report = payload["harness_dry_run_artifacts"]
    summary = report.get("validation_summary", {})
    lines = [
        "# JSH-080c Harness Dry-Run Artifacts",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Mode:** `{report.get('mode')}`",
        f"**Case:** `{report.get('case_id')}`",
        f"**Output dir:** `{report.get('output_dir')}`",
        f"**Report output:** `{report.get('report_output')}`",
        f"**Live benchmark run performed:** `{report.get('live_benchmark_run_performed')}`",
        f"**Model send performed:** `{report.get('model_send_performed')}`",
        f"**Scenario B execution performed:** `{report.get('scenario_b_execution_performed')}`",
        f"**Private corpus materialized:** `{report.get('private_corpus_materialized')}`",
        f"**Claim boundary enforced:** `{report.get('claim_boundary_enforced')}`",
        f"**Missing live counter value:** `{report.get('missing_live_counter_value')}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, path in report.get("artifact_paths", {}).items():
        lines.append(f"- `{key}`: `{path}`")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Status: `{report.get('validation_status')}`",
            f"- Checked files: `{summary.get('checked_files', 0)}`",
            f"- Errors: `{summary.get('error_count', 0)}`",
            f"- Warnings: `{summary.get('warning_count', 0)}`",
        ]
    )
    return "\n".join(lines)


def render_harness_offline_scorer_artifacts_markdown(payload: dict) -> str:
    report = payload["harness_offline_scorer_artifacts"]
    summary = report.get("validation_summary", {})
    lines = [
        "# JSH-080d Harness Offline Scorer Artifacts",
        "",
        f"**Status:** `{report.get('status')}`",
        f"**Mode:** `{report.get('mode')}`",
        f"**Run ID:** `{report.get('run_id')}`",
        f"**Output dir:** `{report.get('output_dir')}`",
        f"**Report output:** `{report.get('report_output')}`",
        f"**Fixture count:** `{report.get('fixture_count')}`",
        f"**Deterministic score status:** `{report.get('deterministic_score_status')}`",
        f"**Live counter status:** `{report.get('live_counter_status')}`",
        f"**Live benchmark run performed:** `{report.get('live_benchmark_run_performed')}`",
        f"**Six-case run performed:** `{report.get('six_case_run_performed')}`",
        f"**Scenario B execution performed:** `{report.get('scenario_b_execution_performed')}`",
        f"**Claim boundary enforced:** `{report.get('claim_boundary_enforced')}`",
        "",
        "## Artifacts",
        "",
    ]
    for key, path in report.get("artifact_paths", {}).items():
        lines.append(f"- `{key}`: `{path}`")
    lines.extend(
        [
            "",
            "## Reviewer Needed Fixtures",
            "",
        ]
    )
    reviewer_needed = report.get("reviewer_needed_fixture_ids", [])
    if reviewer_needed:
        lines.extend(f"- `{item}`" for item in reviewer_needed)
    else:
        lines.append("- `(none)`")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Status: `{report.get('validation_status')}`",
            f"- Checked files: `{summary.get('checked_files', 0)}`",
            f"- Errors: `{summary.get('error_count', 0)}`",
            f"- Warnings: `{summary.get('warning_count', 0)}`",
        ]
    )
    return "\n".join(lines)


def render_live_terminal_protocol_markdown(payload: dict) -> str:
    protocol = payload["live_terminal_protocol"]
    decision = protocol.get("decision", {})
    raw_trace = protocol.get("raw_trace_rule", {})
    opt_in = protocol.get("opt_in_contract", {})
    output_contract = protocol.get("committed_output_contract", {})
    reviewer = protocol.get("reviewer_proof_contract", {})
    template = protocol.get("sanitized_aggregate_template", {})
    counters = template.get("aggregate_counters", {})
    lines = [
        "# JSH-026c Live Terminal Simulation Safety Protocol",
        "",
        f"**Status:** `{protocol.get('status')}`",
        f"**Data source:** `{protocol.get('data_source')}`",
        f"**Command inert:** `{protocol.get('command_inert')}`",
        f"**Live run performed:** `{protocol.get('live_run_performed')}`",
        f"**Codex spawned:** `{protocol.get('codex_spawned')}`",
        f"**Raw trace parsed:** `{protocol.get('raw_trace_parsed')}`",
        f"**Private logs inspected:** `{protocol.get('private_logs_inspected')}`",
        "",
        "## Decision",
        "",
        f"- Live simulation value: `{decision.get('live_terminal_simulation_value')}`",
        f"- Adds value beyond deterministic baselines: `{decision.get('adds_value_beyond_deterministic_baselines')}`",
        f"- Promotion rule: {decision.get('promotion_rule')}",
        f"- Recommended next step: {decision.get('recommended_next_step')}",
        "",
        "## Opt-In Contract",
        "",
        f"- Future live run requires explicit approval: `{opt_in.get('future_live_run_requires_explicit_approval')}`",
        f"- Approval scope: `{opt_in.get('approval_scope')}`",
        f"- Default action: `{opt_in.get('default_action')}`",
        f"- This command can run simulation: `{opt_in.get('this_command_can_run_simulation')}`",
        f"- Requires reviewer-approved case IDs before commit: `{opt_in.get('requires_reviewer_approved_case_ids_before_commit')}`",
        "",
        "## Caps",
        "",
        "| Cap | Value |",
        "| --- | ---: |",
    ]
    for key, value in sorted((protocol.get("caps") or {}).items()):
        lines.append(f"| `{_escape_markdown_table(str(key))}` | {value} |")
    lines.extend(
        [
            "",
            "## Allowlist",
            "",
            "| Command | Constraints |",
            "| --- | --- |",
        ]
    )
    for item in protocol.get("allowlist", []):
        constraints = "; ".join(item.get("constraints") or [])
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(item.get('command')))}` | "
            f"{_escape_markdown_table(constraints)} |"
        )
    lines.extend(["", "## Denylist", "", "| Command | Reason |", "| --- | --- |"])
    for item in protocol.get("denylist", []):
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(item.get('command')))}` | "
            f"{_escape_markdown_table(str(item.get('reason')))} |"
        )
    lines.extend(
        [
            "",
            "## Raw Trace Rule",
            "",
            f"- Directory: `{raw_trace.get('directory')}`",
            f"- Git ignored: `{raw_trace.get('git_ignored')}`",
            f"- Committed raw trace allowed: `{raw_trace.get('committed_raw_trace_allowed')}`",
            f"- Private trace content allowed in reports: `{raw_trace.get('private_trace_content_allowed_in_reports')}`",
            f"- Required cleanup review: `{raw_trace.get('required_cleanup_review')}`",
            "",
            "## Committed Output Contract",
            "",
            "Allowed fields:",
        ]
    )
    lines.extend(f"- `{item}`" for item in output_contract.get("allowed_fields", []))
    lines.extend(["", "Forbidden fields:"])
    lines.extend(f"- `{item}`" for item in output_contract.get("forbidden_fields", []))
    lines.extend(["", f"Report basis: `{output_contract.get('report_basis')}`"])
    lines.extend(["", "## Reviewer Proof Contract", "", "Required checks:"])
    lines.extend(f"- `{item}`" for item in reviewer.get("required_checks", []))
    lines.extend(["", "Reviewer must confirm:"])
    lines.extend(f"- `{item}`" for item in reviewer.get("must_confirm", []))
    lines.extend(
        [
            "",
            "## Sanitized Aggregate Template",
            "",
            f"- Case count: `{template.get('case_count')}`",
            f"- Approved by reviewer: `{template.get('approved_by_reviewer')}`",
            f"- Simulation status: `{template.get('simulation_status')}`",
            f"- Diagnostic only: `{template.get('diagnostic_only')}`",
            f"- Gate promotion allowed: `{template.get('gate_promotion_allowed')}`",
            "",
            "| Counter | Value |",
            "| --- | --- |",
        ]
    )
    for key, value in counters.items():
        lines.append(f"| `{_escape_markdown_table(str(key))}` | `{value}` |")
    return "\n".join(lines)


def render_paired_agent_benchmark_protocol_markdown(payload: dict) -> str:
    protocol = payload["paired_agent_benchmark_protocol"]
    identity = protocol.get("protocol_identity", {})
    complement = protocol.get("complements_live_terminal_protocol", {})
    run_contract = protocol.get("run_contract", {})
    metric_contract = protocol.get("metric_contract", {})
    trace = protocol.get("trace_contract", {})
    output_contract = protocol.get("committed_output_contract", {})
    reviewer = protocol.get("reviewer_proof_contract", {})
    template = protocol.get("sanitized_aggregate_template", {})
    aggregate_by_side = template.get("aggregate_by_side", {})
    aggregate_comparison = template.get("aggregate_comparison", {})
    review_template = template.get("review", {})
    lines = [
        "# JSH-059b1 Paired Agent Benchmark Protocol And Dry Harness",
        "",
        f"**Status:** `{protocol.get('status')}`",
        f"**Command inert:** `{protocol.get('command_inert')}`",
        f"**Dry validation only:** `{protocol.get('dry_validation_only')}`",
        f"**Live paired benchmark run performed:** `{protocol.get('live_paired_benchmark_run_performed')}`",
        f"**Live agents spawned:** `{protocol.get('live_agents_spawned')}`",
        f"**Pilot task remains separate:** `{identity.get('pilot_task')}`",
        f"**Reviewed case manifest dependency:** `{identity.get('case_manifest_dependency')}`",
        "",
        "## Scope Boundary",
        "",
        f"- This command: `{identity.get('command')}`",
        f"- Purpose: `{identity.get('purpose')}`",
        f"- `benchmark-live-terminal-protocol` remains: `{complement.get('related_command_role')}`",
        f"- This surface adds: `{complement.get('this_command_role')}`",
        f"- Related command remains inert: `{complement.get('related_command_remains_inert')}`",
        f"- This command runs the paired pilot by default: `{complement.get('this_command_runs_pilot_by_default')}`",
        f"- Difference summary: {complement.get('difference_summary')}",
        "",
        "## Run Contract",
        "",
        f"- Sanitized case IDs required: `{run_contract.get('case_ids_must_be_sanitized')}`",
        f"- Reviewer-approved case IDs required before commit: `{run_contract.get('reviewer_approved_case_ids_required_before_commit')}`",
        f"- Run ID format: `{run_contract.get('run_id_format')}`",
        f"- Default condition: `{run_contract.get('default_condition')}`",
        f"- Alternate conditions must be labeled: `{run_contract.get('alternate_conditions_must_be_labeled')}`",
        "",
        "Dry validation mode:",
        "",
        f"- Enabled by default: `{((run_contract.get('dry_validation_mode') or {}).get('enabled_by_default'))}`",
        f"- Writes private traces: `{((run_contract.get('dry_validation_mode') or {}).get('writes_private_traces'))}`",
        f"- Writes committed aggregates only when requested: `{((run_contract.get('dry_validation_mode') or {}).get('writes_committed_aggregates_only_when_requested'))}`",
    ]
    for item in ((run_contract.get("dry_validation_mode") or {}).get("validates") or []):
        lines.append(f"- Validates: `{item}`")
    lines.extend(["", "Cleanup rules:"])
    for item in run_contract.get("cleanup_rules", []):
        lines.append(f"- {item}")

    lines.extend(
        [
            "",
            "## Prompt Views",
            "",
            "### Shared Case Packet",
            "",
            "Allowed sections:",
        ]
    )
    shared_case = (protocol.get("prompt_views") or {}).get("shared_case_packet") or {}
    lines.extend(f"- `{item}`" for item in shared_case.get("allowed_sections", []))
    lines.extend(["", "Case packet template:"])
    lines.extend(f"- {item}" for item in shared_case.get("case_packet_template", []))

    manual = (protocol.get("prompt_views") or {}).get("default_manual") or {}
    lines.extend(["", "### Default/Manual Side", "", "Allowed sections:"])
    lines.extend(f"- `{item}`" for item in manual.get("allowed_sections", []))
    lines.extend(["", "Allowed tools:", "", "| Tool | Purpose |", "| --- | --- |"])
    for item in manual.get("allowed_tools", []):
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(item.get('tool')))}` | "
            f"{_escape_markdown_table(str(item.get('purpose')))} |"
        )
    lines.extend(["", "Prompt template:"])
    lines.extend(f"- {item}" for item in manual.get("prompt_template", []))
    lines.extend(["", "Forbidden benchmark material:"])
    lines.extend(f"- `{item}`" for item in manual.get("forbidden_material", []))

    assisted = (protocol.get("prompt_views") or {}).get("jsonl_assisted") or {}
    lines.extend(["", "### JSONL-Assisted Side", "", "Allowed sections:"])
    lines.extend(f"- `{item}`" for item in assisted.get("allowed_sections", []))
    lines.extend(["", "Allowed tools:", "", "| Tool | Purpose |", "| --- | --- |"])
    for item in assisted.get("allowed_tools", []):
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(item.get('tool')))}` | "
            f"{_escape_markdown_table(str(item.get('purpose')))} |"
        )
    lines.extend(["", "Prompt template:"])
    lines.extend(f"- {item}" for item in assisted.get("prompt_template", []))

    lines.extend(["", "## Caps", "", "| Cap | Value |", "| --- | ---: |"])
    for key, value in sorted((protocol.get("caps") or {}).items()):
        lines.append(f"| `{_escape_markdown_table(str(key))}` | {value} |")

    lines.extend(
        [
            "",
            "## Trace Contract",
            "",
            f"- Private root: `{trace.get('private_root')}`",
            f"- Git ignored: `{trace.get('git_ignored')}`",
            f"- Per-run directory pattern: `{trace.get('per_run_directory_pattern')}`",
            f"- Committed raw trace allowed: `{trace.get('committed_raw_trace_allowed')}`",
            f"- Committed trace summary allowed: `{trace.get('committed_trace_summary_allowed')}`",
            "",
            "Trace files:",
        ]
    )
    lines.extend(f"- `{item}`" for item in trace.get("trace_files", []))

    contamination = protocol.get("contamination_rules") or {}
    lines.extend(
        [
            "",
            "## Contamination Labels",
            "",
            f"- Default condition requires clean manual blindness: `{contamination.get('default_condition_requires_clean_manual_blindness')}`",
            "",
            "| Label | Severity | Counts As Default Condition | Description |",
            "| --- | --- | --- | --- |",
        ]
    )
    for item in contamination.get("labels", []):
        lines.append(
            "| "
            f"`{_escape_markdown_table(str(item.get('label')))}` | "
            f"`{_escape_markdown_table(str(item.get('severity')))}` | "
            f"`{_escape_markdown_table(str(item.get('counts_as_default_condition')) )}` | "
            f"{_escape_markdown_table(str(item.get('description')))} |"
        )
    lines.extend(["", "Notes:"])
    lines.extend(f"- {item}" for item in contamination.get("notes", []))

    lines.extend(
        [
            "",
            "## Metric Contract",
            "",
            f"- Missing counter rule: {metric_contract.get('counter_rule')}",
            f"- Approximate token formula: `{((metric_contract.get('estimated_metric_rule') or {}).get('formula'))}`",
            f"- Tokenizer: `{((metric_contract.get('estimated_metric_rule') or {}).get('tokenizer'))}`",
            f"- Estimate status: `{((metric_contract.get('estimated_metric_rule') or {}).get('status'))}`",
            f"- Estimate warning: {((metric_contract.get('estimated_metric_rule') or {}).get('warning'))}",
            "",
            "Required metrics:",
        ]
    )
    lines.extend(f"- `{item}`" for item in metric_contract.get("required_metrics", []))
    example_estimates = metric_contract.get("example_estimates") or {}
    lines.extend(
        [
            "",
            "Example byte-to-token estimates:",
            f"- output_bytes={example_estimates.get('output_bytes')}, output_tokens~{example_estimates.get('output_bytes_estimated_tokens')}",
            f"- context_bytes={example_estimates.get('context_bytes')}, context_tokens~{example_estimates.get('context_bytes_estimated_tokens')}",
        ]
    )

    result_schema = protocol.get("result_schema") or {}
    lines.extend(
        [
            "",
            "## Result Schema",
            "",
            "Required case fields:",
        ]
    )
    lines.extend(f"- `{item}`" for item in result_schema.get("required_case_fields", []))
    lines.extend(["", "Required comparison fields:"])
    lines.extend(f"- `{item}`" for item in result_schema.get("required_comparison_fields", []))
    lines.extend(["", "Required review fields:"])
    lines.extend(f"- `{item}`" for item in result_schema.get("required_review_fields", []))

    lines.extend(["", "## Committed Output Contract", "", "Allowed fields:"])
    lines.extend(f"- `{item}`" for item in output_contract.get("allowed_fields", []))
    lines.extend(["", "Forbidden fields:"])
    lines.extend(f"- `{item}`" for item in output_contract.get("forbidden_fields", []))
    lines.extend(["", f"Report basis: `{output_contract.get('report_basis')}`"])

    lines.extend(["", "## Reviewer Proof Contract", "", "Required checks:"])
    lines.extend(f"- `{item}`" for item in reviewer.get("required_checks", []))
    lines.extend(["", "Reviewer must confirm:"])
    lines.extend(f"- `{item}`" for item in reviewer.get("must_confirm", []))

    lines.extend(
        [
            "",
            "## Sanitized Aggregate Template",
            "",
            f"- Run ID: `{template.get('run_id')}`",
            f"- Case count: `{template.get('case_count')}`",
            f"- Live run performed: `{template.get('live_run_performed')}`",
            f"- Pilot claim allowed: `{template.get('pilot_claim_allowed')}`",
            f"- Default condition: `{template.get('default_condition')}`",
        ]
    )
    for side_name, metrics in aggregate_by_side.items():
        lines.extend(["", f"### Aggregate Side Template: `{side_name}`", "", "| Metric | Value |", "| --- | --- |"])
        for key, value in metrics.items():
            lines.append(f"| `{_escape_markdown_table(str(key))}` | `{_escape_markdown_table(str(value))}` |")
    lines.extend(["", "### Aggregate Comparison Template", "", "| Field | Value |", "| --- | --- |"])
    for key, value in aggregate_comparison.items():
        lines.append(f"| `{_escape_markdown_table(str(key))}` | `{_escape_markdown_table(str(value))}` |")
    lines.extend(["", "### Review Template", "", "| Field | Value |", "| --- | --- |"])
    for key, value in review_template.items():
        lines.append(f"| `{_escape_markdown_table(str(key))}` | `{_escape_markdown_table(str(value))}` |")
    return "\n".join(lines)


def _benchmark_gate_markdown_lines(report: dict) -> list[str]:
    gates = {
        "ranking_quality_gate": report.get("ranking_quality_gate"),
        "rg_prefilter_gate": report.get("rg_prefilter_gate"),
        "manifest_shortlist_gate": report.get("manifest_shortlist_gate"),
        "timestamp_sidecar_gate": report.get("timestamp_sidecar_gate"),
    }
    if not any(gates.values()):
        return []
    lines = ["", "## Gate Status", ""]
    lines.extend(["| Gate | Status | Reasons |", "| --- | --- | --- |"])
    for name, gate in gates.items():
        if not gate:
            continue
        reasons = ", ".join(f"`{reason}`" for reason in gate.get("reasons", [])) or "(none)"
        lines.append(f"| `{name}` | `{gate.get('status')}` | {reasons} |")
    return lines


def render_efficiency_eval_markdown(payload: dict) -> str:
    report = payload["retrieval_efficiency_eval"]
    if report.get("status") == "invalid_fixture":
        lines = [
            "# Retrieval Efficiency Benchmark",
            "",
            "**Status:** INVALID FIXTURE",
            f"**Fixture:** `{report.get('fixture')}`",
            "",
            "## Fixture Errors",
            "",
        ]
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
        return "\n".join(lines)

    thresholds = report.get("large_impact_thresholds", {})
    lines = [
        "# Retrieval Efficiency Benchmark",
        "",
        f"**Status:** {str(report.get('status', 'unknown')).upper()}",
        f"**Fixture:** `{report.get('fixture')}`",
        f"**Data source:** `{report.get('data_source')}`",
        f"**Threshold status:** {str(thresholds.get('status', 'unknown')).upper()}",
        "",
        report.get("measurement_note", ""),
        "",
        "## Strategy Summary",
        "",
        "| Strategy | First-pass hit rate | Median context bytes | Median tokens | Median files | Median tool calls | Mean recall@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, summary in report.get("summary", {}).items():
        lines.append(
            "| "
            f"{_escape_markdown_table(name)} | "
            f"{summary.get('first_pass_hit_rate')} | "
            f"{summary.get('median_prompt_context_bytes')} | "
            f"{summary.get('median_estimated_tokens_returned')} | "
            f"{summary.get('median_files_opened')} | "
            f"{summary.get('median_tool_calls')} | "
            f"{summary.get('mean_recall_at_10')} |"
        )

    lines.extend(
        [
            "",
            "## Large Impact Thresholds",
            "",
            "| Status | Metric | Target | Actual |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for item in thresholds.get("items", []):
        status = "PASS" if item.get("passed") else "FAIL"
        target = f"{item.get('operator')} {item.get('target')}"
        lines.append(
            "| "
            f"{status} | "
            f"{_escape_markdown_table(str(item.get('metric')))} | "
            f"{_escape_markdown_table(target)} | "
            f"{item.get('actual')} |"
        )

    lines.extend(
        [
            "",
            "## Case Results",
            "",
            "| Case | Category | Strategy | Fit | Basis | Context bytes | Files | Tool calls | Missing stages | Unguarded failures |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for case in report.get("cases", []):
        for result in case.get("strategy_results", []):
            lines.append(
                "| "
                f"{_escape_markdown_table(case.get('case_id', ''))} | "
                f"{_escape_markdown_table(case.get('category', ''))} | "
                f"{_escape_markdown_table(result.get('strategy', ''))} | "
                f"{_escape_markdown_table(result.get('fit', ''))} | "
                f"{_escape_markdown_table(result.get('metric_basis', ''))} | "
                f"{result.get('prompt_context_bytes')} | "
                f"{result.get('files_opened')} | "
                f"{result.get('tool_calls')} | "
                f"{_escape_markdown_table(', '.join(result.get('missing_stages') or []) or '(none)')} | "
                f"{_escape_markdown_table(', '.join(result.get('unguarded_failure_modes') or []) or '(none)')} |"
            )

    followup = report.get("coverage_needs_followup") or {}
    stages = followup.get("candidate_generation_stages") or []
    modes = followup.get("failure_modes") or []
    if stages or modes:
        lines.extend(["", "## Follow-Up Coverage", ""])
        if stages:
            lines.append(f"- Candidate generation stages: {', '.join(stages)}")
        if modes:
            lines.append(f"- Failure modes: {', '.join(modes)}")
        if followup.get("notes"):
            lines.append(f"- Notes: {followup['notes']}")
    embedding_spike = report.get("embedding_spike")
    if embedding_spike:
        lines.extend(render_embedding_spike_markdown_lines(embedding_spike))
    return "\n".join(lines)


def render_embedding_spike_text_lines(report: dict) -> list[str]:
    if report.get("status") == "invalid_fixture":
        lines = [
            "Embedding spike: INVALID FIXTURE",
            f"Fixture: {report.get('fixture')}",
        ]
        lines.extend(f"ERROR: {error}" for error in report.get("fixture_errors", []))
        return lines
    summary = report.get("summary", {})
    index_metrics = report.get("index_metrics", {})
    query_metrics = report.get("query_metrics", {})
    lines = [
        "Embedding spike:",
        f"- Vectorizer: {report.get('vectorizer_id')} ({report.get('vector_kind')})",
        f"- Production status: {report.get('production_status')}",
        (
            "- Index: "
            f"docs={index_metrics.get('document_count')} "
            f"features={index_metrics.get('feature_count')} "
            f"bytes={index_metrics.get('serialized_index_bytes')} "
            f"index_ms={index_metrics.get('indexing_time_ms')} "
            f"peak_mem={index_metrics.get('tracemalloc_peak_bytes')}"
        ),
        (
            "- Retrieval: "
            f"vector_hit_rate={summary.get('vector_first_pass_hit_rate')} "
            f"baseline_hit_rate={summary.get('baseline_first_pass_hit_rate')} "
            f"recall@1={summary.get('vector_mean_recall_at_1')} "
            f"recall@3={summary.get('vector_mean_recall_at_3')} "
            f"recall@10={summary.get('vector_mean_recall_at_10')} "
            f"median_query_ms={query_metrics.get('median_query_latency_ms')}"
        ),
        (
            "- Family flags: "
            f"valuable_vector_better={summary.get('valuable_family_vector_better')} "
            f"worse_vector_worse={summary.get('worse_family_vector_worse')} "
            f"expected_matches={summary.get('expected_behavior_matches')}"
        ),
    ]
    return lines


def render_embedding_spike_markdown_lines(report: dict) -> list[str]:
    lines = ["", "## Embedding Spike", ""]
    if report.get("status") == "invalid_fixture":
        lines.extend(
            [
                "**Status:** INVALID FIXTURE",
                f"**Fixture:** `{report.get('fixture')}`",
                "",
                "### Fixture Errors",
                "",
            ]
        )
        lines.extend(f"- {error}" for error in report.get("fixture_errors", []))
        return lines
    summary = report.get("summary", {})
    index_metrics = report.get("index_metrics", {})
    query_metrics = report.get("query_metrics", {})
    lines.extend(
        [
            f"**Vectorizer:** `{report.get('vectorizer_id')}` / `{report.get('vector_kind')}`",
            f"**Production status:** `{report.get('production_status')}`",
            "",
            report.get("measurement_note", ""),
            "",
            "### Cost Metrics",
            "",
            "| Documents | Features | Serialized bytes | Index ms | Peak memory bytes | Median query ms | Max query ms |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                "| "
                f"{index_metrics.get('document_count')} | "
                f"{index_metrics.get('feature_count')} | "
                f"{index_metrics.get('serialized_index_bytes')} | "
                f"{index_metrics.get('indexing_time_ms')} | "
                f"{index_metrics.get('tracemalloc_peak_bytes')} | "
                f"{query_metrics.get('median_query_latency_ms')} | "
                f"{query_metrics.get('max_query_latency_ms')} |"
            ),
            "",
            "### Retrieval Summary",
            "",
            "| Vector hit rate | Baseline hit rate | Recall@1 | Recall@3 | Recall@10 | Valuable vector better | Worse vector worse | Expected matches |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                "| "
                f"{summary.get('vector_first_pass_hit_rate')} | "
                f"{summary.get('baseline_first_pass_hit_rate')} | "
                f"{summary.get('vector_mean_recall_at_1')} | "
                f"{summary.get('vector_mean_recall_at_3')} | "
                f"{summary.get('vector_mean_recall_at_10')} | "
                f"{summary.get('valuable_family_vector_better')} | "
                f"{summary.get('worse_family_vector_worse')} | "
                f"{summary.get('expected_behavior_matches')} |"
            ),
            "",
            "### Case Family Results",
            "",
            "| Case | Family | Scenario | Effect | Vector rank | Baseline rank |",
            "| --- | --- | --- | --- | ---: | ---: |",
        ]
    )
    for case in report.get("cases", []):
        lines.append(
            "| "
            f"{_escape_markdown_table(case.get('case_id', ''))} | "
            f"{_escape_markdown_table(case.get('case_family', ''))} | "
            f"{_escape_markdown_table(case.get('scenario', ''))} | "
            f"{_escape_markdown_table(case.get('case_family_effect', ''))} | "
            f"{case.get('vector', {}).get('gold_rank')} | "
            f"{case.get('baseline', {}).get('gold_rank')} |"
        )
    guidance = report.get("production_guidance", {})
    if guidance:
        lines.extend(
            [
                "",
                "### Production Guidance",
                "",
                f"- Default live search: `{guidance.get('default_live_search')}`",
                f"- Storage: `{guidance.get('storage')}`",
                f"- Exact refs required: `{guidance.get('exact_refs_required')}`",
                f"- Reason: {guidance.get('reason')}",
            ]
        )
    return lines


def render_structured_tool_events_text(payload: dict) -> str:
    report = payload["structured_tool_events"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "Structured Tool Events",
        f"Status: {report.get('status')}",
        (
            "Scope: "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "Evidence: "
            f"{efficiency.get('event_count', 0)} events, "
            f"{efficiency.get('excluded_mention_count', 0)} excluded mentions"
        ),
    ]
    if report.get("report_written"):
        lines.append(f"Report written: {report['report_written']}")
    for event in report.get("events", [])[:12]:
        ref = event.get("source_ref") or {}
        target_values = [target.get("value") for target in event.get("targets", [])[:3]]
        suffix = f" targets={', '.join(target_values)}" if target_values else ""
        command = f" command={event.get('command')}" if event.get("command") else ""
        lines.append(
            "- "
            f"{event.get('event_id')} {event.get('event_type')} "
            f"{event.get('confidence')} "
            f"{ref.get('path')}:{ref.get('line')}"
            f"{command}{suffix}"
        )
    for item in report.get("excluded_mentions", [])[:5]:
        ref = item.get("source_ref") or {}
        lines.append(f"- excluded {ref.get('path')}:{ref.get('line')} {item.get('reason')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)


def render_structured_tool_events_markdown(payload: dict) -> str:
    report = payload["structured_tool_events"]
    efficiency = report.get("token_efficiency", {})
    lines = [
        "# Structured Tool Events",
        "",
        f"**Status:** {report.get('status')}",
        (
            "**Scope:** "
            f"{efficiency.get('searched_files', 0)} files, "
            f"{efficiency.get('searched_bytes', 0)} bytes searched"
        ),
        (
            "**Evidence:** "
            f"{efficiency.get('event_count', 0)} events, "
            f"{efficiency.get('excluded_mention_count', 0)} excluded mentions"
        ),
        "",
        "## Events",
        "",
        "| ID | Type | Confidence | Source | Tool | Command/Targets |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for event in report.get("events", [])[:24]:
        ref = event.get("source_ref") or {}
        source = f"{ref.get('path')}:{ref.get('line')}"
        targets = ", ".join(target.get("value", "") for target in event.get("targets", [])[:3])
        command = event.get("command") or targets
        lines.append(
            "| "
            f"{_escape_markdown_table(str(event.get('event_id')))} | "
            f"{_escape_markdown_table(str(event.get('event_type')))} | "
            f"{_escape_markdown_table(str(event.get('confidence')))} | "
            f"{_escape_markdown_table(source)} | "
            f"{_escape_markdown_table(str(event.get('tool_name') or ''))} | "
            f"{_escape_markdown_table(str(command or ''))} |"
        )
    excluded = report.get("excluded_mentions", [])
    if excluded:
        lines.extend(["", "## Excluded Mentions", ""])
        for item in excluded[:8]:
            ref = item.get("source_ref") or {}
            lines.append(f"- {ref.get('path')}:{ref.get('line')} {item.get('reason')}")
    for warning in payload.get("warnings", []) + report.get("warnings", []):
        lines.append(f"Warning: {warning}")
    for blind in payload.get("blind_spots", []):
        lines.append(f"Blind spot: {blind}")
    return "\n".join(lines)
