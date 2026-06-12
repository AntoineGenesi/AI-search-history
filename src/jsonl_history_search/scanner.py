from __future__ import annotations

from collections import deque
import time

from .adapters import AdapterReadError, iter_records_for_file
from .clustering import facets_for_record
from .masking import MaskContext
from .query import QueryIntent
from .ranking import Match, group_matches, is_weak_partial_generic, make_snippet, score_record, should_consider
from .rg_prefilter import filter_planned_files
from .sources import SourcePlan, enumerate_jsonl_files, filename_preference_score, source_kind_for_path, stat_mtime


SCAN_RECORD_QUANTUM = 128
SCAN_ORDER_STRATEGY = "newest_mtime_desc_filename_query_preference_size_asc_path_asc_record_quantum_128_round_robin"
DEDUPLICATION_STRATEGY = "normalized_text_timestamp_cwd_source_ref_scope_preserving_arbitrary_refs"
CONTENT_MIRROR_SOURCE_KINDS = {"codex_history", "codex_sessions"}


def search(
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None = None,
    mask_context: MaskContext | None = None,
    use_rg_prefilter: bool = False,
    expected_ref_diagnostics: list[dict] | None = None,
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
    dedupe: set[tuple[str, ...]] = set()
    records_read = 0
    records_rejected_by_should_consider = 0
    candidate_records_deduped = 0
    candidate_records_scored = 0
    searched_files = 0
    searched_bytes = 0
    stopped = False
    prefilter = None
    planned_files = []
    expected_diag = _init_expected_ref_diagnostics(expected_ref_diagnostics)

    for enumerated in enumerate_jsonl_files(plan, preferred_terms=intent.base_terms):
        path, size, enum_warnings = enumerated
        warnings = enum_warnings
        planned_files.append((path, size))

    planned_files = order_planned_files_for_scan(planned_files, intent)
    _mark_expected_files_selected(expected_diag, planned_files)
    planned_files_count = len(planned_files)

    if use_rg_prefilter:
        prefilter_result = filter_planned_files(plan=plan, intent=intent, files=planned_files, cwd=cwd)
        prefilter = prefilter_result.stats
        planned_files = [(item.path, item.size) for item in prefilter_result.files]
        planned_files = order_planned_files_for_scan(planned_files, intent)
    _mark_expected_files_queued(expected_diag, planned_files)

    scan_queue = deque(
        {
            "path": path,
            "size": size,
            "records": None,
            "opened": False,
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
                _mark_expected_file_opened(expected_diag, path)
                source_kind = source_kind_for_path(path, plan)
                item["records"] = iter_records_for_file(path, source_kind, default_tz)
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
                expected_state = _expected_state_for_record(expected_diag, record)
                if expected_state is not None:
                    expected_state["expected_record_line_reached_before_elapsed_stop"] = True
                if not should_consider(record, intent):
                    records_rejected_by_should_consider += 1
                    if expected_state is not None:
                        expected_state["would_pass_should_consider"] = False
                        expected_state["pre_reservoir_status"] = "rejected_by_should_consider"
                    continue
                if expected_state is not None:
                    expected_state["would_pass_should_consider"] = True
                key = dedupe_key_for_record(record)
                if key in dedupe:
                    candidate_records_deduped += 1
                    if expected_state is not None:
                        expected_state["deduped_before_scoring"] = True
                        expected_state["pre_reservoir_status"] = "deduped_before_scoring"
                    continue
                dedupe.add(key)
                candidate_records_scored += 1
                score, reasons, artifacts = score_record(record, intent, cwd)
                if expected_state is not None:
                    expected_state["expected_record_present_before_reservoir_retention"] = True
                    expected_state["pre_reservoir_status"] = "present_before_reservoir_retention"
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
                match = Match(
                    path=record.path,
                    line=record.line,
                    role=record.role,
                    timestamp=record.timestamp.isoformat() if record.timestamp else None,
                    cwd=record.cwd,
                    snippet=snippet_text,
                    score=round(score, 3),
                    reasons=reasons,
                    artifacts=facets.artifacts,
                    session=facets.session,
                    tasks=facets.tasks,
                    preferences=facets.preferences,
                )
                if is_weak_partial_generic(reasons):
                    if expected_state is not None:
                        expected_state["candidate_strength"] = "weak"
                    if len(weak_matches) < weak_reservoir_limit:
                        weak_matches.append(match)
                    else:
                        weakest_index = min(
                            range(len(weak_matches)),
                            key=lambda index: match_sort_key(weak_matches[index]),
                        )
                        if match_sort_key(match) > match_sort_key(weak_matches[weakest_index]):
                            weak_matches[weakest_index] = match
                        weak_candidates_skipped += 1
                    continue
                if expected_state is not None:
                    expected_state["candidate_strength"] = "strong"
                if len(matches) < strong_reservoir_limit:
                    matches.append(match)
                    continue
                weakest_index = min(
                    range(len(matches)),
                    key=lambda index: match_sort_key(matches[index]),
                )
                if match_sort_key(match) > match_sort_key(matches[weakest_index]):
                    matches[weakest_index] = match
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
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    _mark_expected_pre_final_candidates(expected_diag, matches + weak_matches)
    matches = matches + weak_matches
    if len(matches) > plan.limits.max_matches:
        warnings.append("Final candidates were capped at max match cap after ranking.")
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    groups = group_matches(matches)
    _mark_expected_final_candidates(expected_diag, matches)
    payload = {
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "scan_order_strategy": SCAN_ORDER_STRATEGY,
        "scan_record_quantum": SCAN_RECORD_QUANTUM,
        "planned_files_count": planned_files_count,
        "opened_files_count": searched_files,
        "records_read": records_read,
        "records_rejected_by_should_consider": records_rejected_by_should_consider,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
        "dedupe_strategy": DEDUPLICATION_STRATEGY,
        "groups": groups,
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(blind_spots)),
    }
    if prefilter is not None:
        payload["prefilter"] = prefilter
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
            "expected_file_selected_by_source_plan": False,
            "expected_file_queued_for_scan": False,
            "expected_file_opened_before_scan_stop": False,
            "expected_record_line_reached_before_elapsed_stop": False,
            "would_pass_should_consider": None,
            "deduped_before_scoring": False,
            "expected_record_present_before_reservoir_retention": False,
            "expected_record_present_in_pre_final_candidate_state": False,
            "expected_record_present_in_final_candidate_refs": False,
            "expected_record_present_in_final_candidate_groups": False,
            "candidate_strength": None,
            "pre_reservoir_status": "not_reached",
            "pre_final_status": "not_reached",
            "final_status": "not_reached",
        }
        refs.append(state)
        by_ref[(state["_path"], line)] = state
        by_file.setdefault(state["_path"], []).append(state)
    if not refs:
        return None
    return {"refs": refs, "by_ref": by_ref, "by_file": by_file}


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


def _mark_expected_file_opened(expected_diag: dict | None, path) -> None:
    if expected_diag is None:
        return
    for state in expected_diag["by_file"].get(str(path), []):
        state["expected_file_opened_before_scan_stop"] = True
        if state["pre_reservoir_status"] == "not_reached":
            state["pre_reservoir_status"] = "file_opened_record_line_not_reached"
            state["pre_final_status"] = "file_opened_record_line_not_reached"
            state["final_status"] = "file_opened_record_line_not_reached"


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
        elif state["expected_record_present_before_reservoir_retention"]:
            state["pre_final_status"] = "dropped_by_reservoir_retention"
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
        elif state["expected_record_present_in_pre_final_candidate_state"]:
            state["final_status"] = "lost_between_pre_final_and_final_output"
        elif state["expected_record_present_before_reservoir_retention"]:
            state["final_status"] = "dropped_before_final_candidates"
        elif state["deduped_before_scoring"]:
            state["final_status"] = "deduped_before_scoring"
        elif state["would_pass_should_consider"] is False:
            state["final_status"] = "rejected_by_should_consider"
        elif state["expected_record_line_reached_before_elapsed_stop"]:
            state["final_status"] = "line_reached_but_not_scored"


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
        public_refs.append(
            {
                "ref_id": state["ref_id"],
                "expected_file_selected_by_source_plan": bool(state["expected_file_selected_by_source_plan"]),
                "expected_file_queued_for_scan": bool(state["expected_file_queued_for_scan"]),
                "expected_file_opened_before_scan_stop": bool(state["expected_file_opened_before_scan_stop"]),
                "expected_record_line_reached_before_elapsed_stop": bool(
                    state["expected_record_line_reached_before_elapsed_stop"]
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
        "refs": public_refs,
    }


def order_planned_files_for_scan(planned_files: list[tuple], intent: QueryIntent) -> list[tuple]:
    return sorted(
        planned_files,
        key=lambda item: (
            -int(stat_mtime(item[0])),
            -filename_preference_score(item[0], intent.base_terms),
            item[1],
            str(item[0]),
        ),
    )


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
    if record.record_id:
        return f"record_id:{source_format}:{record.conversation_id or ''}:{record.record_id}"
    return f"source_ref:{source_format}:{record.path}:{record.line}"


def match_sort_key(match: Match) -> tuple[float, str, str, int]:
    return (match.score, match.timestamp or "", match.path, -match.line)
