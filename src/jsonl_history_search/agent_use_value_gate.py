from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_JSH1043_MANIFEST_PATH = Path("benchmarks/agent_use_value/jsh-1043-case-manifest.json")
DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH = Path(
    "reports/jsh-1043-reduced-warn-agent-use-value-gate-summary.json"
)
DEFAULT_JSH1045_EXPANSION_SUMMARY_PATH = Path(
    "reports/jsh-1045-agent-use-value-gate-expansion-after-stop-contract-summary.json"
)
JSH1043_SCHEMA_VERSION = "jsh-1043-agent-use-value-case-manifest.v1"
JSH1043_REDUCED_RUN_SCHEMA_VERSION = "jsh-1043-agent-use-value-reduced-run-summary.v1"
JSH1045_EXPANSION_SCHEMA_VERSION = "jsh-1045-agent-use-value-expansion-summary.v1"
REQUIRED_GLOBAL_LANES = ("manual_default_clean", "jsonl_current_default")
ALLOWED_MANIFEST_STATUSES = (
    "frozen_before_lane_execution",
    "frozen_reduced_warn_lane_execution_started",
)
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
MIN_REDUCED_RUN_CASES = 2
MIN_EXPANSION_CASES = 3


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


@dataclass(frozen=True)
class AgentUseValueReducedRunValidation:
    summary_path: str
    status: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    run_id: str
    case_count: int
    lane_count: int
    raw_private_trace_root: str
    host_guard_status: str
    result_class: str
    raw_file_check_enabled: bool
    raw_file_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_use_value_reduced_run_validation": {
                "schema_version": JSH1043_REDUCED_RUN_SCHEMA_VERSION,
                "status": self.status,
                "mode": "committed_safe_reduced_run_summary_validation",
                "summary_path": self.summary_path,
                "run_id": self.run_id,
                "case_count": self.case_count,
                "lane_count": self.lane_count,
                "raw_private_trace_root": self.raw_private_trace_root,
                "host_guard_status": self.host_guard_status,
                "result_class": self.result_class,
                "raw_file_check_enabled": self.raw_file_check_enabled,
                "raw_file_count": self.raw_file_count,
                "raw_private_text_committed": False,
                "requires_parent_private_trace_root": True,
                "errors": list(self.errors),
                "warnings": list(self.warnings),
            }
        }


@dataclass(frozen=True)
class AgentUseValueExpansionValidation:
    summary_path: str
    status: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    run_id: str
    case_count: int
    lane_count: int
    raw_private_trace_root: str
    host_guard_status: str
    result_class: str
    c04_answer_ready: bool
    c04_stop_broad_search: bool
    c04_contract_passed: bool
    raw_file_check_enabled: bool
    raw_file_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "agent_use_value_expansion_validation": {
                "schema_version": JSH1045_EXPANSION_SCHEMA_VERSION,
                "status": self.status,
                "mode": "committed_safe_expansion_summary_validation",
                "summary_path": self.summary_path,
                "run_id": self.run_id,
                "case_count": self.case_count,
                "lane_count": self.lane_count,
                "raw_private_trace_root": self.raw_private_trace_root,
                "host_guard_status": self.host_guard_status,
                "result_class": self.result_class,
                "c04_answer_ready": self.c04_answer_ready,
                "c04_stop_broad_search": self.c04_stop_broad_search,
                "c04_contract_passed": self.c04_contract_passed,
                "raw_file_check_enabled": self.raw_file_check_enabled,
                "raw_file_count": self.raw_file_count,
                "raw_private_text_committed": False,
                "requires_parent_private_trace_root": True,
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
    if manifest.get("status") not in ALLOWED_MANIFEST_STATUSES:
        errors.append(f"status must be one of {', '.join(ALLOWED_MANIFEST_STATUSES)}, found {manifest.get('status')!r}")
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
        if lane_execution_started and not blocked_by_host_guard:
            summary_path = execution.get("reduced_warn_run_summary")
            if summary_path != str(DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH):
                errors.append("reduced execution must point to the committed-safe reduced run summary")
            trace_root = execution.get("reduced_warn_private_trace_root")
            if not isinstance(trace_root, str) or not trace_root.startswith(".private/jsh-1043/"):
                errors.append("reduced_warn_private_trace_root must point to parent .private/jsh-1043/")
            if execution.get("current_guard_status") not in {"OK", "WARN"}:
                errors.append("current_guard_status must be OK or WARN for reduced execution")

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


def validate_agent_use_value_reduced_run_summary(
    path: Path = DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH,
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
) -> AgentUseValueReducedRunValidation:
    errors: list[str] = []
    warnings: list[str] = []
    run_id = ""
    raw_private_trace_root = ""
    host_guard_status = ""
    result_class = ""
    case_count = 0
    lane_count = 0
    raw_file_count = 0
    resolved_private_root = private_root or Path(".")

    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return AgentUseValueReducedRunValidation(
            str(path),
            "fail",
            (f"could not read summary: {exc}",),
            (),
            "",
            0,
            0,
            "",
            "",
            "",
            require_raw_files,
            0,
        )
    except json.JSONDecodeError as exc:
        return AgentUseValueReducedRunValidation(
            str(path),
            "fail",
            (f"invalid JSON: {exc}",),
            (),
            "",
            0,
            0,
            "",
            "",
            "",
            require_raw_files,
            0,
        )

    if not isinstance(summary, dict):
        errors.append("summary must be a JSON object")
        summary = {}

    _expect_equal(summary.get("schema_version"), JSH1043_REDUCED_RUN_SCHEMA_VERSION, "schema_version", errors)
    _expect_equal(summary.get("source_manifest"), str(DEFAULT_JSH1043_MANIFEST_PATH), "source_manifest", errors)
    _expect_equal(summary.get("source_design"), "docs/jsh-1042-agent-use-value-gate-design.md", "source_design", errors)
    _expect_equal(summary.get("task"), "tasks/jsh-1043-agent-use-value-gate-with-retrieval-performance-arms.md", "task", errors)

    run_id_value = summary.get("run_id")
    if isinstance(run_id_value, str):
        run_id = run_id_value
    if not run_id:
        errors.append("run_id must be a non-empty string")

    raw_private_trace_root_value = summary.get("raw_private_trace_root")
    if isinstance(raw_private_trace_root_value, str):
        raw_private_trace_root = raw_private_trace_root_value
    if not raw_private_trace_root.startswith(".private/jsh-1043/"):
        errors.append("raw_private_trace_root must point to parent .private/jsh-1043/")
    if "worktrees/" in raw_private_trace_root:
        errors.append("raw_private_trace_root must not point inside worktrees/")

    host_guard = summary.get("host_guard")
    if not isinstance(host_guard, dict):
        errors.append("host_guard must be an object")
    else:
        status_value = host_guard.get("status")
        if isinstance(status_value, str):
            host_guard_status = status_value
        if host_guard_status not in {"OK", "WARN"}:
            errors.append("host_guard.status must be OK or WARN for reduced execution")
        if host_guard_status == "WARN" and host_guard.get("user_explicitly_accepted_warn_progress") is not True:
            errors.append("WARN reduced execution requires user_explicitly_accepted_warn_progress=true")
        private_log = host_guard.get("private_log")
        if not isinstance(private_log, str) or not private_log.startswith(".private/jsh-1043/"):
            errors.append("host_guard.private_log must point to parent .private/jsh-1043/")

    cases = summary.get("selected_cases")
    if not isinstance(cases, list):
        errors.append("selected_cases must be a list")
        cases = []
    case_count = len(cases)
    if case_count < MIN_REDUCED_RUN_CASES:
        errors.append(f"selected_cases must contain at least {MIN_REDUCED_RUN_CASES} cases")

    for case_index, case in enumerate(cases):
        lane_count += _validate_reduced_run_case(
            case,
            case_index,
            errors,
            require_raw_files=require_raw_files,
            private_root=resolved_private_root,
        )
        if require_raw_files and isinstance(case, dict):
            raw_file_count += _count_lane_artifact_paths(case)

    if require_raw_files:
        host_guard_log = summary.get("host_guard", {}).get("private_log") if isinstance(summary.get("host_guard"), dict) else None
        if isinstance(host_guard_log, str):
            raw_file_count += 1
            _validate_raw_artifact_path(
                host_guard_log,
                "host_guard.private_log",
                errors,
                private_root=resolved_private_root,
                parse_json=False,
            )

    result_class_value = summary.get("result_class")
    if isinstance(result_class_value, str):
        result_class = result_class_value
    if result_class not in REQUIRED_DECISION_THRESHOLDS:
        errors.append("result_class must be one of the pre-registered decision thresholds")

    if summary.get("raw_private_text_committed") is not False:
        errors.append("raw_private_text_committed must be false")

    status = "fail" if errors else "pass"
    return AgentUseValueReducedRunValidation(
        str(path),
        status,
        tuple(errors),
        tuple(warnings),
        run_id,
        case_count,
        lane_count,
        raw_private_trace_root,
        host_guard_status,
        result_class,
        require_raw_files,
        raw_file_count,
    )


def build_agent_use_value_manifest_validation_payload(path: Path = DEFAULT_JSH1043_MANIFEST_PATH) -> dict[str, Any]:
    return validate_agent_use_value_manifest(path).to_payload()


def build_agent_use_value_reduced_run_validation_payload(
    path: Path = DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH,
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
) -> dict[str, Any]:
    return validate_agent_use_value_reduced_run_summary(
        path,
        require_raw_files=require_raw_files,
        private_root=private_root,
    ).to_payload()


def validate_agent_use_value_expansion_summary(
    path: Path = DEFAULT_JSH1045_EXPANSION_SUMMARY_PATH,
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
) -> AgentUseValueExpansionValidation:
    errors: list[str] = []
    warnings: list[str] = []
    run_id = ""
    raw_private_trace_root = ""
    host_guard_status = ""
    result_class = ""
    case_count = 0
    lane_count = 0
    raw_file_count = 0
    c04_answer_ready = False
    c04_stop_broad_search = False
    c04_contract_passed = False
    resolved_private_root = private_root or Path(".")

    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return AgentUseValueExpansionValidation(
            str(path),
            "fail",
            (f"could not read summary: {exc}",),
            (),
            "",
            0,
            0,
            "",
            "",
            "",
            False,
            False,
            False,
            require_raw_files,
            0,
        )
    except json.JSONDecodeError as exc:
        return AgentUseValueExpansionValidation(
            str(path),
            "fail",
            (f"invalid JSON: {exc}",),
            (),
            "",
            0,
            0,
            "",
            "",
            "",
            False,
            False,
            False,
            require_raw_files,
            0,
        )

    if not isinstance(summary, dict):
        errors.append("summary must be a JSON object")
        summary = {}

    _expect_equal(summary.get("schema_version"), JSH1045_EXPANSION_SCHEMA_VERSION, "schema_version", errors)
    _expect_equal(summary.get("source_manifest"), str(DEFAULT_JSH1043_MANIFEST_PATH), "source_manifest", errors)
    _expect_equal(summary.get("source_design"), "docs/jsh-1042-agent-use-value-gate-design.md", "source_design", errors)
    _expect_equal(
        summary.get("task"),
        "tasks/jsh-1045-agent-use-value-gate-expansion-after-stop-contract.md",
        "task",
        errors,
    )
    if summary.get("raw_private_text_committed") is not False:
        errors.append("raw_private_text_committed must be false")

    run_id_value = summary.get("run_id")
    if isinstance(run_id_value, str):
        run_id = run_id_value
    if not run_id:
        errors.append("run_id must be a non-empty string")

    raw_private_trace_root_value = summary.get("raw_private_trace_root")
    if isinstance(raw_private_trace_root_value, str):
        raw_private_trace_root = raw_private_trace_root_value
    if not raw_private_trace_root.startswith(".private/jsh-1045/"):
        errors.append("raw_private_trace_root must point to parent .private/jsh-1045/")
    if "worktrees/" in raw_private_trace_root:
        errors.append("raw_private_trace_root must not point inside worktrees/")

    host_guard = summary.get("host_guard")
    if not isinstance(host_guard, dict):
        errors.append("host_guard must be an object")
    else:
        status_value = host_guard.get("status")
        if isinstance(status_value, str):
            host_guard_status = status_value
        if host_guard_status not in {"OK", "WARN"}:
            errors.append("host_guard.status must be OK or WARN for scoped expansion")
        if host_guard_status == "WARN" and host_guard.get("user_explicitly_accepted_warn_progress") is not True:
            errors.append("WARN scoped expansion requires user_explicitly_accepted_warn_progress=true")
        private_log = host_guard.get("private_log")
        if not isinstance(private_log, str) or not private_log.startswith(".private/jsh-1045/"):
            errors.append("host_guard.private_log must point to parent .private/jsh-1045/")

    cases = summary.get("selected_cases")
    if not isinstance(cases, list):
        errors.append("selected_cases must be a list")
        cases = []
    case_count = len(cases)
    if case_count < MIN_EXPANSION_CASES:
        errors.append(f"selected_cases must contain at least {MIN_EXPANSION_CASES} cases")
    case_ids = {case.get("case_id") for case in cases if isinstance(case, dict)}
    if "C04-semantic-wrapup-failure-modes" not in case_ids:
        errors.append("selected_cases must include C04-semantic-wrapup-failure-modes")
    if "C10-literal-provenance-exact-identifier" not in case_ids:
        errors.append("selected_cases must include C10-literal-provenance-exact-identifier")
    if not ({"C06-historical-meta-fsm-provenance", "C08-churn-read-more-over-search"} & case_ids):
        errors.append("selected_cases must include at least one C06 historical/meta-FSM or C08 churn case")

    for case_index, case in enumerate(cases):
        lane_count += _validate_reduced_run_case(
            case,
            case_index,
            errors,
            require_raw_files=require_raw_files,
            private_root=resolved_private_root,
            private_prefix=".private/jsh-1045/",
        )
        if require_raw_files and isinstance(case, dict):
            raw_file_count += _count_lane_artifact_paths(case)

    if require_raw_files:
        host_guard_log = summary.get("host_guard", {}).get("private_log") if isinstance(summary.get("host_guard"), dict) else None
        if isinstance(host_guard_log, str):
            raw_file_count += 1
            _validate_raw_artifact_path(
                host_guard_log,
                "host_guard.private_log",
                errors,
                private_root=resolved_private_root,
                parse_json=False,
            )

    checks = summary.get("post_stop_contract_checks")
    if not isinstance(checks, dict):
        errors.append("post_stop_contract_checks must be an object")
    else:
        c04_contract = checks.get("c04_answer_ready_contract")
        if not isinstance(c04_contract, dict):
            errors.append("post_stop_contract_checks.c04_answer_ready_contract must be an object")
        else:
            c04_answer_ready = c04_contract.get("answer_ready") is True
            c04_stop_broad_search = c04_contract.get("stop_broad_search") is True
            c04_contract_passed = c04_contract.get("all_required_checks_passed") is True
            if not c04_answer_ready:
                errors.append("C04 contract must have answer_ready=true")
            if not c04_stop_broad_search:
                errors.append("C04 contract must have stop_broad_search=true")
            if c04_contract.get("decision") != "answer_ready_after_source_inspection":
                errors.append("C04 contract decision must be answer_ready_after_source_inspection")
            if c04_contract.get("required_next_action_mentions_no_broad_search") is not True:
                errors.append("C04 contract must prove required_next_action says not to broad-search first")
            if not c04_contract_passed:
                errors.append("C04 contract all_required_checks_passed must be true")
        if checks.get("full_gate_completion_claimed") is not False:
            errors.append("full_gate_completion_claimed must be false")
        if checks.get("broad_arm_promotion_claimed") is not False:
            errors.append("broad_arm_promotion_claimed must be false")

    result_class_value = summary.get("result_class")
    if isinstance(result_class_value, str):
        result_class = result_class_value
    if result_class not in REQUIRED_DECISION_THRESHOLDS:
        errors.append("result_class must be one of the pre-registered decision thresholds")

    status = "fail" if errors else "pass"
    return AgentUseValueExpansionValidation(
        str(path),
        status,
        tuple(errors),
        tuple(warnings),
        run_id,
        case_count,
        lane_count,
        raw_private_trace_root,
        host_guard_status,
        result_class,
        c04_answer_ready,
        c04_stop_broad_search,
        c04_contract_passed,
        require_raw_files,
        raw_file_count,
    )


def build_agent_use_value_expansion_validation_payload(
    path: Path = DEFAULT_JSH1045_EXPANSION_SUMMARY_PATH,
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
) -> dict[str, Any]:
    return validate_agent_use_value_expansion_summary(
        path,
        require_raw_files=require_raw_files,
        private_root=private_root,
    ).to_payload()


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


def _validate_reduced_run_case(
    case: Any,
    index: int,
    errors: list[str],
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
    private_prefix: str = ".private/jsh-1043/",
) -> int:
    label = f"selected_cases[{index}]"
    if not isinstance(case, dict):
        errors.append(f"{label} must be an object")
        return 0
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not case_id.startswith("C"):
        errors.append(f"{label}.case_id must be a C-prefixed string")
    lanes = case.get("lanes")
    if not isinstance(lanes, list):
        errors.append(f"{label}.lanes must be a list")
        return 0
    lane_names = [lane.get("lane") for lane in lanes if isinstance(lane, dict)]
    missing_global = [lane for lane in REQUIRED_GLOBAL_LANES if lane not in lane_names]
    if missing_global:
        errors.append(f"{label}.lanes missing required baseline lanes: {', '.join(missing_global)}")
    arm_lanes = [lane for lane in lane_names if isinstance(lane, str) and lane not in REQUIRED_GLOBAL_LANES]
    if not arm_lanes:
        errors.append(f"{label}.lanes must include at least one arm lane")
    for lane_index, lane in enumerate(lanes):
        _validate_reduced_run_lane(
            lane,
            f"{label}.lanes[{lane_index}]",
            errors,
            require_raw_files=require_raw_files,
            private_root=private_root,
            private_prefix=private_prefix,
        )
    return len(lanes)


def _validate_reduced_run_lane(
    lane: Any,
    label: str,
    errors: list[str],
    *,
    require_raw_files: bool = False,
    private_root: Path | None = None,
    private_prefix: str = ".private/jsh-1043/",
) -> None:
    if not isinstance(lane, dict):
        errors.append(f"{label} must be an object")
        return
    lane_name = lane.get("lane")
    if not isinstance(lane_name, str) or not lane_name:
        errors.append(f"{label}.lane must be a non-empty string")
    for key in ("artifact_paths", "artifact_completeness", "opened_source_refs", "cost_buckets", "facet_audit"):
        if key not in lane:
            errors.append(f"{label}.{key} is required")
    if lane.get("command_success") is not True:
        errors.append(f"{label}.command_success must be true")
    artifacts = lane.get("artifact_completeness")
    if not isinstance(artifacts, dict):
        errors.append(f"{label}.artifact_completeness must be an object")
    else:
        missing = [name for name in REQUIRED_LANE_ARTIFACTS if artifacts.get(name) is not True]
        if missing:
            errors.append(f"{label}.artifact_completeness missing true entries: {', '.join(missing)}")
    artifact_paths = lane.get("artifact_paths")
    if not isinstance(artifact_paths, dict):
        errors.append(f"{label}.artifact_paths must be an object")
    else:
        for key in REQUIRED_LANE_ARTIFACTS:
            path = artifact_paths.get(key)
            if not isinstance(path, str) or not path.startswith(private_prefix):
                errors.append(f"{label}.artifact_paths.{key} must point to parent {private_prefix}")
            elif require_raw_files:
                _validate_raw_artifact_path(
                    path,
                    f"{label}.artifact_paths.{key}",
                    errors,
                    private_root=private_root or Path("."),
                    parse_json=key
                    in {
                        "event_or_transcript_log",
                        "timing",
                        "metadata",
                        "command_or_tool_trace",
                        "opened_source_refs",
                        "cost_buckets",
                        "facet_audit",
                    },
                )
    cost_buckets = lane.get("cost_buckets")
    if not isinstance(cost_buckets, dict):
        errors.append(f"{label}.cost_buckets must be an object")
    else:
        for key in ("local_tool_work", "model_facing_handoff", "full_agent_loop_churn"):
            if key not in cost_buckets:
                errors.append(f"{label}.cost_buckets.{key} is required")
    facet_audit = lane.get("facet_audit")
    if not isinstance(facet_audit, dict):
        errors.append(f"{label}.facet_audit must be an object")
    elif "status" not in facet_audit:
        errors.append(f"{label}.facet_audit.status is required")


def _count_lane_artifact_paths(case: dict[str, Any]) -> int:
    lanes = case.get("lanes")
    if not isinstance(lanes, list):
        return 0
    count = 0
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        artifact_paths = lane.get("artifact_paths")
        if isinstance(artifact_paths, dict):
            count += sum(1 for value in artifact_paths.values() if isinstance(value, str))
    return count


def _validate_raw_artifact_path(
    raw_path: str,
    label: str,
    errors: list[str],
    *,
    private_root: Path,
    parse_json: bool,
) -> None:
    path = private_root / raw_path
    if not path.is_file():
        errors.append(f"{label} does not exist under private_root: {raw_path}")
        return
    if parse_json:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            errors.append(f"{label} could not be read: {exc}")
        except json.JSONDecodeError as exc:
            errors.append(f"{label} is not valid JSON: {exc}")


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
