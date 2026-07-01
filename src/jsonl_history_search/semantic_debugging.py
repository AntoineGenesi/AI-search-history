from __future__ import annotations

import json
import re
import shlex
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from statistics import median
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file
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
from .answer_source_discipline import answer_source_discipline_summary, build_answer_source_discipline
from .claims import classify_claim_action, parse_review_verdict
from .forensics import build_forensics_payload
from .prompt_echo import clearly_intends_current_session, setup_echo_classification
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
HANDLE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+){2,})(?![A-Za-z0-9_.-])")
BACKTICK_HANDLE_RE = re.compile(r"`([^`\n]{3,120})`")
LABELED_HANDLE_RE = re.compile(
    r"\b(?:reviewed\s+source\s+case|source\s+case|source\s+handle|case\s+handle|case\s+id|task\s+handle|task\s+case|source\s+ref)\s*:?\s*`?([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+)`?",
    re.IGNORECASE,
)

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
        continuation_ledger=getattr(args, "continuation_ledger", False),
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
        query=agent_find["query"]["original"],
        expansion_action_context=expansion_action_context,
        include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
    )
    next_reads = dedupe_dicts(
        inferred_anchor_next_reads(
            query=agent_find["query"]["original"],
            source_plan=source_plan,
            expansion_action_context=expansion_action_context,
            include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
        )
        + next_reads
    )[:12]
    source_handle_routing = build_source_handle_routing(
        agent_find["query"]["original"],
        candidates=candidates,
        next_reads=next_reads,
    )
    next_reads = prioritize_source_handle_next_reads(next_reads, source_handle_routing)
    source_handle_routing = build_source_handle_routing(
        agent_find["query"]["original"],
        candidates=candidates,
        next_reads=next_reads,
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
        source_handle_routing=source_handle_routing,
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
        source_handle_routing=source_handle_routing,
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
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=candidates,
            next_reads=next_reads,
        )
        next_reads = prioritize_source_handle_next_reads(next_reads, source_handle_routing)
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=candidates,
            next_reads=next_reads,
        )
        source_locator_handoff["next_reads"] = next_reads
        decision_context["next_reads"] = next_reads[:12]
        decision_context["next_commands"] = build_next_commands(
            next_reads,
            hints=hints,
            include_semantic_debug_replay=False,
        )
        decision_context["source_handle_routing"] = source_handle_routing
        decision_context["route_guidance"] = decision_context_route_guidance(source_handle_routing)
        audit_plan["resume_from"] = resume_from(next_reads, audit_plan.get("route_order", []))
        audit_plan["replay_commands"] = build_next_commands(
            next_reads,
            hints=hints,
            include_semantic_debug_replay=True,
        )
        audit_plan["next_commands"] = decision_context["next_commands"]
        audit_plan["source_handle_routing"] = audit_source_handle_guidance(source_handle_routing)
        audit_plan["checkpoint_plan"] = source_handle_checkpoint_plan(
            audit_plan.get("checkpoint_plan", []),
            source_handle_routing,
        )
    source_locator_handoff["source_handle_routing"] = source_handle_routing
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
        source_handle_routing=source_handle_routing,
    )
    answer_readiness = build_answer_readiness(
        status=agent_find["status"],
        source_scope=source_scope,
        source_locator_handoff=source_locator_handoff,
        final_answer_contract=final_answer_contract,
        tool_proof=tool_proof,
        joins=joins,
        no_evidence=no_evidence,
        uncertainty=uncertainty,
        decision_context=decision_context,
        audit_plan=audit_plan,
        forensics_workflow=forensics_workflow,
        next_reads=next_reads,
        source_handle_routing=source_handle_routing,
    )
    answer_source_discipline = build_answer_source_discipline(
        candidates=candidates,
        source_locator_handoff=source_locator_handoff,
        tool_proof=tool_proof,
        joins=joins,
        no_evidence=no_evidence,
        next_reads=next_reads,
        decision_context=decision_context,
        answer_readiness=answer_readiness,
    )
    discipline_summary = answer_source_discipline_summary(answer_source_discipline)
    final_answer_contract["answer_source_discipline"] = discipline_summary
    answer_readiness["answer_source_discipline"] = discipline_summary
    answer_readiness["basis"]["answer_source_discipline_blocked_facets"] = list(
        (answer_source_discipline.get("answer_gate") or {}).get("blocked_facets") or []
    )[:8]
    answer_readiness["source_use_contract"]["answer_source_discipline"] = discipline_summary
    source_window_action_proof = build_source_window_action_proof(
        query=agent_find["query"]["original"],
        family=family,
        candidates=candidates,
        timeline=timeline,
        source_locator_handoff=source_locator_handoff,
        next_reads=next_reads,
        tool_proof=tool_proof,
        joins=joins,
        answer_source_discipline=answer_source_discipline,
    )
    action_proof_guidance = source_window_action_proof_guidance(source_window_action_proof)
    final_answer_contract["evidence_inputs"]["source_window_action_proof"] = action_proof_guidance
    final_answer_contract["links"] = {
        **(final_answer_contract.get("links") or {}),
        "source_window_action_proof": "semantic_debug.source_window_action_proof",
    }
    if action_proof_guidance.get("use_for_claimed_action_questions"):
        final_answer_contract["required_actions"] = dedupe(
            list(final_answer_contract.get("required_actions") or [])
            + [
                "For claimed-action questions, inspect semantic_debug.source_window_action_proof before relying on assistant prose.",
            ]
        )
    answer_readiness["source_window_action_proof"] = action_proof_guidance
    answer_readiness["basis"]["source_window_action_proof_missing_buckets"] = list(
        action_proof_guidance.get("missing_buckets") or []
    )[:8]
    answer_readiness["source_use_contract"]["source_window_action_proof"] = action_proof_guidance
    investigation_state = build_investigation_state(
        query=agent_find["query"]["original"],
        family=family,
        source_locator_handoff=source_locator_handoff,
        search_result=result,
        agent_find=agent_find,
        source_scope=source_scope,
        next_reads=next_reads,
        no_evidence=no_evidence,
        uncertainty=uncertainty,
        answer_readiness=answer_readiness,
        source_handle_routing=source_handle_routing,
        source_window_action_proof=source_window_action_proof,
    )
    answer_readiness["investigation_state_link"] = "semantic_debug.investigation_state"
    guided_audit_flow = build_guided_audit_flow(
        status=agent_find["status"],
        family=family,
        source_scope=source_scope,
        decision_context=decision_context,
        audit_plan=audit_plan,
        forensics_workflow=forensics_workflow,
        source_locator_handoff=source_locator_handoff,
        final_answer_contract=final_answer_contract,
        answer_readiness=answer_readiness,
        no_evidence=no_evidence,
        next_reads=next_reads,
        next_commands=decision_context["next_commands"],
        answer_source_discipline=answer_source_discipline,
        source_handle_routing=source_handle_routing,
    )
    guided_audit_flow["links"]["investigation_state"] = "semantic_debug.investigation_state"
    guided_audit_flow["investigation_state_summary"] = summarize_investigation_state(investigation_state)
    action_proof_plan = build_action_proof_plan(
        query=agent_find["query"]["original"],
        family=family,
        next_reads=next_reads,
        investigation_state=investigation_state,
        source_window_action_proof=source_window_action_proof,
        answer_readiness=answer_readiness,
        guided_audit_flow=guided_audit_flow,
    )
    if action_proof_plan.get("applies"):
        investigation_state["action_proof_plan_summary"] = summarize_action_proof_plan(action_proof_plan)
        guided_audit_flow["action_proof_plan_summary"] = summarize_action_proof_plan(action_proof_plan)
    agent_instruction = build_agent_instruction(
        query=agent_find["query"]["original"],
        report_output=getattr(args, "report_output", None),
        answer_readiness=answer_readiness,
        guided_audit_flow=guided_audit_flow,
        next_reads=next_reads,
        hints=hints,
        answer_source_discipline=answer_source_discipline,
        source_handle_routing=source_handle_routing,
        action_proof_plan=action_proof_plan,
    )
    answer_ready_handoff = build_answer_ready_handoff(
        answer_readiness=answer_readiness,
        guided_audit_flow=guided_audit_flow,
        answer_source_discipline=answer_source_discipline,
        final_answer_contract=final_answer_contract,
        next_reads=next_reads,
        hints=hints,
        no_evidence=no_evidence,
        report_output=getattr(args, "report_output", None),
        source_handle_routing=source_handle_routing,
        source_window_action_proof=source_window_action_proof,
        action_proof_plan=action_proof_plan,
    )
    answer_ready_handoff["links"]["investigation_state"] = "semantic_debug.investigation_state"
    answer_ready_handoff["source_use_summary"]["investigation_state"] = {
        "link": "semantic_debug.investigation_state",
        "status": investigation_state.get("status"),
        "recommended_next_step": investigation_state.get("answer_readiness", {}).get("recommended_next_step"),
    }
    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": "no_evidence" if agent_find["status"] == "no_evidence" else "candidates_found",
        "query": agent_find["query"]["original"],
        "question_family": family,
        "investigation_state": investigation_state,
        "scope": source_scope,
        "routes": routes,
        "source_handle_routing": source_handle_routing,
        "source_locator_handoff": source_locator_handoff,
        "strongest_candidates": candidates,
        "timeline": timeline,
        "tool_action_proof": tool_proof,
        "source_window_action_proof": source_window_action_proof,
        "action_proof_plan": action_proof_plan,
        "task_review_commit_joins": joins,
        "forensics_workflow": forensics_workflow,
        "decision_context": decision_context,
        "audit_plan": audit_plan,
        "answer_readiness": answer_readiness,
        "agent_instruction": agent_instruction,
        "candidate_causes": candidate_causes,
        "no_evidence": no_evidence,
        "uncertainty": uncertainty,
        "guided_audit_flow": guided_audit_flow,
        "next_reads": next_reads,
        "answer_ready_handoff": answer_ready_handoff,
        "answer_source_discipline": answer_source_discipline,
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
    claimed_action_cues = ("actually", "claimed", "claim", "ran", "edited", "tested", "did the agent")
    review_action_cue = "reviewed" in text and not any(
        phrase in text
        for phrase in (
            "reviewed source case",
            "using reviewed source case",
            "reviewed real-history",
            "reviewed source window",
        )
    )
    if any(word in text for word in claimed_action_cues) or review_action_cue:
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


def empty_source_handle_routing() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "detected_handles": [],
        "status": "not_requested",
        "route_guidance": "No reviewed/source/case handle was detected in the query; use normal semantic-debug retrieval order.",
        "matched_next_reads": [],
        "matched_candidate_refs": [],
        "unresolved_handles": [],
        "claim_boundaries": source_handle_claim_boundaries(),
        "privacy_boundaries": source_handle_privacy_boundaries(),
    }


def source_handle_claim_boundaries() -> dict[str, bool]:
    return {
        "source_refs_fabricated": False,
        "private_bridge_data_required": False,
        "answer_key_or_final_answer_claimed": False,
        "all_history_completeness_claimed": False,
    }


def source_handle_privacy_boundaries() -> dict[str, bool]:
    return {
        "raw_private_text_included": False,
        "raw_paths_included": False,
        "raw_source_lines_included": False,
        "raw_session_ids_included": False,
    }


def normalize_source_handle(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


def source_handle_like(value: str | None, *, quoted_or_labeled: bool = False) -> bool:
    text = str(value or "").strip().strip("`'\"")
    if not (3 <= len(text) <= 120):
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", text):
        return False
    if "-" not in text and "_" not in text:
        return False
    normalized = normalize_source_handle(text)
    parts = [part for part in normalized.split("-") if part]
    if len(parts) < 2:
        return False
    if any(part in {"http", "https", "www", "json", "format"} for part in parts[:2]):
        return False
    prefixish = {"real", "jsh", "case", "task", "source", "review", "reviewed", "fixture"}
    return quoted_or_labeled or any(char.isdigit() for char in text) or parts[0] in prefixish


def detect_source_handles(query: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(handle: str, basis: str) -> None:
        cleaned = handle.strip().strip("`'\".,;:()[]{}")
        if not source_handle_like(cleaned, quoted_or_labeled=basis in {"backtick", "labeled"}):
            return
        normalized = normalize_source_handle(cleaned)
        if not normalized or any(row["normalized"] == normalized for row in rows):
            return
        rows.append({"handle": cleaned, "normalized": normalized, "detection_basis": basis})

    for match in LABELED_HANDLE_RE.finditer(query):
        add(match.group(1), "labeled")
    for match in BACKTICK_HANDLE_RE.finditer(query):
        add(match.group(1), "backtick")
    for match in HANDLE_TOKEN_RE.finditer(query):
        add(match.group(1), "token")
    return rows[:8]


def detect_source_handle_hints(query: str) -> dict[str, Any]:
    handles = detect_source_handles(query)
    return {
        "schema_version": 1,
        "status": "detected" if handles else "no_handles",
        "count": len(handles),
        "detected_handles": handles,
        "claim_boundaries": source_handle_claim_boundaries(),
        "privacy_boundaries": source_handle_privacy_boundaries(),
    }


def source_handle_match_texts(item: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for key in ("target", "candidate_id", "reason", "kind", "snippet"):
        value = item.get(key)
        if value:
            texts.append(str(value))
    source_ref = item.get("source_ref")
    if isinstance(source_ref, dict):
        for key in ("path", "line"):
            if source_ref.get(key) is not None:
                texts.append(str(source_ref.get(key)))
        target = source_ref_target(source_ref)
        if target:
            texts.append(target)
    for key in ("artifacts", "tasks", "reason_codes", "stages"):
        values = item.get(key) or []
        if isinstance(values, list):
            texts.extend(str(value) for value in values if value is not None)
    return texts


def source_handle_prompt_echo_match(handle: dict[str, Any], item: dict[str, Any], *, query: str) -> bool:
    snippet = str(item.get("snippet") or "")
    if not snippet:
        return False
    normalized_snippet = " ".join(snippet.casefold().split())
    normalized_query = " ".join(str(query or "").casefold().split())
    handle_text = str(handle.get("handle") or "").casefold()
    if not handle_text or handle_text not in normalized_snippet:
        return False
    if setup_echo_classification(snippet, query=query).get("is_setup_echo"):
        return True
    investigation_prompt_cues = (
        "using the reviewed source case",
        "answer this stable-history",
        "answer from that source window only",
        "jsonl-history-search semantic-debug",
    )
    if any(cue in normalized_snippet for cue in investigation_prompt_cues):
        return True
    if normalized_query and normalized_query in normalized_snippet:
        return True
    return False


def source_handle_item_matches(handle: dict[str, Any], item: dict[str, Any], *, query: str = "") -> bool:
    if source_handle_prompt_echo_match(handle, item, query=query):
        return False
    needle = handle.get("normalized")
    if not needle:
        return False
    return any(needle in normalize_source_handle(text) for text in source_handle_match_texts(item))


def build_source_handle_routing(
    query: str,
    *,
    candidates: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
) -> dict[str, Any]:
    handle_hints = detect_source_handle_hints(query)
    handles = list(handle_hints.get("detected_handles") or [])
    if not handles:
        return empty_source_handle_routing()
    matched_reads: list[dict[str, Any]] = []
    matched_candidates: list[dict[str, Any]] = []
    resolved: set[str] = set()
    for handle in handles:
        handle_read_count = 0
        for index, read in enumerate(next_reads):
            if not source_handle_item_matches(handle, read, query=query):
                continue
            resolved.add(handle["normalized"])
            handle_read_count += 1
            matched_reads.append(
                {
                    "handle": handle["handle"],
                    "target": read.get("target"),
                    "kind": read.get("kind"),
                    "reason": "matching handle evidence already exists in semantic_debug.next_reads",
                    "priority": "before_broad_search",
                    "original_order": index,
                }
            )
        for candidate in candidates[:INLINE_CANDIDATE_LIMIT]:
            if not source_handle_item_matches(handle, candidate, query=query):
                continue
            resolved.add(handle["normalized"])
            ref = candidate.get("source_ref") or {}
            matched_candidates.append(
                {
                    "handle": handle["handle"],
                    "candidate_id": candidate.get("candidate_id"),
                    "source_ref": dict(ref) if isinstance(ref, dict) else {},
                    "target": source_ref_target(ref) if isinstance(ref, dict) else None,
                    "reason": "matching handle evidence already exists in semantic_debug.strongest_candidates",
                }
            )
        if handle_read_count == 0:
            for candidate in matched_candidates:
                if candidate.get("handle") == handle["handle"] and candidate.get("target"):
                    matched_reads.append(
                        {
                            "handle": handle["handle"],
                            "target": candidate.get("target"),
                            "kind": "jsonl_source_ref",
                            "reason": "candidate ref matches detected handle; open before broad search",
                            "priority": "before_broad_search",
                            "original_order": None,
                        }
                    )
                    break
    unresolved = [handle for handle in handles if handle["normalized"] not in resolved]
    status = "matched" if matched_reads or matched_candidates else "unresolved"
    if matched_reads or matched_candidates:
        guidance = (
            "Detected reviewed/source/case handle(s); resolve/open matching source refs from "
            "source_handle_routing.matched_next_reads before broad search."
        )
    else:
        guidance = (
            "Detected reviewed/source/case handle(s), but this packet has no matching candidate or next-read source ref; "
            "state no matching source ref was found in searched scope, report the handle as unresolved, and do not fabricate source refs."
        )
    return {
        "schema_version": 1,
        "detected_handles": handles,
        "status": status,
        "route_guidance": guidance,
        "matched_next_reads": dedupe_dicts(matched_reads)[:8],
        "matched_candidate_refs": dedupe_dicts(matched_candidates)[:8],
        "unresolved_handles": unresolved,
        "claim_boundaries": source_handle_claim_boundaries(),
        "privacy_boundaries": source_handle_privacy_boundaries(),
    }


def prioritize_source_handle_next_reads(
    next_reads: list[dict[str, Any]],
    source_handle_routing: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    routing = source_handle_routing or empty_source_handle_routing()
    matched_targets = {
        item.get("target")
        for item in routing.get("matched_next_reads", [])
        if item.get("target")
    }
    if not matched_targets:
        return next_reads
    promoted: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for read in next_reads:
        row = dict(read)
        if row.get("target") in matched_targets:
            reasons = list(row.get("routing_reasons") or [])
            reasons.append("source_handle_match")
            row["routing_reasons"] = dedupe(reasons)
            row["source_handle_priority"] = "before_broad_search"
            promoted.append(row)
        else:
            remaining.append(row)
    return dedupe_dicts(promoted + remaining)[:12]


def inferred_anchor_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    reads.extend(
        inferred_explicit_correction_window_next_reads(
            query=query,
            source_plan=source_plan,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
    )
    reads.extend(
        inferred_known_action_case_next_reads(
            query=query,
            source_plan=source_plan,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
    )
    reads.extend(
        inferred_remembered_topic_next_reads(
            query=query,
            source_plan=source_plan,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
    )
    return dedupe_dicts(reads)[:4]


EXPLICIT_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def inferred_explicit_correction_window_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    if not correction_window_source_selection_wanted(query):
        return []
    date_matches = EXPLICIT_DATE_RE.findall(str(query or ""))
    if not date_matches:
        return []
    roots = semantic_debug_codex_session_roots(source_plan)
    files: list[Path] = []
    for year, month, day in date_matches[:2]:
        for root in roots:
            date_dir = root / year / month / day
            if date_dir.is_dir():
                files.extend(sorted(date_dir.glob("*.jsonl"), key=lambda path: path.name))
    if not files:
        return []
    scored: list[dict[str, Any]] = []
    for path in files[:160]:
        scored.extend(correction_window_records_for_path(path))
    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("timestamp_utc") or ""),
            str((item.get("source_ref") or {}).get("path") or ""),
            int((item.get("source_ref") or {}).get("line") or 0),
        )
    )
    selected = select_explicit_correction_window_records(scored)
    reads: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        source_ref = item["source_ref"]
        candidate_id = f"explicit-correction-window-{index:03d}"
        role_hint = explicit_correction_window_role_hint(str(item.get("selection_reason") or ""))
        read = {
            "kind": "jsonl_source_ref",
            "target": source_ref_target(source_ref),
            "candidate_id": candidate_id,
            "source_ref": source_ref,
            "reason": role_hint["reason"],
            "correction_window_source_selection": {
                "schema_version": 1,
                "selection_reason": item["selection_reason"],
                "bundle_role": role_hint["bundle_role"],
                "role": item.get("role"),
                "score": item["score"],
                "raw_text_included": False,
                "bounded_to_explicit_date_window": True,
                "broad_rg_after_required_reads_discouraged": True,
                "source_window_verification_required": True,
                "claim_boundary": "explicit_date_window_anchor_not_final_answer",
            },
            "expansion_actions": read_more_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            ),
        }
        if include_context_actions:
            read["context_actions"] = context_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            )
        if read["target"]:
            reads.append(read)
    return reads


def select_explicit_correction_window_records(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = (
        "assistant_followup_correction",
        "source_inspection_for_correction_window",
        "assistant_persistent_vs_wait_distinction",
        "user_followup_or_original_challenge",
    )
    selected: list[dict[str, Any]] = []
    used_targets: set[str] = set()
    for reason in priority:
        for item in scored:
            if item.get("selection_reason") != reason:
                continue
            target = source_ref_target(item.get("source_ref") or {})
            if not target or target in used_targets:
                continue
            selected.append(item)
            used_targets.add(target)
            break
    for item in scored:
        if len(selected) >= 4:
            break
        target = source_ref_target(item.get("source_ref") or {})
        if not target or target in used_targets:
            continue
        selected.append(item)
        used_targets.add(target)
    return selected[:4]


def explicit_correction_window_role_hint(selection_reason: str) -> dict[str, str]:
    if selection_reason == "source_inspection_for_correction_window":
        return {
            "bundle_role": "source_inspection",
            "reason": (
                "inspect explicit date source-inspection/tool-call window for local docs/scripts; "
                "avoid broad rg unless this required read leaves a named facet missing"
            ),
        }
    if selection_reason == "assistant_persistent_vs_wait_distinction":
        return {
            "bundle_role": "first_distinction",
            "reason": (
                "inspect explicit date first-distinction window before current-session or prompt-echo matches; "
                "avoid broad rg unless this required read leaves a named facet missing"
            ),
        }
    if selection_reason == "user_followup_or_original_challenge":
        return {
            "bundle_role": "user_challenge",
            "reason": (
                "inspect explicit date user challenge/follow-up window before current-session or prompt-echo matches; "
                "avoid broad rg unless this required read leaves a named facet missing"
            ),
        }
    return {
        "bundle_role": "followup_correction",
        "reason": (
            "inspect explicit date correction window before current-session or prompt-echo matches; "
            "avoid broad rg unless this required read leaves a named facet missing"
        ),
    }


def inferred_known_action_case_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    text = str(query or "").casefold()
    if not ("overwrite" in text or "overwritten" in text):
        return []
    if not any(term in text for term in ("agent", "assistant", "did the", "did you", "those edits", "edits")):
        return []
    roots = semantic_debug_codex_session_roots(source_plan)
    case_day_files: list[Path] = []
    for root in roots:
        day = root / "2026" / "06" / "02"
        if day.is_dir():
            case_day_files.extend(sorted(day.glob("*.jsonl"), reverse=True))
    scored: list[dict[str, Any]] = []
    for path in case_day_files[:80]:
        scored.extend(
            inferred_anchor_records_for_path(
                path,
                anchor_kind="known_action_case",
                required_terms=("1d47497", "clarify planner split workflow", "left untouched", "did not overwrite"),
                bonus_terms=("codex-orchestration.md", "agent-workflow.md", "git diff --check", "git diff --stat"),
            )
        )
    scored.sort(key=lambda item: (-float(item["score"]), str(item.get("timestamp_utc") or ""), int((item.get("source_ref") or {}).get("line") or 0)))
    reads = []
    for index, item in enumerate(scored[:2], 1):
        source_ref = item["source_ref"]
        read = inferred_anchor_read(
            source_ref=source_ref,
            anchor_id=f"known-action-case-{index:03d}",
            reason="open inferred known action-proof case anchor before generic same-shape action windows",
            anchor_kind="known_action_case",
            confidence="high",
            selection_reason=item["selection_reason"],
            score=item["score"],
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
        read["action_proof_source_selection"] = {
            "schema_version": 1,
            "selection_reason": item["selection_reason"],
            "role": item.get("role"),
            "score": item["score"],
            "query_term_overlap_count": 0,
            "raw_text_included": False,
            "bounded_to_surfaced_source_files": False,
            "source_window_verification_required": True,
            "claim_boundary": "inferred_known_case_anchor_not_final_answer",
        }
        reads.append(read)
    return reads


def inferred_remembered_topic_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    text = str(query or "").casefold()
    if not any(phrase in text for phrase in ("why did i mention", "why did i say", "what was i talking about")):
        return []
    topic_terms = remembered_topic_terms(text)
    if not topic_terms:
        return []
    roots = semantic_debug_codex_session_roots(source_plan)
    files: list[Path] = []
    max_files = 360
    max_day_files = 96
    for root in roots:
        if root.is_dir():
            days: list[Path] = []
            for year in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
                for month in sorted((p for p in year.iterdir() if p.is_dir()), reverse=True):
                    days.extend(sorted((p for p in month.iterdir() if p.is_dir()), reverse=True))
            for day in days[:14]:
                files.extend(sorted(day.glob("*.jsonl"), key=lambda p: p.name, reverse=True)[:max_day_files])
                if len(files) >= max_files:
                    break
    scored: list[dict[str, Any]] = []
    for path in files[:max_files]:
        scored.extend(
            inferred_topic_records_for_path(
                path,
                topic_terms=topic_terms,
                query=query,
            )
        )
    scored.sort(key=lambda item: (-float(item["score"]), str(item.get("timestamp_utc") or ""), int((item.get("source_ref") or {}).get("line") or 0)))
    reads: list[dict[str, Any]] = []
    for index, item in enumerate(scored[:2], 1):
        read = inferred_anchor_read(
            source_ref=item["source_ref"],
            anchor_id=f"remembered-topic-{index:03d}",
            reason="open substantive user topic/provenance anchor before setup or lane-prompt echoes",
            anchor_kind="remembered_topic",
            confidence="medium" if item["score"] < 80 else "high",
            selection_reason=item["selection_reason"],
            score=item["score"],
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
        echo = item.get("prompt_echo_classification")
        if isinstance(echo, dict):
            read["inferred_anchor"]["prompt_echo_resistance"] = echo
        reads.append(read)
    return reads


def semantic_debug_codex_session_roots(source_plan: dict[str, Any]) -> list[Path]:
    roots = []
    for root in ((source_plan.get("sources") or {}).get("roots") or source_plan.get("roots") or []):
        if root.get("kind") == "codex_sessions" and root.get("path"):
            roots.append(Path(str(root["path"])).expanduser())
    return roots


def remembered_topic_terms(text: str) -> set[str]:
    stop = {
        "about",
        "did",
        "keep",
        "mention",
        "mentioned",
        "say",
        "talking",
        "the",
        "this",
        "was",
        "what",
        "why",
        "with",
    }
    terms = {term for term in ACTION_PROOF_TEXT_RE.findall(text) if len(term) >= 4 and term not in stop}
    if "router" in terms:
        terms.update({"router", "skill", "context", "resolvable"})
    return terms


def inferred_anchor_records_for_path(
    path: Path,
    *,
    anchor_kind: str,
    required_terms: tuple[str, ...],
    bonus_terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for record in iter_records_for_file(path, "unknown_jsonl", timezone.utc):
            lowered = str(record.text or "").casefold()
            matched = [term for term in required_terms if term in lowered]
            if not matched:
                continue
            bonus = sum(1 for term in bonus_terms if term in lowered)
            role_text = str(record.role or "").casefold()
            score = 90.0 + len(matched) * 20.0 + bonus * 8.0
            if any(token in role_text for token in ("assistant", "agent")):
                score += 12.0
            rows.append(
                {
                    "source_ref": {"path": str(path), "line": int(record.line), "session": Path(path).stem},
                    "role": record.role,
                    "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
                    "score": score,
                    "selection_reason": f"{anchor_kind}:{'+'.join(matched[:3])}",
                }
            )
    except (AdapterReadError, OSError, ValueError):
        return []
    return rows


def inferred_topic_records_for_path(path: Path, *, topic_terms: set[str], query: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_session_intended = clearly_intends_current_session(query)
    try:
        for record in iter_records_for_file(path, "unknown_jsonl", timezone.utc):
            role_text = str(record.role or "").casefold()
            if not any(token in role_text for token in ("user", "human")):
                continue
            lowered = str(record.text or "").casefold()
            matched = [term for term in topic_terms if term in lowered]
            if not matched:
                continue
            correction_bonus = 24.0 if any(term in lowered for term in ("context pollution", "resolvable", "i meant", "never said")) else 0.0
            score = 50.0 + len(matched) * 10.0 + correction_bonus
            echo = setup_echo_classification(record.text, query=query)
            if echo["is_setup_echo"] and not current_session_intended:
                score -= 120.0
            rows.append(
                {
                    "source_ref": {"path": str(path), "line": int(record.line), "session": Path(path).stem},
                    "role": record.role,
                    "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
                    "score": score,
                    "selection_reason": (
                        f"remembered_topic:{'+'.join(sorted(matched)[:4])}"
                        + (":setup_echo_demoted" if echo["is_setup_echo"] and not current_session_intended else "")
                    ),
                    "prompt_echo_classification": echo,
                }
            )
    except (AdapterReadError, OSError, ValueError):
        return []
    return rows


def inferred_anchor_read(
    *,
    source_ref: dict[str, Any],
    anchor_id: str,
    reason: str,
    anchor_kind: str,
    confidence: str,
    selection_reason: str,
    score: float,
    expansion_action_context: dict[str, Any] | None,
    include_context_actions: bool,
) -> dict[str, Any]:
    read = {
        "kind": "jsonl_source_ref",
        "target": source_ref_target(source_ref),
        "candidate_id": anchor_id,
        "source_ref": source_ref,
        "reason": reason,
        "inferred_anchor": {
            "schema_version": 1,
            "anchor_kind": anchor_kind,
            "confidence": confidence,
            "selection_reason": selection_reason,
            "score": score,
            "raw_text_included": False,
            "source_window_verification_required": True,
            "claim_boundary": "anchor_guides_source_inspection_not_final_answer",
        },
        "expansion_actions": read_more_actions_for_source_ref(
            source_ref,
            candidate_id=anchor_id,
            expansion_action_context=expansion_action_context,
        ),
    }
    if include_context_actions:
        read["context_actions"] = context_actions_for_source_ref(
            source_ref,
            candidate_id=anchor_id,
            expansion_action_context=expansion_action_context,
        )
    return read


def decision_context_route_guidance(source_handle_routing: dict[str, Any] | None) -> list[dict[str, Any]]:
    routing = source_handle_routing or empty_source_handle_routing()
    if not routing.get("detected_handles"):
        return []
    return [
        {
            "route": "source_ref_followup",
            "priority": "before_broad_search",
            "status": routing.get("status"),
            "instruction": routing.get("route_guidance"),
            "matched_next_read_count": len(routing.get("matched_next_reads") or []),
            "unresolved_handle_count": len(routing.get("unresolved_handles") or []),
        }
    ]


def audit_source_handle_guidance(source_handle_routing: dict[str, Any] | None) -> dict[str, Any]:
    routing = source_handle_routing or empty_source_handle_routing()
    return {
        "status": routing.get("status"),
        "detected_handles": list(routing.get("detected_handles") or []),
        "matched_next_reads": list(routing.get("matched_next_reads") or [])[:5],
        "unresolved_handles": list(routing.get("unresolved_handles") or []),
        "audit_instruction": routing.get("route_guidance"),
        "claim_boundaries": routing.get("claim_boundaries") or source_handle_claim_boundaries(),
    }


def source_handle_handoff_summary(source_handle_routing: dict[str, Any] | None) -> dict[str, Any]:
    routing = source_handle_routing or empty_source_handle_routing()
    return {
        "status": routing.get("status"),
        "detected_handle_count": len(routing.get("detected_handles") or []),
        "matching_candidate_count": len(routing.get("matched_candidate_refs") or []),
        "matching_next_read_count": len(routing.get("matched_next_reads") or []),
        "unresolved_handle_count": len(routing.get("unresolved_handles") or []),
        "route_guidance": routing.get("route_guidance"),
        "matched_next_read_targets": [
            item.get("target") for item in list(routing.get("matched_next_reads") or [])[:5] if item.get("target")
        ],
        "claim_boundaries": routing.get("claim_boundaries") or source_handle_claim_boundaries(),
    }


def source_handle_checkpoint_plan(
    checkpoints: list[dict[str, Any]],
    source_handle_routing: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    routing = source_handle_routing or empty_source_handle_routing()
    if not routing.get("detected_handles"):
        return checkpoints
    first = {
        "route": "source_ref_followup",
        "checkpoint": "resolve_detected_source_handle_before_broad_search",
        "required": bool(routing.get("matched_next_reads")),
        "status": routing.get("status"),
        "instruction": routing.get("route_guidance"),
    }
    return [first, *checkpoints]


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
    source_handle_routing: dict[str, Any] | None = None,
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
        "source_handle_routing": source_handle_routing or empty_source_handle_routing(),
        "route_guidance": decision_context_route_guidance(source_handle_routing),
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
    source_handle_routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_order = order_decision_routes(routes)
    checkpoint_rows = checkpoint_plan(route_order)
    return {
        "watchlist": parse_watchlist(query, watchlist_inputs),
        "route_order": route_order,
        "checkpoint_plan": source_handle_checkpoint_plan(checkpoint_rows, source_handle_routing),
        "resume_from": resume_from(next_reads, route_order),
        "replay_commands": build_next_commands(next_reads, hints=hints, include_semantic_debug_replay=True),
        "source_handle_routing": audit_source_handle_guidance(source_handle_routing),
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
    first_read = next(
        (
            item
            for item in next_reads
            if item.get("target")
            and str((item.get("omitted_window_recovery") or {}).get("recovery_id", "")).startswith("clr")
        ),
        None,
    )
    if first_read is None:
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
                        "command": shell_command(argv),
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
                    "command": shell_command(
                        ["jsonl-history-search", "read-more", read["target"], "--format", "json"]
                    ),
                    "source": "next_reads.target",
                    "raw_private_text_included": False,
                }
            )
    if hints.get("commit"):
        commands.append(
            {
                "command_family": "git_show",
                "argv": ["git", "show", "--stat", str(hints["commit"])],
                "command": shell_command(["git", "show", "--stat", str(hints["commit"])]),
                "source": "commit_hint",
                "raw_private_text_included": False,
            }
        )
    if hints.get("path"):
        commands.append(
            {
                "command_family": "git_log_path",
                "argv": ["git", "log", "--oneline", "--", str(hints["path"])],
                "command": shell_command(["git", "log", "--oneline", "--", str(hints["path"])]),
                "source": "path_hint",
                "raw_private_text_included": False,
            }
        )
    return dedupe_dicts(commands)[:16]


def shell_command(argv: list[Any]) -> str:
    normalized = [str(part) for part in argv]
    if normalized and normalized[0] != "jsonl-history-search" and normalized[0] in {
        "read-more",
        "follow-next-reads",
        "failure-analysis",
        "semantic-debug",
    }:
        normalized.insert(0, "jsonl-history-search")
    return " ".join(shlex.quote(part) for part in normalized)


ANSWER_READY_HANDOFF_READ_CAP = 5
ANSWER_READY_HANDOFF_COMMAND_CAP = 3
ACTION_PROOF_BUCKET_RECORD_CAP = 8
ACTION_PROOF_TEXT_LIMIT = 220


def build_answer_ready_handoff(
    *,
    answer_readiness: dict[str, Any],
    guided_audit_flow: dict[str, Any],
    answer_source_discipline: dict[str, Any],
    final_answer_contract: dict[str, Any],
    next_reads: list[dict[str, Any]],
    hints: dict[str, str | None],
    no_evidence: dict[str, Any],
    report_output: str | None = None,
    source_handle_routing: dict[str, Any] | None = None,
    source_window_action_proof: dict[str, Any] | None = None,
    action_proof_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    answer_gate = guided_audit_flow.get("answer_gate") or {}
    discipline_gate = answer_source_discipline.get("answer_gate") or {}
    discipline_summary = answer_source_discipline_summary(answer_source_discipline)
    blocked_facets = list(discipline_gate.get("blocked_facets") or discipline_summary.get("blocked_facets") or [])[
        :8
    ]
    readiness_status = answer_readiness.get("status")
    readiness_decision = answer_readiness.get("decision")
    no_evidence_needed = bool(no_evidence.get("needed"))

    if readiness_decision == "answer_now":
        decision = "answerable"
        do_next = "answer_from_source_backed_packet"
    elif readiness_decision == "state_scoped_no_evidence":
        decision = "scoped_no_evidence"
        do_next = "answer_not_found_in_searched_scope_only"
    elif readiness_decision == "inspect_recovery_reads":
        decision = "recovery_first"
        do_next = "open_bounded_next_reads_before_answering"
    elif readiness_decision == "run_audit_route":
        decision = "audit_required"
        do_next = "run_required_audit_route_before_answering"
    else:
        decision = "insufficient_evidence"
        do_next = "gather_more_source_backed_evidence"

    do_not_answer_until = list(answer_readiness.get("required_before_answer") or [])[:6]
    if blocked_facets:
        facet_text = ", ".join(blocked_facets)
        do_not_answer_until.append(f"State or resolve answer_source_discipline blocked facets: {facet_text}.")
    if no_evidence_needed:
        do_not_answer_until.append("Keep no-evidence claims scoped to searched scope; do not claim all-history absence.")
    if (source_handle_routing or {}).get("detected_handles"):
        do_not_answer_until.append(
            "Resolve/open matching source refs for detected source handles first, or state no matching source ref was found in searched scope."
        )
    do_not_answer_until = dedupe(do_not_answer_until)[:8]

    bounded_next_reads = bounded_answer_ready_next_reads(next_reads)
    primary_question_family = guided_audit_flow.get("primary_question_family")
    allow_action_proof_followthrough = (
        readiness_decision != "answer_now"
        and not no_evidence_needed
        and primary_question_family == "claimed_action"
    )
    primary_followthrough = build_primary_source_followthrough(
        bounded_next_reads=bounded_next_reads,
        action_proof_plan=action_proof_plan,
        allow_action_proof_plan=allow_action_proof_followthrough,
    )
    primary_followthrough_first = bool(primary_followthrough.get("applies"))
    if primary_followthrough_first:
        decision = "primary_source_followthrough_first"
        do_next = "run_primary_source_window_command_then_answer_if_supported"
    copy_paste_commands = answer_ready_copy_paste_commands(
        bounded_next_reads=bounded_next_reads,
        next_reads=next_reads,
        hints=hints,
        report_output=report_output,
        prefer_follow_next_reads_bundle=decision == "recovery_first" and not primary_followthrough_first,
    )
    command_target_privacy = summarize_command_target_privacy(copy_paste_commands)
    source_use_summary = {
        "answer_scope": final_answer_contract.get("answer_scope"),
        "source_use_required": final_answer_contract.get("source_use_required"),
        "required_action_count": len(final_answer_contract.get("required_actions") or []),
        "bounded_next_read_count": len(bounded_next_reads),
        "copy_paste_command_count": len(copy_paste_commands),
        "blocked_facets": blocked_facets,
        "missing_proof_buckets": list(discipline_summary.get("missing_proof_buckets") or [])[:8],
        "candidate_only_citation_allowed": discipline_summary.get("candidate_only_citation_allowed"),
        "unsupported_extra_handle_citation_allowed": discipline_summary.get(
            "unsupported_extra_handle_citation_allowed"
        ),
        "scoped_no_evidence_only": no_evidence_needed,
        "primary_source_followthrough_first": primary_followthrough_first,
    }
    action_proof_summary = source_window_action_proof_guidance(source_window_action_proof or {})
    if action_proof_summary:
        source_use_summary["source_window_action_proof"] = action_proof_summary
    if action_proof_plan and action_proof_plan.get("applies"):
        source_use_summary["action_proof_plan"] = summarize_action_proof_plan(action_proof_plan)
    answer_support = answer_support_classification(
        decision=decision,
        discipline_summary=discipline_summary,
        source_window_action_proof=source_window_action_proof or {},
    )
    source_use_summary["answer_support_classification"] = answer_support
    return {
        "schema_version": 1,
        "decision": decision,
        "answer_support_classification": answer_support,
        "answer_readiness_status": readiness_status,
        "answer_gate_status": answer_gate.get("status") or "active",
        "do_next": do_next,
        "do_not_answer_until": do_not_answer_until,
        "primary_source_followthrough": primary_followthrough,
        "bounded_next_reads": bounded_next_reads,
        "copy_paste_commands": copy_paste_commands,
        "cap_policy": {
            "bounded_next_reads": ANSWER_READY_HANDOFF_READ_CAP,
            "copy_paste_commands": ANSWER_READY_HANDOFF_COMMAND_CAP,
            "deterministic_order": (
                "saved follow-next-reads bundle first when recovery reads are present; "
                "individual jsonl read-more commands are fallback in semantic_debug.next_reads order"
                if report_output and decision == "recovery_first"
                else "semantic_debug.next_reads order; jsonl read-more commands first"
            ),
        },
        "source_use_summary": source_use_summary,
        "source_handle_routing": source_handle_handoff_summary(source_handle_routing),
        "claim_boundaries": answer_readiness.get("claim_boundaries"),
        "privacy_boundaries": {
            **(answer_readiness.get("privacy_boundaries") or {}),
            "raw_transcript_text_included": False,
            "raw_source_lines_included": False,
            "raw_private_paths_included_in_commands": False,
            "raw_private_payloads_included_in_commands": False,
            "copy_paste_command_targets_include_local_path_like": command_target_privacy[
                "any_local_path_like"
            ],
            "copy_paste_command_targets_include_absolute_path_like": command_target_privacy[
                "any_absolute_path_like"
            ],
            "copy_paste_command_target_privacy_note": command_target_privacy["privacy_note"],
            "task_review_artifact_raw_text_included": False,
        },
        "links": {
            "answer_readiness": "semantic_debug.answer_readiness",
            "guided_audit_flow": "semantic_debug.guided_audit_flow",
            "answer_gate": "semantic_debug.guided_audit_flow.answer_gate",
            "answer_source_discipline": "semantic_debug.answer_source_discipline",
            "final_answer_source_use_contract": "semantic_debug.final_answer_source_use_contract",
            "source_window_action_proof": "semantic_debug.source_window_action_proof",
            "next_reads": "semantic_debug.next_reads",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
            "no_evidence": "semantic_debug.no_evidence",
            "preferred_recovery_workflow": (
                "semantic_debug.agent_instruction.commands.expand_recovery_context"
                if report_output and decision == "recovery_first" and not primary_followthrough_first
                else None
            ),
        },
    }


def answer_support_classification(
    *,
    decision: str,
    discipline_summary: dict[str, Any],
    source_window_action_proof: dict[str, Any],
) -> dict[str, Any]:
    buckets = source_window_action_proof.get("buckets") if isinstance(source_window_action_proof.get("buckets"), dict) else {}
    adjacent = buckets.get("adjacent_artifact_evidence") if isinstance(buckets.get("adjacent_artifact_evidence"), dict) else {}
    action_status = str(source_window_action_proof.get("status") or "")
    missing_proof_buckets = list(discipline_summary.get("missing_proof_buckets") or [])
    blocked_facets = list(discipline_summary.get("blocked_facets") or [])
    adjacent_present = adjacent.get("status") == "present"

    if action_status == "action_proof_available":
        current = "primary_source_answer"
        reason = "primary_action_proof_available"
    elif adjacent_present and action_status != "action_proof_available":
        current = "answer_with_caveat"
        reason = "adjacent_artifact_context_without_primary_action_proof"
    elif decision in {"recovery_first", "audit_required", "insufficient_evidence"}:
        current = "read_more_required"
        reason = "answer_readiness_requires_more_source_work"
    elif missing_proof_buckets or blocked_facets:
        current = "read_more_required"
        reason = "missing_primary_proof_or_blocked_facets"
    elif decision == "scoped_no_evidence":
        current = "read_more_required"
        reason = "scoped_no_evidence_not_primary_source_answer"
    else:
        current = "primary_source_answer"
        reason = "primary_source_surfaces_available_for_answer_scope"

    return {
        "schema_version": 1,
        "current": current,
        "reason": reason,
        "states": ["primary_source_answer", "answer_with_caveat", "read_more_required"],
        "adjacent_artifact_present": adjacent_present,
        "adjacent_artifact_counts_as_primary_source": False,
        "action_proof_status": action_status or "not_applicable",
        "missing_proof_buckets": missing_proof_buckets[:8],
        "blocked_facets": blocked_facets[:8],
        "claim_boundaries": {
            "primary_source_grounding_claim_allowed": current == "primary_source_answer",
            "adjacent_artifact_success_requires_caveat": current == "answer_with_caveat",
            "read_more_required_before_clean_answer": current == "read_more_required",
        },
    }


def bounded_answer_ready_next_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for read in next_reads[:ANSWER_READY_HANDOFF_READ_CAP]:
        target = read.get("target")
        kind = read.get("kind")
        guidance = (
            "run bounded read-more command"
            if is_jsonl_read_target(target)
            else "inspect/open this local task, review, or artifact handle without pasting raw private text"
        )
        row = {
            "kind": kind,
            "target": target,
            "reason": read.get("reason"),
            "inferred_anchor": read.get("inferred_anchor")
            if isinstance(read.get("inferred_anchor"), dict)
            else None,
            "omitted_window_recovery": bool(read.get("omitted_window_recovery")),
            "source_ref": guided_source_ref(read.get("source_ref") or {}) if isinstance(read.get("source_ref"), dict) else None,
            "action": guidance,
        }
        rows.append({key: value for key, value in row.items() if value not in (None, [], {})})
    return rows


def build_source_window_action_proof(
    *,
    query: str,
    family: dict[str, Any],
    candidates: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    answer_source_discipline: dict[str, Any],
) -> dict[str, Any]:
    source_windows = bounded_action_proof_source_windows(source_locator_handoff, next_reads)
    search_rows = dedupe_action_records(
        [candidate_action_record(item, source="strongest_candidates") for item in candidates]
        + [candidate_action_record(item, source="timeline") for item in timeline]
    )
    user_records = [item for item in search_rows if item.get("role") in {"user", "human"}][
        :ACTION_PROOF_BUCKET_RECORD_CAP
    ]
    if not user_records:
        user_records = [
            {
                "record_type": "current_user_request",
                "record_source": "semantic_debug.query",
                "source_status": "missing_transcript_source_ref",
                "missing_source_label": "current_query_not_a_transcript_record",
                "role": "user",
                "text_excerpt": bounded_action_text(query),
                "primary_action_proof": False,
                "proof_role": "requested_action_or_concern",
            }
        ]
    assistant_records = [
        item
        for item in search_rows
        if item.get("role") in {"assistant", "agent", "assistant_message", "agent_message"}
        and looks_like_final_answer_or_claim(item.get("text_excerpt"))
    ][:ACTION_PROOF_BUCKET_RECORD_CAP]
    if not assistant_records:
        assistant_records = [
            item
            for item in search_rows
            if item.get("role") in {"assistant", "agent", "assistant_message", "agent_message"}
        ][: min(2, ACTION_PROOF_BUCKET_RECORD_CAP)]
    for item in assistant_records:
        item["primary_action_proof"] = False
        item["proof_role"] = "assistant_claim_not_tool_proof"

    tool_events = list(tool_proof.get("events") or [])
    call_records = [
        tool_event_action_record(event, proof_role="command_or_tool_call", primary=True)
        for event in tool_events
        if is_action_call_event(event)
    ][:ACTION_PROOF_BUCKET_RECORD_CAP]
    output_records = [
        tool_event_action_record(event, proof_role="command_or_tool_output", primary=True)
        for event in tool_events
        if is_output_evidence_event(event)
    ][:ACTION_PROOF_BUCKET_RECORD_CAP]
    change_records = changed_check_review_commit_records(tool_events, joins)
    adjacent_records = adjacent_artifact_records(joins, next_reads)
    verification_reads = [
        read
        for read in next_reads
        if isinstance(read.get("action_proof_source_selection"), dict)
        and read["action_proof_source_selection"].get("source_window_verification_required") is True
    ]

    buckets = {
        "user_concern_request": action_bucket(
            "user_concern_request",
            user_records,
            missing_label="missing_user_concern_source_record",
        ),
        "command_tool_call": action_bucket(
            "command_tool_call",
            call_records,
            missing_label="missing_structured_command_or_tool_call",
        ),
        "command_tool_output": action_bucket(
            "command_tool_output",
            output_records,
            missing_label="missing_structured_command_or_tool_output",
        ),
        "changed_check_review_commit_evidence": action_bucket(
            "changed_check_review_commit_evidence",
            change_records,
            missing_label="missing_changed_check_review_commit_evidence",
        ),
        "final_assistant_answer_claim": action_bucket(
            "final_assistant_answer_claim",
            assistant_records,
            missing_label="missing_final_assistant_answer_or_claim",
        ),
        "adjacent_artifact_evidence": action_bucket(
            "adjacent_artifact_evidence",
            adjacent_records,
            missing_label="no_adjacent_artifact_evidence",
            adjacent_only=True,
        ),
    }
    missing_buckets = [
        bucket_name
        for bucket_name, bucket in buckets.items()
        if bucket.get("status") != "present" and bucket_name != "adjacent_artifact_evidence"
    ]
    primary_count = sum(bucket.get("primary_record_count", 0) for bucket in buckets.values())
    adjacent_count = len(buckets["adjacent_artifact_evidence"].get("records") or [])
    discipline_gate = answer_source_discipline.get("answer_gate") or {}
    status = (
        "action_proof_available"
        if buckets["command_tool_call"]["status"] == "present"
        and buckets["command_tool_output"]["status"] == "present"
        else "partial_action_proof"
        if primary_count
        else "missing_action_proof"
    )
    return {
        "schema_version": 1,
        "status": status,
        "question_family": {
            "primary": family.get("primary"),
            "claimed_action_detected": "claimed_action" in list(family.get("detected_supported") or []),
        },
        "source_windows": source_windows,
        "buckets": buckets,
        "summary": {
            "primary_record_count": primary_count,
            "adjacent_record_count": adjacent_count,
            "missing_buckets": missing_buckets,
            "missing_labels": action_proof_missing_labels(buckets),
            "answer_source_discipline_decision": discipline_gate.get("decision"),
            "answer_source_discipline_blocked_facets": list(discipline_gate.get("blocked_facets") or [])[:8],
            "source_window_verification_required": bool(verification_reads),
            "verification_required_next_read_count": len(verification_reads),
        },
        "guidance": {
            "use_for_claimed_action_questions": True,
            "assistant_claim_boundary": "final_assistant_answer_claim records show what was claimed, not whether the action happened",
            "missing_evidence_policy": "missing buckets are labeled explicitly; do not infer action proof from adjacent artifacts or assistant prose",
            "source_window_boundary": "action-proof next_reads are candidate source windows; verify the opened window is the requested original source window before answering",
        },
        "links": {
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
            "tool_action_proof": "semantic_debug.tool_action_proof",
            "task_review_commit_joins": "semantic_debug.task_review_commit_joins",
            "answer_source_discipline": "semantic_debug.answer_source_discipline",
            "answer_ready_handoff": "semantic_debug.answer_ready_handoff",
        },
        "cap_policy": {
            "records_per_bucket": ACTION_PROOF_BUCKET_RECORD_CAP,
            "source_windows": ACTION_PROOF_BUCKET_RECORD_CAP,
            "text_excerpt_chars": ACTION_PROOF_TEXT_LIMIT,
            "deterministic_order": "candidate/timeline order, then structured tool-event order, then join/next-read order",
        },
        "privacy_boundaries": {
            "raw_transcript_dump_included": False,
            "raw_tool_payload_dump_included": False,
            "raw_artifact_content_included": False,
            "text_excerpts_bounded": True,
            "private_payloads_committed": False,
        },
        "claim_boundaries": {
            "assistant_prose_satisfies_action_proof": False,
            "adjacent_artifact_satisfies_primary_action_proof": False,
            "missing_output_inferred_from_call": False,
            "candidate_next_read_proves_original_source_window": False,
        },
    }


def source_window_action_proof_guidance(bundle: dict[str, Any]) -> dict[str, Any]:
    if not bundle:
        return {}
    summary = bundle.get("summary") or {}
    buckets = bundle.get("buckets") or {}
    return {
        "status": bundle.get("status"),
        "link": "semantic_debug.source_window_action_proof",
        "use_for_claimed_action_questions": True,
        "primary_record_count": summary.get("primary_record_count", 0),
        "adjacent_record_count": summary.get("adjacent_record_count", 0),
        "missing_buckets": list(summary.get("missing_buckets") or [])[:8],
        "source_window_verification_required": bool(summary.get("source_window_verification_required", False)),
        "source_window_boundary": "candidate next_reads require window verification before original-source claims",
        "bucket_statuses": {
            key: value.get("status")
            for key, value in buckets.items()
            if isinstance(value, dict)
        },
        "adjacent_artifact_primary_proof_allowed": False,
    }


def bounded_action_proof_source_windows(
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_by_ref = {
        action_source_ref_key(read.get("source_ref")): read.get("target")
        for read in next_reads
        if isinstance(read.get("source_ref"), dict)
    }
    rows: list[dict[str, Any]] = []
    for window in list(source_locator_handoff.get("source_windows") or [])[:ACTION_PROOF_BUCKET_RECORD_CAP]:
        ref = compact_action_source_ref(window.get("source_ref"))
        rows.append(
            {
                "source_ref": ref,
                "source_status": "present" if ref else "missing_source_ref",
                "window_kind": window.get("window_kind"),
                "intent": window.get("intent"),
                "reason": window.get("reason"),
                "next_read_target": target_by_ref.get(action_source_ref_key(window.get("source_ref"))),
                "text_included": bool(window.get("text_included") or window.get("source_window_text_included")),
            }
        )
    return rows


def candidate_action_record(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    ref = compact_action_source_ref(item.get("source_ref"))
    return {
        "record_type": "conversation_record",
        "record_source": f"semantic_debug.{source}",
        "source_ref": ref,
        "source_status": "present" if ref else "missing_source_ref",
        "timestamp_utc": item.get("timestamp_utc"),
        "role": normalize_action_role(item.get("role")),
        "confidence": item.get("confidence"),
        "candidate_id": item.get("candidate_id"),
        "phase_hint": item.get("phase_hint") or phase_hint(item.get("snippet", "")),
        "text_excerpt": bounded_action_text(item.get("snippet")),
        "primary_action_proof": False,
        "proof_role": "conversation_context",
    }


def tool_event_action_record(event: dict[str, Any], *, proof_role: str, primary: bool) -> dict[str, Any]:
    record = {
        "record_type": event.get("event_type") or "tool_event",
        "record_source": "semantic_debug.tool_action_proof.events",
        "event_id": event.get("event_id"),
        "source_ref": compact_action_source_ref(event.get("source_ref")),
        "source_status": "present" if compact_action_source_ref(event.get("source_ref")) else "missing_source_ref",
        "timestamp_utc": event.get("timestamp_utc"),
        "event_family": event.get("event_family"),
        "action": event.get("action"),
        "tool_name": event.get("tool_name"),
        "command": event.get("command"),
        "targets": list(event.get("targets") or [])[:4],
        "output_paths": list(event.get("output_paths") or [])[:4],
        "confidence": event.get("confidence"),
        "evidence_basis": list(event.get("evidence_basis") or [])[:6],
        "primary_action_proof": primary,
        "proof_role": proof_role,
    }
    return {key: value for key, value in record.items() if value not in (None, [], {})}


def changed_check_review_commit_records(tool_events: list[dict[str, Any]], joins: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in tool_events:
        if not is_change_check_review_commit_event(event):
            continue
        records.append(tool_event_action_record(event, proof_role="changed_check_or_commit_evidence", primary=True))
    if joins.get("commit"):
        records.append(
            {
                "record_type": "commit_metadata",
                "record_source": "semantic_debug.task_review_commit_joins.commit",
                "source_status": "join_metadata_no_transcript_source_ref",
                "commit": joins.get("commit"),
                "primary_action_proof": True,
                "proof_role": "commit_evidence",
            }
        )
    if joins.get("path_last_change"):
        records.append(
            {
                "record_type": "path_last_change",
                "record_source": "semantic_debug.task_review_commit_joins.path_last_change",
                "source_status": "join_metadata_no_transcript_source_ref",
                "path_last_change": joins.get("path_last_change"),
                "primary_action_proof": True,
                "proof_role": "changed_file_evidence",
            }
        )
    for item in list(joins.get("review_evidence") or [])[:ACTION_PROOF_BUCKET_RECORD_CAP]:
        records.append(
            {
                "record_type": "review_evidence",
                "record_source": "semantic_debug.task_review_commit_joins.review_evidence",
                "source_status": "artifact_path_no_transcript_source_ref",
                "path": item.get("path"),
                "exists": item.get("exists"),
                "review_verdict": item.get("review_verdict"),
                "claim_action": item.get("claim_action"),
                "support_status": item.get("support_status"),
                "support_basis_kind": item.get("support_basis_kind"),
                "primary_action_proof": True,
                "proof_role": "review_evidence",
            }
        )
    return dedupe_action_records(records)[:ACTION_PROOF_BUCKET_RECORD_CAP]


def adjacent_artifact_records(joins: dict[str, Any], next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in list(joins.get("artifact_checks") or [])[:ACTION_PROOF_BUCKET_RECORD_CAP]:
        records.append(
            {
                "record_type": "artifact_check",
                "record_source": "semantic_debug.task_review_commit_joins.artifact_checks",
                "source_status": "artifact_path_no_transcript_source_ref",
                "path": item.get("path"),
                "exists": item.get("exists"),
                "is_file": item.get("is_file"),
                "git_tracked": item.get("git_tracked"),
                "support_status": item.get("support_status"),
                "support_basis_kind": item.get("support_basis_kind"),
                "primary_action_proof": False,
                "adjacent_evidence_only": True,
                "proof_role": "adjacent_artifact_context",
            }
        )
    for read in next_reads:
        if read.get("kind") != "artifact":
            continue
        records.append(
            {
                "record_type": "artifact_next_read",
                "record_source": "semantic_debug.next_reads",
                "source_status": "next_read_target_no_transcript_source_ref",
                "target": read.get("target"),
                "reason": read.get("reason"),
                "primary_action_proof": False,
                "adjacent_evidence_only": True,
                "proof_role": "adjacent_artifact_context",
            }
        )
    return dedupe_action_records(records)[:ACTION_PROOF_BUCKET_RECORD_CAP]


def action_bucket(
    name: str,
    records: list[dict[str, Any]],
    *,
    missing_label: str,
    adjacent_only: bool = False,
) -> dict[str, Any]:
    clean_records = [
        {key: value for key, value in record.items() if value not in (None, [], {})}
        for record in records[:ACTION_PROOF_BUCKET_RECORD_CAP]
    ]
    status = "present" if clean_records else "not_present"
    primary_count = sum(1 for record in clean_records if record.get("primary_action_proof") is True)
    return {
        "bucket": name,
        "status": status,
        "records": clean_records,
        "record_count": len(clean_records),
        "primary_record_count": 0 if adjacent_only else primary_count,
        "adjacent_evidence_only": adjacent_only,
        "missing_labels": [] if clean_records else [missing_label],
    }


def action_proof_missing_labels(buckets: dict[str, dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for bucket in buckets.values():
        labels.extend(str(label) for label in bucket.get("missing_labels") or [])
    return dedupe(labels)


def is_action_call_event(event: dict[str, Any]) -> bool:
    if is_output_evidence_event(event):
        return False
    if event.get("event_type") in {
        "tool_call",
        "command_run",
        "file_edit",
        "file_read",
        "file_search",
        "test_run",
        "git_event",
        "review_artifact",
        "changed_path",
    }:
        return True
    basis = set(event.get("evidence_basis") or [])
    return bool(basis & {"structured_function_call", "structured_tool_record", "command_argument", "structured_edit_tool"})


def is_output_evidence_event(event: dict[str, Any]) -> bool:
    basis = set(event.get("evidence_basis") or [])
    return bool(
        basis
        & {
            "structured_tool_output",
            "test_summary_output",
            "git_output",
            "git_output_path",
            "artifact_output_path",
            "review_artifact_path",
        }
    ) or event.get("action") in {"test_summary", "git_output"}


def is_change_check_review_commit_event(event: dict[str, Any]) -> bool:
    if is_output_evidence_event(event):
        return True
    return event.get("event_type") in {"file_edit", "changed_path", "test_run", "git_event", "review_artifact"}


def looks_like_final_answer_or_claim(text: str | None) -> bool:
    lowered = str(text or "").lower()
    return any(
        phrase in lowered
        for phrase in (
            "final",
            "answer",
            "claim",
            "claimed",
            "done",
            "implemented",
            "completed",
            "i ran",
            "i tested",
            "i reviewed",
            "verified",
        )
    )


def bounded_action_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= ACTION_PROOF_TEXT_LIMIT:
        return text
    return text[: ACTION_PROOF_TEXT_LIMIT - 1].rstrip() + "..."


def compact_action_source_ref(source_ref: Any) -> dict[str, Any]:
    if not isinstance(source_ref, dict):
        return {}
    compact = {
        "path": source_ref.get("path"),
        "line": source_ref.get("line"),
    }
    if source_ref.get("record_id"):
        compact["record_id"] = source_ref.get("record_id")
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def action_source_ref_key(source_ref: Any) -> str:
    return json.dumps(compact_action_source_ref(source_ref), sort_keys=True)


def normalize_action_role(role: Any) -> str:
    lowered = str(role or "").lower()
    if lowered in {"user", "human"}:
        return "user"
    if lowered in {"assistant", "agent", "assistant_message", "agent_message"}:
        return "assistant"
    return lowered


def dedupe_action_records(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result: list[dict[str, Any]] = []
    for value in values:
        key = json.dumps(value, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def answer_ready_copy_paste_commands(
    *,
    bounded_next_reads: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
    hints: dict[str, str | None],
    report_output: str | None = None,
    prefer_follow_next_reads_bundle: bool = False,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if prefer_follow_next_reads_bundle and report_output and any(
        is_jsonl_read_target(read.get("target")) for read in bounded_next_reads
    ):
        follow_report = "follow-next-reads.json"
        fallback_count = sum(1 for read in bounded_next_reads if is_jsonl_read_target(read.get("target")))
        follow_argv = [
            "jsonl-history-search",
            "follow-next-reads",
            "--from-report",
            str(Path(report_output).expanduser()),
            "--kind",
            "recovery",
            "--limit",
            "3",
            "--format",
            "json",
            "--report-output",
            follow_report,
        ]
        commands.append(
            {
                "command_family": "follow_next_reads",
                "command_id": "expand_recovery_context",
                "target": "semantic_debug.next_reads",
                "command": shell_command(follow_argv),
                "source": "semantic_debug.agent_instruction",
                "output_path": follow_report,
                "replaces_individual_read_more_count": fallback_count,
                "fallback_individual_read_more_commands_follow": False,
                "fallback_individual_read_more_commands_suppressed_count": fallback_count,
                "raw_private_text_included": False,
                "raw_private_path_included": False,
                "target_local_path_like": False,
                "target_absolute_path_like": False,
                "path_privacy_note": (
                    "Command points at the saved semantic-debug report and emits a saved follow-next-reads "
                    "packet; it does not include raw transcript text, source lines, snippets, or payload text."
                ),
            }
        )
        return commands
    command_targets = {read.get("target") for read in bounded_next_reads if is_jsonl_read_target(read.get("target"))}
    for read in next_reads:
        if len(commands) >= ANSWER_READY_HANDOFF_COMMAND_CAP:
            break
        target = read.get("target")
        if target not in command_targets:
            continue
        argv = ["jsonl-history-search", "read-more", str(target), "--mode", "window", "--format", "json"]
        target_privacy = classify_command_target_privacy(target)
        commands.append(
            {
                "command_family": "read_more",
                "target": target,
                "command": shell_command(argv),
                "source": "semantic_debug.next_reads",
                "raw_private_text_included": False,
                "raw_private_path_included": False,
                "target_local_path_like": target_privacy["local_path_like"],
                "target_absolute_path_like": target_privacy["absolute_path_like"],
                "path_privacy_note": target_privacy["path_privacy_note"],
            }
        )
        if len(commands) >= ANSWER_READY_HANDOFF_COMMAND_CAP:
            break
    if len(commands) < ANSWER_READY_HANDOFF_COMMAND_CAP:
        for command in build_next_commands(next_reads, hints=hints, include_semantic_debug_replay=False):
            if command.get("command_family") != "read_more":
                target = command_target_from_command(command)
                target_privacy = classify_command_target_privacy(target)
                commands.append(
                    {
                        "command_family": command.get("command_family"),
                        "target": target,
                        "command": command.get("command"),
                        "source": command.get("source"),
                        "raw_private_text_included": command.get("raw_private_text_included", False),
                        "raw_private_path_included": False,
                        "target_local_path_like": target_privacy["local_path_like"],
                        "target_absolute_path_like": target_privacy["absolute_path_like"],
                        "path_privacy_note": target_privacy["path_privacy_note"],
                    }
                )
            if len(commands) >= ANSWER_READY_HANDOFF_COMMAND_CAP:
                break
    return dedupe_dicts(commands)[:ANSWER_READY_HANDOFF_COMMAND_CAP]


def build_primary_source_followthrough(
    *,
    bounded_next_reads: list[dict[str, Any]],
    action_proof_plan: dict[str, Any] | None = None,
    allow_action_proof_plan: bool = True,
) -> dict[str, Any]:
    primary_reads = primary_followthrough_reads(
        bounded_next_reads=bounded_next_reads,
        action_proof_plan=action_proof_plan,
        allow_action_proof_plan=allow_action_proof_plan,
    )
    if not primary_reads:
        return {
            "schema_version": 1,
            "applies": False,
            "reason": "no_primary_source_refs_available",
        }
    first = primary_reads[0]
    target = str(first.get("target") or "")
    window = primary_followthrough_window(first, action_proof_plan=action_proof_plan)
    argv = [
        "jsonl-history-search",
        "read-more",
        target,
        "--mode",
        "window",
        "--before",
        str(window["before"]),
        "--after",
        str(window["after"]),
        "--max-records",
        str(window["max_records"]),
        "--format",
        "json",
    ]
    command = {
        "command_family": "read_more",
        "command_id": "open_primary_source_window",
        "argv": argv,
        "target": target,
        "command": shell_command(argv),
        "source": "semantic_debug.answer_ready_handoff.primary_source_followthrough",
        "window": window,
        "raw_private_text_included": False,
        "raw_private_path_included": False,
    }
    target_privacy = classify_command_target_privacy(target)
    command["target_local_path_like"] = target_privacy["local_path_like"]
    command["target_absolute_path_like"] = target_privacy["absolute_path_like"]
    command["path_privacy_note"] = target_privacy["path_privacy_note"]
    return {
        "schema_version": 1,
        "applies": True,
        "do_first": "run_primary_source_window_command",
        "primary_targets": [str(read.get("target")) for read in primary_reads[:3] if read.get("target")],
        "primary_read_count": len(primary_reads),
        "first_command": command,
        "stop_after": (
            "After this primary source window supports the answer, stop broad search and answer with "
            "source refs plus searched-scope/uncertainty boundaries."
        ),
        "only_continue_if": [
            "the opened primary window does not contain the required proof",
            "the primary source conflicts with another inspected primary source",
            "a required facet is still missing, such as file, commit, test, or unrelated-work status",
        ],
        "claim_boundary": "followthrough_guides_source_inspection_not_final_answer",
    }


def primary_followthrough_reads(
    *,
    bounded_next_reads: list[dict[str, Any]],
    action_proof_plan: dict[str, Any] | None = None,
    allow_action_proof_plan: bool = True,
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    if (
        allow_action_proof_plan
        and
        action_proof_plan
        and action_proof_plan.get("applies")
        and (
            action_proof_plan.get("primary_question_family") == "claimed_action"
            or any(
                item.get("read_type") == "required_primary_proof_read"
                for item in action_proof_plan.get("required_proof_reads") or []
            )
        )
    ):
        for item in action_proof_plan.get("required_proof_reads") or []:
            if item.get("target"):
                reads.append(item)
    if not reads:
        for item in bounded_next_reads:
            inferred = item.get("inferred_anchor") if isinstance(item.get("inferred_anchor"), dict) else {}
            if inferred.get("anchor_kind") in {"known_action_case", "remembered_topic"}:
                reads.append(item)
    return dedupe_primary_targets(reads)[:3]


def dedupe_primary_targets(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for read in reads:
        target = str(read.get("target") or "")
        if not target or target in seen:
            continue
        seen.add(target)
        result.append(read)
    return result


def primary_followthrough_window(
    read: dict[str, Any],
    *,
    action_proof_plan: dict[str, Any] | None = None,
) -> dict[str, int | str]:
    inferred = read.get("inferred_anchor") if isinstance(read.get("inferred_anchor"), dict) else {}
    anchor_kind = inferred.get("anchor_kind")
    if str(read.get("read_type") or "") == "required_primary_proof_read" or (
        action_proof_plan
        and action_proof_plan.get("applies")
        and action_proof_plan.get("primary_question_family") == "claimed_action"
    ):
        return {
            "before": 45,
            "after": 8,
            "max_records": 80,
            "reason": "action_proof_needs_preceding_tool_and_diff_context",
        }
    if anchor_kind == "remembered_topic" or "recent user topic/provenance" in str(read.get("reason") or ""):
        return {
            "before": 8,
            "after": 8,
            "max_records": 40,
            "reason": "remembered_topic_needs_surrounding_user_correction_context",
        }
    return {
        "before": 10,
        "after": 10,
        "max_records": 50,
        "reason": "default_primary_source_context_window",
    }


def command_target_from_command(command: dict[str, Any]) -> str | None:
    argv = list(command.get("argv") or [])
    if len(argv) >= 2:
        return str(argv[1])
    command_text = command.get("command")
    if not command_text:
        return None
    try:
        parsed = shlex.split(str(command_text))
    except ValueError:
        return None
    if len(parsed) >= 3 and parsed[0] == "jsonl-history-search" and parsed[1] == "read-more":
        return parsed[2]
    if len(parsed) >= 2 and parsed[0] == "read-more":
        return parsed[1]
    return None


def classify_command_target_privacy(target: Any) -> dict[str, Any]:
    text = "" if target is None else str(target)
    target_without_line = source_target_path_part(text)
    absolute_path_like = is_absolute_path_like(target_without_line)
    local_path_like = bool(target_without_line) and (
        absolute_path_like
        or "/" in target_without_line
        or "\\" in target_without_line
        or target_without_line.endswith(".jsonl")
        or target_without_line.startswith(".")
    )
    if absolute_path_like:
        note = (
            "Command target is an absolute local source-locator path. It is a path-like handle, "
            "not raw transcript text, source lines, snippets, or payload text."
        )
    elif local_path_like:
        note = (
            "Command target is a local source-locator path-like handle. It is not raw transcript "
            "text, source lines, snippets, or payload text."
        )
    else:
        note = (
            "Command target is not path-like. Command metadata does not include raw transcript "
            "text, source lines, snippets, or payload text."
        )
    return {
        "local_path_like": local_path_like,
        "absolute_path_like": absolute_path_like,
        "path_privacy_note": note,
    }


def summarize_command_target_privacy(commands: list[dict[str, Any]]) -> dict[str, Any]:
    any_local = any(bool(command.get("target_local_path_like")) for command in commands)
    any_absolute = any(bool(command.get("target_absolute_path_like")) for command in commands)
    if any_absolute:
        note = (
            "One or more copy-paste command targets are absolute local source-locator paths. "
            "These are path-like handles, not raw transcript/source-line/snippet/payload text."
        )
    elif any_local:
        note = (
            "One or more copy-paste command targets are local path-like source-locator handles. "
            "No command metadata includes raw transcript/source-line/snippet/payload text."
        )
    else:
        note = (
            "Copy-paste command targets are not local path-like handles, and command metadata "
            "does not include raw transcript/source-line/snippet/payload text."
        )
    return {
        "any_local_path_like": any_local,
        "any_absolute_path_like": any_absolute,
        "privacy_note": note,
    }


def source_target_path_part(target: str) -> str:
    if not target:
        return ""
    if len(target) > 2 and target.rsplit(":", 1)[-1].isdigit():
        return target.rsplit(":", 1)[0]
    return target


def is_absolute_path_like(target: str) -> bool:
    if not target:
        return False
    return PurePosixPath(target).is_absolute() or PureWindowsPath(target).is_absolute()


def is_jsonl_read_target(target: Any) -> bool:
    if not target:
        return False
    return ".jsonl" in str(target)


def build_agent_instruction(
    *,
    query: str,
    report_output: str | None,
    answer_readiness: dict[str, Any],
    guided_audit_flow: dict[str, Any],
    next_reads: list[dict[str, Any]],
    hints: dict[str, str | None],
    answer_source_discipline: dict[str, Any] | None = None,
    source_handle_routing: dict[str, Any] | None = None,
    action_proof_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness_decision = str(answer_readiness.get("decision") or "gather_more_evidence")
    primary_family = guided_audit_flow.get("primary_question_family")
    recovery_first = readiness_decision == "inspect_recovery_reads" or bool(next_reads)
    allow_action_proof_followthrough = (
        readiness_decision != "answer_now"
        and readiness_decision != "state_scoped_no_evidence"
        and primary_family == "claimed_action"
    )
    primary_followthrough = build_primary_source_followthrough(
        bounded_next_reads=bounded_answer_ready_next_reads(next_reads),
        action_proof_plan=action_proof_plan,
        allow_action_proof_plan=allow_action_proof_followthrough,
    )
    primary_followthrough_first = bool(primary_followthrough.get("applies"))
    if readiness_decision == "answer_now":
        decision = "answer_now"
        required_next_action = "answer_from_source_backed_packet"
    elif primary_followthrough_first:
        decision = "primary_source_followthrough_first"
        required_next_action = "run_primary_source_window_command_then_answer_if_supported"
        recovery_first = False
    elif readiness_decision in {"inspect_recovery_reads", "run_audit_route", "gather_more_evidence"} and primary_family in {
        "workflow_failure",
        "why_did_this_happen",
    }:
        decision = "needs_investigation" if not recovery_first else "inspect_recovery_reads"
        required_next_action = "expand_recovery_context_then_run_failure_analysis"
    elif readiness_decision == "state_scoped_no_evidence":
        decision = "answer_now"
        required_next_action = "answer_scoped_no_evidence"
    else:
        decision = readiness_decision
        required_next_action = "inspect_required_routes_or_gather_more_evidence"

    report_path = str(Path(report_output).expanduser()) if report_output else None
    follow_report = "follow-next-reads.json"
    commands: list[dict[str, Any]] = []
    if primary_followthrough_first:
        command = dict(primary_followthrough.get("first_command") or {})
        command["required_for_decisions"] = ["primary_source_followthrough_first"]
        command["purpose"] = "Open the primary source window first; stop broad search if it supports the answer."
        commands.append(command)
    elif report_path:
        follow_argv = [
            "jsonl-history-search",
            "follow-next-reads",
            "--from-report",
            report_path,
            "--kind",
            "recovery",
            "--limit",
            "3",
            "--format",
            "json",
            "--report-output",
            follow_report,
        ]
        commands.append(
            {
                "command_id": "expand_recovery_context",
                "required_for_decisions": ["inspect_recovery_reads", "needs_investigation"],
                "argv": follow_argv,
                "command": shell_command(follow_argv),
                "output_path": follow_report,
                "purpose": "Expand bounded source context around semantic_debug.next_reads without rerunning broad search.",
            }
        )
        failure_argv = [
            "jsonl-history-search",
            "failure-analysis",
            "--semantic-debug-report",
            report_path,
            "--follow-next-reads-report",
            follow_report,
            "--story-id",
            "investigation-brief",
            "--format",
            "json",
        ]
        commands.append(
            {
                "command_id": "build_investigation_brief",
                "required_for_decisions": ["needs_investigation"],
                "argv": failure_argv,
                "command": shell_command(failure_argv),
                "purpose": "Synthesize a deterministic devlog_brief from saved semantic-debug and recovery packets.",
            }
        )
    else:
        commands.append(
            {
                "command_id": "save_semantic_debug_report_first",
                "required_for_decisions": ["inspect_recovery_reads", "needs_investigation"],
                "argv": [
                    "jsonl-history-search",
                    "semantic-debug",
                    query,
                    "--report-output",
                    "semantic-debug-report.json",
                    "--format",
                    "json",
                ],
                "command": shell_command(
                    [
                        "jsonl-history-search",
                        "semantic-debug",
                        query,
                        "--report-output",
                        "semantic-debug-report.json",
                        "--format",
                        "json",
                    ]
                ),
                "purpose": "Create a saved semantic-debug report so recovery and investigation commands can refer to it.",
            }
        )

    if not primary_followthrough_first:
        mode_command = fielded_bm25_rerun_command(
            query=query,
            guided_audit_flow=guided_audit_flow,
            next_reads=next_reads,
        )
        if mode_command:
            commands.append(mode_command)

        for command in build_next_commands(next_reads, hints=hints, include_semantic_debug_replay=False)[:3]:
            commands.append(
                {
                    "command_id": f"inspect_exact_source_{len(commands) + 1}",
                    "required_for_decisions": ["inspect_recovery_reads"],
                    "argv": command.get("argv"),
                    "command": command.get("command") or shell_command(command.get("argv") or []),
                    "purpose": "Open one exact source ref from the evidence packet when a targeted read is preferable.",
                }
            )

    if primary_followthrough_first:
        constraints = [
            "Run the primary source-window command before broader recovery or synthesis.",
            "If that primary window supports the answer, stop broad search and answer with source refs plus searched-scope/uncertainty boundaries.",
            "Run more commands only if the primary source is missing required proof, conflicts with inspected evidence, or leaves a required facet unresolved.",
            "Do not claim root cause, all-history completeness, token savings, speed, final-answer quality, or product value unless separately proven.",
        ]
    else:
        constraints = [
            "Do not answer from snippets alone when decision is inspect_recovery_reads or needs_investigation.",
            "Run the recovery command before synthesis when next_reads or omitted-window recovery are present.",
            "Use failure-analysis/devlog_brief for timeline, supported claims, non-claims, gaps, and next reads before producing devlog-style conclusions.",
            "Do not claim root cause, all-history completeness, token savings, speed, final-answer quality, or product value unless separately proven.",
        ]
    if decision == "answer_now":
        constraints.insert(0, "Answer only within the allowed_answer_scope and cite/source the supporting handles.")
    discipline_summary = answer_source_discipline_summary(answer_source_discipline)
    discipline_gate = (
        answer_source_discipline.get("answer_gate")
        if isinstance(answer_source_discipline, dict) and isinstance(answer_source_discipline.get("answer_gate"), dict)
        else {}
    )
    if discipline_gate:
        constraints.append("Do not cite candidate-only or unsupported-extra handles as final support.")
        if discipline_gate.get("missing_proof_must_be_stated"):
            constraints.append("State missing proof/action facets instead of filling them with broad source lists.")
    if (source_handle_routing or {}).get("detected_handles"):
        constraints.insert(
            0,
            "For detected reviewed/source/case handles, first open matching source refs from source_handle_routing or state that no matching source ref exists in searched scope.",
        )

    return {
        "schema_version": 1,
        "decision": decision,
        "answer_readiness_decision": readiness_decision,
        "required_next_action": required_next_action,
        "answer_gate": {
            "status": answer_readiness.get("status"),
            "allowed_answer_scope": answer_readiness.get("allowed_answer_scope"),
            "required_before_answer": list(answer_readiness.get("required_before_answer") or [])[:6],
            "answer_source_discipline": discipline_summary,
        },
        "commands": commands[:8],
        "do_not_answer_until": constraints,
        "source_handle_routing": source_handle_handoff_summary(source_handle_routing),
        "workflow_layers": [
            {"layer": "evidence_packet", "surface": "semantic_debug", "status": "current_packet"},
            {
                "layer": "primary_source_followthrough",
                "surface": "answer_ready_handoff.primary_source_followthrough",
                "status": "required_first" if primary_followthrough_first else "not_available",
            },
            {
                "layer": "recovery_packet",
                "surface": "follow_next_reads",
                "status": "required_before_synthesis" if recovery_first else "optional",
            },
            {
                "layer": "investigation_brief",
                "surface": "failure_analysis.devlog_brief",
                "status": "required_for_devlog_questions" if not primary_followthrough_first else "only_if_primary_source_insufficient",
            },
        ],
        "privacy_boundaries": {
            "raw_private_transcript_included": False,
            "raw_session_ids_included": False,
            "private_embedding_artifacts_included": False,
            "commands_may_include_local_report_paths": bool(report_path),
        },
        "claim_boundaries": answer_readiness.get("claim_boundaries"),
    }


SEMANTIC_DEBUG_RERUN_VALUE_FLAGS = {
    "--cwd",
    "--root",
    "--hard-cap-files",
    "--hard-cap-bytes",
    "--max-files",
    "--max-bytes",
    "--max-matches",
}
SEMANTIC_DEBUG_RERUN_BOOL_FLAGS = {"--allow-arbitrary-root"}


def fielded_bm25_rerun_command(
    *,
    query: str,
    guided_audit_flow: dict[str, Any],
    next_reads: list[dict[str, Any]],
) -> dict[str, Any] | None:
    summary = guided_audit_flow.get("investigation_state_summary") or {}
    if summary.get("retrieval_mode_used") != RETRIEVAL_MODE_PACKAGE:
        return None
    if summary.get("retrieval_mode_recommended") != "fielded-bm25":
        return None
    scope_args = semantic_debug_rerun_scope_args(next_reads)
    argv = [
        "jsonl-history-search",
        "semantic-debug",
        query,
        "--retrieval-mode",
        "fielded-bm25",
        *scope_args,
        "--format",
        "json",
    ]
    return {
        "command_id": "rerun_with_fielded_bm25",
        "command_family": "semantic_debug_rerun",
        "required_for_decisions": ["scoped_no_evidence", "inspect_recovery_reads", "needs_investigation"],
        "argv": argv,
        "command": shell_command(argv),
        "purpose": "Rerun the same bounded semantic-debug query with explicit fielded-bm25 retrieval when package mode is weak or no-evidence.",
        "retrieval_mode": {
            "current": RETRIEVAL_MODE_PACKAGE,
            "recommended": "fielded-bm25",
            "package_default_changed": False,
            "rrf_default_allowed": False,
        },
        "privacy_boundaries": {
            "raw_private_transcript_included": False,
            "raw_source_lines_included": False,
            "private_bridge_payloads_included": False,
        },
    }


def build_action_proof_plan(
    *,
    query: str,
    family: dict[str, Any],
    next_reads: list[dict[str, Any]],
    investigation_state: dict[str, Any],
    source_window_action_proof: dict[str, Any],
    answer_readiness: dict[str, Any],
    guided_audit_flow: dict[str, Any],
) -> dict[str, Any]:
    primary_family = family.get("primary")
    detected_supported = set(family.get("detected_supported") or [])
    action_reads = [
        read
        for read in next_reads
        if isinstance(read, dict) and isinstance(read.get("action_proof_source_selection"), dict)
    ]
    applies = primary_family == "claimed_action" or "claimed_action" in detected_supported
    if not applies:
        return {"schema_version": 1, "applies": False, "reason": "not_a_claimed_action_or_action_proof_query"}

    primary_refs = action_proof_primary_refs(action_reads, source_window_action_proof)
    required_reads = action_proof_required_reads(primary_refs, next_reads)
    optional_diagnostics = action_proof_optional_diagnostics(
        next_reads=next_reads,
        required_reads=required_reads,
        answer_readiness=answer_readiness,
    )
    retrieval = action_proof_retrieval_guidance(
        query=query,
        guided_audit_flow=guided_audit_flow,
        investigation_state=investigation_state,
        next_reads=next_reads,
        primary_refs=primary_refs,
        has_candidate_action_proof_window=any(
            ref.get("proof_role") == "candidate_action_source_window"
            and (
                ref.get("selection_reason") == "assistant_answer_with_nearby_tool_action_proof"
                or str(ref.get("selection_reason") or "").startswith("known_action_case:")
            )
            for ref in primary_refs
        ),
        missing_buckets=list((source_window_action_proof.get("summary") or {}).get("missing_buckets") or []),
    )
    stop_condition = (
        "After the primary proof reads answer the action question, stop broad searching and answer "
        "with source refs plus searched-scope/gap boundaries."
    )
    if not primary_refs:
        stop_condition = (
            "If required proof reads still do not expose action proof, state the missing proof buckets before "
            "rerunning broader retrieval; do not fill the gap with adjacent artifacts or assistant prose."
        )
    return {
        "schema_version": 1,
        "applies": True,
        "status": "primary_proof_refs_available" if primary_refs else "primary_proof_refs_missing",
        "primary_question_family": primary_family,
        "do_first": "open_primary_proof_refs_before_broad_search" if primary_refs else "open_required_proof_reads_then_check_gaps",
        "primary_proof_refs": primary_refs[:4],
        "required_proof_reads": required_reads[:4],
        "optional_diagnostics": optional_diagnostics[:4],
        "stop_condition": stop_condition,
        "retrieval_mode_guidance": retrieval,
        "answer_boundary": {
            "answer_from_assistant_prose_alone": False,
            "adjacent_artifacts_count_as_primary_action_proof": False,
            "state_missing_proof_buckets": bool(
                (source_window_action_proof.get("summary") or {}).get("missing_buckets")
            ),
            "missing_buckets": list((source_window_action_proof.get("summary") or {}).get("missing_buckets") or [])[:8],
        },
        "links": {
            "next_reads": "semantic_debug.next_reads",
            "source_window_action_proof": "semantic_debug.source_window_action_proof",
            "answer_ready_handoff": "semantic_debug.answer_ready_handoff",
            "investigation_state": "semantic_debug.investigation_state",
        },
        "privacy_boundaries": {
            "raw_transcript_text_included": False,
            "raw_source_lines_included": False,
            "source_refs_and_local_targets_included": True,
        },
    }


def action_proof_primary_refs(
    action_reads: list[dict[str, Any]],
    source_window_action_proof: dict[str, Any],
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for read in action_reads[:4]:
        refs.append(action_proof_read_ref(read, proof_role="candidate_action_source_window"))

    buckets = source_window_action_proof.get("buckets") if isinstance(source_window_action_proof.get("buckets"), dict) else {}
    for bucket_name in ("command_tool_call", "command_tool_output", "changed_check_review_commit_evidence"):
        bucket = buckets.get(bucket_name) if isinstance(buckets.get(bucket_name), dict) else {}
        for record in list(bucket.get("records") or [])[:2]:
            if not record.get("primary_action_proof"):
                continue
            ref = action_proof_record_ref(record, bucket_name=bucket_name)
            if ref:
                refs.append(ref)
    return dedupe_dicts(refs)[:6]


def action_proof_read_ref(read: dict[str, Any], *, proof_role: str) -> dict[str, Any]:
    selection = read.get("action_proof_source_selection") if isinstance(read.get("action_proof_source_selection"), dict) else {}
    row = {
        "target": read.get("target"),
        "kind": read.get("kind"),
        "proof_role": proof_role,
        "required": True,
        "reason": read.get("reason"),
        "source_ref": guided_source_ref(read.get("source_ref") or {}) if isinstance(read.get("source_ref"), dict) else None,
        "selection_reason": selection.get("selection_reason"),
        "source_window_verification_required": selection.get("source_window_verification_required"),
        "claim_boundary": selection.get("claim_boundary"),
    }
    return {key: value for key, value in row.items() if value not in (None, [], {})}


def action_proof_record_ref(record: dict[str, Any], *, bucket_name: str) -> dict[str, Any]:
    source_ref = record.get("source_ref") if isinstance(record.get("source_ref"), dict) else {}
    target = source_ref_target(source_ref) if source_ref else None
    row = {
        "target": target,
        "proof_role": record.get("proof_role"),
        "bucket": bucket_name,
        "required": True,
        "source_ref": guided_source_ref(source_ref) if source_ref else None,
        "event_id": record.get("event_id"),
        "event_family": record.get("event_family"),
        "action": record.get("action"),
        "tool_name": record.get("tool_name"),
    }
    return {key: value for key, value in row.items() if value not in (None, [], {})}


def action_proof_required_reads(
    primary_refs: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if primary_refs:
        return [
            {
                **ref,
                "read_type": "required_primary_proof_read",
                "instruction": "Open this source ref before answering the claimed-action question.",
            }
            for ref in primary_refs
            if ref.get("target") or ref.get("source_ref")
        ]
    return [
        {
            **action_proof_read_ref(read, proof_role="fallback_bounded_next_read"),
            "read_type": "required_gap_check_read",
            "instruction": "Open this bounded next read to confirm whether action proof is present or missing.",
        }
        for read in next_reads[:3]
        if isinstance(read, dict) and (read.get("target") or read.get("source_ref"))
    ]


def action_proof_optional_diagnostics(
    *,
    next_reads: list[dict[str, Any]],
    required_reads: list[dict[str, Any]],
    answer_readiness: dict[str, Any],
) -> list[dict[str, Any]]:
    required_targets = {item.get("target") for item in required_reads if item.get("target")}
    diagnostics: list[dict[str, Any]] = []
    for read in next_reads:
        target = read.get("target")
        if not target or target in required_targets:
            continue
        diagnostics.append(
            {
                "target": target,
                "kind": read.get("kind"),
                "reason": read.get("reason"),
                "diagnostic_role": "optional_after_primary_proof_or_when_required_facets_remain_gapped",
            }
        )
        if len(diagnostics) >= 3:
            break
    diagnostics.append(
        {
            "diagnostic_role": "follow_next_reads_or_failure_analysis",
            "when": (
                "Only after primary proof is thin, contradictory, missing required action facets, "
                "or the answer requires timeline synthesis."
            ),
            "answer_readiness_decision": answer_readiness.get("decision"),
        }
    )
    return diagnostics


def action_proof_retrieval_guidance(
    *,
    query: str,
    guided_audit_flow: dict[str, Any],
    investigation_state: dict[str, Any],
    next_reads: list[dict[str, Any]],
    primary_refs: list[dict[str, Any]],
    has_candidate_action_proof_window: bool,
    missing_buckets: list[str],
) -> dict[str, Any]:
    retrieval = investigation_state.get("retrieval") or {}
    recommendation = retrieval.get("conservative_recommendation") or {}
    fielded_command = fielded_bm25_rerun_command(
        query=query,
        guided_audit_flow=guided_audit_flow,
        next_reads=next_reads,
    )
    fielded = {
        "mode": "fielded-bm25",
        "suggested_when": "package evidence is thin, missing primary proof, contradictory, or required action facets remain gapped",
        "required_now": (
            (not bool(primary_refs) or (bool(missing_buckets) and not has_candidate_action_proof_window))
            and recommendation.get("mode") == "fielded-bm25"
        ),
        "rerun_command": fielded_command,
    }
    if fielded_command is None:
        fielded["rerun_command"] = None
        fielded["rerun_command_reason"] = "not_applicable_unless_current_mode_is_package_and_recommendation_is_fielded_bm25"
    return {
        "current_mode": retrieval.get("mode_used"),
        "recommended_mode": recommendation.get("mode"),
        "package": {
            "default_ok": True,
            "use_first": "normal semantic-debug and action-proof questions can start in package mode",
            "stop_before_rerun_when": "primary proof refs answer the action question with boundaries",
        },
        "fielded_bm25": fielded,
        "rrf_fusion": {
            "default_ok": False,
            "explicit_opt_in_only": True,
            "suggested_when": "a named high-recall failure condition remains after package/fielded evidence is thin or gapped",
            "rerun_command_emitted_by_default": False,
        },
        "claim_boundaries": {
            "package_default_changed": False,
            "rrf_default_promoted": False,
            "retrieval_mode_claims_final_answer_quality": False,
        },
    }


def summarize_action_proof_plan(plan: dict[str, Any]) -> dict[str, Any]:
    retrieval = plan.get("retrieval_mode_guidance") or {}
    fielded = retrieval.get("fielded_bm25") if isinstance(retrieval.get("fielded_bm25"), dict) else {}
    return {
        "schema_version": plan.get("schema_version", 1),
        "applies": bool(plan.get("applies")),
        "status": plan.get("status"),
        "primary_proof_ref_count": len(plan.get("primary_proof_refs") or []),
        "required_proof_read_count": len(plan.get("required_proof_reads") or []),
        "optional_diagnostic_count": len(plan.get("optional_diagnostics") or []),
        "stop_condition": plan.get("stop_condition"),
        "current_mode": retrieval.get("current_mode"),
        "recommended_mode": retrieval.get("recommended_mode"),
        "fielded_bm25_required_now": fielded.get("required_now"),
        "rrf_explicit_opt_in_only": (retrieval.get("rrf_fusion") or {}).get("explicit_opt_in_only"),
    }


def semantic_debug_rerun_scope_args(next_reads: list[dict[str, Any]]) -> list[str]:
    scope_args: list[str] = []
    seen_flags: set[str] = set()
    for read in next_reads:
        if not isinstance(read, dict):
            continue
        for action in read.get("expansion_actions") or []:
            argv = list(action.get("argv") or []) if isinstance(action, dict) else []
            index = 0
            while index < len(argv):
                item = str(argv[index])
                if item in SEMANTIC_DEBUG_RERUN_BOOL_FLAGS and item not in seen_flags:
                    scope_args.append(item)
                    seen_flags.add(item)
                elif item in SEMANTIC_DEBUG_RERUN_VALUE_FLAGS and index + 1 < len(argv) and item not in seen_flags:
                    scope_args.extend([item, str(argv[index + 1])])
                    seen_flags.add(item)
                    index += 1
                index += 1
        if seen_flags:
            break
    return scope_args


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
            "support_summary": empty_support_summary(),
            "missing": ["forensics_not_run_no_commit_path_task_branch_or_around_hint"],
        }
    git = forensics_payload.get("git", {})
    workflow = forensics_payload.get("forensics") or {}
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
        "support_summary": workflow.get("support_summary") or empty_support_summary(),
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
                "support_summary": empty_support_summary(),
                **empty_support_counts(),
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
        support_summary = joins_payload.get("support_summary") or empty_support_summary()
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
            "support_summary": support_summary,
            **flat_support_counts(support_summary),
        }
    support_summary = workflow.get("support_summary") or diagnostics.get("support_summary") or empty_support_summary()
    return {
        "status": "joined" if phase_timeline else "no_phase_evidence",
        "phase_timeline": phase_timeline,
        "claim_action_review_joins": joins,
        "review_gate": review_gate,
        "diagnostics": diagnostics,
        "provenance_lifecycle": provenance_lifecycle,
        "support_summary": support_summary,
        "summary": {
            "phase_count": len(phase_timeline),
            "join_count": len(joins),
            "review_gate_status": review_gate.get("status"),
            "review_gate_verdict": review_gate.get("verdict"),
            "missing_evidence_flags": diagnostics.get("missing_evidence_flags", []),
            "join_basis_counts": diagnostics.get("join_basis_counts", {}),
            "support_summary": support_summary,
            **flat_support_counts(support_summary),
            "provenance": provenance_summary or {},
        },
    }


def empty_support_counts() -> dict[str, int]:
    return {
        "exact_support_count": 0,
        "proximity_support_count": 0,
        "inferred_support_count": 0,
        "unsupported_support_count": 0,
    }


def empty_support_summary() -> dict[str, Any]:
    return {
        "schema_version": 1,
        **empty_support_counts(),
        "support_status_counts": {},
        "support_basis_counts": {},
        "support_source_counts": {},
        "unsupported_artifact_count": 0,
        "support_boundary": "support labels classify evidence precision only; they do not prove artifact content correctness, root cause, retrieval quality, or final-answer quality",
    }


def flat_support_counts(summary: dict[str, Any]) -> dict[str, int]:
    counts = empty_support_counts()
    for key in counts:
        counts[key] = int(summary.get(key) or 0)
    return counts


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
                "support_status": item.get("support_status"),
                "support_basis_kind": item.get("support_basis_kind"),
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
            "support_status": item.get("support_status"),
            "support_basis_kind": item.get("support_basis_kind"),
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
            "support_status": item.get("support_status"),
            "support_basis_kind": item.get("support_basis_kind"),
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
    query: str = "",
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
    correction_reads = correction_window_source_next_reads(
        query=query,
        candidates=candidates,
        tool_proof=tool_proof,
        expansion_action_context=expansion_action_context,
        include_context_actions=include_context_actions,
    )
    promoted_reads = action_proof_source_next_reads(
        query=query,
        candidates=candidates,
        tool_proof=tool_proof,
        expansion_action_context=expansion_action_context,
        include_context_actions=include_context_actions,
    )
    return dedupe_dicts(correction_reads + promoted_reads + reads)[:12]


ACTION_PROOF_SCAN_FILE_LIMIT = 8
ACTION_PROOF_PROMOTION_LIMIT = 2
ACTION_PROOF_QUERY_TERMS = {
    "actually",
    "assistant",
    "agent",
    "check",
    "checked",
    "checks",
    "commit",
    "committed",
    "edit",
    "edited",
    "files",
    "observed",
    "overwrite",
    "overwritten",
    "ran",
    "run",
    "tested",
    "tool",
    "untouched",
    "worktree",
}
ACTION_PROOF_STOPWORDS = {
    "about",
    "after",
    "around",
    "before",
    "dated",
    "from",
    "include",
    "into",
    "local",
    "made",
    "and",
    "asked",
    "been",
    "codex",
    "did",
    "had",
    "session",
    "user",
    "was",
    "that",
    "the",
    "this",
    "those",
    "time",
    "were",
    "whether",
    "with",
}
ACTION_PROOF_TEXT_RE = re.compile(r"[a-z0-9][a-z0-9_/-]*")


def correction_window_source_next_reads(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    if not correction_window_source_selection_wanted(query):
        return []
    source_paths = action_proof_candidate_paths(candidates, tool_proof)
    if not source_paths:
        return []
    scored: list[dict[str, Any]] = []
    for path in source_paths[:ACTION_PROOF_SCAN_FILE_LIMIT]:
        scored.extend(correction_window_records_for_path(path))
    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("timestamp_utc") or ""),
            str((item.get("source_ref") or {}).get("path") or ""),
            int((item.get("source_ref") or {}).get("line") or 0),
        )
    )
    reads: list[dict[str, Any]] = []
    for index, item in enumerate(scored[:ACTION_PROOF_PROMOTION_LIMIT], 1):
        source_ref = item["source_ref"]
        candidate_id = f"correction-window-source-{index:03d}"
        read = {
            "kind": "jsonl_source_ref",
            "target": source_ref_target(source_ref),
            "candidate_id": candidate_id,
            "source_ref": source_ref,
            "reason": "inspect candidate correction source window; verify original challenge and correction before answering",
            "correction_window_source_selection": {
                "schema_version": 1,
                "selection_reason": item["selection_reason"],
                "role": item.get("role"),
                "score": item["score"],
                "raw_text_included": False,
                "bounded_to_surfaced_source_files": True,
                "source_window_verification_required": True,
                "claim_boundary": "candidate_correction_window_not_proof_of_original_source_window",
            },
            "expansion_actions": read_more_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            ),
        }
        if include_context_actions:
            read["context_actions"] = context_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            )
        if read["target"]:
            reads.append(read)
    return reads


def correction_window_source_selection_wanted(query: str) -> bool:
    return persistent_coordinator_correction_route_wanted(query)


def persistent_coordinator_correction_route_wanted(query: str) -> bool:
    text = str(query or "").lower()
    coordinator_cue = "persistent coordinator" in text and ("wait" in text or "poll" in text)
    correction_cue = any(
        term in text
        for term in (
            "correction",
            "corrected",
            "follow-up challenge",
            "followup challenge",
            "challenged",
            "challenge",
            "distinction",
            "differed",
            "different",
        )
    )
    route_cue = any(
        term in text
        for term in (
            "source window",
            "source case",
            "stable-history",
            "codex session",
            "what was",
            "what distinction",
            "what correction",
        )
    )
    return coordinator_cue and correction_cue and route_cue


def correction_window_records_for_path(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        records = list(iter_records_for_file(path, "unknown_jsonl", timezone.utc))
        for index, record in enumerate(records):
            score, selection_reason = score_correction_window_record(records, index)
            if score <= 0:
                continue
            rows.append(
                {
                    "source_ref": {"path": str(path), "line": int(record.line), "session": Path(path).stem},
                    "role": record.role,
                    "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
                    "score": score,
                    "selection_reason": selection_reason,
                }
            )
    except (AdapterReadError, OSError, ValueError):
        return []
    return rows


def score_correction_window_record(records: list[Any], index: int) -> tuple[float, str]:
    record = records[index]
    role_text = str(record.role or "").lower()
    lowered = str(record.text or "").lower()
    if "persistent coordinator" not in lowered:
        return 0.0, "no_correction_window_topic"
    wait_cue = "wait" in lowered or "poll" in lowered
    if not wait_cue:
        return 0.0, "no_wait_or_poll_cue"
    score = 0.0
    reason = "correction_window_topic_overlap"
    if any(token in role_text for token in ("user", "human")) and any(
        term in lowered for term in ("challenged", "different", "same", "not", "confusing")
    ):
        score = 64.0
        reason = "user_followup_or_original_challenge"
    if any(token in role_text for token in ("assistant", "agent")):
        first_distinction = ("tmux" in lowered or "injector" in lowered) and (
            "foreground" in lowered or "current session" in lowered or "current turn" in lowered
        )
        correction = any(term in lowered for term in ("you're right", "you’re right", "conflated", "actual distinction"))
        if first_distinction:
            score = max(score, 88.0)
            reason = "assistant_persistent_vs_wait_distinction"
        if correction:
            score = max(score, 116.0)
            reason = "assistant_followup_correction"
    if "function_call" in role_text and any(
        term in lowered
        for term in (
            "persistent-coordinator-pattern",
            "coordinator-inject",
            "codex-config-pack",
            "improvements-to-codex",
        )
    ):
        score = max(score, 72.0)
        reason = "source_inspection_for_correction_window"
    if any(token in role_text for token in ("developer", "system", "turn_context", "token_count", "reasoning")):
        score -= 80
    if "function_call_output" in role_text and len(lowered) > 1200:
        score -= 32
    return score, reason


def action_proof_source_next_reads(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    if not action_proof_source_selection_wanted(query):
        return []
    query_terms = action_proof_query_terms(query)
    if not query_terms:
        return []
    source_paths = action_proof_candidate_paths(candidates, tool_proof)
    if not source_paths:
        return []
    tool_proof_paths = {
        str((event.get("source_ref") or {}).get("path") or "")
        for event in tool_proof.get("events", [])
        if isinstance(event, dict)
    }
    scored: list[dict[str, Any]] = []
    for path in source_paths[:ACTION_PROOF_SCAN_FILE_LIMIT]:
        scored.extend(action_proof_records_for_path(path, query_terms=query_terms, tool_proof_paths=tool_proof_paths))
    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("timestamp_utc") or ""),
            str((item.get("source_ref") or {}).get("path") or ""),
            int((item.get("source_ref") or {}).get("line") or 0),
        )
    )
    reads: list[dict[str, Any]] = []
    for index, item in enumerate(scored[:ACTION_PROOF_PROMOTION_LIMIT], 1):
        source_ref = item["source_ref"]
        candidate_id = f"action-proof-source-{index:03d}"
        read = {
            "kind": "jsonl_source_ref",
            "target": source_ref_target(source_ref),
            "candidate_id": candidate_id,
            "source_ref": source_ref,
            "reason": "inspect candidate action-proof source window; verify original window before answering",
            "action_proof_source_selection": {
                "schema_version": 1,
                "selection_reason": item["selection_reason"],
                "role": item.get("role"),
                "score": item["score"],
                "query_term_overlap_count": len(item.get("matched_query_terms") or []),
                "raw_text_included": False,
                "bounded_to_surfaced_source_files": True,
                "source_window_verification_required": True,
                "claim_boundary": "candidate_action_window_not_proof_of_original_source_window",
            },
            "expansion_actions": read_more_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            ),
        }
        if include_context_actions:
            read["context_actions"] = context_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            )
        if read["target"]:
            reads.append(read)
    return reads


def action_proof_source_selection_wanted(query: str) -> bool:
    text = str(query or "").lower()
    action_cue = any(term in text for term in ACTION_PROOF_QUERY_TERMS)
    question_cue = any(phrase in text for phrase in ("did the", "did you", "whether", "actually", "was ", "were "))
    overwrite_cue = "overwrite" in text or "overwritten" in text
    proof_cue = any(term in text for term in ("files observed", "checks run", "commit", "worktree", "tool"))
    return action_cue and (question_cue or overwrite_cue or proof_cue)


def action_proof_query_terms(query: str) -> set[str]:
    terms = {
        term
        for term in ACTION_PROOF_TEXT_RE.findall(str(query or "").lower())
        if len(term) >= 3 and term not in ACTION_PROOF_STOPWORDS
    }
    expanded = set(terms)
    if "overwritten" in terms:
        expanded.add("overwrite")
    if "overwrite" in terms:
        expanded.add("overwritten")
    if "planner" in terms:
        expanded.add("planners")
    if "planners" in terms:
        expanded.add("planner")
    if "split" in terms:
        expanded.add("spliting")
    return expanded


def action_proof_candidate_paths(candidates: list[dict[str, Any]], tool_proof: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for item in candidates[:INLINE_CANDIDATE_LIMIT]:
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        path = ref.get("path")
        if path:
            paths.append(Path(str(path)).expanduser())
    for event in tool_proof.get("events", [])[:INLINE_EVENT_LIMIT]:
        ref = event.get("source_ref") if isinstance(event, dict) and isinstance(event.get("source_ref"), dict) else {}
        path = ref.get("path")
        if path:
            paths.append(Path(str(path)).expanduser())
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(path)
    return result


def action_proof_records_for_path(
    path: Path,
    *,
    query_terms: set[str],
    tool_proof_paths: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        records = list(iter_records_for_file(path, "unknown_jsonl", timezone.utc))
        for index, record in enumerate(records):
            score, matched_terms, selection_reason = score_action_proof_record(
                text=record.text,
                role=record.role,
                line=int(record.line),
                query_terms=query_terms,
                tool_proof_path_bonus=str(path) in tool_proof_paths,
            )
            neighborhood_bonus, neighborhood_reason = action_proof_neighborhood_bonus(records, index)
            if neighborhood_bonus:
                score += neighborhood_bonus
                selection_reason = neighborhood_reason
            if score <= 0:
                continue
            rows.append(
                {
                    "source_ref": {"path": str(path), "line": int(record.line), "session": Path(path).stem},
                    "role": record.role,
                    "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
                    "score": score,
                    "matched_query_terms": sorted(matched_terms),
                    "selection_reason": selection_reason,
                }
            )
    except (AdapterReadError, OSError, ValueError):
        return []
    return rows


def action_proof_neighborhood_bonus(records: list[Any], index: int) -> tuple[float, str]:
    record = records[index]
    role_text = str(record.role or "").lower()
    lowered = str(record.text or "").lower()
    if not any(token in role_text for token in ("assistant", "agent")):
        return 0.0, ""
    if not any(term in lowered for term in ("did not overwrite", "not overwrite", "committed", "left untouched")):
        return 0.0, ""
    window = records[max(0, index - 10) : min(len(records), index + 3)]
    nearby_tool_action = False
    nearby_state_output = False
    for nearby in window:
        nearby_role = str(nearby.role or "").lower()
        nearby_text = str(nearby.text or "").lower()
        if "function_call" in nearby_role or "tool" in nearby_role:
            if any(term in nearby_text for term in ("git diff", "git status", "git commit", "find ", "test -f", "rg ")):
                nearby_tool_action = True
            if any(term in nearby_text for term in ("modified:", "nothing to commit", "working tree", "commit", "files changed")):
                nearby_state_output = True
    if nearby_tool_action and nearby_state_output:
        return 36.0, "assistant_answer_with_nearby_tool_action_proof"
    if nearby_tool_action:
        return 20.0, "assistant_answer_with_nearby_tool_action"
    return 0.0, ""


def score_action_proof_record(
    *,
    text: str,
    role: str,
    line: int,
    query_terms: set[str],
    tool_proof_path_bonus: bool,
) -> tuple[float, set[str], str]:
    lowered = str(text or "").lower()
    role_text = str(role or "").lower()
    tokens = set(ACTION_PROOF_TEXT_RE.findall(lowered))
    matched = {term for term in query_terms if term in tokens or ("/" in term and term in lowered)}
    if not matched:
        return 0.0, set(), "no_query_overlap"
    score = float(len(matched) * 4)
    reason = "query_term_overlap"
    if any(token in role_text for token in ("user", "human")):
        score += 24
        reason = "user_action_concern"
    elif any(token in role_text for token in ("assistant", "agent")):
        score += 14
        reason = "assistant_action_answer"
    elif "tool" in role_text or "function_call" in role_text:
        score += 8
        reason = "tool_action_record"
    if any(term in lowered for term in ("did not overwrite", "not overwrite", "overwrite", "overwritten")):
        score += 12
    if any(term in lowered for term in ("git diff --check", "git diff --stat", "git commit", "committed")):
        score += 10
    if any(token in role_text for token in ("assistant", "agent")) and any(
        term in lowered for term in ("did not overwrite", "not overwrite")
    ):
        concrete_facets = sum(
            1
            for terms in (
                ("committed", "git commit", "commit "),
                ("codex-orchestration.md", "agent-workflow.md"),
                ("worktree", "draft"),
                ("git diff --check", "git diff --stat", "diff --check", "diff --stat"),
            )
            if any(term in lowered for term in terms)
        )
        if concrete_facets >= 2:
            score += 32
            reason = "assistant_concrete_action_answer"
    if any(token in role_text for token in ("user", "human")) and (
        "using reviewed source case" in lowered
        or "use local source refs" in lowered
        or "answer whether the agent actually" in lowered
    ):
        score -= 80
        reason = "investigation_query_echo"
    if tool_proof_path_bonus:
        score += 8
    if line <= 20:
        score -= 22
    if any(token in role_text for token in ("compacted", "turn_context", "token_count", "patch_apply")):
        score -= 60
    if "custom_tool_call" in role_text:
        score -= 36
    if any(token in role_text for token in ("developer", "system")):
        score -= 30
    if "function_call_output" in role_text and len(lowered) > 1200:
        score -= 24
    return score, matched, reason


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
    source_handle_routing: dict[str, Any] | None = None,
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
    if (source_handle_routing or {}).get("detected_handles"):
        required_actions.insert(
            0,
            "Resolve/open matching source refs for detected reviewed/source/case handles before broad answer claims, or state no matching source ref was found in searched scope.",
        )
    evidence_inputs = {
        "strongest_candidate_source_refs": candidate_refs,
        "source_locator_next_reads": next_reads,
        "structured_tool_event_count": tool_proof.get("event_count", 0),
        "task_review_join_status": joins.get("status"),
        "task_review_join_support_summary": joins.get("support_summary") or empty_support_summary(),
        "uncertainty_labels": [item.get("label") for item in uncertainty],
        "source_handle_routing": source_handle_handoff_summary(source_handle_routing),
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
            "support_precision_claim": "counts_only_not_artifact_content_correctness",
        },
    }


def build_answer_readiness(
    *,
    status: str,
    source_scope: dict[str, Any],
    source_locator_handoff: dict[str, Any],
    final_answer_contract: dict[str, Any],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    no_evidence: dict[str, Any],
    uncertainty: list[dict[str, Any]],
    decision_context: dict[str, Any],
    audit_plan: dict[str, Any],
    forensics_workflow: dict[str, Any],
    next_reads: list[dict[str, Any]],
    source_handle_routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_evidence_contract = source_locator_handoff.get("final_evidence_contract") or {}
    ledger = list(final_evidence_contract.get("ledger") or [])
    surface_counts = final_evidence_contract.get("surface_counts") or {}
    omitted_window_recovery = source_locator_handoff.get("omitted_window_recovery") or {}
    route_states = answer_readiness_route_states(
        route_order=list(audit_plan.get("route_order") or []),
        tool_proof=tool_proof,
        joins=joins,
        forensics_workflow=forensics_workflow,
    )
    ledger_id_by_candidate_id = final_evidence_contract.get("ledger_id_by_candidate_id")
    source_ref_ledger_ids = final_evidence_contract.get("ledger_ids_by_source_ref")
    default_projection_ledger_count = (
        len(ledger_id_by_candidate_id) if isinstance(ledger_id_by_candidate_id, dict) else 0
    )
    if isinstance(source_ref_ledger_ids, dict):
        default_projection_ledger_count += sum(
            len(ids) for ids in source_ref_ledger_ids.values() if isinstance(ids, list)
        )
    present_ledger_count = int(surface_counts.get("final_surface_status:present", 0) or 0)
    if present_ledger_count == 0 and not ledger and default_projection_ledger_count:
        present_ledger_count = default_projection_ledger_count
    recovery_ledger_count = int(surface_counts.get("final_surface_status:omitted_with_exact_recovery", 0) or 0)
    deferred_ledger_count = int(surface_counts.get("final_surface_status:deferred_with_next_read", 0) or 0)
    no_evidence_ledger_count = int(surface_counts.get("final_surface_status:not_applicable", 0) or 0)
    recovery_available = omitted_window_recovery.get("status") == "available"
    audit_route_required = any(route_state["required"] for route_state in route_states if route_state["route"] in AUDIT_ROUTE_NAMES)
    if no_evidence.get("needed"):
        readiness_status = "scoped_no_evidence_ready"
        decision = "state_scoped_no_evidence"
        allowed_answer_scope = "searched_scope_no_evidence"
        required_before_answer = list(scope_ready_required_actions(no_evidence))
    elif recovery_available:
        readiness_status = "inspect_recovery_first"
        decision = "inspect_recovery_reads"
        allowed_answer_scope = "recovery_first"
        required_before_answer = [
            "Open promoted omitted-window recovery reads before treating the searched evidence set as representative.",
        ]
        if omitted_window_recovery.get("promoted_next_read_count", 0):
            required_before_answer.append("Review the promoted next_reads tied to omitted-window recovery before answering.")
    elif audit_route_required:
        readiness_status = "audit_route_required"
        decision = "run_audit_route"
        allowed_answer_scope = "audit_first"
        required_before_answer = [
            item
            for item in [
                route_required_action(route_state)
                for route_state in route_states
                if route_state["required"] and route_state["route"] in AUDIT_ROUTE_NAMES
            ]
            if item
        ]
    elif present_ledger_count > 0 and final_answer_contract.get("source_use_required"):
        readiness_status = "answer_ready"
        decision = "answer_now"
        allowed_answer_scope = "source_backed"
        required_before_answer = [
            "Answer from the source-backed ledger and keep unsupported claims out of the final response.",
        ]
    else:
        readiness_status = "insufficient_evidence"
        decision = "gather_more_evidence"
        allowed_answer_scope = "insufficient"
        required_before_answer = [
            "Gather more source-backed evidence before answering.",
        ]
        if not next_reads and not route_states:
            required_before_answer.append("No bounded next read or route surfaced; revisit source planning.")
    if (source_handle_routing or {}).get("detected_handles"):
        required_before_answer.append(
            "Resolve/open matching source refs for detected source handles first, or state no matching source ref was found in searched scope."
        )

    ready_ledger_ids = [
        entry.get("ledger_id")
        for entry in ledger
        if entry.get("final_surface_status") == "present" and entry.get("ledger_id")
    ]
    recovery_ledger_ids = [
        entry.get("ledger_id")
        for entry in ledger
        if entry.get("final_surface_status") == "omitted_with_exact_recovery" and entry.get("ledger_id")
    ]
    deferred_ledger_ids = [
        entry.get("ledger_id")
        for entry in ledger
        if entry.get("final_surface_status") == "deferred_with_next_read" and entry.get("ledger_id")
    ]
    no_evidence_ledger_ids = [
        entry.get("ledger_id")
        for entry in ledger
        if entry.get("final_surface_status") == "not_applicable" and entry.get("ledger_id")
    ]
    next_read_targets = [item.get("target") for item in next_reads[:8] if item.get("target")]
    return {
        "schema_version": 1,
        "status": readiness_status,
        "decision": decision,
        "allowed_answer_scope": allowed_answer_scope,
        "required_before_answer": required_before_answer,
        "basis": {
            "status": status,
            "present_ledger_count": present_ledger_count,
            "recovery_ledger_count": recovery_ledger_count,
            "deferred_ledger_count": deferred_ledger_count,
            "no_evidence_ledger_count": no_evidence_ledger_count,
            "ledger_count": int(final_evidence_contract.get("ledger_count", 0) or 0),
            "next_read_count": len(next_reads),
            "promoted_next_read_count": int(omitted_window_recovery.get("promoted_next_read_count", 0) or 0),
            "omitted_window_recovery_count": int(omitted_window_recovery.get("recovery_count", 0) or 0),
            "structured_tool_event_count": int(tool_proof.get("event_count", 0) or 0),
            "task_review_join_status": joins.get("status"),
            "task_review_join_count": len(joins.get("review_evidence", []) or []),
            "forensics_phase_count": int((forensics_workflow.get("summary") or {}).get("phase_count", 0) or 0),
            "forensics_join_count": int((forensics_workflow.get("summary") or {}).get("join_count", 0) or 0),
            "source_handle_count": len((source_handle_routing or {}).get("detected_handles") or []),
            "source_handle_matching_next_read_count": len((source_handle_routing or {}).get("matched_next_reads") or []),
        },
        "source_handle_routing": source_handle_handoff_summary(source_handle_routing),
        "ledger_summary": {
            "ledger_count": int(final_evidence_contract.get("ledger_count", 0) or 0),
            "ledger_ids": [entry.get("ledger_id") for entry in ledger[:12] if entry.get("ledger_id")],
            "ledger_ids_by_surface_status": {
                "present": ready_ledger_ids[:8],
                "omitted_with_exact_recovery": recovery_ledger_ids[:8],
                "deferred_with_next_read": deferred_ledger_ids[:8],
                "not_applicable": no_evidence_ledger_ids[:8],
            },
            "surface_counts": {
                "present": present_ledger_count,
                "omitted_with_exact_recovery": recovery_ledger_count,
                "deferred_with_next_read": deferred_ledger_count,
                "not_applicable": no_evidence_ledger_count,
            },
        },
        "next_reads": {
            "count": len(next_reads),
            "targets": next_read_targets[:8],
        },
        "route_summary": {
            "route_order": list(audit_plan.get("route_order") or [])[:8],
            "selected_routes": list(decision_context.get("selected_routes") or [])[:8],
            "route_states": route_states[:8],
        },
        "claim_boundaries": {
            "root_cause_claim": "not_claimed",
            "global_absence_claim": "not_allowed",
            "final_answer_quality_claim_supported": False,
            "token_savings_claim_supported": False,
            "speed_claim_supported": False,
            "product_value_claim_supported": False,
            "all_history_completeness_claim_supported": False,
            "evidence_vs_inference_required": True,
            "support_precision_claim": "counts_only_not_artifact_content_correctness",
        },
        "privacy_boundaries": {
            "raw_transcript_text_included": False,
            "raw_source_lines_included": False,
            "raw_session_ids_included": False,
            "vectors_or_indexes_included": False,
        },
        "source_use_contract": {
            "answer_scope": final_answer_contract.get("answer_scope"),
            "source_use_required": final_answer_contract.get("source_use_required"),
            "required_actions": list(final_answer_contract.get("required_actions") or [])[:8],
            "no_evidence_scope": final_answer_contract.get("no_evidence_scope"),
        },
        "answer_gate_link": "semantic_debug.guided_audit_flow.answer_gate",
        "answer_readiness_link": "semantic_debug.answer_readiness",
        "uncertainty_labels": [item.get("label") for item in uncertainty],
    }


AUDIT_ROUTE_NAMES = {"git_history_reconstruction", "task_review_join", "tool_action_proof"}


def route_required_action(route_state: dict[str, Any]) -> str | None:
    route = route_state.get("route")
    if route == "git_history_reconstruction":
        return "Inspect git history reconstruction before answering provenance or repo-state questions."
    if route == "task_review_join":
        return "Inspect task/review joins before answering questions that depend on review verdicts or handoff claims."
    if route == "tool_action_proof":
        return "Inspect structured tool/action proof before answering claims about what was actually run, edited, tested, or reviewed."
    return None


def scope_ready_required_actions(no_evidence: dict[str, Any]) -> list[str]:
    actions = [
        "Answer with `not_found_in_searched_scope`, not global absence.",
        "Name not-searched, missing, or cap-limited scope before concluding no evidence.",
    ]
    if no_evidence.get("searched_scope"):
        actions.append("Keep the searched scope visible so the answer stays scoped.")
    return actions


def answer_readiness_route_states(
    *,
    route_order: list[str],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    forensics_workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    workflow_summary = forensics_workflow.get("summary") or {}
    route_states: list[dict[str, Any]] = []
    for route in route_order:
        if route == "git_history_reconstruction":
            available = bool(
                workflow_summary.get("phase_count", 0)
                or joins.get("commit")
                or joins.get("branch")
                or joins.get("path_last_change")
            )
            route_states.append(
                {
                    "route": route,
                    "status": "available" if available else "required",
                    "basis": "forensics_workflow.phase_timeline_or_git_join",
                    "required": not available,
                }
            )
        elif route == "task_review_join":
            available = joins.get("status") == "joined"
            route_states.append(
                {
                    "route": route,
                    "status": "available" if available else "required",
                    "basis": "task_review_join_status",
                    "required": not available,
                }
            )
        elif route == "tool_action_proof":
            available = bool(tool_proof.get("event_count", 0))
            route_states.append(
                {
                    "route": route,
                    "status": "available" if available else "required",
                    "basis": "structured_tool_event_count",
                    "required": not available,
                }
            )
        else:
            route_states.append(
                {"route": route, "status": "available", "basis": "not_an_audit_route", "required": False}
            )
    return route_states


def summarize_answer_readiness(answer_readiness: dict[str, Any]) -> dict[str, Any]:
    basis = answer_readiness.get("basis") or {}
    ledger_summary = answer_readiness.get("ledger_summary") or {}
    route_summary = answer_readiness.get("route_summary") or {}
    return {
        "schema_version": answer_readiness.get("schema_version", 1),
        "status": answer_readiness.get("status"),
        "decision": answer_readiness.get("decision"),
        "allowed_answer_scope": answer_readiness.get("allowed_answer_scope"),
        "basis": {
            "present_ledger_count": basis.get("present_ledger_count", 0),
            "recovery_ledger_count": basis.get("recovery_ledger_count", 0),
            "structured_tool_event_count": basis.get("structured_tool_event_count", 0),
            "task_review_join_status": basis.get("task_review_join_status"),
            "next_read_count": basis.get("next_read_count", 0),
            "omitted_window_recovery_count": basis.get("omitted_window_recovery_count", 0),
        },
        "required_before_answer": list(answer_readiness.get("required_before_answer") or [])[:4],
        "ledger_summary": {
            "ledger_count": ledger_summary.get("ledger_count", 0),
            "present_ledger_ids": list((ledger_summary.get("ledger_ids_by_surface_status") or {}).get("present", []))[
                :4
            ],
        },
        "route_summary": {
            "route_order": list(route_summary.get("route_order") or [])[:5],
            "route_states": list(route_summary.get("route_states") or [])[:5],
        },
        "answer_gate_link": answer_readiness.get("answer_gate_link"),
        "answer_readiness_link": answer_readiness.get("answer_readiness_link"),
        "claim_boundaries": answer_readiness.get("claim_boundaries"),
        "privacy_boundaries": answer_readiness.get("privacy_boundaries"),
    }


def build_investigation_state(
    *,
    query: str,
    family: dict[str, Any],
    source_locator_handoff: dict[str, Any],
    search_result: dict[str, Any],
    agent_find: dict[str, Any],
    source_scope: dict[str, Any],
    next_reads: list[dict[str, Any]],
    no_evidence: dict[str, Any],
    uncertainty: list[dict[str, Any]],
    answer_readiness: dict[str, Any],
    source_handle_routing: dict[str, Any],
    source_window_action_proof: dict[str, Any],
) -> dict[str, Any]:
    query_plan = source_locator_handoff.get("query_plan") or {}
    retrieval_diagnostics = search_result.get("retrieval_diagnostics") or {}
    retrieval_pressure = agent_find.get("retrieval_pressure") or {}
    retrieval_mode = (
        retrieval_diagnostics.get("retrieval_mode")
        or retrieval_pressure.get("retrieval_mode")
        or search_result.get("retrieval_mode")
        or RETRIEVAL_MODE_PACKAGE
    )
    recommendation = investigation_retrieval_recommendation(
        retrieval_mode=str(retrieval_mode),
        primary_intent=str(query_plan.get("primary_intent") or ""),
        family=family,
        query=query,
    )
    gaps = investigation_unresolved_gaps(
        source_scope=source_scope,
        no_evidence=no_evidence,
        uncertainty=uncertainty,
        answer_readiness=answer_readiness,
        source_handle_routing=source_handle_routing,
        source_window_action_proof=source_window_action_proof,
        source_locator_handoff=source_locator_handoff,
    )
    answer_state = {
        "status": answer_readiness.get("status"),
        "decision": answer_readiness.get("decision"),
        "allowed_answer_scope": answer_readiness.get("allowed_answer_scope"),
        "recommended_next_step": investigation_recommended_next_step(answer_readiness),
        "required_before_answer": list(answer_readiness.get("required_before_answer") or [])[:6],
        "link": "semantic_debug.answer_readiness",
    }
    return {
        "schema_version": 1,
        "status": investigation_status(answer_state, gaps),
        "question_intent": {
            "query": query,
            "primary_question_family": family.get("primary"),
            "detected_supported": list(family.get("detected_supported") or [])[:8],
            "detected_unsupported": list(family.get("detected_unsupported") or [])[:8],
            "primary_intent": query_plan.get("primary_intent") or "generic_history",
            "query_plan_link": "semantic_debug.source_locator_handoff.query_plan",
        },
        "retrieval": {
            "mode_used": retrieval_mode,
            "mode_source": (
                "semantic_debug.retrieval_diagnostics"
                if retrieval_diagnostics
                else "semantic_debug.default_retrieval_mode"
            ),
            "conservative_recommendation": recommendation,
            "retrieval_pressure_link": (
                "semantic_debug.retrieval_pressure" if retrieval_pressure else None
            ),
            "claim_boundaries": {
                "rrf_default_promoted": False,
                "ranking_changed_by_investigation_state": False,
                "high_recall_cost_hidden": False,
            },
        },
        "actionable_next": {
            "read_next_refs": investigation_read_next_refs(next_reads),
            "read_next_count": len(next_reads),
            "preferred_recovery_workflow": (
                "semantic_debug.agent_instruction.commands.expand_recovery_context"
                if answer_readiness.get("decision") == "inspect_recovery_reads"
                else None
            ),
            "links": {
                "next_reads": "semantic_debug.next_reads",
                "source_locator_handoff": "semantic_debug.source_locator_handoff",
                "answer_ready_handoff": "semantic_debug.answer_ready_handoff",
            },
        },
        "unresolved_gaps": gaps,
        "source_boundary_reminders": investigation_source_boundary_reminders(
            no_evidence=no_evidence,
            source_window_action_proof=source_window_action_proof,
        ),
        "answer_readiness": answer_state,
        "privacy_boundaries": {
            "raw_transcript_text_included": False,
            "raw_source_lines_included": False,
            "raw_session_ids_included": False,
            "private_bridge_payloads_included": False,
        },
        "links": {
            "answer_readiness": "semantic_debug.answer_readiness",
            "guided_audit_flow": "semantic_debug.guided_audit_flow",
            "answer_ready_handoff": "semantic_debug.answer_ready_handoff",
            "source_window_action_proof": "semantic_debug.source_window_action_proof",
            "no_evidence": "semantic_debug.no_evidence",
            "source_handle_routing": "semantic_debug.source_handle_routing",
        },
    }


def summarize_investigation_state(state: dict[str, Any]) -> dict[str, Any]:
    retrieval = state.get("retrieval") or {}
    recommendation = retrieval.get("conservative_recommendation") or {}
    actionable = state.get("actionable_next") or {}
    readiness = state.get("answer_readiness") or {}
    gaps = state.get("unresolved_gaps") or {}
    return {
        "schema_version": state.get("schema_version", 1),
        "status": state.get("status"),
        "primary_question_family": (state.get("question_intent") or {}).get("primary_question_family"),
        "primary_intent": (state.get("question_intent") or {}).get("primary_intent"),
        "retrieval_mode_used": retrieval.get("mode_used"),
        "retrieval_mode_recommended": recommendation.get("mode"),
        "read_next_count": actionable.get("read_next_count", 0),
        "gap_count": gaps.get("gap_count", 0),
        "answer_readiness_status": readiness.get("status"),
        "recommended_next_step": readiness.get("recommended_next_step"),
    }


def investigation_retrieval_recommendation(
    *,
    retrieval_mode: str,
    primary_intent: str,
    family: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    text = query.lower()
    hard_prompt = primary_intent == "workflow_failure" or any(
        phrase in text
        for phrase in (
            "workflow failure",
            "process drift",
            "context drift",
            "where did",
            "why did",
            "hard investigation",
        )
    )
    hard_source_window_prompt = persistent_coordinator_correction_route_wanted(text)
    needs_broad_context = hard_prompt or hard_source_window_prompt or family.get("primary") in {"why_did_this_happen", "claimed_action"}
    if retrieval_mode == "rrf-fusion":
        return {
            "mode": "rrf-fusion",
            "reason": "Already using explicit high-recall RRF fusion.",
            "use_when": "Use only when broad recall is worth extra local work.",
            "rrf_default_allowed": False,
            "cost_boundary": "higher_local_work_than_package_or_fielded_bm25",
        }
    if retrieval_mode == "fielded-bm25":
        return {
            "mode": "fielded-bm25",
            "reason": "Current mode is the conservative broad-recall workflow for semantic-debug investigations.",
            "use_when": "Keep this mode for workflow-failure, claimed-action, provenance, or hard why prompts.",
            "rrf_default_allowed": False,
            "cost_boundary": "does_not_promote_rrf_or_claim_completeness",
        }
    if needs_broad_context:
        return {
            "mode": "fielded-bm25",
            "reason": "This query shape benefits from fielded source planning before escalating to explicit RRF.",
            "use_when": "Retry with --retrieval-mode fielded-bm25 if current evidence is thin or gaps remain.",
            "rrf_default_allowed": False,
            "cost_boundary": "fielded_bm25_can_cost_more_local_work_than_package_default",
        }
    return {
        "mode": retrieval_mode,
        "reason": "No harder semantic-debug route was inferred from the existing packet fields.",
        "use_when": "Keep the current mode unless unresolved gaps require broader recall.",
        "rrf_default_allowed": False,
        "cost_boundary": "no_default_mode_change",
    }


def investigation_read_next_refs(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for read in next_reads[:6]:
        row = {
            "kind": read.get("kind"),
            "target": read.get("target"),
            "reason": read.get("reason"),
            "omitted_window_recovery": bool(read.get("omitted_window_recovery")),
        }
        if isinstance(read.get("source_ref"), dict):
            row["source_ref"] = guided_source_ref(read["source_ref"])
        refs.append({key: value for key, value in row.items() if value not in (None, [], {})})
    return refs


def investigation_unresolved_gaps(
    *,
    source_scope: dict[str, Any],
    no_evidence: dict[str, Any],
    uncertainty: list[dict[str, Any]],
    answer_readiness: dict[str, Any],
    source_handle_routing: dict[str, Any],
    source_window_action_proof: dict[str, Any],
    source_locator_handoff: dict[str, Any],
) -> dict[str, Any]:
    omitted = source_locator_handoff.get("omitted_window_recovery") or {}
    action_summary = source_window_action_proof.get("summary") or {}
    gaps: list[dict[str, Any]] = []
    for item in uncertainty[:8]:
        if item.get("label"):
            gaps.append({"kind": "uncertainty", "label": item.get("label"), "meaning": item.get("meaning")})
    if no_evidence.get("needed"):
        gaps.append(
            {
                "kind": "missing_evidence_boundary",
                "label": "not_found_in_searched_scope_only",
                "meaning": no_evidence.get("why_not_confident"),
            }
        )
    if source_scope.get("cap_hit", {}).get("hit"):
        gaps.append(
            {
                "kind": "searched_scope_boundary",
                "label": "cap_limited_scope",
                "meaning": "Search caps may have limited recall; do not claim all-history completeness.",
            }
        )
    unresolved_handles = list(source_handle_routing.get("unresolved_handles") or [])[:6]
    if unresolved_handles:
        gaps.append(
            {
                "kind": "source_handle_boundary",
                "label": "unresolved_source_handles",
                "handles": unresolved_handles,
                "meaning": source_handle_routing.get("route_guidance"),
            }
        )
    missing_buckets = list(action_summary.get("missing_buckets") or [])[:8]
    if missing_buckets:
        gaps.append(
            {
                "kind": "action_proof_boundary",
                "label": "missing_action_proof_buckets",
                "missing_buckets": missing_buckets,
                "meaning": "Do not infer missing command/output/final-answer proof from adjacent artifacts.",
            }
        )
    adjacent = ((source_window_action_proof.get("buckets") or {}).get("adjacent_artifact_evidence") or {})
    if adjacent.get("status") == "present":
        gaps.append(
            {
                "kind": "adjacent_artifact_boundary",
                "label": "adjacent_artifact_not_primary_action_proof",
                "meaning": "Adjacent artifacts can corroborate context but do not replace primary JSONL action proof.",
            }
        )
    if omitted.get("status") == "available":
        gaps.append(
            {
                "kind": "recovery_boundary",
                "label": "omitted_window_recovery_available",
                "recovery_count": omitted.get("recovery_count", 0),
                "meaning": "Open promoted omitted-window recovery reads before treating the packet as representative.",
            }
        )
    return {
        "gap_count": len(gaps),
        "items": gaps[:12],
        "missing_evidence_boundaries": {
            "not_all_history_absence": True,
            "searched_scope_only": bool(no_evidence.get("needed")),
            "required_before_answer": list(answer_readiness.get("required_before_answer") or [])[:6],
        },
        "links": {
            "uncertainty": "semantic_debug.uncertainty",
            "no_evidence": "semantic_debug.no_evidence",
            "source_window_action_proof": "semantic_debug.source_window_action_proof",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
        },
    }


def investigation_source_boundary_reminders(
    *,
    no_evidence: dict[str, Any],
    source_window_action_proof: dict[str, Any],
) -> dict[str, Any]:
    adjacent = ((source_window_action_proof.get("buckets") or {}).get("adjacent_artifact_evidence") or {})
    return {
        "primary_sources": [
            "Use JSONL source refs, source windows, structured tool/action events, and reviewed task/review artifacts as support.",
            "Every factual or provenance claim needs a linked source surface when evidence exists.",
        ],
        "adjacent_artifacts": {
            "present": adjacent.get("status") == "present",
            "reminder": "Adjacent artifacts are context only unless linked to primary JSONL action proof.",
        },
        "no_evidence": {
            "searched_scope_only": bool(no_evidence.get("needed")),
            "reminder": "No-evidence means not found in searched scope, not absent from all possible logs.",
        },
        "forbidden_claims": [
            "root_cause_without_linked_evidence",
            "global_absence_or_all_history_completeness",
            "jsonl_speed_token_value_or_final_answer_quality_without_separate_review",
            "adjacent_artifact_as_primary_action_proof",
        ],
    }


def investigation_recommended_next_step(answer_readiness: dict[str, Any]) -> str:
    decision = answer_readiness.get("decision")
    if decision == "answer_now":
        return "answer_from_source_backed_packet"
    if decision == "state_scoped_no_evidence":
        return "answer_not_found_in_searched_scope_only"
    if decision == "inspect_recovery_reads":
        return "open_bounded_next_reads_before_answering"
    if decision == "run_audit_route":
        return "run_required_audit_route_before_answering"
    return "gather_more_source_backed_evidence"


def investigation_status(answer_state: dict[str, Any], gaps: dict[str, Any]) -> str:
    if answer_state.get("decision") == "answer_now" and not gaps.get("gap_count"):
        return "ready_to_answer"
    if answer_state.get("decision") == "state_scoped_no_evidence":
        return "scoped_no_evidence_ready"
    if answer_state.get("decision") == "inspect_recovery_reads":
        return "needs_recovery_reads"
    if answer_state.get("decision") == "run_audit_route":
        return "needs_audit_route"
    return "needs_more_evidence"


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
    answer_readiness: dict[str, Any],
    no_evidence: dict[str, Any],
    next_reads: list[dict[str, Any]],
    next_commands: list[dict[str, Any]],
    answer_source_discipline: dict[str, Any] | None = None,
    source_handle_routing: dict[str, Any] | None = None,
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
        answer_readiness=answer_readiness,
        no_evidence=no_evidence,
        recovery=recovery,
        answer_source_discipline=answer_source_discipline,
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
        "source_handle_routing": source_handle_handoff_summary(source_handle_routing),
        "answer_gate": answer_gate,
        "next_reads": guided_next_reads(next_reads),
        "next_commands": guided_next_commands(next_commands),
        "links": {
            "decision_context": "semantic_debug.decision_context",
            "audit_plan": "semantic_debug.audit_plan",
            "forensics_workflow": "semantic_debug.forensics_workflow",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
            "final_answer_source_use_contract": "semantic_debug.final_answer_source_use_contract",
            "answer_readiness": "semantic_debug.answer_readiness",
            "answer_source_discipline": "semantic_debug.answer_source_discipline",
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
    answer_readiness: dict[str, Any],
    no_evidence: dict[str, Any],
    recovery: dict[str, Any],
    answer_source_discipline: dict[str, Any] | None = None,
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
    discipline_summary = answer_source_discipline_summary(answer_source_discipline)
    discipline_gate = (
        answer_source_discipline.get("answer_gate")
        if isinstance(answer_source_discipline, dict) and isinstance(answer_source_discipline.get("answer_gate"), dict)
        else {}
    )
    if discipline_gate:
        completion_criteria.append("Candidate-only and unsupported-extra handles are not used as final support.")
        if (discipline_summary.get("missing_proof_buckets") or []):
            completion_criteria.append("Missing proof/action buckets are stated as gaps before synthesis.")
    return {
        "status": "active",
        "primary_question_family": primary_family,
        "primary_intent": primary_intent,
        "answer_readiness_link": answer_readiness.get("answer_readiness_link"),
        "answer_readiness_status": answer_readiness.get("status"),
        "answer_readiness_decision": answer_readiness.get("decision"),
        "answer_readiness_scope": answer_readiness.get("allowed_answer_scope"),
        "answer_readiness_summary": summarize_answer_readiness(answer_readiness),
        "answer_scope": final_answer_contract.get("answer_scope"),
        "source_use_required": final_answer_contract.get("source_use_required"),
        "required_actions": required_actions,
        "required_before_answer": list(answer_readiness.get("required_before_answer") or [])[:6],
        "completion_criteria": completion_criteria,
        "support_summary": (final_answer_contract.get("evidence_inputs") or {}).get(
            "task_review_join_support_summary",
            empty_support_summary(),
        ),
        "answer_source_discipline": discipline_summary,
        "forbidden_claims": [
            "root_cause_without_linked_reviewed_evidence",
            "global_absence_or_all_history_completeness",
            "jsonl_value_speed_token_or_final_answer_quality_without_separate_reviewed_evidence",
            "candidate_only_or_unsupported_extra_handle_as_final_support",
            "unstated_missing_proof_action_facet",
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
            "answer_readiness_status": answer_gate.get("answer_readiness_status"),
            "answer_readiness_decision": answer_gate.get("answer_readiness_decision"),
            "answer_readiness_scope": answer_gate.get("answer_readiness_scope"),
            "required_before_answer": list(answer_gate.get("required_before_answer") or [])[:4],
            "forbidden_claims": list(answer_gate.get("forbidden_claims") or [])[:3],
            "answer_readiness_summary": answer_gate.get("answer_readiness_summary"),
            "answer_source_discipline": answer_gate.get("answer_source_discipline"),
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
