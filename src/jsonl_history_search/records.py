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
    provider: str | None = None
    source_ref: str | None = None
    source_refs: list[str] | None = None
    native_id: str | None = None
    timestamp_quality: str = "record"
    coverage_notes: list[str] | None = None
    import_provenance: dict[str, Any] | None = None


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


def native_message_payload(raw):
    """Read-only view of Codex message transports; keep original raw evidence.

    item_completed wraps native UserMessage/AgentMessage events. Matching
    thread/turn identity is available to consumers; equal text is not identity.
    """
    payload = raw.get('payload', raw)
    if not isinstance(payload, dict):
        return payload
    if raw.get('type') == 'response_item':
        metadata = payload.get('internal_chat_message_metadata_passthrough')
        if isinstance(metadata, dict) and metadata.get('turn_id'):
            return {**payload, 'turn_id': metadata['turn_id']}
    item = payload.get('item')
    if raw.get('type') == 'event_msg' and payload.get('type') == 'item_completed' and isinstance(item, dict):
        if item.get('type') in {'UserMessage', 'AgentMessage'}:
            return {**payload, 'type': 'message',
                'role': 'user' if item['type'] == 'UserMessage' else 'assistant',
                'content': item.get('content') or [{'type': 'text', 'text': item.get('text', '')}],
                'message_id': item.get('id')}
    return payload


def parse_record(raw: str, *, path: Path, line: int, default_tz) -> Record:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Record(path=str(path), line=line, role="", text=raw.strip(), timestamp=None)

    role = data.get("role") or data.get("type") or ""
    raw_timestamp = data.get("timestamp") or data.get("created_at") or data.get("time")
    text = ""
    cwd = data.get("cwd") or data.get("project") or data.get("project_cwd")

    payload = native_message_payload(data)
    if isinstance(payload, dict):
        role = payload.get("type") or payload.get("role") or role
        raw_timestamp = payload.get("timestamp") or raw_timestamp
        cwd = payload.get("cwd") or payload.get("project") or cwd
        payload_type = payload.get("type")
        # Native event transports use both snake_case and enum spelling.
        payload_type = {"UserMessage": "user_message", "AgentMessage": "agent_message"}.get(payload_type, payload_type)
        if payload_type == "message" and payload.get("role"):
            role = payload.get("role")
        if payload_type == "function_call":
            text = f"{payload.get('name') or 'function_call'} {compact_json(payload.get('arguments'))}"
        elif payload_type == "function_call_output":
            text = text_from_item(payload.get("output"))
        elif payload_type == "message":
            text = text_from_item(payload.get("content") or payload.get("message"))
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
