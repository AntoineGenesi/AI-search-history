from __future__ import annotations

from .source_refs import evidence_identity

from collections import deque
from dataclasses import dataclass
import re
import time

from .adapters import AdapterReadError, RawLineScanStats, iter_records_for_file
from .clustering import facets_for_record
from .masking import MaskContext
from .query import QueryIntent
from .ranking import (
    Match,
    build_match_profile,
    candidate_strength_label,
    evaluate_record_match,
    group_matches,
    is_weak_partial_generic,
    make_snippet,
    score_record,
    strong_evidence_retention_tier,
)
from .rg_prefilter import filter_planned_files
from .sources import (
    PlannedJsonlFiles,
    SourcePlan,
    cap_hits_from_warnings,
    enumerate_jsonl_files,
    filename_preference_score,
    source_kind_for_path,
    stat_mtime,
)


SCAN_RECORD_QUANTUM = 128
SCAN_ORDER_STRATEGY = "newest_mtime_desc_filename_query_preference_size_asc_path_asc_record_quantum_128_round_robin"
DEDUPLICATION_STRATEGY = "normalized_text_timestamp_cwd_source_ref_scope_preserving_arbitrary_refs"
CONTENT_MIRROR_SOURCE_KINDS = {"codex_history", "codex_sessions"}
PRE_PARSE_STRATEGY = "literal_anchor_raw_line_contains_any_jsonl_only"
PRE_PARSE_SAFE_STAGES = {"artifact_path", "task_slug"}
PRE_PARSE_ANCHOR_KINDS = ("path", "task_slug")
CANDIDATE_STRENGTH_STRATEGY = (
    "strong_anchors_low_evidence_broad_partial_generic_to_weak_reservoir_high_evidence_strong_retention"
)


@dataclass(frozen=True)
class PreParseAnchor:
    value: str
    kind: str


class LiteralAnchorRawLineFilter:
    def __init__(self, anchors: list[PreParseAnchor]) -> None:
        self.anchors = tuple(anchors)
        self._needles = tuple(anchor.value.lower() for anchor in anchors)

    def should_parse(self, raw_line: str) -> bool:
        lowered = raw_line.lower()
        return any(needle in lowered for needle in self._needles)


def search(
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None = None,
    mask_context: MaskContext | None = None,
    use_rg_prefilter: bool = False,
    expected_ref_diagnostics: list[dict] | None = None,
    planned_jsonl_files: PlannedJsonlFiles | None = None,
    include_private_diagnostics: bool = False,
    prioritized_file_paths: set[str] | None = None,
) -> dict:
    started = time.monotonic()
    warnings = list(plan.warnings)
    blind_spots = list(plan.blind_spots)
    matches: list[Match] = []
    weak_matches: list[Match] = []
    weak_reservoir_limit = max(1, min(plan.limits.max_matches, max(3, plan.limits.max_matches // 4)))
    strong_reservoir_limit = max(1, plan.limits.max_matches)
    strong_candidates_retention_capped = 0
    weak_candidates_skipped = 0
    candidate_strength = _initial_candidate_strength_stats(weak_reservoir_limit, strong_reservoir_limit)
    dedupe: set[tuple[str, ...]] = set()
    records_read = 0
    records_rejected_by_should_consider = 0
    candidate_records_deduped = 0
    candidate_records_scored = 0
    searched_files = 0
    searched_bytes = 0
    searched_file_paths: list[str] = []
    fielded_priority_opened_files: list[dict] = []
    stopped = False
    prefilter = None
    planned_files = []
    expected_diag = _init_expected_ref_diagnostics(expected_ref_diagnostics)
    match_profile = build_match_profile(intent)
    pre_parse_filter = _build_pre_parse_filter(match_profile)
    raw_line_stats = RawLineScanStats()

    if planned_jsonl_files is not None:
        planned_files = list(planned_jsonl_files.files)
        warnings = sorted(set(warnings + list(planned_jsonl_files.warnings)))
    else:
        for enumerated in enumerate_jsonl_files(plan, preferred_terms=intent.base_terms):
            path, size, enum_warnings = enumerated
            warnings = enum_warnings
            planned_files.append((path, size))

    prioritized_paths = {str(path) for path in (prioritized_file_paths or set())}
    planned_files = order_planned_files_for_scan(planned_files, intent, prioritized_file_paths=prioritized_paths)
    _mark_expected_files_selected(expected_diag, planned_files)
    planned_files_count = len(planned_files)

    if use_rg_prefilter:
        prefilter_result = filter_planned_files(plan=plan, intent=intent, files=planned_files, cwd=cwd)
        prefilter = prefilter_result.stats
        planned_files = [(item.path, item.size) for item in prefilter_result.files]
        planned_files = order_planned_files_for_scan(planned_files, intent, prioritized_file_paths=prioritized_paths)
    _mark_expected_files_queued(expected_diag, planned_files)
    fielded_priority_scan_order = _fielded_priority_scan_order_diagnostics(planned_files, prioritized_paths)

    scan_queue = deque(
        {
            "path": path,
            "size": size,
            "records": None,
            "opened": False,
            "fielded_priority_label": fielded_priority_scan_order["_labels_by_path"].get(str(path)),
        }
        for path, size in planned_files
    )
    while scan_queue:
        item = scan_queue.popleft()
        if time.monotonic() - started > plan.limits.max_elapsed:
            warnings.append("Stopped before opening next file at max elapsed cap.")
            stopped = True
            break
        path = item["path"]
        size = item["size"]
        try:
            if not item["opened"]:
                searched_files += 1
                searched_bytes += size
                if include_private_diagnostics:
                    searched_file_paths.append(str(path))
                if item.get("fielded_priority_label"):
                    fielded_priority_opened_files.append(
                        {
                            "file_label": str(item["fielded_priority_label"]),
                            "opened_order": searched_files,
                        }
                    )
                _mark_opened_file_progress(expected_diag, item, path, searched_files)
                source_kind = source_kind_for_path(path, plan)
                item["records"] = iter_records_for_file(
                    path,
                    source_kind,
                    default_tz,
                    raw_line_filter=pre_parse_filter,
                    raw_line_stats=raw_line_stats,
                    on_raw_line_examined=_raw_line_examined_marker(expected_diag, item),
                    on_raw_line_skipped=_raw_line_skipped_marker(expected_diag, item),
                )
                item["opened"] = True
            records = item["records"]
            exhausted = False
            for _ in range(SCAN_RECORD_QUANTUM):
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                try:
                    record = next(records)
                except StopIteration:
                    exhausted = True
                    break
                records_read += 1
                _mark_opened_file_record_read(expected_diag, item)
                expected_state = _expected_state_for_record(expected_diag, record)
                if expected_state is not None:
                    expected_state["expected_record_line_reached_before_elapsed_stop"] = True
                    if pre_parse_filter is not None and expected_state["pre_parse_phase_label"] == "not_reached":
                        expected_state["pre_parse_phase_label"] = "raw_line_gate_passed"
                        expected_state["pre_parse_status"] = "passed_raw_line_gate"
                    elif pre_parse_filter is None and expected_state["pre_parse_phase_label"] == "not_reached":
                        expected_state["pre_parse_phase_label"] = "raw_line_gate_disabled"
                        expected_state["pre_parse_status"] = "disabled"
                    expected_state["expected_file_records_read_before_stop"] = int(item.get("records_read") or 0)
                    expected_state["expected_file_progress_bucket"] = _records_read_bucket(
                        int(item.get("records_read") or 0)
                    )
                match_evidence = evaluate_record_match(record, intent, profile=match_profile)
                if not match_evidence.should_consider:
                    records_rejected_by_should_consider += 1
                    if expected_state is not None:
                        expected_state["would_pass_should_consider"] = False
                        expected_state["pre_reservoir_status"] = "rejected_by_should_consider"
                        expected_state["should_consider_phase_label"] = "rejected_by_should_consider"
                        expected_state["drop_phase_label"] = "should_consider_drop"
                    continue
                if expected_state is not None:
                    expected_state["would_pass_should_consider"] = True
                    expected_state["should_consider_phase_label"] = "passed_should_consider"
                key = dedupe_key_for_record(record)
                if key in dedupe:
                    candidate_records_deduped += 1
                    if expected_state is not None:
                        expected_state["deduped_before_scoring"] = True
                        expected_state["pre_reservoir_status"] = "deduped_before_scoring"
                        expected_state["dedupe_phase_label"] = "deduped_before_scoring"
                        expected_state["drop_phase_label"] = "dedupe_drop"
                    continue
                dedupe.add(key)
                candidate_records_scored += 1
                score, reasons, artifacts = score_record(
                    record,
                    intent,
                    cwd,
                    profile=match_profile,
                    evidence=match_evidence,
                )
                if expected_state is not None:
                    expected_state["expected_record_present_before_reservoir_retention"] = True
                    expected_state["pre_reservoir_status"] = "present_before_reservoir_retention"
                    expected_state["dedupe_phase_label"] = "not_deduped"
                strength_label = candidate_strength_label(reasons)
                _count_candidate_strength(candidate_strength, strength_label)
                if is_weak_partial_generic(reasons):
                    candidate_sort_key = match_candidate_sort_key(record, score)
                    if expected_state is not None:
                        expected_state["candidate_strength"] = "weak"
                        expected_state["candidate_strength_label"] = strength_label
                    if len(weak_matches) < weak_reservoir_limit:
                        weak_matches.append(_materialize_match(record, score, reasons, artifacts, intent, mask_context))
                        candidate_strength["weak_reservoir_retained"] += 1
                        if expected_state is not None:
                            expected_state["reservoir_phase_label"] = "weak_reservoir_retained"
                    else:
                        weakest_index = min(
                            range(len(weak_matches)),
                            key=lambda index: match_sort_key(weak_matches[index]),
                        )
                        if candidate_sort_key > match_sort_key(weak_matches[weakest_index]):
                            weak_matches[weakest_index] = _materialize_match(
                                record, score, reasons, artifacts, intent, mask_context
                            )
                            candidate_strength["weak_reservoir_replaced"] += 1
                            if expected_state is not None:
                                expected_state["reservoir_phase_label"] = "weak_reservoir_replaced_existing_candidate"
                        else:
                            candidate_strength["weak_reservoir_cap_dropped"] += 1
                            if expected_state is not None:
                                expected_state["reservoir_phase_label"] = "weak_reservoir_cap_drop"
                                expected_state["drop_phase_label"] = "weak_reservoir_drop"
                        weak_candidates_skipped += 1
                    continue
                if expected_state is not None:
                    expected_state["candidate_strength"] = "strong"
                    expected_state["candidate_strength_label"] = strength_label
                evidence_tier = strong_evidence_retention_tier(reasons)
                if evidence_tier:
                    candidate_strength["strong_high_evidence_candidates"] += 1
                candidate_sort_key = match_candidate_sort_key(record, score, reasons)
                if len(matches) < strong_reservoir_limit:
                    matches.append(_materialize_match(record, score, reasons, artifacts, intent, mask_context))
                    candidate_strength["strong_reservoir_retained"] += 1
                    if expected_state is not None:
                        expected_state["reservoir_phase_label"] = "strong_reservoir_retained"
                    continue
                weakest_index = min(
                    range(len(matches)),
                    key=lambda index: match_sort_key(matches[index]),
                )
                if candidate_sort_key > match_sort_key(matches[weakest_index]):
                    matches[weakest_index] = _materialize_match(
                        record, score, reasons, artifacts, intent, mask_context
                    )
                    candidate_strength["strong_reservoir_replaced"] += 1
                    if expected_state is not None:
                        expected_state["reservoir_phase_label"] = "strong_reservoir_replaced_existing_candidate"
                else:
                    candidate_strength["strong_reservoir_cap_dropped"] += 1
                    if evidence_tier:
                        candidate_strength["strong_high_evidence_reservoir_cap_dropped"] += 1
                    if expected_state is not None:
                        expected_state["reservoir_phase_label"] = "strong_reservoir_cap_drop"
                        expected_state["drop_phase_label"] = "strong_reservoir_drop"
                strong_candidates_retention_capped += 1
        except AdapterReadError as exc:
            warnings.append(str(exc))
            continue
        if stopped:
            break
        if not exhausted:
            scan_queue.append(item)

    if strong_candidates_retention_capped:
        warnings.append("Strong candidates were retention-capped at max match cap before final ranking.")
    if weak_candidates_skipped:
        warnings.append("Weak low-evidence candidates were reservoir-capped before final ranking.")
    candidate_strength["strong_high_evidence_reservoir_retained"] = sum(
        1 for match in matches if strong_evidence_retention_tier(match.reasons)
    )
    _mark_expected_pre_final_candidates(expected_diag, matches + weak_matches)
    matches = matches + weak_matches
    if len(matches) > plan.limits.max_matches:
        warnings.append("Final candidates were capped at max match cap after ranking.")
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    groups = group_matches(matches)
    _mark_expected_final_candidates(expected_diag, matches)
    partial_failures = list(raw_line_stats.partial_failures or [])
    for missing in plan.missing_roots:
        if missing.root_format == "sqlite" or missing.path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".vscdb"}:
            partial_failures.append(
                {
                    "provider": missing.provider,
                    "path": str(missing.path),
                    "line": None,
                    "reason": "selected provider database is missing",
                }
            )
    payload = {
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "scan_order_strategy": SCAN_ORDER_STRATEGY,
        "scan_record_quantum": SCAN_RECORD_QUANTUM,
        "planned_files_count": planned_files_count,
        "opened_files_count": searched_files,
        "records_read": records_read,
        "raw_lines_examined": raw_line_stats.raw_lines_examined,
        "raw_lines_skipped_before_full_json_parse": raw_line_stats.raw_lines_skipped_before_full_json_parse,
        "full_json_records_parsed": raw_line_stats.full_json_records_parsed,
        "source_diagnostics": {
            "malformed_rows": raw_line_stats.malformed_rows,
            "skipped_rows": raw_line_stats.skipped_rows,
            "ignored_rows": raw_line_stats.ignored_rows,
            "weak_timestamp_records": raw_line_stats.weak_timestamp_records,
            "fallback_records": raw_line_stats.fallback_records,
            "partial_failures": partial_failures,
            "coverage": "partial" if partial_failures or stopped else "complete_within_caps",
        },
        "providers_searched": sorted({root.provider for root in plan.roots if root.provider}),
        "missing_roots": [root.to_dict() for root in plan.missing_roots],
        "pre_parse": _pre_parse_stats(pre_parse_filter, raw_line_stats),
        "records_rejected_by_should_consider": records_rejected_by_should_consider,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
        "candidate_strength": candidate_strength,
        "dedupe_strategy": DEDUPLICATION_STRATEGY,
        "fielded_priority_scan_order": _public_fielded_priority_scan_order(
            fielded_priority_scan_order,
            opened_files=fielded_priority_opened_files,
        ),
        "groups": groups,
        "warnings": sorted(set(warnings + plan.warnings)),
        "cap_hits": cap_hits_from_warnings(sorted(set(warnings + plan.warnings))),
        "blind_spots": sorted(set(blind_spots)),
    }
    if prefilter is not None:
        payload["prefilter"] = prefilter
    if planned_jsonl_files is not None:
        payload["planned_file_enumeration"] = planned_jsonl_files.to_diagnostics()
    if include_private_diagnostics:
        payload["_searched_file_paths"] = searched_file_paths
    if expected_diag is not None:
        payload["expected_ref_diagnostics"] = _public_expected_ref_diagnostics(expected_diag)
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload


def _init_expected_ref_diagnostics(expected_refs: list[dict] | None) -> dict | None:
    if not expected_refs:
        return None
    refs = []
    by_ref: dict[tuple[str, int], dict] = {}
    by_file: dict[str, list[dict]] = {}
    for index, ref in enumerate(expected_refs, start=1):
        path = ref.get("path") if isinstance(ref, dict) else None
        line = ref.get("line") if isinstance(ref, dict) else None
        if path is None or not isinstance(line, int):
            continue
        state = {
            "ref_id": str(ref.get("ref_id") or f"expected_ref_{index:03d}"),
            "_path": str(path),
            "_line": line,
            "expected_file_label": None,
            "expected_file_open_order": None,
            "expected_file_records_read_before_stop": 0,
            "expected_file_raw_lines_examined_before_stop": 0,
            "expected_file_progress_bucket": "not_opened",
            "expected_file_raw_progress_bucket": "not_opened",
            "expected_file_selected_by_source_plan": False,
            "expected_file_queued_for_scan": False,
            "expected_file_opened_before_scan_stop": False,
            "expected_record_line_reached_before_elapsed_stop": False,
            "expected_record_skipped_before_full_json_parse": False,
            "would_pass_should_consider": None,
            "deduped_before_scoring": False,
            "expected_record_present_before_reservoir_retention": False,
            "expected_record_present_in_pre_final_candidate_state": False,
            "expected_record_present_in_final_candidate_refs": False,
            "expected_record_present_in_final_candidate_groups": False,
            "candidate_strength": None,
            "candidate_strength_label": None,
            "pre_parse_phase_label": "not_reached",
            "should_consider_phase_label": "not_reached",
            "dedupe_phase_label": "not_reached",
            "reservoir_phase_label": "not_reached",
            "final_cap_phase_label": "not_reached",
            "drop_phase_label": None,
            "pre_parse_status": "not_reached",
            "pre_reservoir_status": "not_reached",
            "pre_final_status": "not_reached",
            "final_status": "not_reached",
        }
        refs.append(state)
        by_ref[(state["_path"], line)] = state
        by_file.setdefault(state["_path"], []).append(state)
    if not refs:
        return None
    return {"refs": refs, "by_ref": by_ref, "by_file": by_file, "opened_files": [], "opened_by_path": {}}


def _mark_expected_files_selected(expected_diag: dict | None, planned_files: list[tuple]) -> None:
    if expected_diag is None:
        return
    selected_paths = {str(path) for path, _size in planned_files}
    for state in expected_diag["refs"]:
        state["expected_file_selected_by_source_plan"] = state["_path"] in selected_paths


def _mark_expected_files_queued(expected_diag: dict | None, planned_files: list[tuple]) -> None:
    if expected_diag is None:
        return
    queued_paths = {str(path) for path, _size in planned_files}
    for state in expected_diag["refs"]:
        state["expected_file_queued_for_scan"] = state["_path"] in queued_paths


def _mark_opened_file_progress(expected_diag: dict | None, item: dict, path, opened_order: int) -> None:
    if expected_diag is None:
        return
    item["file_label"] = f"opened_file_{opened_order:03d}"
    item["opened_order"] = opened_order
    item["records_read"] = 0
    progress = {
        "file_label": item["file_label"],
        "opened_order": opened_order,
        "records_read": 0,
        "raw_lines_examined": 0,
        "raw_lines_skipped_before_full_json_parse": 0,
        "records_read_bucket": "0",
        "raw_lines_examined_bucket": "0",
        "expected_ref_file": str(path) in expected_diag["by_file"],
        "_path": str(path),
    }
    expected_diag["opened_files"].append(progress)
    expected_diag["opened_by_path"][str(path)] = progress
    for state in expected_diag["by_file"].get(str(path), []):
        state["expected_file_label"] = item["file_label"]
        state["expected_file_open_order"] = opened_order
        state["expected_file_opened_before_scan_stop"] = True
        state["expected_file_progress_bucket"] = "0"
        if state["pre_reservoir_status"] == "not_reached":
            state["pre_reservoir_status"] = "file_opened_record_line_not_reached"
            state["pre_final_status"] = "file_opened_record_line_not_reached"
            state["final_status"] = "file_opened_record_line_not_reached"


def _mark_opened_file_record_read(expected_diag: dict | None, item: dict) -> None:
    if expected_diag is None:
        return
    item["records_read"] = int(item.get("records_read") or 0) + 1
    progress = expected_diag["opened_by_path"].get(str(item["path"]))
    if progress is None:
        return
    progress["records_read"] = int(item["records_read"])
    progress["records_read_bucket"] = _records_read_bucket(int(item["records_read"]))
    for state in expected_diag["by_file"].get(str(item["path"]), []):
        state["expected_file_records_read_before_stop"] = int(item["records_read"])
        state["expected_file_progress_bucket"] = progress["records_read_bucket"]


def _raw_line_examined_marker(expected_diag: dict | None, item: dict):
    def mark(_path, _line: int) -> None:
        item["raw_lines_examined"] = int(item.get("raw_lines_examined") or 0) + 1
        if expected_diag is None:
            return
        progress = expected_diag["opened_by_path"].get(str(item["path"]))
        if progress is not None:
            progress["raw_lines_examined"] = int(item["raw_lines_examined"])
            progress["raw_lines_examined_bucket"] = _records_read_bucket(int(item["raw_lines_examined"]))
        for state in expected_diag["by_file"].get(str(item["path"]), []):
            state["expected_file_raw_lines_examined_before_stop"] = int(item["raw_lines_examined"])
            state["expected_file_raw_progress_bucket"] = _records_read_bucket(int(item["raw_lines_examined"]))

    return mark


def _raw_line_skipped_marker(expected_diag: dict | None, item: dict):
    def mark(path, line: int) -> None:
        item["raw_lines_skipped_before_full_json_parse"] = int(item.get("raw_lines_skipped_before_full_json_parse") or 0) + 1
        if expected_diag is None:
            return
        progress = expected_diag["opened_by_path"].get(str(item["path"]))
        if progress is not None:
            progress["raw_lines_skipped_before_full_json_parse"] = int(
                item.get("raw_lines_skipped_before_full_json_parse") or 0
            )
        state = expected_diag["by_ref"].get((str(path), int(line)))
        if state is None:
            return
        state["expected_record_line_reached_before_elapsed_stop"] = True
        state["expected_record_skipped_before_full_json_parse"] = True
        state["pre_parse_phase_label"] = "raw_line_gate_skipped"
        state["pre_parse_status"] = "skipped_before_full_json_parse"
        state["pre_reservoir_status"] = "skipped_before_full_json_parse"
        state["pre_final_status"] = "skipped_before_full_json_parse"
        state["final_status"] = "skipped_before_full_json_parse"
        state["drop_phase_label"] = "pre_parse_drop"

    return mark


def _expected_state_for_record(expected_diag: dict | None, record) -> dict | None:
    if expected_diag is None:
        return None
    return expected_diag["by_ref"].get((str(record.path), int(record.line)))


def _mark_expected_pre_final_candidates(expected_diag: dict | None, candidates: list[Match]) -> None:
    if expected_diag is None:
        return
    candidate_keys = {(str(match.path), int(match.line)) for match in candidates}
    for state in expected_diag["refs"]:
        key = (state["_path"], state["_line"])
        present = key in candidate_keys
        state["expected_record_present_in_pre_final_candidate_state"] = present
        if present:
            state["pre_final_status"] = "present_in_pre_final_candidate_state"
        elif state["expected_record_skipped_before_full_json_parse"]:
            state["pre_final_status"] = "skipped_before_full_json_parse"
        elif state["expected_record_present_before_reservoir_retention"]:
            state["pre_final_status"] = "dropped_by_reservoir_retention"
            if state["reservoir_phase_label"] in {"not_reached", "strong_reservoir_retained", "weak_reservoir_retained"}:
                state["reservoir_phase_label"] = "reservoir_retention_drop"
            state["drop_phase_label"] = state["drop_phase_label"] or "reservoir_drop"
        elif state["deduped_before_scoring"]:
            state["pre_final_status"] = "deduped_before_scoring"
        elif state["would_pass_should_consider"] is False:
            state["pre_final_status"] = "rejected_by_should_consider"
        elif state["expected_record_line_reached_before_elapsed_stop"]:
            state["pre_final_status"] = "line_reached_but_not_scored"


def _mark_expected_final_candidates(expected_diag: dict | None, candidates: list[Match]) -> None:
    if expected_diag is None:
        return
    candidate_keys = {(str(match.path), int(match.line)) for match in candidates}
    for state in expected_diag["refs"]:
        key = (state["_path"], state["_line"])
        present = key in candidate_keys
        state["expected_record_present_in_final_candidate_refs"] = present
        state["expected_record_present_in_final_candidate_groups"] = present
        if present:
            state["final_status"] = "present_in_final_candidate_groups"
            state["final_cap_phase_label"] = "present_in_final_output"
        elif state["expected_record_skipped_before_full_json_parse"]:
            state["final_status"] = "skipped_before_full_json_parse"
            state["final_cap_phase_label"] = "pre_parse_drop"
        elif state["expected_record_present_in_pre_final_candidate_state"]:
            state["final_status"] = "lost_between_pre_final_and_final_output"
            state["final_cap_phase_label"] = "final_cap_drop"
            state["drop_phase_label"] = state["drop_phase_label"] or "final_cap_drop"
        elif state["expected_record_present_before_reservoir_retention"]:
            state["final_status"] = "dropped_before_final_candidates"
            state["final_cap_phase_label"] = "absent_before_final_candidates"
        elif state["deduped_before_scoring"]:
            state["final_status"] = "deduped_before_scoring"
            state["final_cap_phase_label"] = "dedupe_drop"
        elif state["would_pass_should_consider"] is False:
            state["final_status"] = "rejected_by_should_consider"
            state["final_cap_phase_label"] = "should_consider_drop"
        elif state["expected_record_line_reached_before_elapsed_stop"]:
            state["final_status"] = "line_reached_but_not_scored"
            state["final_cap_phase_label"] = "line_reached_but_not_scored"


def _public_expected_ref_diagnostics(expected_diag: dict) -> dict:
    public_refs = []
    for state in expected_diag["refs"]:
        line_reach_status = "record_line_reached"
        if not state["expected_record_line_reached_before_elapsed_stop"]:
            line_reach_status = (
                "file_opened_record_line_not_reached"
                if state["expected_file_opened_before_scan_stop"]
                else "file_not_opened"
            )
        elif state["expected_record_skipped_before_full_json_parse"]:
            line_reach_status = "raw_line_skipped_before_full_json_parse"
        public_refs.append(
            {
                "ref_id": state["ref_id"],
                "expected_file_label": state["expected_file_label"],
                "expected_file_open_order": state["expected_file_open_order"],
                "expected_file_records_read_before_stop": int(state["expected_file_records_read_before_stop"]),
                "expected_file_raw_lines_examined_before_stop": int(
                    state["expected_file_raw_lines_examined_before_stop"]
                ),
                "expected_file_progress_bucket": state["expected_file_progress_bucket"],
                "expected_file_raw_progress_bucket": state["expected_file_raw_progress_bucket"],
                "expected_file_selected_by_source_plan": bool(state["expected_file_selected_by_source_plan"]),
                "expected_file_queued_for_scan": bool(state["expected_file_queued_for_scan"]),
                "expected_file_opened_before_scan_stop": bool(state["expected_file_opened_before_scan_stop"]),
                "expected_record_line_reached_before_elapsed_stop": bool(
                    state["expected_record_line_reached_before_elapsed_stop"]
                ),
                "expected_record_skipped_before_full_json_parse": bool(
                    state["expected_record_skipped_before_full_json_parse"]
                ),
                "line_reach_status": line_reach_status,
                "would_pass_should_consider": state["would_pass_should_consider"],
                "deduped_before_scoring": bool(state["deduped_before_scoring"]),
                "expected_record_present_before_reservoir_retention": bool(
                    state["expected_record_present_before_reservoir_retention"]
                ),
                "expected_record_present_in_pre_final_candidate_state": bool(
                    state["expected_record_present_in_pre_final_candidate_state"]
                ),
                "expected_record_present_in_final_candidate_refs": bool(
                    state["expected_record_present_in_final_candidate_refs"]
                ),
                "expected_record_present_in_final_candidate_groups": bool(
                    state["expected_record_present_in_final_candidate_groups"]
                ),
                "candidate_strength": state["candidate_strength"],
                "candidate_strength_label": state["candidate_strength_label"],
                "pre_parse_phase_label": state["pre_parse_phase_label"],
                "should_consider_phase_label": state["should_consider_phase_label"],
                "dedupe_phase_label": state["dedupe_phase_label"],
                "reservoir_phase_label": state["reservoir_phase_label"],
                "final_cap_phase_label": state["final_cap_phase_label"],
                "drop_phase_label": state["drop_phase_label"],
                "pre_parse_status": state["pre_parse_status"],
                "pre_reservoir_status": state["pre_reservoir_status"],
                "pre_final_status": state["pre_final_status"],
                "final_status": state["final_status"],
            }
        )
    return {
        "enabled": True,
        "raw_paths_included": False,
        "line_numbers_included": False,
        "ref_count": len(public_refs),
        "line_reached_count": sum(
            1 for ref in public_refs if ref["expected_record_line_reached_before_elapsed_stop"]
        ),
        "pre_reservoir_present_count": sum(
            1 for ref in public_refs if ref["expected_record_present_before_reservoir_retention"]
        ),
        "pre_final_present_count": sum(
            1 for ref in public_refs if ref["expected_record_present_in_pre_final_candidate_state"]
        ),
        "final_candidate_ref_present_count": sum(
            1 for ref in public_refs if ref["expected_record_present_in_final_candidate_refs"]
        ),
        "final_candidate_group_present_count": sum(
            1 for ref in public_refs if ref["expected_record_present_in_final_candidate_groups"]
        ),
        "opened_file_count": len(expected_diag["opened_files"]),
        "opened_files": [
            {
                "file_label": item["file_label"],
                "opened_order": int(item["opened_order"]),
                "records_read": int(item["records_read"]),
                "raw_lines_examined": int(item.get("raw_lines_examined") or 0),
                "raw_lines_skipped_before_full_json_parse": int(
                    item.get("raw_lines_skipped_before_full_json_parse") or 0
                ),
                "records_read_bucket": item["records_read_bucket"],
                "raw_lines_examined_bucket": item.get("raw_lines_examined_bucket") or "0",
                "expected_ref_file": bool(item["expected_ref_file"]),
            }
            for item in expected_diag["opened_files"]
        ],
        "refs": public_refs,
    }


def _records_read_bucket(records_read: int) -> str:
    if records_read <= 0:
        return "0"
    if records_read <= 128:
        return "1-128"
    if records_read <= 512:
        return "129-512"
    if records_read <= 2048:
        return "513-2048"
    if records_read <= 8192:
        return "2049-8192"
    return "8193_plus"


def _initial_candidate_strength_stats(weak_reservoir_limit: int, strong_reservoir_limit: int) -> dict:
    return {
        "strategy": CANDIDATE_STRENGTH_STRATEGY,
        "strong_reservoir_limit": int(strong_reservoir_limit),
        "weak_reservoir_limit": int(weak_reservoir_limit),
        "strong_candidates": 0,
        "weak_candidates": 0,
        "weak_partial_generic_candidates": 0,
        "weak_broad_unanchored_low_evidence_candidates": 0,
        "strong_reservoir_retained": 0,
        "strong_reservoir_replaced": 0,
        "strong_reservoir_cap_dropped": 0,
        "strong_high_evidence_candidates": 0,
        "strong_high_evidence_reservoir_retained": 0,
        "strong_high_evidence_reservoir_cap_dropped": 0,
        "weak_reservoir_retained": 0,
        "weak_reservoir_replaced": 0,
        "weak_reservoir_cap_dropped": 0,
    }


def _count_candidate_strength(stats: dict, strength_label: str) -> None:
    if strength_label == "strong":
        stats["strong_candidates"] += 1
        return
    stats["weak_candidates"] += 1
    if strength_label == "weak_partial_generic":
        stats["weak_partial_generic_candidates"] += 1
    elif strength_label == "weak_broad_unanchored_low_evidence":
        stats["weak_broad_unanchored_low_evidence_candidates"] += 1


def _build_pre_parse_filter(match_profile) -> LiteralAnchorRawLineFilter | None:
    anchors: dict[str, PreParseAnchor] = {}
    for term in match_profile.active_terms:
        if term.stage not in PRE_PARSE_SAFE_STAGES:
            continue
        value = " ".join(term.term.strip().split())
        kind = _pre_parse_anchor_kind(value, stage=term.stage)
        if kind is None:
            continue
        anchors[value.lower()] = PreParseAnchor(value=value, kind=kind)
    if not anchors:
        return None
    return LiteralAnchorRawLineFilter(sorted(anchors.values(), key=lambda item: (-len(item.value), item.value)))


def _pre_parse_anchor_kind(value: str, *, stage: str) -> str | None:
    if not _fixed_term_safe(value):
        return None
    if stage == "artifact_path" and _path_like_anchor(value):
        return "path"
    if stage == "task_slug" and _task_slug_anchor(value):
        return "task_slug"
    return None


def _path_like_anchor(value: str) -> bool:
    return bool(
        re.search(r"(?:^|/)(?:tasks|docs|reports|reviews|src|tests|codex/state/reviews)/[\w./-]+", value)
        or re.search(r"\.(?:md|html|json|jsonl|csv|txt|py|sh)$", value)
    )


def _task_slug_anchor(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z]{2,12}-\d+[a-z]?(?:-[a-z0-9]+){0,12}", value.lower()))


def _fixed_term_safe(value: str) -> bool:
    return 2 < len(value) <= 240 and "\x00" not in value


def _pre_parse_stats(
    pre_parse_filter: LiteralAnchorRawLineFilter | None,
    raw_line_stats: RawLineScanStats,
) -> dict:
    anchors = list(pre_parse_filter.anchors) if pre_parse_filter is not None else []
    anchor_kind_counts: dict[str, int] = {}
    for anchor in anchors:
        if anchor.kind in PRE_PARSE_ANCHOR_KINDS:
            anchor_kind_counts[anchor.kind] = anchor_kind_counts.get(anchor.kind, 0) + 1
    return {
        "enabled": pre_parse_filter is not None,
        "eligible": pre_parse_filter is not None,
        "status": "active" if pre_parse_filter is not None else "ineligible_query",
        "strategy": PRE_PARSE_STRATEGY,
        "applies_to": "line_oriented_jsonl_records_only",
        "needle_policy": "literal_high_signal_anchors" if pre_parse_filter is not None else None,
        "needle_count": len(anchors),
        "anchor_kind_counts": anchor_kind_counts,
        "raw_lines_examined": int(raw_line_stats.raw_lines_examined),
        "raw_lines_skipped_before_full_json_parse": int(raw_line_stats.raw_lines_skipped_before_full_json_parse),
        "full_json_records_parsed": int(raw_line_stats.full_json_records_parsed),
        "raw_anchor_needles_included": False,
    }


def order_planned_files_for_scan(
    planned_files: list[tuple],
    intent: QueryIntent,
    *,
    prioritized_file_paths: set[str] | None = None,
) -> list[tuple]:
    prioritized_paths = {str(path) for path in (prioritized_file_paths or set())}
    return sorted(
        planned_files,
        key=lambda item: (
            0 if str(item[0]) in prioritized_paths else 1,
            -int(stat_mtime(item[0])),
            -filename_preference_score(item[0], intent.base_terms),
            item[1],
            str(item[0]),
        ),
    )


def _fielded_priority_scan_order_diagnostics(planned_files: list[tuple], prioritized_paths: set[str]) -> dict:
    included_paths = [str(path) for path, _size in planned_files if str(path) in prioritized_paths]
    labels_by_path = {
        path: f"fielded_priority_{index:03d}"
        for index, path in enumerate(included_paths, start=1)
    }
    return {
        "_labels_by_path": labels_by_path,
        "enabled": bool(prioritized_paths),
        "priority_source": "fielded_bm25_candidate_or_opened_files",
        "status": "active" if included_paths else ("no_prioritized_files_in_planned_set" if prioritized_paths else "disabled"),
        "planned_file_count": len(planned_files),
        "prioritized_file_count": len(included_paths),
        "non_prioritized_file_count": max(0, len(planned_files) - len(included_paths)),
        "prioritized_file_labels": list(labels_by_path.values()),
        "raw_paths_included": False,
        "all_planned_files_preserved": True,
        "fielded_priority_excludes_non_prioritized_files": False,
        "no_exclusion_semantics": True,
    }


def _public_fielded_priority_scan_order(diagnostics: dict, *, opened_files: list[dict] | None = None) -> dict:
    opened = [
        {
            "file_label": str(item.get("file_label") or ""),
            "opened_order": int(item.get("opened_order") or 0),
        }
        for item in (opened_files or [])
        if item.get("file_label")
    ]
    return {
        "enabled": bool(diagnostics.get("enabled", False)),
        "priority_source": str(diagnostics.get("priority_source") or "fielded_bm25_candidate_or_opened_files"),
        "status": str(diagnostics.get("status") or "disabled"),
        "planned_file_count": int(diagnostics.get("planned_file_count") or 0),
        "prioritized_file_count": int(diagnostics.get("prioritized_file_count") or 0),
        "non_prioritized_file_count": int(diagnostics.get("non_prioritized_file_count") or 0),
        "opened_prioritized_file_count": len(opened),
        "opened_prioritized_files": opened,
        "prioritized_file_labels": list(diagnostics.get("prioritized_file_labels") or []),
        "raw_paths_included": False,
        "opened_order_labels_include_raw_paths": False,
        "all_planned_files_preserved": bool(diagnostics.get("all_planned_files_preserved", True)),
        "fielded_priority_excludes_non_prioritized_files": False,
        "no_exclusion_semantics": True,
    }


def dedupe_key_for_record(record) -> tuple[str, ...]:
    normalized_text = " ".join(record.text.split()).lower()
    source_scope = dedupe_source_scope(record)
    return (
        normalized_text,
        record.raw_timestamp or "",
        record.cwd or "",
        source_scope,
    )


def dedupe_source_scope(record) -> str:
    source_format = record.source_format or ""
    if source_format in CONTENT_MIRROR_SOURCE_KINDS:
        return "codex_history_session_content_mirror"
    if record.source_ref:
        return repr(evidence_identity(record))
    if record.record_id:
        return f"record_id:{source_format}:{record.conversation_id or ''}:{record.record_id}"
    return f"source_ref:{source_format}:{record.path}:{record.line}"


def _materialize_match(
    record,
    score: float,
    reasons: list[str],
    artifacts: list[str],
    intent: QueryIntent,
    mask_context: MaskContext | None,
) -> Match:
    if mask_context is not None:
        copied_text = mask_context.mask_text(
            record.text,
            source_path=record.path,
            source_index=record.line,
        )
    else:
        copied_text = record.text
    snippet_text = make_snippet(copied_text, intent.terms)
    facets = facets_for_record(path=record.path, text=record.text, artifacts=artifacts)
    return Match(
        path=record.path,
        line=record.line,
        role=record.role,
        timestamp=record.timestamp.isoformat() if record.timestamp else None,
        cwd=record.cwd,
        snippet=snippet_text,
        score=round(score, 3),
        reasons=reasons,
        artifacts=facets.artifacts,
        session=record.conversation_id or facets.session,
        tasks=facets.tasks,
        preferences=facets.preferences,
        provider=record.provider,
        source_ref=record.source_ref,
        source_refs=list(record.source_refs or []),
        native_id=record.native_id,
        timestamp_quality=record.timestamp_quality,
        coverage_notes=list(record.coverage_notes or []),
    )


def match_candidate_sort_key(record, score: float, reasons: list[str] | None = None) -> tuple[int, float, str, str, int]:
    return (
        strong_evidence_retention_tier(reasons or []),
        round(score, 3),
        record.timestamp.isoformat() if record.timestamp else "",
        record.path,
        -record.line,
    )


def match_sort_key(match: Match) -> tuple[int, float, str, str, int]:
    return (strong_evidence_retention_tier(match.reasons), match.score, match.timestamp or "", match.path, -match.line)
