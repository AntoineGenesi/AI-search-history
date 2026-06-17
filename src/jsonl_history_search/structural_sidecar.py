from __future__ import annotations

import hashlib
import re
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError
from .clustering import facets_for_record, session_key
from .metadata_seek import _dedupe_key, _elapsed_ms, _elapsed_ms_float
from .query import QueryIntent
from .ranking import (
    Match,
    group_matches,
    is_weak_partial_generic,
    make_snippet,
    score_record,
    should_consider,
    term_tokens,
)
from .records import Record, parse_record
from .scanner import match_sort_key
from .scanner import (
    _init_expected_ref_diagnostics,
    _mark_expected_final_candidates,
    _mark_expected_pre_final_candidates,
    _mark_opened_file_progress,
    _mark_opened_file_record_read,
    _public_expected_ref_diagnostics,
    _records_read_bucket,
)
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_mtime, stat_size
from .tool_events import detect_events_for_record


WORD_RE = re.compile(r"[a-z0-9]+")
STRUCTURAL_SIDECAR_VERSION = "structural-codex-claude-line-v1"
TEXT_PROBE_LIMIT = 4000
MAX_CANDIDATE_FILES = 12
MAX_ENTRIES_PER_FILE = 80
GLOBAL_STRATIFIED_ENTRY_SAMPLE_COUNT = 20


@dataclass(frozen=True)
class ScheduledStructuralEntry:
    table: StructuralFileTable
    entry: StructuralLineEntry
    candidate_file_rank: int
    candidate_entry_rank: int
    read_rank: int


@dataclass(frozen=True)
class StructuralLineEntry:
    line: int
    byte_start: int
    byte_length: int
    source_kind: str
    role_bucket: str
    message_type: str
    timestamp_bucket: str
    cwd_hash: str | None
    session_hash: str
    event_families: frozenset[str]
    event_types: frozenset[str]
    structural_tags: frozenset[str]
    lexical_terms: frozenset[str]


@dataclass
class StructuralFileTable:
    path: Path
    source_kind: str
    size: int
    mtime: float
    line_count: int
    entries: list[StructuralLineEntry] = field(default_factory=list)
    invalid_reason: str | None = None


@dataclass
class StructuralSidecarIndex:
    files: dict[str, StructuralFileTable]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    version: str = STRUCTURAL_SIDECAR_VERSION

    def sanitized_summary(self) -> dict[str, Any]:
        tag_counts: dict[str, int] = {}
        event_family_counts: dict[str, int] = {}
        source_kinds = sorted({table.source_kind for table in self.files.values()})
        for table in self.files.values():
            for entry in table.entries:
                for tag in entry.structural_tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                for family in entry.event_families:
                    event_family_counts[family] = event_family_counts.get(family, 0) + 1
        return {
            "version": self.version,
            "indexed_file_count": len(self.files),
            "indexed_entry_count": int(self.metrics.get("indexed_entries", 0)),
            "source_kinds": source_kinds,
            "role_bucket_counts": dict(sorted(_metric_counts(self.files, "role_bucket").items())),
            "message_type_counts": dict(sorted(_metric_counts(self.files, "message_type").items())),
            "event_family_counts": dict(sorted(event_family_counts.items())),
            "structural_tag_counts": dict(sorted(tag_counts.items())),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "vectors_or_indexes_included": False,
        }


def build_structural_sidecar_index(
    plan: SourcePlan,
    *,
    default_tz,
) -> StructuralSidecarIndex:
    started = time.perf_counter()
    files: dict[str, StructuralFileTable] = {}
    warnings: list[str] = []
    metrics: dict[str, Any] = {
        "build_bytes": 0,
        "build_elapsed_ms": 0.0,
        "indexed_files": 0,
        "indexed_entries": 0,
        "timestamped_records": 0,
        "structural_tag_count": 0,
        "event_tagged_records": 0,
        "read_error_files": 0,
        "non_line_oriented_files": 0,
    }

    for path, _size, enum_warnings in enumerate_jsonl_files(plan):
        warnings.extend(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        if path.suffix != ".jsonl":
            metrics["non_line_oriented_files"] += 1
            files[str(path)] = StructuralFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                invalid_reason="non_line_oriented_source",
            )
            continue

        table, bytes_read = _build_file_table(path, source_kind=source_kind, default_tz=default_tz)
        files[str(path)] = table
        metrics["build_bytes"] += bytes_read
        metrics["indexed_files"] += 1
        metrics["indexed_entries"] += len(table.entries)
        metrics["timestamped_records"] += sum(1 for entry in table.entries if entry.timestamp_bucket != "timestamp_absent")
        metrics["structural_tag_count"] += sum(len(entry.structural_tags) for entry in table.entries)
        metrics["event_tagged_records"] += sum(1 for entry in table.entries if entry.event_families)
        if table.invalid_reason == "read_error":
            metrics["read_error_files"] += 1

    metrics["build_elapsed_ms"] = _elapsed_ms_float(started)
    return StructuralSidecarIndex(files=files, metrics=metrics, warnings=sorted(set(warnings)))


def search_with_structural_sidecar(
    index: StructuralSidecarIndex,
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None,
    selected_paths: set[str] | None = None,
    expected_ref_diagnostics: list[dict] | None = None,
    candidate_selection_profile: str = "default",
    record_window_radius: int = 0,
    record_window_max_extra_entries_per_file: int = 0,
    record_window_max_extra_entries_total: int = 0,
) -> dict[str, Any]:
    started = time.perf_counter()
    warnings = list(index.warnings) + list(plan.warnings)
    query_profile = _query_profile(intent)
    normalized_candidate_selection_profile = _candidate_selection_profile(candidate_selection_profile)
    seed_candidate_entries = _candidate_entries(
        index,
        query_profile,
        selected_paths=selected_paths,
        candidate_selection_profile=normalized_candidate_selection_profile,
    )
    candidate_entries, record_window_stats = _expand_record_window_entries(
        seed_candidate_entries,
        radius=record_window_radius,
        max_extra_entries_per_file=record_window_max_extra_entries_per_file,
        max_extra_entries_total=record_window_max_extra_entries_total,
    )
    candidate_read_schedule = _candidate_read_schedule(
        candidate_entries,
        query_profile,
        candidate_selection_profile=normalized_candidate_selection_profile,
    )
    candidate_read_schedule_profile = _candidate_read_schedule_profile(normalized_candidate_selection_profile)
    matches: list[Match] = []
    weak_matches: list[Match] = []
    weak_reservoir_limit = max(1, min(plan.limits.max_matches, max(3, plan.limits.max_matches // 4)))
    weak_candidates_skipped = 0
    dedupe: set[tuple[str, str, str, str]] = set()
    opened_paths: set[str] = set()
    searched_bytes = 0
    records_considered = 0
    records_rejected_by_should_consider = 0
    candidate_records_scored = 0
    candidate_records_deduped = 0
    source_kinds: set[str] = set()
    expected_diag = _init_structural_expected_ref_diagnostics(expected_ref_diagnostics)
    _mark_structural_expected_indexed(expected_diag, index)
    _mark_structural_expected_candidates(
        expected_diag,
        index,
        query_profile,
        candidate_entries,
        candidate_read_schedule=candidate_read_schedule,
        candidate_selection_profile=normalized_candidate_selection_profile,
    )

    for table, _entries in candidate_entries:
        fallback_reason = _table_fallback_reason(table)
        if fallback_reason:
            return _fallback_payload(
                started,
                reason=fallback_reason,
                warnings=warnings,
                index=index,
                query_profile=query_profile,
                record_window_stats=record_window_stats,
                candidate_selection_profile=normalized_candidate_selection_profile,
                candidate_read_schedule_profile=candidate_read_schedule_profile,
            )
    opened_handles: dict[str, Any] = {}
    progress_by_path: dict[str, dict[str, Any]] = {}
    with ExitStack() as stack:
        for scheduled in candidate_read_schedule:
            table = scheduled.table
            entry = scheduled.entry
            if time.perf_counter() - started > plan.limits.max_elapsed:
                warnings.append("Stopped structural sidecar at max elapsed cap.")
                break
            try:
                path_key = str(table.path)
                handle = opened_handles.get(path_key)
                if handle is None:
                    handle = stack.enter_context(table.path.open("rb"))
                    opened_handles[path_key] = handle
                    opened_order = len(opened_paths) + 1
                    opened_paths.add(path_key)
                    source_kinds.add(table.source_kind)
                    progress_item = {"path": table.path}
                    progress_by_path[path_key] = progress_item
                    _mark_opened_file_progress(expected_diag, progress_item, table.path, opened_order)
                else:
                    progress_item = progress_by_path[path_key]
                handle.seek(entry.byte_start)
                raw = handle.read(entry.byte_length)
            except OSError as exc:
                raise AdapterReadError(f"Could not open {table.path}: {exc}") from exc
            searched_bytes += len(raw)
            records_considered += 1
            _mark_opened_file_record_read(expected_diag, progress_item)
            record = parse_record(
                raw.decode("utf-8", errors="replace"),
                path=table.path,
                line=entry.line,
                default_tz=default_tz,
            )
            record.source_format = entry.source_kind
            expected_state = _structural_expected_state_for_entry(expected_diag, table.path, entry.line)
            if expected_state is not None:
                expected_state["expected_record_line_reached_before_elapsed_stop"] = True
                expected_state["expected_file_records_read_before_stop"] = int(progress_item.get("records_read") or 0)
                expected_state["expected_file_progress_bucket"] = _records_read_bucket(
                    int(progress_item.get("records_read") or 0)
                )
            if not should_consider(record, intent):
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
            key = _dedupe_key(record)
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
            score, reasons, artifacts = score_record(record, intent, cwd)
            if expected_state is not None:
                expected_state["expected_record_present_before_reservoir_retention"] = True
                expected_state["pre_reservoir_status"] = "present_before_reservoir_retention"
                expected_state["dedupe_phase_label"] = "not_deduped"
                expected_state["score_label_count"] = len(reasons)
                expected_state["artifact_label_count"] = len(artifacts)
            match = _materialize_match(record, score, reasons, artifacts, intent)
            if is_weak_partial_generic(reasons):
                if expected_state is not None:
                    expected_state["candidate_strength"] = "weak"
                if len(weak_matches) < weak_reservoir_limit:
                    weak_matches.append(match)
                    if expected_state is not None:
                        expected_state["reservoir_phase_label"] = "weak_reservoir_retained"
                else:
                    weakest_index = min(
                        range(len(weak_matches)),
                        key=lambda index: match_sort_key(weak_matches[index]),
                    )
                    if match_sort_key(match) > match_sort_key(weak_matches[weakest_index]):
                        weak_matches[weakest_index] = match
                        if expected_state is not None:
                            expected_state["reservoir_phase_label"] = "weak_reservoir_replaced_existing_candidate"
                    elif expected_state is not None:
                        expected_state["reservoir_phase_label"] = "weak_reservoir_cap_drop"
                        expected_state["drop_phase_label"] = "weak_reservoir_drop"
                    weak_candidates_skipped += 1
                continue
            if expected_state is not None:
                expected_state["candidate_strength"] = "strong"
                expected_state["reservoir_phase_label"] = "strong_reservoir_retained"
            matches.append(match)
            if len(matches) >= plan.limits.max_matches:
                warnings.append("Stopped structural sidecar at max match cap.")
                break

    if weak_candidates_skipped:
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    matches = (matches + weak_matches)[: plan.limits.max_matches * 2]
    _mark_structural_expected_pre_sort_rank(expected_diag, matches)
    _mark_expected_pre_final_candidates(expected_diag, matches)
    matches.sort(key=match_sort_key, reverse=True)
    _mark_structural_expected_final_rank(expected_diag, matches, max_matches=plan.limits.max_matches)
    matches = matches[: plan.limits.max_matches]
    _mark_expected_final_candidates(expected_diag, matches)
    candidate_entry_count = sum(len(entries) for _table, entries in candidate_entries)
    payload = {
        "status": "planned_structural_seek",
        "fallback_reason": None,
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_selection_profile": normalized_candidate_selection_profile,
        "candidate_files": len(candidate_entries),
        "candidate_entries": candidate_entry_count,
        "candidate_read_schedule_profile": candidate_read_schedule_profile,
        "candidate_read_schedule_entry_count": len(candidate_read_schedule),
        "candidate_read_schedule_preview": _candidate_read_schedule_preview(candidate_read_schedule),
        "searched_files": len(opened_paths),
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "records_rejected_by_should_consider": records_rejected_by_should_consider,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
        **record_window_stats,
        "groups": group_matches(matches),
        "summary": _search_summary(
            index=index,
            query_profile=query_profile,
            candidate_file_count=len(candidate_entries),
            candidate_entry_count=candidate_entry_count,
            opened_file_count=len(opened_paths),
            searched_bytes=searched_bytes,
            records_considered=records_considered,
            source_kinds=source_kinds,
            record_window_stats=record_window_stats,
            candidate_selection_profile=normalized_candidate_selection_profile,
            candidate_read_schedule_profile=candidate_read_schedule_profile,
        ),
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }
    if expected_diag is not None:
        payload["expected_ref_diagnostics"] = _public_structural_expected_ref_diagnostics(expected_diag)
    return payload


def _init_structural_expected_ref_diagnostics(expected_refs: list[dict] | None) -> dict | None:
    expected_diag = _init_expected_ref_diagnostics(expected_refs)
    if expected_diag is None:
        return None
    for state in expected_diag["refs"]:
        state["expected_file_indexed_by_structural_sidecar"] = False
        state["expected_record_indexed_by_structural_sidecar"] = False
        state["expected_file_selected_as_candidate_file"] = False
        state["expected_record_selected_as_candidate_entry"] = False
        state["structural_candidate_status"] = "not_selected"
        state["candidate_file_rank_label"] = None
        state["candidate_file_entry_count_bucket"] = None
        state["candidate_entry_rank_label"] = None
        state["candidate_entry_rank_bucket"] = None
        state["candidate_entry_selection_reason"] = None
        state["candidate_entry_score_profile"] = None
        state["candidate_entry_selection_limit"] = MAX_ENTRIES_PER_FILE
        state["candidate_file_selection_limit"] = MAX_CANDIDATE_FILES
        state["candidate_file_truncation_label"] = "not_evaluated"
        state["candidate_entry_truncation_label"] = "not_evaluated"
        state["candidate_entry_read_rank_label"] = None
        state["candidate_entry_read_rank_bucket"] = None
        state["candidate_entry_read_schedule_profile"] = None
        state["score_label_count"] = None
        state["artifact_label_count"] = None
        state["pre_final_rank_label"] = None
        state["pre_final_rank_bucket"] = None
        state["final_rank_label"] = None
        state["final_rank_bucket"] = None
    return expected_diag


def _mark_structural_expected_indexed(expected_diag: dict | None, index: StructuralSidecarIndex) -> None:
    if expected_diag is None:
        return
    for state in expected_diag["refs"]:
        table = index.files.get(state["_path"])
        state["expected_file_indexed_by_structural_sidecar"] = table is not None and not table.invalid_reason
        state["expected_record_indexed_by_structural_sidecar"] = bool(
            table is not None and any(entry.line == state["_line"] for entry in table.entries)
        )


def _mark_structural_expected_candidates(
    expected_diag: dict | None,
    index: StructuralSidecarIndex,
    query_profile: dict[str, Any],
    candidate_entries: list[tuple[StructuralFileTable, list[StructuralLineEntry]]],
    *,
    candidate_read_schedule: list[ScheduledStructuralEntry],
    candidate_selection_profile: str,
) -> None:
    if expected_diag is None:
        return
    candidate_file_paths: dict[str, tuple[int, int]] = {}
    candidate_record_keys: dict[tuple[str, int], tuple[int, int]] = {}
    for file_rank, (table, entries) in enumerate(candidate_entries, start=1):
        candidate_file_paths[str(table.path)] = (file_rank, len(entries))
        for entry_rank, entry in enumerate(entries, start=1):
            candidate_record_keys[(str(table.path), entry.line)] = (file_rank, entry_rank)
    read_schedule_by_key = {
        (str(scheduled.table.path), scheduled.entry.line): scheduled for scheduled in candidate_read_schedule
    }
    read_schedule_profile = _candidate_read_schedule_profile(candidate_selection_profile)
    for state in expected_diag["refs"]:
        path = state["_path"]
        key = (path, state["_line"])
        table = index.files.get(path)
        candidate_file_rank = candidate_file_paths.get(path)
        candidate_entry_rank = candidate_record_keys.get(key)
        state["expected_file_selected_by_source_plan"] = bool(state["expected_file_indexed_by_structural_sidecar"])
        state["expected_file_queued_for_scan"] = path in candidate_file_paths
        state["expected_file_selected_as_candidate_file"] = path in candidate_file_paths
        state["expected_record_selected_as_candidate_entry"] = key in candidate_record_keys
        if candidate_file_rank is not None:
            rank, entry_count = candidate_file_rank
            state["candidate_file_rank_label"] = _rank_label("candidate_file", rank)
            state["candidate_file_entry_count_bucket"] = _count_bucket(entry_count)
            state["candidate_file_truncation_label"] = "selected_within_file_limit"
        elif state["expected_file_indexed_by_structural_sidecar"]:
            state["candidate_file_truncation_label"] = "not_selected_by_candidate_file_ranking"
        if candidate_entry_rank is not None:
            _file_rank, rank = candidate_entry_rank
            state["candidate_entry_rank_label"] = _rank_label("candidate_entry", rank)
            state["candidate_entry_rank_bucket"] = _rank_bucket(rank)
            state["candidate_entry_selection_reason"] = "selected_within_entry_limit"
            state["candidate_entry_truncation_label"] = "selected_within_entry_limit"
            scheduled = read_schedule_by_key.get(key)
            if scheduled is not None:
                state["candidate_entry_read_rank_label"] = _rank_label("candidate_entry_read", scheduled.read_rank)
                state["candidate_entry_read_rank_bucket"] = _rank_bucket(scheduled.read_rank)
                state["candidate_entry_read_schedule_profile"] = read_schedule_profile
        elif table is not None:
            entry = next((item for item in table.entries if item.line == state["_line"]), None)
            if entry is not None:
                reason, profile = _candidate_entry_nonselection_reason(
                    table,
                    entry,
                    query_profile,
                    candidate_selection_profile=candidate_selection_profile,
                )
                state["candidate_entry_selection_reason"] = reason
                state["candidate_entry_score_profile"] = profile
                state["candidate_entry_truncation_label"] = reason
        if key in candidate_record_keys:
            state["structural_candidate_status"] = "expected_record_selected_as_candidate_entry"
        elif path in candidate_file_paths:
            state["structural_candidate_status"] = "expected_file_selected_but_record_not_candidate"
        elif state["expected_record_indexed_by_structural_sidecar"]:
            state["structural_candidate_status"] = "expected_record_indexed_but_not_candidate"
        elif state["expected_file_indexed_by_structural_sidecar"]:
            state["structural_candidate_status"] = "expected_file_indexed_but_record_not_indexed"
        else:
            state["structural_candidate_status"] = "expected_file_not_indexed"


def _structural_expected_state_for_entry(expected_diag: dict | None, path: Path, line: int) -> dict | None:
    if expected_diag is None:
        return None
    return expected_diag["by_ref"].get((str(path), int(line)))


def _mark_structural_expected_pre_sort_rank(expected_diag: dict | None, candidates: list[Match]) -> None:
    if expected_diag is None:
        return
    candidate_ranks = {(str(match.path), int(match.line)): rank for rank, match in enumerate(candidates, start=1)}
    for state in expected_diag["refs"]:
        rank = candidate_ranks.get((state["_path"], state["_line"]))
        if rank is None:
            continue
        state["pre_final_rank_label"] = _rank_label("pre_final_candidate", rank)
        state["pre_final_rank_bucket"] = _rank_bucket(rank)


def _mark_structural_expected_final_rank(
    expected_diag: dict | None,
    sorted_candidates: list[Match],
    *,
    max_matches: int,
) -> None:
    if expected_diag is None:
        return
    candidate_ranks = {
        (str(match.path), int(match.line)): rank for rank, match in enumerate(sorted_candidates, start=1)
    }
    for state in expected_diag["refs"]:
        rank = candidate_ranks.get((state["_path"], state["_line"]))
        if rank is None:
            continue
        state["final_rank_label"] = _rank_label("final_candidate", rank)
        state["final_rank_bucket"] = _rank_bucket(rank)
        if rank > max_matches:
            state["final_cap_phase_label"] = "final_cap_drop"
            state["drop_phase_label"] = state["drop_phase_label"] or "final_cap_drop"


def _public_structural_expected_ref_diagnostics(expected_diag: dict) -> dict:
    public = _public_expected_ref_diagnostics(expected_diag)
    indexed_count = 0
    candidate_file_count = 0
    candidate_entry_count = 0
    by_ref_id = {state["ref_id"]: state for state in expected_diag["refs"]}
    for ref in public["refs"]:
        state = by_ref_id.get(ref["ref_id"]) or {}
        ref["expected_file_indexed_by_structural_sidecar"] = bool(
            state.get("expected_file_indexed_by_structural_sidecar")
        )
        ref["expected_record_indexed_by_structural_sidecar"] = bool(
            state.get("expected_record_indexed_by_structural_sidecar")
        )
        ref["expected_file_selected_as_candidate_file"] = bool(
            state.get("expected_file_selected_as_candidate_file")
        )
        ref["expected_record_selected_as_candidate_entry"] = bool(
            state.get("expected_record_selected_as_candidate_entry")
        )
        ref["structural_candidate_status"] = str(state.get("structural_candidate_status") or "unknown")
        ref["candidate_file_rank_label"] = state.get("candidate_file_rank_label")
        ref["candidate_file_entry_count_bucket"] = state.get("candidate_file_entry_count_bucket")
        ref["candidate_entry_rank_label"] = state.get("candidate_entry_rank_label")
        ref["candidate_entry_rank_bucket"] = state.get("candidate_entry_rank_bucket")
        ref["candidate_entry_selection_reason"] = state.get("candidate_entry_selection_reason")
        ref["candidate_entry_score_profile"] = state.get("candidate_entry_score_profile")
        ref["candidate_entry_selection_limit"] = int(state.get("candidate_entry_selection_limit") or 0)
        ref["candidate_file_selection_limit"] = int(state.get("candidate_file_selection_limit") or 0)
        ref["candidate_file_truncation_label"] = str(state.get("candidate_file_truncation_label") or "unknown")
        ref["candidate_entry_truncation_label"] = str(state.get("candidate_entry_truncation_label") or "unknown")
        ref["candidate_entry_read_rank_label"] = state.get("candidate_entry_read_rank_label")
        ref["candidate_entry_read_rank_bucket"] = state.get("candidate_entry_read_rank_bucket")
        ref["candidate_entry_read_schedule_profile"] = state.get("candidate_entry_read_schedule_profile")
        ref["score_label_count"] = state.get("score_label_count")
        ref["artifact_label_count"] = state.get("artifact_label_count")
        ref["pre_final_rank_label"] = state.get("pre_final_rank_label")
        ref["pre_final_rank_bucket"] = state.get("pre_final_rank_bucket")
        ref["final_rank_label"] = state.get("final_rank_label")
        ref["final_rank_bucket"] = state.get("final_rank_bucket")
        indexed_count += int(ref["expected_record_indexed_by_structural_sidecar"])
        candidate_file_count += int(ref["expected_file_selected_as_candidate_file"])
        candidate_entry_count += int(ref["expected_record_selected_as_candidate_entry"])
    public.update(
        {
            "structural_sidecar_enabled": True,
            "indexed_expected_record_count": indexed_count,
            "candidate_file_expected_ref_count": candidate_file_count,
            "candidate_entry_expected_ref_count": candidate_entry_count,
            "raw_paths_included": False,
            "line_numbers_included": False,
        }
    )
    return public


def _candidate_entry_nonselection_reason(
    table: StructuralFileTable,
    expected_entry: StructuralLineEntry,
    query_profile: dict[str, Any],
    *,
    candidate_selection_profile: str,
) -> tuple[str, dict[str, Any]]:
    query_terms: set[str] = set(query_profile["lexical_terms"])
    query_tags: set[str] = set(query_profile["structural_tags"])
    query_event_families: set[str] = set(query_profile["event_families"])
    entry_score = _candidate_entry_base_score(
        expected_entry,
        query_terms=query_terms,
        query_tags=query_tags,
        query_event_families=query_event_families,
        query_profile=query_profile,
        candidate_selection_profile=candidate_selection_profile,
        diversity_bonus=1,
    )
    profile = _safe_candidate_entry_score_profile(expected_entry, entry_score)
    if entry_score[0] == 0 and entry_score[1] == 0:
        return "no_candidate_signal_for_entry", profile

    scored_entries: list[tuple[tuple[int, int, int, int], StructuralLineEntry]] = []
    session_seen: set[str] = set()
    for entry in table.entries:
        diversity_bonus = 0 if entry.session_hash in session_seen else 1
        session_seen.add(entry.session_hash)
        score = _candidate_entry_base_score(
            entry,
            query_terms=query_terms,
            query_tags=query_tags,
            query_event_families=query_event_families,
            query_profile=query_profile,
            candidate_selection_profile=candidate_selection_profile,
            diversity_bonus=diversity_bonus,
        )
        if score[0] or score[1]:
            scored_entries.append((score, entry))
    ordered = _ordered_scored_entries_for_profile(
        scored_entries,
        query_profile,
        candidate_selection_profile=candidate_selection_profile,
    )
    rank = next((index for index, (_score, entry) in enumerate(ordered, start=1) if entry.line == expected_entry.line), None)
    if rank is None:
        return "candidate_entry_rank_not_reconstructed", profile
    profile = {**profile, "reconstructed_entry_rank_bucket": _rank_bucket(rank)}
    if _uses_global_stratified_candidate_selection(candidate_selection_profile) and _use_intent_structural_order(
        query_profile
    ):
        selected = _select_global_stratified_scored_entries(
            ordered,
            limit=max(1, min(MAX_ENTRIES_PER_FILE, len(ordered))),
        )
        if not any(entry.line == expected_entry.line for _score, entry in selected):
            return "global_stratified_entry_limit_drop", profile
    if rank > MAX_ENTRIES_PER_FILE:
        return "per_file_entry_limit_drop", profile
    return "not_selected_after_candidate_file_limit_or_tie_order", profile


def _candidate_entry_base_score(
    entry: StructuralLineEntry,
    *,
    query_terms: set[str],
    query_tags: set[str],
    query_event_families: set[str],
    query_profile: dict[str, Any],
    candidate_selection_profile: str,
    diversity_bonus: int,
) -> tuple[int, int, int, int]:
    lexical_overlap = len(entry.lexical_terms & query_terms)
    structural_overlap = _structural_overlap(
        entry,
        query_tags,
        query_profile,
        candidate_selection_profile=candidate_selection_profile,
    )
    event_bonus = len(entry.event_families & query_event_families)
    return (structural_overlap + event_bonus, lexical_overlap, diversity_bonus, -entry.line)


def _ordered_scored_entries_for_profile(
    scored_entries: list[tuple[tuple[int, int, int, int], StructuralLineEntry]],
    query_profile: dict[str, Any],
    *,
    candidate_selection_profile: str,
) -> list[tuple[tuple[int, int, int, int], StructuralLineEntry]]:
    ordered = list(scored_entries)
    if not _is_intent_structural_profile(candidate_selection_profile) or not _use_intent_structural_order(query_profile):
        ordered.sort(key=lambda item: item[0], reverse=True)
        return ordered

    query_tags = set(query_profile["structural_tags"])
    prefer_timestamp = bool(query_profile.get("date_reason")) or bool(query_profile.get("date_window_present"))
    preferred_role_tag = f"role:{_safe_tag(str(query_profile.get('role') or ''))}" if query_profile.get("role") else None

    def sort_key(item: tuple[tuple[int, int, int, int], StructuralLineEntry]) -> tuple[int, int, int, int, int, int]:
        base_score, entry = item
        timestamp_match = int(prefer_timestamp and entry.timestamp_bucket == "timestamp_present")
        role_match = int(bool(preferred_role_tag and preferred_role_tag in entry.structural_tags))
        structural_overlap = len(entry.structural_tags & query_tags)
        return (
            timestamp_match,
            role_match,
            structural_overlap + base_score[0],
            base_score[1],
            base_score[2],
            entry.line,
        )

    ordered.sort(key=sort_key, reverse=True)
    return ordered


def _safe_candidate_entry_score_profile(
    entry: StructuralLineEntry,
    score: tuple[int, int, int, int],
) -> dict[str, Any]:
    return {
        "structural_event_score": int(score[0]),
        "lexical_overlap_count": int(score[1]),
        "diversity_bonus": int(score[2]),
        "timestamp_bucket": entry.timestamp_bucket,
        "role_bucket": entry.role_bucket,
        "message_type": entry.message_type,
        "event_family_count": len(entry.event_families),
        "structural_tag_count": len(entry.structural_tags),
        "raw_text_included": False,
        "raw_paths_included": False,
        "line_numbers_included": False,
    }


def _rank_label(prefix: str, rank: int) -> str:
    return f"{prefix}_{rank:03d}"


def _rank_bucket(rank: int | None) -> str | None:
    if rank is None:
        return None
    if rank <= 10:
        return "001-010"
    if rank <= 40:
        return "011-040"
    if rank <= 80:
        return "041-080"
    if rank <= 160:
        return "081-160"
    return "161_plus"


def _count_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count <= 10:
        return "1-10"
    if count <= 40:
        return "11-40"
    if count <= 80:
        return "41-80"
    return "81_plus"


def _build_file_table(path: Path, *, source_kind: str, default_tz) -> tuple[StructuralFileTable, int]:
    entries: list[StructuralLineEntry] = []
    bytes_read = 0
    try:
        with path.open("rb") as handle:
            offset = 0
            for line_index, raw in enumerate(handle, 1):
                line_start = offset
                offset += len(raw)
                bytes_read += len(raw)
                record = parse_record(raw.decode("utf-8", errors="replace"), path=path, line=line_index, default_tz=default_tz)
                record.source_format = source_kind
                entries.append(_entry_for_record(record, raw=raw, line_start=line_start, source_kind=source_kind))
    except OSError:
        return (
            StructuralFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                invalid_reason="read_error",
            ),
            bytes_read,
        )
    return (
        StructuralFileTable(
            path=path,
            source_kind=source_kind,
            size=stat_size(path),
            mtime=stat_mtime(path),
            line_count=len(entries),
            entries=entries,
        ),
        bytes_read,
    )


def _entry_for_record(record: Record, *, raw: bytes, line_start: int, source_kind: str) -> StructuralLineEntry:
    event_families: set[str] = set()
    event_types: set[str] = set()
    if _record_may_have_structured_event(record):
        for event in detect_events_for_record(record, mask_context=None):
            event_families.add(_safe_tag(event.event_family))
            event_types.add(_safe_tag(event.event_type))
    role_bucket = _role_bucket(record.role)
    message_type = _message_type(record)
    timestamp_bucket = _timestamp_bucket(record)
    structural_tags = {
        f"source:{_safe_tag(source_kind)}",
        f"role:{role_bucket}",
        f"message:{message_type}",
        f"time:{timestamp_bucket}",
    }
    structural_tags.update(f"event_family:{family}" for family in event_families)
    structural_tags.update(f"event_type:{event_type}" for event_type in event_types)
    structural_tags.update(_artifact_hint_tags(record.text))
    cwd_hash = _hash_public(record.cwd) if record.cwd else None
    session_hash = _hash_public(session_key(record.path))
    if cwd_hash:
        structural_tags.add("cwd:present")
    return StructuralLineEntry(
        line=record.line,
        byte_start=line_start,
        byte_length=len(raw),
        source_kind=source_kind,
        role_bucket=role_bucket,
        message_type=message_type,
        timestamp_bucket=timestamp_bucket,
        cwd_hash=cwd_hash,
        session_hash=session_hash,
        event_families=frozenset(event_families),
        event_types=frozenset(event_types),
        structural_tags=frozenset(structural_tags),
        lexical_terms=frozenset(_lexical_terms(record.text[:TEXT_PROBE_LIMIT])),
    )


def _candidate_entries(
    index: StructuralSidecarIndex,
    query_profile: dict[str, Any],
    *,
    selected_paths: set[str] | None,
    candidate_selection_profile: str = "default",
) -> list[tuple[StructuralFileTable, list[StructuralLineEntry]]]:
    candidates: list[tuple[StructuralFileTable, list[tuple[tuple[int, int, int, int], StructuralLineEntry]]]] = []
    query_terms: set[str] = set(query_profile["lexical_terms"])
    query_tags: set[str] = set(query_profile["structural_tags"])
    for path, table in index.files.items():
        if selected_paths is not None and path not in selected_paths:
            continue
        scored_entries: list[tuple[tuple[int, int, int, int], StructuralLineEntry]] = []
        session_seen: set[str] = set()
        for entry in table.entries:
            lexical_overlap = len(entry.lexical_terms & query_terms)
            structural_overlap = _structural_overlap(
                entry,
                query_tags,
                query_profile,
                candidate_selection_profile=candidate_selection_profile,
            )
            event_bonus = len(entry.event_families & set(query_profile["event_families"]))
            if not lexical_overlap and not structural_overlap and not event_bonus:
                continue
            diversity_bonus = 0 if entry.session_hash in session_seen else 1
            session_seen.add(entry.session_hash)
            score = (structural_overlap + event_bonus, lexical_overlap, diversity_bonus, -entry.line)
            scored_entries.append((score, entry))
        if scored_entries:
            selected_entries = _select_scored_entries(
                scored_entries,
                query_profile,
                candidate_selection_profile=candidate_selection_profile,
            )
            candidates.append((table, selected_entries))
    candidates.sort(key=lambda item: (item[1][0][0], len(item[1])), reverse=True)
    candidates = candidates[:MAX_CANDIDATE_FILES]
    return [(table, [entry for _score, entry in entries]) for table, entries in candidates]


def _select_scored_entries(
    scored_entries: list[tuple[tuple[int, int, int, int], StructuralLineEntry]],
    query_profile: dict[str, Any],
    *,
    candidate_selection_profile: str,
) -> list[tuple[tuple[int, int, int, int], StructuralLineEntry]]:
    limit = max(1, min(MAX_ENTRIES_PER_FILE, len(scored_entries)))
    ordered = _ordered_scored_entries_for_profile(
        scored_entries,
        query_profile,
        candidate_selection_profile=candidate_selection_profile,
    )
    if _uses_global_stratified_candidate_selection(candidate_selection_profile) and _use_intent_structural_order(
        query_profile
    ):
        return _select_global_stratified_scored_entries(ordered, limit=limit)
    return ordered[:limit]


def _select_global_stratified_scored_entries(
    ordered_entries: list[tuple[tuple[int, int, int, int], StructuralLineEntry]],
    *,
    limit: int,
) -> list[tuple[tuple[int, int, int, int], StructuralLineEntry]]:
    if len(ordered_entries) <= limit:
        return ordered_entries[:limit]

    sample_count = min(GLOBAL_STRATIFIED_ENTRY_SAMPLE_COUNT, max(0, limit // 4), len(ordered_entries))
    top_count = max(1, limit - sample_count)
    selected = list(ordered_entries[:top_count])
    seen_lines = {entry.line for _score, entry in selected}
    if sample_count <= 0:
        return selected[:limit]

    rank_by_line = {entry.line: rank for rank, (_score, entry) in enumerate(ordered_entries)}
    line_ordered = sorted(ordered_entries, key=lambda item: item[1].line)
    total = len(line_ordered)
    for bucket_index in range(sample_count):
        start = bucket_index * total // sample_count
        end = (bucket_index + 1) * total // sample_count
        bucket = [item for item in line_ordered[start:end] if item[1].line not in seen_lines]
        if not bucket:
            continue
        chosen = min(bucket, key=lambda item: rank_by_line.get(item[1].line, total))
        selected.append(chosen)
        seen_lines.add(chosen[1].line)
        if len(selected) >= limit:
            return selected

    for item in ordered_entries:
        if item[1].line in seen_lines:
            continue
        selected.append(item)
        seen_lines.add(item[1].line)
        if len(selected) >= limit:
            break
    return selected


def _candidate_read_schedule(
    candidate_entries: list[tuple[StructuralFileTable, list[StructuralLineEntry]]],
    query_profile: dict[str, Any],
    *,
    candidate_selection_profile: str,
) -> list[ScheduledStructuralEntry]:
    scheduled_entries = []
    read_rank = 0
    for file_rank, (table, entries) in enumerate(candidate_entries, start=1):
        for entry_rank, entry in enumerate(entries, start=1):
            read_rank += 1
            scheduled_entries.append(
                ScheduledStructuralEntry(
                    table=table,
                    entry=entry,
                    candidate_file_rank=file_rank,
                    candidate_entry_rank=entry_rank,
                    read_rank=read_rank,
                )
            )
    if not _uses_global_candidate_read_schedule(candidate_selection_profile):
        return scheduled_entries

    ordered = list(scheduled_entries)
    ordered.sort(
        key=lambda scheduled: _global_candidate_read_sort_key(scheduled, query_profile),
        reverse=True,
    )
    return [
        ScheduledStructuralEntry(
            table=scheduled.table,
            entry=scheduled.entry,
            candidate_file_rank=scheduled.candidate_file_rank,
            candidate_entry_rank=scheduled.candidate_entry_rank,
            read_rank=read_rank,
        )
        for read_rank, scheduled in enumerate(ordered, start=1)
    ]


def _global_candidate_read_sort_key(
    scheduled: ScheduledStructuralEntry,
    query_profile: dict[str, Any],
) -> tuple[int, int, int, int, int, int, int, int]:
    entry = scheduled.entry
    query_terms = set(query_profile["lexical_terms"])
    query_tags = set(query_profile["structural_tags"])
    query_event_families = set(query_profile["event_families"])
    prefer_timestamp = bool(query_profile.get("date_reason")) or bool(query_profile.get("date_window_present"))
    preferred_role_tag = f"role:{_safe_tag(str(query_profile.get('role') or ''))}" if query_profile.get("role") else None
    base_score = _candidate_entry_base_score(
        entry,
        query_terms=query_terms,
        query_tags=query_tags,
        query_event_families=query_event_families,
        query_profile=query_profile,
        candidate_selection_profile="intent_structural_global",
        diversity_bonus=int(scheduled.candidate_entry_rank == 1),
    )
    timestamp_match = int(prefer_timestamp and entry.timestamp_bucket == "timestamp_present")
    role_match = int(bool(preferred_role_tag and preferred_role_tag in entry.structural_tags))
    structural_overlap = len(entry.structural_tags & query_tags)
    return (
        timestamp_match,
        role_match,
        structural_overlap + base_score[0],
        base_score[1],
        base_score[2],
        entry.line,
        -scheduled.candidate_file_rank,
        -scheduled.candidate_entry_rank,
    )


def _candidate_read_schedule_profile(candidate_selection_profile: str) -> str:
    if _uses_global_candidate_read_schedule(candidate_selection_profile):
        return "intent_structural_global"
    return "file_order"


def _candidate_read_schedule_preview(
    candidate_read_schedule: list[ScheduledStructuralEntry],
    *,
    limit: int = 20,
) -> list[dict[str, int]]:
    return [
        {
            "read_rank": scheduled.read_rank,
            "candidate_file_rank": scheduled.candidate_file_rank,
            "candidate_entry_rank": scheduled.candidate_entry_rank,
            "line_numbers_included": 0,
            "raw_paths_included": 0,
        }
        for scheduled in candidate_read_schedule[:limit]
    ]


def _candidate_selection_profile(value: str | None) -> str:
    if value in {"intent_structural", "intent_structural_global", "intent_structural_global_stratified"}:
        return value
    return "default"


def _is_intent_structural_profile(value: str) -> bool:
    return value in {"intent_structural", "intent_structural_global", "intent_structural_global_stratified"}


def _uses_global_candidate_read_schedule(value: str) -> bool:
    return value in {"intent_structural_global", "intent_structural_global_stratified"}


def _uses_global_stratified_candidate_selection(value: str) -> bool:
    return value == "intent_structural_global_stratified"


def _use_intent_structural_order(query_profile: dict[str, Any]) -> bool:
    if query_profile.get("date_reason") or query_profile.get("date_window_present"):
        return True
    return bool(query_profile.get("role"))


def _expand_record_window_entries(
    candidate_entries: list[tuple[StructuralFileTable, list[StructuralLineEntry]]],
    *,
    radius: int,
    max_extra_entries_per_file: int,
    max_extra_entries_total: int,
) -> tuple[list[tuple[StructuralFileTable, list[StructuralLineEntry]]], dict[str, Any]]:
    normalized_radius = max(0, int(radius or 0))
    per_file_cap = max(0, int(max_extra_entries_per_file or 0))
    total_cap = max(0, int(max_extra_entries_total or 0))
    seed_entry_count = sum(len(entries) for _table, entries in candidate_entries)
    stats = {
        "record_window_enabled": normalized_radius > 0,
        "record_window_radius": normalized_radius,
        "record_window_seed_entry_count": seed_entry_count,
        "record_window_extra_entry_count": 0,
        "record_window_entry_count": seed_entry_count,
        "record_window_max_extra_entries_per_file": per_file_cap,
        "record_window_max_extra_entries_total": total_cap,
        "record_window_extra_entries_capped": False,
        "oracle_inputs_used": False,
    }
    if normalized_radius <= 0:
        return candidate_entries, stats

    expanded: list[tuple[StructuralFileTable, list[StructuralLineEntry]]] = []
    total_extra_entries = 0
    for table, seed_entries in candidate_entries:
        seed_lines = {entry.line for entry in seed_entries}
        entry_index_by_line = {entry.line: index for index, entry in enumerate(table.entries)}
        file_entries: list[StructuralLineEntry] = []
        seen_lines: set[int] = set()
        file_extra_entries = 0
        for seed in seed_entries:
            if seed.line not in seen_lines:
                file_entries.append(seed)
                seen_lines.add(seed.line)
            seed_index = entry_index_by_line.get(seed.line)
            if seed_index is None:
                continue
            for delta in range(1, normalized_radius + 1):
                for candidate_index in (seed_index - delta, seed_index + delta):
                    if candidate_index < 0 or candidate_index >= len(table.entries):
                        continue
                    candidate = table.entries[candidate_index]
                    if candidate.line in seen_lines or candidate.line in seed_lines:
                        continue
                    if file_extra_entries >= per_file_cap or total_extra_entries >= total_cap:
                        stats["record_window_extra_entries_capped"] = True
                        continue
                    file_entries.append(candidate)
                    seen_lines.add(candidate.line)
                    file_extra_entries += 1
                    total_extra_entries += 1
        expanded.append((table, file_entries))
    stats["record_window_extra_entry_count"] = total_extra_entries
    stats["record_window_entry_count"] = sum(len(entries) for _table, entries in expanded)
    return expanded, stats


def _query_profile(intent: QueryIntent) -> dict[str, Any]:
    terms = _lexical_terms(" ".join(sorted(set(intent.base_terms) | set(intent.terms) | {intent.normalized, intent.original})))
    structural_tags: set[str] = set()
    event_families: set[str] = set()
    lower_terms = " ".join(terms)
    if any(term in {"assistant", "answer", "responded", "said"} for term in terms):
        structural_tags.add("role:assistant")
    if any(term in {"user", "asked", "request", "prompt"} for term in terms):
        structural_tags.add("role:user")
    if any(term in {"tool", "command", "bash", "exec", "run", "pytest", "test"} for term in terms):
        event_families.update({"tool", "command", "verification"})
        structural_tags.update({"event_family:tool", "event_family:command", "event_family:verification"})
    if any(term in {"git", "commit", "diff", "branch", "merge"} for term in terms):
        event_families.add("git")
        structural_tags.add("event_family:git")
    if any(term in {"review", "verdict", "verified"} for term in terms):
        event_families.add("review")
        structural_tags.add("event_family:review")
    if any(term in {"report", "artifact", "task", "file", "wrote", "created"} for term in terms):
        event_families.add("artifact")
        structural_tags.add("event_family:artifact")
    if (
        "date" in terms
        or "timeline" in terms
        or re.search(r"\bwhen\b", lower_terms)
    ):
        structural_tags.add("time:timestamp_present")
    return {
        "lexical_terms": terms,
        "structural_tags": structural_tags,
        "event_families": event_families,
        "role": intent.role,
        "role_filter_explicit": intent.role_filter_explicit,
        "date_reason": intent.date_reason,
        "date_window_present": intent.date_window_start is not None or intent.date_window_end is not None,
        "date_window_filter": intent.date_window_filter,
    }


def _sanitized_query_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "lexical_term_count": len(profile["lexical_terms"]),
        "structural_tags": sorted(profile["structural_tags"]),
        "event_families": sorted(profile["event_families"]),
        "role": profile.get("role"),
        "role_filter_explicit": bool(profile.get("role_filter_explicit")),
        "date_reason": profile.get("date_reason"),
        "date_window_present": bool(profile.get("date_window_present")),
        "date_window_filter": bool(profile.get("date_window_filter")),
        "raw_query_text_included": False,
    }


def _structural_overlap(
    entry: StructuralLineEntry,
    query_tags: set[str],
    query_profile: dict[str, Any],
    *,
    candidate_selection_profile: str,
) -> int:
    overlap = len(entry.structural_tags & query_tags)
    if not _is_intent_structural_profile(candidate_selection_profile):
        return overlap
    role = query_profile.get("role")
    if role and f"role:{_safe_tag(str(role))}" in entry.structural_tags:
        overlap += 1
    if (query_profile.get("date_reason") or query_profile.get("date_window_present")) and (
        entry.timestamp_bucket == "timestamp_present"
    ):
        overlap += 1
    return overlap


def _lexical_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in WORD_RE.findall(text.lower()):
        if len(token) >= 3:
            terms.add(token)
    for term in term_tokens(text):
        if len(term) >= 3:
            terms.add(term)
    return terms


def _artifact_hint_tags(text: str) -> set[str]:
    lower = text[:TEXT_PROBE_LIMIT].lower()
    tags: set[str] = set()
    if "pytest" in lower or " passed" in lower or " failed" in lower:
        tags.add("event_family:verification")
        tags.add("hint:test")
    if "git " in lower or "commit" in lower or "merge" in lower:
        tags.add("event_family:git")
        tags.add("hint:git")
    if "codex/state/reviews" in lower or "verdict:" in lower or "review" in lower:
        tags.add("event_family:review")
        tags.add("hint:review")
    if "reports/" in lower or "benchmarks/" in lower or "tasks/" in lower:
        tags.add("event_family:artifact")
        tags.add("hint:artifact")
    return tags


def _record_may_have_structured_event(record: Record) -> bool:
    raw = record.raw or {}
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
    if not isinstance(payload, dict):
        return False
    if payload.get("type") in {"function_call", "tool_call", "function_call_output", "tool_output", "tool_use", "tool_result"}:
        return True
    if payload.get("role") in {"tool_call", "function_call", "tool_use"}:
        return True
    return bool(
        "tool_name" in payload
        or "tool" in payload
        or ("name" in payload and ("arguments" in payload or "input" in payload or "parameters" in payload))
    )


def _role_bucket(role: str) -> str:
    normalized = _safe_tag(role or "unknown")
    aliases = {
        "assistant_message": "assistant",
        "agent_message": "assistant",
        "user_message": "user",
        "function_call": "tool",
        "function_call_output": "tool",
        "tool_call": "tool",
        "tool_output": "tool",
        "tool_use": "tool",
        "tool_result": "tool",
    }
    return aliases.get(normalized, normalized or "unknown")


def _message_type(record: Record) -> str:
    raw = record.raw or {}
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
    if isinstance(payload, dict):
        value = payload.get("type") or payload.get("role") or raw.get("type")
        if value:
            return _safe_tag(str(value))
    return _role_bucket(record.role)


def _timestamp_bucket(record: Record) -> str:
    return "timestamp_present" if record.raw_timestamp or record.timestamp else "timestamp_absent"


def _safe_tag(value: str) -> str:
    tag = re.sub(r"[^a-z0-9_:-]+", "_", value.lower()).strip("_")
    return tag[:80] or "unknown"


def _hash_public(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _materialize_match(
    record: Record,
    score: float,
    reasons: list[str],
    artifacts: list[str],
    intent: QueryIntent,
) -> Match:
    snippet_text = make_snippet(record.text, intent.terms)
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
        session=facets.session,
        tasks=facets.tasks,
        preferences=facets.preferences,
    )


def _table_fallback_reason(table: StructuralFileTable) -> str | None:
    if table.invalid_reason:
        return table.invalid_reason
    return None


def _fallback_payload(
    started: float,
    *,
    reason: str,
    warnings: list[str],
    index: StructuralSidecarIndex,
    query_profile: dict[str, Any],
    record_window_stats: dict[str, Any] | None = None,
    candidate_selection_profile: str = "default",
    candidate_read_schedule_profile: str = "file_order",
) -> dict[str, Any]:
    record_window_stats = record_window_stats or _default_record_window_stats()
    return {
        "status": "fallback_required",
        "fallback_reason": reason,
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_selection_profile": candidate_selection_profile,
        "candidate_read_schedule_profile": candidate_read_schedule_profile,
        "candidate_read_schedule_entry_count": 0,
        "candidate_read_schedule_preview": [],
        "candidate_files": 0,
        "candidate_entries": 0,
        "searched_files": 0,
        "searched_bytes": 0,
        "records_considered": 0,
        "records_rejected_by_should_consider": 0,
        "candidate_records_scored": 0,
        "candidate_records_deduped": 0,
        **record_window_stats,
        "groups": [],
        "summary": {
            **index.sanitized_summary(),
            "fallback_reason": reason,
            "candidate_read_schedule_profile": candidate_read_schedule_profile,
            "candidate_read_schedule_entry_count": 0,
            **record_window_stats,
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }


def _search_summary(
    *,
    index: StructuralSidecarIndex,
    query_profile: dict[str, Any],
    candidate_file_count: int,
    candidate_entry_count: int,
    opened_file_count: int,
    searched_bytes: int,
    records_considered: int,
    source_kinds: set[str],
    record_window_stats: dict[str, Any],
    candidate_selection_profile: str,
    candidate_read_schedule_profile: str,
) -> dict[str, Any]:
    return {
        **index.sanitized_summary(),
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_selection_profile": candidate_selection_profile,
        "candidate_read_schedule_profile": candidate_read_schedule_profile,
        "candidate_file_count": candidate_file_count,
        "candidate_entry_count": candidate_entry_count,
        "candidate_read_schedule_entry_count": candidate_entry_count,
        "opened_file_count": opened_file_count,
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "source_kinds": sorted(source_kinds),
        **record_window_stats,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
    }


def _default_record_window_stats() -> dict[str, Any]:
    return {
        "record_window_enabled": False,
        "record_window_radius": 0,
        "record_window_seed_entry_count": 0,
        "record_window_extra_entry_count": 0,
        "record_window_entry_count": 0,
        "record_window_max_extra_entries_per_file": 0,
        "record_window_max_extra_entries_total": 0,
        "record_window_extra_entries_capped": False,
        "oracle_inputs_used": False,
    }


def _privacy_payload() -> dict[str, bool]:
    return {
        "index_stores_raw_text": False,
        "summary_includes_raw_paths": False,
        "summary_includes_raw_text": False,
        "summary_includes_raw_session_ids": False,
        "summary_includes_raw_cwd": False,
        "vectors_or_indexes_included": False,
    }


def _metric_counts(files: dict[str, StructuralFileTable], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in files.values():
        for entry in table.entries:
            value = str(getattr(entry, attr))
            counts[value] = counts.get(value, 0) + 1
    return counts
