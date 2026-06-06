from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "jsh-080a-harness-artifact-schemas.v1"
DEFAULT_HARNESS_SCHEMA_PATH = Path("benchmarks/paired_agent/jsh-080a-harness-artifact-schemas.json")
DEFAULT_PILOT_SELECTION_PATH = Path("benchmarks/paired_agent/jsh-059b2-pilot-selection.json")
DEFAULT_CASE_PACKETS_PATH = Path("benchmarks/paired_agent/jsh-060-case-packets.json")
DEFAULT_FIRST_WAVE_BLOCKER_PATH = Path("benchmarks/paired_agent/jsh-070-first-wave-thesis-and-blocker-packet.json")
UNAVAILABLE = "unavailable"

ARTIFACT_FAMILIES = (
    "case_registry",
    "corpus_manifest",
    "prompt_view_manifest",
    "trace_event",
    "offline_scorer_output",
    "result_packet",
    "reviewer_adjudication_summary",
)

SCENARIO_LABELS = (
    "scenario_a_fresh_codex_equal_corpus",
    "scenario_a2_contributor_normal_equal_corpus_no_jsonl_advantage",
    "scenario_b_jsonl_assisted_equal_corpus",
)

DRY_RUN_TASK_ID = "jsh-080c-one-case-dry-run-prompt-and-result-skeleton-builder"
DEFAULT_DRY_RUN_CASE_ID = "jsh-060-case-060c-0018"
DRY_RUN_ID = "jsh-080c-one-case-dry-run"
DRY_RUN_RUN_ID = f"{DRY_RUN_ID}-run-skeleton"
MANUAL_BLIND_HASH_KEYS = (
    "task_prompt",
    "generic_allowed_tools",
    "caps",
    "output_schema",
    "privacy_rules",
)
DRY_RUN_ARTIFACT_FILENAMES = {
    "case_registry": "case-registry.json",
    "corpus_manifest": "corpus-manifest.json",
    "prompt_view_scenario_a": "prompt-view-scenario-a.json",
    "prompt_view_scenario_a2": "prompt-view-scenario-a2.json",
    "prompt_view_scenario_b": "prompt-view-scenario-b.json",
    "result_packet": "result-packet.json",
    "offline_scorer_output": "offline-scorer-output.json",
    "reviewer_adjudication_summary": "reviewer-adjudication-summary.json",
}

TRACE_EVENT_TYPES = (
    "runner_setup",
    "corpus_mount_or_copy",
    "prompt_build",
    "pre_send_gate",
    "runtime_start",
    "runtime_leakage_scan",
    "tool_call",
    "file_search",
    "file_open",
    "bytes_read",
    "git_command",
    "retrieval_hit",
    "answer_submitted",
    "scorer_run",
    "reviewer_adjudication",
    "contamination_event",
    "cleanup",
)

SCORER_DIMENSIONS = (
    "answer_correctness",
    "source_trail_quality",
    "expected_source_ref_hit",
    "first_pass_source_hit",
    "context_recall_at_k",
    "context_precision_at_k",
    "utilization_precision",
    "explored_vs_utilized_gap",
    "no_evidence_abstention",
    "tool_use_efficiency",
    "contamination",
    "reviewer_verdict",
)

LIVE_COUNTER_FIELDS = (
    "elapsed_wall_time_seconds",
    "commands_or_tool_calls",
    "files_searched",
    "files_opened",
    "bytes_parsed_or_scanned",
    "bytes_read",
    "output_bytes",
    "context_bytes",
    "approx_output_tokens",
    "approx_context_tokens",
)

CLAIM_BOUNDARY_FIELDS = (
    "clean_live_claim",
    "result_claim",
    "performance_claim",
    "winner_claim",
    "value_claim",
    "correctness_claim",
    "source_hit_claim",
    "token_efficiency_claim",
    "scenario_b_claim",
    "jsonl_superiority_claim",
)

ARTIFACT_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "case_registry": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "registry_id",
        "cases",
        "live_counters",
        "claim_boundary",
    ),
    "corpus_manifest": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "corpus_manifest_id",
        "source_policy",
        "read_only_required",
        "private_payload_materialized",
        "live_counters",
        "claim_boundary",
    ),
    "prompt_view_manifest": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "prompt_view_id",
        "scenario_label",
        "allowed_sections",
        "prompt_text_committed",
        "prompt_hash_sha256",
        "live_counters",
        "claim_boundary",
    ),
    "trace_event": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "trace_event_id",
        "run_id",
        "event_type",
        "event_time_utc",
        "scenario_label",
        "payload_committed_safe",
        "live_counters",
        "claim_boundary",
    ),
    "offline_scorer_output": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "scorer_output_id",
        "run_id",
        "status",
        "scorer_dimensions",
        "scores",
        "live_counters",
        "claim_boundary",
    ),
    "result_packet": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "run_id",
        "status",
        "scenario_status",
        "comparison",
        "live_counters",
        "claim_boundary",
    ),
    "reviewer_adjudication_summary": (
        "schema_version",
        "artifact_family",
        "privacy_layer",
        "review_id",
        "run_id",
        "adjudication_status",
        "reviewed_live_gate_exists",
        "adjudication",
        "live_counters",
        "claim_boundary",
    ),
}

PRIVATE_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\", re.IGNORECASE),
    re.compile(r"\bcontributor\b", re.IGNORECASE),
    re.compile(r"\bsecret\b", re.IGNORECASE),
    re.compile(r"PRIVATE_SENTINEL", re.IGNORECASE),
    re.compile(r"raw_prompt_payload", re.IGNORECASE),
    re.compile(r"raw_transcript_payload", re.IGNORECASE),
    re.compile(r"model_output_payload", re.IGNORECASE),
)


@dataclass(frozen=True)
class ValidationResult:
    source_label: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    checked_families: tuple[str, ...]

    @property
    def status(self) -> str:
        return "pass" if not self.errors else "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_label": self.source_label,
            "status": self.status,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checked_families": list(self.checked_families),
        }


def build_claim_boundary_template() -> dict[str, bool]:
    return {key: False for key in CLAIM_BOUNDARY_FIELDS}


def build_live_counter_template() -> dict[str, str]:
    return {key: UNAVAILABLE for key in LIVE_COUNTER_FIELDS}


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_canonical_json(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def build_harness_artifact_schema_contract() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": "jsh-080a-harness-artifact-schema-and-validator",
        "status": "schema_contract_only",
        "privacy_layer": "committed_safe_sanitized",
        "reviewed_live_gate_exists": False,
        "live_benchmark_run_performed": False,
        "artifact_families": list(ARTIFACT_FAMILIES),
        "global_rules": {
            "missing_live_counter_value": UNAVAILABLE,
            "zero_counter_rule": "numeric zero is valid only with adjacent <counter>_measurement_status=measured_zero and non-empty <counter>_measurement_basis",
            "claim_boundary_fields": list(CLAIM_BOUNDARY_FIELDS),
            "claim_boundary_default": build_claim_boundary_template(),
            "private_payloads_committed": False,
            "live_result_downstream_tasks_blocked": True,
        },
        "shared_enums": {
            "scenario_labels": list(SCENARIO_LABELS),
            "trace_event_types": list(TRACE_EVENT_TYPES),
            "scorer_dimensions": list(SCORER_DIMENSIONS),
            "live_counter_fields": list(LIVE_COUNTER_FIELDS),
        },
        "artifact_schemas": {
            family: _artifact_schema_entry(family)
            for family in ARTIFACT_FAMILIES
        },
    }


def validate_harness_artifact_file(path: Path) -> ValidationResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return ValidationResult(str(path), (f"could not read JSON file: {exc}",), (), ())
    except json.JSONDecodeError as exc:
        return ValidationResult(str(path), (f"invalid JSON: {exc}",), (), ())
    return validate_harness_artifact_payload(payload, source_label=str(path))


def validate_harness_artifact_payload(payload: Any, *, source_label: str = "<memory>") -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    checked_families: list[str] = []

    if not isinstance(payload, dict):
        return ValidationResult(source_label, ("payload must be an object",), (), ())

    if payload.get("schema_version") == SCHEMA_VERSION and "artifact_schemas" in payload:
        checked_families.extend(_validate_schema_contract(payload, errors, warnings))
    else:
        family = _validate_single_artifact(payload, "$", errors, warnings)
        if family:
            checked_families.append(family)

    for hit in find_private_pattern_hits(payload):
        errors.append(f"private or non-committed-safe sentinel found at {hit}")

    ordered_checked = tuple(family for family in ARTIFACT_FAMILIES if family in set(checked_families))
    return ValidationResult(source_label, tuple(errors), tuple(warnings), ordered_checked)


def build_harness_artifact_validation_payload(
    *,
    schema_path: Path = DEFAULT_HARNESS_SCHEMA_PATH,
    artifact_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    results = [validate_harness_artifact_file(schema_path)]
    results.extend(validate_harness_artifact_file(path) for path in artifact_paths)
    failures = [result for result in results if result.errors]
    checked_set = {family for result in results for family in result.checked_families}
    checked_families = [family for family in ARTIFACT_FAMILIES if family in checked_set]
    return {
        "harness_artifact_validation": {
            "schema_version": SCHEMA_VERSION,
            "status": "fail" if failures else "pass",
            "task_id": "jsh-080a-harness-artifact-schema-and-validator",
            "mode": "non_live_schema_validation_only",
            "schema_path": str(schema_path),
            "artifact_paths": [str(path) for path in artifact_paths],
            "live_benchmark_run_performed": False,
            "model_send_performed": False,
            "codex_exec_performed": False,
            "docker_action_performed": False,
            "private_corpus_materialized": False,
            "claim_boundary_enforced": True,
            "missing_live_counter_value": UNAVAILABLE,
            "checked_family_count": len(checked_families),
            "checked_families": checked_families,
            "summary": {
                "checked_files": len(results),
                "pass": sum(1 for result in results if not result.errors),
                "fail": len(failures),
                "error_count": sum(len(result.errors) for result in results),
                "warning_count": sum(len(result.warnings) for result in results),
            },
            "results": [result.to_dict() for result in results],
        }
    }


def build_harness_dry_run_artifacts_payload(
    *,
    case_id: str = DEFAULT_DRY_RUN_CASE_ID,
    output_dir: Path,
    pilot_selection_path: Path = DEFAULT_PILOT_SELECTION_PATH,
    case_packets_path: Path = DEFAULT_CASE_PACKETS_PATH,
    first_wave_blocker_path: Path = DEFAULT_FIRST_WAVE_BLOCKER_PATH,
    report_output: Path | None = None,
) -> dict[str, Any]:
    pilot_selection = _load_json_object(pilot_selection_path)
    case_packets = _load_json_object(case_packets_path)
    first_wave_blocker = _load_json_object(first_wave_blocker_path)

    selected_case = _find_case_by_id(pilot_selection.get("selected_cases"), case_id, "selected_cases")
    case_packet = _find_case_by_id(case_packets.get("packets"), case_id, "packets")
    manual_view = _require_object((case_packet.get("views") or {}).get("manual_default_blind"), "manual_default_blind")
    jsonl_view = _require_object((case_packet.get("views") or {}).get("jsonl_assisted"), "jsonl_assisted")

    missing_manual_keys = [key for key in MANUAL_BLIND_HASH_KEYS if key not in manual_view]
    if missing_manual_keys:
        raise ValueError(f"manual_default_blind missing required keys: {', '.join(missing_manual_keys)}")
    manual_hash_basis = {key: manual_view[key] for key in MANUAL_BLIND_HASH_KEYS}
    manual_hash = sha256_canonical_json(manual_hash_basis)
    scenario_b_descriptor = _scenario_b_descriptor(case_id, case_packets_path, jsonl_view)
    scenario_b_hash = sha256_canonical_json(scenario_b_descriptor)

    artifacts = _build_dry_run_artifacts(
        case_id=case_id,
        output_dir=output_dir,
        selected_case=selected_case,
        case_packet=case_packet,
        manual_hash=manual_hash,
        scenario_b_hash=scenario_b_hash,
        scenario_b_descriptor=scenario_b_descriptor,
        pilot_selection_path=pilot_selection_path,
        case_packets_path=case_packets_path,
        first_wave_blocker_path=first_wave_blocker_path,
        first_wave_blocker=first_wave_blocker,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths: dict[str, str] = {}
    for key, payload in artifacts.items():
        path = output_dir / DRY_RUN_ARTIFACT_FILENAMES[key]
        write_json_file(path, payload)
        artifact_paths[key] = str(path)

    validation_payload = build_harness_artifact_validation_payload(
        artifact_paths=tuple(Path(path) for path in artifact_paths.values())
    )
    report_path = None
    if report_output is not None:
        report_output.parent.mkdir(parents=True, exist_ok=True)
        report_output.write_text(
            render_harness_dry_run_artifacts_report(
                case_id=case_id,
                artifact_paths=artifact_paths,
                validation_payload=validation_payload,
                manual_hash=manual_hash,
                scenario_b_hash=scenario_b_hash,
            ),
            encoding="utf-8",
        )
        report_path = str(report_output)

    return {
        "harness_dry_run_artifacts": {
            "schema_version": SCHEMA_VERSION,
            "task_id": DRY_RUN_TASK_ID,
            "status": validation_payload["harness_artifact_validation"]["status"],
            "mode": "non_live_committed_safe_dry_run_artifact_build",
            "case_id": case_id,
            "run_id": DRY_RUN_RUN_ID,
            "output_dir": str(output_dir),
            "report_output": report_path,
            "artifact_paths": artifact_paths,
            "prompt_hashes": {
                "scenario_a": manual_hash,
                "scenario_a2": manual_hash,
                "scenario_b": scenario_b_hash,
            },
            "scenario_a_a2_hash_basis_keys": list(MANUAL_BLIND_HASH_KEYS),
            "scenario_a2_calibration_only": True,
            "live_benchmark_run_performed": False,
            "model_send_performed": False,
            "codex_exec_performed": False,
            "docker_action_performed": False,
            "network_action_performed": False,
            "scenario_b_execution_performed": False,
            "private_corpus_materialized": False,
            "corpus_mutation_performed": False,
            "claim_boundary_enforced": True,
            "missing_live_counter_value": UNAVAILABLE,
            "validation_status": validation_payload["harness_artifact_validation"]["status"],
            "validation_summary": validation_payload["harness_artifact_validation"]["summary"],
        }
    }


def render_harness_dry_run_artifacts_report(
    *,
    case_id: str,
    artifact_paths: dict[str, str],
    validation_payload: dict[str, Any],
    manual_hash: str,
    scenario_b_hash: str,
) -> str:
    validation = validation_payload["harness_artifact_validation"]
    summary = validation["summary"]
    lines = [
        "# JSH-080c One-Case Dry-Run Prompt And Result Skeleton Builder",
        "",
        f"**Status:** `{validation['status']}`",
        f"**Case:** `{case_id}`",
        "**Mode:** `non_live_committed_safe_dry_run_artifact_build`",
        "**Live/model work:** `not_performed`",
        "**Scenario B execution:** `not_performed`",
        "**Corpus materialization or mutation:** `not_performed`",
        "**Result claims:** `false`",
        "",
        "## Generated Artifacts",
        "",
    ]
    for key in DRY_RUN_ARTIFACT_FILENAMES:
        lines.append(f"- `{key}`: `{artifact_paths[key]}`")
    lines.extend(
        [
            "",
            "## Prompt Hashes",
            "",
            f"- Scenario A and A2 blind hash: `{manual_hash}`",
            f"- Scenario B committed-safe descriptor hash: `{scenario_b_hash}`",
            "- Scenario A and A2 hash basis fields: `task_prompt`, `generic_allowed_tools`, `caps`, `output_schema`, `privacy_rules`.",
            "- Scenario A2 is calibration-only and remains separate from Scenario A.",
            "",
            "## Validation",
            "",
            f"- Validator status: `{validation['status']}`",
            f"- Checked files: `{summary['checked_files']}`",
            f"- Error count: `{summary['error_count']}`",
            f"- Warning count: `{summary['warning_count']}`",
            "",
            "## Boundary Notes",
            "",
            "- This task built committed-safe skeleton files only.",
            "- Missing live counters remain the literal `unavailable`.",
            "- Every claim-boundary flag remains `false`.",
            "- Downstream live/result tasks, including `jsh-075d`, `jsh-075e`, and `jsh-076`, remain blocked.",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_dry_run_artifacts(
    *,
    case_id: str,
    output_dir: Path,
    selected_case: dict[str, Any],
    case_packet: dict[str, Any],
    manual_hash: str,
    scenario_b_hash: str,
    scenario_b_descriptor: dict[str, Any],
    pilot_selection_path: Path,
    case_packets_path: Path,
    first_wave_blocker_path: Path,
    first_wave_blocker: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    case_family_id = str(case_packet["family"])
    corpus_manifest_id = f"{DRY_RUN_ID}-corpus-manifest"
    prompt_view_ids = {
        "prompt_view_scenario_a": f"{DRY_RUN_ID}-prompt-view-scenario-a",
        "prompt_view_scenario_a2": f"{DRY_RUN_ID}-prompt-view-scenario-a2",
        "prompt_view_scenario_b": f"{DRY_RUN_ID}-prompt-view-scenario-b",
    }
    case_pointer = f"{case_packets_path}#/packets[case_id={case_id}]"
    manual_pointer = f"{case_pointer}/views/manual_default_blind"
    jsonl_pointer = f"{case_pointer}/views/jsonl_assisted"

    case_registry = _base_artifact("case_registry")
    case_registry.update(
        {
            "registry_id": f"{DRY_RUN_ID}-case-registry",
            "dry_run_id": DRY_RUN_ID,
            "selected_case_policy": "fixed_reviewed_one_case_only",
            "source_artifacts": {
                "pilot_selection": str(pilot_selection_path),
                "case_packets": str(case_packets_path),
                "first_wave_blocker": str(first_wave_blocker_path),
            },
            "cases": [
                {
                    "case_id": case_id,
                    "case_family_id": case_family_id,
                    "candidate_type": selected_case.get("candidate_type"),
                    "source_batch": selected_case.get("source_batch"),
                    "difficulty_dimensions": selected_case.get("difficulty_dimensions", []),
                    "duplicate_label": (selected_case.get("duplicate_metadata") or {}).get("duplicate_label"),
                    "scenario_labels": list(SCENARIO_LABELS),
                    "corpus_manifest_id": corpus_manifest_id,
                    "prompt_view_manifest_ids": list(prompt_view_ids.values()),
                    "committed_safe_case_pointer": case_pointer,
                }
            ],
        }
    )

    corpus_manifest = _base_artifact("corpus_manifest")
    corpus_manifest.update(
        {
            "corpus_manifest_id": corpus_manifest_id,
            "dry_run_id": DRY_RUN_ID,
            "case_id": case_id,
            "source_policy": "same_read_only_neutral_committed_safe_manifest_only",
            "read_only_required": True,
            "private_payload_materialized": False,
            "corpus_mutation_performed": False,
            "content_inventory": {
                "file_count": UNAVAILABLE,
                "byte_count": UNAVAILABLE,
                "inventory_basis": "not_measured_in_non_live_dry_run",
            },
            "equal_corpus_access_policy": {
                "scenario_a": "same_neutral_corpus_manifest_only",
                "scenario_a2": "same_neutral_corpus_manifest_only",
                "scenario_b": "same_neutral_corpus_manifest_only",
                "corpus_materialization_status": "not_performed",
            },
            "committed_safe_sources": [str(pilot_selection_path), str(case_packets_path), str(first_wave_blocker_path)],
        }
    )

    prompt_a = _prompt_view_artifact(
        prompt_view_id=prompt_view_ids["prompt_view_scenario_a"],
        scenario_label=SCENARIO_LABELS[0],
        prompt_hash=manual_hash,
        hash_scope="canonical_json_manual_default_blind_five_key_basis",
        allowed_sections=list(MANUAL_BLIND_HASH_KEYS),
        source_view_pointer=manual_pointer,
        model_visible_descriptor=_manual_model_visible_descriptor(
            prompt_view_id=prompt_view_ids["prompt_view_scenario_a"],
            scenario_label=SCENARIO_LABELS[0],
            prompt_hash=manual_hash,
        ),
        contamination_scan_input=_contamination_scan_input(
            scan_id=f"{DRY_RUN_ID}-scenario-a-contamination-scan-input",
            prompt_hash=manual_hash,
            forbidden_fields=[
                "case_id",
                "candidate_id",
                "project_stratum_id",
                "source_cluster_id",
                "private_packet_id",
                "jsonl_helper_hints",
                "task_or_review_state",
                "prior_result_hints",
                "local_user_paths",
                "session_identifiers",
                "transcript_text",
            ],
        ),
        metadata={
            "case_id": case_id,
            "hash_basis_source_pointer": manual_pointer,
            "hash_basis_keys": list(MANUAL_BLIND_HASH_KEYS),
            "model_visible_descriptor_excludes_case_metadata": True,
        },
    )
    prompt_a2 = _prompt_view_artifact(
        prompt_view_id=prompt_view_ids["prompt_view_scenario_a2"],
        scenario_label=SCENARIO_LABELS[1],
        prompt_hash=manual_hash,
        hash_scope="canonical_json_manual_default_blind_five_key_basis_shared_with_scenario_a",
        allowed_sections=list(MANUAL_BLIND_HASH_KEYS),
        source_view_pointer=manual_pointer,
        model_visible_descriptor=_manual_model_visible_descriptor(
            prompt_view_id=prompt_view_ids["prompt_view_scenario_a2"],
            scenario_label=SCENARIO_LABELS[1],
            prompt_hash=manual_hash,
            calibration_only=True,
        ),
        contamination_scan_input=_contamination_scan_input(
            scan_id=f"{DRY_RUN_ID}-scenario-a2-contamination-scan-input",
            prompt_hash=manual_hash,
            forbidden_fields=[
                "case_id",
                "candidate_id",
                "project_stratum_id",
                "source_cluster_id",
                "private_packet_id",
                "jsonl_helper_hints",
                "task_or_review_state",
                "prior_result_hints",
                "local_user_paths",
                "session_identifiers",
                "transcript_text",
            ],
        ),
        metadata={
            "case_id": case_id,
            "hash_basis_source_pointer": manual_pointer,
            "hash_basis_keys": list(MANUAL_BLIND_HASH_KEYS),
            "calibration_only": True,
            "merged_into_scenario_a": False,
        },
    )
    prompt_b = _prompt_view_artifact(
        prompt_view_id=prompt_view_ids["prompt_view_scenario_b"],
        scenario_label=SCENARIO_LABELS[2],
        prompt_hash=scenario_b_hash,
        hash_scope="canonical_json_committed_safe_jsonl_assisted_descriptor_only",
        allowed_sections=["same_case_jsonl_assisted_descriptor", "approved_tools", "private_packet_id"],
        source_view_pointer=jsonl_pointer,
        model_visible_descriptor={
            "prompt_view_id": prompt_view_ids["prompt_view_scenario_b"],
            "scenario_label": SCENARIO_LABELS[2],
            "descriptor": scenario_b_descriptor,
            "prompt_hash_sha256": scenario_b_hash,
            "prompt_text_committed": False,
            "private_packet_payload_committed": False,
        },
        contamination_scan_input=_contamination_scan_input(
            scan_id=f"{DRY_RUN_ID}-scenario-b-contamination-scan-input",
            prompt_hash=scenario_b_hash,
            forbidden_fields=[
                "cross_case_packets",
                "post_hoc_roots",
                "source_lines",
                "transcript_text",
                "model_output_text",
                "auth_material",
                "local_user_paths",
                "scenario_a_outputs",
                "scenario_a2_outputs",
            ],
        ),
        metadata={
            "case_id": case_id,
            "hash_basis_source_pointer": jsonl_pointer,
            "private_packet_payload_committed": False,
            "private_packet_payload_hash_sha256": UNAVAILABLE,
        },
    )

    result_packet = _base_artifact("result_packet")
    result_packet.update(
        {
            "run_id": DRY_RUN_RUN_ID,
            "dry_run_id": DRY_RUN_ID,
            "case_id": case_id,
            "status": "skeleton_no_live_result",
            "live_benchmark_run_performed": False,
            "model_send_performed": False,
            "scenario_b_execution_performed": False,
            "scenario_status": {
                "scenario_a": "not_run_non_live_dry_run_only",
                "scenario_a2": "not_run_calibration_only",
                "scenario_b": "not_run_non_live_descriptor_only",
            },
            "comparison": {
                "winner": UNAVAILABLE,
                "speed_result": UNAVAILABLE,
                "value_result": UNAVAILABLE,
                "correctness_result": UNAVAILABLE,
                "source_hit_result": UNAVAILABLE,
                "token_efficiency_result": UNAVAILABLE,
            },
            "downstream_blockers": {
                "jsh-075d": "blocked",
                "jsh-075e": "blocked",
                "jsh-076": "blocked",
            },
        }
    )

    offline_scorer_output = _base_artifact("offline_scorer_output")
    offline_scorer_output.update(
        {
            "scorer_output_id": f"{DRY_RUN_ID}-offline-scorer-output",
            "run_id": DRY_RUN_RUN_ID,
            "dry_run_id": DRY_RUN_ID,
            "status": "not_run",
            "scorer_dimensions": list(SCORER_DIMENSIONS),
            "scores": {dimension: UNAVAILABLE for dimension in SCORER_DIMENSIONS},
            "input_result_packet": DRY_RUN_ARTIFACT_FILENAMES["result_packet"],
            "scoring_basis": "no_model_output_or_live_trace_available",
        }
    )

    reviewer_summary = _base_artifact("reviewer_adjudication_summary")
    reviewer_summary.update(
        {
            "review_id": f"{DRY_RUN_ID}-reviewer-adjudication-summary",
            "run_id": DRY_RUN_RUN_ID,
            "dry_run_id": DRY_RUN_ID,
            "adjudication_status": "not_adjudicated",
            "reviewed_live_gate_exists": False,
            "adjudication": {
                "reviewer_verdict": UNAVAILABLE,
                "correctness": UNAVAILABLE,
                "source_hit": UNAVAILABLE,
                "residual_risk": [
                    "non_live_dry_run_only",
                    "independent_review_required",
                ],
            },
        }
    )

    claim_boundary = first_wave_blocker.get("result_claim_boundary") or {}
    result_packet["upstream_claim_boundary_snapshot"] = {
        "reviewed_clean_live_one_case_result_packet_exists": claim_boundary.get(
            "reviewed_clean_live_one_case_result_packet_exists", False
        ),
        "winner_claim_created": claim_boundary.get("winner_claim_created", False),
        "correctness_claim_created": claim_boundary.get("correctness_claim_created", False),
        "source_hit_claim_created": claim_boundary.get("source_hit_claim_created", False),
        "token_efficiency_claim_created": claim_boundary.get("token_efficiency_claim_created", False),
    }

    return {
        "case_registry": case_registry,
        "corpus_manifest": corpus_manifest,
        "prompt_view_scenario_a": prompt_a,
        "prompt_view_scenario_a2": prompt_a2,
        "prompt_view_scenario_b": prompt_b,
        "result_packet": result_packet,
        "offline_scorer_output": offline_scorer_output,
        "reviewer_adjudication_summary": reviewer_summary,
    }


def _base_artifact(family: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_family": family,
        "privacy_layer": "committed_safe_sanitized",
        "live_counters": build_live_counter_template(),
        "claim_boundary": build_claim_boundary_template(),
    }


def _prompt_view_artifact(
    *,
    prompt_view_id: str,
    scenario_label: str,
    prompt_hash: str,
    hash_scope: str,
    allowed_sections: list[str],
    source_view_pointer: str,
    model_visible_descriptor: dict[str, Any],
    contamination_scan_input: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    artifact = _base_artifact("prompt_view_manifest")
    artifact.update(
        {
            "prompt_view_id": prompt_view_id,
            "scenario_label": scenario_label,
            "allowed_sections": allowed_sections,
            "prompt_text_committed": False,
            "prompt_hash_sha256": prompt_hash,
            "prompt_hash_scope": hash_scope,
            "source_view_pointer": source_view_pointer,
            "model_visible_prompt_view_descriptor": model_visible_descriptor,
            "contamination_scan_input": contamination_scan_input,
            "metadata": metadata,
        }
    )
    return artifact


def _manual_model_visible_descriptor(
    *,
    prompt_view_id: str,
    scenario_label: str,
    prompt_hash: str,
    calibration_only: bool = False,
) -> dict[str, Any]:
    return {
        "prompt_view_id": prompt_view_id,
        "scenario_label": scenario_label,
        "view_kind": "manual_default_blind",
        "allowed_section_keys": list(MANUAL_BLIND_HASH_KEYS),
        "prompt_text_committed": False,
        "prompt_hash_sha256": prompt_hash,
        "hash_basis_visible": False,
        "calibration_only": calibration_only,
    }


def _contamination_scan_input(*, scan_id: str, prompt_hash: str, forbidden_fields: list[str]) -> dict[str, Any]:
    return {
        "scan_input_id": scan_id,
        "input_kind": "prompt_view_manifest_descriptor_only",
        "prompt_hash_sha256": prompt_hash,
        "prompt_text_available_for_scan": False,
        "forbidden_model_visible_fields": forbidden_fields,
        "scan_status": "not_run_input_manifest_only",
    }


def _scenario_b_descriptor(case_id: str, case_packets_path: Path, jsonl_view: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "committed_pointer": f"{case_packets_path}#/packets[case_id={case_id}]/views/jsonl_assisted",
        "private_packet_id": jsonl_view.get("private_packet_id"),
        "private_packet_payload_committed": False,
        "private_packet_payload_status": "not_materialized_ignored_private_artifact_only",
        "private_packet_payload_hash_sha256": UNAVAILABLE,
        "approved_tools": jsonl_view.get("approved_tools", []),
        "view_storage": jsonl_view.get("view_storage"),
        "private_ref_storage": jsonl_view.get("private_ref_storage"),
    }


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    return _require_object(payload, str(path))


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _find_case_by_id(cases: Any, case_id: str, label: str) -> dict[str, Any]:
    if not isinstance(cases, list):
        raise ValueError(f"{label} must be a list")
    matches = [item for item in cases if isinstance(item, dict) and item.get("case_id") == case_id]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {label} entry for case_id {case_id}, found {len(matches)}")
    return matches[0]


def _artifact_schema_entry(family: str) -> dict[str, Any]:
    return {
        "artifact_family": family,
        "required_fields": list(ARTIFACT_REQUIRED_FIELDS[family]),
        "required_live_counters": list(LIVE_COUNTER_FIELDS),
        "required_claim_boundary_fields": list(CLAIM_BOUNDARY_FIELDS),
        "committed_safe_sample": _sample_artifact(family),
    }


def _sample_artifact(family: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "artifact_family": family,
        "privacy_layer": "committed_safe_sanitized",
        "live_counters": build_live_counter_template(),
        "claim_boundary": build_claim_boundary_template(),
    }
    if family == "case_registry":
        base.update(
            {
                "registry_id": "synthetic-case-registry-v1",
                "cases": [
                    {
                        "case_id": "case-synthetic-alpha",
                        "case_family_id": "family-synthetic-retrieval",
                        "scenario_labels": list(SCENARIO_LABELS),
                        "corpus_manifest_id": "corpus-synthetic-alpha",
                        "prompt_view_manifest_ids": ["prompt-view-synthetic-a", "prompt-view-synthetic-b"],
                    }
                ],
            }
        )
    elif family == "corpus_manifest":
        base.update(
            {
                "corpus_manifest_id": "corpus-synthetic-alpha",
                "source_policy": "same_read_only_neutral_committed_safe_manifest_only",
                "read_only_required": True,
                "private_payload_materialized": False,
                "content_inventory": {
                    "file_count": UNAVAILABLE,
                    "byte_count": UNAVAILABLE,
                    "inventory_basis": "not_measured_in_schema_task",
                },
            }
        )
    elif family == "prompt_view_manifest":
        base.update(
            {
                "prompt_view_id": "prompt-view-synthetic-a",
                "scenario_label": SCENARIO_LABELS[0],
                "allowed_sections": ["task_prompt", "caps", "output_schema", "privacy_rules"],
                "prompt_text_committed": False,
                "prompt_hash_sha256": "sha256:synthetic-prompt-view-alpha",
            }
        )
    elif family == "trace_event":
        base.update(
            {
                "trace_event_id": "trace-event-synthetic-001",
                "run_id": "run-synthetic-not-live",
                "event_type": "runner_setup",
                "event_time_utc": UNAVAILABLE,
                "scenario_label": UNAVAILABLE,
                "payload_committed_safe": True,
                "event_payload": {"status": "schema_only_no_runtime_event"},
            }
        )
    elif family == "offline_scorer_output":
        base.update(
            {
                "scorer_output_id": "scorer-output-synthetic-001",
                "run_id": "run-synthetic-not-live",
                "status": "not_run",
                "scorer_dimensions": list(SCORER_DIMENSIONS),
                "scores": {dimension: UNAVAILABLE for dimension in SCORER_DIMENSIONS},
            }
        )
    elif family == "result_packet":
        base.update(
            {
                "run_id": "run-synthetic-not-live",
                "status": "skeleton_no_live_result",
                "scenario_status": {
                    "scenario_a": "blocked_no_live_gate",
                    "scenario_a2": "blocked_no_live_gate",
                    "scenario_b": "blocked_no_live_gate",
                },
                "comparison": {
                    "winner": UNAVAILABLE,
                    "speed_result": UNAVAILABLE,
                    "value_result": UNAVAILABLE,
                    "correctness_result": UNAVAILABLE,
                    "source_hit_result": UNAVAILABLE,
                    "token_efficiency_result": UNAVAILABLE,
                },
            }
        )
    elif family == "reviewer_adjudication_summary":
        base.update(
            {
                "review_id": "review-synthetic-not-live",
                "run_id": "run-synthetic-not-live",
                "adjudication_status": "not_adjudicated",
                "reviewed_live_gate_exists": False,
                "adjudication": {
                    "reviewer_verdict": UNAVAILABLE,
                    "correctness": UNAVAILABLE,
                    "source_hit": UNAVAILABLE,
                    "residual_risk": ["schema_task_only"],
                },
            }
        )
    return base


def _validate_schema_contract(payload: dict[str, Any], errors: list[str], warnings: list[str]) -> list[str]:
    for key in (
        "task_id",
        "status",
        "privacy_layer",
        "reviewed_live_gate_exists",
        "live_benchmark_run_performed",
        "artifact_families",
        "global_rules",
        "shared_enums",
        "artifact_schemas",
    ):
        if key not in payload:
            errors.append(f"$.{key}: required field missing")

    if payload.get("privacy_layer") != "committed_safe_sanitized":
        errors.append("$.privacy_layer: must be committed_safe_sanitized")
    if payload.get("reviewed_live_gate_exists") is not False:
        errors.append("$.reviewed_live_gate_exists: must be false for this task")
    if payload.get("live_benchmark_run_performed") is not False:
        errors.append("$.live_benchmark_run_performed: must be false for this task")

    families = payload.get("artifact_families")
    if list(families or []) != list(ARTIFACT_FAMILIES):
        errors.append("$.artifact_families: must list all required artifact families in contract order")

    rules = payload.get("global_rules")
    if not isinstance(rules, dict):
        errors.append("$.global_rules: must be an object")
    else:
        if rules.get("missing_live_counter_value") != UNAVAILABLE:
            errors.append("$.global_rules.missing_live_counter_value: must be unavailable")
        if rules.get("live_result_downstream_tasks_blocked") is not True:
            errors.append("$.global_rules.live_result_downstream_tasks_blocked: must be true")
        _validate_claim_boundary(rules.get("claim_boundary_default"), "$.global_rules.claim_boundary_default", errors)

    schemas = payload.get("artifact_schemas")
    if not isinstance(schemas, dict):
        errors.append("$.artifact_schemas: must be an object")
        return []

    checked: list[str] = []
    for family in ARTIFACT_FAMILIES:
        schema = schemas.get(family)
        if not isinstance(schema, dict):
            errors.append(f"$.artifact_schemas.{family}: required schema missing")
            continue
        if schema.get("artifact_family") != family:
            errors.append(f"$.artifact_schemas.{family}.artifact_family: must be {family}")
        if schema.get("required_fields") != list(ARTIFACT_REQUIRED_FIELDS[family]):
            errors.append(f"$.artifact_schemas.{family}.required_fields: does not match validator contract")
        if schema.get("required_live_counters") != list(LIVE_COUNTER_FIELDS):
            errors.append(f"$.artifact_schemas.{family}.required_live_counters: does not match validator contract")
        if schema.get("required_claim_boundary_fields") != list(CLAIM_BOUNDARY_FIELDS):
            errors.append(
                f"$.artifact_schemas.{family}.required_claim_boundary_fields: does not match validator contract"
            )
        sample = schema.get("committed_safe_sample")
        if not isinstance(sample, dict):
            errors.append(f"$.artifact_schemas.{family}.committed_safe_sample: required object missing")
            continue
        sample_family = _validate_single_artifact(
            sample,
            f"$.artifact_schemas.{family}.committed_safe_sample",
            errors,
            warnings,
            expected_family=family,
        )
        if sample_family:
            checked.append(sample_family)
    return checked


def _validate_single_artifact(
    artifact: dict[str, Any],
    path: str,
    errors: list[str],
    warnings: list[str],
    *,
    expected_family: str | None = None,
) -> str | None:
    family = artifact.get("artifact_family")
    if expected_family and family != expected_family:
        errors.append(f"{path}.artifact_family: must be {expected_family}")
        return None
    if family not in ARTIFACT_FAMILIES:
        errors.append(f"{path}.artifact_family: unknown artifact family {family!r}")
        return None

    if artifact.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{path}.schema_version: must be {SCHEMA_VERSION}")
    if artifact.get("privacy_layer") != "committed_safe_sanitized":
        errors.append(f"{path}.privacy_layer: must be committed_safe_sanitized")

    for key in ARTIFACT_REQUIRED_FIELDS[family]:
        if key not in artifact:
            errors.append(f"{path}.{key}: required field missing")

    _validate_live_counters(artifact.get("live_counters"), f"{path}.live_counters", errors)
    _validate_claim_boundary(artifact.get("claim_boundary"), f"{path}.claim_boundary", errors)

    if artifact.get("reviewed_live_gate_exists") is True:
        errors.append(f"{path}.reviewed_live_gate_exists: true is not allowed without a reviewed live gate")
    if artifact.get("live_benchmark_run_performed") is True:
        errors.append(f"{path}.live_benchmark_run_performed: true is not allowed in this schema task")
    if artifact.get("private_payload_materialized") is True:
        errors.append(f"{path}.private_payload_materialized: must not be true in committed-safe artifacts")
    if artifact.get("prompt_text_committed") is True:
        errors.append(f"{path}.prompt_text_committed: must not be true in committed-safe artifacts")

    if family == "trace_event" and artifact.get("event_type") not in TRACE_EVENT_TYPES:
        errors.append(f"{path}.event_type: unknown trace event type {artifact.get('event_type')!r}")
    scenario_label = artifact.get("scenario_label")
    if family in {"prompt_view_manifest", "trace_event"} and scenario_label not in (*SCENARIO_LABELS, UNAVAILABLE):
        errors.append(f"{path}.scenario_label: unknown scenario label {scenario_label!r}")
    if family == "offline_scorer_output":
        dimensions = artifact.get("scorer_dimensions")
        if dimensions != list(SCORER_DIMENSIONS):
            errors.append(f"{path}.scorer_dimensions: must list all scorer dimensions in contract order")

    return str(family)


def _validate_live_counters(counters: Any, path: str, errors: list[str]) -> None:
    if not isinstance(counters, dict):
        errors.append(f"{path}: must be an object")
        return
    for key in LIVE_COUNTER_FIELDS:
        if key not in counters:
            errors.append(f"{path}.{key}: required live counter missing; use unavailable when not measured")
            continue
        value = counters[key]
        if value == UNAVAILABLE:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}.{key}: must be a number or unavailable")
            continue
        if value < 0:
            errors.append(f"{path}.{key}: must not be negative")
        if value == 0:
            status = counters.get(f"{key}_measurement_status")
            basis = counters.get(f"{key}_measurement_basis")
            if status != "measured_zero" or not isinstance(basis, str) or not basis.strip():
                errors.append(
                    f"{path}.{key}: zero requires {key}_measurement_status=measured_zero and a non-empty {key}_measurement_basis"
                )


def _validate_claim_boundary(boundary: Any, path: str, errors: list[str]) -> None:
    if not isinstance(boundary, dict):
        errors.append(f"{path}: must be an object")
        return
    for key in CLAIM_BOUNDARY_FIELDS:
        if key not in boundary:
            errors.append(f"{path}.{key}: required claim-boundary flag missing")
        elif boundary[key] is not False:
            errors.append(f"{path}.{key}: must be false without a later reviewed live gate")


def find_private_pattern_hits(value: Any, *, prefix: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            hits.extend(find_private_pattern_hits(item, prefix=f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(find_private_pattern_hits(item, prefix=f"{prefix}[{index}]"))
    elif isinstance(value, str):
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(value):
                hits.append(prefix)
                break
    return hits
