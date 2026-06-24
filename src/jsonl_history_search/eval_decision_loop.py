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


def build_eval_decision_loop_payload(pressure_report: Path | None = None) -> dict[str, Any]:
    deterministic = _build_deterministic_eval_summaries()
    pressure = _load_pressure_summary(pressure_report)
    decision = _build_decision(deterministic, pressure)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": decision["decision_status"],
        "evidence_basis": {
            "deterministic_eval_builders_reused_in_process": True,
            "pressure_report_supplied": pressure_report is not None,
            "pressure_report_loaded": pressure.get("loaded", False),
            "non_live_eval_synthesis_only": True,
        },
        "deterministic_evals": deterministic,
        "pressure_report": pressure,
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


def _build_decision(deterministic: dict[str, Any], pressure: dict[str, Any]) -> dict[str, Any]:
    deterministic_status = deterministic["status"]
    pressure_status = pressure["status"]
    bottleneck = _dominant_bottleneck(pressure)
    target = _recommended_target(pressure, bottleneck)
    limitations = _limitations(deterministic_status, pressure)
    decision_status = "ok" if deterministic_status == "passing" and pressure_status == "evaluated" else "inconclusive"
    return {
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
    for name, item in deterministic["evals"].items():
        lines.append(
            f"- `{name}`: status `{item['status']}`, cases `{item['case_count']}`, "
            f"positive `{item['positive_count']}`, next `{item['selected_next_product_decision']}`"
        )
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
