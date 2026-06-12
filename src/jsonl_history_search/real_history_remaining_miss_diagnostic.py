from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_BENCHMARK_REPORT = Path(
    "reports/jsh-700-real-history-package-search-enumerated-ref-opening-and-retention-efficiency-repair.json"
)
DEFAULT_MISS_AUDIT_REPORT = Path(
    "reports/jsh-700-real-history-package-search-enumerated-ref-opening-and-retention-efficiency-repair-miss-audit.json"
)
REPORT_KEY = "real_history_remaining_miss_loss_point_diagnostic"
ABSENT_FINAL_REF_LABEL = "expected_ref_absent_from_final_package_candidate_lists"


def build_real_history_remaining_miss_diagnostic_payload(args) -> dict[str, Any]:
    benchmark_path = Path(args.benchmark_report).expanduser()
    miss_audit_path = Path(args.miss_audit_report).expanduser()
    instrumented_path = (
        Path(args.instrumented_miss_audit_report).expanduser()
        if getattr(args, "instrumented_miss_audit_report", None)
        else None
    )
    benchmark = _read_json(benchmark_path).get("real_history_impact_benchmark") or {}
    miss_audit = _read_json(miss_audit_path).get("real_history_package_search_miss_audit") or {}
    instrumented_audit = (
        _read_json(instrumented_path).get("real_history_package_search_miss_audit") if instrumented_path else None
    ) or {}
    instrumented_by_case = {
        str(row.get("case_id")): row
        for row in instrumented_audit.get("rows", [])
        if isinstance(row, dict) and isinstance(row.get("case_id"), str)
    }
    all_rows = [row for row in miss_audit.get("rows", []) if isinstance(row, dict)]
    rows = [row for row in all_rows if ABSENT_FINAL_REF_LABEL in set(row.get("failure_classifications") or [])]
    diagnostic_rows = [_diagnose_row(row, instrumented_by_case.get(str(row.get("case_id")))) for row in rows]
    status = "ok" if len(diagnostic_rows) == 5 else "incomplete"
    summary = _summary(diagnostic_rows)
    instrumented_used = instrumented_path is not None
    report = {
        "schema_version": 1,
        "status": status,
        "data_source": (
            "committed_jsh700_sanitized_reports_plus_instrumented_jsh704_audit"
            if instrumented_used
            else "committed_jsh700_sanitized_reports"
        ),
        "task": (
            "jsh-704-real-history-package-search-expected-record-reach-and-pre-final-retention-diagnostic"
            if instrumented_used
            else "jsh-702-real-history-package-search-remaining-miss-loss-point-diagnostic"
        ),
        "related_work": {
            "jsh_700_baseline_run": "d8354c8de9bb",
            "jsh_702_report": "remaining miss loss-point diagnostic with pre-final evidence unavailable",
            "jsh_703_decision": "no behavior repair without expected record-line reach and pre-final retention proof",
        },
        "canonical_baseline": {
            "benchmark_report_label": benchmark_path.name,
            "miss_audit_report_label": miss_audit_path.name,
            "benchmark_run_id": _safe_string(benchmark.get("run_id")),
            "miss_audit_raw_run_id": _safe_string(miss_audit.get("raw_run_id")),
            "audited_baseline": _safe_string(miss_audit.get("audited_baseline")) or "package_search",
            "case_count": int(benchmark.get("case_count") or miss_audit.get("case_count") or 0),
            "package_search_first_pass_hits": int(
                ((benchmark.get("summary") or {}).get("package_search") or {}).get("first_pass_hits") or 0
            ),
            "package_search_first_pass_cases": int(
                ((benchmark.get("summary") or {}).get("package_search") or {}).get("cases") or 0
            ),
        },
        "auxiliary_private_evidence": {
            "raw_run_label": _safe_string(instrumented_audit.get("raw_run_label")) if instrumented_used else "0f18837a90fc.json",
            "raw_run_id": _safe_string(instrumented_audit.get("raw_run_id")) if instrumented_used else None,
            "used": bool(instrumented_used),
            "instrumented_miss_audit_report_label": instrumented_path.name if instrumented_path else None,
            "reason": (
                "joined_sanitized_instrumented_evidence_by_case_id_for_canonical_jsh700_five_absent_final_refs"
                if instrumented_used
                else "committed_jsh700_reports_contain_the_required_sanitized_counter_and_scope_evidence"
            ),
        },
        "privacy_contract": _privacy_contract(),
        "behavior_boundary": {
            "default_package_search_changed": False,
            "scanner_behavior_changed": False,
            "ranking_repair_claimed": False,
            "query_expansion_repair_claimed": False,
            "date_window_hard_filter_claimed": False,
            "metadata_or_sidecar_or_embedding_claimed": False,
            "rg_prefilter_promotion_claimed": False,
            "token_efficiency_or_product_value_claimed": False,
        },
        "evidence_limits": {
            "instrumented_miss_audit_available": bool(instrumented_used),
            "instrumented_rows_joined_by_case_id": sum(
                1 for row in diagnostic_rows if row["instrumented_expected_ref_evidence"]["available"]
            ),
            "canonical_population_source": "committed_jsh700_absent_final_ref_rows",
            "pre_final_internal_candidate_list_available": bool(
                summary.get("pre_final_internal_candidate_list_available_count", 0)
            ),
            "pre_final_internal_candidate_list_reason": (
                "opt_in_jsh704_expected_ref_diagnostics"
                if instrumented_used
                else "committed_and_raw_jsh700_artifacts_do_not_store_pre_filter_or_pre_reservoir_candidate_lists"
            ),
            "expected_file_opened_does_not_prove_expected_record_line_reached": not instrumented_used,
            "opened_cases_cannot_distinguish_record_reach_stop_from_pre_final_or_final_retention_loss": not instrumented_used,
            "diagnostic_alone_justifies_behavior_repair": False,
        },
        "summary": summary,
        "decision": {
            "headline": (
                _headline(summary, instrumented_used)
            ),
            "repair_supported_by_jsh702": False,
            "repair_supported_by_jsh704": bool(summary.get("measured_later_repair_task_supported", False)),
            "reason": (
                _decision_reason(summary, instrumented_used)
            ),
            "recommended_next_step": (
                "Create a later measured repair task only for the measured loss point shown here; do not change package search from this diagnostic task."
                if summary.get("measured_later_repair_task_supported")
                else "No behavior repair is recommended from this evidence alone."
            ),
        },
        "rows": diagnostic_rows,
    }
    return {REPORT_KEY: report}


def _diagnose_row(row: dict[str, Any], instrumented_row: dict[str, Any] | None = None) -> dict[str, Any]:
    scope = row.get("scope_probe") or {}
    candidate = row.get("candidate_list_probe") or {}
    should = row.get("should_consider_probe") or {}
    ranking = row.get("ranking_probe") or {}
    scanner = row.get("scanner_diagnostics") or {}
    cap = row.get("cap_probe") or {}
    query = row.get("query_basis") or {}
    instrumented = _instrumented_expected_ref_evidence(instrumented_row)
    opened = bool(scope.get("expected_file_opened_before_scan_stop"))
    not_scope = str(scope.get("not_searched_scope") or "unknown")
    loss_bucket = _loss_bucket(not_scope, opened, instrumented)
    next_repair_candidate = _next_repair_candidate(loss_bucket, query, instrumented)
    return {
        "case_id": str(row.get("case_id") or ""),
        "family": str(row.get("family") or "unknown"),
        "path_safe_loss_point_bucket": loss_bucket,
        "expected_file_scope": {
            "selected": instrumented.get("expected_file_selected_by_source_plan")
            if instrumented.get("available")
            else bool(scope.get("expected_file_selected")),
            "queued_for_scan": instrumented.get("expected_file_queued_for_scan")
            if instrumented.get("available")
            else None,
            "enumerated_before_byte_cap": bool(scope.get("expected_file_enumerated_before_byte_cap")),
            "opened_before_scan_stop": instrumented.get("expected_file_opened_before_scan_stop")
            if instrumented.get("available")
            else opened,
            "not_searched_scope": not_scope,
        },
        "candidate_evidence": {
            "expected_ref_absent_from_final_package_candidate_lists": not bool(
                candidate.get("expected_ref_recovered_any_available_list")
            ),
            "pre_final_internal_candidates_available": bool(
                instrumented.get("pre_final_candidate_state_available")
            )
            if instrumented.get("available")
            else False,
            "final_raw_candidate_refs_checked": bool((candidate.get("final_raw_candidate_refs") or {}).get("available")),
            "final_package_result_groups_checked": bool(
                (candidate.get("final_package_result_groups") or {}).get("available")
            ),
            "sanitized_top_candidate_refs_checked": bool(
                (candidate.get("sanitized_top_candidate_refs") or {}).get("available")
            ),
        },
        "should_consider_and_rank_evidence": {
            "expected_record_local_parse_reached": bool(should.get("expected_record_reached_by_local_parser")),
            "would_pass_should_consider": bool(should.get("would_pass_should_consider")),
            "rank_if_reached": ranking.get("rank_against_returned_candidates"),
            "returned_candidate_count": int(ranking.get("returned_candidate_count") or 0),
        },
        "dedupe_evidence": {
            "candidate_records_deduped": int(scanner.get("candidate_records_deduped") or 0),
            "dedupe_measured_cause": bool(int(scanner.get("candidate_records_deduped") or 0) > 0),
        },
        "cap_and_pressure_evidence": {
            "elapsed_cap": bool(cap.get("elapsed_cap")),
            "match_cap": bool(cap.get("match_cap")),
            "weak_reservoir_cap": bool(cap.get("weak_reservoir_cap")),
            "weak_reservoir_noise_pressure": (
                "present_but_not_measured_cause" if cap.get("weak_reservoir_cap") else "not_reported"
            ),
            "cap_hit_labels": [str(item) for item in cap.get("cap_hit_labels") or []],
        },
        "query_source_planning_signal": {
            "family_is_date_timeline": str(row.get("family") or "") == "date_timeline_user_ask",
            "pathology_labels": [str(item) for item in query.get("pathologies") or []],
            "date_window_filter": query.get("date_window_filter"),
            "inferred_date_window": query.get("inferred_date_window"),
        },
        "opened_case_unknowns": {
            "expected_record_line_reached_before_elapsed_stop": instrumented.get(
                "expected_record_line_reached_before_elapsed_stop"
            )
            if instrumented.get("available")
            else (None if opened else False),
            "pre_final_retention_loss": instrumented.get("pre_final_retention_loss")
            if instrumented.get("available")
            else None,
            "final_retention_or_ref_matching_loss": instrumented.get("final_output_ref_surfacing_loss")
            if instrumented.get("available")
            else ("possible_but_unproven" if opened else "not_applicable_until_opened"),
        },
        "instrumented_expected_ref_evidence": instrumented,
        "cost_evidence": instrumented.get("cost_evidence") or _package_cost_from_row(row),
        "next_repair_candidate": next_repair_candidate,
    }


def _loss_bucket(not_scope: str, opened: bool, instrumented: dict[str, Any] | None = None) -> str:
    instrumented = instrumented or {}
    if instrumented.get("available"):
        if not instrumented.get("expected_file_opened_before_scan_stop"):
            return "not_opened_before_stop"
        if not instrumented.get("expected_record_line_reached_before_elapsed_stop"):
            return "opened_file_but_record_line_not_reached_before_elapsed_stop"
        if not instrumented.get("expected_record_present_before_reservoir_retention"):
            return "record_line_reached_but_absent_before_reservoir_retention"
        if not instrumented.get("expected_record_present_in_pre_final_candidate_state"):
            return "present_before_reservoir_but_absent_pre_final"
        if instrumented.get("final_output_ref_surfacing_loss"):
            return "pre_final_present_but_absent_from_final_output"
        if instrumented.get("expected_record_present_in_final_candidate_groups"):
            return "final_output_contains_expected_ref"
    if not_scope == "expected_file_enumerated_but_not_opened_before_scan_stop":
        return "not_opened_before_stop"
    if opened or not_scope == "expected_file_opened":
        return "opened_expected_file_record_reach_unknown"
    if not_scope == "expected_file_not_reached_due_elapsed_cap":
        return "not_reached_before_elapsed_stop"
    return "irreducible_or_no_change_from_committed_evidence"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    loss_counts = Counter(row["path_safe_loss_point_bucket"] for row in rows)
    family_counts = Counter(row["family"] for row in rows)
    query_pathologies = Counter(
        item for row in rows for item in row["query_source_planning_signal"].get("pathology_labels", [])
    )
    instrumented = [row["instrumented_expected_ref_evidence"] for row in rows]
    instrumented_available = [item for item in instrumented if item.get("available")]
    final_loss_count = sum(1 for item in instrumented_available if item.get("final_output_ref_surfacing_loss"))
    pre_final_loss_count = sum(1 for item in instrumented_available if item.get("pre_final_retention_loss"))
    next_repair_counts = Counter(row.get("next_repair_candidate", "unknown") for row in rows)
    measured_repair_supported = bool(
        instrumented_available
        and any(label != "no_repair_candidate_from_this_case" for label in next_repair_counts)
    )
    return {
        "absent_final_ref_case_count": len(rows),
        "family_counts": dict(sorted(family_counts.items())),
        "all_absent_refs_are_date_timeline_user_ask": set(family_counts) == {"date_timeline_user_ask"},
        "candidate_records_deduped_zero_count": sum(
            1 for row in rows if row["dedupe_evidence"]["candidate_records_deduped"] == 0
        ),
        "dedupe_measured_cause_count": sum(1 for row in rows if row["dedupe_evidence"]["dedupe_measured_cause"]),
        "would_pass_should_consider_count": sum(
            1 for row in rows if row["should_consider_and_rank_evidence"]["would_pass_should_consider"]
        ),
        "rank_if_reached_1_count": sum(
            1 for row in rows if row["should_consider_and_rank_evidence"]["rank_if_reached"] == 1
        ),
        "path_safe_loss_point_counts": dict(sorted(loss_counts.items())),
        "opened_expected_file_count": sum(
            1 for row in rows if row["expected_file_scope"]["opened_before_scan_stop"]
        ),
        "not_opened_before_stop_count": loss_counts.get("not_opened_before_stop", 0),
        "pre_final_internal_candidate_list_available_count": sum(
            1 for item in instrumented_available if item.get("pre_final_candidate_state_available")
        ),
        "instrumented_expected_ref_available_count": len(instrumented_available),
        "expected_file_opened_before_scan_stop_instrumented_count": sum(
            1 for item in instrumented_available if item.get("expected_file_opened_before_scan_stop")
        ),
        "expected_record_line_reached_before_elapsed_stop_count": sum(
            1 for item in instrumented_available if item.get("expected_record_line_reached_before_elapsed_stop")
        ),
        "expected_record_present_before_reservoir_retention_count": sum(
            1 for item in instrumented_available if item.get("expected_record_present_before_reservoir_retention")
        ),
        "expected_record_present_in_pre_final_candidate_state_count": sum(
            1 for item in instrumented_available if item.get("expected_record_present_in_pre_final_candidate_state")
        ),
        "expected_record_present_in_final_candidate_groups_count": sum(
            1 for item in instrumented_available if item.get("expected_record_present_in_final_candidate_groups")
        ),
        "sanitized_top_candidate_refs_present_count": sum(
            1 for item in instrumented_available if item.get("sanitized_top_candidate_refs_present")
        ),
        "pre_final_retention_loss_count": pre_final_loss_count,
        "final_output_ref_surfacing_loss_count": final_loss_count,
        "measured_later_repair_task_supported": measured_repair_supported,
        "next_repair_candidate_counts": dict(sorted(next_repair_counts.items())),
        "query_pathology_counts": dict(sorted(query_pathologies.items())),
        "elapsed_cap_count": sum(1 for row in rows if row["cap_and_pressure_evidence"]["elapsed_cap"]),
        "match_cap_count": sum(1 for row in rows if row["cap_and_pressure_evidence"]["match_cap"]),
        "weak_reservoir_cap_count": sum(
            1 for row in rows if row["cap_and_pressure_evidence"]["weak_reservoir_cap"]
        ),
    }


def _instrumented_expected_ref_evidence(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return _missing_instrumented_evidence()
    candidate = row.get("candidate_list_probe") or {}
    scanner = row.get("scanner_diagnostics") or {}
    diagnostics = scanner.get("expected_ref_diagnostics") or {}
    refs = diagnostics.get("refs") or []
    ref = refs[0] if refs and isinstance(refs[0], dict) else {}
    line = candidate.get("expected_record_line") or {}
    pre_reservoir = candidate.get("pre_reservoir_candidate_state") or {}
    pre_final = candidate.get("pre_final_internal_candidates") or {}
    final_groups = candidate.get("final_package_result_groups") or {}
    sanitized_top = candidate.get("sanitized_top_candidate_refs") or {}
    available = bool(line.get("available") or pre_reservoir.get("available") or pre_final.get("available"))
    pre_reservoir_present = _optional_bool(pre_reservoir.get("expected_ref_present"))
    pre_final_present = _optional_bool(pre_final.get("expected_ref_present"))
    final_groups_present = _optional_bool(final_groups.get("expected_ref_present"))
    sanitized_top_present = _optional_bool(sanitized_top.get("expected_ref_present"))
    line_reached = _optional_bool(line.get("reached_before_elapsed_stop"))
    return {
        "available": available,
        "source": "instrumented_miss_audit_report" if available else "instrumented_miss_audit_report_without_expected_ref_diagnostics",
        "ref_id": str(ref.get("ref_id") or ""),
        "expected_file_selected_by_source_plan": _optional_bool(ref.get("expected_file_selected_by_source_plan")),
        "expected_file_queued_for_scan": _optional_bool(ref.get("expected_file_queued_for_scan")),
        "expected_file_opened_before_scan_stop": bool(ref.get("expected_file_opened_before_scan_stop")),
        "expected_record_line_reached_before_elapsed_stop": line_reached,
        "line_reach_status": line.get("status") or ref.get("line_reach_status"),
        "pre_reservoir_candidate_state_available": bool(pre_reservoir.get("available")),
        "expected_record_present_before_reservoir_retention": pre_reservoir_present,
        "pre_reservoir_status": pre_reservoir.get("status") or ref.get("pre_reservoir_status"),
        "pre_final_candidate_state_available": bool(pre_final.get("available")),
        "expected_record_present_in_pre_final_candidate_state": pre_final_present,
        "pre_final_status": pre_final.get("status") or ref.get("pre_final_status"),
        "expected_record_present_in_final_candidate_groups": final_groups_present,
        "sanitized_top_candidate_refs_present": sanitized_top_present,
        "final_status": ref.get("final_status"),
        "pre_final_retention_loss": bool(
            pre_reservoir_present is True and pre_final_present is False
        ),
        "final_output_ref_surfacing_loss": bool(
            pre_final_present is True and not (final_groups_present is True or sanitized_top_present is True)
        ),
        "candidate_path_label": str(ref.get("candidate_strength") or "not_turned_into_candidate"),
        "kept_or_dropped_before_pre_final": str(ref.get("pre_final_status") or pre_final.get("status") or "unknown"),
        "final_kept_or_surfaced": str(ref.get("final_status") or "unknown"),
        "cost_evidence": _package_cost_from_row(row),
        "raw_paths_included": False,
        "line_numbers_included": False,
    }


def _missing_instrumented_evidence() -> dict[str, Any]:
    return {
        "available": False,
        "source": "not_provided",
        "cost_evidence": {},
        "raw_paths_included": False,
        "line_numbers_included": False,
    }


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _package_cost_from_row(row: dict[str, Any] | None) -> dict[str, int]:
    if not row:
        return {"files_opened": 0, "bytes_parsed": 0, "handoff_bytes": 0}
    cost = row.get("package_cost") if isinstance(row.get("package_cost"), dict) else {}
    sanitized = row.get("sanitized_row") if isinstance(row.get("sanitized_row"), dict) else {}
    return {
        "files_opened": int(cost.get("files_opened") or sanitized.get("files_opened") or 0),
        "bytes_parsed": int(
            cost.get("bytes_parsed")
            or sanitized.get("bytes_parsed")
            or sanitized.get("bytes_scanned")
            or 0
        ),
        "handoff_bytes": int(
            cost.get("handoff_bytes")
            or sanitized.get("output_context_bytes")
            or sanitized.get("context_bytes")
            or sanitized.get("output_bytes")
            or 0
        ),
    }


def _next_repair_candidate(loss_bucket: str, query: dict[str, Any], instrumented: dict[str, Any]) -> str:
    pathologies = set(query.get("pathologies") or query.get("pathology_labels") or [])
    if "missing_date_window_handle_for_date_timeline_case" in pathologies:
        return "narrow_date_handle_diagnostic_or_planner_repair_candidate"
    if loss_bucket == "not_opened_before_stop":
        return "source_opening_order_or_elapsed_budget_scheduling_repair_candidate"
    if loss_bucket == "opened_file_but_record_line_not_reached_before_elapsed_stop":
        return "within_file_scanning_date_aware_seek_window_or_record_quantum_repair_candidate"
    if loss_bucket in {
        "record_line_reached_but_absent_before_reservoir_retention",
        "present_before_reservoir_but_absent_pre_final",
        "pre_final_present_but_absent_from_final_output",
    }:
        return "candidate_retention_reservoir_or_final_truncation_repair_candidate"
    if instrumented.get("available"):
        return "no_repair_candidate_from_this_case"
    return "evidence_gap_requires_instrumented_replay"


def _headline(summary: dict[str, Any], instrumented_used: bool) -> str:
    if not instrumented_used:
        return (
            "The five remaining JSH-700 absent-final-ref misses are all date-timeline cases; "
            "three stopped before opening the expected file and two opened the expected file "
            "but lack enough artifact detail to prove record-line reach or retention loss."
        )
    return (
        "JSH-704 keeps the canonical five JSH-700 absent-final-ref misses and adds opt-in expected-ref "
        "reach/retention evidence: "
        f"line reached in {summary.get('expected_record_line_reached_before_elapsed_stop_count', 0)}/"
        f"{summary.get('absent_final_ref_case_count', 0)}, pre-final present in "
        f"{summary.get('expected_record_present_in_pre_final_candidate_state_count', 0)}/"
        f"{summary.get('absent_final_ref_case_count', 0)}, final-output surfacing loss in "
        f"{summary.get('final_output_ref_surfacing_loss_count', 0)}."
    )


def _decision_reason(summary: dict[str, Any], instrumented_used: bool) -> str:
    if not instrumented_used:
        return (
            "All five expected records would pass should_consider and rank first if reached, "
            "dedupe is zero for all five, but pre-final candidate lists and opened-case line reach are unavailable."
        )
    return (
        "The report is diagnostic-only: it uses JSH-700 run d8354c8de9bb as the fixed population, "
        "joins fresh opt-in evidence by case_id, and supports only a later measured repair task for the "
        "reported next-repair candidate labels."
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_query_text_committed": False,
        "raw_source_lines_committed": False,
        "raw_private_paths_committed": False,
        "local_usernames_committed": False,
        "session_ids_committed": False,
        "package_payloads_committed": False,
        "private_auxiliary_paths_committed": False,
        "vectors_or_indexes_committed": False,
    }
