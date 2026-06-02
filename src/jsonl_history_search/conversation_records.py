from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .records import Record, text_from_item


CONVERSATION_RECORD_LABELS: tuple[str, ...] = (
    "human_authored_user_message",
    "injected_environment_context",
    "subagent_notification",
    "assistant_or_agent_message",
    "tool_action_output",
    "turn_control_or_status",
    "compacted_history_summary",
    "unknown",
)

HUMAN_AUTHORED_USER_MESSAGE = "human_authored_user_message"
INJECTED_ENVIRONMENT_CONTEXT = "injected_environment_context"
SUBAGENT_NOTIFICATION = "subagent_notification"
ASSISTANT_OR_AGENT_MESSAGE = "assistant_or_agent_message"
TOOL_ACTION_OUTPUT = "tool_action_output"
TURN_CONTROL_OR_STATUS = "turn_control_or_status"
COMPACTED_HISTORY_SUMMARY = "compacted_history_summary"
UNKNOWN = "unknown"

TOOL_TYPES = {
    "function_call",
    "function_call_output",
    "tool",
    "tool_call",
    "tool_call_output",
    "tool_result",
    "tool_output",
    "local_command_output",
    "command_output",
}
TURN_STATUS_TYPES = {
    "turn_context",
    "session_meta",
    "status",
    "turn_status",
    "turn_control",
    "interrupt",
    "interruption",
    "rate_limit",
}
ASSISTANT_TYPES = {
    "assistant",
    "assistant_message",
    "agent_message",
    "assistant_response",
}
USER_MESSAGE_PAYLOAD_TYPES = {"message", "user_message"}
TEXT_CONTENT_TYPES = {"input_text", "text", "message_text"}

INJECTED_INITIAL_TAGS = {
    "environment_context",
    "skill",
    "system-reminder",
    "system_reminder",
    "local-command-stdout",
    "local_command_stdout",
}
ASSISTANT_INITIAL_TAGS = {
    "assistant",
    "assistant_message",
    "agent",
    "agent_message",
    "coordinator_agent",
    "worker_agent",
}
TURN_STATUS_INITIAL_TAGS = {
    "turn_context",
    "turn-control",
    "turn_control",
    "status",
    "progress",
}

INITIAL_TAG_RE = re.compile(r"^\s*<([a-zA-Z0-9_-]+)")
ENVIRONMENT_MARKER_RE = re.compile(
    r"<environment_context>|</environment_context>|<cwd>|<shell>|<current_date>|<timezone>|<filesystem>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversationRecordClassification:
    label: str
    confidence: str
    reasons: tuple[str, ...]
    extracted_text: str | None = None

    @property
    def is_human_authored(self) -> bool:
        return self.label == HUMAN_AUTHORED_USER_MESSAGE and self.confidence == "high"


@dataclass(frozen=True)
class HumanAskExtraction:
    ask_text: str | None
    classification: ConversationRecordClassification

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.classification.reasons


def classify_conversation_record(record: Mapping[str, Any] | Record | Any) -> ConversationRecordClassification:
    """Classify a raw conversation JSONL record by structure, not flattened role."""

    raw = _raw_mapping(record)
    if raw is None:
        return _classification(UNKNOWN, "low", ["record is not a JSON object"])

    payload = raw.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    message = raw.get("message")
    message_map = message if isinstance(message, Mapping) else {}
    top_type = _normalized(raw.get("type"))
    top_role = _normalized(raw.get("role"))
    payload_type = _normalized(payload_map.get("type"))
    payload_role = _normalized(payload_map.get("role"))
    message_role = _normalized(message_map.get("role"))
    text = _record_text(raw)
    initial_tag = _initial_tag(text)

    if top_type == "compacted" or "replacement_history" in payload_map:
        return _classification(
            COMPACTED_HISTORY_SUMMARY,
            "high",
            ["record is a compacted history summary"],
            text,
        )

    type_markers = {top_type, payload_type, _normalized(message_map.get("type"))} - {""}
    role_markers = {top_role, payload_role, message_role} - {""}

    if type_markers & TOOL_TYPES or role_markers & {"tool", "function"}:
        return _classification(TOOL_ACTION_OUTPUT, "high", ["tool/function structural marker"], text)

    if type_markers & TURN_STATUS_TYPES or initial_tag in TURN_STATUS_INITIAL_TAGS:
        return _classification(TURN_CONTROL_OR_STATUS, "high", ["turn/status structural marker"], text)

    if payload_type == "subagent_notification" or top_type == "subagent_notification" or initial_tag == "subagent_notification":
        return _classification(SUBAGENT_NOTIFICATION, "high", ["subagent notification structural marker"], text)

    if _is_injected_context(text, initial_tag, type_markers):
        return _classification(INJECTED_ENVIRONMENT_CONTEXT, "high", ["injected environment/context marker"], text)

    if type_markers & ASSISTANT_TYPES or role_markers & {"assistant", "agent"} or initial_tag in ASSISTANT_INITIAL_TAGS:
        return _classification(ASSISTANT_OR_AGENT_MESSAGE, "high", ["assistant/agent structural marker"], text)

    if _is_supported_human_user_payload(raw, payload_map, message_map, text):
        return _classification(
            HUMAN_AUTHORED_USER_MESSAGE,
            "high",
            ["supported user-message payload with no non-human structural marker"],
            text,
        )

    if top_role == "user" or payload_role == "user" or message_role == "user":
        return _classification(
            UNKNOWN,
            "low",
            ["user role marker without supported human-authored payload shape"],
            text,
        )

    return _classification(UNKNOWN, "low", ["no recognized conversation record shape"], text)


def extract_human_ask(record: Mapping[str, Any] | Record | Any) -> HumanAskExtraction:
    classification = classify_conversation_record(record)
    if classification.is_human_authored and classification.extracted_text:
        return HumanAskExtraction(ask_text=classification.extracted_text, classification=classification)
    reasons = tuple(classification.reasons + ("not a high-confidence human-authored ask",))
    rejected = ConversationRecordClassification(
        label=classification.label,
        confidence=classification.confidence,
        reasons=reasons,
        extracted_text=None,
    )
    return HumanAskExtraction(ask_text=None, classification=rejected)


def _raw_mapping(record: Mapping[str, Any] | Record | Any) -> Mapping[str, Any] | None:
    if isinstance(record, Record):
        return record.raw if isinstance(record.raw, Mapping) else None
    if isinstance(record, Mapping):
        return record
    return None


def _classification(
    label: str,
    confidence: str,
    reasons: list[str],
    text: str | None = None,
) -> ConversationRecordClassification:
    extracted = _clean_text(text) if text else None
    return ConversationRecordClassification(
        label=label,
        confidence=confidence,
        reasons=tuple(reasons),
        extracted_text=extracted,
    )


def _record_text(raw: Mapping[str, Any]) -> str:
    payload = raw.get("payload")
    if isinstance(payload, Mapping):
        if "content" in payload:
            text = _content_text(payload.get("content"))
            if text:
                return text
        if "message" in payload:
            text = text_from_item(payload.get("message"))
            if text:
                return text
        if "output" in payload:
            text = text_from_item(payload.get("output"))
            if text:
                return text

    message = raw.get("message")
    if isinstance(message, Mapping):
        text = _content_text(message.get("content"))
        if text:
            return text
    elif message is not None:
        text = text_from_item(message)
        if text:
            return text

    return _content_text(raw.get("content") or raw.get("text") or raw.get("prompt"))


def _content_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item) for item in value)))
    if isinstance(value, Mapping):
        for key in ("text", "content", "message", "prompt", "output"):
            if key in value:
                text = _content_text(value[key])
                if text:
                    return text
        return ""
    return text_from_item(value)


def _is_supported_human_user_payload(
    raw: Mapping[str, Any],
    payload: Mapping[str, Any],
    message: Mapping[str, Any],
    text: str,
) -> bool:
    if not _clean_text(text):
        return False

    top_type = _normalized(raw.get("type"))
    payload_type = _normalized(payload.get("type"))
    payload_role = _normalized(payload.get("role"))
    message_role = _normalized(message.get("role"))

    if top_type in {"response_item", "event_msg"} and payload_type in USER_MESSAGE_PAYLOAD_TYPES:
        if payload_type == "message":
            content = payload.get("content")
            if payload_role != "user" or not _content_list_is_plain_text(content):
                return False
        if payload_type == "user_message" and "message" not in payload:
            return False
        return True

    if message_role == "user" and _content_list_is_plain_text(message.get("content")):
        return True

    return False


def _content_list_is_plain_text(value: Any) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, Mapping):
            return False
        item_type = _normalized(item.get("type"))
        if item_type and item_type not in TEXT_CONTENT_TYPES:
            return False
        if not _clean_text(text_from_item(item)):
            return False
    return True


def _is_injected_context(text: str, initial_tag: str, type_markers: set[str]) -> bool:
    if initial_tag in INJECTED_INITIAL_TAGS:
        return True
    if ENVIRONMENT_MARKER_RE.search(text):
        return True
    return bool(type_markers & {"environment_context", "system_context", "injected_context", "skill_context"})


def _initial_tag(text: str) -> str:
    match = INITIAL_TAG_RE.match(text or "")
    return _normalized(match.group(1)) if match else ""


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _clean_text(text: str | None) -> str:
    return "\n".join(line.rstrip() for line in str(text or "").strip().splitlines()).strip()
