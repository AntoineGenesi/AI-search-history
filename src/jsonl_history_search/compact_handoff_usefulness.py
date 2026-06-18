from __future__ import annotations

import json
from collections import Counter
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .agent_contract import build_agent_find_payload, source_ref_target
from .query import infer_query
from .retrieval_modes import RETRIEVAL_MODE_FIELDED_BM25, run_retrieval_mode
from .semantic_debugging import DEFAULT_GOLD_MANIFEST, build_semantic_debug_payload
from .sources import Limits, build_source_plan
from .timeutil import parse_now


SCHEMA_VERSION = 1
DEFAULT_FIXTURE = Path("benchmarks/compact_handoff_usefulness/synthetic_cases.json")
DEFAULT_ANSWER_USE_FIXTURE = Path("benchmarks/compact_handoff_usefulness/approved_safe_answer_use_cases.json")
REQUIRED_QUESTION_FAMILIES = [
    "why",
    "workflow_failure",
    "when_user_said",
    "what_was_i_talking_about",
]
CLAIM_BOUNDARIES = {
    "packet_usefulness_only": True,
    "token_savings_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "retrieval_quality_claim_supported": False,
    "all_history_completeness_claim_supported": False,
    "default_promotion_supported": False,
    "private_transcript_text_used": False,
}
DECISION_BY_FAILURE_STAGE = {
    "query_planning": "improve_query_planning",
    "source_window_selection": "improve_source_window_selection",
    "source_planning_indexing": "improve_source_planning_indexing",
    "compact_packet_fields": "improve_compact_packet_fields",
    "no_evidence_behavior": "improve_no_evidence_behavior",
}
DECISION_PRIORITY = [
    "improve_query_planning",
    "improve_source_window_selection",
    "improve_source_planning_indexing",
    "improve_compact_packet_fields",
    "improve_no_evidence_behavior",
]
ALL_USEFUL_DECISION = "evaluate_reviewer_agent_answer_use_on_approved_safe_cases"
ANSWER_USE_CLAIM_BOUNDARIES = {
    "answer_use_actionability_only": True,
    "token_savings_claim_supported": False,
    "final_answer_quality_claim_supported": False,
    "broad_retrieval_quality_claim_supported": False,
    "all_history_completeness_claim_supported": False,
    "default_promotion_supported": False,
    "private_transcript_text_used": False,
}
ALLOWED_ROUTES = {
    "packet_only_answer",
    "packet_plus_source_fetch",
    "large_history_source_planning_followup",
    "correct_abstain",
}
ALLOWED_SUPPORT_LABELS = {
    "supported",
    "contradicted",
    "unsupported",
    "needs_source_fetch",
    "correct_abstain",
}
DECISION_BY_ANSWER_USE_FAILURE = {
    "packet_missing_evidence": "improve_source_planning_indexing",
    "packet_ref_too_coarse": "improve_source_window_selection",
    "answer_ignored_evidence": "improve_compact_packet_fields_or_answer_guidance",
    "wrong_inference": "improve_compact_packet_fields_or_answer_guidance",
    "timeline_confusion": "improve_query_planning",
    "entity_alias_miss": "improve_query_planning",
    "needs_large_history_search": "build_large_history_source_planning_candidate_generation",
    "unsafe_private_quote_risk": "improve_privacy_boundaries",
}
ANSWER_USE_DECISION_PRIORITY = [
    "improve_privacy_boundaries",
    "improve_source_planning_indexing",
    "improve_source_window_selection",
    "improve_query_planning",
    "improve_compact_packet_fields_or_answer_guidance",
    "build_large_history_source_planning_candidate_generation",
]
ALL_ANSWER_USE_ACTIONABLE_DECISION = "plan_large_history_source_planning_candidate_generation"


def build_compact_handoff_usefulness_eval_payload(
    fixture_path: Path = DEFAULT_FIXTURE,
) -> dict[str, Any]:
    report = build_compact_handoff_usefulness_eval(fixture_path)
    return {"compact_handoff_usefulness_eval": report}


def build_compact_handoff_answer_use_eval_payload(
    fixture_path: Path = DEFAULT_ANSWER_USE_FIXTURE,
) -> dict[str, Any]:
    report = build_compact_handoff_answer_use_eval(fixture_path)
    return {"compact_handoff_answer_use_eval": report}


def build_compact_handoff_usefulness_eval(fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
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

    rows: list[dict[str, Any]] = []
    for case in cases:
        rows.append(evaluate_case(case, fixture_path=fixture_path))

    coverage = question_family_coverage(rows)
    status = "ok"
    if any(row["usefulness_status"] == "inconclusive" for row in rows):
        status = "inconclusive"
    if any(row["usefulness_status"] == "invalid_fixture" for row in rows):
        status = "invalid_fixture"

    summary = summarize_rows(rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "data_source": "synthetic_committed_only",
        "fixture": str(fixture_path),
        "question_family_coverage": coverage,
        "rows": rows,
        "summary": summary,
        "selected_next_product_decision": select_next_product_decision(rows),
        "claim_boundaries": CLAIM_BOUNDARIES,
    }


def build_compact_handoff_answer_use_eval(fixture_path: Path = DEFAULT_ANSWER_USE_FIXTURE) -> dict[str, Any]:
    fixture_path = fixture_path.expanduser()
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return invalid_answer_use_fixture_report(fixture_path, f"fixture_read_failed:{exc}")
    except json.JSONDecodeError as exc:
        return invalid_answer_use_fixture_report(fixture_path, f"fixture_json_invalid:{exc}")

    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases:
        return invalid_answer_use_fixture_report(fixture_path, "fixture_cases_missing")

    rows = [evaluate_answer_use_case(case, fixture_path=fixture_path) for case in cases]
    coverage = question_family_coverage(rows)
    status = "ok"
    if any(row["answer_use_status"] == "inconclusive" for row in rows):
        status = "inconclusive"
    if any(row["answer_use_status"] == "invalid_fixture" for row in rows):
        status = "invalid_fixture"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "data_source": "approved_safe_committed_only",
        "fixture": str(fixture_path),
        "question_family_coverage": coverage,
        "rows": rows,
        "summary": summarize_answer_use_rows(rows),
        "selected_next_product_decision": select_answer_use_next_product_decision(rows),
        "claim_boundaries": ANSWER_USE_CLAIM_BOUNDARIES,
    }


def invalid_fixture_report(fixture_path: Path, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "invalid_fixture",
        "data_source": "synthetic_committed_only",
        "fixture": str(fixture_path),
        "question_family_coverage": {family: False for family in REQUIRED_QUESTION_FAMILIES},
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


def invalid_answer_use_fixture_report(fixture_path: Path, reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "invalid_fixture",
        "data_source": "approved_safe_committed_only",
        "fixture": str(fixture_path),
        "question_family_coverage": {family: False for family in REQUIRED_QUESTION_FAMILIES},
        "rows": [],
        "summary": {
            "case_count": 0,
            "actionable_count": 0,
            "not_actionable_count": 0,
            "inconclusive_count": 0,
            "invalid_fixture_count": 1,
            "invalid_fixture_reasons": [reason],
        },
        "selected_next_product_decision": "fixture_invalid_rerun_after_repair",
        "claim_boundaries": ANSWER_USE_CLAIM_BOUNDARIES,
    }


def evaluate_case(case: dict[str, Any], *, fixture_path: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "missing_case_id")
    question_family = str(case.get("question_family") or "missing_question_family")
    command_surface = str(case.get("command_surface") or "")
    expected = case.get("expected_packet_contract") or {}
    row_base = {
        "case_id": case_id,
        "question_family": question_family,
        "command_surface": command_surface,
        "expected_packet_contract": sanitize_expected_contract(expected),
    }
    if not case.get("query") or command_surface not in {"find", "semantic-debug"}:
        return {
            **row_base,
            "observed_packet_contract": {"case_executed": False},
            "usefulness_status": "invalid_fixture",
            "failure_stage": "invalid_fixture",
            "product_decision_signal": "fixture_invalid_rerun_after_repair",
        }

    try:
        packet_bundle = build_live_packet_bundle(case, fixture_path=fixture_path)
    except Exception as exc:  # pragma: no cover - retained as fixture diagnostics.
        return {
            **row_base,
            "observed_packet_contract": {"case_executed": False, "error": exc.__class__.__name__},
            "usefulness_status": "inconclusive",
            "failure_stage": "execution_error",
            "product_decision_signal": "fixture_or_eval_execution_needs_repair",
        }

    observed = observe_packet_contract(packet_bundle, expected)
    failure_stage = first_failure_stage(observed, expected)
    usefulness_status = "useful" if failure_stage is None else "not_useful"
    return {
        **row_base,
        "observed_packet_contract": observed,
        "usefulness_status": usefulness_status,
        "failure_stage": failure_stage,
        "product_decision_signal": DECISION_BY_FAILURE_STAGE.get(failure_stage, "none"),
    }


def evaluate_answer_use_case(case: dict[str, Any], *, fixture_path: Path) -> dict[str, Any]:
    case_id = str(case.get("case_id") or "missing_case_id")
    question_family = str(case.get("question_family") or "missing_question_family")
    command_surface = str(case.get("command_surface") or "")
    query = str(case.get("query") or "")
    route_expected = str(case.get("route_expected") or "")
    row_base = {
        "case_id": case_id,
        "question_family": question_family,
        "command_surface": command_surface,
        "query": query,
        "route_expected": route_expected,
        "claims": [],
        "source_to_open_expected": list(case.get("source_to_open_expected") or []),
    }
    invalid_reason = answer_use_fixture_invalid_reason(case)
    if invalid_reason:
        return {
            **row_base,
            "route_observed": "invalid_fixture",
            "route_accuracy": False,
            "answer_use_status": "invalid_fixture",
            "source_to_open_observed": [],
            "answer_basis_identified": False,
            "evidence_boundary_identified": False,
            "failure_class": "invalid_fixture",
            "product_decision_signal": "fixture_invalid_rerun_after_repair",
            "invalid_fixture_reason": invalid_reason,
        }

    try:
        packet_bundle = build_live_packet_bundle(case, fixture_path=fixture_path)
    except Exception as exc:  # pragma: no cover - retained as fixture diagnostics.
        return {
            **row_base,
            "route_observed": "execution_error",
            "route_accuracy": False,
            "answer_use_status": "inconclusive",
            "source_to_open_observed": [],
            "answer_basis_identified": False,
            "evidence_boundary_identified": False,
            "failure_class": "execution_error",
            "product_decision_signal": "fixture_or_eval_execution_needs_repair",
            "execution_error": exc.__class__.__name__,
        }

    observed = observe_answer_use(packet_bundle, case)
    failure_class = first_answer_use_failure_class(observed, case)
    answer_use_status = "actionable" if failure_class is None else "not_actionable"
    product_decision_signal = (
        "none" if failure_class is None else DECISION_BY_ANSWER_USE_FAILURE.get(failure_class, "fixture_or_eval_execution_needs_repair")
    )
    return {
        **row_base,
        "route_observed": observed["route_observed"],
        "route_accuracy": observed["route_accuracy"],
        "answer_use_status": answer_use_status,
        "claims": observed["claims"],
        "source_to_open_observed": observed["source_to_open_observed"],
        "answer_basis_identified": observed["answer_basis_identified"],
        "evidence_boundary_identified": observed["evidence_boundary_identified"],
        "failure_class": failure_class,
        "product_decision_signal": product_decision_signal,
    }


def answer_use_fixture_invalid_reason(case: dict[str, Any]) -> str | None:
    if not case.get("query") or case.get("command_surface") not in {"find", "semantic-debug"}:
        return "missing_query_or_invalid_command_surface"
    if case.get("route_expected") not in ALLOWED_ROUTES:
        return "invalid_route_expected"
    claims = case.get("claims")
    if not isinstance(claims, list) or not claims:
        return "claims_missing"
    for claim in claims:
        if claim.get("expected_support_label") not in ALLOWED_SUPPORT_LABELS:
            return "invalid_expected_support_label"
    return None


def build_live_packet_bundle(case: dict[str, Any], *, fixture_path: Path) -> dict[str, Any]:
    root_dir = fixture_path.resolve().parents[2]
    cwd = resolve_fixture_path(root_dir, str(case["cwd"]))
    root = resolve_fixture_path(root_dir, str(case["root"]))
    synthetic_codex_home = root_dir / "tests" / "fixtures" / "compact_handoff_usefulness" / "synthetic_codex_home"
    synthetic_claude_home = root_dir / "tests" / "fixtures" / "compact_handoff_usefulness" / "synthetic_claude_home"
    query = str(case["query"])
    now = parse_now(str(case.get("now") or "2026-06-19T00:00:00+00:00"), timezone.utc)
    limits = Limits(max_files=20, max_bytes=200_000, max_matches=20, max_elapsed=15.0, hard_cap_files=20, hard_cap_bytes=200_000)
    intent = infer_query(query, now=now, role=case.get("role"))
    plan = build_source_plan(
        cwd=cwd,
        codex_home=synthetic_codex_home,
        claude_home=synthetic_claude_home,
        roots=[root],
        allow_arbitrary_root=True,
        limits=limits,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(synthetic_codex_home),
        "claude_home": str(synthetic_claude_home),
        "now": now.isoformat(),
    }
    if case["command_surface"] == "find":
        result = run_retrieval_mode(
            retrieval_mode=RETRIEVAL_MODE_FIELDED_BM25,
            plan=plan,
            intent=intent,
            default_tz=timezone.utc,
            cwd=str(cwd),
            mask_context=None,
            use_rg_prefilter=False,
        )
        result["warnings"] = sorted(set(result.get("warnings", []) + intent.warnings))
        payload = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            output_format="json",
            report_output=None,
            compact_provenance_handoff=True,
        )
        packet = payload["agent_find"]
    else:
        args = SimpleNamespace(
            query=query,
            retrieval_mode=RETRIEVAL_MODE_FIELDED_BM25,
            rg_prefilter=False,
            format="json",
            report_output=None,
            compact_provenance_handoff=True,
            path=None,
            task=None,
            commit=None,
            branch=None,
            around=None,
            gold_manifest=str(DEFAULT_GOLD_MANIFEST),
        )
        payload = build_semantic_debug_payload(
            args,
            source_plan=source_plan,
            intent=intent,
            plan=plan,
            default_tz=timezone.utc,
            mask_context=None,
            configuration_warnings=[],
        )
        packet = payload["semantic_debug"]
    return {
        "packet": packet,
        "compact": packet.get("compact_provenance_handoff") or {},
        "locator": packet.get("source_locator_handoff") or {},
        "repo_root": root_dir,
    }


def observe_answer_use(packet_bundle: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    compact = packet_bundle["compact"]
    repo_root = packet_bundle["repo_root"]
    source_refs = list(compact.get("source_refs") or [])
    source_windows = list(compact.get("source_windows") or [])
    next_reads = list(compact.get("next_reads") or [])
    no_evidence = compact.get("no_evidence") or {}
    claim_boundaries = compact.get("claim_boundaries") or {}
    source_ref_targets = normalize_targets(targets_from_source_refs(source_refs), repo_root=repo_root)
    window_targets = normalize_targets(targets_from_windows(source_windows), repo_root=repo_root)
    next_read_targets = normalize_targets(targets_from_next_reads(next_reads), repo_root=repo_root)
    open_targets = sorted(set(window_targets + next_read_targets))
    observed_claims = [
        observe_answer_use_claim(claim, source_ref_targets, window_targets, next_read_targets, no_evidence, claim_boundaries)
        for claim in case.get("claims", [])
    ]
    route_observed = observe_route(observed_claims, no_evidence, claim_boundaries)
    expected_open_targets = [str(item) for item in case.get("source_to_open_expected") or []]
    answer_basis_identified = all(claim["observed_support_label"] == claim["expected_support_label"] for claim in observed_claims)
    source_to_open_identified = targets_present(expected_open_targets, open_targets) if expected_open_targets else True
    evidence_boundary_identified = answer_use_boundary_identified(case, no_evidence, claim_boundaries, observed_claims)
    return {
        "route_observed": route_observed,
        "route_accuracy": route_observed == case.get("route_expected"),
        "claims": observed_claims,
        "source_to_open_observed": open_targets,
        "answer_basis_identified": answer_basis_identified and source_to_open_identified,
        "evidence_boundary_identified": evidence_boundary_identified,
    }


def observe_answer_use_claim(
    claim: dict[str, Any],
    source_ref_targets: list[str],
    window_targets: list[str],
    next_read_targets: list[str],
    no_evidence: dict[str, Any],
    claim_boundaries: dict[str, Any],
) -> dict[str, Any]:
    expected_targets = [str(item) for item in claim.get("source_ref_targets") or []]
    in_refs = targets_present(expected_targets, source_ref_targets)
    in_windows = targets_present(expected_targets, window_targets)
    in_next_reads = targets_present(expected_targets, next_read_targets)
    expected_support_label = str(claim.get("expected_support_label"))
    if expected_support_label == "correct_abstain":
        observed_support_label = "correct_abstain" if no_evidence_scope_limited(no_evidence, claim_boundaries) else "unsupported"
        support_basis = "searched_scope_boundary" if observed_support_label == "correct_abstain" else "missing_scope_boundary"
    elif in_refs and (in_windows or in_next_reads):
        observed_support_label = "needs_source_fetch"
        support_basis = "source_ref_and_open_target_present"
    elif in_refs:
        observed_support_label = "needs_source_fetch"
        support_basis = "source_ref_present_without_prioritized_open_target"
    else:
        observed_support_label = "unsupported"
        support_basis = "expected_source_ref_absent"
    return {
        "claim_id": str(claim.get("claim_id") or "missing_claim_id"),
        "claim_label": str(claim.get("claim_label") or claim.get("claim_text") or ""),
        "expected_support_label": expected_support_label,
        "observed_support_label": observed_support_label,
        "source_ref_targets": expected_targets,
        "support_basis": support_basis,
    }


def observe_route(observed_claims: list[dict[str, Any]], no_evidence: dict[str, Any], claim_boundaries: dict[str, Any]) -> str:
    labels = {claim.get("observed_support_label") for claim in observed_claims}
    if labels == {"correct_abstain"} and no_evidence_scope_limited(no_evidence, claim_boundaries):
        return "correct_abstain"
    if "unsupported" in labels or "contradicted" in labels:
        return "large_history_source_planning_followup"
    if labels == {"supported"}:
        return "packet_only_answer"
    return "packet_plus_source_fetch"


def answer_use_boundary_identified(
    case: dict[str, Any],
    no_evidence: dict[str, Any],
    claim_boundaries: dict[str, Any],
    observed_claims: list[dict[str, Any]],
) -> bool:
    if case.get("route_expected") == "correct_abstain":
        return all(claim.get("observed_support_label") == "correct_abstain" for claim in observed_claims) and no_evidence_scope_limited(
            no_evidence, claim_boundaries
        )
    return unsupported_claims_false(claim_boundaries) and no_evidence.get("not_all_history_absence") is True


def first_answer_use_failure_class(observed: dict[str, Any], case: dict[str, Any]) -> str | None:
    if not observed["route_accuracy"]:
        expected_route = case.get("route_expected")
        if expected_route == "large_history_source_planning_followup":
            return "needs_large_history_search"
        if case.get("question_family") == "when_user_said":
            return "timeline_confusion"
        return "wrong_inference"
    if not observed["answer_basis_identified"]:
        if any(claim.get("observed_support_label") == "unsupported" for claim in observed["claims"]):
            return "packet_missing_evidence"
        return "packet_ref_too_coarse"
    if not observed["evidence_boundary_identified"]:
        return "answer_ignored_evidence"
    return None


def resolve_fixture_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def observe_packet_contract(packet_bundle: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    packet = packet_bundle["packet"]
    compact = packet_bundle["compact"]
    locator = packet_bundle["locator"]
    repo_root = packet_bundle["repo_root"]
    query_plan = locator.get("query_plan") or {}
    source_windows = list(compact.get("source_windows") or [])
    source_refs = list(compact.get("source_refs") or [])
    next_reads = list(compact.get("next_reads") or [])
    expected_targets = [str(item) for item in expected.get("expected_source_targets") or []]
    source_ref_targets = normalize_targets(targets_from_source_refs(source_refs), repo_root=repo_root)
    window_targets = normalize_targets(targets_from_windows(source_windows), repo_root=repo_root)
    next_read_targets = normalize_targets(targets_from_next_reads(next_reads), repo_root=repo_root)
    expected_in_source_refs = targets_present(expected_targets, source_ref_targets)
    expected_in_windows = targets_present(expected_targets, window_targets)
    expected_in_next_reads = targets_present(expected_targets, next_read_targets)
    stage_diagnostics = compact.get("stage_diagnostics") or locator.get("stage_diagnostics") or {}
    no_evidence = compact.get("no_evidence") or {}
    claim_boundaries = compact.get("claim_boundaries") or {}

    return {
        "case_executed": True,
        "status": packet.get("status"),
        "compact_status": compact.get("status"),
        "query_plan_primary_intent": query_plan.get("primary_intent"),
        "query_plan_primary_intent_matches": query_plan.get("primary_intent") == expected.get("primary_intent"),
        "source_window_needs": list(query_plan.get("source_window_needs") or []),
        "source_window_needs_match": set(expected.get("source_window_needs") or []).issubset(set(query_plan.get("source_window_needs") or [])),
        "source_ref_count": len(source_refs),
        "source_window_count": len(source_windows),
        "next_read_count": len(next_reads),
        "expected_targets": expected_targets,
        "source_ref_targets": source_ref_targets,
        "source_window_targets": window_targets,
        "next_read_targets": next_read_targets,
        "expected_source_refs_present": expected_in_source_refs,
        "expected_source_windows_present": expected_in_windows,
        "expected_next_reads_present": expected_in_next_reads,
        "source_windows_metadata_only": source_windows_metadata_only(source_windows),
        "source_windows_preserve_metadata": source_windows_preserve_metadata(source_windows),
        "stage_diagnostics_fielded_bm25_present": stage_diagnostics_fielded_bm25_present(stage_diagnostics),
        "no_evidence_scope_limited": no_evidence_scope_limited(no_evidence, claim_boundaries),
        "claim_boundaries_separate_unsupported_claims": unsupported_claims_false(claim_boundaries),
        "compact_packet_decision_fields_present": compact_packet_decision_fields_present(compact, locator),
    }


def sanitize_expected_contract(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary_intent": expected.get("primary_intent"),
        "source_window_needs": list(expected.get("source_window_needs") or []),
        "expected_source_targets": list(expected.get("expected_source_targets") or []),
        "no_evidence_expected": bool(expected.get("no_evidence_expected", False)),
    }


def first_failure_stage(observed: dict[str, Any], expected: dict[str, Any]) -> str | None:
    if not observed.get("query_plan_primary_intent_matches") or not observed.get("source_window_needs_match"):
        return "query_planning"
    if expected.get("no_evidence_expected"):
        if observed.get("status") != "no_evidence" or not observed.get("no_evidence_scope_limited"):
            return "no_evidence_behavior"
        if not observed.get("claim_boundaries_separate_unsupported_claims"):
            return "compact_packet_fields"
        return None
    if not observed.get("expected_source_refs_present"):
        return "source_planning_indexing"
    if not observed.get("expected_source_windows_present") or not observed.get("expected_next_reads_present"):
        return "source_window_selection"
    if not observed.get("compact_packet_decision_fields_present"):
        return "compact_packet_fields"
    if not observed.get("source_windows_metadata_only") or not observed.get("source_windows_preserve_metadata"):
        return "compact_packet_fields"
    if not observed.get("stage_diagnostics_fielded_bm25_present"):
        return "compact_packet_fields"
    if not observed.get("claim_boundaries_separate_unsupported_claims"):
        return "compact_packet_fields"
    return None


def targets_from_source_refs(source_refs: list[dict[str, Any]]) -> list[str]:
    targets = []
    for item in source_refs:
        target = item.get("target") or source_ref_target(item.get("source_ref") or item)
        if target:
            targets.append(str(target))
    return sorted(set(targets))


def targets_from_windows(windows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("target")) for item in windows if item.get("target")})


def targets_from_next_reads(next_reads: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("target")) for item in next_reads if item.get("target")})


def normalize_targets(targets: list[str], *, repo_root: Path) -> list[str]:
    normalized = []
    repo_prefix = str(repo_root) + "/"
    for target in targets:
        if target.startswith(repo_prefix):
            normalized.append(target[len(repo_prefix) :])
        else:
            normalized.append(target)
    return sorted(set(normalized))


def targets_present(expected_targets: list[str], observed_targets: list[str]) -> bool:
    if not expected_targets:
        return True
    return any(any(observed.endswith(expected) for observed in observed_targets) for expected in expected_targets)


def source_windows_metadata_only(windows: list[dict[str, Any]]) -> bool:
    for window in windows:
        if "snippet" in window:
            return False
        if window.get("text_included") is not False or window.get("source_window_text_included") is not False:
            return False
    return True


def source_windows_preserve_metadata(windows: list[dict[str, Any]]) -> bool:
    required = {"source_ref", "target", "role", "timestamp_utc", "cwd", "reason_codes", "next_read"}
    return all(required.issubset(set(window)) for window in windows)


def stage_diagnostics_fielded_bm25_present(stage_diagnostics: dict[str, Any]) -> bool:
    if not stage_diagnostics:
        return False
    stages = {stage.get("stage"): stage for stage in stage_diagnostics.get("stages") or []}
    return bool(stages.get("fielded_index_search")) and bool(stages.get("compact_handoff_output"))


def no_evidence_scope_limited(no_evidence: dict[str, Any], claim_boundaries: dict[str, Any]) -> bool:
    return (
        no_evidence.get("answer_scope") == "not_found_in_searched_scope"
        and no_evidence.get("not_all_history_absence") is True
        and claim_boundaries.get("no_evidence_is_scope_limited") is True
    )


def unsupported_claims_false(claim_boundaries: dict[str, Any]) -> bool:
    for key in (
        "token_savings_claim_supported",
        "final_answer_quality_claim_supported",
        "product_value_claim_supported",
        "default_promotion_supported",
    ):
        if claim_boundaries.get(key) is not False:
            return False
    return True


def compact_packet_decision_fields_present(compact: dict[str, Any], locator: dict[str, Any]) -> bool:
    return all(
        [
            compact.get("source_refs") is not None,
            compact.get("source_windows") is not None,
            compact.get("next_reads") is not None,
            compact.get("no_evidence") is not None,
            compact.get("claim_boundaries") is not None,
            compact.get("source_window_packaging") is not None,
            locator.get("query_plan") is not None,
            compact.get("stage_diagnostics") is not None or locator.get("stage_diagnostics") is not None,
        ]
    )


def question_family_coverage(rows: list[dict[str, Any]]) -> dict[str, bool]:
    families = {row.get("question_family") for row in rows}
    return {family: family in families for family in REQUIRED_QUESTION_FAMILIES}


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(row["usefulness_status"] for row in rows)
    decision_counts = Counter(row["product_decision_signal"] for row in rows if row["product_decision_signal"] != "none")
    family_counts = Counter(row["question_family"] for row in rows)
    return {
        "case_count": len(rows),
        "useful_count": status_counts.get("useful", 0),
        "not_useful_count": status_counts.get("not_useful", 0),
        "inconclusive_count": status_counts.get("inconclusive", 0),
        "invalid_fixture_count": status_counts.get("invalid_fixture", 0),
        "usefulness_status_counts": dict(sorted(status_counts.items())),
        "product_decision_signal_counts": dict(sorted(decision_counts.items())),
        "question_family_counts": dict(sorted(family_counts.items())),
    }


def select_next_product_decision(rows: list[dict[str, Any]]) -> str:
    signals = {row.get("product_decision_signal") for row in rows}
    for decision in DECISION_PRIORITY:
        if decision in signals:
            return decision
    if any(row.get("usefulness_status") in {"inconclusive", "invalid_fixture"} for row in rows):
        return "fixture_or_eval_execution_needs_repair"
    return ALL_USEFUL_DECISION


def summarize_answer_use_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(row["answer_use_status"] for row in rows)
    route_counts = Counter(row["route_observed"] for row in rows)
    decision_counts = Counter(row["product_decision_signal"] for row in rows if row["product_decision_signal"] != "none")
    family_counts = Counter(row["question_family"] for row in rows)
    claim_counts = Counter(claim["observed_support_label"] for row in rows for claim in row.get("claims", []))
    return {
        "case_count": len(rows),
        "actionable_count": status_counts.get("actionable", 0),
        "not_actionable_count": status_counts.get("not_actionable", 0),
        "inconclusive_count": status_counts.get("inconclusive", 0),
        "invalid_fixture_count": status_counts.get("invalid_fixture", 0),
        "route_accuracy_count": sum(1 for row in rows if row.get("route_accuracy") is True),
        "answer_use_status_counts": dict(sorted(status_counts.items())),
        "route_observed_counts": dict(sorted(route_counts.items())),
        "claim_support_label_counts": dict(sorted(claim_counts.items())),
        "product_decision_signal_counts": dict(sorted(decision_counts.items())),
        "question_family_counts": dict(sorted(family_counts.items())),
    }


def select_answer_use_next_product_decision(rows: list[dict[str, Any]]) -> str:
    signals = {row.get("product_decision_signal") for row in rows}
    for decision in ANSWER_USE_DECISION_PRIORITY:
        if decision in signals:
            return decision
    if any(row.get("answer_use_status") in {"inconclusive", "invalid_fixture"} for row in rows):
        return "fixture_or_eval_execution_needs_repair"
    return ALL_ANSWER_USE_ACTIONABLE_DECISION


def render_compact_handoff_usefulness_markdown(payload: dict[str, Any]) -> str:
    report = payload["compact_handoff_usefulness_eval"]
    lines = [
        "# Compact Handoff Usefulness Eval",
        "",
        f"- Status: `{report['status']}`",
        f"- Data source: `{report['data_source']}`",
        f"- Selected next product decision: `{report['selected_next_product_decision']}`",
        "",
        "## Summary",
    ]
    summary = report["summary"]
    for key in ("case_count", "useful_count", "not_useful_count", "inconclusive_count", "invalid_fixture_count"):
        lines.append(f"- {key}: `{summary.get(key, 0)}`")
    lines.extend(["", "## Rows", "", "| Case | Family | Surface | Status | Failure Stage | Decision Signal |", "| --- | --- | --- | --- | --- | --- |"])
    for row in report["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["case_id"]),
                    markdown_cell(row["question_family"]),
                    markdown_cell(row["command_surface"]),
                    markdown_cell(row["usefulness_status"]),
                    markdown_cell(row["failure_stage"] or "none"),
                    markdown_cell(row["product_decision_signal"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Claim Boundaries"])
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def render_compact_handoff_answer_use_markdown(payload: dict[str, Any]) -> str:
    report = payload["compact_handoff_answer_use_eval"]
    lines = [
        "# Compact Handoff Answer-Use Eval",
        "",
        f"- Status: `{report['status']}`",
        f"- Data source: `{report['data_source']}`",
        f"- Selected next product decision: `{report['selected_next_product_decision']}`",
        "",
        "## Summary",
    ]
    summary = report["summary"]
    for key in ("case_count", "actionable_count", "not_actionable_count", "inconclusive_count", "invalid_fixture_count", "route_accuracy_count"):
        lines.append(f"- {key}: `{summary.get(key, 0)}`")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Case | Family | Surface | Route | Status | Failure Class | Decision Signal |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(row["case_id"]),
                    markdown_cell(row["question_family"]),
                    markdown_cell(row["command_surface"]),
                    markdown_cell(row["route_observed"]),
                    markdown_cell(row["answer_use_status"]),
                    markdown_cell(row["failure_class"] or "none"),
                    markdown_cell(row["product_decision_signal"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Claim Support Labels"])
    for key, value in summary.get("claim_support_label_counts", {}).items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Claim Boundaries"])
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.append("")
    return "\n".join(lines)


def markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|")
