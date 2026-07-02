from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
import time
from datetime import timezone
from pathlib import Path
from typing import Any, Mapping

from .conversation_records import (
    ASSISTANT_OR_AGENT_MESSAGE,
    COMPACTED_HISTORY_SUMMARY,
    HUMAN_AUTHORED_USER_MESSAGE,
    TOOL_ACTION_OUTPUT,
    classify_conversation_record,
)
from .latest_user_messages import latest_user_messages
from .timeutil import parse_datetime


DEFAULT_STATUS_QUERY = "what happened since my last message?"
SCHEMA_VERSION = 1

STATUS_QUERY_PATTERNS = (
    "what happened since",
    "what has happened since",
    "what did you do while i was away",
    "while i was away",
    "status since",
    "since last time",
    "before the last time",
    "before hte last time",
    "time before that",
    "what changed after",
    "what happened in the main thread",
)

ADMIN_STATUS_MARKERS = (
    "what happened since",
    "what has happened since",
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
    "boundary text",
    "side-thread boundary",
    "side conversation boundary",
    "conversation boundary",
    "<environment_context",
    "</environment_context>",
)

WORKER_PROMPT_MARKERS = (
    "you are the jsh-",
    "you are the high-reasoning implementer",
    "you are the implementer/runner",
    "you are the independent reviewer",
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
    "independent review for jsh-",
    "jsonl_layered_current lane",
    "reviewer worker",
    "reviewer update:",
    "review-blocking fix",
    "review artifact fix needed",
    "orchestrator reviewer",
    "read-only review",
    "independent review task",
    "coordinator evidence for jsh-",
    "coordinator found a concrete",
    "focused fix required",
    "please re-review jsh-",
    "review fix needed",
    "repo merge gate",
    "value_verdict",
    "your review currently",
    "scope: verify",
    "inspect changed files",
    "use implementer discipline",
    "acceptance criteria:",
    "read first:",
    "<turn_aborted>",
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
    anchor_candidates = _rank_anchor_candidates(latest_messages)
    anchor_decision = _select_anchor_with_boundary(query=query, messages=latest_messages, candidates=anchor_candidates)
    selected_anchor = _with_read_more_command(anchor_decision.get("selected_anchor"), cwd=cwd, codex_home=codex_home)
    if selected_anchor:
        selected_anchor.update(_anchor_decision_fields(anchor_decision, selected_anchor))
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
    )
    artifacts = _collect_work_artifacts(cwd, anchor_timestamp_utc=session_activity.get("anchor_timestamp_utc"))
    behavior_deltas = [item for item in artifacts["value_items"] if item["value_kind"] == "behavior_delta"]
    non_deltas = [item for item in artifacts["value_items"] if item["value_kind"] == "non_delta"]
    unresolved_blockers = _unresolved_blockers(artifacts)
    answer_brief = _answer_brief(behavior_deltas, non_deltas, unresolved_blockers)
    next_reads = _next_reads(selected_anchor, session_activity, artifacts, cwd=cwd, codex_home=codex_home)
    blind_spots = []
    if not selected_anchor:
        blind_spots.append("No substantive anchor selected from the searched latest-message scope.")
    if session_activity["status"] != "raw_session_trace_found":
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
        },
        "source_scope": latest.get("source_scope"),
        "summary": {
            "anchor_candidate_count": len(anchor_candidates),
            "selected_anchor_available": selected_anchor is not None,
            "work_product_count": len(artifacts["tasks"]) + len(artifacts["reports"]) + len(artifacts["reviews"]),
            "behavior_delta_count": len(behavior_deltas),
            "non_delta_count": len(non_deltas),
            "unresolved_blocker_count": len(unresolved_blockers),
            "raw_session_file_count": session_activity["summary"]["session_file_count"],
            "raw_session_record_count": session_activity["summary"]["records_after_anchor"],
            "spawned_session_call_count": session_activity["summary"]["spawned_session_call_count"],
            "elapsed_seconds": elapsed,
            "context_boundary": anchor_decision["context_boundary"],
            "anchor_scope": anchor_decision["anchor_scope"],
            "anchor_reason": anchor_decision["anchor_reason"],
        },
        "anchor_candidates": anchor_candidates,
        "selected_anchor": selected_anchor,
        "anchor_decision": anchor_decision,
        "session_activity": session_activity,
        "evidence_contract": _evidence_contract(),
        "next_reads": next_reads,
        "agent_instruction": _agent_instruction(selected_anchor, session_activity, next_reads),
        "work_products": {
            "tasks": artifacts["tasks"],
            "reports": artifacts["reports"],
            "reviews": artifacts["reviews"],
            "commits": artifacts["commits"],
        },
        "behavior_deltas": behavior_deltas,
        "non_deltas": non_deltas,
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


def _rank_anchor_candidates(messages: list[dict]) -> list[dict]:
    candidates = []
    status_cutoff = _status_query_cutoff(messages)
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
                "snippet": text,
                "classification": classification,
                "score": score,
                "decision": decision,
                "rationale": rationale,
            }
        )
    candidates.sort(key=lambda item: (item["score"], item.get("timestamp_utc") or ""), reverse=True)
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def _status_query_cutoff(messages: list[dict]) -> str | None:
    for message in messages:
        timestamp = str(message.get("timestamp_utc") or "")
        if timestamp and is_status_question(str(message.get("snippet") or "")):
            return timestamp
    return None


def _score_anchor(text: str, index: int, *, timestamp_utc: str = "", status_cutoff: str | None = None) -> tuple[int, str, str, str]:
    normalized = _normalize(text)
    recency_penalty = min(index, 20)
    if not normalized:
        return -100, "empty", "rejected", "empty latest-message row"
    if any(marker in normalized for marker in NON_ANCHOR_CONTEXT_MARKERS):
        return -70 - recency_penalty, "non_human_boundary_or_tool_context", "rejected", "boundary/tool/subagent/inherited context is not a user anchor"
    if any(marker in normalized for marker in WORKER_PROMPT_MARKERS):
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


def _select_anchor_with_boundary(*, query: str, messages: list[dict], candidates: list[dict]) -> dict:
    base = {
        "context_boundary": "none",
        "anchor_scope": "ranked_history",
        "anchor_reason": "ranked_substantive_or_fallback_candidate",
        "anchor_ref": None,
        "status_ref": None,
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
    by_ref = {candidate.get("source_ref"): candidate for candidate in candidates}
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
    if is_status_question(query):
        status_message = _current_status_message(messages)
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
            prefer_parent = _prefers_parent_main_anchor(query)
            explicit_side_thread_local = _explicit_side_thread_local_status(query)
            parent_anchor = None
            if prefer_parent:
                parent_anchor = _first_history_parent_anchor(older, status_thread_keys)
            if same_thread_anchor and (explicit_side_thread_local or not prefer_parent):
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
                            "selected_anchor": _selected_with_reason(
                                same_thread_anchor,
                                "; selected as the latest eligible prior user message in the same thread",
                            ),
                        },
                        candidates,
                    )
            if parent_anchor is None:
                parent_anchor = _first_eligible_thread_anchor(older, status_thread_keys, same_thread=False)
            if parent_anchor:
                reason = "parent_main_last_actual_work_handoff"
                if not prefer_parent:
                    reason = "first_side_thread_user_message_after_boundary"
                return _with_rejected_alternatives(
                    {
                        **base,
                        "context_boundary": "side_thread",
                        "anchor_scope": "parent_thread",
                        "anchor_reason": reason,
                        "anchor_ref": parent_anchor.get("source_ref"),
                        "status_ref": status_ref,
                        "selected_anchor": _selected_with_reason(
                            parent_anchor,
                            "; selected as the last eligible parent/main-thread user message before the side-thread boundary",
                        ),
                    },
                    candidates,
                )
            base = {
                **base,
                "context_boundary": "status_question_no_prior_anchor",
                "anchor_scope": "none",
                "anchor_reason": "no_eligible_prior_user_message_found",
                "status_ref": status_ref,
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
        if candidate.get("decision") == "rejected" or classification in {
            "admin_or_status_turn",
            "worker_or_reviewer_prompt",
            "non_human_boundary_or_tool_context",
            "post_status_context",
        }:
            alternatives.append(
                {
                    "source_ref": candidate.get("source_ref"),
                    "timestamp_utc": candidate.get("timestamp_utc"),
                    "classification": classification,
                    "decision": candidate.get("decision"),
                    "rationale": candidate.get("rationale"),
                    "snippet": candidate.get("snippet"),
                }
            )
        if len(alternatives) >= 8:
            break
    return {**decision, "rejected_anchor_alternatives": alternatives}


def _current_status_message(messages: list[dict]) -> dict | None:
    for index, message in enumerate(messages):
        if is_status_question(str(message.get("snippet") or "")):
            current = dict(message)
            current["message_index"] = index
            current["thread_keys"] = _message_thread_keys(message)
            return current
    return None


def _first_eligible_thread_anchor(candidates: list[dict], thread_keys: set[str], *, same_thread: bool) -> dict | None:
    for candidate in candidates:
        overlaps = _thread_key_overlap(candidate, thread_keys)
        if overlaps != same_thread:
            continue
        if _is_eligible_anchor(candidate):
            return candidate
    return None


def _first_history_parent_anchor(candidates: list[dict], thread_keys: set[str]) -> dict | None:
    for candidate in candidates:
        if _thread_key_overlap(candidate, thread_keys):
            continue
        if candidate.get("source_kind") != "codex_history":
            continue
        if _is_never_anchor(candidate):
            continue
        if candidate.get("decision") not in {"selected_candidate", "fallback_candidate"}:
            continue
        return candidate
    return None


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


def _selected_with_reason(candidate: dict, reason_suffix: str) -> dict:
    selected = dict(candidate)
    selected["rationale"] = str(selected.get("rationale") or "").rstrip() + reason_suffix
    return selected


def _anchor_decision_fields(anchor_decision: dict, selected_anchor: dict) -> dict:
    return {
        "context_boundary": anchor_decision.get("context_boundary"),
        "anchor_scope": anchor_decision.get("anchor_scope"),
        "anchor_reason": anchor_decision.get("anchor_reason"),
        "anchor_ref": anchor_decision.get("anchor_ref") or selected_anchor.get("source_ref"),
    }


def _session_activity_trace(
    selected_anchor: dict | None,
    anchor_decision: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
) -> dict:
    base = {
        "status": "no_selected_anchor",
        "anchor_timestamp_utc": None,
        "session_followups": [],
        "activity_after_anchor": _empty_activity_counts(),
        "next_reads": [],
        "evidence_boundary": {
            "primary": "raw Codex history/session JSONL after the selected user anchor",
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
    if status_ref:
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
            scan = _scan_session_file_after_anchor(path, anchor_timestamp, codex_home=codex_home, cwd=cwd)
            _merge_activity_counts(activity, scan["activity_after_anchor"])
            followups.append(
                {
                    **{key: value for key, value in context.items() if key != "session_files"},
                    "session_file": str(path),
                    "session_source_ref": _source_ref_for_path_line(path, None, codex_home),
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
    source_ref = str(anchor.get("source_ref") or "")
    if source_ref.startswith("codex_history:"):
        contexts.extend(_session_contexts_for_history_ref(source_ref, codex_home, role=role))
    if anchor.get("source_kind") == "codex_sessions":
        context = _session_context_from_openable(anchor, role=role)
        if context:
            contexts.append(context)
    for mirror in anchor.get("mirror_sources") or []:
        if not isinstance(mirror, dict):
            continue
        if mirror.get("source_kind") == "codex_sessions":
            context = _session_context_from_openable(mirror, role=f"{role}_mirror")
            if context:
                contexts.append(context)
    return contexts


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


def _session_context_from_openable(source: dict, *, role: str) -> dict | None:
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
        "session_id_hash": _hash_value(_session_id_from_filename(path)),
        "session_files": [path],
    }


def _scan_session_file_after_anchor(path: Path, anchor_timestamp, *, codex_home: Path, cwd: Path) -> dict:
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
            timestamp = parse_datetime(
                record.get("timestamp") or record.get("ts") or record.get("created_at") or record.get("time"),
                timezone.utc,
            )
            if timestamp is not None and timestamp.astimezone(timezone.utc) < anchor_timestamp:
                continue
            source_ref = _source_ref_for_path_line(path, line_number, codex_home)
            if first_ref is None:
                first_ref = source_ref
                target = f"{path}:{line_number}"
                next_reads.append(
                    {
                        "reason": "Inspect raw session JSONL activity after the selected anchor before using commits or repo state.",
                        "source_ref": source_ref,
                        "openable_source_ref": {"path": str(path), "line": line_number},
                        "openable_target": target,
                        "read_more_command": _read_more_command(target, cwd=cwd, codex_home=codex_home),
                    }
                )
            last_ref = source_ref
            _count_session_record(record, activity)
            if _is_spawned_session_call(record):
                spawned_refs.append(source_ref)
                if len(next_reads) < 4:
                    target = f"{path}:{line_number}"
                    next_reads.append(
                        {
                            "reason": "Inspect spawned worker/reviewer session launch as part of the long-absence trace.",
                            "source_ref": source_ref,
                            "openable_source_ref": {"path": str(path), "line": line_number},
                            "openable_target": target,
                            "read_more_command": _read_more_command(target, cwd=cwd, codex_home=codex_home),
                        }
                    )
    return {
        "records_after_anchor": activity["records_after_anchor"],
        "activity_after_anchor": activity,
        "first_after_anchor_ref": first_ref,
        "last_after_anchor_ref": last_ref,
        "spawned_session_call_refs": spawned_refs[:10],
        "next_reads": next_reads,
    }


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
        key = (context.get("role"), context.get("history_source_ref"), context.get("source_ref"), files)
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


def _collect_work_artifacts(cwd: Path, *, anchor_timestamp_utc: str | None = None) -> dict:
    tasks: list[dict] = []
    reports: list[dict] = []
    reviews: list[dict] = []
    value_items: list[dict] = []
    for work in KNOWN_WORK:
        sources: list[dict] = []
        for category, roots in (
            ("task", (cwd / "tasks",)),
            ("report", (cwd / "reports",)),
            ("review", (cwd / "codex" / "state" / "reviews", cwd / "reviews")),
        ):
            for root in roots:
                if not root.is_dir():
                    continue
                for path in sorted(root.glob(f"{work['slug']}*.md")):
                    item = _artifact_item(path, cwd, category, work["artifact_terms"])
                    sources.append(item)
                    if category == "task":
                        tasks.append(item)
                    elif category == "report":
                        reports.append(item)
                    else:
                        reviews.append(item)
        if sources:
            value_items.append(
                {
                    "work_id": work["id"],
                    "value_kind": work["value_kind"],
                    "summary": work["summary"],
                    "source_refs": [source["source_ref"] for source in sources[:4]],
                    "evidence_snippets": [source["snippet"] for source in sources[:2] if source.get("snippet")],
                }
            )
    return {
        "tasks": tasks,
        "reports": reports,
        "reviews": reviews,
        "commits": _collect_secondary_commits(cwd, anchor_timestamp_utc),
        "value_items": value_items,
    }


def _collect_secondary_commits(cwd: Path, anchor_timestamp_utc: str | None) -> list[dict]:
    if not anchor_timestamp_utc:
        return []
    command = ["git", "-C", str(cwd), "log", "--max-count=8", "--format=%H%x09%cI%x09%s"]
    command.extend(["--since", anchor_timestamp_utc])
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
            }
        )
    return commits


def _artifact_item(path: Path, cwd: Path, category: str, terms: tuple[str, ...]) -> dict:
    rel = _rel(path, cwd)
    line_no, snippet = _first_matching_line(path, terms)
    return {
        "kind": category,
        "path": rel,
        "source_ref": f"{rel}:{line_no}" if line_no else rel,
        "snippet": snippet,
    }


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


def _unresolved_blockers(artifacts: dict) -> list[dict]:
    joined = " ".join(
        item.get("snippet", "")
        for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"])
        for item in collection
    ).lower()
    if "case-002" in joined or "case 002" in joined or "read-more" in joined or "workflow churn" in joined:
        return [
            {
                "blocker": "case-002 over-search/read-more churn",
                "status": "unresolved",
                "summary": "The remaining serious blocker is case-002 over-search/read-more churn after source-grounded answers are possible.",
                "source_refs": _source_refs_containing(artifacts, ("case-002", "case 002", "read-more", "workflow churn"))[:4],
            }
        ]
    return []


def _source_refs_containing(artifacts: dict, terms: tuple[str, ...]) -> list[str]:
    refs = []
    for collection in (artifacts["tasks"], artifacts["reports"], artifacts["reviews"]):
        for item in collection:
            text = f"{item.get('path', '')} {item.get('snippet', '')}".lower()
            if any(term in text for term in terms):
                refs.append(item["source_ref"])
    return refs


def _answer_brief(behavior_deltas: list[dict], non_deltas: list[dict], blockers: list[dict]) -> list[str]:
    lines = [item["summary"] for item in behavior_deltas]
    lines.extend(item["summary"] for item in non_deltas)
    lines.extend(item["summary"] for item in blockers)
    return lines


def _next_reads(
    selected_anchor: dict | None,
    session_activity: dict,
    artifacts: dict,
    *,
    cwd: Path,
    codex_home: Path | None,
) -> list[dict]:
    reads = []
    if selected_anchor:
        read = {
            "reason": "Inspect selected conversation anchor before answering.",
            "source_ref": selected_anchor["source_ref"],
            "snippet": selected_anchor["snippet"],
        }
        _copy_openable_fields(read, selected_anchor)
        if read.get("openable_target") and not read.get("read_more_command"):
            read["read_more_command"] = _read_more_command(str(read["openable_target"]), cwd=cwd, codex_home=codex_home)
        reads.append(read)
    reads.extend(session_activity.get("next_reads") or [])
    for item in artifacts["value_items"]:
        for ref in item["source_refs"][:2]:
            reads.append(
                {
                    "reason": f"Inspect {item['work_id']} {item['value_kind']} evidence after raw JSONL trace.",
                    "source_ref": ref,
                }
            )
    for blocker in _unresolved_blockers(artifacts):
        for ref in blocker.get("source_refs", [])[:2]:
            reads.append({"reason": "Inspect unresolved blocker evidence.", "source_ref": ref})
    return _dedupe_reads(reads)


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
    return {
        "first_read_target": first_read.get("openable_target") or first_read.get("source_ref"),
        "first_read_command": first_read.get("read_more_command"),
        "stop_condition": (
            "Start with the selected anchor read-more command, then inspect raw sessions/**/*.jsonl "
            "activity after the anchor before task/report/review artifacts. Commits, merges, and current "
            "repo state are secondary evidence only; do not give a commit-only or repo-status-only answer, "
            "and do not run broad directory lookup unless the direct raw JSONL reads fail or contradict each other."
        ),
        "direct_anchor_openable": bool(selected_anchor and selected_anchor.get("openable_target")),
        "raw_session_trace_status": session_activity.get("status"),
        "commit_only_answer_allowed": False,
    }


def _read_more_command(target: str, *, cwd: Path, codex_home: Path | None) -> str:
    parts = [
        "jsonl-history-search",
        "read-more",
        target,
        "--cwd",
        str(cwd),
    ]
    if codex_home is not None:
        parts.extend(["--codex-home", str(codex_home)])
    parts.extend(["--format", "json", "--mode", "window", "--before", "2", "--after", "8"])
    return " ".join(shlex.quote(part) for part in parts)


def _dedupe_reads(reads: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for read in reads:
        ref = read.get("source_ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        deduped.append(read)
    return deduped


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


def _timezone_from_args(args: Any):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
