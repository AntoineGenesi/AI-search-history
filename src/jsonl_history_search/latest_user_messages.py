from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping

from .conversation_records import extract_human_ask
from .timeutil import parse_datetime


DEDUPLICATION_STRATEGY = "normalized_timestamp_utc_and_text_merge_codex_history_session_mirrors"
SORT_STRATEGY = "timestamp_utc_desc_source_kind_ref_tiebreak"


@dataclass
class LatestUserMessage:
    timestamp_utc: Any
    text: str
    source_kind: str
    source_ref: str
    source_identifier: str
    line: int
    openable_source_ref: dict[str, Any] | None = None
    openable_target: str | None = None
    mirror_sources: list[dict] = field(default_factory=list)


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

    for source in _codex_sources(codex_home, max_files=max_files, warnings=warnings):
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

    messages, deduped_mirror_count = _dedupe_and_sort(candidates)
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


def _codex_sources(codex_home: Path, *, max_files: int, warnings: list[str]) -> list[_Source]:
    sources: list[_Source] = []
    history = codex_home / "history.jsonl"
    if history.is_file():
        sources.append(_Source(kind="codex_history", path=history, ref="codex_history:history.jsonl", identifier="history"))
    elif not history.exists():
        warnings.append("Missing Codex history file.")
    sessions_root = codex_home / "sessions"
    session_paths: list[Path] = []
    if sessions_root.is_dir():
        session_paths = sorted(
            sessions_root.rglob("*.jsonl"),
            key=lambda path: _mtime(path),
            reverse=True,
        )
    elif not sessions_root.exists():
        warnings.append("Missing Codex sessions root.")
    for path in session_paths:
        if len(sources) >= max_files:
            warnings.append("Stopped source selection at max file cap.")
            break
        sources.append(
            _Source(
                kind="codex_sessions",
                path=path,
                ref=_session_ref(path, codex_home, line=None),
                identifier=_session_identifier(path, codex_home),
            )
        )
    if not sources:
        warnings.append("No Codex history or session JSONL sources found.")
    return sources


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
        if not text or timestamp is None:
            return None
        identifier = _history_identifier(data)
        return LatestUserMessage(
            timestamp_utc=timestamp.astimezone(timezone.utc),
            text=text,
            source_kind=source.kind,
            source_ref=f"codex_history:history.jsonl:{line_number}",
            source_identifier=identifier,
            line=line_number,
        )

    extracted = extract_human_ask(data)
    if not extracted.ask_text:
        return None
    timestamp = parse_datetime(data.get("timestamp") or data.get("created_at") or data.get("time"), default_tz)
    if timestamp is None:
        return None
    return LatestUserMessage(
        timestamp_utc=timestamp.astimezone(timezone.utc),
        text=extracted.ask_text,
        source_kind=source.kind,
        source_ref=_session_ref(source.path, codex_home, line=line_number),
        source_identifier=source.identifier,
        line=line_number,
        openable_source_ref=_openable_source_ref(source.path, line_number),
        openable_target=_openable_target(source.path, line_number),
    )


def _dedupe_and_sort(messages: list[LatestUserMessage]) -> tuple[list[LatestUserMessage], int]:
    by_key: dict[tuple[str, str], LatestUserMessage] = {}
    deduped = 0
    for message in messages:
        key = (_timestamp_key(message.timestamp_utc), _normalized_text(message.text))
        existing = by_key.get(key)
        if existing is None:
            message.mirror_sources.append(_mirror_source(message))
            by_key[key] = message
            continue
        deduped += 1
        existing.mirror_sources.append(_mirror_source(message))
        if _source_priority(message.source_kind) > _source_priority(existing.source_kind):
            message.mirror_sources = existing.mirror_sources
            by_key[key] = message
    deduped_messages = list(by_key.values())
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


def _message_to_dict(message: LatestUserMessage, local_tz, snippet_chars: int) -> dict:
    text = message.text if snippet_chars == 0 else message.text[:snippet_chars]
    truncated = snippet_chars > 0 and len(message.text) > snippet_chars
    rendered = {
        "local_timestamp": message.timestamp_utc.astimezone(local_tz).isoformat(timespec="seconds"),
        "timestamp_utc": message.timestamp_utc.isoformat(timespec="seconds"),
        "source_kind": message.source_kind,
        "source_ref": message.source_ref,
        "source_identifier": message.source_identifier,
        "mirror_sources": message.mirror_sources,
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
    }
    if message.openable_source_ref:
        rendered["openable_source_ref"] = message.openable_source_ref
    if message.openable_target:
        rendered["openable_target"] = message.openable_target
    return rendered


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


def _normalized_text(text: str) -> str:
    return " ".join(text.split()).strip().casefold()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return "\n".join(line.rstrip() for line in str(value).strip().splitlines()).strip()


def _source_priority(source_kind: str) -> int:
    return 2 if source_kind == "codex_sessions" else 1


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _timezone_from_args(args):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
