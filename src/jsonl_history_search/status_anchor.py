from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .conversation_records import (
    ASSISTANT_OR_AGENT_MESSAGE,
    COMPACTED_HISTORY_SUMMARY,
    HUMAN_AUTHORED_USER_MESSAGE,
    TOOL_ACTION_OUTPUT,
    classify_conversation_record,
    extract_human_ask,
)
from .latest_user_messages import MIRROR_CORRELATION_WINDOW_SECONDS, latest_user_messages
from .timeutil import parse_datetime


DEFAULT_STATUS_QUERY = "what happened since my last message?"
SCHEMA_VERSION = 1

SOURCE_MODES = (
    "ground_truth_chat_logs",
    "artifact_assisted_context",
    "post_hoc_diagnostic",
)

SOURCE_ROLE_LABELS = (
    "primary_raw_chat_log",
    "primary_tool_or_command_trace",
    "legitimate_pre_existing_artifact",
    "post_hoc_answer_key_or_same_loop_echo",
    "diagnostic_only",
)

STATUS_QUERY_PATTERNS = (
    "what happened since",
    "what has happened since",
    "what happened before",
    "what has happened before",
    "what did you do while i was away",
    "while i was away",
    "status since",
    "since last time",
    "before the last time",
    "before hte last time",
    "time before that",
    "what changed after",
    "what happened in the main thread",
    "message before",
    "last substantive instruction",
    "last substantive message",
    "last real instruction",
)

ADMIN_STATUS_MARKERS = (
    "what happened since",
    "what has happened since",
    "what happened before",
    "what has happened before",
    "what did you do while i was away",
    "while i was away",
    "what happened in the main thread",
    "status since",
    "since last time",
    "repo-state",
    "process-event",
    "nothing happened",
    "side-thread",
    "side thread",
    "latest admin",
    "last message",
    "last substantive instruction",
    "last substantive message",
    "last real instruction",
    "message before",
    "last actual message",
    "not the status question",
    "too narrow",
    "made no sense",
    "wrong anchor",
    "use the updated skill",
    "updated skill",
    "before the last answer",
    "time before the last answer",
    "before the last time",
    "before hte last time",
    "time before that",
    "previous relevant time window",
)

NON_ANCHOR_CONTEXT_MARKERS = (
    "subagent_notification",
    "subagent notification",
    "tool output",
    "tool_result",
    "tool result",
    "function_call_output",
    "function call output",
    "compacted history summary",
    "inherited summary",
    "inherited conversation summary",
    "current-session handoff",
    "current session handoff",
    "handoff snippet",
    "boundary text",
    "side-thread boundary",
    "side conversation boundary",
    "conversation boundary",
    "<environment_context",
    "</environment_context>",
)

WORKER_PROMPT_MARKERS = (
    "you are a read-only pressure-test agent",
    "you are jsh-",
    "read-only pressure-test agent",
    "read-only pressure agent",
    "pressure-test agent",
    "pressure test agent",
    "pressure agent",
    "you are the jsh-",
    "you are the focused fix worker",
    "you are the focused fix implementer",
    "you are the high-reasoning implementer",
    "you are the implementer/runner",
    "you are the independent reviewer",
    "you are the independent rereviewer",
    "you are the jsonl_current_free lane",
    "you are the manual_default lane",
    "you are implementing jsh-",
    "implement jsh-",
    "you are reviewing branch",
    "you are not alone in the codebase",
    "implementation worker",
    "you are the implementer worker",
    "implementer for jsh-",
    "isolated implementer",
    "independent reviewer for jsh-",
    "independent rereviewer for jsh-",
    "independent review for jsh-",
    "jsonl_layered_current lane",
    "reviewer worker",
    "reviewer update:",
    "review-blocking fix",
    "review artifact fix needed",
    "orchestrator reviewer",
    "read-only code exploration",
    "code exploration for jsh-",
    "status nudge from coordinator",
    "read-only review",
    "independent review task",
    "coordinator evidence for jsh-",
    "coordinator found a concrete",
    "focused fix required",
    "final rereview for jsh-",
    "please re-review jsh-",
    "rereview jsh-",
    "review fix needed",
    "review target remains",
    "repo merge gate",
    "value_verdict",
    "your review currently",
    "your latest blocker:",
    "scope: verify",
    "status checkpoint please",
    "inspect changed files",
    "use implementer discipline",
    "acceptance criteria:",
    "keep all edits in the task worktree",
    "read first:",
    "role contract:",
    "read the review artifact",
    "read the updated review artifact first",
    "review verdict is failed",
    "setup-agent",
    "setup agent",
    "same branch. do not start a new task",
    "same worktree and branch",
    "second rereview failed",
    "you are not implementing product code",
    "not implementing product code",
    "do not edit tracked files",
    "do not modify tracked files",
    "write artifacts only under",
    "private artifacts only under",
    "your job is one slice only",
    "do not judge all prompt families",
    "select the global next task",
    "<turn_aborted>",
)

STATUS_BOUNDARY_PROMPT_MARKERS = WORKER_PROMPT_MARKERS + (
    "adversarial smoke",
    "after a failed review",
    "current head includes failed review artifact",
    "do not edit the parent checkout",
    "do not edit the review artifact",
    "failed review artifact",
    "fix requirements:",
    "fsm state:",
    "live cli adversarial smoke",
    "post-status spawned worker",
    "pressure artifact",
    "pressure prompt",
    "review blocker",
    "review blocker to fix",
    "reviewer-cited path",
    "status-shaped pressure",
    "task worktree",
    "worker/pressure prompt",
)

SUBSTANTIVE_WORK_MARKERS = (
    "keep working",
    "implement",
    "execute",
    "fix",
    "build",
    "ship",
    "product delta",
    "semantic-debug",
    "semantic debug",
    "route",
    "workflow",
    "case-002",
    "case 002",
    "case-003",
    "jsh-",
)

KNOWN_WORK = (
    {
        "id": "JSH-998",
        "slug": "jsh-998",
        "value_kind": "behavior_delta",
        "summary": "JSH-998 changed route behavior: generic persistent-coordinator wording now anchors to the intended correction window instead of later adjacent records.",
        "artifact_terms": ("route_anchoring_improved", "generic persistent-coordinator", "correction window"),
    },
    {
        "id": "JSH-999",
        "slug": "jsh-999",
        "value_kind": "behavior_delta",
        "summary": "JSH-999 changed command surface: recovery-first packets now present one bundled follow-next-reads command instead of encouraging multiple read-more fallbacks.",
        "artifact_terms": ("handoff_churn_reduced", "single saved", "follow-next-reads"),
    },
    {
        "id": "JSH-1000",
        "slug": "jsh-1000",
        "value_kind": "non_delta",
        "summary": "JSH-1000 was contract/support work: it locked retained hard cases into a committed-safe product contract rather than changing retrieval behavior directly.",
        "artifact_terms": ("hard-case product contract", "committed-safe product contract", "retained hard cases"),
    },
    {
        "id": "JSH-1001",
        "slug": "jsh-1001",
        "value_kind": "non_delta",
        "summary": "JSH-1001 was measurement/result work: the fresh rerun preserved source-grounded answers but still showed workflow churn blocked.",
        "artifact_terms": ("still_churn_blocked", "workflow churn", "fresh agent rerun"),
    },
    {
        "id": "JSH-1002",
        "slug": "jsh-1002",
        "value_kind": "non_delta",
        "summary": "JSH-1002 was claim-boundary/docs work: it reset supported claims after JSH-1001 rather than fixing product behavior.",
        "artifact_terms": ("claim boundary", "claim-boundary", "does not add a new benchmark"),
    },
)


def build_status_anchor_payload(args: Any) -> dict:
    default_tz = _timezone_from_args(args)
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    cwd = Path(args.cwd).expanduser().resolve()
    query = getattr(args, "query", None) or DEFAULT_STATUS_QUERY
    limit = getattr(args, "limit", None) or getattr(args, "max_matches", 40)
    snippet_chars = max(0, getattr(args, "snippet_chars", 280))
    latest = latest_user_messages(
        codex_home=codex_home,
        default_tz=default_tz,
        limit=limit,
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_elapsed=args.max_elapsed,
        snippet_chars=snippet_chars,
    )
    return {
        "status_anchor": status_anchor_report(
            query=query,
            latest=latest,
            cwd=cwd,
            codex_home=codex_home,
            started=time.monotonic(),
        )
    }


def status_anchor_report(
    *,
    query: str,
    latest: dict,
    cwd: Path,
    codex_home: Path | None = None,
    started: float | None = None,
) -> dict:
    latest_messages = latest.get("messages", [])
    target_window = _target_window_from_query(query)
    status_as_of_boundary = _status_as_of_boundary_from_messages(query, latest_messages)
    audit_run_requested = _audit_run_requested(query, target_window)
    canonical_parent_main_interval = _canonical_parent_main_status_interval(
        query=query,
        codex_home=codex_home,
        cwd=cwd,
        status_as_of_boundary=status_as_of_boundary,
    )
    prior_request_occurrence = _prior_request_occurrence_recovery(
        query=query,
        latest_messages=latest_messages,
        codex_home=codex_home,
        status_as_of_boundary=status_as_of_boundary,
    )
    canonical_parent_main_fallback = _canonical_parent_main_fallback_from_interval(canonical_parent_main_interval)
    parent_thread_side_boundary = canonical_parent_main_interval.get("parent_thread_side_boundary") or {
        "status": "not_applicable",
        "reason": "query_did_not_require_literal_parent_thread_boundary",
    }
    anchor_candidates = _rank_anchor_candidates(
        latest_messages,
        query=query,
        target_window=target_window,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
    )
    anchor_candidates = _apply_parent_thread_lineage_to_ranked_candidates(
        query=query,
        candidates=anchor_candidates,
        cwd=cwd,
        codex_home=codex_home,
    )
    anchor_decision = _select_anchor_with_boundary(
        query=query,
        messages=latest_messages,
        candidates=anchor_candidates,
        target_window=target_window,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
        prior_request_occurrence=prior_request_occurrence,
        canonical_parent_main_interval=canonical_parent_main_interval,
        canonical_parent_main_fallback=canonical_parent_main_fallback,
    )
    if anchor_decision.get("selected_anchor"):
        anchor_decision["selected_anchor"] = _attach_logical_message_identity(
            anchor_decision["selected_anchor"],
            anchor_candidates,
        )
    prior_request_occurrence = anchor_decision.get("prior_request_occurrence") or prior_request_occurrence
    status_as_of_boundary = anchor_decision.get("status_as_of_boundary") or status_as_of_boundary
    selected_anchor = _with_read_more_command(anchor_decision.get("selected_anchor"), cwd=cwd, codex_home=codex_home)
    if selected_anchor:
        selected_anchor.update(_anchor_decision_fields(anchor_decision, selected_anchor))
        selected_anchor = _enrich_selected_parent_thread_lineage(
            selected_anchor,
            anchor_decision=anchor_decision,
            cwd=cwd,
            codex_home=codex_home,
        )
    selected_anchor, anchor_decision = _demote_unproven_parent_main_selected_anchor(
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
    )
    selected_anchor, anchor_decision, parent_main_anchor_shortlist = _apply_parent_main_anchor_shortlist_contract(
        query=query,
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
        anchor_candidates=anchor_candidates,
        canonical_parent_main_interval=canonical_parent_main_interval,
        cwd=cwd,
        codex_home=codex_home,
    )
    anchor_decision = {
        **anchor_decision,
        "selected_anchor": None,
        "anchor_ref": selected_anchor.get("source_ref") if selected_anchor else None,
    }
    session_activity = _session_activity_trace(
        selected_anchor,
        anchor_decision,
        cwd=cwd,
        codex_home=codex_home,
        target_window=target_window,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
    )
    selected_anchor, anchor_decision, session_activity = _demote_unqualified_out_of_band_anchor_without_activity(
        query=query,
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
        session_activity=session_activity,
    )
    artifacts = _collect_work_artifacts(
        cwd,
        anchor_timestamp_utc=session_activity.get("anchor_timestamp_utc"),
        target_window=target_window,
        status_as_of_timestamp_utc=status_as_of_boundary.get("timestamp_utc") if status_as_of_boundary else None,
        audit_run_requested=audit_run_requested,
    )
    current_value_items = [item for item in artifacts["value_items"] if item.get("current_window_evidence")]
    behavior_deltas = [item for item in current_value_items if item["value_kind"] == "behavior_delta"]
    non_deltas = [item for item in current_value_items if item["value_kind"] == "non_delta"]
    diagnostic_value_items = [item for item in artifacts["value_items"] if not item.get("current_window_evidence")]
    unresolved_blockers = _unresolved_blockers(artifacts, current_window_only=True)
    next_reads = _next_reads(
        selected_anchor,
        anchor_decision,
        session_activity,
        artifacts,
        cwd=cwd,
        codex_home=codex_home,
        target_window=target_window,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
    )
    next_reads = _with_source_role_labels(next_reads)
    source_tiers = _source_tiers(next_reads)
    follow_through_plan = _follow_through_plan(
        source_tiers=source_tiers,
        target_window=target_window,
        session_activity=session_activity,
    )
    answer_readiness = _answer_readiness_contract(
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
        session_activity=session_activity,
        source_tiers=source_tiers,
        follow_through_plan=follow_through_plan,
        target_window=target_window,
    )
    answer_brief = _answer_brief(
        selected_anchor=selected_anchor,
        session_activity=session_activity,
        behavior_deltas=behavior_deltas,
        non_deltas=non_deltas,
        blockers=unresolved_blockers,
        answer_allowed=answer_readiness["answer_allowed"],
    )
    fail_closed_guidance = _fail_closed_guidance(
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
        session_activity=session_activity,
        answer_readiness=answer_readiness,
    )
    clarification_request = _clarification_request(
        selected_anchor=selected_anchor,
        anchor_decision=anchor_decision,
        answer_readiness=answer_readiness,
        fail_closed_guidance=fail_closed_guidance,
    )
    suppressed_support = _suppressed_answer_facing_support(
        cwd,
        target_window=target_window,
        audit_run_requested=audit_run_requested,
    )
    blind_spots = []
    if not selected_anchor:
        blind_spots.append("No substantive anchor selected from the searched latest-message scope.")
    if session_activity["status"] not in {"raw_session_trace_found", "partial_raw_session_trace_found"}:
        blind_spots.append("Raw session activity trace was not found; do not answer from commits/repo state alone.")
    if not artifacts["value_items"]:
        blind_spots.append("No known JSH-998 through JSH-1002 task/report/review artifacts were found under --cwd.")
    if not artifacts["commits"]:
        blind_spots.append("No secondary git commits were found; do not substitute git history for the raw JSONL trace.")
    elapsed = None if started is None else round(time.monotonic() - started, 6)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if selected_anchor else "anchor_ambiguous",
        "query": query,
        "routing": {
            "route": "what_happened_since_status_anchor",
            "natural_status_triggered": is_status_question(query),
            "jsonl_first": True,
            "built_on": "latest_user_messages",
            "no_spawned_agents": True,
            "raw_jsonl_session_trace": session_activity["status"],
            "commits_and_repo_state_are_secondary": True,
            "commit_only_answers_allowed": False,
            "context_boundary": anchor_decision["context_boundary"],
            "anchor_scope": anchor_decision["anchor_scope"],
            "anchor_reason": anchor_decision["anchor_reason"],
            "anchor_ref": anchor_decision["anchor_ref"],
            "target_window": target_window,
            "status_as_of_boundary": status_as_of_boundary,
            "audit_run_requested": audit_run_requested,
        },
        "source_scope": latest.get("source_scope"),
        "summary": {
            "anchor_candidate_count": len(anchor_candidates),
            "selected_anchor_available": selected_anchor is not None,
            "work_product_count": len(artifacts["tasks"]) + len(artifacts["reports"]) + len(artifacts["reviews"]),
            "current_window_work_product_count": sum(
                1
                for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
                for item in collection
                if item.get("current_window_evidence")
            ),
            "diagnostic_optional_work_product_count": sum(
                1
                for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
                for item in collection
                if item.get("source_tier") == "diagnostic_optional"
            ),
            "behavior_delta_count": len(behavior_deltas),
            "non_delta_count": len(non_deltas),
            "diagnostic_value_item_count": len(diagnostic_value_items),
            "target_window_work_product_count": sum(
                1
                for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
                for item in collection
                if item.get("target_window_evidence")
            ),
            "suppressed_answer_facing_artifact_count": suppressed_support.get("count", 0),
            "answer_allowed": answer_readiness["answer_allowed"],
            "answer_readiness_status": answer_readiness["status"],
            "missing_required_answer_facet_count": len(answer_readiness["missing_required_answer_facets"]),
            "unresolved_blocker_count": len(unresolved_blockers),
            "raw_session_file_count": session_activity["summary"]["session_file_count"],
            "raw_session_record_count": session_activity["summary"]["records_after_anchor"],
            "spawned_session_call_count": session_activity["summary"]["spawned_session_call_count"],
            "elapsed_seconds": elapsed,
            "context_boundary": anchor_decision["context_boundary"],
            "anchor_scope": anchor_decision["anchor_scope"],
            "anchor_reason": anchor_decision["anchor_reason"],
            "status_as_of_boundary_available": status_as_of_boundary is not None,
            "prior_request_occurrence_status": prior_request_occurrence.get("status"),
            "canonical_parent_main_interval_status": canonical_parent_main_interval.get("status"),
            "canonical_parent_main_fallback_status": canonical_parent_main_fallback.get("status"),
            "parent_main_anchor_shortlist_status": parent_main_anchor_shortlist.get("status"),
            "parent_main_anchor_shortlist_candidate_count": parent_main_anchor_shortlist.get("candidate_count", 0),
            "parent_main_anchor_shortlist_high_confidence_count": parent_main_anchor_shortlist.get(
                "canonical_high_confidence_count", 0
            ),
            "parent_thread_side_boundary_status": parent_thread_side_boundary.get("status"),
        },
        "anchor_candidates": anchor_candidates,
        "parent_main_anchor_shortlist": parent_main_anchor_shortlist,
        "selected_anchor": selected_anchor,
        "anchor_decision": anchor_decision,
        "session_activity": session_activity,
        "evidence_contract": _evidence_contract(),
        "next_reads": next_reads,
        "source_tiers": source_tiers,
        "follow_through_plan": follow_through_plan,
        "answer_readiness": answer_readiness,
        "required_answer_facets": answer_readiness["required_answer_facets"],
        "target_window": target_window,
        "status_as_of_boundary": status_as_of_boundary,
        "prior_request_occurrence_recovery": prior_request_occurrence,
        "canonical_parent_main_status_interval": canonical_parent_main_interval,
        "canonical_parent_main_fallback": canonical_parent_main_fallback,
        "parent_thread_side_boundary": parent_thread_side_boundary,
        "suppressed_support": suppressed_support,
        "clarification_request": clarification_request,
        "fail_closed_guidance": fail_closed_guidance,
        "agent_instruction": _agent_instruction(selected_anchor, session_activity, next_reads),
        "work_products": {
            "tasks": artifacts["tasks"],
            "reports": artifacts["reports"],
            "reviews": artifacts["reviews"],
            "commits": artifacts["commits"],
        },
        "behavior_deltas": behavior_deltas,
        "non_deltas": non_deltas,
        "diagnostic_optional_work_products": diagnostic_value_items,
        "unresolved_blockers": unresolved_blockers,
        "answer_brief": answer_brief,
        "blind_spots": blind_spots,
        "privacy": {
            "raw_transcript_dump_included": False,
            "raw_text_included": True,
            "raw_session_activity_snippets_included": False,
            "snippets_are_compact": True,
            "source_refs_included": True,
        },
        "latest_user_messages_summary": latest.get("summary", {}),
        "warnings": latest.get("sources", {}).get("warnings", []),
    }


def is_status_question(query: str) -> bool:
    normalized = _normalize(query)
    return any(pattern in normalized for pattern in STATUS_QUERY_PATTERNS)


def _rank_anchor_candidates(
    messages: list[dict],
    *,
    query: str,
    target_window: dict | None = None,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
) -> list[dict]:
    candidates = []
    status_cutoff = (status_as_of_boundary or {}).get("timestamp_utc") or _status_query_cutoff(query, messages)
    for index, message in enumerate(messages):
        text = str(message.get("snippet") or "")
        timestamp = message.get("timestamp_utc")
        score, classification, decision, rationale = _score_anchor(
            text,
            index,
            timestamp_utc=str(timestamp or ""),
            status_cutoff=status_cutoff,
        )
        candidates.append(
            {
                "rank": 0,
                "source_ref": message.get("source_ref"),
                "source_kind": message.get("source_kind"),
                "source_identifier": message.get("source_identifier"),
                "openable_source_ref": message.get("openable_source_ref"),
                "openable_target": message.get("openable_target"),
                "timestamp_utc": message.get("timestamp_utc"),
                "local_timestamp": message.get("local_timestamp"),
                "message_index": index,
                "thread_key": message.get("thread_key"),
                "thread_keys": _message_thread_keys(message),
                "mirror_sources": message.get("mirror_sources") or [],
                "logical_message_id": message.get("logical_message_id"),
                "correlation_status": message.get("correlation_status"),
                "correlated_session_identity": message.get("correlated_session_identity"),
                "canonical_authority": message.get("canonical_authority"),
                "session_activity_location": message.get("session_activity_location"),
                "correlation_diagnostics": message.get("correlation_diagnostics") or [],
                "snippet": text,
                "classification": classification,
                "score": score,
                "decision": decision,
                "rationale": rationale,
                "target_window_hits": _target_window_hits(text, target_window),
                "outside_target_task_ids": _outside_target_task_ids(text, target_window),
                "newer_than_status_as_of": _is_newer_than_status_as_of(
                    str(timestamp or ""),
                    status_as_of_boundary=status_as_of_boundary,
                    audit_run_requested=audit_run_requested,
                ),
            }
        )
    candidates.sort(key=lambda item: (item["score"], item.get("timestamp_utc") or ""), reverse=True)
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def _status_query_cutoff(query: str, messages: list[dict]) -> str | None:
    current_status = _current_status_message(messages, query=query)
    if current_status:
        return str(current_status.get("timestamp_utc") or "") or None
    if _requires_query_equivalent_status_boundary(query):
        return None
    for message in messages:
        timestamp = str(message.get("timestamp_utc") or "")
        if (
            timestamp
            and is_status_question(str(message.get("snippet") or ""))
            and not _is_status_boundary_prompt_noise(message)
        ):
            return timestamp
    return None


def _status_as_of_boundary_from_messages(query: str, messages: list[dict]) -> dict | None:
    if not is_status_question(query):
        return None
    status_message = _current_status_message(messages, query=query)
    if not status_message:
        return None
    boundary_source = status_message
    if status_message.get("source_kind") != "codex_history":
        boundary_source = _history_mirror_candidate(status_message) or status_message
    timestamp = str(status_message.get("timestamp_utc") or "")
    if not timestamp:
        return None
    return {
        "kind": "current_status_question_row",
        "source_ref": boundary_source.get("source_ref"),
        "source_kind": boundary_source.get("source_kind"),
        "source_identifier": boundary_source.get("source_identifier"),
        "openable_source_ref": boundary_source.get("openable_source_ref"),
        "openable_target": boundary_source.get("openable_target"),
        "timestamp_utc": timestamp,
        "local_timestamp": status_message.get("local_timestamp"),
        "message_index": status_message.get("message_index"),
        "thread_keys": status_message.get("thread_keys") or [],
        "logical_message_id": boundary_source.get("logical_message_id"),
        "canonical_authority": boundary_source.get("canonical_authority"),
        "session_activity_location": boundary_source.get("session_activity_location"),
        "snippet": status_message.get("snippet"),
        "boundary_policy": (
            "For non-audit status questions, candidate selection, raw session activity, source tiers, "
            "follow-through, and answer-facing support must not use evidence newer than this row."
        ),
    }


def _audit_run_requested(query: str, target_window: dict | None) -> bool:
    if target_window and target_window.get("audit_run_requested"):
        return True
    normalized = _normalize(query)
    return any(marker in normalized for marker in _RUN_AUDIT_MARKERS)


def _is_newer_than_status_as_of(
    timestamp_utc: str,
    *,
    status_as_of_boundary: dict | None,
    audit_run_requested: bool,
) -> bool:
    if audit_run_requested or not timestamp_utc or not status_as_of_boundary:
        return False
    status_timestamp = parse_datetime(str(status_as_of_boundary.get("timestamp_utc") or ""), timezone.utc)
    timestamp = parse_datetime(timestamp_utc, timezone.utc)
    if status_timestamp is None or timestamp is None:
        return False
    return timestamp.astimezone(timezone.utc) > status_timestamp.astimezone(timezone.utc)


def _score_anchor(text: str, index: int, *, timestamp_utc: str = "", status_cutoff: str | None = None) -> tuple[int, str, str, str]:
    normalized = _normalize(text)
    recency_penalty = min(index, 20)
    if not normalized:
        return -100, "empty", "rejected", "empty latest-message row"
    if any(marker in normalized for marker in NON_ANCHOR_CONTEXT_MARKERS):
        return -70 - recency_penalty, "non_human_boundary_or_tool_context", "rejected", "boundary/tool/subagent/inherited context is not a user anchor"
    if any(marker in normalized for marker in WORKER_PROMPT_MARKERS) or _looks_like_task_prompt(normalized):
        return -55 - recency_penalty, "worker_or_reviewer_prompt", "rejected", "worker/reviewer prompt is not a human status anchor"
    if any(marker in normalized for marker in ADMIN_STATUS_MARKERS):
        return -30 - recency_penalty, "admin_or_status_turn", "rejected", "admin/status turn is useful context but a poor unattended-work anchor"
    if status_cutoff and timestamp_utc and timestamp_utc > status_cutoff:
        return -40 - recency_penalty, "post_status_context", "rejected", "newer than the status question; context, not the since-anchor"
    work_hits = [marker for marker in SUBSTANTIVE_WORK_MARKERS if marker in normalized]
    if work_hits:
        score = 70 + min(len(work_hits), 5) * 4 - recency_penalty
        return score, "substantive_work_anchor", "selected_candidate", f"substantive work wording matched: {', '.join(work_hits[:3])}"
    if len(normalized.split()) >= 12:
        return 20 - recency_penalty, "ordinary_human_turn", "fallback_candidate", "ordinary human turn; usable only if no stronger work anchor exists"
    return 10 - recency_penalty, "short_human_turn", "fallback_candidate", "short human turn; weak status anchor"


def _select_anchor(candidates: list[dict]) -> dict | None:
    for candidate in candidates:
        if candidate["decision"] == "selected_candidate" and candidate["score"] > 0:
            selected = dict(candidate)
            selected["rationale"] = (
                selected["rationale"]
                + "; selected over newer admin/status or worker-prompt turns to avoid latest-message literalism"
            )
            return selected
    for candidate in candidates:
        if candidate["decision"] == "fallback_candidate" and candidate["score"] > 0:
            selected = dict(candidate)
            selected["rationale"] = selected["rationale"] + "; selected as fallback because no stronger work anchor was found"
            return selected
    return None


def _select_anchor_with_boundary(
    *,
    query: str,
    messages: list[dict],
    candidates: list[dict],
    target_window: dict | None = None,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
    prior_request_occurrence: dict | None = None,
    canonical_parent_main_interval: dict | None = None,
    canonical_parent_main_fallback: dict | None = None,
) -> dict:
    base = {
        "context_boundary": "none",
        "anchor_scope": "ranked_history",
        "anchor_reason": "ranked_substantive_or_fallback_candidate",
        "anchor_ref": None,
        "status_ref": None,
        "status_as_of_boundary": status_as_of_boundary,
        "selected_anchor": None,
        "rejected_anchor_alternatives": [],
        "excluded_as_anchor": [
            "subagent_notification",
            "tool_output",
            "inherited_summary",
            "boundary_text",
            "worker_prompt",
            "status_question",
        ],
    }
    if _requires_raw_parent_thread_boundary(query):
        interval = canonical_parent_main_interval or {"status": "not_applicable"}
        selected = _literal_parent_thread_contract_anchor(interval)
        interval_boundary = interval.get("status_as_of_boundary") or status_as_of_boundary
        if selected is None:
            return {
                **base,
                "context_boundary": interval.get("context_boundary")
                or "explicit_parent_thread_side_boundary_missing",
                "anchor_scope": "parent_thread",
                "anchor_reason": interval.get("anchor_reason")
                or "raw_parent_thread_side_boundary_not_proven",
                "status_ref": (interval_boundary or {}).get("source_ref"),
                "status_as_of_boundary": interval_boundary,
                "selected_anchor": None,
                "canonical_parent_main_status_interval": interval,
                "rejected_anchor_alternatives": interval.get("rejected_anchor_alternatives") or [],
            }
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": "explicit_parent_thread_side_boundary",
                "anchor_scope": "parent_thread",
                "anchor_reason": interval.get("anchor_reason")
                or "raw_side_session_parent_thread_id_matched_parent_history_session",
                "anchor_ref": selected.get("source_ref"),
                "status_ref": (interval_boundary or {}).get("source_ref"),
                "status_as_of_boundary": interval_boundary,
                "selected_anchor": _selected_with_reason(
                    selected,
                    "; selected exclusively by the raw side-to-parent boundary contract",
                ),
                "canonical_parent_main_status_interval": interval,
            },
            candidates,
        )
    quoted_authority = _quoted_anchor_authority_resolution(query, candidates)
    if quoted_authority.get("status") == "selected":
        selected = quoted_authority["selected_anchor"]
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": "explicit_quoted_canonical_authority",
                "anchor_scope": "explicit_user_anchor",
                "anchor_reason": selected.get("explicit_anchor_reason") or "unique_canonical_authority_for_quoted_message",
                "anchor_ref": selected.get("source_ref"),
                "selected_anchor": _selected_with_reason(
                    selected,
                    "; selected as the unique canonical authority for the quoted logical message",
                ),
                "quoted_anchor_authority": quoted_authority,
            },
            candidates,
        )
    if quoted_authority.get("status") == "ambiguous":
        return {
            **base,
            "context_boundary": "explicit_quoted_anchor_authority_ambiguous",
            "anchor_scope": "explicit_user_anchor",
            "anchor_reason": quoted_authority.get("reason"),
            "selected_anchor": None,
            "quoted_anchor_authority": quoted_authority,
            "rejected_anchor_alternatives": quoted_authority.get("candidate_diagnostics") or [],
        }
    by_ref = {candidate.get("source_ref"): candidate for candidate in candidates}
    user_chronology = _current_user_message_chronology_decision(
        query=query,
        candidates=candidates,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
    )
    if user_chronology.get("status") == "selected":
        selected = user_chronology.get("selected_anchor") or {}
        boundary = user_chronology.get("status_as_of_boundary") or status_as_of_boundary
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": user_chronology.get("context_boundary"),
                "anchor_scope": "current_user_message_chronology",
                "anchor_reason": user_chronology.get("anchor_reason"),
                "anchor_ref": selected.get("source_ref"),
                "status_ref": (boundary or {}).get("source_ref"),
                "status_as_of_boundary": boundary,
                "selected_anchor": _selected_with_reason(
                    selected,
                    "; selected from user-message chronology before status/control/artifact fallback",
                ),
                "current_user_message_chronology": user_chronology,
            },
            candidates,
        )
    if user_chronology.get("status") == "ambiguous":
        return {
            **base,
            "context_boundary": user_chronology.get("context_boundary"),
            "anchor_scope": "current_user_message_chronology",
            "anchor_reason": user_chronology.get("anchor_reason"),
            "selected_anchor": None,
            "current_user_message_chronology": user_chronology,
            "rejected_anchor_alternatives": user_chronology.get("rejected_anchor_alternatives") or [],
        }
    explicit_anchor = _explicit_anchor_candidate(query, candidates)
    if explicit_anchor:
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": "explicit_anchor",
                "anchor_scope": "explicit_user_anchor",
                "anchor_reason": explicit_anchor.get("explicit_anchor_reason") or "explicit_timestamp_or_quoted_message_anchor",
                "anchor_ref": explicit_anchor.get("source_ref"),
                "selected_anchor": _selected_with_reason(
                    explicit_anchor,
                    "; selected because the query explicitly named this timestamp/message anchor",
                ),
            },
            candidates,
        )
    target_anchor = _target_window_anchor_candidate(
        candidates,
        target_window,
        status_as_of_boundary=status_as_of_boundary,
        audit_run_requested=audit_run_requested,
    )
    if target_anchor:
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": "explicit_task_window",
                "anchor_scope": "target_window",
                "anchor_reason": target_window.get("anchor_reason") if target_window else "explicit_task_window_anchor",
                "anchor_ref": target_anchor.get("source_ref"),
                "selected_anchor": _selected_with_reason(
                    target_anchor,
                    "; selected because the query explicitly named this task/window range",
                ),
            },
            candidates,
        )
    prior_request_occurrence = prior_request_occurrence or {"status": "not_applicable"}
    if prior_request_occurrence.get("status") == "selected":
        occurrence_boundary = prior_request_occurrence.get("status_as_of_boundary") or status_as_of_boundary
        selected = prior_request_occurrence.get("selected_anchor") or {}
        interval_selected = (canonical_parent_main_interval or {}).get("selected_anchor") or {}
        if (
            selected.get("source_ref")
            and selected.get("source_ref") == interval_selected.get("source_ref")
            and interval_selected.get("parent_thread_lineage_anchor")
        ):
            selected = {
                **selected,
                "parent_thread_lineage_anchor": interval_selected.get("parent_thread_lineage_anchor"),
                "side_parent_boundary_anchor": interval_selected.get("side_parent_boundary_anchor"),
                "lineage_resolution": interval_selected.get("lineage_resolution"),
            }
        return _with_rejected_alternatives(
            {
                **base,
                "context_boundary": "prior_request_occurrence",
                "anchor_scope": "parent_thread",
                "anchor_reason": "unique_prior_request_occurrence_parent_main_anchor",
                "anchor_ref": selected.get("source_ref"),
                "status_ref": (occurrence_boundary or {}).get("source_ref"),
                "status_as_of_boundary": occurrence_boundary,
                "selected_anchor": _selected_with_reason(
                    selected,
                    "; selected as the preceding eligible parent/main history row before the unique prior occurrence of the current request",
                ),
                "prior_request_occurrence": prior_request_occurrence,
            },
            candidates,
        )
    if prior_request_occurrence.get("status") == "ambiguous":
        boundary = prior_request_occurrence.get("status_as_of_boundary") or status_as_of_boundary
        interval = canonical_parent_main_interval or {"status": "not_applicable"}
        interval_anchor = interval.get("selected_anchor") or {}
        interval_refs = _parent_main_high_confidence_refs(interval.get("anchor_candidates") or [])
        natural_parent_main_status = (
            is_status_question(query)
            and _relative_status_requires_history_first_anchor(query)
            and not _prefers_parent_main_anchor(query)
            and not _explicit_side_thread_local_status(query)
        )
        unique_interval_anchor = (
            interval.get("status") == "selected"
            and bool(boundary)
            and bool(interval_anchor)
            and _is_proven_parent_main_history_anchor(interval_anchor)
            and len(interval_refs) == 1
            and str(interval_anchor.get("source_ref") or "") in interval_refs
        )
        if natural_parent_main_status and unique_interval_anchor:
            interval_boundary = interval.get("status_as_of_boundary") or boundary
            return _with_rejected_alternatives(
                {
                    **base,
                    "context_boundary": "natural_status_duplicate_prior_parent_main_canonical_fallback",
                    "anchor_scope": "parent_thread",
                    "anchor_reason": "nearest_preceding_canonical_parent_main_history_anchor",
                    "anchor_ref": interval_anchor.get("source_ref"),
                    "status_ref": (interval_boundary or {}).get("source_ref") or (boundary or {}).get("source_ref"),
                    "status_as_of_boundary": interval_boundary,
                    "selected_anchor": _selected_with_reason(
                        interval_anchor,
                        "; selected by canonical parent/main history fallback after duplicate prior natural status prompts",
                    ),
                    "prior_request_occurrence": {
                        **prior_request_occurrence,
                        "fallback_applied": "unique_canonical_parent_main_history_anchor",
                    },
                    "canonical_parent_main_fallback": canonical_parent_main_fallback,
                    "canonical_parent_main_status_interval": interval,
                },
                candidates,
            )
        return {
            **base,
            "context_boundary": prior_request_occurrence.get("context_boundary") or "prior_request_occurrence_ambiguous",
            "anchor_scope": "parent_thread",
            "anchor_reason": prior_request_occurrence.get("anchor_reason") or "prior_request_occurrence_not_unique",
            "status_ref": (boundary or {}).get("source_ref"),
            "status_as_of_boundary": boundary,
            "selected_anchor": None,
            "prior_request_occurrence": prior_request_occurrence,
            "rejected_anchor_alternatives": prior_request_occurrence.get("rejected_anchor_alternatives") or [],
        }
    if prior_request_occurrence.get("status") == "not_found" and _is_parent_main_complaint_request(query):
        return {
            **base,
            "context_boundary": "prior_request_occurrence_not_found",
            "anchor_scope": "parent_thread",
            "anchor_reason": "prior_request_occurrence_not_unique",
            "selected_anchor": None,
            "prior_request_occurrence": prior_request_occurrence,
            "rejected_anchor_alternatives": [],
        }
    if is_status_question(query):
        prefer_parent = _prefers_parent_main_anchor(query)
        relative_history_first = _relative_status_requires_history_first_anchor(query) and _has_canonical_history_source(messages)
        status_message = _current_status_message(messages, query=query)
        if not status_message and (prefer_parent or relative_history_first):
            if relative_history_first and not prefer_parent:
                latest_history_anchor = _latest_eligible_canonical_history_anchor(candidates)
                if latest_history_anchor:
                    return _with_rejected_alternatives(
                        {
                            **base,
                            "context_boundary": "out_of_band_plain_latest_human",
                            "anchor_scope": "current_user_message_chronology",
                            "anchor_reason": "latest_eligible_canonical_human_before_out_of_band_status_request",
                            "anchor_ref": latest_history_anchor.get("source_ref"),
                            "selected_anchor": _selected_with_reason(
                                latest_history_anchor,
                                "; selected as the latest eligible canonical human row because no query-equivalent "
                                "current status boundary was stored",
                            ),
                        },
                        candidates,
                    )
            return {
                **base,
                "context_boundary": (
                    "explicit_parent_main_status_boundary_missing"
                    if prefer_parent
                    else "relative_status_history_boundary_missing"
                ),
                "anchor_scope": "parent_thread",
                "anchor_reason": (
                    "no_status_question_boundary_for_parent_main_anchor"
                    if prefer_parent
                    else "no_status_question_boundary_for_history_first_relative_status"
                ),
                "selected_anchor": None,
                "rejected_anchor_alternatives": _unproven_parent_main_status_boundary_alternatives(candidates),
            }
        if status_message:
            status_ref = status_message.get("source_ref")
            status_index = int(status_message.get("message_index", 0))
            status_thread_keys = set(status_message.get("thread_keys") or [])
            older = sorted(
                (
                    candidate
                    for candidate in candidates
                    if int(candidate.get("message_index", 0)) > status_index
                ),
                key=lambda item: int(item.get("message_index", 0)),
            )
            same_thread_anchor = _first_eligible_thread_anchor(older, status_thread_keys, same_thread=True)
            explicit_side_thread_local = _explicit_side_thread_local_status(query)
            parent_anchor = None
            if prefer_parent:
                interval = canonical_parent_main_interval or {"status": "not_applicable"}
                if interval.get("status") == "selected":
                    interval_anchor = interval.get("selected_anchor") or {}
                    candidate_anchor = _first_history_parent_anchor(older, status_thread_keys)
                    if not candidate_anchor:
                        interval_boundary = interval.get("status_as_of_boundary") or status_as_of_boundary
                        return _with_rejected_alternatives(
                            {
                                **base,
                                "context_boundary": "explicit_parent_main_canonical_history_fallback",
                                "anchor_scope": "parent_thread",
                                "anchor_reason": "nearest_preceding_canonical_parent_main_history_anchor",
                                "anchor_ref": interval_anchor.get("source_ref"),
                                "status_ref": (interval_boundary or {}).get("source_ref") or status_ref,
                                "status_as_of_boundary": interval_boundary,
                                "selected_anchor": _selected_with_reason(
                                    interval_anchor,
                                    "; selected by bounded canonical history fallback for explicit parent/main status",
                                ),
                                "canonical_parent_main_fallback": canonical_parent_main_fallback,
                                "canonical_parent_main_status_interval": interval,
                            },
                            candidates,
                        )
                    if candidate_anchor.get("source_ref") != interval_anchor.get("source_ref"):
                        interval_boundary = interval.get("status_as_of_boundary") or status_as_of_boundary
                        return _with_rejected_alternatives(
                            {
                                **base,
                                "context_boundary": "explicit_parent_main_canonical_status_interval",
                                "anchor_scope": "parent_thread",
                                "anchor_reason": "canonical_parent_main_status_interval",
                                "anchor_ref": interval_anchor.get("source_ref"),
                                "status_ref": (interval_boundary or {}).get("source_ref") or status_ref,
                                "status_as_of_boundary": interval_boundary,
                                "selected_anchor": _selected_with_reason(
                                    interval_anchor,
                                    "; selected by canonical parent/main status interval before latest-message ranking",
                                ),
                                "canonical_parent_main_status_interval": interval,
                            },
                            candidates,
                        )
                    parent_anchor = candidate_anchor
                    status_as_of_boundary = interval.get("status_as_of_boundary") or status_as_of_boundary
                    status_ref = (status_as_of_boundary or {}).get("source_ref") or status_ref
                elif interval.get("status") == "ambiguous":
                    interval_boundary = interval.get("status_as_of_boundary") or status_as_of_boundary
                    context_boundary = interval.get("context_boundary") or "explicit_parent_main_history_anchor_missing"
                    anchor_reason = interval.get("anchor_reason") or "no_proven_parent_main_history_anchor_found"
                    if context_boundary == "explicit_parent_main_canonical_history_anchor_missing":
                        context_boundary = "explicit_parent_main_history_anchor_missing"
                        anchor_reason = "no_proven_parent_main_history_anchor_found"
                    return {
                        **base,
                        "context_boundary": context_boundary,
                        "anchor_scope": "parent_thread",
                        "anchor_reason": anchor_reason,
                        "status_ref": (interval_boundary or {}).get("source_ref") or status_ref,
                        "status_as_of_boundary": interval_boundary,
                        "selected_anchor": None,
                        "rejected_anchor_alternatives": _unproven_parent_main_anchor_alternatives(
                            candidates,
                            status_thread_keys,
                        ) or interval.get("rejected_anchor_alternatives") or [],
                        "canonical_parent_main_status_interval": interval,
                    }
                else:
                    parent_anchor = _first_history_parent_anchor(older, status_thread_keys)
            elif relative_history_first:
                parent_anchor = _first_canonical_history_anchor(older, status_thread_keys, parent_only=False)
            if same_thread_anchor and (explicit_side_thread_local or not prefer_parent):
                if relative_history_first and not explicit_side_thread_local:
                    same_thread_anchor = _history_mirror_candidate(same_thread_anchor) or same_thread_anchor
                    if not _is_proven_parent_main_history_anchor(same_thread_anchor):
                        same_thread_anchor = None
                if same_thread_anchor is None:
                    pass
                else:
                    has_prior_same_thread_status = any(
                        _thread_key_overlap(candidate, status_thread_keys) and candidate.get("classification") == "admin_or_status_turn"
                        for candidate in older
                    )
                    same_thread_is_substantive = same_thread_anchor.get("classification") == "substantive_work_anchor"
                    if explicit_side_thread_local or same_thread_is_substantive or not has_prior_same_thread_status:
                        reason = (
                            "side_thread_follow_up_prior_same_thread_user_message"
                            if has_prior_same_thread_status
                            else "latest_prior_same_thread_user_message"
                        )
                        boundary = "side_thread_follow_up" if has_prior_same_thread_status else "same_thread"
                        return _with_rejected_alternatives(
                            {
                                **base,
                                "context_boundary": boundary,
                                "anchor_scope": "same_thread",
                                "anchor_reason": reason,
                                "anchor_ref": same_thread_anchor.get("source_ref"),
                                "status_ref": status_ref,
                                "status_as_of_boundary": status_as_of_boundary,
                                "selected_anchor": _selected_with_reason(
                                    same_thread_anchor,
                                    "; selected as the latest eligible prior user message in the same thread",
                                ),
                            },
                            candidates,
                        )
            if prefer_parent and parent_anchor is None:
                fallback = canonical_parent_main_fallback or {"status": "not_applicable"}
                if fallback.get("status") == "selected":
                    selected = fallback.get("selected_anchor") or {}
                    fallback_boundary = fallback.get("status_as_of_boundary") or status_as_of_boundary
                    return _with_rejected_alternatives(
                        {
                            **base,
                            "context_boundary": "explicit_parent_main_canonical_history_fallback",
                            "anchor_scope": "parent_thread",
                            "anchor_reason": "nearest_preceding_canonical_parent_main_history_anchor",
                            "anchor_ref": selected.get("source_ref"),
                            "status_ref": (fallback_boundary or {}).get("source_ref") or status_ref,
                            "status_as_of_boundary": fallback_boundary,
                            "selected_anchor": _selected_with_reason(
                                selected,
                                "; selected by bounded canonical history fallback for explicit parent/main status",
                            ),
                            "canonical_parent_main_fallback": fallback,
                            "canonical_parent_main_status_interval": canonical_parent_main_interval,
                        },
                        candidates,
                    )
                return {
                    **base,
                    "context_boundary": "explicit_parent_main_history_anchor_missing",
                    "anchor_scope": "parent_thread",
                    "anchor_reason": "no_proven_parent_main_history_anchor_found",
                    "status_ref": status_ref,
                    "status_as_of_boundary": status_as_of_boundary,
                    "selected_anchor": None,
                    "rejected_anchor_alternatives": _unproven_parent_main_anchor_alternatives(
                        candidates,
                        status_thread_keys,
                    ),
                    "canonical_parent_main_fallback": fallback,
                    "canonical_parent_main_status_interval": canonical_parent_main_interval,
                }
            if relative_history_first and parent_anchor is None:
                return {
                    **base,
                    "context_boundary": "relative_status_history_anchor_missing",
                    "anchor_scope": "parent_thread",
                    "anchor_reason": "no_canonical_history_anchor_found_for_relative_status",
                    "status_ref": status_ref,
                    "status_as_of_boundary": status_as_of_boundary,
                    "selected_anchor": None,
                    "rejected_anchor_alternatives": _unproven_parent_main_anchor_alternatives(
                        candidates,
                        status_thread_keys,
                        include_same_thread_unproven=True,
                    ),
                }
            if parent_anchor is None:
                parent_anchor = _first_eligible_thread_anchor(older, status_thread_keys, same_thread=False)
            if parent_anchor:
                parent_anchor = _history_mirror_candidate(parent_anchor) or parent_anchor
            if parent_anchor:
                parent_is_same_thread = _thread_key_overlap(parent_anchor, status_thread_keys)
                has_prior_same_thread_status = any(
                    _thread_key_overlap(candidate, status_thread_keys) and candidate.get("classification") == "admin_or_status_turn"
                    for candidate in older
                )
                if parent_is_same_thread:
                    reason = (
                        "side_thread_follow_up_prior_same_thread_user_message"
                        if has_prior_same_thread_status
                        else "latest_prior_same_thread_user_message"
                    )
                    boundary = "side_thread_follow_up" if has_prior_same_thread_status else "same_thread"
                    anchor_scope = "same_thread"
                    reason_suffix = "; selected as the latest eligible prior user message in the same thread"
                else:
                    reason = "parent_main_last_actual_work_handoff" if prefer_parent else "first_side_thread_user_message_after_boundary"
                    boundary = "side_thread"
                    anchor_scope = "parent_thread"
                    reason_suffix = "; selected as the last eligible parent/main-thread user message before the side-thread boundary"
                    if relative_history_first and not prefer_parent:
                        reason_suffix += " using canonical history.jsonl before unmirrored current-session candidates"
                return _with_rejected_alternatives(
                    {
                        **base,
                        "context_boundary": boundary,
                        "anchor_scope": anchor_scope,
                        "anchor_reason": reason,
                        "anchor_ref": parent_anchor.get("source_ref"),
                        "status_ref": status_ref,
                        "status_as_of_boundary": status_as_of_boundary,
                        "selected_anchor": _selected_with_reason(
                            parent_anchor,
                            reason_suffix,
                        ),
                        "canonical_parent_main_status_interval": canonical_parent_main_interval,
                    },
                    candidates,
                )
            base = {
                **base,
                "context_boundary": "status_question_no_prior_anchor",
                "anchor_scope": "none",
                "anchor_reason": "no_eligible_prior_user_message_found",
                "status_ref": status_ref,
                "status_as_of_boundary": status_as_of_boundary,
            }
    selected = _select_anchor(candidates)
    if selected:
        selected_ref = selected.get("source_ref")
        selected = by_ref.get(selected_ref, selected)
        return _with_rejected_alternatives(
            {
                **base,
                "anchor_ref": selected_ref,
                "selected_anchor": selected,
            },
            candidates,
        )
    return _with_rejected_alternatives(base, candidates)


_TIMESTAMP_ANCHOR_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:Z|[+-][0-9]{2}:?[0-9]{2})?\b"
)
_QUOTED_ANCHOR_RE = re.compile(r"[\"'`]([^\"'`]{8,220})[\"'`]")
_TASK_ID_RE = re.compile(r"\b([A-Z]{2,12})[- ]?(\d{1,6})\b", re.IGNORECASE)
_TASK_RANGE_MARKERS = (
    "->",
    " through ",
    " thru ",
    " to ",
    " until ",
    " between ",
    " from ",
    " range ",
)
_RUN_AUDIT_MARKERS = (
    "audit this run",
    "audit that run",
    "audit the run",
    "audit same-run",
    "audit same run",
    "pressure run",
    "manual lane",
    "manual-lane",
    "private run",
    ".private",
)


def _target_window_from_query(query: str) -> dict | None:
    normalized = _normalize(query)
    matches = list(_TASK_ID_RE.finditer(query))
    if not matches:
        return None
    ids = []
    seen = set()
    for match in matches:
        prefix = match.group(1).upper()
        number = int(match.group(2))
        task_id = f"{prefix}-{number}"
        if task_id in seen:
            continue
        ids.append({"prefix": prefix, "number": number, "id": task_id})
        seen.add(task_id)
    if not ids:
        return None
    same_prefix_ids = [item for item in ids if item["prefix"] == ids[0]["prefix"]]
    range_cued = len(same_prefix_ids) >= 2 and any(marker in normalized for marker in _TASK_RANGE_MARKERS)
    audit_run_requested = any(marker in normalized for marker in _RUN_AUDIT_MARKERS)
    if range_cued:
        start = min(item["number"] for item in same_prefix_ids)
        end = max(item["number"] for item in same_prefix_ids)
        if end - start > 50:
            return {
                "kind": "task_id_mentions",
                "ids": [item["id"] for item in ids],
                "start_id": same_prefix_ids[0]["id"],
                "end_id": same_prefix_ids[-1]["id"],
                "audit_run_requested": audit_run_requested,
                "anchor_reason": "explicit_task_id_mentions_anchor",
            }
        prefix = same_prefix_ids[0]["prefix"]
        window_ids = [f"{prefix}-{number}" for number in range(start, end + 1)]
        return {
            "kind": "task_id_range",
            "ids": window_ids,
            "start_id": f"{prefix}-{start}",
            "end_id": f"{prefix}-{end}",
            "audit_run_requested": audit_run_requested,
            "anchor_reason": "explicit_task_id_range_anchor",
        }
    return {
        "kind": "task_id_mentions",
        "ids": [item["id"] for item in ids],
        "start_id": ids[0]["id"],
        "end_id": ids[-1]["id"],
        "audit_run_requested": audit_run_requested,
        "anchor_reason": "explicit_task_id_mentions_anchor",
    }


def _target_window_hits(text: str, target_window: dict | None) -> list[str]:
    if not target_window:
        return []
    normalized = _normalize_task_ids(text)
    return [task_id for task_id in target_window.get("ids") or [] if task_id in normalized]


def _task_ids_in_text(text: str) -> set[str]:
    return {
        f"{match.group(1).upper()}-{int(match.group(2))}"
        for match in _TASK_ID_RE.finditer(text)
        if match.group(2).isdigit()
    }


def _outside_target_task_ids(text: str, target_window: dict | None) -> list[str]:
    if not target_window:
        return []
    target_ids = set(target_window.get("ids") or [])
    if not target_ids:
        return []
    return sorted(_task_ids_in_text(_normalize_task_ids(text)) - target_ids)


def _target_window_anchor_candidate(
    candidates: list[dict],
    target_window: dict | None,
    *,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
) -> dict | None:
    if not target_window:
        return None
    matches = []
    for candidate in candidates:
        if _is_never_anchor(candidate):
            continue
        if _is_newer_than_status_as_of(
            str(candidate.get("timestamp_utc") or ""),
            status_as_of_boundary=status_as_of_boundary,
            audit_run_requested=audit_run_requested,
        ):
            continue
        hits = set(candidate.get("target_window_hits") or [])
        if not hits:
            continue
        text = _normalize_task_ids(f"{candidate.get('source_ref', '')} {candidate.get('snippet', '')}")
        outside_task_ids = set(candidate.get("outside_target_task_ids") or _outside_target_task_ids(text, target_window))
        outside_task_penalty = 0
        if not target_window.get("audit_run_requested"):
            outside_task_penalty = len(outside_task_ids) * 28
        score = 130 + len(hits) * 12 + int(candidate.get("score") or 0) - outside_task_penalty
        if not outside_task_ids and not target_window.get("audit_run_requested"):
            score += 30
        if target_window.get("start_id") in hits:
            score += 10
        if target_window.get("end_id") in hits:
            score += 10
        selected = dict(candidate)
        selected["score"] = max(score, int(selected.get("score") or 0))
        selected["target_window_hits"] = sorted(hits)
        selected["outside_target_task_ids"] = sorted(outside_task_ids)
        selected["explicit_anchor_reason"] = target_window.get("anchor_reason")
        matches.append(selected)
    matches.sort(key=lambda item: (int(item.get("score") or 0), item.get("timestamp_utc") or ""), reverse=True)
    return matches[0] if matches else None


def _prior_request_occurrence_recovery(
    *,
    query: str,
    latest_messages: list[dict],
    codex_home: Path | None,
    status_as_of_boundary: dict | None,
) -> dict:
    if not codex_home or not _should_try_prior_request_occurrence(query):
        return {"status": "not_applicable", "reason": "query_not_status_or_parent_main_complaint"}
    history_path = codex_home / "history.jsonl"
    if not history_path.is_file():
        return {"status": "not_applicable", "reason": "canonical_history_missing"}
    normalized_query = _normalize(query)
    if not normalized_query:
        return {"status": "not_applicable", "reason": "empty_query"}
    rows = _canonical_history_human_rows(history_path, query=query)
    if not rows:
        return {"status": "not_applicable", "reason": "canonical_history_has_no_human_rows"}
    matches = [row for row in rows if row.get("normalized_text") == normalized_query]
    occurrence_mode = "duplicate_prior_request"
    if len(matches) == 1:
        occurrence = matches[0]
        if not _has_out_of_band_current_prompt_echo(query, latest_messages, occurrence):
            return {"status": "not_found", "reason": "no_prior_occurrence_of_current_request"}
        if not _out_of_band_single_occurrence_allowed(query, rows, occurrence):
            return {"status": "not_found", "reason": "no_prior_occurrence_of_current_request"}
        prior_matches = [occurrence]
        occurrence_mode = "out_of_band_single_canonical_occurrence"
    elif len(matches) < 1:
        return {"status": "not_found", "reason": "no_prior_occurrence_of_current_request"}
    else:
        current_boundary_line = _line_from_source_ref(str((status_as_of_boundary or {}).get("source_ref") or ""))
        current_boundary_timestamp = str((status_as_of_boundary or {}).get("timestamp_utc") or "")
        if current_boundary_line is not None:
            prior_matches = [row for row in matches if int(row.get("line") or 0) < current_boundary_line]
        elif current_boundary_timestamp:
            prior_matches = [row for row in matches if str(row.get("timestamp_utc") or "") < current_boundary_timestamp]
        else:
            prior_matches = matches[:-1]

    if len(prior_matches) != 1:
        return {
            "status": "ambiguous",
            "context_boundary": "prior_request_occurrence_ambiguous",
            "anchor_reason": "prior_request_occurrence_not_unique",
            "reason": "expected_exactly_one_prior_occurrence_before_current_request_boundary",
            "occurrence_count": len(matches),
            "prior_occurrence_count": len(prior_matches),
            "occurrence_mode": occurrence_mode,
            "occurrence_candidates": [_occurrence_candidate(row) for row in prior_matches[:6]],
            "rejected_anchor_alternatives": [_occurrence_candidate(row) for row in prior_matches[:6]],
        }

    occurrence = prior_matches[0]
    if (
        is_status_question(query)
        and not _prefers_parent_main_anchor(query)
        and not _is_parent_main_complaint_request(query)
        and _has_same_thread_anchor_between_prior_occurrence_and_current(rows, occurrence, status_as_of_boundary)
    ):
        return {
            "status": "not_applicable",
            "reason": "generic_same_thread_status_followup_has_local_anchor",
            "occurrence_count": len(matches),
            "prior_occurrence_count": 1,
            "occurrence_mode": occurrence_mode,
        }
    anchor = _preceding_eligible_history_anchor(
        rows,
        occurrence,
        require_parent_main=occurrence_mode == "out_of_band_single_canonical_occurrence",
    )
    if anchor is None:
        return {
            "status": "ambiguous",
            "context_boundary": "prior_request_occurrence_anchor_missing",
            "anchor_reason": "prior_request_occurrence_preceding_anchor_not_proven",
            "reason": "unique_prior_occurrence_found_but_no_preceding_eligible_parent_main_history_anchor",
            "occurrence_count": len(matches),
            "prior_occurrence_count": 1,
            "occurrence_mode": occurrence_mode,
            "occurrence_candidates": [_occurrence_candidate(occurrence)],
            "rejected_anchor_alternatives": [_occurrence_candidate(occurrence)],
        }

    boundary = _prior_occurrence_boundary(occurrence)
    return {
        "status": "selected",
        "reason": (
            "unique_out_of_band_canonical_occurrence_and_preceding_parent_main_history_anchor_found"
            if occurrence_mode == "out_of_band_single_canonical_occurrence"
            else "unique_prior_occurrence_and_preceding_parent_main_history_anchor_found"
        ),
        "occurrence_count": len(matches),
        "prior_occurrence_count": 1,
        "occurrence_mode": occurrence_mode,
        "status_as_of_boundary": boundary,
        "selected_anchor": anchor,
        "occurrence": _occurrence_candidate(occurrence),
        "occurrence_candidates": [_occurrence_candidate(occurrence)],
    }


def _canonical_parent_main_status_interval(
    *,
    query: str,
    codex_home: Path | None,
    cwd: Path,
    status_as_of_boundary: dict | None,
) -> dict:
    if not codex_home or not is_status_question(query) or not (
        _prefers_parent_main_anchor(query) or _relative_status_requires_history_first_anchor(query)
    ):
        return {"status": "not_applicable", "reason": "query_not_explicit_parent_main_status"}
    history_path = codex_home / "history.jsonl"
    if not history_path.is_file():
        return {"status": "not_applicable", "reason": "canonical_history_missing"}
    rows = _canonical_history_human_rows(history_path, query=query)
    if not rows:
        return {"status": "not_applicable", "reason": "canonical_history_has_no_human_rows"}
    require_side_parent_boundary = _requires_raw_parent_thread_boundary(query)
    boundary = _canonical_parent_main_boundary_row(
        rows,
        query=query,
        status_as_of_boundary=status_as_of_boundary,
        require_exact_query=require_side_parent_boundary,
    )
    if boundary is None:
        return {
            "status": "ambiguous",
            "kind": "canonical_parent_main_status_interval",
            "context_boundary": "explicit_parent_main_canonical_history_boundary_missing",
            "anchor_reason": "canonical_parent_main_status_boundary_not_proven",
            "reason": "could_not_find_unique_canonical_history_status_boundary_for_parent_main_query",
            "rejected_anchor_alternatives": [
                _occurrence_candidate(row)
                for row in _canonical_history_boundary_rows(rows, query=query)[:6]
            ],
        }
    if require_side_parent_boundary:
        lineage = _parent_thread_side_boundary_resolution(
            rows=rows,
            boundary=boundary,
            cwd=cwd,
            codex_home=codex_home,
        )
        if lineage.get("status") != "selected":
            return {
                "status": "ambiguous",
                "kind": "canonical_parent_main_status_interval",
                "context_boundary": "explicit_parent_thread_side_boundary_missing",
                "anchor_reason": "raw_parent_thread_side_boundary_not_proven",
                "reason": lineage.get("reason") or "side_conversation_parent_thread_relationship_not_proven",
                "status_as_of_boundary": _canonical_parent_main_boundary(boundary),
                "boundary": _occurrence_candidate(boundary),
                "parent_thread_side_boundary": lineage,
                "rejected_anchor_alternatives": (lineage.get("rejected_anchor_alternatives") or [])
                + [_occurrence_candidate(boundary)],
            }
    else:
        lineage = _parent_thread_lineage_anchor_resolution(
            rows=rows,
            boundary=boundary,
            cwd=cwd,
            codex_home=codex_home,
        )
    structurally_selected = lineage.get("selected_anchor") if lineage.get("status") == "selected" else None
    anchor = structurally_selected or _preceding_eligible_history_anchor(rows, boundary, require_parent_main=True)
    if anchor is None:
        return {
            "status": "ambiguous",
            "kind": "canonical_parent_main_status_interval",
            "context_boundary": "explicit_parent_main_canonical_history_anchor_missing",
            "anchor_reason": "canonical_parent_main_preceding_anchor_not_proven",
            "reason": "canonical_history_status_boundary_found_but_no_preceding_different_thread_human_anchor",
            "status_as_of_boundary": _canonical_parent_main_boundary(boundary),
            "boundary": _occurrence_candidate(boundary),
            "rejected_anchor_alternatives": [_occurrence_candidate(boundary)],
        }
    selected = dict(anchor)
    selected["rationale"] = (
        str(selected.get("rationale") or "").rstrip()
        + "; selected by bounded canonical history fallback before the explicit parent/main status boundary"
    )
    anchor_candidates = _canonical_parent_main_anchor_candidates(
        rows,
        boundary,
        required_parent_thread_id_hash=lineage.get("parent_thread_id_hash") if require_side_parent_boundary else None,
        required_parent_session_identifier=(
            lineage.get("selected_session_identifier") if require_side_parent_boundary else None
        ),
    )
    if lineage.get("status") in {"selected", "ambiguous"}:
        anchor_candidates = _apply_lineage_resolution_to_candidates(anchor_candidates, lineage)
        if lineage.get("status") == "selected":
            selected["parent_thread_lineage_anchor"] = True
            selected["lineage_resolution"] = lineage.get("selected_lineage")
            selected["rationale"] = (
                str(selected.get("rationale") or "").rstrip()
                + "; selected by structural parent/root lineage, cwd, workspace-root, and role/source metadata before ranking"
            )
    return {
        "status": "selected",
        "kind": "canonical_parent_main_status_interval",
        "reason": (
            (lineage.get("reason") or "structural_parent_thread_lineage_anchor_selected")
            if lineage.get("status") == "selected"
            else "nearest_preceding_different_thread_canonical_history_human_row_found"
        ),
        "context_boundary": "explicit_parent_main_canonical_status_interval",
        "anchor_reason": (
            (lineage.get("reason") or "parent_thread_lineage_anchor")
            if lineage.get("status") == "selected"
            else "canonical_parent_main_status_interval"
        ),
        "status_as_of_boundary": _canonical_parent_main_boundary(boundary),
        "selected_anchor": selected,
        "start_anchor": selected,
        "boundary": _occurrence_candidate(boundary),
        "anchor_candidates": anchor_candidates,
        "rejected_anchor_alternatives": (lineage.get("rejected_anchor_alternatives") or [])
        + [_occurrence_candidate(boundary)],
        "parent_thread_lineage_anchor": lineage,
        "parent_thread_side_boundary": (
            lineage
            if require_side_parent_boundary
            else {"status": "not_applicable", "reason": "literal_parent_thread_not_requested"}
        ),
        "answer_target_window": {
            "kind": "canonical_parent_main_status_interval",
            "start_source_ref": selected.get("source_ref"),
            "start_timestamp_utc": selected.get("timestamp_utc"),
            "boundary_source_ref": boundary.get("source_ref"),
            "boundary_timestamp_utc": boundary.get("timestamp_utc"),
            "inclusive_start": True,
            "inclusive_boundary": True,
            "policy": (
                "For explicit parent/main status asks, selected anchor, raw session activity, "
                "source tiers, follow-through, and answer-facing support are constrained to "
                "this canonical history.jsonl interval."
            ),
        },
    }


def _canonical_parent_main_anchor_candidates(
    rows: list[dict],
    boundary: dict,
    *,
    limit: int = 8,
    required_parent_thread_id_hash: str | None = None,
    required_parent_session_identifier: str | None = None,
) -> list[dict]:
    boundary_line = int(boundary.get("line") or 0)
    boundary_threads = set(boundary.get("thread_keys") or [])
    candidates = []
    for row in sorted(
        (item for item in rows if int(item.get("line") or 0) < boundary_line),
        key=lambda item: int(item.get("line") or 0),
        reverse=True,
    ):
        if required_parent_thread_id_hash and row.get("source_identifier") != (
            f"history-session-{required_parent_thread_id_hash}"
        ):
            continue
        if required_parent_session_identifier and row.get("source_identifier") != required_parent_session_identifier:
            continue
        if _thread_key_overlap(row, boundary_threads):
            continue
        if _is_never_anchor(row) or not _is_eligible_anchor(row):
            continue
        candidate = dict(row)
        candidate["source_scope"] = "canonical_parent_main_history"
        candidate["canonical_parent_main_candidate"] = True
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def _parent_thread_side_boundary_resolution(
    *,
    rows: list[dict],
    boundary: dict,
    cwd: Path,
    codex_home: Path | None,
) -> dict:
    if codex_home is None:
        return {"status": "ambiguous", "reason": "missing_codex_home_for_side_boundary_proof"}
    boundary_lineage = _lineage_for_anchor(boundary, codex_home=codex_home, cwd=cwd)
    if boundary_lineage.get("status") != "ok":
        return _recover_parent_thread_side_boundary_without_direct_edge(
            rows=rows,
            boundary=boundary,
            cwd=cwd,
            codex_home=codex_home,
            side_lineage=boundary_lineage,
        )
    if boundary_lineage.get("cwd_relation") == "unrelated" and boundary_lineage.get("workspace_relation") == "unrelated":
        return {
            "status": "ambiguous",
            "contract": "parent_thread_side_boundary",
            "reason": "raw_side_session_is_outside_requested_workspace",
            "boundary_source_ref": boundary.get("source_ref"),
            "side_lineage": boundary_lineage,
        }
    parent_thread_id_hash = boundary_lineage.get("parent_thread_id_hash")
    boundary_thread_hashes = {
        boundary_lineage.get("session_id_hash"),
        boundary_lineage.get("raw_thread_id_hash"),
    } - {None}
    if not parent_thread_id_hash or parent_thread_id_hash in boundary_thread_hashes:
        return _recover_parent_thread_side_boundary_without_direct_edge(
            rows=rows,
            boundary=boundary,
            cwd=cwd,
            codex_home=codex_home,
            side_lineage=boundary_lineage,
        )
    boundary_line = int(boundary.get("line") or 0)
    parent_identifier = f"history-session-{parent_thread_id_hash}"
    parent_rows = [
        row
        for row in rows
        if int(row.get("line") or 0) < boundary_line
        and row.get("source_identifier") == parent_identifier
        and not _is_never_anchor(row)
        and _is_eligible_anchor(row)
    ]
    parent_rows.sort(key=lambda row: int(row.get("line") or 0), reverse=True)
    rejected = []
    for row in parent_rows:
        parent_lineage = _lineage_for_anchor(row, codex_home=codex_home, cwd=cwd)
        rejection = _parent_thread_lineage_rejection_reason(parent_lineage)
        parent_hashes = {
            parent_lineage.get("session_id_hash"),
            parent_lineage.get("raw_thread_id_hash"),
        } - {None}
        if parent_lineage.get("status") != "ok" or parent_thread_id_hash not in parent_hashes or rejection:
            rejected.append(
                _lineage_rejected_anchor(
                    row,
                    parent_lineage,
                    rejection or "side_parent_thread_id_did_not_match_candidate_session",
                )
            )
            continue
        selected = dict(row)
        selected["parent_thread_lineage_anchor"] = True
        selected["side_parent_boundary_anchor"] = True
        selected["lineage_resolution"] = parent_lineage
        selected["rationale"] = (
            str(selected.get("rationale") or "").rstrip()
            + "; selected only after raw side-session metadata named this candidate session as its parent thread"
        )
        return {
            "status": "selected",
            "contract": "parent_thread_side_boundary",
            "reason": "raw_side_session_parent_thread_id_matched_parent_history_session",
            "boundary_source_ref": boundary.get("source_ref"),
            "selected_anchor": selected,
            "selected_anchor_ref": selected.get("source_ref"),
            "selected_lineage": parent_lineage,
            "side_lineage": boundary_lineage,
            "parent_thread_id_hash": parent_thread_id_hash,
            "rejected_anchor_alternatives": rejected[:8],
            "selection_policy": (
                "Literal parent-thread requests require a raw side-session parent_thread_id edge; "
                "only the latest eligible history.jsonl user row from that exact parent session may anchor the answer."
            ),
        }
    return {
        "status": "ambiguous",
        "contract": "parent_thread_side_boundary",
        "reason": "raw_side_parent_edge_found_but_no_eligible_parent_history_anchor",
        "boundary_source_ref": boundary.get("source_ref"),
        "side_lineage": boundary_lineage,
        "parent_thread_id_hash": parent_thread_id_hash,
        "rejected_anchor_alternatives": rejected[:8],
    }


def _recover_parent_thread_side_boundary_without_direct_edge(
    *,
    rows: list[dict],
    boundary: dict,
    cwd: Path,
    codex_home: Path,
    side_lineage: dict,
) -> dict:
    boundary_line = int(boundary.get("line") or 0)
    boundary_identifier = str(boundary.get("source_identifier") or "")
    if (
        not boundary_line
        or boundary.get("source_kind") != "codex_history"
        or not _requires_raw_parent_thread_boundary(str(boundary.get("snippet") or ""))
        or not is_status_question(str(boundary.get("snippet") or ""))
    ):
        return {
            "status": "ambiguous",
            "contract": "parent_thread_side_boundary",
            "reason": "canonical_side_thread_ask_not_proven_for_no_edge_recovery",
            "boundary_source_ref": boundary.get("source_ref"),
            "side_lineage": side_lineage,
        }

    prior_rows = sorted(
        (row for row in rows if int(row.get("line") or 0) < boundary_line),
        key=lambda row: (str(row.get("timestamp_utc") or ""), int(row.get("line") or 0)),
        reverse=True,
    )
    side_session_identifiers = {
        str(row.get("source_identifier") or "")
        for row in prior_rows
        if is_status_question(str(row.get("snippet") or ""))
        and _requires_raw_parent_thread_boundary(str(row.get("snippet") or ""))
    } - {""}

    eligible_sessions = []
    rejected = []
    inspected_sessions: set[str] = set()
    latest_eligible_timestamp: str | None = None
    for candidate in prior_rows:
        candidate_timestamp = str(candidate.get("timestamp_utc") or "")
        if latest_eligible_timestamp is not None and candidate_timestamp < latest_eligible_timestamp:
            break
        identifier = str(candidate.get("source_identifier") or "")
        if (
            not identifier
            or identifier == boundary_identifier
            or identifier in inspected_sessions
            or _is_never_anchor(candidate)
            or not _is_eligible_anchor(candidate)
        ):
            continue
        inspected_sessions.add(identifier)
        if identifier in side_session_identifiers:
            rejected.append(
                _lineage_rejected_anchor(
                    candidate,
                    {"status": "canonical_side_thread_status_session"},
                    "prior_side_thread_status_session_not_parent_main",
                )
            )
            continue
        lineage = _lineage_for_anchor(candidate, codex_home=codex_home, cwd=cwd)
        if lineage.get("status") != "ok":
            rejected.append(
                _lineage_rejected_anchor(candidate, lineage, "candidate_session_metadata_not_found")
            )
            continue
        rejection = _no_edge_parent_candidate_rejection_reason(lineage)
        if rejection:
            rejected.append(_lineage_rejected_anchor(candidate, lineage, rejection))
            continue
        selected = dict(candidate)
        selected["parent_thread_lineage_anchor"] = True
        selected["side_parent_boundary_anchor"] = True
        selected["deterministic_parent_session_recovery_anchor"] = True
        selected["lineage_resolution"] = lineage
        selected["rationale"] = (
            str(selected.get("rationale") or "").rstrip()
            + "; selected as the latest canonical human row in a raw user/root session compatible "
            "with the requested workspace after side/status and unrelated sessions were rejected"
        )
        eligible_sessions.append(selected)
        latest_eligible_timestamp = candidate_timestamp

    if not eligible_sessions:
        return {
            "status": "ambiguous",
            "contract": "parent_thread_side_boundary",
            "reason": "no_workspace_compatible_parent_session_candidate_without_direct_edge",
            "boundary_source_ref": boundary.get("source_ref"),
            "side_lineage": side_lineage,
            "rejected_anchor_alternatives": rejected[:8],
        }

    eligible_sessions.sort(
        key=lambda candidate: (
            str(candidate.get("timestamp_utc") or ""),
            int(candidate.get("line") or 0),
        ),
        reverse=True,
    )
    latest_timestamp = str(eligible_sessions[0].get("timestamp_utc") or "")
    latest_sessions = [
        candidate
        for candidate in eligible_sessions
        if str(candidate.get("timestamp_utc") or "") == latest_timestamp
    ]
    if len(latest_sessions) != 1:
        return {
            "status": "ambiguous",
            "contract": "parent_thread_side_boundary",
            "reason": "multiple_equally_latest_workspace_compatible_parent_sessions_without_direct_edge",
            "boundary_source_ref": boundary.get("source_ref"),
            "side_lineage": side_lineage,
            "eligible_parent_session_count": len(latest_sessions),
            "rejected_anchor_alternatives": [
                {
                    **_lineage_rejected_anchor(
                        candidate,
                        candidate.get("lineage_resolution") or {},
                        "equally_latest_parent_session_candidate",
                    ),
                    "decision": "ambiguous",
                }
                for candidate in latest_sessions[:8]
            ]
            + rejected[: max(0, 8 - len(latest_sessions))],
        }

    selected = latest_sessions[0]
    selected_identifier = str(selected.get("source_identifier") or "")
    return {
        "status": "selected",
        "contract": "parent_thread_side_boundary",
        "reason": "unique_latest_workspace_compatible_parent_session_recovered_without_direct_edge",
        "selection_mode": "canonical_history_session_chronology_recovery",
        "boundary_source_ref": boundary.get("source_ref"),
        "selected_anchor": selected,
        "selected_anchor_ref": selected.get("source_ref"),
        "selected_lineage": selected.get("lineage_resolution"),
        "selected_session_identifier": selected_identifier,
        "side_lineage": side_lineage,
        "direct_parent_thread_id_available": False,
        "eligible_parent_session_count": 1,
        "rejected_anchor_alternatives": rejected[:8],
        "selection_policy": (
            "When the exact canonical parent-thread ask has no direct parent edge, select only the uniquely "
            "latest eligible canonical human row whose raw session is a user/root session compatible with "
            "--cwd/workspace roots; reject side/status, child/control, unrelated, and equally latest sessions."
        ),
    }


def _parent_thread_lineage_anchor_resolution(
    *,
    rows: list[dict],
    boundary: dict,
    cwd: Path,
    codex_home: Path | None,
    limit: int = 12,
) -> dict:
    if codex_home is None:
        return {"status": "not_applicable", "reason": "missing_codex_home"}
    boundary_line = int(boundary.get("line") or 0)
    boundary_threads = set(boundary.get("thread_keys") or [])
    inspected = []
    rejected = []
    eligible = []
    for row in sorted(
        (item for item in rows if int(item.get("line") or 0) < boundary_line),
        key=lambda item: int(item.get("line") or 0),
        reverse=True,
    ):
        if _thread_key_overlap(row, boundary_threads):
            continue
        if _is_never_anchor(row) or not _is_eligible_anchor(row):
            continue
        candidate = dict(row)
        lineage = _lineage_for_anchor(candidate, codex_home=codex_home, cwd=cwd)
        if lineage.get("status") == "no_raw_session_metadata":
            continue
        inspected.append(candidate.get("source_ref"))
        reason = _parent_thread_lineage_rejection_reason(lineage)
        if reason:
            rejected.append(_lineage_rejected_anchor(candidate, lineage, reason))
        else:
            selected = dict(candidate)
            selected["parent_thread_lineage_anchor"] = True
            selected["lineage_resolution"] = lineage
            eligible.append(selected)
        if len(inspected) >= limit:
            break
    if not inspected:
        return {"status": "not_applicable", "reason": "no_candidate_raw_session_metadata"}
    if eligible:
        selected = eligible[0]
        return {
            "status": "selected",
            "contract": "parent_thread_lineage_anchor",
            "selected_anchor": selected,
            "selected_anchor_ref": selected.get("source_ref"),
            "selected_lineage": selected.get("lineage_resolution"),
            "eligible_count": len(eligible),
            "rejected_count": len(rejected),
            "inspected_count": len(inspected),
            "rejected_anchor_alternatives": rejected[:8],
            "selection_policy": (
                "For explicit parent/main status asks, resolve parent/root lineage from raw session metadata "
                "before ranking: require a user/root session compatible with --cwd/workspace roots and reject "
                "unrelated project roots plus worker/reviewer/control child sessions."
            ),
        }
    return {
        "status": "ambiguous",
        "contract": "parent_thread_lineage_anchor",
        "reason": "raw_session_metadata_found_but_no_structurally_valid_parent_thread_anchor",
        "eligible_count": 0,
        "rejected_count": len(rejected),
        "inspected_count": len(inspected),
        "rejected_anchor_alternatives": rejected[:8],
        "selection_policy": (
            "All inspected candidate anchors were rejected by raw session lineage, cwd/workspace-root, "
            "or role/source metadata."
        ),
    }


def _apply_lineage_resolution_to_candidates(candidates: list[dict], lineage: dict) -> list[dict]:
    by_ref = {
        str(item.get("source_ref") or ""): item
        for item in lineage.get("rejected_anchor_alternatives") or []
        if item.get("source_ref")
    }
    selected_ref = str(lineage.get("selected_anchor_ref") or "")
    updated = []
    for candidate in candidates:
        ref = str(candidate.get("source_ref") or "")
        if ref == selected_ref:
            enriched = dict(candidate)
            enriched["parent_thread_lineage_anchor"] = True
            enriched["lineage_resolution"] = lineage.get("selected_lineage")
            updated.append(enriched)
            continue
        rejected = by_ref.get(ref)
        if rejected:
            enriched = dict(candidate)
            enriched.update(
                {
                    "decision": "rejected",
                    "source_scope": "structural_lineage_rejected_parent_main_candidate",
                    "lineage_resolution": rejected.get("lineage_resolution"),
                    "lineage_rejection_reason": rejected.get("lineage_rejection_reason"),
                    "rationale": rejected.get("rationale"),
                }
            )
            updated.append(enriched)
            continue
        updated.append(candidate)
    return updated


def _lineage_for_anchor(anchor: dict, *, codex_home: Path, cwd: Path) -> dict:
    contexts = _session_contexts_for_anchor(anchor, codex_home, role="lineage_candidate")
    files = []
    for context in contexts:
        files.extend(Path(path) for path in context.get("session_files") or [])
    if not files:
        return {"status": "no_raw_session_metadata", "reason": "no_session_file_for_candidate"}
    metas = [_session_lineage_metadata(path, cwd=cwd) for path in files]
    metas = [meta for meta in metas if meta.get("status") == "ok"]
    if not metas:
        return {"status": "no_raw_session_metadata", "reason": "session_files_have_no_session_meta"}
    primary = metas[0]
    return {
        "status": "ok",
        "session_id_hash": primary.get("session_id_hash"),
        "raw_thread_id_hash": primary.get("raw_thread_id_hash"),
        "parent_thread_id_hash": primary.get("parent_thread_id_hash"),
        "root_thread_id_hash": primary.get("root_thread_id_hash"),
        "thread_source": primary.get("thread_source"),
        "agent_role": primary.get("agent_role"),
        "source_kind": primary.get("source_kind"),
        "cwd": primary.get("cwd"),
        "cwd_relation": primary.get("cwd_relation"),
        "workspace_roots": primary.get("workspace_roots"),
        "workspace_relation": primary.get("workspace_relation"),
        "session_file_count": len(metas),
        "session_source_refs": [meta.get("session_source_ref") for meta in metas if meta.get("session_source_ref")],
    }


def _session_lineage_metadata(path: Path, *, cwd: Path) -> dict:
    session_meta: Mapping[str, Any] | None = None
    workspace_roots: list[str] = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return {"status": "missing", "reason": "session_file_open_failed"}
    with handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            if record.get("type") == "session_meta" and isinstance(record.get("payload"), Mapping):
                session_meta = record.get("payload")
                continue
            if record.get("type") == "turn_context" and isinstance(record.get("payload"), Mapping):
                roots = record.get("payload", {}).get("workspace_roots")
                if isinstance(roots, list):
                    workspace_roots.extend(str(root) for root in roots if root)
            if session_meta is not None and workspace_roots:
                break
    if session_meta is None:
        return {"status": "missing", "reason": "session_meta_missing"}
    source = session_meta.get("source")
    source_kind = "cli"
    if isinstance(source, Mapping) and "subagent" in source:
        source_kind = "subagent"
    elif source:
        source_kind = str(source)
    session_id = session_meta.get("session_id")
    raw_thread_id = session_meta.get("id") or session_id
    parent_thread_id = _raw_parent_thread_id(session_meta)
    root_thread_id = parent_thread_id or session_id or raw_thread_id
    meta_cwd = str(session_meta.get("cwd") or "")
    cwd_relation = _path_relation(meta_cwd, cwd)
    workspace_roots = workspace_roots or ([meta_cwd] if meta_cwd else [])
    workspace_relation = "unknown"
    if workspace_roots:
        workspace_relation = (
            "compatible"
            if any(_path_relation(root, cwd) != "unrelated" for root in workspace_roots)
            else "unrelated"
        )
    return {
        "status": "ok",
        "session_source_ref": str(path),
        "session_id_hash": _hash_value(session_id),
        "raw_thread_id_hash": _hash_value(raw_thread_id),
        "parent_thread_id_hash": _hash_value(parent_thread_id),
        "root_thread_id_hash": _hash_value(root_thread_id),
        "thread_source": session_meta.get("thread_source"),
        "agent_role": session_meta.get("agent_role"),
        "source_kind": source_kind,
        "cwd": meta_cwd,
        "cwd_relation": cwd_relation,
        "workspace_roots": workspace_roots,
        "workspace_relation": workspace_relation,
    }


def _raw_parent_thread_id(session_meta: Mapping[str, Any]) -> Any:
    relationship_keys = (
        "parent_thread_id",
        "parent_session_id",
        "parent_conversation_id",
        "forked_from_thread_id",
        "forked_from_session_id",
    )
    for key in relationship_keys:
        value = session_meta.get(key)
        if value:
            return value
    source = session_meta.get("source")
    if not isinstance(source, Mapping):
        return None
    stack = [source]
    while stack:
        current = stack.pop()
        for key, value in current.items():
            if key in relationship_keys and value:
                return value
            if isinstance(value, Mapping):
                stack.append(value)
    return None


def _path_relation(path_text: str, cwd: Path) -> str:
    if not path_text:
        return "unknown"
    try:
        left = Path(path_text).expanduser().resolve()
        right = cwd.expanduser().resolve()
    except OSError:
        return "unknown"
    if left == right:
        return "same"
    try:
        right.relative_to(left)
        return "candidate_root_contains_cwd"
    except ValueError:
        pass
    try:
        left.relative_to(right)
        return "cwd_contains_candidate_root"
    except ValueError:
        pass
    return "unrelated"


def _parent_thread_lineage_rejection_reason(lineage: dict) -> str | None:
    role = str(lineage.get("agent_role") or "").casefold()
    thread_source = str(lineage.get("thread_source") or "").casefold()
    source_kind = str(lineage.get("source_kind") or "").casefold()
    if thread_source in {"subagent", "worker", "reviewer"} or source_kind == "subagent" or role in {
        "worker",
        "reviewer",
        "rereviewer",
    }:
        return "child_worker_reviewer_or_control_session"
    if lineage.get("cwd_relation") == "unrelated" and lineage.get("workspace_relation") == "unrelated":
        return "unrelated_project_or_workspace_root"
    if thread_source and thread_source != "user":
        return "non_user_thread_source_not_parent_thread_anchor"
    return None


def _no_edge_parent_candidate_rejection_reason(lineage: dict) -> str | None:
    rejection = _parent_thread_lineage_rejection_reason(lineage)
    if rejection:
        return rejection
    parent_thread_id_hash = str(lineage.get("parent_thread_id_hash") or "")
    candidate_thread_hashes = {
        str(lineage.get("session_id_hash") or ""),
        str(lineage.get("raw_thread_id_hash") or ""),
    } - {""}
    if parent_thread_id_hash and parent_thread_id_hash not in candidate_thread_hashes:
        return "candidate_session_has_distinct_parent_thread_id"
    return None


def _append_rejection_rationale(rationale: Any, reason: str) -> str:
    prefix = str(rationale or "").rstrip()
    suffix = f"rejected by parent_thread_lineage_anchor: {reason}"
    return f"{prefix}; {suffix}" if prefix else suffix


def _lineage_rejected_anchor(candidate: dict, lineage: dict, reason: str) -> dict:
    return {
        "source_ref": candidate.get("source_ref"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "classification": candidate.get("classification"),
        "decision": "rejected",
        "rationale": _append_rejection_rationale(candidate.get("rationale"), reason),
        "snippet": candidate.get("snippet"),
        "source_kind": candidate.get("source_kind"),
        "source_scope": "structural_lineage_rejected_parent_main_candidate",
        "lineage_rejection_reason": reason,
        "lineage_resolution": lineage,
        "openable_source_ref": candidate.get("openable_source_ref"),
        "openable_target": candidate.get("openable_target"),
        "target_window_hits": candidate.get("target_window_hits") or [],
        "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
    }


def _canonical_parent_main_fallback_from_interval(interval: dict) -> dict:
    if interval.get("status") == "selected":
        return {
            "status": "selected",
            "reason": interval.get("reason") or "nearest_preceding_different_thread_canonical_history_human_row_found",
            "status_as_of_boundary": interval.get("status_as_of_boundary"),
            "selected_anchor": interval.get("selected_anchor"),
            "boundary": interval.get("boundary"),
            "rejected_anchor_alternatives": interval.get("rejected_anchor_alternatives") or [],
        }
    if interval.get("status") == "ambiguous":
        return {
            key: value
            for key, value in interval.items()
            if key
            in {
                "status",
                "context_boundary",
                "anchor_reason",
                "reason",
                "status_as_of_boundary",
                "boundary",
                "rejected_anchor_alternatives",
            }
        }
    return dict(interval)


def _canonical_parent_main_boundary_row(
    rows: list[dict],
    *,
    query: str,
    status_as_of_boundary: dict | None,
    require_exact_query: bool = False,
) -> dict | None:
    boundary_line = _line_from_source_ref(str((status_as_of_boundary or {}).get("source_ref") or ""))
    if boundary_line is not None:
        for row in rows:
            if int(row.get("line") or 0) != boundary_line:
                continue
            if (
                _canonical_row_matches_exact_status_query_boundary(row, query=query)
                if require_exact_query
                else is_status_question(str(row.get("snippet") or ""))
                and not _is_status_boundary_prompt_noise(row)
            ):
                return row
        if not require_exact_query:
            return None
    candidates = (
        [row for row in rows if _canonical_row_matches_exact_status_query_boundary(row, query=query)]
        if require_exact_query
        else _canonical_history_boundary_rows(rows, query=query)
    )
    boundary_timestamp = parse_datetime(str((status_as_of_boundary or {}).get("timestamp_utc") or ""), timezone.utc)
    if boundary_timestamp is not None:
        bounded = []
        for row in candidates:
            row_timestamp = parse_datetime(str(row.get("timestamp_utc") or ""), timezone.utc)
            if row_timestamp is None:
                continue
            if row_timestamp.astimezone(timezone.utc) <= boundary_timestamp.astimezone(timezone.utc):
                bounded.append(row)
        candidates = bounded
    if len(candidates) == 1:
        return candidates[0]
    return None


def _canonical_history_boundary_rows(rows: list[dict], *, query: str) -> list[dict]:
    return [row for row in rows if _canonical_row_matches_status_query_boundary(row, query=query)]


def _canonical_row_matches_status_query_boundary(row: dict, *, query: str) -> bool:
    text = str(row.get("snippet") or "")
    normalized_text = _normalize(text)
    normalized_query = _normalize(query)
    if not normalized_text or not normalized_query:
        return False
    if _is_status_boundary_prompt_noise(row):
        return False
    if not is_status_question(text):
        return False
    return normalized_query in normalized_text or normalized_text in normalized_query


def _canonical_row_matches_exact_status_query_boundary(row: dict, *, query: str) -> bool:
    if _is_status_boundary_prompt_noise(row):
        return False
    normalized_text = _normalize(str(row.get("snippet") or "")).strip(" .?!")
    normalized_query = _normalize(query).strip(" .?!")
    return bool(normalized_text and normalized_text == normalized_query and is_status_question(normalized_text))


def _canonical_parent_main_boundary(row: dict) -> dict:
    boundary = _prior_occurrence_boundary(row)
    boundary["kind"] = "canonical_parent_main_status_boundary"
    boundary["boundary_policy"] = (
        "This canonical history.jsonl row is the explicit parent/main status boundary; "
        "answer-facing support must stay after the selected parent/main anchor and at or before this row."
    )
    return boundary


def _should_try_prior_request_occurrence(query: str) -> bool:
    return is_status_question(query) or _prefers_parent_main_anchor(query) or _is_parent_main_complaint_request(query)


def _is_parent_main_complaint_request(query: str) -> bool:
    normalized = _normalize(query)
    parent_or_boundary = (
        "parent/main",
        "parent thread",
        "main thread",
        "side-thread",
        "side thread",
        "last actual message",
        "last message",
        "latest message",
        "wrong context",
    )
    status_or_answer = (
        "status",
        "answer",
        "what happened since",
        "while i was away",
        "jsonl",
    )
    complaint = (
        "wrong",
        "wrong anchor",
        "wrong context",
        "made no sense",
        "failed",
        "failure",
        "complaint",
        "correcting",
        "correction",
        "did not use",
        "didn't use",
    )
    return (
        any(marker in normalized for marker in parent_or_boundary)
        and any(marker in normalized for marker in status_or_answer)
        and any(marker in normalized for marker in complaint)
    )


def _out_of_band_single_occurrence_allowed(query: str, rows: list[dict], occurrence: dict) -> bool:
    anchor = _preceding_eligible_history_anchor(rows, occurrence, require_parent_main=True)
    if anchor is None:
        return False
    if is_status_question(query):
        return _prefers_parent_main_anchor(query) or _relative_status_requires_history_first_anchor(query)
    if _is_parent_main_complaint_request(query):
        return _canonical_occurrence_has_status_failure_evidence(rows, occurrence)
    return False


def _has_out_of_band_current_prompt_echo(query: str, latest_messages: list[dict], occurrence: dict) -> bool:
    normalized_query = _normalize(query)
    if not normalized_query:
        return False
    occurrence_timestamp = parse_datetime(str(occurrence.get("timestamp_utc") or ""), timezone.utc)
    for message in latest_messages:
        if message.get("source_kind") == "codex_history":
            continue
        normalized_text = _normalize(str(message.get("snippet") or ""))
        if normalized_text != normalized_query and normalized_query not in normalized_text:
            continue
        message_timestamp = parse_datetime(str(message.get("timestamp_utc") or ""), timezone.utc)
        if occurrence_timestamp is not None and message_timestamp is not None:
            if message_timestamp.astimezone(timezone.utc) <= occurrence_timestamp.astimezone(timezone.utc):
                continue
        return True
    return False


def _canonical_occurrence_has_status_failure_evidence(rows: list[dict], occurrence: dict) -> bool:
    occurrence_line = int(occurrence.get("line") or 0)
    occurrence_threads = set(occurrence.get("thread_keys") or [])
    nearby = []
    for row in rows:
        line = int(row.get("line") or 0)
        if abs(line - occurrence_line) > 3:
            continue
        if occurrence_threads and not _thread_key_overlap(row, occurrence_threads):
            continue
        nearby.append(str(row.get("snippet") or ""))
    if not nearby:
        nearby = [str(occurrence.get("snippet") or "")]
    normalized_nearby = " ".join(_normalize(text) for text in nearby)
    has_status_boundary = any(is_status_question(text) for text in nearby) or any(
        marker in normalized_nearby
        for marker in (
            "status",
            "what happened since",
            "what has happened since",
            "while i was away",
        )
    )
    has_wrong_context = any(
        marker in normalized_nearby
        for marker in (
            "wrong anchor",
            "wrong context",
            "wrong thread",
            "wrong conversation",
            "side-thread answer",
            "side thread answer",
            "made no sense",
            "failed to use jsonl",
            "did not use jsonl",
            "didn't use jsonl",
        )
    )
    has_parent_main_boundary = any(
        marker in normalized_nearby
        for marker in (
            "parent/main",
            "parent thread",
            "main thread",
            "last actual message",
            "latest message",
            "previous relevant time window",
        )
    )
    return has_status_boundary and has_wrong_context and has_parent_main_boundary


def _canonical_history_human_rows(path: Path, *, query: str) -> list[dict]:
    rows = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            text = _clean_history_text(record.get("text"))
            timestamp = parse_datetime(record.get("ts") or record.get("timestamp") or record.get("created_at"), timezone.utc)
            if not text or timestamp is None:
                continue
            candidate = _history_row_candidate(
                record,
                text=text,
                path=path,
                line_number=line_number,
                query=query,
                timestamp_utc=timestamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
            )
            if _history_row_is_non_human_or_boundary(record, candidate):
                continue
            rows.append(candidate)
    return rows


def _history_row_candidate(
    record: Mapping[str, Any],
    *,
    text: str,
    path: Path,
    line_number: int,
    query: str,
    timestamp_utc: str,
) -> dict:
    score, classification, decision, rationale = _score_anchor(text, 0, timestamp_utc=timestamp_utc)
    source_identifier = _history_source_identifier(record)
    source_ref = f"codex_history:history.jsonl:{line_number}"
    target_window = _target_window_from_query(query)
    return {
        "source_ref": source_ref,
        "source_kind": "codex_history",
        "source_identifier": source_identifier,
        "thread_key": source_identifier,
        "thread_keys": [source_identifier],
        "mirror_sources": [
            {
                "source_kind": "codex_history",
                "source_ref": source_ref,
                "source_identifier": source_identifier,
                "thread_key": source_identifier,
                "openable_source_ref": {"path": str(path), "line": line_number},
                "openable_target": f"{path}:{line_number}",
            }
        ],
        "openable_source_ref": {"path": str(path), "line": line_number},
        "openable_target": f"{path}:{line_number}",
        "timestamp_utc": timestamp_utc,
        "local_timestamp": timestamp_utc,
        "message_index": line_number,
        "line": line_number,
        "snippet": _compact(text),
        "normalized_text": _normalize(text),
        "classification": classification,
        "score": score,
        "decision": decision,
        "rationale": rationale,
        "target_window_hits": _target_window_hits(text, target_window),
        "outside_target_task_ids": _outside_target_task_ids(text, target_window),
    }


def _history_row_is_non_human_or_boundary(record: Mapping[str, Any], candidate: dict) -> bool:
    classification = classify_conversation_record(record)
    if classification.label in {
        ASSISTANT_OR_AGENT_MESSAGE,
        COMPACTED_HISTORY_SUMMARY,
        TOOL_ACTION_OUTPUT,
    }:
        return True
    normalized = _normalize(str(candidate.get("snippet") or ""))
    if any(marker in normalized for marker in NON_ANCHOR_CONTEXT_MARKERS):
        return True
    return False


def _preceding_eligible_history_anchor(
    rows: list[dict],
    occurrence: dict,
    *,
    require_parent_main: bool = False,
) -> dict | None:
    occurrence_line = int(occurrence.get("line") or 0)
    occurrence_threads = set(occurrence.get("thread_keys") or [])
    prior_rows = sorted(
        (item for item in rows if int(item.get("line") or 0) < occurrence_line),
        key=lambda item: int(item.get("line") or 0),
        reverse=True,
    )
    for row in prior_rows:
        if require_parent_main and _thread_key_overlap(row, occurrence_threads):
            continue
        if _is_never_anchor(row):
            continue
        if not _is_eligible_anchor(row):
            continue
        selected = dict(row)
        selected["rationale"] = (
            str(selected.get("rationale") or "").rstrip()
            + "; selected by canonical history prior-request occurrence recovery"
        )
        return selected
    return None


def _has_same_thread_anchor_between_prior_occurrence_and_current(
    rows: list[dict],
    occurrence: dict,
    status_as_of_boundary: dict | None,
) -> bool:
    current_line = _line_from_source_ref(str((status_as_of_boundary or {}).get("source_ref") or ""))
    if current_line is None:
        return False
    occurrence_line = int(occurrence.get("line") or 0)
    occurrence_threads = set(occurrence.get("thread_keys") or [])
    for row in rows:
        line = int(row.get("line") or 0)
        if line <= occurrence_line or line >= current_line:
            continue
        if not occurrence_threads.intersection(set(row.get("thread_keys") or [])):
            continue
        if _is_never_anchor(row) or not _is_eligible_anchor(row):
            continue
        return True
    return False


def _prior_occurrence_boundary(occurrence: dict) -> dict:
    return {
        "kind": "prior_request_occurrence_boundary",
        "source_ref": occurrence.get("source_ref"),
        "source_kind": occurrence.get("source_kind"),
        "source_identifier": occurrence.get("source_identifier"),
        "openable_source_ref": occurrence.get("openable_source_ref"),
        "openable_target": occurrence.get("openable_target"),
        "timestamp_utc": occurrence.get("timestamp_utc"),
        "local_timestamp": occurrence.get("local_timestamp"),
        "message_index": occurrence.get("message_index"),
        "thread_keys": occurrence.get("thread_keys") or [],
        "snippet": occurrence.get("snippet"),
        "boundary_policy": (
            "This prior canonical occurrence of the current request is the status/query boundary; "
            "answer-facing support must stay in the interval after the selected parent/main anchor and at or before this row."
        ),
    }


def _occurrence_candidate(row: dict) -> dict:
    return {
        "source_ref": row.get("source_ref"),
        "timestamp_utc": row.get("timestamp_utc"),
        "classification": row.get("classification"),
        "decision": "rejected",
        "rationale": "prior request occurrence candidate; use only to disambiguate and rerun, not as answer support",
        "snippet": row.get("snippet"),
        "source_kind": row.get("source_kind"),
        "openable_source_ref": row.get("openable_source_ref"),
        "openable_target": row.get("openable_target"),
        "target_window_hits": row.get("target_window_hits") or [],
        "outside_target_task_ids": row.get("outside_target_task_ids") or [],
        "prior_request_occurrence_candidate": True,
        "verification_or_rerun_only": True,
        "answer_support_role": "prior_request_occurrence_disambiguation_only",
    }


def _history_source_identifier(record: Mapping[str, Any]) -> str:
    session_id = record.get("session_id")
    if session_id:
        digest = hashlib.sha256(str(session_id).encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"history-session-{digest}"
    return "history"


def _clean_history_text(value: Any) -> str:
    if value is None:
        return ""
    return "\n".join(line.rstrip() for line in str(value).strip().splitlines()).strip()


def _current_user_message_chronology_decision(
    *,
    query: str,
    candidates: list[dict],
    status_as_of_boundary: dict | None,
    audit_run_requested: bool,
) -> dict:
    normalized = _normalize(query)
    target_fragments = _current_user_boundary_target_fragments(query)
    wants_prior = _wants_message_before_named_anchor(normalized)
    if target_fragments and not wants_prior:
        return {"status": "not_applicable"}
    if target_fragments:
        target = _find_current_user_boundary_target(
            candidates,
            target_fragments,
            status_as_of_boundary=status_as_of_boundary,
            audit_run_requested=audit_run_requested,
        )
        if not target:
            return {
                "status": "ambiguous",
                "context_boundary": "current_user_message_boundary_target_missing",
                "anchor_reason": "named_or_quoted_user_message_boundary_not_found",
                "reason": "could_not_find_a_user_message_matching_the_named_or_quoted_boundary",
                "target_fragments": target_fragments,
                "rejected_anchor_alternatives": _current_user_chronology_alternatives(candidates),
            }
        if wants_prior:
            selected = _preceding_current_user_anchor(candidates, target)
            if not selected:
                return {
                    "status": "ambiguous",
                    "context_boundary": "current_user_message_before_anchor_missing",
                    "anchor_reason": "message_before_named_user_boundary_not_proven",
                    "reason": "named_or_quoted_user_boundary_found_but_no_preceding_eligible_user_message_was_found",
                    "target_boundary": _current_user_boundary_candidate(target),
                    "rejected_anchor_alternatives": [target] + _current_user_chronology_alternatives(candidates),
                }
            boundary = _status_boundary_from_user_candidate(
                target,
                kind="current_user_message_boundary",
                policy=(
                    "This named/quoted user message is the before/not-after boundary; answer-facing support "
                    "must stay after the selected prior user anchor and at or before this boundary."
                ),
            )
            return {
                "status": "selected",
                "context_boundary": "current_user_message_before_named_boundary",
                "anchor_reason": "preceding_user_message_before_named_boundary",
                "reason": "selected_preceding_user_message_before_named_or_quoted_user_boundary",
                "selected_anchor": selected,
                "status_as_of_boundary": boundary,
                "target_boundary": _current_user_boundary_candidate(target),
                "target_fragments": target_fragments,
            }
        return {
            "status": "selected",
            "context_boundary": "current_user_message_exact_anchor",
            "anchor_reason": "named_or_quoted_user_message_anchor",
            "reason": "selected_user_message_matching_named_or_quoted_boundary",
            "selected_anchor": target,
            "status_as_of_boundary": status_as_of_boundary,
            "target_boundary": _current_user_boundary_candidate(target),
            "target_fragments": target_fragments,
        }
    if _wants_last_substantive_instruction(normalized):
        selected = _last_substantive_user_instruction_anchor(
            candidates,
            status_as_of_boundary=status_as_of_boundary,
            audit_run_requested=audit_run_requested,
        )
        if selected:
            return {
                "status": "selected",
                "context_boundary": "current_user_last_substantive_instruction",
                "anchor_reason": "last_substantive_user_instruction_anchor",
                "reason": "selected_latest_substantive_user_instruction_before_current_status_boundary",
                "selected_anchor": selected,
                "status_as_of_boundary": status_as_of_boundary,
            }
        return {
            "status": "ambiguous",
            "context_boundary": "current_user_last_substantive_instruction_missing",
            "anchor_reason": "last_substantive_user_instruction_not_proven",
            "reason": "no_substantive_user_instruction_anchor_found_before_status_or_control_rows",
            "rejected_anchor_alternatives": _current_user_chronology_alternatives(candidates),
        }
    return {"status": "not_applicable"}


def _current_user_boundary_target_fragments(query: str) -> list[str]:
    fragments = [
        _normalize(match.group(1))
        for match in _QUOTED_ANCHOR_RE.finditer(query)
        if len(_normalize(match.group(1)).split()) >= 3
    ]
    for pattern in (
        r"\bmessage\s+before\s+(?P<target>.+?)(?:\?|$)",
        r"\bbefore\s+(?:the\s+)?(?P<target>.+?)\s+message\b",
    ):
        for match in re.finditer(pattern, query, flags=re.IGNORECASE):
            fragment = _clean_boundary_fragment(match.group("target"))
            if fragment:
                fragments.append(fragment)
    deduped = []
    for fragment in fragments:
        if fragment and fragment not in deduped:
            deduped.append(fragment)
    return deduped


def _clean_boundary_fragment(value: str) -> str:
    fragment = _normalize(value)
    fragment = re.sub(r"\bnot\s+after\s+it\b.*$", "", fragment).strip()
    fragment = re.sub(r"\bnot\s+after\b.*$", "", fragment).strip()
    fragment = re.sub(r"\s+", " ", fragment).strip(" ,.;:-")
    if any(
        marker in fragment
        for marker in (
            "last answer",
            "last actual",
            "last message",
            "last time",
            "time before",
            "previous relevant",
            "answering",
            "giving another answer",
            "giving an answer",
            "giving answer",
        )
    ):
        return ""
    words = fragment.split()
    while words and words[0] in {"the", "a", "an"}:
        words.pop(0)
    while words and words[-1] in {"message", "msg", "turn", "anchor"}:
        words.pop()
    if len(words) < 3:
        return ""
    return " ".join(words)


def _wants_message_before_named_anchor(normalized_query: str) -> bool:
    return (
        "message before" in normalized_query
        or "before the" in normalized_query
        or "before " in normalized_query and " not after" in normalized_query
    )


def _wants_last_substantive_instruction(normalized_query: str) -> bool:
    return any(
        marker in normalized_query
        for marker in (
            "last substantive instruction",
            "latest substantive instruction",
            "last substantive message",
            "latest substantive message",
            "last real instruction",
            "latest real instruction",
        )
    )


def _find_current_user_boundary_target(
    candidates: list[dict],
    fragments: list[str],
    *,
    status_as_of_boundary: dict | None,
    audit_run_requested: bool,
) -> dict | None:
    matches = []
    for candidate in candidates:
        if _is_never_anchor(candidate):
            continue
        if not _is_eligible_anchor(candidate):
            continue
        if _is_newer_than_status_as_of(
            str(candidate.get("timestamp_utc") or ""),
            status_as_of_boundary=status_as_of_boundary,
            audit_run_requested=audit_run_requested,
        ):
            continue
        snippet = _normalize(str(candidate.get("snippet") or ""))
        if not any(fragment and (fragment in snippet or snippet in fragment) for fragment in fragments):
            continue
        selected = dict(candidate)
        selected["current_user_boundary_target"] = True
        selected["matched_boundary_fragments"] = [fragment for fragment in fragments if fragment in snippet or snippet in fragment]
        matches.append(selected)
    matches.sort(key=_chronology_sort_key, reverse=True)
    return matches[0] if matches else None


def _last_substantive_user_instruction_anchor(
    candidates: list[dict],
    *,
    status_as_of_boundary: dict | None,
    audit_run_requested: bool,
) -> dict | None:
    for candidate in sorted(candidates, key=_chronology_sort_key, reverse=True):
        if candidate.get("classification") != "substantive_work_anchor":
            continue
        if not _is_eligible_anchor(candidate):
            continue
        if not _is_proven_parent_main_history_anchor(candidate):
            continue
        if _is_newer_than_status_as_of(
            str(candidate.get("timestamp_utc") or ""),
            status_as_of_boundary=status_as_of_boundary,
            audit_run_requested=audit_run_requested,
        ):
            continue
        return _history_mirror_candidate(candidate) or dict(candidate)
    return None


def _preceding_current_user_anchor(candidates: list[dict], target: dict) -> dict | None:
    older = [
        candidate
        for candidate in candidates
        if candidate.get("source_ref") != target.get("source_ref")
        and _is_eligible_anchor(candidate)
        and not _is_never_anchor(candidate)
        and _candidate_is_before(candidate, target)
    ]
    if not older:
        return None
    older.sort(key=_chronology_sort_key, reverse=True)
    return dict(older[0])


def _candidate_is_before(candidate: dict, target: dict) -> bool:
    candidate_time = parse_datetime(str(candidate.get("timestamp_utc") or ""), timezone.utc)
    target_time = parse_datetime(str(target.get("timestamp_utc") or ""), timezone.utc)
    if candidate_time is not None and target_time is not None:
        return candidate_time.astimezone(timezone.utc) < target_time.astimezone(timezone.utc)
    try:
        return int(candidate.get("message_index") or 0) > int(target.get("message_index") or 0)
    except (TypeError, ValueError):
        return False


def _chronology_sort_key(candidate: dict) -> tuple[str, int]:
    timestamp = str(candidate.get("timestamp_utc") or "")
    try:
        index = -int(candidate.get("message_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return (timestamp, index)


def _status_boundary_from_user_candidate(candidate: dict, *, kind: str, policy: str) -> dict:
    return {
        "kind": kind,
        "source_ref": candidate.get("source_ref"),
        "source_kind": candidate.get("source_kind"),
        "source_identifier": candidate.get("source_identifier"),
        "openable_source_ref": candidate.get("openable_source_ref"),
        "openable_target": candidate.get("openable_target"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "local_timestamp": candidate.get("local_timestamp"),
        "message_index": candidate.get("message_index"),
        "thread_keys": candidate.get("thread_keys") or [],
        "logical_message_id": candidate.get("logical_message_id"),
        "canonical_authority": candidate.get("canonical_authority"),
        "session_activity_location": candidate.get("session_activity_location"),
        "snippet": candidate.get("snippet"),
        "boundary_policy": policy,
    }


def _current_user_boundary_candidate(candidate: dict) -> dict:
    return {
        "source_ref": candidate.get("source_ref"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "classification": candidate.get("classification"),
        "decision": candidate.get("decision"),
        "rationale": candidate.get("rationale"),
        "snippet": candidate.get("snippet"),
        "source_kind": candidate.get("source_kind"),
        "openable_source_ref": candidate.get("openable_source_ref"),
        "openable_target": candidate.get("openable_target"),
        "current_user_boundary_target": bool(candidate.get("current_user_boundary_target")),
        "matched_boundary_fragments": candidate.get("matched_boundary_fragments") or [],
    }


def _current_user_chronology_alternatives(candidates: list[dict], *, limit: int = 8) -> list[dict]:
    alternatives = []
    for candidate in sorted(candidates, key=_chronology_sort_key, reverse=True):
        if len(alternatives) >= limit:
            break
        if not (_is_never_anchor(candidate) or _is_eligible_anchor(candidate)):
            continue
        alternatives.append(_current_user_boundary_candidate(candidate))
    return alternatives


def _quoted_anchor_authority_resolution(query: str, candidates: list[dict]) -> dict:
    if _wants_message_before_named_anchor(_normalize(query)):
        return {"status": "not_applicable"}
    fragments = [
        _normalize(match.group(1))
        for match in _QUOTED_ANCHOR_RE.finditer(query)
        if len(_normalize(match.group(1)).split()) >= 3
    ]
    if not fragments:
        return {"status": "not_applicable"}
    matches = []
    for candidate in candidates:
        if _is_never_anchor(candidate):
            continue
        snippet = _normalize(str(candidate.get("snippet") or ""))
        if any(fragment and (fragment in snippet or snippet in fragment) for fragment in fragments):
            matches.append(candidate)
    if not matches:
        return {"status": "not_applicable"}

    canonical = [candidate for candidate in matches if candidate.get("source_kind") == "codex_history"]
    ambiguous = [
        candidate
        for candidate in matches
        if str(candidate.get("correlation_status") or "").startswith("ambiguous_")
    ]
    diagnostics = [_quoted_authority_candidate_diagnostic(candidate) for candidate in matches[:8]]
    if len(canonical) == 1 and not ambiguous:
        selected = dict(canonical[0])
        has_timestamp = any(
            parse_datetime(match.group(0).replace(" ", "T"), timezone.utc) is not None
            for match in _TIMESTAMP_ANCHOR_RE.finditer(query)
        )
        selected["explicit_anchor_reason"] = (
            "explicit_timestamp_and_quoted_message_anchor"
            if has_timestamp
            else "unique_canonical_authority_for_quoted_message"
        )
        selected["score"] = max(int(selected.get("score") or 0), 100)
        return {
            "status": "selected",
            "selection_rule": "exact normalized quote plus correlated session identity and bounded chronology",
            "selected_anchor": selected,
            "canonical_candidate_count": 1,
            "session_candidate_count": len(matches) - 1,
            "candidate_diagnostics": diagnostics,
        }
    if len(canonical) > 1:
        reason = "quoted_message_matched_multiple_canonical_history_rows"
    elif ambiguous:
        reason = "quoted_message_canonical_session_correlation_is_ambiguous"
    else:
        reason = "quoted_message_has_no_canonical_history_authority"
    return {
        "status": "ambiguous",
        "reason": reason,
        "selection_rule": "quoted status anchors require exactly one canonical history authority",
        "canonical_candidate_count": len(canonical),
        "session_candidate_count": len(matches) - len(canonical),
        "candidate_diagnostics": diagnostics,
    }


def _quoted_authority_candidate_diagnostic(candidate: dict) -> dict:
    return {
        "source_ref": candidate.get("source_ref"),
        "source_kind": candidate.get("source_kind"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "logical_message_id": candidate.get("logical_message_id"),
        "correlation_status": candidate.get("correlation_status"),
        "decision": "candidate_diagnostic",
        "rationale": "quoted logical-message authority candidate",
        "snippet": candidate.get("snippet"),
        "openable_source_ref": candidate.get("openable_source_ref"),
        "openable_target": candidate.get("openable_target"),
        "correlation_diagnostics": candidate.get("correlation_diagnostics") or [],
    }


def _explicit_anchor_candidate(query: str, candidates: list[dict]) -> dict | None:
    normalized_query = _normalize(query)
    parsed_times = [
        parsed.astimezone(timezone.utc)
        for parsed in (parse_datetime(match.group(0).replace(" ", "T"), timezone.utc) for match in _TIMESTAMP_ANCHOR_RE.finditer(query))
        if parsed is not None
    ]
    quoted_fragments = [
        _normalize(match.group(1))
        for match in _QUOTED_ANCHOR_RE.finditer(query)
        if len(_normalize(match.group(1)).split()) >= 3
    ]
    if not parsed_times and not quoted_fragments:
        return None
    matches = []
    for candidate in candidates:
        if _is_never_anchor(candidate):
            continue
        timestamp = parse_datetime(str(candidate.get("timestamp_utc") or ""), timezone.utc)
        timestamp_match = False
        if timestamp is not None:
            timestamp_utc = timestamp.astimezone(timezone.utc)
            timestamp_match = any(abs((timestamp_utc - target).total_seconds()) <= 1 for target in parsed_times)
        snippet = _normalize(str(candidate.get("snippet") or ""))
        quote_match = any(fragment and (fragment in snippet or snippet in normalized_query) for fragment in quoted_fragments)
        if not timestamp_match and not quote_match:
            continue
        selected = dict(candidate)
        if timestamp_match and quote_match:
            selected["explicit_anchor_reason"] = "explicit_timestamp_and_quoted_message_anchor"
            selected["score"] = max(int(selected.get("score") or 0), 120)
        elif timestamp_match:
            selected["explicit_anchor_reason"] = "explicit_timestamp_anchor"
            selected["score"] = max(int(selected.get("score") or 0), 110)
        else:
            selected["explicit_anchor_reason"] = "explicit_quoted_message_anchor"
            selected["score"] = max(int(selected.get("score") or 0), 100)
        matches.append(selected)
    matches.sort(key=lambda item: (int(item.get("score") or 0), item.get("timestamp_utc") or ""), reverse=True)
    return matches[0] if matches else None


def _prefers_parent_main_anchor(query: str) -> bool:
    normalized = _normalize(query)
    return any(
        marker in normalized
        for marker in (
            "parent/main",
            "parent thread",
            "main thread",
            "before this side conversation",
            "before the side conversation",
            "last actual message",
            "last actual substantive",
            "work-handoff",
            "work handoff",
            "not the status question",
            "not the side thread",
            "not this side thread",
            "previous relevant time window",
            "before the last answer",
            "time before the last answer",
            "before the last time",
            "before hte last time",
            "time before that",
        )
    )


def _requires_raw_parent_thread_boundary(query: str) -> bool:
    normalized = _normalize(query)
    return "parent thread" in normalized or "parent-thread" in normalized


def _relative_status_requires_history_first_anchor(query: str) -> bool:
    normalized = _normalize(query)
    return any(
        marker in normalized
        for marker in (
            "what happened since my last message",
            "what has happened since my last message",
            "since my last message",
            "since i last messaged",
            "since my latest message",
            "last actual message",
            "latest actual message",
            "before this side conversation",
            "before the side conversation",
            "before this side thread",
            "before the side thread",
        )
    )


def _has_canonical_history_source(messages: list[dict]) -> bool:
    for message in messages:
        if message.get("source_kind") == "codex_history":
            return True
        for mirror in message.get("mirror_sources") or []:
            if isinstance(mirror, Mapping) and mirror.get("source_kind") == "codex_history":
                return True
    return False


def _explicit_side_thread_local_status(query: str) -> bool:
    normalized = _normalize(query)
    return any(
        marker in normalized
        for marker in (
            "side-thread-local",
            "side thread local",
            "this side thread",
            "this side conversation",
            "in the side thread",
            "in this side",
        )
    )


def _is_never_anchor(candidate: dict) -> bool:
    return candidate.get("classification") in {
        "empty",
        "non_human_boundary_or_tool_context",
        "worker_or_reviewer_prompt",
        "admin_or_status_turn",
        "post_status_context",
    }


def _with_rejected_alternatives(decision: dict, candidates: list[dict]) -> dict:
    selected_ref = decision.get("anchor_ref")
    alternatives = []
    for candidate in candidates:
        if candidate.get("source_ref") == selected_ref:
            continue
        classification = candidate.get("classification")
        target_window_outside_candidate = bool(candidate.get("target_window_hits") and candidate.get("outside_target_task_ids"))
        if target_window_outside_candidate or candidate.get("decision") == "rejected" or classification in {
            "admin_or_status_turn",
            "worker_or_reviewer_prompt",
            "non_human_boundary_or_tool_context",
            "post_status_context",
        }:
            alternative = {
                "source_ref": candidate.get("source_ref"),
                "timestamp_utc": candidate.get("timestamp_utc"),
                "classification": classification,
                "decision": candidate.get("decision"),
                "rationale": candidate.get("rationale"),
                "snippet": candidate.get("snippet"),
                "source_kind": candidate.get("source_kind"),
                "source_scope": candidate.get("source_scope"),
                "openable_source_ref": candidate.get("openable_source_ref"),
                "openable_target": candidate.get("openable_target"),
                "target_window_hits": candidate.get("target_window_hits") or [],
                "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
            }
            if candidate.get("lineage_rejection_reason"):
                alternative["lineage_rejection_reason"] = candidate.get("lineage_rejection_reason")
            if candidate.get("lineage_resolution"):
                alternative["lineage_resolution"] = candidate.get("lineage_resolution")
            alternatives.append(alternative)
        if len(alternatives) >= 8:
            break
    return {**decision, "rejected_anchor_alternatives": alternatives}


def _current_status_message(messages: list[dict], *, query: str | None = None) -> dict | None:
    status_messages = [
        (index, message)
        for index, message in enumerate(messages)
        if is_status_question(str(message.get("snippet") or ""))
        and not _is_status_boundary_prompt_noise(message)
        and (
            not query
            or not _requires_query_equivalent_status_boundary(query)
            or _status_boundary_is_query_equivalent(query, str(message.get("snippet") or ""))
        )
    ]
    if not status_messages:
        return None
    first_index, first_message = status_messages[0]
    first_timestamp = str(first_message.get("timestamp_utc") or "")
    same_boundary_rows = [
        (index, message)
        for index, message in status_messages
        if str(message.get("timestamp_utc") or "") == first_timestamp
    ]
    for index, message in same_boundary_rows:
        if message.get("source_kind") == "codex_history":
            current = dict(message)
            current["message_index"] = index
            current["thread_keys"] = _message_thread_keys(message)
            return current
    current = dict(first_message)
    current["message_index"] = first_index
    current["thread_keys"] = _message_thread_keys(first_message)
    return current


def _requires_query_equivalent_status_boundary(query: str) -> bool:
    return (
        _relative_status_requires_history_first_anchor(query)
        and not _prefers_parent_main_anchor(query)
        and not _explicit_side_thread_local_status(query)
    )


def _status_boundary_is_query_equivalent(query: str, candidate_text: str) -> bool:
    normalized_query = _normalize(query).strip(" .?!")
    normalized_candidate = _normalize(candidate_text).strip(" .?!")
    if normalized_query and normalized_query == normalized_candidate:
        return True
    query_words = re.sub(r"[^a-z0-9]+", " ", normalized_query).split()
    candidate_words = re.sub(r"[^a-z0-9]+", " ", normalized_candidate).split()
    return bool(
        query_words
        and candidate_words
        and len(candidate_words) <= len(query_words) + 4
        and _relative_status_requires_history_first_anchor(candidate_text)
        and "last" in candidate_words
        and "message" in candidate_words
    )


def _latest_eligible_canonical_history_anchor(candidates: list[dict]) -> dict | None:
    chronological = sorted(candidates, key=lambda item: int(item.get("message_index", 0)))
    return _first_canonical_history_anchor(chronological, set(), parent_only=False)


def _is_status_boundary_prompt_noise(message: dict) -> bool:
    text = str(message.get("snippet") or "")
    normalized = _normalize(text)
    if not normalized:
        return True
    if any(marker in normalized for marker in STATUS_BOUNDARY_PROMPT_MARKERS):
        return True
    if _looks_like_task_prompt(normalized):
        return True
    return any(_mirror_source_looks_like_prompt_noise(mirror) for mirror in message.get("mirror_sources") or [])


def _looks_like_task_prompt(normalized: str) -> bool:
    taskish = (
        "acceptance criteria:" in normalized
        or "branch:" in normalized
        or "checks run" in normalized
        or "current head" in normalized
        or "do not revert" in normalized
        or "review artifact" in normalized
        or "run:" in normalized
        or "work only in" in normalized
        or "worktree:" in normalized
    )
    role_or_process = (
        "fix worker" in normalized
        or "implementation worker" in normalized
        or "implementer" in normalized
        or "reviewer" in normalized
        or "worker" in normalized
        or "failed review" in normalized
        or "pressure run" in normalized
        or "eval" in normalized
    )
    return taskish and role_or_process


def _mirror_source_looks_like_prompt_noise(mirror: object) -> bool:
    if not isinstance(mirror, Mapping):
        return False
    text = _normalize(
        " ".join(
            str(mirror.get(key) or "")
            for key in (
                "source_ref",
                "source_identifier",
                "thread_key",
            )
        )
    )
    return any(
        marker in text
        for marker in (
            "worker",
            "reviewer",
            "pressure",
            "eval",
            "manual_default",
            "jsonl_current",
            "jsonl-layered",
        )
    )


def _first_eligible_thread_anchor(candidates: list[dict], thread_keys: set[str], *, same_thread: bool) -> dict | None:
    for candidate in candidates:
        overlaps = _thread_key_overlap(candidate, thread_keys)
        if overlaps != same_thread:
            continue
        if _is_eligible_anchor(candidate):
            return candidate
    return None


def _first_history_parent_anchor(candidates: list[dict], thread_keys: set[str]) -> dict | None:
    return _first_canonical_history_anchor(candidates, thread_keys, parent_only=True)


def _first_canonical_history_anchor(candidates: list[dict], thread_keys: set[str], *, parent_only: bool) -> dict | None:
    for candidate in candidates:
        if parent_only and _thread_key_overlap(candidate, thread_keys):
            continue
        if _is_never_anchor(candidate):
            continue
        if candidate.get("decision") not in {"selected_candidate", "fallback_candidate"}:
            continue
        if candidate.get("source_kind") == "codex_history":
            return candidate
        history_mirror = _history_mirror_candidate(candidate)
        if history_mirror:
            return history_mirror
    return None


def _unproven_parent_main_anchor_alternatives(
    candidates: list[dict],
    thread_keys: set[str],
    *,
    include_same_thread_unproven: bool = False,
) -> list[dict]:
    alternatives = []
    for candidate in candidates:
        is_never_anchor = _is_never_anchor(candidate)
        is_eligible_candidate = _is_eligible_anchor(candidate)
        if not include_same_thread_unproven:
            is_eligible_candidate = is_eligible_candidate and not _thread_key_overlap(candidate, thread_keys)
        if is_never_anchor or is_eligible_candidate:
            alternatives.append(
                {
                    "source_ref": candidate.get("source_ref"),
                    "timestamp_utc": candidate.get("timestamp_utc"),
                    "classification": candidate.get("classification"),
                    "decision": "rejected",
                    "rationale": _parent_main_rejection_rationale(candidate),
                    "snippet": candidate.get("snippet"),
                    "source_kind": candidate.get("source_kind"),
                    "logical_message_id": candidate.get("logical_message_id"),
                    "correlation_status": candidate.get("correlation_status"),
                    "correlation_diagnostics": candidate.get("correlation_diagnostics") or [],
                    "openable_source_ref": candidate.get("openable_source_ref"),
                    "openable_target": candidate.get("openable_target"),
                    "target_window_hits": candidate.get("target_window_hits") or [],
                    "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
                }
            )
        if len(alternatives) >= 8:
            break
    return alternatives


def _unproven_parent_main_status_boundary_alternatives(candidates: list[dict]) -> list[dict]:
    alternatives = []
    for candidate in candidates:
        if not _is_never_anchor(candidate) and not _is_eligible_anchor(candidate):
            continue
        alternatives.append(
            {
                "source_ref": candidate.get("source_ref"),
                "timestamp_utc": candidate.get("timestamp_utc"),
                "classification": candidate.get("classification"),
                "decision": "rejected",
                "rationale": _parent_main_boundary_missing_rationale(candidate),
                "snippet": candidate.get("snippet"),
                "source_kind": candidate.get("source_kind"),
                "openable_source_ref": candidate.get("openable_source_ref"),
                "openable_target": candidate.get("openable_target"),
                "target_window_hits": candidate.get("target_window_hits") or [],
                "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
            }
        )
        if len(alternatives) >= 8:
            break
    return alternatives


def _parent_main_rejection_rationale(candidate: dict) -> str:
    source_kind = candidate.get("source_kind")
    if source_kind == "codex_sessions" and _is_eligible_anchor(candidate):
        return (
            str(candidate.get("rationale") or "").rstrip()
            + "; rejected for explicit parent/main status because no codex_history:history.jsonl parent anchor or history mirror proves this row"
        )
    return str(candidate.get("rationale") or "")


def _parent_main_boundary_missing_rationale(candidate: dict) -> str:
    rationale = str(candidate.get("rationale") or "").rstrip()
    suffix = (
        "rejected for explicit parent/main status because the current status-question boundary was not found "
        "in the searched history/session candidates; selecting a ranked latest interval would be ambiguous"
    )
    if candidate.get("source_kind") == "codex_sessions" and _is_eligible_anchor(candidate):
        suffix += " and no canonical history.jsonl parent anchor or history mirror proves this row"
    return f"{rationale}; {suffix}" if rationale else suffix


def _is_eligible_anchor(candidate: dict) -> bool:
    return candidate.get("decision") in {"selected_candidate", "fallback_candidate"} and int(candidate.get("score") or 0) > 0


def _thread_key_overlap(candidate: dict, thread_keys: set[str]) -> bool:
    if not thread_keys:
        return False
    candidate_keys = set(candidate.get("thread_keys") or [])
    return bool(candidate_keys & thread_keys)


def _message_thread_keys(message: dict) -> list[str]:
    keys = {str(key) for key in message.get("thread_keys", []) if key}
    key = message.get("thread_key")
    if key:
        keys.add(str(key))
    identifier = message.get("source_identifier")
    if identifier:
        keys.add(str(identifier))
    for mirror in message.get("mirror_sources") or []:
        if not isinstance(mirror, dict):
            continue
        mirror_key = mirror.get("thread_key") or mirror.get("source_identifier")
        if mirror_key:
            keys.add(str(mirror_key))
    return sorted(keys)


def _history_mirror_candidate(candidate: dict) -> dict | None:
    for mirror in candidate.get("mirror_sources") or []:
        if not isinstance(mirror, dict):
            continue
        if mirror.get("source_kind") != "codex_history":
            continue
        selected = dict(candidate)
        for key in (
            "source_kind",
            "source_ref",
            "source_identifier",
            "thread_key",
            "openable_source_ref",
            "openable_target",
        ):
            if key in mirror:
                selected[key] = mirror.get(key)
        selected["thread_keys"] = _message_thread_keys(selected)
        selected["rationale"] = (
            str(selected.get("rationale") or "").rstrip()
            + "; selected the canonical history.jsonl mirror for explicit parent/main-thread intent"
        )
        return selected
    return None


def _selected_with_reason(candidate: dict, reason_suffix: str) -> dict:
    selected = dict(candidate)
    selected["rationale"] = str(selected.get("rationale") or "").rstrip() + reason_suffix
    return selected


def _attach_logical_message_identity(selected: dict, candidates: list[dict]) -> dict:
    selected_ref = str(selected.get("source_ref") or "")
    for candidate in candidates:
        if str(candidate.get("source_ref") or "") != selected_ref:
            continue
        return {**candidate, **selected}
    return selected


def _anchor_decision_fields(anchor_decision: dict, selected_anchor: dict) -> dict:
    return {
        "context_boundary": anchor_decision.get("context_boundary"),
        "anchor_scope": anchor_decision.get("anchor_scope"),
        "anchor_reason": anchor_decision.get("anchor_reason"),
        "anchor_ref": anchor_decision.get("anchor_ref") or selected_anchor.get("source_ref"),
    }


def _demote_unproven_parent_main_selected_anchor(
    *,
    selected_anchor: dict | None,
    anchor_decision: dict,
) -> tuple[dict | None, dict]:
    if not selected_anchor or not _is_parent_main_status_decision(anchor_decision):
        return selected_anchor, anchor_decision
    if _is_proven_parent_main_history_anchor(selected_anchor):
        return selected_anchor, anchor_decision
    rejected = _rejected_unproven_parent_main_selected_anchor(selected_anchor)
    alternatives = [rejected]
    for item in anchor_decision.get("rejected_anchor_alternatives") or []:
        if item.get("source_ref") == rejected.get("source_ref"):
            continue
        alternatives.append(item)
    return None, {
        **anchor_decision,
        "context_boundary": "parent_main_selected_anchor_unproven",
        "anchor_scope": "parent_thread",
        "anchor_reason": "no_proven_parent_main_history_anchor_found",
        "anchor_ref": None,
        "selected_anchor": None,
        "rejected_anchor_alternatives": alternatives[:8],
        "answer_readiness_demoted_selected_anchor": rejected.get("source_ref"),
    }


def _apply_parent_main_anchor_shortlist_contract(
    *,
    query: str,
    selected_anchor: dict | None,
    anchor_decision: dict,
    anchor_candidates: list[dict],
    canonical_parent_main_interval: dict,
    cwd: Path,
    codex_home: Path | None,
) -> tuple[dict | None, dict, dict]:
    if not (
        _prefers_parent_main_anchor(query)
        or _is_parent_main_complaint_request(query)
        or str(anchor_decision.get("context_boundary") or "").startswith("natural_status_duplicate_prior_parent_main")
    ):
        return selected_anchor, anchor_decision, {
            "contract": "parent_main_anchor_shortlist",
            "status": "not_applicable",
            "answer_allowed": True,
            "candidate_count": 0,
            "canonical_high_confidence_count": 0,
            "candidates": [],
        }

    canonical_candidates = _parent_main_canonical_shortlist_candidates(
        canonical_parent_main_interval=canonical_parent_main_interval,
        selected_anchor=selected_anchor,
    )
    high_confidence_refs = _parent_main_high_confidence_refs(canonical_candidates)
    selected_ref = str((selected_anchor or {}).get("source_ref") or "")
    literal_parent_contract_anchor = _literal_parent_thread_contract_anchor(canonical_parent_main_interval)
    literal_parent_contract_selected = not _requires_raw_parent_thread_boundary(query) or bool(
        literal_parent_contract_anchor
        and selected_ref == literal_parent_contract_anchor.get("source_ref")
    )
    unique_selected = (
        bool(selected_anchor)
        and _is_proven_parent_main_history_anchor(selected_anchor)
        and selected_ref in high_confidence_refs
        and len(high_confidence_refs) == 1
        and literal_parent_contract_selected
    )
    diagnostic_candidates = _parent_main_diagnostic_shortlist_candidates(
        anchor_candidates=anchor_candidates,
        rejected=(anchor_decision.get("rejected_anchor_alternatives") or []),
        canonical_refs={str(candidate.get("source_ref") or "") for candidate in canonical_candidates},
    )
    status = "selected" if unique_selected else "anchor_ambiguous"
    answer_allowed = bool(unique_selected)
    selected_shortlist_ref = selected_ref if unique_selected else None
    shortlist_candidates = []
    for candidate in canonical_candidates:
        ref = str(candidate.get("source_ref") or "")
        if ref in high_confidence_refs:
            confidence = "high"
            ambiguity = "unique_high_confidence" if len(high_confidence_refs) == 1 else "multiple_high_confidence"
        else:
            confidence = "medium"
            ambiguity = "older_canonical_parent_main_candidate"
        decision = "selected" if unique_selected and ref == selected_ref else "rejected"
        if status == "anchor_ambiguous" and ref in high_confidence_refs:
            decision = "ambiguous"
        shortlist_candidates.append(
            _parent_main_shortlist_item(
                candidate,
                source_scope="canonical_parent_main_history",
                decision=decision,
                confidence_label=confidence,
                ambiguity_label=ambiguity,
                cwd=cwd,
                codex_home=codex_home,
            )
        )
    for candidate in diagnostic_candidates:
        shortlist_candidates.append(
            _parent_main_shortlist_item(
                candidate,
                source_scope=_parent_main_candidate_scope(candidate),
                decision="diagnostic_rejected",
                confidence_label="not_anchor",
                ambiguity_label=_parent_main_diagnostic_ambiguity_label(candidate),
                cwd=cwd,
                codex_home=codex_home,
            )
        )
        if len(shortlist_candidates) >= 10:
            break

    recovery_commands = [
        item.get("read_more_command")
        for item in shortlist_candidates
        if item.get("read_more_command") and item.get("decision") in {"ambiguous", "diagnostic_rejected"}
    ][:4]
    recovery = {
        "read_more_commands": recovery_commands,
        "rerun_instruction": (
            "Rerun what-happened-since with the intended canonical history.jsonl timestamp or quoted message "
            "after opening the ambiguous/recovery refs."
        ),
    }
    shortlist = {
        "contract": "parent_main_anchor_shortlist",
        "status": status,
        "answer_allowed": answer_allowed,
        "selected_anchor": selected_shortlist_ref,
        "selection_rule": (
            "Select exactly one unique high-confidence canonical codex_history:history.jsonl:<line> "
            "parent/main human row; later current-loop, worker, reviewer, session-only, task, report, "
            "commit, and repo-state rows are diagnostic or recovery material only."
        ),
        "stop_condition": (
            "Do not synthesize a parent/main status answer unless parent_main_anchor_shortlist.status is "
            "selected and answer_allowed is true. If ambiguous, open recovery refs and rerun with the "
            "intended timestamp or quoted message."
        ),
        "canonical_high_confidence_count": len(high_confidence_refs),
        "candidate_count": len(shortlist_candidates),
        "candidates": shortlist_candidates,
        "recovery": recovery,
    }
    if unique_selected or not selected_anchor:
        return selected_anchor, anchor_decision, shortlist

    rejected = _shortlist_rejected_anchor_alternatives(shortlist_candidates)
    for item in anchor_decision.get("rejected_anchor_alternatives") or []:
        if item.get("source_ref") in {alt.get("source_ref") for alt in rejected}:
            continue
        rejected.append(item)
        if len(rejected) >= 8:
            break
    updated_decision = {
        **anchor_decision,
        "context_boundary": (
            "parent_main_anchor_shortlist_ambiguous"
            if high_confidence_refs
            else anchor_decision.get("context_boundary")
        ),
        "anchor_scope": "parent_thread",
        "anchor_reason": (
            "parent_main_anchor_shortlist_requires_unique_high_confidence_canonical_history_anchor"
            if high_confidence_refs
            else anchor_decision.get("anchor_reason")
        ),
        "anchor_ref": None,
        "selected_anchor": None,
        "rejected_anchor_alternatives": rejected[:8],
        "parent_main_anchor_shortlist_status": status,
    }
    return None, updated_decision, shortlist


def _parent_main_canonical_shortlist_candidates(
    *,
    canonical_parent_main_interval: dict,
    selected_anchor: dict | None,
) -> list[dict]:
    candidates = [dict(candidate) for candidate in canonical_parent_main_interval.get("anchor_candidates") or []]
    selected = canonical_parent_main_interval.get("selected_anchor") or selected_anchor
    if selected and not any(candidate.get("source_ref") == selected.get("source_ref") for candidate in candidates):
        candidates.insert(0, dict(selected))
    return candidates[:8]


def _parent_main_high_confidence_refs(candidates: list[dict]) -> set[str]:
    candidates = [
        candidate
        for candidate in candidates
        if not candidate.get("lineage_rejection_reason") and candidate.get("decision") != "rejected"
    ]
    if not candidates:
        return set()
    top = candidates[0]
    top_score = int(top.get("score") or 0)
    top_timestamp = str(top.get("timestamp_utc") or "")
    refs = {str(top.get("source_ref") or "")}
    for candidate in candidates[1:]:
        if int(candidate.get("score") or 0) == top_score and str(candidate.get("timestamp_utc") or "") == top_timestamp:
            refs.add(str(candidate.get("source_ref") or ""))
    return {ref for ref in refs if ref}


def _parent_main_diagnostic_shortlist_candidates(
    *,
    anchor_candidates: list[dict],
    rejected: list[dict],
    canonical_refs: set[str],
) -> list[dict]:
    diagnostics = []
    seen = set(canonical_refs)
    for collection in (rejected, anchor_candidates):
        for candidate in collection:
            ref = str(candidate.get("source_ref") or "")
            if not ref or ref in seen:
                continue
            if (
                candidate.get("source_kind") == "codex_history"
                or candidate.get("source_kind") == "codex_sessions"
                or candidate.get("classification")
                in {
                    "worker_or_reviewer_prompt",
                    "non_human_boundary_or_tool_context",
                    "admin_or_status_turn",
                    "post_status_context",
                }
            ):
                diagnostics.append(candidate)
                seen.add(ref)
            if len(diagnostics) >= 6:
                return diagnostics
    return diagnostics


def _parent_main_shortlist_item(
    candidate: dict,
    *,
    source_scope: str,
    decision: str,
    confidence_label: str,
    ambiguity_label: str,
    cwd: Path,
    codex_home: Path | None,
) -> dict:
    lineage = candidate.get("lineage_resolution")
    lineage_reason = candidate.get("lineage_rejection_reason")
    if not lineage_reason and codex_home is not None and candidate.get("source_kind") == "codex_sessions":
        computed_lineage = _lineage_for_anchor(candidate, codex_home=codex_home, cwd=cwd)
        computed_reason = _parent_thread_lineage_rejection_reason(computed_lineage)
        if computed_reason:
            lineage = computed_lineage
            lineage_reason = computed_reason
            source_scope = "structural_lineage_rejected_parent_main_candidate"
            ambiguity_label = computed_reason
    item = {
        "source_ref": candidate.get("source_ref"),
        "source_kind": candidate.get("source_kind"),
        "source_scope": candidate.get("source_scope") or source_scope,
        "timestamp_utc": candidate.get("timestamp_utc"),
        "classification": candidate.get("classification"),
        "decision": decision,
        "confidence_label": confidence_label,
        "ambiguity_label": ambiguity_label,
        "rationale": candidate.get("rationale"),
        "snippet": candidate.get("snippet"),
        "openable_source_ref": candidate.get("openable_source_ref"),
        "openable_target": candidate.get("openable_target"),
        "target_window_hits": candidate.get("target_window_hits") or [],
        "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
    }
    if item.get("openable_target"):
        item["openable_target"] = str(item["openable_target"])
        item["read_more_command"] = candidate.get("read_more_command") or _read_more_command(
            str(item["openable_target"]),
            cwd=cwd,
            codex_home=codex_home,
        )
    if candidate.get("parent_thread_lineage_anchor"):
        item["parent_thread_lineage_anchor"] = candidate.get("parent_thread_lineage_anchor")
    if lineage:
        item["lineage_resolution"] = lineage
    if lineage_reason:
        item["lineage_rejection_reason"] = lineage_reason
    return item


def _parent_main_candidate_scope(candidate: dict) -> str:
    if candidate.get("lineage_rejection_reason"):
        return "structural_lineage_rejected_parent_main_candidate"
    if candidate.get("classification") in {"worker_or_reviewer_prompt", "non_human_boundary_or_tool_context"}:
        return "worker_reviewer_or_current_loop_diagnostic"
    if candidate.get("source_kind") == "codex_history":
        return "canonical_history_rejected_or_boundary"
    if candidate.get("source_kind") == "codex_sessions":
        return "session_only_unproven_parent_main_candidate"
    return "diagnostic_anchor_candidate"


def _parent_main_diagnostic_ambiguity_label(candidate: dict) -> str:
    if candidate.get("lineage_rejection_reason"):
        return str(candidate.get("lineage_rejection_reason"))
    classification = candidate.get("classification")
    if classification == "worker_or_reviewer_prompt":
        return "worker_or_reviewer_row_not_parent_main_anchor"
    if classification in {"non_human_boundary_or_tool_context", "admin_or_status_turn", "post_status_context"}:
        return "later_current_loop_or_boundary_context_not_anchor"
    if candidate.get("source_kind") == "codex_sessions":
        return "missing_canonical_history_proof"
    return "rejected_diagnostic_only"


def _shortlist_rejected_anchor_alternatives(shortlist_candidates: list[dict]) -> list[dict]:
    alternatives = []
    for item in shortlist_candidates:
        if item.get("decision") == "selected":
            continue
        alternatives.append(
            {
                "source_ref": item.get("source_ref"),
                "timestamp_utc": item.get("timestamp_utc"),
                "classification": item.get("classification"),
                "decision": "rejected" if item.get("decision") == "diagnostic_rejected" else item.get("decision"),
                "rationale": item.get("rationale"),
                "snippet": item.get("snippet"),
                "source_kind": item.get("source_kind"),
                "source_scope": item.get("source_scope"),
                "confidence_label": item.get("confidence_label"),
                "ambiguity_label": item.get("ambiguity_label"),
                "openable_source_ref": item.get("openable_source_ref"),
                "openable_target": item.get("openable_target"),
                "read_more_command": item.get("read_more_command"),
                "lineage_rejection_reason": item.get("lineage_rejection_reason"),
                "lineage_resolution": item.get("lineage_resolution"),
                "target_window_hits": item.get("target_window_hits") or [],
                "outside_target_task_ids": item.get("outside_target_task_ids") or [],
            }
        )
        if len(alternatives) >= 8:
            break
    return alternatives


def _is_parent_main_status_decision(anchor_decision: dict) -> bool:
    context_boundary = str(anchor_decision.get("context_boundary") or "")
    anchor_reason = str(anchor_decision.get("anchor_reason") or "")
    return (
        context_boundary.startswith("explicit_parent_main")
        or context_boundary.startswith("relative_status_history")
        or context_boundary.startswith("prior_request_occurrence")
        or context_boundary.startswith("natural_status_duplicate_prior_parent_main")
        or context_boundary == "parent_main_selected_anchor_unproven"
        or context_boundary == "parent_main_anchor_shortlist_ambiguous"
        or anchor_reason in {
            "first_side_thread_user_message_after_boundary",
            "parent_main_last_actual_work_handoff",
            "unique_prior_request_occurrence_parent_main_anchor",
            "prior_request_occurrence_not_unique",
            "prior_request_occurrence_preceding_anchor_not_proven",
            "no_proven_parent_main_history_anchor_found",
            "no_status_question_boundary_for_parent_main_anchor",
            "no_canonical_history_anchor_found_for_relative_status",
            "no_status_question_boundary_for_history_first_relative_status",
            "parent_main_anchor_shortlist_requires_unique_high_confidence_canonical_history_anchor",
        }
    )


def _is_proven_parent_main_history_anchor(anchor: dict) -> bool:
    source_ref = str(anchor.get("source_ref") or "")
    source_kind = anchor.get("source_kind")
    if source_kind == "codex_history" and source_ref.startswith("codex_history:history.jsonl:"):
        return True
    for mirror in anchor.get("mirror_sources") or []:
        if not isinstance(mirror, Mapping):
            continue
        mirror_ref = str(mirror.get("source_ref") or "")
        if mirror.get("source_kind") == "codex_history" and mirror_ref.startswith("codex_history:history.jsonl:"):
            return True
    return False


def _literal_parent_thread_contract_anchor(canonical_parent_main_interval: dict) -> dict | None:
    if canonical_parent_main_interval.get("status") != "selected":
        return None
    side_boundary = canonical_parent_main_interval.get("parent_thread_side_boundary") or {}
    selected = canonical_parent_main_interval.get("selected_anchor") or {}
    selected_ref = str(selected.get("source_ref") or "")
    parent_thread_id_hash = str(side_boundary.get("parent_thread_id_hash") or "")
    recovered_identifier = str(side_boundary.get("selected_session_identifier") or "")
    recovered_without_edge = (
        side_boundary.get("selection_mode") == "canonical_history_session_chronology_recovery"
        and selected.get("deterministic_parent_session_recovery_anchor")
        and recovered_identifier
        and selected.get("source_identifier") == recovered_identifier
    )
    if (
        side_boundary.get("status") != "selected"
        or not selected.get("side_parent_boundary_anchor")
        or not selected_ref
        or selected_ref != side_boundary.get("selected_anchor_ref")
        or not (
            recovered_without_edge
            or (
                parent_thread_id_hash
                and selected.get("source_identifier") == f"history-session-{parent_thread_id_hash}"
            )
        )
    ):
        return None
    return selected


def _rejected_unproven_parent_main_selected_anchor(anchor: dict) -> dict:
    rationale = str(anchor.get("rationale") or "").rstrip()
    suffix = (
        "rejected for parent/main status answer readiness because this row is not a canonical "
        "history.jsonl parent/main human anchor or proven history mirror; use only as recovery material"
    )
    return {
        "source_ref": anchor.get("source_ref"),
        "timestamp_utc": anchor.get("timestamp_utc"),
        "classification": anchor.get("classification"),
        "decision": "rejected",
        "rationale": f"{rationale}; {suffix}" if rationale else suffix,
        "snippet": anchor.get("snippet"),
        "source_kind": anchor.get("source_kind"),
        "openable_source_ref": anchor.get("openable_source_ref"),
        "openable_target": anchor.get("openable_target"),
        "read_more_command": anchor.get("read_more_command"),
        "target_window_hits": anchor.get("target_window_hits") or [],
        "outside_target_task_ids": anchor.get("outside_target_task_ids") or [],
    }


def _session_activity_trace(
    selected_anchor: dict | None,
    anchor_decision: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
    target_window: dict | None = None,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
) -> dict:
    base = {
        "status": "no_selected_anchor",
        "anchor_timestamp_utc": None,
        "logical_message_id": (selected_anchor or {}).get("logical_message_id"),
        "canonical_authority": (selected_anchor or {}).get("canonical_authority"),
        "session_activity_location": (selected_anchor or {}).get("session_activity_location"),
        "session_followups": [],
        "activity_after_anchor": _empty_activity_counts(),
        "next_reads": [],
        "evidence_boundary": {
            "primary": "raw Codex history/session JSONL after the selected user anchor",
            "status_as_of_boundary": status_as_of_boundary,
            "audit_run_requested": audit_run_requested,
            "secondary_only": ["commits/merges", "current repo state", "task/report/review artifacts"],
            "commit_only_answers_allowed": False,
        },
        "missing_logs": [],
        "summary": {
            "session_file_count": 0,
            "records_after_anchor": 0,
            "spawned_session_call_count": 0,
        },
    }
    if not selected_anchor:
        return base
    if codex_home is None:
        return {**base, "status": "missing_codex_home", "missing_logs": ["codex_home was not provided"]}

    anchor_timestamp = parse_datetime(str(selected_anchor.get("timestamp_utc") or ""), timezone.utc)
    if anchor_timestamp is None:
        return {
            **base,
            "status": "missing_anchor_timestamp",
            "missing_logs": ["selected anchor did not include a parseable timestamp"],
        }
    anchor_timestamp = anchor_timestamp.astimezone(timezone.utc)
    contexts = _session_contexts_for_anchor(selected_anchor, codex_home, role="selected_anchor")
    status_ref = anchor_decision.get("status_ref")
    if status_ref and not selected_anchor.get("side_parent_boundary_anchor"):
        contexts.extend(_session_contexts_for_history_ref(str(status_ref), codex_home, role="status_question"))

    unique_contexts = _dedupe_session_contexts(contexts)
    if not unique_contexts:
        return {
            **base,
            "status": "no_session_context_found",
            "anchor_timestamp_utc": anchor_timestamp.isoformat(timespec="seconds"),
            "missing_logs": ["no raw session file could be derived from the selected/history source refs"],
        }

    activity = _empty_activity_counts()
    followups = []
    next_reads = []
    missing = []
    for context in unique_contexts:
        session_files = list(context.get("session_files") or [])
        if not session_files:
            missing.append(f"{context['role']} session file not found from {context.get('history_source_ref') or context.get('source_ref')}")
        for path in session_files:
            activity_boundary = parse_datetime(str(context.get("session_anchor_timestamp_utc") or ""), timezone.utc)
            if activity_boundary is None:
                activity_boundary = anchor_timestamp
            scan = _scan_session_file_after_anchor(
                path,
                activity_boundary.astimezone(timezone.utc),
                codex_home=codex_home,
                cwd=cwd,
                after_line=context.get("session_anchor_line"),
                logical_message_id=selected_anchor.get("logical_message_id"),
                target_window=target_window,
                status_as_of_boundary=status_as_of_boundary,
                audit_run_requested=audit_run_requested,
            )
            _merge_activity_counts(activity, scan["activity_after_anchor"])
            followups.append(
                {
                    **{key: value for key, value in context.items() if key != "session_files"},
                    "session_file": str(path),
                    "session_source_ref": _source_ref_for_path_line(path, None, codex_home),
                    "logical_message_id": selected_anchor.get("logical_message_id"),
                    "session_anchor_line": context.get("session_anchor_line"),
                    "session_anchor_timestamp_utc": context.get("session_anchor_timestamp_utc"),
                    "activity_boundary_policy": "strictly_after_correlated_session_user_row",
                    "records_after_anchor": scan["records_after_anchor"],
                    "first_after_anchor_ref": scan["first_after_anchor_ref"],
                    "last_after_anchor_ref": scan["last_after_anchor_ref"],
                    "spawned_session_call_refs": scan["spawned_session_call_refs"],
                    "raw_jsonl_primary_evidence": True,
                }
            )
            next_reads.extend(scan["next_reads"])

    summary = {
        "session_file_count": len({item["session_file"] for item in followups}),
        "records_after_anchor": activity["records_after_anchor"],
        "spawned_session_call_count": activity["spawned_session_calls"],
    }
    status = "raw_session_trace_found" if summary["session_file_count"] and summary["records_after_anchor"] else "raw_session_trace_empty"
    if missing and status == "raw_session_trace_found":
        status = "partial_raw_session_trace_found"
    return {
        **base,
        "status": status,
        "anchor_timestamp_utc": anchor_timestamp.isoformat(timespec="seconds"),
        "session_followups": followups,
        "activity_after_anchor": activity,
        "next_reads": _dedupe_reads(next_reads),
        "missing_logs": missing,
        "summary": summary,
    }


def _demote_unqualified_out_of_band_anchor_without_activity(
    *,
    query: str,
    selected_anchor: dict | None,
    anchor_decision: dict,
    session_activity: dict,
) -> tuple[dict | None, dict, dict]:
    if (
        not selected_anchor
        or anchor_decision.get("context_boundary") != "out_of_band_plain_latest_human"
        or not _requires_query_equivalent_status_boundary(query)
        or session_activity.get("status") in {"raw_session_trace_found", "partial_raw_session_trace_found"}
    ):
        return selected_anchor, anchor_decision, session_activity

    rejected = {
        "source_ref": selected_anchor.get("source_ref"),
        "timestamp_utc": selected_anchor.get("timestamp_utc"),
        "classification": selected_anchor.get("classification"),
        "decision": "rejected",
        "rationale": (
            "latest eligible canonical human candidate had no raw post-anchor activity; "
            "request an explicit quote or timestamp instead of treating it as a selected anchor"
        ),
        "snippet": selected_anchor.get("snippet"),
        "source_kind": selected_anchor.get("source_kind"),
        "openable_source_ref": selected_anchor.get("openable_source_ref"),
        "openable_target": selected_anchor.get("openable_target"),
        "unqualified_latest_human_activity_required": True,
    }
    demoted_decision = {
        **anchor_decision,
        "context_boundary": "out_of_band_plain_latest_human_activity_missing",
        "anchor_scope": "current_user_message_chronology",
        "anchor_reason": "latest_canonical_human_anchor_has_no_raw_post_anchor_activity",
        "anchor_ref": None,
        "selected_anchor": None,
        "rejected_anchor_alternatives": [
            rejected,
            *(anchor_decision.get("rejected_anchor_alternatives") or []),
        ][:8],
    }
    demoted_activity = {
        **session_activity,
        "status": "no_selected_anchor",
        "anchor_timestamp_utc": None,
        "session_followups": [],
        "activity_after_anchor": _empty_activity_counts(),
        "next_reads": [],
        "missing_logs": [
            "latest eligible canonical human candidate had no raw post-anchor activity; explicit anchor required"
        ],
        "summary": {
            "session_file_count": 0,
            "records_after_anchor": 0,
            "spawned_session_call_count": 0,
        },
    }
    return None, demoted_decision, demoted_activity


def _empty_activity_counts() -> dict:
    return {
        "records_after_anchor": 0,
        "conversation_actions": 0,
        "human_user_messages": 0,
        "assistant_or_agent_messages": 0,
        "tool_calls": 0,
        "tool_outputs": 0,
        "spawned_session_calls": 0,
        "compacted_summaries": 0,
        "unknown_records": 0,
        "commits_merges": "secondary_evidence_only_not_anchor_source",
        "current_repo_state": "secondary_evidence_only_after_raw_jsonl_trace",
    }


def _merge_activity_counts(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, int):
            target[key] = int(target.get(key, 0)) + value


def _session_contexts_for_anchor(anchor: dict, codex_home: Path, *, role: str) -> list[dict]:
    contexts = []
    activity_location = anchor.get("session_activity_location")
    if isinstance(activity_location, Mapping) and activity_location.get("source_kind") == "codex_sessions":
        context = _session_context_from_openable(
            activity_location,
            role=f"{role}_activity_location",
            logical_message_id=anchor.get("logical_message_id"),
        )
        if context:
            return [context]
    source_ref = str(anchor.get("source_ref") or "")
    if source_ref.startswith("codex_history:"):
        contexts.extend(_session_contexts_for_history_ref(source_ref, codex_home, role=role))
    if anchor.get("source_kind") == "codex_sessions":
        context = _session_context_from_openable(
            anchor,
            role=role,
            logical_message_id=anchor.get("logical_message_id"),
        )
        if context:
            contexts.append(context)
    for mirror in anchor.get("mirror_sources") or []:
        if not isinstance(mirror, dict):
            continue
        if mirror.get("source_kind") == "codex_sessions":
            context = _session_context_from_openable(
                mirror,
                role=f"{role}_mirror",
                logical_message_id=anchor.get("logical_message_id"),
            )
            if context:
                contexts.append(context)
    return contexts


def _apply_parent_thread_lineage_to_ranked_candidates(
    *,
    query: str,
    candidates: list[dict],
    cwd: Path,
    codex_home: Path | None,
) -> list[dict]:
    if codex_home is None or not is_status_question(query) or not (
        _prefers_parent_main_anchor(query) or _relative_status_requires_history_first_anchor(query)
    ):
        return candidates
    updated = []
    for candidate in candidates:
        if candidate.get("source_kind") != "codex_sessions" and not candidate.get("session_activity_location"):
            updated.append(candidate)
            continue
        lineage = candidate.get("lineage_resolution") or _lineage_for_anchor(candidate, codex_home=codex_home, cwd=cwd)
        reason = candidate.get("lineage_rejection_reason") or _parent_thread_lineage_rejection_reason(lineage)
        if not reason:
            updated.append(candidate)
            continue
        enriched = dict(candidate)
        enriched.update(
            {
                "decision": "rejected",
                "source_scope": "structural_lineage_rejected_parent_main_candidate",
                "lineage_resolution": lineage,
                "lineage_rejection_reason": reason,
                "rationale": _append_rejection_rationale(candidate.get("rationale"), reason),
            }
        )
        updated.append(enriched)
    return updated


def _enrich_selected_parent_thread_lineage(
    selected_anchor: dict,
    *,
    anchor_decision: dict,
    cwd: Path,
    codex_home: Path | None,
) -> dict:
    if codex_home is None or selected_anchor.get("parent_thread_lineage_anchor"):
        return selected_anchor
    if anchor_decision.get("anchor_scope") != "parent_thread":
        return selected_anchor
    if selected_anchor.get("source_kind") not in {"codex_history", "codex_sessions"}:
        return selected_anchor
    lineage = _lineage_for_anchor(selected_anchor, codex_home=codex_home, cwd=cwd)
    if lineage.get("status") != "ok" or _parent_thread_lineage_rejection_reason(lineage):
        return selected_anchor
    enriched = dict(selected_anchor)
    enriched["parent_thread_lineage_anchor"] = True
    enriched["lineage_resolution"] = lineage
    enriched["rationale"] = (
        str(enriched.get("rationale") or "").rstrip()
        + "; confirmed by structural parent/root lineage, cwd, workspace-root, and role/source metadata"
    )
    return enriched


def _session_contexts_for_history_ref(source_ref: str, codex_home: Path, *, role: str) -> list[dict]:
    path = codex_home / "history.jsonl"
    line_number = _line_from_source_ref(source_ref)
    if line_number is None:
        return []
    record = _read_jsonl_line(path, line_number)
    if not isinstance(record, Mapping):
        return []
    session_id = record.get("session_id")
    timestamp = parse_datetime(record.get("ts") or record.get("timestamp") or record.get("created_at"), timezone.utc)
    return [
        {
            "role": role,
            "history_source_ref": source_ref,
            "history_openable_target": f"{path}:{line_number}",
            "session_id_hash": _hash_value(session_id),
            "history_timestamp_utc": timestamp.astimezone(timezone.utc).isoformat(timespec="seconds") if timestamp else None,
            "session_files": _session_files_for_id(codex_home, session_id),
        }
    ]


def _session_context_from_openable(
    source: Mapping[str, Any],
    *,
    role: str,
    logical_message_id: str | None = None,
) -> dict | None:
    openable = source.get("openable_source_ref") or {}
    path_text = openable.get("path")
    if not path_text:
        target = str(source.get("openable_target") or "")
        path_text = target.rsplit(":", 1)[0] if target else ""
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_file():
        return None
    return {
        "role": role,
        "source_ref": source.get("source_ref"),
        "logical_message_id": logical_message_id,
        "session_id_hash": _hash_value(_session_id_from_filename(path)),
        "correlated_session_identity": source.get("correlated_session_identity"),
        "session_anchor_line": openable.get("line") or _line_from_source_ref(str(source.get("source_ref") or "")),
        "session_anchor_timestamp_utc": source.get("timestamp_utc"),
        "session_files": [path],
    }


def _scan_session_file_after_anchor(
    path: Path,
    anchor_timestamp,
    *,
    codex_home: Path,
    cwd: Path,
    after_line: int | None = None,
    logical_message_id: str | None = None,
    target_window: dict | None = None,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
) -> dict:
    activity = _empty_activity_counts()
    first_ref = None
    last_ref = None
    spawned_refs = []
    next_reads = []
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return {
            "records_after_anchor": 0,
            "activity_after_anchor": activity,
            "first_after_anchor_ref": None,
            "last_after_anchor_ref": None,
            "spawned_session_call_refs": [],
            "next_reads": [],
        }
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            if after_line is not None and line_number <= int(after_line):
                continue
            timestamp = parse_datetime(
                record.get("timestamp") or record.get("ts") or record.get("created_at") or record.get("time"),
                timezone.utc,
            )
            if after_line is None and timestamp is not None and timestamp.astimezone(timezone.utc) <= anchor_timestamp:
                continue
            if _is_newer_than_status_as_of(
                timestamp.astimezone(timezone.utc).isoformat(timespec="seconds") if timestamp is not None else "",
                status_as_of_boundary=status_as_of_boundary,
                audit_run_requested=audit_run_requested,
            ):
                continue
            source_ref = _source_ref_for_path_line(path, line_number, codex_home)
            if first_ref is None:
                first_ref = source_ref
                target = f"{path}:{line_number}"
                next_reads.append(
                    _raw_session_next_read(
                        record,
                        source_ref,
                        target,
                        line_number,
                        path=path,
                        cwd=cwd,
                        codex_home=codex_home,
                        target_window=target_window,
                        logical_message_id=logical_message_id,
                        window_role="session_activity",
                        reason="Inspect raw session JSONL activity after the selected anchor before using commits or repo state.",
                    )
                )
            last_ref = source_ref
            _count_session_record(record, activity)
            if _is_spawned_session_call(record):
                spawned_refs.append(source_ref)
                if len(next_reads) < 4:
                    target = f"{path}:{line_number}"
                    next_reads.append(
                        _raw_session_next_read(
                            record,
                            source_ref,
                            target,
                            line_number,
                            path=path,
                            cwd=cwd,
                            codex_home=codex_home,
                            target_window=target_window,
                            logical_message_id=logical_message_id,
                            window_role="spawned_session_activity",
                            reason="Inspect spawned worker/reviewer session launch as part of the long-absence trace.",
                        )
                    )
    return {
        "records_after_anchor": activity["records_after_anchor"],
        "activity_after_anchor": activity,
        "first_after_anchor_ref": first_ref,
        "last_after_anchor_ref": last_ref,
        "spawned_session_call_refs": spawned_refs[:10],
        "next_reads": next_reads,
    }


def _selected_anchor_raw_session_reads(
    selected_anchor: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
    target_window: dict | None,
) -> list[dict]:
    if codex_home is None:
        return []
    anchor_timestamp = parse_datetime(str(selected_anchor.get("timestamp_utc") or ""), timezone.utc)
    if anchor_timestamp is None:
        return []
    normalized_anchor = _normalize(str(selected_anchor.get("snippet") or ""))
    if not normalized_anchor:
        return []
    contexts = _dedupe_session_contexts(
        _session_contexts_for_anchor(selected_anchor, codex_home, role="selected_anchor_raw_row")
    )
    for context in contexts:
        for path_item in context.get("session_files") or []:
            path = Path(path_item)
            match = _find_raw_session_anchor_row(path, anchor_timestamp, normalized_anchor)
            if not match:
                continue
            line_number, record = match
            source_ref = _source_ref_for_path_line(path, line_number, codex_home)
            target = f"{path}:{line_number}"
            read = _raw_session_next_read(
                record,
                source_ref,
                target,
                line_number,
                path=path,
                cwd=cwd,
                codex_home=codex_home,
                target_window=target_window,
                reason="Inspect the selected parent-thread raw session user row before answering.",
            )
            read.update(
                {
                    "source_tier": "required_primary",
                    "answer_use_required": True,
                    "selected_anchor_raw_session_row": True,
                    "history_anchor_source_ref": selected_anchor.get("source_ref"),
                    "answer_support_role": "selected_parent_thread_raw_user_row",
                    "snippet": selected_anchor.get("snippet"),
                }
            )
            return [read]
    return []


def _find_raw_session_anchor_row(path: Path, anchor_timestamp, normalized_anchor: str) -> tuple[int, Mapping[str, Any]] | None:
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return None
    anchor_utc = anchor_timestamp.astimezone(timezone.utc)
    matches = []
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, Mapping):
                continue
            classification = classify_conversation_record(record)
            if classification.label != HUMAN_AUTHORED_USER_MESSAGE:
                continue
            record_text = _normalize(extract_human_ask(record).ask_text or "")
            if record_text != normalized_anchor:
                continue
            timestamp = parse_datetime(
                record.get("timestamp") or record.get("ts") or record.get("created_at") or record.get("time"),
                timezone.utc,
            )
            if (
                timestamp is None
                or abs((timestamp.astimezone(timezone.utc) - anchor_utc).total_seconds())
                > MIRROR_CORRELATION_WINDOW_SECONDS
            ):
                continue
            matches.append((line_number, record))
    return matches[0] if len(matches) == 1 else None


def _raw_session_next_read(
    record: Mapping[str, Any],
    source_ref: str,
    target: str,
    line_number: int,
    *,
    path: Path,
    cwd: Path,
    codex_home: Path,
    target_window: dict | None,
    reason: str,
    logical_message_id: str | None = None,
    window_role: str = "session_activity",
) -> dict:
    record_text = _record_task_text(record)
    session_source_identity = _source_ref_for_path_line(path, None, codex_home)
    window_material = "|".join(
        (logical_message_id or "unlinked", session_source_identity, window_role, str(line_number))
    )
    read = {
        "reason": reason,
        "source_ref": source_ref,
        "source_kind": "codex_sessions",
        "source_chronology": "pre_evaluation_primary",
        "source_role": "raw_session_activity",
        "logical_message_id": logical_message_id,
        "logical_window_id": "logical-window-" + hashlib.sha256(window_material.encode()).hexdigest()[:16],
        "window_role": window_role,
        "window_source_identity": session_source_identity,
        "window_start_line": line_number,
        "window_end_line": line_number + 8,
        "openable_source_ref": {"path": str(path), "line": line_number},
        "openable_target": target,
        "read_more_command": _read_more_command(target, cwd=cwd, codex_home=codex_home, before=0, after=8),
    }
    if target_window:
        read["target_window_hits"] = _target_window_hits(record_text, target_window)
        read["outside_target_task_ids"] = _outside_target_task_ids(record_text, target_window)
    return read


def _record_task_text(record: Mapping[str, Any]) -> str:
    try:
        return json.dumps(record, sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError):
        return str(record)


def _count_session_record(record: Mapping[str, Any], activity: dict) -> None:
    activity["records_after_anchor"] += 1
    classification = classify_conversation_record(record)
    if classification.label == HUMAN_AUTHORED_USER_MESSAGE:
        activity["human_user_messages"] += 1
        activity["conversation_actions"] += 1
    elif classification.label == ASSISTANT_OR_AGENT_MESSAGE:
        activity["assistant_or_agent_messages"] += 1
        activity["conversation_actions"] += 1
    elif classification.label == TOOL_ACTION_OUTPUT:
        activity["tool_outputs"] += 1
    elif classification.label == COMPACTED_HISTORY_SUMMARY:
        activity["compacted_summaries"] += 1
    else:
        activity["unknown_records"] += 1

    payload = record.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    payload_type = str(payload_map.get("type") or record.get("type") or "").casefold()
    if payload_type in {"function_call", "custom_tool_call", "tool_call", "tool_search_call"}:
        activity["tool_calls"] += 1
    if payload_type in {"function_call_output", "custom_tool_call_output", "tool_call_output", "tool_search_output"}:
        activity["tool_outputs"] += 1
    if _is_spawned_session_call(record):
        activity["spawned_session_calls"] += 1


def _is_spawned_session_call(record: Mapping[str, Any]) -> bool:
    payload = record.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    name = str(payload_map.get("name") or record.get("name") or "")
    return name == "spawn_agent"


def _session_files_for_id(codex_home: Path, session_id: Any) -> list[Path]:
    if not session_id:
        return []
    sessions_root = codex_home / "sessions"
    if not sessions_root.is_dir():
        return []
    needle = str(session_id)
    return sorted(path for path in sessions_root.rglob("*.jsonl") if needle in path.name)


def _dedupe_session_contexts(contexts: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for context in contexts:
        files = tuple(str(path) for path in context.get("session_files") or [])
        key = (
            context.get("logical_message_id"),
            context.get("correlated_session_identity") or context.get("session_id_hash"),
            context.get("session_anchor_line"),
            files,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(context)
    return deduped


def _read_jsonl_line(path: Path, line_number: int) -> dict | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for current, line in enumerate(handle, 1):
                if current == line_number:
                    return json.loads(line)
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _line_from_source_ref(source_ref: str) -> int | None:
    try:
        return int(source_ref.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _source_ref_for_path_line(path: Path, line: int | None, codex_home: Path) -> str:
    try:
        rel = path.resolve().relative_to((codex_home / "sessions").resolve())
        parts = rel.parts
        date_label = "/".join(parts[:3]) if len(parts) >= 4 else "unknown-date"
        base = f"codex_sessions:{date_label}/session-{hashlib.sha256(str(rel).encode()).hexdigest()[:12]}"
    except (OSError, ValueError):
        base = str(path)
    return f"{base}:{line}" if line is not None else base


def _session_id_from_filename(path: Path) -> str:
    stem = path.stem
    if "-" not in stem:
        return stem
    return stem.rsplit("-", 1)[-1]


def _hash_value(value: Any) -> str | None:
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:12]


def _evidence_contract() -> dict:
    return {
        "source_modes": {
            "ground_truth_chat_logs": "Raw Codex history/session/tool evidence used before secondary artifacts.",
            "artifact_assisted_context": "Chronologically legitimate task/report/review/commit context used after raw logs.",
            "post_hoc_diagnostic": "Same-loop, later, answer-key-like, rejected, or diagnostic-only material not counted as primary proof.",
        },
        "source_role_labels": {
            "primary_raw_chat_log": "Raw human chat/history rows such as selected anchors and status boundaries.",
            "primary_tool_or_command_trace": "Raw session/tool/command trace rows from sessions/**/*.jsonl.",
            "legitimate_pre_existing_artifact": "Task/report/review/commit artifact context that is allowed only as secondary support.",
            "post_hoc_answer_key_or_same_loop_echo": "Later/current-loop echoes, pressure artifacts, or answer-key-like material.",
            "diagnostic_only": "Recovery, disambiguation, stale, or optional material that must not be normal answer support.",
        },
        "source_tiers": {
            "required_primary": "Selected anchor plus raw current-window Codex history/session JSONL refs.",
            "required_secondary": "Current-window commits and task/report/review artifacts, only after required primary reads.",
            "diagnostic_optional": "Stale/demo/example artifacts, rejected anchors, broad work products, and other non-answer refs.",
        },
        "primary_evidence_order": [
            "selected user anchor from Codex history/session JSONL",
            "raw sessions/**/*.jsonl activity after that anchor",
            "source refs/read-more windows opened from the raw JSONL trace",
        ],
        "secondary_evidence_only": [
            "commits/merges",
            "task/report/review artifacts",
            "current repo/worktree state",
            "compacted or inherited summaries",
        ],
        "summary_categories_required": [
            "conversation actions",
            "tool calls",
            "spawned worker/reviewer/free-agent sessions",
            "commits/merges as secondary evidence",
            "current repo state as secondary evidence",
            "uncertainty or missing logs",
        ],
        "commit_only_answers_allowed": False,
    }


def _collect_work_artifacts(
    cwd: Path,
    *,
    anchor_timestamp_utc: str | None = None,
    target_window: dict | None = None,
    status_as_of_timestamp_utc: str | None = None,
    audit_run_requested: bool = False,
) -> dict:
    anchor_timestamp = parse_datetime(anchor_timestamp_utc or "", timezone.utc)
    if anchor_timestamp is not None:
        anchor_timestamp = anchor_timestamp.astimezone(timezone.utc)
    status_as_of_timestamp = parse_datetime(status_as_of_timestamp_utc or "", timezone.utc)
    if status_as_of_timestamp is not None:
        status_as_of_timestamp = status_as_of_timestamp.astimezone(timezone.utc)
    tasks: list[dict] = []
    reports: list[dict] = []
    reviews: list[dict] = []
    value_items: list[dict] = []
    for work in _work_specs(target_window):
        sources: list[dict] = []
        target_window_match = _work_id_in_target_window(work["id"], target_window)
        for category, roots in (
            ("task", (cwd / "tasks",)),
            ("report", (cwd / "reports",)),
            ("review", (cwd / "codex" / "state" / "reviews", cwd / "reviews")),
        ):
            for root in roots:
                if not root.is_dir():
                    continue
                for path in sorted(root.glob(f"{work['slug']}*.md")):
                    item = _artifact_item(
                        path,
                        cwd,
                        category,
                        work["artifact_terms"],
                        anchor_timestamp=anchor_timestamp,
                        status_as_of_timestamp=status_as_of_timestamp,
                        audit_run_requested=audit_run_requested,
                        force_current=target_window_match,
                        target_window_evidence=target_window_match,
                    )
                    sources.append(item)
                    if category == "task":
                        tasks.append(item)
                    elif category == "report":
                        reports.append(item)
                    else:
                        reviews.append(item)
        if sources:
            current_sources = [source for source in sources if source.get("current_window_evidence")]
            selected_sources = current_sources or sources
            source_tier = "required_secondary" if current_sources else "diagnostic_optional"
            value_items.append(
                {
                    "work_id": work["id"],
                    "value_kind": work["value_kind"],
                    "summary": work["summary"],
                    "source_tier": source_tier,
                    "current_window_evidence": bool(current_sources),
                    "source_refs": [source["source_ref"] for source in selected_sources[:4]],
                    "evidence_snippets": [source["snippet"] for source in selected_sources[:2] if source.get("snippet")],
                }
            )
    return {
        "tasks": tasks,
        "reports": reports,
        "reviews": reviews,
        "commits": _collect_secondary_commits(
            cwd,
            anchor_timestamp_utc,
            status_as_of_timestamp_utc=status_as_of_timestamp_utc,
            audit_run_requested=audit_run_requested,
        ),
        "value_items": value_items,
    }


def _work_specs(target_window: dict | None) -> list[dict]:
    specs = {work["id"]: dict(work) for work in KNOWN_WORK}
    for task_id in (target_window or {}).get("ids") or []:
        specs.setdefault(
            task_id,
            {
                "id": task_id,
                "slug": task_id.casefold(),
                "value_kind": "target_window_work",
                "summary": (
                    f"{task_id} is part of the explicit target window; inspect its task/report/review "
                    "artifact as secondary support after raw JSONL primary refs."
                ),
                "artifact_terms": (
                    task_id.casefold(),
                    "status:",
                    "verdict",
                    "result classification",
                    "validation",
                    "merged",
                    "closeout",
                ),
            },
        )
    return list(specs.values())


def _work_id_in_target_window(work_id: str, target_window: dict | None) -> bool:
    return bool(target_window and work_id in set(target_window.get("ids") or []))


def _collect_secondary_commits(
    cwd: Path,
    anchor_timestamp_utc: str | None,
    *,
    status_as_of_timestamp_utc: str | None = None,
    audit_run_requested: bool = False,
) -> list[dict]:
    if not anchor_timestamp_utc:
        return []
    command = ["git", "-C", str(cwd), "log", "--max-count=8", "--format=%H%x09%cI%x09%s"]
    command.extend(["--since", anchor_timestamp_utc])
    if status_as_of_timestamp_utc and not audit_run_requested:
        command.extend(["--until", status_as_of_timestamp_utc])
    try:
        result = subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    commits = []
    for line in result.stdout.splitlines():
        commit_hash, committed_at, subject = (line.split("\t", 2) + ["", "", ""])[:3]
        if not commit_hash:
            continue
        commits.append(
            {
                "kind": "git_commit_secondary",
                "commit": commit_hash[:12],
                "committed_at": committed_at,
                "subject": _compact(subject, 160),
                "evidence_role": "secondary_only_after_raw_jsonl_trace",
                "source_tier": "required_secondary",
            }
        )
    return commits


def _artifact_item(
    path: Path,
    cwd: Path,
    category: str,
    terms: tuple[str, ...],
    *,
    anchor_timestamp,
    status_as_of_timestamp=None,
    audit_run_requested: bool = False,
    force_current: bool = False,
    target_window_evidence: bool = False,
) -> dict:
    rel = _rel(path, cwd)
    line_no, snippet = _first_matching_line(path, terms)
    evidence_timestamp = _artifact_evidence_timestamp(path, cwd)
    current_window = bool(
        force_current
        or (
            anchor_timestamp is not None
            and evidence_timestamp is not None
            and evidence_timestamp.astimezone(timezone.utc) >= anchor_timestamp
            and (
                audit_run_requested
                or status_as_of_timestamp is None
                or evidence_timestamp.astimezone(timezone.utc) <= status_as_of_timestamp
            )
        )
    )
    source_tier = "required_secondary" if current_window else "diagnostic_optional"
    return {
        "kind": category,
        "path": rel,
        "source_ref": f"{rel}:{line_no}" if line_no else rel,
        "snippet": snippet,
        "source_tier": source_tier,
        "current_window_evidence": current_window,
        "target_window_evidence": target_window_evidence,
        "evidence_timestamp_utc": (
            evidence_timestamp.astimezone(timezone.utc).isoformat(timespec="seconds") if evidence_timestamp else None
        ),
        "diagnostic_only": not current_window,
    }


def _artifact_evidence_timestamp(path: Path, cwd: Path):
    rel = _rel(path, cwd)
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "log", "-1", "--format=%cI", "--", rel],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        timestamp = parse_datetime(result.stdout.strip(), timezone.utc)
        if timestamp is not None:
            return timestamp.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None


def _first_matching_line(path: Path, terms: tuple[str, ...]) -> tuple[int | None, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, ""
    lower_terms = tuple(term.lower() for term in terms)
    for index, line in enumerate(lines, 1):
        normalized = line.lower()
        if any(term in normalized for term in lower_terms):
            return index, _compact(line)
    for index, line in enumerate(lines, 1):
        if line.strip():
            return index, _compact(line)
    return None, ""


def _unresolved_blockers(artifacts: dict, *, current_window_only: bool = False) -> list[dict]:
    collections = (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
    if current_window_only:
        collections = tuple([item for item in collection if item.get("current_window_evidence")] for collection in collections)
    joined = " ".join(
        item.get("snippet", "")
        for collection in collections
        for item in collection
    ).lower()
    if "case-002" in joined or "case 002" in joined or "read-more" in joined or "workflow churn" in joined:
        return [
            {
                "blocker": "case-002 over-search/read-more churn",
                "status": "unresolved",
                "summary": "The remaining serious blocker is case-002 over-search/read-more churn after source-grounded answers are possible.",
                "source_tier": "required_secondary",
                "source_refs": _source_refs_containing(
                    artifacts,
                    ("case-002", "case 002", "read-more", "workflow churn"),
                    current_window_only=current_window_only,
                )[:4],
            }
        ]
    return []


def _source_refs_containing(artifacts: dict, terms: tuple[str, ...], *, current_window_only: bool = False) -> list[str]:
    refs = []
    for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"]):
        for item in collection:
            if current_window_only and not item.get("current_window_evidence"):
                continue
            text = f"{item.get('path', '')} {item.get('snippet', '')}".lower()
            if any(term in text for term in terms):
                refs.append(item["source_ref"])
    return refs


def _answer_brief(
    *,
    selected_anchor: dict | None,
    session_activity: dict,
    behavior_deltas: list[dict],
    non_deltas: list[dict],
    blockers: list[dict],
    answer_allowed: bool = True,
) -> list[str]:
    if not answer_allowed:
        return []
    lines = []
    activity = session_activity.get("activity_after_anchor") or {}
    if selected_anchor:
        lines.append(
            "Current window starts at "
            f"{selected_anchor.get('timestamp_utc')} from {selected_anchor.get('source_ref')}; "
            "answer from required primary raw JSONL refs before secondary artifacts."
        )
    if session_activity.get("status") == "raw_session_trace_found" or activity.get("records_after_anchor"):
        lines.append(
            "Current-window raw trace: "
            f"{activity.get('records_after_anchor', 0)} records, "
            f"{activity.get('conversation_actions', 0)} conversation actions, "
            f"{activity.get('tool_calls', 0)} tool calls, "
            f"{activity.get('spawned_session_calls', 0)} spawned worker/reviewer session launches."
        )
    lines.extend(item["summary"] for item in behavior_deltas)
    lines.extend(item["summary"] for item in non_deltas)
    lines.extend(item["summary"] for item in blockers)
    return lines


def _next_reads(
    selected_anchor: dict | None,
    anchor_decision: dict,
    session_activity: dict,
    artifacts: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
    target_window: dict | None = None,
    status_as_of_boundary: dict | None = None,
    audit_run_requested: bool = False,
) -> list[dict]:
    reads = []
    if selected_anchor:
        logical_message_id = selected_anchor.get("logical_message_id")
        read = {
            "reason": "Inspect selected conversation anchor before answering.",
            "source_ref": selected_anchor["source_ref"],
            "source_kind": selected_anchor.get("source_kind"),
            "source_chronology": "pre_evaluation_primary",
            "source_role": "canonical_message_authority",
            "snippet": selected_anchor["snippet"],
            "source_tier": "required_primary",
            "answer_use_required": True,
            "logical_message_id": logical_message_id,
            "logical_window_id": f"{logical_message_id}:canonical-anchor" if logical_message_id else None,
            "window_role": "canonical_anchor",
            "canonical_authority_read": selected_anchor.get("source_kind") == "codex_history",
        }
        if target_window:
            read["target_window_hits"] = selected_anchor.get("target_window_hits") or _target_window_hits(
                str(selected_anchor.get("snippet") or ""),
                target_window,
            )
            read["outside_target_task_ids"] = selected_anchor.get("outside_target_task_ids") or _outside_target_task_ids(
                str(selected_anchor.get("snippet") or ""),
                target_window,
            )
        _copy_openable_fields(read, selected_anchor)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        reads.append(read)
    boundary_read = _prior_request_boundary_read(status_as_of_boundary, cwd=cwd, codex_home=codex_home)
    if boundary_read:
        reads.append(boundary_read)
    for read in session_activity.get("next_reads") or []:
        reads.append(
            {
                **read,
                "source_tier": "required_primary",
                "answer_use_required": True,
            }
        )
    reads.extend(
        _target_window_follow_on_reads(
            anchor_decision,
            cwd=cwd,
            codex_home=codex_home,
            target_window=target_window,
            audit_run_requested=audit_run_requested,
        )
    )
    reads.extend(
        _prior_request_occurrence_recovery_reads(
            anchor_decision=anchor_decision,
            cwd=cwd,
            codex_home=codex_home,
        )
    )
    reads.extend(
        _parent_main_recovery_reads(
            selected_anchor=selected_anchor,
            anchor_decision=anchor_decision,
            cwd=cwd,
            codex_home=codex_home,
        )
    )
    for item in artifacts["value_items"]:
        for ref in item["source_refs"][:2]:
            tier = item.get("source_tier") or "diagnostic_optional"
            read = {
                "reason": f"Inspect {item['work_id']} {item['value_kind']} evidence after raw JSONL trace.",
                "source_ref": ref,
                "source_tier": tier,
                "answer_use_required": tier != "diagnostic_optional",
                "source_chronology": (
                    "pre_evaluation_secondary" if tier == "required_secondary" else "pre_anchor_diagnostic"
                ),
            }
            if tier == "diagnostic_optional":
                read.update(
                    {
                        "diagnostic_only": True,
                        "read_more_actionable": False,
                        "diagnostic_open_policy": (
                            "stale/demo/example work product; open only if the current-window primary and secondary refs leave "
                            "a named diagnostic gap"
                        ),
                    }
                )
            reads.append(read)
    for blocker in _unresolved_blockers(artifacts, current_window_only=True):
        for ref in blocker.get("source_refs", [])[:2]:
            reads.append(
                {
                    "reason": "Inspect unresolved blocker evidence.",
                    "source_ref": ref,
                    "source_tier": blocker.get("source_tier") or "required_secondary",
                    "answer_use_required": True,
                    "source_chronology": "pre_evaluation_secondary",
                }
            )
    for commit in artifacts.get("commits") or []:
        commit_ref = f"git_commit:{commit.get('commit')}"
        reads.append(
            {
                "reason": "Use commit as required secondary support after required primary raw JSONL refs.",
                "source_ref": commit_ref,
                "snippet": commit.get("subject"),
                "source_tier": commit.get("source_tier") or "required_secondary",
                "answer_use_required": True,
                "source_chronology": "pre_evaluation_secondary",
                "commit": commit.get("commit"),
                "committed_at": commit.get("committed_at"),
            }
        )
    reads = _apply_target_window_primary_dominance(reads, target_window=target_window)
    reads = _apply_answer_facing_suppression(
        reads,
        target_window=target_window,
        audit_run_requested=audit_run_requested,
    )
    return _dedupe_reads(reads)


def _prior_request_boundary_read(status_as_of_boundary: dict | None, *, cwd: Path, codex_home: Path | None) -> dict | None:
    if not status_as_of_boundary or status_as_of_boundary.get("kind") not in {
        "prior_request_occurrence_boundary",
        "current_user_message_boundary",
    }:
        return None
    current_user_boundary = status_as_of_boundary.get("kind") == "current_user_message_boundary"
    read = {
        "reason": (
            "Inspect the named user-message boundary; this row bounds the before/not-after answer interval."
            if current_user_boundary
            else "Inspect the prior occurrence of the current request; this row is the status/query boundary, not the answer anchor."
        ),
        "source_ref": status_as_of_boundary.get("source_ref"),
        "source_kind": status_as_of_boundary.get("source_kind"),
        "snippet": status_as_of_boundary.get("snippet"),
        "source_tier": "required_primary",
        "answer_use_required": True,
        "prior_request_occurrence_boundary": not current_user_boundary,
        "current_user_message_boundary": current_user_boundary,
        "answer_support_role": "current_user_message_boundary" if current_user_boundary else "status_query_boundary",
        "source_chronology": "pre_evaluation_primary",
        "source_role": "canonical_message_boundary",
        "logical_message_id": status_as_of_boundary.get("logical_message_id"),
    }
    _copy_openable_fields(read, status_as_of_boundary)
    if read.get("openable_target") and not read.get("read_more_command"):
        read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
    return read


def _prior_request_occurrence_recovery_reads(
    *,
    anchor_decision: dict,
    cwd: Path,
    codex_home: Path | None,
) -> list[dict]:
    prior = anchor_decision.get("prior_request_occurrence") or {}
    if prior.get("status") != "ambiguous":
        return []
    if prior.get("fallback_applied"):
        return []
    reads = []
    for candidate in prior.get("occurrence_candidates") or prior.get("rejected_anchor_alternatives") or []:
        read = {
            "reason": (
                "Inspect this exact prior request occurrence candidate only to disambiguate the boundary; "
                "do not use it as normal answer support."
            ),
            "source_ref": candidate.get("source_ref"),
            "source_kind": candidate.get("source_kind"),
            "snippet": candidate.get("snippet"),
            "source_tier": "required_primary",
            "answer_use_required": False,
            "read_more_actionable": True,
            "verification_or_rerun_only": True,
            "source_chronology": "unknown_diagnostic",
            "prior_request_occurrence_candidate": True,
            "fail_closed_before_answer": True,
            "answer_support_role": "prior_request_occurrence_disambiguation_only",
            "diagnostic_open_policy": (
                "prior request occurrence disambiguation only; rerun with an explicit timestamp or quoted-message "
                "anchor after choosing the intended occurrence"
            ),
        }
        _copy_openable_fields(read, candidate)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        reads.append(read)
        if len(reads) >= 4:
            break
    return reads


def _parent_main_recovery_reads(
    *,
    selected_anchor: dict | None,
    anchor_decision: dict,
    cwd: Path,
    codex_home: Path | None,
) -> list[dict]:
    if selected_anchor or anchor_decision.get("anchor_scope") != "parent_thread":
        return []
    if str(anchor_decision.get("context_boundary") or "").startswith("prior_request_occurrence"):
        return []
    reads = []
    recovery_candidates = [
        candidate
        for candidate in anchor_decision.get("rejected_anchor_alternatives") or []
        if _is_parent_main_recovery_candidate(candidate)
    ]
    recovery_candidates.sort(key=lambda item: str(item.get("timestamp_utc") or ""), reverse=True)
    recovery_candidates.sort(key=lambda item: 0 if item.get("source_kind") == "codex_history" else 1)
    if codex_home is not None and not any(candidate.get("source_kind") == "codex_history" for candidate in recovery_candidates):
        history_path = codex_home / "history.jsonl"
        read = {
            "reason": (
                "Discover the canonical history.jsonl parent/main anchor before answering; "
                "current-session or task-window candidates are not answer support while the history anchor is ambiguous."
            ),
            "source_ref": "codex_history:history.jsonl",
            "source_kind": "codex_history",
            "source_tier": "required_primary",
            "answer_use_required": False,
            "read_more_actionable": False,
            "verification_or_rerun_only": True,
            "source_chronology": "unknown_diagnostic",
            "diagnostic_open_policy": (
                "canonical history-anchor discovery material only; locate the intended history.jsonl human row, "
                "then rerun with that timestamp or quoted message before a factual answer"
            ),
            "parent_main_anchor_recovery_candidate": True,
            "canonical_history_candidate": True,
            "canonical_history_anchor_discovery": True,
            "fail_closed_before_answer": True,
            "answer_support_role": "canonical_history_anchor_discovery_only",
        }
        if history_path.is_file():
            read["openable_source_ref"] = {"path": str(history_path)}
            read["openable_target"] = str(history_path)
        reads.append(read)
    for candidate in recovery_candidates:
        source_kind = candidate.get("source_kind")
        if source_kind == "codex_history":
            reason = (
                "Inspect candidate canonical history.jsonl parent/main anchor before answering; "
                "if it is the intended anchor, rerun with its timestamp or quoted message."
            )
        else:
            reason = (
                "Inspect unproven parent/main session candidate only to locate the matching canonical "
                "history.jsonl anchor, then rerun with that timestamp or quote before answering."
            )
        read = {
            "reason": reason,
            "source_ref": candidate.get("source_ref"),
            "source_kind": source_kind,
            "snippet": candidate.get("snippet"),
            "source_tier": "required_primary",
            "answer_use_required": False,
            "read_more_actionable": True,
            "verification_or_rerun_only": True,
            "source_chronology": "unknown_diagnostic",
            "diagnostic_open_policy": (
                "parent/main recovery candidate; open only to prove the canonical history.jsonl anchor, "
                "then rerun with that timestamp or quoted message before a factual answer"
            ),
            "parent_main_anchor_recovery_candidate": True,
            "canonical_history_candidate": source_kind == "codex_history",
            "fail_closed_before_answer": True,
            "answer_support_role": "verification_rerun_material_only",
        }
        _copy_openable_fields(read, candidate)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        reads.append(read)
        if len(reads) >= 4:
            break
    return reads


def _is_parent_main_recovery_candidate(candidate: dict) -> bool:
    if _is_never_anchor(candidate):
        return False
    if candidate.get("classification") not in {"substantive_work_anchor", "ordinary_human_turn", "short_human_turn"}:
        return False
    if candidate.get("source_kind") not in {"codex_history", "codex_sessions"}:
        return False
    if candidate.get("source_kind") == "codex_sessions" and candidate.get("correlation_status") in {
        "session_only",
        "ambiguous_multiple_canonical",
        "ambiguous_multiple_session_copies",
    }:
        return False
    return bool(candidate.get("source_ref") or candidate.get("openable_target"))


def _target_window_follow_on_reads(
    anchor_decision: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
    target_window: dict | None,
    audit_run_requested: bool = False,
) -> list[dict]:
    if not target_window:
        return []
    reads = []
    for candidate in anchor_decision.get("rejected_anchor_alternatives") or []:
        if not _is_target_window_outside_task_ref(candidate, target_window=target_window):
            continue
        tier = "required_primary" if audit_run_requested else "diagnostic_optional"
        read = {
            "reason": (
                "Inspect later raw conversation ref because the query explicitly asks to audit that run."
                if audit_run_requested
                else (
                    "Later raw conversation ref mentions target-window IDs plus outside-window tasks; "
                    "keep as follow-on context, not required primary support for the target window."
                )
            ),
            "source_ref": candidate.get("source_ref"),
            "snippet": candidate.get("snippet"),
            "source_tier": tier,
            "answer_use_required": audit_run_requested,
            "source_chronology": "pre_evaluation_primary" if audit_run_requested else "same_loop_or_post_hoc",
            "target_window_hits": candidate.get("target_window_hits") or [],
            "outside_target_task_ids": candidate.get("outside_target_task_ids") or [],
            "target_window_follow_on_context": not audit_run_requested,
        }
        _copy_openable_fields(read, candidate)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        if not audit_run_requested:
            read.update(
                {
                    "diagnostic_only": True,
                    "read_more_actionable": False,
                    "diagnostic_open_policy": (
                        "post-window recovery/current-run ref; open only after target-window primary and secondary refs "
                        "leave a named follow-on gap"
                    ),
                }
            )
        reads.append(read)
    return reads


def _source_tiers(reads: list[dict]) -> dict:
    tiers = {
        "required_primary": [],
        "required_secondary": [],
        "diagnostic_optional": [],
    }
    for read in reads:
        tier = read.get("source_tier") or "diagnostic_optional"
        if tier not in tiers:
            tier = "diagnostic_optional"
        tiers[tier].append(read)
    return {
        "tier_order": ["required_primary", "required_secondary", "diagnostic_optional"],
        "source_mode_order": list(SOURCE_MODES),
        "source_role_labels": list(SOURCE_ROLE_LABELS),
        "required_primary": tiers["required_primary"],
        "required_secondary": tiers["required_secondary"],
        "diagnostic_optional": tiers["diagnostic_optional"],
        "counts": {tier: len(items) for tier, items in tiers.items()},
        "answer_brief_source_policy": (
            "answer_brief is derived from the selected current anchor/window plus current-window secondary evidence; "
            "diagnostic_optional stale/demo/example and same-run/private pressure refs are not answer-facing summary support"
        ),
    }


def _with_source_role_labels(reads: list[dict]) -> list[dict]:
    return [_with_source_role_label(read) for read in reads]


def _with_source_role_label(read: dict) -> dict:
    labeled = dict(read)
    evidence_mode, source_role_label, reason = _source_role_for_read(labeled)
    labeled["evidence_mode"] = evidence_mode
    labeled["source_role_label"] = source_role_label
    labeled["source_role_reason"] = reason
    return labeled


def _source_role_for_read(read: dict) -> tuple[str, str, str]:
    source_ref = str(read.get("source_ref") or "")
    source_kind = str(read.get("source_kind") or "")
    openable_target = _normalize_pathish(str(read.get("openable_target") or ""))
    source_tier = str(read.get("source_tier") or "")
    chronology = str(read.get("source_chronology") or "")
    if chronology in {"same_loop", "post_hoc", "same_loop_or_post_hoc"}:
        return (
            "post_hoc_diagnostic",
            "post_hoc_answer_key_or_same_loop_echo",
            "explicit source chronology marks this material as same-loop or post-hoc diagnostic evidence",
        )
    if _is_post_hoc_or_diagnostic_read(read):
        label = (
            "post_hoc_answer_key_or_same_loop_echo"
            if _is_post_hoc_or_same_loop_read(read)
            else "diagnostic_only"
        )
        return (
            "post_hoc_diagnostic",
            label,
            "diagnostic, recovery, suppressed, same-loop, or later material is not counted as primary status proof",
        )
    if source_kind == "codex_history" or source_ref.startswith("codex_history:") or "history.jsonl" in openable_target:
        return (
            "ground_truth_chat_logs",
            "primary_raw_chat_log",
            "raw canonical history/chat row used as primary status evidence",
        )
    if source_kind == "codex_sessions" or source_ref.startswith("codex_sessions:") or "/sessions/" in openable_target:
        return (
            "ground_truth_chat_logs",
            "primary_tool_or_command_trace",
            "raw session/tool/command trace used before artifact summaries",
        )
    if source_tier == "required_secondary" or source_ref.startswith(("tasks/", "reports/", "codex/state/reviews/", "git_commit:")):
        return (
            "artifact_assisted_context",
            "legitimate_pre_existing_artifact",
            "chronologically eligible artifact or commit context used only after raw JSONL evidence",
        )
    return (
        "post_hoc_diagnostic",
        "diagnostic_only",
        "unclassified status read defaults to diagnostic-only until its chronology/source role is proven",
    )


def _is_post_hoc_or_diagnostic_read(read: dict) -> bool:
    if read.get("diagnostic_only") or str(read.get("source_tier") or "") == "diagnostic_optional":
        return True
    diagnostic_flags = (
        "verification_or_rerun_only",
        "prior_request_occurrence_candidate",
        "parent_main_anchor_recovery_candidate",
        "canonical_history_anchor_discovery",
        "fail_closed_before_answer",
        "suppressed_from_answer_facing",
        "target_window_follow_on_context",
    )
    return any(bool(read.get(flag)) for flag in diagnostic_flags)


def _is_post_hoc_or_same_loop_read(read: dict) -> bool:
    chronology = str(read.get("source_chronology") or "").casefold()
    if chronology in {"same_loop", "post_hoc", "same_loop_or_post_hoc"}:
        return True
    role_text = " ".join(
        str(read.get(key) or "").casefold()
        for key in ("answer_support_role", "diagnostic_open_policy", "reason")
    )
    text = role_text
    markers = (
        "same-run",
        "same_loop",
        "current-run",
        "current loop",
        "pressure",
        "answer-key",
        "answer key",
        "post-window",
        "suppressed",
        "echo",
    )
    return any(marker in text for marker in markers)


def _follow_through_plan(
    *,
    source_tiers: dict,
    target_window: dict | None,
    session_activity: dict,
) -> dict:
    required_primary = list(source_tiers.get("required_primary") or [])
    required_secondary = list(source_tiers.get("required_secondary") or [])
    diagnostic_optional = list(source_tiers.get("diagnostic_optional") or [])
    target_ids = list((target_window or {}).get("ids") or [])
    audit_run_requested = bool(target_window and target_window.get("audit_run_requested"))
    open_first = [_plan_read(read) for read in required_primary[:4]]
    then_confirm = _follow_through_confirmation_groups(required_secondary, target_ids=target_ids)
    do_not_open_by_default = [_plan_read(read) for read in diagnostic_optional[:8]]
    has_parent_main_recovery = any(
        read.get("parent_main_anchor_recovery_candidate") or read.get("prior_request_occurrence_candidate")
        for read in required_primary
    )
    named_gap = (
        "a named target-window status facet remains missing or contradictory"
        if target_ids and not audit_run_requested
        else "a named status facet remains missing or contradictory"
    )
    if has_parent_main_recovery:
        stop_condition = (
            "Open the parent/main anchor recovery candidate refs only to prove the canonical history.jsonl "
            "anchor. Do not answer from these candidates, task/report artifacts, commits, or visible context "
            "until a parent/main history anchor is selected or the command is rerun with an explicit timestamp "
            "or quoted-message anchor."
        )
    elif target_ids and not audit_run_requested:
        stop_condition = (
            "Open the required target-window primary raw ref first, then confirm the target-window "
            "task/report/review/commit refs listed in then_confirm. Stop when those refs cover the "
            "expected status facets; do not open diagnostic/post-window refs or rerun JSON/JQ unless "
            f"{named_gap}."
        )
    else:
        stop_condition = (
            "Open required primary raw refs first, then confirm required secondary task/report/review/commit "
            "refs. Stop when those refs cover the expected status facets; do not open diagnostics or rerun "
            f"JSON/JQ unless {named_gap}."
        )
    return {
        "status": "ready" if open_first or then_confirm else "no_follow_through_refs",
        "open_first": open_first,
        "then_confirm": then_confirm,
        "do_not_open_by_default": do_not_open_by_default,
        "stop_condition": stop_condition,
        "read_more_boundary_warning": _read_more_boundary_warning(
            target_window=target_window,
            diagnostic_count=len(diagnostic_optional),
        ),
        "expected_status_answer_facets": _expected_status_answer_facets(
            target_window=target_window,
            session_activity=session_activity,
        ),
        "required_answer_facets": _required_answer_facets(target_window=target_window),
        "required_facets_stop_condition": (
            "Do not synthesize a factual status answer until every required_answer_facet is covered "
            "by inspected required primary raw refs and required secondary refs, or explicitly reported "
            "as unknown with a next action."
        ),
        "rerun_policy": (
            "Use this plan before rerunning what-happened-since as JSON/JQ. Rerun only if the plan is missing, "
            "internally contradictory, or a named expected facet remains unresolved after the listed reads."
        ),
    }


def _follow_through_confirmation_groups(reads: list[dict], *, target_ids: list[str]) -> list[dict]:
    groups = []
    assigned: set[str] = set()
    if target_ids:
        for task_id in target_ids:
            refs = []
            for read in reads:
                ref = str(read.get("source_ref") or "")
                if ref in assigned:
                    continue
                if task_id.casefold() not in _target_window_read_text(read).casefold():
                    continue
                refs.append(_plan_read(read))
                assigned.add(ref)
            if refs:
                groups.append(
                    {
                        "task_id": task_id,
                        "refs": refs[:4],
                        "purpose": "Confirm target-window task/report/review/commit status after open_first.",
                    }
                )
    remaining = [_plan_read(read) for read in reads if str(read.get("source_ref") or "") not in assigned]
    if remaining:
        groups.append(
            {
                "task_id": None,
                "label": "current_window_secondary_support",
                "refs": remaining[:6],
                "purpose": "Confirm secondary status evidence after required primary raw refs.",
            }
        )
    return groups


def _plan_read(read: dict) -> dict:
    planned = {
        "source_ref": read.get("source_ref"),
        "source_tier": read.get("source_tier"),
        "evidence_mode": read.get("evidence_mode"),
        "source_role_label": read.get("source_role_label"),
        "source_role_reason": read.get("source_role_reason"),
        "reason": read.get("reason"),
        "answer_use_required": bool(read.get("answer_use_required")),
    }
    for key in (
        "openable_target",
        "read_more_command",
        "target_window_hits",
        "outside_target_task_ids",
        "diagnostic_only",
        "read_more_actionable",
        "diagnostic_open_policy",
        "target_window_follow_on_context",
        "suppressed_from_answer_facing",
        "parent_main_anchor_recovery_candidate",
        "canonical_history_candidate",
        "canonical_history_anchor_discovery",
        "fail_closed_before_answer",
        "answer_support_role",
        "verification_or_rerun_only",
        "prior_request_occurrence_boundary",
        "current_user_message_boundary",
        "prior_request_occurrence_candidate",
    ):
        if key in read:
            planned[key] = read.get(key)
    return planned


def _read_more_boundary_warning(*, target_window: dict | None, diagnostic_count: int) -> str:
    if target_window and not target_window.get("audit_run_requested"):
        return (
            "Raw read-more uses a bounded window around the opened ref and may show adjacent records outside "
            "the requested target window. Treat outside-window records as diagnostic only; do not use them as "
            "answer support unless the user explicitly asks to audit that later run or a named gap remains."
        )
    if diagnostic_count:
        return (
            "Raw read-more windows can include adjacent or diagnostic context. Keep diagnostics out of answer "
            "support unless a named gap remains after required primary and secondary reads."
        )
    return (
        "Raw read-more windows are context windows, not a signal to broaden by default. Stop after required "
        "primary and secondary reads answer the named status facets."
    )


def _expected_status_answer_facets(
    *,
    target_window: dict | None,
    session_activity: dict,
) -> list[str]:
    facets = ["anchor_and_boundary", "raw_current_window_activity"]
    if target_window:
        facets.append("target_window_task_report_review_status")
    else:
        facets.append("current_window_task_report_review_status")
    if (session_activity.get("activity_after_anchor") or {}).get("spawned_session_calls"):
        facets.append("worker_reviewer_or_spawned_session_activity")
    facets.extend(["commits_or_validations", "caveats_non_claims", "unresolved_blockers_or_next_action"])
    return facets


def _required_answer_facets(*, target_window: dict | None) -> list[str]:
    task_facet = "target_window_task_outcome_value_deltas" if target_window else "task_outcome_value_deltas"
    return [
        "selected_anchor_and_boundary",
        "raw_post_anchor_activity",
        task_facet,
        "unresolved_blockers_or_next_action",
        "caveats_non_claims",
    ]


def _answer_readiness_contract(
    *,
    selected_anchor: dict | None,
    anchor_decision: dict,
    session_activity: dict,
    source_tiers: dict,
    follow_through_plan: dict,
    target_window: dict | None,
) -> dict:
    required_facets = _required_answer_facets(target_window=target_window)
    missing = []
    parent_main_status = _is_parent_main_status_decision(anchor_decision)
    selected_anchor_ready = bool(selected_anchor)
    if not selected_anchor:
        missing.append("selected_anchor_and_boundary")
    elif parent_main_status and not _is_proven_parent_main_history_anchor(selected_anchor):
        selected_anchor_ready = False
        missing.append("selected_anchor_and_boundary")
    raw_status = session_activity.get("status")
    raw_activity_ready = raw_status in {"raw_session_trace_found", "partial_raw_session_trace_found"}
    if not raw_activity_ready:
        missing.append("raw_post_anchor_activity")
    task_facet = "target_window_task_outcome_value_deltas" if target_window else "task_outcome_value_deltas"
    target_or_task_ready = True
    recovery_material = any(
        read.get("parent_main_anchor_recovery_candidate") or read.get("prior_request_occurrence_candidate")
        for read in (source_tiers.get("required_primary") or [])
    )
    if recovery_material and "selected_anchor_and_boundary" not in missing:
        missing.append("selected_anchor_and_boundary")
    answer_allowed = not missing
    if parent_main_status:
        answer_allowed = answer_allowed and bool(selected_anchor and _is_proven_parent_main_history_anchor(selected_anchor))
    status = "answer_ready" if answer_allowed else "fail_closed_missing_required_facets"
    stop_condition = (
        "Answer allowed only after selected_anchor_and_boundary and raw_post_anchor_activity are proven, "
        "then every required_answer_facet is covered or explicitly caveated. Missing facets are a stop "
        "condition, not optional guidance."
    )
    if recovery_material or not selected_anchor:
        stop_condition = (
            "Fail closed before answer: recovery candidates are verification/rerun material only. "
            "Rerun with a proven timestamp or quoted-message anchor before synthesizing a factual status answer."
        )
    return {
        "status": status,
        "answer_allowed": answer_allowed,
        "selected_anchor_answer_ready": bool(
            selected_anchor
            and (
                not parent_main_status
                or _is_proven_parent_main_history_anchor(selected_anchor)
            )
        ),
        "parent_main_status_answer": parent_main_status,
        "proven_parent_main_history_anchor": bool(
            selected_anchor and _is_proven_parent_main_history_anchor(selected_anchor)
        ),
        "raw_session_trace_status": raw_status,
        "required_answer_facets": required_facets,
        "task_label_only_summary_allowed": False,
        "task_label_only_summary_policy": (
            "Task IDs or task-range labels alone are insufficient. A factual status answer must expand them into "
            "product-before behavior, product-after behavior, remaining blockers or unproven claims, and cited evidence."
        ),
        "answer_quality_contract": {
            "task_label_only_summary_allowed": False,
            "required_summary_facets": [
                "product_before_behavior",
                "product_after_behavior",
                "remaining_blockers_or_unproven_claims",
                "cited_raw_and_task_evidence",
            ],
        },
        "facet_status": {
            "selected_anchor_and_boundary": selected_anchor_ready,
            "raw_post_anchor_activity": raw_activity_ready,
            task_facet: target_or_task_ready,
            "unresolved_blockers_or_next_action": True,
            "caveats_non_claims": True,
        },
        "missing_required_answer_facets": missing,
        "stop_condition": stop_condition,
        "required_facets_stop_condition": follow_through_plan.get("required_facets_stop_condition"),
        "non_answer_support_fields": [
            "anchor_candidates",
            "anchor_decision.rejected_anchor_alternatives",
            "source_tiers.required_primary[parent_main_anchor_recovery_candidate]",
            "source_tiers.required_primary[prior_request_occurrence_candidate]",
            "parent_main_anchor_shortlist",
            "source_tiers.diagnostic_optional",
        ],
    }


def _apply_target_window_primary_dominance(reads: list[dict], *, target_window: dict | None) -> list[dict]:
    if not target_window or target_window.get("audit_run_requested"):
        return reads
    demoted_reads = []
    for read in reads:
        if not _is_target_window_outside_task_ref(read, target_window=target_window):
            demoted_reads.append(read)
            continue
        demoted = dict(read)
        demoted.update(
            {
                "source_tier": "diagnostic_optional",
                "answer_use_required": False,
                "diagnostic_only": True,
                "read_more_actionable": False,
                "target_window_follow_on_context": True,
                "demoted_from_required_primary": read.get("source_tier") == "required_primary",
                "diagnostic_open_policy": (
                    "post-window recovery/current-run raw ref mentions target-window and outside-window tasks; "
                    "open only as follow-on context unless explicitly auditing that later run"
                ),
            }
        )
        demoted_reads.append(demoted)
    return demoted_reads


def _is_target_window_outside_task_ref(read: dict, *, target_window: dict | None) -> bool:
    if not target_window:
        return False
    target_hits = set(read.get("target_window_hits") or [])
    outside_task_ids = set(read.get("outside_target_task_ids") or [])
    has_task_metadata = "target_window_hits" in read or "outside_target_task_ids" in read
    if not has_task_metadata:
        text = _target_window_read_text(read)
        target_hits = set(_target_window_hits(text, target_window))
        outside_task_ids = set(_outside_target_task_ids(text, target_window))
    return bool(target_hits and outside_task_ids and _is_raw_conversation_ref(read))


def _target_window_read_text(read: dict) -> str:
    return " ".join(
        str(read.get(key) or "")
        for key in (
            "source_ref",
            "openable_target",
            "reason",
            "snippet",
        )
    )


def _is_raw_conversation_ref(read: dict) -> bool:
    source_ref = str(read.get("source_ref") or "")
    openable_target = _normalize_pathish(str(read.get("openable_target") or ""))
    return (
        source_ref.startswith("codex_history:")
        or source_ref.startswith("codex_sessions:")
        or "/sessions/" in openable_target
        or openable_target.endswith("history.jsonl")
        or "history.jsonl:" in openable_target
    )


def _apply_answer_facing_suppression(
    reads: list[dict],
    *,
    target_window: dict | None,
    audit_run_requested: bool = False,
) -> list[dict]:
    if audit_run_requested or (target_window and target_window.get("audit_run_requested")):
        return reads
    suppressed = []
    for read in reads:
        if not _is_suppressed_answer_facing_ref(read):
            suppressed.append(read)
            continue
        demoted = dict(read)
        demoted.update(
            {
                "source_tier": "diagnostic_optional",
                "answer_use_required": False,
                "diagnostic_only": True,
                "read_more_actionable": False,
                "suppressed_from_answer_facing": True,
                "diagnostic_open_policy": (
                    "explicitly same-loop/post-hoc or manual-lane output; open only when explicitly auditing that run"
                ),
            }
        )
        suppressed.append(demoted)
    return suppressed


def _is_suppressed_answer_facing_ref(read: dict) -> bool:
    chronology = str(read.get("source_chronology") or "").casefold()
    return bool(
        read.get("suppress_answer_facing")
        or chronology in {"same_loop", "post_hoc", "same_loop_or_post_hoc"}
    )


def _suppressed_answer_facing_support(
    cwd: Path,
    *,
    target_window: dict | None = None,
    audit_run_requested: bool = False,
) -> dict:
    audit_run_requested = bool(audit_run_requested or (target_window and target_window.get("audit_run_requested")))
    if audit_run_requested:
        return {
            "policy": "same-run/private pressure artifacts may be answer-facing because the query explicitly asks to audit the run",
            "audit_run_requested": True,
            "count": 0,
            "class_counts": {},
            "paths_included": False,
        }
    private_root = cwd / ".private"
    class_counts: dict[str, int] = {}
    truncated = False
    if private_root.is_dir():
        for path in private_root.glob("*/run-*/*/final_answer.txt"):
            _increment_class(class_counts, _suppressed_artifact_class(path))
            if sum(class_counts.values()) >= 20:
                truncated = True
                break
    return {
        "policy": (
            "same-run pressure artifacts, private run directories, and manual-lane outputs are omitted from "
            "required/open-first support unless the query explicitly asks to audit that run"
        ),
        "audit_run_requested": False,
        "count": sum(class_counts.values()),
        "class_counts": class_counts,
        "truncated": truncated,
        "paths_included": False,
    }


def _suppressed_artifact_class(path: Path) -> str:
    text = _normalize_pathish(str(path))
    if "/manual_default/" in text or "/manual-lane/" in text or "/manual_lane/" in text:
        return "manual_lane_final_output"
    if ".private/" in text:
        return "private_pressure_artifact"
    return "same_run_pressure_artifact"


def _increment_class(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _with_read_more_command(anchor: dict | None, *, cwd: Path, codex_home: Path | None) -> dict | None:
    if not anchor:
        return None
    selected = dict(anchor)
    target = selected.get("openable_target")
    if target:
        selected["openable_target"] = str(target)
        selected["read_more_command"] = _read_more_command(str(target), cwd=cwd, codex_home=codex_home)
    return selected


def _copy_openable_fields(read: dict, source: dict) -> None:
    for key in ("openable_source_ref", "openable_target", "read_more_command"):
        value = source.get(key)
        if value:
            read[key] = value


def _agent_instruction(selected_anchor: dict | None, session_activity: dict, next_reads: list[dict]) -> dict:
    first_read = next_reads[0] if next_reads else {}
    if not selected_anchor and first_read.get("parent_main_anchor_recovery_candidate"):
        stop_condition = (
            "Open the parent/main recovery candidate to prove the canonical history.jsonl anchor, then rerun "
            "with that timestamp or quoted message. Do not answer from visible context, current worker/reviewer "
            "prompts, task/report artifacts, commits, or repo state while selected_anchor is null."
        )
    else:
        stop_condition = (
            "Start with the selected anchor read-more command, then inspect raw sessions/**/*.jsonl "
            "activity after the anchor before task/report/review artifacts. Commits, merges, and current "
            "repo state are secondary evidence only; do not give a commit-only or repo-status-only answer, "
            "and do not run broad directory lookup unless the direct raw JSONL reads fail or contradict each other."
        )
    return {
        "first_read_target": first_read.get("openable_target") or first_read.get("source_ref"),
        "first_read_command": first_read.get("read_more_command"),
        "stop_condition": stop_condition,
        "direct_anchor_openable": bool(selected_anchor and selected_anchor.get("openable_target")),
        "raw_session_trace_status": session_activity.get("status"),
        "commit_only_answer_allowed": False,
    }


def _fail_closed_guidance(
    *,
    selected_anchor: dict | None,
    anchor_decision: dict,
    session_activity: dict,
    answer_readiness: dict,
) -> dict:
    if not answer_readiness.get("answer_allowed"):
        parent_main_recovery = anchor_decision.get("anchor_scope") == "parent_thread"
        guidance = (
            "Fail closed: required answer facets are missing. Do not answer from visible context, "
            "inherited summaries, current worker prompts, commits, or repo status. Inspect candidate anchors "
            "and rejected alternatives first, then rerun with an explicit timestamp/quoted-message anchor if needed."
        )
        candidate_fields = [
            "anchor_candidates",
            "anchor_decision.rejected_anchor_alternatives",
        ]
        if parent_main_recovery:
            guidance = (
                "Fail closed: no canonical parent/main history.jsonl anchor was selected. Open only the "
                "parent/main anchor recovery candidate (parent_main_anchor_recovery_candidate) or "
                "prior-request occurrence recovery candidate reads to prove the intended "
                "parent/main anchor and status/query boundary, then rerun with that timestamp or quoted message. Do not answer from "
                "visible context, inherited summaries, current worker/reviewer prompts, task/report artifacts, "
                "commits, repo status, or current-session handoff snippets while selected_anchor is null."
            )
            candidate_fields.append("source_tiers.required_primary[parent_main_anchor_recovery_candidate]")
            candidate_fields.append("source_tiers.required_primary[prior_request_occurrence_candidate]")
        return {
            "mode": "candidate_anchors_required_before_answer",
            "answer_allowed": False,
            "guidance": guidance,
            "candidate_fields": candidate_fields,
            "parent_main_recovery_required": parent_main_recovery,
            "required_answer_facets": answer_readiness.get("required_answer_facets") or [],
            "missing_required_answer_facets": answer_readiness.get("missing_required_answer_facets") or [],
            "stop_condition": answer_readiness.get("stop_condition"),
        }
    raw_status = session_activity.get("status")
    return {
        "mode": "selected_anchor_requires_primary_read_followthrough",
        "answer_allowed": True,
        "guidance": (
            "Use the selected human history/session anchor and required primary raw JSONL reads before answering. "
            "Do not answer from inherited context, current-session worker/reviewer prompts, rejected false anchors, "
            "or secondary repo artifacts unless the primary reads have been inspected."
        ),
        "selected_anchor_ref": selected_anchor.get("source_ref"),
        "anchor_scope": selected_anchor.get("anchor_scope") or anchor_decision.get("anchor_scope"),
        "raw_session_trace_status": raw_status,
        "required_answer_facets": answer_readiness.get("required_answer_facets") or [],
        "missing_required_answer_facets": [],
    }


def _clarification_request(
    *,
    selected_anchor: dict | None,
    anchor_decision: dict,
    answer_readiness: dict,
    fail_closed_guidance: dict,
) -> dict | None:
    if selected_anchor or answer_readiness.get("answer_allowed") is not False:
        return None
    if anchor_decision.get("anchor_scope") == "current_user_message_chronology":
        return {
            "status": "needs_user_anchor_clarification",
            "answer_allowed": False,
            "selected_anchor": None,
            "request": (
                "I cannot safely identify the intended user-message boundary. Please provide the exact quoted "
                "message, a timestamp, or say whether you mean the message itself or the user message before it."
            ),
            "accepted_anchor_forms": [
                "quoted_instruction_or_message",
                "exact_timestamp",
                "message_itself_or_message_before",
            ],
            "reason": anchor_decision.get("anchor_reason") or "current user-message chronology did not produce one safe anchor",
            "candidate_refs_policy": (
                "Rejected/status/control candidates are verification-only; do not use them as answer support."
            ),
            "rerun_instruction": "Rerun what-happened-since with the exact quoted user message or timestamp before answering.",
        }
    if anchor_decision.get("anchor_scope") != "parent_thread":
        return None
    if not fail_closed_guidance.get("parent_main_recovery_required"):
        return None
    return {
        "status": "needs_user_anchor_clarification",
        "answer_allowed": False,
        "selected_anchor": None,
        "request": (
            "I cannot safely identify the parent/main anchor. Please provide a quoted instruction/message, "
            "an exact timestamp, or a thread/session boundary to use as the anchor."
        ),
        "accepted_anchor_forms": [
            "quoted_instruction_or_message",
            "exact_timestamp",
            "thread_or_session_boundary",
        ],
        "reason": "multiple or unproven parent/main candidates were found and no canonical selected_anchor is available",
        "candidate_refs_policy": (
            "Recovery/read-more refs are verification/rerun-only; do not use them as answer support."
        ),
        "rerun_instruction": "Rerun what-happened-since with the provided timestamp or quoted message before answering.",
    }


def _read_more_command(
    target: str,
    *,
    cwd: Path,
    codex_home: Path | None,
    before: int = 2,
    after: int = 8,
) -> str:
    parts = [
        "jsonl-history-search",
        "read-more",
        target,
        "--cwd",
        str(cwd),
    ]
    if codex_home is not None:
        parts.extend(["--codex-home", str(codex_home)])
    parts.extend(["--format", "json", "--mode", "window", "--before", str(before), "--after", str(after)])
    return " ".join(shlex.quote(part) for part in parts)


def _dedupe_reads(reads: list[dict]) -> list[dict]:
    seen_refs = set()
    seen_windows = set()
    deduped = []
    for read in reads:
        ref = read.get("source_ref")
        window_id = read.get("logical_window_id")
        if not ref:
            continue
        if window_id and window_id in seen_windows:
            continue
        if ref in seen_refs:
            continue
        if any(_logical_windows_overlap(existing, read) for existing in deduped):
            continue
        seen_refs.add(ref)
        if window_id:
            seen_windows.add(window_id)
        deduped.append(read)
    tier_order = {"required_primary": 0, "required_secondary": 1, "diagnostic_optional": 2}
    deduped.sort(key=lambda item: tier_order.get(item.get("source_tier"), 2))
    return deduped


def _logical_windows_overlap(left: dict, right: dict) -> bool:
    logical_id = left.get("logical_message_id")
    if not logical_id or logical_id != right.get("logical_message_id"):
        return False
    left_role = str(left.get("window_role") or "")
    right_role = str(right.get("window_role") or "")
    if left_role == right_role == "canonical_anchor":
        return True
    if "activity" not in left_role or "activity" not in right_role:
        return False
    if left.get("window_source_identity") != right.get("window_source_identity"):
        return False
    try:
        left_start = int(left.get("window_start_line"))
        left_end = int(left.get("window_end_line"))
        right_start = int(right.get("window_start_line"))
        right_end = int(right.get("window_end_line"))
    except (TypeError, ValueError):
        return False
    return left_start <= right_end and right_start <= left_end


def _rel(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except (OSError, ValueError):
        return str(path)


def _compact(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _normalize_task_ids(text: str) -> str:
    return _TASK_ID_RE.sub(lambda match: f"{match.group(1).upper()}-{int(match.group(2))}", text)


def _normalize_pathish(text: str) -> str:
    return _normalize(text).replace("\\", "/")


def _timezone_from_args(args: Any):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
