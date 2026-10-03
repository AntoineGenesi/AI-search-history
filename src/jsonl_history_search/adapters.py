from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .records import Record, parse_record, text_from_item
from .timeutil import parse_datetime
from .operation_budget import BudgetExceeded, open_source_text, read_source_text


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
        budget=None, budget_phase="retrieval",
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
        budget=None, budget_phase="retrieval",
    ) -> Iterable[Record]:
        try:
            handle = open_source_text(path, budget=budget, phase=budget_phase)
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
                if record.raw is None and raw_line_stats is not None:
                    _note_malformed(raw_line_stats, "jsonl", path, line_number, "malformed JSONL row")
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
        budget=None, budget_phase="retrieval",
    ) -> Iterable[Record]:
        try:
            with open_source_text(path, budget=budget, phase=budget_phase) as handle:
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
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        try:
            data = json.loads(read_source_text(path, budget=budget, phase=budget_phase))
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
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        session: dict[str, Any] = {}
        messages: list[tuple[dict[str, Any], str, int]] = []
        try:
            handle = open_source_text(path, budget=budget, phase=budget_phase)
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
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        session_dir = path.parent
        summary_path = session_dir / "summary.json"
        summary: dict[str, Any] = {}
        summary_usable = False
        if summary_path.is_file():
            try:
                parsed = json.loads(read_source_text(summary_path, budget=budget, phase=budget_phase))
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
            budget=budget, budget_phase=budget_phase,
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
                budget=budget, budget_phase=budget_phase,
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
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        try:
            lines = read_source_text(path, budget=budget, phase=budget_phase).lstrip("\ufeff").splitlines()
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


class CursorAgentJsonlAdapter:
    name = "cursor_agent_jsonl"
    line_oriented = True
    supports_append_only = True

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("cursor_") and path.suffix.lower() == ".jsonl"

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        try:
            handle = open_source_text(path, budget=budget, phase=budget_phase)
        except OSError as exc:
            _note_malformed(raw_line_stats, "cursor", path, None, f"unreadable Cursor Agent JSONL: {exc}")
            return
        with handle:
            for line_number, raw_line in enumerate(handle, 1):
                if raw_line_stats is not None:
                    raw_line_stats.raw_lines_examined += 1
                if on_raw_line_examined is not None:
                    on_raw_line_examined(path, line_number)
                if not raw_line.strip():
                    continue
                try:
                    data = json.loads(raw_line.lstrip("\ufeff"))
                except json.JSONDecodeError:
                    _note_malformed(raw_line_stats, "cursor", path, line_number, "malformed Cursor Agent JSONL row")
                    continue
                if not isinstance(data, dict):
                    _note_skipped(raw_line_stats, "cursor", path, line_number, "non-object Cursor Agent row")
                    continue
                if raw_line_stats is not None:
                    raw_line_stats.full_json_records_parsed += 1
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                text = _mixed_text(message.get("content") or message.get("text") or message.get("message"))
                if not text:
                    _note_ignored(raw_line_stats)
                    continue
                role = _normalized_role(message.get("role") or data.get("role") or data.get("type"))
                raw_timestamp = message.get("timestamp") or data.get("timestamp") or data.get("created_at")
                native_id = _string_or_none(
                    message.get("id") or message.get("messageId") or message.get("bubbleId") or data.get("id")
                ) or f"line-{line_number}"
                session_id = _string_or_none(
                    data.get("sessionId") or data.get("session_id") or data.get("conversationId")
                ) or path.stem
                locator = f"{path}:{line_number}"
                yield Record(
                    path=str(path), line=line_number, role=role, text=text,
                    timestamp=_provider_datetime(raw_timestamp, default_tz),
                    cwd=_string_or_none(data.get("cwd") or data.get("workspace") or data.get("projectPath")),
                    raw_timestamp=str(raw_timestamp) if raw_timestamp is not None else None,
                    raw=data, source_format=source_kind, record_id=native_id,
                    conversation_id=session_id, provider="cursor", source_ref=locator,
                    source_refs=[locator], native_id=native_id,
                    coverage_notes=["Cursor Agent JSONL support is based on the documented local shape; retain the raw row when attributing actions."],
                )


class CursorSqliteAdapter:
    name = "cursor_composer_sqlite"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("cursor_") and path.suffix.lower() in {".vscdb", ".db", ".sqlite", ".sqlite3"}

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        _note_immutable_wal_omission(raw_line_stats, "cursor", path)
        try:
            connection = _open_immutable_query_only(path, budget=budget, phase=budget_phase)
        except (OSError, sqlite3.Error) as exc:
            _note_malformed(raw_line_stats, "cursor", path, None, f"unreadable or locked Cursor SQLite store: {exc}")
            return
        try:
            tables = _sqlite_tables(connection)
            supported = [table for table in ("cursorDiskKV", "ItemTable") if table in tables]
            if not supported:
                _note_skipped(raw_line_stats, "cursor", path, None, "unsupported Cursor SQLite schema: no cursorDiskKV or ItemTable")
                return
            emitted = False
            for table in supported:
                columns = _sqlite_columns(connection, table)
                if not {"key", "value"}.issubset(columns):
                    _note_skipped(raw_line_stats, "cursor", path, None, f"unsupported Cursor {table} columns")
                    continue
                rows = connection.execute(
                    f'SELECT key, value FROM "{table}" '
                    "WHERE key LIKE 'composerData:%' OR key LIKE 'bubbleId:%' ORDER BY key LIMIT ?",
                    (_SQLITE_ROW_LIMIT + 1,),
                ).fetchall()
                if len(rows) > _SQLITE_ROW_LIMIT:
                    raw_line_stats.note_partial(provider="cursor", path=path, line=None, reason=f"Cursor {table} row cap reached") if raw_line_stats else None
                    rows = rows[:_SQLITE_ROW_LIMIT]
                decoded: dict[str, dict[str, Any]] = {}
                for row in rows:
                    key = str(row["key"])
                    try:
                        value = json.loads(row["value"])
                    except (TypeError, json.JSONDecodeError):
                        _note_malformed(raw_line_stats, "cursor", path, None, f"malformed JSON in {table}:{key}")
                        continue
                    if isinstance(value, dict):
                        decoded[key] = value
                    else:
                        _note_skipped(raw_line_stats, "cursor", path, None, f"non-object JSON in {table}:{key}")
                for key, composer in decoded.items():
                    if not key.startswith("composerData:"):
                        continue
                    composer_id = key.split(":", 1)[1]
                    for ordinal, (message, pointer, supporting_key) in enumerate(
                        _cursor_messages(composer, composer_id=composer_id, decoded=decoded), 1
                    ):
                        text = _mixed_text(
                            message.get("text") or message.get("rawText") or message.get("content") or message.get("message")
                        )
                        if not text:
                            continue
                        raw_timestamp = message.get("timestamp") or composer.get("createdAt")
                        message_id = _string_or_none(message.get("bubbleId") or message.get("id")) or f"message-{ordinal}"
                        base_ref = f"{path}#{table}:{key}"
                        source_refs = [f"{base_ref}#{pointer}"]
                        if supporting_key:
                            source_refs.append(f"{path}#{table}:{supporting_key}")
                        if raw_line_stats is not None:
                            raw_line_stats.full_json_records_parsed += 1
                        emitted = True
                        yield Record(
                            path=str(path), line=ordinal, role=_normalized_role(message.get("role") or message.get("type")),
                            text=text, timestamp=_provider_datetime(raw_timestamp, default_tz),
                            cwd=_cursor_workspace(message) or _cursor_workspace(composer),
                            raw_timestamp=str(raw_timestamp) if raw_timestamp is not None else None,
                            raw={"composer": composer, "message": message}, source_format=source_kind,
                            record_id=message_id, conversation_id=composer_id, provider="cursor",
                            source_ref=source_refs[0],
                            source_refs=source_refs, native_id=message_id,
                        )
            if not emitted:
                _note_skipped(raw_line_stats, "cursor", path, None, "Cursor SQLite store contained no readable Composer messages")
        except sqlite3.Error as exc:
            _note_malformed(raw_line_stats, "cursor", path, None, f"Cursor SQLite query failed: {exc}")
        finally:
            connection.close()


class OpenCodeSqliteAdapter:
    name = "opencode_sqlite"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("opencode_") and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        _note_immutable_wal_omission(raw_line_stats, "opencode", path)
        try:
            connection = _open_immutable_query_only(path, budget=budget, phase=budget_phase)
        except (OSError, sqlite3.Error) as exc:
            _note_malformed(raw_line_stats, "opencode", path, None, f"unreadable or locked OpenCode SQLite store: {exc}")
            return
        try:
            tables = _sqlite_tables(connection)
            emitted = False
            v2_sessions: set[str] = set()
            if "session_message" in tables:
                v2_records, v2_sessions = _opencode_v2_records(
                    connection, path=path, source_kind=source_kind, default_tz=default_tz,
                    tables=tables, stats=raw_line_stats,
                )
                for record in v2_records:
                    emitted = True
                    yield record
            if {"session", "message", "part"}.issubset(tables):
                for record in _opencode_v1_records(
                    connection, path=path, source_kind=source_kind, default_tz=default_tz,
                    stats=raw_line_stats, excluded_sessions=v2_sessions,
                ):
                    emitted = True
                    yield record
            if "session_message" not in tables and not {"session", "message", "part"}.issubset(tables):
                _note_skipped(raw_line_stats, "opencode", path, None, "unsupported OpenCode SQLite schema")
            elif not emitted:
                _note_skipped(raw_line_stats, "opencode", path, None, "OpenCode SQLite store contained no readable messages")
        except sqlite3.Error as exc:
            _note_malformed(raw_line_stats, "opencode", path, None, f"OpenCode SQLite query failed: {exc}")
        finally:
            connection.close()


class OpenCodeLegacyJsonAdapter:
    name = "opencode_legacy_json"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return source_kind.startswith("opencode_") and path.suffix.lower() == ".json"

    def iter_records(self, path: Path, *, source_kind: str, default_tz, raw_line_filter=None,
                     raw_line_stats=None, on_raw_line_examined=None, on_raw_line_skipped=None,
                     budget=None, budget_phase="retrieval") -> Iterable[Record]:
        storage_root = _legacy_storage_root(path)
        if storage_root is None or path.parent.parent.name != "message":
            return
        try:
            message = json.loads(read_source_text(path, budget=budget, phase=budget_phase))
        except (OSError, json.JSONDecodeError) as exc:
            _note_malformed(raw_line_stats, "opencode", path, None, f"malformed or unreadable legacy message JSON: {exc}")
            return
        if not isinstance(message, dict):
            _note_skipped(raw_line_stats, "opencode", path, None, "legacy message JSON is not an object")
            return
        message_id = _string_or_none(message.get("id")) or path.stem
        session_id = _string_or_none(message.get("sessionID") or message.get("session_id")) or path.parent.name
        part_dir = storage_root / "part" / message_id
        part_payloads: list[tuple[Path, dict[str, Any]]] = []
        if part_dir.is_dir():
            for part_path in sorted(part_dir.glob("*.json")):
                try:
                    part = json.loads(read_source_text(part_path, budget=budget, phase=budget_phase))
                except (OSError, json.JSONDecodeError) as exc:
                    _note_malformed(raw_line_stats, "opencode", part_path, None, f"malformed or unreadable legacy part JSON: {exc}")
                    continue
                if isinstance(part, dict):
                    part_payloads.append((part_path, part))
        else:
            _note_skipped(raw_line_stats, "opencode", path, None, f"legacy message {message_id} has no part directory")
        text = _opencode_parts_text([payload for _part_path, payload in part_payloads]) or _mixed_text(message.get("content"))
        if not text:
            return
        raw_timestamp = _nested_time(message, "created")
        message_ref = f"{path}#message:{message_id}"
        part_refs = [f"{part_path}#part:{_string_or_none(payload.get('id')) or part_path.stem}" for part_path, payload in part_payloads]
        session_ref = f"{storage_root}#session:{session_id}"
        if raw_line_stats is not None:
            raw_line_stats.full_json_records_parsed += 1
        yield Record(
            path=str(path), line=1, role=_normalized_role(message.get("role")), text=text,
            timestamp=_provider_datetime(raw_timestamp, default_tz),
            raw_timestamp=str(raw_timestamp) if raw_timestamp is not None else None,
            raw={"message": message, "parts": [payload for _path, payload in part_payloads]},
            source_format=source_kind, record_id=message_id, conversation_id=session_id,
            provider="opencode", source_ref=message_ref,
            source_refs=[message_ref, session_ref, *part_refs], native_id=message_id,
        )


ADAPTERS: tuple[RecordAdapter, ...] = (
    CursorAgentJsonlAdapter(),
    CursorSqliteAdapter(),
    OpenCodeSqliteAdapter(),
    OpenCodeLegacyJsonAdapter(),
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
    budget=None, budget_phase="retrieval",
) -> Iterable[Record]:
    records = adapter_for_file(path, source_kind).iter_records(
        path,
        source_kind=source_kind,
        default_tz=default_tz,
        raw_line_filter=raw_line_filter,
        raw_line_stats=raw_line_stats,
        on_raw_line_examined=on_raw_line_examined,
        on_raw_line_skipped=on_raw_line_skipped,
        budget=budget, budget_phase=budget_phase,
    )
    for record in records:
        if budget is not None:
            budget.check(budget_phase)
        if record.provider is None:
            record.provider = _provider_from_source_kind(source_kind)
        if record.source_ref is None:
            record.source_ref = f"{record.path}:{record.line}"
        if record.source_refs is None:
            record.source_refs = [record.source_ref]
        if record.native_id is None:
            record.native_id = record.record_id
        yield record
    if budget is not None and budget.exhausted_reasons:
        budget.stop(budget.exhausted_reasons[0])


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _provider_from_source_kind(source_kind: str) -> str:
    for provider in ("codex", "claude", "gemini", "grok", "aider", "cursor", "opencode"):
        if provider in source_kind:
            return provider
    return "unknown"


_SQLITE_ROW_LIMIT = 5000


def _open_immutable_query_only(path: Path, *, budget=None, phase="retrieval") -> sqlite3.Connection:
    if budget is not None:
        budget.reserve_native(path, phase)
    if not path.is_file():
        raise OSError("database file is missing")
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=0)
    if budget is not None:
        connection.set_progress_handler(lambda: 1 if budget.remaining_seconds <= 0 else 0, 1000)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] == 1:
            return connection
    except sqlite3.Error:
        connection.close()
        raise
    connection.close()
    raise sqlite3.OperationalError("query_only could not be enabled")


def _note_immutable_wal_omission(
    stats: RawLineScanStats | None, provider: str, path: Path
) -> None:
    if stats is None:
        return
    wal_path = Path(f"{path}-wal")
    try:
        wal_size = wal_path.stat().st_size
    except FileNotFoundError:
        return
    except OSError as exc:
        stats.note_partial(
            provider=provider,
            path=path,
            line=None,
            reason=f"could not inspect SQLite WAL sidecar before immutable read: {exc}",
        )
        return
    if wal_size > 0:
        stats.note_partial(
            provider=provider,
            path=path,
            line=None,
            reason=(
                f"non-empty SQLite WAL sidecar ({wal_size} bytes) was omitted by immutable read; "
                "committed uncheckpointed rows may be absent"
            ),
        )


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if table not in {"cursorDiskKV", "ItemTable", "session", "session_v2", "session_message", "message", "part"}:
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _cursor_messages(
    composer: dict[str, Any], *, composer_id: str, decoded: dict[str, dict[str, Any]]
) -> list[tuple[dict[str, Any], str, str | None]]:
    messages: list[tuple[dict[str, Any], str, str | None]] = []
    headers = composer.get("fullConversationHeadersOnly")
    if isinstance(headers, list):
        for index, header in enumerate(headers):
            if not isinstance(header, dict):
                continue
            bubble_id = _string_or_none(header.get("bubbleId") or header.get("id"))
            supporting_key = f"bubbleId:{composer_id}:{bubble_id}" if bubble_id else None
            bubble = decoded.get(supporting_key or "")
            if bubble is not None:
                messages.append((bubble, f"/fullConversationHeadersOnly/{index}", supporting_key))
    tabs = composer.get("tabs")
    if isinstance(tabs, list):
        for tab_index, tab in enumerate(tabs):
            bubbles = tab.get("bubbles") if isinstance(tab, dict) else None
            if not isinstance(bubbles, list):
                continue
            for bubble_index, bubble in enumerate(bubbles):
                if isinstance(bubble, dict):
                    messages.append((bubble, f"/tabs/{tab_index}/bubbles/{bubble_index}", None))
    if not messages and _mixed_text(
        composer.get("text") or composer.get("rawText") or composer.get("content") or composer.get("message")
    ):
        messages.append((composer, "/", None))
    return messages


def _normalized_role(value: Any) -> str:
    if value in {1, "1", "human", "user", "prompt"}:
        return "user"
    if value in {2, "2", "ai", "assistant", "model", "tool", "tool_result"}:
        return "assistant"
    return str(value or "assistant")


def _provider_datetime(value: Any, default_tz):
    if isinstance(value, (int, float)) and value > 10_000_000_000:
        value = value / 1000
    return parse_datetime(value, default_tz)


def _mixed_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(item for item in (_mixed_text(entry) for entry in value) if item)
    if isinstance(value, dict):
        pieces: list[str] = []
        for key in ("text", "rawText", "content", "message", "prompt", "output", "result", "arguments", "input", "state", "reasoning"):
            if key in value:
                piece = _mixed_text(value[key])
                if piece and piece not in pieces:
                    pieces.append(piece)
        return "\n".join(pieces)
    return str(value)


def _cursor_workspace(value: dict[str, Any]) -> str | None:
    direct = value.get("workspaceProjectDir") or value.get("cwd") or value.get("workspace")
    if direct:
        return str(direct)
    uris = value.get("workspaceUris")
    if isinstance(uris, list) and uris:
        return str(uris[0])
    return None


def _opencode_v2_records(
    connection: sqlite3.Connection, *, path: Path, source_kind: str, default_tz,
    tables: set[str], stats: RawLineScanStats | None,
) -> tuple[list[Record], set[str]]:
    required = {"id", "session_id", "type", "seq", "data"}
    if not required.issubset(_sqlite_columns(connection, "session_message")):
        _note_skipped(stats, "opencode", path, None, "unsupported OpenCode v2 session_message columns")
        return [], set()
    session_table = "session_v2" if "session_v2" in tables else "session" if "session" in tables else None
    if session_table is None:
        _note_skipped(stats, "opencode", path, None, "OpenCode v2 session_message has no session_v2 or session table")
    session_meta = _opencode_session_metadata(connection, session_table) if session_table else {}
    rows = connection.execute(
        "SELECT * FROM session_message ORDER BY session_id, seq LIMIT ?", (_SQLITE_ROW_LIMIT + 1,)
    ).fetchall()
    if len(rows) > _SQLITE_ROW_LIMIT:
        stats.note_partial(provider="opencode", path=path, line=None, reason="OpenCode v2 row cap reached") if stats else None
        rows = rows[:_SQLITE_ROW_LIMIT]
    records: list[Record] = []
    session_ids: set[str] = set()
    for row in rows:
        row_id = str(row["id"])
        session_id = str(row["session_id"])
        session_ids.add(session_id)
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            _note_malformed(stats, "opencode", path, None, f"malformed JSON in session_message:{row_id}")
            continue
        if not isinstance(data, dict):
            _note_skipped(stats, "opencode", path, None, f"non-object JSON in session_message:{row_id}")
            continue
        text = _mixed_text(data)
        role = _normalized_role(data.get("role") or row["type"])
        if not text or role not in {"user", "assistant"}:
            _note_ignored(stats)
            continue
        timestamp_value = row["time_created"] if "time_created" in row.keys() else data.get("timestamp")
        locator = f"{path}#session_message:{row_id}#/data"
        sequence_ref = f"{path}#session:{session_id}:seq:{row['seq']}"
        meta = session_meta.get(session_id, {})
        if stats is not None:
            stats.full_json_records_parsed += 1
        records.append(Record(
            path=str(path), line=int(row["seq"] or 0) + 1, role=role, text=text,
            timestamp=_provider_datetime(timestamp_value, default_tz), cwd=_string_or_none(meta.get("directory")),
            raw_timestamp=str(timestamp_value) if timestamp_value is not None else None,
            raw=data, source_format=source_kind, record_id=row_id, conversation_id=session_id,
            provider="opencode", source_ref=locator, source_refs=[locator, sequence_ref], native_id=row_id,
        ))
    return records, session_ids


def _opencode_v1_records(
    connection: sqlite3.Connection, *, path: Path, source_kind: str, default_tz,
    stats: RawLineScanStats | None, excluded_sessions: set[str],
) -> list[Record]:
    if not {"id", "session_id", "data"}.issubset(_sqlite_columns(connection, "message")):
        _note_skipped(stats, "opencode", path, None, "unsupported OpenCode v1 message columns")
        return []
    if not {"id", "message_id", "data"}.issubset(_sqlite_columns(connection, "part")):
        _note_skipped(stats, "opencode", path, None, "unsupported OpenCode v1 part columns")
        return []
    session_meta = _opencode_session_metadata(connection, "session")
    part_rows = connection.execute("SELECT * FROM part ORDER BY message_id, time_created, id LIMIT ?", (_SQLITE_ROW_LIMIT + 1,)).fetchall()
    if len(part_rows) > _SQLITE_ROW_LIMIT:
        stats.note_partial(provider="opencode", path=path, line=None, reason="OpenCode v1 part row cap reached") if stats else None
        part_rows = part_rows[:_SQLITE_ROW_LIMIT]
    parts_by_message: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for row in part_rows:
        part_id = str(row["id"])
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            _note_malformed(stats, "opencode", path, None, f"malformed JSON in part:{part_id}")
            continue
        if isinstance(data, dict):
            parts_by_message.setdefault(str(row["message_id"]), []).append((part_id, data))
    rows = connection.execute("SELECT * FROM message ORDER BY session_id, time_created, id LIMIT ?", (_SQLITE_ROW_LIMIT + 1,)).fetchall()
    if len(rows) > _SQLITE_ROW_LIMIT:
        stats.note_partial(provider="opencode", path=path, line=None, reason="OpenCode v1 message row cap reached") if stats else None
        rows = rows[:_SQLITE_ROW_LIMIT]
    records: list[Record] = []
    for ordinal, row in enumerate(rows, 1):
        message_id = str(row["id"])
        session_id = str(row["session_id"])
        if session_id in excluded_sessions:
            continue
        try:
            data = json.loads(row["data"])
        except (TypeError, json.JSONDecodeError):
            _note_malformed(stats, "opencode", path, None, f"malformed JSON in message:{message_id}")
            continue
        if not isinstance(data, dict):
            _note_skipped(stats, "opencode", path, None, f"non-object JSON in message:{message_id}")
            continue
        parts = parts_by_message.get(message_id, [])
        text = _opencode_parts_text([payload for _part_id, payload in parts]) or _mixed_text(data)
        if not text:
            _note_skipped(stats, "opencode", path, None, f"OpenCode v1 message {message_id} has no readable parts")
            continue
        timestamp_value = row["time_created"] if "time_created" in row.keys() else data.get("timestamp")
        message_ref = f"{path}#message:{message_id}"
        part_refs = [f"{path}#part:{part_id}" for part_id, _payload in parts]
        session_ref = f"{path}#session:{session_id}"
        meta = session_meta.get(session_id, {})
        if stats is not None:
            stats.full_json_records_parsed += 1
        records.append(Record(
            path=str(path), line=ordinal, role=_normalized_role(data.get("role")), text=text,
            timestamp=_provider_datetime(timestamp_value, default_tz), cwd=_string_or_none(meta.get("directory")),
            raw_timestamp=str(timestamp_value) if timestamp_value is not None else None,
            raw={"message": data, "parts": [payload for _id, payload in parts]},
            source_format=source_kind, record_id=message_id, conversation_id=session_id,
            provider="opencode", source_ref=message_ref,
            source_refs=[message_ref, session_ref, *part_refs], native_id=message_id,
        ))
    return records


def _opencode_session_metadata(connection: sqlite3.Connection, table: str | None) -> dict[str, dict[str, Any]]:
    if table is None or "id" not in _sqlite_columns(connection, table):
        return {}
    rows = connection.execute(f'SELECT * FROM "{table}" LIMIT ?', (_SQLITE_ROW_LIMIT,)).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def _opencode_parts_text(parts: list[dict[str, Any]]) -> str:
    pieces: list[str] = []
    for part in parts:
        text = _mixed_text(part.get("text") or part.get("content"))
        kind = str(part.get("type") or "").lower()
        if kind == "reasoning" and text:
            pieces.append(f"[Reasoning]\n{text}")
        elif kind in {"tool", "tool_result", "tool-invocation"}:
            tool_text = text or _mixed_text(part.get("state") or part.get("output") or part.get("input"))
            if tool_text:
                pieces.append(f"[Tool]\n{tool_text}")
        elif text:
            pieces.append(text)
    return "\n".join(pieces)


def _legacy_storage_root(path: Path) -> Path | None:
    parts = path.parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] in {"session", "message", "part"}:
            return Path(*parts[:index])
    return None


def _nested_time(data: dict[str, Any], key: str) -> Any:
    value = data.get("time")
    return value.get(key) if isinstance(value, dict) else data.get(f"time_{key}")


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
                         on_raw_line_examined=None, budget=None, budget_phase="retrieval") -> list[Record]:
    records: list[Record] = []
    try:
        handle = open_source_text(path, budget=budget, phase=budget_phase)
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
                           on_raw_line_examined=None, budget=None, budget_phase="retrieval") -> list[Record]:
    records: list[Record] = []
    summary_id, summary_cwd = _grok_summary_identity(summary)
    session_id = summary_id or path.parent.name
    cwd = summary_cwd
    summary_ref = f"{path.parent / 'summary.json'}#info" if summary_usable else None
    try:
        handle = open_source_text(path, budget=budget, phase=budget_phase)
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
