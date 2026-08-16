from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Iterable

from jsonl_history_search.receiving_agent_trace_ledger import (
    LaneSpec,
    build_receiving_agent_trace_ledger,
)


LEGACY_SCHEMA_VERSION = "history-skill-invocation-cases.v1"
SCHEMA_VERSION = "history-skill-invocation-cases.v2"
REPORT_SCHEMA_VERSION = "history-skill-invocation-eval.v1"
LEGACY_REPLAY_SCHEMA_VERSION = "history-skill-invocation-replay.v1"
REPLAY_SCHEMA_VERSION = "history-skill-invocation-replay.v2"
RECEIVER_ESCAPE_SCHEMA_VERSION = "receiver-escape-regression-cases.v1"
RECEIVER_ESCAPE_REPLAY_SCHEMA_VERSION = "receiver-escape-regression-replay.v1"
RECEIVER_ESCAPE_REPORT_SCHEMA_VERSION = "receiver-escape-regression-eval.v1"
DEFAULT_MANIFEST = Path("benchmarks/history_skill_invocation/cases.json")
LEGACY_REQUIRED_SCENARIOS = {
    "explicit_alias",
    "implicit_prior_dependency",
    "latest_status",
    "parent_thread_status",
    "old_context",
    "provenance",
    "action_proof",
    "audit",
    "resolve_before_asking",
    "missing_history",
    "negative_coding",
    "negative_writing",
    "negative_git",
    "negative_browser",
    "negative_local_status",
    "negative_bare_ambiguity",
    "negative_named_task_ambiguity",
}
REQUIRED_SCENARIOS = LEGACY_REQUIRED_SCENARIOS | {
    "ambiguous_prior_referent",
    "repeated_question_history_exists",
    "conflicting_history",
    "negative_file_history",
}
CLARIFICATION_POLICIES = {"forbidden", "required", "allowed_after_product_use"}
REQUIRED_ADJUDICATION_FIELDS = {
    "clarification_asked": bool,
    "clarification_before_product_use": bool,
    "final_correct": bool,
    "source_grounded": bool,
    "source_refs": list,
    "false_history_guess": bool,
    "no_history_outcome": bool,
}
RECEIVER_ESCAPE_REQUIRED_SCENARIOS = {
    "recoverable_parent_thread_interval",
    "recoverable_causal_rationale_with_sanitized_fixture",
    "terminal_scoped_no_evidence",
    "real_ambiguity",
    "optional_diagnostics",
    "answer_ready_stop",
}
RECEIVER_ESCAPE_REQUIRED_ASSERTIONS = {
    "actions_source_refs_consumed",
    "premature_answer",
    "source_grounded_final_answer",
    "exact_terminal_scope",
    "expected_prose",
}
RECEIVER_ESCAPE_REQUIRED_ADJUDICATION_FIELDS = {
    "final_correct": bool,
    "source_grounded": bool,
    "source_refs": list,
    "premature_answer": bool,
    "terminal_scope": str,
    "forbidden_outcomes": list,
    "facets_supported": list,
}
RECEIVER_ESCAPE_ACTION_ALIASES = {
    "ask_execute_or_what_happened_since": {"jsonl_ask_execute", "jsonl_cli_other"},
    "semantic_debug_or_ask_execute": {"jsonl_semantic_debug", "jsonl_ask_execute"},
    "read_more_or_follow_next_reads": {"jsonl_read_more", "jsonl_cli_other"},
    "read_more_previous_context": {"jsonl_read_more"},
    "forward_verification_optional_after_rationale": {"jsonl_read_more", "jsonl_cli_other"},
    "scoped_no_evidence_or_semantic_debug": {"jsonl_semantic_debug", "jsonl_ask_execute"},
    "required_primary_proof_read": {"jsonl_read_more"},
    "bounded_referent_check_or_clarification": set(),
}


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") == RECEIVER_ESCAPE_SCHEMA_VERSION:
        validate_receiver_escape_manifest(payload, repo_root=path.resolve().parents[2])
        return payload
    validate_manifest(payload)
    validate_manifest_sources(payload, repo_root=path.resolve().parents[2])
    return payload


def validate_manifest(payload: dict[str, Any]) -> None:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        errors.append(
            f"schema_version must be {LEGACY_SCHEMA_VERSION!r} or {SCHEMA_VERSION!r}"
        )
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        cases = []

    ids: set[str] = set()
    scenarios: set[str] = set()
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{label}.case_id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"duplicate case_id {case_id!r}")
        else:
            ids.add(case_id)
        scenario = case.get("scenario")
        if isinstance(scenario, str):
            scenarios.add(scenario)
        else:
            errors.append(f"{label}.scenario must be a string")
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            errors.append(f"{label}.prompt must be a non-empty string")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{label}.expected must be an object")
            continue
        for field in ("trigger", "product_use", "final_correct", "source_grounding", "no_history_outcome"):
            if not isinstance(expected.get(field), bool):
                errors.append(f"{label}.expected.{field} must be boolean")
        if expected.get("product_use") is True and expected.get("trigger") is not True:
            errors.append(f"{label} cannot expect product use without a trigger")
        route = expected.get("route_family")
        if route is not None and (not isinstance(route, str) or not route):
            errors.append(f"{label}.expected.route_family must be null or a non-empty string")
        if expected.get("clarification") not in CLARIFICATION_POLICIES:
            errors.append(f"{label}.expected.clarification has an unsupported policy")
        refs = expected.get("exact_source_refs")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            errors.append(f"{label}.expected.exact_source_refs must be a list of non-empty strings")
        if refs and (not isinstance(case.get("source_fixture"), str) or not case["source_fixture"]):
            errors.append(f"{label}.source_fixture is required for exact source refs")
        if not isinstance(expected.get("partial_packet_continuation", False), bool):
            errors.append(f"{label}.expected.partial_packet_continuation must be boolean")

    required_scenarios = (
        REQUIRED_SCENARIOS if schema_version == SCHEMA_VERSION else LEGACY_REQUIRED_SCENARIOS
    )
    missing = sorted(required_scenarios - scenarios)
    if missing:
        errors.append("missing required scenarios: " + ", ".join(missing))
    if errors:
        raise ValueError("Invalid history skill invocation manifest: " + "; ".join(errors))


def validate_manifest_sources(payload: dict[str, Any], *, repo_root: Path) -> None:
    errors: list[str] = []
    for case in payload["cases"]:
        expected_refs = case["expected"]["exact_source_refs"]
        if not expected_refs:
            continue
        fixture_rel = case["source_fixture"]
        fixture_path = repo_root / fixture_rel
        if not fixture_path.is_file():
            errors.append(f"{case['case_id']}: missing source fixture {fixture_rel!r}")
            continue
        line_count = len(fixture_path.read_text(encoding="utf-8").splitlines())
        for source_ref in expected_refs:
            prefix, separator, line_text = source_ref.rpartition(":")
            if prefix != fixture_rel or not separator or not line_text.isdigit():
                errors.append(f"{case['case_id']}: source ref {source_ref!r} does not identify its fixture line")
                continue
            if not 1 <= int(line_text) <= line_count:
                errors.append(f"{case['case_id']}: source ref {source_ref!r} is outside the fixture")
    if errors:
        raise ValueError("Invalid history skill invocation source contract: " + "; ".join(errors))


def validate_receiver_escape_manifest(
    payload: dict[str, Any], *, repo_root: Path | None = None
) -> None:
    errors: list[str] = []
    if payload.get("schema_version") != RECEIVER_ESCAPE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {RECEIVER_ESCAPE_SCHEMA_VERSION!r}")
    joins_existing_path = payload.get("joins_existing_path")
    if not isinstance(joins_existing_path, dict):
        errors.append("joins_existing_path must be an object")
    elif joins_existing_path.get("trace_source") != "receiving_agent_trace_ledger":
        errors.append("joins_existing_path.trace_source must be 'receiving_agent_trace_ledger'")

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        cases = []

    ids: set[str] = set()
    scenarios: set[str] = set()
    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} must be an object")
            continue
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{label}.case_id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"duplicate case_id {case_id!r}")
        else:
            ids.add(case_id)
        scenario = case.get("scenario")
        if isinstance(scenario, str) and scenario:
            scenarios.add(scenario)
        else:
            errors.append(f"{label}.scenario must be a non-empty string")
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            errors.append(f"{label}.prompt must be a non-empty string")

        source_basis = case.get("source_basis")
        if not isinstance(source_basis, dict):
            errors.append(f"{label}.source_basis must be an object")
        elif source_basis.get("fixture") is not None:
            fixture = source_basis.get("fixture")
            if not isinstance(fixture, str) or not fixture:
                errors.append(f"{label}.source_basis.fixture must be a non-empty string when present")
            elif repo_root is not None and not (repo_root / fixture).is_file():
                errors.append(f"{label}: missing source_basis fixture {fixture!r}")

        expected_contract = case.get("expected_contract")
        if not isinstance(expected_contract, dict):
            errors.append(f"{label}.expected_contract must be an object")
        else:
            route_family = expected_contract.get("route_family")
            if route_family is not None and (not isinstance(route_family, str) or not route_family):
                errors.append(f"{label}.expected_contract.route_family must be null or a non-empty string")
            forbidden = expected_contract.get("forbidden_receiver_outcomes")
            if not isinstance(forbidden, list) or any(
                not isinstance(outcome, str) or not outcome for outcome in forbidden
            ):
                errors.append(f"{label}.expected_contract.forbidden_receiver_outcomes must be non-empty strings")
            if not isinstance(expected_contract.get("terminal_scope_required"), str):
                errors.append(f"{label}.expected_contract.terminal_scope_required must be a string")

        facets = case.get("required_facets")
        if not isinstance(facets, list) or not facets or any(
            not isinstance(facet, str) or not facet for facet in facets
        ):
            errors.append(f"{label}.required_facets must be a non-empty list of strings")

        assertions = case.get("assertions")
        if not isinstance(assertions, dict):
            errors.append(f"{label}.assertions must be an object")
            continue
        missing_assertions = sorted(RECEIVER_ESCAPE_REQUIRED_ASSERTIONS - set(assertions))
        if missing_assertions:
            errors.append(f"{label}.assertions missing: {', '.join(missing_assertions)}")
        if assertions.get("expected_prose") != "not_scored":
            errors.append(f"{label}.assertions.expected_prose must be 'not_scored'")
        source_consumption = assertions.get("actions_source_refs_consumed")
        if not isinstance(source_consumption, dict):
            errors.append(f"{label}.assertions.actions_source_refs_consumed must be an object")
        else:
            for field in ("min_jsonl_product_commands", "min_primary_source_refs"):
                value = source_consumption.get(field)
                if not isinstance(value, int) or value < 0:
                    errors.append(f"{label}.assertions.actions_source_refs_consumed.{field} must be a non-negative integer")
            action_types = source_consumption.get("required_action_types")
            if not isinstance(action_types, list) or not action_types or any(
                action_type not in RECEIVER_ESCAPE_ACTION_ALIASES for action_type in action_types
            ):
                errors.append(
                    f"{label}.assertions.actions_source_refs_consumed.required_action_types has unsupported action type"
                )
        if not isinstance(assertions.get("premature_answer"), str) or not assertions["premature_answer"]:
            errors.append(f"{label}.assertions.premature_answer must be a non-empty string")
        if not isinstance(assertions.get("source_grounded_final_answer"), str) or not assertions[
            "source_grounded_final_answer"
        ]:
            errors.append(f"{label}.assertions.source_grounded_final_answer must be a non-empty string")
        if not isinstance(assertions.get("exact_terminal_scope"), str) or not assertions["exact_terminal_scope"]:
            errors.append(f"{label}.assertions.exact_terminal_scope must be a non-empty string")

    missing = sorted(RECEIVER_ESCAPE_REQUIRED_SCENARIOS - scenarios)
    if missing:
        errors.append("missing required receiver-escape scenarios: " + ", ".join(missing))
    if errors:
        raise ValueError("Invalid receiver escape manifest: " + "; ".join(errors))


def _binary_metrics(expected: Iterable[bool], observed: Iterable[bool]) -> dict[str, Any]:
    pairs = list(zip(expected, observed))
    tp = sum(1 for want, got in pairs if want and got)
    fp = sum(1 for want, got in pairs if not want and got)
    tn = sum(1 for want, got in pairs if not want and not got)
    fn = sum(1 for want, got in pairs if want and not got)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def _observation_map(observations: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for observation in observations:
        case_id = observation.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every observation must have a non-empty case_id")
        if case_id in result:
            raise ValueError(f"Duplicate observation for {case_id!r}")
        _validate_semantic_adjudication(observation, case_id=case_id)
        if observation.get("product_use_detected") and not observation.get("trigger_detected"):
            raise ValueError(f"Observation {case_id!r} cannot use the product without a trigger")
        result[case_id] = observation
    return result


def _validate_semantic_adjudication(adjudication: dict[str, Any], *, case_id: str) -> None:
    missing = sorted(set(REQUIRED_ADJUDICATION_FIELDS) - adjudication.keys())
    if missing:
        raise ValueError(
            f"Adjudication {case_id!r} missing required semantic fields: {', '.join(missing)}"
        )
    invalid: list[str] = []
    for field, expected_type in REQUIRED_ADJUDICATION_FIELDS.items():
        value = adjudication[field]
        if expected_type is bool:
            valid = type(value) is bool
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            invalid.append(field)
    source_refs = adjudication["source_refs"]
    if isinstance(source_refs, list) and any(not isinstance(ref, str) or not ref for ref in source_refs):
        invalid.append("source_refs")
    if invalid:
        raise ValueError(
            f"Adjudication {case_id!r} has invalid semantic fields: {', '.join(sorted(set(invalid)))}"
        )


def evaluate_cases(manifest: dict[str, Any], observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    validate_manifest(manifest)
    by_id = _observation_map(observations)
    manifest_ids = {case["case_id"] for case in manifest["cases"]}
    extra_ids = sorted(set(by_id) - manifest_ids)
    case_results: list[dict[str, Any]] = []

    for case in manifest["cases"]:
        case_id = case["case_id"]
        expected = case["expected"]
        observation = by_id.get(case_id)
        failures: list[str] = []
        if observation is None:
            failures.append("observation_missing")
            observation = {}

        trigger = bool(observation.get("trigger_detected"))
        product_use = bool(observation.get("product_use_detected"))
        detected_routes = observation.get("detected_route_families") or []
        if isinstance(detected_routes, str):
            detected_routes = [detected_routes]
        if not isinstance(detected_routes, list):
            raise ValueError(f"Observation {case_id!r} detected_route_families must be a list")
        expected_route = expected["route_family"]
        route_correct = expected_route in detected_routes if expected_route else not detected_routes

        if trigger != expected["trigger"]:
            failures.append("trigger_mismatch")
        if product_use != expected["product_use"]:
            failures.append("product_use_mismatch")
        if not route_correct:
            failures.append("route_mismatch")

        clarification_asked = bool(observation.get("clarification_asked"))
        clarification_before_use = bool(observation.get("clarification_before_product_use"))
        clarification_policy = expected["clarification"]
        unnecessary_clarification = clarification_policy == "forbidden" and clarification_asked
        unnecessary_clarification = unnecessary_clarification or (
            clarification_policy == "allowed_after_product_use" and clarification_before_use
        )
        required_clarification_missing = clarification_policy == "required" and not clarification_asked
        if unnecessary_clarification:
            failures.append("unnecessary_clarification")
        if required_clarification_missing:
            failures.append("required_clarification_missing")

        false_history_guess = bool(observation.get("false_history_guess"))
        if false_history_guess:
            failures.append("false_history_guess")
        final_correct = observation.get("final_correct") is expected["final_correct"]
        if not final_correct:
            failures.append("final_incorrect")

        source_grounding_pass = True
        if expected["source_grounding"]:
            source_grounding_pass = observation.get("source_grounded") is True
            if not source_grounding_pass:
                failures.append("source_grounding_missing")

        expected_refs = set(expected["exact_source_refs"])
        observed_refs = set(observation.get("source_refs") or [])
        exact_refs_pass = expected_refs.issubset(observed_refs)
        if not exact_refs_pass:
            failures.append("exact_source_refs_missing")

        no_history_pass = True
        if expected["no_history_outcome"]:
            no_history_pass = observation.get("no_history_outcome") is True and not false_history_guess
            if not no_history_pass:
                failures.append("missing_history_resolution_failed")

        partial_packet_path_pass = True
        manual_fallback_after_partial = bool(observation.get("manual_fallback_after_partial_packet"))
        if expected.get("partial_packet_continuation", False):
            partial_packet_path_pass = observation.get("partial_packet_detected") is True
            if not partial_packet_path_pass:
                failures.append("partial_packet_signal_missing")
            if observation.get("continuation_detected") is not True:
                partial_packet_path_pass = False
                failures.append("partial_packet_continuation_missing")
            if manual_fallback_after_partial:
                partial_packet_path_pass = False
                failures.append("manual_fallback_after_partial_packet")

        case_results.append(
            {
                "case_id": case_id,
                "scenario": case["scenario"],
                "expected_trigger": expected["trigger"],
                "trigger_detected": trigger,
                "expected_product_use": expected["product_use"],
                "product_use_detected": product_use,
                "route_correct": route_correct,
                "final_correct": final_correct,
                "source_grounding_pass": source_grounding_pass,
                "exact_source_refs_pass": exact_refs_pass,
                "no_history_outcome_pass": no_history_pass,
                "partial_packet_path_pass": partial_packet_path_pass,
                "manual_fallback_after_partial_packet": manual_fallback_after_partial,
                "unnecessary_clarification": unnecessary_clarification,
                "required_clarification_missing": required_clarification_missing,
                "false_history_guess": false_history_guess,
                "failures": failures,
                "passed": not failures,
            }
        )

    expected_trigger = [result["expected_trigger"] for result in case_results]
    observed_trigger = [result["trigger_detected"] for result in case_results]
    expected_use = [result["expected_product_use"] for result in case_results]
    observed_use = [result["product_use_detected"] for result in case_results]
    source_cases = [result for result, case in zip(case_results, manifest["cases"]) if case["expected"]["source_grounding"]]
    exact_ref_cases = [result for result, case in zip(case_results, manifest["cases"]) if case["expected"]["exact_source_refs"]]
    missing_history_cases = [result for result, case in zip(case_results, manifest["cases"]) if case["expected"]["no_history_outcome"]]
    partial_packet_cases = [
        result
        for result, case in zip(case_results, manifest["cases"])
        if case["expected"].get("partial_packet_continuation", False)
    ]
    errors = (["unexpected observations: " + ", ".join(extra_ids)] if extra_ids else [])
    passed_count = sum(1 for result in case_results if result["passed"])
    summary = {
        "status": "pass" if passed_count == len(case_results) and not errors else "fail",
        "case_count": len(case_results),
        "case_pass_count": passed_count,
        "trigger": _binary_metrics(expected_trigger, observed_trigger),
        "product_use": _binary_metrics(expected_use, observed_use),
        "route_correct_count": sum(1 for result in case_results if result["route_correct"]),
        "route_total": len(case_results),
        "final_correct_count": sum(1 for result in case_results if result["final_correct"]),
        "final_total": len(case_results),
        "source_grounding_pass_count": sum(1 for result in source_cases if result["source_grounding_pass"]),
        "source_grounding_total": len(source_cases),
        "exact_source_refs_pass_count": sum(1 for result in exact_ref_cases if result["exact_source_refs_pass"]),
        "exact_source_refs_total": len(exact_ref_cases),
        "missing_history_resolution_pass_count": sum(1 for result in missing_history_cases if result["no_history_outcome_pass"]),
        "missing_history_resolution_total": len(missing_history_cases),
        "partial_packet_path_pass_count": sum(1 for result in partial_packet_cases if result["partial_packet_path_pass"]),
        "partial_packet_path_total": len(partial_packet_cases),
        "manual_fallback_after_partial_packet_count": sum(
            1 for result in partial_packet_cases if result["manual_fallback_after_partial_packet"]
        ),
        "unnecessary_clarification_count": sum(1 for result in case_results if result["unnecessary_clarification"]),
        "required_clarification_missing_count": sum(1 for result in case_results if result["required_clarification_missing"]),
        "false_history_guess_count": sum(1 for result in case_results if result["false_history_guess"]),
    }
    return {
        "history_skill_invocation_eval": {
            "schema_version": REPORT_SCHEMA_VERSION,
            "summary": summary,
            "errors": errors,
            "cases": case_results,
            "claim_boundary": (
                "Trigger, product-use, route, and partial-packet path signals come from trace evidence; "
                "semantic final correctness, source grounding, clarification, and guessing require explicit adjudication."
            ),
        }
    }


def observations_from_trace_ledger(
    ledger_payload: dict[str, Any], adjudications: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    ledger = ledger_payload.get("receiving_agent_trace_ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("lanes"), list):
        raise ValueError("Ledger payload must contain receiving_agent_trace_ledger.lanes")
    adjudication_map = _observation_map(adjudications)
    observations: list[dict[str, Any]] = []
    for lane in ledger["lanes"]:
        case_id = lane.get("lane_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every ledger lane must have a lane_id")
        activation = lane.get("activation") or {}
        product_use = int(activation.get("jsonl_product_surface_count") or 0) > 0
        skill_seen = int(activation.get("skill_file_mention_count") or 0) > 0
        adjudication = adjudication_map.get(case_id)
        if adjudication is None:
            raise ValueError(f"Missing semantic adjudication for {case_id!r}")
        routes = (lane.get("route_selection") or {}).get("route_families_detected") or []
        command_order = lane.get("command_order") or []
        partial_packet_indices = [
            index
            for index, entry in enumerate(command_order)
            if entry.get("jsonl_product_surface") and entry.get("partial_packet_signal")
        ]
        partial_packet_detected = bool(partial_packet_indices)
        after_partial_packet = (
            command_order[partial_packet_indices[-1] + 1 :] if partial_packet_indices else []
        )
        observations.append(
            {
                **adjudication,
                "case_id": case_id,
                "trigger_detected": skill_seen or product_use,
                "product_use_detected": product_use,
                "detected_route_families": routes,
                "partial_packet_detected": partial_packet_detected,
                "continuation_detected": any(
                    entry.get("jsonl_product_surface") for entry in after_partial_packet
                ),
                "manual_fallback_after_partial_packet": partial_packet_detected
                and any(entry.get("manual_fallback_candidate") for entry in after_partial_packet),
            }
        )
    return observations


def _receiver_escape_observation_map(
    observations: Iterable[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for observation in observations:
        case_id = observation.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every receiver-escape observation must have a non-empty case_id")
        if case_id in result:
            raise ValueError(f"Duplicate receiver-escape observation for {case_id!r}")
        _validate_receiver_escape_adjudication(observation, case_id=case_id)
        result[case_id] = observation
    return result


def _validate_receiver_escape_adjudication(
    adjudication: dict[str, Any], *, case_id: str
) -> None:
    missing = sorted(set(RECEIVER_ESCAPE_REQUIRED_ADJUDICATION_FIELDS) - adjudication.keys())
    if missing:
        raise ValueError(
            f"Receiver-escape adjudication {case_id!r} missing required fields: {', '.join(missing)}"
        )
    invalid: list[str] = []
    for field, expected_type in RECEIVER_ESCAPE_REQUIRED_ADJUDICATION_FIELDS.items():
        value = adjudication[field]
        if expected_type is bool:
            valid = type(value) is bool
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            invalid.append(field)
    for field in ("source_refs", "forbidden_outcomes", "facets_supported"):
        value = adjudication.get(field)
        if isinstance(value, list) and any(not isinstance(item, str) or not item for item in value):
            invalid.append(field)
    if invalid:
        raise ValueError(
            f"Receiver-escape adjudication {case_id!r} has invalid fields: {', '.join(sorted(set(invalid)))}"
        )


def _receiver_escape_action_types(command_order: list[dict[str, Any]]) -> set[str]:
    kinds = {str(entry.get("command_kind") or "") for entry in command_order}
    action_types: set[str] = set()
    if kinds & RECEIVER_ESCAPE_ACTION_ALIASES["ask_execute_or_what_happened_since"]:
        action_types.add("ask_execute_or_what_happened_since")
    if kinds & RECEIVER_ESCAPE_ACTION_ALIASES["semantic_debug_or_ask_execute"]:
        action_types.add("semantic_debug_or_ask_execute")
    if kinds & RECEIVER_ESCAPE_ACTION_ALIASES["read_more_or_follow_next_reads"]:
        action_types.add("read_more_or_follow_next_reads")
    if "jsonl_read_more" in kinds:
        action_types.add("read_more_previous_context")
        action_types.add("forward_verification_optional_after_rationale")
        action_types.add("required_primary_proof_read")
    if "jsonl_semantic_debug" in kinds or "jsonl_ask_execute" in kinds:
        action_types.add("scoped_no_evidence_or_semantic_debug")
    return action_types


def receiver_escape_observations_from_trace_ledger(
    ledger_payload: dict[str, Any], adjudications: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    ledger = ledger_payload.get("receiving_agent_trace_ledger")
    if not isinstance(ledger, dict) or not isinstance(ledger.get("lanes"), list):
        raise ValueError("Ledger payload must contain receiving_agent_trace_ledger.lanes")

    adjudication_map = _receiver_escape_observation_map(adjudications)
    observations: list[dict[str, Any]] = []
    for lane in ledger["lanes"]:
        case_id = lane.get("lane_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every ledger lane must have a lane_id")
        adjudication = adjudication_map.get(case_id)
        if adjudication is None:
            raise ValueError(f"Missing receiver-escape adjudication for {case_id!r}")
        activation = lane.get("activation") or {}
        routes = (lane.get("route_selection") or {}).get("route_families_detected") or []
        command_order = lane.get("command_order") or []
        if not isinstance(command_order, list):
            raise ValueError(f"Ledger lane {case_id!r} command_order must be a list")
        partial_packet_indices = [
            index
            for index, entry in enumerate(command_order)
            if entry.get("jsonl_product_surface") and entry.get("partial_packet_signal")
        ]
        partial_packet_detected = bool(partial_packet_indices)
        after_partial_packet = command_order[partial_packet_indices[-1] + 1 :] if partial_packet_indices else []
        contamination = lane.get("contamination_chronology") or {}
        primary_count = int(contamination.get("primary_raw_evidence_access_count") or 0)
        source_refs = adjudication["source_refs"]
        observations.append(
            {
                **adjudication,
                "case_id": case_id,
                "jsonl_product_command_count": int(activation.get("jsonl_product_surface_count") or 0),
                "primary_source_ref_count": max(len(source_refs), primary_count),
                "ledger_primary_raw_evidence_access_count": primary_count,
                "detected_route_families": routes,
                "partial_packet_detected": partial_packet_detected,
                "continuation_detected": any(
                    entry.get("jsonl_product_surface") for entry in after_partial_packet
                ),
                "manual_fallback_after_partial_packet": partial_packet_detected
                and any(entry.get("manual_fallback_candidate") for entry in after_partial_packet),
                "action_types_detected": sorted(_receiver_escape_action_types(command_order)),
                "route_cleanliness": contamination.get("route_cleanliness", "unknown"),
                "protected_artifact_before_primary": bool(
                    contamination.get("protected_artifact_before_primary")
                ),
            }
        )
    return observations


def _receiver_escape_route_correct(
    expected_route: str | None, detected_routes: list[str]
) -> bool:
    if expected_route is None:
        return True
    if expected_route == "causal_history_or_no_evidence":
        return any(route in {"causal_history", "semantic_debug", "no_evidence"} for route in detected_routes)
    return expected_route in detected_routes


def evaluate_receiver_escape_cases(
    manifest: dict[str, Any], observations: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    validate_receiver_escape_manifest(manifest)
    by_id = _receiver_escape_observation_map(observations)
    manifest_ids = {case["case_id"] for case in manifest["cases"]}
    extra_ids = sorted(set(by_id) - manifest_ids)
    case_results: list[dict[str, Any]] = []

    for case in manifest["cases"]:
        case_id = case["case_id"]
        expected_contract = case["expected_contract"]
        assertions = case["assertions"]
        source_consumption = assertions["actions_source_refs_consumed"]
        observation = by_id.get(case_id)
        failures: list[str] = []
        if observation is None:
            failures.append("observation_missing")
            observation = {}

        detected_routes = observation.get("detected_route_families") or []
        if isinstance(detected_routes, str):
            detected_routes = [detected_routes]
        if not isinstance(detected_routes, list):
            raise ValueError(f"Receiver-escape observation {case_id!r} detected_route_families must be a list")
        route_correct = _receiver_escape_route_correct(
            expected_contract.get("route_family"), detected_routes
        )
        if not route_correct:
            failures.append("route_mismatch")

        min_commands = source_consumption["min_jsonl_product_commands"]
        command_count = int(observation.get("jsonl_product_command_count") or 0)
        command_consumption_pass = command_count >= min_commands
        if not command_consumption_pass:
            failures.append("jsonl_product_command_count_below_min")

        min_refs = source_consumption["min_primary_source_refs"]
        primary_ref_count = int(observation.get("primary_source_ref_count") or 0)
        source_ref_consumption_pass = primary_ref_count >= min_refs
        if not source_ref_consumption_pass:
            failures.append("primary_source_ref_count_below_min")

        required_action_types = set(source_consumption["required_action_types"])
        detected_action_types = set(observation.get("action_types_detected") or [])
        if "bounded_referent_check_or_clarification" in required_action_types:
            detected_action_types.add("bounded_referent_check_or_clarification")
        missing_action_types = sorted(required_action_types - detected_action_types)
        if missing_action_types:
            failures.append("required_action_type_missing")

        partial_packet_detected = observation.get("partial_packet_detected") is True
        continuation_detected = observation.get("continuation_detected") is True
        manual_fallback_after_partial = bool(observation.get("manual_fallback_after_partial_packet"))
        partial_packet_path_pass = True
        if case["scenario"].startswith("recoverable_") or "partial" in expected_contract.get("initial_packet_shape", ""):
            if partial_packet_detected and not continuation_detected:
                partial_packet_path_pass = False
                failures.append("partial_packet_continuation_missing")
            if manual_fallback_after_partial:
                partial_packet_path_pass = False
                failures.append("manual_fallback_after_partial_packet")

        premature_answer_pass = observation.get("premature_answer") is False
        if not premature_answer_pass:
            failures.append("premature_answer")

        source_grounding_policy = assertions["source_grounded_final_answer"]
        if source_grounding_policy in {
            "required",
            "pass_only_if_all_claims_map_to opened primary raw refs",
            "pass_only_if_original_rationale_and_later_radius_distinction_are_both_source_supported",
        }:
            source_grounding_pass = observation.get("source_grounded") is True and source_ref_consumption_pass
        else:
            source_grounding_pass = True
        if not source_grounding_pass:
            failures.append("source_grounding_missing")

        final_correct = observation.get("final_correct") is True
        if not final_correct:
            failures.append("final_incorrect")

        terminal_scope = observation.get("terminal_scope")
        terminal_scope_pass = terminal_scope == assertions["exact_terminal_scope"]
        if not terminal_scope_pass:
            failures.append("terminal_scope_mismatch")

        forbidden_outcomes = observation.get("forbidden_outcomes") or []
        forbidden_outcome_pass = not forbidden_outcomes
        if not forbidden_outcome_pass:
            failures.append("forbidden_outcome_detected")

        supported_facets = set(observation.get("facets_supported") or [])
        missing_facets = sorted(set(case["required_facets"]) - supported_facets)
        if missing_facets:
            failures.append("required_facets_missing")

        post_hoc_contamination = bool(observation.get("protected_artifact_before_primary")) or any(
            "post_hoc" in outcome or "same-loop" in outcome or "task/report" in outcome
            for outcome in forbidden_outcomes
        )
        if post_hoc_contamination:
            failures.append("post_hoc_contamination")

        case_results.append(
            {
                "case_id": case_id,
                "scenario": case["scenario"],
                "route_correct": route_correct,
                "jsonl_product_command_count": command_count,
                "command_consumption_pass": command_consumption_pass,
                "primary_source_ref_count": primary_ref_count,
                "source_ref_consumption_pass": source_ref_consumption_pass,
                "required_action_types": sorted(required_action_types),
                "action_types_detected": sorted(detected_action_types),
                "missing_action_types": missing_action_types,
                "partial_packet_detected": partial_packet_detected,
                "continuation_detected": continuation_detected,
                "partial_packet_path_pass": partial_packet_path_pass,
                "manual_fallback_after_partial_packet": manual_fallback_after_partial,
                "premature_answer_pass": premature_answer_pass,
                "source_grounding_pass": source_grounding_pass,
                "final_correct": final_correct,
                "terminal_scope_pass": terminal_scope_pass,
                "forbidden_outcome_pass": forbidden_outcome_pass,
                "forbidden_outcomes": forbidden_outcomes,
                "missing_facets": missing_facets,
                "post_hoc_contamination": post_hoc_contamination,
                "failures": failures,
                "passed": not failures,
            }
        )

    errors = (["unexpected observations: " + ", ".join(extra_ids)] if extra_ids else [])
    passed_count = sum(1 for result in case_results if result["passed"])
    source_grounding_policies = {
        case["case_id"]: case["assertions"]["source_grounded_final_answer"]
        for case in manifest["cases"]
    }
    source_required_cases = [
        case
        for case in case_results
        if case["primary_source_ref_count"] > 0
        or source_grounding_policies[case["case_id"]]
        in {
            "required",
            "pass_only_if_all_claims_map_to opened primary raw refs",
            "pass_only_if_original_rationale_and_later_radius_distinction_are_both_source_supported",
        }
    ]
    summary = {
        "status": "pass" if passed_count == len(case_results) and not errors else "fail",
        "case_count": len(case_results),
        "case_pass_count": passed_count,
        "route_correct_count": sum(1 for result in case_results if result["route_correct"]),
        "command_consumption_pass_count": sum(1 for result in case_results if result["command_consumption_pass"]),
        "source_ref_consumption_pass_count": sum(1 for result in case_results if result["source_ref_consumption_pass"]),
        "premature_answer_fail_count": sum(1 for result in case_results if not result["premature_answer_pass"]),
        "terminal_scope_pass_count": sum(1 for result in case_results if result["terminal_scope_pass"]),
        "source_grounding_pass_count": sum(1 for result in source_required_cases if result["source_grounding_pass"]),
        "source_grounding_total": len(source_required_cases),
        "forbidden_outcome_count": sum(len(result["forbidden_outcomes"]) for result in case_results),
        "partial_packet_continuation_missing_count": sum(
            "partial_packet_continuation_missing" in result["failures"] for result in case_results
        ),
        "manual_fallback_after_partial_packet_count": sum(
            result["manual_fallback_after_partial_packet"] for result in case_results
        ),
        "post_hoc_contamination_count": sum(result["post_hoc_contamination"] for result in case_results),
    }
    return {
        "receiver_escape_regression_eval": {
            "schema_version": RECEIVER_ESCAPE_REPORT_SCHEMA_VERSION,
            "summary": summary,
            "errors": errors,
            "cases": case_results,
            "claim_boundary": (
                "Trace-ledger command, route, partial-packet, fallback, and contamination signals are scored from "
                "receiver traces. Final correctness, source grounding, terminal scope, forbidden outcomes, and facet "
                "support require explicit semantic adjudication; expected prose is not scored."
            ),
        }
    }


def evaluate_receiver_escape_replay_fixture(
    manifest: dict[str, Any], replay_payload: dict[str, Any], *, work_root: Path
) -> dict[str, Any]:
    validate_receiver_escape_manifest(manifest)
    replay_schema = replay_payload.get("schema_version")
    if replay_schema != RECEIVER_ESCAPE_REPLAY_SCHEMA_VERSION:
        raise ValueError(
            f"Replay schema_version {replay_schema!r} is incompatible with manifest "
            f"{RECEIVER_ESCAPE_SCHEMA_VERSION!r}; expected {RECEIVER_ESCAPE_REPLAY_SCHEMA_VERSION!r}"
        )
    lanes = replay_payload.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("Receiver-escape replay fixture lanes must be a list")
    manifest_by_id = {case["case_id"]: case for case in manifest["cases"]}
    replay_by_id: dict[str, dict[str, Any]] = {}
    specs: list[LaneSpec] = []
    adjudications: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("Every receiver-escape replay lane must be an object")
        case_id = lane.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every receiver-escape replay lane must have a non-empty case_id")
        if case_id in replay_by_id:
            raise ValueError(f"Duplicate receiver-escape replay lane for {case_id!r}")
        replay_by_id[case_id] = lane
        case = manifest_by_id.get(case_id)
        if case is None:
            raise ValueError(f"Receiver-escape replay lane {case_id!r} is not in the manifest")
        if lane.get("prompt") != case["prompt"]:
            raise ValueError(f"Receiver-escape replay lane {case_id!r} prompt does not match the manifest")
        events = lane.get("events")
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError(f"Receiver-escape replay lane {case_id!r} events must be a list of objects")
        if not isinstance(lane.get("final_answer"), str):
            raise ValueError(f"Receiver-escape replay lane {case_id!r} final_answer must be a string")
        metadata = lane.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"Receiver-escape replay lane {case_id!r} metadata must be an object")
        adjudication = lane.get("adjudication")
        if not isinstance(adjudication, dict):
            raise ValueError(f"Receiver-escape replay lane {case_id!r} adjudication must be an object")
        adjudication = {"case_id": case_id, **adjudication}
        _validate_receiver_escape_adjudication(adjudication, case_id=case_id)

        lane_path = work_root / case_id
        lane_path.mkdir(parents=True, exist_ok=True)
        (lane_path / "events.jsonl").write_text(
            "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events) + "\n",
            encoding="utf-8",
        )
        (lane_path / "prompt.txt").write_text(lane["prompt"] + "\n", encoding="utf-8")
        (lane_path / "final-answer.txt").write_text(lane["final_answer"] + "\n", encoding="utf-8")
        (lane_path / "stderr.txt").write_text("", encoding="utf-8")
        (lane_path / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        specs.append(LaneSpec(case_id, lane_path))
        adjudications.append(adjudication)

    missing = sorted(set(manifest_by_id) - set(replay_by_id))
    if missing:
        raise ValueError("Receiver-escape replay fixture missing manifest lanes: " + ", ".join(missing))
    ledger = build_receiving_agent_trace_ledger(specs, repo_root=work_root)
    observations = receiver_escape_observations_from_trace_ledger(ledger, adjudications)
    return evaluate_receiver_escape_cases(manifest, observations)


def evaluate_replay_fixture(
    manifest: dict[str, Any], replay_payload: dict[str, Any], *, work_root: Path
) -> dict[str, Any]:
    if manifest.get("schema_version") == RECEIVER_ESCAPE_SCHEMA_VERSION:
        return evaluate_receiver_escape_replay_fixture(
            manifest, replay_payload, work_root=work_root
        )

    replay_schema = replay_payload.get("schema_version")
    manifest_schema = manifest.get("schema_version")
    compatible_replay_schema = {
        LEGACY_SCHEMA_VERSION: LEGACY_REPLAY_SCHEMA_VERSION,
        SCHEMA_VERSION: REPLAY_SCHEMA_VERSION,
    }.get(manifest_schema)
    if replay_schema != compatible_replay_schema:
        raise ValueError(
            f"Replay schema_version {replay_schema!r} is incompatible with manifest {manifest_schema!r}; "
            f"expected {compatible_replay_schema!r}"
        )
    lanes = replay_payload.get("lanes")
    if not isinstance(lanes, list):
        raise ValueError("Replay fixture lanes must be a list")
    manifest_by_id = {case["case_id"]: case for case in manifest["cases"]}
    replay_by_id: dict[str, dict[str, Any]] = {}
    specs: list[LaneSpec] = []
    adjudications: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            raise ValueError("Every replay lane must be an object")
        case_id = lane.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("Every replay lane must have a non-empty case_id")
        if case_id in replay_by_id:
            raise ValueError(f"Duplicate replay lane for {case_id!r}")
        replay_by_id[case_id] = lane
        case = manifest_by_id.get(case_id)
        if case is None:
            raise ValueError(f"Replay lane {case_id!r} is not in the manifest")
        if lane.get("prompt") != case["prompt"]:
            raise ValueError(f"Replay lane {case_id!r} prompt does not match the manifest")
        events = lane.get("events")
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError(f"Replay lane {case_id!r} events must be a list of objects")
        if not isinstance(lane.get("final_answer"), str):
            raise ValueError(f"Replay lane {case_id!r} final_answer must be a string")
        metadata = lane.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"Replay lane {case_id!r} metadata must be an object")
        adjudication = lane.get("adjudication")
        if not isinstance(adjudication, dict):
            raise ValueError(f"Replay lane {case_id!r} adjudication must be an object")
        adjudication = {"case_id": case_id, **adjudication}
        _validate_semantic_adjudication(adjudication, case_id=case_id)

        lane_path = work_root / case_id
        lane_path.mkdir(parents=True, exist_ok=True)
        (lane_path / "events.jsonl").write_text(
            "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events) + "\n",
            encoding="utf-8",
        )
        (lane_path / "prompt.txt").write_text(lane["prompt"] + "\n", encoding="utf-8")
        (lane_path / "final-answer.txt").write_text(lane["final_answer"] + "\n", encoding="utf-8")
        (lane_path / "stderr.txt").write_text("", encoding="utf-8")
        (lane_path / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        specs.append(LaneSpec(case_id, lane_path))
        adjudications.append(adjudication)

    missing = sorted(set(manifest_by_id) - set(replay_by_id))
    if missing:
        raise ValueError("Replay fixture missing manifest lanes: " + ", ".join(missing))
    ledger = build_receiving_agent_trace_ledger(specs, repo_root=work_root)
    observations = observations_from_trace_ledger(ledger, adjudications)
    return evaluate_cases(manifest, observations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score history-skill invocation traces and adjudications.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--ledger", type=Path)
    inputs.add_argument("--replay-fixture", type=Path)
    parser.add_argument("--adjudications", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    if args.replay_fixture:
        if args.adjudications:
            parser.error("--adjudications cannot be used with --replay-fixture")
        replay_payload = json.loads(args.replay_fixture.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="history-skill-invocation-") as temp_dir:
            report = evaluate_replay_fixture(manifest, replay_payload, work_root=Path(temp_dir))
    else:
        if not args.adjudications:
            parser.error("--adjudications is required with --ledger")
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        adjudications_payload = json.loads(args.adjudications.read_text(encoding="utf-8"))
        adjudications = adjudications_payload.get("adjudications", adjudications_payload)
        if not isinstance(adjudications, list):
            raise ValueError("Adjudications must be a list or an object containing an adjudications list")
        if manifest.get("schema_version") == RECEIVER_ESCAPE_SCHEMA_VERSION:
            report = evaluate_receiver_escape_cases(
                manifest,
                receiver_escape_observations_from_trace_ledger(ledger, adjudications),
            )
        else:
            report = evaluate_cases(manifest, observations_from_trace_ledger(ledger, adjudications))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    report_key = (
        "receiver_escape_regression_eval"
        if manifest.get("schema_version") == RECEIVER_ESCAPE_SCHEMA_VERSION
        else "history_skill_invocation_eval"
    )
    return 0 if report[report_key]["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
