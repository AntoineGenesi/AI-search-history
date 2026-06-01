from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .timeutil import parse_datetime


@dataclass
class Record:
    path: str
    line: int
    role: str
    text: str
    timestamp: datetime | None
    cwd: str | None = None
    raw_timestamp: str | None = None
    raw: dict[str, Any] | None = None
    source_format: str | None = None
    record_id: str | None = None
    parent_id: str | None = None
    conversation_id: str | None = None
    unsupported_fields: list[str] | None = None
    lossy_conversions: list[str] | None = None


def text_from_item(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text_from_item(item) for item in value)))
    if isinstance(value, dict):
        for key in ("text", "content", "message", "prompt", "output"):
            if key in value:
                text = text_from_item(value[key])
                if text:
                    return text
    return ""


def compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def parse_record(raw: str, *, path: Path, line: int, default_tz) -> Record:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Record(path=str(path), line=line, role="", text=raw.strip(), timestamp=None)

    role = data.get("role") or data.get("type") or ""
    raw_timestamp = data.get("timestamp") or data.get("created_at") or data.get("time")
    text = ""
    cwd = data.get("cwd") or data.get("project") or data.get("project_cwd")

    payload = data.get("payload")
    if isinstance(payload, dict):
        role = payload.get("type") or payload.get("role") or role
        raw_timestamp = payload.get("timestamp") or raw_timestamp
        cwd = payload.get("cwd") or payload.get("project") or cwd
        payload_type = payload.get("type")
        if payload_type == "function_call":
            text = f"{payload.get('name') or 'function_call'} {compact_json(payload.get('arguments'))}"
        elif payload_type == "function_call_output":
            text = text_from_item(payload.get("output"))
        elif payload_type in {"agent_message", "assistant_message", "user_message"}:
            text = text_from_item(payload.get("message") or payload.get("content"))
        elif payload_type in {"turn_context", "session_meta"}:
            text = compact_json(payload)

    message = data.get("message")
    if isinstance(message, dict):
        role = message.get("role") or role
        raw_timestamp = message.get("timestamp") or raw_timestamp
        text = text_from_item(message.get("content")) or text
    elif message is not None and not text:
        text = text_from_item(message)

    if not text:
        text = text_from_item(data.get("content") or data.get("text") or data.get("prompt"))
    if not text and payload is not None:
        text = compact_json(payload)
    if not cwd and isinstance(data.get("session"), dict):
        cwd = data["session"].get("cwd") or data["session"].get("project")

    return Record(
        path=str(path),
        line=line,
        role=str(role or ""),
        text=text,
        timestamp=parse_datetime(raw_timestamp, default_tz),
        cwd=str(cwd) if cwd else None,
        raw_timestamp=str(raw_timestamp) if raw_timestamp else None,
        raw=data,
    )
