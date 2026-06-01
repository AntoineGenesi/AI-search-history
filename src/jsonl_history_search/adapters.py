from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from .records import Record, parse_record, text_from_item
from .timeutil import parse_datetime


class AdapterReadError(OSError):
    pass


class RecordAdapter(Protocol):
    name: str
    line_oriented: bool
    supports_append_only: bool

    def can_handle(self, path: Path, source_kind: str) -> bool:
        ...

    def iter_records(self, path: Path, *, source_kind: str, default_tz) -> Iterable[Record]:
        ...


class JsonlAdapter:
    name = "jsonl"
    line_oriented = True
    supports_append_only = True

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return path.suffix == ".jsonl"

    def iter_records(self, path: Path, *, source_kind: str, default_tz) -> Iterable[Record]:
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            raise AdapterReadError(f"Could not open {path}: {exc}") from exc
        with handle:
            for line_number, line in enumerate(handle, 1):
                record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
                record.source_format = source_kind or self.name
                yield record


class GenericMessageArrayJsonAdapter:
    name = "generic_message_array_json"
    line_oriented = False
    supports_append_only = False

    def can_handle(self, path: Path, source_kind: str) -> bool:
        return path.suffix == ".json"

    def iter_records(self, path: Path, *, source_kind: str, default_tz) -> Iterable[Record]:
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


ADAPTERS: tuple[RecordAdapter, ...] = (
    JsonlAdapter(),
    GenericMessageArrayJsonAdapter(),
)


def adapter_for_file(path: Path, source_kind: str) -> RecordAdapter:
    for adapter in ADAPTERS:
        if adapter.can_handle(path, source_kind):
            return adapter
    return JsonlAdapter()


def iter_records_for_file(path: Path, source_kind: str, default_tz) -> Iterable[Record]:
    yield from adapter_for_file(path, source_kind).iter_records(path, source_kind=source_kind, default_tz=default_tz)


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _lossy_conversions(message: dict[str, Any]) -> list[str]:
    lossy: list[str] = []
    content = message.get("content")
    if not isinstance(content, str) and content is not None:
        lossy.append("content normalized with text_from_item")
    return lossy
