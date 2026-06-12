from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "jsh-725-agent-loop-route-counter-contract.v1"
DEFAULT_CASE_SELECTION_PATH = Path("benchmarks/paired_agent/jsh-724-held-out-agent-loop-case-selection.json")
DEFAULT_OUTPUT_PATH = Path("benchmarks/paired_agent/jsh-725-agent-loop-route-counter-contract.json")
UNAVAILABLE = "unavailable"

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
    output_path.write_text(json.dumps(payload["agent_loop_cost_contract"], indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _read_selection(path: Path) -> dict[str, Any]:
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
        "allowed_prompt_case_fields": ["case_id", "family", "difficulty", "source_kind_basis", "coverage_dimensions"],
        "forbidden_material": list(FORBIDDEN_MATERIAL),
        "must_record_counters": ["local_tool_work", "model_facing_context_input", "full_agent_loop_cost"],
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
                "allowed_tools": ["python3 -m jsonl_history_search.cli find", "python3 -m jsonl_history_search.cli semantic-debug"],
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
                "allowed_tools": ["python3 -m jsonl_history_search.cli find --rg-prefilter", "python3 -m jsonl_history_search.cli semantic-debug --rg-prefilter"],
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
