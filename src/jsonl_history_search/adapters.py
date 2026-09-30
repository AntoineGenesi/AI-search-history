from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .records import Record, parse_record, text_from_item
from .timeutil import parse_datetime


class AdapterReadError(OSError):
    pass


@dataclass
class RawLineScanStats:
    raw_lines_examined: int = 0
    raw_lines_skipped_before_full_json_parse: int = 0
    full_json_records_parsed: int = 0
    malformed_rows: int = 0
    skipped_rows: int = 0
    ignored_rows: int = 0
    weak_timestamp_records: int = 0
    fallback_records: int = 0
    partial_failures: list[dict[str, object]] | None = None

    def note_partial(self, *, provider: str, path: Path, line: int | None, reason: str) -> None:
        if self.partial_failures is None:
            self.partial_failures = []
        self.partial_failures.append(
            {"provider": provider, "path": str(path), "line": line, "reason": reason}
        )


class RawLinePreParseFilter(Protocol):
    def should_parse(self, raw_line: str) -> bool:
        ...


class RecordAdapter(Protocol):
    name: str
    line_oriented: bool
    supports_append_only: bool

    def can_handle(self, path: Path, source_kind: str) -> bool:
        ...

    def iter_records(
        self,
        path: Path,
        *,
        source_kind: str,
        default_tz,
        raw_line_filter: RawLinePreParseFilter | None = None,
        raw_line_stats: RawLineScanStats | None = None,
        on_raw_line_examined: Callable[[Path, int], None] | None = None,
        on_raw_line_skipped: Callable[[Path, int], None] | None = None,
    ) -> Iterable[Record]:
        ...


class JsonlAdapter:
    name = "jsonl"
    line_oriented = True
    supports_append_only = True

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return path.suffix == ".jsonl"

    def iter_records(
        self,
        path: Path,
        *,
        source_kind: str,
        default_tz,
        raw_line_filter: RawLinePreParseFilter | None = None,
        raw_line_stats: RawLineScanStats | None = None,
        on_raw_line_examined: Callable[[Path, int], None] | None = None,
        on_raw_line_skipped: Callable[[Path, int], None] | None = None,
    ) -> Iterable[Record]:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        with handle:
            for line_number, line in enumerate(handle, 1):
                if raw_line_stats is not None:
                    raw_line_stats.raw_lines_examined += 1
                if on_raw_line_examined is not None:
                    on_raw_line_examined(path, line_number)
                if raw_line_filter is not None and not raw_line_filter.should_parse(line):
                    if raw_line_stats is not None:
                        raw_line_stats.raw_lines_skipped_before_full_json_parse += 1
                    if on_raw_line_skipped is not None:
                        on_raw_line_skipped(path, line_number)
                    continue
                if raw_line_stats is not None:
                    raw_line_stats.full_json_records_parsed += 1
                record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
                record.source_format = source_kind or self.name
                yield record


class GenericMessageArrayJsonAdapter:
    name = "generic_message_array_json"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return path.suffix == ".json"

    def iter_records(
        self,
        path: Path,
        *,
        source_kind: str,
        default_tz,
        raw_line_filter: RawLinePreParseFilter | None = None,
        raw_line_stats: RawLineScanStats | None = None,
        on_raw_line_examined: Callable[[Path, int], None] | None = None,
        on_raw_line_skipped: Callable[[Path, int], None] | None = None,
    ) -> Iterable[Record]:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                data = json.load(handle)
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return
        messages = data.get("messages")
        if not isinstance(messages, list):
            return
        conversation_id = _string_or_none(data.get("conversation_id"))
        top_level_unsupported = sorted(set(data) - {"conversation_id", "messages"})
        for index, message in enumerate(messages, 1):
            if raw_line_stats is not None:
                raw_line_stats.full_json_records_parsed += 1
            if not isinstance(message, dict):
                yield Record(
                    path=str(path),
                    line=index,
                    role="",
                    text=text_from_item(message),
                    timestamp=None,
                    raw={"message": message, "conversation_id": conversation_id},
                    source_format=self.name,
                    conversation_id=conversation_id,
                    unsupported_fields=top_level_unsupported or None,
                    lossy_conversions=["messages[] item was not an object"],
                )
                continue
            raw_timestamp = message.get("created_at")
            content = message.get("content")
            unsupported = sorted(
                (set(message) - {"id", "role", "created_at", "content"})
                | {f"conversation.{key}" for key in top_level_unsupported}
            )
            lossy = _lossy_conversions(message)
            yield Record(
                path=str(path),
                line=index,
                role=str(message.get("role") or ""),
                text=text_from_item(content),
                timestamp=parse_datetime(raw_timestamp, default_tz),
                raw_timestamp=str(raw_timestamp) if raw_timestamp else None,
                raw={"message": message, "conversation_id": conversation_id},
                source_format=self.name,
                record_id=_string_or_none(message.get("id")),
                conversation_id=conversation_id,
                unsupported_fields=unsupported or None,
                lossy_conversions=lossy or None,
            )


class GeminiJsonAdapter:
    name = "gemini_legacy_json"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("gemini_") and path.suffix.lower() == ".json"

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None) -> Iterable[Record]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            _note_malformed(raw_line_stats, "gemini", path, exc.lineno, "malformed legacy session JSON")
            return
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            _note_skipped(raw_line_stats, "gemini", path, None, "legacy session has no messages array")
            return
        session_id = _string_or_none(data.get("sessionId")) or path.stem
        cwd = _string_or_none(data.get("cwd") or data.get("projectPath"))
        for index, message in enumerate(data["messages"]):
            if not isinstance(message, dict):
                _note_skipped(raw_line_stats, "gemini", path, index, "non-object message")
                continue
            record = _gemini_message_record(
                message,
                path=path,
                source_kind=source_kind,
                locator=f"{path}:messages[{index}]",
                ordinal=index + 1,
                session_id=session_id,
                cwd=cwd,
                default_tz=default_tz,
            )
            if record.text.strip():
                if raw_line_stats is not None:
                    raw_line_stats.full_json_records_parsed += 1
                yield record


class GeminiJsonlAdapter:
    name = "gemini_event_jsonl"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("gemini_") and path.suffix.lower() == ".jsonl"

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None) -> Iterable[Record]:
        session: dict[str, Any] = {}
        messages: list[tuple[dict[str, Any], str, int]] = []
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        with handle:
            for line_number, raw in enumerate(handle, 1):
                if raw_line_stats is not None:
                    raw_line_stats.raw_lines_examined += 1
                if on_raw_line_examined is not None:
                    on_raw_line_examined(path, line_number)
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw.lstrip("\ufeff"))
                except json.JSONDecodeError:
                    _note_malformed(raw_line_stats, "gemini", path, line_number, "malformed event JSONL row")
                    continue
                if raw_line_stats is not None:
                    raw_line_stats.full_json_records_parsed += 1
                if not isinstance(event, dict):
                    _note_skipped(raw_line_stats, "gemini", path, line_number, "non-object event row")
                    continue
                patch = event.get("$set")
                if patch is not None:
                    if not isinstance(patch, dict):
                        _note_malformed(raw_line_stats, "gemini", path, line_number, "non-object $set patch")
                        continue
                    for key, value in patch.items():
                        if key == "messages":
                            if isinstance(value, list):
                                messages = [
                                    (item, f"{path}:{line_number}#/$set/messages/{index}", line_number)
                                    for index, item in enumerate(value)
                                    if isinstance(item, dict)
                                ]
                            else:
                                _note_malformed(raw_line_stats, "gemini", path, line_number, "non-array messages reseed")
                        else:
                            session[key] = value
                    continue
                if event.get("kind") == "main":
                    session.update(event)
                    continue
                if isinstance(event.get("type"), str):
                    messages.append((event, f"{path}:{line_number}", line_number))
        session_id = _string_or_none(session.get("sessionId")) or path.stem
        cwd = _string_or_none(session.get("cwd") or session.get("projectPath"))
        for ordinal, (message, locator, physical_line) in enumerate(messages, 1):
            record = _gemini_message_record(
                message,
                path=path,
                source_kind=source_kind,
                locator=locator,
                ordinal=ordinal,
                session_id=session_id,
                cwd=cwd,
                default_tz=default_tz,
                physical_line=physical_line,
            )
            if record.text.strip():
                yield record


class GrokSessionAdapter:
    name = "grok_compound_session"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("grok_") and path.name in {"updates.jsonl", "chat_history.jsonl"}

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None) -> Iterable[Record]:
        session_dir = path.parent
        summary_path = session_dir / "summary.json"
        summary: dict[str, Any] = {}
        summary_usable = False
        if summary_path.is_file():
            try:
                parsed = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    summary = parsed
                    summary_id, summary_cwd = _grok_summary_identity(summary)
                    summary_usable = bool(summary_id or summary_cwd)
            except (OSError, json.JSONDecodeError):
                _note_malformed(raw_line_stats, "grok", summary_path, None, "malformed or unreadable summary sidecar")
        updates_path = session_dir / "updates.jsonl"
        records = _grok_update_records(
            updates_path,
            source_kind=source_kind,
            summary=summary,
            default_tz=default_tz,
            stats=raw_line_stats,
            summary_usable=summary_usable,
            on_raw_line_examined=on_raw_line_examined,
        ) if updates_path.is_file() else []
        if records:
            yield from records
            return
        fallback_path = session_dir / "chat_history.jsonl"
        if fallback_path.is_file():
            fallback = _grok_fallback_records(
                fallback_path,
                source_kind=source_kind,
                summary=summary,
                default_tz=default_tz,
                stats=raw_line_stats,
                summary_usable=summary_usable,
                on_raw_line_examined=on_raw_line_examined,
            )
            if raw_line_stats is not None:
                raw_line_stats.fallback_records += len(fallback)
            yield from fallback


class AiderMarkdownAdapter:
    name = "aider_markdown"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("aider_") and path.name == ".aider.chat.history.md"

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None) -> Iterable[Record]:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff").splitlines()
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        blocks: list[tuple[str, list[str], int, int]] = []
        role = "system"
        content: list[str] = []
        start_line = 1

        def flush(end_line: int) -> None:
            nonlocal content
            text = "\n".join(content).strip()
            if text:
                blocks.append((role, content[:], start_line, end_line))
            content = []

        for line_number, line in enumerate(lines, 1):
            stripped = line.lstrip()
            if stripped.startswith(">"):
                if role != "user" and content:
                    flush(line_number - 1)
                    start_line = line_number
                role = "user"
                quoted = stripped[1:]
                content.append((quoted[1:] if quoted.startswith(" ") else quoted).rstrip())
            else:
                if role == "user" and line.strip():
                    flush(line_number - 1)
                    role = "assistant"
                    start_line = line_number
                content.append(line)
        flush(len(lines))
        for index, (block_role, block_lines, first, last) in enumerate(blocks, 1):
            if raw_line_stats is not None:
                raw_line_stats.full_json_records_parsed += 1
                raw_line_stats.weak_timestamp_records += 1
            yield Record(
                path=str(path),
                line=first,
                role=block_role,
                text="\n".join(block_lines).strip(),
                timestamp=None,
                raw_timestamp=mtime,
                raw={"start_line": first, "end_line": last},
                source_format=source_kind,
                record_id=f"block-{index}",
                conversation_id=str(path),
                provider="aider",
                source_ref=f"{path}:{first}-{last}",
                source_refs=[f"{path}:{first}-{last}"],
                native_id=f"block-{index}",
                timestamp_quality="file_mtime_weak_not_message_time",
                coverage_notes=["Aider has no per-message timestamps; file mtime is weak conversation-level evidence only."],
            )


ADAPTERS: tuple[RecordAdapter, ...] = (
    GeminiJsonAdapter(),
    GeminiJsonlAdapter(),
    GrokSessionAdapter(),
    AiderMarkdownAdapter(),
    JsonlAdapter(),
    GenericMessageArrayJsonAdapter(),
)


def adapter_for_file(path: Path, source_kind: str) -> RecordAdapter:
    for adapter in ADAPTERS:
        if adapter.can_handle(path, source_kind):
            return adapter
    return JsonlAdapter()


def iter_records_for_file(
    path: Path,
    source_kind: str,
    default_tz,
    *,
    raw_line_filter: RawLinePreParseFilter | None = None,
    raw_line_stats: RawLineScanStats | None = None,
    on_raw_line_examined: Callable[[Path, int], None] | None = None,
    on_raw_line_skipped: Callable[[Path, int], None] | None = None,
) -> Iterable[Record]:
    records = adapter_for_file(path, source_kind).iter_records(
        path,
        source_kind=source_kind,
        default_tz=default_tz,
        raw_line_filter=raw_line_filter,
        raw_line_stats=raw_line_stats,
        on_raw_line_examined=on_raw_line_examined,
        on_raw_line_skipped=on_raw_line_skipped,
    )
    for record in records:
        if record.provider is None:
            record.provider = _provider_from_source_kind(source_kind)
        if record.source_ref is None:
            record.source_ref = f"{record.path}:{record.line}"
        if record.source_refs is None:
            record.source_refs = [record.source_ref]
        if record.native_id is None:
            record.native_id = record.record_id
        yield record


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _provider_from_source_kind(source_kind: str) -> str:
    for provider in ("codex", "claude", "gemini", "grok", "aider"):
        if provider in source_kind:
            return provider
    return "unknown"


def _lossy_conversions(message: dict[str, Any]) -> list[str]:
    lossy: list[str] = []
    content = message.get("content")
    if not isinstance(content, str) and content is not None:
        lossy.append("content normalized with text_from_item")
    return lossy


def _gemini_message_record(message: dict[str, Any], *, path: Path, source_kind: str, locator: str,
                           ordinal: int, session_id: str, cwd: str | None, default_tz,
                           physical_line: int | None = None) -> Record:
    raw_timestamp = message.get("timestamp")
    role = str(message.get("type") or "model")
    role = {"model": "assistant", "gemini": "assistant", "info": "system", "error": "system"}.get(role, role)
    native_id = _string_or_none(message.get("id")) or f"message-{ordinal}"
    return Record(
        path=str(path), line=physical_line or ordinal, role=role,
        text=text_from_item(message.get("content")), timestamp=parse_datetime(raw_timestamp, default_tz),
        cwd=cwd, raw_timestamp=str(raw_timestamp) if raw_timestamp else None, raw=message,
        source_format=source_kind, record_id=native_id, conversation_id=session_id,
        provider="gemini", source_ref=locator, source_refs=[locator], native_id=native_id,
    )


def _note_malformed(stats: RawLineScanStats | None, provider: str, path: Path, line: int | None, reason: str) -> None:
    if stats is not None:
        stats.malformed_rows += 1
        stats.note_partial(provider=provider, path=path, line=line, reason=reason)


def _note_skipped(stats: RawLineScanStats | None, provider: str, path: Path, line: int | None, reason: str) -> None:
    if stats is not None:
        stats.skipped_rows += 1
        stats.note_partial(provider=provider, path=path, line=line, reason=reason)


def _note_ignored(stats: RawLineScanStats | None) -> None:
    if stats is not None:
        stats.ignored_rows += 1


def _grok_timestamp(event: dict[str, Any], update: dict[str, Any], default_tz):
    value = (((event.get("params") or {}).get("_meta") or {}).get("agentTimestampMs")
             or ((update.get("_meta") or {}).get("agentTimestampMs")) or event.get("timestamp"))
    if isinstance(value, (int, float)) and value > 10_000_000_000:
        value = value / 1000
    return parse_datetime(value, default_tz), str(value) if value is not None else None


def _grok_summary_identity(summary: dict[str, Any]) -> tuple[str | None, str | None]:
    info = summary.get("info")
    if not isinstance(info, dict):
        return None, None
    return _string_or_none(info.get("id")), _string_or_none(info.get("cwd"))


def _grok_update_records(path: Path, *, source_kind: str, summary: dict[str, Any], default_tz,
                         stats: RawLineScanStats | None, summary_usable: bool,
                         on_raw_line_examined=None) -> list[Record]:
    records: list[Record] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return records
    summary_id, summary_cwd = _grok_summary_identity(summary)
    session_id = summary_id or path.parent.name
    cwd = summary_cwd
    summary_ref = f"{path.parent / 'summary.json'}#info" if summary_usable else None
    last_chunk_kind: str | None = None
    tool_by_id: dict[str, Record] = {}
    with handle:
        for line_number, raw in enumerate(handle, 1):
            if stats is not None:
                stats.raw_lines_examined += 1
            if on_raw_line_examined is not None:
                on_raw_line_examined(path, line_number)
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                _note_malformed(stats, "grok", path, line_number, "malformed updates JSONL row")
                continue
            if stats is not None:
                stats.full_json_records_parsed += 1
            if not isinstance(event, dict):
                _note_skipped(stats, "grok", path, line_number, "non-object update row")
                continue
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            update = params.get("update") if isinstance(params.get("update"), dict) else event.get("update")
            update = update if isinstance(update, dict) else event
            kind = update.get("sessionUpdate")
            timestamp, raw_timestamp = _grok_timestamp(event, update, default_tz)
            locator = f"{path}:{line_number}"
            if kind in {"user_message_chunk", "agent_message_chunk", "agent_thought_chunk"}:
                text = text_from_item(update.get("content"))
                if not text:
                    continue
                role = "user" if kind == "user_message_chunk" else "assistant"
                if last_chunk_kind == kind and records and records[-1].role == role:
                    records[-1].text += text
                    records[-1].source_refs = [*(records[-1].source_refs or []), locator]
                    records[-1].source_ref = ",".join(
                        ref for ref in (records[-1].source_refs or []) if "updates.jsonl:" in ref
                    )
                else:
                    refs = [locator] + ([summary_ref] if summary_ref else [])
                    records.append(Record(
                        path=str(path), line=line_number, role=role, text=text, timestamp=timestamp,
                        cwd=cwd, raw_timestamp=raw_timestamp, raw=event, source_format=source_kind,
                        record_id=_string_or_none((update.get("_meta") or {}).get("eventId")) or f"update-{line_number}",
                        conversation_id=session_id, provider="grok", source_ref=locator,
                        source_refs=refs, native_id=_string_or_none((update.get("_meta") or {}).get("eventId")),
                        coverage_notes=["Authoritative Grok updates.jsonl stream; consecutive same-kind chunks are coalesced."],
                    ))
                last_chunk_kind = str(kind)
                continue
            last_chunk_kind = None
            if kind not in {"tool_call", "tool_call_update"}:
                continue
            call_id = _string_or_none(update.get("toolCallId")) or f"tool-{line_number}"
            output = text_from_item(update.get("content") or update.get("rawOutput"))
            text = " ".join(part for part in [str(update.get("title") or update.get("kind") or "tool"), output] if part).strip()
            if kind == "tool_call_update" and call_id in tool_by_id:
                record = tool_by_id[call_id]
                if text:
                    record.text = text
                record.source_refs = [*(record.source_refs or []), locator]
                record.source_ref = ",".join(record.source_refs or [])
                continue
            record = Record(
                path=str(path), line=line_number, role="tool", text=text, timestamp=timestamp,
                cwd=cwd, raw_timestamp=raw_timestamp, raw=event, source_format=source_kind,
                record_id=call_id, conversation_id=session_id, provider="grok", source_ref=locator,
                source_refs=[locator] + ([summary_ref] if summary_ref else []), native_id=call_id,
                coverage_notes=[f"Grok tool status: {update.get('status') or 'unknown'}."],
            )
            records.append(record)
            tool_by_id[call_id] = record
    return [record for record in records if record.text.strip()]


def _grok_fallback_records(path: Path, *, source_kind: str, summary: dict[str, Any], default_tz,
                           stats: RawLineScanStats | None, summary_usable: bool,
                           on_raw_line_examined=None) -> list[Record]:
    records: list[Record] = []
    summary_id, summary_cwd = _grok_summary_identity(summary)
    session_id = summary_id or path.parent.name
    cwd = summary_cwd
    summary_ref = f"{path.parent / 'summary.json'}#info" if summary_usable else None
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return records
    with handle:
        for line_number, raw in enumerate(handle, 1):
            if stats is not None:
                stats.raw_lines_examined += 1
            if on_raw_line_examined is not None:
                on_raw_line_examined(path, line_number)
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                _note_malformed(stats, "grok", path, line_number, "malformed fallback JSONL row")
                continue
            if stats is not None:
                stats.full_json_records_parsed += 1
            if not isinstance(message, dict):
                continue
            role = str(message.get("type") or "")
            if role == "user" and (message.get("synthetic_reason") is not None or message.get("prompt_index") is None):
                _note_ignored(stats)
                continue
            if role not in {"user", "assistant"}:
                continue
            text = text_from_item(message.get("content"))
            if role == "user":
                tagged = _tagged_blocks(text, "user_query")
                text = "\n\n".join(tagged) if tagged else text.strip()
            if not text.strip():
                continue
            locator = f"{path}:{line_number}"
            records.append(Record(
                path=str(path), line=line_number, role=role, text=text, timestamp=None,
                cwd=cwd, raw=message, source_format=source_kind, record_id=f"fallback-{line_number}",
                conversation_id=session_id, provider="grok", source_ref=locator,
                source_refs=[locator] + ([summary_ref] if summary_ref else []),
                native_id=_string_or_none(message.get("prompt_index")), timestamp_quality="unavailable",
                coverage_notes=["Fallback chat_history.jsonl used because authoritative updates had no message-bearing events."],
            ))
    return records


def _tagged_blocks(content: str, tag: str) -> list[str]:
    results: list[str] = []
    opening, closing = f"<{tag}>", f"</{tag}>"
    rest = content
    while opening in rest:
        _, rest = rest.split(opening, 1)
        if closing not in rest:
            break
        body, rest = rest.split(closing, 1)
        results.append(body.strip())
    return results
