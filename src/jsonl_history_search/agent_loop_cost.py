from __future__ import annotations

import hashlib
import json
import shlex
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "jsh-725-agent-loop-route-counter-contract.v1"
SOURCE_REF_RETENTION_SCHEMA_VERSION = "jsh-748a-held-out-loop-source-ref-retention-contract.v1"
RETAINED_TRACE_COUNTER_SCHEMA_VERSION = "jsh-756-held-out-retained-trace-counters.v1"
DEFAULT_CASE_SELECTION_PATH = Path("benchmarks/paired_agent/jsh-724-held-out-agent-loop-case-selection.json")
DEFAULT_OUTPUT_PATH = Path("benchmarks/paired_agent/jsh-725-agent-loop-route-counter-contract.json")
DEFAULT_SOURCE_REF_RETENTION_OUTPUT_PATH = Path(
    "benchmarks/paired_agent/jsh-748a-held-out-loop-source-ref-retention-contract.json"
)
DEFAULT_RETAINED_TRACE_COUNTER_OUTPUT_PATH = Path("benchmarks/paired_agent/jsh-756-held-out-retained-trace-counters.json")
DEFAULT_RETAINED_TRACE_COUNTER_REPORT_PATH = Path(
    "reports/jsh-756-offline-retained-trace-counter-extractor-for-held-out-loop.md"
)
DEFAULT_JSH747_PACKET_PATH = Path(
    "benchmarks/paired_agent/jsh-747-adjudicate-jsh-743-repaired-route-live-trace-after-protocol-reclassification.json"
)
UNAVAILABLE = "unavailable"

RETAINED_TRACE_ALLOWED_FILES = (
    "stdout.jsonl",
    "stderr.txt",
    "command-log.jsonl",
    "counters.json",
    "prompt-view.json",
    "final-answer.txt",
    "runtime-trace-retention.json",
)

LANES = (
    "manual_newest_first",
    "raw_rg",
    "package_search",
    "package_search_rg_prefilter",
)

LOCAL_TOOL_COUNTERS = (
    "tool_calls",
    "subprocess_calls",
    "files_enumerated",
    "files_searched",
    "files_opened",
    "bytes_scanned_or_parsed",
    "bytes_read",
    "elapsed_wall_time_seconds",
)

MODEL_FACING_COUNTERS = (
    "prompt_input_bytes",
    "prompt_input_sha256",
    "prompt_input_approx_tokens",
    "tool_stdout_bytes_shown",
    "opened_context_bytes_shown",
    "handoff_packet_bytes_shown",
    "diagnostic_bytes_shown",
    "total_model_facing_input_bytes",
)

FULL_AGENT_LOOP_COUNTERS = (
    "turns_to_first_evidence",
    "total_turns",
    "failed_search_attempts",
    "query_revisions",
    "model_input_tokens",
    "cached_input_tokens",
    "model_output_tokens",
    "reasoning_output_tokens",
    "final_answer_bytes",
    "total_elapsed_wall_time_seconds",
)

FORBIDDEN_MATERIAL = (
    "answer_keys",
    "expected_refs",
    "expected_answers",
    "source_maps",
    "source_windows",
    "precomputed_jsonl_outputs",
    "coordinator_precomputed_packets",
    "adjudication_notes",
)

PACKAGE_COMMANDS = (
    "python3 -m jsonl_history_search.cli find",
    "python3 -m jsonl_history_search.cli semantic-debug",
)

SOURCE_REF_RETENTION_FIELDS = (
    "durable_committed_safe_source_id_refs",
    "retained_private_trace_mapping",
    "temporary_sandbox_refs",
    "missing_refs",
    "unsupported_refs",
    "adjudication_fallback_basis",
)

ELIGIBLE_MEASURED_MECHANISMS = (
    "sidecars",
    "embeddings",
    "generated_metadata",
    "rg",
    "scanner",
    "adapter",
    "package_search",
)


def build_agent_loop_cost_contract_payload(case_selection_path: Path = DEFAULT_CASE_SELECTION_PATH) -> dict[str, Any]:
    selection = _read_selection(case_selection_path)
    cases = _case_summaries(selection)
    lanes = [_lane_contract(lane) for lane in LANES]
    prompts = [_prompt_materialization(case, lane) for case in cases for lane in lanes]
    contract = {
        "schema_version": SCHEMA_VERSION,
        "task_id": "JSH-725",
        "artifact_kind": "held_out_agent_loop_no_send_route_counter_contract",
        "status": "no_send_contract_ready",
        "case_selection_source": str(case_selection_path),
        "held_out_case_count": len(cases),
        "no_send_boundary": {
            "live_model_sends_performed": False,
            "codex_exec_or_subprocess_live_sends_started": False,
            "adjudication_performed": False,
            "private_source_windows_materialized": False,
            "answer_keys_loaded": False,
            "route_artifact_only": True,
        },
        "scope_boundary": {
            "defines_runnable_lanes": True,
            "captures_live_traces": False,
            "compares_quality_or_cost": False,
            "claims_jsonl_superiority": False,
            "claims_token_efficiency": False,
        },
        "cases": cases,
        "strategy_lanes": lanes,
        "counter_schema": _counter_schema(),
        "source_ref_retention_quality": _source_ref_retention_quality_contract(),
        "eligible_measured_mechanisms": _eligible_measured_mechanisms(),
        "leakage_gates": _leakage_gates(),
        "package_lane_runtime_contract": {
            "installed_live_jsonl_required": True,
            "coordinator_precomputed_packet_allowed": False,
            "allowed_entrypoints": list(PACKAGE_COMMANDS),
            "proof_required_in_jsh_726": [
                "which jsonl-history-search or python module import path inside isolated run",
                "validate-install result inside run before package lane search",
                "command transcript showing package/helper invocation occurred after prompt start",
                "no committed/private route packet copied from coordinator as search output",
            ],
        },
        "prompt_materialization": {
            "prompt_text_committed": False,
            "materialization_count": len(prompts),
            "hash_algorithm": "sha256_canonical_json",
            "proof_rows": prompts,
        },
        "prompt_hash_stability": {
            "canonical_json": {
                "ensure_ascii": False,
                "sort_keys": True,
                "separators": [",", ":"],
            },
            "contract_hash_sha256": _sha256(
                {
                    "schema_version": SCHEMA_VERSION,
                    "cases": cases,
                    "strategy_lanes": lanes,
                    "counter_schema": _counter_schema(),
                    "source_ref_retention_quality": _source_ref_retention_quality_contract(),
                    "eligible_measured_mechanisms": _eligible_measured_mechanisms(),
                    "leakage_gates": _leakage_gates(),
                    "prompt_materialization_rows": prompts,
                }
            ),
        },
        "next_task_contract": {
            "task_id": "JSH-726",
            "live_trace_capture_authorized_by_this_artifact": False,
            "required_before_live_send": [
                "review this no-send artifact",
                "materialize private prompts from these prompt view hashes",
                "run same-run no-send leakage gates",
                "attach separated counter capture to trace runner",
            ],
        },
        "privacy_contract": {
            "committed_artifact_contains_raw_private_text": False,
            "committed_artifact_contains_raw_prompt_text": False,
            "committed_artifact_contains_source_windows": False,
            "committed_artifact_contains_answer_keys": False,
            "committed_artifact_contains_absolute_private_paths": False,
        },
    }
    return {"agent_loop_cost_contract": contract}


def write_agent_loop_cost_contract(output_path: Path, case_selection_path: Path = DEFAULT_CASE_SELECTION_PATH) -> dict[str, Any]:
    payload = build_agent_loop_cost_contract_payload(case_selection_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload["agent_loop_cost_contract"], indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def build_source_ref_retention_contract_payload(
    case_selection_path: Path = DEFAULT_CASE_SELECTION_PATH,
    jsh747_packet_path: Path = DEFAULT_JSH747_PACKET_PATH,
) -> dict[str, Any]:
    base = build_agent_loop_cost_contract_payload(case_selection_path)["agent_loop_cost_contract"]
    seed = _jsh747_metric_seed(_read_optional_json(jsh747_packet_path), jsh747_packet_path)
    contract = {
        "schema_version": SOURCE_REF_RETENTION_SCHEMA_VERSION,
        "task_id": "JSH-748a",
        "artifact_kind": "held_out_agent_loop_source_ref_retention_metric_contract",
        "status": "no_send_metric_contract_ready",
        "created_date": "2026-06-13",
        "reuse_foundations": {
            "jsh_724_case_selection_source": str(case_selection_path),
            "jsh_724_case_count": base["held_out_case_count"],
            "jsh_724_case_ids": [case["case_id"] for case in base["cases"]],
            "jsh_725_lane_contract_source": str(DEFAULT_OUTPUT_PATH),
            "jsh_725_lanes": [lane["lane_id"] for lane in base["strategy_lanes"]],
            "jsh_725_counter_buckets_reused": [
                "local_tool_work",
                "model_facing_context_input",
                "full_agent_loop_cost",
            ],
        },
        "no_send_boundary": {
            "live_prompt_sends_performed_by_jsh_748a": 0,
            "live_model_sends_performed_by_jsh_748a": 0,
            "live_retries_performed_by_jsh_748a": 0,
            "send_capable_codex_exec_run_by_jsh_748a": False,
            "private_upload_performed": False,
            "route_repair_performed": False,
            "scanner_ranking_or_package_behavior_changed": False,
            "offline_contract_artifact_only": True,
        },
        "metric_boundary": {
            "local_tool_work_bucket": "separate",
            "model_facing_context_input_bucket": "separate",
            "full_agent_loop_cost_bucket": "separate",
            "source_ref_retention_quality_bucket": "separate_quality_metric_not_a_token_or_cost_counter",
            "local_byte_reductions_are_token_savings": False,
            "token_savings_claim": False,
            "quality_or_winner_claim": False,
        },
        "source_ref_retention_quality": _source_ref_retention_quality_contract(),
        "jsh_747_metric_seed": seed,
        "eligible_measured_mechanisms": _eligible_measured_mechanisms(),
        "row_contract": {
            "required_per_row_fields": [
                "case_id",
                "lane_id",
                "final_answer_correctness",
                "evidence_source_correctness",
                "source_ref_retention_quality",
                "cost_buckets",
            ],
            "cost_buckets": [
                "local_tool_work",
                "model_facing_context_input",
                "full_agent_loop_cost",
            ],
            "source_ref_retention_quality_fields": list(SOURCE_REF_RETENTION_FIELDS),
            "missing_values_literal": UNAVAILABLE,
            "adjudication_must_not_substitute_for_missing_source_refs": True,
            "adjudication_fallback_must_name_retained_basis": True,
        },
        "privacy_contract": {
            "committed_artifact_contains_raw_private_text": False,
            "committed_artifact_contains_raw_prompt_text": False,
            "committed_artifact_contains_source_windows": False,
            "committed_artifact_contains_answer_keys": False,
            "committed_artifact_contains_absolute_private_paths": False,
            "committed_artifact_contains_temporary_sandbox_paths": False,
        },
    }
    return {"source_ref_retention_contract": contract}


def write_source_ref_retention_contract(
    output_path: Path,
    case_selection_path: Path = DEFAULT_CASE_SELECTION_PATH,
    jsh747_packet_path: Path = DEFAULT_JSH747_PACKET_PATH,
) -> dict[str, Any]:
    payload = build_source_ref_retention_contract_payload(case_selection_path, jsh747_packet_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload["source_ref_retention_contract"], indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def build_retained_trace_counter_payload(
    trace_root: Path,
    jsh747_packet_path: Path = DEFAULT_JSH747_PACKET_PATH,
) -> dict[str, Any]:
    root = Path(trace_root)
    counters = _read_optional_json(root / "counters.json")
    jsh747 = _read_optional_json(jsh747_packet_path)
    command_rows = _read_jsonl_objects(root / "command-log.jsonl")
    stderr_events = _parse_stderr_events(root / "stderr.txt")
    runtime_manifest = _read_optional_json(root / "runtime-trace-retention.json")
    runtime_stats = _runtime_jsonl_stats(root, runtime_manifest)
    file_evidence = _retained_file_evidence(root)
    command_summary = _command_log_summary(command_rows)
    event_summary = _stderr_event_summary(stderr_events)
    token_usage = event_summary["token_usage"]
    jsh747_cost = jsh747.get("cost_counters", {}).get("jsh_743_benchmark_loop_cost", {})
    source_ref_quality = _jsh747_metric_seed(jsh747, jsh747_packet_path).get(
        "source_ref_retention_quality", UNAVAILABLE
    )

    packet = {
        "schema_version": RETAINED_TRACE_COUNTER_SCHEMA_VERSION,
        "task_id": "JSH-756",
        "artifact_kind": "offline_retained_trace_counter_packet",
        "created_date": "2026-06-13",
        "source_label": "jsh743_jsh747_clean_held_out_row",
        "scope": {
            "row_id": "heldout-agent-action-proof-001::manual_newest_first",
            "case_id": "heldout-agent-action-proof-001",
            "lane_id": "manual_newest_first",
            "source_tasks": ["JSH-743", "JSH-747"],
            "offline_retained_trace_extraction_only": True,
            "live_sends_performed_by_jsh_756": 0,
            "send_capable_codex_exec_run_by_jsh_756": False,
            "private_upload_performed": False,
            "jsh_748b_c_d_resumed": False,
            "final_answer_correctness_readjudicated": False,
            "scanner_ranking_rg_sidecar_embedding_metadata_tuning_performed": False,
        },
        "input_evidence": {
            "retained_trace_root_committed": False,
            "raw_private_paths_committed": False,
            "allowed_file_count": len(RETAINED_TRACE_ALLOWED_FILES),
            "allowed_files": file_evidence,
            "command_log_rows": command_summary,
            "stderr_event_summary": event_summary["safe_summary"],
            "runtime_jsonl_evidence": runtime_stats,
        },
        "counter_buckets": {
            "local_tool_work": {
                "tool_calls": _measured_counter(
                    "tool_calls",
                    event_summary["function_call_count"],
                    "stderr parseable Codex response_item function_call events",
                    _hash_evidence("stderr_function_call_event_lines", event_summary["function_call_lines_sha256"]),
                ),
                "subprocess_calls": _measured_counter(
                    "subprocess_calls",
                    command_summary["live_codex_exec_rows"],
                    "command-log rows labelled live-codex-exec",
                    _hash_evidence("command_log_sha256", file_evidence["command-log.jsonl"]["sha256"]),
                ),
                "command_count": _measured_counter(
                    "command_count",
                    command_summary["row_count"],
                    "command-log.jsonl retained row count",
                    _hash_evidence("command_log_sha256", file_evidence["command-log.jsonl"]["sha256"]),
                ),
                "exec_command_function_calls": _measured_counter(
                    "exec_command_function_calls",
                    event_summary["function_call_names"].get("exec_command", 0),
                    "stderr parseable function_call name counts",
                    {"function_call_name_counts": event_summary["function_call_names"]},
                ),
                "function_call_outputs": _measured_counter(
                    "function_call_outputs",
                    event_summary["function_call_output_count"],
                    "stderr parseable function_call_output events",
                    _hash_evidence("function_call_output_aggregate_sha256", event_summary["function_output_sha256"]),
                ),
                "stdout_bytes": _file_counter("stdout_bytes", file_evidence["stdout.jsonl"]),
                "stderr_bytes": _file_counter("stderr_bytes", file_evidence["stderr.txt"]),
                "retained_runtime_jsonl_line_count": runtime_stats["line_count"],
                "retained_runtime_jsonl_bytes": runtime_stats["bytes"],
                "elapsed_wall_time_seconds": _counter_from(
                    counters,
                    ("local_tool_work", "elapsed_wall_time_seconds"),
                    "retained counters.json local_tool_work.elapsed_wall_time_seconds",
                ),
                "files_enumerated": _unavailable_counter("files_enumerated"),
                "files_searched": _unavailable_counter("files_searched"),
                "files_opened": _unavailable_counter("files_opened"),
                "bytes_scanned_or_parsed": _unavailable_counter("bytes_scanned_or_parsed"),
                "bytes_read": _unavailable_counter("bytes_read"),
            },
            "model_facing_context_input": {
                "prompt_input_bytes": _counter_from(
                    counters,
                    ("model_facing_context_input", "prompt_input_bytes"),
                    "retained counters.json model_facing_context_input.prompt_input_bytes",
                ),
                "prompt_input_sha256": _counter_from(
                    counters,
                    ("model_facing_context_input", "prompt_input_sha256"),
                    "retained counters.json model_facing_context_input.prompt_input_sha256",
                ),
                "prompt_view_file_bytes": _file_counter("prompt_view_file_bytes", file_evidence["prompt-view.json"]),
                "prompt_view_file_sha256": _file_hash_counter(
                    "prompt_view_file_sha256", file_evidence["prompt-view.json"]
                ),
                "tool_stdout_bytes_shown": _measured_counter(
                    "tool_stdout_bytes_shown",
                    event_summary["function_output_bytes"],
                    "stderr function_call_output payload byte total",
                    _hash_evidence("function_call_output_aggregate_sha256", event_summary["function_output_sha256"]),
                ),
                "opened_context_bytes_shown": _measured_counter(
                    "opened_context_bytes_shown",
                    event_summary["output_bytes_by_command"].get("head", 0),
                    "function_call_output bytes paired with head commands",
                    {"command_output_byte_buckets": event_summary["output_bytes_by_command"]},
                ),
                "handoff_packet_bytes_shown": _unavailable_counter("handoff_packet_bytes_shown"),
                "diagnostic_bytes_shown": _measured_counter(
                    "diagnostic_bytes_shown",
                    event_summary["output_bytes_by_command"].get("ls", 0)
                    + event_summary["output_bytes_by_command"].get("find", 0),
                    "function_call_output bytes paired with ls/find commands",
                    {"command_output_byte_buckets": event_summary["output_bytes_by_command"]},
                ),
                "total_model_facing_input_bytes": _measured_counter(
                    "total_model_facing_input_bytes",
                    _numeric_counter_value(counters, ("model_facing_context_input", "prompt_input_bytes"))
                    + event_summary["function_output_bytes"],
                    "prompt_input_bytes plus stderr function_call_output payload bytes",
                    {
                        "prompt_source": "retained counters.json prompt_input_bytes",
                        "tool_output_source": "stderr function_call_output payload byte total",
                    },
                ),
            },
            "full_agent_loop_cost": {
                "codex_exec_prompt_sends_performed": _measured_counter(
                    "codex_exec_prompt_sends_performed",
                    command_summary["live_prompt_send_rows"],
                    "command-log live_prompt_send markers",
                    _hash_evidence("command_log_sha256", file_evidence["command-log.jsonl"]["sha256"]),
                ),
                "live_model_sends_performed": _counter_from(
                    jsh747_cost,
                    ("full_agent_loop_cost", "live_model_sends_performed"),
                    "JSH-747 reviewed cost packet",
                ),
                "retries_performed": _counter_from(
                    jsh747_cost,
                    ("full_agent_loop_cost", "retries_performed"),
                    "JSH-747 reviewed cost packet",
                ),
                "codex_exec_event_count": _measured_counter(
                    "codex_exec_event_count",
                    command_summary["live_codex_exec_rows"],
                    "command-log rows labelled live-codex-exec",
                    _hash_evidence("command_log_sha256", file_evidence["command-log.jsonl"]["sha256"]),
                ),
                "final_answer_bytes": _file_counter("final_answer_bytes", file_evidence["final-answer.txt"]),
                "final_answer_sha256": _file_hash_counter("final_answer_sha256", file_evidence["final-answer.txt"]),
                "total_elapsed_wall_time_seconds": _counter_from(
                    counters,
                    ("full_agent_loop_cost", "total_elapsed_wall_time_seconds"),
                    "retained counters.json full_agent_loop_cost.total_elapsed_wall_time_seconds",
                ),
                "model_input_tokens": _token_counter("model_input_tokens", token_usage, "input_tokens"),
                "cached_input_tokens": _token_counter("cached_input_tokens", token_usage, "cached_input_tokens"),
                "model_output_tokens": _token_counter("model_output_tokens", token_usage, "output_tokens"),
                "reasoning_output_tokens": _token_counter(
                    "reasoning_output_tokens", token_usage, "reasoning_output_tokens"
                ),
                "total_model_tokens": _token_counter("total_model_tokens", token_usage, "total_tokens"),
                "observable_turn_context_events": _measured_counter(
                    "observable_turn_context_events",
                    event_summary["payload_type_counts"].get("turn_context", 0)
                    + event_summary["top_event_type_counts"].get("turn_context", 0),
                    "stderr top-level and payload type counts",
                    {
                        "payload_type_counts": event_summary["payload_type_counts"],
                        "top_event_type_counts": event_summary["top_event_type_counts"],
                    },
                ),
                "observable_search_attempt_proxy_count": _measured_counter(
                    "observable_search_attempt_proxy_count",
                    event_summary["search_attempt_proxy_count"],
                    "stderr function_call command first-word categories rg/grep/find",
                    {"function_call_command_first_words": event_summary["command_first_words"]},
                ),
                "total_turns": _unavailable_counter("total_turns"),
                "turns_to_first_evidence": _unavailable_counter("turns_to_first_evidence"),
                "failed_search_attempts": _unavailable_counter("failed_search_attempts"),
                "query_revisions": _unavailable_counter("query_revisions"),
            },
            "source_ref_retention_quality": source_ref_quality,
        },
        "privacy_boundary": {
            "raw_private_transcript_text_committed": False,
            "raw_prompt_text_committed": False,
            "raw_final_answer_text_committed": False,
            "raw_source_snippets_committed": False,
            "raw_private_paths_committed": False,
            "session_ids_committed": False,
            "source_windows_committed": False,
            "answer_keys_committed": False,
            "expected_refs_committed": False,
            "private_bridge_payloads_committed": False,
            "evaluator_only_raw_material_committed": False,
        },
        "unavailable_policy": "unobserved counters remain literal unavailable, not zero",
        "selected_next_value_producing_action": "plan_next_clean_held_out_measurement_row_using_jsh_756_counter_packet",
    }
    return {"retained_trace_counters": packet}


def write_retained_trace_counter_packet(
    output_path: Path,
    trace_root: Path,
    jsh747_packet_path: Path = DEFAULT_JSH747_PACKET_PATH,
) -> dict[str, Any]:
    payload = build_retained_trace_counter_payload(trace_root, jsh747_packet_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload["retained_trace_counters"], indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _retained_file_evidence(root: Path) -> dict[str, Any]:
    return {name: _file_stats(root / name) for name in RETAINED_TRACE_ALLOWED_FILES}


def _file_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "source_id": path.name,
            "retained_filename": path.name,
            "availability": UNAVAILABLE,
            "bytes": UNAVAILABLE,
            "line_count": UNAVAILABLE,
            "sha256": UNAVAILABLE,
            "raw_text_committed": False,
        }
    data = path.read_bytes()
    return {
        "source_id": path.name,
        "retained_filename": path.name,
        "availability": "measured",
        "bytes": len(data),
        "line_count": _line_count_bytes(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "raw_text_committed": False,
    }


def _line_count_bytes(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _read_jsonl_objects(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _command_log_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(str(row.get("label", UNAVAILABLE)) for row in rows)
    return {
        "row_count": len(rows),
        "live_prompt_send_rows": sum(1 for row in rows if row.get("live_prompt_send") is True),
        "live_codex_exec_rows": labels.get("live-codex-exec", 0),
        "label_counts": dict(sorted(labels.items())),
        "argv_text_committed": False,
        "argv_sha256_count": sum(1 for row in rows if row.get("argv_sha256")),
    }


def _parse_stderr_events(path: Path) -> list[dict[str, Any]]:
    events = []
    if not path.exists():
        return events
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        stripped = line.strip()
        if not (stripped.startswith("{") and stripped.endswith("}")):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        events.append(
            {
                "line_number": line_number,
                "line": stripped,
                "event_type": str(obj.get("type", UNAVAILABLE)),
                "payload": payload,
            }
        )
    return events


def _stderr_event_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    payload_type_counts: Counter[str] = Counter()
    top_event_type_counts: Counter[str] = Counter()
    function_call_names: Counter[str] = Counter()
    command_first_words: Counter[str] = Counter()
    function_call_lines: list[bytes] = []
    function_outputs: list[bytes] = []
    output_bytes_by_call: dict[str, int] = {}
    command_by_call: dict[str, str] = {}
    token_usage: dict[str, Any] = {}

    for event in events:
        payload = event["payload"]
        top_event_type_counts[str(event.get("event_type", UNAVAILABLE))] += 1
        payload_type = str(payload.get("type", UNAVAILABLE))
        payload_type_counts[payload_type] += 1
        if payload_type == "function_call":
            function_call_lines.append(event["line"].encode("utf-8"))
            name = str(payload.get("name", UNAVAILABLE))
            function_call_names[name] += 1
            call_id = str(payload.get("call_id", ""))
            first_word = _function_call_first_word(payload.get("arguments"))
            command_first_words[first_word or UNAVAILABLE] += 1
            if call_id:
                command_by_call[call_id] = first_word
        elif payload_type == "function_call_output":
            output = payload.get("output", "")
            output_bytes = str(output).encode("utf-8")
            function_outputs.append(output_bytes)
            call_id = str(payload.get("call_id", ""))
            if call_id:
                output_bytes_by_call[call_id] = len(output_bytes)
        elif payload_type == "token_count":
            info = payload.get("info", {})
            if isinstance(info, dict):
                usage = info.get("total_token_usage") or info.get("last_token_usage") or {}
                if isinstance(usage, dict):
                    token_usage = usage

    output_bytes_by_command: Counter[str] = Counter()
    for call_id, byte_count in output_bytes_by_call.items():
        output_bytes_by_command[command_by_call.get(call_id, UNAVAILABLE) or UNAVAILABLE] += byte_count

    function_output_bytes = sum(len(output) for output in function_outputs)
    safe_summary = {
        "parseable_json_event_count": len(events),
        "payload_type_counts": dict(sorted(payload_type_counts.items())),
        "top_event_type_counts": dict(sorted(top_event_type_counts.items())),
        "function_call_count": sum(function_call_names.values()),
        "function_call_output_count": payload_type_counts.get("function_call_output", 0),
        "function_call_names": dict(sorted(function_call_names.items())),
        "function_call_command_first_words": dict(sorted(command_first_words.items())),
        "function_call_raw_arguments_committed": False,
        "function_call_output_raw_text_committed": False,
        "token_count_event_count": payload_type_counts.get("token_count", 0),
    }
    return {
        "safe_summary": safe_summary,
        "payload_type_counts": dict(sorted(payload_type_counts.items())),
        "top_event_type_counts": dict(sorted(top_event_type_counts.items())),
        "function_call_count": sum(function_call_names.values()),
        "function_call_output_count": payload_type_counts.get("function_call_output", 0),
        "function_call_names": dict(sorted(function_call_names.items())),
        "command_first_words": dict(sorted(command_first_words.items())),
        "function_call_lines_sha256": hashlib.sha256(b"".join(function_call_lines)).hexdigest(),
        "function_output_bytes": function_output_bytes,
        "function_output_sha256": hashlib.sha256(b"".join(function_outputs)).hexdigest(),
        "output_bytes_by_command": dict(sorted(output_bytes_by_command.items())),
        "search_attempt_proxy_count": sum(
            count for command, count in command_first_words.items() if command in {"find", "grep", "rg"}
        ),
        "token_usage": token_usage,
    }


def _function_call_first_word(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return UNAVAILABLE
    if not isinstance(arguments, dict):
        return UNAVAILABLE
    cmd = arguments.get("cmd")
    if not isinstance(cmd, str) or not cmd.strip():
        return UNAVAILABLE
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = cmd.split()
    return parts[0] if parts else UNAVAILABLE


def _runtime_jsonl_stats(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    raw_path = manifest.get("retained_runtime_sessions_private_path")
    if not raw_path:
        return {
            "availability": UNAVAILABLE,
            "file_count": UNAVAILABLE,
            "line_count": _unavailable_counter("retained_runtime_jsonl_line_count"),
            "bytes": _unavailable_counter("retained_runtime_jsonl_bytes"),
            "sha256": UNAVAILABLE,
            "manifest_runtime_session_jsonl_count": manifest.get("runtime_session_jsonl_count", UNAVAILABLE),
            "raw_private_path_committed": False,
            "session_ids_committed": False,
        }
    runtime_dir = Path(raw_path)
    if not runtime_dir.is_absolute():
        runtime_dir = root / runtime_dir
    files = sorted(runtime_dir.glob("*.jsonl")) if runtime_dir.exists() else []
    if not files:
        return {
            "availability": UNAVAILABLE,
            "file_count": UNAVAILABLE,
            "line_count": _unavailable_counter(
                "retained_runtime_jsonl_line_count",
                "runtime-trace-retention manifest present but JSONL files were not locally readable",
            ),
            "bytes": _unavailable_counter(
                "retained_runtime_jsonl_bytes",
                "runtime-trace-retention manifest present but JSONL files were not locally readable",
            ),
            "sha256": UNAVAILABLE,
            "manifest_runtime_session_jsonl_count": manifest.get("runtime_session_jsonl_count", UNAVAILABLE),
            "raw_private_path_committed": False,
            "session_ids_committed": False,
        }
    blobs = [path.read_bytes() for path in files]
    return {
        "availability": "measured",
        "file_count": len(files),
        "line_count": _measured_counter(
            "retained_runtime_jsonl_line_count",
            sum(_line_count_bytes(blob) for blob in blobs),
            "retained runtime session JSONL files",
            {"runtime_jsonl_file_count": len(files)},
        ),
        "bytes": _measured_counter(
            "retained_runtime_jsonl_bytes",
            sum(len(blob) for blob in blobs),
            "retained runtime session JSONL files",
            {"runtime_jsonl_file_count": len(files)},
        ),
        "sha256": hashlib.sha256(b"".join(blobs)).hexdigest(),
        "manifest_runtime_session_jsonl_count": manifest.get("runtime_session_jsonl_count", UNAVAILABLE),
        "raw_private_path_committed": False,
        "session_ids_committed": False,
    }


def _measured_counter(field: str, value: Any, source: str, evidence: Any | None = None) -> dict[str, Any]:
    counter: dict[str, Any] = {
        "availability": "measured",
        "field": field,
        "source": source,
        "value": value,
    }
    if evidence is not None:
        counter["evidence"] = evidence
    return counter


def _unavailable_counter(field: str, source: str = UNAVAILABLE) -> dict[str, Any]:
    return {
        "availability": UNAVAILABLE,
        "field": field,
        "source": source,
        "value": UNAVAILABLE,
    }


def _file_counter(field: str, stats: dict[str, Any]) -> dict[str, Any]:
    if stats.get("availability") != "measured":
        return _unavailable_counter(field)
    return _measured_counter(
        field,
        stats["bytes"],
        f"retained {stats['retained_filename']} file metadata",
        {"sha256": stats["sha256"], "line_count": stats["line_count"]},
    )


def _file_hash_counter(field: str, stats: dict[str, Any]) -> dict[str, Any]:
    if stats.get("availability") != "measured":
        return _unavailable_counter(field)
    return _measured_counter(
        field,
        stats["sha256"],
        f"retained {stats['retained_filename']} file metadata",
        {"bytes": stats["bytes"], "line_count": stats["line_count"]},
    )


def _counter_from(source_obj: dict[str, Any], path: tuple[str, ...], source_label: str) -> dict[str, Any]:
    current: Any = source_obj
    for part in path:
        if not isinstance(current, dict):
            return _unavailable_counter(path[-1])
        current = current.get(part)
    if not isinstance(current, dict) or current.get("value", UNAVAILABLE) == UNAVAILABLE:
        return _unavailable_counter(path[-1])
    field = str(current.get("field", path[-1]))
    return _measured_counter(field, current["value"], source_label, {"upstream_source": current.get("source", UNAVAILABLE)})


def _numeric_counter_value(source_obj: dict[str, Any], path: tuple[str, ...]) -> int:
    current: Any = source_obj
    for part in path:
        if not isinstance(current, dict):
            return 0
        current = current.get(part)
    if not isinstance(current, dict):
        return 0
    value = current.get("value", 0)
    return value if isinstance(value, int) else 0


def _token_counter(field: str, token_usage: dict[str, Any], source_key: str) -> dict[str, Any]:
    value = token_usage.get(source_key)
    if not isinstance(value, int):
        return _unavailable_counter(field)
    return _measured_counter(
        field,
        value,
        "stderr token_count total_token_usage event",
        {"token_usage_field": source_key},
    )


def _hash_evidence(label: str, value: str) -> dict[str, str]:
    return {label: value}


def _read_selection(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _case_summaries(selection: dict[str, Any]) -> list[dict[str, Any]]:
    cases = []
    for case in selection.get("held_out_cases", []):
        cases.append(
            {
                "case_id": case["case_id"],
                "family": case["family"],
                "difficulty": case["difficulty"],
                "source_kind_basis": list(case.get("source_kind_basis", [])),
                "coverage_dimensions": list(case.get("coverage_dimensions", [])),
                "expected_facet_labels_visible_to_lane": False,
                "false_positive_risk_labels_visible_to_lane": False,
                "private_artifact_path_visible_to_lane": False,
            }
        )
    return cases


def _lane_contract(lane: str) -> dict[str, Any]:
    base = {
        "lane_id": lane,
        "runnable_in_jsh_726": True,
        "answer_keys_allowed": False,
        "expected_refs_allowed": False,
        "precomputed_jsonl_outputs_allowed": False,
        "source_maps_allowed": False,
        "private_case_notes_allowed": False,
        "allowed_prompt_case_fields": [
            "case_id",
            "family",
            "difficulty",
            "source_kind_basis",
            "coverage_dimensions",
        ],
        "forbidden_material": list(FORBIDDEN_MATERIAL),
        "must_record_counters": ["local_tool_work", "model_facing_context_input", "full_agent_loop_cost"],
        "must_record_quality_metrics": ["source_ref_retention_quality"],
    }
    if lane == "manual_newest_first":
        base.update(
            {
                "strategy_family": "manual_baseline",
                "allowed_tools": ["find", "ls", "head", "sed", "tail", "python3"],
                "route_instructions": [
                    "Enumerate approved JSONL roots by modification time newest first.",
                    "Open bounded recent files and inspect user/assistant/tool records manually.",
                    "Do not use rg or JSONL package/helper outputs in this lane.",
                ],
                "jsonl_package_available": False,
                "rg_prefilter": False,
            }
        )
    elif lane == "raw_rg":
        base.update(
            {
                "strategy_family": "raw_shell_baseline",
                "allowed_tools": ["rg", "sed", "head", "python3"],
                "route_instructions": [
                    "Search approved roots with raw rg terms derived only from the prompt.",
                    "Open bounded source windows from raw rg hits.",
                    "Do not use JSONL package/helper outputs, source maps, or package query plans.",
                ],
                "jsonl_package_available": False,
                "rg_prefilter": True,
            }
        )
    elif lane == "package_search":
        base.update(
            {
                "strategy_family": "jsonl_assisted",
                "allowed_tools": [
                    "python3 -m jsonl_history_search.cli find",
                    "python3 -m jsonl_history_search.cli semantic-debug",
                ],
                "route_instructions": [
                    "Validate the installed/live JSONL package inside the isolated run.",
                    "Call the JSONL package/helper after prompt start using only prompt-visible case fields.",
                    "Do not consume coordinator-precomputed packets or private source maps.",
                ],
                "jsonl_package_available": True,
                "rg_prefilter": False,
            }
        )
    elif lane == "package_search_rg_prefilter":
        base.update(
            {
                "strategy_family": "jsonl_assisted",
                "allowed_tools": [
                    "python3 -m jsonl_history_search.cli find --rg-prefilter",
                    "python3 -m jsonl_history_search.cli semantic-debug --rg-prefilter",
                ],
                "route_instructions": [
                    "Validate the installed/live JSONL package inside the isolated run.",
                    "Use package search with opt-in rg prefilter only inside the run.",
                    "Do not consume coordinator-precomputed packets or private source maps.",
                ],
                "jsonl_package_available": True,
                "rg_prefilter": True,
            }
        )
    else:
        raise ValueError(f"unknown lane: {lane}")
    return base


def _counter_schema() -> dict[str, Any]:
    return {
        "missing_counter_literal": UNAVAILABLE,
        "zero_policy": "zero is valid only when directly measured as zero; missing or uninstrumented counters must be unavailable",
        "local_tool_work": {field: _counter_field(field) for field in LOCAL_TOOL_COUNTERS},
        "model_facing_context_input": {field: _counter_field(field) for field in MODEL_FACING_COUNTERS},
        "full_agent_loop_cost": {field: _counter_field(field) for field in FULL_AGENT_LOOP_COUNTERS},
        "row_status_fields": {
            "final_answer_correctness": UNAVAILABLE,
            "evidence_source_correctness": UNAVAILABLE,
            "source_ref_retention_quality": UNAVAILABLE,
            "source_hit_label": UNAVAILABLE,
            "failure_or_waste_label": UNAVAILABLE,
            "contamination_label": "not_evaluated_no_send",
        },
    }


def _counter_field(name: str) -> dict[str, str]:
    return {
        "availability": UNAVAILABLE,
        "value": UNAVAILABLE,
        "source": UNAVAILABLE,
        "field": name,
    }


def _source_ref_retention_quality_contract() -> dict[str, Any]:
    return {
        "metric_kind": "source_ref_retention_quality",
        "bucket_relation": "separate_from_local_tool_work_model_facing_context_input_and_full_agent_loop_cost",
        "missing_literal": UNAVAILABLE,
        "fields": {field: _source_ref_retention_field(field) for field in SOURCE_REF_RETENTION_FIELDS},
        "quality_labels": [
            "durable_source_refs_present",
            "retained_trace_mapping_only",
            "temporary_sandbox_refs_only",
            "source_refs_missing",
            "source_refs_unsupported",
            "not_evaluated_no_send",
        ],
        "durable_ref_definition": [
            "committed-safe source_id plus line/count/hash metadata",
            "or retained private trace mapping recorded by source_id/hash/count without raw private text",
            "not a temporary live-sandbox path alone",
        ],
        "jsh_747_seed_caveat": "correct answers and supported evidence can still have weak durable source-ref retention",
    }


def _source_ref_retention_field(name: str) -> dict[str, Any]:
    descriptions = {
        "durable_committed_safe_source_id_refs": (
            "Whether the answer or adjudication retained committed-safe source IDs, hashes, counts, or line metadata."
        ),
        "retained_private_trace_mapping": (
            "Whether ignored private proof maps answer/source claims to retained traces without committing raw private text."
        ),
        "temporary_sandbox_refs": "Whether refs point only to live sandbox or approved-history paths that are not durable review IDs.",
        "missing_refs": "Whether answer/source support lacks refs entirely.",
        "unsupported_refs": "Whether provided refs do not support the answer or source-use claim.",
        "adjudication_fallback_basis": "Named retained basis used when final-answer refs are weak, missing, temporary, or unsupported.",
    }
    return {
        "field": name,
        "availability": UNAVAILABLE,
        "value": UNAVAILABLE,
        "source": UNAVAILABLE,
        "description": descriptions[name],
    }


def _eligible_measured_mechanisms() -> dict[str, Any]:
    return {
        mechanism: {
            "eligible": True,
            "promotion_status": "measured_strategy_only_not_default",
            "must_preserve_bucket_separation": True,
            "must_preserve_no_send_boundary_until_reviewed_live_gate": True,
        }
        for mechanism in ELIGIBLE_MEASURED_MECHANISMS
    }


def _jsh747_metric_seed(jsh747: dict[str, Any], jsh747_packet_path: Path) -> dict[str, Any]:
    if not jsh747:
        return {
            "source_packet": str(jsh747_packet_path),
            "available": False,
            "reason": "JSH-747 packet unavailable while building contract",
        }
    evidence = jsh747.get("evidence_source_adjudication", {})
    return {
        "source_packet": str(jsh747_packet_path),
        "available": True,
        "row_id": jsh747.get("scope", {}).get("row_id", UNAVAILABLE),
        "final_answer_correctness": jsh747.get("final_answer_correctness", UNAVAILABLE),
        "evidence_source_correctness": jsh747.get("evidence_source_correctness", UNAVAILABLE),
        "seed_caveat": "correct answer can have weak source-ref durability",
        "source_ref_retention_quality": {
            "durable_committed_safe_source_id_refs": {
                "availability": "measured",
                "value": False,
                "source": "JSH-747 evidence_source_adjudication.source_refs_durable_in_final_answer",
            },
            "retained_private_trace_mapping": {
                "availability": "measured",
                "value": evidence.get("retained_trace_supports_source_use", UNAVAILABLE),
                "source": "JSH-747 retained JSH-743 runtime trace and sanitized source IDs",
            },
            "temporary_sandbox_refs": {
                "availability": "measured",
                "value": evidence.get("source_refs_durable_in_final_answer") is False,
                "source": "JSH-747 source_file_retention_weakness",
            },
            "missing_refs": {
                "availability": "measured",
                "value": False,
                "source": "JSH-747 final answer contained refs, but they were temporary",
            },
            "unsupported_refs": {
                "availability": "measured",
                "value": False,
                "source": "JSH-747 retained trace supports source use",
            },
            "adjudication_fallback_basis": {
                "availability": "measured",
                "value": "retained_jsh743_runtime_trace_hashes_and_jsh747_private_adjudication_proof",
                "source": "JSH-747 adjudication packet",
            },
        },
    }


def _leakage_gates() -> dict[str, Any]:
    return {
        "gates_apply_to_all_lanes": [
            "prompt_must_not_include_expected_facet_labels",
            "prompt_must_not_include_private_artifact_paths",
            "prompt_must_not_include_source_basis_refs",
            "prompt_must_not_include_answer_keys_or_expected_refs",
            "prompt_must_not_include_precomputed_jsonl_outputs",
        ],
        "manual_and_raw_rg_extra_gates": [
            "jsonl_package_skill_helper_unavailable",
            "package_docs_or_query_plan_unavailable",
            "source_locator_handoff_unavailable",
        ],
        "package_lane_extra_gates": [
            "installed_live_package_invocation_required",
            "coordinator_precomputed_packet_forbidden",
            "private_bridge_or_case_notes_forbidden",
        ],
        "same_run_no_send_proof_required": True,
    }


def _prompt_materialization(case: dict[str, Any], lane: dict[str, Any]) -> dict[str, Any]:
    prompt_view = {
        "case": {field: case[field] for field in lane["allowed_prompt_case_fields"]},
        "lane": {
            "lane_id": lane["lane_id"],
            "strategy_family": lane["strategy_family"],
            "allowed_tools": lane["allowed_tools"],
            "route_instructions": lane["route_instructions"],
        },
        "output_contract": {
            "answer": "source_backed_answer_or_explicit_uncertainty",
            "source_refs": "refs_found_by_this_lane_only",
            "counters": "separated local/model-facing/full-loop buckets",
            "source_ref_retention_quality": (
                "durable refs, retained trace mapping, temporary refs, missing/unsupported refs, and fallback basis"
            ),
            "missing_counters": UNAVAILABLE,
        },
        "forbidden": lane["forbidden_material"],
    }
    return {
        "case_id": case["case_id"],
        "lane_id": lane["lane_id"],
        "prompt_view_hash_sha256": _sha256(prompt_view),
        "prompt_view_bytes": len(_canonical_json_bytes(prompt_view)),
        "prompt_text_committed": False,
        "private_materialization_required_for_jsh_726": True,
        "leakage_scan_expected_forbidden_count": 0,
    }


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()
