from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .conversation_records import HUMAN_AUTHORED_USER_MESSAGE, extract_human_ask
from .real_history_gold import validate_manifest


BRIDGE_SCHEMA_VERSION = 1
EXPECTED_REVIEW_STATUS = "reviewed_real_history_gold"
EXPECTED_SPEAKER_ORIGIN = "human_user"
HISTORICAL_ORIGINAL_CASE_COUNT = 18


@dataclass(frozen=True)
class BridgeValidationResult:
    errors: tuple[str, ...]
    report: dict[str, Any]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def validate_bridge_files(manifest_path: Path, bridge_path: Path) -> BridgeValidationResult:
    return validate_bridge(
        load_json(manifest_path),
        load_json(bridge_path),
    )


def validate_bridge(manifest: dict[str, Any], bridge: dict[str, Any]) -> BridgeValidationResult:
    errors: list[str] = []
    manifest_result = validate_manifest(manifest, source_label="real_history_gold_manifest")
    errors.extend(f"manifest: {error}" for error in manifest_result.errors)

    cases = manifest.get("cases") if isinstance(manifest, Mapping) else None
    manifest_cases = [case for case in cases if isinstance(case, dict)] if isinstance(cases, list) else []
    benchmark_cases = [
        case
        for case in manifest_cases
        if case.get("review_status") == EXPECTED_REVIEW_STATUS
        and case.get("downstream_applicability", {}).get("impact_benchmark") is True
    ]
    expected_case_count = len(manifest_cases)
    if expected_case_count == 0:
        errors.append("manifest must contain at least one case for this bridge")
    if len(benchmark_cases) != expected_case_count:
        errors.append("every manifest case must be benchmarkable and reviewed for this bridge")

    manifest_by_case_id: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(manifest_cases):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            continue
        manifest_by_case_id[case_id] = case
        if case.get("speaker_origin") != EXPECTED_SPEAKER_ORIGIN:
            errors.append(f"{case_id}: manifest speaker_origin must be human_user")
        if case.get("source_structure") != HUMAN_AUTHORED_USER_MESSAGE:
            errors.append(f"{case_id}: manifest source_structure must be human_authored_user_message")
        if case.get("review_status") != EXPECTED_REVIEW_STATUS:
            errors.append(f"{case_id}: manifest review_status must be reviewed_real_history_gold")
        if case.get("downstream_applicability", {}).get("impact_benchmark") is not True:
            errors.append(f"{case_id}: manifest impact_benchmark must be true")

    bridge_errors, bridge_cases = _load_bridge_cases(bridge)
    errors.extend(bridge_errors)
    bridge_by_case_id: dict[str, dict[str, Any]] = {}
    duplicate_bridge_ids: set[str] = set()
    for index, case in enumerate(bridge_cases):
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"bridge.cases[{index}]: case_id must be a non-empty string")
            continue
        if case_id in bridge_by_case_id:
            duplicate_bridge_ids.add(case_id)
        bridge_by_case_id[case_id] = case
    for case_id in sorted(duplicate_bridge_ids):
        errors.append(f"{case_id}: duplicate bridge case_id")

    manifest_ids = set(manifest_by_case_id)
    bridge_ids = set(bridge_by_case_id)
    for case_id in sorted(manifest_ids - bridge_ids):
        errors.append(f"{case_id}: missing from private bridge")
    for case_id in sorted(bridge_ids - manifest_ids):
        errors.append(f"{case_id}: bridge case is not present in manifest")

    case_reports: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    rejection_counts: Counter[str] = Counter()
    query_text_checks = Counter()
    resolved_ref_count = 0

    for case_id in sorted(manifest_ids & bridge_ids):
        manifest_case = manifest_by_case_id[case_id]
        bridge_case = bridge_by_case_id[case_id]
        refs = bridge_case.get("refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"{case_id}: bridge refs must be a non-empty list")
            status = "rejected"
            status_counts[status] += 1
            case_reports.append(_case_report(manifest_case, status, 0, []))
            continue

        expected_ref_ids = {
            ref.get("ref_id")
            for ref in manifest_case.get("expected_refs", [])
            if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
        }
        must_ref_ids = {
            ref.get("ref_id")
            for ref in manifest_case.get("must_find_refs", [])
            if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
        }
        bridge_ref_ids = {
            ref.get("ref_id")
            for ref in refs
            if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
        }
        if not must_ref_ids.issubset(bridge_ref_ids):
            errors.append(f"{case_id}: bridge refs must include every manifest must_find_ref")
        if not bridge_ref_ids.issubset(expected_ref_ids):
            errors.append(f"{case_id}: bridge refs include a ref_id outside expected_refs")

        case_labels: list[str] = []
        case_ref_errors: list[str] = []
        extracted_texts: list[str] = []
        for ref_index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                case_ref_errors.append(f"{case_id}: bridge ref {ref_index} must be an object")
                continue
            try:
                raw_record = _read_one_based_ref(ref)
            except json.JSONDecodeError:
                case_ref_errors.append(f"{case_id}: bridge ref {ref_index} is not valid JSON")
                rejection_counts["invalid_json"] += 1
                continue
            except OSError:
                case_ref_errors.append(f"{case_id}: bridge ref {ref_index} is unresolvable")
                rejection_counts["unresolvable_ref"] += 1
                continue

            extraction = extract_human_ask(raw_record)
            classification = extraction.classification
            resolved_ref_count += 1
            source_counts[classification.label] += 1
            confidence_counts[classification.confidence] += 1
            case_labels.append(classification.label)
            if classification.label != HUMAN_AUTHORED_USER_MESSAGE or classification.confidence != "high":
                case_ref_errors.append(f"{case_id}: bridge ref {ref_index} is {classification.label}/{classification.confidence}")
                rejection_counts[classification.label] += 1
                continue
            if extraction.ask_text is None:
                case_ref_errors.append(f"{case_id}: bridge ref {ref_index} did not extract human ask text")
                rejection_counts["missing_human_ask_text"] += 1
                continue
            extracted_texts.append(extraction.ask_text)
            query_text = ref.get("query_text", bridge_case.get("query_text"))
            if isinstance(query_text, str) and query_text:
                if _normalize_text(query_text) == _normalize_text(extraction.ask_text):
                    query_text_checks["matched"] += 1
                else:
                    case_ref_errors.append(f"{case_id}: bridge ref {ref_index} query_text does not match extracted ask")
                    rejection_counts["query_text_mismatch"] += 1
            else:
                query_text_checks["not_supplied"] += 1

        errors.extend(case_ref_errors)
        if case_ref_errors:
            status = "rejected" if any("unresolvable" in item or "not valid JSON" in item for item in case_ref_errors) else "quarantined"
        elif len(extracted_texts) == len(refs):
            status = "accepted"
        else:
            status = "quarantined"
        status_counts[status] += 1
        case_reports.append(_case_report(manifest_case, status, len(refs), case_labels))

    report = {
        "real_history_benchmark_bridge": {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "status": "ok" if not errors else "rejected",
            "manifest_case_count": len(manifest_cases),
            "benchmarkable_reviewed_case_count": len(benchmark_cases),
            "expected_case_count": expected_case_count,
            "historical_original_case_count": HISTORICAL_ORIGINAL_CASE_COUNT,
            "bridge_case_count": len(bridge_cases),
            "accepted_case_count": status_counts.get("accepted", 0),
            "quarantined_case_count": status_counts.get("quarantined", 0),
            "rejected_case_count": status_counts.get("rejected", 0),
            "resolved_ref_count": resolved_ref_count,
            "source_structure_counts": dict(sorted(source_counts.items())),
            "classifier_confidence_counts": dict(sorted(confidence_counts.items())),
            "query_text_check_counts": dict(sorted(query_text_checks.items())),
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "error_count": len(errors),
            "case_count_floor_status": _case_count_floor_status(expected_case_count),
            "privacy_contract": {
                "raw_private_text_committed": False,
                "raw_private_paths_committed": False,
                "local_usernames_committed": False,
                "raw_session_ids_committed": False,
                "raw_query_wording_committed": False,
                "raw_source_lines_committed": False,
                "vectors_or_indexes_committed": False,
            },
            "cases": case_reports,
        }
    }
    return BridgeValidationResult(errors=tuple(errors), report=report)


def _load_bridge_cases(bridge: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    if not isinstance(bridge, dict):
        return ["bridge root must be a JSON object"], []
    if bridge.get("schema_version") != BRIDGE_SCHEMA_VERSION:
        errors.append(f"bridge schema_version must be {BRIDGE_SCHEMA_VERSION}")
    cases = bridge.get("cases")
    if not isinstance(cases, list):
        errors.append("bridge cases must be a list")
        return errors, []
    bridge_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"bridge.cases[{index}] must be an object")
            continue
        bridge_cases.append(case)
    return errors, bridge_cases


def _read_one_based_ref(ref: dict[str, Any]) -> dict[str, Any]:
    path_value = ref.get("path")
    line_value = ref.get("line")
    if not isinstance(path_value, str) or not isinstance(line_value, int) or line_value < 1:
        raise OSError("ref requires path string and positive one-based line integer")
    path = Path(path_value)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number == line_value:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise OSError("line JSON must be an object")
                return payload
    raise OSError("line not found")


def _case_report(manifest_case: dict[str, Any], status: str, bridge_ref_count: int, source_labels: list[str]) -> dict[str, Any]:
    return {
        "case_id": manifest_case.get("case_id"),
        "family": manifest_case.get("family"),
        "status": status,
        "speaker_origin": manifest_case.get("speaker_origin"),
        "manifest_source_structure": manifest_case.get("source_structure"),
        "bridge_ref_count": bridge_ref_count,
        "source_structure_labels": sorted(set(source_labels)),
    }


def _normalize_text(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def _case_count_floor_status(case_count: int) -> str:
    if case_count < 20:
        return "below_old_20_50_target_floor"
    if case_count <= 50:
        return "within_old_20_50_target_band"
    return "above_old_20_50_target_band"


def render_bridge_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def render_bridge_text(report: dict[str, Any]) -> str:
    payload = report["real_history_benchmark_bridge"]
    if payload["status"] == "ok":
        return (
            "OK: real-history benchmark bridge valid "
            f"({payload['accepted_case_count']}/{payload['expected_case_count']} cases accepted; "
            f"{payload['quarantined_case_count']} quarantined; {payload['rejected_case_count']} rejected)"
        )
    return (
        "ERROR: real-history benchmark bridge rejected "
        f"({payload['error_count']} errors; {payload['accepted_case_count']} accepted; "
        f"{payload['quarantined_case_count']} quarantined; {payload['rejected_case_count']} rejected)"
    )
