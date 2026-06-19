from __future__ import annotations

import hashlib
import itertools
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
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
FIELDED_BM25_INDEX_SCHEMA_VERSION = "fielded-bm25-segment-cache-v2"
FIELDED_BM25_PARSER_VERSION = "jsonl-record-parser-v1"
FTS_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*", re.IGNORECASE)
TIMESTAMP_BUCKET_RE = re.compile(r"^(\d{4}-\d{2}(?:-\d{2})?)")
# Keep JSONL rows as source-record candidates so the FTS LIMIT bounds
# downstream parse/rank cost instead of expanding each hit into large chunks.
CHUNK_RECORD_LIMIT = 1
FIELDED_BM25_CHUNKING_VERSION = f"record-limit-{CHUNK_RECORD_LIMIT}"
INDEX_LINE_TEXT_LIMIT = 1200
FIELDED_SOURCE_GROUPING_POOL_FACTOR = 4
FTS_FIELD_NAMES = {"role", "timestamp_bucket", "tool_event", "artifact_task_path", "cwd_project", "text"}
CONTEXT_NEIGHBORHOOD_STREAM_LABEL = "context_neighborhood"
CONTEXT_NEIGHBORHOOD_RADIUS_BEFORE = 5
CONTEXT_NEIGHBORHOOD_RADIUS_AFTER = 2
FIELDED_EVIDENCE_RRF_RANK_CONSTANT = 60
FIELDED_EVIDENCE_SELECTION_POLICY_VERSION = "fielded-evidence-selection-v1"
FIELDED_BM25_CONTENT_FINGERPRINT_STRATEGY = "sha256_full_source_bytes_for_cache_validation"
FIELDED_BM25_SQLITE_TEMP_STORE = "FILE"
FIELDED_BM25_SQLITE_CACHE_SIZE_KIB = 65536
FIELDED_BM25_SQLITE_MMAP_SIZE_BYTES = 0
QUERY_PLAN_MODE_AUTO = "auto"
QUERY_PLAN_MODE_OFF = "off"
QUERY_PLAN_MODE_DEBUG = "debug"
QUERY_PLAN_MODES = {QUERY_PLAN_MODE_AUTO, QUERY_PLAN_MODE_OFF, QUERY_PLAN_MODE_DEBUG}
SUPPORTED_CHILD_CHANNEL_INTENTS = {"when_user_said", "workflow_failure"}
WORKFLOW_FAILURE_MARKERS = ("failure", "failed", "error", "regression", "blocker", "wrong")
WORKFLOW_REPAIR_MARKERS = ("repair", "retry", "fix", "fixed", "rerun", "retest")


@dataclass(frozen=True)
class FieldedBm25RecordRef:
    rowid: int
    path: Path
    line: int
    source_kind: str
    source_ordinal: int
    role_bucket: str = "role_unknown"
    timestamp_bucket: str = "timestamp_absent"
    session_key: str | None = None
    day_key: str | None = None
    raw_text: str = ""
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
            role_bucket = ref.role_bucket
            role_bucket_counts[role_bucket] = role_bucket_counts.get(role_bucket, 0) + 1
            timestamp_bucket = ref.timestamp_bucket
            timestamp_bucket_counts[timestamp_bucket] = timestamp_bucket_counts.get(timestamp_bucket, 0) + 1
        return {
            "version": self.version,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "persistent_index_enabled": bool(self.metrics.get("persistent_index_enabled", False)),
            "index_schema_version": FIELDED_BM25_INDEX_SCHEMA_VERSION,
            "parser_version": FIELDED_BM25_PARSER_VERSION,
            "chunking_version": FIELDED_BM25_CHUNKING_VERSION,
            "sources_reused": int(self.metrics.get("sources_reused", 0)),
            "sources_reindexed": int(self.metrics.get("sources_reindexed", 0)),
            "sources_invalidated": int(self.metrics.get("sources_invalidated", 0)),
            "sources_deleted": int(self.metrics.get("sources_deleted", 0)),
            "segments_loaded": int(self.metrics.get("segments_loaded", 0)),
            "segments_written": int(self.metrics.get("segments_written", 0)),
            "warm_query_reused_index": bool(self.metrics.get("warm_query_reused_index", False)),
            "content_fingerprint_validation_enabled": bool(
                self.metrics.get("content_fingerprint_validation_enabled", False)
            ),
            "content_fingerprint_strategy": FIELDED_BM25_CONTENT_FINGERPRINT_STRATEGY,
            "content_fingerprint_files": int(self.metrics.get("content_fingerprint_files", 0)),
            "content_fingerprint_bytes": int(self.metrics.get("content_fingerprint_bytes", 0)),
            "persistent_reuse_claim": "local_work_reduction_only_not_token_savings_or_final_answer_quality",
            "sqlite_temp_store": str(self.metrics.get("sqlite_temp_store") or FIELDED_BM25_SQLITE_TEMP_STORE),
            "sqlite_cache_size_kib": int(
                self.metrics.get("sqlite_cache_size_kib") or FIELDED_BM25_SQLITE_CACHE_SIZE_KIB
            ),
            "sqlite_mmap_size_bytes": int(
                self.metrics.get("sqlite_mmap_size_bytes") or FIELDED_BM25_SQLITE_MMAP_SIZE_BYTES
            ),
            "sqlite_pressure_settings_enabled": bool(
                self.metrics.get("sqlite_pressure_settings_enabled", True)
            ),
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


@dataclass
class FieldedBm25PersistentCache:
    root: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    sources_deleted: int = 0

    @classmethod
    def open(cls, root: Path) -> "FieldedBm25PersistentCache":
        root = Path(root).expanduser()
        segments_dir = root / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "manifest.json"
        manifest: dict[str, Any] = {
            "schema_version": FIELDED_BM25_INDEX_SCHEMA_VERSION,
            "parser_version": FIELDED_BM25_PARSER_VERSION,
            "chunking_version": FIELDED_BM25_CHUNKING_VERSION,
            "sources": {},
        }
        if manifest_path.exists():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if (
                isinstance(loaded, dict)
                and loaded.get("schema_version") == FIELDED_BM25_INDEX_SCHEMA_VERSION
                and loaded.get("parser_version") == FIELDED_BM25_PARSER_VERSION
                and loaded.get("chunking_version") == FIELDED_BM25_CHUNKING_VERSION
                and isinstance(loaded.get("sources"), dict)
            ):
                manifest = loaded
        cache = cls(root=root, manifest=manifest)
        cache.sources_deleted = cache.prune_missing_segments()
        return cache

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def segments_dir(self) -> Path:
        return self.root / "segments"

    def source_key(self, path: Path) -> str:
        return hashlib.sha256(str(Path(path).expanduser().resolve()).encode("utf-8", errors="replace")).hexdigest()

    def segment_path(self, source_key: str) -> Path:
        return self.segments_dir / f"{source_key}.json"

    def metadata_for(
        self,
        path: Path,
        *,
        size: int,
        source_kind: str,
        content_fingerprint: str,
    ) -> dict[str, Any]:
        stat = path.stat()
        return {
            "schema_version": FIELDED_BM25_INDEX_SCHEMA_VERSION,
            "parser_version": FIELDED_BM25_PARSER_VERSION,
            "chunking_version": FIELDED_BM25_CHUNKING_VERSION,
            "source_kind": source_kind,
            "size": int(size),
            "mtime_ns": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            "content_fingerprint_strategy": FIELDED_BM25_CONTENT_FINGERPRINT_STRATEGY,
            "content_fingerprint": content_fingerprint,
            "source_order_metadata": "one_based_source_ordinal_and_line_bounds",
        }

    def load_segment(
        self,
        path: Path,
        *,
        size: int,
        source_kind: str,
        content_fingerprint: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        source_key = self.source_key(path)
        metadata = self.metadata_for(
            path,
            size=size,
            source_kind=source_kind,
            content_fingerprint=content_fingerprint,
        )
        entry = (self.manifest.get("sources") or {}).get(source_key)
        if not isinstance(entry, dict):
            return None, False
        expected = entry.get("metadata")
        if not isinstance(expected, dict) or any(expected.get(key) != value for key, value in metadata.items()):
            self._drop_source(source_key)
            return None, True
        segment_path = self.segment_path(source_key)
        try:
            segment = json.loads(segment_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._drop_source(source_key)
            return None, True
        if not _valid_persistent_segment(segment, metadata=metadata):
            self._drop_source(source_key)
            return None, True
        return segment, False

    def write_segment(
        self,
        path: Path,
        *,
        size: int,
        source_kind: str,
        record_count: int,
        byte_count: int,
        rows: list[dict[str, Any]],
        content_fingerprint: str,
    ) -> None:
        source_key = self.source_key(path)
        metadata = self.metadata_for(
            path,
            size=size,
            source_kind=source_kind,
            content_fingerprint=content_fingerprint,
        )
        segment = {
            "schema_version": FIELDED_BM25_INDEX_SCHEMA_VERSION,
            "parser_version": FIELDED_BM25_PARSER_VERSION,
            "chunking_version": FIELDED_BM25_CHUNKING_VERSION,
            "metadata": metadata,
            "record_count": int(record_count),
            "byte_count": int(byte_count),
            "row_count": len(rows),
            "content_fingerprint_strategy": FIELDED_BM25_CONTENT_FINGERPRINT_STRATEGY,
            "content_fingerprint": content_fingerprint,
            "rows": rows,
            "raw_paths_included": False,
            "raw_query_text_included": False,
            "vectors_included": False,
        }
        tmp_path = self.segment_path(source_key).with_suffix(".tmp")
        tmp_path.write_text(json.dumps(segment, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self.segment_path(source_key))
        self.manifest.setdefault("sources", {})[source_key] = {
            "metadata": metadata,
            "segment_file": f"segments/{source_key}.json",
            "source_key": source_key,
            "safe_path_identity_hash": source_key,
            "record_count": int(record_count),
            "row_count": len(rows),
            "raw_paths_included": False,
        }

    def prune_missing_segments(self) -> int:
        sources = self.manifest.setdefault("sources", {})
        deleted = 0
        for source_key in list(sources):
            if not self.segment_path(source_key).exists():
                sources.pop(source_key, None)
                deleted += 1
        return deleted

    def prune_unselected_sources(self, selected_source_keys: set[str]) -> int:
        sources = self.manifest.setdefault("sources", {})
        deleted = 0
        for source_key in list(sources):
            if source_key not in selected_source_keys:
                self._drop_source(source_key)
                deleted += 1
        return deleted

    def save(self) -> None:
        payload = {
            "schema_version": FIELDED_BM25_INDEX_SCHEMA_VERSION,
            "parser_version": FIELDED_BM25_PARSER_VERSION,
            "chunking_version": FIELDED_BM25_CHUNKING_VERSION,
            "sources": self.manifest.get("sources") or {},
        }
        tmp_path = self.manifest_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(self.manifest_path)
        self.manifest = payload

    def _drop_source(self, source_key: str) -> None:
        try:
            self.segment_path(source_key).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self.manifest.setdefault("sources", {}).pop(source_key, None)


@dataclass(frozen=True)
class FieldedQueryStream:
    label: str
    query: str
    fields: tuple[str, ...]
    channel: str = "query_stream"
    intent: str = "generic_history"
    kind: str = "base"
    reason_codes: tuple[str, ...] = ()
    child: bool = False
    skip_reason_code: str | None = None


@dataclass(frozen=True)
class FieldedEvidenceChannel:
    label: str
    kind: str
    rows: list[tuple[Any, ...]]


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
    persistent_index_dir: Path | str | None = None,
) -> FieldedBm25Index:
    started = time.perf_counter()
    profile_token = start_phase_profile("fielded_bm25_index_build")
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA temp_store = FILE")
        connection.execute(f"PRAGMA cache_size = -{FIELDED_BM25_SQLITE_CACHE_SIZE_KIB}")
        connection.execute(f"PRAGMA mmap_size = {FIELDED_BM25_SQLITE_MMAP_SIZE_BYTES}")
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
            "persistent_index_enabled": persistent_index_dir is not None,
            "sources_reused": 0,
            "sources_reindexed": 0,
            "sources_invalidated": 0,
            "sources_deleted": 0,
            "segments_loaded": 0,
            "segments_written": 0,
            "warm_query_reused_index": False,
            "content_fingerprint_validation_enabled": persistent_index_dir is not None,
            "content_fingerprint_files": 0,
            "content_fingerprint_bytes": 0,
            "sqlite_temp_store": FIELDED_BM25_SQLITE_TEMP_STORE,
            "sqlite_cache_size_kib": FIELDED_BM25_SQLITE_CACHE_SIZE_KIB,
            "sqlite_mmap_size_bytes": FIELDED_BM25_SQLITE_MMAP_SIZE_BYTES,
            "sqlite_pressure_settings_enabled": True,
        },
    )
    persistent_cache = FieldedBm25PersistentCache.open(Path(persistent_index_dir)) if persistent_index_dir else None
    if persistent_cache is not None:
        index.metrics["sources_deleted"] = persistent_cache.sources_deleted
    rowid = 0
    insert_buffer: list[tuple[int, str, str, str, str, str, str, str]] = []
    if planned_jsonl_files is not None:
        enumerated_files = [(path, size, list(planned_jsonl_files.warnings)) for path, size in planned_jsonl_files.files]
    else:
        enumerated_files = enumerate_jsonl_files(plan)
    if persistent_cache is not None:
        selected_source_keys = {persistent_cache.source_key(path) for path, _size, _warnings in enumerated_files}
        index.metrics["sources_deleted"] = int(index.metrics.get("sources_deleted", 0)) + persistent_cache.prune_unselected_sources(
            selected_source_keys
        )

    for path, size, enum_warnings in enumerated_files:
        index.warnings = list(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        file_records = 0
        file_bytes = int(size) if planned_jsonl_files is not None else stat_size(path)
        segment_start_rowid = rowid
        content_hasher = hashlib.sha256()
        content_fingerprint = ""
        if persistent_cache is not None:
            content_fingerprint, fingerprint_bytes = _source_content_fingerprint(path)
            index.metrics["content_fingerprint_files"] += 1
            index.metrics["content_fingerprint_bytes"] += fingerprint_bytes
            segment, invalidated = persistent_cache.load_segment(
                path,
                size=file_bytes,
                source_kind=source_kind,
                content_fingerprint=content_fingerprint,
            )
            if invalidated:
                index.metrics["sources_invalidated"] += 1
            if segment is not None:
                rowid = _load_persistent_segment_rows(
                    connection,
                    index,
                    insert_buffer,
                    rowid,
                    path=path,
                    source_kind=source_kind,
                    segment=segment,
                )
                file_records = int(segment.get("record_count") or 0)
                index.files[str(path)] = {
                    "source_kind": source_kind,
                    "record_count": file_records,
                    "byte_count": file_bytes,
                }
                index.metrics["indexed_files"] += 1
                index.metrics["indexed_records"] += file_records
                index.metrics["build_bytes"] += file_bytes
                index.metrics["sources_reused"] += 1
                index.metrics["segments_loaded"] += int(segment.get("row_count") or len(segment.get("rows") or []))
                continue
            index.metrics["sources_reindexed"] += 1
        try:
            if path.suffix == ".jsonl":
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    chunk_lines: list[tuple[int, str]] = []
                    for line_number, raw_line in enumerate(handle, 1):
                        content_hasher.update(raw_line.encode("utf-8", errors="replace"))
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
                    content_hasher.update(record.text.encode("utf-8", errors="replace"))
                    rowid += 1
                    file_records += 1
                    _insert_record(connection, rowid, record, source_kind)
                    index.records[rowid] = FieldedBm25RecordRef(
                        rowid=rowid,
                        path=Path(record.path),
                        line=int(record.line),
                        source_kind=source_kind,
                        raw_text=record.text,
                        source_ordinal=file_records,
                        role_bucket=_role_bucket(record.role),
                        timestamp_bucket=_timestamp_bucket(record),
                        session_key=_record_session_grouping_key(record),
                        day_key=_record_day_grouping_key(record),
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
        if persistent_cache is not None:
            segment_rows = [
                _persistent_row_from_ref(connection, index.records[candidate_rowid])
                for candidate_rowid in range(segment_start_rowid + 1, rowid + 1)
                if candidate_rowid in index.records
            ]
            persistent_cache.write_segment(
                path,
                size=file_bytes,
                source_kind=source_kind,
                record_count=file_records,
                byte_count=file_bytes,
                rows=segment_rows,
                content_fingerprint=content_fingerprint or content_hasher.hexdigest(),
            )
            index.metrics["segments_written"] += len(segment_rows)
    _flush_insert_buffer(connection, insert_buffer)
    connection.commit()
    if persistent_cache is not None:
        persistent_cache.save()
        index.metrics["warm_query_reused_index"] = bool(index.metrics.get("sources_reused", 0))
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
    query_plan_mode: str = QUERY_PLAN_MODE_AUTO,
) -> dict[str, Any]:
    started = time.perf_counter()
    profile_token = start_phase_profile("fielded_bm25_search_materialization")
    warnings = list(index.warnings) + list(plan.warnings)
    query = _fts_query_for_intent(intent)
    query_plan_mode = _normalize_query_plan_mode(query_plan_mode)
    query_streams = _fielded_query_streams_for_intent(intent, query, query_plan_mode=query_plan_mode)
    child_plan_summary = _child_query_plan_summary(
        intent=intent,
        streams=query_streams,
        query_plan_mode=query_plan_mode,
        diagnostics=[],
        candidate_pool_limit=0,
    )
    expected_diag = _init_fielded_expected_ref_diagnostics(expected_ref_diagnostics)
    _mark_expected_indexed(expected_diag, index)
    if not index.available or index.connection is None:
        payload = _unavailable_payload(
            index,
            started,
            warnings,
            query,
            expected_diag,
            query_streams=query_streams,
            child_query_plan=child_plan_summary,
        )
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
            child_query_plan=child_plan_summary,
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
            child_query_plan=_child_query_plan_summary(
                intent=intent,
                streams=query_streams,
                query_plan_mode=query_plan_mode,
                diagnostics=stream_diagnostics,
                candidate_pool_limit=candidate_pool_limit,
            ),
        )
        payload["profile"] = finish_phase_profile(profile_token, candidate_rows=0, candidate_count=0)
        return payload
    evidence_channels = _build_fielded_evidence_channels(
        raw_rows=raw_rows,
        query_streams=query_streams,
        stream_rows=stream_rows,
        candidate_pool_limit=candidate_pool_limit,
    )
    context_neighborhood = _context_neighborhood_candidate_rows(
        raw_rows,
        index,
        intent,
        row_stream_labels=row_stream_labels,
        stream_rows=stream_rows,
        row_limit=candidate_limit,
    )
    if context_neighborhood["rows"]:
        evidence_channels.insert(
            0,
            FieldedEvidenceChannel(
                label=CONTEXT_NEIGHBORHOOD_STREAM_LABEL,
                kind="structural_neighborhood",
                rows=sorted(
                    list(context_neighborhood["rows"]),
                    key=lambda row: _context_neighborhood_selection_key(index, int(row[0])),
                ),
            )
        )
    selection_rows, fusion = _fuse_fielded_evidence_channels(
        evidence_channels,
        candidate_pool_limit=candidate_pool_limit,
    )
    if context_neighborhood["enabled"]:
        selection_rows = sorted(selection_rows, key=lambda row: _context_neighborhood_selection_key(index, int(row[0])))
    rows, source_grouping = _select_source_grouped_candidate_rows(
        selection_rows,
        index,
        selected_limit=candidate_limit,
        pool_limit=candidate_pool_limit,
        row_stream_labels=row_stream_labels,
        stream_rows=stream_rows,
        fusion=fusion,
    )
    context_neighborhood["selected_neighbor_rows"] = sum(
        1 for row in rows if int(row[0]) in context_neighborhood["generated_rowids"]
    )
    context_neighborhood["omitted_neighbor_rows"] = max(
        int(context_neighborhood["generated_neighbor_rows"]) - int(context_neighborhood["selected_neighbor_rows"]),
        0,
    )
    context_neighborhood_summary = _context_neighborhood_diagnostics(
        context_neighborhood,
        source_grouping=source_grouping,
    )
    query_stream_summary = _query_stream_summary(
        streams=query_streams,
        diagnostics=stream_diagnostics,
        deduped_row_count=len(raw_rows),
        candidate_pool_limit=candidate_pool_limit,
        selection=source_grouping.get("stream_selection"),
        query_plan_mode=query_plan_mode,
    )
    child_plan_summary = _child_query_plan_summary(
        intent=intent,
        streams=query_streams,
        query_plan_mode=query_plan_mode,
        diagnostics=stream_diagnostics,
        candidate_pool_limit=candidate_pool_limit,
        selection=source_grouping.get("stream_selection"),
    )
    evidence_channel_summary = _fielded_evidence_channel_summary(
        fusion=fusion,
        selected_rows=rows,
        candidate_pool_limit=candidate_pool_limit,
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
            if rowid in context_neighborhood["generated_rowids"]:
                reasons = [*reasons, "fielded_bm25:context_neighborhood"]
                if score <= 0:
                    score = 1.0
            for label in _selected_evidence_reason_labels(rowid, fusion):
                reasons = [*reasons, f"fielded_bm25:evidence_channel:{label}"]
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
        "query_plan_mode": query_plan_mode,
        "fielded_child_query_plan": child_plan_summary,
        "fielded_query_streams": query_stream_summary,
        "fielded_evidence_channels": evidence_channel_summary,
        "candidate_rows": len(rows),
        "candidate_row_limit": candidate_limit,
        "candidate_row_pool_limit": candidate_pool_limit,
        "candidate_rows_generated": len(rows),
        "candidate_rows_considered": len(selection_rows),
        "candidate_rows_from_query_streams": len(raw_rows),
        "candidate_rows_retained": pre_cap_count,
        "candidate_rows_surfaced": len(matches),
        "fielded_context_neighborhood": context_neighborhood_summary,
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
        "_fielded_context_index": _fielded_context_index(index),
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
    connection.execute(
        """
        INSERT INTO records(
            rowid, source_kind, role, cwd_project, timestamp_bucket, tool_event, artifact_task_path, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _record_insert_row(rowid, record, source_kind),
    )


def _record_insert_row(rowid: int, record: Record, source_kind: str) -> tuple[int, str, str, str, str, str, str, str]:
    facets = facets_for_record(path=record.path, text=record.text, artifacts=[])
    tool_event_terms = _tool_event_terms(record)
    artifact_task_path_terms = " ".join(sorted(set(facets.artifacts + facets.tasks + _path_terms(record))))
    return (
        rowid,
        source_kind,
        _role_bucket(record.role),
        _cwd_project_terms(record),
        _timestamp_bucket(record),
        tool_event_terms,
        artifact_task_path_terms,
        record.text,
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


def _persistent_row_from_ref(connection: sqlite3.Connection, ref: FieldedBm25RecordRef) -> dict[str, Any]:
    fts = _fts_payload_for_rowid(connection, ref.rowid, fallback_source_kind=ref.source_kind)
    if ref.record is not None:
        record_payload = _record_to_persistent_payload(ref.record)
    else:
        record_payload = None
    return {
        "line": int(ref.line),
        "line_end": int(ref.line_end or ref.line),
        "source_ordinal": int(ref.source_ordinal),
        "source_kind": ref.source_kind,
        "role_bucket": ref.role_bucket,
        "timestamp_bucket": ref.timestamp_bucket,
        "session_key": ref.session_key,
        "day_key": ref.day_key,
        "record": record_payload,
        "fts": fts,
    }


def _fts_payload_for_rowid(
    connection: sqlite3.Connection,
    rowid: int,
    *,
    fallback_source_kind: str,
) -> dict[str, str]:
    try:
        row = connection.execute(
            """
            SELECT source_kind, role, cwd_project, timestamp_bucket, tool_event, artifact_task_path, text
            FROM records
            WHERE rowid = ?
            """,
            (rowid,),
        ).fetchone()
    except sqlite3.Error:
        row = None
    values = tuple(str(item or "") for item in row) if row is not None else ("", "", "", "", "", "", "")
    return {
        "source_kind": values[0] or fallback_source_kind,
        "role": values[1],
        "cwd_project": values[2],
        "timestamp_bucket": values[3],
        "tool_event": values[4],
        "artifact_task_path": values[5],
        "text": values[6],
    }


def _load_persistent_segment_rows(
    connection: sqlite3.Connection,
    index: FieldedBm25Index,
    insert_buffer: list[tuple[int, str, str, str, str, str, str, str]],
    rowid: int,
    *,
    path: Path,
    source_kind: str,
    segment: dict[str, Any],
) -> int:
    for row in segment.get("rows") or []:
        if not isinstance(row, dict):
            continue
        fts = row.get("fts") if isinstance(row.get("fts"), dict) else {}
        rowid += 1
        insert_buffer.append(
            (
                rowid,
                str(fts.get("source_kind") or source_kind),
                str(fts.get("role") or ""),
                str(fts.get("cwd_project") or ""),
                str(fts.get("timestamp_bucket") or ""),
                str(fts.get("tool_event") or ""),
                str(fts.get("artifact_task_path") or ""),
                str(fts.get("text") or row.get("raw_text") or ""),
            )
        )
        record_payload = row.get("record") if isinstance(row.get("record"), dict) else None
        record = _record_from_persistent_payload(record_payload, path=path) if record_payload else None
        fts_text = str(fts.get("text") or row.get("raw_text") or "")
        role_bucket = str(row.get("role_bucket") or fts.get("role") or _raw_role_bucket(fts_text))
        timestamp_bucket = str(row.get("timestamp_bucket") or fts.get("timestamp_bucket") or _raw_timestamp_bucket(fts_text))
        index.records[rowid] = FieldedBm25RecordRef(
            rowid=rowid,
            path=path,
            line=int(row.get("line") or 1),
            line_end=int(row.get("line_end") or row.get("line") or 1),
            source_kind=source_kind,
            source_ordinal=int(row.get("source_ordinal") or row.get("line") or 1),
            role_bucket=role_bucket,
            timestamp_bucket=timestamp_bucket,
            session_key=str(row.get("session_key")) if row.get("session_key") else _raw_session_grouping_key(fts_text),
            day_key=str(row.get("day_key")) if row.get("day_key") else _day_key_from_timestamp_bucket(timestamp_bucket),
            record=record,
        )
        index.metrics["indexed_chunks"] += 1
        if len(insert_buffer) >= 500:
            _flush_insert_buffer(connection, insert_buffer)
    return rowid


def _record_to_persistent_payload(record: Record) -> dict[str, Any]:
    return {
        "line": int(record.line),
        "role": record.role,
        "text": record.text,
        "timestamp": record.timestamp.isoformat() if record.timestamp is not None else None,
        "cwd": record.cwd,
        "raw_timestamp": record.raw_timestamp,
        "source_format": record.source_format,
        "record_id": record.record_id,
        "parent_id": record.parent_id,
        "conversation_id": record.conversation_id,
    }


def _record_from_persistent_payload(payload: dict[str, Any] | None, *, path: Path) -> Record | None:
    if not isinstance(payload, dict):
        return None
    timestamp = None
    raw_timestamp = payload.get("timestamp")
    if isinstance(raw_timestamp, str) and raw_timestamp:
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            timestamp = None
    return Record(
        path=str(path),
        line=int(payload.get("line") or 1),
        role=str(payload.get("role") or ""),
        text=str(payload.get("text") or ""),
        timestamp=timestamp,
        cwd=str(payload.get("cwd")) if payload.get("cwd") else None,
        raw_timestamp=str(payload.get("raw_timestamp")) if payload.get("raw_timestamp") else None,
        raw=None,
        source_format=str(payload.get("source_format")) if payload.get("source_format") else None,
        record_id=str(payload.get("record_id")) if payload.get("record_id") else None,
        parent_id=str(payload.get("parent_id")) if payload.get("parent_id") else None,
        conversation_id=str(payload.get("conversation_id")) if payload.get("conversation_id") else None,
    )


def _valid_persistent_segment(segment: Any, *, metadata: dict[str, Any]) -> bool:
    if not isinstance(segment, dict):
        return False
    if segment.get("schema_version") != FIELDED_BM25_INDEX_SCHEMA_VERSION:
        return False
    if segment.get("parser_version") != FIELDED_BM25_PARSER_VERSION:
        return False
    if segment.get("chunking_version") != FIELDED_BM25_CHUNKING_VERSION:
        return False
    segment_metadata = segment.get("metadata")
    if not isinstance(segment_metadata, dict):
        return False
    if any(segment_metadata.get(key) != value for key, value in metadata.items()):
        return False
    rows = segment.get("rows")
    if not isinstance(rows, list):
        return False
    return all(
        isinstance(row, dict)
        and isinstance(row.get("source_ordinal"), int)
        and isinstance(row.get("line"), int)
        and isinstance(row.get("line_end"), int)
        for row in rows
    )


def _source_content_fingerprint(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            byte_count += len(chunk)
            hasher.update(chunk)
    return hasher.hexdigest(), byte_count


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
    insert_buffer.append(_raw_insert_row(rowid, raw_text, source_kind))
    index.records[rowid] = FieldedBm25RecordRef(
        rowid=rowid,
        path=path,
        line=first_line,
        line_end=last_line,
        source_kind=source_kind,
        source_ordinal=first_line,
        role_bucket=_raw_role_bucket(raw_text),
        timestamp_bucket=_raw_timestamp_bucket(raw_text),
        session_key=_raw_session_grouping_key(raw_text),
        day_key=_day_key_from_timestamp_bucket(_raw_timestamp_bucket(raw_text)),
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
    fusion: dict[str, Any] | None = None,
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    bounded_limit = max(int(selected_limit), 0)
    row_stream_labels = row_stream_labels or {}
    stream_rows = stream_rows or {}
    fusion = fusion or {}
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
            group_category_counts=_empty_group_category_counts(),
            representative_reason_counts={},
            selected_representative_reason_counts={},
            group_interleaved_rows_selected=0,
            adjacent_run_collapse=_adjacent_run_collapse_diagnostics([], [], {}, {}),
            group_interleaving=_group_interleaving_diagnostics([], {}, {}, {}),
            selection_policy=_evidence_selection_policy_diagnostics(
                rows,
                [],
                index,
                row_stream_labels=row_stream_labels,
                stream_rows=stream_rows,
                fusion=fusion,
                representative_to_suppressed={},
                selected_representative_reason_counts={},
            ),
        )

    bm25_rank_by_rowid = {int(row[0]): rank for rank, row in enumerate(rows, start=1)}
    considered_source_counts: dict[str, int] = {}
    considered_group_counts: dict[tuple[str, str, str], int] = {}
    row_by_id: dict[int, tuple[Any, ...]] = {}
    for row in rows:
        rowid = int(row[0])
        row_by_id[rowid] = row
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key = _source_grouping_key(ref)
        group_key = _source_session_date_grouping_key(ref)
        considered_source_counts[source_key] = considered_source_counts.get(source_key, 0) + 1
        considered_group_counts[group_key] = considered_group_counts.get(group_key, 0) + 1

    representatives, run_sizes, representative_to_suppressed = _collapse_adjacent_candidate_runs(
        rows,
        index,
        bm25_rank_by_rowid=bm25_rank_by_rowid,
    )
    protected_rows = _protected_evidence_selection_rows(
        rows,
        index,
        row_stream_labels=row_stream_labels,
        fusion=fusion,
        bm25_rank_by_rowid=bm25_rank_by_rowid,
    )
    representative_rowids = {int(row[0]) for row in representatives}
    if protected_rows:
        representatives = [
            *representatives,
            *(row for row in protected_rows if int(row[0]) not in representative_rowids),
        ]
        representatives.sort(key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
    group_interleaved_rows = _interleave_group_representatives(
        representatives,
        index,
        bm25_rank_by_rowid=bm25_rank_by_rowid,
        selected_limit=bounded_limit,
    )
    selected, representative_reason_counts, selected_representative_reason_counts, group_category_counts = (
        _select_source_session_day_category_representatives(
            representatives,
            group_interleaved_rows,
            index,
            bm25_rank_by_rowid=bm25_rank_by_rowid,
            selected_limit=bounded_limit,
            seed_rows=protected_rows,
        )
    )
    selected_rowids: set[int] = {int(row[0]) for row in selected}

    selected_source_counts: dict[str, int] = {}
    selected_group_counts: dict[tuple[str, str, str], int] = {}
    selected_stream_counts: dict[str, int] = {}
    for row in selected:
        rowid = int(row[0])
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key = _source_grouping_key(ref)
        group_key = _source_session_date_grouping_key(ref)
        selected_source_counts[source_key] = selected_source_counts.get(source_key, 0) + 1
        selected_group_counts[group_key] = selected_group_counts.get(group_key, 0) + 1
        for label in row_stream_labels.get(rowid, set()):
            selected_stream_counts[label] = selected_stream_counts.get(label, 0) + 1
    rows_dropped_by_source_cap = sum(
        max(int(count) - int(selected_source_counts.get(source_key, 0)), 0)
        for source_key, count in considered_source_counts.items()
    )
    dropped_stream_counts = _stream_dropped_counts(stream_rows, selected_rowids)
    group_keys_considered = set(considered_group_counts)
    group_keys_selected = set(selected_group_counts)
    adjacent_run_collapse = _adjacent_run_collapse_diagnostics(
        rows,
        representatives,
        run_sizes,
        representative_to_suppressed,
    )
    group_interleaving = _group_interleaving_diagnostics(
        selected,
        considered_group_counts,
        selected_group_counts,
        {
            "sources": {key[0] for key in group_keys_considered},
            "sessions": {key[1] for key in group_keys_considered},
            "days": {key[2] for key in group_keys_considered},
            "selected_sources": {key[0] for key in group_keys_selected},
            "selected_sessions": {key[1] for key in group_keys_selected},
            "selected_days": {key[2] for key in group_keys_selected},
        },
    )

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
        group_category_counts=group_category_counts,
        representative_reason_counts=representative_reason_counts,
        selected_representative_reason_counts=selected_representative_reason_counts,
        group_interleaved_rows_selected=len(group_interleaved_rows),
        adjacent_run_collapse=adjacent_run_collapse,
        group_interleaving=group_interleaving,
        selection_policy=_evidence_selection_policy_diagnostics(
            rows,
            selected,
            index,
            row_stream_labels=row_stream_labels,
            stream_rows=stream_rows,
            fusion=fusion,
            representative_to_suppressed=representative_to_suppressed,
            selected_representative_reason_counts=selected_representative_reason_counts,
        ),
    )


def _context_neighborhood_candidate_rows(
    anchor_rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    intent: QueryIntent,
    *,
    row_stream_labels: dict[int, set[str]],
    stream_rows: dict[str, list[tuple[Any, ...]]],
    row_limit: int,
) -> dict[str, Any]:
    enabled, reasons = _context_neighborhood_intent(intent)
    bounded_limit = max(int(row_limit), 0)
    diagnostics: dict[str, Any] = {
        "enabled": enabled,
        "strategy": "disabled" if not enabled else "bounded_same_source_session_adjacent_rows_from_fielded_anchors",
        "intent_detected": enabled,
        "intent_reason_codes": reasons,
        "anchor_rows_considered": int(len(anchor_rows)),
        "anchor_row_limit": bounded_limit,
        "neighbor_radius_before": CONTEXT_NEIGHBORHOOD_RADIUS_BEFORE,
        "neighbor_radius_after": CONTEXT_NEIGHBORHOOD_RADIUS_AFTER,
        "generated_neighbor_rows": 0,
        "selected_neighbor_rows": 0,
        "omitted_neighbor_rows": 0,
        "deduped_existing_row_count": 0,
        "deduped_generated_row_count": 0,
        "rows": [],
        "generated_rowids": set(),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }
    if not enabled or bounded_limit <= 0 or not anchor_rows:
        return diagnostics

    anchor_rowids = [int(row[0]) for row in anchor_rows]
    existing_rowids = set(anchor_rowids)
    row_by_id = {int(row[0]): row for row in anchor_rows}
    anchor_score_by_rowid = {int(row[0]): float(row[1] or 0.0) for row in anchor_rows}
    group_positions: dict[tuple[str, str], dict[int, int]] = {}
    group_rowids: dict[tuple[str, str], list[int]] = {}
    for rowid, ref in index.records.items():
        source_key, session_key, _day_key = _source_session_date_grouping_key(ref)
        group_rowids.setdefault((source_key, session_key), []).append(int(rowid))
    for key, rowids in group_rowids.items():
        rowids.sort(key=lambda candidate: (_line_order_key(index.records[candidate]), candidate))
        group_positions[key] = {rowid: position for position, rowid in enumerate(rowids)}

    generated_rows: list[tuple[Any, ...]] = []
    generated_rowids: set[int] = set()
    omitted_by_limit = 0
    for anchor_row in anchor_rows[:bounded_limit]:
        anchor_rowid = int(anchor_row[0])
        anchor_ref = index.records.get(anchor_rowid)
        if anchor_ref is None:
            continue
        source_key, session_key, _day_key = _source_session_date_grouping_key(anchor_ref)
        group_key = (source_key, session_key)
        rowids = group_rowids.get(group_key) or []
        position = group_positions.get(group_key, {}).get(anchor_rowid)
        if position is None:
            continue
        offsets = [
            *range(-CONTEXT_NEIGHBORHOOD_RADIUS_BEFORE, 0),
            *range(1, CONTEXT_NEIGHBORHOOD_RADIUS_AFTER + 1),
        ]
        for offset in offsets:
            neighbor_position = position + offset
            if neighbor_position < 0 or neighbor_position >= len(rowids):
                continue
            neighbor_rowid = int(rowids[neighbor_position])
            row_stream_labels.setdefault(neighbor_rowid, set()).add(CONTEXT_NEIGHBORHOOD_STREAM_LABEL)
            if neighbor_rowid in existing_rowids:
                diagnostics["deduped_existing_row_count"] = int(diagnostics["deduped_existing_row_count"]) + 1
                continue
            if neighbor_rowid in generated_rowids:
                diagnostics["deduped_generated_row_count"] = int(diagnostics["deduped_generated_row_count"]) + 1
                continue
            if len(generated_rows) >= bounded_limit:
                omitted_by_limit += 1
                continue
            generated_rowids.add(neighbor_rowid)
            neighbor_row = row_by_id.get(neighbor_rowid)
            if neighbor_row is None:
                neighbor_row = (neighbor_rowid, anchor_score_by_rowid.get(anchor_rowid, 0.0))
            generated_rows.append(neighbor_row)
            stream_rows.setdefault(CONTEXT_NEIGHBORHOOD_STREAM_LABEL, []).append(neighbor_row)

    diagnostics["rows"] = generated_rows
    diagnostics["generated_rowids"] = generated_rowids
    diagnostics["generated_neighbor_rows"] = len(generated_rows)
    diagnostics["omitted_neighbor_rows"] = omitted_by_limit
    return diagnostics


def _context_neighborhood_intent(intent: QueryIntent) -> tuple[bool, list[str]]:
    plan = intent.query_plan
    primary = plan.primary_intent if plan is not None else ""
    detected = set(plan.detected_intents if plan is not None else [])
    source_window_needs = set(plan.source_window_needs if plan is not None else [])
    reasons: list[str] = []
    if primary == "what_was_i_talking_about" or "what_was_i_talking_about" in detected:
        reasons.append("intent:what_was_i_talking_about")
    if "topic_context" in source_window_needs:
        reasons.append("source_window_need:topic_context")
    if re.search(r"\b(?:then|around|before|after|nearby|surrounding|context|previous|next|earlier|later)\b", intent.normalized):
        reasons.append("deictic_or_context_term")
    if reasons and (intent.base_terms or primary == "what_was_i_talking_about"):
        return True, _unique_ordered_strings(reasons)
    return False, _unique_ordered_strings(reasons)


def _context_neighborhood_diagnostics(value: dict[str, Any], *, source_grouping: dict[str, Any]) -> dict[str, Any]:
    stream_selection = source_grouping.get("stream_selection") if isinstance(source_grouping, dict) else {}
    selected_by_stream = stream_selection.get("selected_rows_by_stream") if isinstance(stream_selection, dict) else {}
    dropped_by_stream = stream_selection.get("rows_dropped_by_stream_cap") if isinstance(stream_selection, dict) else {}
    return {
        "enabled": bool(value.get("enabled", False)),
        "strategy": str(value.get("strategy") or "disabled"),
        "intent_detected": bool(value.get("intent_detected", False)),
        "intent_reason_codes": [str(reason) for reason in value.get("intent_reason_codes") or []],
        "anchor_rows_considered": int(value.get("anchor_rows_considered") or 0),
        "anchor_row_limit": int(value.get("anchor_row_limit") or 0),
        "neighbor_radius_before": int(value.get("neighbor_radius_before") or 0),
        "neighbor_radius_after": int(value.get("neighbor_radius_after") or 0),
        "generated_neighbor_rows": int(value.get("generated_neighbor_rows") or 0),
        "selected_neighbor_rows": int(value.get("selected_neighbor_rows") or 0),
        "omitted_neighbor_rows": int(value.get("omitted_neighbor_rows") or 0),
        "deduped_existing_row_count": int(value.get("deduped_existing_row_count") or 0),
        "deduped_generated_row_count": int(value.get("deduped_generated_row_count") or 0),
        "participated_in_grouping": bool(value.get("generated_neighbor_rows") or 0),
        "selected_by_group_interleaving": int(selected_by_stream.get(CONTEXT_NEIGHBORHOOD_STREAM_LABEL) or 0),
        "dropped_by_group_interleaving": int(dropped_by_stream.get(CONTEXT_NEIGHBORHOOD_STREAM_LABEL) or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _context_neighborhood_selection_key(index: FieldedBm25Index, rowid: int) -> tuple[str, str, int, int]:
    ref = index.records.get(rowid)
    if ref is None:
        return ("", "", 10**12, rowid)
    source_key, session_key, _day_key = _source_session_date_grouping_key(ref)
    return (source_key, session_key, int(ref.line), rowid)


def _source_grouping_key(ref: FieldedBm25RecordRef) -> str:
    return str(ref.path)


def _line_order_key(ref: FieldedBm25RecordRef) -> tuple[int, int]:
    return int(ref.line), int(ref.line_end or ref.line)


def _source_session_date_grouping_key(ref: FieldedBm25RecordRef) -> tuple[str, str, str]:
    source_key = _source_grouping_key(ref)
    session_key = _session_grouping_key(ref) or source_key
    day_key = _day_grouping_key(ref) or "day_absent"
    return source_key, session_key, day_key


def _session_grouping_key(ref: FieldedBm25RecordRef) -> str | None:
    return ref.session_key


def _day_grouping_key(ref: FieldedBm25RecordRef) -> str | None:
    return ref.day_key


def _day_key_from_timestamp_bucket(timestamp_bucket: str) -> str | None:
    bucket_parts = timestamp_bucket.split()
    day_key = bucket_parts[-1] if bucket_parts and timestamp_bucket != "timestamp_absent" else None
    if day_key is not None and len(day_key) == 7:
        day_key = f"{day_key}-xx"
    return day_key


def _collapse_adjacent_candidate_runs(
    rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    bm25_rank_by_rowid: dict[int, int],
) -> tuple[list[tuple[Any, ...]], dict[int, int], dict[int, int]]:
    row_by_id = {int(row[0]): row for row in rows}
    sortable: list[tuple[tuple[str, str], int, int, int]] = []
    for row in rows:
        rowid = int(row[0])
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key, session_key, _day_key = _source_session_date_grouping_key(ref)
        sortable.append(((source_key, session_key), int(ref.line), rowid, int(ref.line_end or ref.line)))
    sortable.sort(key=lambda item: (item[0][0], item[0][1], item[1], item[2]))

    run_sizes: dict[int, int] = {}
    representative_to_suppressed: dict[int, int] = {}
    representatives: list[tuple[Any, ...]] = []
    current_key: tuple[str, str] | None = None
    current_run: list[int] = []
    previous_line_end: int | None = None

    def flush_run() -> None:
        if not current_run:
            return
        representative = min(current_run, key=lambda rowid: (bm25_rank_by_rowid.get(rowid, 10**12), rowid))
        run_sizes[representative] = len(current_run)
        representative_to_suppressed[representative] = max(len(current_run) - 1, 0)
        row = row_by_id.get(representative)
        if row is not None:
            representatives.append(row)

    for key, line, rowid, line_end in sortable:
        adjacent = current_key == key and previous_line_end is not None and line == previous_line_end + 1
        if current_key is None or not adjacent:
            flush_run()
            current_key = key
            current_run = [rowid]
        else:
            current_run.append(rowid)
        previous_line_end = line_end
    flush_run()
    representatives.sort(key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
    return representatives, run_sizes, representative_to_suppressed


def _interleave_group_representatives(
    rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    bm25_rank_by_rowid: dict[int, int],
    selected_limit: int,
) -> list[tuple[Any, ...]]:
    grouped: dict[tuple[str, str, str], list[tuple[Any, ...]]] = {}
    for row in rows:
        rowid = int(row[0])
        ref = index.records.get(rowid)
        if ref is None:
            continue
        grouped.setdefault(_source_session_date_grouping_key(ref), []).append(row)
    for group_rows in grouped.values():
        group_rows.sort(key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
    selected: list[tuple[Any, ...]] = []
    max_group_depth = max((len(group_rows) for group_rows in grouped.values()), default=0)
    for within_group_rank in range(max_group_depth):
        round_rows = [
            group_rows[within_group_rank]
            for group_rows in grouped.values()
            if within_group_rank < len(group_rows)
        ]
        round_rows.sort(key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
        for row in round_rows:
            if len(selected) >= selected_limit:
                return selected
            selected.append(row)
    return selected


def _select_source_session_day_category_representatives(
    rows: list[tuple[Any, ...]],
    stable_fill_rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    bm25_rank_by_rowid: dict[int, int],
    selected_limit: int,
    seed_rows: list[tuple[Any, ...]] | None = None,
) -> tuple[list[tuple[Any, ...]], dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    categories = ("source", "session", "day")
    grouped: dict[str, dict[str, list[tuple[Any, ...]]]] = {category: {} for category in categories}
    for row in rows:
        rowid = int(row[0])
        ref = index.records.get(rowid)
        if ref is None:
            continue
        source_key = _source_grouping_key(ref)
        grouped["source"].setdefault(source_key, []).append(row)
        session_key = _session_grouping_key(ref)
        if session_key:
            grouped["session"].setdefault(session_key, []).append(row)
        day_key = _day_grouping_key(ref)
        if day_key:
            grouped["day"].setdefault(day_key, []).append(row)

    representative_rows_by_category: dict[str, list[tuple[Any, ...]]] = {}
    group_counts: dict[str, dict[str, int]] = {}
    for category in categories:
        category_representatives = [
            min(group_rows, key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
            for group_rows in grouped[category].values()
            if group_rows
        ]
        category_representatives.sort(key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])))
        representative_rows_by_category[category] = category_representatives
        group_counts[category] = {
            "groups_considered": len(grouped[category]),
            "groups_selected": 0,
            "groups_omitted": len(grouped[category]),
        }

    seed_rows = seed_rows or []
    selected: list[tuple[Any, ...]] = []
    selected_rowids: set[int] = set()
    selected_row_category_counts: dict[int, int] = {}
    selected_group_keys: dict[str, set[str]] = {category: set() for category in categories}
    representative_reason_counts: dict[str, int] = {"adjacent_run_head": len(rows)}
    selected_representative_reason_counts: dict[str, int] = {}

    for row in seed_rows:
        if len(selected) >= selected_limit:
            break
        rowid = int(row[0])
        if rowid in selected_rowids:
            continue
        ref = index.records.get(rowid)
        if ref is None:
            continue
        selected.append(row)
        selected_rowids.add(rowid)
        selected_row_category_counts[rowid] = 0
        selected_representative_reason_counts["protected_top_exact_or_channel_head"] = (
            selected_representative_reason_counts.get("protected_top_exact_or_channel_head", 0) + 1
        )
        representative_reason_counts["protected_top_exact_or_channel_head"] = (
            representative_reason_counts.get("protected_top_exact_or_channel_head", 0) + 1
        )

    max_category_depth = max((len(items) for items in representative_rows_by_category.values()), default=0)
    for depth in range(max_category_depth):
        for category in categories:
            category_rows = representative_rows_by_category[category]
            if depth >= len(category_rows):
                continue
            row = category_rows[depth]
            rowid = int(row[0])
            ref = index.records.get(rowid)
            if ref is None:
                continue
            category_key = _category_group_key(ref, category)
            if category_key is None:
                continue
            reason = f"{category}_representative"
            representative_reason_counts[reason] = representative_reason_counts.get(reason, 0) + 1
            if rowid in selected_rowids:
                first_selected_category_for_row = int(selected_row_category_counts.get(rowid, 0)) == 0
                selected_group_keys[category].add(category_key)
                selected_row_category_counts[rowid] = int(selected_row_category_counts.get(rowid, 0)) + 1
                if first_selected_category_for_row:
                    selected_representative_reason_counts[reason] = (
                        selected_representative_reason_counts.get(reason, 0) + 1
                    )
                    continue
                selected_representative_reason_counts["duplicate_category_representative"] = (
                    selected_representative_reason_counts.get("duplicate_category_representative", 0) + 1
                )
                continue
            if len(selected) >= selected_limit:
                continue
            selected.append(row)
            selected_rowids.add(rowid)
            selected_row_category_counts[rowid] = 1
            selected_group_keys[category].add(category_key)
            selected_representative_reason_counts[reason] = selected_representative_reason_counts.get(reason, 0) + 1

    fill_count = 0
    for row in stable_fill_rows:
        if len(selected) >= selected_limit:
            break
        rowid = int(row[0])
        if rowid in selected_rowids:
            continue
        selected.append(row)
        selected_rowids.add(rowid)
        selected_row_category_counts[rowid] = selected_row_category_counts.get(rowid, 0)
        fill_count += 1
    if fill_count:
        selected_representative_reason_counts["source_session_day_group_fill"] = fill_count
        representative_reason_counts["source_session_day_group_fill"] = fill_count

    for category in categories:
        selected_count = len(selected_group_keys[category])
        considered_count = group_counts[category]["groups_considered"]
        group_counts[category]["groups_selected"] = selected_count
        group_counts[category]["groups_omitted"] = max(considered_count - selected_count, 0)

    return selected, representative_reason_counts, selected_representative_reason_counts, group_counts


def _category_group_key(ref: FieldedBm25RecordRef, category: str) -> str | None:
    if category == "source":
        return _source_grouping_key(ref)
    if category == "session":
        return _session_grouping_key(ref)
    if category == "day":
        return _day_grouping_key(ref)
    return None


def _empty_group_category_counts() -> dict[str, dict[str, int]]:
    return {
        category: {"groups_considered": 0, "groups_selected": 0, "groups_omitted": 0}
        for category in ("source", "session", "day")
    }


def _adjacent_run_collapse_diagnostics(
    rows: list[tuple[Any, ...]],
    representatives: list[tuple[Any, ...]],
    run_sizes: dict[int, int],
    representative_to_suppressed: dict[int, int],
) -> dict[str, Any]:
    suppressed_rows = sum(int(count) for count in representative_to_suppressed.values())
    collapsed_runs = sum(1 for count in run_sizes.values() if int(count) > 1)
    return {
        "enabled": True,
        "strategy": "exact_adjacent_same_source_session_provenance_order_best_bm25_representative",
        "rows_before_collapse": int(len(rows)),
        "representative_rows_after_collapse": int(len(representatives)),
        "adjacent_run_count": int(len(run_sizes)),
        "collapsed_adjacent_run_count": int(collapsed_runs),
        "suppressed_adjacent_row_count": int(suppressed_rows),
        "max_adjacent_run_size": max((int(count) for count in run_sizes.values()), default=0),
        "suppressed_rows_are_aggregate_counters_only": True,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _group_interleaving_diagnostics(
    selected: list[tuple[Any, ...]],
    considered_group_counts: dict[tuple[str, str, str], int],
    selected_group_counts: dict[tuple[str, str, str], int],
    distinct_sets: dict[str, set[str]],
) -> dict[str, Any]:
    return {
        "enabled": True,
        "strategy": "source_session_date_groups_interleaved_by_within_group_rank_bm25_rank_rowid",
        "group_count_considered": int(len(considered_group_counts)),
        "group_count_selected": int(len(selected_group_counts)),
        "group_count_omitted": max(int(len(considered_group_counts)) - int(len(selected_group_counts)), 0),
        "rows_selected_by_interleaving": int(len(selected)),
        "distinct_sources_considered": int(len(distinct_sets.get("sources", set()))),
        "distinct_sessions_considered": int(len(distinct_sets.get("sessions", set()))),
        "distinct_days_considered": int(len(distinct_sets.get("days", set()))),
        "distinct_sources_selected": int(len(distinct_sets.get("selected_sources", set()))),
        "distinct_sessions_selected": int(len(distinct_sets.get("selected_sessions", set()))),
        "distinct_days_selected": int(len(distinct_sets.get("selected_days", set()))),
        "dominant_group_share_before": _dominant_group_share(considered_group_counts),
        "dominant_group_share_after": _dominant_group_share(selected_group_counts),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


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
    group_category_counts: dict[str, dict[str, int]],
    representative_reason_counts: dict[str, int],
    selected_representative_reason_counts: dict[str, int],
    group_interleaved_rows_selected: int,
    adjacent_run_collapse: dict[str, Any],
    group_interleaving: dict[str, Any],
    selection_policy: dict[str, Any],
) -> dict[str, Any]:
    rows_dropped_by_stream_cap = sum(int(count) for count in dropped_stream_counts.values())
    source_group_counts = group_category_counts.get("source", {})
    session_group_counts = group_category_counts.get("session", {})
    day_group_counts = group_category_counts.get("day", {})
    category_groups_considered = sum(int(counts.get("groups_considered") or 0) for counts in group_category_counts.values())
    category_groups_selected = sum(int(counts.get("groups_selected") or 0) for counts in group_category_counts.values())
    category_groups_omitted = sum(int(counts.get("groups_omitted") or 0) for counts in group_category_counts.values())
    return {
        "enabled": True,
        "strategy": "adjacent_run_collapse_then_source_session_day_category_representatives_then_group_interleaving",
        "candidate_rows_considered": int(rows_considered),
        "candidate_rows_selected": int(rows_selected),
        "candidate_rows_dropped": max(int(rows_considered) - int(rows_selected), 0),
        "candidate_row_limit": int(selected_limit),
        "candidate_row_pool_limit": int(pool_limit),
        "source_count_considered": len(considered_source_counts),
        "source_count_selected": len(selected_source_counts),
        "source_group_count_considered": int(source_group_counts.get("groups_considered") or 0),
        "source_group_count_selected": int(source_group_counts.get("groups_selected") or 0),
        "source_group_count_omitted": int(source_group_counts.get("groups_omitted") or 0),
        "session_group_count_considered": int(session_group_counts.get("groups_considered") or 0),
        "session_group_count_selected": int(session_group_counts.get("groups_selected") or 0),
        "session_group_count_omitted": int(session_group_counts.get("groups_omitted") or 0),
        "day_group_count_considered": int(day_group_counts.get("groups_considered") or 0),
        "day_group_count_selected": int(day_group_counts.get("groups_selected") or 0),
        "day_group_count_omitted": int(day_group_counts.get("groups_omitted") or 0),
        "category_group_count_considered": int(category_groups_considered),
        "category_group_count_selected": int(category_groups_selected),
        "category_group_count_omitted": int(category_groups_omitted),
        "dominant_source_share_before": _dominant_source_share(considered_source_counts),
        "dominant_source_share_after": _dominant_source_share(selected_source_counts),
        "stream_head_rows_selected": 0,
        "source_head_rows_selected": 0,
        "global_tail_rows_selected": 0,
        "group_interleaved_rows_selected": int(group_interleaved_rows_selected),
        "representative_reason_counts": dict(sorted(representative_reason_counts.items())),
        "selected_representative_reason_counts": dict(sorted(selected_representative_reason_counts.items())),
        "representative_reason_categories": [
            "adjacent_run_head",
            "source_representative",
            "session_representative",
            "day_representative",
            "duplicate_category_representative",
            "source_session_day_group_fill",
            "protected_top_exact_or_channel_head",
        ],
        "group_key_categories": ["source", "session", "day"],
        "raw_group_keys_included": False,
        "adjacent_run_collapse": adjacent_run_collapse,
        "group_interleaving": group_interleaving,
        "selection_policy": selection_policy,
        "stream_selection": {
            "enabled": bool(selected_stream_counts or dropped_stream_counts),
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


def _protected_evidence_selection_rows(
    rows: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    row_stream_labels: dict[int, set[str]],
    fusion: dict[str, Any],
    bm25_rank_by_rowid: dict[int, int],
) -> list[tuple[Any, ...]]:
    row_by_id = {int(row[0]): row for row in rows}
    channel_head_rowids = {int(rowid) for rowid in (fusion.get("channel_head_rowids") or set()) if int(rowid) in row_by_id}
    row_channel_ranks = fusion.get("row_channel_ranks") if isinstance(fusion.get("row_channel_ranks"), dict) else {}
    protected_rowids: set[int] = set()
    for rowid, labels in row_stream_labels.items():
        if int(rowid) not in row_by_id:
            continue
        fusion_labels = (row_channel_ranks.get(int(rowid)) or {}).keys()
        all_labels = {*labels, *fusion_labels}
        if any(_selection_channel_bucket(label) == "top_exact" for label in all_labels):
            protected_rowids.add(int(rowid))
    protected_rowids.update(
        rowid
        for rowid in channel_head_rowids
        if any(_selection_channel_bucket(label) == "top_exact" for label in (row_channel_ranks.get(rowid) or {}))
    )
    return sorted(
        (row_by_id[rowid] for rowid in protected_rowids if rowid in row_by_id and rowid in index.records),
        key=lambda row: (bm25_rank_by_rowid.get(int(row[0]), 10**12), int(row[0])),
    )


def _evidence_selection_policy_diagnostics(
    rows: list[tuple[Any, ...]],
    selected: list[tuple[Any, ...]],
    index: FieldedBm25Index,
    *,
    row_stream_labels: dict[int, set[str]],
    stream_rows: dict[str, list[tuple[Any, ...]]],
    fusion: dict[str, Any],
    representative_to_suppressed: dict[int, int],
    selected_representative_reason_counts: dict[str, int],
) -> dict[str, Any]:
    selected_rowids = {int(row[0]) for row in selected}
    considered_rowids = {int(row[0]) for row in rows}
    selected_channel_labels: set[str] = set()
    omitted_channel_labels: set[str] = set()
    selected_bucket_counts: dict[str, int] = {
        "top_exact": 0,
        "child_channel": 0,
        "metadata_match": 0,
        "nearby_context": 0,
        "diversity_fill": 0,
    }
    row_channel_ranks = fusion.get("row_channel_ranks") if isinstance(fusion.get("row_channel_ranks"), dict) else {}
    for rowid in considered_rowids:
        labels = {
            _safe_evidence_channel_label(label)
            for label in {*row_stream_labels.get(rowid, set()), *(row_channel_ranks.get(rowid) or {}).keys()}
            if label
        }
        if rowid in selected_rowids:
            selected_channel_labels.update(labels)
        else:
            omitted_channel_labels.update(labels)
    for row in selected:
        rowid = int(row[0])
        buckets = {
            _selection_channel_bucket(label)
            for label in {*row_stream_labels.get(rowid, set()), *(row_channel_ranks.get(rowid) or {}).keys()}
        }
        if not buckets or buckets == {"diversity_fill"}:
            buckets = {"diversity_fill"}
        for bucket in buckets:
            selected_bucket_counts[bucket] = selected_bucket_counts.get(bucket, 0) + 1

    role_groups_considered = {
        _role_grouping_key(index.records[rowid])
        for rowid in considered_rowids
        if rowid in index.records
    }
    role_groups_selected = {
        _role_grouping_key(index.records[rowid])
        for rowid in selected_rowids
        if rowid in index.records
    }
    dropped_stream_counts = _stream_dropped_counts(stream_rows, selected_rowids)
    omission_reason_counts = {
        "candidate_limit": max(len(considered_rowids) - len(selected_rowids), 0),
        "adjacent_or_duplicate_collapse": _duplicate_or_collapsed_row_count(
            rows,
            stream_rows=stream_rows,
            representative_to_suppressed=representative_to_suppressed,
            fusion=fusion,
        ),
    }
    if dropped_stream_counts:
        omission_reason_counts["channel_not_selected"] = sum(int(count) for count in dropped_stream_counts.values())
    if role_groups_considered - role_groups_selected:
        omission_reason_counts["role_group_omitted"] = len(role_groups_considered - role_groups_selected)
    if omitted_channel_labels:
        omission_reason_counts["channel_label_omitted"] = len(omitted_channel_labels)

    return {
        "selection_policy_version": FIELDED_EVIDENCE_SELECTION_POLICY_VERSION,
        "selection_strategy": "protect_top_exact_and_channel_heads_then_source_session_day_role_diversity_fill",
        "top_exact_rows_selected": int(selected_bucket_counts.get("top_exact") or 0),
        "child_channel_rows_selected": int(selected_bucket_counts.get("child_channel") or 0),
        "metadata_match_rows_selected": int(selected_bucket_counts.get("metadata_match") or 0),
        "nearby_context_rows_selected": int(selected_bucket_counts.get("nearby_context") or 0),
        "diversity_fill_rows_selected": int(selected_bucket_counts.get("diversity_fill") or 0),
        "duplicate_or_collapsed_row_count": int(omission_reason_counts["adjacent_or_duplicate_collapse"]),
        "role_group_count_considered": int(len(role_groups_considered)),
        "role_group_count_selected": int(len(role_groups_selected)),
        "role_group_count_omitted": max(int(len(role_groups_considered)) - int(len(role_groups_selected)), 0),
        "omission_reason_counts": dict(sorted((key, int(value)) for key, value in omission_reason_counts.items())),
        "selected_channel_labels": sorted(selected_channel_labels),
        "omitted_channel_labels": sorted(omitted_channel_labels - selected_channel_labels),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
        "index_payloads_included": False,
    }


def _selection_channel_bucket(label: str) -> str:
    safe_label = _safe_evidence_channel_label(label)
    if safe_label == CONTEXT_NEIGHBORHOOD_STREAM_LABEL:
        return "nearby_context"
    if safe_label.startswith("child_"):
        return "child_channel"
    if "exact_phrase" in safe_label or safe_label == "exact_phrase":
        return "top_exact"
    if (
        safe_label.startswith("role_")
        or safe_label.startswith("timestamp_")
        or safe_label.startswith("artifact_")
        or safe_label.startswith("tool_event_")
    ):
        return "metadata_match"
    return "diversity_fill"


def _role_grouping_key(ref: FieldedBm25RecordRef) -> str:
    return ref.role_bucket


def _duplicate_or_collapsed_row_count(
    rows: list[tuple[Any, ...]],
    *,
    stream_rows: dict[str, list[tuple[Any, ...]]],
    representative_to_suppressed: dict[int, int],
    fusion: dict[str, Any],
) -> int:
    stream_row_total = sum(len(channel_rows) for channel_rows in stream_rows.values())
    duplicate_stream_rows = max(stream_row_total - len({int(row[0]) for channel_rows in stream_rows.values() for row in channel_rows}), 0)
    fusion_dropped = int(fusion.get("dropped_row_count") or 0)
    adjacent_suppressed = sum(int(count) for count in representative_to_suppressed.values())
    return max(duplicate_stream_rows, 0) + max(fusion_dropped, 0) + max(adjacent_suppressed, 0)


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


def _dominant_group_share(group_counts: dict[tuple[str, str, str], int]) -> float:
    total = sum(group_counts.values())
    if total <= 0:
        return 0.0
    return round(max(group_counts.values()) / total, 6)


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


def _fielded_context_index(index: FieldedBm25Index) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": "fielded_bm25_metadata_only_context_fallback",
        "persistent_index_enabled": bool(index.metrics.get("persistent_index_enabled", False)),
        "warm_query_reused_index": bool(index.metrics.get("warm_query_reused_index", False)),
        "source_count": int(index.metrics.get("indexed_files", 0)),
        "record_count": 0,
        "records_by_source": {},
        "context_records_omitted": int(index.metrics.get("indexed_chunks", 0)),
        "fallback_required": True,
        "raw_paths_included": False,
        "raw_text_included": False,
        "internal_only": True,
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


def _fielded_query_streams_for_intent(
    intent: QueryIntent,
    base_query: str | None,
    *,
    query_plan_mode: str = QUERY_PLAN_MODE_AUTO,
) -> list[FieldedQueryStream]:
    query_plan_mode = _normalize_query_plan_mode(query_plan_mode)
    if query_plan_mode == QUERY_PLAN_MODE_OFF:
        return _legacy_fielded_query_streams_for_intent(intent, base_query)

    streams: list[FieldedQueryStream] = []
    if base_query:
        streams.append(
            FieldedQueryStream(
                label="base_lexical",
                query=base_query,
                fields=("text",),
                channel="base_lexical",
                intent=_primary_intent(intent),
                kind="base_lexical",
                reason_codes=("base_query",),
            )
        )

    text_query = _field_query("text", _anchor_values_for_fielded_streams(intent))
    if not text_query:
        return _unique_streams(streams)

    plan = intent.query_plan
    primary_intent = plan.primary_intent if plan is not None else "generic_history"
    if primary_intent == "when_user_said":
        streams.extend(_when_user_said_child_streams(intent, text_query))
        return _unique_streams(streams)
    if primary_intent == "workflow_failure":
        streams.extend(_workflow_failure_child_streams(intent, text_query))
        streams.extend(_legacy_fielded_query_streams_for_intent(intent, None))
        return _unique_streams(streams)
    return _unique_streams(streams + _legacy_fielded_query_streams_for_intent(intent, None))


def _legacy_fielded_query_streams_for_intent(intent: QueryIntent, base_query: str | None) -> list[FieldedQueryStream]:
    streams: list[FieldedQueryStream] = []
    if base_query:
        streams.append(
            FieldedQueryStream(
                label="base_lexical",
                query=base_query,
                fields=("text",),
                channel="base_lexical",
                intent=_primary_intent(intent),
                kind="base_lexical",
                reason_codes=("base_query",),
            )
        )

    text_query = _field_query("text", _anchor_values_for_fielded_streams(intent))
    if not text_query:
        return _unique_streams(streams)

    plan = intent.query_plan
    primary_intent = plan.primary_intent if plan is not None else "generic_history"
    detected_intents = set(plan.detected_intents if plan is not None else [])
    phrase_query = _phrase_query("text", _anchor_values_for_fielded_streams(intent))
    if phrase_query and primary_intent in {"when_user_said", "why", "workflow_failure"}:
        streams.append(
            FieldedQueryStream(
                label=f"exact_phrase_text_{_stream_label_part(primary_intent)}",
                query=phrase_query,
                fields=("text",),
                channel="exact_phrase",
                intent=primary_intent,
                kind="legacy_exact_phrase",
                reason_codes=("legacy_intent_phrase",),
            )
        )
    role_hints = _role_hints_for_query_streams(intent)
    for role in role_hints:
        role_query = _field_query("role", [role])
        if role_query:
            streams.append(
                FieldedQueryStream(
                    label=f"role_text_{_stream_label_part(role)}",
                    query=f"{role_query} AND ({text_query})",
                    fields=("role", "text"),
                    channel=f"role_text_{_stream_label_part(role)}",
                    intent=primary_intent,
                    kind="legacy_role_text",
                    reason_codes=("legacy_role_hint",),
                )
            )
            if phrase_query and primary_intent in {"when_user_said", "why", "workflow_failure"}:
                streams.append(
                    FieldedQueryStream(
                        label=f"role_phrase_{_stream_label_part(role)}",
                        query=f"{role_query} AND ({phrase_query})",
                        fields=("role", "text"),
                        channel=f"role_phrase_{_stream_label_part(role)}",
                        intent=primary_intent,
                        kind="legacy_role_phrase",
                        reason_codes=("legacy_role_hint", "legacy_intent_phrase"),
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
                    channel="timestamp_text",
                    intent=primary_intent,
                    kind="legacy_timestamp_text",
                    reason_codes=("date_window_hint",),
                )
            )

    artifact_query = _field_query("artifact_task_path", intent.artifact_hints)
    if artifact_query:
        streams.append(
            FieldedQueryStream(
                label="artifact_path_text",
                query=f"{artifact_query} AND ({text_query})",
                fields=("artifact_task_path", "text"),
                channel="artifact_path_text",
                intent=primary_intent,
                kind="legacy_artifact_path_text",
                reason_codes=("artifact_hint",),
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
                    channel=f"tool_event_text_{_stream_label_part(primary_intent)}",
                    intent=primary_intent,
                    kind="legacy_tool_event_text",
                    reason_codes=("tool_event_hint",),
                )
            )
            if phrase_query and primary_intent in {"workflow_failure", "why"}:
                streams.append(
                    FieldedQueryStream(
                        label=f"tool_event_phrase_{_stream_label_part(primary_intent)}",
                        query=f"{tool_query} AND ({phrase_query})",
                        fields=("tool_event", "text"),
                        channel=f"tool_event_phrase_{_stream_label_part(primary_intent)}",
                        intent=primary_intent,
                        kind="legacy_tool_event_phrase",
                        reason_codes=("tool_event_hint", "legacy_intent_phrase"),
                    )
                )

    return _unique_streams(streams)


def _when_user_said_child_streams(intent: QueryIntent, text_query: str) -> list[FieldedQueryStream]:
    phrase_query = _phrase_query("text", _anchor_values_for_fielded_streams(intent))
    streams: list[FieldedQueryStream] = []
    if phrase_query:
        streams.append(
            _child_stream(
                "child_when_user_said_exact_phrase_text",
                phrase_query,
                ("text",),
                "when_user_said",
                "exact_phrase_text",
                ("intent:when_user_said", "phrase_anchor"),
            )
        )
    streams.append(
        _child_stream(
            "child_when_user_said_user_role_text",
            f'{_field_query("role", ["user"])} AND ({text_query})',
            ("role", "text"),
            "when_user_said",
            "user_role_text",
            ("intent:when_user_said", "role:user"),
        )
    )
    if phrase_query:
        streams.append(
            _child_stream(
                "child_when_user_said_user_role_phrase",
                f'{_field_query("role", ["user"])} AND ({phrase_query})',
                ("role", "text"),
                "when_user_said",
                "user_role_phrase",
                ("intent:when_user_said", "role:user", "phrase_anchor"),
            )
        )
    timestamp_query = _timestamp_query_for_intent(intent)
    if timestamp_query:
        streams.append(
            _child_stream(
                "child_when_user_said_timestamp_text",
                f"{timestamp_query} AND ({text_query})",
                ("timestamp_bucket", "text"),
                "when_user_said",
                "timestamp_text",
                ("date_window_hint",),
            )
        )
    artifact_query = _field_query("artifact_task_path", intent.artifact_hints)
    if artifact_query:
        streams.append(
            _child_stream(
                "child_when_user_said_artifact_path_text",
                f"{artifact_query} AND ({text_query})",
                ("artifact_task_path", "text"),
                "when_user_said",
                "artifact_path_text",
                ("artifact_hint",),
            )
        )
    return streams


def _workflow_failure_child_streams(intent: QueryIntent, text_query: str) -> list[FieldedQueryStream]:
    streams: list[FieldedQueryStream] = [
        _child_stream(
            "child_workflow_failure_subject_text",
            text_query,
            ("text",),
            "workflow_failure",
            "subject_text",
            ("intent:workflow_failure", "subject_anchor"),
        )
    ]
    failure_query = _field_query("text", WORKFLOW_FAILURE_MARKERS)
    if failure_query:
        streams.append(
            _child_stream(
                "child_workflow_failure_failure_marker_text",
                f"({failure_query}) AND ({text_query})",
                ("text",),
                "workflow_failure",
                "failure_marker_text",
                ("failure_marker_family",),
            )
        )
    repair_query = _field_query("text", WORKFLOW_REPAIR_MARKERS)
    if repair_query:
        streams.append(
            _child_stream(
                "child_workflow_failure_repair_retry_text",
                f"({repair_query}) AND ({text_query})",
                ("text",),
                "workflow_failure",
                "repair_retry_text",
                ("repair_retry_marker_family",),
            )
        )
    tool_query = _field_query("tool_event", _tool_event_query_terms_for_intent("workflow_failure"))
    if tool_query:
        streams.append(
            _child_stream(
                "child_workflow_failure_tool_event_text",
                f"{tool_query} AND ({text_query})",
                ("tool_event", "text"),
                "workflow_failure",
                "tool_event_text",
                ("tool_event_hint",),
            )
        )
    timestamp_query = _timestamp_query_for_intent(intent)
    if timestamp_query:
        streams.append(
            _child_stream(
                "child_workflow_failure_timestamp_text",
                f"{timestamp_query} AND ({text_query})",
                ("timestamp_bucket", "text"),
                "workflow_failure",
                "timestamp_text",
                ("date_window_hint",),
            )
        )
    artifact_query = _field_query("artifact_task_path", intent.artifact_hints)
    if artifact_query:
        streams.append(
            _child_stream(
                "child_workflow_failure_artifact_path_text",
                f"{artifact_query} AND ({text_query})",
                ("artifact_task_path", "text"),
                "workflow_failure",
                "artifact_path_text",
                ("artifact_hint",),
            )
        )
    return streams


def _child_stream(
    label: str,
    query: str,
    fields: tuple[str, ...],
    intent: str,
    kind: str,
    reason_codes: tuple[str, ...],
) -> FieldedQueryStream:
    return FieldedQueryStream(
        label=label,
        query=query,
        fields=fields,
        channel=label,
        intent=intent,
        kind=kind,
        reason_codes=reason_codes,
        child=True,
    )


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


def _build_fielded_evidence_channels(
    *,
    raw_rows: list[tuple[Any, ...]],
    query_streams: list[FieldedQueryStream],
    stream_rows: dict[str, list[tuple[Any, ...]]],
    candidate_pool_limit: int,
) -> list[FieldedEvidenceChannel]:
    channels: list[FieldedEvidenceChannel] = []
    for stream in query_streams:
        rows = list(stream_rows.get(stream.label) or [])
        if not rows:
            continue
        label = _safe_evidence_channel_label(stream.channel or stream.label)
        channels.append(FieldedEvidenceChannel(label=label, kind="query_stream", rows=rows[:candidate_pool_limit]))
    if raw_rows:
        channels.append(
            FieldedEvidenceChannel(
                label="fielded_stream_union",
                kind="query_stream_union",
                rows=list(raw_rows[:candidate_pool_limit]),
            )
        )
    return channels


def _fuse_fielded_evidence_channels(
    channels: list[FieldedEvidenceChannel],
    *,
    candidate_pool_limit: int,
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    bounded_limit = max(int(candidate_pool_limit), 0)
    row_by_id: dict[int, tuple[Any, ...]] = {}
    channel_ranks_by_rowid: dict[int, dict[str, int]] = {}
    channel_row_counts: dict[str, int] = {}
    for channel in channels:
        label = _safe_evidence_channel_label(channel.label)
        seen_in_channel: set[int] = set()
        for rank, row in enumerate(channel.rows, start=1):
            rowid = int(row[0])
            if rowid in seen_in_channel:
                continue
            seen_in_channel.add(rowid)
            row_by_id.setdefault(rowid, row)
            channel_ranks_by_rowid.setdefault(rowid, {})[label] = rank
        channel_row_counts[label] = len(seen_in_channel)

    fused_ranked_rowids = sorted(
        row_by_id,
        key=lambda rowid: (
            -_rrf_score(channel_ranks_by_rowid.get(rowid, {})),
            min(channel_ranks_by_rowid.get(rowid, {}).values(), default=10**12),
            -len(channel_ranks_by_rowid.get(rowid, {})),
            rowid,
        ),
    )
    channel_head_rowids: list[int] = []
    for channel in channels:
        label = _safe_evidence_channel_label(channel.label)
        ranked = sorted(
            (
                int(row[0])
                for row in channel.rows
                if label in channel_ranks_by_rowid.get(int(row[0]), {})
            ),
            key=lambda rowid: (channel_ranks_by_rowid.get(rowid, {}).get(label, 10**12), rowid),
        )
        if ranked:
            channel_head_rowids.append(ranked[0])
    selected_rowids: list[int] = []
    seen_selected: set[int] = set()
    for rowid in [*channel_head_rowids, *fused_ranked_rowids]:
        if rowid in seen_selected:
            continue
        if len(selected_rowids) >= bounded_limit:
            break
        selected_rowids.append(rowid)
        seen_selected.add(rowid)
    fused_rows = [(rowid, -_rrf_score(channel_ranks_by_rowid.get(rowid, {}))) for rowid in selected_rowids]
    selected_set = set(selected_rowids)
    selected_by_channel: dict[str, int] = {}
    dropped_by_channel: dict[str, int] = {}
    for label, row_count in channel_row_counts.items():
        selected_count = sum(1 for rowid in selected_set if label in channel_ranks_by_rowid.get(rowid, {}))
        if selected_count:
            selected_by_channel[label] = selected_count
        dropped_count = max(int(row_count) - selected_count, 0)
        if dropped_count:
            dropped_by_channel[label] = dropped_count
    return fused_rows, {
        "enabled": True,
        "strategy": "rank_only_rrf_evidence_channel_fusion_before_adjacent_run_collapse",
        "rank_constant": FIELDED_EVIDENCE_RRF_RANK_CONSTANT,
        "candidate_row_pool_limit": bounded_limit,
        "channel_count": len(channels),
        "channel_labels": [_safe_evidence_channel_label(channel.label) for channel in channels],
        "channel_kinds": {_safe_evidence_channel_label(channel.label): str(channel.kind) for channel in channels},
        "channel_row_counts": dict(sorted(channel_row_counts.items())),
        "unique_row_count_before_fusion": len(row_by_id),
        "selected_row_count": len(selected_rowids),
        "dropped_row_count": max(len(row_by_id) - len(selected_rowids), 0),
        "channel_head_rows_seeded": len(set(channel_head_rowids) & selected_set),
        "selected_rows_by_channel": dict(sorted(selected_by_channel.items())),
        "dropped_rows_by_channel": dict(sorted(dropped_by_channel.items())),
        "row_channel_ranks": channel_ranks_by_rowid,
        "selected_rowids": set(selected_rowids),
        "channel_head_rowids": set(channel_head_rowids),
        "raw_score_inputs_used_for_fusion": False,
        "raw_bm25_scores_used_for_fusion": False,
        "score_normalization_used": False,
        "equal_channel_weights": True,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _rrf_score(channel_ranks: dict[str, int]) -> float:
    return sum(1.0 / (FIELDED_EVIDENCE_RRF_RANK_CONSTANT + int(rank)) for rank in channel_ranks.values())


def _fielded_evidence_channel_summary(
    *,
    fusion: dict[str, Any],
    selected_rows: list[tuple[Any, ...]],
    candidate_pool_limit: int,
) -> dict[str, Any]:
    selected_rowids = {int(row[0]) for row in selected_rows}
    row_channel_ranks = fusion.get("row_channel_ranks") if isinstance(fusion.get("row_channel_ranks"), dict) else {}
    selected_after_grouping: dict[str, int] = {}
    dropped_after_grouping: dict[str, int] = {}
    channel_row_counts = {
        str(label): int(count or 0)
        for label, count in (fusion.get("channel_row_counts") or {}).items()
    }
    for rowid in selected_rowids:
        for label in (row_channel_ranks.get(rowid) or {}):
            selected_after_grouping[str(label)] = selected_after_grouping.get(str(label), 0) + 1
    for label, count in channel_row_counts.items():
        dropped = max(count - int(selected_after_grouping.get(label, 0)), 0)
        if dropped:
            dropped_after_grouping[label] = dropped
    return {
        "enabled": bool(fusion.get("enabled", False)),
        "strategy": str(fusion.get("strategy") or "disabled"),
        "fusion_stage": "before_adjacent_run_collapse_and_source_session_day_rollup",
        "rank_fusion_style": "reciprocal_rank_fusion",
        "rank_constant": int(fusion.get("rank_constant") or FIELDED_EVIDENCE_RRF_RANK_CONSTANT),
        "rank_only": True,
        "raw_score_inputs_used_for_fusion": False,
        "raw_bm25_scores_used_for_fusion": False,
        "score_normalization_used": False,
        "equal_channel_weights": True,
        "candidate_row_pool_limit": int(candidate_pool_limit),
        "channel_count": int(fusion.get("channel_count") or 0),
        "channel_labels": [str(label) for label in fusion.get("channel_labels") or []],
        "channel_kinds": {str(label): str(kind) for label, kind in (fusion.get("channel_kinds") or {}).items()},
        "channel_row_counts": dict(sorted(channel_row_counts.items())),
        "unique_row_count_before_fusion": int(fusion.get("unique_row_count_before_fusion") or 0),
        "selected_row_count_after_fusion": int(fusion.get("selected_row_count") or 0),
        "dropped_row_count_after_fusion": int(fusion.get("dropped_row_count") or 0),
        "selected_rows_by_channel_after_fusion": _safe_int_count_dict(fusion.get("selected_rows_by_channel")),
        "dropped_rows_by_channel_after_fusion": _safe_int_count_dict(fusion.get("dropped_rows_by_channel")),
        "selected_rows_by_channel_after_grouping": dict(sorted(selected_after_grouping.items())),
        "dropped_rows_by_channel_after_grouping": dict(sorted(dropped_after_grouping.items())),
        "selected_row_reason_label_prefix": "fielded_bm25:evidence_channel:",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _selected_evidence_reason_labels(rowid: int, fusion: dict[str, Any]) -> list[str]:
    row_channel_ranks = fusion.get("row_channel_ranks") if isinstance(fusion.get("row_channel_ranks"), dict) else {}
    ranks = row_channel_ranks.get(int(rowid)) or {}
    return [_safe_evidence_channel_label(label) for label in ranks]


def _safe_evidence_channel_label(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(value).lower()).strip("_")
    return cleaned[:48] or "unknown"


def _safe_int_count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count or 0) for key, count in sorted(value.items())}


def _query_stream_summary(
    *,
    streams: list[FieldedQueryStream],
    diagnostics: list[dict[str, Any]],
    deduped_row_count: int,
    candidate_pool_limit: int,
    selection: dict[str, Any] | None = None,
    query_plan_mode: str = QUERY_PLAN_MODE_AUTO,
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
        "strategy": "query_plan_fielded_streams_then_adjacent_run_collapse_then_group_interleaving",
        "query_plan_mode": _normalize_query_plan_mode(query_plan_mode),
        "stream_count": len(streams),
        "streams": diagnostics,
        "child_stream_count": sum(1 for stream in streams if stream.child),
        "child_stream_labels": [stream.label for stream in streams if stream.child],
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
        "channel": _safe_evidence_channel_label(stream.channel or stream.label),
        "intent": stream.intent,
        "kind": stream.kind,
        "child": bool(stream.child),
        "reason_codes": list(stream.reason_codes),
        "skip_reason_code": stream.skip_reason_code,
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


def _child_query_plan_summary(
    *,
    intent: QueryIntent,
    streams: list[FieldedQueryStream],
    query_plan_mode: str,
    diagnostics: list[dict[str, Any]],
    candidate_pool_limit: int,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query_plan_mode = _normalize_query_plan_mode(query_plan_mode)
    primary_intent = _primary_intent(intent)
    child_streams = [stream for stream in streams if stream.child]
    diagnostic_by_label = {str(item.get("label")): item for item in diagnostics if isinstance(item, dict)}
    selected_by_stream = (selection or {}).get("selected_rows_by_stream") if isinstance(selection, dict) else {}
    dropped_by_stream = (selection or {}).get("rows_dropped_by_stream_cap") if isinstance(selection, dict) else {}
    supported = primary_intent in SUPPORTED_CHILD_CHANNEL_INTENTS
    children: list[dict[str, Any]] = []
    for stream in child_streams:
        diag = diagnostic_by_label.get(stream.label, {})
        children.append(
            {
                "label": stream.label,
                "intent": stream.intent,
                "kind": stream.kind,
                "fields": list(stream.fields),
                "status": str(diag.get("status") or "planned"),
                "skip_reason_code": stream.skip_reason_code,
                "reason_codes": list(stream.reason_codes),
                "row_count": int(diag.get("row_count") or 0),
                "deduped_row_count": int(diag.get("deduped_row_count") or 0),
                "stream_limit": int(diag.get("stream_limit") or candidate_pool_limit or 0),
                "hit_limit": bool(diag.get("hit_limit", False)),
                "selected_rows_after_grouping": int((selected_by_stream or {}).get(stream.label) or 0),
                "dropped_rows_after_grouping": int((dropped_by_stream or {}).get(stream.label) or 0),
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
                "raw_paths_included": False,
                "raw_text_included": False,
                "raw_session_ids_included": False,
                "raw_cwd_included": False,
                "vectors_or_persisted_indexes_included": False,
            }
        )
    return {
        "query_plan_mode": query_plan_mode,
        "primary_intent": primary_intent,
        "supported_child_channel_intent": primary_intent if supported else None,
        "enabled": query_plan_mode != QUERY_PLAN_MODE_OFF and supported,
        "debug": query_plan_mode == QUERY_PLAN_MODE_DEBUG,
        "child_channel_count": len(child_streams),
        "child_channel_labels": [stream.label for stream in child_streams],
        "children": children,
        "skipped": _child_query_plan_skipped(primary_intent, query_plan_mode, child_streams),
        "privacy": {
            "raw_query_text_included": False,
            "raw_query_terms_included": False,
            "private_transcript_text_included": False,
            "raw_paths_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _empty_child_query_plan_summary() -> dict[str, Any]:
    return {
        "query_plan_mode": QUERY_PLAN_MODE_AUTO,
        "primary_intent": "generic_history",
        "supported_child_channel_intent": None,
        "enabled": False,
        "debug": False,
        "child_channel_count": 0,
        "child_channel_labels": [],
        "children": [],
        "skipped": [],
        "privacy": {
            "raw_query_text_included": False,
            "raw_query_terms_included": False,
            "private_transcript_text_included": False,
            "raw_paths_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _child_query_plan_skipped(
    primary_intent: str,
    query_plan_mode: str,
    child_streams: list[FieldedQueryStream],
) -> list[dict[str, Any]]:
    if query_plan_mode == QUERY_PLAN_MODE_OFF:
        return [
            {
                "intent": primary_intent,
                "status": "skipped",
                "skip_reason_code": "query_plan_mode_off",
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
            }
        ]
    if primary_intent not in SUPPORTED_CHILD_CHANNEL_INTENTS:
        return [
            {
                "intent": primary_intent,
                "status": "skipped",
                "skip_reason_code": "unsupported_intent",
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
            }
        ]
    if not child_streams:
        return [
            {
                "intent": primary_intent,
                "status": "skipped",
                "skip_reason_code": "no_child_streams_generated",
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
            }
        ]
    return []


def _normalize_query_plan_mode(value: str | None) -> str:
    mode = str(value or QUERY_PLAN_MODE_AUTO).strip().lower()
    return mode if mode in QUERY_PLAN_MODES else QUERY_PLAN_MODE_AUTO


def _primary_intent(intent: QueryIntent) -> str:
    plan = intent.query_plan
    return plan.primary_intent if plan is not None else "generic_history"


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


def _phrase_query(field_name: str, values: list[str] | tuple[str, ...]) -> str | None:
    if field_name not in FTS_FIELD_NAMES:
        return None
    tokens: list[str] = []
    for value in values:
        for token in _fts_tokens(str(value)):
            lowered = token.lower()
            if not tokens or tokens[-1] != lowered:
                tokens.append(lowered)
    tokens = tokens[:8]
    if len(tokens) < 2:
        return None
    phrase = " ".join(_escape_fts_phrase(token) for token in tokens)
    return f'{field_name}:"{phrase}"'


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


def _raw_session_grouping_key(raw_text: str) -> str | None:
    return _raw_json_string(raw_text, "session_id") or _raw_json_string(raw_text, "session")


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


def _record_session_grouping_key(record: Record) -> str | None:
    return record.conversation_id or record.parent_id or record.record_id


def _record_day_grouping_key(record: Record) -> str | None:
    return _day_key_from_timestamp_bucket(_timestamp_bucket(record))


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
    child_query_plan: dict[str, Any] | None = None,
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
        "query_plan_mode": (child_query_plan or {}).get("query_plan_mode", QUERY_PLAN_MODE_AUTO),
        "fielded_child_query_plan": child_query_plan or _empty_child_query_plan_summary(),
        "fielded_query_streams": _query_stream_summary(
            streams=query_streams,
            diagnostics=query_stream_diagnostics,
            deduped_row_count=0,
            candidate_pool_limit=0,
            query_plan_mode=(child_query_plan or {}).get("query_plan_mode", QUERY_PLAN_MODE_AUTO),
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
    child_query_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _unavailable_payload(
        index,
        started,
        warnings,
        query,
        expected_diag,
        query_streams=query_streams,
        query_stream_diagnostics=query_stream_diagnostics,
        child_query_plan=child_query_plan,
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
