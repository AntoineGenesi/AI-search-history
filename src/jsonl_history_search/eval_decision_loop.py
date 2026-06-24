from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .compact_handoff_usefulness import (
    DEFAULT_ANSWER_USE_FIXTURE as DEFAULT_COMPACT_HANDOFF_ANSWER_USE_FIXTURE,
    DEFAULT_FIXTURE as DEFAULT_COMPACT_HANDOFF_USEFULNESS_FIXTURE,
    build_compact_handoff_answer_use_eval_payload,
    build_compact_handoff_usefulness_eval_payload,
)
from .retrieve_expand_answer_use import (
    DEFAULT_FIXTURE as DEFAULT_RETRIEVE_EXPAND_ANSWER_USE_FIXTURE,
    build_retrieve_expand_answer_use_eval_payload,
)
from .semantic_debugging import summarize_answer_readiness, summarize_guided_audit_flow
from .source_tier_answer_use import (
    DEFAULT_FIXTURE as DEFAULT_SOURCE_TIER_ANSWER_USE_FIXTURE,
    build_source_tier_answer_use_eval_payload,
)


SCHEMA_VERSION = 1
MIN_PRESSURE_CASE_COUNT = 20
PRESSURE_KEY = "real_history_impact_benchmark"
UNPROVEN_CLAIMS = {
    "token_savings_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "default_promotion_supported": False,
    "all_history_completeness_claim_supported": False,
    "live_harness_superiority_claim_supported": False,
    "product_value_claim_supported": False,
    "llm_judge_as_sole_arbiter": False,
}
PRIVACY_BOUNDARIES = {
    "raw_private_text_included": False,
    "raw_private_paths_included": False,
    "raw_query_text_included": False,
    "raw_session_ids_included": False,
    "raw_usernames_included": False,
    "source_lines_included": False,
    "line_numbers_included": False,
    "vectors_or_indexes_included": False,
}
NON_LIVE_BOUNDARIES = {
    "live_model_send_used": False,
    "send_capable_codex_exec_used": False,
    "docker_action_used": False,
    "auth_handling_used": False,
    "app_server_turn_or_start_used": False,
}
SEMANTIC_DEBUG_VALID_DECISIONS = {
    "answer_now",
    "inspect_recovery_reads",
    "run_audit_route",
    "state_scoped_no_evidence",
    "gather_more_evidence",
}
SEMANTIC_DEBUG_VALID_READINESS_STATUSES = {
    "answer_ready",
    "inspect_recovery_first",
    "audit_route_required",
    "scoped_no_evidence_ready",
    "insufficient_evidence",
}
FOLLOW_NEXT_READ_VALID_STATUSES = {"expanded", "partial_expanded", "no_next_reads", "selected_reads_failed"}
FOLLOWED_RECOVERY_TARGET = "use_followed_recovery_evidence_for_bounded_answer_decision"


def build_eval_decision_loop_payload(
    pressure_report: Path | None = None,
    semantic_debug_report: Path | None = None,
    follow_next_reads_report: Path | None = None,
) -> dict[str, Any]:
    deterministic = _build_deterministic_eval_summaries()
    pressure = _load_pressure_summary(pressure_report)
    semantic_debug_readiness = _load_semantic_debug_readiness_summary(semantic_debug_report)
    recovery_follow_through = _load_recovery_follow_through_summary(follow_next_reads_report)
    decision = _build_decision(deterministic, pressure, semantic_debug_readiness, recovery_follow_through)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": decision["decision_status"],
        "evidence_basis": {
            "deterministic_eval_builders_reused_in_process": True,
            "pressure_report_supplied": pressure_report is not None,
            "pressure_report_loaded": pressure.get("loaded", False),
            "semantic_debug_report_supplied": semantic_debug_report is not None,
            "semantic_debug_report_loaded": bool(semantic_debug_readiness.get("loaded")) if semantic_debug_readiness else False,
            "semantic_debug_report_privacy_contract_ok": semantic_debug_readiness.get("privacy_contract_ok")
            if semantic_debug_readiness
            else None,
            "follow_next_reads_report_supplied": follow_next_reads_report is not None,
            "follow_next_reads_report_loaded": bool(recovery_follow_through.get("loaded"))
            if recovery_follow_through
            else False,
            "follow_next_reads_report_privacy_contract_ok": recovery_follow_through.get("privacy_contract_ok")
            if recovery_follow_through
            else None,
            "non_live_eval_synthesis_only": True,
        },
        "deterministic_evals": deterministic,
        "pressure_report": pressure,
        **({"semantic_debug_readiness": semantic_debug_readiness} if semantic_debug_readiness is not None else {}),
        **({"recovery_follow_through": recovery_follow_through} if recovery_follow_through is not None else {}),
        "decision": decision,
        "claim_boundaries": {
            **UNPROVEN_CLAIMS,
            **PRIVACY_BOUNDARIES,
            **NON_LIVE_BOUNDARIES,
        },
        "privacy_contract": {
            **PRIVACY_BOUNDARIES,
            "aggregate_only_pressure_fields": True,
            "deterministic_eval_rows_omitted": True,
            "pressure_report_path_echoed": False,
            "semantic_debug_report_path_echoed": False,
            "follow_next_reads_report_path_echoed": False,
        },
    }
    return {"eval_decision_loop": report}


def _build_deterministic_eval_summaries() -> dict[str, Any]:
    payloads = {
        "retrieve_expand_answer_use": build_retrieve_expand_answer_use_eval_payload(
            DEFAULT_RETRIEVE_EXPAND_ANSWER_USE_FIXTURE
        )["retrieve_expand_answer_use_eval"],
        "source_tier_answer_use": build_source_tier_answer_use_eval_payload(DEFAULT_SOURCE_TIER_ANSWER_USE_FIXTURE)[
            "source_tier_answer_use_eval"
        ],
        "compact_handoff_answer_use": build_compact_handoff_answer_use_eval_payload(
            DEFAULT_COMPACT_HANDOFF_ANSWER_USE_FIXTURE
        )["compact_handoff_answer_use_eval"],
        "compact_handoff_usefulness": build_compact_handoff_usefulness_eval_payload(
            DEFAULT_COMPACT_HANDOFF_USEFULNESS_FIXTURE
        )["compact_handoff_usefulness_eval"],
    }
    evals = {name: _summarize_eval(name, payload) for name, payload in payloads.items()}
    statuses = {name: item["status"] for name, item in evals.items()}
    failing = [name for name, status in statuses.items() if status != "ok"]
    return {
        "status": "passing" if not failing else "inconclusive",
        "eval_statuses": statuses,
        "failing_or_inconclusive_evals": failing,
        "evals": evals,
        "claim_boundaries": {
            "deterministic_packet_evals_only": True,
            "synthetic_or_approved_safe_fixtures_only": True,
            "large_history_product_value_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "token_savings_claim_supported": False,
            "default_promotion_supported": False,
        },
    }


def _summarize_eval(name: str, report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    claim_boundaries = report.get("claim_boundaries") if isinstance(report.get("claim_boundaries"), dict) else {}
    return {
        "status": str(report.get("status") or "missing"),
        "data_source": str(report.get("data_source") or "unknown"),
        "case_count": _int(summary.get("case_count")),
        "positive_count": _positive_count(name, summary),
        "negative_or_inconclusive_count": _negative_count(summary),
        "selected_next_product_decision": str(report.get("selected_next_product_decision") or "no_decision"),
        "selected_next_bottleneck": str(report.get("selected_next_bottleneck") or "no_decision"),
        "claim_boundaries": {
            "token_savings_claim_supported": bool(claim_boundaries.get("token_savings_claim_supported", False)),
            "final_answer_quality_claim_supported": bool(
                claim_boundaries.get("final_answer_quality_claim_supported", False)
            ),
            "all_history_completeness_claim_supported": bool(
                claim_boundaries.get("all_history_completeness_claim_supported", False)
            ),
            "default_promotion_supported": bool(claim_boundaries.get("default_promotion_supported", False)),
            "private_transcript_text_used": bool(claim_boundaries.get("private_transcript_text_used", False)),
            "llm_judge_gate_used": bool(claim_boundaries.get("llm_judge_gate_used", False)),
        },
    }


def _positive_count(name: str, summary: dict[str, Any]) -> int:
    if name == "retrieve_expand_answer_use":
        return _int(summary.get("actionable_or_deferred_count"))
    for key in ("useful_count", "actionable_count"):
        if key in summary:
            return _int(summary.get(key))
    return 0


def _negative_count(summary: dict[str, Any]) -> int:
    keys = (
        "failure_count",
        "not_useful_count",
        "not_actionable_count",
        "inconclusive_count",
        "invalid_fixture_count",
    )
    return sum(_int(summary.get(key)) for key in keys)


def _load_pressure_summary(pressure_report: Path | None) -> dict[str, Any]:
    if pressure_report is None:
        return _missing_pressure("not_supplied")
    try:
        loaded = json.loads(pressure_report.expanduser().read_text(encoding="utf-8"))
    except OSError:
        return _missing_pressure("read_failed")
    except json.JSONDecodeError:
        return {
            **_missing_pressure("json_invalid"),
            "status": "inconclusive",
            "classification": "inconclusive",
        }
    report = loaded.get(PRESSURE_KEY) if isinstance(loaded, dict) else None
    if not isinstance(report, dict):
        return {
            **_missing_pressure("real_history_impact_benchmark_missing"),
            "status": "inconclusive",
            "classification": "inconclusive",
        }
    return _summarize_pressure_report(report)


def _missing_pressure(reason: str) -> dict[str, Any]:
    return {
        "status": "missing",
        "classification": "missing",
        "loaded": False,
        "reason": reason,
        "case_count": 0,
        "minimum_case_count": MIN_PRESSURE_CASE_COUNT,
        "caps_hit": False,
        "cap_hit_labels": [],
        "selected_next_bottleneck": "no_decision",
        "selected_next_tool_delta": "run_full_or_large_history_pressure_before_product_claim",
        "expected_ref_reach": {},
        "source_tier_answerability": {},
        "compact_vs_full_bytes": {"status": "missing"},
        "privacy_contract_ok": True,
    }


def _summarize_pressure_report(report: dict[str, Any]) -> dict[str, Any]:
    case_count = _int(report.get("case_count") or report.get("manifest_case_count"))
    aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}
    fielded = _nested_dict(aggregate, "fielded_bm25")
    expected_ref_reach = _summarize_expected_ref_reach(_nested_dict(fielded, "expected_ref_reach"))
    stage_pressure = _nested_dict(report, "large_history_stage_attribution_pressure")
    if not stage_pressure:
        stage_pressure = _nested_dict(aggregate, "large_history_stage_attribution_pressure")
    stage_summary = _nested_dict(stage_pressure, "stage_bottleneck_summary")
    signal_counts = _nested_dict(stage_summary, "signal_counts")
    source_tier = _nested_dict(report, "source_tier_answerability_pressure")
    if not source_tier:
        source_tier = _nested_dict(aggregate, "source_tier_answerability_pressure")
    compact_budget = _nested_dict(source_tier, "compact_context_budget")
    selected_delta = _nested_dict(stage_pressure, "selected_next_tool_delta").get("next_tool_delta")
    if not selected_delta:
        selected_delta = _recommendation_for_bottleneck(str(stage_pressure.get("selected_next_bottleneck") or ""))
    caps_hit = bool(report.get("caps_hit") or _nested_dict(aggregate, "cap_hit_rows_by_label"))
    cap_hit_labels = [str(item) for item in report.get("cap_hit_labels") or aggregate.get("cap_hit_labels") or []]
    if not cap_hit_labels:
        cap_hit_labels = sorted(str(key) for key in _nested_dict(aggregate, "cap_hit_rows_by_label").keys())
    classification = _pressure_classification(case_count, caps_hit, stage_pressure)
    return {
        "status": classification,
        "classification": classification,
        "loaded": True,
        "case_count": case_count,
        "minimum_case_count": MIN_PRESSURE_CASE_COUNT,
        "caps_hit": caps_hit,
        "cap_hit_labels": cap_hit_labels,
        "selected_next_bottleneck": str(stage_pressure.get("selected_next_bottleneck") or "no_decision"),
        "selected_next_tool_delta": str(selected_delta or "run_full_or_large_history_pressure_before_product_claim"),
        "expected_ref_reach": expected_ref_reach,
        "source_tier_answerability": {
            "answerable_from_packet_rows": _int(source_tier.get("answerable_from_packet_rows")),
            "supported_in_searched_scope_rows": _int(signal_counts.get("supported_in_searched_scope_rows")),
            "deferred_source_needed_rows": _int(signal_counts.get("deferred_source_needed_rows")),
            "searched_scope_no_evidence_rows": _int(signal_counts.get("searched_scope_no_evidence_rows")),
            "next_read_recoverable_rows": _int(signal_counts.get("next_read_recoverable_rows")),
            "candidate_presence_implies_answerability_rows": _int(
                signal_counts.get("candidate_presence_implies_answerability_rows")
            ),
        },
        "stage_bottleneck_summary": {
            "bottleneck_stage_counts": _safe_int_dict(_nested_dict(stage_summary, "bottleneck_stage_counts")),
            "signal_counts": _safe_int_dict(signal_counts),
            "selection_basis": str(stage_summary.get("selection_basis") or ""),
        },
        "compact_vs_full_bytes": _compact_vs_full_bytes(compact_budget),
        "privacy_contract_ok": _privacy_contract_ok(report, aggregate, stage_pressure, source_tier),
    }


def _summarize_expected_ref_reach(reach: dict[str, Any]) -> dict[str, Any]:
    stage_presence = _safe_int_dict(_nested_dict(reach, "stage_presence_counts"))
    reach_stage = _safe_int_dict(_nested_dict(reach, "expected_ref_reach_stage_counts"))
    dropped = _safe_int_dict(_nested_dict(reach, "dropped_by_stage_counts"))
    pre_grouping_presence = _int(reach.get("pre_grouping_candidate_presence_count"))
    final_presence = _int(stage_presence.get("final") or reach_stage.get("final"))
    grouping_loss = _int(dropped.get("grouping"))
    return {
        "pre_grouping_candidate_presence_count": pre_grouping_presence,
        "stage_presence_counts": stage_presence,
        "expected_ref_reach_stage_counts": reach_stage,
        "dropped_by_stage_counts": dropped,
        "final_presence_count": final_presence,
        "grouping_loss_count": grouping_loss,
        "pre_grouping_all_cases_reached": pre_grouping_presence >= MIN_PRESSURE_CASE_COUNT,
        "grouping_loss_observed": grouping_loss > 0,
    }


def _compact_vs_full_bytes(compact_budget: dict[str, Any]) -> dict[str, Any]:
    compact = _int(compact_budget.get("compact_context_bytes"))
    full = _int(compact_budget.get("full_result_output_context_bytes"))
    if not compact and not full:
        return {"status": "missing"}
    return {
        "status": "measured",
        "compact_context_bytes": compact,
        "full_result_output_context_bytes": full,
        "median_compact_context_bytes": compact_budget.get("median_compact_context_bytes"),
        "median_full_result_output_context_bytes": compact_budget.get("median_full_result_output_context_bytes"),
        "compact_to_full_ratio": round(compact / full, 4) if full else None,
        "token_savings_claim_supported": False,
    }


def _pressure_classification(case_count: int, caps_hit: bool, stage_pressure: dict[str, Any]) -> str:
    if case_count < MIN_PRESSURE_CASE_COUNT:
        return "small"
    if not stage_pressure:
        return "inconclusive"
    if caps_hit:
        return "capped"
    return "evaluated"


def _build_decision(
    deterministic: dict[str, Any],
    pressure: dict[str, Any],
    semantic_debug_readiness: dict[str, Any] | None,
    recovery_follow_through: dict[str, Any] | None,
) -> dict[str, Any]:
    deterministic_status = deterministic["status"]
    pressure_status = pressure["status"]
    bottleneck = _dominant_bottleneck(pressure)
    pressure_target = _recommended_target(pressure, bottleneck)
    readiness_target = _semantic_debug_readiness_target(semantic_debug_readiness)
    follow_through_target = _recovery_follow_through_target(semantic_debug_readiness, recovery_follow_through)
    target = follow_through_target or readiness_target or pressure_target
    limitations = _limitations(deterministic_status, pressure)
    if semantic_debug_readiness and not _semantic_debug_readiness_is_valid(semantic_debug_readiness):
        limitations.append("semantic-debug readiness report was invalid or privacy-flagged, so it was not used for routing")
    if recovery_follow_through and not _recovery_follow_through_is_valid(recovery_follow_through):
        limitations.append("follow-next-reads report was invalid, unsupported, or privacy-flagged, so it was not used for routing")
    elif recovery_follow_through and readiness_target == "inspect_recovery_reads" and not follow_through_target:
        limitations.append("follow-next-reads report did not contain usable expanded recovery evidence")
    decision_status = "ok" if deterministic_status == "passing" and pressure_status == "evaluated" else "inconclusive"
    decision = {
        "decision_status": decision_status,
        "packet_eval_status": deterministic_status,
        "source_tier_compact_status": deterministic_status,
        "pressure_status": pressure_status,
        "dominant_bottleneck": bottleneck,
        "recommended_next_product_target": target,
        "limitations": limitations,
        "supported_claims": {
            "deterministic_packet_eval_contracts_pass": deterministic_status == "passing",
            "aggregate_pressure_bottleneck_identified": pressure_status in {"capped", "evaluated"}
            and bottleneck not in {"no_decision", "real_history_pressure_missing", "real_history_pressure_too_small"},
            "compact_vs_full_bytes_measured": pressure.get("compact_vs_full_bytes", {}).get("status") == "measured",
        },
        "unsupported_claims": UNPROVEN_CLAIMS,
    }
    if semantic_debug_readiness is not None:
        decision["pressure_recommended_next_product_target"] = pressure_target
        decision["routing_basis"] = "semantic_debug_readiness" if readiness_target else "aggregate_pressure"
    if recovery_follow_through is not None:
        decision["pressure_recommended_next_product_target"] = pressure_target
        if readiness_target:
            decision["semantic_debug_recommended_next_product_target"] = readiness_target
        decision["routing_basis"] = (
            "recovery_follow_through"
            if follow_through_target
            else "semantic_debug_readiness"
            if readiness_target
            else "aggregate_pressure"
        )
    return decision


def _dominant_bottleneck(pressure: dict[str, Any]) -> str:
    status = pressure.get("status")
    if status == "missing":
        return "real_history_pressure_missing"
    if status == "small":
        return "real_history_pressure_too_small"
    selected = str(pressure.get("selected_next_bottleneck") or "no_decision")
    if selected != "no_decision":
        return selected
    return "real_history_pressure_inconclusive"


def _recommended_target(pressure: dict[str, Any], bottleneck: str) -> str:
    if pressure.get("status") not in {"capped", "evaluated"}:
        return _recommendation_for_bottleneck(bottleneck)
    selected = str(pressure.get("selected_next_tool_delta") or "")
    if selected and selected != "run_full_or_large_history_pressure_before_product_claim":
        return selected
    return _recommendation_for_bottleneck(bottleneck)


def _semantic_debug_readiness_target(semantic_debug_readiness: dict[str, Any] | None) -> str | None:
    if not semantic_debug_readiness:
        return None
    if not _semantic_debug_readiness_is_valid(semantic_debug_readiness):
        return None
    decision = str(semantic_debug_readiness.get("decision") or "")
    if decision in SEMANTIC_DEBUG_VALID_DECISIONS:
        return decision
    return None


def _semantic_debug_readiness_is_valid(semantic_debug_readiness: dict[str, Any]) -> bool:
    return (
        bool(semantic_debug_readiness.get("privacy_contract_ok", False))
        and semantic_debug_readiness.get("status") in SEMANTIC_DEBUG_VALID_READINESS_STATUSES
        and semantic_debug_readiness.get("decision") in SEMANTIC_DEBUG_VALID_DECISIONS
    )


def _recovery_follow_through_target(
    semantic_debug_readiness: dict[str, Any] | None,
    recovery_follow_through: dict[str, Any] | None,
) -> str | None:
    if _semantic_debug_readiness_target(semantic_debug_readiness) != "inspect_recovery_reads":
        return None
    if not recovery_follow_through or not _recovery_follow_through_is_valid(recovery_follow_through):
        return None
    if _int(recovery_follow_through.get("usable_recovery_evidence_count")) <= 0:
        return None
    return FOLLOWED_RECOVERY_TARGET


def _recovery_follow_through_is_valid(recovery_follow_through: dict[str, Any]) -> bool:
    return (
        bool(recovery_follow_through.get("privacy_contract_ok", False))
        and recovery_follow_through.get("status") in FOLLOW_NEXT_READ_VALID_STATUSES
        and bool(recovery_follow_through.get("loaded", False))
    )


def _recommendation_for_bottleneck(bottleneck: str) -> str:
    return {
        "source_window_choice": "improve_source_window_choice_under_large_history_pressure",
        "within_source_depth": "improve_within_source_depth_and_expansion",
        "query_planning": "improve_query_planning",
        "output_behavior": "improve_output_behavior_and_answer_guidance",
        "indexing_latency": "improve_indexing_latency_only_if_it_blocks_evidence_quality",
        "real_history_pressure_missing": "run_full_or_large_history_pressure_before_product_claim",
        "real_history_pressure_too_small": "rerun_pressure_with_at_least_20_cases_before_product_claim",
    }.get(bottleneck, "inspect_inconclusive_pressure_before_product_delta")


def _limitations(deterministic_status: str, pressure: dict[str, Any]) -> list[str]:
    limitations = [
        "no live/model-send harness was run",
        "deterministic evals are packet/actionability contract checks, not final-answer quality proof",
        "no token-savings, default-promotion, all-history completeness, or live-superiority claim is supported",
    ]
    if deterministic_status != "passing":
        limitations.append("one or more deterministic evals are not passing")
    if pressure["status"] == "missing":
        limitations.append("real-history pressure report was not supplied or could not be loaded")
    elif pressure["status"] == "small":
        limitations.append("real-history pressure case count is below the decision floor")
    elif pressure["status"] == "capped":
        limitations.append("real-history pressure is capped, so success and completeness claims are not supported")
    elif pressure["status"] == "inconclusive":
        limitations.append("real-history pressure fields are insufficient for a product bottleneck decision")
    return limitations


def _load_semantic_debug_readiness_summary(semantic_debug_report: Path | None) -> dict[str, Any] | None:
    if semantic_debug_report is None:
        return None
    try:
        loaded = json.loads(semantic_debug_report.expanduser().read_text(encoding="utf-8"))
    except OSError:
        return _invalid_semantic_debug_readiness("read_failed")
    except json.JSONDecodeError:
        return _invalid_semantic_debug_readiness("json_invalid")
    packet = loaded.get("semantic_debug") if isinstance(loaded, dict) else loaded
    if not isinstance(packet, dict):
        return _invalid_semantic_debug_readiness("semantic_debug_packet_missing")
    answer_readiness = packet.get("answer_readiness") if isinstance(packet.get("answer_readiness"), dict) else None
    guided_audit_flow = packet.get("guided_audit_flow") if isinstance(packet.get("guided_audit_flow"), dict) else None
    if not answer_readiness:
        return _invalid_semantic_debug_readiness("answer_readiness_missing")
    privacy_contract_ok = _semantic_debug_privacy_contract_ok(packet, answer_readiness, guided_audit_flow)
    readiness_summary = summarize_answer_readiness(answer_readiness)
    guided_summary = summarize_guided_audit_flow(guided_audit_flow) if guided_audit_flow else {}
    decision = readiness_summary.get("decision") if privacy_contract_ok else "gather_more_evidence"
    status = readiness_summary.get("status") if privacy_contract_ok else "invalid"
    allowed_answer_scope = readiness_summary.get("allowed_answer_scope") if privacy_contract_ok else "invalid"
    required_before_answer = list(readiness_summary.get("required_before_answer") or [])[:4]
    if not privacy_contract_ok:
        required_before_answer = [
            "Do not route from this semantic-debug report; fix the privacy flags or regenerate a committed-safe report.",
        ]
    return {
        "schema_version": readiness_summary.get("schema_version", 1),
        "loaded": True,
        "status": status,
        "decision": decision,
        "allowed_answer_scope": allowed_answer_scope,
        "privacy_contract_ok": privacy_contract_ok,
        "basis": readiness_summary.get("basis") or {},
        "required_before_answer": required_before_answer,
        "route_summary": readiness_summary.get("route_summary") or {},
        "claim_boundaries": readiness_summary.get("claim_boundaries") or {},
        "privacy_boundaries": readiness_summary.get("privacy_boundaries") or {},
        "guided_audit_flow_summary": {
            "schema_version": guided_summary.get("schema_version", 1) if guided_summary else 1,
            "status": guided_summary.get("status") if guided_summary else None,
            "primary_question_family": guided_summary.get("primary_question_family") if guided_summary else None,
            "primary_intent": guided_summary.get("primary_intent") if guided_summary else None,
            "stage_count": guided_summary.get("stage_count", 0) if guided_summary else 0,
            "evidence_handle_count": guided_summary.get("evidence_handle_count", 0) if guided_summary else 0,
            "route_order": list(guided_summary.get("route_order") or [])[:5] if guided_summary else [],
            "answer_gate": {
                "answer_scope": (guided_summary.get("answer_gate") or {}).get("answer_scope") if guided_summary else None,
                "source_use_required": (guided_summary.get("answer_gate") or {}).get("source_use_required")
                if guided_summary
                else None,
                "answer_readiness_status": (guided_summary.get("answer_gate") or {}).get("answer_readiness_status")
                if guided_summary
                else None,
                "answer_readiness_decision": (guided_summary.get("answer_gate") or {}).get("answer_readiness_decision")
                if guided_summary
                else None,
                "answer_readiness_scope": (guided_summary.get("answer_gate") or {}).get("answer_readiness_scope")
                if guided_summary
                else None,
                "required_before_answer": list((guided_summary.get("answer_gate") or {}).get("required_before_answer") or [])[:4]
                if guided_summary
                else [],
            },
            "privacy_boundaries": guided_summary.get("privacy_boundaries") if guided_summary else {},
            "next_read_targets": list(guided_summary.get("next_read_targets") or [])[:5] if guided_summary else [],
        },
    }


def _load_recovery_follow_through_summary(follow_next_reads_report: Path | None) -> dict[str, Any] | None:
    if follow_next_reads_report is None:
        return None
    try:
        loaded = json.loads(follow_next_reads_report.expanduser().read_text(encoding="utf-8"))
    except OSError:
        return _invalid_recovery_follow_through("read_failed")
    except json.JSONDecodeError:
        return _invalid_recovery_follow_through("json_invalid")
    packet = loaded.get("follow_next_reads") if isinstance(loaded, dict) else loaded
    if not isinstance(packet, dict):
        return _invalid_recovery_follow_through("follow_next_reads_packet_missing")
    status = str(packet.get("status") or "missing")
    source_packet = packet.get("source_packet") if isinstance(packet.get("source_packet"), dict) else {}
    claim_boundaries = packet.get("claim_boundaries") if isinstance(packet.get("claim_boundaries"), dict) else {}
    privacy_flags = packet.get("privacy_flags") if isinstance(packet.get("privacy_flags"), dict) else {}
    privacy_contract_ok = _follow_next_reads_privacy_contract_ok(packet, source_packet, claim_boundaries)
    failure_reasons = _follow_next_reads_failure_reasons(packet)
    summary = {
        "schema_version": _int(packet.get("schema_version")) or 1,
        "loaded": True,
        "status": status if privacy_contract_ok else "invalid",
        "privacy_contract_ok": privacy_contract_ok,
        "source_packet_status": source_packet.get("status"),
        "source_packet_type": source_packet.get("packet_type"),
        "selected_count": _int(packet.get("selected_count")),
        "expanded_count": _int(packet.get("expanded_count")),
        "failed_count": _int(packet.get("failed_count")),
        "usable_recovery_evidence_count": _int(packet.get("usable_recovery_evidence_count")),
        "failure_reasons": failure_reasons[:8],
        "claim_boundaries": {
            "broad_search_rerun": bool(claim_boundaries.get("broad_search_rerun", False)),
            "ranking_or_cap_changed": bool(claim_boundaries.get("ranking_or_cap_changed", False)),
            "retrieval_defaults_changed": bool(claim_boundaries.get("retrieval_defaults_changed", False)),
            "answer_generation_included": bool(claim_boundaries.get("answer_generation_included", False)),
            "final_answer_claim_supported": bool(claim_boundaries.get("final_answer_claim_supported", False)),
            "all_history_completeness_claim_supported": bool(
                claim_boundaries.get("all_history_completeness_claim_supported", False)
            ),
        },
        "privacy_boundaries": {
            "raw_runtime_source_text_consumed": False,
            "raw_runtime_source_text_included_in_summary": False,
            "raw_paths_included_in_summary": False,
            "line_numbers_included_in_summary": False,
            "follow_next_reads_report_path_echoed": False,
            "source_refs_or_targets_echoed": False,
            "raw_follow_next_reads_packet_may_include_text": bool(privacy_flags.get("raw_text_included", False)),
            "raw_follow_next_reads_packet_may_include_paths": bool(privacy_flags.get("raw_paths_included", False)),
        },
    }
    if status not in FOLLOW_NEXT_READ_VALID_STATUSES:
        summary["invalid_reason"] = f"unsupported_status:{status}"
    if not privacy_contract_ok:
        summary["invalid_reason"] = "privacy_or_claim_boundary_failed"
    return summary


def _invalid_recovery_follow_through(reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "loaded": False,
        "status": "invalid",
        "privacy_contract_ok": False,
        "source_packet_status": None,
        "source_packet_type": None,
        "selected_count": 0,
        "expanded_count": 0,
        "failed_count": 0,
        "usable_recovery_evidence_count": 0,
        "failure_reasons": [],
        "claim_boundaries": {},
        "privacy_boundaries": {
            "raw_runtime_source_text_consumed": False,
            "raw_runtime_source_text_included_in_summary": False,
            "raw_paths_included_in_summary": False,
            "follow_next_reads_report_path_echoed": False,
        },
        "invalid_reason": reason,
    }


def _follow_next_reads_failure_reasons(packet: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if packet.get("failure_reason"):
        reasons.append(str(packet.get("failure_reason")))
    for item in packet.get("failed_reads") or []:
        if isinstance(item, dict) and item.get("failure_reason"):
            reasons.append(str(item.get("failure_reason")))
    if not reasons:
        for item in packet.get("expanded_reads") or []:
            if isinstance(item, dict) and item.get("status") != "expanded":
                reasons.append(str(item.get("failure_reason") or item.get("status") or "failed"))
    return list(dict.fromkeys(reasons))


def _follow_next_reads_privacy_contract_ok(*sections: dict[str, Any]) -> bool:
    forbidden_true_keys = {
        "broad_search_rerun",
        "ranking_or_cap_changed",
        "retrieval_defaults_changed",
        "answer_generation_included",
        "final_answer_claim_supported",
        "all_history_completeness_claim_supported",
        "vectors_indexes_or_persistent_cache_used",
    }
    for section in sections:
        for key, value in _walk_dict_items(section):
            if key in forbidden_true_keys and value is True:
                return False
    return True


def _invalid_semantic_debug_readiness(reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "loaded": False,
        "status": "invalid",
        "decision": "gather_more_evidence",
        "allowed_answer_scope": "invalid",
        "privacy_contract_ok": False,
        "basis": {},
        "required_before_answer": [
            "Do not route from this semantic-debug report; fix the privacy flags or regenerate a committed-safe report.",
        ],
        "route_summary": {},
        "claim_boundaries": {},
        "privacy_boundaries": {},
        "guided_audit_flow_summary": {},
        "invalid_reason": reason,
    }


def _semantic_debug_privacy_contract_ok(*sections: dict[str, Any]) -> bool:
    forbidden_true_keys = {
        "raw_private_text_included",
        "raw_private_path_included",
        "raw_private_paths_included",
        "raw_query_text_included",
        "raw_path_included",
        "raw_paths_included",
        "private_path_included",
        "private_paths_included",
        "path_included",
        "paths_included",
        "raw_session_id_included",
        "raw_session_ids_included",
        "raw_session_ref_included",
        "raw_session_refs_included",
        "session_id_included",
        "session_ids_included",
        "session_ref_included",
        "session_refs_included",
        "raw_username_included",
        "raw_usernames_included",
        "username_included",
        "usernames_included",
        "local_username_included",
        "local_usernames_included",
        "raw_source_lines_included",
        "source_lines_included",
        "line_numbers_included",
        "raw_text_included",
        "snippets_included",
        "raw_cwd_included",
        "vectors_or_indexes_included",
        "private_payloads_included",
        "payloads_included",
    }
    for section in sections:
        for key, value in _walk_dict_items(section):
            if key in forbidden_true_keys and value is True:
                return False
    return True


def _privacy_contract_ok(*sections: dict[str, Any]) -> bool:
    forbidden_true_keys = {
        "raw_private_text_included",
        "raw_private_path_included",
        "raw_private_paths_included",
        "raw_query_text_included",
        "raw_path_included",
        "raw_paths_included",
        "private_path_included",
        "private_paths_included",
        "path_included",
        "paths_included",
        "raw_session_id_included",
        "raw_session_ids_included",
        "raw_session_ref_included",
        "raw_session_refs_included",
        "session_id_included",
        "session_ids_included",
        "session_ref_included",
        "session_refs_included",
        "raw_username_included",
        "raw_usernames_included",
        "username_included",
        "usernames_included",
        "local_username_included",
        "local_usernames_included",
        "source_lines_included",
        "line_numbers_included",
        "vectors_or_indexes_included",
        "payloads_included",
    }
    for section in sections:
        for key, value in _walk_dict_items(section):
            if key in forbidden_true_keys and value is True:
                return False
    return True


def _walk_dict_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_dict_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dict_items(child)


def _nested_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    child = value.get(key) if isinstance(value, dict) else None
    return child if isinstance(child, dict) else {}


def _safe_int_dict(value: dict[str, Any]) -> dict[str, int]:
    return {str(key): _int(item) for key, item in value.items()} if isinstance(value, dict) else {}


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def render_eval_decision_loop_markdown(payload: dict[str, Any]) -> str:
    report = payload["eval_decision_loop"]
    decision = report["decision"]
    pressure = report["pressure_report"]
    deterministic = report["deterministic_evals"]
    lines = [
        "# Eval Decision Loop",
        "",
        "## Decision",
        "",
        f"- Status: `{report['status']}`",
        f"- Packet eval status: `{decision['packet_eval_status']}`",
        f"- Pressure status: `{decision['pressure_status']}`",
        f"- Dominant bottleneck: `{decision['dominant_bottleneck']}`",
        f"- Recommended next product target: `{decision['recommended_next_product_target']}`",
        "",
        "## Deterministic Evals",
        "",
    ]
    if "routing_basis" in decision:
        lines[10:10] = [
            f"- Pressure recommended target: `{decision.get('pressure_recommended_next_product_target')}`",
            f"- Routing basis: `{decision.get('routing_basis')}`",
        ]
    for name, item in deterministic["evals"].items():
        lines.append(
            f"- `{name}`: status `{item['status']}`, cases `{item['case_count']}`, "
            f"positive `{item['positive_count']}`, next `{item['selected_next_product_decision']}`"
        )
    readiness = report.get("semantic_debug_readiness") or {}
    if readiness:
        lines.extend(
            [
                "",
                "## Answer Readiness Routing",
                "",
                f"- Loaded: `{readiness.get('loaded')}`",
                f"- Privacy contract ok: `{readiness.get('privacy_contract_ok')}`",
                f"- Status: `{readiness.get('status')}`",
                f"- Decision: `{readiness.get('decision')}`",
                f"- Allowed answer scope: `{readiness.get('allowed_answer_scope')}`",
            ]
        )
        if readiness.get("invalid_reason"):
            lines.append(f"- Invalid reason: `{readiness.get('invalid_reason')}`")
        basis = readiness.get("basis") or {}
        if basis:
            lines.extend(
                [
                    f"- Present ledger count: `{basis.get('present_ledger_count', 0)}`",
                    f"- Recovery ledger count: `{basis.get('recovery_ledger_count', 0)}`",
                    f"- Structured tool event count: `{basis.get('structured_tool_event_count', 0)}`",
                    f"- Next-read count: `{basis.get('next_read_count', 0)}`",
                ]
            )
        route_summary = readiness.get("route_summary") or {}
        if route_summary:
            lines.append(f"- Route order: `{', '.join(route_summary.get('route_order', []))}`")
        for action in readiness.get("required_before_answer", [])[:4]:
            lines.append(f"- Required before answer: {action}")
    follow_through = report.get("recovery_follow_through") or {}
    if follow_through:
        lines.extend(
            [
                "",
                "## Recovery Follow-Through",
                "",
                f"- Loaded: `{follow_through.get('loaded')}`",
                f"- Privacy contract ok: `{follow_through.get('privacy_contract_ok')}`",
                f"- Status: `{follow_through.get('status')}`",
                f"- Selected reads: `{follow_through.get('selected_count', 0)}`",
                f"- Expanded reads: `{follow_through.get('expanded_count', 0)}`",
                f"- Failed reads: `{follow_through.get('failed_count', 0)}`",
                f"- Usable recovery evidence: `{follow_through.get('usable_recovery_evidence_count', 0)}`",
            ]
        )
        if follow_through.get("invalid_reason"):
            lines.append(f"- Invalid reason: `{follow_through.get('invalid_reason')}`")
        for reason in follow_through.get("failure_reasons", [])[:4]:
            lines.append(f"- Failure reason: `{reason}`")
    lines.extend(
        [
            "",
            "## Pressure",
            "",
            f"- Classification: `{pressure['classification']}`",
            f"- Cases: `{pressure['case_count']}` / floor `{pressure['minimum_case_count']}`",
            f"- Caps hit: `{pressure['caps_hit']}`",
            f"- Cap labels: `{', '.join(pressure['cap_hit_labels']) or 'none'}`",
            f"- Selected bottleneck: `{pressure['selected_next_bottleneck']}`",
            f"- Selected tool delta: `{pressure['selected_next_tool_delta']}`",
        ]
    )
    reach = pressure.get("expected_ref_reach") or {}
    if reach:
        lines.extend(
            [
                "",
                "## Expected Ref Reach",
                "",
                f"- Pre-grouping candidate presence: `{reach.get('pre_grouping_candidate_presence_count', 0)}`",
                f"- Final presence: `{reach.get('final_presence_count', 0)}`",
                f"- Grouping loss: `{reach.get('grouping_loss_count', 0)}`",
            ]
        )
    source_tier = pressure.get("source_tier_answerability") or {}
    if source_tier:
        lines.extend(
            [
                "",
                "## Source-Tier Answerability",
                "",
                f"- Answerable from packet rows: `{source_tier.get('answerable_from_packet_rows', 0)}`",
                f"- Supported in searched scope rows: `{source_tier.get('supported_in_searched_scope_rows', 0)}`",
                f"- Deferred source needed rows: `{source_tier.get('deferred_source_needed_rows', 0)}`",
                f"- Next-read recoverable rows: `{source_tier.get('next_read_recoverable_rows', 0)}`",
            ]
        )
    compact = pressure.get("compact_vs_full_bytes") or {}
    if compact.get("status") == "measured":
        lines.extend(
            [
                "",
                "## Compact Vs Full Bytes",
                "",
                f"- Compact context bytes: `{compact.get('compact_context_bytes')}`",
                f"- Full-result output context bytes: `{compact.get('full_result_output_context_bytes')}`",
                f"- Compact/full ratio: `{compact.get('compact_to_full_ratio')}`",
                "- Token-savings claim supported: `False`",
            ]
        )
    lines.extend(["", "## Claim Boundaries", ""])
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Limitations", ""])
    for item in decision["limitations"]:
        lines.append(f"- {item}")
    return "\n".join(lines)
