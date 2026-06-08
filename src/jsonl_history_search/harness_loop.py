from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .harness_artifacts import UNAVAILABLE, build_harness_dry_run_artifacts_payload, build_harness_offline_scorer_artifacts_payload
from .inspect_adapter import write_inspect_compatible_export


LOOP_SCHEMA_VERSION = "jsh-605-harness-minimum-user-facing-loop.v1"
LOOP_TASK_ID = "jsh-605-harness-minimum-user-facing-dry-run-and-adjudication-loop"
LOOP_SUMMARY_FILENAME = "minimum-user-facing-loop-summary.json"
DEFAULT_MINIMUM_LOOP_CASE_ID = "jsh-060-case-060c-0013"


def build_harness_minimum_loop_payload(
    *,
    output_dir: Path,
    case_id: str = DEFAULT_MINIMUM_LOOP_CASE_ID,
    report_output: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    dry_run_dir = output_dir / "dry-run"
    offline_scorer_dir = output_dir / "offline-scorer"
    inspect_export_path = output_dir / "inspect-adapter-proof.json"
    dry_run_report = output_dir / "dry-run-report.md"
    offline_scorer_report = output_dir / "offline-scorer-report.md"

    dry_run_payload = build_harness_dry_run_artifacts_payload(
        case_id=case_id,
        output_dir=dry_run_dir,
        report_output=dry_run_report,
    )["harness_dry_run_artifacts"]
    offline_scorer_payload = build_harness_offline_scorer_artifacts_payload(
        output_dir=offline_scorer_dir,
        report_output=offline_scorer_report,
    )["harness_offline_scorer_artifacts"]
    inspect_export = write_inspect_compatible_export(output_path=inspect_export_path, sequence=3)

    readiness = {
        "case_readiness": dry_run_payload["status"],
        "scorer_adjudication_readiness": offline_scorer_payload["status"],
        "inspect_export_status": inspect_export["status"],
        "prompt_source_material_readiness": (
            "pass"
            if dry_run_payload["status"] == "pass"
            and dry_run_payload["scenario_b_execution_performed"] is False
            else "fail"
        ),
        "missing_unavailable_counter_policy": (
            "pass"
            if dry_run_payload["missing_live_counter_value"] == UNAVAILABLE
            and offline_scorer_payload["missing_live_counter_value"] == UNAVAILABLE
            and inspect_export["jsonl_row_summary"]["missing_counters_inferred_as_zero"] is False
            else "fail"
        ),
        "contamination_gate": (
            "pass"
            if inspect_export["jsonl_row_summary"]["contamination_labels"] == ["counter_unavailable"]
            else "fail"
        ),
        "source_backed_adjudication": (
            "pass"
            if inspect_export["inspect_projection"]["scorer_output"]["source_backed_adjudication_preserved"]
            else "fail"
        ),
        "retained_refs": (
            "pass"
            if inspect_export["inspect_projection"]["scorer_output"]["retained_evidence_refs_preserved"]
            else "fail"
        ),
        "no_upload_no_broad_claim_gate": "pass",
    }
    status = "pass" if all(value == "pass" for value in readiness.values()) else "fail"
    next_action = _next_action(status=status, readiness=readiness)

    payload = {
        "harness_minimum_user_facing_loop": {
            "schema_version": LOOP_SCHEMA_VERSION,
            "task_id": LOOP_TASK_ID,
            "status": status,
            "mode": "no_send_committed_safe_user_facing_loop",
            "case_id": case_id,
            "output_dir": str(output_dir),
            "report_output": str(report_output) if report_output else None,
            "artifact_paths": {
                "dry_run_dir": str(dry_run_dir),
                "dry_run_report": str(dry_run_report),
                "offline_scorer_dir": str(offline_scorer_dir),
                "offline_scorer_report": str(offline_scorer_report),
                "inspect_adapter_export": str(inspect_export_path),
                "summary": str(output_dir / LOOP_SUMMARY_FILENAME),
            },
            "readiness": readiness,
            "case_readiness": {
                "status": dry_run_payload["status"],
                "case_id": dry_run_payload["case_id"],
                "run_id": dry_run_payload["run_id"],
                "validation_status": dry_run_payload["validation_status"],
                "scenario_a2_calibration_only": dry_run_payload["scenario_a2_calibration_only"],
                "prompt_hashes": dry_run_payload["prompt_hashes"],
            },
            "scorer_adjudication_readiness": {
                "status": offline_scorer_payload["status"],
                "run_id": offline_scorer_payload["run_id"],
                "fixture_count": offline_scorer_payload["fixture_count"],
                "deterministic_score_status": offline_scorer_payload["deterministic_score_status"],
                "reviewer_needed_fixture_ids": offline_scorer_payload["reviewer_needed_fixture_ids"],
                "validation_status": offline_scorer_payload["validation_status"],
            },
            "inspect_export_status": {
                "status": inspect_export["status"],
                "sample_id": inspect_export["inspect_projection"]["dataset_sample"]["id"],
                "case_id": inspect_export["jsonl_row_summary"]["case_id"],
                "runner_contract": inspect_export["inspect_projection"]["dataset_sample"]["input"]["runner_contract"],
                "inspect_hard_dependency_required": inspect_export["inspect_ai"]["hard_dependency_required"],
                "inspect_current_environment_importable": inspect_export["inspect_ai"]["current_environment_importable"],
                "source_backed_adjudication_preserved": inspect_export["inspect_projection"]["scorer_output"]["source_backed_adjudication_preserved"],
                "retained_evidence_refs_preserved": inspect_export["inspect_projection"]["scorer_output"]["retained_evidence_refs_preserved"],
            },
            "counter_and_claim_boundaries": {
                "missing_counter_value": UNAVAILABLE,
                "unavailable_counters_preserved": readiness["missing_unavailable_counter_policy"] == "pass",
                "contamination_labels": inspect_export["jsonl_row_summary"]["contamination_labels"],
                "live_model_send_performed": False,
                "codex_exec_performed": False,
                "docker_action_performed": False,
                "private_upload_performed": False,
                "winner_claim": False,
                "value_claim": False,
                "jsonl_superiority_claim": False,
                "speed_claim": False,
                "token_efficiency_claim": False,
            },
            "case_alignment": {
                "dry_run_case_id": dry_run_payload["case_id"],
                "inspect_export_case_id": inspect_export["jsonl_row_summary"]["case_id"],
                "pass": dry_run_payload["case_id"] == inspect_export["jsonl_row_summary"]["case_id"],
            },
            "next_action": next_action,
        }
    }
    _write_json(output_dir / LOOP_SUMMARY_FILENAME, payload)
    if report_output is not None:
        report_output.parent.mkdir(parents=True, exist_ok=True)
        report_output.write_text(render_harness_minimum_loop_report(payload), encoding="utf-8")
    return payload


def render_harness_minimum_loop_report(payload: dict[str, Any]) -> str:
    loop = payload["harness_minimum_user_facing_loop"]
    readiness = loop["readiness"]
    next_action = loop["next_action"]
    lines = [
        "# JSH-605 Harness Minimum User-Facing Dry-Run And Adjudication Loop",
        "",
        f"**Status:** `{loop['status']}`",
        f"**Mode:** `{loop['mode']}`",
        f"**Case:** `{loop['case_id']}`",
        "**Live/model send:** `not_performed`",
        "**Codex exec:** `not_performed`",
        "**Docker action:** `not_performed`",
        "**Private upload:** `not_performed`",
        "**Broad value/performance/winner claim:** `false`",
        "",
        "## Readiness",
        "",
        "| Check | Status |",
        "| --- | --- |",
    ]
    for key, value in readiness.items():
        lines.append(f"| `{key}` | `{value}` |")
    lines.extend(
        [
            "",
            "## Surfaces",
            "",
            f"- Case readiness: `{loop['case_readiness']['status']}`",
            f"- Scorer/adjudication readiness: `{loop['scorer_adjudication_readiness']['status']}`",
            f"- Inspect-compatible export: `{loop['inspect_export_status']['status']}`",
            f"- Case alignment: `{str(loop['case_alignment']['pass']).lower()}`",
            f"- Runner contract: `{loop['inspect_export_status']['runner_contract']}`",
            f"- Missing counter value: `{loop['counter_and_claim_boundaries']['missing_counter_value']}`",
            f"- Contamination labels: `{', '.join(loop['counter_and_claim_boundaries']['contamination_labels'])}`",
            "",
            "## Next Action",
            "",
            f"- Action: `{next_action['action']}`",
            f"- Task: `{next_action['task']}`",
            f"- Reason: {next_action['reason']}",
            "",
            "## Artifacts",
            "",
        ]
    )
    for key, path in loop["artifact_paths"].items():
        lines.append(f"- `{key}`: `{path}`")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- This is a no-send user-facing harness readiness loop.",
            "- It does not run an agent, call a model, start Docker, install Inspect, or upload private data.",
            "- It does not claim JSONL is faster, cheaper, better, or the winner.",
        ]
    )
    return "\n".join(lines) + "\n"


def _next_action(*, status: str, readiness: dict[str, str]) -> dict[str, Any]:
    if status == "pass":
        return {
            "action": "real_run_gate",
            "task": "jsh-606-reviewed-one-case-real-run-gate-after-minimum-loop",
            "reason": (
                "The no-send user-facing loop can build case readiness, scorer "
                "readiness, and Inspect-compatible export surfaces without boundary "
                "failures. The next step is a reviewed gate that decides whether and "
                "how to perform one real A/A2/B comparison."
            ),
        }
    failing = sorted(key for key, value in readiness.items() if value != "pass")
    return {
        "action": "repair_task",
        "task": "jsh-606-minimum-loop-readiness-repair",
        "reason": f"Readiness checks failed: {', '.join(failing)}.",
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
