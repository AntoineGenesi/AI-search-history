from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from .history_home import history_home_from_args
from .providers import PROVIDERS, resolve_provider_root
from .status_anchor import (
    _canonical_history_human_rows,
    _canonical_row_matches_exact_status_query_boundary,
    _fast_lineage_for_anchor,
    _hash_value,
    _parent_thread_lineage_rejection_reason,
    _session_lineage_metadata,
    build_status_anchor_payload,
    is_status_question,
)


DIRECT_ANCHOR_CANDIDATE_CAP = 500
DIRECT_RECAP_ITEM_CAP = 8


def build_direct_history_answer_payload(args: Any, *, route_payload: dict | None = None) -> dict:
    """Answer latest-status questions without packet or continuation choreography."""

    query = str(getattr(args, "query", "") or "").strip()
    cwd = Path(getattr(args, "cwd", ".")).expanduser().resolve()
    codex_home = history_home_from_args(args, provider="codex")
    route = _public_route((route_payload or {}).get("ask_router") or {})
    inventory = _source_inventory(args, cwd=cwd)
    limits = _limits(args)

    selection = _select_latest_parent_anchor(
        query=query,
        cwd=cwd,
        codex_home=codex_home,
        candidate_cap=DIRECT_ANCHOR_CANDIDATE_CAP,
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

    direct_args = copy.copy(args)
    direct_args.query = query
    direct_args.parent_thread = True
    direct_args.complete_interval = True
    direct_args.anchor_ref = selection["selected_anchor"]["source_ref"]
    # The exact anchor bypasses the legacy answer-mode preflight blocker. Answer
    # mode retains the bounded raw interval in memory; the direct surface below
    # emits only the useful answer/refs/coverage fields, never its legacy packet.
    direct_args.format = "answer"
    direct_args.receiver_answer_mode = False
    report = build_status_anchor_payload(direct_args)["status_anchor"]
    selected = report.get("selected_anchor") if isinstance(report.get("selected_anchor"), dict) else {}
    interval = (
        report.get("post_anchor_interval_reconstruction")
        if isinstance(report.get("post_anchor_interval_reconstruction"), dict)
        else {}
    )
    coverage = interval.get("coverage") if isinstance(interval.get("coverage"), dict) else {}
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
) -> dict:
    history_path = codex_home / "history.jsonl"
    if not history_path.is_file():
        return {
            "status": "not_found",
            "reason": "codex_history_missing",
            "boundary": {"missing": str(history_path)},
            "recovery": "same_root_history_and_sessions_exhausted",
        }
    rows = _canonical_history_human_rows(history_path, query=query)
    if not rows:
        return {
            "status": "not_found",
            "reason": "no_human_history_rows",
            "boundary": {"history_path": str(history_path)},
            "recovery": "same_root_history_and_sessions_exhausted",
        }

    rows.sort(key=_chronology_key)
    current_lineage = _current_environment_lineage(codex_home=codex_home, cwd=cwd, max_files=candidate_cap)
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
        _fast_lineage_for_anchor(boundary, codex_home=codex_home, cwd=cwd)
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
            cwd=cwd,
            codex_home=codex_home,
            candidate_cap=candidate_cap,
        )
        if direct is not None:
            return {
                "status": "selected",
                "reason": "current_or_side_session_parent_thread_id_then_latest_human_chronology",
                "selected_anchor": direct,
                "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
                "recovery": "direct_parent_edge_resolved_inside_single_invocation",
            }
        return {
            "status": "not_found",
            "reason": "direct_parent_thread_id_has_no_eligible_human_history_row",
            "boundary": _boundary_summary(boundary, boundary_lineage, boundary_source),
            "recovery": "same_root_direct_parent_rows_exhausted",
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
        lineage = _fast_lineage_for_anchor(row, codex_home=codex_home, cwd=cwd)
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
    }


def _latest_matching_parent_row(
    rows: list[dict],
    *,
    parent_hash: str,
    cwd: Path,
    codex_home: Path,
    candidate_cap: int,
) -> dict | None:
    for row in reversed(rows[-candidate_cap:]):
        if _source_identifier_hash(row) != parent_hash:
            continue
        lineage = _fast_lineage_for_anchor(row, codex_home=codex_home, cwd=cwd)
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


def _current_environment_lineage(*, codex_home: Path, cwd: Path, max_files: int) -> dict:
    raw_ids = [os.environ.get("CODEX_THREAD_ID"), os.environ.get("CODEX_SESSION_ID")]
    expected = {_hash_value(value) for value in raw_ids if value}
    expected.discard(None)
    sessions_root = codex_home / "sessions"
    if not expected or not sessions_root.is_dir():
        return {"status": "missing", "reason": "current_session_environment_not_available_in_selected_root"}
    try:
        paths = sorted(sessions_root.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return {"status": "missing", "reason": "current_session_root_enumeration_failed"}
    for path in paths[:max_files]:
        meta = _session_lineage_metadata(path, cwd=cwd)
        observed = {meta.get("session_id_hash"), meta.get("raw_thread_id_hash")}
        if expected & observed:
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
        "fail_closed_reason": selection.get("reason"),
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
            "current_or_side_boundary": selection.get("boundary"),
            "source_inventory": inventory,
            "limits": limits,
            "visible_context_used_as_proof": False,
            "legacy_packet_or_execute_continuation_used": False,
        },
    }


def _source_inventory(args: Any, *, cwd: Path) -> list[dict]:
    inventory = []
    for spec in PROVIDERS:
        root = resolve_provider_root(args, provider=spec.name, cwd=cwd)
        inventory.append(
            {
                "provider": spec.name,
                "formats": list(spec.formats),
                "root": str(root),
                "status": "available" if root.exists() else "missing",
                "selected_for_direct_answer": spec.name == "codex",
            }
        )
    return inventory


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
