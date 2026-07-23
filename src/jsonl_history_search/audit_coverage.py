from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from .timeutil import parse_datetime


TRACE_CLASSIFICATIONS = ("included", "excluded_with_reason", "blocked", "unparseable")
SESSION_METADATA_TYPES = {"session_meta", "turn_context"}
SELECTED_MODEL_KEYS = ("selected_model", "selectedModel", "selected_model_name", "selectedModelName", "model")


@dataclass(frozen=True)
class AuditCoverageFilters:
    selected_models: tuple[str, ...] = ()
    thread_sources: tuple[str, ...] = ()
    agent_roles: tuple[str, ...] = ()
    cwd_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuditCoverageOptions:
    roots: tuple[Path, ...]
    start_date: date | None = None
    end_date: date | None = None
    window_days: int = 1
    filters: AuditCoverageFilters = AuditCoverageFilters()
    max_files: int | None = None
    max_bytes: int | None = None
    trace_ledger_limit: int = 200


def build_audit_coverage_ledger(options: AuditCoverageOptions) -> dict[str, Any]:
    if not options.roots:
        return {"audit_coverage_ledger": {"status": "blocked", "reason": "no_roots"}}
    if options.window_days < 1:
        raise ValueError("window_days must be >= 1")
    if options.start_date and options.end_date and options.start_date > options.end_date:
        raise ValueError("start_date must be <= end_date")

    roots = tuple(path.expanduser().resolve() for path in options.roots)
    candidates = _candidate_paths(roots)
    if options.start_date or options.end_date:
        candidates = [
            path
            for path in candidates
            if _date_in_range(_date_from_path(path), start=options.start_date, end=options.end_date)
        ]

    windows = _build_windows(candidates, start=options.start_date, end=options.end_date, window_days=options.window_days)
    slice_rows = {label: _empty_slice(label, start, end) for label, start, end in windows}
    if any(_date_from_path(path) is None for path in candidates):
        slice_rows.setdefault("undated", _empty_slice("undated", None, None))

    trace_rows: list[dict[str, Any]] = []
    total_bytes = 0
    blocked_by_file_cap = 0
    blocked_by_byte_cap = 0
    for index, path in enumerate(candidates):
        label = _slice_label(_date_from_path(path), windows)
        slice_rows.setdefault(label, _empty_slice(label, None, None))
        size = _safe_size(path)
        if options.max_files is not None and index >= options.max_files:
            blocked_by_file_cap += 1
            row = _blocked_trace(path, label, "file_cap_reached")
        elif options.max_bytes is not None and total_bytes + size > options.max_bytes:
            blocked_by_byte_cap += 1
            row = _blocked_trace(path, label, "byte_cap_reached")
        else:
            total_bytes += size
            row = _inspect_trace(path, label, filters=options.filters)
        _apply_slice_row(slice_rows[label], row)
        if options.trace_ledger_limit < 0 or len(trace_rows) < options.trace_ledger_limit:
            trace_rows.append(row)

    classification_counts = Counter()
    included_model_counts = Counter()
    structural_model_counts = Counter()
    thread_source_counts = Counter()
    agent_role_counts = Counter()
    parse_status_counts = Counter()
    cwd_hash_counts = Counter()
    for slice_row in slice_rows.values():
        classification_counts.update(slice_row["classification_counts"])
        included_model_counts.update(slice_row["included_selected_model_counts"])
        structural_model_counts.update(slice_row["structural_selected_model_counts"])
        thread_source_counts.update(slice_row["thread_source_counts"])
        agent_role_counts.update(slice_row["agent_role_counts"])
        parse_status_counts.update(slice_row["parse_status_counts"])
        cwd_hash_counts.update(slice_row["cwd_hash_counts"])

    status = "capped" if blocked_by_file_cap or blocked_by_byte_cap else "complete"
    if classification_counts.get("unparseable") and not classification_counts.get("included") and status == "complete":
        status = "incomplete"

    return normalize_ledger_for_json(
        {
            "audit_coverage_ledger": {
                "status": status,
                "privacy_boundary": "committed_safe_aggregate_no_raw_text_no_raw_session_ids_no_raw_private_paths",
                "metadata_extraction_policy": {
                    "selected_model_sources": sorted(SESSION_METADATA_TYPES),
                    "selected_model_keys": list(SELECTED_MODEL_KEYS),
                    "quoted_or_tool_output_model_text": "ignored_unless_present_in_structural_metadata",
                },
                "classification_contract": list(TRACE_CLASSIFICATIONS),
                "roots_count": len(roots),
                "deterministic_windowing": {
                    "start_date": options.start_date.isoformat() if options.start_date else None,
                    "end_date": options.end_date.isoformat() if options.end_date else None,
                    "window_days": options.window_days,
                    "assignment": "path_date_from_yyyy/mm/dd_or_iso_filename_then_undated",
                },
                "filters": _filters_payload(options.filters),
                "summary": {
                    "candidate_trace_count": len(candidates),
                    "traces_reported": len(trace_rows),
                    "bytes_read_or_accounted": total_bytes,
                    "classification_counts": _sorted_counter(classification_counts),
                    "included_selected_model_counts": _sorted_counter(included_model_counts),
                    "structural_selected_model_counts": _sorted_counter(structural_model_counts),
                    "thread_source_counts": _sorted_counter(thread_source_counts),
                    "agent_role_counts": _sorted_counter(agent_role_counts),
                    "parse_status_counts": _sorted_counter(parse_status_counts),
                    "cwd_hash_counts": _sorted_counter(cwd_hash_counts),
                    "blocked_by_file_cap": blocked_by_file_cap,
                    "blocked_by_byte_cap": blocked_by_byte_cap,
                },
                "slices": list(slice_rows.values()),
                "trace_ledger": trace_rows,
                "trace_ledger_truncated": max(0, len(candidates) - len(trace_rows)),
            }
        }
    )


def audit_coverage_options_from_args(args: Any) -> AuditCoverageOptions:
    roots = tuple(Path(root) for root in (getattr(args, "root", None) or []))
    if not roots:
        codex_home = Path(getattr(args, "codex_home", None) or Path.home() / ".codex")
        roots = (codex_home / "sessions",)
    return AuditCoverageOptions(
        roots=roots,
        start_date=_date_arg(getattr(args, "start_date", None)),
        end_date=_date_arg(getattr(args, "end_date", None)),
        window_days=int(getattr(args, "window_days", 1)),
        filters=AuditCoverageFilters(
            selected_models=tuple(getattr(args, "selected_model", None) or ()),
            thread_sources=tuple(getattr(args, "thread_source", None) or ()),
            agent_roles=tuple(getattr(args, "agent_role", None) or ()),
            cwd_contains=tuple(getattr(args, "cwd_contains", None) or ()),
        ),
        max_files=getattr(args, "max_files", None),
        max_bytes=getattr(args, "max_bytes", None),
        trace_ledger_limit=int(getattr(args, "trace_ledger_limit", 200)),
    )


def normalize_ledger_for_json(payload: dict[str, Any]) -> dict[str, Any]:
    ledger = payload.get("audit_coverage_ledger")
    if not isinstance(ledger, dict):
        return payload
    normalized_slices = []
    for slice_row in ledger.get("slices") or []:
        row = dict(slice_row)
        for key in (
            "classification_counts",
            "included_selected_model_counts",
            "structural_selected_model_counts",
            "thread_source_counts",
            "agent_role_counts",
            "parse_status_counts",
            "cwd_hash_counts",
        ):
            if isinstance(row.get(key), Counter):
                row[key] = _sorted_counter(row[key])
        normalized_slices.append(row)
    ledger["slices"] = normalized_slices
    return payload


def _candidate_paths(roots: Sequence[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".jsonl":
            paths.append(root)
        elif root.is_dir():
            paths.extend(path for path in root.rglob("*.jsonl") if path.is_file())
    return sorted(dict.fromkeys(paths), key=lambda item: item.as_posix())


def _inspect_trace(path: Path, slice_label: str, *, filters: AuditCoverageFilters) -> dict[str, Any]:
    structural_records_seen = 0
    parse_errors = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    selected_model: str | None = None
    selected_model_source: str | None = None
    thread_source: str | None = None
    agent_role: str | None = None
    cwd: str | None = None
    workspace_roots: list[str] = []
    parse_status_values: list[str] = []
    model_metadata_candidates: list[str] = []
    session_id: Any = None

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return _blocked_trace(path, slice_label, "open_failed")

    with handle:
        for line in handle:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                parse_errors += 1
                continue
            if not isinstance(raw, Mapping):
                parse_errors += 1
                continue
            record_kind, data = _structural_metadata_payload(raw)
            if record_kind is None or data is None:
                continue
            structural_records_seen += 1
            timestamp = _metadata_timestamp(raw, data)
            if timestamp is not None:
                first_timestamp = timestamp if first_timestamp is None else min(first_timestamp, timestamp)
                last_timestamp = timestamp if last_timestamp is None else max(last_timestamp, timestamp)
            if session_id is None:
                session_id = data.get("session_id") or data.get("id") or raw.get("session_id")
            if cwd is None:
                cwd = _string_or_none(data.get("cwd") or raw.get("cwd"))
            roots = data.get("workspace_roots")
            if isinstance(roots, list):
                workspace_roots.extend(str(root) for root in roots if root)
            if thread_source is None:
                thread_source = _string_or_none(data.get("thread_source"))
            if agent_role is None:
                agent_role = _string_or_none(data.get("agent_role") or data.get("agent_nickname"))
            parse_status = _string_or_none(data.get("parse_status") or data.get("parsing_status"))
            if parse_status:
                parse_status_values.append(parse_status)
            model_value = _selected_model_from_metadata(data)
            if model_value:
                model_metadata_candidates.append(model_value)
                if selected_model is None:
                    selected_model = model_value
                    selected_model_source = f"{record_kind}.{_selected_model_key_from_metadata(data)}"

    classification, reason = _classify_trace(
        structural_records_seen=structural_records_seen,
        parse_errors=parse_errors,
        selected_model=selected_model,
        thread_source=thread_source,
        agent_role=agent_role,
        cwd=cwd,
        filters=filters,
    )
    parse_status = "parsed"
    if parse_errors:
        parse_status = "parsed_with_errors" if structural_records_seen else "unparseable"
    if parse_status_values:
        parse_status = parse_status_values[0]

    workspace_hashes = sorted({_hash_value(root) for root in workspace_roots if root})
    return {
        "trace_id": _hash_value(path.as_posix()),
        "path_date": _date_from_path(path).isoformat() if _date_from_path(path) else None,
        "slice": slice_label,
        "classification": classification,
        "reason": reason,
        "selected_model": selected_model,
        "selected_model_source": selected_model_source,
        "thread_source": thread_source,
        "agent_role": agent_role,
        "cwd_hash": _hash_value(cwd),
        "cwd_present": bool(cwd),
        "workspace_root_hashes": workspace_hashes,
        "workspace_root_count": len(workspace_hashes),
        "timestamp_first": first_timestamp.isoformat() if first_timestamp else None,
        "timestamp_last": last_timestamp.isoformat() if last_timestamp else None,
        "parse_status": parse_status,
        "structural_records_seen": structural_records_seen,
        "parse_error_count": parse_errors,
        "structural_selected_model_candidates": sorted(set(model_metadata_candidates)),
        "session_id_hash": _hash_value(session_id),
    }


def _structural_metadata_payload(raw: Mapping[str, Any]) -> tuple[str | None, Mapping[str, Any] | None]:
    payload = raw.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else None
    top_type = _string_or_none(raw.get("type"))
    payload_type = _string_or_none(payload_map.get("type")) if payload_map else None
    if top_type in SESSION_METADATA_TYPES:
        return top_type, payload_map if payload_map is not None else raw
    if payload_type in SESSION_METADATA_TYPES and payload_map is not None:
        return payload_type, payload_map
    return None, None


def _selected_model_from_metadata(data: Mapping[str, Any]) -> str | None:
    key = _selected_model_key_from_metadata(data)
    return _string_or_none(data.get(key)) if key else None


def _selected_model_key_from_metadata(data: Mapping[str, Any]) -> str | None:
    for key in SELECTED_MODEL_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return key
    return None


def _metadata_timestamp(raw: Mapping[str, Any], data: Mapping[str, Any]) -> datetime | None:
    raw_value = data.get("timestamp") or raw.get("timestamp") or data.get("created_at") or raw.get("created_at")
    return parse_datetime(raw_value, None) if raw_value is not None else None


def _classify_trace(
    *,
    structural_records_seen: int,
    parse_errors: int,
    selected_model: str | None,
    thread_source: str | None,
    agent_role: str | None,
    cwd: str | None,
    filters: AuditCoverageFilters,
) -> tuple[str, str]:
    if structural_records_seen == 0 and parse_errors:
        return "unparseable", "json_parse_errors_no_structural_metadata"
    if structural_records_seen == 0:
        return "excluded_with_reason", "structural_metadata_missing"
    if not selected_model:
        return "excluded_with_reason", "selected_model_metadata_missing"
    if filters.selected_models and selected_model not in filters.selected_models:
        return "excluded_with_reason", "selected_model_filter_mismatch"
    if filters.thread_sources and (thread_source or "") not in filters.thread_sources:
        return "excluded_with_reason", "thread_source_filter_mismatch"
    if filters.agent_roles and (agent_role or "") not in filters.agent_roles:
        return "excluded_with_reason", "agent_role_filter_mismatch"
    if filters.cwd_contains and not any(fragment in (cwd or "") for fragment in filters.cwd_contains):
        return "excluded_with_reason", "cwd_filter_mismatch"
    return "included", "structural_selected_model_metadata_present"


def _blocked_trace(path: Path, slice_label: str, reason: str) -> dict[str, Any]:
    return {
        "trace_id": _hash_value(path.as_posix()),
        "path_date": _date_from_path(path).isoformat() if _date_from_path(path) else None,
        "slice": slice_label,
        "classification": "blocked",
        "reason": reason,
        "selected_model": None,
        "selected_model_source": None,
        "thread_source": None,
        "agent_role": None,
        "cwd_hash": None,
        "cwd_present": False,
        "workspace_root_hashes": [],
        "workspace_root_count": 0,
        "timestamp_first": None,
        "timestamp_last": None,
        "parse_status": "blocked",
        "structural_records_seen": 0,
        "parse_error_count": 0,
        "structural_selected_model_candidates": [],
        "session_id_hash": None,
    }


def _apply_slice_row(slice_row: dict[str, Any], row: Mapping[str, Any]) -> None:
    slice_row["candidate_trace_count"] += 1
    slice_row["classification_counts"][row["classification"]] += 1
    selected_model = row.get("selected_model")
    if selected_model:
        slice_row["structural_selected_model_counts"][selected_model] += 1
        if row.get("classification") == "included":
            slice_row["included_selected_model_counts"][selected_model] += 1
    if row.get("thread_source"):
        slice_row["thread_source_counts"][row["thread_source"]] += 1
    if row.get("agent_role"):
        slice_row["agent_role_counts"][row["agent_role"]] += 1
    if row.get("parse_status"):
        slice_row["parse_status_counts"][row["parse_status"]] += 1
    if row.get("cwd_hash"):
        slice_row["cwd_hash_counts"][row["cwd_hash"]] += 1
    if row.get("classification") == "blocked":
        slice_row["cap_status"] = "capped_or_blocked" if row.get("reason", "").endswith("_cap_reached") else "blocked"


def _empty_slice(label: str, start: date | None, end: date | None) -> dict[str, Any]:
    return {
        "slice": label,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "cap_status": "complete",
        "candidate_trace_count": 0,
        "classification_counts": Counter(),
        "included_selected_model_counts": Counter(),
        "structural_selected_model_counts": Counter(),
        "thread_source_counts": Counter(),
        "agent_role_counts": Counter(),
        "parse_status_counts": Counter(),
        "cwd_hash_counts": Counter(),
    }


def _build_windows(
    candidates: Sequence[Path],
    *,
    start: date | None,
    end: date | None,
    window_days: int,
) -> list[tuple[str, date | None, date | None]]:
    dates = sorted({_date_from_path(path) for path in candidates if _date_from_path(path) is not None})
    first = start or (dates[0] if dates else None)
    last = end or (dates[-1] if dates else None)
    if first is None or last is None:
        return []
    windows: list[tuple[str, date | None, date | None]] = []
    current = first
    while current <= last:
        window_end = min(last, current + timedelta(days=window_days - 1))
        windows.append((f"{current.isoformat()}..{window_end.isoformat()}", current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def _slice_label(value: date | None, windows: Sequence[tuple[str, date | None, date | None]]) -> str:
    if value is None:
        return "undated"
    for label, start, end in windows:
        if start is not None and end is not None and start <= value <= end:
            return label
    return value.isoformat()


def _date_in_range(value: date | None, *, start: date | None, end: date | None) -> bool:
    if value is None:
        return True
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def _date_from_path(path: Path) -> date | None:
    parts = path.parts
    for index in range(len(parts) - 2):
        if re.fullmatch(r"\d{4}", parts[index]) and re.fullmatch(r"\d{2}", parts[index + 1]) and re.fullmatch(
            r"\d{2}", parts[index + 2]
        ):
            try:
                return date(int(parts[index]), int(parts[index + 1]), int(parts[index + 2]))
            except ValueError:
                pass
    match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", path.name)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    return None


def _filters_payload(filters: AuditCoverageFilters) -> dict[str, list[str]]:
    return {
        "selected_models": list(filters.selected_models),
        "thread_sources": list(filters.thread_sources),
        "agent_roles": list(filters.agent_roles),
        "cwd_contains": list(filters.cwd_contains),
    }


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _hash_value(value: Any) -> str | None:
    if value is None:
        return None
    digest = hashlib.sha256(f"jsh-1484b:{value}".encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest[:16]}"


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_arg(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)
