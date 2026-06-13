from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError
from .clustering import facets_for_record
from .query import QueryIntent
from .ranking import (
    Match,
    active_match_terms,
    group_matches,
    is_weak_partial_generic,
    make_snippet,
    score_record,
    should_consider,
    term_tokens,
    token_set,
)
from .records import Record, parse_record
from .scanner import match_sort_key
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_mtime, stat_size


WORD_RE = re.compile(r"[a-z0-9]+")
FINGERPRINT_VERSION = "sha256-normalized-term-v1"


@dataclass(frozen=True)
class MetadataLineEntry:
    line: int
    byte_start: int
    byte_length: int
    source_kind: str
    timestamp_present: bool
    timestamp_string: str | None
    role: str
    fingerprints: frozenset[str]


@dataclass
class MetadataFileTable:
    path: Path
    source_kind: str
    size: int
    mtime: float
    line_count: int
    content_hash: str | None = None
    entries: list[MetadataLineEntry] = field(default_factory=list)
    invalid_reason: str | None = None


@dataclass
class MetadataSeekIndex:
    files: dict[str, MetadataFileTable]
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    fingerprint_version: str = FINGERPRINT_VERSION

    def sanitized_summary(self) -> dict[str, Any]:
        source_kinds = sorted({table.source_kind for table in self.files.values()})
        return {
            "fingerprint_version": self.fingerprint_version,
            "indexed_file_count": len(self.files),
            "indexed_entry_count": int(self.metrics.get("indexed_entries", 0)),
            "fingerprinted_record_count": int(self.metrics.get("fingerprinted_records", 0)),
            "timestamped_record_count": int(self.metrics.get("timestamped_records", 0)),
            "missing_timestamp_record_count": int(self.metrics.get("missing_timestamp_records", 0)),
            "source_kinds": source_kinds,
            "raw_paths_included": False,
            "raw_text_included": False,
        }


def build_metadata_seek_index(
    plan: SourcePlan,
    *,
    default_tz,
) -> MetadataSeekIndex:
    started = time.perf_counter()
    files: dict[str, MetadataFileTable] = {}
    warnings: list[str] = []
    metrics: dict[str, Any] = {
        "build_bytes": 0,
        "build_elapsed_ms": 0.0,
        "indexed_files": 0,
        "indexed_entries": 0,
        "fingerprinted_records": 0,
        "timestamped_records": 0,
        "missing_timestamp_records": 0,
        "non_line_oriented_files": 0,
        "read_error_files": 0,
        "fingerprint_count": 0,
    }

    for path, _size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = list(enum_warnings)
        source_kind = source_kind_for_path(path, plan)
        if path.suffix != ".jsonl":
            metrics["non_line_oriented_files"] += 1
            files[str(path)] = MetadataFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                content_hash=None,
                invalid_reason="non_line_oriented_source",
            )
            continue

        table, bytes_read = _build_file_table(path, source_kind=source_kind, default_tz=default_tz)
        files[str(path)] = table
        metrics["build_bytes"] += bytes_read
        metrics["indexed_files"] += 1
        metrics["indexed_entries"] += len(table.entries)
        metrics["fingerprinted_records"] += sum(1 for entry in table.entries if entry.fingerprints)
        metrics["timestamped_records"] += sum(1 for entry in table.entries if entry.timestamp_present)
        metrics["missing_timestamp_records"] += sum(1 for entry in table.entries if not entry.timestamp_present)
        metrics["fingerprint_count"] += sum(len(entry.fingerprints) for entry in table.entries)
        if table.invalid_reason == "read_error":
            metrics["read_error_files"] += 1

    metrics["build_elapsed_ms"] = _elapsed_ms_float(started)
    return MetadataSeekIndex(files=files, metrics=metrics, warnings=warnings)


def search_with_metadata_seek(
    index: MetadataSeekIndex,
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None,
    selected_paths: set[str] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    warnings = list(index.warnings) + list(plan.warnings)
    query_fingerprints = _fingerprints_for_query(intent)
    if not query_fingerprints:
        return _fallback_payload(
            started,
            reason="query_fingerprint_absent",
            warnings=warnings,
            index=index,
            query_fingerprint_count=0,
        )

    candidate_entries = _candidate_entries(index, query_fingerprints, selected_paths=selected_paths)
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

    for table, entries in candidate_entries:
        fallback_reason = _table_fallback_reason(table)
        if fallback_reason:
            return _fallback_payload(
                started,
                reason=fallback_reason,
                warnings=warnings,
                index=index,
                query_fingerprint_count=len(query_fingerprints),
            )
        try:
            with table.path.open("rb") as handle:
                opened_paths.add(str(table.path))
                source_kinds.add(table.source_kind)
                for entry in entries:
                    if time.perf_counter() - started > plan.limits.max_elapsed:
                        warnings.append("Stopped metadata seek at max elapsed cap.")
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
                    record.source_format = entry.source_kind
                    if not should_consider(record, intent):
                        records_rejected_by_should_consider += 1
                        continue
                    key = _dedupe_key(record)
                    if key in dedupe:
                        candidate_records_deduped += 1
                        continue
                    dedupe.add(key)
                    candidate_records_scored += 1
                    score, reasons, artifacts = score_record(record, intent, cwd)
                    match = _materialize_match(record, score, reasons, artifacts, intent)
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
                        warnings.append("Stopped metadata seek at max match cap.")
                        break
        except OSError as exc:
            raise AdapterReadError(f"Could not open {table.path}: {exc}") from exc

    if weak_candidates_skipped:
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    matches = (matches + weak_matches)[: plan.limits.max_matches * 2]
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    candidate_entry_count = sum(len(entries) for _table, entries in candidate_entries)
    candidate_file_count = len(candidate_entries)
    summary = _search_summary(
        index=index,
        query_fingerprint_count=len(query_fingerprints),
        candidate_file_count=candidate_file_count,
        candidate_entry_count=candidate_entry_count,
        opened_file_count=len(opened_paths),
        searched_bytes=searched_bytes,
        records_considered=records_considered,
        source_kinds=source_kinds,
    )
    return {
        "status": "planned_seek",
        "fallback_reason": None,
        "query_fingerprint_count": len(query_fingerprints),
        "candidate_files": candidate_file_count,
        "candidate_entries": candidate_entry_count,
        "searched_files": len(opened_paths),
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "records_rejected_by_should_consider": records_rejected_by_should_consider,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
        "groups": group_matches(matches),
        "summary": summary,
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }


def _build_file_table(path: Path, *, source_kind: str, default_tz) -> tuple[MetadataFileTable, int]:
    entries: list[MetadataLineEntry] = []
    bytes_read = 0
    try:
        with path.open("rb") as handle:
            offset = 0
            for line_index, raw in enumerate(handle, 1):
                line_start = offset
                offset += len(raw)
                bytes_read += len(raw)
                record = parse_record(raw.decode("utf-8", errors="replace"), path=path, line=line_index, default_tz=default_tz)
                fingerprints = frozenset(_fingerprints_for_text(record.text))
                timestamp_string = record.raw_timestamp or (record.timestamp.isoformat() if record.timestamp else None)
                entries.append(
                    MetadataLineEntry(
                        line=line_index,
                        byte_start=line_start,
                        byte_length=len(raw),
                        source_kind=source_kind,
                        timestamp_present=timestamp_string is not None,
                        timestamp_string=timestamp_string,
                        role=record.role,
                        fingerprints=fingerprints,
                    )
                )
    except OSError:
        return (
            MetadataFileTable(
                path=path,
                source_kind=source_kind,
                size=stat_size(path),
                mtime=stat_mtime(path),
                line_count=0,
                content_hash=None,
                invalid_reason="read_error",
            ),
            bytes_read,
        )
    content_hash, hash_bytes_read = _hash_file_prefix(path, stat_size(path))
    return (
        MetadataFileTable(
            path=path,
            source_kind=source_kind,
            size=stat_size(path),
            mtime=stat_mtime(path),
            line_count=len(entries),
            content_hash=content_hash,
            entries=entries,
        ),
        bytes_read + hash_bytes_read,
    )


def _candidate_entries(
    index: MetadataSeekIndex,
    query_fingerprints: set[str],
    *,
    selected_paths: set[str] | None,
) -> list[tuple[MetadataFileTable, list[MetadataLineEntry]]]:
    candidates: list[tuple[MetadataFileTable, list[tuple[int, MetadataLineEntry]]]] = []
    for path, table in index.files.items():
        if selected_paths is not None and path not in selected_paths:
            continue
        table_entries: list[tuple[int, MetadataLineEntry]] = []
        for entry in table.entries:
            overlap = len(entry.fingerprints & query_fingerprints)
            if overlap:
                table_entries.append((overlap, entry))
        if table_entries:
            table_entries.sort(key=lambda item: (-item[0], item[1].line))
            candidates.append((table, table_entries))
    candidates.sort(key=lambda item: (-max(overlap for overlap, _entry in item[1]), str(item[0].path)))
    return [(table, [entry for _overlap, entry in entries]) for table, entries in candidates]


def _fingerprints_for_query(intent: QueryIntent) -> set[str]:
    raw_terms: set[str] = set(active_match_terms(intent)) | set(intent.base_terms)
    normalized_terms: set[str] = set()
    for term in raw_terms:
        normalized_terms.update(_normalized_terms_from_query_term(term))
    return {_hash_term(term) for term in normalized_terms if term}


def _fingerprints_for_text(text: str) -> set[str]:
    return {_hash_term(term) for term in _normalized_terms_from_text(text)}


def _normalized_terms_from_query_term(term: str) -> set[str]:
    tokens = term_tokens(term)
    terms = {token for token in tokens if len(token) >= 3}
    if len(tokens) > 1:
        terms.add(" ".join(tokens))
    for token in token_set(term):
        terms.add(token)
    return terms


def _normalized_terms_from_text(text: str) -> set[str]:
    tokens = [token for token in WORD_RE.findall(text.lower()) if len(token) >= 3]
    terms = set(token_set(text))
    terms.update(tokens)
    for width in (2, 3):
        for index in range(0, max(0, len(tokens) - width + 1)):
            terms.add(" ".join(tokens[index : index + width]))
    return terms


def _hash_term(term: str) -> str:
    normalized = " ".join(term.lower().split())
    return hashlib.sha256(f"{FINGERPRINT_VERSION}:{normalized}".encode("utf-8")).hexdigest()


def _table_fallback_reason(table: MetadataFileTable) -> str | None:
    if table.invalid_reason:
        return table.invalid_reason
    if stat_size(table.path) < table.size:
        return "metadata_index_truncated"
    if stat_size(table.path) != table.size or stat_mtime(table.path) != table.mtime:
        return "metadata_index_stale_or_changed"
    return None


def _hash_file_prefix(path: Path, size: int) -> tuple[str | None, int]:
    digest = hashlib.sha256()
    bytes_read = 0
    remaining = max(0, size)
    try:
        with path.open("rb") as handle:
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                bytes_read += len(chunk)
                remaining -= len(chunk)
    except OSError:
        return None, bytes_read
    if bytes_read != size:
        return None, bytes_read
    return digest.hexdigest(), bytes_read


def _dedupe_key(record: Record) -> tuple[str, str, str, str]:
    normalized_text = " ".join(record.text.split()).lower()
    source_ref = f"{record.source_format or ''}:{record.path}:{record.line}"
    return (
        normalized_text,
        record.raw_timestamp or "",
        record.cwd or "",
        source_ref,
    )


def _materialize_match(record: Record, score: float, reasons: list[str], artifacts: list[str], intent: QueryIntent) -> Match:
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


def _fallback_payload(
    started: float,
    *,
    reason: str,
    warnings: list[str],
    index: MetadataSeekIndex,
    query_fingerprint_count: int,
) -> dict[str, Any]:
    return {
        "status": "fallback",
        "fallback_reason": reason,
        "query_fingerprint_count": query_fingerprint_count,
        "candidate_files": 0,
        "candidate_entries": 0,
        "searched_files": 0,
        "searched_bytes": 0,
        "records_considered": 0,
        "records_rejected_by_should_consider": 0,
        "candidate_records_scored": 0,
        "candidate_records_deduped": 0,
        "groups": [],
        "summary": _search_summary(
            index=index,
            query_fingerprint_count=query_fingerprint_count,
            candidate_file_count=0,
            candidate_entry_count=0,
            opened_file_count=0,
            searched_bytes=0,
            records_considered=0,
            source_kinds=set(),
        ),
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }


def _search_summary(
    *,
    index: MetadataSeekIndex,
    query_fingerprint_count: int,
    candidate_file_count: int,
    candidate_entry_count: int,
    opened_file_count: int,
    searched_bytes: int,
    records_considered: int,
    source_kinds: set[str],
) -> dict[str, Any]:
    return {
        "fingerprint_version": index.fingerprint_version,
        "query_fingerprint_count": query_fingerprint_count,
        "indexed_file_count": len(index.files),
        "indexed_entry_count": int(index.metrics.get("indexed_entries", 0)),
        "candidate_file_count": candidate_file_count,
        "candidate_entry_count": candidate_entry_count,
        "opened_file_count": opened_file_count,
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "source_kinds": sorted(source_kinds),
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def _privacy_payload() -> dict[str, Any]:
    return {
        "index_stores_raw_text": False,
        "summary_includes_raw_text": False,
        "summary_includes_raw_paths": False,
        "fingerprints_are_hashed": True,
        "local_seek_requires_index_paths": True,
        "normal_match_groups_may_include_source_refs": True,
    }


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _elapsed_ms_float(started: float) -> float:
    return max(0.0, round((time.perf_counter() - started) * 1000, 3))
