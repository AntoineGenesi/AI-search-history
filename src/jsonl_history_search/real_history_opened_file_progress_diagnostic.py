from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_JSH706_BENCHMARK_REPORT = Path(
    "reports/jsh-706-real-history-package-search-source-opening-and-elapsed-budget-scheduling-repair.json"
)
DEFAULT_JSH706_MISS_AUDIT_REPORT = Path(
    "reports/jsh-706-real-history-package-search-source-opening-and-elapsed-budget-scheduling-repair-miss-audit.json"
)
DEFAULT_JSH707_BENCHMARK_REPORT = Path(
    "reports/jsh-707-real-history-package-search-opened-file-record-line-reach-repair.json"
)
DEFAULT_JSH707_MISS_AUDIT_REPORT = Path(
    "reports/jsh-707-opened-jsonl-midpoint-line-window-failed-attempt-miss-audit.json"
)
REPORT_KEY = "real_history_opened_file_progress_displacement_diagnostic"
REGRESSED_CASE_IDS = (
    "real-jsh047-did-agent-do-x-001",
    "real-jsh047-did-agent-do-x-002",
    "real-jsh047c2-date-timeline-005",
)
CANONICAL_OPENED_DATE_TIMELINE_MISSES = (
    "real-jsh047c2-date-timeline-001",
    "real-jsh047c2-date-timeline-002",
    "real-jsh047c2-date-timeline-006",
)
CALIBRATION_CASE_IDS = (
    "real-jsh047c2-date-timeline-003",
    "real-jsh047c2-date-timeline-004",
)


def build_real_history_opened_file_progress_diagnostic_payload(args) -> dict[str, Any]:
    jsh706_benchmark = _read_report(args.jsh706_benchmark_report, "real_history_impact_benchmark")
    jsh706_audit = _read_report(args.jsh706_miss_audit_report, "real_history_package_search_miss_audit")
    jsh707_benchmark = _read_report(args.jsh707_benchmark_report, "real_history_impact_benchmark")
    jsh707_audit = _read_report(args.jsh707_miss_audit_report, "real_history_package_search_miss_audit")
    raw706 = _read_optional_raw_run(getattr(args, "jsh706_raw_run", None))
    raw707 = _read_optional_raw_run(getattr(args, "jsh707_raw_run", None))

    case_ids = list(REGRESSED_CASE_IDS + CANONICAL_OPENED_DATE_TIMELINE_MISSES + CALIBRATION_CASE_IDS)
    rows = [
        _case_comparison(
            case_id=case_id,
            jsh706_benchmark=jsh706_benchmark,
            jsh706_audit=jsh706_audit,
            jsh707_benchmark=jsh707_benchmark,
            jsh707_audit=jsh707_audit,
            raw706=raw706,
            raw707=raw707,
        )
        for case_id in case_ids
    ]
    report = {
        "schema_version": 1,
        "status": "ok",
        "data_source": "committed_jsh706_jsh707_reports_plus_optional_ignored_raw_run_counters",
        "task": "jsh-708-real-history-package-search-opened-file-progress-and-candidate-displacement-diag",
        "compared_runs": _compared_runs(jsh706_benchmark, jsh707_benchmark, jsh706_audit, jsh707_audit),
        "privacy_contract": _privacy_contract(),
        "behavior_boundary": {
            "default_package_search_changed": False,
            "scanner_behavior_changed_without_diagnostic_opt_in": False,
            "blind_midpoint_or_positional_probe_retried": False,
            "ranking_or_candidate_retention_repair_claimed": False,
            "token_efficiency_or_product_value_claimed": False,
        },
        "evidence_limits": {
            "legacy_runs_have_expected_ref_reach_counters": True,
            "legacy_runs_have_per_opened_file_progress_records": _legacy_per_file_progress_available(rows),
            "new_opt_in_diagnostic_fields_added_for_future_runs": True,
            "line_window_probe_counters_available_for_jsh707_failed_run": any(
                row["jsh707_failed"]["line_window_probe"]["probe_records"] > 0 for row in rows
            ),
            "raw_runs_used_for_safe_probe_counters": bool(raw706 or raw707),
            "raw_run_paths_committed": False,
        },
        "summary": _summary(rows, jsh706_benchmark, jsh707_benchmark),
        "decision": {
            "headline": (
                "JSH-707 regressed three previously recovered package_search cases because the failed "
                "midpoint window probe consumed scan work inside opened files before the expected record "
                "line was reached; the canonical opened date-timeline misses still opened the expected "
                "file but stopped before reaching the expected record line."
            ),
            "repair_supported_by_this_diagnostic": False,
            "reject_retrying_blind_midpoint_or_positional_line_window_probe": True,
            "recommended_next_step": (
                "Use the new opt-in per-opened-file progress counters in a later measured diagnostic or "
                "narrow repair task; do not retry blind midpoint/positional line-window probing."
            ),
        },
        "regressed_case_ids": list(REGRESSED_CASE_IDS),
        "canonical_opened_date_timeline_miss_case_ids": list(CANONICAL_OPENED_DATE_TIMELINE_MISSES),
        "rows": rows,
    }
    return {REPORT_KEY: report}


def _case_comparison(
    *,
    case_id: str,
    jsh706_benchmark: dict[str, Any],
    jsh706_audit: dict[str, Any],
    jsh707_benchmark: dict[str, Any],
    jsh707_audit: dict[str, Any],
    raw706: dict[str, Any],
    raw707: dict[str, Any],
) -> dict[str, Any]:
    row706 = _benchmark_row(jsh706_benchmark, case_id)
    row707 = _benchmark_row(jsh707_benchmark, case_id)
    audit706 = _audit_row(jsh706_audit, case_id)
    audit707 = _audit_row(jsh707_audit, case_id)
    raw_row706 = _raw_package_row(raw706, case_id)
    raw_row707 = _raw_package_row(raw707, case_id)
    view706 = _case_run_view(row706, audit706, raw_row706, run_label="jsh_706")
    view707 = _case_run_view(row707, audit707, raw_row707, run_label="jsh_707_failed")
    return {
        "case_id": case_id,
        "family": str((row706 or row707 or audit706 or audit707 or {}).get("family") or "unknown"),
        "case_role": _case_role(case_id),
        "jsh706": view706,
        "jsh707_failed": view707,
        "candidate_displacement": _candidate_displacement(view706, view707),
    }


def _case_run_view(
    row: dict[str, Any] | None,
    audit_row: dict[str, Any] | None,
    raw_row: dict[str, Any] | None,
    *,
    run_label: str,
) -> dict[str, Any]:
    row = row or {}
    audit_row = audit_row or {}
    raw_result = (raw_row or {}).get("package_result") if isinstance((raw_row or {}).get("package_result"), dict) else {}
    scan_order = row.get("scan_order") if isinstance(row.get("scan_order"), dict) else {}
    expected_diag = _expected_diagnostics(row, raw_result)
    ref = _first_ref(expected_diag)
    line_window_probe = _line_window_probe(raw_result)
    opened_files_count = int(
        expected_diag.get("opened_file_count")
        or scan_order.get("opened_files_count")
        or row.get("files_opened")
        or raw_result.get("opened_files_count")
        or raw_result.get("searched_files")
        or 0
    )
    return {
        "run_label": run_label,
        "first_pass_hit": bool(row.get("first_pass_hit")),
        "candidate_count": int(row.get("candidate_count") or 0),
        "opened_files_count": opened_files_count,
        "records_read_total": int(scan_order.get("records_read") or raw_result.get("records_read") or 0),
        "records_rejected_by_should_consider": int(
            scan_order.get("records_rejected_by_should_consider")
            or raw_result.get("records_rejected_by_should_consider")
            or 0
        ),
        "candidate_records_scored": int(scan_order.get("candidate_records_scored") or raw_result.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(scan_order.get("candidate_records_deduped") or raw_result.get("candidate_records_deduped") or 0),
        "scan_order_strategy": str(scan_order.get("strategy") or raw_result.get("scan_order_strategy") or "unknown"),
        "cap_hit_labels": [str(item) for item in row.get("cap_hit_labels") or []],
        "warnings": [str(item) for item in row.get("warnings") or []],
        "line_window_probe": line_window_probe,
        "expected_ref": _expected_ref_view(ref),
        "opened_file_progress": _opened_file_progress(expected_diag, opened_files_count),
        "miss_audit": _audit_view(audit_row),
    }


def _expected_ref_view(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected": bool(ref.get("expected_file_selected_by_source_plan")),
        "queued": bool(ref.get("expected_file_queued_for_scan")),
        "opened": bool(ref.get("expected_file_opened_before_scan_stop")),
        "reached": bool(ref.get("expected_record_line_reached_before_elapsed_stop")),
        "candidate_strength": ref.get("candidate_strength") or "not_reached",
        "pre_reservoir_status": str(ref.get("pre_reservoir_status") or "unknown"),
        "pre_final_status": str(ref.get("pre_final_status") or "unknown"),
        "final_candidate_status": str(ref.get("final_status") or "unknown"),
        "final_output_status": (
            "present_in_final_output"
            if ref.get("expected_record_present_in_final_candidate_groups")
            else "absent_from_final_output"
        ),
        "expected_file_label": ref.get("expected_file_label"),
        "expected_file_open_order": ref.get("expected_file_open_order"),
        "expected_file_records_read_before_stop": int(ref.get("expected_file_records_read_before_stop") or 0),
        "expected_file_progress_bucket": str(ref.get("expected_file_progress_bucket") or "legacy_not_captured"),
        "should_consider_phase_label": str(ref.get("should_consider_phase_label") or "legacy_not_captured"),
        "dedupe_phase_label": str(ref.get("dedupe_phase_label") or "legacy_not_captured"),
        "reservoir_phase_label": str(ref.get("reservoir_phase_label") or "legacy_not_captured"),
        "final_cap_phase_label": str(ref.get("final_cap_phase_label") or "legacy_not_captured"),
        "drop_phase_label": ref.get("drop_phase_label") or "none_or_legacy_not_captured",
    }


def _opened_file_progress(expected_diag: dict[str, Any], opened_files_count: int) -> list[dict[str, Any]]:
    opened_files = expected_diag.get("opened_files") if isinstance(expected_diag.get("opened_files"), list) else []
    if opened_files:
        return [
            {
                "file_label": str(item.get("file_label") or f"opened_file_{index:03d}"),
                "opened_order": int(item.get("opened_order") or index),
                "records_read": int(item.get("records_read") or 0),
                "records_read_bucket": str(item.get("records_read_bucket") or "unknown"),
                "expected_ref_file": bool(item.get("expected_ref_file")),
                "counter_status": "measured",
            }
            for index, item in enumerate(opened_files, start=1)
            if isinstance(item, dict)
        ]
    return [
        {
            "file_label": f"opened_file_{index:03d}",
            "opened_order": index,
            "records_read": None,
            "records_read_bucket": "legacy_not_captured",
            "expected_ref_file": None,
            "counter_status": "legacy_run_did_not_capture_per_opened_file_progress",
        }
        for index in range(1, opened_files_count + 1)
    ]


def _candidate_displacement(jsh706: dict[str, Any], jsh707: dict[str, Any]) -> dict[str, Any]:
    ref706 = jsh706["expected_ref"]
    ref707 = jsh707["expected_ref"]
    labels = []
    if jsh706["first_pass_hit"] and not jsh707["first_pass_hit"]:
        labels.append("recovered_to_absent_final_output")
    if ref706["reached"] and not ref707["reached"] and ref707["opened"]:
        labels.append("opened_file_progress_displaced_before_expected_record_line")
    if jsh707["line_window_probe"]["probe_records"] > 0:
        labels.append("failed_midpoint_probe_consumed_scan_records")
    if jsh707["records_read_total"] > jsh706["records_read_total"]:
        labels.append("total_records_read_increased")
    if jsh707["candidate_records_deduped"] > jsh706["candidate_records_deduped"]:
        labels.append("dedupe_pressure_increased")
    if ref706["final_output_status"] == "present_in_final_output" and ref707["final_output_status"] != "present_in_final_output":
        labels.append("final_output_status_changed_to_absent")
    if not labels:
        labels.append("no_final_hit_regression_for_this_case")
    return {
        "displacement_labels": labels,
        "records_read_delta": jsh707["records_read_total"] - jsh706["records_read_total"],
        "candidate_records_scored_delta": jsh707["candidate_records_scored"] - jsh706["candidate_records_scored"],
        "candidate_records_deduped_delta": jsh707["candidate_records_deduped"] - jsh706["candidate_records_deduped"],
        "line_window_probe_records_added_in_jsh707": jsh707["line_window_probe"]["probe_records"],
        "line_window_probe_files_in_jsh707": jsh707["line_window_probe"]["probe_files"],
        "expected_reach_delta": {
            "jsh706_reached": ref706["reached"],
            "jsh707_reached": ref707["reached"],
        },
        "final_output_delta": {
            "jsh706_final_output_status": ref706["final_output_status"],
            "jsh707_final_output_status": ref707["final_output_status"],
        },
        "measured_explanation": _measured_explanation(labels, jsh706, jsh707),
    }


def _measured_explanation(labels: list[str], jsh706: dict[str, Any], jsh707: dict[str, Any]) -> str:
    if "opened_file_progress_displaced_before_expected_record_line" in labels:
        return (
            "JSH-706 reached and surfaced the expected record; JSH-707 opened the expected file but did not "
            "reach the expected record line. The failed run also reports midpoint probe records before final "
            "ranking, so the measured loss point is opened-file scan progress, not final truncation."
        )
    if jsh706["expected_ref"]["opened"] and not jsh706["expected_ref"]["reached"]:
        return (
            "Both compared runs opened the expected file but did not reach the expected record line. The "
            "measured loss point remains within-file progress before elapsed stop."
        )
    if not jsh706["expected_ref"]["opened"]:
        return "The expected file was not opened in the baseline evidence; this row is calibration for unopened scope."
    return "No regressed final hit is measured for this row."


def _summary(rows: list[dict[str, Any]], jsh706_benchmark: dict[str, Any], jsh707_benchmark: dict[str, Any]) -> dict[str, Any]:
    regressed = [row for row in rows if row["case_role"] == "jsh707_regression"]
    opened_misses = [row for row in rows if row["case_role"] == "canonical_opened_date_timeline_miss"]
    return {
        "jsh706_package_search_first_pass_hits": _first_pass_hits(jsh706_benchmark),
        "jsh707_failed_package_search_first_pass_hits": _first_pass_hits(jsh707_benchmark),
        "package_search_first_pass_hit_delta": _first_pass_hits(jsh707_benchmark) - _first_pass_hits(jsh706_benchmark),
        "regressed_case_count": len(regressed),
        "regressed_cases_opened_but_not_reached_in_jsh707": sum(
            1 for row in regressed if row["jsh707_failed"]["expected_ref"]["opened"] and not row["jsh707_failed"]["expected_ref"]["reached"]
        ),
        "regressed_cases_with_jsh707_midpoint_probe_records": sum(
            1 for row in regressed if row["jsh707_failed"]["line_window_probe"]["probe_records"] > 0
        ),
        "canonical_opened_date_timeline_miss_count": len(opened_misses),
        "canonical_opened_date_timeline_misses_opened_not_reached_in_both_runs": sum(
            1
            for row in opened_misses
            if row["jsh706"]["expected_ref"]["opened"]
            and not row["jsh706"]["expected_ref"]["reached"]
            and row["jsh707_failed"]["expected_ref"]["opened"]
            and not row["jsh707_failed"]["expected_ref"]["reached"]
        ),
    }


def _compared_runs(
    jsh706_benchmark: dict[str, Any],
    jsh707_benchmark: dict[str, Any],
    jsh706_audit: dict[str, Any],
    jsh707_audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "jsh706": {
            "run_id": _safe_string(jsh706_benchmark.get("run_id")),
            "miss_audit_raw_run_id": _safe_string(jsh706_audit.get("raw_run_id")),
            "package_search_first_pass_hits": _first_pass_hits(jsh706_benchmark),
            "package_search_cases": _package_cases(jsh706_benchmark),
            "package_search_recall_mrr": _package_mrr(jsh706_benchmark),
        },
        "jsh707_failed": {
            "run_id": _safe_string(jsh707_benchmark.get("run_id")),
            "miss_audit_raw_run_id": _safe_string(jsh707_audit.get("raw_run_id")),
            "package_search_first_pass_hits": _first_pass_hits(jsh707_benchmark),
            "package_search_cases": _package_cases(jsh707_benchmark),
            "package_search_recall_mrr": _package_mrr(jsh707_benchmark),
        },
    }


def _audit_view(audit_row: dict[str, Any]) -> dict[str, Any]:
    candidate = audit_row.get("candidate_list_probe") if isinstance(audit_row.get("candidate_list_probe"), dict) else {}
    scope = audit_row.get("scope_probe") if isinstance(audit_row.get("scope_probe"), dict) else {}
    cap = audit_row.get("cap_probe") if isinstance(audit_row.get("cap_probe"), dict) else {}
    ranking = audit_row.get("ranking_probe") if isinstance(audit_row.get("ranking_probe"), dict) else {}
    return {
        "scope_label": str(scope.get("not_searched_scope") or "unknown"),
        "expected_ref_recovered_any_available_list": bool(candidate.get("expected_ref_recovered_any_available_list")),
        "rank_if_reached": ranking.get("rank_against_returned_candidates"),
        "cap_hit_labels": [str(item) for item in cap.get("cap_hit_labels") or []],
        "weak_reservoir_cap": bool(cap.get("weak_reservoir_cap")),
        "failure_classifications": [str(item) for item in audit_row.get("failure_classifications") or []],
    }


def _expected_diagnostics(row: dict[str, Any], raw_result: dict[str, Any]) -> dict[str, Any]:
    diagnostics = row.get("expected_ref_diagnostics") if isinstance(row.get("expected_ref_diagnostics"), dict) else None
    if diagnostics is None:
        diagnostics = raw_result.get("expected_ref_diagnostics") if isinstance(raw_result.get("expected_ref_diagnostics"), dict) else {}
    return diagnostics or {}


def _first_ref(expected_diag: dict[str, Any]) -> dict[str, Any]:
    refs = expected_diag.get("refs") if isinstance(expected_diag.get("refs"), list) else []
    if refs and isinstance(refs[0], dict):
        return refs[0]
    return {}


def _line_window_probe(raw_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": str(raw_result.get("line_window_probe_strategy") or "not_enabled"),
        "probe_files": int(raw_result.get("line_window_probe_files") or 0),
        "probe_records": int(raw_result.get("line_window_probe_records") or 0),
        "probe_max_records_per_file": int(raw_result.get("line_window_probe_max_records") or 0),
    }


def _benchmark_row(report: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in report.get("rows", [])
            if isinstance(row, dict)
            and row.get("case_id") == case_id
            and row.get("baseline") == "package_search"
            and int(row.get("repeat_index") or 0) == 0
        ),
        None,
    )


def _audit_row(report: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    return next(
        (row for row in report.get("rows", []) if isinstance(row, dict) and row.get("case_id") == case_id),
        None,
    )


def _raw_package_row(raw_run: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    return next(
        (
            row
            for row in raw_run.get("rows", [])
            if isinstance(row, dict)
            and row.get("case_id") == case_id
            and row.get("baseline") == "package_search"
            and int(row.get("repeat_index") or 0) == 0
        ),
        None,
    )


def _case_role(case_id: str) -> str:
    if case_id in REGRESSED_CASE_IDS:
        return "jsh707_regression"
    if case_id in CANONICAL_OPENED_DATE_TIMELINE_MISSES:
        return "canonical_opened_date_timeline_miss"
    return "calibration"


def _legacy_per_file_progress_available(rows: list[dict[str, Any]]) -> bool:
    return any(
        item.get("counter_status") == "measured"
        for row in rows
        for run_key in ("jsh706", "jsh707_failed")
        for item in row[run_key].get("opened_file_progress") or []
    )


def _read_report(path_value: str | Path, key: str) -> dict[str, Any]:
    payload = json.loads(Path(path_value).expanduser().read_text(encoding="utf-8"))
    report = payload.get(key)
    if not isinstance(report, dict):
        raise ValueError(f"missing report key: {key}")
    return report


def _read_optional_raw_run(path_value: str | Path | None) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value).expanduser()
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _first_pass_hits(report: dict[str, Any]) -> int:
    return int(((report.get("summary") or {}).get("package_search") or {}).get("first_pass_hits") or 0)


def _package_cases(report: dict[str, Any]) -> int:
    return int(((report.get("summary") or {}).get("package_search") or {}).get("cases") or 0)


def _package_mrr(report: dict[str, Any]) -> float:
    return float(((report.get("summary") or {}).get("package_search") or {}).get("mean_mrr") or 0.0)


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_query_text_committed": False,
        "raw_source_lines_committed": False,
        "raw_private_paths_committed": False,
        "local_usernames_committed": False,
        "session_ids_committed": False,
        "payload_snippets_committed": False,
        "vectors_or_indexes_committed": False,
        "expected_line_numbers_committed": False,
    }
