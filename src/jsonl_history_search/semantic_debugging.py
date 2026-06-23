from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from .agent_contract import (
    build_agent_find_payload,
    build_compact_provenance_handoff,
    compact_stage_diagnostics_summary,
    compact_retrieval_pressure_summary,
    context_actions_for_source_ref,
    expansion_action_context_from_source_plan,
    build_source_scope_summary,
    build_stage_diagnostics,
    build_source_locator_handoff,
    estimate_tokens,
    read_more_actions_for_source_ref,
    reduction_ratio,
    source_ref_target,
)
from .claims import classify_claim_action, parse_review_verdict
from .forensics import build_forensics_payload
from .query import infer_query
from .real_history_gold import validate_manifest_file
from .retrieval_modes import RETRIEVAL_MODE_PACKAGE, run_retrieval_mode
from .scanner import search
from .tool_events import build_tool_events_payload


SCHEMA_VERSION = 1
INLINE_CANDIDATE_LIMIT = 8
INLINE_TIMELINE_LIMIT = 12
INLINE_EVENT_LIMIT = 12
DEFAULT_GOLD_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")

DECISION_CONTEXT_ROUTES = {
    "conversation_reconstruction",
    "git_history_reconstruction",
    "task_review_join",
    "tool_action_proof",
    "source_ref_followup",
    "no_evidence_scoped_report",
}

SUPPORTED_QUESTION_FAMILIES = {
    "why_did_this_happen": "Vague bug, incident, regression, symptom, or assumption questions.",
    "provenance": "Task, path, branch, commit, review, or artifact provenance questions.",
    "claimed_action": "Whether an agent actually ran, edited, tested, reviewed, or claimed a concrete action.",
    "remembered_instruction": "Prior user instruction, preference, feedback, or remembered wording recovery.",
    "no_evidence": "Bounded not-found reporting for the searched source scope.",
}

UNSUPPORTED_QUESTION_FAMILIES = {
    "automatic_root_cause": "The packet does not prove root cause automatically; it emits candidates and uncertainty.",
    "exhaustive_all_history_absence": "No-evidence means not found in searched scope, not absent from all possible logs.",
    "default_embeddings_or_sidecars": "No default embeddings, timestamp sidecars, native scanner, or persistent indexes are run.",
    "semantic_linter_runtime": "No Semantic Linter daemon, hook, pending-file bridge, or live runtime dependency is used.",
    "private_bridge_required": "Committed semantic-debug evidence does not require private benchmark bridge artifacts.",
}


def build_semantic_debug_payload(
    args: Any,
    *,
    source_plan: dict[str, Any],
    intent: Any,
    plan: Any,
    default_tz: Any,
    mask_context: Any = None,
    configuration_warnings: list[str] | None = None,
) -> dict[str, Any]:
    expansion_action_context = expansion_action_context_from_source_plan(source_plan)
    result = run_retrieval_mode(
        retrieval_mode=getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE),
        plan=plan,
        intent=intent,
        default_tz=default_tz,
        cwd=source_plan["cwd"],
        mask_context=mask_context,
        use_rg_prefilter=getattr(args, "rg_prefilter", False),
        fielded_bm25_index_dir=getattr(args, "fielded_bm25_index_dir", None),
        query_plan_mode=getattr(args, "query_plan", "auto"),
    )
    result["warnings"] = sorted(
        set(result.get("warnings", []) + intent.warnings + (configuration_warnings or []))
    )
    requested_evidence_context_window = max(0, int(getattr(args, "evidence_context_window", 0) or 0))
    agent_payload = build_agent_find_payload(
        source_plan=source_plan,
        search_result=result,
        manifest_report=None,
        output_format=getattr(args, "format", "json"),
        report_output=getattr(args, "report_output", None),
        compact_provenance_handoff=False,
        evidence_context_window=0,
    )
    agent_find = agent_payload["agent_find"]

    tool_payload = build_tool_events_payload(
        source_plan=source_plan,
        plan=plan,
        default_tz=default_tz,
        mask_context=mask_context,
        event_types=[],
        skill=None,
        path_globs=[args.path] if getattr(args, "path", None) else [],
        output_format=getattr(args, "format", "json"),
        report_output=None,
    )
    tool_report = tool_payload["structured_tool_events"]

    hints = {
        "task": getattr(args, "task", None),
        "path": getattr(args, "path", None),
        "commit": getattr(args, "commit", None),
        "branch": getattr(args, "branch", None),
        "around": getattr(args, "around", None),
    }
    forensics_payload = build_forensics_payload(args) if any(hints.values()) else None

    family = classify_question_family(agent_find["query"]["original"], hints=hints, status=agent_find["status"])
    source_scope = build_source_scope_summary(source_plan, result)
    candidates = compact_candidates(agent_find.get("find_candidates", []))
    timeline = compact_timeline(agent_find.get("timeline", []))
    tool_proof = compact_tool_proof(tool_report)
    joins = compact_joins(forensics_payload, hints)
    joins = merge_local_task_review_joins(joins, hints, cwd=Path(source_plan["cwd"]))
    forensics_workflow = compact_forensics_workflow(forensics_payload, joins)
    no_evidence = build_no_evidence_section(agent_find, source_scope, tool_report, forensics_payload)
    uncertainty = build_uncertainty(agent_find, source_scope, tool_report, forensics_payload, family)
    next_reads = build_next_reads(
        candidates,
        tool_proof,
        joins,
        hints,
        expansion_action_context=expansion_action_context,
        include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
    )
    decision_context = build_decision_context(
        query=agent_find["query"]["original"],
        family=family,
        source_scope=source_scope,
        candidates=candidates,
        tool_proof=tool_proof,
        joins=joins,
        no_evidence=no_evidence,
        uncertainty=uncertainty,
        next_reads=next_reads,
        hints=hints,
        source_plan=source_plan,
    )
    audit_plan = build_audit_plan(
        query=agent_find["query"]["original"],
        routes=decision_context["selected_routes"],
        source_scope=source_scope,
        candidates=candidates,
        tool_proof=tool_proof,
        joins=joins,
        no_evidence=no_evidence,
        next_reads=next_reads,
        next_commands=decision_context["next_commands"],
        uncertainty=uncertainty,
        hints=hints,
        watchlist_inputs=list(getattr(args, "watchlist", []) or []),
    )
    source_locator_handoff = build_source_locator_handoff(
        source_plan=source_plan,
        search_result=result,
        candidates=candidates,
        next_reads=next_reads,
        source_family="local_ai_conversation_jsonl",
        question_family=compact_handoff_question_family(family),
        product_path="semantic_debug",
        no_evidence_report=agent_find.get("no_evidence_report"),
        retrieval_pressure=agent_find.get("retrieval_pressure"),
        expansion_action_context=expansion_action_context,
        include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
        evidence_context_window=requested_evidence_context_window,
    )
    if source_locator_handoff.get("next_reads") != next_reads:
        next_reads = list(source_locator_handoff.get("next_reads") or next_reads)
        decision_context["next_reads"] = next_reads[:12]
        decision_context["next_commands"] = build_next_commands(
            next_reads,
            hints=hints,
            include_semantic_debug_replay=False,
        )
        audit_plan["resume_from"] = resume_from(next_reads, audit_plan.get("route_order", []))
        audit_plan["replay_commands"] = build_next_commands(
            next_reads,
            hints=hints,
            include_semantic_debug_replay=True,
        )
        audit_plan["next_commands"] = decision_context["next_commands"]
    tradeoffs = build_tradeoff_counters(agent_find, tool_report, candidates, timeline, tool_proof)
    benchmark_gate = build_manifest_evaluation(
        Path(getattr(args, "gold_manifest", DEFAULT_GOLD_MANIFEST)),
        source_plan=source_plan,
        plan=plan,
        default_tz=default_tz,
        mask_context=mask_context,
        output_format=getattr(args, "format", "json"),
        use_rg_prefilter=getattr(args, "rg_prefilter", False),
        evaluate_cases=getattr(args, "evaluate_sanitized_manifest_gate", False),
    )
    candidate_causes = build_candidate_causes(candidates, timeline, tool_proof, joins)
    routes = build_routes(agent_find, source_scope, tool_report, forensics_payload, hints, benchmark_gate)
    final_answer_contract = build_final_answer_source_use_contract(
        status=agent_find["status"],
        candidates=candidates,
        source_locator_handoff=source_locator_handoff,
        tool_proof=tool_proof,
        joins=joins,
        no_evidence=no_evidence,
        uncertainty=uncertainty,
        hints=hints,
        source_scope=source_scope,
    )
    guided_audit_flow = build_guided_audit_flow(
        status=agent_find["status"],
        family=family,
        source_scope=source_scope,
        decision_context=decision_context,
        audit_plan=audit_plan,
        forensics_workflow=forensics_workflow,
        source_locator_handoff=source_locator_handoff,
        final_answer_contract=final_answer_contract,
        no_evidence=no_evidence,
        next_reads=next_reads,
        next_commands=decision_context["next_commands"],
    )

    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": "no_evidence" if agent_find["status"] == "no_evidence" else "candidates_found",
        "query": agent_find["query"]["original"],
        "question_family": family,
        "scope": source_scope,
        "routes": routes,
        "source_locator_handoff": source_locator_handoff,
        "strongest_candidates": candidates,
        "timeline": timeline,
        "tool_action_proof": tool_proof,
        "task_review_commit_joins": joins,
        "forensics_workflow": forensics_workflow,
        "decision_context": decision_context,
        "audit_plan": audit_plan,
        "candidate_causes": candidate_causes,
        "no_evidence": no_evidence,
        "uncertainty": uncertainty,
        "guided_audit_flow": guided_audit_flow,
        "next_reads": next_reads,
        "final_answer_source_use_contract": final_answer_contract,
        "token_byte_tradeoffs": tradeoffs,
        "sanitized_manifest_gate_evidence": benchmark_gate,
        "claim_boundaries": {
            "root_cause_claim": "not_claimed",
            "allowed_cause_language": ["candidate_cause", "likely_contributing_assumption"],
            "private_text_committed": False,
            "semantic_linter_runtime_dependency": False,
            "default_embeddings_or_sidecars": False,
            "native_scanner_default": False,
        },
    }
    if result.get("retrieval_diagnostics"):
        packet["retrieval_diagnostics"] = result["retrieval_diagnostics"]
        packet["claim_boundaries"].update(result["retrieval_diagnostics"].get("claim_boundaries", {}))
    if agent_find.get("retrieval_pressure"):
        packet["retrieval_pressure"] = agent_find["retrieval_pressure"]
    if source_locator_handoff.get("stage_diagnostics") and packet.get("retrieval_pressure"):
        packet["retrieval_pressure"]["stage_diagnostics"] = source_locator_handoff["stage_diagnostics"]
    if getattr(args, "compact_provenance_handoff", False):
        packet["compact_provenance_handoff"] = build_compact_provenance_handoff(
            status=packet["status"],
            command_surface="semantic_debug",
            candidates=candidates,
            source_locator_handoff=source_locator_handoff,
            next_reads=next_reads,
            no_evidence=no_evidence,
            retrieval_pressure=packet.get("retrieval_pressure"),
        )
        compact_stage_diagnostics = build_stage_diagnostics(
            product_path="semantic_debug",
            query_plan=source_locator_handoff.get("query_plan"),
            search_result=result,
            retrieval_pressure=packet.get("retrieval_pressure"),
            source_window_packaging=source_locator_handoff.get("source_window_packaging"),
            evidence_packet_summary=source_locator_handoff.get("evidence_packet_packaging"),
            compact_context_budget=packet["compact_provenance_handoff"].get("context_budget"),
            no_evidence_report=agent_find.get("no_evidence_report"),
            compact_handoff_requested=True,
        )
        if compact_stage_diagnostics:
            source_locator_handoff["stage_diagnostics"] = compact_stage_diagnostics
            if packet.get("retrieval_pressure"):
                packet["retrieval_pressure"]["stage_diagnostics"] = compact_stage_diagnostics
                packet["compact_provenance_handoff"]["retrieval_pressure_summary"] = (
                    compact_retrieval_pressure_summary(packet["retrieval_pressure"])
                )
            packet["compact_provenance_handoff"]["derived_from"].append("stage_diagnostics_summary")
            packet["compact_provenance_handoff"]["stage_diagnostics_summary"] = compact_stage_diagnostics_summary(
                compact_stage_diagnostics
            )
    return {
        "semantic_debug": packet,
        "warnings": sorted(set(result.get("warnings", []) + tool_payload.get("warnings", []) + (forensics_payload or {}).get("warnings", []))),
        "blind_spots": sorted(set(result.get("blind_spots", []) + tool_payload.get("blind_spots", []) + (forensics_payload or {}).get("blind_spots", []))),
    }


def classify_question_family(query: str, *, hints: dict[str, str | None], status: str) -> dict[str, Any]:
    text = query.lower()
    detected: list[str] = []
    if any(word in text for word in ("why", "cause", "caused", "happen", "regression", "bug", "symptom", "assumption")):
        detected.append("why_did_this_happen")
    if any(hints.values()) or any(word in text for word in ("commit", "branch", "task", "review", "provenance", "path")):
        detected.append("provenance")
    if any(word in text for word in ("actually", "claimed", "claim", "ran", "edited", "tested", "reviewed", "did the agent")):
        detected.append("claimed_action")
    if any(phrase in text for phrase in ("told you", "tell you before", "remember", "instruction", "preference", "said before", "what did i say")):
        detected.append("remembered_instruction")
    if status == "no_evidence":
        detected.append("no_evidence")
    if not detected:
        detected.append("why_did_this_happen")
    detected = dedupe(detected)
    unsupported_detected = []
    if "root cause" in text or "root-cause" in text or "automaticrootcause" in text:
        unsupported_detected.append("automatic_root_cause")
    if "all history" in text or "all-history" in text or "everywhere" in text or "prove absent" in text:
        unsupported_detected.append("exhaustive_all_history_absence")
    return {
        "primary": detected[0],
        "detected_supported": detected,
        "supported_question_families": [
            {"label": label, "meaning": SUPPORTED_QUESTION_FAMILIES[label]} for label in sorted(SUPPORTED_QUESTION_FAMILIES)
        ],
        "detected_unsupported": unsupported_detected,
        "unsupported_question_families": [
            {"label": label, "meaning": meaning} for label, meaning in sorted(UNSUPPORTED_QUESTION_FAMILIES.items())
        ],
    }


def build_decision_context(
    *,
    query: str,
    family: dict[str, Any],
    source_scope: dict[str, Any],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    uncertainty: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
    hints: dict[str, str | None],
    source_plan: dict[str, Any],
) -> dict[str, Any]:
    project_time_needed = project_time_reconstruction_needed(query, hints)
    selected_routes = select_decision_routes(
        family=family,
        status=no_evidence.get("answer_scope"),
        candidates=candidates,
        tool_proof=tool_proof,
        joins=joins,
        project_time_needed=project_time_needed,
    )
    return {
        "primary_question_family": family.get("primary"),
        "decision_summary": decision_summary(
            status=no_evidence.get("answer_scope"),
            candidate_count=len(candidates),
            route_count=len(selected_routes),
            project_time_needed=project_time_needed,
        ),
        "decision_time_or_window": decision_time_or_window(
            candidates=candidates,
            hints=hints,
            source_plan=source_plan,
        ),
        "project_time_reconstruction_needed": project_time_needed,
        "selected_routes": selected_routes,
        "evidence_handles": decision_evidence_handles(candidates, tool_proof, joins),
        "searched_scope": source_scope.get("searched_scope"),
        "not_searched_scope": source_scope.get("not_searched_scope"),
        "confidence": decision_confidence(
            candidates=candidates,
            tool_proof=tool_proof,
            joins=joins,
            no_evidence=no_evidence,
            source_scope=source_scope,
        ),
        "uncertainty": uncertainty,
        "next_reads": next_reads[:12],
        "next_commands": build_next_commands(next_reads, hints=hints, include_semantic_debug_replay=False),
    }


def build_audit_plan(
    *,
    query: str,
    routes: list[str],
    source_scope: dict[str, Any],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    next_reads: list[dict[str, Any]],
    next_commands: list[dict[str, Any]],
    uncertainty: list[dict[str, Any]],
    hints: dict[str, str | None],
    watchlist_inputs: list[str],
) -> dict[str, Any]:
    route_order = order_decision_routes(routes)
    return {
        "watchlist": parse_watchlist(query, watchlist_inputs),
        "route_order": route_order,
        "checkpoint_plan": checkpoint_plan(route_order),
        "resume_from": resume_from(next_reads, route_order),
        "replay_commands": build_next_commands(next_reads, hints=hints, include_semantic_debug_replay=True),
        "retained_evidence_ledger": retained_evidence_ledger(candidates, tool_proof, joins),
        "omitted_or_dropped_scope": omitted_or_dropped_scope(source_scope, no_evidence),
        "aggregate_report": {
            "candidate_count": len(candidates),
            "structured_event_count": tool_proof.get("event_count", 0),
            "task_review_join_status": joins.get("status"),
            "no_evidence_answer_scope": no_evidence.get("answer_scope"),
            "uncertainty_labels": [item.get("label") for item in uncertainty],
            "route_count": len(route_order),
        },
        "cap_pressure_notes": cap_pressure_notes(source_scope),
        "next_commands": next_commands,
    }


def project_time_reconstruction_needed(query: str, hints: dict[str, str | None]) -> bool:
    text = query.lower()
    decision_terms = (
        "decision",
        "decide",
        "decided",
        "rationale",
        "why did",
        "why was",
        "why were",
        "time",
        "date",
        "when",
        "around",
        "before",
        "after",
        "commit",
        "branch",
        "git",
        "codebase",
        "project state",
        "repo state",
        "what was i talking about",
    )
    return any(hints.get(key) for key in ("commit", "branch", "around", "path")) or any(term in text for term in decision_terms)


def select_decision_routes(
    *,
    family: dict[str, Any],
    status: str | None,
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    project_time_needed: bool,
) -> list[str]:
    selected = ["conversation_reconstruction"]
    detected = set(family.get("detected_supported", []))
    if project_time_needed:
        selected.append("git_history_reconstruction")
    if joins.get("status") in {"joined", "no_join_evidence", "not_run"} and (
        project_time_needed or "provenance" in detected or joins.get("review_evidence")
    ):
        selected.append("task_review_join")
    if tool_proof.get("event_count", 0) or "claimed_action" in detected:
        selected.append("tool_action_proof")
    if candidates:
        selected.append("source_ref_followup")
    if status == "not_found_in_searched_scope" or "no_evidence" in detected:
        selected.append("no_evidence_scoped_report")
    return [route for route in dedupe(selected) if route in DECISION_CONTEXT_ROUTES]


def decision_summary(
    *,
    status: str | None,
    candidate_count: int,
    route_count: int,
    project_time_needed: bool,
) -> str:
    if status == "not_found_in_searched_scope":
        return "No final decision is claimed; the packet preserves searched-scope no-evidence boundaries and follow-up routes."
    if candidate_count:
        suffix = " including project-time reconstruction" if project_time_needed else ""
        return f"Candidate decision context is available from {candidate_count} bounded evidence handle(s) across {route_count} route(s){suffix}."
    return "Decision context is not established yet; use the selected routes and next commands to gather source-backed evidence."


def decision_time_or_window(
    *,
    candidates: list[dict[str, Any]],
    hints: dict[str, str | None],
    source_plan: dict[str, Any],
) -> dict[str, Any]:
    query_window = (source_plan.get("query") or {}).get("date_window") or {}
    candidate_times = [item.get("timestamp_utc") for item in candidates if item.get("timestamp_utc")]
    return {
        "around_hint": hints.get("around"),
        "query_date_window": {
            "start": query_window.get("start"),
            "end": query_window.get("end"),
            "reason": query_window.get("reason"),
        },
        "candidate_timestamp_min": min(candidate_times) if candidate_times else None,
        "candidate_timestamp_max": max(candidate_times) if candidate_times else None,
        "basis": "explicit_hint_or_query_window_or_candidate_timestamps",
    }


def decision_evidence_handles(
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
) -> list[dict[str, Any]]:
    handles: list[dict[str, Any]] = []
    for item in candidates[:8]:
        if item.get("source_ref"):
            handles.append(
                {
                    "kind": "candidate_source_ref",
                    "candidate_id": item.get("candidate_id"),
                    "source_ref": item.get("source_ref"),
                    "confidence": item.get("confidence"),
                }
            )
    for event in tool_proof.get("events", [])[:6]:
        if event.get("source_ref"):
            handles.append(
                {
                    "kind": "tool_event_source_ref",
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "source_ref": event.get("source_ref"),
                    "confidence": event.get("confidence"),
                }
            )
    for item in joins.get("review_evidence", [])[:6]:
        if item.get("path"):
            handles.append(
                {
                    "kind": "task_review_artifact",
                    "path": item.get("path"),
                    "exists": item.get("exists"),
                    "review_verdict": item.get("review_verdict"),
                }
            )
    if joins.get("commit"):
        handles.append({"kind": "git_commit", "commit": joins.get("commit"), "confidence": joins.get("confidence")})
    if joins.get("branch"):
        handles.append({"kind": "git_branch", "branch": joins.get("branch"), "confidence": joins.get("confidence")})
    return handles[:20]


def decision_confidence(
    *,
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    source_scope: dict[str, Any],
) -> dict[str, Any]:
    if no_evidence.get("needed"):
        level = "low"
        reasons = ["no_candidate_rows_in_searched_scope"]
    else:
        signals = 0
        reasons = []
        if candidates:
            signals += 1
            reasons.append("candidate_source_refs_available")
        if tool_proof.get("event_count", 0):
            signals += 1
            reasons.append("structured_tool_events_available")
        if joins.get("status") == "joined":
            signals += 1
            reasons.append("task_review_or_git_join_available")
        if signals >= 2:
            level = "medium"
        elif signals == 1:
            level = "low"
        else:
            level = "low"
            reasons.append("no_independent_decision_evidence")
    if source_scope.get("cap_hit", {}).get("hit"):
        reasons.append("scope_cap_limited")
    return {
        "level": level,
        "basis": reasons,
        "claim_boundary": "confidence describes route/evidence readiness, not final-answer truth",
    }


def parse_watchlist(query: str, watchlist_inputs: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(watchlist_inputs, 1):
        text = str(value).strip()
        if text:
            rows.append({"predicate": text, "source": "cli", "watch_id": f"watch-{index:03d}"})
    lowered = query.lower()
    inferred = {
        "failure": "failure_or_regression_mentions",
        "regression": "failure_or_regression_mentions",
        "blocked": "blocked_or_unverified_workflow",
        "blocker": "blocked_or_unverified_workflow",
        "not reviewed": "review_gap",
        "unreviewed": "review_gap",
        "claim": "claim_requires_action_proof",
        "claimed": "claim_requires_action_proof",
        "assumption": "assumption_or_rationale_gap",
        "rationale": "assumption_or_rationale_gap",
    }
    for term, predicate in inferred.items():
        if term in lowered and all(item["predicate"] != predicate for item in rows):
            rows.append({"predicate": predicate, "source": "query_terms", "matched_term": term})
    return rows


def order_decision_routes(routes: list[str]) -> list[str]:
    preferred = [
        "conversation_reconstruction",
        "git_history_reconstruction",
        "task_review_join",
        "tool_action_proof",
        "source_ref_followup",
        "no_evidence_scoped_report",
    ]
    route_set = set(routes)
    return [route for route in preferred if route in route_set]


def checkpoint_plan(route_order: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "checkpoint_id": f"route-{index:02d}",
            "route": route,
            "complete_when": checkpoint_completion(route),
        }
        for index, route in enumerate(route_order, 1)
    ]


def checkpoint_completion(route: str) -> str:
    return {
        "conversation_reconstruction": "candidate source refs have been opened with read-more and evidence vs inference is separated",
        "git_history_reconstruction": "git commit/branch/path/time commands have been checked without treating git state as user intent alone",
        "task_review_join": "task and review artifacts have been read for verdicts, blockers, and handoff claims",
        "tool_action_proof": "structured tool events have been confirmed from source refs before action claims",
        "source_ref_followup": "next_reads have been replayed for surrounding context as needed",
        "no_evidence_scoped_report": "searched, not-searched, missing, and cap-limited scope have been stated",
    }.get(route, "route evidence checked")


def resume_from(next_reads: list[dict[str, Any]], route_order: list[str]) -> dict[str, Any]:
    first_read = next((item for item in next_reads if item.get("target")), None)
    return {
        "route": route_order[0] if route_order else None,
        "next_read_target": first_read.get("target") if first_read else None,
        "instruction": "start with next_read_target, then follow route_order checkpoints",
    }


def retained_evidence_ledger(
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
) -> list[dict[str, Any]]:
    ledger: list[dict[str, Any]] = []
    for item in candidates[:8]:
        ledger.append(
            {
                "kind": "candidate",
                "id": item.get("candidate_id"),
                "source_ref": item.get("source_ref"),
                "retained_fields": ["candidate_id", "source_ref", "timestamp_utc", "role", "confidence", "reason_codes"],
                "omitted_fields": ["raw_private_text"],
            }
        )
    for event in tool_proof.get("events", [])[:6]:
        ledger.append(
            {
                "kind": "tool_event",
                "id": event.get("event_id"),
                "source_ref": event.get("source_ref"),
                "retained_fields": ["event_id", "event_type", "tool_name", "source_ref", "timestamp_utc", "confidence"],
                "omitted_fields": ["raw_tool_payload"],
            }
        )
    for item in joins.get("review_evidence", [])[:6]:
        ledger.append(
            {
                "kind": "task_review_artifact",
                "id": item.get("path"),
                "path": item.get("path"),
                "retained_fields": ["path", "exists", "review_verdict", "claim_action"],
                "omitted_fields": ["artifact_body"],
            }
        )
    return ledger[:20]


def omitted_or_dropped_scope(source_scope: dict[str, Any], no_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    not_searched = source_scope.get("not_searched_scope") or {}
    excluded = source_scope.get("excluded_scope") or {}
    cap_hit = source_scope.get("cap_hit") or {}
    if not_searched:
        rows.append({"kind": "not_searched_scope", "details": not_searched})
    if excluded:
        rows.append({"kind": "excluded_scope", "details": excluded})
    if cap_hit.get("hit"):
        rows.append({"kind": "cap_limited_scope", "details": cap_hit})
    if no_evidence.get("needed"):
        rows.append({"kind": "searched_scope_no_evidence", "details": no_evidence.get("searched_scope")})
    return rows


def cap_pressure_notes(source_scope: dict[str, Any]) -> list[dict[str, Any]]:
    cap_hit = source_scope.get("cap_hit") or {}
    if not cap_hit.get("hit"):
        return [{"status": "no_cap_pressure_reported", "caps_hit": []}]
    return [
        {
            "status": "cap_pressure_reported",
            "caps_hit": list(cap_hit.get("caps_hit", [])),
            "meaning": "recall may be limited to searched scope; do not claim all-history completeness",
        }
    ]


def build_next_commands(
    next_reads: list[dict[str, Any]],
    *,
    hints: dict[str, str | None],
    include_semantic_debug_replay: bool,
) -> list[dict[str, Any]]:
    _ = include_semantic_debug_replay
    commands: list[dict[str, Any]] = []
    for read in next_reads[:8]:
        actions = read.get("expansion_actions") or []
        for action in actions[:1]:
            argv = action.get("argv")
            if argv:
                commands.append(
                    {
                        "command_family": "read_more",
                        "argv": argv,
                        "source": "next_reads.expansion_actions",
                        "raw_private_text_included": False,
                    }
                )
                break
        if not actions and read.get("target"):
            commands.append(
                {
                    "command_family": "read_more",
                    "argv": ["jsonl-history-search", "read-more", read["target"], "--format", "json"],
                    "source": "next_reads.target",
                    "raw_private_text_included": False,
                }
            )
    if hints.get("commit"):
        commands.append(
            {
                "command_family": "git_show",
                "argv": ["git", "show", "--stat", str(hints["commit"])],
                "source": "commit_hint",
                "raw_private_text_included": False,
            }
        )
    if hints.get("path"):
        commands.append(
            {
                "command_family": "git_log_path",
                "argv": ["git", "log", "--oneline", "--", str(hints["path"])],
                "source": "path_hint",
                "raw_private_text_included": False,
            }
        )
    return dedupe_dicts(commands)[:16]


def compact_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for item in candidates[:INLINE_CANDIDATE_LIMIT]:
        compacted.append(
            {
                "candidate_id": item.get("candidate_id"),
                "rank": item.get("rank"),
                "source_ref": item.get("source_ref"),
                "timestamp_utc": item.get("timestamp_utc"),
                "role": item.get("role"),
                "confidence": item.get("confidence"),
                "score": item.get("score"),
                "stages": item.get("stages", []),
                "reason_codes": item.get("reason_codes", []),
                "artifacts": item.get("artifacts", [])[:8],
                "tasks": item.get("tasks", [])[:8],
                "snippet": item.get("snippet", ""),
            }
        )
    return compacted


def compact_handoff_question_family(family: dict[str, Any]) -> dict[str, Any]:
    return {
        "primary": family.get("primary"),
        "detected_supported": list(family.get("detected_supported", [])),
        "detected_unsupported": list(family.get("detected_unsupported", [])),
    }


def compact_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in timeline[:INLINE_TIMELINE_LIMIT]:
        rows.append(
            {
                "candidate_id": item.get("candidate_id"),
                "timestamp_utc": item.get("timestamp_utc"),
                "source_ref": item.get("source_ref"),
                "role": item.get("role"),
                "confidence": item.get("confidence"),
                "phase_hint": phase_hint(item.get("snippet", "")),
                "snippet": item.get("snippet", ""),
            }
        )
    return rows


def compact_tool_proof(tool_report: dict[str, Any]) -> dict[str, Any]:
    events = []
    for event in tool_report.get("events", [])[:INLINE_EVENT_LIMIT]:
        events.append(
            {
                "event_id": event.get("event_id"),
                "event_type": event.get("event_type"),
                "event_family": event.get("event_family"),
                "action": event.get("action"),
                "tool_name": event.get("tool_name"),
                "command": event.get("command"),
                "source_ref": event.get("source_ref"),
                "timestamp_utc": event.get("timestamp_utc"),
                "confidence": event.get("confidence"),
                "targets": event.get("targets", []),
                "output_paths": event.get("output_paths", []),
                "evidence_basis": event.get("evidence_basis", []),
                "uncertainty": event.get("uncertainty", []),
            }
        )
    return {
        "status": tool_report.get("status"),
        "events": events,
        "event_count": tool_report.get("token_efficiency", {}).get("event_count", len(events)),
        "excluded_mention_count": tool_report.get("token_efficiency", {}).get("excluded_mention_count", 0),
        "proof_boundary": "structured events support action/tool proof; prose-only mentions remain excluded or low-confidence leads",
        "context_budget": tool_report.get("context_budget", {}),
    }


def compact_joins(forensics_payload: dict[str, Any] | None, hints: dict[str, str | None]) -> dict[str, Any]:
    if not forensics_payload:
        return {
            "status": "not_run",
            "hints": hints,
            "commit": None,
            "branch": None,
            "path_last_change": None,
            "artifact_checks": [],
            "review_evidence": [],
            "missing": ["forensics_not_run_no_commit_path_task_branch_or_around_hint"],
        }
    git = forensics_payload.get("git", {})
    return {
        "status": "joined" if any((git.get("commit"), git.get("branch"), git.get("path_last_change"), forensics_payload.get("review_evidence"))) else "no_join_evidence",
        "hints": forensics_payload.get("forensics", {}).get("hints", hints),
        "confidence": forensics_payload.get("forensics", {}).get("confidence"),
        "confidence_reasons": forensics_payload.get("forensics", {}).get("confidence_reasons", []),
        "commit": git.get("commit"),
        "branch": git.get("branch"),
        "path_last_change": git.get("path_last_change"),
        "artifact_checks": compact_artifact_checks(forensics_payload.get("artifact_checks", [])),
        "review_evidence": compact_review_evidence(forensics_payload.get("review_evidence", [])),
        "missing": missing_join_labels(forensics_payload),
    }


def compact_forensics_workflow(forensics_payload: dict[str, Any] | None, joins_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not forensics_payload:
        return {
            "status": "not_run",
            "phase_timeline": [],
            "claim_action_review_joins": [],
            "review_gate": {
                "status": "not_run",
                "verdict": None,
                "gate_source_refs": [],
                "blockers": [],
                "merge_commit": None,
                "confidence": "low",
                "uncertainty": ["forensics_not_run_no_commit_path_task_branch_or_around_hint"],
            },
            "diagnostics": {
                "phase_counts": {},
                "join_count": 0,
                "missing_evidence_flags": ["forensics_not_run"],
            },
            "summary": {
                "phase_count": 0,
                "join_count": 0,
                "review_gate_status": "not_run",
                "review_gate_verdict": None,
                "missing_evidence_flags": ["forensics_not_run"],
                "join_basis_counts": {},
                "provenance": {},
            },
        }
    workflow = forensics_payload.get("forensics", {})
    phase_timeline = workflow.get("phase_timeline", [])
    joins = workflow.get("claim_action_review_joins", [])
    review_gate = workflow.get("review_gate") or {}
    diagnostics = workflow.get("diagnostics") or {}
    provenance_lifecycle = workflow.get("provenance_lifecycle") or {}
    provenance_summary = provenance_lifecycle.get("summary") if isinstance(provenance_lifecycle, dict) else {}
    if not phase_timeline and joins_payload and joins_payload.get("review_evidence"):
        phase_timeline = phase_timeline_from_compact_review_evidence(joins_payload.get("review_evidence", []))
        review_gate = review_gate_from_compact_review_evidence(joins_payload.get("review_evidence", []))
        diagnostics = {
            "phase_counts": {"review": len(phase_timeline)},
            "join_count": 0,
            "join_relation_counts": {},
            "join_coverage": {"phase_count": len(phase_timeline), "joined_phase_count": 0},
            "review_gate_status_counts": {review_gate.get("status") or "unknown": 1},
            "missing_evidence_flags": ["git_or_jsonl_phase_evidence_missing"] if phase_timeline else ["phase_evidence_missing"],
            "inferred_only_count": 0,
            "indirect_join_count": 0,
            "unsupported_claim_count": 0,
        }
    return {
        "status": "joined" if phase_timeline else "no_phase_evidence",
        "phase_timeline": phase_timeline,
        "claim_action_review_joins": joins,
        "review_gate": review_gate,
        "diagnostics": diagnostics,
        "provenance_lifecycle": provenance_lifecycle,
        "summary": {
            "phase_count": len(phase_timeline),
            "join_count": len(joins),
            "review_gate_status": review_gate.get("status"),
            "review_gate_verdict": review_gate.get("verdict"),
            "missing_evidence_flags": diagnostics.get("missing_evidence_flags", []),
            "join_basis_counts": diagnostics.get("join_basis_counts", {}),
            "provenance": provenance_summary or {},
        },
    }


def phase_timeline_from_compact_review_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = []
    for index, item in enumerate(items[:12], 1):
        verdict = item.get("review_verdict") or {}
        action = verdict.get("action") or (item.get("claim_action") or {}).get("action") or "unsupported"
        phases.append(
            {
                "phase_id": f"phase-{index:03d}",
                "phase_kind": "review",
                "order_key": f"9999-12-31T23:59:59+00:00|semantic-debug-local-review|{index:04d}",
                "timestamp_utc": None,
                "source_kind": "review",
                "source_ref": {"path": item.get("path")},
                "evidence_kind": "review_verdict" if verdict else "claim",
                "claim_action": action,
                "review_verdict": verdict or None,
                "confidence": "high" if verdict else "medium",
                "uncertainty": [] if verdict else ["review_artifact_without_explicit_verdict"],
            }
        )
    return phases


def review_gate_from_compact_review_evidence(items: list[dict[str, Any]]) -> dict[str, Any]:
    actions = [
        (item.get("review_verdict") or {}).get("action")
        for item in items
        if isinstance(item.get("review_verdict"), dict)
    ]
    if "verified" in actions:
        status = "verified"
        verdict = "verified"
        confidence = "high"
    elif "merged" in actions:
        status = "merged"
        verdict = "merged"
        confidence = "high"
    elif any(action in {"failed", "blocked"} for action in actions):
        status = "blocked"
        verdict = next(action for action in actions if action in {"failed", "blocked"})
        confidence = "high"
    elif "partially_verified" in actions:
        status = "partial"
        verdict = "partially_verified"
        confidence = "medium"
    else:
        status = "unknown"
        verdict = None
        confidence = "low"
    return {
        "status": status,
        "verdict": verdict,
        "gate_source_refs": [{"path": item.get("path")} for item in items if item.get("path")],
        "blockers": [
            {"path": item.get("path"), "verdict": (item.get("review_verdict") or {}).get("action")}
            for item in items
            if (item.get("review_verdict") or {}).get("action") in {"failed", "blocked", "partially_verified"}
        ],
        "merge_commit": None,
        "confidence": confidence,
        "uncertainty": [] if verdict else ["review_verdict_not_found"],
    }


def merge_local_task_review_joins(joins: dict[str, Any], hints: dict[str, str | None], *, cwd: Path) -> dict[str, Any]:
    task = hints.get("task")
    if not task:
        return joins
    local_evidence = discover_local_task_review_evidence(task, cwd=cwd)
    if not local_evidence:
        return joins
    merged = dict(joins)
    merged["status"] = "joined"
    merged["review_evidence"] = dedupe_review_evidence(list(merged.get("review_evidence", [])) + local_evidence)
    merged["missing"] = [item for item in merged.get("missing", []) if item != "task_or_review_artifact_missing"]
    merged["local_task_review_join"] = {
        "status": "found",
        "cwd": str(cwd),
        "evidence_count": len(local_evidence),
    }
    return merged


def discover_local_task_review_evidence(task: str, *, cwd: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for directory in (cwd / "tasks", cwd / "reviews", cwd / "codex" / "state" / "reviews"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(f"*{task}*.md"))[:12]:
            item = {
                "path": relative_or_absolute(path, cwd),
                "exists": path.exists(),
                "review_verdict": None,
                "claim_action": None,
                "git_last_change": None,
                "warning": None,
            }
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                item["warning"] = f"Could not read local task/review artifact: {exc}"
                evidence.append(item)
                continue
            verdict = parse_review_verdict(content)
            item["review_verdict"] = verdict.to_dict() if verdict is not None else None
            item["claim_action"] = classify_claim_action(content).to_dict()
            evidence.append(item)
    return evidence


def dedupe_review_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = item.get("path")
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:12]


def compact_artifact_checks(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.get("path"),
            "exists": item.get("exists"),
            "is_file": item.get("is_file"),
            "git_tracked": item.get("git_tracked"),
            "git_last_change": item.get("git_last_change"),
            "reason": item.get("reason"),
        }
        for item in items[:12]
    ]


def compact_review_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": item.get("path"),
            "exists": item.get("exists"),
            "review_verdict": item.get("review_verdict"),
            "claim_action": item.get("claim_action"),
            "git_last_change": item.get("git_last_change"),
            "warning": item.get("warning"),
        }
        for item in items[:12]
    ]


def missing_join_labels(forensics_payload: dict[str, Any]) -> list[str]:
    missing = []
    git = forensics_payload.get("git", {})
    if not git.get("commit"):
        missing.append("commit_metadata_missing")
    if not git.get("branch"):
        missing.append("branch_metadata_missing")
    if not git.get("path_last_change"):
        missing.append("path_last_change_missing")
    if not forensics_payload.get("review_evidence"):
        missing.append("task_or_review_artifact_missing")
    if not forensics_payload.get("artifact_checks"):
        missing.append("artifact_checks_missing")
    return missing


def build_no_evidence_section(
    agent_find: dict[str, Any],
    source_scope: dict[str, Any],
    tool_report: dict[str, Any],
    forensics_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    needed = agent_find.get("status") == "no_evidence"
    return {
        "needed": needed,
        "answer_scope": "not_found_in_searched_scope" if needed else "candidates_found",
        "candidate_rows_found": agent_find.get("token_efficiency", {}).get("candidate_count", 0),
        "structured_events_found": tool_report.get("token_efficiency", {}).get("event_count", 0),
        "join_evidence_found": bool(forensics_payload and (forensics_payload.get("review_evidence") or forensics_payload.get("artifact_checks"))),
        "searched_scope": source_scope.get("searched_scope"),
        "excluded_scope": source_scope.get("excluded_scope"),
        "missing_scope": source_scope.get("not_searched_scope", {}).get("missing_roots", []),
        "not_searched_scope": source_scope.get("not_searched_scope"),
        "why_not_confident": (
            "No matching candidate rows survived the searched scope; this does not prove absence outside selected roots or beyond caps."
            if needed
            else "Candidate rows exist; inspect confidence, source refs, and joins before causal interpretation."
        ),
    }


def build_uncertainty(
    agent_find: dict[str, Any],
    source_scope: dict[str, Any],
    tool_report: dict[str, Any],
    forensics_payload: dict[str, Any] | None,
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    labels = []
    if agent_find.get("status") == "no_evidence":
        labels.append(("no_candidate_rows", "No candidate rows matched within the searched scope."))
    if source_scope.get("not_searched_scope", {}).get("missing_root_count", 0):
        labels.append(("missing_selected_roots", "At least one selected root was missing and not opened."))
    if source_scope.get("cap_hit", {}).get("hit"):
        labels.append(("cap_limited_scope", "At least one file, byte, match, or elapsed cap may have limited recall."))
    if tool_report.get("status") == "no_structured_events":
        labels.append(("structured_action_proof_missing", "No structured tool/action events were detected in the searched scope."))
    if not forensics_payload:
        labels.append(("git_task_review_join_not_run", "No commit/path/task/branch/around hint was provided, so forensics joins were not run."))
    elif forensics_payload.get("forensics", {}).get("confidence") == "low":
        labels.append(("low_forensics_join_confidence", "Forensics found fewer than two independent join signals."))
    for unsupported in family.get("detected_unsupported", []):
        labels.append((f"unsupported_{unsupported}", UNSUPPORTED_QUESTION_FAMILIES.get(unsupported, unsupported)))
    labels.append(("root_cause_not_claimed", "Candidate causes are interpretation leads, not automatic root-cause proof."))
    return [{"label": label, "meaning": meaning} for label, meaning in labels]


def build_candidate_causes(
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    basis_refs = []
    for item in (timeline or candidates)[:5]:
        if item.get("source_ref"):
            basis_refs.append(item["source_ref"])
    if tool_proof.get("events"):
        basis_refs.extend(event.get("source_ref") for event in tool_proof["events"][:3] if event.get("source_ref"))
    if joins.get("review_evidence"):
        basis_refs.extend({"path": item.get("path")} for item in joins["review_evidence"][:3] if item.get("path"))
    return [
        {
            "label": "candidate_cause",
            "allowed_language": "likely_contributing_assumption",
            "basis": basis_refs[:8],
            "claim_strength": "hypothesis_from_correlated_evidence",
            "root_cause_claimed": False,
        }
    ]


def build_next_reads(
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    hints: dict[str, str | None],
    *,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, str]]:
    reads: list[dict[str, Any]] = []
    for candidate in candidates[:5]:
        ref = candidate.get("source_ref") or {}
        target = source_ref_target(ref)
        if target:
            read = {
                "kind": "jsonl_source_ref",
                "target": target,
                "source_ref": dict(ref),
                "reason": "confirm candidate snippet in source context",
                "expansion_actions": read_more_actions_for_source_ref(
                    ref,
                    expansion_action_context=expansion_action_context,
                ),
            }
            if include_context_actions:
                read["context_actions"] = context_actions_for_source_ref(
                    ref,
                    expansion_action_context=expansion_action_context,
                )
            reads.append(read)
    for event in tool_proof.get("events", [])[:4]:
        ref = event.get("source_ref") or {}
        target = source_ref_target(ref)
        if target:
            read = {
                "kind": "tool_event_source_ref",
                "target": target,
                "source_ref": dict(ref),
                "reason": "confirm structured action proof",
                "expansion_actions": read_more_actions_for_source_ref(
                    ref,
                    expansion_action_context=expansion_action_context,
                ),
            }
            if include_context_actions:
                read["context_actions"] = context_actions_for_source_ref(
                    ref,
                    expansion_action_context=expansion_action_context,
                )
            reads.append(read)
    for item in joins.get("review_evidence", [])[:4]:
        if item.get("path"):
            reads.append({"kind": "task_or_review_artifact", "target": item["path"], "reason": "confirm task/review verdict and claim-action classification"})
    hint_path = hints.get("path")
    for item in joins.get("artifact_checks", [])[:4]:
        if item.get("path") and (not hint_path or item.get("path") == hint_path):
            reads.append({"kind": "artifact", "target": item["path"], "reason": "confirm local artifact state"})
    if hints.get("path") and not any(read.get("target") == hints["path"] for read in reads):
        reads.append({"kind": "hint_path", "target": hints["path"] or "", "reason": "caller supplied path hint"})
    return dedupe_dicts(reads)[:12]


def build_final_answer_source_use_contract(
    *,
    status: str,
    candidates: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    uncertainty: list[dict[str, str]],
    hints: dict[str, str | None],
    source_scope: dict[str, Any],
) -> dict[str, Any]:
    candidate_refs = [
        candidate.get("source_ref")
        for candidate in candidates[:5]
        if isinstance(candidate.get("source_ref"), dict) and candidate.get("source_ref", {}).get("path")
    ]
    next_reads = list(source_locator_handoff.get("next_reads") or [])[:8]
    hint_obligations = [
        {"hint": key, "value": value, "requirement": "address this supplied handle in the final answer or say it was not resolved"}
        for key, value in hints.items()
        if value
    ]
    required_actions = [
        "Use source refs from strongest_candidates and source_locator_handoff.next_reads before answering when they exist.",
        "State which evidence supports the answer and which parts remain inference.",
        "Preserve uncertainty labels instead of smoothing them into a stronger claim.",
        "Do not claim root cause, exhaustive all-history absence, JSONL superiority, speed, value, or token-efficiency unless separate reviewed evidence supports it.",
    ]
    evidence_inputs = {
        "strongest_candidate_source_refs": candidate_refs,
        "source_locator_next_reads": next_reads,
        "structured_tool_event_count": tool_proof.get("event_count", 0),
        "task_review_join_status": joins.get("status"),
        "uncertainty_labels": [item.get("label") for item in uncertainty],
    }
    if status == "no_evidence":
        required_actions.insert(0, "Answer with `not_found_in_searched_scope`, not global absence.")
        required_actions.append("Name not-searched, missing, or cap-limited scope before concluding no evidence.")
        answer_scope = "not_found_in_searched_scope"
        source_use_required = False
    else:
        required_actions.insert(0, "Use or cite at least one source ref before making a factual or provenance claim.")
        if tool_proof.get("events"):
            required_actions.append("Use structured tool/action proof for claims about what an agent actually ran, edited, tested, or reviewed.")
        if joins.get("status") == "joined":
            required_actions.append("Use task/review/commit joins for provenance claims before relying on transcript snippets alone.")
        answer_scope = "source_backed_answer_required"
        source_use_required = bool(candidate_refs or next_reads or tool_proof.get("events") or joins.get("status") == "joined")
    return {
        "status": "active",
        "answer_scope": answer_scope,
        "source_use_required": source_use_required,
        "required_actions": required_actions,
        "hint_obligations": hint_obligations,
        "evidence_inputs": evidence_inputs,
        "no_evidence_scope": {
            "answer_scope": no_evidence.get("answer_scope"),
            "searched_scope": no_evidence.get("searched_scope"),
            "not_searched_scope": no_evidence.get("not_searched_scope"),
            "cap_hit": source_scope.get("cap_hit"),
        },
        "claim_boundaries": {
            "root_cause_claim": "not_claimed",
            "global_absence_claim": "not_allowed",
            "jsonl_value_speed_or_token_efficiency_claim": "not_allowed_without_separate_reviewed_evidence",
            "evidence_vs_inference_required": True,
        },
    }


def build_guided_audit_flow(
    *,
    status: str,
    family: dict[str, Any],
    source_scope: dict[str, Any],
    decision_context: dict[str, Any],
    audit_plan: dict[str, Any],
    forensics_workflow: dict[str, Any],
    source_locator_handoff: dict[str, Any],
    final_answer_contract: dict[str, Any],
    no_evidence: dict[str, Any],
    next_reads: list[dict[str, Any]],
    next_commands: list[dict[str, Any]],
) -> dict[str, Any]:
    query_plan = source_locator_handoff.get("query_plan") or {}
    primary_intent = query_plan.get("primary_intent") or "generic_history"
    primary_family = guided_primary_question_family(
        family=family,
        primary_intent=primary_intent,
        no_evidence=no_evidence,
    )
    route_order = guided_route_order(
        primary_family=primary_family,
        primary_intent=primary_intent,
        routes=audit_plan.get("route_order", []),
    )
    recovery = guided_recovery(source_locator_handoff)
    guide_status = guided_status(
        command_status=status,
        next_reads=next_reads,
        recovery=recovery,
        no_evidence=no_evidence,
    )
    answer_gate = guided_answer_gate(
        primary_family=primary_family,
        primary_intent=primary_intent,
        final_answer_contract=final_answer_contract,
        no_evidence=no_evidence,
        recovery=recovery,
    )
    return {
        "schema_version": 1,
        "primary_question_family": primary_family,
        "primary_intent": primary_intent,
        "status": guide_status,
        "route_order": route_order,
        "stages": guided_stages(
            route_order=route_order,
            decision_context=decision_context,
            audit_plan=audit_plan,
            forensics_workflow=forensics_workflow,
            source_locator_handoff=source_locator_handoff,
            no_evidence=no_evidence,
            recovery=recovery,
        ),
        "scope": guided_scope(source_scope, no_evidence),
        "evidence_handles": guided_evidence_handles(decision_context.get("evidence_handles", [])),
        "recovery": recovery,
        "answer_gate": answer_gate,
        "next_reads": guided_next_reads(next_reads),
        "next_commands": guided_next_commands(next_commands),
        "links": {
            "decision_context": "semantic_debug.decision_context",
            "audit_plan": "semantic_debug.audit_plan",
            "forensics_workflow": "semantic_debug.forensics_workflow",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
            "final_answer_source_use_contract": "semantic_debug.final_answer_source_use_contract",
            "no_evidence": "semantic_debug.no_evidence",
            "omitted_window_recovery": "semantic_debug.source_locator_handoff.omitted_window_recovery",
        },
        "privacy_boundaries": {
            "raw_transcript_text_included": False,
            "snippets_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "raw_source_lines_included": False,
            "private_payloads_included": False,
        },
    }


def guided_primary_question_family(
    *,
    family: dict[str, Any],
    primary_intent: str,
    no_evidence: dict[str, Any],
) -> str:
    if no_evidence.get("needed"):
        return "no_evidence"
    if primary_intent == "workflow_failure":
        return "workflow_failure"
    primary = family.get("primary")
    if primary in {"why_did_this_happen", "claimed_action", "remembered_instruction"}:
        return str(primary)
    detected = set(family.get("detected_supported") or [])
    for label in ("claimed_action", "remembered_instruction", "why_did_this_happen"):
        if label in detected:
            return label
    return str(primary or "why_did_this_happen")


def guided_route_order(*, primary_family: str, primary_intent: str, routes: list[str]) -> list[str]:
    route_set = set(routes)
    if primary_family == "no_evidence":
        preferred = ["no_evidence_scoped_report", "source_ref_followup", "conversation_reconstruction"]
    elif primary_family == "workflow_failure" or primary_intent == "workflow_failure":
        preferred = ["conversation_reconstruction", "tool_action_proof", "task_review_join", "source_ref_followup", "no_evidence_scoped_report"]
    elif primary_family == "claimed_action":
        preferred = ["tool_action_proof", "task_review_join", "conversation_reconstruction", "source_ref_followup", "no_evidence_scoped_report"]
    elif primary_family == "remembered_instruction":
        preferred = ["conversation_reconstruction", "source_ref_followup", "no_evidence_scoped_report"]
    else:
        preferred = ["conversation_reconstruction", "git_history_reconstruction", "task_review_join", "tool_action_proof", "source_ref_followup", "no_evidence_scoped_report"]
    ordered = [route for route in preferred if route in route_set]
    ordered.extend(route for route in routes if route not in ordered)
    return ordered


def guided_status(
    *,
    command_status: str,
    next_reads: list[dict[str, Any]],
    recovery: dict[str, Any],
    no_evidence: dict[str, Any],
) -> str:
    if no_evidence.get("needed"):
        return "scoped_no_evidence_ready"
    if recovery.get("status") == "available":
        return "evidence_available_with_recovery_reads"
    if command_status == "no_evidence":
        return "scoped_no_evidence_ready"
    if next_reads:
        return "evidence_available_needs_source_reads"
    return "needs_followup_evidence"


def guided_scope(source_scope: dict[str, Any], no_evidence: dict[str, Any]) -> dict[str, Any]:
    searched = source_scope.get("searched_scope") or {}
    not_searched = source_scope.get("not_searched_scope") or {}
    excluded = source_scope.get("excluded_scope") or {}
    cap_hit = source_scope.get("cap_hit") or {}
    source_plan = not_searched.get("source_plan") or {}
    return {
        "searched_scope": {
            "searched_files": searched.get("searched_files", 0),
            "searched_bytes": searched.get("searched_bytes", 0),
            "source_plan_selected_files": searched.get("source_plan_selected_files", 0),
            "source_plan_deferred_files": searched.get("source_plan_deferred_files", 0),
            "source_plan_pruned_files": searched.get("source_plan_pruned_files", 0),
            "result_meaning": searched.get("result_meaning"),
        },
        "not_searched_scope": {
            "missing_root_count": not_searched.get("missing_root_count", 0),
            "outside_selected_roots": not_searched.get("outside_selected_roots"),
            "source_plan": {
                "sources_total": source_plan.get("sources_total"),
                "sources_selected": source_plan.get("sources_selected"),
                "sources_deferred_budget": source_plan.get("sources_deferred_budget"),
                "sources_pruned_hard": source_plan.get("sources_pruned_hard"),
                "exhaustive_available": source_plan.get("exhaustive_available"),
                "raw_paths_included": source_plan.get("raw_paths_included", False),
                "raw_text_included": source_plan.get("raw_text_included", False),
            },
            "notes": list(not_searched.get("notes") or [])[:5],
        },
        "excluded_scope": {
            "rejected_root_count": excluded.get("rejected_root_count", 0),
            "meaning": excluded.get("meaning"),
        },
        "cap_hit": {
            "hit": cap_hit.get("hit", False),
            "caps_hit": list(cap_hit.get("caps_hit") or [])[:8],
        },
        "answer_scope": no_evidence.get("answer_scope"),
        "not_all_history_absence": True,
    }


def guided_evidence_handles(handles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for handle in handles[:12]:
        row = {key: value for key, value in handle.items() if key != "source_ref"}
        if isinstance(handle.get("source_ref"), dict):
            row["source_ref"] = guided_source_ref(handle["source_ref"])
        compact.append(row)
    return compact


def guided_source_ref(source_ref: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source_ref.get(key)
        for key in ("path", "line", "timestamp_utc")
        if source_ref.get(key) is not None
    }


def guided_recovery(source_locator_handoff: dict[str, Any]) -> dict[str, Any]:
    omitted = source_locator_handoff.get("omitted_window_recovery") or {}
    status = omitted.get("status") or "not_available"
    items = []
    for item in list(omitted.get("items") or [])[:5]:
        row = {
            "candidate_id": item.get("candidate_id"),
            "target": item.get("target"),
            "omission_reason": item.get("omission_reason"),
            "read_more_actionable": item.get("read_more_actionable"),
            "intent_match": item.get("intent_match"),
        }
        if isinstance(item.get("source_ref"), dict):
            row["source_ref"] = guided_source_ref(item["source_ref"])
        items.append({key: value for key, value in row.items() if value is not None})
    return {
        "status": status,
        "source": "source_locator_handoff.omitted_window_recovery",
        "recovery_count": omitted.get("recovery_count", 0),
        "promoted_next_read_count": omitted.get("promoted_next_read_count", 0),
        "items": items,
        "action": (
            "open promoted omitted-window next_reads before treating the evidence set as representative"
            if status == "available"
            else "no omitted-window recovery handles were emitted for this packet"
        ),
    }


def guided_stages(
    *,
    route_order: list[str],
    decision_context: dict[str, Any],
    audit_plan: dict[str, Any],
    forensics_workflow: dict[str, Any],
    source_locator_handoff: dict[str, Any],
    no_evidence: dict[str, Any],
    recovery: dict[str, Any],
) -> list[dict[str, Any]]:
    checkpoint_by_route = {
        item.get("route"): item
        for item in audit_plan.get("checkpoint_plan", [])
        if isinstance(item, dict) and item.get("route")
    }
    stages = []
    for index, route in enumerate(route_order, 1):
        checkpoint = checkpoint_by_route.get(route) or {}
        stages.append(
            {
                "stage_id": f"guided-stage-{index:02d}",
                "route": route,
                "status": guided_stage_status(
                    route=route,
                    decision_context=decision_context,
                    forensics_workflow=forensics_workflow,
                    source_locator_handoff=source_locator_handoff,
                    no_evidence=no_evidence,
                    recovery=recovery,
                ),
                "inputs": guided_stage_inputs(route),
                "outputs": guided_stage_outputs(route),
                "exit_criteria": checkpoint.get("complete_when") or checkpoint_completion(route),
            }
        )
    return stages


def guided_stage_status(
    *,
    route: str,
    decision_context: dict[str, Any],
    forensics_workflow: dict[str, Any],
    source_locator_handoff: dict[str, Any],
    no_evidence: dict[str, Any],
    recovery: dict[str, Any],
) -> str:
    if route == "conversation_reconstruction":
        return "ready" if decision_context.get("evidence_handles") else "needs_evidence"
    if route == "git_history_reconstruction":
        workflow_summary = forensics_workflow.get("summary") or {}
        return "available" if workflow_summary.get("phase_count", 0) else "not_run_or_no_hints"
    if route == "task_review_join":
        workflow_summary = forensics_workflow.get("summary") or {}
        return "available" if workflow_summary.get("join_count", 0) else "not_run_or_no_join"
    if route == "tool_action_proof":
        return "available" if any(handle.get("kind") == "tool_event_source_ref" for handle in decision_context.get("evidence_handles", [])) else "needs_structured_event_source"
    if route == "source_ref_followup":
        if recovery.get("status") == "available":
            return "recovery_reads_available"
        return "ready" if source_locator_handoff.get("next_reads") else "needs_source_ref"
    if route == "no_evidence_scoped_report":
        return "available" if no_evidence.get("needed") else "guardrail_only"
    return "ready"


def guided_stage_inputs(route: str) -> list[str]:
    return {
        "conversation_reconstruction": ["strongest_candidates", "source_locator_handoff.source_windows", "next_reads"],
        "git_history_reconstruction": ["task_review_commit_joins", "forensics_workflow"],
        "task_review_join": ["task_review_commit_joins", "forensics_workflow.review_gate"],
        "tool_action_proof": ["tool_action_proof", "structured_tool_events"],
        "source_ref_followup": ["source_locator_handoff.next_reads", "source_locator_handoff.omitted_window_recovery"],
        "no_evidence_scoped_report": ["no_evidence", "scope", "final_answer_source_use_contract.no_evidence_scope"],
    }.get(route, ["semantic_debug"])


def guided_stage_outputs(route: str) -> list[str]:
    return {
        "conversation_reconstruction": ["evidence_vs_inference_notes", "candidate_timeline"],
        "git_history_reconstruction": ["project_time_context", "artifact_state_context"],
        "task_review_join": ["review_verdict_context", "task_handoff_context"],
        "tool_action_proof": ["action_claim_support_or_gap"],
        "source_ref_followup": ["confirmed_context_or_recovery_gap"],
        "no_evidence_scoped_report": ["searched_scope_only_no_evidence_statement"],
    }.get(route, ["checked_route"])


def guided_answer_gate(
    *,
    primary_family: str,
    primary_intent: str,
    final_answer_contract: dict[str, Any],
    no_evidence: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    required_actions = list(final_answer_contract.get("required_actions") or [])[:8]
    family_gate = {
        "why_did_this_happen": "Separate source-backed observations from candidate-cause inference before saying why.",
        "workflow_failure": "Use failure-timeline/process evidence from the query-plan route before naming where the workflow went wrong.",
        "claimed_action": "Confirm structured tool/action proof before saying an agent ran, edited, tested, reviewed, or claimed an action.",
        "remembered_instruction": "Open source refs for the prior user wording before restating remembered instructions.",
        "no_evidence": "State not_found_in_searched_scope and list unsearched, missing, or cap-limited scope.",
    }.get(primary_family, "Use source refs before making factual or provenance claims.")
    completion_criteria = [
        family_gate,
        "At least one source ref or reviewed artifact supports every factual/provenance claim when evidence exists.",
        "Uncertainty labels remain visible in the final answer.",
    ]
    if recovery.get("status") == "available":
        completion_criteria.append("Promoted omitted-window recovery reads have been opened or explicitly deferred.")
    if no_evidence.get("needed"):
        completion_criteria.append("The answer avoids global all-history absence language.")
    return {
        "status": "active",
        "primary_question_family": primary_family,
        "primary_intent": primary_intent,
        "answer_scope": final_answer_contract.get("answer_scope"),
        "source_use_required": final_answer_contract.get("source_use_required"),
        "required_actions": required_actions,
        "completion_criteria": completion_criteria,
        "forbidden_claims": [
            "root_cause_without_linked_reviewed_evidence",
            "global_absence_or_all_history_completeness",
            "jsonl_value_speed_token_or_final_answer_quality_without_separate_reviewed_evidence",
        ],
        "claim_boundaries": final_answer_contract.get("claim_boundaries", {}),
    }


def guided_next_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for read in next_reads[:8]:
        row = {
            "kind": read.get("kind"),
            "target": read.get("target"),
            "reason": read.get("reason"),
            "omitted_window_recovery": bool(read.get("omitted_window_recovery")),
        }
        if isinstance(read.get("source_ref"), dict):
            row["source_ref"] = guided_source_ref(read["source_ref"])
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def guided_next_commands(next_commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for command in next_commands[:8]:
        argv = list(command.get("argv") or [])
        row = {
            "command_family": command.get("command_family"),
            "source": command.get("source"),
            "target": argv[1] if len(argv) > 1 else None,
            "mode": command_mode_from_argv(argv),
            "raw_private_text_included": command.get("raw_private_text_included", False),
            "argv_omitted_from_guide": True,
            "omission_reason": "guided_audit_flow avoids raw cwd/root argv; use top-level next_reads expansion_actions for executable commands",
        }
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def command_mode_from_argv(argv: list[Any]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--mode" and index + 1 < len(argv):
            return str(argv[index + 1])
    return None


def summarize_guided_audit_flow(guide: dict[str, Any]) -> dict[str, Any]:
    recovery = guide.get("recovery") or {}
    answer_gate = guide.get("answer_gate") or {}
    return {
        "schema_version": guide.get("schema_version", 1),
        "primary_question_family": guide.get("primary_question_family"),
        "primary_intent": guide.get("primary_intent"),
        "status": guide.get("status"),
        "route_order": list(guide.get("route_order") or [])[:8],
        "stage_count": len(guide.get("stages") or []),
        "evidence_handle_count": len(guide.get("evidence_handles") or []),
        "recovery": {
            "status": recovery.get("status"),
            "recovery_count": recovery.get("recovery_count", 0),
            "promoted_next_read_count": recovery.get("promoted_next_read_count", 0),
        },
        "answer_gate": {
            "answer_scope": answer_gate.get("answer_scope"),
            "source_use_required": answer_gate.get("source_use_required"),
            "forbidden_claims": list(answer_gate.get("forbidden_claims") or [])[:3],
        },
        "next_read_targets": [item.get("target") for item in list(guide.get("next_reads") or [])[:5] if item.get("target")],
        "privacy_boundaries": guide.get("privacy_boundaries"),
    }


def build_tradeoff_counters(
    agent_find: dict[str, Any],
    tool_report: dict[str, Any],
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    tool_proof: dict[str, Any],
) -> dict[str, Any]:
    packet_basis = {
        "strongest_candidates": candidates,
        "timeline": timeline,
        "tool_action_proof": tool_proof,
    }
    packet_bytes = len(json.dumps(packet_basis, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    find_budget = agent_find.get("context_budget", {})
    tool_budget = tool_report.get("context_budget", {})
    raw_baseline_bytes = int(find_budget.get("raw_baseline_bytes", 0) or 0) + int(tool_budget.get("raw_baseline_bytes", 0) or 0)
    return {
        "packet_basis_bytes": packet_bytes,
        "packet_basis_estimated_tokens": estimate_tokens(packet_bytes),
        "raw_baseline_bytes": raw_baseline_bytes,
        "estimated_reduction_ratio": reduction_ratio(packet_bytes, raw_baseline_bytes),
        "find": {
            "candidate_count": agent_find.get("token_efficiency", {}).get("candidate_count", 0),
            "searched_files": agent_find.get("token_efficiency", {}).get("searched_files", 0),
            "searched_bytes": agent_find.get("token_efficiency", {}).get("searched_bytes", 0),
            "inline_context_bytes": agent_find.get("token_efficiency", {}).get("inline_context_bytes", 0),
        },
        "structured_tool_events": {
            "event_count": tool_report.get("token_efficiency", {}).get("event_count", 0),
            "searched_files": tool_report.get("token_efficiency", {}).get("searched_files", 0),
            "searched_bytes": tool_report.get("token_efficiency", {}).get("searched_bytes", 0),
            "inline_context_bytes": tool_report.get("token_efficiency", {}).get("inline_context_bytes", 0),
        },
    }


def build_manifest_evaluation(
    path: Path,
    *,
    source_plan: dict[str, Any],
    plan: Any,
    default_tz: Any,
    mask_context: Any,
    output_format: str,
    use_rg_prefilter: bool,
    evaluate_cases: bool,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "status": "manifest_missing",
            "manifest": str(path),
            "not_searched": True,
            "evaluation_kind": "not_run",
            "case_count": 0,
            "cases": [],
            "caveats": ["sanitized real-history manifest was not available; no private bridge was required or run"],
        }
    validation = validate_manifest_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid_manifest", "manifest": str(path), "evaluation_kind": "not_run", "case_count": 0, "cases": [], "errors": [str(exc)]}
    cases = payload.get("cases", []) if isinstance(payload, dict) else []
    family_counts = Counter(case.get("family", "unknown") for case in cases if isinstance(case, dict))
    no_evidence_count = sum(1 for case in cases if "no_evidence" in str(case.get("family", "")))
    thin_families = sorted(family for family, count in family_counts.items() if count < 3)
    reviewed_count = sum(1 for case in cases if isinstance(case, dict) and case.get("review_status") == "reviewed_real_history_gold")
    if not evaluate_cases:
        return {
            "status": "evaluation_deferred" if validation.ok else "invalid_manifest",
            "manifest": str(path),
            "evaluation_kind": "not_run",
            "reason": "sanitized manifest per-case evaluation is deferred by default; pass --evaluate-sanitized-manifest-gate to run it",
            "measurement_basis": "cheap sanitized manifest metadata only; no per-case retrieval, scanner loop, private bridge, snippets, raw paths, raw private query text, vectors, or indexes are run",
            "case_count": len(cases),
            "reviewed_count": reviewed_count,
            "reviewed_case_count": len(cases),
            "below_floor_case_count": len(cases) < 20,
            "old_target_floor": 20,
            "family_counts": dict(sorted(family_counts.items())),
            "thin_families": thin_families,
            "missing_families": ["no_evidence"] if no_evidence_count == 0 else [],
            "privacy_contract": "sanitized aggregate only; default path does not run manifest case queries, private bridge refs, raw private text, paths, session IDs, vectors, indexes, or persistent caches",
            "correctness_gate": "not_run",
            "correctness_evaluable_count": 0,
            "correctness_not_evaluable_count": 0,
            "correctness_pass_count": 0,
            "correctness_fail_count": 0,
            "correctness_status_counts": {},
            "command_status_counts": {},
            "elapsed_ms": {
                "total": 0,
                "median": 0,
                "p90": 0,
                "evaluation_total": 0,
            },
            "output_context_bytes": {
                "total": 0,
                "median": 0,
                "p90": 0,
            },
            "estimated_output_tokens": {
                "total": 0,
                "median": 0,
                "p90": 0,
            },
            "cases": [],
            "errors": list(validation.errors),
            "caveats": [
                "Sanitized manifest per-case retrieval evaluation was not run in the default semantic-debug path.",
                "Pass --evaluate-sanitized-manifest-gate to run the expensive case loop.",
                "18 reviewed real-history cases are below the old 20-50 target floor."
                if len(cases) < 20
                else "case count meets the old target floor",
                "No no-evidence family is present in the reviewed manifest." if no_evidence_count == 0 else "no-evidence family present",
                "Correctness is not evaluated when the manifest gate is deferred.",
            ],
        }
    started = time.monotonic()
    case_rows = evaluate_manifest_cases(
        cases,
        source_plan=source_plan,
        plan=plan,
        default_tz=default_tz,
        mask_context=mask_context,
        output_format=output_format,
        use_rg_prefilter=use_rg_prefilter,
    ) if validation.ok else []
    elapsed_values = [int(row["elapsed_ms"]) for row in case_rows]
    byte_values = [int(row["output_context_bytes"]) for row in case_rows]
    token_values = [int(row["estimated_output_tokens"]) for row in case_rows]
    correctness_counts = Counter(row.get("correctness_status", "unknown") for row in case_rows)
    command_status_counts = Counter(row.get("command_status", "unknown") for row in case_rows)
    return {
        "status": "evaluated_sanitized_manifest" if validation.ok else "invalid_manifest",
        "manifest": str(path),
        "evaluation_kind": "sanitized_manifest_iteration",
        "measurement_basis": "runs sanitized ask_pattern_paraphrase values through candidate recovery over the caller-selected source scope; per-case output omits snippets, raw paths, raw private query text, and private bridge refs",
        "case_count": len(cases),
        "reviewed_count": reviewed_count,
        "reviewed_case_count": len(cases),
        "below_floor_case_count": len(cases) < 20,
        "old_target_floor": 20,
        "family_counts": dict(sorted(family_counts.items())),
        "thin_families": thin_families,
        "missing_families": ["no_evidence"] if no_evidence_count == 0 else [],
        "privacy_contract": "sanitized aggregate only; no raw private text, paths, query wording, session IDs, vectors, indexes, or private bridge refs are required",
        "correctness_gate": "not_evaluable_without_private_bridge" if correctness_counts.get("not_evaluable_without_private_bridge") else "not_evaluated",
        "correctness_evaluable_count": sum(count for status, count in correctness_counts.items() if status in {"passed", "failed"}),
        "correctness_not_evaluable_count": sum(count for status, count in correctness_counts.items() if status.startswith("not_evaluable")),
        "correctness_pass_count": correctness_counts.get("passed", 0),
        "correctness_fail_count": correctness_counts.get("failed", 0),
        "correctness_status_counts": dict(sorted(correctness_counts.items())),
        "command_status_counts": dict(sorted(command_status_counts.items())),
        "elapsed_ms": {
            "total": sum(elapsed_values),
            "median": median_int(elapsed_values),
            "p90": percentile_nearest(elapsed_values, 90),
            "evaluation_total": int(round((time.monotonic() - started) * 1000)),
        },
        "output_context_bytes": {
            "total": sum(byte_values),
            "median": median_int(byte_values),
            "p90": percentile_nearest(byte_values, 90),
        },
        "estimated_output_tokens": {
            "total": sum(token_values),
            "median": median_int(token_values),
            "p90": percentile_nearest(token_values, 90),
        },
        "cases": case_rows,
        "errors": list(validation.errors),
        "caveats": [
            "18 reviewed real-history cases are below the old 20-50 target floor."
            if len(cases) < 20
            else "case count meets the old target floor",
            "No no-evidence family is present in the reviewed manifest." if no_evidence_count == 0 else "no-evidence family present",
            "Correctness is not evaluable for private sanitized refs without the ignored private bridge.",
        ],
    }


def evaluate_manifest_cases(
    cases: list[Any],
    *,
    source_plan: dict[str, Any],
    plan: Any,
    default_tz: Any,
    mask_context: Any,
    output_format: str,
    use_rg_prefilter: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = parse_source_plan_now(source_plan)
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            continue
        query_text, query_basis = sanitized_case_query(case)
        started = time.monotonic()
        command_status = "not_run"
        candidate_count = 0
        searched_files = 0
        searched_bytes = 0
        warning_count = 0
        try:
            case_intent = infer_query(query_text, now=now, role=None)
            case_source_plan = dict(source_plan)
            case_source_plan["query"] = case_intent.to_dict()
            case_result = search(
                plan,
                case_intent,
                default_tz=default_tz,
                cwd=source_plan["cwd"],
                mask_context=mask_context,
                use_rg_prefilter=use_rg_prefilter,
            )
            case_result["warnings"] = sorted(set(case_result.get("warnings", []) + case_intent.warnings))
            agent_find = build_agent_find_payload(
                source_plan=case_source_plan,
                search_result=case_result,
                manifest_report=None,
                output_format=output_format,
                report_output=None,
            )["agent_find"]
            command_status = agent_find.get("status", "unknown")
            candidate_count = int(agent_find.get("token_efficiency", {}).get("candidate_count", 0) or 0)
            searched_files = int(agent_find.get("token_efficiency", {}).get("searched_files", 0) or 0)
            searched_bytes = int(agent_find.get("token_efficiency", {}).get("searched_bytes", 0) or 0)
            warning_count = len(case_result.get("warnings", []))
        except Exception:
            command_status = "evaluation_error"
        elapsed_ms = int(round((time.monotonic() - started) * 1000))
        correctness_status, evaluability_status = correctness_for_sanitized_case(case)
        row = {
            "case_id": case.get("case_id"),
            "family": case.get("family"),
            "evidence_type": case.get("evidence_type"),
            "expected_outcome": case.get("expected_outcome"),
            "review_status": case.get("review_status"),
            "query_basis": query_basis,
            "elapsed_ms": elapsed_ms,
            "command": "semantic-debug sanitized-manifest-eval",
            "command_status": command_status,
            "evaluability_status": evaluability_status,
            "correctness_status": correctness_status,
            "candidate_count": candidate_count,
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "warning_count": warning_count,
        }
        row_bytes = len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        row["output_context_bytes"] = row_bytes
        row["estimated_output_tokens"] = estimate_tokens(row_bytes)
        row["case_index"] = index
        rows.append(row)
    return rows


def sanitized_case_query(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(case.get("ask_pattern_paraphrase"), str) and case["ask_pattern_paraphrase"].strip():
        return case["ask_pattern_paraphrase"], {
            "source": "ask_pattern_paraphrase",
            "sanitized": True,
            "paraphrase_only": True,
            "raw_query_wording_used": False,
        }
    fallback = " ".join(
        str(case.get(key) or "")
        for key in ("family", "evidence_type", "expected_outcome", "timestamp_label", "window_label")
    ).strip() or "sanitized manifest case"
    return fallback, {
        "source": "sanitized_case_labels",
        "sanitized": True,
        "paraphrase_only": True,
        "raw_query_wording_used": False,
    }


def correctness_for_sanitized_case(case: dict[str, Any]) -> tuple[str, str]:
    refs = list(case.get("must_find_refs") or case.get("expected_refs") or [])
    if not refs:
        return "not_evaluable_no_expected_refs", "not_evaluable_no_expected_refs"
    if any(is_private_sanitized_ref(ref) for ref in refs if isinstance(ref, dict)):
        return "not_evaluable_without_private_bridge", "expected_refs_are_private_sanitized_refs"
    return "not_evaluable_sanitized_refs_only", "no_raw_source_refs_available"


def is_private_sanitized_ref(ref: dict[str, Any]) -> bool:
    ref_kind = str(ref.get("ref_kind") or "")
    return "private" in ref_kind or "sanitized" in ref_kind


def parse_source_plan_now(source_plan: dict[str, Any]) -> datetime:
    value = str(source_plan.get("now") or "")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


def median_int(values: list[int]) -> int:
    return int(round(median(values))) if values else 0


def percentile_nearest(values: list[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round((percentile / 100) * (len(ordered) - 1)))
    return ordered[index]


def build_routes(
    agent_find: dict[str, Any],
    source_scope: dict[str, Any],
    tool_report: dict[str, Any],
    forensics_payload: dict[str, Any] | None,
    hints: dict[str, str | None],
    benchmark_gate: dict[str, Any],
) -> dict[str, Any]:
    selected = [
        {"primitive": "find", "reason": "candidate recovery, timeline, source scope, no-evidence fields"},
        {"primitive": "structured-tool-events", "reason": "structured action/tool proof"},
    ]
    if benchmark_gate.get("evaluation_kind") == "sanitized_manifest_iteration":
        selected.append({"primitive": "sanitized_manifest_family_gate", "reason": "sanitized manifest-case evaluation without private bridge"})
    else:
        selected.append({"primitive": "sanitized_manifest_family_gate", "reason": "cheap sanitized manifest metadata only; per-case evaluation deferred by default"})
    if forensics_payload:
        selected.append({"primitive": "forensics", "reason": "commit/path/task/branch/around hint supplied"})
    rejected = [
        {"primitive": "embeddings", "reason": "not default; gate remains opt-in and unpromoted"},
        {"primitive": "timestamp_sidecar", "reason": "not default; sidecar gate remains inconclusive"},
        {"primitive": "native_scanner", "reason": "not default; native scanner gate not evaluated"},
        {"primitive": "semantic_linter_runtime", "reason": "out of product scope for JSONL"},
    ]
    missing = []
    if not any(hints.values()):
        missing.append({"primitive": "forensics", "reason": "no commit/path/task/branch/around hint"})
    if benchmark_gate.get("missing_families"):
        missing.append({"primitive": "sanitized_manifest_family", "reason": "missing families: " + ", ".join(benchmark_gate["missing_families"])})
    not_searched = list(source_scope.get("not_searched_scope", {}).get("notes", []))
    if tool_report.get("status") == "no_structured_events":
        not_searched.append("Structured-event scan found no events in the selected source scope.")
    return {
        "selected": selected,
        "rejected": rejected,
        "missing": missing,
        "not_searched": not_searched,
        "source_status": agent_find.get("status"),
    }


def phase_hint(snippet: str) -> str | None:
    text = snippet.lower()
    for label, terms in {
        "user_request": ("please", "requested", "should total", "add "),
        "agent_plan": ("plan:", "will "),
        "code_change": ("apply_patch", "add file", "return "),
        "review_outcome": ("review outcome", "verified", "not covered"),
        "later_symptom": ("returns", "expected", "symptom"),
        "likely_wrong_assumption": ("likely contributing assumption", "treated"),
    }.items():
        if any(term in text for term in terms):
            return label
    return None


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def dedupe_dicts(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in values:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
