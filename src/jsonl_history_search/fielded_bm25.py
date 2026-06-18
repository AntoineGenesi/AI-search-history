from __future__ import annotations

import hashlib
import itertools
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
from .clustering import facets_for_record
from .profiling import finish_phase_profile, start_phase_profile
from .query import QueryIntent
from .ranking import Match, group_matches, make_snippet, score_record
from .records import Record, parse_record
from .masking import MaskContext
from .scanner import (
    _init_expected_ref_diagnostics,
    _mark_expected_final_candidates,
    _mark_expected_pre_final_candidates,
    _public_expected_ref_diagnostics,
    dedupe_key_for_record,
    match_sort_key,
)
from .sources import PlannedJsonlFiles, SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_size
from .tool_events import detect_events_for_record


FIELDED_BM25_VERSION = "fielded-sqlite-fts5-record-chunk-v1"
FTS_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*", re.IGNORECASE)
TIMESTAMP_BUCKET_RE = re.compile(r"^(\d{4}-\d{2}(?:-\d{2})?)")
# Keep JSONL rows as source-record candidates so the FTS LIMIT bounds
# downstream parse/rank cost instead of expanding each hit into large chunks.
CHUNK_RECORD_LIMIT = 1
INDEX_LINE_TEXT_LIMIT = 1200
FIELDED_SOURCE_GROUPING_POOL_FACTOR = 4
FTS_FIELD_NAMES = {"role", "timestamp_bucket", "tool_event", "artifact_task_path", "cwd_project", "text"}


@dataclass(frozen=True)
class FieldedBm25RecordRef:
    rowid: int
    path: Path
    line: int
    source_kind: str
    raw_text: str
    raw_materialization_text: str | None = None
    line_end: int | None = None
    record: Record | None = None


@dataclass
class FieldedBm25Index:
    connection: sqlite3.Connection | None
    records: dict[int, FieldedBm25RecordRef] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    available: bool = True
    unavailable_reason: str | None = None
    version: str = FIELDED_BM25_VERSION

    def sanitized_summary(self) -> dict[str, Any]:
        source_kind_counts: dict[str, int] = {}
        role_bucket_counts: dict[str, int] = {}
        timestamp_bucket_counts: dict[str, int] = {}
        for ref in self.records.values():
            source_kind_counts[ref.source_kind] = source_kind_counts.get(ref.source_kind, 0) + 1
            record = ref.record
            role_bucket = _role_bucket(record.role) if record is not None else _raw_role_bucket(ref.raw_text)
            role_bucket_counts[role_bucket] = role_bucket_counts.get(role_bucket, 0) + 1
            timestamp_bucket = _timestamp_bucket(record) if record is not None else _raw_timestamp_bucket(ref.raw_text)
            timestamp_bucket_counts[timestamp_bucket] = timestamp_bucket_counts.get(timestamp_bucket, 0) + 1
        return {
            "version": self.version,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "indexed_file_count": int(self.metrics.get("indexed_files", 0)),
            "indexed_record_count": int(self.metrics.get("indexed_records", 0)),
            "indexed_chunk_count": int(self.metrics.get("indexed_chunks", 0)),
            "build_bytes": int(self.metrics.get("build_bytes", 0)),
            "build_elapsed_ms": self.metrics.get("build_elapsed_ms", 0.0),
            "profile": _phase_profile_payload(self.metrics.get("profile"), "fielded_bm25_index_build"),
            "planned_file_enumeration_reused": bool(self.metrics.get("planned_file_enumeration_reused", False)),
            "planned_file_count": int(self.metrics.get("planned_file_count", 0)),
            "planned_file_bytes": int(self.metrics.get("planned_file_bytes", 0)),
            "source_kind_counts": dict(sorted(source_kind_counts.items())),
            "role_bucket_counts": dict(sorted(role_bucket_counts.items())),
            "timestamp_bucket_counts": dict(sorted(timestamp_bucket_counts.items())),
            "field_names": [
                "source_kind",
                "role",
                "cwd_project",
                "timestamp_bucket",
                "tool_event",
                "artifact_task_path",
                "text",
            ],
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "vectors_or_persisted_indexes_included": False,
        }


@dataclass(frozen=True)
class FieldedQueryStream:
    label: str
    query: str
    fields: tuple[str, ...]


def sqlite_fts5_available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(text)")
        finally:
            connection.close()
    except sqlite3.Error:
        return False
    return True


def build_fielded_bm25_index(
    plan: SourcePlan,
    *,
    default_tz,
    planned_jsonl_files: PlannedJsonlFiles | None = None,
) -> FieldedBm25Index:
    started = time.perf_counter()
    profile_token = start_phase_profile("fielded_bm25_index_build")
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute(
            """
            CREATE VIRTUAL TABLE records USING fts5(
                source_kind,
                role,
                cwd_project,
                timestamp_bucket,
                tool_event,
                artifact_task_path,
                text
            )
            """
        )
    except sqlite3.Error as exc:
        profile = finish_phase_profile(profile_token, indexed_file_count=0, indexed_record_count=0, indexed_chunk_count=0)
        return FieldedBm25Index(
            connection=None,
            metrics={"build_elapsed_ms": _elapsed_ms(started), "profile": profile},
            available=False,
            unavailable_reason=type(exc).__name__,
            warnings=["SQLite FTS5 is unavailable; fielded BM25 lane cannot run."],
        )

    index = FieldedBm25Index(
        connection=connection,
        metrics={
            "build_bytes": 0,
            "indexed_files": 0,
            "indexed_records": 0,
            "indexed_chunks": 0,
            "read_error_files": 0,
        },
    )
    rowid = 0
    insert_buffer: list[tuple[int, str, str, str, str, str, str, str]] = []
    if planned_jsonl_files is not None:
        enumerated_files = [(path, size, list(planned_jsonl_files.warnings)) for path, size in planned_jsonl_files.files]
    else:
        enumerated_files = enumerate_jsonl_files(plan)

    for path, size, enum_warnings in enumerated_files:
        index.warnings = list(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        file_records = 0
        file_bytes = int(size) if planned_jsonl_files is not None else stat_size(path)
        try:
            if path.suffix == ".jsonl":
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    chunk_lines: list[tuple[int, str]] = []
                    for line_number, raw_line in enumerate(handle, 1):
                        file_records += 1
                        chunk_lines.append((line_number, raw_line.rstrip("\n")))
                        if len(chunk_lines) >= CHUNK_RECORD_LIMIT:
                            rowid = _add_raw_chunk(
                                connection,
                                index,
                                insert_buffer,
                                rowid,
                                path=path,
                                source_kind=source_kind,
                                chunk_lines=chunk_lines,
                            )
                            chunk_lines = []
                    if chunk_lines:
                        rowid = _add_raw_chunk(
                            connection,
                            index,
                            insert_buffer,
                            rowid,
                            path=path,
                            source_kind=source_kind,
                            chunk_lines=chunk_lines,
                        )
                _flush_insert_buffer(connection, insert_buffer)
            else:
                for record in iter_records_for_file(path, source_kind, default_tz):
                    rowid += 1
                    file_records += 1
                    _insert_record(connection, rowid, record, source_kind)
                    index.records[rowid] = FieldedBm25RecordRef(
                        rowid=rowid,
                        path=Path(record.path),
                        line=int(record.line),
                        source_kind=source_kind,
                        raw_text=record.text,
                        record=record,
                    )
        except AdapterReadError as exc:
            index.metrics["read_error_files"] += 1
            index.warnings.append(str(exc))
            continue
        except OSError as exc:
            index.metrics["read_error_files"] += 1
            index.warnings.append(f"Could not open {path}: {exc}")
            continue
        index.files[str(path)] = {
            "source_kind": source_kind,
            "record_count": file_records,
            "byte_count": file_bytes,
        }
        index.metrics["indexed_files"] += 1
        index.metrics["indexed_records"] += file_records
        if path.suffix != ".jsonl":
            index.metrics["indexed_chunks"] += file_records
        index.metrics["build_bytes"] += file_bytes
    _flush_insert_buffer(connection, insert_buffer)
    connection.commit()
    index.metrics["build_elapsed_ms"] = _elapsed_ms(started)
    if planned_jsonl_files is not None:
        index.metrics["planned_file_enumeration_reused"] = True
        index.metrics["planned_file_count"] = planned_jsonl_files.file_count
        index.metrics["planned_file_bytes"] = planned_jsonl_files.byte_count
    index.metrics["profile"] = finish_phase_profile(
        profile_token,
        indexed_file_count=int(index.metrics.get("indexed_files", 0)),
        indexed_record_count=int(index.metrics.get("indexed_records", 0)),
        indexed_chunk_count=int(index.metrics.get("indexed_chunks", 0)),
        build_bytes=int(index.metrics.get("build_bytes", 0)),
    )
    index.warnings = sorted(set(index.warnings + plan.warnings))
    return index


def search_with_fielded_bm25(
    index: FieldedBm25Index,
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None,
    mask_context: MaskContext | None = None,
    expected_ref_diagnostics: list[dict] | None = None,
    include_private_diagnostics: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile_token = start_phase_profile("fielded_bm25_search_materialization")
    warnings = list(index.warnings) + list(plan.warnings)
    query = _fts_query_for_intent(intent)
    query_streams = _fielded_query_streams_for_intent(intent, query)
    expected_diag = _init_fielded_expected_ref_diagnostics(expected_ref_diagnostics)
    _mark_expected_indexed(expected_diag, index)
    if not index.available or index.connection is None:
        payload = _unavailable_payload(index, started, warnings, query, expected_diag, query_streams=query_streams)
        payload["profile"] = finish_phase_profile(profile_token, candidate_rows=0, candidate_count=0)
        return payload
    if not query_streams:
        warnings.append("Fielded BM25 query had no escaped FTS terms.")
        payload = _empty_payload(
            index,
            started,
            warnings,
            query,
            expected_diag,
            status="ineligible_query",
            query_streams=query_streams,
        )
        payload["profile"] = finish_phase_profile(profile_token, candidate_rows=0, candidate_count=0)
        return payload

    candidate_limit = max(plan.limits.max_matches * 5, plan.limits.max_matches)
    candidate_pool_limit = max(candidate_limit * FIELDED_SOURCE_GROUPING_POOL_FACTOR, candidate_limit)
    raw_rows, row_stream_labels, stream_rows, stream_diagnostics, stream_error_types = _execute_fielded_query_streams(
        index,
        query_streams,
        candidate_pool_limit=candidate_pool_limit,
    )
    if stream_error_types:
        warnings.append("Fielded BM25 query stream failed: " + ",".join(sorted(stream_error_types)) + ".")
    if not raw_rows and stream_error_types:
        payload = _empty_payload(
            index,
            started,
            warnings,
            query,
            expected_diag,
            status="query_failed",
            query_streams=query_streams,
            query_stream_diagnostics=stream_diagnostics,
        )
        payload["profile"] = finish_phase_profile(profile_token, candidate_rows=0, candidate_count=0)
        return payload
    rows, source_grouping = _select_source_grouped_candidate_rows(
        raw_rows,
        index,
        selected_limit=candidate_limit,
        pool_limit=candidate_pool_limit,
        row_stream_labels=row_stream_labels,
        stream_rows=stream_rows,
    )
    query_stream_summary = _query_stream_summary(
        streams=query_streams,
        diagnostics=stream_diagnostics,
        deduped_row_count=len(raw_rows),
        candidate_pool_limit=candidate_pool_limit,
        selection=source_grouping.get("stream_selection"),
    )
    _mark_expected_candidate_rows(expected_diag, rows)
    matches: list[Match] = []
    dedupe: set[tuple[str, ...]] = set()
    records_considered = 0
    records_rejected_by_ranker = 0
    candidate_records_deduped = 0
    candidate_records_scored = 0
    bm25_rank_buckets: dict[str, int] = {}
    opened_paths: set[str] = set()
    searched_bytes = 0
    materialization = _new_materialization_counters()

    for rank, row in enumerate(rows, start=1):
        rowid = int(row[0])
        chunk_ref = index.records.get(rowid)
        if chunk_ref is None:
            continue
        opened_paths.add(str(chunk_ref.path))
        bm25_rank_buckets[_rank_bucket(rank)] = bm25_rank_buckets.get(_rank_bucket(rank), 0) + 1
        for record in _materialize_chunk_records(chunk_ref, default_tz=default_tz, counters=materialization):
            records_considered += 1
            expected_state = _fielded_expected_state(expected_diag, record)
            if expected_state is not None:
                expected_state["expected_file_opened_before_scan_stop"] = True
                expected_state["expected_record_line_reached_before_elapsed_stop"] = True
                expected_state["expected_record_selected_as_bm25_candidate_row"] = True
                expected_state["fielded_candidate_status"] = "selected_as_bm25_candidate_row"
                expected_state["bm25_candidate_rank_label"] = f"rank_{rank:03d}" if rank <= 20 else "rank_021_plus"
                expected_state["bm25_candidate_rank_bucket"] = _rank_bucket(rank)
                expected_state["pre_parse_phase_label"] = "fts_index_lookup"
                expected_state["pre_parse_status"] = "fts_index_lookup"
            key = dedupe_key_for_record(record)
            if key in dedupe:
                candidate_records_deduped += 1
                if expected_state is not None:
                    expected_state["deduped_before_scoring"] = True
                    expected_state["dedupe_phase_label"] = "deduped_before_scoring"
                    expected_state["drop_phase_label"] = "dedupe_drop"
                continue
            dedupe.add(key)
            score, reasons, artifacts = score_record(record, intent, cwd)
            if score <= 0:
                records_rejected_by_ranker += 1
                if expected_state is not None:
                    expected_state["would_pass_should_consider"] = False
                    expected_state["should_consider_phase_label"] = "rejected_by_existing_ranker"
                    expected_state["pre_reservoir_status"] = "rejected_by_existing_ranker"
                    expected_state["drop_phase_label"] = "ranker_drop"
                continue
            candidate_records_scored += 1
            if expected_state is not None:
                expected_state["would_pass_should_consider"] = True
                expected_state["should_consider_phase_label"] = "passed_existing_ranker"
                expected_state["dedupe_phase_label"] = "not_deduped"
                expected_state["expected_record_present_before_reservoir_retention"] = True
                expected_state["pre_reservoir_status"] = "present_before_bm25_rank_cap"
                expected_state["reservoir_phase_label"] = "bm25_candidate_retained"
                expected_state["candidate_strength"] = "bm25_candidate"
                expected_state["candidate_strength_label"] = "fielded_bm25"
                expected_state["score_label_count"] = len(reasons)
                expected_state["artifact_label_count"] = len(artifacts)
            matches.append(_materialize_match(record, score, reasons, artifacts, intent, mask_context))

    searched_bytes = sum(int(index.files.get(path, {}).get("byte_count") or 0) for path in opened_paths)
    _mark_expected_pre_final_candidates(expected_diag, matches)
    matches.sort(key=match_sort_key, reverse=True)
    pre_cap_count = len(matches)
    matches = matches[: plan.limits.max_matches]
    _mark_expected_final_candidates(expected_diag, matches)
    if pre_cap_count > len(matches):
        warnings.append("Fielded BM25 candidates were capped at max match cap after ranking.")
    payload = {
        "status": "planned_fielded_bm25",
        "fallback_reason": None,
        "fts": _fts_stats(index, query=query),
        "query_profile": _query_profile(intent, query),
        "fielded_query_streams": query_stream_summary,
        "candidate_rows": len(rows),
        "candidate_row_limit": candidate_limit,
        "candidate_row_pool_limit": candidate_pool_limit,
        "candidate_rows_generated": len(rows),
        "candidate_rows_considered": len(raw_rows),
        "candidate_rows_retained": pre_cap_count,
        "candidate_rows_surfaced": len(matches),
        "fielded_source_grouping": source_grouping,
        "bm25_rank_buckets": dict(sorted(bm25_rank_buckets.items())),
        "searched_files": len(opened_paths),
        "opened_files": len(opened_paths),
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "records_rejected_by_existing_ranker": records_rejected_by_ranker,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
        "materialization": materialization,
        "groups": group_matches(matches),
        "summary": index.sanitized_summary(),
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
        "profile": finish_phase_profile(
            profile_token,
            candidate_rows=len(rows),
            candidate_rows_considered=len(raw_rows),
            candidate_count=len(matches),
            searched_files=len(opened_paths),
            searched_bytes=searched_bytes,
            records_considered=records_considered,
            candidate_rows_materialized=int(materialization.get("candidate_rows_materialized") or 0),
        ),
    }
    if include_private_diagnostics:
        payload["_searched_file_paths"] = sorted(opened_paths)
    if expected_diag is not None:
        payload["expected_ref_diagnostics"] = _public_fielded_expected_ref_diagnostics(expected_diag)
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload


def _insert_record(connection: sqlite3.Connection, rowid: int, record: Record, source_kind: str) -> None:
    facets = facets_for_record(path=record.path, text=record.text, artifacts=[])
    tool_event_terms = _tool_event_terms(record)
    artifact_task_path_terms = " ".join(sorted(set(facets.artifacts + facets.tasks + _path_terms(record))))
    connection.execute(
        """
        INSERT INTO records(
            rowid, source_kind, role, cwd_project, timestamp_bucket, tool_event, artifact_task_path, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rowid,
            source_kind,
            _role_bucket(record.role),
            _cwd_project_terms(record),
            _timestamp_bucket(record),
            tool_event_terms,
            artifact_task_path_terms,
            record.text,
        ),
    )


def _raw_insert_row(rowid: int, raw_text: str, source_kind: str) -> tuple[int, str, str, str, str, str, str, str]:
    return (
        rowid,
        source_kind,
        _raw_role_bucket(raw_text),
        _raw_cwd_project_terms(raw_text),
        _raw_timestamp_bucket(raw_text),
        _raw_tool_event_terms(raw_text),
        _raw_artifact_task_path_terms(raw_text),
        raw_text,
    )


def _add_raw_chunk(
    connection: sqlite3.Connection,
    index: FieldedBm25Index,
    insert_buffer: list[tuple[int, str, str, str, str, str, str, str]],
    rowid: int,
    *,
    path: Path,
    source_kind: str,
    chunk_lines: list[tuple[int, str]],
) -> int:
    rowid += 1
    first_line = chunk_lines[0][0]
    last_line = chunk_lines[-1][0]
    raw_lines = tuple(raw for _line, raw in chunk_lines)
    raw_text = "\n".join(_clip_index_text(raw) for raw in raw_lines)
    raw_materialization_text = "\n".join(raw_lines)
    insert_buffer.append(_raw_insert_row(rowid, raw_text, source_kind))
    index.records[rowid] = FieldedBm25RecordRef(
        rowid=rowid,
        path=path,
        line=first_line,
        line_end=last_line,
        source_kind=source_kind,
        raw_text=raw_text,
        raw_materialization_text=raw_materialization_text,
    )
    index.metrics["indexed_chunks"] += 1
    if len(insert_buffer) >= 500:
        _flush_insert_buffer(connection, insert_buffer)
    return rowid


def _flush_insert_buffer(
    connection: sqlite3.Connection,
    insert_buffer: list[tuple[int, str, str, str, str, str, str, str]],
) -> None:
    if not insert_buffer:
        return
    connection.executemany(
        """
        INSERT INTO records(
            rowid, source_kind, role, cwd_project, timestamp_bucket, tool_event, artifact_task_path, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        insert_buffer,
    )
    insert_buffer.clear()


def _materialize_record_ref(ref: FieldedBm25RecordRef, *, default_tz) -> Record:
    if ref.record is not None:
        return ref.record
    record = parse_record(ref.raw_text, path=ref.path, line=ref.line, default_tz=default_tz)
    record.source_format = ref.source_kind
    ref_record = record
    object.__setattr__(ref, "record", ref_record)
    return ref_record


def _materialize_chunk_records(
    ref: FieldedBm25RecordRef,
    *,
    default_tz,
    counters: dict[str, int | bool] | None = None,
) -> list[Record]:
    if counters is not None:
        counters["candidate_rows_materialized"] = int(counters["candidate_rows_materialized"]) + 1
    line_end = int(ref.line_end or ref.line)
    if line_end <= ref.line and ref.record is not None:
        if counters is not None:
            counters["candidate_rows_materialized_from_cached_record"] = (
                int(counters["candidate_rows_materialized_from_cached_record"]) + 1
            )
            counters["records_materialized_from_cached_record"] = (
                int(counters["records_materialized_from_cached_record"]) + 1
            )
        return [_materialize_record_ref(ref, default_tz=default_tz)]
    if ref.raw_materialization_text is not None:
        if counters is not None:
            counters["in_memory_raw_materialization_available"] = True
            counters["candidate_rows_materialized_from_in_memory_raw"] = (
                int(counters["candidate_rows_materialized_from_in_memory_raw"]) + 1
            )
        records = []
        for offset, raw_line in enumerate(ref.raw_materialization_text.splitlines()):
            line = ref.line + offset
            record = parse_record(raw_line, path=ref.path, line=line, default_tz=default_tz)
            record.source_format = ref.source_kind
            records.append(record)
        if counters is not None:
            counters["records_materialized_from_in_memory_raw"] = (
                int(counters["records_materialized_from_in_memory_raw"]) + len(records)
            )
        return records
    records: list[Record] = []
    if counters is not None:
        counters["file_reopen_attempts"] = int(counters["file_reopen_attempts"]) + 1
        counters["candidate_rows_materialized_from_file_reopen"] = (
            int(counters["candidate_rows_materialized_from_file_reopen"]) + 1
        )
    try:
        with ref.path.open("r", encoding="utf-8", errors="replace") as handle:
            selected = itertools.islice(handle, ref.line - 1, line_end)
            for offset, raw_line in enumerate(selected):
                line = ref.line + offset
                record = parse_record(raw_line, path=ref.path, line=line, default_tz=default_tz)
                record.source_format = ref.source_kind
                records.append(record)
    except OSError:
        if counters is not None:
            counters["file_reopen_failures"] = int(counters["file_reopen_failures"]) + 1
            counters["candidate_rows_materialization_failed"] = (
                int(counters["candidate_rows_materialization_failed"]) + 1
            )
        return []
    if counters is not None:
        counters["records_materialized_from_file_reopen"] = (
            int(counters["records_materialized_from_file_reopen"]) + len(records)
        )
    return records


def _select_source_grouped_candidate_rows(
    rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    selected_limit: int,
    pool_limit: int,
    row_stream_labels: dict[int, set[str]] | None = None,
    stream_rows: dict[str, list[tuple[Any, ...]]] | None = None,
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    bounded_limit = max(int(selected_limit), 0)
    row_stream_labels = row_stream_labels or {}
    stream_rows = stream_rows or {}
    if bounded_limit <= 0:
        return [], _source_grouping_diagnostics(
            rows_considered=len(rows),
            rows_selected=0,
            selected_limit=bounded_limit,
            pool_limit=pool_limit,
            considered_source_counts={},
            selected_source_counts={},
            selected_stream_counts={},
            dropped_stream_counts=_stream_dropped_counts(stream_rows, set()),
            rows_dropped_by_source_cap=0,
            source_head_rows_selected=0,
            stream_head_rows_selected=0,
            global_tail_rows_selected=0,
        )

    grouped_rows: dict[str, list[tuple[Any, ...]]] = {}
    considered_source_counts: dict[str, int] = {}
    row_by_id: dict[int, tuple[Any, ...]] = {}
    for row in rows:
        rowid = int(row[0])
        row_by_id[rowid] = row
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key = _source_grouping_key(ref)
        grouped_rows.setdefault(source_key, []).append(row)
        considered_source_counts[source_key] = considered_source_counts.get(source_key, 0) + 1

    selected: list[tuple[Any, ...]] = []
    selected_rowids: set[int] = set()
    stream_head_rows_selected = 0

    for label, label_rows in stream_rows.items():
        if len(selected) >= bounded_limit:
            break
        for row in label_rows:
            rowid = int(row[0])
            if rowid in selected_rowids or rowid not in row_by_id:
                continue
            selected.append(row_by_id[rowid])
            selected_rowids.add(rowid)
            stream_head_rows_selected += 1
            break

    source_head_rows_selected = 0
    for source_rows in grouped_rows.values():
        if len(selected) >= bounded_limit:
            break
        head = source_rows[0]
        rowid = int(head[0])
        if rowid in selected_rowids:
            continue
        selected.append(head)
        selected_rowids.add(rowid)
        source_head_rows_selected += 1

    global_tail_rows_selected = 0
    for row in rows:
        if len(selected) >= bounded_limit:
            break
        rowid = int(row[0])
        if rowid in selected_rowids:
            continue
        selected.append(row)
        selected_rowids.add(rowid)
        global_tail_rows_selected += 1

    selected_source_counts: dict[str, int] = {}
    selected_stream_counts: dict[str, int] = {}
    for row in selected:
        rowid = int(row[0])
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key = _source_grouping_key(ref)
        selected_source_counts[source_key] = selected_source_counts.get(source_key, 0) + 1
        for label in row_stream_labels.get(rowid, set()):
            selected_stream_counts[label] = selected_stream_counts.get(label, 0) + 1
    rows_dropped_by_source_cap = sum(
        max(int(count) - int(selected_source_counts.get(source_key, 0)), 0)
        for source_key, count in considered_source_counts.items()
    )
    dropped_stream_counts = _stream_dropped_counts(stream_rows, selected_rowids)

    return selected, _source_grouping_diagnostics(
        rows_considered=len(rows),
        rows_selected=len(selected),
        selected_limit=bounded_limit,
        pool_limit=pool_limit,
        considered_source_counts=considered_source_counts,
        selected_source_counts=selected_source_counts,
        selected_stream_counts=selected_stream_counts,
        dropped_stream_counts=dropped_stream_counts,
        rows_dropped_by_source_cap=rows_dropped_by_source_cap,
        source_head_rows_selected=source_head_rows_selected,
        stream_head_rows_selected=stream_head_rows_selected,
        global_tail_rows_selected=global_tail_rows_selected,
    )


def _source_grouping_key(ref: FieldedBm25RecordRef) -> str:
    return str(ref.path)


def _source_grouping_diagnostics(
    *,
    rows_considered: int,
    rows_selected: int,
    selected_limit: int,
    pool_limit: int,
    considered_source_counts: dict[str, int],
    selected_source_counts: dict[str, int],
    selected_stream_counts: dict[str, int],
    dropped_stream_counts: dict[str, int],
    rows_dropped_by_source_cap: int,
    source_head_rows_selected: int,
    stream_head_rows_selected: int,
    global_tail_rows_selected: int,
) -> dict[str, Any]:
    rows_dropped_by_stream_cap = sum(int(count) for count in dropped_stream_counts.values())
    return {
        "enabled": True,
        "strategy": "stream_heads_then_source_heads_then_global_bm25_tail"
        if selected_stream_counts or stream_head_rows_selected
        else "source_heads_then_global_bm25_tail",
        "candidate_rows_considered": int(rows_considered),
        "candidate_rows_selected": int(rows_selected),
        "candidate_row_limit": int(selected_limit),
        "candidate_row_pool_limit": int(pool_limit),
        "source_count_considered": len(considered_source_counts),
        "source_count_selected": len(selected_source_counts),
        "dominant_source_share_before": _dominant_source_share(considered_source_counts),
        "dominant_source_share_after": _dominant_source_share(selected_source_counts),
        "stream_head_rows_selected": int(stream_head_rows_selected),
        "source_head_rows_selected": int(source_head_rows_selected),
        "global_tail_rows_selected": int(global_tail_rows_selected),
        "stream_selection": {
            "enabled": bool(selected_stream_counts or dropped_stream_counts or stream_head_rows_selected),
            "selected_rows_by_stream": dict(sorted(selected_stream_counts.items())),
            "rows_dropped_by_stream_cap": dict(sorted(dropped_stream_counts.items())),
            "rows_dropped_by_stream_cap_total": int(rows_dropped_by_stream_cap),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_query_terms_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "line_numbers_included": False,
            "source_lines_included": False,
        },
        "rows_dropped_by_source_cap": int(rows_dropped_by_source_cap),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _stream_dropped_counts(stream_rows: dict[str, list[tuple[Any, ...]]], selected_rowids: set[int]) -> dict[str, int]:
    dropped: dict[str, int] = {}
    for label, rows in stream_rows.items():
        rowids = {int(row[0]) for row in rows}
        count = sum(1 for rowid in rowids if rowid not in selected_rowids)
        if count:
            dropped[label] = count
    return dropped


def _dominant_source_share(source_counts: dict[str, int]) -> float:
    total = sum(source_counts.values())
    if total <= 0:
        return 0.0
    return round(max(source_counts.values()) / total, 6)


def _new_materialization_counters() -> dict[str, int | bool]:
    return {
        "candidate_rows_materialized": 0,
        "candidate_rows_materialized_from_in_memory_raw": 0,
        "candidate_rows_materialized_from_cached_record": 0,
        "candidate_rows_materialized_from_file_reopen": 0,
        "candidate_rows_materialization_failed": 0,
        "records_materialized_from_in_memory_raw": 0,
        "records_materialized_from_cached_record": 0,
        "records_materialized_from_file_reopen": 0,
        "file_reopen_attempts": 0,
        "file_reopen_failures": 0,
        "in_memory_raw_materialization_available": False,
        "raw_text_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _phase_profile_payload(value: Any, phase: str) -> dict[str, Any]:
    profile = value if isinstance(value, dict) else {}
    rss_available = bool(profile.get("rss_available", False))
    return {
        "phase": str(profile.get("phase") or phase),
        "elapsed_ms": float(profile.get("elapsed_ms") or 0.0),
        "rss_available": rss_available,
        "rss_units": "bytes",
        "rss_start_max_bytes": _optional_int(profile.get("rss_start_max_bytes")),
        "rss_end_max_bytes": _optional_int(profile.get("rss_end_max_bytes")),
        "rss_max_bytes": _optional_int(profile.get("rss_max_bytes")),
        "rss_delta_bytes": _optional_int(profile.get("rss_delta_bytes")),
        "rss_unavailable_reason": None if rss_available else str(profile.get("rss_unavailable_reason") or "not_profiled"),
        "rss_current_bytes": None,
        "rss_current_unavailable_reason": "resource_ru_maxrss_reports_process_peak_not_current",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clip_index_text(raw_text: str) -> str:
    if len(raw_text) <= INDEX_LINE_TEXT_LIMIT:
        return raw_text
    return raw_text[:INDEX_LINE_TEXT_LIMIT]


def _fts_query_for_intent(intent: QueryIntent) -> str | None:
    values: list[str] = []
    for collection in (intent.artifact_hints, intent.base_terms, intent.terms):
        values.extend(str(item) for item in collection if str(item).strip())
    if intent.role:
        values.append(intent.role)
    if intent.date_window_start is not None:
        values.append(intent.date_window_start.strftime("%Y-%m-%d"))
        values.append(intent.date_window_start.strftime("%Y-%m"))
    seen: set[str] = set()
    parts: list[str] = []
    for value in values:
        for token in _fts_tokens(value):
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            parts.append(f'"{lowered.replace(chr(34), chr(34) + chr(34))}"')
    if not parts:
        return None
    return " OR ".join(parts)


def _fielded_query_streams_for_intent(intent: QueryIntent, base_query: str | None) -> list[FieldedQueryStream]:
    streams: list[FieldedQueryStream] = []
    if base_query:
        streams.append(FieldedQueryStream(label="base_lexical", query=base_query, fields=("text",)))

    text_query = _field_query("text", _anchor_values_for_fielded_streams(intent))
    if not text_query:
        return _unique_streams(streams)

    plan = intent.query_plan
    primary_intent = plan.primary_intent if plan is not None else "generic_history"
    detected_intents = set(plan.detected_intents if plan is not None else [])
    role_hints = _role_hints_for_query_streams(intent)
    for role in role_hints:
        role_query = _field_query("role", [role])
        if role_query:
            streams.append(
                FieldedQueryStream(
                    label=f"role_text_{_stream_label_part(role)}",
                    query=f"{role_query} AND ({text_query})",
                    fields=("role", "text"),
                )
            )

    if primary_intent in {"when_user_said", "workflow_failure"} or "when_user_said" in detected_intents:
        timestamp_query = _timestamp_query_for_intent(intent)
        if timestamp_query:
            streams.append(
                FieldedQueryStream(
                    label="timestamp_text",
                    query=f"{timestamp_query} AND ({text_query})",
                    fields=("timestamp_bucket", "text"),
                )
            )

    artifact_query = _field_query("artifact_task_path", intent.artifact_hints)
    if artifact_query:
        streams.append(
            FieldedQueryStream(
                label="artifact_path_text",
                query=f"{artifact_query} AND ({text_query})",
                fields=("artifact_task_path", "text"),
            )
        )

    if primary_intent in {"workflow_failure", "why", "source_next_reads"}:
        tool_query = _field_query("tool_event", _tool_event_query_terms_for_intent(primary_intent))
        if tool_query:
            streams.append(
                FieldedQueryStream(
                    label=f"tool_event_text_{_stream_label_part(primary_intent)}",
                    query=f"{tool_query} AND ({text_query})",
                    fields=("tool_event", "text"),
                )
            )

    return _unique_streams(streams)


def _execute_fielded_query_streams(
    index: FieldedBm25Index,
    streams: list[FieldedQueryStream],
    *,
    candidate_pool_limit: int,
) -> tuple[list[tuple[Any, ...]], dict[int, set[str]], dict[str, list[tuple[Any, ...]]], list[dict[str, Any]], set[str]]:
    merged_rows: list[tuple[Any, ...]] = []
    seen_rowids: set[int] = set()
    row_stream_labels: dict[int, set[str]] = {}
    stream_rows: dict[str, list[tuple[Any, ...]]] = {}
    diagnostics: list[dict[str, Any]] = []
    error_types: set[str] = set()
    assert index.connection is not None
    for stream in streams:
        try:
            rows = list(
                index.connection.execute(
                    """
                    SELECT rowid, bm25(records) AS bm25_score
                    FROM records
                    WHERE records MATCH ?
                    ORDER BY bm25_score ASC
                    LIMIT ?
                    """,
                    (stream.query, candidate_pool_limit),
                )
            )
        except sqlite3.Error as exc:
            error_type = type(exc).__name__
            error_types.add(error_type)
            diagnostics.append(
                _query_stream_diagnostic(
                    stream,
                    row_count=0,
                    deduped_row_count=0,
                    stream_limit=candidate_pool_limit,
                    hit_limit=False,
                    status="query_failed",
                    error_type=error_type,
                )
            )
            continue
        stream_rows[stream.label] = rows
        deduped_count = 0
        for row in rows:
            rowid = int(row[0])
            row_stream_labels.setdefault(rowid, set()).add(stream.label)
            if rowid in seen_rowids:
                continue
            seen_rowids.add(rowid)
            merged_rows.append(row)
            deduped_count += 1
        diagnostics.append(
            _query_stream_diagnostic(
                stream,
                row_count=len(rows),
                deduped_row_count=deduped_count,
                stream_limit=candidate_pool_limit,
                hit_limit=len(rows) >= candidate_pool_limit,
                status="available",
                error_type=None,
            )
        )
    return merged_rows, row_stream_labels, stream_rows, diagnostics, error_types


def _query_stream_summary(
    *,
    streams: list[FieldedQueryStream],
    diagnostics: list[dict[str, Any]],
    deduped_row_count: int,
    candidate_pool_limit: int,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    generated = sum(int(item.get("row_count") or 0) for item in diagnostics)
    deduped_by_stream = sum(int(item.get("deduped_row_count") or 0) for item in diagnostics)
    saturated_labels = [
        str(item.get("label"))
        for item in diagnostics
        if bool(item.get("hit_limit")) and item.get("label")
    ]
    selection = selection if isinstance(selection, dict) else {}
    return {
        "enabled": True,
        "strategy": "query_plan_fielded_streams_then_stream_source_heads_then_rowid_dedupe",
        "stream_count": len(streams),
        "streams": diagnostics,
        "candidate_row_pool_limit_per_stream": int(candidate_pool_limit),
        "per_stream_limit": int(candidate_pool_limit),
        "stream_hit_limit_count": len(saturated_labels),
        "saturated_stream_count": len(saturated_labels),
        "saturated_stream_labels": saturated_labels,
        "row_count_before_dedupe": int(generated),
        "row_count_after_dedupe": int(deduped_row_count),
        "deduped_row_count_from_streams": int(deduped_by_stream),
        "duplicate_row_count": max(int(generated) - int(deduped_row_count), 0),
        "selected_rows_by_stream": dict(selection.get("selected_rows_by_stream") or {}),
        "rows_dropped_by_stream_cap": dict(selection.get("rows_dropped_by_stream_cap") or {}),
        "rows_dropped_by_stream_cap_total": int(selection.get("rows_dropped_by_stream_cap_total") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _query_stream_diagnostic(
    stream: FieldedQueryStream,
    *,
    row_count: int,
    deduped_row_count: int,
    stream_limit: int,
    hit_limit: bool,
    status: str,
    error_type: str | None,
) -> dict[str, Any]:
    return {
        "label": stream.label,
        "fields": list(stream.fields),
        "status": status,
        "row_count": int(row_count),
        "deduped_row_count": int(deduped_row_count),
        "stream_limit": int(stream_limit),
        "hit_limit": bool(hit_limit),
        "error_type": error_type,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
    }


def _unique_streams(streams: list[FieldedQueryStream]) -> list[FieldedQueryStream]:
    seen: set[tuple[str, str]] = set()
    unique: list[FieldedQueryStream] = []
    for stream in streams:
        key = (stream.label, stream.query)
        if key in seen:
            continue
        seen.add(key)
        unique.append(stream)
    return unique


def _field_query(field_name: str, values: list[str] | tuple[str, ...]) -> str | None:
    if field_name not in FTS_FIELD_NAMES:
        return None
    seen: set[str] = set()
    parts: list[str] = []
    for value in values:
        for token in _fts_tokens(str(value)):
            lowered = token.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            parts.append(f'{field_name}:"{_escape_fts_phrase(lowered)}"')
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "(" + " OR ".join(parts) + ")"


def _anchor_values_for_fielded_streams(intent: QueryIntent) -> list[str]:
    values: list[str] = []
    plan = intent.query_plan
    if plan is not None:
        values.extend(str(item) for item in plan.anchor_terms if str(item).strip())
    values.extend(str(item) for item in intent.base_terms if str(item).strip())
    values.extend(str(item) for item in intent.terms if str(item).strip())
    return values


def _role_hints_for_query_streams(intent: QueryIntent) -> list[str]:
    plan = intent.query_plan
    roles: list[str] = []
    if plan is not None:
        field_roles = (plan.field_hints or {}).get("roles") or []
        roles.extend(str(role) for role in field_roles if str(role).strip())
    if intent.role:
        roles.append(intent.role)
    primary_intent = plan.primary_intent if plan is not None else "generic_history"
    if primary_intent == "when_user_said":
        roles.append("user")
    if primary_intent in {"why", "workflow_failure", "source_next_reads"}:
        roles.extend(["assistant", "tool"])
    return _unique_ordered_strings(_role_bucket(role) for role in roles)


def _timestamp_query_for_intent(intent: QueryIntent) -> str | None:
    values: list[str] = []
    if intent.date_window_start is not None:
        values.append(intent.date_window_start.strftime("%Y-%m-%d"))
        values.append(intent.date_window_start.strftime("%Y-%m"))
    return _field_query("timestamp_bucket", values)


def _tool_event_query_terms_for_intent(primary_intent: str) -> list[str]:
    if primary_intent == "workflow_failure":
        return ["function_call", "function_call_output", "tool", "error", "failed", "failure"]
    if primary_intent == "source_next_reads":
        return ["function_call", "function_call_output", "tool"]
    return ["function_call", "function_call_output", "tool", "assistant_message", "agent_message"]


def _unique_ordered_strings(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        lowered = str(value).lower().strip()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(lowered)
    return ordered


def _stream_label_part(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return cleaned[:40] or "unknown"


def _escape_fts_phrase(value: str) -> str:
    return value.replace('"', '""')


def _fts_tokens(value: str) -> list[str]:
    return [match.group(0) for match in FTS_TOKEN_RE.finditer(value) if len(match.group(0)) >= 2]


def _tool_event_terms(record: Record) -> str:
    terms: list[str] = []
    for event in detect_events_for_record(record):
        terms.extend([event.event_type, event.event_family, event.tool_name or "", event.action or ""])
        terms.extend(target.value for target in event.targets)
    return " ".join(term for term in terms if term)


def _raw_role_bucket(raw_text: str) -> str:
    role = _raw_json_string(raw_text, "role") or _raw_json_string(raw_text, "type")
    payload_type = _raw_json_string(raw_text, "type")
    if payload_type in {"user_message", "agent_message", "assistant_message", "function_call", "function_call_output"}:
        role = payload_type
    return _role_bucket(role)


def _raw_cwd_project_terms(raw_text: str) -> str:
    cwd = _raw_json_string(raw_text, "cwd") or _raw_json_string(raw_text, "project") or _raw_json_string(
        raw_text, "project_cwd"
    )
    if not cwd:
        return "cwd_absent"
    path = Path(cwd)
    hash_label = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]
    return " ".join([path.name, f"cwd_hash_{hash_label}"])


def _raw_timestamp_bucket(raw_text: str) -> str:
    timestamp = (
        _raw_json_string(raw_text, "timestamp")
        or _raw_json_string(raw_text, "created_at")
        or _raw_json_string(raw_text, "time")
    )
    if timestamp:
        match = TIMESTAMP_BUCKET_RE.match(timestamp)
        if match:
            value = match.group(1)
            if len(value) >= 7:
                return f"{value[:7]} {value}"
            return value
    return "timestamp_absent"


def _raw_tool_event_terms(raw_text: str) -> str:
    terms: list[str] = []
    payload_type = _raw_json_string(raw_text, "type")
    tool_name = _raw_json_string(raw_text, "name")
    if payload_type:
        terms.append(payload_type)
    if tool_name:
        terms.append(tool_name)
    if "function_call" in raw_text:
        terms.extend(["tool", "function_call"])
    if "function_call_output" in raw_text:
        terms.extend(["tool", "function_call_output"])
    return " ".join(sorted(set(terms)))


def _raw_artifact_task_path_terms(raw_text: str) -> str:
    return " ".join(sorted(set(re.findall(r"(?:docs|reports|tasks|codex/state/reviews)/[\w./-]+|\b[\w./-]+\.(?:md|html|json|jsonl|csv|txt|py)\b", raw_text.lower()))))


def _raw_json_string(raw_text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', raw_text)
    if not match:
        return None
    return match.group(1).replace(r"\/", "/")


def _path_terms(record: Record) -> list[str]:
    path = Path(record.path)
    terms = [path.name, path.stem]
    terms.extend(part for part in path.parts[-5:] if part not in {"/", ""})
    return terms


def _cwd_project_terms(record: Record) -> str:
    if not record.cwd:
        return "cwd_absent"
    path = Path(record.cwd)
    hash_label = hashlib.sha256(str(record.cwd).encode("utf-8")).hexdigest()[:12]
    return " ".join([path.name, f"cwd_hash_{hash_label}"])


def _timestamp_bucket(record: Record) -> str:
    if record.timestamp is not None:
        return record.timestamp.strftime("%Y-%m-%d %Y-%m")
    if record.raw_timestamp:
        match = TIMESTAMP_BUCKET_RE.match(record.raw_timestamp)
        if match:
            return match.group(1)
    return "timestamp_absent"


def _role_bucket(role: str | None) -> str:
    lowered = (role or "").lower()
    if "user" in lowered or lowered == "human":
        return "user"
    if "assistant" in lowered or "agent" in lowered:
        return "assistant"
    if "tool" in lowered or "function" in lowered:
        return "tool"
    if "session" in lowered:
        return "session"
    return lowered[:40] if lowered else "unknown"


def _query_profile(intent: QueryIntent, query: str | None) -> dict[str, Any]:
    return {
        "query_term_count": len(intent.terms),
        "base_term_count": len(intent.base_terms),
        "artifact_hint_count": len(intent.artifact_hints),
        "role_present": bool(intent.role),
        "date_window_present": bool(intent.date_window_start is not None or intent.date_window_end is not None),
        "escaped_fts_token_count": len(_fts_tokens(query or "")),
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "expected_refs_used_for_retrieval": False,
    }


def _fts_stats(index: FieldedBm25Index, *, query: str | None) -> dict[str, Any]:
    return {
        "available": index.available,
        "engine": "sqlite_fts5",
        "score_function": "bm25",
        "query_escaped": query is not None,
        "unavailable_reason": index.unavailable_reason,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
    }


def _materialize_match(
    record: Record,
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
    facets = facets_for_record(path=record.path, text=record.text, artifacts=artifacts)
    return Match(
        path=record.path,
        line=record.line,
        role=record.role,
        timestamp=record.timestamp.isoformat() if record.timestamp else None,
        cwd=record.cwd,
        snippet=make_snippet(copied_text, intent.terms),
        score=round(score, 3),
        reasons=sorted(set(["fielded_bm25:candidate"] + reasons), key=(["fielded_bm25:candidate"] + reasons).index),
        artifacts=facets.artifacts,
        session=facets.session,
        tasks=facets.tasks,
        preferences=facets.preferences,
    )


def _init_fielded_expected_ref_diagnostics(expected_refs: list[dict] | None) -> dict | None:
    expected_diag = _init_expected_ref_diagnostics(expected_refs)
    if expected_diag is None:
        return None
    for state in expected_diag["refs"]:
        state["expected_file_indexed_by_fielded_bm25"] = False
        state["expected_record_indexed_by_fielded_bm25"] = False
        state["expected_record_selected_as_bm25_candidate_row"] = False
        state["fielded_candidate_status"] = "not_selected"
        state["bm25_candidate_rank_label"] = None
        state["bm25_candidate_rank_bucket"] = None
        state["score_label_count"] = None
        state["artifact_label_count"] = None
    return expected_diag


def _mark_expected_indexed(expected_diag: dict | None, index: FieldedBm25Index) -> None:
    if expected_diag is None:
        return
    for state in expected_diag["refs"]:
        table = index.files.get(state["_path"])
        state["expected_file_indexed_by_fielded_bm25"] = table is not None
        state["expected_file_selected_by_source_plan"] = table is not None
        state["expected_file_queued_for_scan"] = table is not None
        state["expected_file_label"] = "fielded_indexed_file" if table is not None else None
        state["expected_file_open_order"] = None
        state["expected_record_indexed_by_fielded_bm25"] = any(_ref_contains_line(ref, state) for ref in index.records.values())
        if state["expected_record_indexed_by_fielded_bm25"]:
            state["pre_reservoir_status"] = "indexed_by_fielded_bm25"
            state["pre_final_status"] = "indexed_not_selected_as_bm25_candidate"
            state["final_status"] = "indexed_not_selected_as_bm25_candidate"


def _mark_expected_candidate_rows(expected_diag: dict | None, rows: list[tuple[Any, ...]]) -> None:
    if expected_diag is None:
        return
    rowids = {int(row[0]) for row in rows}
    for state in expected_diag["refs"]:
        if not state.get("expected_record_indexed_by_fielded_bm25"):
            continue
        if not any(True for _rowid in rowids):
            state["fielded_candidate_status"] = "no_bm25_candidate_rows"


def _ref_contains_line(ref: FieldedBm25RecordRef, state: dict[str, Any]) -> bool:
    if str(ref.path) != state["_path"]:
        return False
    line_end = int(ref.line_end or ref.line)
    return int(ref.line) <= int(state["_line"]) <= line_end


def _fielded_expected_state(expected_diag: dict | None, record: Record) -> dict | None:
    if expected_diag is None:
        return None
    return expected_diag["by_ref"].get((str(record.path), int(record.line)))


def _public_fielded_expected_ref_diagnostics(expected_diag: dict) -> dict:
    public = _public_expected_ref_diagnostics(expected_diag)
    by_ref = {state["ref_id"]: state for state in expected_diag["refs"]}
    for ref in public["refs"]:
        state = by_ref.get(ref["ref_id"], {})
        ref["expected_file_indexed_by_fielded_bm25"] = bool(state.get("expected_file_indexed_by_fielded_bm25"))
        ref["expected_record_indexed_by_fielded_bm25"] = bool(state.get("expected_record_indexed_by_fielded_bm25"))
        ref["expected_record_selected_as_bm25_candidate_row"] = bool(
            state.get("expected_record_selected_as_bm25_candidate_row")
        )
        ref["fielded_candidate_status"] = state.get("fielded_candidate_status")
        ref["bm25_candidate_rank_label"] = state.get("bm25_candidate_rank_label")
        ref["bm25_candidate_rank_bucket"] = state.get("bm25_candidate_rank_bucket")
        ref["score_label_count"] = state.get("score_label_count")
        ref["artifact_label_count"] = state.get("artifact_label_count")
    public["fielded_bm25_enabled"] = True
    public["fielded_bm25_indexed_expected_record_count"] = sum(
        1 for state in expected_diag["refs"] if state.get("expected_record_indexed_by_fielded_bm25")
    )
    public["fielded_bm25_candidate_expected_ref_count"] = sum(
        1 for state in expected_diag["refs"] if state.get("expected_record_selected_as_bm25_candidate_row")
    )
    return public


def _unavailable_payload(
    index: FieldedBm25Index,
    started: float,
    warnings: list[str],
    query: str | None,
    expected_diag: dict | None,
    query_streams: list[FieldedQueryStream] | None = None,
    query_stream_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    query_streams = query_streams or []
    query_stream_diagnostics = query_stream_diagnostics or [
        _query_stream_diagnostic(
            stream,
            row_count=0,
            deduped_row_count=0,
            stream_limit=0,
            hit_limit=False,
            status="not_run",
            error_type=None,
        )
        for stream in query_streams
    ]
    return {
        "status": "fts_unavailable",
        "fallback_reason": "sqlite_fts5_unavailable",
        "fts": _fts_stats(index, query=query),
        "query_profile": {"raw_query_text_included": False, "raw_query_terms_included": False},
        "fielded_query_streams": _query_stream_summary(
            streams=query_streams,
            diagnostics=query_stream_diagnostics,
            deduped_row_count=0,
            candidate_pool_limit=0,
        ),
        "candidate_rows": 0,
        "candidate_rows_generated": 0,
        "candidate_rows_retained": 0,
        "candidate_rows_surfaced": 0,
        "searched_files": 0,
        "searched_bytes": 0,
        "records_considered": 0,
        "candidate_records_scored": 0,
        "candidate_records_deduped": 0,
        "materialization": _new_materialization_counters(),
        "bm25_rank_buckets": {},
        "groups": [],
        "summary": index.sanitized_summary(),
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
        **({"expected_ref_diagnostics": _public_fielded_expected_ref_diagnostics(expected_diag)} if expected_diag else {}),
    }


def _empty_payload(
    index: FieldedBm25Index,
    started: float,
    warnings: list[str],
    query: str | None,
    expected_diag: dict | None,
    *,
    status: str,
    query_streams: list[FieldedQueryStream] | None = None,
    query_stream_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _unavailable_payload(
        index,
        started,
        warnings,
        query,
        expected_diag,
        query_streams=query_streams,
        query_stream_diagnostics=query_stream_diagnostics,
    )
    payload["status"] = status
    payload["fallback_reason"] = None
    payload["fts"] = _fts_stats(index, query=query)
    return payload


def _rank_bucket(rank: int) -> str:
    if rank <= 1:
        return "1"
    if rank <= 3:
        return "2-3"
    if rank <= 10:
        return "4-10"
    if rank <= 40:
        return "11-40"
    if rank <= 100:
        return "41-100"
    return "101_plus"


def _privacy_payload() -> dict[str, bool]:
    return {
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
