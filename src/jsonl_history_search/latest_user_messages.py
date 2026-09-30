from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping

from .conversation_records import (
    ASSISTANT_OR_AGENT_MESSAGE,
    COMPACTED_HISTORY_SUMMARY,
    INJECTED_ENVIRONMENT_CONTEXT,
    SUBAGENT_NOTIFICATION,
    TOOL_ACTION_OUTPUT,
    TURN_CONTROL_OR_STATUS,
    UNKNOWN,
    classify_conversation_record,
    extract_human_ask,
)
from .timeutil import parse_datetime


MIRROR_CORRELATION_WINDOW_SECONDS = 5.0
DEDUPLICATION_STRATEGY = (
    "normalized_text_correlated_session_identity_bounded_chronology_and_adjacent_transport_pair"
)
SORT_STRATEGY = "timestamp_utc_desc_source_kind_ref_tiebreak"


@dataclass
class LatestUserMessage:
    timestamp_utc: Any
    text: str
    source_kind: str
    source_ref: str
    source_identifier: str
    thread_key: str
    line: int
    correlated_session_identity: str | None = None
    session_identity_source: str | None = None
    transport_record_type: str | None = None
    transport_payload_type: str | None = None
    openable_source_ref: dict[str, Any] | None = None
    openable_target: str | None = None
    mirror_sources: list[dict] = field(default_factory=list)
    logical_message_id: str | None = None
    correlation_status: str = "unresolved"
    canonical_authority: dict[str, Any] | None = None
    session_activity_location: dict[str, Any] | None = None
    correlation_diagnostics: list[dict[str, Any]] = field(default_factory=list)


def build_latest_user_messages_payload(args) -> dict:
    default_tz = _timezone_from_args(args)
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    limit = getattr(args, "limit", None) or args.max_matches
    snippet_chars = max(0, getattr(args, "snippet_chars", 500))
    result = latest_user_messages(
        codex_home=codex_home,
        default_tz=default_tz,
        limit=limit,
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_elapsed=args.max_elapsed,
        snippet_chars=snippet_chars,
    )
    return {"latest_user_messages": result}


def latest_user_messages(
    *,
    codex_home: Path,
    default_tz,
    limit: int,
    max_files: int,
    max_bytes: int,
    max_elapsed: float,
    snippet_chars: int = 500,
    collapse_canonical_duplicates: bool = True,
) -> dict:
    started = time.monotonic()
    warnings: list[str] = []
    included_sources: list[dict] = []
    excluded_sources: list[dict] = []
    candidates: list[LatestUserMessage] = []
    searched_files = 0
    searched_bytes = 0
    records_read = 0
    rejected_records = 0

    for source in _codex_sources(
        codex_home,
        max_files=max_files,
        warnings=warnings,
        deadline=started + max_elapsed,
    ):
        if time.monotonic() - started > max_elapsed:
            warnings.append("Stopped before opening next file at max elapsed cap.")
            excluded_sources.append(_source_metadata(source, "max_elapsed_before_open"))
            break
        try:
            size = source.path.stat().st_size
        except OSError:
            excluded_sources.append(_source_metadata(source, "stat_failed"))
            continue
        if searched_bytes + size > max_bytes:
            warnings.append("Stopped before opening next file at max byte cap.")
            excluded_sources.append(_source_metadata(source, "max_bytes_cap"))
            break
        source_records = 0
        source_rejected = 0
        searched_files += 1
        searched_bytes += size
        try:
            handle = source.path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            excluded_sources.append(_source_metadata(source, "open_failed"))
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                if time.monotonic() - started > max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    break
                records_read += 1
                source_records += 1
                record = _extract_user_message(line, source, line_number, codex_home, default_tz)
                if record is None:
                    rejected_records += 1
                    source_rejected += 1
                    continue
                candidates.append(record)
        included_sources.append(
            {
                **_source_metadata(source, "searched"),
                "bytes": size,
                "records_read": source_records,
                "records_rejected": source_rejected,
            }
        )

    messages, deduped_mirror_count = _dedupe_and_sort(
        candidates,
        collapse_canonical_duplicates=collapse_canonical_duplicates,
    )
    rendered = [_message_to_dict(item, default_tz, snippet_chars) for item in messages[:limit]]
    raw_paths_included = any(message.get("openable_target") for message in rendered)
    return {
        "schema_version": 1,
        "status": "ok" if rendered else "no_user_messages_found",
        "lookup": "deterministic_latest_user_messages",
        "source_scope": "codex_history_and_codex_sessions_only",
        "sort_strategy": SORT_STRATEGY,
        "deduplication_strategy": DEDUPLICATION_STRATEGY,
        "summary": {
            "message_count": len(rendered),
            "candidate_user_message_count": len(candidates),
            "deduped_mirror_count": deduped_mirror_count,
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "records_read": records_read,
            "records_rejected": rejected_records,
        },
        "sources": {
            "included": included_sources,
            "excluded": excluded_sources,
            "warnings": sorted(set(warnings)),
        },
        "messages": rendered,
        "privacy": {
            "raw_local_paths_included": raw_paths_included,
            "raw_session_ids_included": raw_paths_included,
            "raw_text_included": True,
            "source_identifiers_are_hashed": True,
        },
    }


@dataclass(frozen=True)
class _Source:
    kind: str
    path: Path
    ref: str
    identifier: str
    raw_session_id: str | None = None
    raw_session_identity_source: str | None = None


def _codex_sources(
    codex_home: Path,
    *,
    max_files: int,
    warnings: list[str],
    deadline: float | None = None,
) -> list[_Source]:
    sources: list[_Source] = []
    history = codex_home / "history.jsonl"
    if history.is_file():
        sources.append(_Source(kind="codex_history", path=history, ref="codex_history:history.jsonl", identifier="history"))
    elif not history.exists():
        warnings.append("Missing Codex history file.")
    sessions_root = codex_home / "sessions"
    session_paths: list[Path] = []
    if sessions_root.is_dir():
        session_paths = _bounded_session_paths(
            sessions_root,
            limit=max(0, max_files - len(sources)),
            deadline=deadline,
            warnings=warnings,
        )
    elif not sessions_root.exists():
        warnings.append("Missing Codex sessions root.")
    for path in session_paths:
        if len(sources) >= max_files:
            warnings.append("Stopped source selection at max file cap.")
            break
        raw_session_id, raw_session_identity_source = _session_raw_identity(path)
        sources.append(
            _Source(
                kind="codex_sessions",
                path=path,
                ref=_session_ref(path, codex_home, line=None),
                identifier=_session_identifier(path, codex_home),
                raw_session_id=raw_session_id,
                raw_session_identity_source=raw_session_identity_source,
            )
        )
    if not sources:
        warnings.append("No Codex history or session JSONL sources found.")
    return sources


def _bounded_session_paths(
    root: Path,
    *,
    limit: int,
    deadline: float | None,
    warnings: list[str],
) -> list[Path]:
    """Enumerate newest name-ordered session paths without statting the whole tree."""

    if limit <= 0:
        return []
    selected: list[Path] = []
    pending = [root]
    while pending and len(selected) < limit:
        if deadline is not None and time.monotonic() >= deadline:
            warnings.append("Stopped session source enumeration at max elapsed cap.")
            break
        directory = pending.pop()
        directories: list[Path] = []
        files: list[Path] = []
        try:
            with os.scandir(str(directory)) as entries:
                for entry in entries:
                    if deadline is not None and time.monotonic() >= deadline:
                        warnings.append("Stopped within session directory enumeration at max elapsed cap.")
                        break
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
                        files.append(Path(entry.path))
        except OSError:
            continue
        directories.sort(key=lambda path: path.name)
        files.sort(key=lambda path: path.name, reverse=True)
        selected.extend(files[: max(0, limit - len(selected))])
        # Stack order makes lexically newest year/month/day directories pop first.
        pending.extend(directories)
    return selected


def _extract_user_message(
    line: str,
    source: _Source,
    line_number: int,
    codex_home: Path,
    default_tz,
) -> LatestUserMessage | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, Mapping):
        return None
    if source.kind == "codex_history":
        text = _clean_text(data.get("text"))
        timestamp = parse_datetime(data.get("ts") or data.get("timestamp") or data.get("created_at"), default_tz)
        if not text or timestamp is None or _history_text_is_non_human(data, text):
            return None
        identifier = _history_identifier(data)
        return LatestUserMessage(
            timestamp_utc=timestamp.astimezone(timezone.utc),
            text=text,
            source_kind=source.kind,
            source_ref=f"codex_history:history.jsonl:{line_number}",
            source_identifier=identifier,
            thread_key=identifier,
            line=line_number,
            correlated_session_identity=_correlated_session_identity(data.get("session_id")),
            session_identity_source="history_session_id" if data.get("session_id") else None,
            openable_source_ref=_openable_source_ref(source.path, line_number),
            openable_target=_openable_target(source.path, line_number),
        )

    extracted = extract_human_ask(data)
    ask_text = extracted.ask_text or _simple_top_level_user_text(data)
    if not ask_text:
        return None
    timestamp = parse_datetime(data.get("timestamp") or data.get("created_at") or data.get("time"), default_tz)
    if timestamp is None:
        return None
    return LatestUserMessage(
        timestamp_utc=timestamp.astimezone(timezone.utc),
        text=ask_text,
        source_kind=source.kind,
        source_ref=_session_ref(source.path, codex_home, line=line_number),
        source_identifier=source.identifier,
        thread_key=source.identifier,
        line=line_number,
        correlated_session_identity=_correlated_session_identity(source.raw_session_id),
        session_identity_source=source.raw_session_identity_source,
        transport_record_type=str(data.get("type") or "") or None,
        transport_payload_type=(
            str(data.get("payload", {}).get("type") or "")
            if isinstance(data.get("payload"), Mapping)
            else None
        )
        or None,
        openable_source_ref=_openable_source_ref(source.path, line_number),
        openable_target=_openable_target(source.path, line_number),
    )


def _simple_top_level_user_text(data: Mapping[str, Any]) -> str | None:
    classification = classify_conversation_record(data)
    if classification.label != UNKNOWN or classification.confidence != "low":
        return None
    if str(data.get("role") or "").strip().casefold() != "user":
        return None
    if isinstance(data.get("payload"), Mapping) or isinstance(data.get("message"), Mapping):
        return None
    text = _clean_text(data.get("text") or data.get("content") or data.get("message"))
    return text or None


def _dedupe_and_sort(
    messages: list[LatestUserMessage],
    *,
    collapse_canonical_duplicates: bool,
) -> tuple[list[LatestUserMessage], int]:
    canonical_messages = [message for message in messages if message.source_kind == "codex_history"]
    canonical_duplicates = 0
    if collapse_canonical_duplicates:
        canonical_messages, canonical_duplicates = _collapse_canonical_history_duplicates(canonical_messages)
    canonical_groups: list[list[LatestUserMessage]] = [
        [message] for message in canonical_messages
    ]
    session_messages, transport_duplicates = _collapse_session_transport_duplicates(
        [item for item in messages if item.source_kind != "codex_history"]
    )
    standalone: list[LatestUserMessage] = []
    deduped = canonical_duplicates + transport_duplicates
    for message in session_messages:
        matches = [group for group in canonical_groups if _is_correlated_mirror(group[0], message)]
        if len(matches) == 1:
            matches[0].append(message)
            deduped += 1
            continue
        if len(matches) > 1:
            diagnostic = {
                "reason": "session row matched multiple canonical rows inside the mirror correlation window",
                "session_source_ref": message.source_ref,
                "canonical_source_refs": [group[0].source_ref for group in matches],
            }
            message.correlation_status = "ambiguous_multiple_canonical"
            message.correlation_diagnostics.append(diagnostic)
            for group in matches:
                group[0].correlation_status = "ambiguous_multiple_canonical"
                group[0].correlation_diagnostics.append(diagnostic)
        standalone.append(message)

    deduped_messages = []
    for group in canonical_groups:
        canonical = group[0]
        session_copies = group[1:]
        canonical.mirror_sources = canonical.mirror_sources or [_mirror_source(canonical)]
        for session_copy in session_copies:
            canonical.mirror_sources.extend(session_copy.mirror_sources or [_mirror_source(session_copy)])
        canonical.canonical_authority = _mirror_source(canonical)
        canonical.logical_message_id = _logical_message_id(canonical)
        if canonical.correlation_status == "ambiguous_multiple_canonical":
            canonical.session_activity_location = None
        elif len(session_copies) == 1:
            canonical.correlation_status = "unique_canonical_session_mirror"
            canonical.session_activity_location = _mirror_source(session_copies[0])
        elif len(session_copies) > 1:
            canonical.correlation_status = "ambiguous_multiple_session_copies"
            canonical.correlation_diagnostics.append(
                {
                    "reason": "canonical row matched multiple session rows inside the mirror correlation window",
                    "canonical_source_ref": canonical.source_ref,
                    "session_source_refs": [item.source_ref for item in session_copies],
                }
            )
        else:
            canonical.correlation_status = "canonical_only"
        deduped_messages.append(canonical)

    for message in standalone:
        if not message.mirror_sources:
            message.mirror_sources = [_mirror_source(message)]
        message.logical_message_id = _logical_message_id(message)
        if message.correlation_status == "unresolved":
            message.correlation_status = "session_only"
        message.session_activity_location = _mirror_source(message)
        deduped_messages.append(message)

    deduped_messages.sort(
        key=lambda item: (
            item.timestamp_utc,
            _source_priority(item.source_kind),
            item.source_ref,
            item.line,
        ),
        reverse=True,
    )
    return deduped_messages, deduped


def _collapse_canonical_history_duplicates(
    messages: list[LatestUserMessage],
) -> tuple[list[LatestUserMessage], int]:
    ordered = sorted(messages, key=lambda item: item.line)
    collapsed: list[LatestUserMessage] = []
    duplicate_count = 0
    for message in ordered:
        previous = collapsed[-1] if collapsed else None
        is_adjacent_duplicate = bool(
            previous
            and message.line == previous.line + 1
            and message.source_identifier == previous.source_identifier
            and message.correlated_session_identity == previous.correlated_session_identity
            and _normalized_text(message.text) == _normalized_text(previous.text)
            and abs((message.timestamp_utc - previous.timestamp_utc).total_seconds())
            <= MIRROR_CORRELATION_WINDOW_SECONDS
        )
        if not is_adjacent_duplicate:
            collapsed.append(message)
            continue
        message.mirror_sources = [
            *(previous.mirror_sources or [_mirror_source(previous)]),
            _mirror_source(message),
        ]
        collapsed[-1] = message
        duplicate_count += 1
    return collapsed, duplicate_count


def _collapse_session_transport_duplicates(
    messages: list[LatestUserMessage],
) -> tuple[list[LatestUserMessage], int]:
    buckets: dict[tuple[str, str, str, str], list[LatestUserMessage]] = {}
    for message in messages:
        key = (
            message.source_identifier,
            message.correlated_session_identity or "uncorrelated",
            message.timestamp_utc.isoformat(timespec="milliseconds"),
            _normalized_text(message.text),
        )
        buckets.setdefault(key, []).append(message)

    collapsed: list[LatestUserMessage] = []
    duplicate_count = 0
    for group in buckets.values():
        ordered = sorted(group, key=lambda item: item.line)
        signatures = {
            (item.transport_record_type, item.transport_payload_type)
            for item in ordered
        }
        is_known_pair = (
            len(ordered) == 2
            and ordered[1].line == ordered[0].line + 1
            and signatures
            == {
                ("response_item", "message"),
                ("event_msg", "user_message"),
            }
        )
        if not is_known_pair:
            collapsed.extend(ordered)
            continue
        activity_boundary = ordered[-1]
        activity_boundary.mirror_sources = [_mirror_source(item) for item in ordered]
        collapsed.append(activity_boundary)
        duplicate_count += 1
    return collapsed, duplicate_count


def _message_to_dict(message: LatestUserMessage, local_tz, snippet_chars: int) -> dict:
    text = message.text if snippet_chars == 0 else message.text[:snippet_chars]
    truncated = snippet_chars > 0 and len(message.text) > snippet_chars
    rendered = {
        "local_timestamp": message.timestamp_utc.astimezone(local_tz).isoformat(timespec="seconds"),
        "timestamp_utc": message.timestamp_utc.isoformat(timespec="seconds"),
        "source_kind": message.source_kind,
        "source_ref": message.source_ref,
        "source_identifier": message.source_identifier,
        "thread_key": message.thread_key,
        "thread_keys": _thread_keys(message),
        "mirror_sources": message.mirror_sources,
        "logical_message_id": message.logical_message_id,
        "correlation_status": message.correlation_status,
        "correlated_session_identity": message.correlated_session_identity,
        "session_identity_source": message.session_identity_source,
        "canonical_authority": message.canonical_authority,
        "session_activity_location": message.session_activity_location,
        "correlation_diagnostics": message.correlation_diagnostics,
        "snippet": text.replace("\n", " "),
        "truncated": truncated,
    }
    if message.openable_source_ref:
        rendered["openable_source_ref"] = message.openable_source_ref
    if message.openable_target:
        rendered["openable_target"] = message.openable_target
    return rendered


def _mirror_source(message: LatestUserMessage) -> dict:
    rendered = {
        "source_kind": message.source_kind,
        "source_ref": message.source_ref,
        "source_identifier": message.source_identifier,
        "thread_key": message.thread_key,
        "timestamp_utc": message.timestamp_utc.isoformat(timespec="milliseconds"),
        "correlated_session_identity": message.correlated_session_identity,
        "session_identity_source": message.session_identity_source,
    }
    if message.openable_source_ref:
        rendered["openable_source_ref"] = message.openable_source_ref
    if message.openable_target:
        rendered["openable_target"] = message.openable_target
    return rendered


def _thread_keys(message: LatestUserMessage) -> list[str]:
    keys = {message.thread_key}
    for mirror in message.mirror_sources:
        key = mirror.get("thread_key") or mirror.get("source_identifier")
        if key:
            keys.add(str(key))
    return sorted(keys)


def _history_text_is_non_human(data: Mapping[str, Any], text: str) -> bool:
    classification = classify_conversation_record(data)
    if classification.label in {
        ASSISTANT_OR_AGENT_MESSAGE,
        COMPACTED_HISTORY_SUMMARY,
        INJECTED_ENVIRONMENT_CONTEXT,
        SUBAGENT_NOTIFICATION,
        TOOL_ACTION_OUTPUT,
        TURN_CONTROL_OR_STATUS,
    }:
        return True
    normalized = _normalized_text(text)
    if normalized.startswith(("<subagent_notification", "<environment_context", "<tool_output", "<tool_result")):
        return True
    return any(
        marker in normalized
        for marker in (
            "subagent_notification",
            "subagent notification",
            "tool output",
            "tool_result",
            "function_call_output",
            "compacted history summary",
            "inherited summary",
            "inherited conversation summary",
            "side-thread boundary",
            "side conversation boundary",
            "conversation boundary",
        )
    )


def _openable_source_ref(path: Path, line: int) -> dict[str, Any]:
    return {"path": _resolved_path_text(path), "line": line}


def _openable_target(path: Path, line: int) -> str:
    return f"{_resolved_path_text(path)}:{line}"


def _resolved_path_text(path: Path) -> str:
    try:
        return str(path.expanduser().resolve())
    except OSError:
        return str(path.expanduser())


def _source_metadata(source: _Source, status: str) -> dict:
    return {
        "kind": source.kind,
        "source_ref": source.ref,
        "source_identifier": source.identifier,
        "status": status,
    }


def _session_ref(path: Path, codex_home: Path, line: int | None) -> str:
    date_label = _session_date_label(path, codex_home)
    identifier = _session_identifier(path, codex_home)
    base = f"codex_sessions:{date_label}/{identifier}"
    if line is not None:
        return f"{base}:{line}"
    return base


def _session_date_label(path: Path, codex_home: Path) -> str:
    try:
        rel = path.resolve().relative_to((codex_home / "sessions").resolve())
    except (OSError, ValueError):
        return "unknown-date"
    parts = rel.parts
    if len(parts) >= 4 and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        return "/".join(parts[:3])
    return "unknown-date"


def _session_identifier(path: Path, codex_home: Path) -> str:
    try:
        rel = path.resolve().relative_to((codex_home / "sessions").resolve())
        raw = str(rel)
    except (OSError, ValueError):
        raw = path.name
    return "session-" + hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]


def _history_identifier(data: Mapping[str, Any]) -> str:
    session_id = data.get("session_id")
    if session_id:
        digest = hashlib.sha256(str(session_id).encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"history-session-{digest}"
    return "history"


def _timestamp_key(value) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _is_correlated_mirror(canonical: LatestUserMessage, session_copy: LatestUserMessage) -> bool:
    if canonical.source_kind != "codex_history" or session_copy.source_kind != "codex_sessions":
        return False
    if not canonical.correlated_session_identity or not session_copy.correlated_session_identity:
        return False
    if canonical.correlated_session_identity != session_copy.correlated_session_identity:
        return False
    if _normalized_text(canonical.text) != _normalized_text(session_copy.text):
        return False
    delta = abs((session_copy.timestamp_utc - canonical.timestamp_utc).total_seconds())
    return delta <= MIRROR_CORRELATION_WINDOW_SECONDS


def _logical_message_id(message: LatestUserMessage) -> str:
    identity = message.correlated_session_identity or "uncorrelated"
    material = "\0".join((identity, _normalized_text(message.text), message.timestamp_utc.isoformat()))
    return "logical-message-" + hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:16]


def _correlated_session_identity(raw_session_id: Any) -> str | None:
    if raw_session_id is None or not str(raw_session_id).strip():
        return None
    digest = hashlib.sha256(str(raw_session_id).encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"session-identity-{digest}"


def _session_raw_identity(path: Path) -> tuple[str | None, str | None]:
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    with handle:
        for index, line in enumerate(handle):
            if index >= 64:
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping) or str(record.get("type") or "") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, Mapping):
                continue
            raw_id = payload.get("session_id") or payload.get("id")
            return (str(raw_id), "session_meta") if raw_id else (None, None)
    stem = path.stem
    if stem.startswith("rollout-"):
        return None, None
    return (stem, "filename_fallback") if stem else (None, None)


def _normalized_text(text: str) -> str:
    return " ".join(text.split()).strip().casefold()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return "\n".join(line.rstrip() for line in str(value).strip().splitlines()).strip()


def _source_priority(source_kind: str) -> int:
    return 2 if source_kind == "codex_history" else 1


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _timezone_from_args(args):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
