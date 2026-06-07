from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = "normalized_counter_row.v1"
PRESENT = "present"
UNAVAILABLE = "unavailable"
ZERO_MEASURED_BASIS = "direct_measurement"

ALLOWED_AVAILABILITY = {PRESENT, UNAVAILABLE}
UNSUPPORTED_CLAIM_KEYS = (
    "speed",
    "token_efficiency",
    "user_benefit",
    "winner",
    "jsonl_vs_default",
    "broad_value",
    "scenario_a_or_a2_update",
    "inferred_zero_counters",
    "measurement_claim",
)

PRIVATE_TEXT_MARKERS = (
    "/home/",
    "/mnt/c/",
    "/mnt/d/",
    "C:\\",
    "D:\\",
    ".codex/",
    ".claude/",
    ".private/",
    "session_id",
    "raw_prompt",
    "raw_final_answer",
    "stdout_text",
    "stderr_text",
    "conversation.jsonl",
)

REQUIRED_CATEGORIES: dict[str, tuple[str, ...]] = {
    "row_identity": (
        "case_id",
        "lane",
        "scenario_label",
        "sequence",
        "run_id",
        "task_id",
    ),
    "execution_status": (
        "status",
        "pre_send_checks_passed",
        "pre_send_fail_reasons",
        "send_capable_codex_exec_attempts",
        "send_capable_command_started",
        "stop_reason",
        "not_run_reason",
    ),
    "no_send_materialization": (
        "prompt_input_exit_code",
        "valid_json",
        "required_prompt_path",
        "recursive_exact_prompt_count",
        "send_capable_command_count",
        "codex_exec_count",
    ),
    "live_runtime_observations": (
        "elapsed_seconds",
        "elapsed_seconds_source",
        "json_stdout_event_counts",
        "parse_errors",
        "host_wrapper_exit_code",
        "codex_exec_exit_code",
    ),
    "stream_and_final_answer_metadata": (
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "final_answer_bytes",
        "final_answer_sha256",
        "final_answer_parseable_json_object",
        "final_answer_json_object_keys",
    ),
    "token_usage": (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "usage_source",
    ),
    "answer_schema_retrieval": (
        "commands_or_tool_calls",
        "files_searched",
        "files_opened",
        "bytes_read",
        "bytes_parsed_or_scanned",
        "wall_time_seconds",
    ),
    "context_output_coordinator_cost": (
        "context_bytes",
        "output_bytes",
        "approx_context_tokens",
        "approx_output_tokens",
        "coordinator_context_cost",
    ),
    "adjudication_guardrails": (
        "correctness_label",
        "source_hit",
        "first_pass_source_hit",
        "answer_contract_required_fields_present",
        "unpreserved_available_facets",
        "missing_counter_policy",
    ),
    "privacy_contamination": (
        "same_case_boundary_pass",
        "unexpected_case_id_count",
        "unexpected_private_packet_id_count",
        "a_a2_marker_hit_count",
        "expectation_map_marker_hit_count",
        "retained_artifact_safe_count",
    ),
}

RAW_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "case_id": ("case_id",),
    "lane": ("lane", "scenario"),
    "scenario_label": ("scenario_label", "scenario"),
    "sequence": ("sequence", "case_index", "index"),
    "run_id": ("run_id",),
    "task_id": ("task_id",),
    "status": ("status", "row_status"),
    "pre_send_checks_passed": ("pre_send_checks_passed", "pre_send_passed"),
    "pre_send_fail_reasons": ("pre_send_fail_reasons", "fail_reasons"),
    "send_capable_codex_exec_attempts": ("send_capable_codex_exec_attempts", "codex_exec_attempts"),
    "send_capable_command_started": ("send_capable_command_started",),
    "stop_reason": ("stop_reason",),
    "not_run_reason": ("not_run_reason",),
    "prompt_input_exit_code": ("prompt_input_exit_code",),
    "valid_json": ("valid_json",),
    "required_prompt_path": ("required_prompt_path",),
    "recursive_exact_prompt_count": ("recursive_exact_prompt_count", "exact_prompt_count"),
    "send_capable_command_count": ("send_capable_command_count",),
    "codex_exec_count": ("codex_exec_count",),
    "elapsed_seconds": ("elapsed_seconds", "runtime_elapsed_seconds"),
    "elapsed_seconds_source": ("elapsed_seconds_source",),
    "json_stdout_event_counts": ("json_stdout_event_counts",),
    "parse_errors": ("parse_errors",),
    "host_wrapper_exit_code": ("host_wrapper_exit_code",),
    "codex_exec_exit_code": ("codex_exec_exit_code",),
    "stdout_bytes": ("stdout_bytes",),
    "stdout_sha256": ("stdout_sha256",),
    "stderr_bytes": ("stderr_bytes",),
    "stderr_sha256": ("stderr_sha256",),
    "final_answer_bytes": ("final_answer_bytes",),
    "final_answer_sha256": ("final_answer_sha256",),
    "final_answer_parseable_json_object": ("final_answer_parseable_json_object",),
    "final_answer_json_object_keys": ("final_answer_json_object_keys",),
    "input_tokens": ("input_tokens",),
    "cached_input_tokens": ("cached_input_tokens",),
    "output_tokens": ("output_tokens",),
    "reasoning_output_tokens": ("reasoning_output_tokens",),
    "usage_source": ("usage_source",),
    "commands_or_tool_calls": ("commands_or_tool_calls", "tool_calls"),
    "files_searched": ("files_searched",),
    "files_opened": ("files_opened",),
    "bytes_read": ("bytes_read",),
    "bytes_parsed_or_scanned": ("bytes_parsed_or_scanned", "bytes_scanned", "bytes_parsed"),
    "wall_time_seconds": ("wall_time_seconds",),
    "context_bytes": ("context_bytes",),
    "output_bytes": ("output_bytes",),
    "approx_context_tokens": ("approx_context_tokens",),
    "approx_output_tokens": ("approx_output_tokens",),
    "coordinator_context_cost": ("coordinator_context_cost",),
    "correctness_label": ("correctness_label", "per_case_correctness_guardrail"),
    "source_hit": ("source_hit",),
    "first_pass_source_hit": ("first_pass_source_hit", "first_pass_source_hit_guardrail"),
    "answer_contract_required_fields_present": ("answer_contract_required_fields_present",),
    "unpreserved_available_facets": ("unpreserved_available_facets",),
    "missing_counter_policy": ("missing_counter_policy",),
    "same_case_boundary_pass": ("same_case_boundary_pass",),
    "unexpected_case_id_count": ("unexpected_case_id_count",),
    "unexpected_private_packet_id_count": ("unexpected_private_packet_id_count",),
    "a_a2_marker_hit_count": ("a_a2_marker_hit_count",),
    "expectation_map_marker_hit_count": ("expectation_map_marker_hit_count",),
    "retained_artifact_safe_count": ("retained_artifact_safe_count",),
}


class NormalizedCounterError(ValueError):
    """Raised when normalized counter rows fail the strict no-inferred-zero contract."""


def available(value: Any, source: str, *, measurement_basis: str | None = None) -> dict[str, Any]:
    if source == UNAVAILABLE:
        raise NormalizedCounterError("present counter source must not be 'unavailable'")
    entry = {
        "availability": PRESENT,
        "value": deepcopy(value),
        "source": source,
        "unavailable_reason": None,
    }
    if measurement_basis is not None:
        entry["measurement_basis"] = measurement_basis
    if _is_numeric_zero(value) and measurement_basis != ZERO_MEASURED_BASIS:
        raise NormalizedCounterError("zero counter values require measurement_basis='direct_measurement'")
    return entry


def unavailable(reason: str, *, source: str = UNAVAILABLE) -> dict[str, Any]:
    if not str(reason or "").strip():
        raise NormalizedCounterError("unavailable counters require a reason")
    return {
        "availability": UNAVAILABLE,
        "value": None,
        "source": source,
        "unavailable_reason": str(reason),
    }


def build_normalized_counter_row(raw_row: dict[str, Any], *, task_id: str, run_id: str) -> dict[str, Any]:
    if not isinstance(raw_row, dict):
        raise NormalizedCounterError("raw_row must be a dictionary")
    flattened = _flatten_row(raw_row)
    flattened.setdefault("task_id", task_id)
    flattened.setdefault("run_id", run_id)

    categories: dict[str, dict[str, Any]] = {}
    for category, fields in REQUIRED_CATEGORIES.items():
        category_values: dict[str, Any] = {}
        for field in fields:
            found, value, source_key = _find_value(flattened, field)
            if not found or value is None:
                category_values[field] = unavailable(f"{field} unavailable in source row")
            else:
                basis = ZERO_MEASURED_BASIS if _is_numeric_zero(value) else None
                category_values[field] = available(value, f"raw_row.{source_key}", measurement_basis=basis)
        categories[category] = category_values

    row = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "run_id": run_id,
        "normalized_counters": categories,
        "unsupported_claims": {key: False for key in UNSUPPORTED_CLAIM_KEYS},
    }
    errors = validate_normalized_counter_row(row)
    if errors:
        raise NormalizedCounterError("; ".join(errors))
    return row


def attach_normalized_counter_rows(
    packet: dict[str, Any],
    *,
    task_id: str,
    run_id: str,
    row_key: str = "rows",
    output_key: str = "normalized_counter_rows",
) -> dict[str, Any]:
    if not isinstance(packet, dict):
        raise NormalizedCounterError("packet must be a dictionary")
    rows = packet.get(row_key)
    if rows is None:
        rows = packet.get("cases")
    if rows is None:
        rows = [packet]
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise NormalizedCounterError(f"{row_key} must be a list of dictionaries")
    packet[output_key] = [build_normalized_counter_row(row, task_id=task_id, run_id=run_id) for row in rows]
    return packet


def validate_normalized_counter_row(row: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(row, dict):
        return ["row must be a dictionary"]
    if row.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    counters = row.get("normalized_counters")
    if not isinstance(counters, dict):
        return errors + ["normalized_counters must be a dictionary"]

    for category, fields in REQUIRED_CATEGORIES.items():
        values = counters.get(category)
        if not isinstance(values, dict):
            errors.append(f"{category}: missing category")
            continue
        for field in fields:
            if field not in values:
                errors.append(f"{category}.{field}: missing field")
                continue
            errors.extend(_availability_errors(f"{category}.{field}", values[field]))

    extra_categories = sorted(set(counters) - set(REQUIRED_CATEGORIES))
    for category in extra_categories:
        errors.append(f"{category}: unexpected category")

    claims = row.get("unsupported_claims", {})
    if claims:
        if not isinstance(claims, dict):
            errors.append("unsupported_claims must be a dictionary")
        else:
            for key in UNSUPPORTED_CLAIM_KEYS:
                if claims.get(key) is not False:
                    errors.append(f"unsupported_claims.{key}: unsupported claim must be false")

    private_hits = private_text_hits(row)
    for hit in private_hits:
        errors.append(f"private marker detected at {hit}")
    return errors


def private_text_hits(value: Any, *, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            hits.extend(private_text_hits(item, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(private_text_hits(item, path=f"{path}[{index}]"))
    elif isinstance(value, str):
        if _looks_private_text(value):
            hits.append(path)
    return hits


def _availability_errors(label: str, entry: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(entry, dict):
        return [f"{label}: availability object required"]
    availability = entry.get("availability")
    value = entry.get("value")
    source = entry.get("source")
    reason = entry.get("unavailable_reason")
    if availability not in ALLOWED_AVAILABILITY:
        errors.append(f"{label}: availability must be present or unavailable")
    if not isinstance(source, str) or not source.strip():
        errors.append(f"{label}: source is required")
    if availability == UNAVAILABLE:
        if value is not None:
            errors.append(f"{label}: unavailable value must be null")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{label}: unavailable_reason is required")
    if availability == PRESENT:
        if reason is not None:
            errors.append(f"{label}: present unavailable_reason must be null")
        if source == UNAVAILABLE:
            errors.append(f"{label}: present source must not be unavailable")
        if _is_numeric_zero(value) and entry.get("measurement_basis") != ZERO_MEASURED_BASIS:
            errors.append(f"{label}: zero present value requires direct measurement basis")
    return errors


def _flatten_row(row: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dict) and key in {
            "live_metrics",
            "runtime_counters",
            "no_send_summary",
            "adjudication",
            "guardrails",
            "privacy",
            "answer_schema_retrieval",
        }:
            for nested_key, nested_value in value.items():
                flattened.setdefault(nested_key, nested_value)
        else:
            flattened[key] = value
    return flattened


def _find_value(flattened: dict[str, Any], field: str) -> tuple[bool, Any, str]:
    for key in RAW_FIELD_ALIASES.get(field, (field,)):
        if key in flattened:
            return True, flattened[key], key
    return False, None, field


def _is_numeric_zero(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _looks_private_text(text: str) -> bool:
    if any(marker in text for marker in PRIVATE_TEXT_MARKERS):
        return True
    path = PurePosixPath(text)
    return len(path.parts) >= 2 and path.parts[0] in {"home", "mnt"} and path.is_absolute()
