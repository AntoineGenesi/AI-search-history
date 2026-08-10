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


SCHEMA_VERSION = "history-skill-invocation-cases.v1"
REPORT_SCHEMA_VERSION = "history-skill-invocation-eval.v1"
REPLAY_SCHEMA_VERSION = "history-skill-invocation-replay.v1"
DEFAULT_MANIFEST = Path("benchmarks/history_skill_invocation/cases.json")
REQUIRED_SCENARIOS = {
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


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(payload)
    validate_manifest_sources(payload, repo_root=path.resolve().parents[2])
    return payload


def validate_manifest(payload: dict[str, Any]) -> None:
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
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

    missing = sorted(REQUIRED_SCENARIOS - scenarios)
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
        first_product_index = next(
            (index for index, entry in enumerate(command_order) if entry.get("jsonl_product_surface")),
            None,
        )
        after_first_product = command_order[first_product_index + 1 :] if first_product_index is not None else []
        audit_signals = lane.get("deep_audit_target_persistence") or {}
        latest_signals = lane.get("latest_status_freshness") or {}
        partial_packet_detected = bool(
            audit_signals.get("audit_continuation_workflow_seen")
            or latest_signals.get("not_answerable_yet_seen")
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
                    entry.get("jsonl_product_surface") for entry in after_first_product
                ),
                "manual_fallback_after_partial_packet": partial_packet_detected
                and any(entry.get("broad_search_or_listing") for entry in after_first_product),
            }
        )
    return observations


def evaluate_replay_fixture(
    manifest: dict[str, Any], replay_payload: dict[str, Any], *, work_root: Path
) -> dict[str, Any]:
    if replay_payload.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise ValueError(f"Replay schema_version must be {REPLAY_SCHEMA_VERSION!r}")
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
        report = evaluate_cases(manifest, observations_from_trace_ledger(ledger, adjudications))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["history_skill_invocation_eval"]["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
