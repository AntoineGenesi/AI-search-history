from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_JSH1043_MANIFEST_PATH = Path("benchmarks/agent_use_value/jsh-1043-case-manifest.json")
JSH1043_SCHEMA_VERSION = "jsh-1043-agent-use-value-case-manifest.v1"
REQUIRED_GLOBAL_LANES = ("manual_default_clean", "jsonl_current_default")
REQUIRED_LANE_ARTIFACTS = (
    "prompt",
    "event_or_transcript_log",
    "final_answer",
    "stderr",
    "timing",
    "metadata",
    "command_or_tool_trace",
    "opened_source_refs",
    "cost_buckets",
    "facet_audit",
)
REQUIRED_DECISION_THRESHOLDS = (
    "PROMOTE_ARM",
    "PROMOTE_SCOPED_ESCALATION",
    "ABANDON_ARM",
    "INSERT_BLOCKER_LOOP",
    "EXPAND_COVERAGE_READY",
    "REWRITE_GATE",
)
MIN_CASES = 8
MAX_CASES = 12


@dataclass(frozen=True)
class AgentUseValueManifestValidation:
    manifest_path: str
    status: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    case_count: int
    family_count: int
    blocked_by_host_guard: bool
    lane_execution_started: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_use_value_gate_manifest_validation": {
                "schema_version": JSH1043_SCHEMA_VERSION,
                "status": self.status,
                "mode": "committed_safe_manifest_validation_only",
                "manifest_path": self.manifest_path,
                "case_count": self.case_count,
                "family_count": self.family_count,
                "blocked_by_host_guard": self.blocked_by_host_guard,
                "lane_execution_started": self.lane_execution_started,
                "live_lane_execution_performed": False,
                "model_send_performed": False,
                "codex_exec_performed": False,
                "private_payload_materialized": False,
                "value_claim_created": False,
                "errors": list(self.errors),
                "warnings": list(self.warnings),
            }
        }


def validate_agent_use_value_manifest(path: Path = DEFAULT_JSH1043_MANIFEST_PATH) -> AgentUseValueManifestValidation:
    errors: list[str] = []
    warnings: list[str] = []
    case_count = 0
    family_count = 0
    blocked_by_host_guard = False
    lane_execution_started = False

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return AgentUseValueManifestValidation(
            str(path),
            "fail",
            (f"could not read manifest: {exc}",),
            (),
            0,
            0,
            False,
            False,
        )
    except json.JSONDecodeError as exc:
        return AgentUseValueManifestValidation(
            str(path),
            "fail",
            (f"invalid JSON: {exc}",),
            (),
            0,
            0,
            False,
            False,
        )

    if not isinstance(manifest, dict):
        errors.append("manifest must be a JSON object")
        manifest = {}

    _expect_equal(manifest.get("schema_version"), JSH1043_SCHEMA_VERSION, "schema_version", errors)
    _expect_equal(manifest.get("status"), "frozen_before_lane_execution", "status", errors)
    _expect_equal(manifest.get("source_design"), "docs/jsh-1042-agent-use-value-gate-design.md", "source_design", errors)
    _expect_equal(
        manifest.get("task"),
        "tasks/jsh-1043-agent-use-value-gate-with-retrieval-performance-arms.md",
        "task",
        errors,
    )
    _expect_list_contains(manifest.get("global_lanes"), REQUIRED_GLOBAL_LANES, "global_lanes", errors)
    _expect_list_contains(manifest.get("required_lane_artifacts"), REQUIRED_LANE_ARTIFACTS, "required_lane_artifacts", errors)
    _expect_list_contains(manifest.get("decision_thresholds"), REQUIRED_DECISION_THRESHOLDS, "decision_thresholds", errors)

    privacy = manifest.get("privacy_contract")
    if not isinstance(privacy, dict):
        errors.append("privacy_contract must be an object")
    else:
        _expect_equal(privacy.get("committed_safe"), True, "privacy_contract.committed_safe", errors)
        _expect_equal(privacy.get("raw_private_text_committed"), False, "privacy_contract.raw_private_text_committed", errors)
        _expect_equal(
            privacy.get("raw_lane_traces_parent_private_root"),
            ".private/jsh-1043/<run-id>/",
            "privacy_contract.raw_lane_traces_parent_private_root",
            errors,
        )
        _expect_equal(
            privacy.get("worktree_private_retention_allowed"),
            False,
            "privacy_contract.worktree_private_retention_allowed",
            errors,
        )
        retained = privacy.get("retained_private_evidence_handle")
        if not isinstance(retained, str) or not retained.startswith(".private/jsh-1043/"):
            errors.append("privacy_contract.retained_private_evidence_handle must start with .private/jsh-1043/")
        if isinstance(retained, str) and retained.startswith("worktrees/"):
            errors.append("privacy_contract.retained_private_evidence_handle must not point inside worktrees/")

    cases = manifest.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be a list")
        cases = []
    case_count = len(cases)
    if case_count < MIN_CASES or case_count > MAX_CASES:
        errors.append(f"cases must contain {MIN_CASES}-{MAX_CASES} entries, found {case_count}")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    duplicate_case_ids = sorted({case_id for case_id in case_ids if case_ids.count(case_id) > 1})
    if duplicate_case_ids:
        errors.append(f"duplicate case_id values: {', '.join(str(item) for item in duplicate_case_ids)}")
    families = {case.get("family") for case in cases if isinstance(case, dict) and case.get("family")}
    family_count = len(families)
    if family_count != case_count:
        warnings.append("case family count differs from case count; verify intentional duplicate-family coverage")
    for index, case in enumerate(cases):
        _validate_case(case, index, errors)

    execution = manifest.get("current_execution_status")
    if not isinstance(execution, dict):
        errors.append("current_execution_status must be an object")
    else:
        blocked_by_host_guard = bool(execution.get("blocked_by_host_guard"))
        lane_execution_started = bool(execution.get("lane_execution_started"))
        if lane_execution_started and blocked_by_host_guard:
            errors.append("lane_execution_started cannot be true while blocked_by_host_guard is true")
        if blocked_by_host_guard:
            reason = execution.get("blocked_reason")
            if not isinstance(reason, str) or "HEAVY_COMPUTE_GUARD_STATUS=BLOCK" not in reason:
                errors.append("blocked host-guard status must include HEAVY_COMPUTE_GUARD_STATUS=BLOCK in blocked_reason")
            log_path = execution.get("host_guard_private_log")
            if not isinstance(log_path, str) or not log_path.startswith(".private/jsh-1043/"):
                errors.append("host_guard_private_log must point to parent .private/jsh-1043/")

    status = "fail" if errors else "pass"
    return AgentUseValueManifestValidation(
        str(path),
        status,
        tuple(errors),
        tuple(warnings),
        case_count,
        family_count,
        blocked_by_host_guard,
        lane_execution_started,
    )


def build_agent_use_value_manifest_validation_payload(path: Path = DEFAULT_JSH1043_MANIFEST_PATH) -> dict[str, Any]:
    return validate_agent_use_value_manifest(path).to_payload()


def _validate_case(case: Any, index: int, errors: list[str]) -> None:
    label = f"cases[{index}]"
    if not isinstance(case, dict):
        errors.append(f"{label} must be an object")
        return
    for key in (
        "case_id",
        "family",
        "question_shape",
        "expected_facets",
        "seed_evidence",
        "applicable_arm_lanes",
        "forbidden_hints",
    ):
        if key not in case:
            errors.append(f"{label}.{key} is required")
    for key in ("expected_facets", "seed_evidence", "applicable_arm_lanes", "forbidden_hints"):
        value = case.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"{label}.{key} must be a non-empty list")
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.startswith("C"):
        errors.append(f"{label}.case_id must be a C-prefixed string")


def _expect_equal(value: Any, expected: Any, field: str, errors: list[str]) -> None:
    if value != expected:
        errors.append(f"{field} must be {expected!r}, found {value!r}")


def _expect_list_contains(value: Any, required: tuple[str, ...], field: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{field} must be a list")
        return
    missing = [item for item in required if item not in value]
    if missing:
        errors.append(f"{field} missing required entries: {', '.join(missing)}")
