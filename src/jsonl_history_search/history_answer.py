from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .latest_user_messages import _bounded_session_paths, _session_ref
from .providers import PROVIDERS, resolve_provider_root
from .status_anchor import (
    _canonical_history_human_rows,
    _canonical_row_matches_exact_status_query_boundary,
    _hash_value,
    _parent_thread_lineage_rejection_reason,
    _session_lineage_metadata,
    is_status_question,
)


DIRECT_ANCHOR_CANDIDATE_CAP = 500
DIRECT_RECAP_ITEM_CAP = 8

_LATEST_MESSAGE_INTERVAL_RE = re.compile(
    r"\b(?:since|after)\s+(?:my|i)\s+(?:last|previous)?\s*(?:message|messaged|said|asked|wrote|told)\b"
    r"|\bwhile\s+i\s+was\s+away\b",
    re.IGNORECASE,
)


def is_direct_latest_message_history_query(query: str) -> bool:
    """Return true for ordinary natural requests anchored on the user's last turn."""

    normalized = " ".join(str(query or "").split())
    return bool(_LATEST_MESSAGE_INTERVAL_RE.search(normalized))


def build_direct_history_answer_payload(args: Any, *, route_payload: dict | None = None) -> dict:
    """Answer latest-status questions without packet or continuation choreography."""

    query = str(getattr(args, "query", "") or "").strip()
    cwd = Path(getattr(args, "cwd", ".")).expanduser().resolve()
    started = time.monotonic()
    deadline = started + max(0.01, float(getattr(args, "max_elapsed", 15.0) or 15.0))
    provider = _select_direct_provider(args, cwd=cwd)
    route = _public_route((route_payload or {}).get("ask_router") or {})
    inventory = _source_inventory(args, cwd=cwd, selected_provider=provider)
    limits = _limits(args)

    if provider == "claude":
        return _build_claude_direct_history_answer(
            args,
            query=query,
            cwd=cwd,
            route=route,
            inventory=inventory,
            limits=limits,
            deadline=deadline,
        )

    codex_home = resolve_provider_root(args, provider="codex", cwd=cwd)

    selection = _select_latest_parent_anchor(
        query=query,
        cwd=cwd,
        codex_home=codex_home,
        candidate_cap=DIRECT_ANCHOR_CANDIDATE_CAP,
        deadline=deadline,
        max_bytes=int(getattr(args, "max_bytes", 100 * 1024 * 1024) or 0),
        max_files=int(getattr(args, "max_files", 200) or 0),
    )
    if selection.get("status") != "selected":
        return {
            "ask_answer": _boundary_answer(
                query=query,
                cwd=cwd,
                route=route,
                selection=selection,
                inventory=inventory,
                limits=limits,
            )
        }

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        selection = {
            "status": "partial",
            "reason": "max_elapsed_reached_before_post_anchor_interval_reconstruction",
            "boundary": selection.get("boundary"),
            "recovery": "bounded_same_root_recovery_stopped_at_max_elapsed",
            "selected_anchor": selection.get("selected_anchor"),
        }
        return {"ask_answer": _boundary_answer(query=query, cwd=cwd, route=route, selection=selection, inventory=inventory, limits=limits)}
    reconstruction = _reconstruct_codex_interval(
        selection=selection,
        codex_home=codex_home,
        deadline=deadline,
        max_bytes=int(getattr(args, "max_bytes", 100 * 1024 * 1024) or 0),
    )
    selected = reconstruction["selected_anchor"]
    interval = reconstruction["interval"]
    coverage = reconstruction["coverage"]
    activity = [item for item in interval.get("chronological_activity") or [] if isinstance(item, dict)]
    recap_items = _recap_items(activity)
    refs = _answer_refs(selected, interval, activity)

    anchor_matches = str(selected.get("source_ref") or "") == str(
        selection["selected_anchor"].get("source_ref") or ""
    )
    complete = coverage.get("complete") is True
    has_raw_interval = bool(activity) and int(coverage.get("records_after_anchor") or 0) > 0
    answer_status = "answered-from-proof" if anchor_matches and complete and has_raw_interval else "partial"
    fail_reason = None
    if not anchor_matches:
        answer_status = "ambiguous"
        fail_reason = "the explicit chronological anchor was not preserved by interval reconstruction"
    elif not has_raw_interval:
        answer_status = "not_found"
        fail_reason = "no raw post-anchor interval records were found in the bounded selected session lineage"
    elif not complete:
        fail_reason = "the retrieved post-anchor interval is partial; omitted or missing scope is reported below"

    answer_text = _answer_text(
        selected=selected or selection["selected_anchor"],
        interval=interval,
        recap_items=recap_items,
        answer_status=answer_status,
    )
    source_contract = {
        "built_from": "direct_history_answer",
        "answer_action_status": answer_status,
        "promotion_status": "direct_lineage_chronology_and_bounded_interval",
        "selection_policy": "lineage_then_timestamp_utc_then_canonical_history_line; no semantic ranking",
        "selection_reason": selection.get("reason"),
        "current_or_side_boundary": selection.get("boundary"),
        "anchor": _public_anchor(selected or selection["selected_anchor"]),
        "searched_interval": {
            "anchor": interval.get("anchor"),
            "boundary": interval.get("boundary"),
            "chronological_entry_count": interval.get("chronological_entry_count"),
        },
        "coverage": coverage,
        "missing_or_capped_scope": _missing_or_capped_scope(interval, coverage),
        "source_inventory": inventory,
        "limits": limits,
        "native_source_refs": [item.get("source_ref") for item in refs if item.get("source_ref")],
        "visible_context_used_as_proof": False,
        "legacy_packet_or_execute_continuation_used": False,
    }
    return {
        "ask_answer": {
            "schema_version": "direct_history_answer.v1",
            "query": query,
            "cwd": str(cwd),
            "selected_route": route,
            "execution": {
                "status": "executed",
                "command_family": "direct-history-answer",
                "internal_recovery": selection.get("recovery"),
            },
            "answer_status": answer_status,
            "answer": answer_text,
            "refs_used": refs,
            "proof_summary": recap_items,
            "fail_closed_reason": fail_reason,
            "next_proof_step": _next_boundary(interval, coverage) if answer_status != "answered-from-proof" else None,
            "source_role_explanation": None,
            "source_contract": source_contract,
        }
    }


def _select_latest_parent_anchor(
    *,
    query: str,
    cwd: Path,
    codex_home: Path,
    candidate_cap: int,
    deadline: float,
    max_bytes: int,
    max_files: int,
) -> dict:
    history_path = codex_home / "history.jsonl"
    if not history_path.is_file():
        return {
            "status": "not_found",
            "reason": "codex_history_missing",
            "boundary": {"missing": str(history_path)},
            "recovery": "same_root_history_and_sessions_exhausted",
        }
    scan_state: dict[str, Any] = {}
    rows = _canonical_history_human_rows(
        history_path,
        query=query,
        deadline=deadline,
        max_bytes=max_bytes,
        scan_state=scan_state,
    )
    if not scan_state.get("complete", True):
        return {
            "status": "partial",
            "reason": "canonical_history_scan_stopped_at_explicit_byte_or_elapsed_cap",
            "boundary": {"history_path": str(history_path), **scan_state},
            "recovery": "bounded_history_scan_stopped_at_cap",
        }
    if not rows:
        return {
            "status": "not_found",
            "reason": "no_human_history_rows",
            "boundary": {"history_path": str(history_path)},
            "recovery": "same_root_history_and_sessions_exhausted",
        }

    rows.sort(key=_chronology_key)
    lineage_index = _build_lineage_index(
        codex_home=codex_home,
        cwd=cwd,
        max_files=min(candidate_cap, max_files),
        deadline=deadline,
    )
    current_lineage = _current_environment_lineage(lineage_index=lineage_index)
    current_hashes = {
        str(current_lineage.get(key) or "")
        for key in ("session_id_hash", "raw_thread_id_hash")
        if current_lineage.get(key)
    }
    boundary = next(
        (
            row
            for row in reversed(rows)
            if _source_identifier_hash(row) in current_hashes
        ),
        None,
    )
    boundary_source = "current_session_environment" if boundary else None
    if boundary is None:
        boundary = next(
            (
                row
                for row in reversed(rows)
                if _canonical_row_matches_exact_status_query_boundary(row, query=query)
            ),
            None,
        )
        boundary_source = "exact_status_query_history_row" if boundary else None
    if boundary is None:
        boundary = next(
            (row for row in reversed(rows) if is_status_question(str(row.get("snippet") or ""))),
            None,
        )
        boundary_source = "latest_status_query_history_row" if boundary else None

    boundary_lineage = (
        _lineage_for_row(boundary, lineage_index)
        if boundary is not None
        else current_lineage
    )
    boundary_line = int(boundary.get("line") or 0) if boundary is not None else int(rows[-1].get("line") or 0) + 1
    prior_rows = [row for row in rows if int(row.get("line") or 0) < boundary_line]
    direct_parent_hash = str(boundary_lineage.get("parent_thread_id_hash") or "")
    boundary_hashes = {
        str(boundary_lineage.get(key) or "")
        for key in ("session_id_hash", "raw_thread_id_hash")
        if boundary_lineage.get(key)
    }
    if direct_parent_hash and direct_parent_hash not in boundary_hashes:
        direct = _latest_matching_parent_row(
            prior_rows,
            parent_hash=direct_parent_hash,
            candidate_cap=candidate_cap,
            lineage_index=lineage_index,
            deadline=deadline,
        )
        if direct is not None:
            return {
                "status": "selected",
                "reason": "current_or_side_session_parent_thread_id_then_latest_human_chronology",
                "selected_anchor": direct,
                "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
                "recovery": "direct_parent_edge_resolved_inside_single_invocation",
                "_lineage_index": lineage_index,
            }
        return {
            "status": "not_found",
            "reason": "direct_parent_thread_id_has_no_eligible_human_history_row",
            "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
            "recovery": "same_root_direct_parent_rows_exhausted",
        }

    # In a main session there is no parent edge: the anchor is simply the
    # latest earlier human turn in that same canonical session.
    if boundary_hashes:
        for row in reversed(prior_rows[-candidate_cap:]):
            if _source_identifier_hash(row) not in boundary_hashes:
                continue
            if not _canonical_history_only_anchor_allowed(row):
                continue
            selected = dict(row)
            selected["lineage_resolution"] = _lineage_for_row(row, lineage_index)
            selected["parent_thread_lineage_anchor"] = False
            return {
                "status": "selected",
                "reason": "current_main_session_then_latest_prior_human_chronology",
                "selected_anchor": selected,
                "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
                "recovery": "same_session_chronology_resolved_inside_single_invocation",
                "_lineage_index": lineage_index,
            }

    side_identifiers = {
        str(row.get("source_identifier") or "")
        for row in rows
        if is_status_question(str(row.get("snippet") or ""))
    }
    if boundary is not None:
        side_identifiers.add(str(boundary.get("source_identifier") or ""))
    eligible: list[dict] = []
    for row in reversed(prior_rows[-candidate_cap:]):
        if str(row.get("source_identifier") or "") in side_identifiers:
            continue
        if time.monotonic() >= deadline:
            return {
                "status": "partial",
                "reason": "parent_candidate_scan_stopped_at_max_elapsed_cap",
                "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
                "recovery": "bounded_same_root_recovery_stopped_at_max_elapsed",
            }
        lineage = _lineage_for_row(row, lineage_index)
        if lineage.get("status") == "ok" and _parent_thread_lineage_rejection_reason(lineage):
            continue
        if lineage.get("status") == "ok":
            candidate_parent = str(lineage.get("parent_thread_id_hash") or "")
            candidate_hashes = {
                str(lineage.get(key) or "")
                for key in ("session_id_hash", "raw_thread_id_hash")
                if lineage.get(key)
            }
            if candidate_parent and candidate_parent not in candidate_hashes:
                continue
        elif not _canonical_history_only_anchor_allowed(row):
            continue
        enriched = dict(row)
        enriched["lineage_resolution"] = lineage
        enriched["parent_thread_lineage_anchor"] = True
        enriched["canonical_history_only_lineage"] = lineage.get("status") != "ok"
        eligible.append(enriched)
        if len(eligible) >= 2 and _timestamp_key(eligible[-1]) < _timestamp_key(eligible[0]):
            break
    if not eligible:
        return {
            "status": "not_found",
            "reason": "no_eligible_parent_or_main_human_row_in_bounded_same_root_recovery",
            "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
            "recovery": "same_root_root_session_candidates_exhausted",
        }
    newest_timestamp = _timestamp_key(eligible[0])
    newest = [item for item in eligible if _timestamp_key(item) == newest_timestamp]
    distinct_sessions = {str(item.get("source_identifier") or "") for item in newest}
    if len(distinct_sessions) > 1:
        return {
            "status": "ambiguous",
            "reason": "multiple_equally_latest_eligible_parent_or_main_sessions_without_a_direct_parent_edge",
            "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
            "candidate_refs": [item.get("source_ref") for item in newest[:8]],
            "recovery": "same_root_root_session_candidates_exhausted",
        }
    selected = max(newest, key=_chronology_key)
    return {
        "status": "selected",
        "reason": "latest_eligible_root_human_row_before_side_boundary_by_chronology",
        "selected_anchor": selected,
        "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
        "recovery": "bounded_same_root_root_session_recovery_inside_single_invocation",
        "_lineage_index": lineage_index,
    }


def _latest_matching_parent_row(
    rows: list[dict],
    *,
    parent_hash: str,
    candidate_cap: int,
    lineage_index: dict,
    deadline: float,
) -> dict | None:
    for row in reversed(rows[-candidate_cap:]):
        if time.monotonic() >= deadline:
            return None
        if _source_identifier_hash(row) != parent_hash:
            continue
        lineage = _lineage_for_row(row, lineage_index)
        if lineage.get("status") == "ok" and _parent_thread_lineage_rejection_reason(lineage):
            continue
        if lineage.get("status") != "ok" and not _canonical_history_only_anchor_allowed(row):
            continue
        if lineage.get("status") == "ok":
            hashes = {
                str(lineage.get(key) or "")
                for key in ("session_id_hash", "raw_thread_id_hash")
                if lineage.get(key)
            }
            if parent_hash not in hashes:
                continue
        selected = dict(row)
        selected["lineage_resolution"] = lineage
        selected["parent_thread_lineage_anchor"] = True
        selected["canonical_history_only_lineage"] = lineage.get("status") != "ok"
        return selected
    return None


def _build_lineage_index(*, codex_home: Path, cwd: Path, max_files: int, deadline: float) -> dict:
    warnings: list[str] = []
    scan_state: dict[str, Any] = {}
    paths = _bounded_session_paths(
        codex_home / "sessions",
        limit=max_files,
        deadline=deadline,
        warnings=warnings,
        scan_state=scan_state,
    ) if (codex_home / "sessions").is_dir() else []
    by_hash: dict[str, dict] = {}
    for path in paths:
        if time.monotonic() >= deadline:
            warnings.append("Stopped lineage metadata reads at max elapsed cap.")
            break
        meta = _session_lineage_metadata(path, cwd=cwd, deadline=deadline, max_bytes=256 * 1024)
        for key in ("session_id_hash", "raw_thread_id_hash"):
            if meta.get(key):
                by_hash[str(meta[key])] = meta
    metas = []
    seen_refs: set[str] = set()
    for meta in by_hash.values():
        ref = str(meta.get("session_source_ref") or "")
        if ref and ref not in seen_refs:
            seen_refs.add(ref)
            metas.append(meta)
    return {
        "by_hash": by_hash,
        "metas": metas,
        "warnings": warnings,
        "paths_inspected": len(paths),
        "scan_state": scan_state,
    }


def _lineage_for_row(row: Mapping[str, Any] | None, lineage_index: dict) -> dict:
    if row is None:
        return {"status": "missing", "reason": "canonical_boundary_not_available"}
    return lineage_index.get("by_hash", {}).get(
        _source_identifier_hash(row),
        {"status": "missing", "reason": "session_lineage_not_found_in_bounded_index"},
    )


def _current_environment_lineage(*, lineage_index: dict) -> dict:
    raw_ids = [os.environ.get("CODEX_THREAD_ID"), os.environ.get("CODEX_SESSION_ID")]
    expected = {_hash_value(value) for value in raw_ids if value}
    expected.discard(None)
    if not expected:
        return {"status": "missing", "reason": "current_session_environment_not_available_in_selected_root"}
    for expected_hash in expected:
        meta = lineage_index.get("by_hash", {}).get(expected_hash)
        if meta:
            return {**meta, "matched_current_session_environment": True}
    return {"status": "missing", "reason": "current_session_id_not_found_in_selected_root"}


def _source_identifier_hash(row: Mapping[str, Any]) -> str:
    identifier = str(row.get("source_identifier") or "")
    prefix = "history-session-"
    return identifier[len(prefix) :] if identifier.startswith(prefix) else ""


def _canonical_history_only_anchor_allowed(row: Mapping[str, Any]) -> bool:
    """Allow chronological human follow-ups while rejecting known control prompts."""

    return str(row.get("classification") or "") not in {
        "empty",
        "non_human_boundary_or_tool_context",
        "worker_or_reviewer_prompt",
        "admin_or_status_turn",
        "post_status_context",
    }


def _chronology_key(row: Mapping[str, Any]) -> tuple[str, int]:
    return (_timestamp_key(row), int(row.get("line") or 0))


def _timestamp_key(row: Mapping[str, Any]) -> str:
    return str(row.get("timestamp_utc") or "")


def _boundary_summary(boundary: dict | None, lineage: dict, source: str | None) -> dict:
    return {
        "status": "located" if boundary is not None or lineage.get("status") == "ok" else "unavailable",
        "source": source or ("current_session_environment" if lineage.get("status") == "ok" else None),
        "source_ref": boundary.get("source_ref") if boundary else None,
        "timestamp_utc": boundary.get("timestamp_utc") if boundary else None,
        "session_id_hash": lineage.get("session_id_hash"),
        "parent_thread_id_hash": lineage.get("parent_thread_id_hash"),
    }


def _reconstruct_codex_interval(
    *, selection: dict, codex_home: Path, deadline: float, max_bytes: int
) -> dict:
    """Read the selected Codex lineage directly, bounded by the same deadline."""

    selected = dict(selection["selected_anchor"])
    anchor_hash = _source_identifier_hash(selected)
    anchor_timestamp = str(selected.get("timestamp_utc") or "")
    boundary = selection.get("boundary") if isinstance(selection.get("boundary"), dict) else {}
    boundary_timestamp = str(boundary.get("timestamp_utc") or "")
    index = selection.get("_lineage_index") or {}
    metas = [
        meta for meta in index.get("metas", [])
        if anchor_hash in {
            str(meta.get("session_id_hash") or ""),
            str(meta.get("raw_thread_id_hash") or ""),
            str(meta.get("parent_thread_id_hash") or ""),
            str(meta.get("root_thread_id_hash") or ""),
        }
    ]
    activities: list[dict] = []
    warnings = list(index.get("warnings") or [])
    bytes_read = 0
    inspected = 0
    for meta in metas:
        if time.monotonic() >= deadline:
            warnings.append("Stopped before the next Codex lineage file at max elapsed cap.")
            break
        path = Path(str(meta.get("session_source_ref") or ""))
        try:
            size = path.stat().st_size
        except OSError:
            warnings.append(f"Could not stat selected lineage source: {path}")
            continue
        if bytes_read + size > max_bytes:
            warnings.append("Stopped before the next Codex lineage file at max byte cap.")
            break
        bytes_read += size
        records = _read_codex_activity(path, codex_home=codex_home, deadline=deadline)
        if records is None:
            warnings.append("Stopped within a Codex lineage file at max elapsed cap.")
            break
        inspected += 1
        for record in records:
            timestamp = str(record.get("timestamp_utc") or "")
            if anchor_timestamp and timestamp and timestamp <= anchor_timestamp:
                continue
            if boundary_timestamp and timestamp and timestamp >= boundary_timestamp:
                continue
            activities.append(record)
    activities.sort(key=lambda item: (str(item.get("timestamp_utc") or ""), str(item.get("source_ref") or "")))
    scan_state = index.get("scan_state") if isinstance(index.get("scan_state"), dict) else {}
    complete = not warnings and scan_state.get("complete", True) and bool(metas) and inspected == len(metas)
    coverage = {
        "status": "complete" if complete else "partial" if metas else "missing",
        "complete": complete,
        "records_after_anchor": len(activities),
        "warnings": warnings,
        "searched_files": inspected,
        "searched_bytes": bytes_read,
        "file_cap_hit": scan_state.get("file_cap_hit", False),
        "enumeration_complete": scan_state.get("complete", True),
        "omitted_source_file_lower_bound": scan_state.get("omitted_file_lower_bound", 0),
        "omitted_source_directory_lower_bound": scan_state.get("omitted_directory_lower_bound", 0),
    }
    interval = {
        "anchor": _public_anchor(selected),
        "boundary": boundary,
        "chronological_entry_count": len(activities),
        "chronological_activity": activities,
        "coverage": coverage,
        "omitted_ranges": [],
    }
    return {"selected_anchor": selected, "interval": interval, "coverage": coverage}


def _read_codex_activity(path: Path, *, codex_home: Path, deadline: float) -> list[dict] | None:
    activities: list[dict] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return activities
    with handle:
        for line_number, line in enumerate(handle, 1):
            if time.monotonic() >= deadline:
                return None
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
            timestamp = str(record.get("timestamp") or payload.get("timestamp") or "")
            activity_kind = None
            classification = None
            snippet = ""
            if payload.get("type") == "message" and str(payload.get("role") or "") in {"user", "assistant"}:
                role = str(payload.get("role"))
                content = payload.get("content")
                if isinstance(content, list):
                    snippet = " ".join(
                        str(item.get("text") or "") for item in content if isinstance(item, Mapping)
                    )
                else:
                    snippet = str(content or "")
                activity_kind = f"{role}_message"
                classification = "human_user_message" if role == "user" else "assistant_message"
            elif payload.get("type") in {"function_call", "custom_tool_call", "tool_call"}:
                snippet = str(payload.get("name") or "tool call")
                activity_kind = "tool_call"
                classification = "tool_action"
            elif payload.get("type") in {"function_call_output", "custom_tool_call_output", "tool_call_output"}:
                snippet = str(payload.get("output") or "tool output")
                activity_kind = "tool_output"
                classification = "tool_action_output"
            snippet = " ".join(snippet.split())[:1000]
            if not activity_kind or not snippet:
                continue
            activities.append({
                "timestamp_utc": timestamp,
                "activity_kind": activity_kind,
                "classification": classification,
                "snippet": snippet,
                "source_ref": _session_ref(path, codex_home, line_number),
                "openable_target": f"{path}:{line_number}",
            })
    return activities


def _recap_items(activity: list[dict]) -> list[str]:
    items: list[str] = []
    for entry in activity:
        snippet = str(entry.get("snippet") or "").strip()
        if not snippet:
            continue
        kind = str(entry.get("activity_kind") or "activity").replace("_", " ")
        items.append(f"{kind}: {snippet}")
        if len(items) >= DIRECT_RECAP_ITEM_CAP:
            break
    return _dedupe(items)[:DIRECT_RECAP_ITEM_CAP]


def _answer_text(*, selected: dict, interval: dict, recap_items: list[str], answer_status: str) -> str:
    anchor = str(selected.get("snippet") or "").strip()
    anchor_ref = str(selected.get("source_ref") or "").strip()
    start = str(selected.get("timestamp_utc") or "").strip()
    boundary = interval.get("boundary") if isinstance(interval.get("boundary"), dict) else {}
    end = str(boundary.get("timestamp_utc") or "now/latest available bounded history")
    if recap_items:
        recap = " ".join(f"- {item}" for item in recap_items)
    else:
        recap = "No compact activity text was available in the retrieved raw interval."
    qualifier = "" if answer_status == "answered-from-proof" else f" Result status: {answer_status}."
    return (
        f'Last parent/main human message: "{anchor}" ({start}; {anchor_ref}). '
        f"Retrieved interval: after that anchor through {end}. {recap}{qualifier}"
    )


def _answer_refs(selected: dict, interval: dict, activity: list[dict]) -> list[dict]:
    refs = []
    if selected:
        refs.append(
            {
                "target": selected.get("openable_target") or selected.get("source_ref"),
                "source_ref": selected.get("source_ref"),
                "kind": "selected_parent_human_anchor",
                "proof_surface": "primary_raw_history",
                "target_source_role": "human_user_message",
            }
        )
    for entry in activity[:DIRECT_RECAP_ITEM_CAP]:
        refs.append(
            {
                "target": entry.get("openable_target") or entry.get("source_ref"),
                "source_ref": entry.get("source_ref"),
                "kind": entry.get("activity_kind") or "post_anchor_activity",
                "proof_surface": "primary_raw_session_interval",
                "target_source_role": entry.get("classification"),
            }
        )
    boundary = interval.get("boundary") if isinstance(interval.get("boundary"), dict) else {}
    if boundary.get("source_ref") and all(item.get("source_ref") != boundary.get("source_ref") for item in refs):
        refs.append(
            {
                "target": boundary.get("openable_target") or boundary.get("source_ref"),
                "source_ref": boundary.get("source_ref"),
                "kind": "exclusive_status_boundary",
                "proof_surface": "primary_raw_history",
                "target_source_role": "human_user_message",
            }
        )
    return refs


def _public_anchor(anchor: Mapping[str, Any]) -> dict:
    return {
        key: anchor.get(key)
        for key in ("source_ref", "openable_target", "timestamp_utc", "snippet", "source_kind")
    }


def _missing_or_capped_scope(interval: dict, coverage: dict) -> dict:
    return {
        "status": "none" if coverage.get("complete") is True else coverage.get("status") or "unknown",
        "missing_logs": coverage.get("blocking_missing_logs") or coverage.get("missing_logs") or [],
        "omitted_activity_entry_count": coverage.get("omitted_activity_entry_count", 0),
        "omitted_ranges": interval.get("omitted_ranges") or [],
        "warnings": coverage.get("warnings") or [],
        "file_cap_hit": coverage.get("file_cap_hit", False),
        "omitted_source_file_lower_bound": coverage.get("omitted_source_file_lower_bound", 0),
        "omitted_source_directory_lower_bound": coverage.get("omitted_source_directory_lower_bound", 0),
    }


def _next_boundary(interval: dict, coverage: dict) -> dict:
    guidance = interval.get("next_read_guidance") if isinstance(interval.get("next_read_guidance"), dict) else {}
    return {
        "required_next_proof": "complete the named missing or capped raw interval scope",
        "commands": guidance.get("commands") or [],
        "missing_logs": coverage.get("blocking_missing_logs") or coverage.get("missing_logs") or [],
    }


def _boundary_answer(
    *,
    query: str,
    cwd: Path,
    route: dict,
    selection: dict,
    inventory: list[dict],
    limits: dict,
) -> dict:
    reason = str(selection.get("reason") or "history_scope_unavailable")
    boundary = selection.get("boundary") if isinstance(selection.get("boundary"), dict) else {}
    missing_sources = [str(boundary.get("missing"))] if boundary.get("missing") else []
    capped = "cap" in reason or "elapsed" in reason
    coverage_status = "capped" if capped else "missing"
    return {
        "schema_version": "direct_history_answer.v1",
        "query": query,
        "cwd": str(cwd),
        "selected_route": route,
        "execution": {
            "status": "executed",
            "command_family": "direct-history-answer",
            "internal_recovery": selection.get("recovery"),
        },
        "answer_status": selection.get("status") or "not_found",
        "answer": None,
        "refs_used": [],
        "proof_summary": [],
        "fail_closed_reason": reason,
        "next_proof_step": {
            "required_next_proof": "one parent/main session lineage edge or one unique eligible root human session in the selected history root",
            "candidate_refs": selection.get("candidate_refs") or [],
        },
        "source_role_explanation": None,
        "source_contract": {
            "built_from": "direct_history_answer",
            "answer_action_status": selection.get("status"),
            "promotion_status": "precise_unavailable_boundary",
            "selection_policy": "lineage_then_timestamp_utc_then_canonical_history_line; no semantic ranking",
            "current_or_side_boundary": boundary,
            "anchor": _public_anchor(selection.get("selected_anchor") or {}),
            "searched_interval": {
                "status": "not_started",
                "boundary": boundary,
                "chronological_entry_count": 0,
            },
            "coverage": {
                "status": coverage_status,
                "complete": False,
                "records_after_anchor": 0,
                "warnings": [reason],
            },
            "missing_or_capped_scope": {
                "status": coverage_status,
                "missing_logs": missing_sources,
                "missing_sources": missing_sources,
                "omitted_activity_entry_count": 0,
                "omitted_ranges": [],
                "warnings": [reason],
            },
            "source_inventory": inventory,
            "limits": limits,
            "visible_context_used_as_proof": False,
            "legacy_packet_or_execute_continuation_used": False,
        },
    }


def _source_inventory(args: Any, *, cwd: Path, selected_provider: str) -> list[dict]:
    inventory = []
    for spec in PROVIDERS:
        root = resolve_provider_root(args, provider=spec.name, cwd=cwd)
        usable, detail = _provider_root_status(spec.name, root)
        inventory.append(
            {
                "provider": spec.name,
                "formats": list(spec.formats),
                "root": str(root),
                "status": "available" if usable else detail,
                "selected_for_direct_answer": spec.name == selected_provider,
            }
        )
    return inventory


def _provider_root_status(provider: str, root: Path) -> tuple[bool, str]:
    if not root.exists():
        return False, "missing"
    if provider == "codex":
        if (root / "history.jsonl").is_file():
            return True, "available"
        return False, "missing_required_history"
    if provider == "claude":
        search_root = root / "projects" if (root / "projects").is_dir() else root
        warnings: list[str] = []
        if _bounded_session_paths(
            search_root,
            limit=1,
            deadline=time.monotonic() + 0.05,
            warnings=warnings,
        ):
            return True, "available"
        if warnings:
            return False, "root_detected_unverified"
        return False, "missing_required_history"
    return False, "root_detected_unverified"


def _select_direct_provider(args: Any, *, cwd: Path) -> str:
    explicit_codex = bool(getattr(args, "codex_home", None))
    explicit_claude = bool(getattr(args, "claude_home", None))
    if explicit_claude and not explicit_codex:
        return "claude"
    if explicit_codex and not explicit_claude:
        return "codex"
    if os.environ.get("CLAUDE_SESSION_ID") and not (os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")):
        return "claude"
    if os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    codex = resolve_provider_root(args, provider="codex", cwd=cwd)
    claude = resolve_provider_root(args, provider="claude", cwd=cwd)
    codex_usable = _provider_root_status("codex", codex)[0]
    claude_usable = _provider_root_status("claude", claude)[0]
    return "claude" if claude_usable and not codex_usable else "codex"


def _build_claude_direct_history_answer(
    args: Any,
    *,
    query: str,
    cwd: Path,
    route: dict,
    inventory: list[dict],
    limits: dict,
    deadline: float,
) -> dict:
    """Resolve a Claude JSONL turn chronologically without Codex assumptions."""

    root = resolve_provider_root(args, provider="claude", cwd=cwd)
    search_root = root / "projects" if (root / "projects").is_dir() else root
    max_files = max(0, int(getattr(args, "max_files", 200) or 0))
    max_bytes = max(0, int(getattr(args, "max_bytes", 100 * 1024 * 1024) or 0))
    warnings: list[str] = []
    scan_state: dict[str, Any] = {}
    paths = _bounded_session_paths(
        search_root,
        limit=max_files,
        deadline=deadline,
        warnings=warnings,
        scan_state=scan_state,
    ) if search_root.is_dir() else []
    bytes_read = 0
    matching: list[dict] = []
    capped = bool(warnings)
    for path in paths:
        if time.monotonic() >= deadline:
            capped = True
            warnings.append("Stopped Claude history reads at max elapsed cap.")
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if bytes_read + size > max_bytes:
            capped = True
            warnings.append("Stopped Claude history reads at max byte cap.")
            break
        bytes_read += size
        turns = _read_claude_turns(path, deadline=deadline)
        if turns is None:
            capped = True
            warnings.append("Stopped within a Claude JSONL read at max elapsed cap.")
            break
        query_indexes = [index for index, turn in enumerate(turns) if turn["role"] == "user" and _same_text(turn["text"], query)]
        if not query_indexes:
            continue
        query_index = query_indexes[-1]
        anchors = [turn for turn in turns[:query_index] if turn["role"] == "user"]
        if anchors:
            matching.append({"path": path, "turns": turns, "query_index": query_index, "anchor": anchors[-1]})
    if capped:
        selection = {
            "status": "partial",
            "reason": "claude_history_scan_stopped_at_explicit_byte_or_elapsed_cap",
            "boundary": {
                "root": str(search_root),
                "warnings": warnings,
                "bytes_read": bytes_read,
                **scan_state,
            },
            "recovery": "bounded_claude_history_scan_stopped_at_cap",
        }
        return {"ask_answer": _boundary_answer(query=query, cwd=cwd, route=route, selection=selection, inventory=inventory, limits=limits)}
    if len(matching) != 1:
        selection = {
            "status": "ambiguous" if len(matching) > 1 else "not_found",
            "reason": "multiple_claude_query_boundaries" if len(matching) > 1 else "claude_query_boundary_or_prior_human_turn_not_found",
            "boundary": {"root": str(search_root), "matching_files": len(matching)},
            "candidate_refs": [f"{item['path']}:{item['anchor']['line']}" for item in matching[:8]],
            "recovery": "bounded_claude_same_root_scan_exhausted",
        }
        return {"ask_answer": _boundary_answer(query=query, cwd=cwd, route=route, selection=selection, inventory=inventory, limits=limits)}
    match = matching[0]
    anchor = match["anchor"]
    query_turn = match["turns"][match["query_index"]]
    activity = [turn for turn in match["turns"] if anchor["line"] < turn["line"] < query_turn["line"]]
    anchor_ref = f"{match['path']}:{anchor['line']}"
    refs = [{"target": f"{match['path']}:{anchor['line']}", "source_ref": anchor_ref, "kind": "selected_main_human_anchor", "proof_surface": "primary_raw_history", "target_source_role": "human_user_message"}]
    recap = []
    for turn in activity[:DIRECT_RECAP_ITEM_CAP]:
        ref = f"{match['path']}:{turn['line']}"
        refs.append({"target": f"{match['path']}:{turn['line']}", "source_ref": ref, "kind": f"{turn['role']}_message", "proof_surface": "primary_raw_session_interval", "target_source_role": turn["role"]})
        recap.append(f"{turn['role']} message: {turn['text']}")
    public_anchor = {"source_ref": anchor_ref, "openable_target": f"{match['path']}:{anchor['line']}", "timestamp_utc": anchor.get("timestamp"), "snippet": anchor["text"], "source_kind": "claude_projects"}
    coverage = {"status": "complete", "complete": True, "records_after_anchor": len(activity), "warnings": []}
    contract = {
        "built_from": "direct_history_answer", "answer_action_status": "answered-from-proof",
        "promotion_status": "direct_lineage_chronology_and_bounded_interval",
        "selection_policy": "provider_native_session_chronology; no semantic ranking",
        "anchor": public_anchor,
        "searched_interval": {"anchor": public_anchor, "boundary": {"source_ref": f"{match['path']}:{query_turn['line']}", "timestamp_utc": query_turn.get("timestamp")}, "chronological_entry_count": len(activity)},
        "coverage": coverage, "missing_or_capped_scope": _missing_or_capped_scope({}, coverage),
        "source_inventory": inventory, "limits": limits,
        "native_source_refs": [item["source_ref"] for item in refs],
        "visible_context_used_as_proof": False, "legacy_packet_or_execute_continuation_used": False,
    }
    answer = _answer_text(selected=public_anchor, interval=contract["searched_interval"], recap_items=recap, answer_status="answered-from-proof")
    return {"ask_answer": {"schema_version": "direct_history_answer.v1", "query": query, "cwd": str(cwd), "selected_route": route, "execution": {"status": "executed", "command_family": "direct-history-answer", "internal_recovery": "bounded_claude_same_file_chronology"}, "answer_status": "answered-from-proof", "answer": answer, "refs_used": refs, "proof_summary": recap, "fail_closed_reason": None, "next_proof_step": None, "source_role_explanation": None, "source_contract": contract}}


def _read_claude_turns(path: Path, *, deadline: float) -> list[dict] | None:
    turns: list[dict] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return turns
    with handle:
        for line_number, line in enumerate(handle, 1):
            if time.monotonic() >= deadline:
                return None
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            message = record.get("message") if isinstance(record.get("message"), Mapping) else record
            role = str(message.get("role") or record.get("type") or "").casefold()
            if role not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if isinstance(content, list):
                text = " ".join(str(item.get("text") or "") for item in content if isinstance(item, Mapping))
            else:
                text = str(content or message.get("text") or "")
            text = " ".join(text.split())
            if text:
                turns.append({"role": role, "text": text, "timestamp": record.get("timestamp"), "line": line_number})
    return turns


def _same_text(left: str, right: str) -> bool:
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


def _public_route(route: Mapping[str, Any]) -> dict:
    return {
        key: route.get(key)
        for key in ("route_family", "chosen_route", "detected_intent", "activation_reason")
        if route.get(key) is not None
    }


def _limits(args: Any) -> dict:
    limits = {
        key: getattr(args, key, None)
        for key in ("max_files", "max_bytes", "max_matches", "max_elapsed", "hard_cap_files", "hard_cap_bytes")
    }
    limits["direct_anchor_candidate_cap"] = DIRECT_ANCHOR_CANDIDATE_CAP
    return limits


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
