from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from .harness_artifacts import UNAVAILABLE, sha256_canonical_json


ADAPTER_SCHEMA_VERSION = "jsh-603-inspect-compatible-local-scorer-adapter.v1"
DEFAULT_X12G_ADJUDICATION_PATH = Path(
    "benchmarks/paired_agent/"
    "jsh-601x12g-assisted-b-live-retry-offline-adjudication-and-next-decision.json"
)

REPO_OWNED_CONTRACT_FIELDS = (
    "a_a2_b_lane_construction",
    "codex_runtime_isolation",
    "jsonl_helper_suppression_for_a_and_a2",
    "scenario_b_same_case_source_material_bridge",
    "contamination_gates",
    "private_artifact_retention",
    "source_backed_adjudication",
    "literal_unavailable_counter_policy",
)

INSPECT_COMPATIBLE_FIELDS = (
    "dataset_sample",
    "task_metadata",
    "solver_boundary",
    "scorer_output",
    "result_log_projection",
)


def inspect_ai_available() -> bool:
    """Return whether Inspect is importable without making it a hard dependency."""

    return importlib.util.find_spec("inspect_ai") is not None


def build_inspect_compatible_export(
    *,
    adjudication_path: Path = DEFAULT_X12G_ADJUDICATION_PATH,
    sequence: int = 3,
) -> dict[str, Any]:
    payload = _load_json_object(adjudication_path)
    row = _find_row(payload, sequence=sequence)
    sample_id = f"{row['case_id']}::scenario-{row['scenario'].lower()}::sequence-{sequence:02d}"
    retained_refs = _retained_refs(row)
    source_hit = row.get("source_hit_semantics", {})
    correctness = row.get("row_level_correctness_guardrail", {})
    unavailable = row.get("literal_unavailable_counter_preservation", {})
    contamination = row.get("contamination_labels", {})

    scores = {
        "answer_correctness": _score_bool(correctness.get("correct_under_x12e_contract")),
        "source_trail_quality": _score_bool(source_hit.get("expected_source_hit_found")),
        "source_backing": _score_bool(source_hit.get("pass")),
        "contamination": _score_bool(_contamination_is_acceptable(contamination)),
        "unavailable_counter_preservation": _score_bool(unavailable.get("pass")),
        "retained_evidence_refs": _score_bool(bool(retained_refs)),
    }

    export = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "task_id": "jsh-603-inspect-compatible-local-scorer-adapter-no-send-proof",
        "status": "pass" if all(item["passed"] for item in scores.values()) else "fail",
        "mode": "no_send_committed_safe_adapter_proof",
        "inspect_ai": {
            "hard_dependency_required": False,
            "import_guard_function": "inspect_ai_available",
            "current_environment_importable": inspect_ai_available(),
            "adoption_role": "optional_outer_dataset_task_scorer_log_abstraction",
        },
        "source_artifact": {
            "path": str(adjudication_path),
            "run_id": payload.get("run_id"),
            "schema_version": payload.get("schema_version"),
            "source_review": payload.get("source_review"),
            "reviewed_committed_safe": True,
            "raw_private_payload_read": False,
        },
        "jsonl_row_summary": _row_summary(row, retained_refs),
        "inspect_projection": {
            "dataset_sample": {
                "id": sample_id,
                "input": {
                    "case_id": row["case_id"],
                    "scenario": row["scenario"],
                    "lane": row["lane"],
                    "task_family": "project_history_provenance_retrieval",
                    "prompt_payload_committed": False,
                    "runner_contract": "repo_owned_a_a2_b_runner",
                },
                "target": {
                    "expected_adjudication": correctness.get("label"),
                    "expected_source_kind": "same_case_bridge",
                    "expected_source_ref_hash": (
                        row.get("same_case_bridge_consultation_use", {})
                        .get("same_case_bridge_identity_sha256")
                    ),
                    "expected_unavailable_policy": UNAVAILABLE,
                },
                "metadata": {
                    "sequence": sequence,
                    "privacy_layer": "committed_safe_sanitized_with_private_refs_only",
                    "contamination_labels": contamination.get("sanitized_labels", []),
                    "retained_ref_count": len(retained_refs),
                    "raw_final_answer_text_committed": False,
                    "raw_bridge_payload_committed": False,
                },
            },
            "task_metadata": {
                "task_name": "jsonl_private_history_source_backed_adjudication",
                "dataset_kind": "committed_safe_reviewed_harness_row",
                "sample_count": 1,
                "repo_owned_contract_fields": list(REPO_OWNED_CONTRACT_FIELDS),
                "inspect_compatible_fields": list(INSPECT_COMPATIBLE_FIELDS),
            },
            "solver_boundary": {
                "inspect_solver_replaces_repo_runner": False,
                "solver_role_for_this_proof": "not_run_no_send_existing_row_projection",
                "future_solver_role_if_adopted": (
                    "wrap or launch the repo-owned runner only after the runner has "
                    "already enforced lane isolation and privacy gates"
                ),
            },
            "scorer_output": {
                "sample_id": sample_id,
                "scorer_name": "jsonl_source_backed_contract_scorer",
                "score_scale": "0_to_1",
                "scores": scores,
                "literal_unavailable_preserved": _all_required_counters_literal_unavailable(unavailable),
                "source_backed_adjudication_preserved": bool(source_hit.get("pass")),
                "contamination_fields_preserved": "sanitized_labels" in contamination,
                "retained_evidence_refs_preserved": bool(retained_refs),
            },
            "result_log_projection": {
                "run_id": payload.get("run_id"),
                "sample_id": sample_id,
                "status": "scored_from_reviewed_committed_safe_row",
                "scores_sha256": sha256_canonical_json(scores),
                "retained_refs": retained_refs,
                "claim_boundary": {
                    "live_send_performed": False,
                    "codex_exec_performed": False,
                    "docker_action_performed": False,
                    "private_upload_performed": False,
                    "winner_claim": False,
                    "value_claim": False,
                    "jsonl_superiority_claim": False,
                    "speed_claim": False,
                    "token_efficiency_claim": False,
                },
            },
        },
        "decision": {
            "inspect_adoption": "conditional_adopt_outer_adapter_layer",
            "reason": (
                "The reviewed JSONL row maps cleanly to Inspect-style sample, task "
                "metadata, scorer output, and result-log concepts, while the A/A2/B "
                "runner and privacy/source-evidence contract remain repo-owned."
            ),
            "next_bounded_direction": "jsh-604-inspect-adapter-cli-and-fixture-regression-gate",
        },
    }
    _validate_export(export)
    return export


def _load_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected JSON object")
    return data


def _find_row(payload: dict[str, Any], *, sequence: int) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("source payload missing rows list")
    matches = [row for row in rows if isinstance(row, dict) and row.get("sequence") == sequence]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one row for sequence {sequence}, found {len(matches)}")
    return matches[0]


def _retained_refs(row: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    final_answer = row.get("final_answer_evidence", {})
    if isinstance(final_answer, dict) and final_answer.get("retained_path"):
        refs.append(
            {
                "kind": "final_answer",
                "retained_path": final_answer.get("retained_path"),
                "sha256": final_answer.get("sha256"),
                "bytes": final_answer.get("bytes"),
                "raw_text_committed": final_answer.get("raw_final_answer_text_committed", False),
            }
        )
    bridge = row.get("same_case_bridge_consultation_use", {})
    if isinstance(bridge, dict) and bridge.get("same_case_bridge_identity_sha256"):
        refs.append(
            {
                "kind": "same_case_bridge",
                "sha256": bridge.get("same_case_bridge_identity_sha256"),
                "raw_payload_committed": bridge.get("raw_bridge_payload_committed", False),
            }
        )
    return refs


def _row_summary(row: dict[str, Any], retained_refs: list[dict[str, Any]]) -> dict[str, Any]:
    unavailable = row.get("literal_unavailable_counter_preservation", {})
    return {
        "case_id": row.get("case_id"),
        "sequence": row.get("sequence"),
        "scenario": row.get("scenario"),
        "lane": row.get("lane"),
        "send_classification": row.get("send_classification"),
        "source_trace_status": row.get("source_trace_status"),
        "correctness_label": row.get("row_level_correctness_guardrail", {}).get("label"),
        "source_hit_label": row.get("source_hit_semantics", {}).get("label"),
        "contamination_labels": row.get("contamination_labels", {}).get("sanitized_labels", []),
        "literal_unavailable_counter_status": unavailable.get("required_counter_status", {}),
        "missing_counters_inferred_as_zero": unavailable.get("missing_counters_inferred_as_zero"),
        "retained_ref_count": len(retained_refs),
    }


def _score_bool(value: Any) -> dict[str, Any]:
    passed = bool(value)
    return {
        "score": 1.0 if passed else 0.0,
        "passed": passed,
        "status": "deterministic",
    }


def _contamination_is_acceptable(contamination: dict[str, Any]) -> bool:
    labels = contamination.get("sanitized_labels")
    return labels == ["counter_unavailable"]


def _all_required_counters_literal_unavailable(unavailable: dict[str, Any]) -> bool:
    statuses = unavailable.get("required_counter_status")
    if not isinstance(statuses, dict) or not statuses:
        return False
    return all(value == "literal_unavailable" for value in statuses.values())


def _validate_export(export: dict[str, Any]) -> None:
    projection = export.get("inspect_projection")
    if not isinstance(projection, dict):
        raise ValueError("missing inspect_projection")
    for key in INSPECT_COMPATIBLE_FIELDS:
        if key not in projection:
            raise ValueError(f"inspect_projection missing {key}")
    scorer = projection["scorer_output"]
    if scorer["literal_unavailable_preserved"] is not True:
        raise ValueError("literal unavailable counters were not preserved")
    if scorer["contamination_fields_preserved"] is not True:
        raise ValueError("contamination fields were not preserved")
    if scorer["source_backed_adjudication_preserved"] is not True:
        raise ValueError("source-backed adjudication fields were not preserved")
