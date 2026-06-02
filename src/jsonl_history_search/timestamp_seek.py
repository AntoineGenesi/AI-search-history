from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError
from .clustering import facets_for_record
from .ranking import Match, group_matches, is_weak_partial_generic, make_snippet, score_record, should_consider
from .records import parse_record
from .scanner import match_sort_key
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_mtime, stat_size


@dataclass
class TimestampLineEntry:
    line: int
    byte_start: int
    byte_length: int
    timestamp: datetime | None


@dataclass
class TimestampFileTable:
    path: Path
    source_kind: str
    size: int
    mtime: float
    line_count: int
    entries: list[TimestampLineEntry] = field(default_factory=list)
    invalid_reason: str | None = None


@dataclass
class TimestampSeekIndex:
    files: dict[str, TimestampFileTable]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def build_timestamp_seek_index(
    plan: SourcePlan,
    *,
    default_tz,
    previous: TimestampSeekIndex | None = None,
) -> TimestampSeekIndex:
    started = time.perf_counter()
    files: dict[str, TimestampFileTable] = {}
    warnings: list[str] = []
    metrics: dict[str, Any] = {
        "build_bytes": 0,
        "build_elapsed_ms": 0,
        "refresh_elapsed_ms": 0,
        "refresh_count": 0,
        "full_build_count": 0,
        "no_change_refresh_count": 0,
        "append_refresh_count": 0,
        "stale_refresh_count": 0,
        "line_count": 0,
        "timestamped_records": 0,
        "missing_timestamp_records": 0,
        "non_line_oriented_files": 0,
    }
    for path, _size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = list(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        if path.suffix != ".jsonl":
            metrics["non_line_oriented_files"] += 1
            files[str(path)] = TimestampFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                invalid_reason="non_line_oriented_source",
            )
            continue
        previous_table = previous.files.get(str(path)) if previous else None
        if previous_table is None:
            table, bytes_read = _build_file_table(path, source_kind=source_kind, default_tz=default_tz)
            metrics["full_build_count"] += 1
        else:
            table, bytes_read, refresh_kind = _refresh_file_table(previous_table, default_tz=default_tz)
            metrics["refresh_count"] += 1
            if refresh_kind == "no_change":
                metrics["no_change_refresh_count"] += 1
            elif refresh_kind == "append":
                metrics["append_refresh_count"] += 1
            elif refresh_kind == "stale":
                metrics["stale_refresh_count"] += 1
        files[str(path)] = table
        metrics["build_bytes"] += bytes_read
        metrics["line_count"] += table.line_count
        metrics["timestamped_records"] += sum(1 for entry in table.entries if entry.timestamp is not None)
        metrics["missing_timestamp_records"] += sum(1 for entry in table.entries if entry.timestamp is None)
    elapsed = _elapsed_ms_float(started)
    if previous is None:
        metrics["build_elapsed_ms"] = elapsed
    else:
        metrics["refresh_elapsed_ms"] = elapsed
    return TimestampSeekIndex(files=files, metrics=metrics, warnings=warnings)


def search_with_timestamp_seek(
    index: TimestampSeekIndex,
    plan: SourcePlan,
    intent,
    *,
    default_tz,
    cwd: str | None,
    selected_paths: set[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    warnings = list(index.warnings) + list(plan.warnings)
    if not intent.date_window_start or not intent.date_window_end:
        return {
            "status": "fallback",
            "fallback_reason": "timestamp_window_absent",
            "searched_files": 0,
            "searched_bytes": 0,
            "records_considered": 0,
            "sidecar_hits": 0,
            "sidecar_misses": 0,
            "refresh_count": int(index.metrics.get("refresh_count", 0)),
            "groups": [],
            "warnings": sorted(set(warnings)),
            "elapsed_ms": _elapsed_ms(started),
        }

    matches: list[Match] = []
    weak_matches: list[Match] = []
    weak_reservoir_limit = max(1, min(plan.limits.max_matches, max(3, plan.limits.max_matches // 4)))
    weak_candidates_skipped = 0
    dedupe: set[tuple[str, str | None]] = set()
    searched_files = 0
    searched_bytes = 0
    records_considered = 0
    sidecar_hits = 0
    sidecar_misses = 0
    opened_paths: set[str] = set()

    tables = _selected_tables(index, selected_paths)
    for table in tables:
        fallback_reason = _table_fallback_reason(table)
        if fallback_reason:
            return {
                "status": "fallback",
                "fallback_reason": fallback_reason,
                "searched_files": 0,
                "searched_bytes": 0,
                "records_considered": 0,
                "sidecar_hits": 0,
                "sidecar_misses": 0,
                "refresh_count": int(index.metrics.get("refresh_count", 0)),
                "groups": [],
                "warnings": sorted(set(warnings)),
                "elapsed_ms": _elapsed_ms(started),
            }
        candidate_entries = []
        for entry in table.entries:
            if entry.timestamp is None:
                sidecar_misses += 1
                continue
            local_ts = entry.timestamp.astimezone(intent.date_window_start.tzinfo)
            if intent.date_window_start <= local_ts < intent.date_window_end:
                candidate_entries.append(entry)
                sidecar_hits += 1
            else:
                sidecar_misses += 1
        if not candidate_entries:
            continue
        try:
            with table.path.open("rb") as handle:
                searched_files += 1
                opened_paths.add(str(table.path))
                for entry in candidate_entries:
                    if time.perf_counter() - started > plan.limits.max_elapsed:
                        warnings.append("Stopped scanning at max elapsed cap.")
                        break
                    handle.seek(entry.byte_start)
                    raw = handle.read(entry.byte_length)
                    searched_bytes += len(raw)
                    records_considered += 1
                    record = parse_record(
                        raw.decode("utf-8", errors="replace"),
                        path=table.path,
                        line=entry.line,
                        default_tz=default_tz,
                    )
                    record.source_format = table.source_kind
                    if not should_consider(record, intent):
                        continue
                    key = (" ".join(record.text.split()).lower(), record.raw_timestamp)
                    if key in dedupe:
                        continue
                    dedupe.add(key)
                    score, reasons, artifacts = score_record(record, intent, cwd)
                    snippet_text = make_snippet(record.text, intent.terms)
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
                    matches.append(match)
                    if len(matches) >= plan.limits.max_matches:
                        warnings.append("Stopped scanning at max match cap.")
                        break
        except OSError as exc:
            raise AdapterReadError(f"Could not open {table.path}: {exc}") from exc
    if weak_candidates_skipped:
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    matches = (matches + weak_matches)[: plan.limits.max_matches * 2]
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    return {
        "status": "planned_seek",
        "fallback_reason": None,
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "sidecar_hits": sidecar_hits,
        "sidecar_misses": sidecar_misses,
        "refresh_count": int(index.metrics.get("refresh_count", 0)),
        "opened_paths": opened_paths,
        "groups": group_matches(matches),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }


def _build_file_table(path: Path, *, source_kind: str, default_tz, offset_base: int = 0, line_base: int = 0):
    entries: list[TimestampLineEntry] = []
    bytes_read = 0
    try:
        with path.open("rb") as handle:
            offset = offset_base
            if offset_base:
                handle.seek(offset_base)
            for line_index, raw in enumerate(handle, line_base + 1):
                line_start = offset
                offset += len(raw)
                bytes_read += len(raw)
                record = parse_record(raw.decode("utf-8", errors="replace"), path=path, line=line_index, default_tz=default_tz)
                entries.append(
                    TimestampLineEntry(
                        line=line_index,
                        byte_start=line_start,
                        byte_length=len(raw),
                        timestamp=record.timestamp,
                    )
                )
    except OSError:
        return (
            TimestampFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                invalid_reason="read_error",
            ),
            bytes_read,
        )
    table = TimestampFileTable(
        path=path,
        source_kind=source_kind,
        size=stat_size(path),
        mtime=stat_mtime(path),
        line_count=line_base + len(entries),
        entries=entries,
    )
    return table, bytes_read


def _refresh_file_table(previous: TimestampFileTable, *, default_tz):
    current_size = stat_size(previous.path)
    current_mtime = stat_mtime(previous.path)
    if previous.invalid_reason:
        return previous, 0, "stale"
    if current_size == previous.size and current_mtime == previous.mtime:
        return previous, 0, "no_change"
    if current_size > previous.size and current_mtime >= previous.mtime:
        tail_table, bytes_read = _build_file_table(
            previous.path,
            source_kind=previous.source_kind,
            default_tz=default_tz,
            offset_base=previous.size,
            line_base=previous.line_count,
        )
        tail_table.entries = previous.entries + tail_table.entries
        tail_table.line_count = len(tail_table.entries)
        return tail_table, bytes_read, "append"
    stale = TimestampFileTable(
        path=previous.path,
        source_kind=previous.source_kind,
        size=current_size,
        mtime=current_mtime,
        line_count=previous.line_count,
        entries=list(previous.entries),
        invalid_reason="timestamp_index_stale_or_rewritten",
    )
    return stale, 0, "stale"


def _selected_tables(index: TimestampSeekIndex, selected_paths: set[str] | None) -> list[TimestampFileTable]:
    tables = []
    for path, table in index.files.items():
        if selected_paths is not None and path not in selected_paths:
            continue
        tables.append(table)
    return tables


def _table_fallback_reason(table: TimestampFileTable) -> str | None:
    if table.invalid_reason:
        return table.invalid_reason
    if stat_size(table.path) < table.size:
        return "timestamp_index_truncated"
    if stat_size(table.path) != table.size or stat_mtime(table.path) != table.mtime:
        return "timestamp_index_stale_or_changed"
    return None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _elapsed_ms_float(started: float) -> float:
    return max(0.0, round((time.perf_counter() - started) * 1000, 3))
