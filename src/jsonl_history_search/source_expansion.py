from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
from .agent_contract import source_ref_target
from .sources import SourcePlan, path_is_inside, source_kind_for_path


DEFAULT_WINDOW_BEFORE = 2
DEFAULT_WINDOW_AFTER = 2
DEFAULT_NEXT_COUNT = 3
DEFAULT_MAX_RECORDS = 20


@dataclass(frozen=True)
class ExpansionRequest:
    target: str | None = None
    source_ref: dict[str, Any] | None = None
    mode: str = "record"
    before: int = DEFAULT_WINDOW_BEFORE
    after: int = DEFAULT_WINDOW_AFTER
    next_count: int = DEFAULT_NEXT_COUNT
    max_records: int = DEFAULT_MAX_RECORDS


@dataclass(frozen=True)
class ParsedTarget:
    path_text: str | None
    line: int | None
    raw_target: str | None


def build_source_expansion_payload(
    *,
    request: ExpansionRequest,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
) -> dict[str, Any]:
    parsed = parse_expansion_target(request.target, request.source_ref)
    warnings = list(plan.warnings)
    base_payload: dict[str, Any] = {
        "schema_version": 1,
        "mode": request.mode,
        "requested_target": parsed.raw_target,
        "anchor": {
            "target": parsed.raw_target,
            "source_ref": request.source_ref or source_ref_from_parsed_target(parsed),
            "line": parsed.line,
            "line_is_one_based": True,
        },
        "limits": {
            "before": max(0, int(request.before)),
            "after": max(0, int(request.after)),
            "next_count": max(0, int(request.next_count)),
            "max_records": max(0, int(request.max_records)),
        },
        "privacy_flags": {
            "raw_text_included": True,
            "raw_paths_included": True,
            "source_refs_may_point_to_local_transcripts": True,
            "vectors_indexes_or_persistent_cache_used": False,
        },
        "claim_boundaries": {
            "broad_search_rerun": False,
            "ranking_or_cap_changed": False,
            "all_history_completeness_claim_supported": False,
        },
        "warnings": warnings,
    }
    if request.mode not in {"record", "window", "next"}:
        return {"source_expansion": {**base_payload, "status": "invalid_mode", "records": []}}
    if not parsed.path_text:
        return {"source_expansion": {**base_payload, "status": "missing_path", "records": []}}
    if parsed.line is None:
        return {"source_expansion": {**base_payload, "status": "missing_line", "records": []}}
    if parsed.line < 1:
        return {"source_expansion": {**base_payload, "status": "invalid_line", "records": []}}

    source_path = resolve_source_path(parsed.path_text, plan=plan, cwd=cwd)
    if source_path is None:
        return {
            "source_expansion": {
                **base_payload,
                "status": "source_not_found_or_out_of_scope",
                "records": [],
                "scope": source_scope(plan),
            }
        }
    source_kind = source_kind_for_path(source_path, plan)
    try:
        records = list(iter_records_for_file(source_path, source_kind, default_tz))
    except (AdapterReadError, OSError) as exc:
        return {
            "source_expansion": {
                **base_payload,
                "status": "read_error",
                "records": [],
                "source_path": str(source_path),
                "error": str(exc),
                "scope": source_scope(plan),
            }
        }

    total_records = len(records)
    if parsed.line > total_records:
        return {
            "source_expansion": {
                **base_payload,
                "status": "anchor_line_not_found",
                "records": [],
                "source_path": str(source_path),
                "source_kind": source_kind,
                "total_records_in_source": total_records,
                "scope": source_scope(plan),
            }
        }

    selected, requested_count, range_start, range_end = select_records(
        records,
        anchor_line=parsed.line,
        mode=request.mode,
        before=max(0, int(request.before)),
        after=max(0, int(request.after)),
        next_count=max(0, int(request.next_count)),
        max_records=max(0, int(request.max_records)),
    )
    included_count = len(selected)
    omitted_count = max(0, requested_count - included_count)
    payload = {
        **base_payload,
        "status": "expanded",
        "source_path": str(source_path),
        "source_kind": source_kind,
        "total_records_in_source": total_records,
        "line_range": {
            "start": range_start,
            "end": range_end,
            "anchor": parsed.line,
            "line_is_one_based": True,
        },
        "counts": {
            "requested_count": requested_count,
            "included_count": included_count,
            "omitted_count": omitted_count,
            "before_available": max(0, parsed.line - 1),
            "after_available": max(0, total_records - parsed.line),
        },
        "truncated": omitted_count > 0,
        "boundary_flags": {
            "start_of_source_reached": bool(range_start == 1),
            "end_of_source_reached": bool(range_end == total_records or (request.mode == "next" and included_count < request.next_count)),
        },
        "records": [expanded_record(record) for record in selected],
        "scope": source_scope(plan),
    }
    return {"source_expansion": payload}


def parse_expansion_target(target: str | None, source_ref: dict[str, Any] | None = None) -> ParsedTarget:
    if source_ref:
        ref_target = source_ref_target(source_ref)
        line = _positive_int_or_none(source_ref.get("line"))
        return ParsedTarget(path_text=str(source_ref.get("path") or "") or None, line=line, raw_target=ref_target)
    if not target:
        return ParsedTarget(path_text=None, line=None, raw_target=None)
    path_text = str(target)
    line: int | None = None
    head, sep, tail = path_text.rpartition(":")
    if sep and tail.strip().isdigit():
        path_text = head
        line = int(tail.strip())
    return ParsedTarget(path_text=path_text or None, line=line, raw_target=target)


def source_ref_from_parsed_target(parsed: ParsedTarget) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    if parsed.path_text:
        ref["path"] = parsed.path_text
    if parsed.line is not None:
        ref["line"] = parsed.line
    return ref


def resolve_source_path(path_text: str, *, plan: SourcePlan, cwd: Path) -> Path | None:
    candidates: list[Path] = []
    raw_path = Path(path_text).expanduser()
    candidates.append(raw_path if raw_path.is_absolute() else cwd / raw_path)
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_dir() and not raw_path.is_absolute():
            candidates.append(base / raw_path)
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.exists() or not resolved.is_file():
            continue
        if source_path_allowed(resolved, plan):
            return resolved
    return None


def source_path_allowed(path: Path, plan: SourcePlan) -> bool:
    for root in plan.roots:
        base = root.path.expanduser().resolve()
        if base.is_file() and path == base:
            return True
        if base.is_dir() and path_is_inside(path, base):
            return True
    return False


def select_records(
    records: list[Any],
    *,
    anchor_line: int,
    mode: str,
    before: int,
    after: int,
    next_count: int,
    max_records: int,
) -> tuple[list[Any], int, int | None, int | None]:
    total_records = len(records)
    if mode == "record":
        start = anchor_line
        end = anchor_line
    elif mode == "window":
        start = max(1, anchor_line - before)
        end = min(total_records, anchor_line + after)
    else:
        start = min(total_records + 1, anchor_line + 1)
        end = min(total_records, anchor_line + next_count)
    requested_count = max(0, end - start + 1)
    if max_records > 0:
        end = min(end, start + max_records - 1)
    else:
        end = start - 1
    if end < start:
        return [], requested_count, None, None
    return records[start - 1 : end], requested_count, start, end


def expanded_record(record: Any) -> dict[str, Any]:
    return {
        "source_ref": {
            "path": str(record.path),
            "line": int(record.line),
        },
        "target": f"{record.path}:{record.line}",
        "line": int(record.line),
        "role": record.role,
        "timestamp": record.raw_timestamp,
        "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
        "cwd": record.cwd,
        "text": record.text,
        "text_included": True,
    }


def source_scope(plan: SourcePlan) -> dict[str, Any]:
    return {
        "selected_roots": [root.to_dict() for root in plan.roots],
        "rejected_roots": [root.to_dict() for root in plan.rejected_roots],
        "root_count": len(plan.roots),
        "rejected_root_count": len(plan.rejected_roots),
    }


def _positive_int_or_none(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed
