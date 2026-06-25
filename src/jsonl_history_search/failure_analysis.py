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
STORY_PHASE_LIMIT = 30
DEVLOG_TAKE_LIMIT = 6
DEVLOG_SUMMARY_LIMIT = 8
DEVLOG_ARC_ROW_LIMIT = 20
DEVLOG_CLAIM_LEDGER_LIMIT = 40
DEVLOG_BEHAVIOR_LABEL_LIMIT = 12

TASK_ID_RE = re.compile(r"\b(?:jsh|sl)-\d+[a-z]?\b", re.IGNORECASE)
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
SAFE_REPO_PATH_PREFIXES = (
    ("codex/state/reviews/", "review_path"),
    ("reviews/", "review_path"),
    ("tasks/", "task_path"),
    ("docs/", "doc_path"),
    ("reports/", "report_path"),
)

BEHAVIOR_QUALITY_LABEL_SPECS = {
    "false_positive_model_input_stripping": {
        "patterns": (
            "active-model-input-false-positive-stripping",
            "prompt-assembly-sanitizer",
            "history-search-copied-context-masking-gate",
            "model-input-false-positive",
            "copied-context-masking",
        ),
        "story_useful_signal": "False-positive cleanup evidence is tied to model-input, prompt-assembly, or copied-context stripping handles rather than vague cleanup language.",
        "boundary": "does not prove live TUI cleanup, model forgetting, root cause, or final prose quality",
    },
    "runtime_suppression_zero_stdout": {
        "patterns": (
            "runtime-ack-to-pending-suppression-hardening",
            "deferred-sessionstart-pending-suppression",
            "pending-suppression",
            "zero-stdout",
        ),
        "story_useful_signal": "Runtime suppression evidence points at pending/deferred delivery paths that avoid model-visible stdout while retaining audit evidence.",
        "boundary": "does not prove global promotion, token savings, speed, or all-history completeness",
    },
    "false_positive_boundary_control": {
        "patterns": (
            "active-model-input-false-positive-stripping",
            "normal-tui-row-cleanup-runtime-path",
            "fork-api-feasibility-spike",
            "terminal-control-negative-proof",
            "integrated-proof-and-promotion-gate",
            "false-positive-boundary",
        ),
        "story_useful_signal": "Boundary-control evidence separates supported false-positive cleanup from unsupported model forgetting, transcript rewrite, or sanitized-output substitutes.",
        "boundary": "does not claim root cause, model forgetting, or canonical transcript cleanup",
    },
    "runtime_quality_tui_path": {
        "patterns": (
            "normal-tui-row-cleanup-runtime-path",
            "fork-api-feasibility-spike",
            "display-only-collapse-prototype",
            "tui-path",
            "tui-row-cleanup",
        ),
        "story_useful_signal": "Runtime-quality evidence preserves a concrete normal TUI cleanup path as safe repo metadata.",
        "boundary": "does not prove live TUI cleanup happened in this failure-analysis run",
    },
    "negative_proof_against_fake_cleanup": {
        "patterns": (
            "terminal-control-negative-proof",
            "negative-proof",
            "fake-cleanup",
        ),
        "story_useful_signal": "Negative proof evidence records rejected cleanup substitutes as story constraints.",
        "boundary": "negative proof is not a root-cause finding or a live runtime cleanup claim",
    },
    "proof_and_promotion_gate": {
        "patterns": (
            "integrated-proof-and-promotion-gate",
            "proof-and-promotion-gate",
            "no_promotion",
            "no-promotion",
        ),
        "story_useful_signal": "Proof/promotion evidence ties supported and unsupported claims to review-gate handles and keeps promotion explicit.",
        "boundary": "does not claim global promotion, live model send, or production default change",
    },
}

SEMANTIC_DEBUG_CATEGORY_SPECS = {
    "evidence_boundary_control": {
        "name": "Evidence boundary control",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies whether evidence boundaries are controlled without treating case-specific cleanup handles as product taxonomy",
    },
    "runtime_delivery_boundary": {
        "name": "Runtime delivery boundary",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies runtime delivery or suppression evidence without proving live delivery, promotion, speed, or token savings",
    },
    "unsupported_claim_boundary": {
        "name": "Unsupported claim boundary",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies explicit unsupported-claim boundaries without converting them into root-cause or completeness findings",
    },
    "implementation_path_evidence": {
        "name": "Implementation path evidence",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies safe implementation-path handles as evidence leads without proving the path executed in this analysis run",
    },
    "negative_proof_boundary": {
        "name": "Negative proof boundary",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies rejected substitutes and negative proof as constraints, not as root-cause or live-runtime claims",
    },
    "promotion_gate_boundary": {
        "name": "Promotion gate boundary",
        "scope": "reusable_semantic_debugging_category",
        "source_boundary": "classifies proof and promotion-gate evidence without claiming global promotion or production default changes",
    },
    "unknown_semantic_debugging_evidence": {
        "name": "Unknown semantic-debug evidence",
        "scope": "unclassified_semantic_debugging_evidence",
        "source_boundary": "safe custom follow-through evidence is surfaced for local review without coercion into the case-specific label set",
    },
}

BEHAVIOR_LABEL_CATEGORY_IDS = {
    "false_positive_model_input_stripping": "evidence_boundary_control",
    "runtime_suppression_zero_stdout": "runtime_delivery_boundary",
    "false_positive_boundary_control": "unsupported_claim_boundary",
    "runtime_quality_tui_path": "implementation_path_evidence",
    "negative_proof_against_fake_cleanup": "negative_proof_boundary",
    "proof_and_promotion_gate": "promotion_gate_boundary",
}

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
    follow_through_behavior_labels_report: Path | None = None,
    repo_artifact_evidence_report: Path | None = None,
) -> dict[str, Any]:
    semantic_loaded = _load_report_packet(semantic_debug_report, "semantic_debug")
    forensics_loaded = _load_report_packet(forensics_report, "forensics") if forensics_report else _not_supplied("forensics")
    follow_loaded = (
        _load_report_packet(follow_next_reads_report, "follow_next_reads")
        if follow_next_reads_report
        else _not_supplied("follow_next_reads")
    )
    followthrough_labels_loaded = (
        _load_report_packet(follow_through_behavior_labels_report, "follow_through_behavior_labels")
        if follow_through_behavior_labels_report
        else _not_supplied("follow_through_behavior_labels")
    )
    repo_artifact_evidence_loaded = (
        _load_report_packet(repo_artifact_evidence_report, "repo_artifact_evidence")
        if repo_artifact_evidence_report
        else _not_supplied("repo_artifact_evidence")
    )

    semantic_packet = semantic_loaded.get("packet") if semantic_loaded.get("loaded") else {}
    forensics_packet = forensics_loaded.get("packet") if forensics_loaded.get("loaded") else {}
    follow_packet = follow_loaded.get("packet") if follow_loaded.get("loaded") else {}
    followthrough_labels_packet = (
        followthrough_labels_loaded.get("packet") if followthrough_labels_loaded.get("loaded") else {}
    )
    followthrough_behavior_label_rows = _sanitized_followthrough_behavior_label_rows(followthrough_labels_packet)
    repo_artifact_evidence_packet = (
        repo_artifact_evidence_loaded.get("packet") if repo_artifact_evidence_loaded.get("loaded") else {}
    )
    repo_artifact_evidence_rows = _sanitized_repo_artifact_evidence_rows(repo_artifact_evidence_packet)

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
    story_phase_summary = _build_story_phase_summary(
        repo_handle_chronology,
        committed_safe_narrative_ledger,
        narrative_readiness,
        next_reads,
    )
    evidence_inputs = _build_evidence_backed_summary_inputs(
        semantic_loaded,
        forensics_loaded,
        follow_loaded,
        followthrough_labels_loaded,
        semantic_packet,
        forensics_packet,
        follow_packet,
        followthrough_behavior_label_rows,
        repo_artifact_evidence_loaded,
        repo_artifact_evidence_rows,
    )
    claim_boundaries = _claim_boundaries(semantic_loaded, forensics_loaded, follow_loaded)
    devlog_story_packet = _build_devlog_story_packet(
        narrative_readiness=narrative_readiness,
        story_phase_summary=story_phase_summary,
        repo_handle_chronology=repo_handle_chronology,
        committed_safe_narrative_ledger=committed_safe_narrative_ledger,
        failure_mode_ledger=failure_mode_ledger,
        claim_boundaries=claim_boundaries,
        next_reads=next_reads,
        followthrough_behavior_label_rows=followthrough_behavior_label_rows,
        repo_artifact_evidence_rows=repo_artifact_evidence_rows,
    )
    tool_critique = _tool_critique(
        semantic_loaded=semantic_loaded,
        forensics_loaded=forensics_loaded,
        follow_loaded=follow_loaded,
        followthrough_labels_loaded=followthrough_labels_loaded,
        repo_artifact_evidence_loaded=repo_artifact_evidence_loaded,
        semantic_packet=semantic_packet,
        forensics_packet=forensics_packet,
        follow_packet=follow_packet,
        play_by_play=play_by_play,
        next_reads=next_reads,
    )
    devlog_brief = _build_devlog_brief(
        story_id=story_id,
        date_range=date_range,
        devlog_story_packet=devlog_story_packet,
        failure_mode_ledger=failure_mode_ledger,
        interesting_moments=interesting_moments,
        next_reads=next_reads,
        tool_critique=tool_critique,
    )
    privacy_flags = dict(PRIVACY_FLAGS)
    privacy_flags["input_reports_may_contain_raw_private_material"] = _input_reports_may_contain_raw_private_material(
        semantic_packet,
        forensics_packet,
        follow_packet,
        followthrough_labels_packet,
        repo_artifact_evidence_packet,
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
        "story_phase_summary": story_phase_summary,
        "repo_handle_chronology": repo_handle_chronology,
        "committed_safe_narrative_ledger": committed_safe_narrative_ledger,
        "devlog_story_packet": devlog_story_packet,
        "devlog_brief": devlog_brief,
        "interesting_moments": interesting_moments,
        "tool_critique": tool_critique,
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
    if not isinstance(packet, dict) and packet_key == "repo_artifact_evidence" and isinstance(loaded, dict):
        packet = loaded if loaded.get("schema_version") or loaded.get("rows") else None
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
    followthrough_labels_loaded: dict[str, Any],
    semantic_packet: dict[str, Any],
    forensics_packet: dict[str, Any],
    follow_packet: dict[str, Any],
    followthrough_behavior_label_rows: list[dict[str, Any]],
    repo_artifact_evidence_loaded: dict[str, Any],
    repo_artifact_evidence_rows: list[dict[str, Any]],
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
            "usable_recovery_evidence_count": _usable_recovery_evidence_count(follow_packet),
            "reviewed_usable_recovery_evidence_count": _int(
                follow_packet.get("reviewed_usable_recovery_evidence_count")
            ),
            "reviewed_not_useful_recovery_count": _int(follow_packet.get("reviewed_not_useful_recovery_count")),
            "still_unreviewed_recovery_count": _int(follow_packet.get("still_unreviewed_recovery_count")),
            "review_gap_recovery_count": _int(follow_packet.get("review_gap_recovery_count")),
        },
        {
            "input": "follow_through_behavior_labels",
            "supplied": followthrough_labels_loaded.get("supplied"),
            "loaded": followthrough_labels_loaded.get("loaded"),
            "status": followthrough_labels_loaded.get("status"),
            "schema_version": followthrough_labels_loaded.get("schema_version"),
            "sanitized_label_row_count": len(followthrough_behavior_label_rows),
            "sanitized_label_count": len({row.get("label") for row in followthrough_behavior_label_rows}),
            "source_surface": "follow_through_behavior_labels.rows",
        },
        {
            "input": "repo_artifact_evidence",
            "supplied": repo_artifact_evidence_loaded.get("supplied"),
            "loaded": repo_artifact_evidence_loaded.get("loaded"),
            "status": repo_artifact_evidence_loaded.get("status"),
            "schema_version": repo_artifact_evidence_loaded.get("schema_version"),
            "sanitized_evidence_row_count": len(repo_artifact_evidence_rows),
            "sanitized_category_count": len(
                {
                    category_id
                    for row in repo_artifact_evidence_rows
                    for category_id in _list(row.get("category_ids"))
                }
            ),
            "source_surface": "repo_artifact_evidence.rows",
        },
    ]
    for item, loaded in (
        (inputs[0], semantic_loaded),
        (inputs[1], forensics_loaded),
        (inputs[2], follow_loaded),
        (inputs[3], followthrough_labels_loaded),
        (inputs[4], repo_artifact_evidence_loaded),
    ):
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
                    "recovery_usability": _safe_recovery_usability(read.get("recovery_usability")),
                    "inference_boundary": "opened recovery metadata proves follow-through shape and optional reviewed usability only; raw transcript content remains private",
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
    usability_counts = _recovery_usability_counts(follow_packet)
    usable_recovery_count = max(_int(follow_packet.get("usable_recovery_evidence_count")), usability_counts["private_reviewed_usable"])
    reviewed_not_useful_count = usability_counts["reviewed_not_useful"]
    still_unreviewed_count = usability_counts["still_unreviewed"]
    review_gap_count = usability_counts["review_gap"]
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
    reviewed_all_opened = opened_count and not still_unreviewed_count and not review_gap_count
    if narrative_ready:
        status = "narrative_ready"
    elif opened_count and reviewed_not_useful_count and reviewed_all_opened and not usable_recovery_count:
        status = "recovery_reviewed_not_useful"
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
    if reviewed_not_useful_count:
        reason_codes.append("opened_recovery_reviewed_not_useful")
    if review_gap_count:
        reason_codes.append("opened_recovery_review_gap")
    if still_unreviewed_count:
        reason_codes.append("opened_recovery_still_unreviewed")
    if opened_count and usable_recovery_count and not narrative_ready:
        reason_codes.append("safe_metadata_too_thin_for_committed_narrative")
    if next_reads.get("status") in {"followup_gap", "explicit_no_next_reads"} and not opened_count:
        reason_codes.append(str(next_reads.get("status")))
    return {
        "status": status,
        "candidate_count": candidate_count,
        "recovery_opened_count": opened_count,
        "usable_recovery_evidence_count": usable_recovery_count,
        "reviewed_usable_recovery_evidence_count": usability_counts["private_reviewed_usable"],
        "reviewed_not_useful_recovery_count": reviewed_not_useful_count,
        "still_unreviewed_recovery_count": still_unreviewed_count,
        "review_gap_recovery_count": review_gap_count,
        "recovery_gap_count": recovery_gap_count,
        "safe_dated_candidate_count": dated_candidate_count,
        "safe_recovery_line_range_count": recovery_with_line_range_count,
        "ledger_row_count": len(ledger),
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "recovery_opened": bool(opened_count),
        "follow_next_reads_loaded": bool(follow_loaded.get("loaded")),
        "inference_boundary": "readiness classifies committed-safe reconstruction metadata; it does not claim root cause, all-history completeness, final-answer quality, speed, token savings, or product value",
    }


def _build_story_phase_summary(
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    narrative_readiness: dict[str, Any],
    next_reads: dict[str, Any],
) -> dict[str, Any]:
    repo_rows = [row for row in _list(repo_handle_chronology.get("rows")) if isinstance(row, dict)]
    grouped = _group_story_phase_rows(repo_rows)
    all_rows: list[dict[str, Any]] = []
    for index, group in enumerate(grouped, 1):
        all_rows.append(
            _story_phase_row(
                index=index,
                group=group,
                narrative_readiness=narrative_readiness,
            )
        )
    rows = all_rows[:STORY_PHASE_LIMIT]

    phase_count = len(grouped)
    usable_phase_count = sum(1 for row in all_rows if row.get("usable"))
    ledger_kind_counts = _ledger_kind_counts(committed_safe_narrative_ledger)
    recovery_opened = bool(narrative_readiness.get("recovery_opened_count"))
    usable_recovery_count = _int(narrative_readiness.get("usable_recovery_evidence_count"))
    gap_reasons = _story_phase_gap_reasons(
        repo_handle_chronology=repo_handle_chronology,
        narrative_readiness=narrative_readiness,
        next_reads=next_reads,
        ledger_kind_counts=ledger_kind_counts,
        phase_count=phase_count,
        usable_phase_count=usable_phase_count,
        usable_recovery_count=usable_recovery_count,
        recovery_opened=recovery_opened,
    )
    status = _story_phase_status(
        narrative_status=str(narrative_readiness.get("status") or ""),
        phase_count=phase_count,
        usable_phase_count=usable_phase_count,
        usable_recovery_count=usable_recovery_count,
        recovery_opened=recovery_opened,
    )
    return {
        "status": status,
        "phase_count": phase_count,
        "usable_phase_count": usable_phase_count,
        "rendered_phase_count": len(rows),
        "gap_reasons": gap_reasons,
        "next_review_needs": _story_phase_next_review_needs(
            gap_reasons=gap_reasons,
            repo_handle_chronology=repo_handle_chronology,
            narrative_readiness=narrative_readiness,
            next_reads=next_reads,
        ),
        "inference_boundaries": [
            "story phases are deterministic metadata groups derived only from committed-safe repo chronology, narrative readiness, and narrative ledger rows",
            "rows may identify usable setup/review/recovery metadata, but they do not include raw transcript text or source payloads",
            "private transcript interpretation, root cause, all-history completeness, final-answer quality, speed, token savings, and product value remain unsupported unless separately reviewed",
            "safe next-review needs are labels for local/private review, not generated story prose",
        ],
        "input_counts": {
            "repo_chronology_rows": len(repo_rows),
            "narrative_ledger_rows": len(committed_safe_narrative_ledger),
            "next_read_rows": _int(next_reads.get("count")),
        },
        "ledger_kind_counts": ledger_kind_counts,
        "rows": rows,
    }


def _build_devlog_story_packet(
    *,
    narrative_readiness: dict[str, Any],
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    failure_mode_ledger: list[dict[str, Any]],
    claim_boundaries: dict[str, Any],
    next_reads: dict[str, Any],
    followthrough_behavior_label_rows: list[dict[str, Any]],
    repo_artifact_evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness_status = str(narrative_readiness.get("status") or "unknown")
    phase_status = str(story_phase_summary.get("status") or "unknown")
    supported_claims = _devlog_supported_claims(
        narrative_readiness=narrative_readiness,
        story_phase_summary=story_phase_summary,
        repo_handle_chronology=repo_handle_chronology,
        committed_safe_narrative_ledger=committed_safe_narrative_ledger,
        failure_mode_ledger=failure_mode_ledger,
        next_reads=next_reads,
    )
    non_claims = _devlog_explicit_non_claims(claim_boundaries)
    gaps = _devlog_gaps(
        narrative_readiness=narrative_readiness,
        story_phase_summary=story_phase_summary,
        next_reads=next_reads,
    )
    behavior_quality_labels = _devlog_behavior_quality_labels(
        story_phase_summary=story_phase_summary,
        repo_handle_chronology=repo_handle_chronology,
        committed_safe_narrative_ledger=committed_safe_narrative_ledger,
        failure_mode_ledger=failure_mode_ledger,
        next_reads=next_reads,
        followthrough_behavior_label_rows=followthrough_behavior_label_rows,
    )
    semantic_debugging_categories = _devlog_semantic_debugging_categories(
        behavior_quality_labels=behavior_quality_labels,
        followthrough_behavior_label_rows=followthrough_behavior_label_rows,
        repo_artifact_evidence_rows=repo_artifact_evidence_rows,
    )
    return {
        "status": _devlog_story_status(readiness_status, phase_status, supported_claims, gaps),
        "mode": "deterministic_committed_safe_story_synthesis",
        "take_candidates": _devlog_take_candidates(
            readiness_status=readiness_status,
            phase_status=phase_status,
            narrative_readiness=narrative_readiness,
            story_phase_summary=story_phase_summary,
            repo_handle_chronology=repo_handle_chronology,
            committed_safe_narrative_ledger=committed_safe_narrative_ledger,
        ),
        "evidence_backed_summary_bullets": _devlog_summary_bullets(
            narrative_readiness=narrative_readiness,
            story_phase_summary=story_phase_summary,
            repo_handle_chronology=repo_handle_chronology,
            failure_mode_ledger=failure_mode_ledger,
            next_reads=next_reads,
        ),
        "dated_arc_rows": _devlog_dated_arc_rows(story_phase_summary, repo_handle_chronology),
        "claim_support_ledger": _devlog_claim_support_ledger(
            supported_claims=supported_claims,
            non_claims=non_claims,
            failure_mode_ledger=failure_mode_ledger,
            semantic_debugging_categories=semantic_debugging_categories,
        ),
        "semantic_debugging_categories": semantic_debugging_categories,
        "behavior_quality_labels": behavior_quality_labels,
        "supported_claims": supported_claims,
        "explicit_non_claims": non_claims,
        "gaps": gaps,
        "next_reads": _devlog_next_reads(next_reads),
        "inference_privacy_boundaries": [
            "packet is synthesized only from already-sanitized failure-analysis structures",
            "take candidates are drafting angles, not generated devlog prose",
            "repo artifacts may support semantic-debugging categories and case/story labels when safe handles are present, but they do not prove root cause or runtime promotion",
            "reviewed-negative retained recovery rows are retained as reviewed outcomes and are not promoted to open debt by this packet",
            "raw private text, absolute private paths, session identifiers, record selections, embedding/search artifacts, command details, and source details are not included",
        ],
        "input_surfaces": {
            "narrative_readiness_status": readiness_status,
            "story_phase_summary_status": phase_status,
            "repo_handle_chronology_status": repo_handle_chronology.get("status"),
            "committed_safe_narrative_ledger_rows": len(committed_safe_narrative_ledger),
            "failure_mode_ledger_rows": len(failure_mode_ledger),
            "semantic_debugging_category_rows": len(semantic_debugging_categories),
            "unclassified_semantic_debugging_rows": sum(
                1
                for row in semantic_debugging_categories
                if row.get("classification_status") in {"unclassified", "unknown"}
            ),
            "behavior_quality_label_rows": len(behavior_quality_labels),
            "followthrough_behavior_label_rows": len(followthrough_behavior_label_rows),
            "repo_artifact_evidence_rows": len(repo_artifact_evidence_rows),
            "next_read_rows": _int(next_reads.get("count")),
        },
    }


def _devlog_story_status(
    readiness_status: str,
    phase_status: str,
    supported_claims: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> str:
    if readiness_status == "narrative_ready" and phase_status == "story_ready_metadata":
        return "story_packet_ready"
    if any(claim.get("claim_id") == "repo_artifacts_support_story_evidence" for claim in supported_claims):
        if any(gap.get("gap_id") == "private_recovery_review_needed" for gap in gaps):
            return "story_packet_usable_with_private_review_gaps"
        return "story_packet_usable_with_boundaries"
    if supported_claims:
        return "story_packet_partial"
    return "story_packet_gap"


def _build_devlog_brief(
    *,
    story_id: str,
    date_range: dict[str, Any],
    devlog_story_packet: dict[str, Any],
    failure_mode_ledger: list[dict[str, Any]],
    interesting_moments: list[dict[str, Any]],
    next_reads: dict[str, Any],
    tool_critique: dict[str, Any],
) -> dict[str, Any]:
    categories = _list(devlog_story_packet.get("semantic_debugging_categories"))
    supported_claims = _list(devlog_story_packet.get("supported_claims"))
    non_claims = _list(devlog_story_packet.get("explicit_non_claims"))
    gaps = _list(devlog_story_packet.get("gaps"))
    brief = {
        "status": _devlog_brief_status(devlog_story_packet, categories, supported_claims),
        "mode": "deterministic_committed_safe_devlog_brief",
        "story_id": story_id,
        "date_range": date_range,
        "short_take": _devlog_brief_short_take(devlog_story_packet),
        "evidence_backed_summary": _devlog_brief_summary(devlog_story_packet),
        "timeline_highlights": _devlog_brief_timeline(devlog_story_packet, interesting_moments),
        "semantic_debugging_category_framing": _devlog_brief_categories(categories),
        "failure_mode_framing": _devlog_brief_failure_modes(failure_mode_ledger),
        "non_claims_and_gaps": _devlog_brief_non_claims_and_gaps(non_claims, gaps),
        "next_reads": _devlog_brief_next_reads(next_reads, devlog_story_packet),
        "tool_critique": _devlog_brief_tool_critique(tool_critique),
        "readiness": {
            "story_packet_status": devlog_story_packet.get("status"),
            "supported_claim_count": len(supported_claims),
            "category_count": len(categories),
            "gap_count": len(gaps),
            "non_claim_count": len(non_claims),
            "brief_is_final_prose": False,
        },
        "claim_boundaries": [
            "brief is deterministic packaging from already-sanitized failure-analysis fields",
            "short-take lines are source-backed drafting handles, not final prose",
            "case labels are secondary to reusable semantic-debugging categories",
            "brief does not claim root cause, all-history completeness, token savings, speed, retrieval quality, final prose quality, live runtime cleanup, model forgetting, or global promotion",
        ],
    }
    return brief


def _devlog_brief_status(
    devlog_story_packet: dict[str, Any],
    categories: list[Any],
    supported_claims: list[Any],
) -> str:
    packet_status = str(devlog_story_packet.get("status") or "")
    if supported_claims and categories and packet_status in {
        "story_packet_ready",
        "story_packet_usable_with_boundaries",
        "story_packet_usable_with_private_review_gaps",
    }:
        return "brief_usable_with_boundaries"
    if supported_claims:
        return "brief_partial"
    return "brief_gap"


def _devlog_brief_short_take(devlog_story_packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(devlog_story_packet.get("take_candidates"))[:6], 1):
        if not isinstance(item, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "take_id": item.get("take_id") or f"take-{index:03d}",
                    "line": item.get("take"),
                    "support": item.get("support"),
                    "boundary": item.get("boundary"),
                }
            )
        )
    return rows


def _devlog_brief_summary(devlog_story_packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(_list(devlog_story_packet.get("evidence_backed_summary_bullets"))[:8], 1):
        if not isinstance(item, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "summary_id": f"summary-{index:03d}",
                    "bullet": item.get("bullet"),
                    "support": item.get("support"),
                    "counts": item.get("counts"),
                    "boundary": item.get("boundary"),
                }
            )
        )
    return rows


def _devlog_brief_timeline(
    devlog_story_packet: dict[str, Any],
    interesting_moments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list(devlog_story_packet.get("dated_arc_rows"))[:8]:
        if not isinstance(row, dict):
            continue
        handles = row.get("handle_types") or row.get("handles")
        rows.append(
            _clean_row(
                {
                    "timeline_id": row.get("arc_id"),
                    "source": row.get("source"),
                    "status": row.get("status"),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "task_id": row.get("task_id"),
                    "handles": handles,
                    "boundary": "timeline highlight is a sanitized story arc row, not complete history",
                }
            )
        )
    if rows:
        return rows
    for index, row in enumerate(interesting_moments[:6], 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "timeline_id": f"moment-{index:03d}",
                    "source": row.get("source"),
                    "timestamp_utc": row.get("timestamp_utc"),
                    "status": row.get("status"),
                    "source_handle": row.get("source_handle"),
                    "boundary": row.get("boundary") or "interesting moment is a sanitized saved-report row",
                }
            )
        )
    return rows


def _devlog_brief_categories(categories: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in categories[:8]:
        if not isinstance(category, dict):
            continue
        strength = category.get("evidence_strength") if isinstance(category.get("evidence_strength"), dict) else {}
        rows.append(
            _clean_row(
                {
                    "category_id": category.get("category_id"),
                    "classification_status": category.get("classification_status"),
                    "scope": category.get("scope"),
                    "case_labels": category.get("case_labels"),
                    "representative_safe_handles": category.get("representative_safe_handles"),
                    "support_surfaces": category.get("support_surfaces"),
                    "matched_row_count": strength.get("matched_row_count"),
                    "source_boundary": category.get("source_boundary"),
                }
            )
        )
    return rows


def _devlog_brief_failure_modes(failure_mode_ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(failure_mode_ledger[:6], 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            _clean_row(
                {
                    "failure_id": f"failure-{index:03d}",
                    "failure_mode": row.get("failure_mode"),
                    "status": row.get("status"),
                    "evidence_surface": row.get("evidence_surface"),
                    "boundary": row.get("interpretation_boundary"),
                }
            )
        )
    return rows


def _devlog_brief_non_claims_and_gaps(
    non_claims: list[Any],
    gaps: list[Any],
) -> dict[str, Any]:
    return _clean_row(
        {
            "non_claims": [
                _clean_row(
                    {
                        "claim_id": row.get("claim_id"),
                        "non_claim": row.get("non_claim"),
                        "support_surface": row.get("support_surface"),
                        "boundary": row.get("boundary"),
                    }
                )
                for row in non_claims[:8]
                if isinstance(row, dict)
            ],
            "gaps": [
                _clean_row(
                    {
                        "gap_id": row.get("gap_id"),
                        "status": row.get("status"),
                        "reason": row.get("reason"),
                        "next_action": row.get("next_action"),
                    }
                )
                for row in gaps[:6]
                if isinstance(row, dict)
            ],
        }
    )


def _devlog_brief_next_reads(next_reads: dict[str, Any], devlog_story_packet: dict[str, Any]) -> dict[str, Any]:
    story_next_reads = devlog_story_packet.get("next_reads") if isinstance(devlog_story_packet.get("next_reads"), dict) else {}
    reads = _list(story_next_reads.get("reads")) or _list(next_reads.get("reads"))
    return _clean_row(
        {
            "status": story_next_reads.get("status") or next_reads.get("status"),
            "count": story_next_reads.get("count") if story_next_reads.get("count") is not None else next_reads.get("count"),
            "reads": [
                _clean_row(
                    {
                        "read_id": row.get("read_id"),
                        "kind": row.get("kind"),
                        "source_handle": row.get("source_handle"),
                        "reason": row.get("reason"),
                        "boundary": row.get("boundary"),
                    }
                )
                for row in reads[:6]
                if isinstance(row, dict)
            ],
        }
    )


def _devlog_brief_tool_critique(tool_critique: dict[str, Any]) -> dict[str, Any]:
    return _clean_row(
        {
            "mode": tool_critique.get("mode"),
            "what_the_tool_could_package": tool_critique.get("what_the_tool_could_package"),
            "gaps_and_limitations": _list(tool_critique.get("gaps_and_limitations"))[:6],
            "not_changed_or_claimed": _list(tool_critique.get("not_changed_or_claimed"))[:8],
        }
    )


def _devlog_take_candidates(
    *,
    readiness_status: str,
    phase_status: str,
    narrative_readiness: dict[str, Any],
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    takes: list[dict[str, Any]] = []
    if story_phase_summary.get("usable_phase_count"):
        takes.append(
            {
                "take_id": "metadata_arc_available",
                "take": "Use the safe repo chronology as the story spine before adding private transcript detail.",
                "support": ["story_phase_summary.rows", "repo_handle_chronology.rows"],
                "boundary": "drafting angle only; not generated prose or full-history proof",
            }
        )
    if repo_handle_chronology.get("handle_type_counts"):
        takes.append(
            {
                "take_id": "repo_artifacts_carry_behavior_evidence",
                "take": "Repo artifacts can support reusable semantic-debugging categories with case-specific labels when tied to safe task/review/doc/report handles.",
                "support": ["repo_handle_chronology.handle_type_counts", "story_phase_summary.rows"],
                "boundary": "supports story evidence quality only; not live runtime cleanup or promotion",
            }
        )
    if readiness_status == "recovery_reviewed_not_useful" or narrative_readiness.get("reviewed_not_useful_recovery_count"):
        takes.append(
            {
                "take_id": "reviewed_negative_recovery_closed",
                "take": "Retained recovery rows reviewed as not useful should be described as reviewed-negative recovery, not open debt.",
                "support": ["narrative_readiness.reviewed_not_useful_recovery_count"],
                "boundary": "does not re-review private transcript content",
            }
        )
    if readiness_status == "narrative_ready":
        takes.append(
            {
                "take_id": "ready_without_prose_generation",
                "take": "The packet has enough safe metadata for devlog planning without generating final prose.",
                "support": ["narrative_readiness", "committed_safe_narrative_ledger"],
                "boundary": "ready metadata is not a final-answer-quality claim",
            }
        )
    if phase_status in {"usable_with_private_review_gaps", "usable_with_recovery_gaps"}:
        takes.append(
            {
                "take_id": "bounded_story_with_gaps",
                "take": "Draft from safe phases while keeping recovery and next-read gaps explicit.",
                "support": ["story_phase_summary.gap_reasons", "next_reads"],
                "boundary": "gaps remain local follow-up guidance, not absence-of-evidence claims",
            }
        )
    if committed_safe_narrative_ledger:
        takes.append(
            {
                "take_id": "ledger_before_interpretation",
                "take": "Move from ledger rows to claims only where support surfaces are named.",
                "support": ["committed_safe_narrative_ledger", "claim_support_ledger"],
                "boundary": "ledger metadata does not prove intent or root cause",
            }
        )
    return takes[:DEVLOG_TAKE_LIMIT]


def _devlog_summary_bullets(
    *,
    narrative_readiness: dict[str, Any],
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    failure_mode_ledger: list[dict[str, Any]],
    next_reads: dict[str, Any],
) -> list[dict[str, Any]]:
    repo_counts = repo_handle_chronology.get("handle_type_counts") if isinstance(repo_handle_chronology.get("handle_type_counts"), dict) else {}
    bullets = [
        {
            "bullet": "Failure-analysis produced a claim-bounded story synthesis packet from saved report metadata.",
            "support": ["failure_analysis.devlog_story_packet"],
            "counts": {
                "story_phases": _int(story_phase_summary.get("phase_count")),
                "usable_story_phases": _int(story_phase_summary.get("usable_phase_count")),
            },
            "boundary": "live packaging behavior only; no prose generation",
        },
        {
            "bullet": "Safe repo handles provide a dated artifact arc separate from transcript recovery chronology.",
            "support": ["repo_handle_chronology.date_range", "repo_handle_chronology.rows"],
            "counts": {
                "repo_rows": _int(repo_handle_chronology.get("phase_row_count")),
                "handle_types": len(repo_counts),
            },
            "boundary": "saved-report chronology, not a full repository history audit",
        },
        {
            "bullet": "Narrative readiness remains bounded by reviewed recovery status and safe metadata thickness.",
            "support": ["narrative_readiness"],
            "counts": {
                "recovery_opened": _int(narrative_readiness.get("recovery_opened_count")),
                "reviewed_not_useful": _int(narrative_readiness.get("reviewed_not_useful_recovery_count")),
                "usable_recovery": _int(narrative_readiness.get("usable_recovery_evidence_count")),
            },
            "boundary": "readiness is a packaging classification, not an answer-quality claim",
        },
        {
            "bullet": "Failure modes and next reads remain explicit drafting constraints.",
            "support": ["failure_mode_ledger", "next_reads"],
            "counts": {
                "failure_modes": len(failure_mode_ledger),
                "next_reads": _int(next_reads.get("count")),
            },
            "boundary": "constraints guide follow-up; they do not imply all missing evidence is recoverable",
        },
    ]
    return bullets[:DEVLOG_SUMMARY_LIMIT]


def _devlog_dated_arc_rows(
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(_list(story_phase_summary.get("rows")), 1):
        if not isinstance(row, dict):
            continue
        date_range = row.get("date_range") if isinstance(row.get("date_range"), dict) else {}
        rows.append(
            _clean_row(
                {
                    "arc_id": f"arc-{index:03d}",
                    "source": "story_phase_summary.rows",
                    "phase_id": row.get("phase_id"),
                    "task_id": row.get("task_id"),
                    "start": date_range.get("start"),
                    "end": date_range.get("end"),
                    "status": row.get("status"),
                    "handle_types": row.get("handle_types"),
                    "safe_handles": _bounded_phase_handles(row.get("handles")),
                    "support_surfaces": row.get("source_surfaces"),
                    "gap_reasons": row.get("gap_reasons"),
                    "inference_boundary": "dated arc row is a safe planning row, not generated narrative prose",
                }
            )
        )
    if not rows:
        repo_rows = [row for row in _list(repo_handle_chronology.get("rows")) if isinstance(row, dict)]
        for index, row in enumerate(repo_rows[:DEVLOG_ARC_ROW_LIMIT], 1):
            rows.append(
                _clean_row(
                    {
                        "arc_id": f"arc-{index:03d}",
                        "source": "repo_handle_chronology.rows",
                        "phase_id": row.get("phase_id"),
                        "start": row.get("date"),
                        "end": row.get("date"),
                        "status": row.get("status"),
                        "handle_types": [row.get("handle_type")] if row.get("handle_type") else [],
                        "safe_handles": {row.get("handle_type"): [row.get("handle")]}
                        if row.get("handle_type") and row.get("handle")
                        else {},
                        "support_surfaces": [row.get("source")] if row.get("source") else [],
                        "inference_boundary": "repo row is a safe planning row, not generated narrative prose",
                    }
                )
            )
    return rows[:DEVLOG_ARC_ROW_LIMIT]


def _bounded_phase_handles(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, list[str]] = {}
    for key, items in sorted(value.items()):
        if not isinstance(items, list):
            continue
        safe_items = [str(item) for item in items[:3] if isinstance(item, (str, int))]
        if safe_items:
            safe[str(key)] = safe_items
    return safe


def _devlog_supported_claims(
    *,
    narrative_readiness: dict[str, Any],
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    failure_mode_ledger: list[dict[str, Any]],
    next_reads: dict[str, Any],
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    if story_phase_summary.get("phase_count"):
        claims.append(
            {
                "claim_id": "story_phase_scaffold_available",
                "claim": "A committed-safe story phase scaffold is available.",
                "support_surfaces": ["story_phase_summary.rows"],
                "support_counts": {
                    "phase_count": _int(story_phase_summary.get("phase_count")),
                    "usable_phase_count": _int(story_phase_summary.get("usable_phase_count")),
                },
                "boundary": "scaffold availability does not imply final prose readiness",
            }
        )
    if repo_handle_chronology.get("phase_row_count"):
        claims.append(
            {
                "claim_id": "repo_artifacts_support_story_evidence",
                "claim": "Committed-safe repo artifacts can support semantic-debugging category evidence and bounded case-specific labels.",
                "support_surfaces": ["repo_handle_chronology.rows", "repo_handle_chronology.handle_type_counts"],
                "support_counts": {
                    "repo_rows": _int(repo_handle_chronology.get("phase_row_count")),
                    "handle_type_count": len(repo_handle_chronology.get("handle_type_counts") or {}),
                },
                "boundary": "artifact support is story evidence, not a live Semantic Linter runtime claim",
            }
        )
    if committed_safe_narrative_ledger:
        claims.append(
            {
                "claim_id": "committed_safe_ledger_available",
                "claim": "A committed-safe narrative ledger is available for claim support checks.",
                "support_surfaces": ["committed_safe_narrative_ledger"],
                "support_counts": {"ledger_rows": len(committed_safe_narrative_ledger)},
                "boundary": "ledger rows expose metadata only",
            }
        )
    if narrative_readiness.get("reviewed_not_useful_recovery_count"):
        claims.append(
            {
                "claim_id": "reviewed_negative_recovery_retained",
                "claim": "Reviewed-negative retained recovery rows are classified as reviewed outcomes rather than open debt.",
                "support_surfaces": ["narrative_readiness.reviewed_not_useful_recovery_count"],
                "support_counts": {
                    "reviewed_not_useful": _int(narrative_readiness.get("reviewed_not_useful_recovery_count"))
                },
                "boundary": "does not reinterpret private recovery content",
            }
        )
    if failure_mode_ledger:
        claims.append(
            {
                "claim_id": "failure_modes_are_explicit",
                "claim": "Failure-mode constraints are exposed alongside the story packet.",
                "support_surfaces": ["failure_mode_ledger"],
                "support_counts": {"failure_mode_rows": len(failure_mode_ledger)},
                "boundary": "failure-mode labels are deterministic classifications, not root cause",
            }
        )
    if next_reads.get("count"):
        claims.append(
            {
                "claim_id": "next_reads_available",
                "claim": "Safe next-read handles are available for local follow-up.",
                "support_surfaces": ["next_reads.reads"],
                "support_counts": {"next_read_rows": _int(next_reads.get("count"))},
                "boundary": "next reads are follow-up handles, not raw source content",
            }
        )
    return claims[:DEVLOG_CLAIM_LEDGER_LIMIT]


def _devlog_explicit_non_claims(claim_boundaries: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {
        "root_cause_claim_supported": "root cause",
        "all_history_completeness_claim_supported": "all-history completeness",
        "final_answer_quality_claim_supported": "final answer quality",
        "speed_claim_supported": "speed",
        "token_savings_claim_supported": "token savings",
        "pressure_improvement_claim_supported": "retrieval or pressure improvement",
        "product_value_claim_supported": "global product value",
        "llm_prose_generation_used": "generated final devlog prose",
        "retrieval_rerun": "retrieval rerun",
        "ranking_or_cap_changed": "ranking or cap change",
        "live_model_send_used": "live model send",
    }
    non_claims: list[dict[str, Any]] = []
    for key in sorted(labels):
        if claim_boundaries.get(key) is False:
            non_claims.append(
                {
                    "claim_id": key,
                    "non_claim": labels[key],
                    "support_surface": "claim_boundaries",
                    "boundary": "explicitly unsupported or not performed by failure-analysis",
                }
            )
    non_claims.extend(
        [
            {
                "claim_id": "live_tui_cleanup_claim_supported",
                "non_claim": "live TUI cleanup",
                "support_surface": "task_boundary",
                "boundary": "repo-artifact story evidence does not prove live TUI cleanup",
            },
            {
                "claim_id": "model_forgetting_claim_supported",
                "non_claim": "model forgetting",
                "support_surface": "task_boundary",
                "boundary": "saved report metadata does not prove model memory behavior",
            },
            {
                "claim_id": "global_promotion_claim_supported",
                "non_claim": "global promotion",
                "support_surface": "task_boundary",
                "boundary": "failure-analysis packaging does not promote Semantic Linter behavior globally",
            },
        ]
    )
    return non_claims


def _devlog_gaps(
    *,
    narrative_readiness: dict[str, Any],
    story_phase_summary: dict[str, Any],
    next_reads: dict[str, Any],
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    reason_codes = [str(item) for item in _list(narrative_readiness.get("reason_codes"))]
    phase_gaps = [str(item) for item in _list(story_phase_summary.get("gap_reasons"))]
    for reason in list(dict.fromkeys(reason_codes + phase_gaps)):
        if reason == "opened_recovery_reviewed_not_useful":
            gaps.append(
                {
                    "gap_id": "reviewed_negative_recovery_not_open_debt",
                    "status": "closed_boundary",
                    "reason": reason,
                    "next_step": "cite as reviewed-negative recovery only if relevant",
                    "boundary": "retained reviewed-negative rows do not become open debt in the story packet",
                }
            )
            continue
        if reason in {"opened_recovery_still_unreviewed", "opened_recovery_review_gap", "phase_requires_private_recovery_review"}:
            gap_id = "private_recovery_review_needed"
        elif reason in {"follow_next_reads_not_loaded", "followup_gap", "explicit_no_next_reads"}:
            gap_id = "next_read_or_followup_gap"
        elif reason == "opened_recovery_not_marked_usable":
            gap_id = "usable_recovery_not_marked"
        else:
            gap_id = reason
        gaps.append(
            {
                "gap_id": gap_id,
                "status": "open",
                "reason": reason,
                "next_step": _devlog_gap_next_step(gap_id),
                "boundary": "gap is a drafting/review constraint, not proof that evidence is absent",
            }
        )
    if narrative_readiness.get("status") in {"needs_private_review", "recovery_opened"}:
        gaps.append(
            {
                "gap_id": "private_recovery_review_needed",
                "status": "open",
                "reason": str(narrative_readiness.get("status")),
                "next_step": "privately review opened recovery before making transcript-content claims",
                "boundary": "private review gap is a drafting constraint, not proof that evidence is absent",
            }
        )
    if next_reads.get("status") == "available":
        gaps.append(
            {
                "gap_id": "next_reads_require_local_review",
                "status": "open",
                "reason": "safe_next_read_handles_available",
                "next_step": "open local next-read handles before adding private transcript claims",
                "boundary": "handles are safe metadata; raw source content is not included",
            }
        )
    return _dedupe_gap_rows(gaps)


def _devlog_gap_next_step(gap_id: str) -> str:
    if gap_id == "private_recovery_review_needed":
        return "privately review opened recovery before making transcript-content claims"
    if gap_id == "next_read_or_followup_gap":
        return "load or run follow-next-reads if the story needs source context"
    if gap_id == "usable_recovery_not_marked":
        return "mark usable evidence only after private review"
    return "keep this boundary explicit while drafting"


def _dedupe_gap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row.get("gap_id")), str(row.get("status")), str(row.get("reason")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _devlog_next_reads(next_reads: dict[str, Any]) -> dict[str, Any]:
    reads: list[dict[str, Any]] = []
    for read in _list(next_reads.get("reads"))[:NEXT_READ_LIMIT]:
        if not isinstance(read, dict):
            continue
        reads.append(
            _clean_row(
                {
                    "rank": read.get("rank"),
                    "source": read.get("source"),
                    "kind": read.get("kind"),
                    "status": read.get("status"),
                    "failure_reason": read.get("failure_reason"),
                    "candidate_id": read.get("candidate_id"),
                    "target_handle": read.get("target_handle"),
                    "source_handle": read.get("source_handle"),
                    "reason": read.get("reason"),
                    "boundary": "safe next-read handle only; open locally for raw context",
                }
            )
        )
    return {
        "status": next_reads.get("status"),
        "count": len(reads),
        "reads": reads,
        "explicit_gap": next_reads.get("explicit_gap"),
    }


def _devlog_behavior_quality_labels(
    *,
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    failure_mode_ledger: list[dict[str, Any]],
    next_reads: dict[str, Any],
    followthrough_behavior_label_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_rows = _behavior_quality_evidence_rows(
        story_phase_summary=story_phase_summary,
        repo_handle_chronology=repo_handle_chronology,
        committed_safe_narrative_ledger=committed_safe_narrative_ledger,
        failure_mode_ledger=failure_mode_ledger,
        next_reads=next_reads,
        followthrough_behavior_label_rows=followthrough_behavior_label_rows,
    )
    labels: list[dict[str, Any]] = []
    for label, spec in BEHAVIOR_QUALITY_LABEL_SPECS.items():
        matched = _matched_behavior_quality_rows(label, spec, evidence_rows)
        if not matched:
            continue
        representative_handles = _representative_behavior_handles(matched)
        category_id = BEHAVIOR_LABEL_CATEGORY_IDS[label]
        category_spec = SEMANTIC_DEBUG_CATEGORY_SPECS[category_id]
        labels.append(
            {
                "label": label,
                "case_label": label,
                "case_label_scope": "semantic_linter_story_adapter",
                "story_specific_labels": [label],
                "category_id": category_id,
                "category_name": category_spec["name"],
                "category_scope": category_spec["scope"],
                "classification_status": "classified",
                "source_boundary": category_spec["source_boundary"],
                "evidence_strength": {
                    "matched_row_count": len(matched),
                    "representative_handle_count": len(representative_handles),
                    "source_surface_count": len({str(row.get("source_surface")) for row in matched}),
                },
                "representative_safe_handles": representative_handles,
                "story_useful_signal": spec["story_useful_signal"],
                "support_surfaces": sorted({str(row.get("source_surface")) for row in matched})[:6],
                "boundaries_non_claims": [
                    spec["boundary"],
                    "label is inferred only from sanitized labels and safe repo-relative handles already present in failure-analysis structures or supplied follow-through label rows",
                    "raw private transcript material, private locations, opaque session identifiers, record selections, embedding/search artifacts, command details, and source details are not inspected or emitted",
                    "does not claim live TUI cleanup, model forgetting, global promotion, root cause, all-history completeness, token savings, speed, retrieval improvement, or final prose quality",
                ],
            }
        )
    return labels[:DEVLOG_BEHAVIOR_LABEL_LIMIT]


def _devlog_semantic_debugging_categories(
    *,
    behavior_quality_labels: list[dict[str, Any]],
    followthrough_behavior_label_rows: list[dict[str, Any]],
    repo_artifact_evidence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label_row in behavior_quality_labels:
        if not isinstance(label_row, dict):
            continue
        category_id = str(label_row.get("category_id") or "")
        category_spec = SEMANTIC_DEBUG_CATEGORY_SPECS.get(category_id)
        if not category_spec:
            continue
        rows.append(
            _clean_row(
                {
                    "category_id": category_id,
                    "category_name": category_spec["name"],
                    "classification_status": "classified",
                    "scope": category_spec["scope"],
                    "source_boundary": category_spec["source_boundary"],
                    "case_labels": _list(label_row.get("story_specific_labels")) or [label_row.get("label")],
                    "story_specific_labels": _list(label_row.get("story_specific_labels")) or [label_row.get("label")],
                    "case_label_scope": label_row.get("case_label_scope"),
                    "evidence_strength": label_row.get("evidence_strength"),
                    "representative_safe_handles": label_row.get("representative_safe_handles"),
                    "support_surfaces": label_row.get("support_surfaces"),
                    "boundaries_non_claims": label_row.get("boundaries_non_claims"),
                }
            )
        )

    classified_labels = {str(row.get("label")) for row in behavior_quality_labels if isinstance(row, dict)}
    for row in followthrough_behavior_label_rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        if label in classified_labels or label in BEHAVIOR_QUALITY_LABEL_SPECS:
            continue
        category_spec = SEMANTIC_DEBUG_CATEGORY_SPECS["unknown_semantic_debugging_evidence"]
        safe_handles = _list(row.get("representative_safe_handles"))
        rows.append(
            _clean_row(
                {
                    "category_id": "unknown_semantic_debugging_evidence",
                    "category_name": category_spec["name"],
                    "classification_status": "unclassified",
                    "scope": category_spec["scope"],
                    "source_boundary": category_spec["source_boundary"],
                    "case_labels": [label] if label else [],
                    "story_specific_labels": [label] if label else [],
                    "case_label_scope": "custom_followthrough_behavior_evidence",
                    "evidence_strength": {
                        "matched_row_count": _int(row.get("matched_row_count")) or 1,
                        "representative_handle_count": len(safe_handles),
                        "source_surface_count": len({str(surface) for surface in _list(row.get("support_surfaces"))}),
                    },
                    "representative_safe_handles": safe_handles,
                    "support_surfaces": row.get("support_surfaces"),
                    "safe_labels": row.get("safe_labels"),
                    "boundaries_non_claims": [
                        "unclassified follow-through row is retained as safe semantic-debug evidence for local review; it is not coerced into the Semantic Linter case-label set",
                        "raw private transcript material, private locations, opaque session identifiers, record selections, embedding/search artifacts, command details, and source details are not inspected or emitted",
                        "does not claim product understanding, root cause, all-history completeness, live TUI cleanup, model forgetting, global promotion, token savings, speed, retrieval improvement, or final prose quality",
                    ],
                }
            )
        )
    for repo_row in repo_artifact_evidence_rows:
        if not isinstance(repo_row, dict):
            continue
        category_ids = _list(repo_row.get("category_ids")) or ["unknown_semantic_debugging_evidence"]
        primary_category_id = str(category_ids[0])
        category_spec = SEMANTIC_DEBUG_CATEGORY_SPECS.get(primary_category_id)
        category_name = (
            category_spec["name"]
            if category_spec
            else str(repo_row.get("category_name") or primary_category_id.replace("_", " ").replace("-", " ").title())
        )
        classification_status = "classified" if category_spec else "unclassified"
        scope = category_spec["scope"] if category_spec else "generic_repo_artifact_evidence"
        source_boundary = (
            category_spec["source_boundary"]
            if category_spec
            else "safe generic repo-artifact evidence is surfaced for local review without coercion into Semantic Linter behavior labels"
        )
        safe_handles = _list(repo_row.get("representative_safe_handles"))
        support_surfaces = _list(repo_row.get("support_surfaces")) or ["repo_artifact_evidence.rows"]
        story_specific_labels = _list(repo_row.get("story_specific_labels"))
        rows.append(
            _clean_row(
                {
                    "category_id": primary_category_id,
                    "category_name": category_name,
                    "classification_status": classification_status,
                    "scope": scope,
                    "source_boundary": source_boundary,
                    "case_labels": story_specific_labels,
                    "story_specific_labels": story_specific_labels,
                    "case_label_scope": "generic_repo_artifact_evidence",
                    "category_candidates": category_ids,
                    "phase_hints": repo_row.get("phase_hints"),
                    "evidence_strength": repo_row.get("evidence_strength"),
                    "representative_safe_handles": safe_handles,
                    "support_surfaces": support_surfaces,
                    "matched_safe_terms": repo_row.get("matched_safe_terms"),
                    "claim_boundaries": repo_row.get("claim_boundaries"),
                    "boundaries_non_claims": [
                        *(_list(repo_row.get("claim_boundaries"))[:4]),
                        "repo artifact evidence rows are supplied as sanitized metadata only; failure-analysis does not crawl repository artifacts",
                        "generic repo-artifact categories are not forced into Semantic Linter behavior-quality labels",
                        "raw private transcript material, absolute paths, private labels, opaque session identifiers, record selections, embedding/search artifacts, command details, and source payloads are not inspected or emitted",
                    ],
                }
            )
        )
    return rows[:DEVLOG_BEHAVIOR_LABEL_LIMIT]


def _behavior_quality_evidence_rows(
    *,
    story_phase_summary: dict[str, Any],
    repo_handle_chronology: dict[str, Any],
    committed_safe_narrative_ledger: list[dict[str, Any]],
    failure_mode_ledger: list[dict[str, Any]],
    next_reads: dict[str, Any],
    followthrough_behavior_label_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _list(repo_handle_chronology.get("rows")):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "source_surface": "repo_handle_chronology.rows",
                "safe_handle": row.get("handle") if row.get("handle_type") != "commit_hash" else None,
                "safe_labels": [row.get("status"), row.get("confidence"), row.get("handle_type")],
                "search_text": " ".join(
                    str(item)
                    for item in (
                        row.get("handle"),
                        row.get("status"),
                        row.get("confidence"),
                        row.get("source"),
                    )
                    if item
                ),
            }
        )
    for row in _list(story_phase_summary.get("rows")):
        if not isinstance(row, dict):
            continue
        handles = row.get("handles") if isinstance(row.get("handles"), dict) else {}
        for handle_type, values in sorted(handles.items()):
            for handle in _list(values):
                rows.append(
                    {
                        "source_surface": "story_phase_summary.rows",
                        "safe_handle": handle if handle_type != "commit_hash" else None,
                        "safe_labels": [handle_type, row.get("status"), *(_list(row.get("repo_statuses")))],
                        "search_text": " ".join(
                            str(item)
                            for item in (
                                handle,
                                handle_type,
                                row.get("task_id"),
                                row.get("status"),
                                *(_list(row.get("repo_statuses"))),
                                *(_list(row.get("gap_reasons"))),
                            )
                            if item
                        ),
                    }
                )
    for row in committed_safe_narrative_ledger:
        if not isinstance(row, dict):
            continue
        usability = row.get("recovery_usability") if isinstance(row.get("recovery_usability"), dict) else {}
        labels = [
            row.get("ledger_kind"),
            row.get("status"),
            row.get("recovery_kind"),
            usability.get("evidence_label"),
            usability.get("reason_label"),
            usability.get("decision_id"),
            *(_list(row.get("reason_codes"))),
        ]
        rows.append(
            {
                "source_surface": "committed_safe_narrative_ledger",
                "safe_handle": None,
                "safe_labels": labels,
                "search_text": " ".join(str(item) for item in labels if item),
            }
        )
    for row in failure_mode_ledger:
        if not isinstance(row, dict):
            continue
        labels = [row.get("failure_mode"), row.get("status"), row.get("evidence_surface")]
        rows.append(
            {
                "source_surface": "failure_mode_ledger",
                "safe_handle": None,
                "safe_labels": labels,
                "search_text": " ".join(str(item) for item in labels if item),
            }
        )
    for row in _list(next_reads.get("reads")):
        if not isinstance(row, dict):
            continue
        labels = [row.get("kind"), row.get("status"), row.get("failure_reason"), row.get("reason")]
        rows.append(
            {
                "source_surface": "next_reads.reads",
                "safe_handle": None,
                "safe_labels": labels,
                "search_text": " ".join(str(item) for item in labels if item),
            }
        )
    for row in followthrough_behavior_label_rows:
        handles = _list(row.get("representative_safe_handles"))
        labels = [
            row.get("label"),
            row.get("status"),
            row.get("evidence_status"),
            *(_list(row.get("safe_labels"))),
            *(_list(row.get("support_surfaces"))),
            *handles,
        ]
        rows.append(
            {
                "source_surface": "follow_through_behavior_labels.rows",
                "safe_handle": handles[0] if handles else None,
                "safe_labels": labels,
                "search_text": " ".join(str(item) for item in labels if item),
                "followthrough_safe_handles": handles,
            }
        )
    return rows


def _matched_behavior_quality_rows(
    label: str,
    spec: dict[str, Any],
    evidence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for row in evidence_rows:
        labels = {str(item).lower() for item in _list(row.get("safe_labels")) if item}
        search_text = str(row.get("search_text") or "").lower()
        if label in labels or any(str(pattern).lower() in search_text for pattern in spec.get("patterns", ())):
            matched.append(row)
    return matched


def _representative_behavior_handles(rows: list[dict[str, Any]]) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()
    for row in rows:
        row_handles = _list(row.get("followthrough_safe_handles")) or [row.get("safe_handle")]
        for handle in row_handles:
            if not isinstance(handle, (str, int)):
                continue
            text = str(handle)
            if text in seen:
                continue
            seen.add(text)
            handles.append(text)
            if len(handles) >= 4:
                return handles
    return handles


def _sanitized_followthrough_behavior_label_rows(packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = _list(packet.get("rows") if isinstance(packet, dict) else [])
    for index, row in enumerate(raw_rows, 1):
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        known_case_label = label in BEHAVIOR_QUALITY_LABEL_SPECS
        if not known_case_label and not _safe_unknown_followthrough_case_label(label):
            continue
        safe_handles = [
            handle
            for handle in (_safe_followthrough_handle(item) for item in _list(row.get("representative_safe_handles")))
            if handle
        ][:4]
        support_surfaces = [
            str(item)
            for item in _list(row.get("support_surfaces"))[:6]
            if _safe_support_surface_label(str(item))
        ]
        safe_labels = [
            str(item)
            for item in _list(row.get("safe_labels"))[:8]
            if _safe_followthrough_label_token(str(item))
        ]
        if not safe_handles and not support_surfaces and not safe_labels:
            continue
        rows.append(
            _clean_row(
                {
                    "source_index": index,
                    "label": label,
                    "classification_status": "classified" if known_case_label else "unclassified",
                    "case_label_scope": (
                        "semantic_linter_story_adapter" if known_case_label else "custom_followthrough_behavior_evidence"
                    ),
                    "status": _safe_followthrough_status(row.get("status")),
                    "evidence_status": _safe_followthrough_status(row.get("evidence_status")),
                    "matched_row_count": _int(row.get("matched_row_count")),
                    "representative_safe_handles": safe_handles,
                    "support_surfaces": support_surfaces or ["follow_through_behavior_labels.rows"],
                    "safe_labels": safe_labels,
                    "inference_boundary": (
                        "sanitized follow-through row only; supports semantic-debug evidence planning, "
                        "not raw evidence, product understanding, root cause, all-history completeness, live TUI cleanup, "
                        "model forgetting, token/speed claims, global promotion, or final prose"
                    ),
                }
            )
        )
    return rows[:DEVLOG_BEHAVIOR_LABEL_LIMIT]


def _sanitized_repo_artifact_evidence_rows(packet: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_rows = _list(packet.get("rows") if isinstance(packet, dict) else [])
    for index, row in enumerate(raw_rows, 1):
        if not isinstance(row, dict):
            continue
        if _repo_artifact_row_has_forbidden_payload(row):
            continue
        if _repo_artifact_row_has_private_label_values(row):
            continue
        if _repo_artifact_row_has_unsafe_handle_or_surface_values(row):
            continue
        safe_handles = _safe_repo_artifact_handles(row)
        support_surfaces = [
            str(item)
            for item in _repo_artifact_values(row, "support_surface", "support_surfaces", "surface", "surfaces")[:6]
            if _safe_support_surface_label(str(item))
        ]
        category_ids = [
            str(item)
            for item in _repo_artifact_values(
                row,
                "category_id",
                "category_ids",
                "category_candidate",
                "category_candidates",
                "semantic_debugging_category",
                "semantic_debugging_categories",
            )[:6]
            if _safe_repo_artifact_label_token(str(item))
        ]
        story_specific_labels = [
            str(item)
            for item in _repo_artifact_values(
                row,
                "story_specific_label",
                "story_specific_labels",
                "story_label",
                "story_labels",
                "safe_label",
                "safe_labels",
            )[:6]
            if _safe_repo_artifact_story_label(str(item))
        ]
        phase_hints = [
            str(item)
            for item in _repo_artifact_values(row, "phase_hint", "phase_hints", "story_phase", "story_phases", "phase")[:6]
            if _safe_repo_artifact_label_token(str(item))
        ]
        matched_safe_terms = [
            str(item)
            for item in _repo_artifact_values(row, "matched_safe_term", "matched_safe_terms", "safe_terms")[:8]
            if _safe_repo_artifact_label_token(str(item))
        ]
        claim_boundaries = [
            str(item)
            for item in _repo_artifact_values(row, "claim_boundary", "claim_boundaries", "boundary", "boundaries")[:6]
            if _safe_repo_artifact_boundary(str(item))
        ]
        evidence_strength = _repo_artifact_evidence_strength(row)
        category_name = _safe_repo_artifact_category_name(row.get("category_name") or row.get("name"))
        if not (safe_handles or support_surfaces or category_ids or story_specific_labels or phase_hints):
            continue
        rows.append(
            _clean_row(
                {
                    "source_index": index,
                    "category_ids": category_ids or ["unknown_semantic_debugging_evidence"],
                    "category_name": category_name,
                    "story_specific_labels": story_specific_labels,
                    "phase_hints": phase_hints,
                    "evidence_strength": evidence_strength,
                    "representative_safe_handles": safe_handles,
                    "support_surfaces": support_surfaces or ["repo_artifact_evidence.rows"],
                    "matched_safe_terms": matched_safe_terms,
                    "claim_boundaries": claim_boundaries
                    or [
                        "sanitized repo-artifact metadata only; no raw artifact crawling or root-cause claim"
                    ],
                    "inference_boundary": (
                        "sanitized repo-artifact evidence row only; supports semantic-debug category framing, "
                        "not Semantic Linter behavior-label classification, root cause, all-history completeness, "
                        "token/speed claims, global promotion, final prose, or raw source inspection"
                    ),
                }
            )
        )
    return rows[:DEVLOG_BEHAVIOR_LABEL_LIMIT]


def _repo_artifact_values(row: dict[str, Any], *keys: str) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        value = row.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return values


def _repo_artifact_row_has_forbidden_payload(row: dict[str, Any]) -> bool:
    forbidden_keys = {
        "body",
        "command",
        "command_payload",
        "commands",
        "cwd",
        "index",
        "indexes",
        "payload",
        "raw",
        "raw_body",
        "raw_text",
        "record",
        "records",
        "selected_record",
        "selected_records",
        "session",
        "session_id",
        "session_ids",
        "snippet",
        "source",
        "source_payload",
        "source_ref",
        "source_refs",
        "text",
        "vector",
        "vectors",
    }
    for key, _value in _walk_items(row):
        lowered = str(key).lower()
        if lowered in forbidden_keys:
            return True
        if "payload" in lowered or "session" in lowered or "vector" in lowered or lowered in {"indexes", "records"}:
            return True
    return False


def _repo_artifact_row_has_private_label_values(row: dict[str, Any]) -> bool:
    for value in _repo_artifact_values(
        row,
        "category_id",
        "category_ids",
        "category_candidate",
        "category_candidates",
        "semantic_debugging_category",
        "semantic_debugging_categories",
        "story_specific_label",
        "story_specific_labels",
        "story_label",
        "story_labels",
        "safe_label",
        "safe_labels",
        "phase_hint",
        "phase_hints",
        "story_phase",
        "story_phases",
        "phase",
        "matched_safe_term",
        "matched_safe_terms",
        "safe_terms",
    ):
        if isinstance(value, str) and _private_like_repo_artifact_token(value):
            return True
    return False


def _repo_artifact_row_has_unsafe_handle_or_surface_values(row: dict[str, Any]) -> bool:
    for value in _repo_artifact_values(
        row,
        "handle",
        "handles",
        "safe_handle",
        "safe_handles",
        "repo_relative_handle",
        "repo_relative_handles",
        "representative_safe_handle",
        "representative_safe_handles",
    ):
        if _safe_followthrough_handle(value) is None:
            return True
    for value in _repo_artifact_values(row, "support_surface", "support_surfaces", "surface", "surfaces"):
        if not _safe_support_surface_label(str(value)):
            return True
    return False


def _safe_repo_artifact_handles(row: dict[str, Any]) -> list[str]:
    handles: list[str] = []
    seen: set[str] = set()
    for value in _repo_artifact_values(
        row,
        "handle",
        "handles",
        "safe_handle",
        "safe_handles",
        "repo_relative_handle",
        "repo_relative_handles",
        "representative_safe_handle",
        "representative_safe_handles",
    ):
        safe = _safe_followthrough_handle(value)
        if not safe or safe in seen:
            continue
        seen.add(safe)
        handles.append(safe)
        if len(handles) >= 4:
            break
    return handles


def _safe_repo_artifact_label_token(value: str) -> bool:
    if not _safe_followthrough_label_token(value):
        return False
    return not _private_like_repo_artifact_token(value)


def _safe_repo_artifact_story_label(value: str) -> bool:
    if not _safe_repo_artifact_label_token(value):
        return False
    return len(value) <= 80


def _private_like_repo_artifact_token(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "private",
            "raw",
            "secret",
            "session",
            "payload",
            "vector",
            "index",
            "command",
        )
    )


def _safe_repo_artifact_boundary(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 180 or text.startswith("/") or "\\" in text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in ("private", "raw ", "session", "payload", "secret", "vector", "index")):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.,:;()' /-]+", text))


def _safe_repo_artifact_category_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.startswith("/") or len(text) > 80 or _private_like_repo_artifact_token(text):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_ .:/-]+", text):
        return None
    return text


def _repo_artifact_evidence_strength(row: dict[str, Any]) -> dict[str, int]:
    strength = row.get("evidence_strength") if isinstance(row.get("evidence_strength"), dict) else {}
    counts = {
        "matched_row_count": _int(row.get("matched_row_count") or strength.get("matched_row_count")),
        "evidence_count": _int(row.get("evidence_count") or strength.get("evidence_count")),
        "support_count": _int(row.get("support_count") or strength.get("support_count")),
    }
    return {key: value for key, value in counts.items() if value > 0} or {"matched_row_count": 1}


def _safe_followthrough_handle(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or text.startswith("/") or "\\" in text or ".." in text.split("/"):
        return None
    if any(text.startswith(prefix) for prefix, _kind in SAFE_REPO_PATH_PREFIXES):
        return text
    return None


def _safe_support_surface_label(value: str) -> bool:
    text = value.strip()
    if not text or text.startswith("/") or "\\" in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.\-/]+", text))


def _safe_followthrough_label_token(value: str) -> bool:
    text = value.strip()
    if not text or text.startswith("/") or "\\" in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", text))


def _safe_unknown_followthrough_case_label(value: str) -> bool:
    if not _safe_followthrough_label_token(value):
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in ("private", "raw", "session", "secret", "token", "payload")):
        return False
    return True


def _safe_followthrough_status(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9_.:-]+", text):
        return None
    return text


def _devlog_claim_support_ledger(
    *,
    supported_claims: list[dict[str, Any]],
    non_claims: list[dict[str, Any]],
    failure_mode_ledger: list[dict[str, Any]],
    semantic_debugging_categories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, claim in enumerate(supported_claims, 1):
        rows.append(
            {
                "ledger_id": f"claim-{index:03d}",
                "ledger_kind": "supported_claim",
                "claim_id": claim.get("claim_id"),
                "support_surfaces": claim.get("support_surfaces"),
                "support_counts": claim.get("support_counts"),
                "boundary": claim.get("boundary"),
            }
        )
    for index, non_claim in enumerate(non_claims, 1):
        rows.append(
            {
                "ledger_id": f"non-claim-{index:03d}",
                "ledger_kind": "explicit_non_claim",
                "claim_id": non_claim.get("claim_id"),
                "support_surfaces": [non_claim.get("support_surface")],
                "boundary": non_claim.get("boundary"),
            }
        )
    for index, failure in enumerate(failure_mode_ledger, 1):
        rows.append(
            {
                "ledger_id": f"constraint-{index:03d}",
                "ledger_kind": "drafting_constraint",
                "claim_id": failure.get("failure_mode"),
                "support_surfaces": [failure.get("evidence_surface")],
                "boundary": failure.get("interpretation_boundary"),
            }
        )
    for index, category in enumerate(semantic_debugging_categories, 1):
        rows.append(
            {
                "ledger_id": f"semantic-debug-category-{index:03d}",
                "ledger_kind": "semantic_debugging_category",
                "claim_id": category.get("category_id"),
                "classification_status": category.get("classification_status"),
                "case_labels": category.get("case_labels"),
                "support_surfaces": category.get("support_surfaces"),
                "support_counts": category.get("evidence_strength"),
                "representative_safe_handles": category.get("representative_safe_handles"),
                "boundary": "; ".join(str(item) for item in _list(category.get("boundaries_non_claims"))[:2])
                or category.get("source_boundary"),
            }
        )
    return rows[:DEVLOG_CLAIM_LEDGER_LIMIT]


def _ledger_kind_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ledger_kind = str(row.get("ledger_kind") or "unknown")
        counts[ledger_kind] = counts.get(ledger_kind, 0) + 1
    return dict(sorted(counts.items()))


def _group_story_phase_rows(repo_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    for row in repo_rows:
        key = _story_phase_group_key(row)
        if key not in groups:
            groups[key] = {
                "key": key,
                "task_ids": set(),
                "handles": {},
                "dates": set(),
                "statuses": set(),
                "sources": set(),
                "repo_phase_ids": [],
            }
            ordered_keys.append(key)
        group = groups[key]
        handle_type = str(row.get("handle_type") or "unknown")
        handle = str(row.get("handle") or "")
        if handle:
            group["handles"].setdefault(handle_type, set()).add(handle)
            for task_id in _task_ids(handle):
                group["task_ids"].add(task_id)
        if row.get("date"):
            group["dates"].add(str(row.get("date")))
        if row.get("status"):
            group["statuses"].add(str(row.get("status")))
        if row.get("source"):
            group["sources"].add(str(row.get("source")))
        if row.get("phase_id"):
            group["repo_phase_ids"].append(str(row.get("phase_id")))

    def sort_key(key: str) -> tuple[str, str]:
        group = groups[key]
        dates = sorted(group["dates"])
        task_ids = sorted(group["task_ids"])
        return (dates[0] if dates else "9999-12-31T23:59:59+00:00", task_ids[0] if task_ids else key)

    return [groups[key] for key in sorted(ordered_keys, key=sort_key)]


def _story_phase_group_key(row: dict[str, Any]) -> str:
    handle_type = str(row.get("handle_type") or "")
    handle = str(row.get("handle") or "")
    task_ids = _task_ids(handle)
    if task_ids:
        return f"task:{task_ids[0]}"
    if handle_type in {"task_path", "review_path", "doc_path", "report_path"} and handle:
        return f"path:{handle}"
    if handle_type == "commit_hash" and handle:
        return f"commit:{handle}"
    return f"{handle_type}:{handle}"


def _story_phase_row(
    *,
    index: int,
    group: dict[str, Any],
    narrative_readiness: dict[str, Any],
) -> dict[str, Any]:
    handles = {
        handle_type: sorted(values)[:3]
        for handle_type, values in sorted(group["handles"].items())
        if values
    }
    dates = sorted(group["dates"])
    task_ids = sorted(group["task_ids"])
    gap_reasons: list[str] = []
    handle_types = set(handles)
    if not dates:
        gap_reasons.append("phase_missing_safe_date")
    if not (handle_types & {"task_path", "review_path", "doc_path", "report_path"}):
        gap_reasons.append("phase_missing_safe_artifact_path")
    if narrative_readiness.get("status") in {"recovery_opened", "needs_private_review"}:
        gap_reasons.append("phase_requires_private_recovery_review")
    if narrative_readiness.get("status") == "recovery_reviewed_not_useful":
        gap_reasons.append("phase_recovery_reviewed_not_story_useful")
    if narrative_readiness.get("status") == "candidate_found":
        gap_reasons.append("phase_recovery_not_opened")
    usable = bool(handles and (dates or task_ids or handle_types & {"task_path", "review_path", "doc_path", "report_path"}))
    status = "usable_metadata"
    if not usable:
        status = "insufficient_safe_metadata"
    elif "phase_requires_private_recovery_review" in gap_reasons:
        status = "usable_metadata_private_review_needed"
    elif gap_reasons:
        status = "usable_metadata_with_gaps"
    return _clean_row(
        {
            "phase_id": f"story-phase-{index:03d}",
            "status": status,
            "usable": usable,
            "task_id": task_ids[0] if task_ids else None,
            "date_range": {
                "start": dates[0] if dates else None,
                "end": dates[-1] if dates else None,
                "date_count": len(dates),
            },
            "handle_types": sorted(handle_types),
            "handles": handles,
            "repo_phase_ids": group["repo_phase_ids"][:6],
            "source_surfaces": sorted(group["sources"])[:6],
            "repo_statuses": sorted(group["statuses"])[:6],
            "gap_reasons": gap_reasons,
            "next_review_needs": _story_phase_row_next_review_needs(gap_reasons),
            "inference_boundary": "phase row is a safe metadata grouping for review/story planning; it is not generated narrative prose or a root-cause claim",
        }
    )


def _story_phase_row_next_review_needs(gap_reasons: list[str]) -> list[str]:
    needs: list[str] = []
    if "phase_requires_private_recovery_review" in gap_reasons:
        needs.append("private_review_opened_recovery_before_devlog_claim")
    if "phase_recovery_not_opened" in gap_reasons:
        needs.append("run_or_load_follow_next_reads_for_phase_context")
    if "phase_missing_safe_date" in gap_reasons:
        needs.append("add_safe_repo_or_review_date_metadata")
    if "phase_missing_safe_artifact_path" in gap_reasons:
        needs.append("add_safe_task_review_doc_or_report_handle")
    return needs


def _story_phase_status(
    *,
    narrative_status: str,
    phase_count: int,
    usable_phase_count: int,
    usable_recovery_count: int,
    recovery_opened: bool,
) -> str:
    if not phase_count:
        return "no_story_phases_available"
    if not usable_phase_count:
        return "blocked_on_safe_phase_metadata"
    if narrative_status == "narrative_ready" and usable_recovery_count:
        return "story_ready_metadata"
    if narrative_status == "recovery_reviewed_not_useful":
        return "usable_with_reviewed_negative_recovery"
    if recovery_opened and not usable_recovery_count:
        return "usable_with_private_review_gaps"
    if narrative_status in {"needs_private_review", "recovery_opened"}:
        return "usable_with_private_review_gaps"
    return "usable_with_recovery_gaps"


def _story_phase_gap_reasons(
    *,
    repo_handle_chronology: dict[str, Any],
    narrative_readiness: dict[str, Any],
    next_reads: dict[str, Any],
    ledger_kind_counts: dict[str, int],
    phase_count: int,
    usable_phase_count: int,
    usable_recovery_count: int,
    recovery_opened: bool,
) -> list[str]:
    gaps: list[str] = []
    if not phase_count:
        gaps.append("no_committed_safe_repo_phases")
    if phase_count and not usable_phase_count:
        gaps.append("no_usable_phase_rows")
    gaps.extend(str(item) for item in _list(repo_handle_chronology.get("gaps"))[:12])
    gaps.extend(str(item) for item in _list(narrative_readiness.get("reason_codes"))[:12])
    if ledger_kind_counts.get("recovery_gap"):
        gaps.append("narrative_ledger_recovery_gaps_present")
    if recovery_opened and not usable_recovery_count:
        gaps.append("opened_recovery_has_no_usable_evidence")
    if next_reads.get("status") in {"followup_gap", "explicit_no_next_reads"}:
        gaps.append(str(next_reads.get("status")))
    return list(dict.fromkeys(gaps))


def _story_phase_next_review_needs(
    *,
    gap_reasons: list[str],
    repo_handle_chronology: dict[str, Any],
    narrative_readiness: dict[str, Any],
    next_reads: dict[str, Any],
) -> list[str]:
    needs: list[str] = []
    if not (repo_handle_chronology.get("rows") or []):
        needs.append("add_or_load_committed_safe_repo_chronology")
    if "some_repo_handles_missing_safe_dates" in gap_reasons:
        needs.append("add_safe_dates_for_undated_repo_handles")
    if any(reason.endswith("_not_present") for reason in gap_reasons):
        needs.append("add_missing_safe_task_review_doc_report_or_commit_handles")
    if "follow_next_reads_not_loaded" in gap_reasons or next_reads.get("status") == "followup_gap":
        needs.append("load_or_run_follow_next_reads")
    if narrative_readiness.get("status") in {"recovery_opened", "needs_private_review"}:
        needs.append("private_review_opened_recovery_evidence")
    if narrative_readiness.get("status") == "recovery_reviewed_not_useful":
        needs.append("decide_whether_reviewed_negative_recovery_blocks_story")
    if "opened_recovery_has_no_usable_evidence" in gap_reasons:
        needs.append("mark_or_recover_usable_evidence_before_story_claim")
    if not needs:
        needs.append("review_metadata_phase_rows_before_writing_devlog_prose")
    return list(dict.fromkeys(needs))


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
    followthrough_labels_loaded: dict[str, Any],
    repo_artifact_evidence_loaded: dict[str, Any],
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
            "follow_through_behavior_labels": _input_status(followthrough_labels_loaded),
            "repo_artifact_evidence": _input_status(repo_artifact_evidence_loaded),
        },
        "what_the_tool_could_package": {
            "play_by_play_rows": len(play_by_play),
            "next_read_rows": next_reads.get("count", 0),
            "forensics_phase_rows": len(_list(forensics_packet.get("phase_timeline"))),
            "follow_next_reads_usable_recovery_evidence_count": _usable_recovery_evidence_count(follow_packet),
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
    if not followthrough_labels_loaded.get("supplied"):
        gaps.append("follow-through behavior labels report was not supplied")
    if not repo_artifact_evidence_loaded.get("supplied"):
        gaps.append("repo-artifact evidence report was not supplied")
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
                "usable_recovery_evidence_count": _usable_recovery_evidence_count(follow_packet),
                "reviewed_usable_recovery_evidence_count": _int(
                    follow_packet.get("reviewed_usable_recovery_evidence_count")
                ),
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


def _safe_recovery_usability(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed_statuses = {
        "private_reviewed_usable",
        "reviewed_not_useful",
        "still_unreviewed",
        "review_gap",
    }
    status = str(value.get("status") or "")
    if status not in allowed_statuses:
        return {}
    safe: dict[str, Any] = {
        "status": status,
        "matched": bool(value.get("matched")),
        "decision_source": _safe_label_or_none(value.get("decision_source")),
        "matched_by": _safe_label_or_none(value.get("matched_by")),
        "decision_id": _safe_label_or_none(value.get("decision_id")),
        "candidate_id": _safe_label_or_none(value.get("candidate_id")),
        "recovery_id": _safe_label_or_none(value.get("recovery_id")),
        "evidence_label": _safe_label_or_none(value.get("evidence_label")),
        "reason_label": _safe_label_or_none(value.get("reason_label")),
        "reviewer_label": _safe_label_or_none(value.get("reviewer_label")),
    }
    for key in ("reviewed_record_count", "usable_record_count"):
        if value.get(key) is not None:
            safe[key] = _int(value.get(key))
    if value.get("committed_safe") is not None:
        safe["committed_safe"] = bool(value.get("committed_safe"))
    match_context = value.get("match_context") if isinstance(value.get("match_context"), dict) else {}
    safe_context = {
        key: _safe_label_or_none(match_context.get(key))
        for key in ("target_handle", "source_handle", "original_target_handle", "recovery_kind")
    }
    if match_context.get("rank") is not None:
        safe_context["rank"] = _int(match_context.get("rank"))
    safe_context = {key: item for key, item in safe_context.items() if item not in (None, "", [], {})}
    if safe_context:
        safe["match_context"] = safe_context
    return {key: item for key, item in safe.items() if item not in (None, "", [], {})}


def _recovery_usability_counts(follow_packet: dict[str, Any]) -> dict[str, int]:
    top_level_counts = {
        "private_reviewed_usable": _int(follow_packet.get("reviewed_usable_recovery_evidence_count")),
        "reviewed_not_useful": _int(follow_packet.get("reviewed_not_useful_recovery_count")),
        "still_unreviewed": _int(follow_packet.get("still_unreviewed_recovery_count")),
        "review_gap": _int(follow_packet.get("review_gap_recovery_count")),
    }
    derived_counts = dict.fromkeys(top_level_counts, 0)
    for read in _list(follow_packet.get("expanded_reads")):
        if not isinstance(read, dict):
            continue
        if str(read.get("status") or "") != "expanded":
            continue
        usability = _safe_recovery_usability(read.get("recovery_usability"))
        status = usability.get("status")
        if status in derived_counts:
            derived_counts[status] += 1
    return {
        status: max(top_level_counts[status], derived_counts[status])
        for status in top_level_counts
    }


def _usable_recovery_evidence_count(follow_packet: dict[str, Any]) -> int:
    return max(
        _int(follow_packet.get("usable_recovery_evidence_count")),
        _recovery_usability_counts(follow_packet)["private_reviewed_usable"],
    )


def _safe_label_or_none(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    if not text or len(text) > 160:
        return None
    if _safe_short_label(text) or re.match(r"^[A-Za-z0-9._:-]+$", text):
        return text
    return None


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
