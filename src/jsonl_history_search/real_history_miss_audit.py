from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .conversation_records import HUMAN_AUTHORED_USER_MESSAGE, classify_conversation_record
from .query import QueryIntent, infer_query
from .ranking import score_record, should_consider
from .records import Record, parse_record
from .timeutil import parse_now, timezone_from_args


DEFAULT_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")
DEFAULT_PRIVATE_BRIDGE = Path(".private/jsh-049/bridge.json")
DEFAULT_RAW_RUN = Path(".private/jsh-049/raw-runs/f1e55694c6cb.json")
REPORT_KEY = "real_history_package_search_miss_audit"
PROVENANCE_HANDLE_TERMS = {
    "artifact",
    "commit",
    "handoff",
    "provenance",
    "report",
    "review",
    "reviewed",
    "task",
}


@dataclass(frozen=True)
class ExpectedRef:
    ref_id: str
    path: Path
    line: int
    query_text: str


@dataclass(frozen=True)
class AuditCase:
    case_id: str
    family: str
    difficulty: str
    expected_outcome: str
    stage_hits: tuple[str, ...]
    timestamp_applicable: bool
    must_find_ref_ids: tuple[str, ...]
    refs: tuple[ExpectedRef, ...]


def build_real_history_package_miss_audit_payload(args) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser()
    bridge_path = Path(args.private_bridge).expanduser()
    raw_run_path = Path(args.raw_run).expanduser()
    default_tz = timezone_from_args(getattr(args, "timezone", None), getattr(args, "timezone_offset", "+00:00"))
    now = parse_now(getattr(args, "now", None), default_tz)
    cwd = str(Path(getattr(args, "cwd", ".")).expanduser().resolve())

    manifest = _read_json(manifest_path)
    bridge = _read_json(bridge_path)
    raw_run = _read_json(raw_run_path)
    cases = _case_bundle(manifest, bridge)
    raw_rows = [row for row in raw_run.get("rows", []) if isinstance(row, dict)]
    package_rows = _package_rows_by_case_id(raw_rows)
    source_order = _source_order(cases)
    enumerated_count = _infer_enumerated_file_count(source_order, raw_rows)

    rows = [
        _audit_case(
            case=case,
            package_row=package_rows.get(case.case_id),
            source_order=source_order,
            enumerated_count=enumerated_count,
            default_tz=default_tz,
            now=now,
            cwd=cwd,
        )
        for case in cases
    ]
    status = "ok" if len(rows) == len(cases) and all(row["failure_classifications"] for row in rows) else "incomplete"
    report = {
        "schema_version": 1,
        "status": status,
        "data_source": "private_real_history_sanitized_miss_audit",
        "case_count": len(rows),
        "manifest_case_count": len(manifest.get("cases", []) if isinstance(manifest.get("cases"), list) else []),
        "raw_run_label": raw_run_path.name,
        "raw_run_id": _safe_string(raw_run.get("run_id")),
        "audited_baseline": "package_search",
        "privacy_contract": _privacy_contract(),
        "evidence_limits": {
            "pre_reservoir_candidate_state_available": _expected_ref_diagnostics_available(rows),
            "pre_final_internal_candidate_list_available": _expected_ref_diagnostics_available(rows),
            "pre_final_internal_candidate_list_reason": (
                "available_only_when_raw_run_contains_opt_in_expected_ref_diagnostics"
            ),
            "final_raw_candidate_refs_checked": True,
            "final_package_result_groups_checked": True,
            "sanitized_top_candidate_refs_checked": True,
            "sanitized_scanner_dedupe_and_reach_counters_checked": True,
        },
        "source_scope": {
            "selected_expected_file_count": len(source_order),
            "inferred_enumerated_expected_file_count": enumerated_count,
            "source_order_basis": "sorted_private_bridge_expected_paths_local_only",
            "labels_only": True,
        },
        "cap_impact": _cap_impact(rows),
        "query_basis_summary": _query_basis_summary(rows),
        "failure_classification_counts": _classification_counts(rows),
        "rows": rows,
    }
    return {REPORT_KEY: report}


def _audit_case(
    *,
    case: AuditCase,
    package_row: dict[str, Any] | None,
    source_order: dict[Path, dict[str, int]],
    enumerated_count: int,
    default_tz,
    now,
    cwd: str,
) -> dict[str, Any]:
    expected_ref = case.refs[0] if case.refs else None
    source_probe = _probe_expected_source(expected_ref, default_tz=default_tz) if expected_ref else _missing_source_probe()
    intent = infer_query(expected_ref.query_text if expected_ref else "", now=now)
    query_basis = _query_basis(intent, expected_ref.query_text if expected_ref else "", case=case)
    ranking_probe = _ranking_probe(
        record=source_probe.get("_record"),
        intent=intent,
        package_row=package_row,
        expected_ref=expected_ref,
        cwd=cwd,
    )
    candidate_probe = _candidate_probe(package_row, expected_ref, case.must_find_ref_ids)
    cap_probe = _cap_probe(package_row)
    scanner_diagnostics = _scanner_diagnostics_probe(package_row)
    scope_probe = _scope_probe(
        expected_ref=expected_ref,
        source_order=source_order,
        enumerated_count=enumerated_count,
        package_row=package_row,
        cap_probe=cap_probe,
    )
    classifications = _failure_classifications(
        source_probe=source_probe,
        query_basis=query_basis,
        ranking_probe=ranking_probe,
        candidate_probe=candidate_probe,
        scope_probe=scope_probe,
        cap_probe=cap_probe,
        scanner_diagnostics=scanner_diagnostics,
        package_row=package_row,
    )
    return {
        "case_id": case.case_id,
        "family": case.family,
        "difficulty": case.difficulty,
        "expected_outcome": case.expected_outcome,
        "stage_hits": list(case.stage_hits),
        "timestamp_applicable": case.timestamp_applicable,
        "expected_ref_count": len(case.refs),
        "package_row_found": package_row is not None,
        "package_candidate_count": int(((package_row or {}).get("sanitized_row") or {}).get("candidate_count", 0)),
        "package_top_expected_ref_ids": list(((package_row or {}).get("sanitized_row") or {}).get("top_candidate_ref_ids") or []),
        "source_probe": _public_source_probe(source_probe),
        "scope_probe": scope_probe,
        "candidate_list_probe": candidate_probe,
        "should_consider_probe": ranking_probe["should_consider_probe"],
        "ranking_probe": ranking_probe["ranking_probe"],
        "cap_probe": cap_probe,
        "scanner_diagnostics": scanner_diagnostics,
        "package_cost": _package_cost_probe(package_row),
        "query_basis": query_basis,
        "failure_classifications": classifications,
    }


def _probe_expected_source(expected_ref: ExpectedRef | None, *, default_tz) -> dict[str, Any]:
    if expected_ref is None:
        return _missing_source_probe()
    line_text = _read_one_based_line(expected_ref.path, expected_ref.line)
    if line_text is None:
        return {
            "expected_file_exists": expected_ref.path.exists(),
            "expected_line_exists": False,
            "json_parse_ok": False,
            "record_text_present": False,
            "record_role_label": None,
            "record_timestamp_present": False,
            "record_cwd_present": False,
            "conversation_classification": {"label": "unavailable", "confidence": "none", "human_authored_high_confidence": False},
            "_record": None,
        }
    try:
        json.loads(line_text)
        json_parse_ok = True
    except json.JSONDecodeError:
        json_parse_ok = False
    record = parse_record(line_text, path=expected_ref.path, line=expected_ref.line, default_tz=default_tz)
    classification = classify_conversation_record(record)
    return {
        "expected_file_exists": True,
        "expected_line_exists": True,
        "json_parse_ok": json_parse_ok,
        "record_text_present": bool(record.text),
        "record_role_label": _role_label(record.role),
        "record_timestamp_present": record.timestamp is not None,
        "record_cwd_present": bool(record.cwd),
        "conversation_classification": {
            "label": classification.label,
            "confidence": classification.confidence,
            "human_authored_high_confidence": bool(
                classification.label == HUMAN_AUTHORED_USER_MESSAGE and classification.confidence == "high"
            ),
        },
        "_record": record,
    }


def _ranking_probe(
    *,
    record: Record | None,
    intent: QueryIntent,
    package_row: dict[str, Any] | None,
    expected_ref: ExpectedRef | None,
    cwd: str,
) -> dict[str, Any]:
    if record is None or expected_ref is None:
        return {
            "should_consider_probe": {
                "expected_record_reached_by_local_parser": False,
                "would_pass_should_consider": False,
                "rejection_basis": "expected_record_unavailable",
            },
            "ranking_probe": {
                "score_available": False,
                "expected_score": None,
                "reason_label_count": 0,
                "rank_against_returned_candidates": None,
                "returned_candidate_count": _returned_candidate_count(package_row),
            },
        }
    would_consider = should_consider(record, intent)
    rejection_basis = None if would_consider else _should_consider_rejection_basis(record, intent)
    expected_score = None
    reason_count = 0
    rank = None
    returned_count = _returned_candidate_count(package_row)
    if would_consider:
        expected_score, reasons, _artifacts = score_record(record, intent, cwd)
        reason_count = len(reasons)
        rank = _rank_against_returned_candidates(
            package_row=package_row,
            expected_ref=expected_ref,
            record=record,
            expected_score=expected_score,
        )
    return {
        "should_consider_probe": {
            "expected_record_reached_by_local_parser": True,
            "would_pass_should_consider": bool(would_consider),
            "rejection_basis": rejection_basis,
        },
        "ranking_probe": {
            "score_available": expected_score is not None,
            "expected_score": round(expected_score, 3) if expected_score is not None else None,
            "reason_label_count": reason_count,
            "rank_against_returned_candidates": rank,
            "returned_candidate_count": returned_count,
        },
    }


def _candidate_probe(
    package_row: dict[str, Any] | None,
    expected_ref: ExpectedRef | None,
    must_find_ref_ids: tuple[str, ...],
) -> dict[str, Any]:
    expected_diagnostic = _expected_ref_diagnostic_for(package_row, expected_ref)
    if package_row is None or expected_ref is None:
        return {
            "expected_record_line": {"available": False, "reached_before_elapsed_stop": None},
            "pre_reservoir_candidate_state": {"available": False, "expected_ref_present": None},
            "pre_final_internal_candidates": {"available": False, "expected_ref_present": None},
            "final_raw_candidate_refs": {"available": False, "expected_ref_present": None},
            "final_package_result_groups": {"available": False, "expected_ref_present": None},
            "sanitized_top_candidate_refs": {"available": False, "expected_ref_present": None},
            "expected_ref_recovered_any_available_list": False,
            "must_find_ref_count": len(must_find_ref_ids),
        }
    final_raw = _candidate_refs_contain(package_row.get("candidate_refs"), expected_ref)
    final_groups = _package_groups_contain(package_row.get("package_result"), expected_ref)
    sanitized_ids = (((package_row.get("sanitized_row") or {}).get("top_candidate_ref_ids")) or [])
    sanitized = expected_ref.ref_id in sanitized_ids
    diagnostic_available = expected_diagnostic is not None
    return {
        "expected_record_line": {
            "available": diagnostic_available,
            "reached_before_elapsed_stop": (
                bool(expected_diagnostic.get("expected_record_line_reached_before_elapsed_stop"))
                if expected_diagnostic
                else None
            ),
            "status": expected_diagnostic.get("line_reach_status") if expected_diagnostic else None,
        },
        "pre_reservoir_candidate_state": {
            "available": diagnostic_available,
            "expected_ref_present": (
                bool(expected_diagnostic.get("expected_record_present_before_reservoir_retention"))
                if expected_diagnostic
                else None
            ),
            "status": expected_diagnostic.get("pre_reservoir_status") if expected_diagnostic else None,
        },
        "pre_final_internal_candidates": {
            "available": diagnostic_available,
            "expected_ref_present": (
                bool(expected_diagnostic.get("expected_record_present_in_pre_final_candidate_state"))
                if expected_diagnostic
                else None
            ),
            "status": expected_diagnostic.get("pre_final_status") if expected_diagnostic else None,
            "reason": None
            if diagnostic_available
            else "raw_run_does_not_store_pre_filter_or_pre_reservoir_package_candidate_lists",
        },
        "final_raw_candidate_refs": {"available": True, "expected_ref_present": bool(final_raw)},
        "final_package_result_groups": {"available": True, "expected_ref_present": bool(final_groups)},
        "sanitized_top_candidate_refs": {"available": True, "expected_ref_present": bool(sanitized)},
        "expected_ref_recovered_any_available_list": bool(
            final_raw
            or final_groups
            or sanitized
            or (expected_diagnostic or {}).get("expected_record_present_in_final_candidate_refs")
            or (expected_diagnostic or {}).get("expected_record_present_in_final_candidate_groups")
        ),
        "must_find_ref_count": len(must_find_ref_ids),
    }


def _scope_probe(
    *,
    expected_ref: ExpectedRef | None,
    source_order: dict[Path, dict[str, int]],
    enumerated_count: int,
    package_row: dict[str, Any] | None,
    cap_probe: dict[str, Any],
) -> dict[str, Any]:
    if expected_ref is None:
        return {
            "expected_file_selected": False,
            "expected_file_order": None,
            "expected_file_enumerated_before_byte_cap": False,
            "expected_file_opened_before_scan_stop": False,
            "not_searched_scope": "missing_expected_ref",
        }
    order = source_order.get(expected_ref.path)
    file_order = order["order"] if order else None
    selected = order is not None
    opened_count = int(((package_row or {}).get("sanitized_row") or {}).get("files_opened", 0))
    enumerated = bool(selected and file_order is not None and file_order <= enumerated_count)
    opened = bool(enumerated and file_order is not None and file_order <= opened_count)
    if not selected:
        not_scope = "expected_file_not_selected"
    elif not enumerated:
        if cap_probe.get("enumeration_byte_cap"):
            not_scope = "expected_file_not_enumerated_due_byte_cap"
        elif cap_probe.get("elapsed_cap"):
            not_scope = "expected_file_not_reached_due_elapsed_cap"
        else:
            not_scope = "expected_file_not_enumerated_in_selected_scope"
    elif not opened:
        not_scope = "expected_file_enumerated_but_not_opened_before_scan_stop"
    else:
        not_scope = "expected_file_opened"
    return {
        "expected_file_selected": selected,
        "expected_file_order": file_order,
        "inferred_enumerated_expected_file_count": enumerated_count,
        "package_files_opened": opened_count,
        "expected_file_enumerated_before_byte_cap": enumerated,
        "expected_file_opened_before_scan_stop": opened,
        "not_searched_scope": not_scope,
    }


def _cap_probe(package_row: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = (package_row or {}).get("sanitized_row") or {}
    labels = set(sanitized.get("cap_hit_labels") or [])
    warning_labels = _warning_labels(((package_row or {}).get("package_result") or {}).get("warnings") or sanitized.get("warnings") or [])
    return {
        "enumeration_byte_cap": bool("max_byte" in labels or "enumeration_byte_cap" in warning_labels),
        "elapsed_cap": bool("max_elapsed" in labels or "elapsed_cap" in warning_labels),
        "match_cap": bool("max_match" in labels or "match_cap" in warning_labels),
        "weak_reservoir_cap": bool("weak_reservoir_cap" in warning_labels),
        "cap_hit_labels": sorted(labels),
        "warning_labels": sorted(warning_labels),
    }


def _scanner_diagnostics_probe(package_row: dict[str, Any] | None) -> dict[str, Any]:
    result = (package_row or {}).get("package_result") or {}
    sanitized = (package_row or {}).get("sanitized_row") or {}
    scan_order = sanitized.get("scan_order") if isinstance(sanitized.get("scan_order"), dict) else {}
    return {
        "records_read": int(result.get("records_read") or scan_order.get("records_read") or 0),
        "records_rejected_by_should_consider": int(
            result.get("records_rejected_by_should_consider")
            or scan_order.get("records_rejected_by_should_consider")
            or 0
        ),
        "candidate_records_scored": int(result.get("candidate_records_scored") or scan_order.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(
            result.get("candidate_records_deduped") or scan_order.get("candidate_records_deduped") or 0
        ),
        "dedupe_strategy": str(result.get("dedupe_strategy") or scan_order.get("dedupe_strategy") or "unknown"),
        "expected_ref_diagnostics": _public_expected_ref_diagnostics(result.get("expected_ref_diagnostics")),
        "raw_paths_included": False,
    }


def _query_basis(intent: QueryIntent, query: str, *, case: AuditCase) -> dict[str, Any]:
    exact_terms = [term for term, reasons in intent.expansion_reasons.items() if "exact_phrase" in reasons]
    task_slug_terms = [term for term, reasons in intent.expansion_reasons.items() if "task_slug" in reasons]
    synonym_terms = [term for term, reasons in intent.expansion_reasons.items() if "synonym_paraphrase" in reasons]
    semantic_terms = [term for term, reasons in intent.expansion_reasons.items() if "semantic_local" in reasons]
    provenance_terms = [term for term in intent.terms if term in PROVENANCE_HANDLE_TERMS]
    pathologies = []
    if len(query) > 1000:
        pathologies.append("very_long_private_query_wording")
    if len(intent.base_terms) > 80:
        pathologies.append("large_base_term_set")
    if len(intent.terms) > 120:
        pathologies.append("large_expanded_term_set")
    if exact_terms and max(len(term) for term in exact_terms) > 300:
        pathologies.append("very_long_exact_phrase_term")
    if len(synonym_terms) + len(semantic_terms) > 20:
        pathologies.append("generic_or_semantic_expansion_noise")
    if "date_timeline" in case.family and intent.date_reason is None:
        pathologies.append("missing_date_window_handle_for_date_timeline_case")
    if _expects_task_or_artifact_handle(case) and not intent.artifact_hints and not task_slug_terms and not provenance_terms:
        pathologies.append("missing_artifact_task_or_date_handle")
    return {
        "private_query_char_count": len(query),
        "private_query_word_count": len(query.split()),
        "private_query_line_count": query.count("\n") + 1 if query else 0,
        "base_term_count": len(intent.base_terms),
        "expanded_term_count": len(intent.terms),
        "exact_phrase_term_count": len(exact_terms),
        "longest_exact_phrase_chars": max((len(term) for term in exact_terms), default=0),
        "artifact_hint_count": len(intent.artifact_hints),
        "task_slug_term_count": len(task_slug_terms),
        "provenance_term_count": len(provenance_terms),
        "synonym_or_semantic_term_count": len(synonym_terms) + len(semantic_terms),
        "inferred_role": intent.role,
        "role_filter_explicit": intent.role_filter_explicit,
        "inferred_date_window": intent.date_reason,
        "date_window_filter": intent.date_window_filter,
        "pathologies": sorted(set(pathologies)),
    }


def _failure_classifications(
    *,
    source_probe: dict[str, Any],
    query_basis: dict[str, Any],
    ranking_probe: dict[str, Any],
    candidate_probe: dict[str, Any],
    scope_probe: dict[str, Any],
    cap_probe: dict[str, Any],
    scanner_diagnostics: dict[str, Any],
    package_row: dict[str, Any] | None,
) -> list[str]:
    labels: list[str] = []
    if package_row is None:
        labels.append("package_search_raw_row_missing")
    if not source_probe.get("expected_file_exists"):
        labels.append("expected_file_unresolvable")
    elif not source_probe.get("expected_line_exists"):
        labels.append("expected_line_missing")
    if not source_probe.get("json_parse_ok"):
        labels.append("expected_record_json_parse_failed")
    if not source_probe.get("record_text_present"):
        labels.append("expected_record_text_not_extracted")
    classification = source_probe.get("conversation_classification") or {}
    if not classification.get("human_authored_high_confidence"):
        labels.append("expected_record_source_semantics_not_human_high_confidence")
    if scope_probe.get("not_searched_scope") == "expected_file_not_enumerated_due_byte_cap":
        labels.append("expected_file_not_enumerated_due_byte_cap")
    if scope_probe.get("not_searched_scope") == "expected_file_not_reached_due_elapsed_cap":
        labels.append("expected_file_not_reached_due_elapsed_cap")
    if scope_probe.get("not_searched_scope") == "expected_file_not_enumerated_in_selected_scope":
        labels.append("expected_file_not_enumerated_in_selected_scope")
    if scope_probe.get("not_searched_scope") == "expected_file_enumerated_but_not_opened_before_scan_stop":
        labels.append("expected_file_not_opened_before_scan_stop")
    if not candidate_probe.get("expected_ref_recovered_any_available_list"):
        labels.append("expected_ref_absent_from_final_package_candidate_lists")
        if scanner_diagnostics.get("candidate_records_deduped"):
            labels.append("candidate_dedupe_seen_before_final_output")
    expected_line = candidate_probe.get("expected_record_line") or {}
    pre_reservoir = candidate_probe.get("pre_reservoir_candidate_state") or {}
    pre_final = candidate_probe.get("pre_final_internal_candidates") or {}
    if expected_line.get("available") and expected_line.get("reached_before_elapsed_stop") is False:
        labels.append("expected_record_line_not_reached_before_elapsed_stop")
    if pre_reservoir.get("available") and pre_reservoir.get("expected_ref_present") is False:
        labels.append("expected_record_absent_before_reservoir_retention")
    if pre_final.get("available") and pre_final.get("expected_ref_present") is False:
        labels.append("expected_record_absent_from_pre_final_candidate_state")
    if (
        pre_final.get("available")
        and pre_final.get("expected_ref_present") is True
        and (candidate_probe.get("final_package_result_groups") or {}).get("expected_ref_present") is False
    ):
        labels.append("expected_record_lost_between_pre_final_and_final_output")
    if (candidate_probe.get("sanitized_top_candidate_refs") or {}).get("expected_ref_present") is False:
        labels.append("output_filtering_or_ref_matching_did_not_surface_expected_ref")
    should_probe = ranking_probe["should_consider_probe"]
    if should_probe.get("would_pass_should_consider") is False:
        labels.append("expected_record_rejected_by_should_consider")
        basis = should_probe.get("rejection_basis")
        if basis:
            labels.append(f"query_basis_{basis}")
    rank_probe = ranking_probe["ranking_probe"]
    rank = rank_probe.get("rank_against_returned_candidates")
    if isinstance(rank, int) and rank > 10:
        labels.append("expected_record_would_rank_below_returned_candidates")
    if cap_probe.get("enumeration_byte_cap"):
        labels.append("enumeration_byte_cap_limited_scope")
    if cap_probe.get("elapsed_cap"):
        labels.append("elapsed_cap_stopped_scan")
    if cap_probe.get("match_cap"):
        labels.append("match_cap_limited_candidate_retention")
    if cap_probe.get("weak_reservoir_cap"):
        labels.append("weak_reservoir_cap_limited_noisy_candidates")
    labels.extend(f"query_basis_{item}" for item in query_basis.get("pathologies", []))
    return sorted(set(labels))


def _case_bundle(manifest: dict[str, Any], bridge: dict[str, Any]) -> list[AuditCase]:
    manifest_by_id = {
        case.get("case_id"): case
        for case in manifest.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    cases: list[AuditCase] = []
    for bridge_case in bridge.get("cases", []):
        if not isinstance(bridge_case, dict):
            continue
        case_id = bridge_case.get("case_id")
        manifest_case = manifest_by_id.get(case_id)
        if not manifest_case:
            continue
        refs = []
        for ref in bridge_case.get("refs", []):
            if not isinstance(ref, dict):
                continue
            if not (isinstance(ref.get("ref_id"), str) and isinstance(ref.get("path"), str) and isinstance(ref.get("line"), int)):
                continue
            refs.append(
                ExpectedRef(
                    ref_id=ref["ref_id"],
                    path=Path(ref["path"]),
                    line=int(ref["line"]),
                    query_text=str(ref.get("query_text") or bridge_case.get("query_text") or ""),
                )
            )
        cases.append(
            AuditCase(
                case_id=str(case_id),
                family=str(manifest_case.get("family") or "unknown"),
                difficulty=str(manifest_case.get("difficulty") or "unknown"),
                expected_outcome=str(manifest_case.get("expected_outcome") or "evidence_found"),
                stage_hits=tuple(str(item) for item in manifest_case.get("stage_hits", []) if isinstance(item, str)),
                timestamp_applicable=bool((manifest_case.get("downstream_applicability") or {}).get("timestamp_seek")),
                must_find_ref_ids=tuple(
                    ref.get("ref_id")
                    for ref in manifest_case.get("must_find_refs", [])
                    if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
                ),
                refs=tuple(refs),
            )
        )
    return sorted(cases, key=lambda item: item.case_id)


def _package_rows_by_case_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    package_rows = [
        row
        for row in rows
        if row.get("baseline") == "package_search" and int(row.get("repeat_index", 0) or 0) == 0
    ]
    return {str(row.get("case_id")): row for row in package_rows if isinstance(row.get("case_id"), str)}


def _source_order(cases: list[AuditCase]) -> dict[Path, dict[str, int]]:
    paths = sorted({ref.path for case in cases for ref in case.refs}, key=str)
    order: dict[Path, dict[str, int]] = {}
    cumulative = 0
    for index, path in enumerate(paths, start=1):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        cumulative += size
        order[path] = {"order": index, "size": size, "cumulative_bytes": cumulative}
    return order


def _infer_enumerated_file_count(source_order: dict[Path, dict[str, int]], raw_rows: list[dict[str, Any]]) -> int:
    max_scanned = max(
        (int((row.get("sanitized_row") or {}).get("bytes_scanned") or 0) for row in raw_rows),
        default=0,
    )
    if not source_order:
        return 0
    count = 0
    for item in sorted(source_order.values(), key=lambda value: value["order"]):
        if item["cumulative_bytes"] <= max_scanned:
            count = item["order"]
    if count == 0 and max_scanned > 0:
        return 1
    return count


def _candidate_refs_contain(candidate_refs: Any, expected_ref: ExpectedRef) -> bool:
    if not isinstance(candidate_refs, list):
        return False
    expected_path = str(expected_ref.path)
    for candidate in candidate_refs:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("path") == expected_path and candidate.get("line") == expected_ref.line:
            return True
    return False


def _package_groups_contain(package_result: Any, expected_ref: ExpectedRef) -> bool:
    return any(_match_is_ref(match, expected_ref) for match in _iter_package_matches(package_result))


def _iter_package_matches(package_result: Any) -> list[dict[str, Any]]:
    if not isinstance(package_result, dict):
        return []
    matches: list[dict[str, Any]] = []
    for group in package_result.get("groups", []):
        if not isinstance(group, dict):
            continue
        for match in group.get("matches", []):
            if isinstance(match, dict):
                matches.append(match)
    return matches


def _match_is_ref(match: dict[str, Any], expected_ref: ExpectedRef) -> bool:
    return match.get("path") == str(expected_ref.path) and match.get("line") == expected_ref.line


def _expected_ref_diagnostic_for(package_row: dict[str, Any] | None, expected_ref: ExpectedRef | None) -> dict[str, Any] | None:
    if package_row is None or expected_ref is None:
        return None
    candidates = [
        (((package_row.get("package_result") or {}).get("expected_ref_diagnostics") or {}).get("refs") or []),
        (((package_row.get("sanitized_row") or {}).get("expected_ref_diagnostics") or {}).get("refs") or []),
    ]
    for refs in candidates:
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict) and ref.get("ref_id") == expected_ref.ref_id:
                return ref
    return None


def _public_expected_ref_diagnostics(diagnostics: Any) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {"available": False, "raw_paths_included": False, "line_numbers_included": False, "refs": []}
    refs = []
    for ref in diagnostics.get("refs") or []:
        if not isinstance(ref, dict):
            continue
        refs.append(
            {
                "ref_id": str(ref.get("ref_id") or ""),
                "expected_file_label": ref.get("expected_file_label"),
                "expected_file_open_order": ref.get("expected_file_open_order"),
                "expected_file_records_read_before_stop": int(ref.get("expected_file_records_read_before_stop") or 0),
                "expected_file_progress_bucket": str(ref.get("expected_file_progress_bucket") or "unknown"),
                "expected_file_selected_by_source_plan": bool(ref.get("expected_file_selected_by_source_plan")),
                "expected_file_queued_for_scan": bool(ref.get("expected_file_queued_for_scan")),
                "expected_file_opened_before_scan_stop": bool(ref.get("expected_file_opened_before_scan_stop")),
                "expected_record_line_reached_before_elapsed_stop": bool(
                    ref.get("expected_record_line_reached_before_elapsed_stop")
                ),
                "line_reach_status": str(ref.get("line_reach_status") or "unknown"),
                "expected_record_present_before_reservoir_retention": bool(
                    ref.get("expected_record_present_before_reservoir_retention")
                ),
                "expected_record_present_in_pre_final_candidate_state": bool(
                    ref.get("expected_record_present_in_pre_final_candidate_state")
                ),
                "expected_record_present_in_final_candidate_refs": bool(
                    ref.get("expected_record_present_in_final_candidate_refs")
                ),
                "expected_record_present_in_final_candidate_groups": bool(
                    ref.get("expected_record_present_in_final_candidate_groups")
                ),
                "candidate_strength": ref.get("candidate_strength"),
                "should_consider_phase_label": str(ref.get("should_consider_phase_label") or "unknown"),
                "dedupe_phase_label": str(ref.get("dedupe_phase_label") or "unknown"),
                "reservoir_phase_label": str(ref.get("reservoir_phase_label") or "unknown"),
                "final_cap_phase_label": str(ref.get("final_cap_phase_label") or "unknown"),
                "drop_phase_label": ref.get("drop_phase_label"),
                "pre_reservoir_status": str(ref.get("pre_reservoir_status") or "unknown"),
                "pre_final_status": str(ref.get("pre_final_status") or "unknown"),
                "final_status": str(ref.get("final_status") or "unknown"),
            }
        )
    opened_files = []
    for item in diagnostics.get("opened_files") or []:
        if not isinstance(item, dict):
            continue
        opened_files.append(
            {
                "file_label": str(item.get("file_label") or ""),
                "opened_order": int(item.get("opened_order") or 0),
                "records_read": int(item.get("records_read") or 0),
                "records_read_bucket": str(item.get("records_read_bucket") or "unknown"),
                "expected_ref_file": bool(item.get("expected_ref_file")),
            }
        )
    return {
        "available": bool(diagnostics.get("enabled")),
        "raw_paths_included": False,
        "line_numbers_included": False,
        "ref_count": int(diagnostics.get("ref_count") or len(refs)),
        "opened_file_count": int(diagnostics.get("opened_file_count") or len(opened_files)),
        "opened_files": opened_files,
        "line_reached_count": int(diagnostics.get("line_reached_count") or 0),
        "pre_reservoir_present_count": int(diagnostics.get("pre_reservoir_present_count") or 0),
        "pre_final_present_count": int(diagnostics.get("pre_final_present_count") or 0),
        "final_candidate_ref_present_count": int(diagnostics.get("final_candidate_ref_present_count") or 0),
        "final_candidate_group_present_count": int(diagnostics.get("final_candidate_group_present_count") or 0),
        "refs": refs,
    }


def _returned_candidate_count(package_row: dict[str, Any] | None) -> int:
    return len(_iter_package_matches((package_row or {}).get("package_result")))


def _rank_against_returned_candidates(
    *,
    package_row: dict[str, Any] | None,
    expected_ref: ExpectedRef,
    record: Record,
    expected_score: float,
) -> int | None:
    matches = _iter_package_matches((package_row or {}).get("package_result"))
    if not matches:
        return 1
    expected_key = (
        float(expected_score),
        record.timestamp.isoformat() if record.timestamp else "",
        str(expected_ref.path),
        -expected_ref.line,
    )
    rank = 1
    for match in matches:
        try:
            score = float(match.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        key = (score, str(match.get("timestamp") or ""), str(match.get("path") or ""), -int(match.get("line") or 0))
        if key > expected_key:
            rank += 1
    return rank


def _should_consider_rejection_basis(record: Record, intent: QueryIntent) -> str:
    if intent.role and intent.role_filter_explicit and not _role_matches_for_audit(record.role, intent.role):
        return "role_filter_rejected_expected_record"
    if intent.date_window_filter and intent.date_window_start and intent.date_window_end:
        if record.timestamp is None:
            return "date_window_rejected_missing_timestamp"
        local_ts = record.timestamp.astimezone(intent.date_window_start.tzinfo)
        if not (intent.date_window_start <= local_ts < intent.date_window_end):
            return "date_window_excluded_expected_record"
    return "term_gate_rejected_expected_record"


def _role_matches_for_audit(actual: str, wanted: str) -> bool:
    actual = (actual or "").lower()
    wanted = wanted.lower()
    if wanted == "assistant":
        return any(token in actual for token in ("assistant", "agent"))
    if wanted == "user":
        return "user" in actual or actual == "human"
    return wanted in actual


def _read_one_based_line(path: Path, line: int) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, text in enumerate(handle, start=1):
                if index == line:
                    return text.rstrip("\n")
    except OSError:
        return None
    return None


def _public_source_probe(source_probe: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in source_probe.items() if key != "_record"}


def _missing_source_probe() -> dict[str, Any]:
    return {
        "expected_file_exists": False,
        "expected_line_exists": False,
        "json_parse_ok": False,
        "record_text_present": False,
        "record_role_label": None,
        "record_timestamp_present": False,
        "record_cwd_present": False,
        "conversation_classification": {"label": "unavailable", "confidence": "none", "human_authored_high_confidence": False},
        "_record": None,
    }


def _role_label(role: str) -> str:
    normalized = (role or "").lower()
    if "user" in normalized or normalized == "human":
        return "user_like"
    if "assistant" in normalized or "agent" in normalized:
        return "assistant_like"
    if "tool" in normalized or "function" in normalized:
        return "tool_like"
    return "other_or_empty"


def _warning_labels(warnings: list[Any]) -> set[str]:
    labels: set[str] = set()
    for warning in warnings:
        text = str(warning).lower()
        if "max byte cap" in text:
            labels.add("enumeration_byte_cap")
        if "max elapsed cap" in text:
            labels.add("elapsed_cap")
        if "max match cap" in text or "candidate_retention_capped_at_max_match_cap" in text:
            labels.add("match_cap")
        if "reservoir-capped" in text:
            labels.add("weak_reservoir_cap")
        if "arbitrary root enabled" in text:
            labels.add("arbitrary_root_hard_cap_scope")
    return labels


def _cap_impact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = ("enumeration_byte_cap", "elapsed_cap", "match_cap", "weak_reservoir_cap")
    summary = {label: {"case_count": sum(1 for row in rows if row["cap_probe"].get(label))} for label in labels}
    not_searched = Counter(row["scope_probe"].get("not_searched_scope", "unknown") for row in rows)
    return {
        **summary,
        "not_searched_scope": dict(sorted(not_searched.items())),
    }


def _query_basis_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pathologies = Counter(item for row in rows for item in row["query_basis"].get("pathologies", []))
    return {
        "pathology_counts": dict(sorted(pathologies.items())),
        "max_private_query_char_count": max((row["query_basis"]["private_query_char_count"] for row in rows), default=0),
        "max_base_term_count": max((row["query_basis"]["base_term_count"] for row in rows), default=0),
        "max_expanded_term_count": max((row["query_basis"]["expanded_term_count"] for row in rows), default=0),
    }


def _classification_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(label for row in rows for label in row.get("failure_classifications", []))
    return dict(sorted(counts.items()))


def _package_cost_probe(package_row: dict[str, Any] | None) -> dict[str, int]:
    sanitized = (package_row or {}).get("sanitized_row") or {}
    return {
        "files_opened": int(sanitized.get("files_opened") or 0),
        "bytes_parsed": int(sanitized.get("bytes_parsed") or sanitized.get("bytes_scanned") or 0),
        "handoff_bytes": int(
            sanitized.get("output_context_bytes")
            or sanitized.get("context_bytes")
            or sanitized.get("output_bytes")
            or 0
        ),
    }


def _expected_ref_diagnostics_available(rows: list[dict[str, Any]]) -> bool:
    return any(
        ((row.get("scanner_diagnostics") or {}).get("expected_ref_diagnostics") or {}).get("available")
        for row in rows
    )


def _expects_task_or_artifact_handle(case: AuditCase) -> bool:
    text = " ".join([case.family, *case.stage_hits]).lower()
    return any(token in text for token in ("task", "review", "commit", "artifact", "report", "provenance"))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_private_text_committed": False,
        "raw_private_paths_committed": False,
        "local_usernames_committed": False,
        "raw_session_refs_committed": False,
        "raw_query_wording_committed": False,
        "raw_source_lines_committed": False,
        "raw_package_payloads_committed": False,
        "vectors_or_indexes_committed": False,
    }
