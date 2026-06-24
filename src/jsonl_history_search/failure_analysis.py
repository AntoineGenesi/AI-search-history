from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PLAY_BY_PLAY_LIMIT = 40
INTERESTING_MOMENT_LIMIT = 20
NEXT_READ_LIMIT = 20
NARRATIVE_LEDGER_LIMIT = 40
REPO_CHRONOLOGY_LIMIT = 80

TASK_ID_RE = re.compile(r"\b(?:jsh|sl)-\d+[a-z]?\b", re.IGNORECASE)
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
SAFE_REPO_PATH_PREFIXES = (
    ("codex/state/reviews/", "review_path"),
    ("reviews/", "review_path"),
    ("tasks/", "task_path"),
    ("docs/", "doc_path"),
    ("reports/", "report_path"),
)

UNSUPPORTED_CLAIMS = {
    "root_cause_claim_supported": False,
    "all_history_completeness_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "speed_claim_supported": False,
    "token_savings_claim_supported": False,
    "pressure_improvement_claim_supported": False,
    "product_value_claim_supported": False,
}

PRIVACY_FLAGS = {
    "raw_private_transcript_text_included": False,
    "raw_private_paths_included": False,
    "raw_session_ids_included": False,
    "raw_query_text_included": False,
    "source_line_payloads_included": False,
    "private_embedding_artifacts_included": False,
    "saved_report_paths_echoed": False,
    "hashed_source_handles_included": True,
    "line_numbers_included": True,
}

NON_GOAL_BOUNDARIES = {
    "llm_prose_generation_used": False,
    "retrieval_rerun": False,
    "ranking_or_cap_changed": False,
    "continuation_ledger_changed": False,
    "live_model_send_used": False,
    "docker_or_auth_harness_used": False,
}


def build_failure_analysis_payload(
    *,
    semantic_debug_report: Path,
    story_id: str,
    forensics_report: Path | None = None,
    follow_next_reads_report: Path | None = None,
) -> dict[str, Any]:
    semantic_loaded = _load_report_packet(semantic_debug_report, "semantic_debug")
    forensics_loaded = _load_report_packet(forensics_report, "forensics") if forensics_report else _not_supplied("forensics")
    follow_loaded = (
        _load_report_packet(follow_next_reads_report, "follow_next_reads")
        if follow_next_reads_report
        else _not_supplied("follow_next_reads")
    )

    semantic_packet = semantic_loaded.get("packet") if semantic_loaded.get("loaded") else {}
    forensics_packet = forensics_loaded.get("packet") if forensics_loaded.get("loaded") else {}
    follow_packet = follow_loaded.get("packet") if follow_loaded.get("loaded") else {}

    play_by_play = _build_play_by_play(semantic_packet, forensics_packet) if semantic_packet else []
    interesting_moments = _build_interesting_moments(semantic_packet, forensics_packet, follow_packet)
    next_reads = _build_next_reads(semantic_packet, follow_packet)
    failure_mode_ledger = _build_failure_mode_ledger(semantic_packet, forensics_packet, follow_packet, next_reads)
    committed_safe_narrative_ledger = _build_committed_safe_narrative_ledger(
        semantic_packet,
        follow_packet,
        next_reads,
    )
    date_range = _date_range(play_by_play, interesting_moments)
    transcript_recovery_date_range = _transcript_recovery_date_range(semantic_packet, follow_packet)
    repo_handle_chronology = _build_repo_handle_chronology(
        semantic_packet,
        forensics_packet,
        follow_packet,
        next_reads,
        committed_safe_narrative_ledger,
        transcript_date_range=transcript_recovery_date_range,
    )
    narrative_readiness = _build_narrative_readiness(
        semantic_loaded,
        follow_loaded,
        semantic_packet,
        follow_packet,
        next_reads,
        committed_safe_narrative_ledger,
    )
    evidence_inputs = _build_evidence_backed_summary_inputs(
        semantic_loaded,
        forensics_loaded,
        follow_loaded,
        semantic_packet,
        forensics_packet,
        follow_packet,
    )
    claim_boundaries = _claim_boundaries(semantic_loaded, forensics_loaded, follow_loaded)
    privacy_flags = dict(PRIVACY_FLAGS)
    privacy_flags["input_reports_may_contain_raw_private_material"] = _input_reports_may_contain_raw_private_material(
        semantic_packet,
        forensics_packet,
        follow_packet,
    )
    privacy_flags["output_contains_only_sanitized_report_summaries"] = True

    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if semantic_loaded.get("loaded") else "invalid_input",
        "story_scope": _story_scope(story_id, semantic_packet, semantic_loaded),
        "date_range": date_range,
        "evidence_backed_summary_inputs": evidence_inputs,
        "play_by_play": play_by_play,
        "failure_mode_ledger": failure_mode_ledger,
        "narrative_readiness": narrative_readiness,
        "repo_handle_chronology": repo_handle_chronology,
        "committed_safe_narrative_ledger": committed_safe_narrative_ledger,
        "interesting_moments": interesting_moments,
        "tool_critique": _tool_critique(
            semantic_loaded=semantic_loaded,
            forensics_loaded=forensics_loaded,
            follow_loaded=follow_loaded,
            semantic_packet=semantic_packet,
            forensics_packet=forensics_packet,
            follow_packet=follow_packet,
            play_by_play=play_by_play,
            next_reads=next_reads,
        ),
        "evidence_vs_inference": _evidence_vs_inference(
            semantic_packet,
            forensics_packet,
            follow_packet,
            play_by_play,
            next_reads,
            failure_mode_ledger,
        ),
        "claim_boundaries": claim_boundaries,
        "next_reads": next_reads,
        "privacy_flags": privacy_flags,
    }
    return {"failure_analysis": packet}


def _load_report_packet(path: Path | None, packet_key: str) -> dict[str, Any]:
    if path is None:
        return _not_supplied(packet_key)
    try:
        loaded = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except OSError:
        return _invalid_input(packet_key, "read_failed")
    except json.JSONDecodeError:
        return _invalid_input(packet_key, "json_invalid")
    packet = loaded.get(packet_key) if isinstance(loaded, dict) else None
    if not isinstance(packet, dict) and packet_key == "semantic_debug" and isinstance(loaded, dict):
        packet = loaded if loaded.get("schema_version") or loaded.get("answer_readiness") else None
    if not isinstance(packet, dict):
        return _invalid_input(packet_key, f"{packet_key}_packet_missing")
    return {
        "name": packet_key,
        "supplied": True,
        "loaded": True,
        "status": str(packet.get("status") or "loaded"),
        "schema_version": packet.get("schema_version"),
        "packet": packet,
    }


def _not_supplied(packet_key: str) -> dict[str, Any]:
    return {
        "name": packet_key,
        "supplied": False,
        "loaded": False,
        "status": "not_supplied",
        "schema_version": None,
    }


def _invalid_input(packet_key: str, reason: str) -> dict[str, Any]:
    return {
        "name": packet_key,
        "supplied": True,
        "loaded": False,
        "status": "invalid",
        "schema_version": None,
        "invalid_reason": reason,
    }


def _story_scope(story_id: str, semantic_packet: dict[str, Any], semantic_loaded: dict[str, Any]) -> dict[str, Any]:
    question_family = semantic_packet.get("question_family") if isinstance(semantic_packet.get("question_family"), dict) else {}
    scope = semantic_packet.get("scope") if isinstance(semantic_packet.get("scope"), dict) else {}
    decision_context = (
        semantic_packet.get("decision_context") if isinstance(semantic_packet.get("decision_context"), dict) else {}
    )
    return {
        "story_id": story_id,
        "semantic_debug_report_loaded": bool(semantic_loaded.get("loaded")),
        "semantic_debug_status": semantic_loaded.get("status"),
        "primary_question_family": question_family.get("primary") or decision_context.get("primary_question_family"),
        "detected_supported_question_families": list(question_family.get("detected_supported") or [])[:8],
        "selected_routes": list(decision_context.get("selected_routes") or [])[:8],
        "searched_scope": _summarize_scope(scope.get("searched_scope") or scope),
        "not_searched_scope": _summarize_scope(scope.get("not_searched_scope") or {}),
        "query_text_included": False,
    }


def _summarize_scope(scope: Any) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    keys = (
        "root_count",
        "searched_files",
        "searched_bytes",
        "searched_records",
        "candidate_count",
        "warning_count",
        "blind_spot_count",
        "elapsed_cap_hit",
        "match_cap_hit",
        "file_cap_hit",
        "byte_cap_hit",
    )
    summary = {key: scope.get(key) for key in keys if key in scope}
    for key in ("status", "answer_scope", "scope_kind"):
        if key in scope:
            summary[key] = scope.get(key)
    return summary


def _build_evidence_backed_summary_inputs(
    semantic_loaded: dict[str, Any],
    forensics_loaded: dict[str, Any],
    follow_loaded: dict[str, Any],
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    inputs = [
        {
            "input": "semantic_debug",
            "supplied": semantic_loaded.get("supplied"),
            "loaded": semantic_loaded.get("loaded"),
            "status": semantic_loaded.get("status"),
            "schema_version": semantic_loaded.get("schema_version"),
            "candidate_count": len(_list(semantic_packet.get("strongest_candidates"))),
            "timeline_count": len(_list(semantic_packet.get("timeline"))),
            "next_read_count": len(_list(semantic_packet.get("next_reads"))),
            "route_count": len(_list(_nested(semantic_packet, "decision_context").get("selected_routes"))),
        },
        {
            "input": "forensics",
            "supplied": forensics_loaded.get("supplied"),
            "loaded": forensics_loaded.get("loaded"),
            "status": forensics_loaded.get("status"),
            "schema_version": forensics_loaded.get("schema_version"),
            "phase_count": len(_list(forensics_packet.get("phase_timeline"))),
            "join_count": len(_list(forensics_packet.get("claim_action_review_joins"))),
            "review_gate_status": _nested(forensics_packet, "review_gate").get("status"),
        },
        {
            "input": "follow_next_reads",
            "supplied": follow_loaded.get("supplied"),
            "loaded": follow_loaded.get("loaded"),
            "status": follow_loaded.get("status"),
            "schema_version": follow_loaded.get("schema_version"),
            "selected_count": _int(follow_packet.get("selected_count")),
            "expanded_count": _int(follow_packet.get("expanded_count")),
            "failed_count": _int(follow_packet.get("failed_count")),
            "usable_recovery_evidence_count": _int(follow_packet.get("usable_recovery_evidence_count")),
        },
    ]
    for item, loaded in ((inputs[0], semantic_loaded), (inputs[1], forensics_loaded), (inputs[2], follow_loaded)):
        if loaded.get("invalid_reason"):
            item["invalid_reason"] = loaded["invalid_reason"]
    return inputs


def _build_play_by_play(semantic_packet: dict[str, Any], forensics_packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(_list(semantic_packet.get("timeline")), 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "source": "semantic_debug.timeline",
                    "source_index": index,
                    "timestamp_utc": row.get("timestamp_utc") or row.get("timestamp"),
                    "role": row.get("role"),
                    "candidate_id": row.get("candidate_id"),
                    "confidence": row.get("confidence"),
                    "phase_hint": row.get("phase_hint"),
                    "source_handle": _source_handle(row.get("source_ref")),
                }
            )
        )
    for index, row in enumerate(_list(_nested(semantic_packet, "tool_action_proof").get("events")), 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "source": "semantic_debug.tool_action_proof",
                    "source_index": index,
                    "timestamp_utc": row.get("timestamp_utc") or row.get("timestamp"),
                    "event_type": row.get("event_type"),
                    "event_family": row.get("event_family"),
                    "tool_name": row.get("tool_name"),
                    "action": row.get("action"),
                    "confidence": row.get("confidence"),
                    "source_handle": _source_handle(row.get("source_ref")),
                }
            )
        )
    for index, row in enumerate(_list(forensics_packet.get("phase_timeline")), 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "source": "forensics.phase_timeline",
                    "source_index": index,
                    "timestamp_utc": row.get("timestamp_utc") or row.get("timestamp"),
                    "phase_kind": row.get("phase_kind") or row.get("kind"),
                    "evidence_kind": row.get("evidence_kind"),
                    "claim_action": row.get("claim_action"),
                    "support": row.get("support"),
                    "confidence": row.get("confidence"),
                    "source_handle": _source_handle(row.get("source_ref")),
                }
            )
        )
    return _sorted_rows(rows)[:PLAY_BY_PLAY_LIMIT]


def _build_failure_mode_ledger(
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    next_reads: dict[str, Any],
) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    readiness = _nested(semantic_packet, "answer_readiness")
    if readiness:
        status = str(readiness.get("status") or "")
        decision = str(readiness.get("decision") or "")
        if status in {"inspect_recovery_first", "insufficient_evidence", "audit_route_required"} or decision in {
            "inspect_recovery_reads",
            "gather_more_evidence",
            "run_audit_route",
        }:
            ledger.append(
                {
                    "failure_mode": "answer_not_ready_without_followup",
                    "status": status or "unknown",
                    "evidence_surface": "semantic_debug.answer_readiness",
                    "decision": decision or None,
                    "basis": _safe_basis(readiness.get("basis")),
                    "interpretation_boundary": "readiness routing is evidence-backed; it is not a root-cause claim",
                }
            )
    no_evidence = _nested(semantic_packet, "no_evidence")
    if no_evidence and no_evidence.get("answer_scope") in {"not_found_in_searched_scope", "scoped_no_evidence"}:
        ledger.append(
            {
                "failure_mode": "searched_scope_no_evidence",
                "status": no_evidence.get("answer_scope"),
                "evidence_surface": "semantic_debug.no_evidence",
                "interpretation_boundary": "not found means absent from searched scope only",
            }
        )
    cap_notes = list(_nested(semantic_packet, "audit_plan").get("cap_pressure_notes") or [])
    if cap_notes:
        ledger.append(
            {
                "failure_mode": "cap_or_omission_pressure",
                "status": "present",
                "evidence_surface": "semantic_debug.audit_plan.cap_pressure_notes",
                "note_count": len(cap_notes),
                "notes": [str(item) for item in cap_notes[:4]],
                "interpretation_boundary": "cap notes identify searched-scope limitations, not all-history completeness",
            }
        )
    support = _nested(forensics_packet, "support_summary") or _nested(_nested(forensics_packet, "summary"), "support_summary")
    unsupported = _int(support.get("unsupported_support_count"))
    inferred = _int(support.get("inferred_support_count"))
    if unsupported or inferred:
        ledger.append(
            {
                "failure_mode": "unsupported_or_inferred_support",
                "status": "present",
                "evidence_surface": "forensics.support_summary",
                "unsupported_support_count": unsupported,
                "inferred_support_count": inferred,
                "interpretation_boundary": "support labels classify evidence precision; they do not prove intent",
            }
        )
    failed_count = _int(follow_packet.get("failed_count"))
    if failed_count:
        ledger.append(
            {
                "failure_mode": "follow_next_read_failures",
                "status": str(follow_packet.get("status") or "unknown"),
                "evidence_surface": "follow_next_reads",
                "failed_count": failed_count,
                "failure_reasons": _failure_reasons(follow_packet)[:8],
                "interpretation_boundary": "failed follow reads are recovery gaps, not proof that evidence does not exist",
            }
        )
    if next_reads.get("status") in {"available", "followup_gap"}:
        ledger.append(
            {
                "failure_mode": "additional_source_context_needed",
                "status": next_reads.get("status"),
                "evidence_surface": "failure_analysis.next_reads",
                "next_read_count": next_reads.get("count", 0),
                "interpretation_boundary": "next reads identify how to continue reconstruction",
            }
        )
    if not ledger:
        ledger.append(
            {
                "failure_mode": "no_failure_mode_classified_from_saved_reports",
                "status": "gap",
                "evidence_surface": "failure_analysis",
                "interpretation_boundary": "saved reports did not expose a deterministic failure-mode row",
            }
        )
    return ledger


def _build_interesting_moments(
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    moments: list[dict[str, Any]] = []
    for index, candidate in enumerate(_list(semantic_packet.get("strongest_candidates")), 1):
        if not isinstance(candidate, dict):
            continue
        moments.append(
            _clean_row(
                {
                    "kind": "candidate",
                    "source": "semantic_debug.strongest_candidates",
                    "source_index": index,
                    "timestamp_utc": candidate.get("timestamp_utc") or candidate.get("timestamp"),
                    "candidate_id": candidate.get("candidate_id"),
                    "rank": candidate.get("rank"),
                    "role": candidate.get("role"),
                    "confidence": candidate.get("confidence"),
                    "reason_codes": [str(item) for item in _list(candidate.get("reason_codes"))[:8]],
                    "source_handle": _source_handle(candidate.get("source_ref")),
                }
            )
        )
    review_gate = _nested(forensics_packet, "review_gate")
    if review_gate:
        moments.append(
            _clean_row(
                {
                    "kind": "review_gate",
                    "source": "forensics.review_gate",
                    "status": review_gate.get("status"),
                    "verdict": review_gate.get("verdict"),
                    "support": review_gate.get("support"),
                    "source_handle": _source_handle(review_gate.get("source_ref")),
                }
            )
        )
    for reason in _failure_reasons(follow_packet)[:8]:
        moments.append(
            {
                "kind": "follow_next_read_failure",
                "source": "follow_next_reads.failed_reads",
                "failure_reason": reason,
            }
        )
    return _sorted_rows(moments)[:INTERESTING_MOMENT_LIMIT]


def _build_committed_safe_narrative_ledger(
    semantic_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    next_reads: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(_list(semantic_packet.get("strongest_candidates")), 1):
        if not isinstance(candidate, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "ledger_kind": "candidate_found",
                    "source": "semantic_debug.strongest_candidates",
                    "source_index": index,
                    "timestamp_utc": candidate.get("timestamp_utc") or candidate.get("timestamp"),
                    "role": candidate.get("role"),
                    "candidate_id": candidate.get("candidate_id"),
                    "rank": candidate.get("rank"),
                    "confidence": candidate.get("confidence"),
                    "reason_codes": [str(item) for item in _list(candidate.get("reason_codes"))[:8]],
                    "source_handle": _source_handle(candidate.get("source_ref")),
                    "inference_boundary": "candidate metadata identifies possible narrative evidence; it is not a root-cause claim",
                }
            )
        )
    for index, read in enumerate(_list(follow_packet.get("expanded_reads")), 1):
        if not isinstance(read, dict):
            continue
        recovery = _safe_recovery_metadata(read.get("recovery"))
        status = str(read.get("status") or "unknown")
        rows.append(
            _clean_row(
                {
                    "ledger_kind": "recovery_opened" if status == "expanded" else "recovery_gap",
                    "source": "follow_next_reads.expanded_reads",
                    "source_index": index,
                    "rank": read.get("rank"),
                    "status": status,
                    "candidate_id": read.get("candidate_id") or recovery.get("candidate_id"),
                    "recovery_kind": recovery.get("kind"),
                    "recovery_id": recovery.get("recovery_id"),
                    "reason_codes": [recovery["omission_reason"]] if recovery.get("omission_reason") else [],
                    "target_handle": _target_handle(read.get("target")),
                    "source_handle": _source_handle(read.get("source_ref")),
                    "selected_record_count": len(_list(read.get("selected_records"))),
                    "counts": _safe_counts(read.get("counts")),
                    "line_range": _safe_line_range(read.get("line_range")),
                    "truncated": bool(read.get("truncated")),
                    "cap_flags": _safe_cap_flags(read),
                    "inference_boundary": "opened recovery metadata proves follow-through shape only; raw transcript content remains private",
                }
            )
        )
    for index, read in enumerate(_list(follow_packet.get("failed_reads")), 1):
        if not isinstance(read, dict):
            continue
        recovery = _safe_recovery_metadata(read.get("recovery"))
        rows.append(
            _clean_row(
                {
                    "ledger_kind": "recovery_gap",
                    "source": "follow_next_reads.failed_reads",
                    "source_index": index,
                    "rank": read.get("rank"),
                    "status": read.get("status"),
                    "candidate_id": read.get("candidate_id") or recovery.get("candidate_id"),
                    "recovery_kind": recovery.get("kind"),
                    "recovery_id": recovery.get("recovery_id"),
                    "reason_codes": [str(read.get("failure_reason") or read.get("status") or "failed")],
                    "target_handle": _target_handle(read.get("target")),
                    "source_handle": _source_handle(read.get("source_ref")),
                    "inference_boundary": "failed follow reads are explicit reconstruction gaps, not evidence absence claims",
                }
            )
        )
    if not rows and next_reads.get("status") in {"followup_gap", "explicit_no_next_reads"}:
        rows.append(
            {
                "ledger_kind": "recovery_gap",
                "source": "failure_analysis.next_reads",
                "status": next_reads.get("status"),
                "reason_codes": ["no_safe_recovery_metadata_available"],
                "next_read_count": next_reads.get("count", 0),
                "inference_boundary": "old or sparse saved reports did not expose follow-next-reads metadata",
            }
        )
    return _sorted_rows(rows)[:NARRATIVE_LEDGER_LIMIT]


def _build_narrative_readiness(
    semantic_loaded: dict[str, Any],
    follow_loaded: dict[str, Any],
    semantic_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    next_reads: dict[str, Any],
    ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_count = len(_list(semantic_packet.get("strongest_candidates")))
    if candidate_count == 0:
        candidate_count = sum(1 for row in _list(semantic_packet.get("timeline")) if isinstance(row, dict))
    opened_count = sum(1 for row in ledger if row.get("ledger_kind") == "recovery_opened")
    usable_recovery_count = _int(follow_packet.get("usable_recovery_evidence_count"))
    recovery_gap_count = sum(1 for row in ledger if row.get("ledger_kind") == "recovery_gap")
    dated_candidate_count = sum(
        1 for row in ledger if row.get("ledger_kind") == "candidate_found" and row.get("timestamp_utc")
    )
    recovery_with_line_range_count = sum(
        1 for row in ledger if row.get("ledger_kind") == "recovery_opened" and row.get("line_range")
    )
    recovery_with_counts_count = sum(
        1 for row in ledger if row.get("ledger_kind") == "recovery_opened" and row.get("selected_record_count")
    )
    has_safe_candidate = any(
        row.get("ledger_kind") == "candidate_found" and row.get("candidate_id") and row.get("source_handle")
        for row in ledger
    )
    has_safe_recovery = any(
        row.get("ledger_kind") == "recovery_opened"
        and (row.get("target_handle") or row.get("source_handle"))
        and (row.get("line_range") or row.get("selected_record_count"))
        for row in ledger
    )
    narrative_ready = bool(
        semantic_loaded.get("loaded")
        and candidate_count
        and opened_count
        and usable_recovery_count
        and dated_candidate_count
        and recovery_with_line_range_count
        and recovery_with_counts_count
        and has_safe_candidate
        and has_safe_recovery
    )
    if narrative_ready:
        status = "narrative_ready"
    elif opened_count and usable_recovery_count:
        status = "needs_private_review"
    elif opened_count:
        status = "recovery_opened"
    elif candidate_count:
        status = "candidate_found"
    else:
        status = "no_recovery_available"
    reason_codes: list[str] = []
    if not semantic_loaded.get("loaded"):
        reason_codes.append("semantic_debug_not_loaded")
    if not candidate_count:
        reason_codes.append("no_candidate_metadata")
    if not follow_loaded.get("loaded"):
        reason_codes.append("follow_next_reads_not_loaded")
    if candidate_count and not opened_count:
        reason_codes.append("recovery_not_opened")
    if opened_count and not usable_recovery_count:
        reason_codes.append("opened_recovery_not_marked_usable")
    if opened_count and usable_recovery_count and not narrative_ready:
        reason_codes.append("safe_metadata_too_thin_for_committed_narrative")
    if next_reads.get("status") in {"followup_gap", "explicit_no_next_reads"} and not opened_count:
        reason_codes.append(str(next_reads.get("status")))
    return {
        "status": status,
        "candidate_count": candidate_count,
        "recovery_opened_count": opened_count,
        "usable_recovery_evidence_count": usable_recovery_count,
        "recovery_gap_count": recovery_gap_count,
        "safe_dated_candidate_count": dated_candidate_count,
        "safe_recovery_line_range_count": recovery_with_line_range_count,
        "ledger_row_count": len(ledger),
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "recovery_opened": bool(opened_count),
        "follow_next_reads_loaded": bool(follow_loaded.get("loaded")),
        "inference_boundary": "readiness classifies committed-safe reconstruction metadata; it does not claim root cause, all-history completeness, final-answer quality, speed, token savings, or product value",
    }


def _build_repo_handle_chronology(
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    next_reads: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    *,
    transcript_date_range: dict[str, Any],
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_repo_handle_candidates_from_semantic_debug(semantic_packet))
    candidates.extend(_repo_handle_candidates_from_forensics(forensics_packet))
    candidates.extend(_repo_handle_candidates_from_follow_next_reads(follow_packet))
    candidates.extend(_repo_handle_candidates_from_failure_analysis(next_reads, committed_safe_narrative_ledger))

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        for handle_type, handle in _safe_repo_handles(candidate.get("values")):
            date_value, date_source = _safe_repo_date(candidate.get("row"))
            source = str(candidate.get("source") or "unknown")
            source_index = _int(candidate.get("source_index"))
            key = (handle_type, handle, str(date_value or ""), source)
            if key in seen:
                continue
            seen.add(key)
            row = _clean_row(
                {
                    "phase_id": f"repo-phase-{len(rows) + 1:03d}",
                    "source": source,
                    "source_index": source_index,
                    "handle_type": handle_type,
                    "handle": handle,
                    "date": date_value,
                    "date_source": date_source,
                    "status": _safe_repo_status(candidate.get("row")),
                    "confidence": _safe_repo_confidence(candidate.get("row")),
                    "inference_boundary": _repo_inference_boundary(
                        handle_type=handle_type,
                        has_date=bool(date_value),
                        source=source,
                    ),
                }
            )
            rows.append(row)
    rows = _sorted_repo_rows(rows)[:REPO_CHRONOLOGY_LIMIT]
    handle_type_counts = _handle_type_counts(rows)
    dated_count = sum(1 for row in rows if row.get("date"))
    date_range = _repo_date_range(rows)
    gaps = _repo_chronology_gaps(
        row_count=len(rows),
        dated_count=dated_count,
        candidates_considered=len(candidates),
        handle_type_counts=handle_type_counts,
    )
    if rows and dated_count:
        status = "available"
    elif rows:
        status = "partial_no_dates"
    else:
        status = "gap"
    return {
        "status": status,
        "phase_row_count": len(rows),
        "handle_type_counts": handle_type_counts,
        "date_range": date_range,
        "transcript_date_range": {
            "start": transcript_date_range.get("start"),
            "end": transcript_date_range.get("end"),
            "timestamp_count": transcript_date_range.get("timestamp_count", 0),
        },
        "date_range_boundary": "repo-handle dates are derived only from committed-safe saved-report metadata and are separate from transcript recovery chronology",
        "gaps": gaps,
        "inference_boundaries": [
            "task/review/doc/report paths are preserved only when they can be reduced to safe repo-relative handles",
            "absolute or private paths are omitted unless a safe repo-relative suffix can be extracted",
            "commit hashes and git_last_change dates are saved-report metadata, not proof of full development history",
            "missing dates or joins are reported as chronology gaps, not inferred from raw transcripts",
        ],
        "rows": rows,
    }


def _repo_handle_candidates_from_semantic_debug(packet: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    joins = _nested(packet, "task_review_commit_joins")
    if joins:
        candidates.append({"source": "semantic_debug.task_review_commit_joins", "source_index": 0, "row": joins, "values": joins})
        for section in ("review_evidence", "artifact_checks"):
            for index, row in enumerate(_list(joins.get(section)), 1):
                if isinstance(row, dict):
                    candidates.append(
                        {
                            "source": f"semantic_debug.task_review_commit_joins.{section}",
                            "source_index": index,
                            "row": row,
                            "values": _repo_candidate_values(row),
                        }
                    )
        for key in ("git", "commit", "path_last_change"):
            child = joins.get(key)
            if isinstance(child, dict):
                candidates.append(
                    {
                        "source": f"semantic_debug.task_review_commit_joins.{key}",
                        "source_index": 1,
                        "row": child,
                        "values": _repo_candidate_values(child),
                    }
                )
    for section in ("next_reads",):
        for index, row in enumerate(_list(packet.get(section)), 1):
            if isinstance(row, dict):
                candidates.append(
                    {
                        "source": f"semantic_debug.{section}",
                        "source_index": index,
                        "row": row,
                        "values": [row.get("target"), row.get("source_ref")],
                    }
                )
    return candidates


def _repo_handle_candidates_from_forensics(packet: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for section in ("phase_timeline", "claim_action_review_joins", "review_evidence", "artifact_checks"):
        for index, row in enumerate(_list(packet.get(section)), 1):
            if not isinstance(row, dict):
                continue
            values: list[Any] = _repo_candidate_values(row)
            if section == "claim_action_review_joins":
                values.extend(row.get("source_refs") for _ in [None])
                values.append(row.get("join_basis"))
            candidates.append(
                {
                    "source": f"forensics.{section}",
                    "source_index": index,
                    "row": row,
                    "values": values,
                }
            )
    for key in ("git", "review_gate"):
        child = packet.get(key)
        if isinstance(child, dict):
            candidates.append(
                {
                    "source": f"forensics.{key}",
                    "source_index": 1,
                    "row": child,
                    "values": _repo_candidate_values(child),
                }
            )
    return candidates


def _repo_handle_candidates_from_follow_next_reads(packet: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for section in ("expanded_reads", "failed_reads"):
        for index, row in enumerate(_list(packet.get(section)), 1):
            if isinstance(row, dict):
                candidates.append(
                    {
                        "source": f"follow_next_reads.{section}",
                        "source_index": index,
                        "row": row,
                        "values": [row.get("target"), row.get("source_ref"), row.get("recovery")],
                    }
                )
    return candidates


def _repo_handle_candidates_from_failure_analysis(
    next_reads: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, row in enumerate(_list(next_reads.get("reads")), 1):
        if isinstance(row, dict):
            candidates.append(
                {
                    "source": "failure_analysis.next_reads",
                    "source_index": index,
                    "row": row,
                    "values": [row.get("target_handle"), row.get("source_handle")],
                }
            )
    for index, row in enumerate(committed_safe_narrative_ledger, 1):
        if isinstance(row, dict):
            candidates.append(
                {
                    "source": "failure_analysis.committed_safe_narrative_ledger",
                    "source_index": index,
                    "row": row,
                    "values": [row.get("target_handle"), row.get("source_handle"), row.get("candidate_id")],
                }
            )
    return candidates


def _repo_candidate_values(row: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key in (
        "path",
        "target",
        "task",
        "task_id",
        "commit",
        "merge_commit",
        "source_ref",
        "source_refs",
        "gate_source_refs",
        "blockers",
        "git_last_change",
        "review_verdict",
        "claim_action",
        "changed_files",
        "path_last_change",
    ):
        if key in row:
            values.append(row.get(key))
    return values


def _safe_repo_handles(value: Any) -> list[tuple[str, str]]:
    handles: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(handle_type: str, handle: str) -> None:
        item = (handle_type, handle)
        if item in seen:
            return
        seen.add(item)
        handles.append(item)

    def visit(item: Any, *, key_hint: str = "") -> None:
        if item is None:
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if key in {
                    "body",
                    "message",
                    "note",
                    "notes",
                    "reason",
                    "snippet",
                    "subject",
                    "text",
                    "warning",
                    "records",
                    "selected_records",
                    "command",
                    "command_payload",
                    "vectors",
                    "indexes",
                    "source_context",
                    "source_payload",
                    "payload",
                    "metadata",
                    "cwd",
                    "derived_roots",
                }:
                    continue
                visit(child, key_hint=str(key))
            return
        if isinstance(item, list):
            for child in item:
                visit(child, key_hint=key_hint)
            return
        if not isinstance(item, (str, int)):
            return
        text = str(item)
        for path_type, path in _safe_repo_paths(text):
            add(path_type, path)
            for task_id in _task_ids(path):
                add("task_id", task_id)
        if "commit" in key_hint.lower() or "hash" in key_hint.lower():
            for commit in _commit_hashes(text):
                add("commit_hash", commit)
        for task_id in _task_ids(text):
            add("task_id", task_id)

    visit(value)
    return handles


def _safe_repo_paths(text: str) -> list[tuple[str, str]]:
    normalized = text.replace("\\", "/").strip().strip("`'\"")
    if not normalized:
        return []
    if ":" in normalized:
        head, tail = normalized.rsplit(":", 1)
        if tail.isdigit():
            normalized = head
    normalized = normalized.split("#", 1)[0].split("?", 1)[0]
    paths: list[tuple[str, str]] = []
    for prefix, handle_type in SAFE_REPO_PATH_PREFIXES:
        start = _safe_path_prefix_index(normalized, prefix)
        if start < 0:
            continue
        candidate = normalized[start:]
        terminators = [len(candidate)]
        for marker in (" ", "\n", "\t", ")", "]", "}", ",", ";"):
            position = candidate.find(marker)
            if position >= 0:
                terminators.append(position)
        candidate = candidate[: min(terminators)].strip().strip("`'\"")
        candidate = candidate.rstrip(".")
        if _safe_repo_path(candidate, prefix):
            paths.append((handle_type, candidate))
    return list(dict.fromkeys(paths))


def _safe_path_prefix_index(value: str, prefix: str) -> int:
    if value.startswith(prefix):
        return 0
    marker = f"/{prefix}"
    index = value.find(marker)
    if index >= 0:
        return index + 1
    return -1


def _safe_repo_path(value: str, prefix: str) -> bool:
    if not value.startswith(prefix):
        return False
    if value.startswith("/") or ".." in value.split("/"):
        return False
    if len(value) > 240:
        return False
    suffix = value.rsplit("/", 1)[-1]
    return bool(suffix and re.search(r"\.(?:md|json|html|csv|txt)$", suffix, re.IGNORECASE))


def _task_ids(text: str) -> list[str]:
    return [match.group(0).lower() for match in TASK_ID_RE.finditer(text)]


def _commit_hashes(text: str) -> list[str]:
    return [match.group(0).lower() for match in COMMIT_RE.finditer(text)]


def _safe_repo_date(row: Any) -> tuple[str | None, str | None]:
    if not isinstance(row, dict):
        return None, None
    nested_keys = (
        "git_last_change",
        "path_last_change",
        "commit",
        "merge_commit",
    )
    direct_keys = (
        "commit_time",
        "author_time",
        "last_changed_at",
        "date",
        "timestamp_utc",
        "timestamp",
    )
    for parent_key in nested_keys:
        child = row.get(parent_key)
        if isinstance(child, dict):
            for key in direct_keys:
                value = child.get(key)
                if _looks_like_safe_date(value):
                    return str(value), f"{parent_key}.{key}"
    for key in direct_keys:
        value = row.get(key)
        if _looks_like_safe_date(value):
            return str(value), key
    return None, None


def _looks_like_safe_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+-]+(?:Z)?)?$", value))


def _safe_repo_status(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in ("status", "phase_kind", "evidence_kind", "relation", "support_status", "support", "claim_action"):
        value = row.get(key)
        if isinstance(value, str) and _safe_short_label(value):
            return value
        if isinstance(value, dict):
            action = value.get("action") or value.get("status") or value.get("label")
            if isinstance(action, str) and _safe_short_label(action):
                return action
    review = row.get("review_verdict")
    if isinstance(review, dict):
        action = review.get("action") or review.get("verdict")
        if isinstance(action, str) and _safe_short_label(action):
            return action
    return None


def _safe_repo_confidence(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    value = row.get("confidence") or row.get("support_level") or row.get("support_basis_kind")
    if isinstance(value, str) and _safe_short_label(value):
        return value
    return None


def _safe_short_label(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_./:-]{1,80}$", value))


def _repo_inference_boundary(*, handle_type: str, has_date: bool, source: str) -> str:
    if not has_date:
        return "safe handle was present in saved-report metadata, but no safe repo date was supplied"
    if handle_type == "commit_hash":
        return "commit hash and date are saved-report metadata; no repository history scan was run"
    if source.startswith("failure_analysis."):
        return "handle was derived from already-sanitized failure-analysis rows, not raw source payloads"
    return "safe repo handle was joined from supplied saved-report metadata only"


def _sorted_repo_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("date") or "9999-12-31T23:59:59+00:00"),
            str(row.get("handle_type") or ""),
            str(row.get("handle") or ""),
            str(row.get("source") or ""),
            int(row.get("source_index") or 0),
        ),
    )


def _handle_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        handle_type = str(row.get("handle_type") or "unknown")
        counts[handle_type] = counts.get(handle_type, 0) + 1
    return dict(sorted(counts.items()))


def _repo_date_range(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted({str(row.get("date")) for row in rows if row.get("date")})
    return {
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "timestamp_count": len(dates),
        "boundary": "derived from saved report repo-handle dates only; separate from transcript date_range",
    }


def _transcript_recovery_date_range(semantic_packet: dict[str, Any], follow_packet: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for section in ("timeline", "strongest_candidates"):
        for row in _list(semantic_packet.get(section)):
            if isinstance(row, dict):
                rows.append({"timestamp_utc": row.get("timestamp_utc") or row.get("timestamp")})
    for row in _list(_nested(semantic_packet, "tool_action_proof").get("events")):
        if isinstance(row, dict):
            rows.append({"timestamp_utc": row.get("timestamp_utc") or row.get("timestamp")})
    for section in ("expanded_reads", "failed_reads"):
        for row in _list(follow_packet.get(section)):
            if isinstance(row, dict):
                rows.append({"timestamp_utc": row.get("timestamp_utc") or row.get("timestamp")})
    date_range = _date_range(rows)
    date_range["boundary"] = "derived from semantic-debug and follow-next-reads transcript/recovery timestamps only; excludes repo-handle forensics dates"
    return date_range


def _repo_chronology_gaps(
    *,
    row_count: int,
    dated_count: int,
    candidates_considered: int,
    handle_type_counts: dict[str, int],
) -> list[str]:
    gaps: list[str] = []
    if not row_count:
        if candidates_considered:
            gaps.append("saved_report_metadata_contained_no_committed_safe_repo_handles")
        else:
            gaps.append("no_repo_handle_metadata_sections_present")
    if row_count and dated_count < row_count:
        gaps.append("some_repo_handles_missing_safe_dates")
    for required in ("task_id", "task_path", "review_path", "doc_path", "commit_hash"):
        if not handle_type_counts.get(required):
            gaps.append(f"{required}_not_present")
    return gaps


def _build_next_reads(semantic_packet: dict[str, Any], follow_packet: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for index, read in enumerate(_list(semantic_packet.get("next_reads")), 1):
        if not isinstance(read, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "rank": index,
                    "source": "semantic_debug.next_reads",
                    "kind": read.get("kind"),
                    "reason": read.get("reason"),
                    "target_handle": _target_handle(read.get("target")),
                    "source_handle": _source_handle(read.get("source_ref")),
                    "expansion_action_count": len(_list(read.get("expansion_actions"))),
                    "context_action_count": len(_list(read.get("context_actions"))),
                    "omitted_window_recovery": _recovery_summary(read.get("omitted_window_recovery")),
                }
            )
        )
    for index, read in enumerate(_list(follow_packet.get("failed_reads")), 1):
        if not isinstance(read, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "rank": len(rows) + index,
                    "source": "follow_next_reads.failed_reads",
                    "kind": "failed_follow_next_read",
                    "status": read.get("status"),
                    "failure_reason": read.get("failure_reason") or read.get("status"),
                    "target_handle": _target_handle(read.get("target")),
                    "source_handle": _source_handle(read.get("source_ref")),
                    "candidate_id": read.get("candidate_id"),
                    "recovery": _recovery_summary(read.get("recovery")),
                }
            )
        )
    rows = rows[:NEXT_READ_LIMIT]
    if rows:
        status = "available"
    elif follow_packet and str(follow_packet.get("status") or "") == "no_next_reads":
        status = "explicit_no_next_reads"
    else:
        status = "followup_gap"
    return {
        "status": status,
        "count": len(rows),
        "reads": rows,
        "explicit_gap": None
        if rows
        else "No next-read handles were present in the supplied saved reports; rerun semantic-debug or follow-next-reads if source context is needed.",
    }


def _tool_critique(
    *,
    semantic_loaded: dict[str, Any],
    forensics_loaded: dict[str, Any],
    follow_loaded: dict[str, Any],
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    play_by_play: list[dict[str, Any]],
    next_reads: dict[str, Any],
) -> dict[str, Any]:
    audit_plan = _nested(semantic_packet, "audit_plan")
    no_evidence = _nested(semantic_packet, "no_evidence")
    diagnostics = _nested(forensics_packet, "diagnostics")
    critique = {
        "mode": "deterministic_saved_report_packaging",
        "report_inputs": {
            "semantic_debug": _input_status(semantic_loaded),
            "forensics": _input_status(forensics_loaded),
            "follow_next_reads": _input_status(follow_loaded),
        },
        "what_the_tool_could_package": {
            "play_by_play_rows": len(play_by_play),
            "next_read_rows": next_reads.get("count", 0),
            "forensics_phase_rows": len(_list(forensics_packet.get("phase_timeline"))),
            "follow_next_reads_usable_recovery_evidence_count": _int(
                follow_packet.get("usable_recovery_evidence_count")
            ),
        },
        "gaps_and_limitations": [],
        "not_changed_or_claimed": NON_GOAL_BOUNDARIES,
    }
    gaps = critique["gaps_and_limitations"]
    if not semantic_loaded.get("loaded"):
        gaps.append("semantic-debug report was required but not loaded")
    if not forensics_loaded.get("supplied"):
        gaps.append("forensics report was not supplied")
    if not follow_loaded.get("supplied"):
        gaps.append("follow-next-reads report was not supplied")
    if next_reads.get("status") == "followup_gap":
        gaps.append("no next-read handles were available in supplied reports")
    if audit_plan.get("cap_pressure_notes"):
        gaps.append("semantic-debug reported cap or omitted-context pressure")
    if no_evidence.get("answer_scope"):
        gaps.append(f"semantic-debug no-evidence scope: {no_evidence.get('answer_scope')}")
    for item in _list(diagnostics.get("warnings"))[:4]:
        gaps.append(str(item))
    if not gaps:
        gaps.append("no deterministic packaging gaps were exposed by supplied reports")
    return critique


def _evidence_vs_inference(
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    play_by_play: list[dict[str, Any]],
    next_reads: dict[str, Any],
    failure_mode_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness = _nested(semantic_packet, "answer_readiness")
    support = _nested(forensics_packet, "support_summary") or _nested(_nested(forensics_packet, "summary"), "support_summary")
    return {
        "evidence_backed": [
            {
                "claim": "semantic-debug report status and counts were loaded",
                "surface": "semantic_debug",
                "status": semantic_packet.get("status"),
                "candidate_count": len(_list(semantic_packet.get("strongest_candidates"))),
                "timeline_count": len(_list(semantic_packet.get("timeline"))),
            },
            {
                "claim": "play-by-play rows preserve available timestamps and sanitized source handles",
                "surface": "failure_analysis.play_by_play",
                "row_count": len(play_by_play),
                "dated_row_count": sum(1 for row in play_by_play if row.get("timestamp_utc")),
            },
            {
                "claim": "answer-readiness routing came from semantic-debug when present",
                "surface": "semantic_debug.answer_readiness",
                "status": readiness.get("status"),
                "decision": readiness.get("decision"),
            },
            {
                "claim": "forensics support counts came from supplied forensics report when present",
                "surface": "forensics.support_summary",
                "support_summary": _safe_counts(support),
            },
            {
                "claim": "follow-next-reads counts came from supplied follow-through report when present",
                "surface": "follow_next_reads",
                "selected_count": _int(follow_packet.get("selected_count")),
                "expanded_count": _int(follow_packet.get("expanded_count")),
                "failed_count": _int(follow_packet.get("failed_count")),
                "usable_recovery_evidence_count": _int(follow_packet.get("usable_recovery_evidence_count")),
            },
        ],
        "inference": [
            {
                "claim": "failure-mode ledger labels are deterministic classifications of saved report statuses and counts",
                "surface": "failure_analysis.failure_mode_ledger",
                "ledger_count": len(failure_mode_ledger),
                "boundary": "labels are reconstruction aids, not root cause",
            },
            {
                "claim": "next reads indicate useful follow-up context when present",
                "surface": "failure_analysis.next_reads",
                "status": next_reads.get("status"),
                "boundary": "next reads do not imply all missing evidence is recoverable",
            },
        ],
        "not_inferred": [
            "root cause",
            "all-history completeness",
            "final answer quality",
            "speed or token savings",
            "product value",
        ],
    }


def _claim_boundaries(
    semantic_loaded: dict[str, Any],
    forensics_loaded: dict[str, Any],
    follow_loaded: dict[str, Any],
) -> dict[str, Any]:
    return {
        **UNSUPPORTED_CLAIMS,
        **NON_GOAL_BOUNDARIES,
        "required_semantic_debug_report_loaded": bool(semantic_loaded.get("loaded")),
        "optional_forensics_report_loaded": bool(forensics_loaded.get("loaded")),
        "optional_follow_next_reads_report_loaded": bool(follow_loaded.get("loaded")),
        "saved_report_synthesis_only": True,
        "missing_or_capped_evidence_reported_as_scope_limitation": True,
    }


def _date_range(*row_groups: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = sorted(
        {
            str(row.get("timestamp_utc"))
            for rows in row_groups
            for row in rows
            if isinstance(row, dict) and row.get("timestamp_utc")
        }
    )
    return {
        "start": timestamps[0] if timestamps else None,
        "end": timestamps[-1] if timestamps else None,
        "timestamp_count": len(timestamps),
        "boundary": "derived from saved report timestamps only; no new source scan was run",
    }


def _source_handle(source_ref: Any) -> dict[str, Any] | None:
    if not isinstance(source_ref, dict):
        return None
    path = source_ref.get("path")
    session = source_ref.get("session")
    handle: dict[str, Any] = {"kind": "source_ref"}
    if path:
        handle["path_handle"] = _stable_handle(str(path), "path")
    if session:
        handle["session_handle"] = _stable_handle(str(session), "session")
    for key in ("line", "timestamp", "timestamp_utc", "role", "source_kind"):
        if source_ref.get(key) is not None:
            handle[key] = source_ref.get(key)
    return handle if len(handle) > 1 else None


def _target_handle(target: Any) -> dict[str, Any] | None:
    if not target:
        return None
    target_text = str(target)
    line = None
    if ":" in target_text:
        tail = target_text.rsplit(":", 1)[1]
        if tail.isdigit():
            line = int(tail)
    handle = {"kind": "target", "target_handle": _stable_handle(target_text, "target")}
    if line is not None:
        handle["line"] = line
    return handle


def _stable_handle(value: str, prefix: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _recovery_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {}
    for key in ("kind", "recovery_id", "reason", "status"):
        if value.get(key) is not None:
            allowed[key] = value.get(key)
    return allowed or None


def _safe_recovery_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {}
    for key in ("kind", "recovery_id", "candidate_id", "omission_reason"):
        if value.get(key) is not None:
            allowed[key] = str(value.get(key))
    return allowed


def _safe_line_range(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed: dict[str, Any] = {}
    for key in ("start", "end", "anchor"):
        if value.get(key) is not None:
            allowed[key] = _int(value.get(key))
    return allowed


def _safe_cap_flags(read: dict[str, Any]) -> dict[str, Any]:
    flags: dict[str, Any] = {}
    claim_boundaries = read.get("claim_boundaries") if isinstance(read.get("claim_boundaries"), dict) else {}
    for key in ("ranking_or_cap_changed", "broad_search_rerun", "answer_generation_included"):
        if claim_boundaries.get(key) is not None:
            flags[key] = bool(claim_boundaries.get(key))
    if read.get("truncated") is not None:
        flags["truncated"] = bool(read.get("truncated"))
    return flags


def _failure_reasons(packet: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if packet.get("failure_reason"):
        reasons.append(str(packet.get("failure_reason")))
    for item in _list(packet.get("failed_reads")):
        if isinstance(item, dict) and (item.get("failure_reason") or item.get("status")):
            reasons.append(str(item.get("failure_reason") or item.get("status")))
    return list(dict.fromkeys(reasons))


def _safe_basis(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    safe_keys = (
        "present_ledger_count",
        "recovery_ledger_count",
        "structured_tool_event_count",
        "task_review_join_status",
        "next_read_count",
        "omitted_window_recovery_count",
    )
    return {key: value.get(key) for key in safe_keys if key in value}


def _safe_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _int(item)
        for key, item in value.items()
        if str(key).endswith("_count") or str(key) in {"phase_count", "join_count"}
    }


def _input_status(loaded: dict[str, Any]) -> dict[str, Any]:
    status = {
        "supplied": loaded.get("supplied"),
        "loaded": loaded.get("loaded"),
        "status": loaded.get("status"),
        "schema_version": loaded.get("schema_version"),
    }
    if loaded.get("invalid_reason"):
        status["invalid_reason"] = loaded["invalid_reason"]
    return status


def _input_reports_may_contain_raw_private_material(*packets: dict[str, Any]) -> bool:
    raw_keys = {
        "raw_text_included",
        "raw_paths_included",
        "raw_private_text_included",
        "raw_private_paths_included",
        "snippets_included",
        "source_lines_included",
    }
    for packet in packets:
        for key, value in _walk_items(packet):
            if key in raw_keys and value is True:
                return True
        if packet.get("strongest_candidates") or packet.get("timeline") or packet.get("expanded_reads"):
            return True
    return False


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    blocked = {
        "snippet",
        "text",
        "path",
        "target",
        "session",
        "cwd",
        "command",
        "output_paths",
        "targets",
        "selected_records",
        "records",
    }
    return {key: value for key, value in row.items() if key not in blocked and value not in (None, [], {})}


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("timestamp_utc") or ""),
            str(row.get("source") or ""),
            int(row.get("source_index") or row.get("rank") or 0),
            str(row.get("candidate_id") or ""),
        ),
    )


def _nested(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key) if isinstance(value, dict) else None
    return child if isinstance(child, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _walk_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_items(child)
