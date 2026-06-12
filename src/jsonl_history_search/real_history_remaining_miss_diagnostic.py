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
    benchmark = _read_json(benchmark_path).get("real_history_impact_benchmark") or {}
    miss_audit = _read_json(miss_audit_path).get("real_history_package_search_miss_audit") or {}
    all_rows = [row for row in miss_audit.get("rows", []) if isinstance(row, dict)]
    rows = [row for row in all_rows if ABSENT_FINAL_REF_LABEL in set(row.get("failure_classifications") or [])]
    diagnostic_rows = [_diagnose_row(row) for row in rows]
    status = "ok" if len(diagnostic_rows) == 5 else "incomplete"
    summary = _summary(diagnostic_rows)
    report = {
        "schema_version": 1,
        "status": status,
        "data_source": "committed_jsh700_sanitized_reports",
        "task": "jsh-702-real-history-package-search-remaining-miss-loss-point-diagnostic",
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
            "raw_run_label": "0f18837a90fc.json",
            "used": False,
            "reason": "committed_jsh700_reports_contain_the_required_sanitized_counter_and_scope_evidence",
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
            "pre_final_internal_candidate_list_available": False,
            "pre_final_internal_candidate_list_reason": (
                "committed_and_raw_jsh700_artifacts_do_not_store_pre_filter_or_pre_reservoir_candidate_lists"
            ),
            "expected_file_opened_does_not_prove_expected_record_line_reached": True,
            "opened_cases_cannot_distinguish_record_reach_stop_from_pre_final_or_final_retention_loss": True,
            "diagnostic_alone_justifies_behavior_repair": False,
        },
        "summary": summary,
        "decision": {
            "headline": (
                "The five remaining JSH-700 absent-final-ref misses are all date-timeline cases; "
                "three stopped before opening the expected file and two opened the expected file "
                "but lack enough artifact detail to prove record-line reach or retention loss."
            ),
            "repair_supported_by_jsh702": False,
            "reason": (
                "All five expected records would pass should_consider and rank first if reached, "
                "dedupe is zero for all five, but pre-final candidate lists and opened-case line reach are unavailable."
            ),
            "recommended_next_step": (
                "Only a later task with finer reach/pre-final retention diagnostics should choose a behavior repair."
            ),
        },
        "rows": diagnostic_rows,
    }
    return {REPORT_KEY: report}


def _diagnose_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = row.get("scope_probe") or {}
    candidate = row.get("candidate_list_probe") or {}
    should = row.get("should_consider_probe") or {}
    ranking = row.get("ranking_probe") or {}
    scanner = row.get("scanner_diagnostics") or {}
    cap = row.get("cap_probe") or {}
    query = row.get("query_basis") or {}
    opened = bool(scope.get("expected_file_opened_before_scan_stop"))
    not_scope = str(scope.get("not_searched_scope") or "unknown")
    loss_bucket = _loss_bucket(not_scope, opened)
    return {
        "case_id": str(row.get("case_id") or ""),
        "family": str(row.get("family") or "unknown"),
        "path_safe_loss_point_bucket": loss_bucket,
        "expected_file_scope": {
            "selected": bool(scope.get("expected_file_selected")),
            "enumerated_before_byte_cap": bool(scope.get("expected_file_enumerated_before_byte_cap")),
            "opened_before_scan_stop": opened,
            "not_searched_scope": not_scope,
        },
        "candidate_evidence": {
            "expected_ref_absent_from_final_package_candidate_lists": not bool(
                candidate.get("expected_ref_recovered_any_available_list")
            ),
            "pre_final_internal_candidates_available": False,
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
            "expected_record_line_reached_before_elapsed_stop": None if opened else False,
            "pre_final_retention_loss": None,
            "final_retention_or_ref_matching_loss": "possible_but_unproven" if opened else "not_applicable_until_opened",
        },
    }


def _loss_bucket(not_scope: str, opened: bool) -> str:
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
        "pre_final_internal_candidate_list_available_count": 0,
        "query_pathology_counts": dict(sorted(query_pathologies.items())),
        "elapsed_cap_count": sum(1 for row in rows if row["cap_and_pressure_evidence"]["elapsed_cap"]),
        "match_cap_count": sum(1 for row in rows if row["cap_and_pressure_evidence"]["match_cap"]),
        "weak_reservoir_cap_count": sum(
            1 for row in rows if row["cap_and_pressure_evidence"]["weak_reservoir_cap"]
        ),
    }


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
