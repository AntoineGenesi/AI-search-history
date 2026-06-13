from __future__ import annotations

import hashlib
import re
import time
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
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_mtime, stat_size
from .tool_events import detect_events_for_record


WORD_RE = re.compile(r"[a-z0-9]+")
STRUCTURAL_SIDECAR_VERSION = "structural-codex-claude-line-v1"
TEXT_PROBE_LIMIT = 4000
MAX_CANDIDATE_FILES = 12
MAX_ENTRIES_PER_FILE = 80


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
) -> dict[str, Any]:
    started = time.perf_counter()
    warnings = list(index.warnings) + list(plan.warnings)
    query_profile = _query_profile(intent)
    candidate_entries = _candidate_entries(index, query_profile, selected_paths=selected_paths)
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
                query_profile=query_profile,
            )
        try:
            with table.path.open("rb") as handle:
                opened_paths.add(str(table.path))
                source_kinds.add(table.source_kind)
                for entry in entries:
                    if time.perf_counter() - started > plan.limits.max_elapsed:
                        warnings.append("Stopped structural sidecar at max elapsed cap.")
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
                        warnings.append("Stopped structural sidecar at max match cap.")
                        break
        except OSError as exc:
            raise AdapterReadError(f"Could not open {table.path}: {exc}") from exc

    if weak_candidates_skipped:
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    matches = (matches + weak_matches)[: plan.limits.max_matches * 2]
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    candidate_entry_count = sum(len(entries) for _table, entries in candidate_entries)
    return {
        "status": "planned_structural_seek",
        "fallback_reason": None,
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_files": len(candidate_entries),
        "candidate_entries": candidate_entry_count,
        "searched_files": len(opened_paths),
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "records_rejected_by_should_consider": records_rejected_by_should_consider,
        "candidate_records_scored": candidate_records_scored,
        "candidate_records_deduped": candidate_records_deduped,
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
        ),
        "privacy": _privacy_payload(),
        "warnings": sorted(set(warnings)),
        "elapsed_ms": _elapsed_ms(started),
    }


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
            structural_overlap = len(entry.structural_tags & query_tags)
            event_bonus = len(entry.event_families & set(query_profile["event_families"]))
            if not lexical_overlap and not structural_overlap and not event_bonus:
                continue
            diversity_bonus = 0 if entry.session_hash in session_seen else 1
            session_seen.add(entry.session_hash)
            score = (structural_overlap + event_bonus, lexical_overlap, diversity_bonus, -entry.line)
            scored_entries.append((score, entry))
        if scored_entries:
            scored_entries.sort(key=lambda item: item[0], reverse=True)
            candidates.append((table, scored_entries[: max(1, min(MAX_ENTRIES_PER_FILE, len(scored_entries)))]))
    candidates.sort(key=lambda item: (item[1][0][0], len(item[1])), reverse=True)
    candidates = candidates[:MAX_CANDIDATE_FILES]
    return [(table, [entry for _score, entry in entries]) for table, entries in candidates]


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
    if "date" in terms or "timeline" in terms or re.search(r"\bwhen\b", lower_terms):
        structural_tags.add("time:timestamp_present")
    return {
        "lexical_terms": terms,
        "structural_tags": structural_tags,
        "event_families": event_families,
    }


def _sanitized_query_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "lexical_term_count": len(profile["lexical_terms"]),
        "structural_tags": sorted(profile["structural_tags"]),
        "event_families": sorted(profile["event_families"]),
        "raw_query_text_included": False,
    }


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
) -> dict[str, Any]:
    return {
        "status": "fallback_required",
        "fallback_reason": reason,
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_files": 0,
        "candidate_entries": 0,
        "searched_files": 0,
        "searched_bytes": 0,
        "records_considered": 0,
        "records_rejected_by_should_consider": 0,
        "candidate_records_scored": 0,
        "candidate_records_deduped": 0,
        "groups": [],
        "summary": {
            **index.sanitized_summary(),
            "fallback_reason": reason,
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
) -> dict[str, Any]:
    return {
        **index.sanitized_summary(),
        "query_profile": _sanitized_query_profile(query_profile),
        "candidate_file_count": candidate_file_count,
        "candidate_entry_count": candidate_entry_count,
        "opened_file_count": opened_file_count,
        "searched_bytes": searched_bytes,
        "records_considered": records_considered,
        "source_kinds": sorted(source_kinds),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
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
