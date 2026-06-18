from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .compact_handoff_usefulness import (
    build_live_packet_bundle,
    markdown_cell,
    no_evidence_scope_limited,
    normalize_targets,
    targets_from_next_reads,
    targets_from_source_refs,
    targets_from_windows,
    targets_present,
    unsupported_claims_false,
)


SCHEMA_VERSION = 1
DEFAULT_FIXTURE = Path("benchmarks/source_tier_answer_use/synthetic_cases.json")
REQUIRED_FIXTURE_CLASSES = [
    "answerable_with_included_sources",
    "answerable_only_if_deferred_source_read",
    "not_supported_in_available_scope",
]
CLAIM_BOUNDARIES = {
    "source_tier_answer_use_eval_only": True,
    "synthetic_public_fixture_only": True,
    "token_savings_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "broad_retrieval_quality_claim_supported": False,
    "all_history_completeness_claim_supported": False,
    "default_promotion_supported": False,
    "llm_judge_gate_used": False,
    "private_transcript_text_used": False,
}
DECISION_PRIORITY = [
    "improve_source_tier_next_read_guidance",
    "improve_scoped_no_evidence_wording",
    "improve_compact_packet_budget_fields",
    "improve_source_tier_context_packaging",
]
ALL_USEFUL_DECISION = "proceed_to_larger_source_tier_real_history_pressure_eval"


def build_source_tier_answer_use_eval_payload(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    return {"source_tier_answer_use_eval": build_source_tier_answer_use_eval(fixture_path)}


def build_source_tier_answer_use_eval(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    fixture_path = fixture_path.expanduser()
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return invalid_fixture_report(fixture_path, f"fixture_read_failed:{exc}")
    except json.JSONDecodeError as exc:
        return invalid_fixture_report(fixture_path, f"fixture_json_invalid:{exc}")

    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        return invalid_fixture_report(fixture_path, "fixture_cases_missing")

    rows = [evaluate_source_tier_case(case, fixture_path=fixture_path) for case in cases]
    status = "ok"
    if any(row["answer_use_status"] == "inconclusive" for row in rows):
        status = "inconclusive"
    if any(row["answer_use_status"] == "invalid_fixture" for row in rows):
        status = "invalid_fixture"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "data_source": "synthetic_public_committed_only",
        "fixture": str(fixture_path),
        "fixture_class_coverage": fixture_class_coverage(rows),
        "retrieval_mode_required": "fielded-bm25",
        "rows": rows,
        "summary": summarize_rows(rows),
        "selected_next_product_decision": select_next_product_decision(rows),
        "claim_boundaries": CLAIM_BOUNDARIES,
    }


def invalid_fixture_report(fixture_path: Path, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "invalid_fixture",
        "data_source": "synthetic_public_committed_only",
        "fixture": str(fixture_path),
        "fixture_class_coverage": {name: False for name in REQUIRED_FIXTURE_CLASSES},
        "retrieval_mode_required": "fielded-bm25",
        "rows": [],
        "summary": {
            "case_count": 0,
            "useful_count": 0,
            "not_useful_count": 0,
            "inconclusive_count": 0,
            "invalid_fixture_count": 1,
            "invalid_fixture_reasons": [reason],
        },
        "selected_next_product_decision": "fixture_invalid_rerun_after_repair",
        "claim_boundaries": CLAIM_BOUNDARIES,
    }


def evaluate_source_tier_case(case: dict[str, Any], *, fixture_path: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "missing_case_id")
    fixture_class = str(case.get("fixture_class") or "missing_fixture_class")
    command_surface = str(case.get("command_surface") or "")
    query = str(case.get("query") or "")
    row_base = {
        "case_id": case_id,
        "fixture_class": fixture_class,
        "question_family": str(case.get("question_family") or ""),
        "command_surface": command_surface,
        "query": query,
        "expected_answer_route": str(case.get("expected_answer_route") or ""),
        "expected_source_targets": list(case.get("expected_source_targets") or []),
    }
    invalid_reason = fixture_invalid_reason(case)
    if invalid_reason:
        return {
            **row_base,
            "answer_use_status": "invalid_fixture",
            "metrics": empty_metrics(),
            "selected_source_tier_summary": {},
            "deferred_source_tier_summary": {},
            "next_read_deferred_retry_guidance": {},
            "no_evidence_scope": {},
            "compact_budget_honesty": {},
            "failure_class": "invalid_fixture",
            "product_decision_signal": "fixture_invalid_rerun_after_repair",
            "invalid_fixture_reason": invalid_reason,
        }

    try:
        packet_bundle = build_live_packet_bundle(case, fixture_path=fixture_path)
    except Exception as exc:  # pragma: no cover - retained for report diagnostics.
        return {
            **row_base,
            "answer_use_status": "inconclusive",
            "metrics": empty_metrics(),
            "selected_source_tier_summary": {},
            "deferred_source_tier_summary": {},
            "next_read_deferred_retry_guidance": {},
            "no_evidence_scope": {},
            "compact_budget_honesty": {},
            "failure_class": "execution_error",
            "product_decision_signal": "fixture_or_eval_execution_needs_repair",
            "execution_error": exc.__class__.__name__,
        }

    observed = observe_source_tier_packet(packet_bundle, case)
    metrics = score_metrics(observed, case)
    failure_class = first_failure_class(metrics, case)
    return {
        **row_base,
        "retrieval_mode_observed": observed["retrieval_mode"],
        "packet_status": observed["packet_status"],
        "compact_status": observed["compact_status"],
        "answerability_status": observed["answerability_status"],
        "answerability_scope": observed["answerability_scope"],
        "answer_use_status": "useful" if failure_class is None else "not_useful",
        "metrics": metrics,
        "selected_source_tier_summary": observed["selected_source_tier_summary"],
        "deferred_source_tier_summary": observed["deferred_source_tier_summary"],
        "next_read_deferred_retry_guidance": observed["next_read_deferred_retry_guidance"],
        "no_evidence_scope": observed["no_evidence_scope"],
        "compact_budget_honesty": observed["compact_budget_honesty"],
        "failure_class": failure_class,
        "product_decision_signal": "none" if failure_class is None else product_decision_for_failure(failure_class),
    }


def fixture_invalid_reason(case: dict[str, Any]) -> str | None:
    if case.get("fixture_class") not in REQUIRED_FIXTURE_CLASSES:
        return "invalid_fixture_class"
    if not case.get("query") or case.get("command_surface") not in {"find", "semantic-debug"}:
        return "missing_query_or_invalid_command_surface"
    if not case.get("expected_answer_route"):
        return "missing_expected_answer_route"
    return None


def observe_source_tier_packet(packet_bundle: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    packet = packet_bundle["packet"]
    compact = packet_bundle["compact"]
    repo_root = packet_bundle["repo_root"]
    retrieval_diagnostics = packet.get("retrieval_diagnostics") or {}
    source_plan = (
        retrieval_diagnostics.get("source_plan")
        or compact.get("stage_diagnostics", {}).get("source_plan")
        or {}
    )
    source_catalog = retrieval_diagnostics.get("source_catalog") or compact.get("stage_diagnostics", {}).get("source_catalog") or {}
    source_refs = list(compact.get("source_refs") or [])
    source_windows = list(compact.get("source_windows") or [])
    next_reads = list(compact.get("next_reads") or [])
    no_evidence = compact.get("no_evidence") or {}
    evidence_sufficiency = compact.get("evidence_sufficiency") if isinstance(compact.get("evidence_sufficiency"), dict) else {}
    claim_boundaries = compact.get("claim_boundaries") or {}
    expected_targets = [str(item) for item in case.get("expected_source_targets") or []]
    source_ref_targets = normalize_targets(targets_from_source_refs(source_refs), repo_root=repo_root)
    window_targets = normalize_targets(targets_from_windows(source_windows), repo_root=repo_root)
    next_read_targets = normalize_targets(targets_from_next_reads(next_reads), repo_root=repo_root)
    return {
        "retrieval_mode": retrieval_diagnostics.get("retrieval_mode"),
        "packet_status": packet.get("status"),
        "compact_status": compact.get("status"),
        "answerability_status": str(compact.get("answerability_status") or evidence_sufficiency.get("support_class") or ""),
        "evidence_sufficiency": evidence_sufficiency,
        "expected_source_refs_present": targets_present(expected_targets, source_ref_targets),
        "expected_source_windows_present": targets_present(expected_targets, window_targets),
        "expected_next_reads_present": targets_present(expected_targets, next_read_targets),
        "selected_source_tier_summary": selected_source_tier_summary(source_catalog, source_plan),
        "deferred_source_tier_summary": deferred_source_tier_summary(source_plan),
        "next_read_deferred_retry_guidance": next_read_deferred_retry_guidance(no_evidence, source_plan),
        "no_evidence_scope": no_evidence_scope(no_evidence, claim_boundaries),
        "answerability_scope": answerability_scope(evidence_sufficiency),
        "compact_budget_honesty": compact_budget_honesty(compact, source_plan),
        "claim_boundaries_honest": unsupported_claims_false(claim_boundaries),
    }


def selected_source_tier_summary(source_catalog: dict[str, Any], source_plan: dict[str, Any]) -> dict[str, Any]:
    source_tiers = source_plan.get("source_tiers") if isinstance(source_plan.get("source_tiers"), dict) else {}
    return {
        "source_catalog_present": bool(source_catalog),
        "source_catalog_file_count": int(source_catalog.get("file_count") or 0),
        "source_plan_present": bool(source_plan),
        "sources_total": int(source_plan.get("sources_total") or 0),
        "sources_selected": int(source_plan.get("sources_selected") or 0),
        "sources_pruned_hard": int(source_plan.get("sources_pruned_hard") or 0),
        "tier_order": [str(item) for item in source_plan.get("tier_order") or []],
        "selected_counts": sanitize_counts(source_tiers.get("selected_counts") if isinstance(source_tiers, dict) else {}),
        "raw_paths_included": bool(source_plan.get("raw_paths_included", False) or source_catalog.get("raw_paths_included", False)),
        "raw_text_included": bool(source_plan.get("raw_text_included", False) or source_catalog.get("raw_text_included", False)),
    }


def deferred_source_tier_summary(source_plan: dict[str, Any]) -> dict[str, Any]:
    source_tiers = source_plan.get("source_tiers") if isinstance(source_plan.get("source_tiers"), dict) else {}
    retry_guidance = source_plan.get("retry_guidance") if isinstance(source_plan.get("retry_guidance"), dict) else {}
    return {
        "sources_deferred_budget": int(source_plan.get("sources_deferred_budget") or 0),
        "deferred_counts": sanitize_counts(source_tiers.get("deferred_counts") if isinstance(source_tiers, dict) else {}),
        "deferred_reason_counts": sanitize_counts(source_plan.get("deferred_reason_counts")),
        "retry_needed": bool(retry_guidance.get("needed", False)),
        "retry_deferred_source_count": int(retry_guidance.get("deferred_source_count") or 0),
        "retry_guidance": str(retry_guidance.get("guidance") or ""),
        "exhaustive_available": bool(source_plan.get("exhaustive_available", False)),
    }


def next_read_deferred_retry_guidance(no_evidence: dict[str, Any], source_plan: dict[str, Any]) -> dict[str, Any]:
    retry = source_plan.get("retry_guidance") if isinstance(source_plan.get("retry_guidance"), dict) else {}
    no_evidence_retry = no_evidence.get("retry_guidance") if isinstance(no_evidence.get("retry_guidance"), dict) else {}
    return {
        "retry_guidance_present": bool(retry.get("guidance") or no_evidence_retry.get("guidance")),
        "retry_needed": bool(retry.get("needed", False) or no_evidence_retry.get("needed", False)),
        "deferred_source_count": int(retry.get("deferred_source_count") or no_evidence.get("deferred_source_count") or 0),
        "guidance": str(no_evidence_retry.get("guidance") or retry.get("guidance") or ""),
    }


def no_evidence_scope(no_evidence: dict[str, Any], claim_boundaries: dict[str, Any]) -> dict[str, Any]:
    return {
        "present": bool(no_evidence),
        "answer_scope": no_evidence.get("answer_scope"),
        "not_all_history_absence": bool(no_evidence.get("not_all_history_absence", False)),
        "scope_limited": no_evidence_scope_limited(no_evidence, claim_boundaries),
        "deferred_source_count": int(no_evidence.get("deferred_source_count") or 0),
    }


def answerability_scope(evidence_sufficiency: dict[str, Any]) -> dict[str, Any]:
    no_evidence_boundary = (
        evidence_sufficiency.get("no_evidence_boundary")
        if isinstance(evidence_sufficiency.get("no_evidence_boundary"), dict)
        else {}
    )
    searched_scope_support = (
        evidence_sufficiency.get("searched_scope_support")
        if isinstance(evidence_sufficiency.get("searched_scope_support"), dict)
        else {}
    )
    return {
        "support_class": str(evidence_sufficiency.get("support_class") or ""),
        "answerable_from_packet": bool(evidence_sufficiency.get("answerable_from_packet", False)),
        "candidate_presence_implies_answerability": bool(
            evidence_sufficiency.get("candidate_presence_implies_answerability", True)
        ),
        "no_evidence_needed": bool(no_evidence_boundary.get("needed", False)),
        "no_evidence_answer_scope": no_evidence_boundary.get("answer_scope"),
        "not_all_history_absence": bool(no_evidence_boundary.get("not_all_history_absence", False)),
        "strong_candidate_count": int(searched_scope_support.get("strong_candidate_count") or 0),
        "weak_or_related_candidate_count": int(searched_scope_support.get("weak_or_related_candidate_count") or 0),
    }


def compact_budget_honesty(compact: dict[str, Any], source_plan: dict[str, Any]) -> dict[str, Any]:
    packaging = compact.get("source_window_packaging") if isinstance(compact.get("source_window_packaging"), dict) else {}
    budget_basis = source_plan.get("budget_basis") if isinstance(source_plan.get("budget_basis"), dict) else {}
    return {
        "source_window_packaging_present": bool(packaging),
        "source_window_count": int(packaging.get("included_count") or len(compact.get("source_windows") or [])),
        "source_window_omitted_count": int(packaging.get("omitted_count") or 0),
        "budget_basis_present": bool(budget_basis),
        "max_selected_files": int(budget_basis.get("max_selected_files") or 0),
        "max_selected_bytes": int(budget_basis.get("max_selected_bytes") or 0),
        "uses_scoring_weights": bool(budget_basis.get("uses_scoring_weights", False)),
    }


def score_metrics(observed: dict[str, Any], case: dict[str, Any]) -> dict[str, bool]:
    fixture_class = case.get("fixture_class")
    route = case.get("expected_answer_route")
    deferred_count = observed["deferred_source_tier_summary"]["sources_deferred_budget"]
    retry_present = observed["next_read_deferred_retry_guidance"]["retry_guidance_present"]
    if fixture_class == "answerable_with_included_sources":
        context_usefulness = observed["expected_source_refs_present"] and observed["expected_source_windows_present"]
        groundedness = (
            observed["answerability_status"] == "supported_in_searched_scope"
            and observed["expected_source_refs_present"]
            and observed["claim_boundaries_honest"]
        )
        answer_relevance = (
            route == "answer_from_included_sources"
            and observed["answerability_status"] == "supported_in_searched_scope"
        )
        scoped_no_evidence = True
        next_read_quality = observed["expected_next_reads_present"]
    elif fixture_class == "answerable_only_if_deferred_source_read":
        context_usefulness = deferred_count > 0 and retry_present
        groundedness = (
            observed["answerability_status"] == "deferred_source_needed"
            and observed["claim_boundaries_honest"]
            and observed["answerability_scope"]["candidate_presence_implies_answerability"] is False
        )
        answer_relevance = route == "deferred_source_retry" and observed["answerability_status"] == "deferred_source_needed"
        scoped_no_evidence = True
        next_read_quality = observed["deferred_source_tier_summary"]["retry_needed"] and retry_present
    else:
        context_usefulness = observed["answerability_status"] in {
            "searched_scope_no_evidence",
            "insufficient_grounding",
        }
        groundedness = observed["claim_boundaries_honest"]
        answer_relevance = route == "scoped_no_evidence" and observed["answerability_status"] in {
            "searched_scope_no_evidence",
            "insufficient_grounding",
        }
        scoped_no_evidence = (
            observed["answerability_scope"]["support_class"] == "searched_scope_no_evidence"
            and observed["answerability_scope"]["no_evidence_needed"]
            and observed["answerability_scope"]["not_all_history_absence"]
        )
        next_read_quality = retry_present if deferred_count > 0 else True
    budget = observed["compact_budget_honesty"]
    selected = observed["selected_source_tier_summary"]
    budget_honesty = (
        observed["retrieval_mode"] == "fielded-bm25"
        and selected["source_catalog_present"]
        and selected["source_plan_present"]
        and selected["sources_total"] >= selected["sources_selected"] >= 0
        and budget["source_window_packaging_present"]
        and budget["budget_basis_present"]
        and budget["uses_scoring_weights"] is False
        and selected["raw_paths_included"] is False
        and selected["raw_text_included"] is False
    )
    return {
        "context_usefulness": context_usefulness,
        "groundedness": groundedness,
        "answer_relevance": answer_relevance,
        "scoped_no_evidence": scoped_no_evidence,
        "next_read_quality": next_read_quality,
        "budget_honesty": budget_honesty,
    }


def first_failure_class(metrics: dict[str, bool], case: dict[str, Any]) -> str | None:
    if not metrics["budget_honesty"]:
        return "budget_honesty_missing"
    if case.get("fixture_class") == "not_supported_in_available_scope" and not metrics["scoped_no_evidence"]:
        return "scoped_no_evidence_missing"
    if not metrics["context_usefulness"]:
        if case.get("fixture_class") == "answerable_only_if_deferred_source_read":
            return "deferred_source_not_identified"
        return "included_context_not_useful"
    if not metrics["groundedness"]:
        return "grounding_boundary_missing"
    if not metrics["answer_relevance"]:
        return "answer_route_not_relevant"
    if not metrics["scoped_no_evidence"]:
        return "scoped_no_evidence_missing"
    if not metrics["next_read_quality"]:
        return "next_read_guidance_missing"
    return None


def product_decision_for_failure(failure_class: str) -> str:
    if failure_class in {"deferred_source_not_identified", "next_read_guidance_missing"}:
        return "improve_source_tier_next_read_guidance"
    if failure_class == "scoped_no_evidence_missing":
        return "improve_scoped_no_evidence_wording"
    if failure_class == "budget_honesty_missing":
        return "improve_compact_packet_budget_fields"
    return "improve_source_tier_context_packaging"


def empty_metrics() -> dict[str, bool]:
    return {
        "context_usefulness": False,
        "groundedness": False,
        "answer_relevance": False,
        "scoped_no_evidence": False,
        "next_read_quality": False,
        "budget_honesty": False,
    }


def sanitize_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count or 0) for key, count in sorted(value.items())}


def fixture_class_coverage(rows: list[dict[str, Any]]) -> dict[str, bool]:
    classes = {row.get("fixture_class") for row in rows}
    return {name: name in classes for name in REQUIRED_FIXTURE_CLASSES}


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(row["answer_use_status"] for row in rows)
    decision_counts = Counter(row["product_decision_signal"] for row in rows if row["product_decision_signal"] != "none")
    metric_pass_counts = Counter(
        metric
        for row in rows
        for metric, passed in row.get("metrics", {}).items()
        if passed is True
    )
    failure_counts = Counter(row["failure_class"] for row in rows if row.get("failure_class"))
    class_counts = Counter(row["fixture_class"] for row in rows)
    return {
        "case_count": len(rows),
        "useful_count": status_counts.get("useful", 0),
        "not_useful_count": status_counts.get("not_useful", 0),
        "inconclusive_count": status_counts.get("inconclusive", 0),
        "invalid_fixture_count": status_counts.get("invalid_fixture", 0),
        "answer_use_status_counts": dict(sorted(status_counts.items())),
        "metric_pass_counts": dict(sorted(metric_pass_counts.items())),
        "failure_class_counts": dict(sorted(failure_counts.items())),
        "fixture_class_counts": dict(sorted(class_counts.items())),
        "product_decision_signal_counts": dict(sorted(decision_counts.items())),
    }


def select_next_product_decision(rows: list[dict[str, Any]]) -> str:
    signals = {row.get("product_decision_signal") for row in rows}
    for decision in DECISION_PRIORITY:
        if decision in signals:
            return decision
    if any(row.get("answer_use_status") in {"inconclusive", "invalid_fixture"} for row in rows):
        return "fixture_or_eval_execution_needs_repair"
    return ALL_USEFUL_DECISION


def render_source_tier_answer_use_markdown(payload: dict[str, Any]) -> str:
    report = payload["source_tier_answer_use_eval"]
    summary = report["summary"]
    lines = [
        "# Source Tier Answer-Use Eval",
        "",
        f"- Status: `{report['status']}`",
        f"- Data source: `{report['data_source']}`",
        f"- Retrieval mode: `{report['retrieval_mode_required']}`",
        f"- Selected next product decision: `{report['selected_next_product_decision']}`",
        "",
        "## Summary",
    ]
    for key in ("case_count", "useful_count", "not_useful_count", "inconclusive_count", "invalid_fixture_count"):
        lines.append(f"- {key}: `{summary.get(key, 0)}`")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Case | Fixture Class | Surface | Status | Answerability | Failure Class | Selected/Deferred | Next Read | No-Evidence Scope | Budget | Decision Signal |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        selected = row.get("selected_source_tier_summary", {})
        deferred = row.get("deferred_source_tier_summary", {})
        next_read = row.get("next_read_deferred_retry_guidance", {})
        no_evidence = row.get("no_evidence_scope", {})
        answerability = row.get("answerability_scope", {})
        budget = row.get("compact_budget_honesty", {})
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["case_id"]),
                    markdown_cell(row["fixture_class"]),
                    markdown_cell(row["command_surface"]),
                    markdown_cell(row["answer_use_status"]),
                    markdown_cell(row.get("answerability_status") or "missing"),
                    markdown_cell(row["failure_class"] or "none"),
                    markdown_cell(f"{selected.get('sources_selected', 0)}/{deferred.get('sources_deferred_budget', 0)}"),
                    markdown_cell(
                        f"needed={next_read.get('retry_needed', False)}, deferred={next_read.get('deferred_source_count', 0)}"
                    ),
                    markdown_cell(
                        f"present={no_evidence.get('present', False)}, scoped={no_evidence.get('scope_limited', False)}, answerability_no_evidence={answerability.get('no_evidence_needed', False)}"
                    ),
                    markdown_cell(
                        f"windows={budget.get('source_window_count', 0)}, honest={row.get('metrics', {}).get('budget_honesty', False)}"
                    ),
                    markdown_cell(row["product_decision_signal"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Metric Pass Counts"])
    for key, value in summary.get("metric_pass_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Claim Boundaries"])
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)
