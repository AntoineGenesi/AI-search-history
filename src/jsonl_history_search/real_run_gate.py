from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GATE_SCHEMA_VERSION = "jsh-606-reviewed-one-case-real-run-gate.v1"
GATE_TASK_ID = "jsh-606-reviewed-one-case-real-run-gate-after-minimum-loop"
GATE_SUMMARY_FILENAME = "real-run-gate-summary.json"
DEFAULT_JSH605_SUMMARY_PATH = Path(
    "benchmarks/paired_agent/"
    "jsh-605-harness-minimum-user-facing-dry-run-and-adjudication-loop/"
    "minimum-user-facing-loop-summary.json"
)
DEFAULT_JSH605_REVIEW_PATH = Path(
    "codex/state/reviews/"
    "jsh-605-harness-minimum-user-facing-dry-run-and-adjudication-loop.md"
)
AUTHORIZED_CHILD_TASK_ID = "jsh-607-one-case-a-a2-b-real-run-execution-after-reviewed-gate"
AUTHORIZED_CHILD_TASK_PATH = f"tasks/{AUTHORIZED_CHILD_TASK_ID}.md"


def build_real_run_gate_payload(
    *,
    summary_path: Path = DEFAULT_JSH605_SUMMARY_PATH,
    review_path: Path = DEFAULT_JSH605_REVIEW_PATH,
    output_dir: Path,
    report_output: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_summary = _read_json(summary_path)
    review_text = (
        review_path.read_text(encoding="utf-8", errors="replace")
        if review_path.exists()
        else ""
    )
    loop = source_summary.get("harness_minimum_user_facing_loop", {})
    readiness = loop.get("readiness", {})
    boundary = loop.get("counter_and_claim_boundaries", {})
    artifact_paths = loop.get("artifact_paths", {})

    checks = {
        "jsh605_review_verified": "pass" if "verdict: VERIFIED" in review_text else "fail",
        "jsh605_loop_status": "pass" if loop.get("status") == "pass" else "fail",
        "jsh605_selected_this_gate": (
            "pass"
            if loop.get("next_action", {}).get("action") == "real_run_gate"
            and loop.get("next_action", {}).get("task") == GATE_TASK_ID
            else "fail"
        ),
        "readiness_all_pass": (
            "pass"
            if readiness and all(value == "pass" for value in readiness.values())
            else "fail"
        ),
        "case_alignment": "pass" if loop.get("case_alignment", {}).get("pass") is True else "fail",
        "unavailable_counter_policy": (
            "pass"
            if boundary.get("missing_counter_value") == "unavailable"
            and boundary.get("unavailable_counters_preserved") is True
            else "fail"
        ),
        "no_send_no_upload_no_broad_claim_boundary": "pass" if _boundary_passes(boundary) else "fail",
        "artifact_paths_exist": "pass" if _artifact_paths_exist(artifact_paths) else "fail",
    }
    status = "pass" if all(value == "pass" for value in checks.values()) else "fail"
    decision = _decision(status=status, checks=checks, loop=loop)
    payload = {
        "harness_real_run_gate": {
            "schema_version": GATE_SCHEMA_VERSION,
            "task_id": GATE_TASK_ID,
            "status": status,
            "mode": "no_send_reviewed_real_run_gate",
            "source_summary_path": str(summary_path),
            "source_review_path": str(review_path),
            "output_dir": str(output_dir),
            "report_output": str(report_output) if report_output else None,
            "case_id": loop.get("case_id"),
            "source_loop_status": loop.get("status"),
            "source_loop_next_action": loop.get("next_action"),
            "checks": checks,
            "readiness_snapshot": readiness,
            "case_alignment": loop.get("case_alignment"),
            "counter_and_claim_boundaries": boundary,
            "artifact_paths": artifact_paths,
            "decision": decision,
            "authorized_child_task": _authorized_child_task(loop.get("case_id")),
        }
    }
    _write_json(output_dir / GATE_SUMMARY_FILENAME, payload)
    if report_output is not None:
        report_output.parent.mkdir(parents=True, exist_ok=True)
        report_output.write_text(render_real_run_gate_report(payload), encoding="utf-8")
    return payload


def render_real_run_gate_report(payload: dict[str, Any]) -> str:
    gate = payload["harness_real_run_gate"]
    decision = gate["decision"]
    lines = [
        "# JSH-606 Reviewed One-Case Real-Run Gate",
        "",
        f"**Status:** `{gate['status']}`",
        f"**Mode:** `{gate['mode']}`",
        f"**Case:** `{gate['case_id']}`",
        "**Live/model send in this gate:** `not_performed`",
        "**Codex exec in this gate:** `not_performed`",
        "**Docker action in this gate:** `not_performed`",
        "**Private upload:** `not_performed`",
        "**Broad value/performance/winner claim:** `false`",
        "",
        "## Source",
        "",
        f"- JSH-605 summary: `{gate['source_summary_path']}`",
        f"- JSH-605 review: `{gate['source_review_path']}`",
        f"- Source loop status: `{gate['source_loop_status']}`",
        "",
        "## Gate Checks",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in gate["checks"].items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Action: `{decision['action']}`",
            f"- Task: `{decision['task']}`",
            f"- Reason: {decision['reason']}",
            "",
            "## Authorized Child Contract",
            "",
        ]
    )
    for key, value in gate["authorized_child_task"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This gate did not run a live/model send, `codex exec`, Docker, "
            "Inspect, or any private upload.",
            "- This gate authorizes only a separately reviewed child task; it "
            "does not itself produce a benchmark result.",
            "- No speed, token-efficiency, value, winner, or JSONL-beats-default claim is authorized.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_real_run_gate_text(payload: dict[str, Any]) -> str:
    gate = payload["harness_real_run_gate"]
    decision = gate["decision"]
    lines = [
        "Reviewed One-Case Real-Run Gate",
        f"Status: {gate['status']}",
        f"Case: {gate['case_id']}",
        "Live/model send in this gate: not_performed",
    ]
    for key, value in gate["checks"].items():
        lines.append(f"Check {key}: {value}")
    lines.extend(
        [
            f"Decision action: {decision['action']}",
            f"Decision task: {decision['task']}",
            f"Decision reason: {decision['reason']}",
        ]
    )
    return "\n".join(lines)


def _decision(*, status: str, checks: dict[str, str], loop: dict[str, Any]) -> dict[str, Any]:
    if status == "pass":
        return {
            "action": "authorize_child_task",
            "task": AUTHORIZED_CHILD_TASK_ID,
            "reason": (
                "Verified JSH-605 readiness is aligned, all no-send gates pass, "
                "and the source loop selected this reviewed gate. The next step "
                "is a separate child task that may perform one capped A/A2/B "
                "comparison only after its own pre-send checks."
            ),
        }
    failing = sorted(key for key, value in checks.items() if value != "pass")
    return {
        "action": "repair_or_stop",
        "task": "jsh-607-real-run-gate-readiness-repair-or-stop",
        "reason": f"Real-run gate checks failed: {', '.join(failing)}.",
    }


def _authorized_child_task(case_id: str | None) -> dict[str, Any]:
    return {
        "task_id": AUTHORIZED_CHILD_TASK_ID,
        "task_path": AUTHORIZED_CHILD_TASK_PATH,
        "case_id": case_id,
        "lane_plan": "one serialized Scenario A, one Scenario A2, and one Scenario B attempt",
        "max_live_model_sends": 3,
        "max_attempts_per_lane": 1,
        "requires_same_run_no_send_gate": True,
        "requires_independent_review_before_merge": True,
        "stop_on_pre_send_gate_failure": True,
        "stop_on_prompt_hash_or_case_alignment_mismatch": True,
        "stop_on_private_upload_or_raw_payload_materialization": True,
        "claim_boundary": "trace_capture_only_no_winner_speed_value_or_jsonl_superiority_claim",
    }


def _boundary_passes(boundary: dict[str, Any]) -> bool:
    false_flags = [
        "live_model_send_performed",
        "codex_exec_performed",
        "docker_action_performed",
        "private_upload_performed",
        "winner_claim",
        "value_claim",
        "jsonl_superiority_claim",
        "speed_claim",
        "token_efficiency_claim",
    ]
    return all(boundary.get(flag) is False for flag in false_flags)


def _artifact_paths_exist(artifact_paths: dict[str, Any]) -> bool:
    required = [
        "dry_run_dir",
        "dry_run_report",
        "offline_scorer_dir",
        "offline_scorer_report",
        "inspect_adapter_export",
        "summary",
    ]
    return all(Path(str(artifact_paths.get(key, ""))).exists() for key in required)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
