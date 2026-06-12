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


SCAN_RECORD_QUANTUM = 512
SCAN_ORDER_STRATEGY = "newest_mtime_desc_filename_query_preference_size_asc_path_asc_record_quantum_round_robin"
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

    for enumerated in enumerate_jsonl_files(plan, preferred_terms=intent.base_terms):
        path, size, enum_warnings = enumerated
        warnings = enum_warnings
        planned_files.append((path, size))

    planned_files = order_planned_files_for_scan(planned_files, intent)
    planned_files_count = len(planned_files)

    if use_rg_prefilter:
        prefilter_result = filter_planned_files(plan=plan, intent=intent, files=planned_files, cwd=cwd)
        prefilter = prefilter_result.stats
        planned_files = [(item.path, item.size) for item in prefilter_result.files]
        planned_files = order_planned_files_for_scan(planned_files, intent)

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
                if not should_consider(record, intent):
                    records_rejected_by_should_consider += 1
                    continue
                key = dedupe_key_for_record(record)
                if key in dedupe:
                    candidate_records_deduped += 1
                    continue
                dedupe.add(key)
                candidate_records_scored += 1
                score, reasons, artifacts = score_record(record, intent, cwd)
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
    matches = matches + weak_matches
    if len(matches) > plan.limits.max_matches:
        warnings.append("Final candidates were capped at max match cap after ranking.")
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
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
        "groups": group_matches(matches),
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(blind_spots)),
    }
    if prefilter is not None:
        payload["prefilter"] = prefilter
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload


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
