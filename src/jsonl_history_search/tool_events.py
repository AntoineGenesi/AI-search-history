from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from .agent_contract import estimate_tokens, reduction_ratio
from .clustering import session_key
from .masking import MaskContext
from .adapters import AdapterReadError, iter_records_for_file
from .records import Record, compact_json, text_from_item
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path


SCHEMA_VERSION = 1
INLINE_EVENT_LIMIT = 80
EXCERPT_LIMIT = 360


@dataclass(frozen=True)
class Target:
    kind: str
    value: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "value": self.value}


@dataclass
class ToolEvent:
    event_id: str
    event_type: str
    event_family: str
    source_ref: dict
    timestamp: str | None
    timestamp_utc: str | None
    role: str
    cwd: str | None
    project: str | None
    tool_name: str | None
    confidence: str
    evidence_basis: list[str]
    uncertainty: list[str] = field(default_factory=list)
    action: str | None = None
    targets: list[Target] = field(default_factory=list)
    command: str | None = None
    arguments: dict | str | None = None
    output_paths: list[str] = field(default_factory=list)
    exit_status: int | str | None = None
    sequence_id: str | None = None
    sequence_index: int | None = None
    related_event_ids: list[str] = field(default_factory=list)
    payload_excerpt: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "event_id": self.event_id,
            "schema_version": SCHEMA_VERSION,
            "event_type": self.event_type,
            "event_family": self.event_family,
            "source_ref": self.source_ref,
            "timestamp": self.timestamp,
            "timestamp_utc": self.timestamp_utc,
            "role": self.role,
            "cwd": self.cwd,
            "project": self.project,
            "tool_name": self.tool_name,
            "confidence": self.confidence,
            "evidence_basis": sorted(set(self.evidence_basis)),
            "uncertainty": sorted(set(self.uncertainty)),
        }
        optional = {
            "action": self.action,
            "targets": [target.to_dict() for target in self.targets],
            "command": self.command,
            "arguments": self.arguments,
            "output_paths": self.output_paths,
            "exit_status": self.exit_status,
            "sequence_id": self.sequence_id,
            "sequence_index": self.sequence_index,
            "related_event_ids": self.related_event_ids,
            "payload_excerpt": self.payload_excerpt,
        }
        for key, value in optional.items():
            if value not in (None, [], {}):
                payload[key] = value
        return payload


def build_tool_events_payload(
    *,
    source_plan: dict,
    plan: SourcePlan,
    default_tz,
    mask_context: MaskContext | None = None,
    event_types: list[str] | None = None,
    skill: str | None = None,
    path_globs: list[str] | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict:
    scan = scan_tool_events(
        plan,
        default_tz=default_tz,
        mask_context=mask_context,
        filters=ToolEventFilters(event_types=event_types or [], skill=skill, path_globs=path_globs or []),
    )
    events = [event.to_dict() for event in scan["events"]]
    excluded_mentions = scan["excluded_mentions"]
    detector_observations = scan.get("detector_observations", empty_detector_observations())
    inline_payload = events[:INLINE_EVENT_LIMIT]
    inline_context_bytes = len(json.dumps(inline_payload + excluded_mentions[:20], ensure_ascii=False).encode("utf-8"))
    raw_baseline_bytes = int(scan["searched_bytes"])
    status = "found" if events else "no_structured_events"
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "command_surface": {
            "structured-tool-events": "Returns normalized structured JSONL/tool payload evidence for tool calls, command runs, file operations, artifacts, tests, reviews, git events, skill signals, and command sequence fingerprints."
        },
        "event_schema": event_schema(),
        "filters": {
            "event_types": event_types or [],
            "skill": skill,
            "path_globs": path_globs or [],
        },
        "events": inline_payload,
        "excluded_mentions": excluded_mentions[:20],
        "ai_handoff_packet": compact_ai_handoff(events, excluded_mentions),
        "context_budget": {
            "payload_bytes": inline_context_bytes,
            "estimated_tokens": estimate_tokens(inline_context_bytes),
            "raw_baseline_bytes": raw_baseline_bytes,
            "estimated_reduction_ratio": reduction_ratio(inline_context_bytes, raw_baseline_bytes),
            "report_recommended": len(events) > INLINE_EVENT_LIMIT,
            "report_written": report_output,
            "format": output_format,
        },
        "token_efficiency": {
            "event_count": len(events),
            "excluded_mention_count": len(excluded_mentions),
            "inline_event_limit": INLINE_EVENT_LIMIT,
            "inline_context_bytes": inline_context_bytes,
            "searched_files": scan["searched_files"],
            "searched_bytes": scan["searched_bytes"],
        },
        "detector_contract": {
            "execution_support": "Executed events require structured tool/function records, parsed tool arguments, command payloads, structured edit payloads, or machine-readable tool output. Prose-only mentions are excluded.",
            "sequence_fingerprints": "Adjacent command_run events in the same session and cwd receive deterministic sha256 fingerprints over normalized command chains.",
            "privacy": "Payload excerpts are bounded and may be masked/redacted; raw private transcripts are never required in fixtures.",
        },
        "detector_observations": detector_observations,
        "detector_schema_gap": detector_schema_gap(events=events, observations=detector_observations, warnings=scan["warnings"]),
        "warnings": sorted(set(scan["warnings"] + source_plan.get("sources", {}).get("warnings", []))),
    }
    payload = {
        "structured_tool_events": report,
        "source_plan": source_plan,
        "warnings": report["warnings"],
        "blind_spots": sorted(set(scan["blind_spots"] + source_plan.get("sources", {}).get("blind_spots", []))),
    }
    if "masking" in scan:
        payload["masking"] = scan["masking"]
    return payload


@dataclass(frozen=True)
class ToolEventFilters:
    event_types: list[str] = field(default_factory=list)
    skill: str | None = None
    path_globs: list[str] = field(default_factory=list)


def scan_tool_events(
    plan: SourcePlan,
    *,
    default_tz,
    mask_context: MaskContext | None = None,
    filters: ToolEventFilters | None = None,
) -> dict:
    started = time.monotonic()
    warnings = list(plan.warnings)
    searched_files = 0
    searched_bytes = 0
    stopped = False
    detected_events: list[ToolEvent] = []
    excluded_mentions: list[dict] = []
    detector_observations = empty_detector_observations()
    event_counter = 0
    filters = filters or ToolEventFilters()

    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = enum_warnings
        searched_files += 1
        searched_bytes += size
        source_kind = source_kind_for_path(path, plan)
        try:
            for record in iter_records_for_file(path, source_kind, default_tz):
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                observe_record_shape(record, detector_observations)
                line_events = detect_events_for_record(record, mask_context=mask_context)
                for event in line_events:
                    event_counter += 1
                    event.event_id = f"e{event_counter:04d}"
                    detected_events.append(event)
                if not line_events:
                    excluded_mentions.extend(excluded_mentions_for_record(record, mask_context=mask_context))
        except AdapterReadError as exc:
            warnings.append(str(exc))
            continue
        if stopped:
            break

    events = [
        event
        for event in add_command_sequences(detected_events, start_index=event_counter + 1, filters=ToolEventFilters())
        if include_event(event, filters)
    ]
    return {
        "events": events,
        "excluded_mentions": excluded_mentions,
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(plan.blind_spots)),
        "detector_observations": detector_observations,
        **mask_payload(mask_context),
    }


def detect_events_for_record(record: Record, *, mask_context: MaskContext | None = None) -> list[ToolEvent]:
    data = record.raw or {}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    events: list[ToolEvent] = []
    tool_name = tool_name_from_payload(payload)
    arguments = arguments_from_payload(payload)
    text = record.text or text_from_item(payload)
    excerpt = bounded_excerpt(text or compact_json(payload), record=record, mask_context=mask_context)
    base = base_event_fields(record, tool_name=tool_name, payload_excerpt=excerpt)

    if is_structured_tool_call(payload):
        events.append(
            ToolEvent(
                event_type="tool_call",
                event_family="tool",
                confidence="high",
                evidence_basis=["structured_function_call"],
                action=action_from_tool(tool_name),
                arguments=safe_arguments(arguments),
                **base,
            )
        )
        command = command_from_call(tool_name, arguments)
        if command:
            events.extend(events_from_command(command, base=base, arguments=safe_arguments(arguments)))
        elif tool_name:
            events.extend(events_from_structured_tool_arguments(tool_name, arguments, base=base))
    elif is_structured_tool_output(payload):
        output_text = output_text_from_payload(payload) or text
        output_excerpt = bounded_excerpt(output_text, record=record, mask_context=mask_context)
        output_base = {**base, "payload_excerpt": output_excerpt}
        events.extend(events_from_tool_output(output_text, base=output_base, tool_name=tool_name))
    elif is_claude_like_tool_record(data):
        events.extend(events_from_claude_like_record(data, record=record, base=base))
    elif has_claude_message_content_tool_items(data):
        events.extend(events_from_claude_message_content_items(data, record=record, base=base, mask_context=mask_context))
    return dedupe_events(events)


def base_event_fields(record: Record, *, tool_name: str | None, payload_excerpt: str | None) -> dict:
    project = project_from_raw(record.raw)
    return {
        "event_id": "",
        "source_ref": {"path": record.path, "line": record.line, "session": session_key(record.path)},
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "timestamp_utc": timestamp_utc(record.timestamp),
        "role": record.role,
        "cwd": record.cwd,
        "project": project,
        "tool_name": tool_name,
        "payload_excerpt": payload_excerpt,
    }


def events_from_command(command: str, *, base: dict, arguments: dict | str | None) -> list[ToolEvent]:
    events = [
        ToolEvent(
            event_type="command_run",
            event_family="command",
            confidence="high",
            evidence_basis=["structured_function_call", "command_argument"],
            action="run_command",
            command=command,
            arguments=arguments,
            targets=targets_from_paths(paths_from_command(command)),
            **base,
        )
    ]
    command_kind = classify_command(command)
    paths = paths_from_command(command)
    if command_kind == "file_read":
        events.append(
            ToolEvent(
                event_type="file_read",
                event_family="file",
                confidence="high",
                evidence_basis=["structured_function_call", "parsed_read_command"],
                action="read_file",
                command=command,
                targets=targets_from_paths(paths),
                **base,
            )
        )
        events.extend(skill_file_events(paths, command=command, base=base))
    elif command_kind == "file_search":
        events.append(
            ToolEvent(
                event_type="file_search",
                event_family="file",
                confidence="high",
                evidence_basis=["structured_function_call", "parsed_search_command"],
                action="search_files",
                command=command,
                targets=targets_from_paths(paths),
                **base,
            )
        )
    elif command_kind == "test_run":
        events.append(
            ToolEvent(
                event_type="test_run",
                event_family="verification",
                confidence="high",
                evidence_basis=["structured_function_call", "parsed_test_command"],
                action="run_tests",
                command=command,
                targets=targets_from_paths(paths),
                **base,
            )
        )
    elif command_kind == "git_event":
        events.append(
            ToolEvent(
                event_type="git_event",
                event_family="git",
                confidence="high",
                evidence_basis=["structured_function_call", "parsed_git_command"],
                action="run_git",
                command=command,
                targets=targets_from_paths(paths),
                **base,
            )
        )
    for helper in skill_helper_events(paths, command=command, base=base):
        events.append(helper)
    for path in artifact_write_paths_from_command(command):
        events.append(artifact_event(path, base=base, command=command, confidence="high", basis="report_output_argument"))
        if is_review_path(path):
            events.append(review_event(path, base=base, confidence="high", basis="review_artifact_path"))
    return events


def events_from_structured_tool_arguments(tool_name: str, arguments: Any, *, base: dict) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    text = arguments if isinstance(arguments, str) else compact_json(arguments)
    paths = extract_paths(text)
    targets = targets_from_paths(paths)
    lower_name = tool_name.lower()
    if "apply_patch" in lower_name or "edit" in lower_name or "write" in lower_name:
        events.append(
            ToolEvent(
                event_type="file_edit",
                event_family="file",
                confidence="high",
                evidence_basis=["structured_edit_tool"],
                action="edit_file",
                targets=targets,
                arguments=safe_arguments(arguments),
                **base,
            )
        )
        for changed in changed_paths_from_patch(text) or paths:
            events.append(changed_path_event(changed, base=base, confidence="high", basis="structured_edit_tool"))
    elif paths and any(token in lower_name for token in ("open", "read", "view")):
        events.append(
            ToolEvent(
                event_type="file_read",
                event_family="file",
                confidence="high",
                evidence_basis=["structured_file_tool"],
                action="read_file",
                targets=targets,
                arguments=safe_arguments(arguments),
                **base,
            )
        )
        events.extend(skill_file_events(paths, command=None, base=base))
    return events


def events_from_tool_output(output_text: str, *, base: dict, tool_name: str | None) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    if test_output_re().search(output_text):
        events.append(
            ToolEvent(
                event_type="test_run",
                event_family="verification",
                confidence="medium",
                evidence_basis=["structured_tool_output", "test_summary_output"],
                action="test_summary",
                tool_name=tool_name or base.get("tool_name"),
                **{key: value for key, value in base.items() if key != "tool_name"},
            )
        )
    if re.search(r"(^|\n)(diff --git|[ MARC?D]{1,2}\s+[\w./-]+|On branch\b)", output_text):
        events.append(
            ToolEvent(
                event_type="git_event",
                event_family="git",
                confidence="medium",
                evidence_basis=["structured_tool_output", "git_output"],
                action="git_output",
                tool_name=tool_name or base.get("tool_name"),
                **{key: value for key, value in base.items() if key != "tool_name"},
            )
        )
    for path in changed_paths_from_git_output(output_text):
        events.append(changed_path_event(path, base=base, confidence="medium", basis="git_output_path"))
    for path in artifact_paths_from_text(output_text):
        events.append(artifact_event(path, base=base, command=None, confidence="medium", basis="artifact_output_path"))
        if is_review_path(path):
            events.append(review_event(path, base=base, confidence="medium", basis="review_artifact_path"))
    return events


def events_from_claude_like_record(data: dict, *, record: Record, base: dict) -> list[ToolEvent]:
    name = str(data.get("name") or data.get("tool_name") or data.get("tool") or "unknown_tool")
    args = data.get("input") or data.get("arguments") or data.get("parameters") or {}
    claude_base = {**base, "tool_name": name}
    events = [
        ToolEvent(
            event_type="tool_call",
            event_family="tool",
            confidence="high",
            evidence_basis=["structured_tool_record"],
            action=action_from_tool(name),
            arguments=safe_arguments(args),
            **claude_base,
        )
    ]
    command = command_from_call(name, args)
    if command:
        events.extend(events_from_command(command, base=claude_base, arguments=safe_arguments(args)))
    elif name == "unknown_tool":
        events[0].uncertainty.append("unknown_tool_name_from_claude_like_record")
    return events


def events_from_claude_message_content_items(
    data: dict,
    *,
    record: Record,
    base: dict,
    mask_context: MaskContext | None,
) -> list[ToolEvent]:
    events: list[ToolEvent] = []
    for item in claude_message_content_items(data):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "tool_use":
            name = str(item.get("name") or item.get("tool_name") or item.get("tool") or "unknown_tool")
            args = item.get("input") or item.get("arguments") or item.get("parameters") or {}
            item_base = {
                **base,
                "tool_name": name,
                "payload_excerpt": bounded_excerpt(compact_json(item), record=record, mask_context=mask_context),
            }
            events.append(
                ToolEvent(
                    event_type="tool_call",
                    event_family="tool",
                    confidence="high",
                    evidence_basis=["claude_message_content_tool_use"],
                    action=action_from_tool(name),
                    arguments=safe_arguments(args),
                    **item_base,
                )
            )
            command = command_from_call(name, args)
            if command:
                for event in events_from_command(command, base=item_base, arguments=safe_arguments(args)):
                    event.evidence_basis.append("claude_message_content_tool_use")
                    events.append(event)
            elif name == "unknown_tool":
                events[-1].uncertainty.append("unknown_nested_tool_name_from_claude_message_content")
            else:
                for event in events_from_structured_tool_arguments(name, args, base=item_base):
                    event.evidence_basis.append("claude_message_content_tool_use")
                    events.append(event)
        elif item_type == "tool_result":
            output_text = output_text_from_payload(item)
            if not output_text:
                continue
            item_base = {
                **base,
                "tool_name": str(item.get("name") or item.get("tool_name") or "tool_result"),
                "payload_excerpt": bounded_excerpt(output_text, record=record, mask_context=mask_context),
            }
            for event in events_from_tool_output(output_text, base=item_base, tool_name=item_base["tool_name"]):
                event.evidence_basis.append("claude_message_content_tool_result")
                events.append(event)
    return events


def add_command_sequences(events: list[ToolEvent], *, start_index: int, filters: ToolEventFilters) -> list[ToolEvent]:
    command_events = [event for event in events if event.event_type == "command_run" and event.command]
    command_events.sort(key=lambda event: (event.source_ref.get("session") or "", event.cwd or "", event.timestamp_utc or "9999", event.source_ref.get("path") or "", event.source_ref.get("line") or 0))
    sequence_events: list[ToolEvent] = []
    next_index = start_index
    for left, right in zip(command_events, command_events[1:]):
        if (left.source_ref.get("session"), left.cwd) != (right.source_ref.get("session"), right.cwd):
            continue
        normalized = [normalize_command(left.command or ""), normalize_command(right.command or "")]
        digest = hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()[:16]
        event = ToolEvent(
            event_id=f"e{next_index:04d}",
            event_type="command_sequence",
            event_family="workflow",
            source_ref=left.source_ref,
            timestamp=left.timestamp,
            timestamp_utc=left.timestamp_utc,
            role=left.role,
            cwd=left.cwd,
            project=left.project,
            tool_name=left.tool_name,
            confidence="high",
            evidence_basis=["adjacent_structured_command_runs", "deterministic_command_fingerprint"],
            uncertainty=[],
            action="command_chain",
            command=" && ".join(command for command in (left.command, right.command) if command),
            targets=[],
            sequence_id=f"cmdseq-{digest}",
            sequence_index=0,
            related_event_ids=[left.event_id, right.event_id],
            payload_excerpt=f"{normalized[0]} -> {normalized[1]}",
        )
        if include_event(event, filters):
            sequence_events.append(event)
            next_index += 1
    return sorted(events + sequence_events, key=lambda event: (event.timestamp_utc or "9999", event.source_ref.get("path") or "", event.source_ref.get("line") or 0, event.event_id))


def include_event(event: ToolEvent, filters: ToolEventFilters) -> bool:
    if filters.event_types and event.event_type not in filters.event_types:
        return False
    if filters.skill:
        slug = skill_slug(filters.skill)
        haystack = " ".join([event.command or "", compact_json([target.to_dict() for target in event.targets]), event.payload_excerpt or ""]).lower()
        if slug not in haystack and filters.skill.lower() not in haystack:
            return False
    if filters.path_globs:
        values = [target.value for target in event.targets] + event.output_paths
        if not any(fnmatch.fnmatch(value, pattern) for value in values for pattern in filters.path_globs):
            return False
    return True


def event_schema() -> dict:
    return {
        "required": [
            "event_id",
            "schema_version",
            "event_type",
            "event_family",
            "source_ref",
            "timestamp",
            "timestamp_utc",
            "role",
            "cwd",
            "tool_name",
            "confidence",
            "evidence_basis",
            "uncertainty",
        ],
        "optional": [
            "action",
            "targets",
            "command",
            "arguments",
            "output_paths",
            "exit_status",
            "sequence_id",
            "sequence_index",
            "related_event_ids",
            "payload_excerpt",
        ],
        "confidence": {
            "high": "Structured tool/function arguments, explicit tool names plus parsed command, or structured edit/write payloads.",
            "medium": "Structured tool output with strong machine-readable markers such as test, git, artifact, or review paths.",
            "low": "Reserved for prose-derived rows paired with structured evidence; not emitted as execution-supporting in this slice.",
            "excluded": "Docs, install, roadmap, planning, suggestion, or prose-only mentions separated from executed events.",
        },
    }


def compact_ai_handoff(events: list[dict], excluded_mentions: list[dict]) -> dict:
    top_events = events[:12]
    return {
        "event_count": len(events),
        "excluded_mention_count": len(excluded_mentions),
        "top_events": [
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "source_ref": event["source_ref"],
                "tool_name": event.get("tool_name"),
                "command": event.get("command"),
                "targets": event.get("targets", [])[:3],
                "confidence": event["confidence"],
                "basis": event.get("evidence_basis", []),
            }
            for event in top_events
        ],
        "excluded_examples": excluded_mentions[:5],
    }


def detector_schema_gap(*, events: list[dict], observations: dict, warnings: list[str]) -> dict | None:
    if events:
        return None
    cap_warnings = [warning for warning in warnings if "cap" in str(warning).lower()]
    saw_top_level = int(observations.get("top_level_tool_records") or 0)
    saw_nested_use = int(observations.get("claude_message_content_tool_use_records") or 0)
    saw_nested_result = int(observations.get("claude_message_content_tool_result_records") or 0)
    if saw_nested_use or saw_nested_result:
        blocker = "nested_claude_tool_payload_unparsed"
        missing_fields = []
        next_read = "Inspect nested Claude message.content tool_use/tool_result records; the detector saw structured candidates but emitted no events."
    elif saw_top_level:
        blocker = "top_level_tool_payload_unparsed"
        missing_fields = []
        next_read = "Inspect top-level tool/function records; the detector saw structured candidates but emitted no events."
    else:
        blocker = "no_structured_tool_payloads_in_scanned_scope"
        missing_fields = [
            "top-level payload.type=function_call/tool_call/tool_use/tool_result",
            "top-level name/tool_name plus arguments/input",
            "Claude message.content[].type=tool_use/tool_result",
        ]
        next_read = "Open the selected source refs or broaden the approved source window; scanned records only exposed prose/text-like shapes."
    return {
        "status": "no_executable_action_events",
        "blocker": blocker,
        "missing_event_roles": ["tool_call", "command_run", "file_read", "git_event", "test_run"],
        "missing_schema_fields": missing_fields,
        "detector_observations": observations,
        "cap_limited": bool(cap_warnings),
        "cap_warnings": cap_warnings,
        "next_read": next_read,
    }


def is_structured_tool_call(payload: Any) -> bool:
    return isinstance(payload, dict) and (
        payload.get("type") in {"function_call", "tool_call"}
        or payload.get("role") in {"tool_call", "function_call"}
        or ("name" in payload and ("arguments" in payload or "input" in payload) and payload.get("type") != "function_call_output")
    )


def is_structured_tool_output(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("type") in {"function_call_output", "tool_output"}


def is_claude_like_tool_record(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    return data.get("type") in {"tool_use", "tool_result"} or data.get("role") == "tool_use" or ("tool_name" in data and ("input" in data or "arguments" in data))


def claude_message_content_items(data: dict) -> list[dict]:
    if not isinstance(data, dict):
        return []
    message = data.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def has_claude_message_content_tool_items(data: dict) -> bool:
    return any(item.get("type") in {"tool_use", "tool_result"} for item in claude_message_content_items(data))


def empty_detector_observations() -> dict:
    return {
        "records_scanned": 0,
        "roles": {},
        "top_level_tool_records": 0,
        "claude_message_content_tool_use_records": 0,
        "claude_message_content_tool_result_records": 0,
        "claude_message_content_text_only_records": 0,
    }


def observe_record_shape(record: Record, observations: dict) -> None:
    observations["records_scanned"] += 1
    role = record.role or "unknown"
    observations["roles"][role] = observations["roles"].get(role, 0) + 1
    data = record.raw or {}
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
    if is_structured_tool_call(payload) or is_structured_tool_output(payload) or is_claude_like_tool_record(data):
        observations["top_level_tool_records"] += 1
    item_types = [item.get("type") for item in claude_message_content_items(data)]
    if "tool_use" in item_types:
        observations["claude_message_content_tool_use_records"] += 1
    if "tool_result" in item_types:
        observations["claude_message_content_tool_result_records"] += 1
    if item_types and set(item_types) <= {"text"}:
        observations["claude_message_content_text_only_records"] += 1


def tool_name_from_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    name = payload.get("name") or payload.get("tool_name") or payload.get("tool")
    return str(name) if name else None


def arguments_from_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    args = payload.get("arguments", payload.get("input", payload.get("parameters")))
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return args
    return args


def command_from_call(tool_name: str | None, arguments: Any) -> str | None:
    if not tool_name:
        return None
    lower = tool_name.lower()
    if not any(token in lower for token in ("exec_command", "bash", "shell", "run_command")):
        return None
    if isinstance(arguments, dict):
        command = arguments.get("cmd") or arguments.get("command") or arguments.get("script")
        return str(command) if command else None
    if isinstance(arguments, str):
        return arguments
    return None


def output_text_from_payload(payload: dict) -> str:
    return text_from_item(payload.get("output") or payload.get("content") or payload.get("result"))


def safe_arguments(arguments: Any) -> dict | str | None:
    if isinstance(arguments, (dict, str)) or arguments is None:
        return arguments
    return compact_json(arguments)


def action_from_tool(tool_name: str | None) -> str:
    if not tool_name:
        return "invoke_tool"
    lower = tool_name.lower()
    if "exec_command" in lower or "bash" in lower or "shell" in lower:
        return "run_command"
    if "apply_patch" in lower or "edit" in lower:
        return "edit_file"
    if "read" in lower or "open" in lower or "view" in lower:
        return "read_file"
    return "invoke_tool"


def classify_command(command: str) -> str | None:
    parts = shell_words(command)
    if not parts:
        return None
    first = PurePosixPath(parts[0]).name
    joined = " ".join(parts).lower()
    if first in {"cat", "sed", "less", "head", "tail", "nl"}:
        return "file_read"
    if first == "rg" or first in {"grep", "find"}:
        return "file_search"
    if first == "git":
        return "git_event"
    if any(token in joined for token in ("pytest", "validate-install", "benchmark", "diff --check")):
        return "test_run"
    return None


def shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def paths_from_command(command: str) -> list[str]:
    parts = shell_words(command)
    paths: list[str] = []
    for part in parts[1:]:
        if part.startswith("-") or part in {"|", "&&", ";"}:
            continue
        if looks_like_path(part):
            paths.append(strip_path_punctuation(part))
    paths.extend(artifact_write_paths_from_command(command))
    return sorted(set(paths))


def extract_paths(text: str) -> list[str]:
    candidates = re.findall(r"(?:/?[\w.~-]+(?:/[\w. @%+=:~-]+)+|\b[\w.-]+\.(?:md|py|json|jsonl|txt|csv|html|patch)\b)", text)
    return sorted({strip_path_punctuation(item) for item in candidates if looks_like_path(item)})


def looks_like_path(value: str) -> bool:
    value = strip_path_punctuation(value)
    if not value or value.startswith("$"):
        return False
    return "/" in value or bool(re.search(r"\.(?:md|py|json|jsonl|txt|csv|html|patch|sh)$", value, re.I))


def strip_path_punctuation(value: str) -> str:
    return value.strip("'\"`,:;()[]{}<>")


def targets_from_paths(paths: list[str]) -> list[Target]:
    return [Target(kind="path", value=path) for path in sorted(set(paths))]


def skill_file_events(paths: list[str], *, command: str | None, base: dict) -> list[ToolEvent]:
    events = []
    for path in paths:
        if path.lower().endswith("skill.md"):
            events.append(
                ToolEvent(
                    event_type="skill_file_read",
                    event_family="skill",
                    confidence="high",
                    evidence_basis=["structured_function_call", "skill_md_path"],
                    action="read_skill_file",
                    targets=[Target("path", path)],
                    command=command,
                    **base,
                )
            )
    return events


def skill_helper_events(paths: list[str], *, command: str | None, base: dict) -> list[ToolEvent]:
    events = []
    for path in paths:
        if re.search(r"(?:^|/)skills/[^/]+/scripts/|/\.codex/skills/[^/]+/scripts/", path):
            events.append(
                ToolEvent(
                    event_type="skill_helper_invocation",
                    event_family="skill",
                    confidence="high",
                    evidence_basis=["structured_function_call", "skill_helper_path"],
                    action="invoke_skill_helper",
                    targets=[Target("path", path)],
                    command=command,
                    **base,
                )
            )
    return events


def changed_paths_from_patch(text: str) -> list[str]:
    paths = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", text, flags=re.M)
    paths.extend(re.findall(r"^[-+]{3} [ab]/(.+)$", text, flags=re.M))
    paths.extend(re.findall(r"^diff --git a/(.+?) b/", text, flags=re.M))
    return sorted({strip_path_punctuation(path) for path in paths})


def changed_paths_from_git_output(text: str) -> list[str]:
    paths = re.findall(r"^\s*(?:modified:|new file:|deleted:|renamed:)\s+(.+)$", text, flags=re.M)
    paths.extend(re.findall(r"^\s*[ MARC?D]{1,2}\s+([\w./-]+)$", text, flags=re.M))
    paths.extend(re.findall(r"^diff --git a/(.+?) b/", text, flags=re.M))
    return sorted({strip_path_punctuation(path) for path in paths if looks_like_path(path)})


def changed_path_event(path: str, *, base: dict, confidence: str, basis: str) -> ToolEvent:
    return ToolEvent(
        event_type="changed_path",
        event_family="artifact",
        confidence=confidence,
        evidence_basis=[basis],
        action="path_changed",
        targets=[Target("path", path)],
        output_paths=[path],
        **base,
    )


def artifact_write_paths_from_command(command: str) -> list[str]:
    paths = []
    for flag in ("--report-output", "--output", "-o"):
        pattern = rf"{re.escape(flag)}(?:=|\s+)([^\s]+)"
        paths.extend(re.findall(pattern, command))
    redirect_match = re.findall(r">\s*([^\s]+)", command)
    paths.extend(redirect_match)
    return sorted({strip_path_punctuation(path) for path in paths if looks_like_path(path)})


def artifact_paths_from_text(text: str) -> list[str]:
    patterns = [
        r"(?:report written|wrote|written to|saved to|created)\s*:?\s*([^\s]+(?:\.md|\.json|\.html|\.csv|\.txt))",
        r"((?:reviews|codex/state/reviews|docs|tasks|reports)/[\w./-]+\.(?:md|json|html|csv|txt))",
    ]
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(re.findall(pattern, text, flags=re.I))
    return sorted({strip_path_punctuation(path) for path in paths if looks_like_path(path)})


def artifact_event(path: str, *, base: dict, command: str | None, confidence: str, basis: str) -> ToolEvent:
    return ToolEvent(
        event_type="artifact_write",
        event_family="artifact",
        confidence=confidence,
        evidence_basis=[basis],
        action="write_artifact",
        targets=[Target("path", path)],
        output_paths=[path],
        command=command,
        **base,
    )


def review_event(path: str, *, base: dict, confidence: str, basis: str) -> ToolEvent:
    return ToolEvent(
        event_type="review_event",
        event_family="review",
        confidence=confidence,
        evidence_basis=[basis],
        action="review_artifact",
        targets=[Target("path", path)],
        output_paths=[path],
        **base,
    )


def is_review_path(path: str) -> bool:
    return path.startswith("reviews/") or "codex/state/reviews/" in path


def test_output_re():
    return re.compile(r"(\b\d+\s+passed\b|\b\d+\s+failed\b|pytest|validate-install|git diff --check)", re.I)


def excluded_mentions_for_record(record: Record, *, mask_context: MaskContext | None) -> list[dict]:
    text = record.text or ""
    lower = text.lower()
    markers = ("skill.md", "should use", "could use", "install", "roadmap", "planning", "suggest")
    if not any(marker in lower for marker in markers):
        return []
    if record.role.lower() in {"function_call", "tool_call", "tool", "function_call_output", "tool_output"}:
        return []
    return [
        {
            "classification": "excluded",
            "reason": "prose_or_docs_without_structured_execution_evidence",
            "source_ref": {"path": record.path, "line": record.line, "session": session_key(record.path)},
            "timestamp": record.timestamp.isoformat() if record.timestamp else None,
            "timestamp_utc": timestamp_utc(record.timestamp),
            "role": record.role,
            "confidence": "excluded",
            "payload_excerpt": bounded_excerpt(text, record=record, mask_context=mask_context),
        }
    ]


def dedupe_events(events: list[ToolEvent]) -> list[ToolEvent]:
    seen = set()
    unique: list[ToolEvent] = []
    for event in events:
        key = (
            event.event_type,
            event.command,
            tuple((target.kind, target.value) for target in event.targets),
            tuple(event.output_paths),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def bounded_excerpt(text: str, *, record: Record, mask_context: MaskContext | None) -> str:
    if mask_context is not None:
        text = mask_context.mask_text(text, source_path=record.path, source_index=record.line)
    text = " ".join(str(text).split())
    if len(text) <= EXCERPT_LIMIT:
        return text
    return text[: EXCERPT_LIMIT - 3] + "..."


def timestamp_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def project_from_raw(data: dict | None) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("project"):
        return str(data["project"])
    session = data.get("session")
    if isinstance(session, dict) and session.get("project"):
        return str(session["project"])
    payload = data.get("payload")
    if isinstance(payload, dict) and payload.get("project"):
        return str(payload["project"])
    return None


def normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip())


def skill_slug(skill: str) -> str:
    return skill.lower().strip().replace("_", "-").replace(" ", "-")


def mask_payload(mask_context: MaskContext | None) -> dict:
    if mask_context is not None and mask_context.stats is not None:
        return {"masking": mask_context.stats.to_dict()}
    return {}
