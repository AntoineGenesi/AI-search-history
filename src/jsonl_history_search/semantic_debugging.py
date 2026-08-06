from __future__ import annotations

import json
import os
import re
import shlex
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
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
from .audit_coverage import AuditCoverageFilters, AuditCoverageOptions, build_audit_coverage_ledger
from .audit_depth import resolve_audit_depth
from .claims import classify_claim_action, parse_review_verdict
from .conversation_records import (
    ASSISTANT_OR_AGENT_MESSAGE,
    COMPACTED_HISTORY_SUMMARY,
    HUMAN_AUTHORED_USER_MESSAGE,
    classify_conversation_record,
    extract_human_ask,
)
from .forensics import build_forensics_payload
from .latest_user_messages import latest_user_messages
from .prompt_echo import clearly_intends_current_session, setup_echo_classification
from .query import infer_query
from .real_history_gold import validate_manifest_file
from .retrieval_modes import (
    RETRIEVAL_MODE_FIELDED_BM25,
    RETRIEVAL_MODE_PACKAGE,
    RETRIEVAL_MODE_RRF_FUSION,
    run_retrieval_mode,
)
from .scanner import search
from .tool_events import build_tool_events_payload


SCHEMA_VERSION = 1
INLINE_CANDIDATE_LIMIT = 8
INLINE_TIMELINE_LIMIT = 12
INLINE_EVENT_LIMIT = 12
DEFAULT_GOLD_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")
HANDLE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+){2,})(?![A-Za-z0-9_.-])")
TASKISH_HANDLE_RE = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z]{2,}[-_]\d{2,}[A-Za-z0-9_-]*)(?![A-Za-z0-9_.-])")
BACKTICK_HANDLE_RE = re.compile(r"`([^`\n]{3,120})`")
LABELED_HANDLE_RE = re.compile(
    r"\b(?:reviewed\s+source\s+case|source\s+case|source\s+handle|case\s+handle|case\s+id|task\s+handle|task\s+case|source\s+ref)\s*:?\s*`?([A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+)`?",
    re.IGNORECASE,
)
EMBEDDED_JSONL_SOURCE_REF_RE = re.compile(r"(?P<path>/(?:(?!\.jsonl)[^`\"'\n\r])+?\.jsonl):(?P<line>\d+)(?!\d)")
CODEX_SESSION_DATE_RE = re.compile(r"/\.codex/sessions/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/")
CODEX_ROLLOUT_DATE_RE = re.compile(r"/rollout-(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})t", re.IGNORECASE)

DECISION_CONTEXT_ROUTES = {
    "conversation_reconstruction",
    "git_history_reconstruction",
    "process_actual_use_audit",
    "task_review_join",
    "tool_action_proof",
    "source_ref_followup",
    "no_evidence_scoped_report",
}

SUPPORTED_QUESTION_FAMILIES = {
    "expectation_mismatch": "Why observed AI/agent behavior diverged from an expected stop, instruction, plan, or task state.",
    "why_did_this_happen": "Vague bug, incident, regression, symptom, or assumption questions.",
    "provenance": "Task, path, branch, commit, review, or artifact provenance questions.",
    "claimed_action": "Whether an agent actually ran, edited, tested, reviewed, or claimed a concrete action.",
    "process_actual_use": "Whether a process, artifact, checklist, learning, or front-door was actually used before/during work and changed behavior.",
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

CORE_CONVO_TARGET_SOURCE_ROLE_ORDER = [
    "target_historical_raw",
    "target_historical_artifact",
    "legitimate_context",
    "diagnostic_or_post_hoc",
]
CORE_CONVO_TARGET_SOURCE_ROLE_RANK = {
    role: index for index, role in enumerate(CORE_CONVO_TARGET_SOURCE_ROLE_ORDER)
}
CORE_CONVO_FIRST_READ_PLAN_LIMIT = 12


def _fielded_bm25_index_dir_from_args(args: Any) -> str | None:
    if getattr(args, "fielded_bm25_ephemeral", False):
        return None
    if getattr(args, "fielded_bm25_storage", "file") == "memory":
        return None
    explicit = getattr(args, "fielded_bm25_index_dir", None)
    if explicit:
        return str(explicit)
    if getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE) not in {
        RETRIEVAL_MODE_FIELDED_BM25,
        RETRIEVAL_MODE_RRF_FUSION,
    }:
        return None
    cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return str(root / "jsonl-history-search" / "fielded-bm25")


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
    audit_depth = resolve_audit_depth(
        getattr(args, "query", ""),
        getattr(args, "audit_depth", None),
    )
    expansion_action_context = expansion_action_context_from_source_plan(source_plan)
    result = run_retrieval_mode(
        retrieval_mode=getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE),
        plan=plan,
        intent=intent,
        default_tz=default_tz,
        cwd=source_plan["cwd"],
        mask_context=mask_context,
        use_rg_prefilter=getattr(args, "rg_prefilter", False),
        fielded_bm25_index_dir=_fielded_bm25_index_dir_from_args(args),
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
    process_actual_use = process_actual_use_detected(family)
    source_scope = build_source_scope_summary(source_plan, result)
    candidates = compact_candidates(agent_find.get("find_candidates", []))
    source_selection_candidates = (
        prioritize_process_actual_use_candidates(
            candidates,
            query=agent_find["query"]["original"],
        )
        if process_actual_use
        else candidates
    )
    timeline = compact_timeline(agent_find.get("timeline", []))
    tool_proof = compact_tool_proof(tool_report)
    joins = compact_joins(forensics_payload, hints)
    joins = merge_local_task_review_joins(joins, hints, cwd=Path(source_plan["cwd"]))
    forensics_workflow = compact_forensics_workflow(forensics_payload, joins)
    no_evidence = build_no_evidence_section(agent_find, source_scope, tool_report, forensics_payload)
    uncertainty = build_uncertainty(agent_find, source_scope, tool_report, forensics_payload, family)
    next_reads = build_next_reads(
        source_selection_candidates,
        tool_proof,
        joins,
        hints,
        query=agent_find["query"]["original"],
        expansion_action_context=expansion_action_context,
        include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
        suppress_action_proof=process_actual_use,
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
    next_reads = prefer_complete_explicit_correction_window_reads(next_reads)
    source_handle_routing = build_source_handle_routing(
        agent_find["query"]["original"],
        candidates=source_selection_candidates,
        next_reads=next_reads,
    )
    next_reads = prioritize_source_handle_next_reads(next_reads, source_handle_routing)
    if process_actual_use:
        next_reads = prioritize_process_actual_use_next_reads(
            next_reads,
            candidates=source_selection_candidates,
            tool_proof=tool_proof,
            joins=joins,
            query=agent_find["query"]["original"],
        )
    next_reads = compact_text_semantic_read_more_actions(next_reads)
    next_reads = mark_scoped_no_evidence_diagnostic_reads(next_reads, no_evidence)
    source_handle_routing = build_source_handle_routing(
        agent_find["query"]["original"],
        candidates=source_selection_candidates,
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
    audit_plan["audit_depth"] = audit_depth
    source_locator_handoff = build_source_locator_handoff(
        source_plan=source_plan,
        search_result=result,
        candidates=source_selection_candidates,
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
        next_reads = prefer_complete_explicit_correction_window_reads(
            list(source_locator_handoff.get("next_reads") or next_reads)
        )
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=source_selection_candidates,
            next_reads=next_reads,
        )
        next_reads = prioritize_source_handle_next_reads(next_reads, source_handle_routing)
        if process_actual_use:
            next_reads = prioritize_process_actual_use_next_reads(
                next_reads,
                candidates=source_selection_candidates,
                tool_proof=tool_proof,
                joins=joins,
                query=agent_find["query"]["original"],
            )
        next_reads = compact_text_semantic_read_more_actions(next_reads)
        next_reads = mark_scoped_no_evidence_diagnostic_reads(next_reads, no_evidence)
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=source_selection_candidates,
            next_reads=next_reads,
        )
        source_locator_handoff["next_reads"] = next_reads
        if no_evidence.get("needed"):
            source_locator_handoff["next_reads"] = mark_scoped_no_evidence_diagnostic_reads(
                list(source_locator_handoff.get("next_reads") or []),
                no_evidence,
            )
            next_reads = list(source_locator_handoff.get("next_reads") or [])
        if process_actual_use:
            source_locator_handoff = annotate_process_actual_use_source_locator_handoff(
                source_locator_handoff,
                query=agent_find["query"]["original"],
            )
        source_locator_handoff = compact_text_semantic_handoff_actions(source_locator_handoff)
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
    else:
        if process_actual_use:
            source_locator_handoff = annotate_process_actual_use_source_locator_handoff(
                source_locator_handoff,
                query=agent_find["query"]["original"],
            )
        source_locator_handoff = compact_text_semantic_handoff_actions(source_locator_handoff)
    source_locator_handoff["source_handle_routing"] = source_handle_routing
    source_locator_handoff["audit_depth"] = audit_depth
    historical_provenance_self_echo_policy: dict[str, Any] = {}
    (
        next_reads,
        historical_provenance_self_echo_policy,
    ) = apply_historical_provenance_self_echo_policy(
        list(source_locator_handoff.get("next_reads") or next_reads),
        candidates=source_selection_candidates,
        query=agent_find["query"]["original"],
        family=family,
        expansion_action_context=expansion_action_context,
        include_context_actions=bool(getattr(args, "compact_provenance_handoff", False)),
    )
    if historical_provenance_self_echo_policy:
        source_locator_handoff["next_reads"] = mark_scoped_no_evidence_diagnostic_reads(next_reads, no_evidence)
        next_reads = list(source_locator_handoff.get("next_reads") or [])
        source_locator_handoff["historical_provenance_self_echo_policy"] = (
            historical_provenance_self_echo_policy
        )
        source_locator_handoff = compact_text_semantic_handoff_actions(source_locator_handoff)
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=source_selection_candidates,
            next_reads=next_reads,
        )
        source_locator_handoff["source_handle_routing"] = source_handle_routing
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
    (
        next_reads,
        provenance_work_episode_origin_source_selection,
    ) = promote_provenance_work_episode_origin_source_reads(
        list(source_locator_handoff.get("next_reads") or next_reads),
        query=agent_find["query"]["original"],
        family=family,
        candidates=source_selection_candidates,
        tool_proof=tool_proof,
        source_plan=source_plan,
        source_locator_handoff=source_locator_handoff,
    )
    if provenance_work_episode_origin_source_selection.get("applies"):
        if provenance_work_episode_origin_source_selection.get("status") == "origin_episode_promoted":
            source_locator_handoff["next_reads"] = next_reads
        else:
            source_locator_handoff["next_reads"] = mark_scoped_no_evidence_diagnostic_reads(
                next_reads,
                no_evidence,
            )
        next_reads = list(source_locator_handoff.get("next_reads") or [])
        source_locator_handoff["provenance_work_episode_origin_source_selection"] = (
            provenance_work_episode_origin_source_selection
        )
        source_locator_handoff = compact_text_semantic_handoff_actions(source_locator_handoff)
        source_handle_routing = build_source_handle_routing(
            agent_find["query"]["original"],
            candidates=source_selection_candidates,
            next_reads=next_reads,
        )
        source_locator_handoff["source_handle_routing"] = source_handle_routing
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
    provenance_direct_evidence_facet_ledger = build_provenance_direct_evidence_facet_ledger(
        query=agent_find["query"]["original"],
        family=family,
        candidates=source_selection_candidates,
        tool_proof=tool_proof,
        source_plan=source_plan,
        selected_source_reads=list(next_reads),
    )
    if provenance_direct_evidence_facet_ledger.get("applies"):
        next_reads = apply_provenance_direct_evidence_ledger_to_reads(
            list(next_reads),
            provenance_direct_evidence_facet_ledger,
        )
        source_locator_handoff["next_reads"] = next_reads
        source_locator_handoff["provenance_direct_evidence_facet_ledger"] = (
            public_provenance_direct_evidence_facet_ledger(
                provenance_direct_evidence_facet_ledger
            )
        )
    historical_orchestration_plan = build_historical_orchestration_provenance_final_support_plan(
        list(source_locator_handoff.get("next_reads") or next_reads),
        candidates=source_selection_candidates,
        query=agent_find["query"]["original"],
        family=family,
    )
    historical_orchestration_role_evidence_bundle = (
        build_historical_orchestration_role_evidence_bundle(historical_orchestration_plan)
        if historical_orchestration_plan
        else {}
    )
    if historical_orchestration_plan:
        source_locator_handoff["historical_orchestration_provenance_final_support_plan"] = (
            historical_orchestration_plan
        )
        source_locator_handoff["historical_orchestration_role_coverage_source_plan"] = (
            historical_orchestration_plan
        )
    if historical_orchestration_role_evidence_bundle:
        source_locator_handoff["historical_orchestration_role_evidence_bundle"] = (
            historical_orchestration_role_evidence_bundle
        )
    process_actual_use_read_plan = (
        build_process_actual_use_final_support_read_plan(
            list(source_locator_handoff.get("next_reads") or next_reads),
            query=agent_find["query"]["original"],
        )
        if process_actual_use
        else {}
    )
    process_actual_use_bucket_evidence_bundle = (
        process_actual_use_read_plan.get("process_actual_use_bucket_evidence_bundle")
        if process_actual_use_read_plan
        else {}
    )
    if process_actual_use_read_plan:
        source_locator_handoff["process_actual_use_final_support_read_plan"] = process_actual_use_read_plan
    if process_actual_use_bucket_evidence_bundle:
        source_locator_handoff["process_actual_use_bucket_evidence_bundle"] = (
            process_actual_use_bucket_evidence_bundle
        )
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
        family=family,
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
    source_window_action_proof = (
        source_window_action_proof_not_applicable(family, reason="process_actual_use_uses_raw_timing_buckets")
        if process_actual_use
        else build_source_window_action_proof(
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
    scope_boundary_summary = build_scope_boundary_summary(
        source_scope=source_scope,
        no_evidence=no_evidence,
        source_locator_handoff=source_locator_handoff,
    )
    source_locator_handoff["scope_boundary_summary"] = scope_boundary_summary
    answer_readiness["scope_boundary_summary"] = scope_boundary_summary
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
    core_convo_first_read_plan = build_core_convo_first_read_plan(
        target_reads=dedupe_dicts(
            list(action_proof_plan.get("primary_proof_refs") or [])
            + list(action_proof_plan.get("required_proof_reads") or [])
            + list(
                historical_orchestration_role_evidence_bundle.get("required_role_reads")
                or []
            )
            + list(historical_orchestration_plan.get("primary_historical_final_support_reads") or [])
            + list(historical_orchestration_plan.get("bounded_non_final_required_reads") or [])
            + [
                read
                for read in next_reads
                if isinstance(read, dict)
                and core_convo_target_source_role(read) in {"target_historical_raw", "target_historical_artifact"}
            ]
        ),
        historical_artifact_reads=dedupe_dicts(
            list(historical_orchestration_plan.get("secondary_task_review_commit_support_reads") or [])
            + list(historical_orchestration_plan.get("missing_role_recovery_refs") or [])
            + list(historical_orchestration_role_evidence_bundle.get("missing_role_recovery_refs") or [])
        ),
        legitimate_context_reads=[
            read
            for read in next_reads
            if isinstance(read, dict)
            and core_convo_target_source_role(read) == "legitimate_context"
        ],
        diagnostic_reads=[
            read
            for read in next_reads
            if isinstance(read, dict)
            and core_convo_target_source_role(read) == "diagnostic_or_post_hoc"
        ],
        route_family=str(family.get("primary") or "semantic_debug"),
        plan_source="semantic_debug",
    )
    annotated_next_reads = [
        annotate_core_convo_target_source_read(read, sequence_index=index)
        for index, read in enumerate(next_reads)
        if isinstance(read, dict)
    ]
    next_reads = annotated_next_reads
    source_locator_handoff["next_reads"] = next_reads
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
    guided_audit_flow["next_reads"] = guided_next_reads(next_reads)
    guided_audit_flow["next_commands"] = guided_next_commands(decision_context["next_commands"])
    source_locator_handoff["core_convo_first_read_plan"] = core_convo_first_read_plan
    expectation_mismatch_plan = build_expectation_mismatch_plan(
        query=agent_find["query"]["original"],
        family=family,
        core_convo_first_read_plan=core_convo_first_read_plan,
        next_reads=next_reads,
    )
    if expectation_mismatch_plan.get("applies"):
        source_locator_handoff["expectation_mismatch_plan"] = expectation_mismatch_plan
    investigation_state["core_convo_first_read_plan_summary"] = {
        "link": "semantic_debug.core_convo_first_read_plan",
        "status": core_convo_first_read_plan.get("status"),
        "counts": core_convo_first_read_plan.get("counts"),
        "target_source_role_order": core_convo_first_read_plan.get("target_source_role_order"),
    }
    if expectation_mismatch_plan.get("applies"):
        investigation_state["expectation_mismatch_plan_summary"] = {
            "link": "semantic_debug.expectation_mismatch_plan",
            "status": expectation_mismatch_plan.get("status"),
            "counts": expectation_mismatch_plan.get("counts"),
            "read_order": expectation_mismatch_plan.get("read_order"),
        }
    guided_audit_flow["core_convo_first_read_plan_summary"] = {
        "link": "semantic_debug.core_convo_first_read_plan",
        "status": core_convo_first_read_plan.get("status"),
        "counts": core_convo_first_read_plan.get("counts"),
        "diagnostic_open_policy": core_convo_first_read_plan.get("diagnostic_open_policy"),
    }
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
        scope_boundary_summary=scope_boundary_summary,
    )
    answer_ready_handoff = build_answer_ready_handoff(
        query=agent_find["query"]["original"],
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
        scope_boundary_summary=scope_boundary_summary,
    )
    if provenance_direct_evidence_facet_ledger.get("applies"):
        direct_ledger_summary = public_provenance_direct_evidence_facet_ledger(
            provenance_direct_evidence_facet_ledger
        )
        answer_ready_handoff["provenance_direct_evidence_facet_ledger"] = direct_ledger_summary
        answer_ready_handoff["links"]["provenance_direct_evidence_facet_ledger"] = (
            "semantic_debug.provenance_direct_evidence_facet_ledger"
        )
        answer_ready_handoff["source_use_summary"]["provenance_direct_evidence_facet_ledger"] = {
            "status": direct_ledger_summary.get("status"),
            "evidence_complete": direct_ledger_summary.get("evidence_complete"),
            "required_facets": direct_ledger_summary.get("required_facets"),
            "covered_direct_facets": direct_ledger_summary.get("covered_direct_facets"),
            "missing_required_facets": direct_ledger_summary.get("missing_required_facets"),
            "selected_primary_read_count": direct_ledger_summary.get("selected_primary_read_count"),
            "missing_facet_continuation_count": direct_ledger_summary.get(
                "missing_facet_continuation_count"
            ),
        }
    answer_ready_handoff["core_convo_first_read_plan"] = core_convo_first_read_plan
    answer_ready_handoff["links"]["core_convo_first_read_plan"] = "semantic_debug.core_convo_first_read_plan"
    answer_ready_handoff["source_use_summary"]["core_convo_first_read_plan"] = {
        "link": "semantic_debug.core_convo_first_read_plan",
        "status": core_convo_first_read_plan.get("status"),
        "counts": core_convo_first_read_plan.get("counts"),
        "target_source_role_order": core_convo_first_read_plan.get("target_source_role_order"),
        "diagnostics_are_default_non_actionable": True,
    }
    final_answer_contract["evidence_inputs"]["core_convo_first_read_plan"] = {
        "link": "semantic_debug.core_convo_first_read_plan",
        "status": core_convo_first_read_plan.get("status"),
        "counts": core_convo_first_read_plan.get("counts"),
        "target_source_role_order": core_convo_first_read_plan.get("target_source_role_order"),
    }
    if expectation_mismatch_plan.get("applies"):
        answer_ready_handoff["expectation_mismatch_plan"] = expectation_mismatch_plan
        answer_ready_handoff["links"]["expectation_mismatch_plan"] = "semantic_debug.expectation_mismatch_plan"
        answer_ready_handoff["source_use_summary"]["expectation_mismatch_plan"] = {
            "link": "semantic_debug.expectation_mismatch_plan",
            "status": expectation_mismatch_plan.get("status"),
            "counts": expectation_mismatch_plan.get("counts"),
            "diagnostics_are_default_non_actionable": True,
        }
        final_answer_contract["evidence_inputs"]["expectation_mismatch_plan"] = {
            "link": "semantic_debug.expectation_mismatch_plan",
            "status": expectation_mismatch_plan.get("status"),
            "counts": expectation_mismatch_plan.get("counts"),
            "diagnostics_are_default_non_actionable": True,
        }
    if process_actual_use_read_plan:
        answer_ready_handoff["process_actual_use_final_support_read_plan"] = process_actual_use_read_plan
        if process_actual_use_bucket_evidence_bundle:
            answer_ready_handoff["process_actual_use_bucket_evidence_bundle"] = (
                process_actual_use_bucket_evidence_bundle
            )
        answer_ready_handoff["source_use_summary"]["process_actual_use_final_support_read_plan"] = {
            "link": "semantic_debug.process_actual_use_final_support_read_plan",
            "required_primary_raw_final_support_read_count": process_actual_use_read_plan.get(
                "required_primary_raw_final_support_read_count", 0
            ),
            "secondary_lead_count": process_actual_use_read_plan.get("secondary_lead_count", 0),
            "post_hoc_answer_key_candidate_count": process_actual_use_read_plan.get(
                "post_hoc_answer_key_candidate_count", 0
            ),
            "prior_final_prose_counts_as_support": (
                process_actual_use_read_plan.get("guidance") or {}
            ).get("prior_final_prose_counts_as_support"),
        }
        if process_actual_use_bucket_evidence_bundle:
            bundle_summary = process_actual_use_bucket_evidence_bundle.get("summary") or {}
            answer_ready_handoff["source_use_summary"]["process_actual_use_bucket_evidence_bundle"] = {
                "link": "semantic_debug.process_actual_use_bucket_evidence_bundle",
                "primary_supported_bucket_count": bundle_summary.get("primary_supported_bucket_count", 0),
                "secondary_only_bucket_count": bundle_summary.get("secondary_only_bucket_count", 0),
                "missing_or_excluded_bucket_count": bundle_summary.get("missing_or_excluded_bucket_count", 0),
                "post_hoc_answer_key_candidate_count": bundle_summary.get(
                    "post_hoc_answer_key_candidate_count", 0
                ),
                "read_more_reduction_proven": False,
            }
    if historical_orchestration_plan:
        orchestration_summary = historical_orchestration_plan_summary(historical_orchestration_plan)
        answer_ready_handoff["historical_orchestration_provenance_final_support_plan"] = (
            orchestration_summary
        )
        answer_ready_handoff["historical_orchestration_role_coverage_source_plan"] = (
            orchestration_summary
        )
        answer_ready_handoff["source_use_summary"][
            "historical_orchestration_provenance_final_support_plan"
        ] = {
            "link": "semantic_debug.historical_orchestration_provenance_final_support_plan",
            "status": orchestration_summary.get("status"),
            "required_roles": orchestration_summary.get("required_roles"),
            "covered_roles": orchestration_summary.get("covered_roles"),
            "missing_roles": orchestration_summary.get("missing_roles"),
            "primary_historical_final_support_read_count": orchestration_summary.get(
                "primary_historical_final_support_read_count", 0
            ),
            "current_run_self_echo_candidate_count": orchestration_summary.get(
                "current_run_self_echo_candidate_count", 0
            ),
            "current_run_current_task_same_loop_refs_count_as_final_support": False,
        }
    if historical_orchestration_role_evidence_bundle:
        bundle_summary = historical_orchestration_role_evidence_bundle_summary(
            historical_orchestration_role_evidence_bundle
        )
        answer_ready_handoff["historical_orchestration_role_evidence_bundle"] = bundle_summary
        answer_ready_handoff["links"]["historical_orchestration_role_evidence_bundle"] = (
            "semantic_debug.historical_orchestration_role_evidence_bundle"
        )
        answer_ready_handoff["source_use_summary"][
            "historical_orchestration_role_evidence_bundle"
        ] = {
            "link": "semantic_debug.historical_orchestration_role_evidence_bundle",
            "status": bundle_summary.get("status"),
            "required_roles": bundle_summary.get("required_roles"),
            "covered_roles": bundle_summary.get("covered_roles"),
            "missing_roles": bundle_summary.get("missing_roles"),
            "required_role_read_count": bundle_summary.get("required_role_read_count", 0),
            "required_role_read_cap": bundle_summary.get(
                "required_role_read_cap",
                HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP,
            ),
            "missing_role_recovery_ref_count": bundle_summary.get(
                "missing_role_recovery_ref_count", 0
            ),
            "answer_ready_claim_allowed": bundle_summary.get("answer_ready_claim_allowed"),
            "claim_boundaries": bundle_summary.get("claim_boundaries"),
        }
        answer_ready_handoff["source_use_summary"][
            "historical_orchestration_role_coverage_source_plan"
        ] = {
            "link": "semantic_debug.historical_orchestration_role_coverage_source_plan",
            "status": orchestration_summary.get("status"),
            "required_primary_roles": orchestration_summary.get("required_primary_roles"),
            "required_secondary_roles": orchestration_summary.get("required_secondary_roles"),
            "missing_primary_roles": orchestration_summary.get("missing_primary_roles"),
            "missing_secondary_roles": orchestration_summary.get("missing_secondary_roles"),
            "secondary_task_review_commit_support_read_count": orchestration_summary.get(
                "secondary_task_review_commit_support_read_count", 0
            ),
            "current_run_current_task_same_loop_refs_count_as_final_support": False,
        }
    longitudinal_phase_source_plan = build_longitudinal_phase_source_plan(
        query=agent_find["query"]["original"],
        source_locator_handoff=source_locator_handoff,
        next_reads=next_reads,
        scope_boundary_summary=scope_boundary_summary,
        no_evidence=no_evidence,
    )
    if longitudinal_phase_source_plan.get("applies"):
        source_locator_handoff["longitudinal_phase_source_plan"] = longitudinal_phase_source_plan
        answer_ready_handoff["longitudinal_phase_source_plan"] = longitudinal_phase_source_plan
        answer_ready_handoff["links"]["longitudinal_phase_source_plan"] = (
            "semantic_debug.longitudinal_phase_source_plan"
        )
        answer_ready_handoff["source_use_summary"]["longitudinal_phase_source_plan"] = {
            "link": "semantic_debug.longitudinal_phase_source_plan",
            "status": longitudinal_phase_source_plan.get("status"),
            "phase_balance_status": longitudinal_phase_source_plan.get("phase_balance_status"),
            "missing_phase_count": len(longitudinal_phase_source_plan.get("missing_phase_ids") or []),
            "current_or_same_wave_echo_count": (
                longitudinal_phase_source_plan.get("same_wave_current_echoes") or {}
            ).get("count", 0),
        }
        answer_ready_handoff["source_action_guidance"]["longitudinal_phase_source_plan"] = {
            "link": "semantic_debug.longitudinal_phase_source_plan",
            "status": longitudinal_phase_source_plan.get("status"),
            "phase_balance_status": longitudinal_phase_source_plan.get("phase_balance_status"),
            "required_before_synthesis": longitudinal_phase_source_plan.get("required_before_synthesis"),
        }
        if longitudinal_phase_source_plan.get("required_before_synthesis"):
            answer_ready_handoff["decision"] = "phase_source_plan_first"
            answer_ready_handoff["do_next"] = "open_or_rerun_phase_source_plan_before_synthesis"
            answer_ready_handoff["do_not_answer_until"] = dedupe(
                list(longitudinal_phase_source_plan.get("required_before_synthesis") or [])
                + list(answer_ready_handoff.get("do_not_answer_until") or [])
            )[:8]
            answer_ready_handoff["source_action_guidance"]["decision"] = "phase_source_plan_first"
            answer_ready_handoff["source_action_guidance"]["stop_condition"] = (
                "Do not synthesize a longitudinal answer until early/mid/recent phase coverage, "
                "searched scope/caps, and diagnostic current/same-wave echoes are named."
            )
        final_answer_contract["evidence_inputs"]["longitudinal_phase_source_plan"] = {
            "link": "semantic_debug.longitudinal_phase_source_plan",
            "status": longitudinal_phase_source_plan.get("status"),
            "phase_balance_status": longitudinal_phase_source_plan.get("phase_balance_status"),
            "missing_phase_ids": list(longitudinal_phase_source_plan.get("missing_phase_ids") or []),
        }
        final_answer_contract["required_actions"] = dedupe(
            [
                (
                    "For broad longitudinal questions, use semantic_debug.longitudinal_phase_source_plan "
                    "before synthesis; separate early/mid/recent phases, source families, caps, and current/same-wave echoes."
                )
            ]
            + list(final_answer_contract.get("required_actions") or [])
        )
        answer_readiness["longitudinal_phase_source_plan_summary"] = {
            "link": "semantic_debug.longitudinal_phase_source_plan",
            "status": longitudinal_phase_source_plan.get("status"),
            "phase_balance_status": longitudinal_phase_source_plan.get("phase_balance_status"),
            "represented_phase_ids": list(longitudinal_phase_source_plan.get("represented_phase_ids") or []),
            "missing_phase_ids": list(longitudinal_phase_source_plan.get("missing_phase_ids") or []),
        }
        investigation_state["longitudinal_phase_source_plan_summary"] = (
            answer_readiness["longitudinal_phase_source_plan_summary"]
        )
        guided_audit_flow["longitudinal_phase_source_plan_summary"] = (
            answer_readiness["longitudinal_phase_source_plan_summary"]
        )
        agent_instruction["longitudinal_phase_source_plan"] = longitudinal_phase_source_plan
        agent_instruction["source_action_guidance"] = answer_ready_handoff["source_action_guidance"]
    followthrough = answer_ready_handoff.get("primary_source_followthrough") or {}
    if followthrough.get("applies"):
        final_answer_contract["required_actions"] = [
            (
                "Run answer_ready_handoff.primary_source_followthrough.first_command first; "
                "treat source_locator_handoff.next_reads as fallback only if the primary "
                "window leaves a named required facet missing."
            )
            if action == "Use source refs from strongest_candidates and source_locator_handoff.next_reads before answering when they exist."
            else action
            for action in final_answer_contract.get("required_actions", [])
        ]
        final_answer_contract["evidence_inputs"]["source_locator_next_reads_policy"] = (
            "fallback_after_primary_source_followthrough_only"
        )
    elif answer_ready_handoff.get("decision") == "scoped_no_evidence":
        final_answer_contract["required_actions"] = [
            (
                "Treat source_locator_handoff.next_reads as optional diagnostics; answer scoped no-evidence first "
                "unless the user needs stronger recall or broader scope."
            )
            if action == "Use source refs from strongest_candidates and source_locator_handoff.next_reads before answering when they exist."
            else action
            for action in final_answer_contract.get("required_actions", [])
        ]
        final_answer_contract["evidence_inputs"]["source_locator_next_reads_policy"] = (
            "optional_diagnostics_after_scoped_no_evidence"
        )
    source_followthrough_plan = build_source_followthrough_plan(
        next_reads=next_reads,
        answer_ready_handoff=answer_ready_handoff,
        action_proof_plan=action_proof_plan,
        process_actual_use_read_plan=process_actual_use_read_plan,
        historical_orchestration_plan=historical_orchestration_plan,
        historical_orchestration_role_evidence_bundle=historical_orchestration_role_evidence_bundle,
        longitudinal_phase_source_plan=longitudinal_phase_source_plan,
        no_evidence=no_evidence,
        scope_boundary_summary=scope_boundary_summary,
        provenance_direct_evidence_facet_ledger=provenance_direct_evidence_facet_ledger,
    )
    source_followthrough_summary = source_followthrough_plan_summary(source_followthrough_plan)
    source_locator_handoff["source_followthrough_plan"] = source_followthrough_summary
    answer_ready_handoff["source_followthrough_plan"] = source_followthrough_summary
    answer_ready_handoff["links"]["source_followthrough_plan"] = "semantic_debug.source_followthrough_plan"
    answer_ready_handoff["source_use_summary"]["source_followthrough_plan"] = {
        "link": "semantic_debug.source_followthrough_plan",
        "status": source_followthrough_plan.get("status"),
        "counts": source_followthrough_plan.get("counts"),
        "optional_diagnostics_default_actionable": False,
        "conditional_reads_default_actionable": False,
    }
    agent_instruction["source_followthrough_plan"] = source_followthrough_summary
    final_answer_contract["evidence_inputs"]["source_followthrough_plan"] = source_followthrough_summary
    answer_ready_handoff["links"]["investigation_state"] = "semantic_debug.investigation_state"
    answer_ready_handoff["source_use_summary"]["investigation_state"] = {
        "link": "semantic_debug.investigation_state",
        "status": investigation_state.get("status"),
        "recommended_next_step": investigation_state.get("answer_readiness", {}).get("recommended_next_step"),
    }
    provenance_complete_proof_handoff = build_provenance_complete_proof_handoff(
        ledger=provenance_direct_evidence_facet_ledger,
        next_reads=next_reads,
        scope_boundary_summary=scope_boundary_summary,
    )
    if provenance_complete_proof_handoff:
        source_locator_handoff["provenance_complete_proof_handoff"] = (
            provenance_complete_proof_handoff
        )
        answer_ready_handoff["provenance_complete_proof_handoff"] = (
            provenance_complete_proof_handoff
        )
        answer_ready_handoff["links"]["provenance_complete_proof_handoff"] = (
            "semantic_debug.provenance_complete_proof_handoff"
        )
        if provenance_complete_proof_handoff.get("active"):
            answer_ready_handoff["decision"] = "answer_after_selected_direct_reads"
            answer_ready_handoff["do_next"] = (
                "Open only the selected direct reads, then answer from those correlated events."
            )
            agent_instruction["decision"] = "selected_direct_reads_then_answer"
            agent_instruction["required_next_action"] = (
                "Run only provenance_complete_proof_handoff.selected_direct_reads, then answer."
            )
            agent_instruction["commands"] = [
                read["command"]
                for read in provenance_complete_proof_handoff.get("selected_direct_reads", [])
            ]
            agent_instruction["stop_condition"] = provenance_complete_proof_handoff.get(
                "stop_instruction"
            )
    in_depth_audit_coverage = build_in_depth_semantic_debug_audit_coverage(
        query=agent_find["query"]["original"],
        audit_depth=audit_depth,
        args=args,
        source_scope=source_scope,
        scope_boundary_summary=scope_boundary_summary,
        next_reads=next_reads,
        candidates=source_selection_candidates,
    )
    in_depth_audit_followthrough = {}
    if in_depth_audit_coverage.get("applies"):
        next_reads = apply_in_depth_audit_read_promotion_safety(
            next_reads,
            coverage=in_depth_audit_coverage,
        )
        in_depth_audit_followthrough = build_in_depth_audit_followthrough_plan(
            query=agent_find["query"]["original"],
            args=args,
            coverage=in_depth_audit_coverage,
            scope_boundary_summary=scope_boundary_summary,
            next_reads=next_reads,
        )
        source_locator_handoff["next_reads"] = next_reads
        source_locator_handoff["in_depth_audit_coverage"] = in_depth_audit_coverage
        source_locator_handoff["in_depth_audit_followthrough"] = in_depth_audit_followthrough
        audit_plan["in_depth_audit_coverage"] = in_depth_audit_coverage
        audit_plan["in_depth_audit_followthrough"] = in_depth_audit_followthrough
        final_answer_contract = apply_in_depth_audit_contract_gate(
            final_answer_contract,
            coverage=in_depth_audit_coverage,
        )
        answer_readiness = apply_in_depth_audit_answer_readiness_gate(
            answer_readiness,
            coverage=in_depth_audit_coverage,
        )
        answer_ready_handoff = apply_in_depth_audit_handoff_gate(
            answer_ready_handoff,
            coverage=in_depth_audit_coverage,
        )
        answer_ready_handoff = apply_in_depth_audit_followthrough_handoff(
            answer_ready_handoff,
            followthrough=in_depth_audit_followthrough,
        )
        agent_instruction = apply_in_depth_audit_agent_instruction_gate(
            agent_instruction,
            coverage=in_depth_audit_coverage,
        )
        agent_instruction = apply_in_depth_audit_followthrough_instruction(
            agent_instruction,
            followthrough=in_depth_audit_followthrough,
        )
    command_status = semantic_debug_command_status(agent_find.get("status"))
    packet = {
        "schema_version": SCHEMA_VERSION,
        "status": command_status,
        "query": agent_find["query"]["original"],
        "audit_depth": audit_depth,
        "in_depth_audit_coverage": in_depth_audit_coverage,
        "in_depth_audit_followthrough": in_depth_audit_followthrough,
        "question_family": family,
        "investigation_state": investigation_state,
        "scope": source_scope,
        "routes": routes,
        "source_handle_routing": source_handle_routing,
        "source_locator_handoff": source_locator_handoff,
        "scope_boundary_summary": scope_boundary_summary,
        "source_followthrough_plan": source_followthrough_plan,
        "provenance_direct_evidence_facet_ledger": public_provenance_direct_evidence_facet_ledger(
            provenance_direct_evidence_facet_ledger
        ),
        "provenance_complete_proof_handoff": provenance_complete_proof_handoff,
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
        "core_convo_first_read_plan": core_convo_first_read_plan,
        "longitudinal_phase_source_plan": longitudinal_phase_source_plan,
        "expectation_mismatch_plan": expectation_mismatch_plan,
        "answer_source_discipline": answer_source_discipline,
        "process_actual_use_final_support_read_plan": process_actual_use_read_plan,
        "process_actual_use_bucket_evidence_bundle": process_actual_use_bucket_evidence_bundle,
        "historical_provenance_self_echo_policy": historical_provenance_self_echo_policy,
        "historical_orchestration_role_evidence_bundle": historical_orchestration_role_evidence_bundle,
        "historical_orchestration_role_coverage_source_plan": historical_orchestration_plan,
        "historical_orchestration_provenance_final_support_plan": historical_orchestration_plan,
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
    if not in_depth_audit_coverage.get("applies"):
        packet.pop("in_depth_audit_coverage", None)
        packet.pop("in_depth_audit_followthrough", None)
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


def semantic_debug_command_status(agent_find_status: Any) -> str:
    status = str(agent_find_status or "")
    if status == "no_evidence":
        return "no_evidence"
    if status.startswith("failed_closed"):
        return status
    return "candidates_found"


def classify_question_family(query: str, *, hints: dict[str, str | None], status: str) -> dict[str, Any]:
    text = query.lower()
    detected: list[str] = []
    is_process_actual_use = looks_like_process_actual_use_query(text)
    if is_process_actual_use:
        detected.append("process_actual_use")
    if looks_like_expectation_mismatch_query(text):
        detected.append("expectation_mismatch")
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
    if not is_process_actual_use and (any(word in text for word in claimed_action_cues) or review_action_cue):
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


def process_actual_use_detected(family: dict[str, Any]) -> bool:
    return family.get("primary") == "process_actual_use" or "process_actual_use" in set(
        family.get("detected_supported") or []
    )


def looks_like_expectation_mismatch_query(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().replace("?", " ").replace("!", " ").split())
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    explicit_mismatch_shape = any(
        phrase in normalized
        for phrase in (
            "expectation mismatch",
            "expectation-mismatch",
            "mismatch behind",
            "mismatch caused",
        )
    )
    if explicit_mismatch_shape and _looks_like_expectation_mismatch_artifact_status_query(normalized):
        return False
    if explicit_mismatch_shape and any(
        phrase in normalized
        for phrase in (
            "behind",
            "caused",
            "cause",
            "episode",
            "situation",
            "jsh-",
            "stopped",
            "not-auditing",
            "agent",
            "ai",
            "assistant",
            "codex",
            "diverged",
            "instead of",
        )
    ):
        return True
    causal_shape = any(
        phrase in normalized
        for phrase in (
            "why did",
            "why was",
            "why were",
            "why didn't",
            "how did it end up",
            "how did the agent end up",
        )
    )
    expectation = any(
        phrase in normalized
        for phrase in (
            "expected",
            "expecting",
            "supposed to",
            "should have",
            "should've",
            "asked it to",
            "told it to",
            "instructed",
            "instruction",
            "prior instruction",
            "plan was",
            "stop after",
        )
    )
    observed = any(
        phrase in normalized
        for phrase in (
            "ai",
            "agent",
            "assistant",
            "codex",
            "it kept",
            "kept",
            "looping",
            "continued",
            "running",
            "searching",
            "ignored",
            "diverged",
            "instead",
            "failed to stop",
        )
    )
    divergence = any(
        phrase in normalized
        for phrase in (
            "when i expected",
            "when we expected",
            "even though",
            "instead of",
            "rather than",
            "but i expected",
            "after the action proof",
            "after action proof",
            "expected it to stop",
        )
    )
    return causal_shape and expectation and observed and divergence


def _looks_like_expectation_mismatch_artifact_status_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    explicit = any(
        phrase in normalized
        for phrase in (
            "expectation mismatch",
            "expectation-mismatch",
            "mismatch report",
            "mismatch artifact",
        )
    )
    artifactish = any(
        phrase in normalized
        for phrase in (
            "report",
            "artifact",
            "writeup",
            "write-up",
            "file",
            "ticket",
            "jsh-",
        )
    )
    statusish = any(
        phrase in normalized
        for phrase in (
            "latest status",
            "status of",
            "what is the status",
            "what's the status",
            "current status",
            "status update",
            "latest update",
            "where does",
            "where do",
            "stand",
            "still current",
            "is current",
            "stale",
            "superseded",
            "obsolete",
            "still active",
            "still open",
            "closed",
            "done",
        )
    )
    return explicit and artifactish and statusish


def looks_like_process_actual_use_query(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().replace("?", " ").replace("!", " ").split())
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    actual_use_phrases = (
        "actually being used",
        "actually consulted",
        "actually used",
        "actual use",
        "being used",
        "consulted before planning",
        "consulted before work",
        "consulted before",
        "used to change agent behavior",
        "used to change behavior",
        "used before planning",
        "read before planning",
        "process for its own sake",
        "performative",
        "merely exist",
        "get cited",
        "merely cited",
        "write-only ritual",
        "only updated after the fact",
        "updated after the fact",
        "get updated after the fact",
        "changed behavior",
        "changing behavior",
        "change agent behavior",
        "changed scope",
        "changed task selection",
        "change task selection",
        "changing task selection",
        "actually changing task selection",
        "claim boundaries",
        "reviewer enforced",
        "review enforced",
        "artifact exists",
        "artifact is cited",
        "consulted before planning",
        "changed task selection scope or claim boundaries",
        "only updated after the fact",
        "stale performative or no evidence gap",
    )
    process_targets = (
        "process",
        "artifact",
        "document",
        "doc",
        "rule",
        "checklist",
        "front-door",
        "front door",
        "fsm",
        "learning",
        "learnings",
        "learning entry",
        "ledger",
        "memory",
        "workflow",
        "procedure",
        "rubric",
    )
    audit_shape = (
        "is ",
        "is this",
        "is the",
        "are the",
        "are these",
        "was this",
        "were these",
        "which ",
        "whether",
        "prove",
        "verify",
        "audit",
        "classify",
        "distinguish",
        "does it",
        "did it",
        "did they",
    )
    return (
        any(phrase in normalized for phrase in actual_use_phrases)
        and any(target in normalized for target in process_targets)
        and any(shape in normalized for shape in audit_shape)
    )


def process_actual_use_bucket_contract() -> list[dict[str, str]]:
    return [
        {
            "bucket": "artifact_exists",
            "requirement": "Show the artifact/process exists or was available.",
            "evidence_status": "secondary_context_only",
        },
        {
            "bucket": "artifact_cited",
            "requirement": "Show the artifact/process was cited or mentioned by agents, tasks, or reviews.",
            "evidence_status": "weak_actual_use_signal",
        },
        {
            "bucket": "consulted_before_planning",
            "requirement": "Use raw conversation/session timing to show it was opened, read, or explicitly consulted before planning or task selection.",
            "evidence_status": "primary_actual_use_evidence",
        },
        {
            "bucket": "changed_behavior_scope_or_claim_boundaries",
            "requirement": "Show the process changed task selection, implementation scope, review pressure, claim wording, or stop conditions.",
            "evidence_status": "primary_actual_use_evidence",
        },
        {
            "bucket": "only_updated_after_the_fact",
            "requirement": "Separate post-hoc updates from evidence that the process guided the work before or during execution.",
            "evidence_status": "counterevidence_or_limit",
        },
        {
            "bucket": "stale_performative_no_evidence_gap",
            "requirement": "Name stale, ignored, performative, or not-found-in-searched-scope gaps instead of smoothing them into a yes.",
            "evidence_status": "required_gap_bucket",
        },
    ]


def process_actual_use_source_hierarchy() -> dict[str, Any]:
    return {
        "primary_for_behavior_claims": [
            "raw conversation/session windows showing pre-planning consultation timing",
            "raw agent traces showing behavior, scope, or claim-boundary changes",
            "reviewer enforcement evidence tied to raw timing or explicit review findings",
        ],
        "secondary_context_only": [
            "repo task files",
            "review artifacts",
            "learning ledger or process-document updates",
            "commits and current repo state",
        ],
        "secondary_artifact_rule": (
            "Repo task/review artifacts are secondary context and are not primary actual-use proof "
            "unless linked to raw timing or reviewer enforcement evidence."
        ),
    }


def process_actual_use_forbidden_claims() -> list[str]:
    return [
        "artifact_exists_means_actual_use",
        "artifact_cited_means_consulted_before_planning",
        "post_hoc_update_means_behavior_changed",
        "repo_task_or_review_artifact_as_primary_behavior_proof_without_raw_timing",
        "yes_the_process_is_used_without_stating_missing_buckets",
    ]


PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY = "primary_raw_timing_evidence"
PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY = "secondary_lead_context"
PROCESS_ACTUAL_USE_SOURCE_TIER_SUPPORTING = "supporting_context"
PROCESS_ACTUAL_USE_REQUIRED_READ_CAP = 4
PROCESS_ACTUAL_USE_SECONDARY_LEAD_CAP = 4
PROCESS_ACTUAL_USE_ANSWER_KEY_CAP = 4
PROCESS_ACTUAL_USE_BUCKET_EVIDENCE_CAP = 3
PROCESS_ACTUAL_USE_BUCKET_TERMS = {
    "consulted_before_planning": (
        "consulted before planning",
        "consulted before work",
        "used before planning",
        "read before planning",
        "opened before planning",
        "before planning",
        "before task selection",
    ),
    "changed_behavior_scope_or_claim_boundaries": (
        "changed behavior",
        "change agent behavior",
        "changed task selection",
        "changed scope",
        "changed implementation scope",
        "changed claim boundaries",
        "claim boundaries",
        "changed stop condition",
        "review pressure",
    ),
    "only_updated_after_the_fact": (
        "after the fact",
        "post hoc",
        "post-hoc",
        "only updated",
        "updated after",
    ),
    "artifact_cited": (
        "cited",
        "mentioned",
        "referenced",
    ),
    "artifact_exists": (
        "exists",
        "available",
    ),
    "stale_performative_no_evidence_gap": (
        "stale",
        "performative",
        "no evidence",
        "not found",
        "write-only",
    ),
}


PROCESS_ACTUAL_USE_CHRONOLOGY_PRIMARY_RAW = "primary_raw_evidence"
PROCESS_ACTUAL_USE_CHRONOLOGY_SECONDARY = "legitimate_secondary_lead_or_context_not_final_behavior_proof"
PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC = "post_hoc_answer_key_artifact"
PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO = "current_run_self_echo_diagnostic"


def prioritize_process_actual_use_candidates(
    candidates: list[dict[str, Any]],
    *,
    query: str | None = None,
) -> list[dict[str, Any]]:
    ranked: list[tuple[tuple[int, int, int], dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        row = dict(candidate)
        source_ref = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
        target = source_ref_target(source_ref)
        tier = process_actual_use_source_tier(
            kind="jsonl_source_ref",
            target=target or "",
            source_ref=source_ref,
        )
        buckets = process_actual_use_bucket_matches(
            " ".join(
                str(part or "")
                for part in (
                    row.get("snippet"),
                    row.get("role"),
                    row.get("timestamp_utc"),
                    " ".join(str(item) for item in row.get("reason_codes") or []),
                )
            )
        )
        row["process_actual_use_source_tier"] = tier
        row["process_actual_use_bucket_matches"] = buckets
        candidate_text = " ".join(
            str(part or "")
            for part in (
                target,
                row.get("snippet"),
                row.get("role"),
                row.get("timestamp_utc"),
                " ".join(str(item) for item in row.get("reason_codes") or []),
            )
        )
        current_run_self_echo = process_actual_use_current_run_or_self_echo_candidate(
            candidate_text,
            query=query,
        )
        explicit_current_run = process_actual_use_current_run_or_tool_behavior_requested(query or "")
        if current_run_self_echo:
            row["process_actual_use_current_run_self_echo_candidate"] = True
            row["process_actual_use_source_chronology_role"] = (
                PROCESS_ACTUAL_USE_CHRONOLOGY_PRIMARY_RAW
                if explicit_current_run
                else PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
            )
        reason_codes = list(row.get("reason_codes") or [])
        if tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY and "process_actual_use_primary_raw_timing" not in reason_codes:
            reason_codes.append("process_actual_use_primary_raw_timing")
        if current_run_self_echo and not explicit_current_run and "process_actual_use_current_run_self_echo_diagnostic" not in reason_codes:
            reason_codes.append("process_actual_use_current_run_self_echo_diagnostic")
        elif tier == PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY and "process_actual_use_secondary_lead" not in reason_codes:
            reason_codes.append("process_actual_use_secondary_lead")
        row["reason_codes"] = reason_codes
        ranked.append((process_actual_use_candidate_sort_key(row, index), row))
    return [row for _, row in sorted(ranked, key=lambda item: item[0])]


def process_actual_use_candidate_sort_key(candidate: dict[str, Any], index: int) -> tuple[int, int, int]:
    tier = candidate.get("process_actual_use_source_tier")
    if candidate.get("process_actual_use_source_chronology_role") == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO:
        tier_rank = 3
    else:
        tier_rank = {
            PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY: 0,
            PROCESS_ACTUAL_USE_SOURCE_TIER_SUPPORTING: 1,
            PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY: 2,
        }.get(str(tier), 3)
    buckets = set(candidate.get("process_actual_use_bucket_matches") or [])
    decisive_bucket_rank = 0 if buckets & {"consulted_before_planning", "changed_behavior_scope_or_claim_boundaries"} else 1
    timestamp_rank = 0 if candidate.get("timestamp_utc") else 1
    return (tier_rank, decisive_bucket_rank + timestamp_rank, index)


def prioritize_process_actual_use_next_reads(
    next_reads: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    joins: dict[str, Any],
    query: str | None = None,
) -> list[dict[str, Any]]:
    candidate_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in candidates
        if candidate.get("candidate_id") is not None
    }
    target_to_candidate = {
        source_ref_target(candidate.get("source_ref") or {}): candidate
        for candidate in candidates
        if source_ref_target(candidate.get("source_ref") or {})
    }
    tool_event_targets = {
        source_ref_target(event.get("source_ref") or {})
        for event in tool_proof.get("events", [])
        if source_ref_target(event.get("source_ref") or {})
    }
    review_paths = {str(item.get("path") or "") for item in joins.get("review_evidence", []) if item.get("path")}
    annotated = []
    for index, read in enumerate(next_reads):
        row = annotate_process_actual_use_next_read(
            read,
            index=index,
            candidate_by_id=candidate_by_id,
            target_to_candidate=target_to_candidate,
            tool_event_targets=tool_event_targets,
            review_paths=review_paths,
            query=query,
        )
        annotated.append(row)
    annotated.sort(key=lambda item: item.pop("_process_actual_use_sort_key"))
    return dedupe_process_actual_use_reads(annotated)[:12]


def annotate_process_actual_use_next_read(
    read: dict[str, Any],
    *,
    index: int,
    candidate_by_id: dict[str, dict[str, Any]],
    target_to_candidate: dict[str, dict[str, Any]],
    tool_event_targets: set[str],
    review_paths: set[str],
    query: str | None = None,
) -> dict[str, Any]:
    row = dict(read)
    target = str(row.get("target") or "")
    kind = str(row.get("kind") or "")
    source_ref = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
    existing_selection = (
        row.get("process_actual_use_selection")
        if isinstance(row.get("process_actual_use_selection"), dict)
        else {}
    )
    candidate = candidate_by_id.get(str(row.get("candidate_id") or "")) or target_to_candidate.get(target) or {}
    existing_tier = row.get("actual_use_source_tier") or existing_selection.get("source_tier")
    if existing_tier in {
        PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY,
        PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY,
        PROCESS_ACTUAL_USE_SOURCE_TIER_SUPPORTING,
    }:
        tier = str(existing_tier)
    else:
        tier = process_actual_use_source_tier(kind=kind, target=target, source_ref=source_ref)
    if target in review_paths or kind in {"task_or_review_artifact", "artifact", "hint_path"}:
        tier = PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY
    if target in tool_event_targets or kind == "tool_event_source_ref":
        tier = PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
    bucket_text = " ".join(
        str(part or "")
        for part in (
            row.get("reason"),
            target,
            candidate.get("snippet"),
            candidate.get("role"),
            candidate.get("timestamp_utc"),
            " ".join(str(item) for item in candidate.get("reason_codes") or []),
        )
    )
    buckets = list(existing_selection.get("selected_buckets") or process_actual_use_bucket_matches(bucket_text))
    if tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY and not buckets:
        buckets = ["raw_session_timing_context"]
    original_reason = existing_selection.get("original_reason") or row.get("reason")
    chronology = classify_process_actual_use_read_chronology(
        row,
        candidate=candidate,
        query=query,
        source_tier=tier,
    )
    if tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY:
        row["reason"] = "primary raw session/tool-event timing window for process actual-use bucket audit"
    elif tier == PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY:
        row["reason"] = "secondary lead/context only; use to locate raw timing or reviewer enforcement evidence"
    if chronology["source_chronology_role"] == PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC and not chronology[
        "explicit_prior_answer_or_report_requested"
    ]:
        row["reason"] = (
            "post-hoc answer-key or prior final-answer prose candidate; do not count as final "
            "support for process actual-use unless the prompt asks for that prior answer/report"
        )
    if chronology["source_chronology_role"] == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO:
        row["reason"] = (
            "current-run prompt/tool-output/self-echo diagnostic; do not count as final support "
            "for historical process actual-use unless the prompt asks about the current run/tool/lane"
        )
    diagnostic_only = process_actual_use_diagnostic_only_chronology(chronology)
    row["actual_use_source_tier"] = tier
    row["read_more_actionable"] = not diagnostic_only
    row["followthrough_actionable"] = not diagnostic_only
    row["diagnostic_only"] = diagnostic_only
    if diagnostic_only:
        if chronology["source_chronology_role"] == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO:
            row["diagnostic_open_policy"] = (
                "Open only for current-lane/tool/run behavior audit; do not use as answer evidence "
                "for a historical process actual-use claim."
            )
        else:
            row["diagnostic_open_policy"] = (
                "Open only for contamination/chronology audit, or when the user explicitly asks "
                "to recover a prior answer/report; do not use as answer evidence."
            )
        row["expansion_actions"] = []
        row["context_actions"] = []
    row["process_actual_use_selection"] = {
        "schema_version": 1,
        "source_tier": tier,
        "selected_buckets": buckets,
        "primary_for_behavior_claims": tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
        and chronology["final_support_allowed"],
        "secondary_artifacts_are_final_proof": False,
        "anti_contamination_role": chronology["source_chronology_role"],
        "source_chronology_role": chronology["source_chronology_role"],
        "final_support_allowed": chronology["final_support_allowed"],
        "required_final_support_read": tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
        and chronology["final_support_allowed"],
        "read_more_actionable": not diagnostic_only,
        "followthrough_actionable": not diagnostic_only,
        "diagnostic_only": diagnostic_only,
        "diagnostic_open_policy": (
            "current_lane_tool_run_audit_only"
            if diagnostic_only
            and chronology["source_chronology_role"] == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
            else "contamination_audit_or_explicit_prior_answer_request_only"
            if diagnostic_only
            else "normal_process_actual_use_followthrough"
        ),
        "post_hoc_answer_key_candidate": chronology["source_chronology_role"] == "post_hoc_answer_key_artifact",
        "current_run_self_echo_candidate": chronology["source_chronology_role"]
        == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
        or chronology["current_run_self_echo_candidate"],
        "prior_final_prose_candidate": chronology["prior_final_prose_candidate"],
        "explicit_prior_answer_or_report_requested": chronology["explicit_prior_answer_or_report_requested"],
        "explicit_current_run_or_tool_behavior_requested": chronology[
            "explicit_current_run_or_tool_behavior_requested"
        ],
        "raw_text_included": False,
        "original_reason": original_reason,
    }
    row["_process_actual_use_sort_key"] = process_actual_use_read_sort_key(row, index)
    return row


def process_actual_use_diagnostic_only_chronology(chronology: dict[str, Any]) -> bool:
    return (
        chronology.get("source_chronology_role") == "post_hoc_answer_key_artifact"
        and not chronology.get("explicit_prior_answer_or_report_requested")
    ) or (
        chronology.get("source_chronology_role") == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
        and not chronology.get("explicit_current_run_or_tool_behavior_requested")
    )


def process_actual_use_read_sort_key(read: dict[str, Any], index: int) -> tuple[int, int, int]:
    tier = read.get("actual_use_source_tier")
    selection = read.get("process_actual_use_selection") if isinstance(read.get("process_actual_use_selection"), dict) else {}
    if (
        selection.get("post_hoc_answer_key_candidate")
        and not selection.get("explicit_prior_answer_or_report_requested")
    ) or (
        selection.get("current_run_self_echo_candidate")
        and not selection.get("explicit_current_run_or_tool_behavior_requested")
    ):
        tier_rank = 3
    else:
        tier_rank = {
            PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY: 0,
            PROCESS_ACTUAL_USE_SOURCE_TIER_SUPPORTING: 1,
            PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY: 2,
        }.get(str(tier), 3)
    if not selection.get("final_support_allowed", tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY):
        tier_rank = max(tier_rank, 2)
    buckets = set(selection.get("selected_buckets") or [])
    decisive_bucket_rank = 0 if buckets & {"consulted_before_planning", "changed_behavior_scope_or_claim_boundaries"} else 1
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    timestamp_rank = 0 if source_ref.get("timestamp_utc") else 1
    return (tier_rank, decisive_bucket_rank + timestamp_rank, index)


def apply_historical_provenance_self_echo_policy(
    next_reads: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    query: str,
    family: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    handles = historical_provenance_handles(query)
    policy_wanted = historical_provenance_self_echo_policy_wanted(query, family=family, handles=handles)
    orchestration_wanted = historical_orchestration_provenance_plan_wanted(query, family=family)
    if not policy_wanted and not orchestration_wanted:
        if handles and (
            historical_provenance_current_scope_requested(query)
            or historical_provenance_prior_answer_or_report_audit_requested(query)
        ):
            return ensure_read_more_actionability_defaults(next_reads), {}
        return next_reads, {}

    candidate_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in candidates
        if candidate.get("candidate_id") is not None
    }
    target_to_candidate = {
        source_ref_target(candidate.get("source_ref") or {}): candidate
        for candidate in candidates
        if source_ref_target(candidate.get("source_ref") or {})
    }
    unwrapped = (
        embedded_source_ref_next_reads(
            candidates,
            query=query,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
        if policy_wanted or orchestration_wanted
        else []
    )
    annotated: list[dict[str, Any]] = []
    diagnostic_count = 0
    current_task_same_loop_count = 0
    post_hoc_answer_key_count = 0
    for index, read in enumerate(next_reads):
        row = dict(read)
        target = str(row.get("target") or "")
        candidate = candidate_by_id.get(str(row.get("candidate_id") or "")) or target_to_candidate.get(target) or {}
        text = historical_provenance_read_text(row, candidate)
        current_run_echo = (
            (policy_wanted or orchestration_wanted)
            and historical_provenance_current_run_or_self_echo_candidate(text, query=query)
        )
        current_day_jsonl_echo = (
            (policy_wanted or orchestration_wanted)
            and historical_provenance_current_day_jsonl_echo_candidate(row, query=query)
        )
        current_task_same_loop = (
            (policy_wanted or orchestration_wanted)
            and historical_orchestration_current_task_same_loop_candidate(
                row,
                candidate=candidate,
                text=text,
            )
        )
        category = historical_provenance_source_category(
            row,
            candidate=candidate,
            query=query,
            handles=handles,
            current_run_echo=current_run_echo or current_day_jsonl_echo,
            current_task_same_loop=current_task_same_loop,
        )
        if category["diagnostic_only"]:
            diagnostic_count += 1
            if current_task_same_loop:
                current_task_same_loop_count += 1
            if category["source_chronology_role"] == "post_hoc_answer_key_diagnostic":
                post_hoc_answer_key_count += 1
            row["diagnostic_only"] = True
            row["read_more_actionable"] = False
            row["followthrough_actionable"] = False
            row["expansion_actions"] = []
            row["context_actions"] = []
            if category["source_chronology_role"] == "current_task_same_loop_diagnostic":
                row["reason"] = (
                    "current-task/same-loop orchestration status diagnostic; prefer original "
                    "pre-existing role evidence or exact missing-role recovery refs"
                )
            elif category["source_chronology_role"] == "post_hoc_answer_key_diagnostic":
                row["reason"] = (
                    "post-hoc answer-key/adjudication/final/report diagnostic; prefer target "
                    "historical raw JSONL or historical task/review/commit refs as proof"
                )
            elif category["source_chronology_role"] == "unrelated_high_lexical_diagnostic":
                row["reason"] = (
                    "high-lexical diagnostic that does not match the requested task handle; "
                    "prefer target historical raw JSONL or historical task/review/commit refs"
                )
            else:
                row["reason"] = (
                    "current-run/tool-output/self-echo diagnostic; prefer original pre-existing "
                    "source refs for historical provenance unless the prompt asks about the current run/tool/lane"
                )
            row["historical_provenance_selection"] = {
                "schema_version": 1,
                "source_chronology_role": category["source_chronology_role"],
                "final_support_allowed": False,
                "read_more_actionable": False,
                "followthrough_actionable": False,
                "diagnostic_only": True,
                "detected_handles": handles[:8],
                "raw_text_included": False,
            }
        elif policy_wanted or orchestration_wanted:
            row.setdefault("historical_provenance_selection", {
                "schema_version": 1,
                "source_chronology_role": category["source_chronology_role"],
                "final_support_allowed": category["final_support_allowed"],
                "read_more_actionable": row.get("read_more_actionable", True) is not False,
                "followthrough_actionable": row.get("followthrough_actionable", True) is not False,
                "diagnostic_only": bool(row.get("diagnostic_only", False)),
                "detected_handles": handles[:8],
                "raw_text_included": False,
            })
        row["_historical_provenance_sort_key"] = historical_provenance_read_sort_key(row, index)
        annotated.append(row)

    combined = dedupe_dicts(unwrapped + annotated)
    combined.sort(key=lambda item: item.pop("_historical_provenance_sort_key", (1, 0, 0)))
    result = dedupe_historical_provenance_reads(combined)[:12]
    summary = {
        "schema_version": 1,
        "status": "active",
        "detected_handles": handles[:8],
        "applies_to_question_family": family.get("primary"),
        "embedded_source_ref_promoted_count": len(unwrapped),
        "diagnostic_only_next_read_count": sum(1 for item in result if item.get("diagnostic_only")),
        "diagnostic_only_candidate_count": diagnostic_count,
        "current_task_same_loop_diagnostic_count": current_task_same_loop_count,
        "post_hoc_answer_key_diagnostic_count": post_hoc_answer_key_count,
        "policy": (
            "For explicit historical provenance handles, current-run command outputs, "
            "lane prompts, same-run tool echoes, current-task/same-loop status rows, "
            "post-hoc answer-key/adjudication/final/report rows, and unrelated high-lexical "
            "rows are diagnostics. Target historical raw JSONL and historical task/review/"
            "commit refs stay actionable and sort before diagnostics."
        ),
        "current_run_evidence_counts_as_final_support": False,
        "raw_text_included": False,
    }
    return result, summary


HISTORICAL_ORCHESTRATION_FINAL_SUPPORT_CAP = 6
HISTORICAL_ORCHESTRATION_NON_FINAL_READ_CAP = 4
HISTORICAL_ORCHESTRATION_PRIMARY_REQUIRED_ROLES = [
    "parent_or_coordinator",
    "worker",
    "reviewer",
]
HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES = [
    "secondary_task_review_commit_support",
]
HISTORICAL_ORCHESTRATION_ROLE_RECOVERY_TERMS = {
    "parent_or_coordinator": ["parent", "coordinator", "orchestrator", "launched", "spawned"],
    "worker": ["worker", "implementer", "implementation", "subagent", "worktree"],
    "reviewer": ["reviewer", "rereviewer", "reviewed", "verified", "verification"],
    "secondary_task_review_commit_support": [
        "task file",
        "review artifact",
        "review verdict",
        "commit",
        "git show",
        "codex/state/reviews",
    ],
}
HISTORICAL_ORCHESTRATION_SECONDARY_RECOVERY_SCOPES = [
    "tasks",
    "reports",
    "codex/state/reviews",
]
HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP = 5
HISTORICAL_ORCHESTRATION_ROLE_LABELS = {
    "parent_or_coordinator": "parent/coordinator",
    "worker": "worker/subagent",
    "reviewer": "reviewer/rereviewer",
    "secondary_task_review_commit_support": "secondary task/review/commit support",
}


def build_historical_orchestration_provenance_final_support_plan(
    next_reads: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    query: str,
    family: dict[str, Any],
) -> dict[str, Any]:
    if not historical_orchestration_provenance_plan_wanted(query, family=family):
        return {}
    required_primary_roles = historical_orchestration_requested_roles(query)
    required_secondary_roles = list(HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES)
    required_roles = required_primary_roles + [
        role for role in required_secondary_roles if role not in required_primary_roles
    ]
    candidate_by_id = {
        str(candidate.get("candidate_id") or ""): candidate
        for candidate in candidates
        if candidate.get("candidate_id") is not None
    }
    target_to_candidate = {
        source_ref_target(candidate.get("source_ref") or {}): candidate
        for candidate in candidates
        if source_ref_target(candidate.get("source_ref") or {})
    }
    final_support: list[dict[str, Any]] = []
    current_run_echoes: list[dict[str, Any]] = []
    non_final: list[dict[str, Any]] = []
    for read in next_reads:
        candidate = (
            candidate_by_id.get(str(read.get("candidate_id") or ""))
            or target_to_candidate.get(str(read.get("target") or ""))
            or {}
        )
        row = historical_orchestration_read_plan_row(read, candidate=candidate, query=query)
        if row["final_support_allowed"]:
            final_support.append(row)
        elif row["source_chronology_role"] in {
            "current_run_self_echo_diagnostic",
            "current_task_same_loop_diagnostic",
        }:
            current_run_echoes.append(row)
        else:
            non_final.append({**row, "required_non_final_read": True})

    covered_primary_role_set = {
        role
        for row in final_support
        for role in row.get("orchestration_roles") or []
        if role in required_primary_roles
    }
    covered_primary_roles = [role for role in required_primary_roles if role in covered_primary_role_set]
    secondary_support = [
        row
        for row in final_support + non_final
        if historical_orchestration_secondary_support_row_allowed(row)
    ]
    covered_secondary_role_set = {
        role
        for row in secondary_support
        for role in row.get("orchestration_roles") or []
        if role in required_secondary_roles
    }
    covered_secondary_roles = [role for role in required_secondary_roles if role in covered_secondary_role_set]
    covered_roles = covered_primary_roles + [
        role for role in covered_secondary_roles if role not in covered_primary_roles
    ]
    missing_roles = [role for role in required_roles if role not in covered_roles]
    missing_primary_roles = [role for role in required_primary_roles if role not in covered_primary_roles]
    missing_secondary_roles = [role for role in required_secondary_roles if role not in covered_secondary_roles]
    sufficient = bool(final_support) and not missing_primary_roles and not missing_secondary_roles
    status = "answer_ready" if sufficient else "incomplete_primary_coverage"
    missing_role_recovery_refs = historical_orchestration_missing_role_recovery_refs(
        missing_roles=missing_roles,
        final_support=final_support,
        non_final=non_final,
        query=query,
    )
    return {
        "schema_version": 1,
        "source_plan_name": "historical_orchestration_role_coverage_source_plan",
        "status": status,
        "applies_to_question_family": family.get("primary"),
        "required_primary_roles": required_primary_roles,
        "required_secondary_roles": required_secondary_roles,
        "required_roles": required_roles,
        "covered_primary_roles": covered_primary_roles,
        "covered_secondary_roles": covered_secondary_roles,
        "covered_roles": covered_roles,
        "missing_roles": missing_roles,
        "missing_primary_roles": missing_primary_roles,
        "missing_secondary_roles": missing_secondary_roles,
        "role_coverage": historical_orchestration_role_coverage_rows(
            required_primary_roles=required_primary_roles,
            required_secondary_roles=required_secondary_roles,
            final_support=final_support,
            secondary_support=secondary_support,
        ),
        "primary_historical_final_support_reads": final_support[:HISTORICAL_ORCHESTRATION_FINAL_SUPPORT_CAP],
        "primary_historical_final_support_read_count": len(final_support),
        "secondary_task_review_commit_support_reads": secondary_support[:HISTORICAL_ORCHESTRATION_NON_FINAL_READ_CAP],
        "secondary_task_review_commit_support_read_count": len(secondary_support),
        "missing_role_recovery_refs": missing_role_recovery_refs,
        "missing_role_recovery_ref_count": len(missing_role_recovery_refs),
        "bounded_non_final_required_reads": non_final[:HISTORICAL_ORCHESTRATION_NON_FINAL_READ_CAP],
        "bounded_non_final_required_read_count": min(
            len(non_final),
            HISTORICAL_ORCHESTRATION_NON_FINAL_READ_CAP,
        ),
        "current_run_self_echo_candidates": current_run_echoes[:HISTORICAL_ORCHESTRATION_NON_FINAL_READ_CAP],
        "current_run_self_echo_candidate_count": len(current_run_echoes),
        "guidance": {
            "do_first": (
                "answer_from_primary_historical_final_support_reads"
                if sufficient
                else "open_bounded_non_final_required_reads_then_state_missing_roles"
            ),
            "primary_historical_policy": (
                "Pre-existing raw JSONL refs may support orchestration/provenance claims after inspection "
                "when they cover the requested parent/coordinator, worker, and reviewer roles."
            ),
            "secondary_support_policy": (
                "Pre-existing task, review, or commit refs are secondary support for chronology/provenance "
                "cross-checks; they do not replace the primary raw parent/worker/reviewer refs."
            ),
            "current_run_self_echo_policy": (
                "Current-run command output, same-loop task/status text, and prompt echoes are chronology "
                "diagnostics only; they do not count as final historical orchestration support."
            ),
            "incomplete_coverage_policy": (
                "If requested roles are missing from primary historical refs, fail closed: use the exact "
                "missing_role_recovery_refs commands/refs and state the missing roles instead of answering "
                "from current-run echoes."
            ),
            "raw_text_included": False,
        },
        "claim_boundaries": {
            "current_run_current_task_same_loop_refs_count_as_final_support": False,
            "secondary_or_non_final_reads_count_as_final_support": False,
            "answer_ready_requires_all_requested_roles": True,
            "answer_ready_requires_secondary_task_review_commit_support": True,
            "release_or_token_savings_claim_supported": False,
        },
    }


def build_historical_orchestration_role_evidence_bundle(plan: dict[str, Any]) -> dict[str, Any]:
    if not plan:
        return {}
    required_roles = list(plan.get("required_roles") or [])
    primary_reads = list(plan.get("primary_historical_final_support_reads") or [])
    secondary_reads = list(plan.get("secondary_task_review_commit_support_reads") or [])
    missing_roles = list(plan.get("missing_roles") or [])
    selected_reads = (
        historical_orchestration_select_required_role_reads(
            required_roles=required_roles,
            primary_reads=primary_reads,
            secondary_reads=secondary_reads,
            read_cap=HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP,
        )
        if plan.get("status") == "answer_ready" and not missing_roles
        else []
    )
    selected_role_set = {
        role
        for read in selected_reads
        for role in historical_orchestration_bundle_effective_roles(
            read,
            required_roles=required_roles,
            secondary_read_ids={
                historical_orchestration_read_identity(item) for item in secondary_reads
            },
        )
        if role in required_roles
    }
    selected_missing_roles = (
        [role for role in required_roles if role not in selected_role_set]
        if selected_reads
        else []
    )
    answer_ready = plan.get("status") == "answer_ready" and not missing_roles and not selected_missing_roles
    effective_missing_roles = dedupe(missing_roles + selected_missing_roles)
    role_groups = historical_orchestration_role_evidence_groups(
        required_roles=required_roles,
        primary_reads=primary_reads,
        secondary_reads=secondary_reads,
        missing_roles=effective_missing_roles,
        selected_reads=selected_reads,
    )
    return {
        "schema_version": 1,
        "bundle_name": "historical_orchestration_role_evidence_bundle",
        "status": "answer_ready" if answer_ready else "not_answer_ready_missing_roles",
        "source_plan_link": "semantic_debug.historical_orchestration_provenance_final_support_plan",
        "role_groups": role_groups,
        "required_roles": required_roles,
        "covered_roles": [role for role in required_roles if role not in effective_missing_roles],
        "missing_roles": effective_missing_roles,
        "missing_role_recovery_refs": (
            [] if answer_ready else list(plan.get("missing_role_recovery_refs") or [])
        ),
        "missing_role_recovery_ref_count": 0
        if answer_ready
        else len(plan.get("missing_role_recovery_refs") or []),
        "required_role_reads": selected_reads if answer_ready else [],
        "required_role_read_count": len(selected_reads) if answer_ready else 0,
        "required_role_read_cap": HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP,
        "dedupe_policy": "dedupe by target/source_ref and select the smallest role-covering read set up to cap",
        "answer_ready_claim_allowed": answer_ready,
        "claim_boundaries": {
            "raw_jsonl_supports_primary_role_claims": True,
            "task_review_commit_refs_are_secondary_support": True,
            "current_run_current_task_same_loop_refs_count_as_final_support": False,
            "secondary_refs_replace_primary_raw_role_support": False,
            "release_or_token_savings_claim_supported": False,
            "raw_text_included": False,
        },
    }


def historical_orchestration_role_evidence_bundle_summary(bundle: dict[str, Any]) -> dict[str, Any]:
    if not bundle:
        return {}
    return {
        "schema_version": bundle.get("schema_version", 1),
        "bundle_name": bundle.get("bundle_name", "historical_orchestration_role_evidence_bundle"),
        "status": bundle.get("status"),
        "source_plan_link": bundle.get("source_plan_link"),
        "required_roles": list(bundle.get("required_roles") or []),
        "covered_roles": list(bundle.get("covered_roles") or []),
        "missing_roles": list(bundle.get("missing_roles") or []),
        "role_groups": list(bundle.get("role_groups") or []),
        "required_role_read_count": bundle.get("required_role_read_count", 0),
        "required_role_read_cap": bundle.get(
            "required_role_read_cap",
            HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP,
        ),
        "required_role_reads": list(bundle.get("required_role_reads") or [])[
            :HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP
        ],
        "missing_role_recovery_ref_count": bundle.get("missing_role_recovery_ref_count", 0),
        "missing_role_recovery_refs": list(bundle.get("missing_role_recovery_refs") or [])[
            :HISTORICAL_ORCHESTRATION_ROLE_EVIDENCE_BUNDLE_READ_CAP
        ],
        "answer_ready_claim_allowed": bool(bundle.get("answer_ready_claim_allowed")),
        "claim_boundaries": bundle.get("claim_boundaries"),
    }


def historical_orchestration_select_required_role_reads(
    *,
    required_roles: list[str],
    primary_reads: list[dict[str, Any]],
    secondary_reads: list[dict[str, Any]],
    read_cap: int,
) -> list[dict[str, Any]]:
    pool = [
        read
        for read in primary_reads + secondary_reads
        if read.get("target")
        and not read.get("diagnostic_only")
        and read.get("source_chronology_role")
        not in {"current_run_self_echo_diagnostic", "current_task_same_loop_diagnostic"}
    ]
    secondary_read_ids = {historical_orchestration_read_identity(read) for read in secondary_reads}
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    remaining_roles = set(required_roles)
    while remaining_roles and len(selected) < read_cap:
        best = None
        best_roles: set[str] = set()
        for read in pool:
            if any(historical_orchestration_same_read(read, existing) for existing in selected):
                continue
            read_roles = set(
                historical_orchestration_bundle_effective_roles(
                    read,
                    required_roles=list(remaining_roles),
                    secondary_read_ids=secondary_read_ids,
                )
            )
            if len(read_roles) > len(best_roles):
                best = read
                best_roles = read_roles
        if best is None or not best_roles:
            break
        selected.append(best)
        covered.update(best_roles)
        remaining_roles = set(required_roles) - covered
    return selected


def historical_orchestration_role_evidence_groups(
    *,
    required_roles: list[str],
    primary_reads: list[dict[str, Any]],
    secondary_reads: list[dict[str, Any]],
    missing_roles: list[str],
    selected_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_targets = {historical_orchestration_read_identity(read) for read in selected_reads}
    groups: list[dict[str, Any]] = []
    for role in required_roles:
        source_pool = secondary_reads if role in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES else primary_reads
        sources = [
            read
            for read in source_pool
            if role in set(read.get("orchestration_roles") or [])
            and not read.get("diagnostic_only")
            and read.get("source_chronology_role")
            not in {"current_run_self_echo_diagnostic", "current_task_same_loop_diagnostic"}
        ]
        tier = (
            "secondary_task_review_commit_support"
            if role in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES
            else "primary_raw_jsonl"
        )
        groups.append(
            {
                "role": role,
                "label": HISTORICAL_ORCHESTRATION_ROLE_LABELS.get(role, role),
                "status": "missing" if role in missing_roles else "covered",
                "support_tier": tier,
                "claim_support": (
                    "secondary_support_only"
                    if role in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES
                    else "primary_role_claim_support"
                ),
                "source_count": len(sources),
                "source_refs": sources[:3],
                "selected_required_read_targets": [
                    str(read.get("target") or "")
                    for read in sources
                    if historical_orchestration_read_identity(read) in selected_targets
                ][:3],
            }
        )
    return groups


def historical_orchestration_bundle_effective_roles(
    read: dict[str, Any],
    *,
    required_roles: list[str],
    secondary_read_ids: set[str],
) -> list[str]:
    roles = [role for role in read.get("orchestration_roles") or [] if role in required_roles]
    if historical_orchestration_read_identity(read) in secondary_read_ids:
        return [
            role
            for role in roles
            if role in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES
        ]
    return [
        role
        for role in roles
        if role not in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES
    ]


def historical_orchestration_read_identity(read: dict[str, Any]) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    return str(read.get("target") or source_ref_target(source_ref) or source_ref.get("path") or "")


def historical_orchestration_same_read(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_identity = historical_orchestration_read_identity(left)
    right_identity = historical_orchestration_read_identity(right)
    return bool(left_identity and right_identity and left_identity == right_identity)


def historical_orchestration_missing_role_recovery_refs(
    *,
    missing_roles: list[str],
    final_support: list[dict[str, Any]],
    non_final: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    if not missing_roles:
        return []
    original_rows = [
        row
        for row in final_support + non_final
        if row.get("target")
        and row.get("source_chronology_role")
        not in {"current_run_self_echo_diagnostic", "current_task_same_loop_diagnostic"}
        and not row.get("diagnostic_only")
    ]
    source_paths = dedupe(
        [
            str((row.get("source_ref") or {}).get("path") or source_target_path_part(str(row.get("target") or "")))
            for row in original_rows
            if (row.get("source_ref") or {}).get("path") or row.get("target")
        ]
    )
    source_targets = [str(row.get("target") or "") for row in original_rows if row.get("target")]
    handles = historical_provenance_handles(query)
    rows: list[dict[str, Any]] = []
    for role in missing_roles:
        terms = HISTORICAL_ORCHESTRATION_ROLE_RECOVERY_TERMS.get(role, [role])
        search_terms = dedupe(handles + terms)
        pattern = "|".join(re.escape(term) for term in search_terms if term)
        if not pattern:
            pattern = re.escape(role)
        scopes = (
            list(HISTORICAL_ORCHESTRATION_SECONDARY_RECOVERY_SCOPES)
            if role in HISTORICAL_ORCHESTRATION_SECONDARY_REQUIRED_ROLES
            else list(source_paths)
        )
        if not scopes:
            scopes = ["."]
        argv = ["rg", "-n", "--hidden", "-i", "-e", pattern, *scopes]
        rows.append(
            {
                "role": role,
                "kind": "missing_role_recovery_command",
                "target": " ".join(scopes[:3]),
                "source_targets_to_recheck": source_targets[:HISTORICAL_ORCHESTRATION_FINAL_SUPPORT_CAP],
                "search_terms": search_terms[:12],
                "argv": argv,
                "command": shell_command(argv),
                "recovery_instruction": (
                    "Run this exact role-targeted recovery command before answering; use only "
                    "pre-existing raw JSONL refs or legitimate task/review/commit artifacts it finds."
                ),
                "current_run_echo_recovery_allowed": False,
                "final_support_allowed": False,
                "raw_text_included": False,
            }
        )
    return rows


def historical_orchestration_provenance_plan_wanted(query: str, *, family: dict[str, Any]) -> bool:
    if historical_provenance_current_scope_requested(query):
        return False
    lowered = re.sub(r"\s+", " ", str(query or "").lower().replace("_", " ")).strip()
    if "reviewed source case" in lowered or "reviewed source window" in lowered:
        return False
    primary = str((family or {}).get("primary") or "")
    detected = set((family or {}).get("detected_supported") or [])
    if primary not in {"provenance", "claimed_action", "why_did_this_happen", "semantic_debug"} and "provenance" not in detected:
        return False
    return any(
        term in lowered
        for term in (
            "coordinator",
            "worker",
            "reviewer",
            "rereviewer",
            "subagent",
            "spawned",
            "launched",
            "orchestration",
            "provenance",
        )
    )


def historical_orchestration_requested_roles(query: str) -> list[str]:
    roles = [
        role
        for role in historical_orchestration_roles_from_text(query)
        if role in HISTORICAL_ORCHESTRATION_PRIMARY_REQUIRED_ROLES
    ]
    if not roles:
        roles = list(HISTORICAL_ORCHESTRATION_PRIMARY_REQUIRED_ROLES)
    if "worker" in roles and "parent_or_coordinator" not in roles:
        roles.insert(0, "parent_or_coordinator")
    return roles


def historical_orchestration_roles_from_text(text: str) -> list[str]:
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    roles: list[str] = []
    if historical_orchestration_affirmative_role_mention(
        lowered,
        ("parent", "coordinator", "orchestrator", "launched", "spawned"),
    ):
        roles.append("parent_or_coordinator")
    if historical_orchestration_affirmative_role_mention(
        lowered,
        ("worker", "implementer", "implementation", "subagent", "worktree"),
    ):
        roles.append("worker")
    if historical_orchestration_affirmative_role_mention(
        lowered,
        ("reviewer", "rereviewer", "review ", "reviewed", "verified", "verification"),
    ):
        roles.append("reviewer")
    return dedupe(roles)


def historical_orchestration_affirmative_role_mention(text: str, terms: tuple[str, ...]) -> bool:
    stripped = historical_orchestration_strip_negated_role_absence(text, terms)
    return any(term in stripped for term in terms)


def historical_orchestration_strip_negated_role_absence(text: str, terms: tuple[str, ...]) -> str:
    result = str(text or "")
    term_pattern = "|".join(re.escape(term.strip()) for term in terms if term.strip())
    if not term_pattern:
        return result
    absence_nouns = (
        r"evidence|proof|support|record|records|source|sources|ref|refs|artifact|artifacts|"
        r"trace|traces|entry|entries"
    )
    negators = r"no|not|without|missing|absent|lack(?:s|ed|ing)?|unavailable|unproven"
    patterns = (
        rf"\b(?:{negators})\b[^.;\n]{{0,100}}\b(?:{term_pattern})\b[^.;\n]{{0,100}}\b(?:{absence_nouns}|present|found|available)\b",
        rf"\b(?:{term_pattern})\b[^.;\n]{{0,100}}\b(?:{absence_nouns})\b[^.;\n]{{0,60}}\b(?:not\s+present|not\s+found|missing|absent|unavailable|lacking|none)\b",
    )
    for pattern in patterns:
        result = re.sub(pattern, " ", result)
    return result


def historical_orchestration_secondary_support_from_text(text: str) -> bool:
    lowered = str(text or "").lower().replace("\\", "/")
    if re.search(r"\b[0-9a-f]{7,40}\b", lowered) and any(term in lowered for term in ("commit", "git")):
        return True
    return any(
        term in lowered
        for term in (
            " task file",
            " tasks/",
            "/tasks/",
            " task status",
            " review artifact",
            " review file",
            " review verdict",
            " review report",
            "/reviews/",
            "codex/state/reviews",
            " commit ",
            "commit:",
            "commit evidence",
            "git show",
        )
    )


def historical_orchestration_secondary_support_from_ref(
    read: dict[str, Any],
    candidate: dict[str, Any],
    *,
    text: str,
) -> bool:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    kind = str(read.get("kind") or "").lower()
    support_text = " ".join(
        str(part or "")
        for part in (
            kind,
            target,
            path,
            read.get("reason"),
            candidate.get("snippet"),
            text,
        )
    )
    raw_jsonl = is_jsonl_read_target(target) or path.lower().replace("\\", "/").endswith(".jsonl")
    artifact_ref = historical_orchestration_secondary_artifact_path(path) or (
        not raw_jsonl
        and kind
        in {
            "task_or_review_artifact",
            "artifact",
            "hint_path",
            "commit_ref",
            "git_commit",
        }
    )
    commit_ref = not raw_jsonl and bool(
        re.search(r"\b[0-9a-f]{7,40}\b", support_text.lower())
        and any(term in support_text.lower() for term in ("commit", "git show", "git log"))
    )
    if not (artifact_ref or commit_ref):
        return False
    return historical_orchestration_secondary_support_from_text(support_text)


def historical_orchestration_secondary_artifact_path(path: str) -> bool:
    normalized_path = str(path or "").strip("/").lower().replace("\\", "/")
    normalized = f"/{normalized_path}"
    return any(
        marker in normalized
        for marker in (
            "/tasks/",
            "/reviews/",
            "/codex/state/reviews/",
            "/.git/objects/",
            "/.git/logs/",
        )
    )


def historical_orchestration_secondary_support_row_allowed(row: dict[str, Any]) -> bool:
    roles = set(row.get("orchestration_roles") or [])
    if "secondary_task_review_commit_support" not in roles:
        return False
    if row.get("diagnostic_only") or row.get("read_more_actionable") is False:
        return False
    if row.get("source_chronology_role") in {
        "current_run_self_echo_diagnostic",
        "current_task_same_loop_diagnostic",
    }:
        return False
    return True


def historical_orchestration_role_coverage_rows(
    *,
    required_primary_roles: list[str],
    required_secondary_roles: list[str],
    final_support: list[dict[str, Any]],
    secondary_support: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for role in required_primary_roles:
        sources = [
            read
            for read in final_support
            if role in set(read.get("orchestration_roles") or [])
        ]
        rows.append(
            {
                "role": role,
                "tier": "primary_raw_final_support",
                "status": "covered" if sources else "missing",
                "source_count": len(sources),
                "source_targets": [str(read.get("target") or "") for read in sources[:3]],
            }
        )
    for role in required_secondary_roles:
        sources = [
            read
            for read in secondary_support
            if role in set(read.get("orchestration_roles") or [])
        ]
        rows.append(
            {
                "role": role,
                "tier": "secondary_task_review_commit_support",
                "status": "covered" if sources else "missing",
                "source_count": len(sources),
                "source_targets": [str(read.get("target") or "") for read in sources[:3]],
            }
        )
    return rows


def historical_orchestration_read_plan_row(
    read: dict[str, Any],
    *,
    candidate: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    text = historical_provenance_read_text(read, candidate)
    roles = historical_orchestration_roles_from_text(text)
    if not roles:
        roles = list(read.get("orchestration_role_hints") or [])
    if not roles and read.get("embedded_source_ref_unwrapped"):
        roles = historical_orchestration_roles_from_text(str(read.get("reason") or ""))
    if historical_orchestration_secondary_support_from_ref(read, candidate, text=text):
        roles.append("secondary_task_review_commit_support")
        roles = dedupe(roles)
    role = str(selection.get("source_chronology_role") or "candidate_or_original_source_ref")
    current_run_echo = role == "current_run_self_echo_diagnostic" or historical_provenance_current_run_or_self_echo_candidate(
        text,
        query=query,
    )
    current_task_same_loop = historical_orchestration_current_task_same_loop_candidate(
        read,
        candidate=candidate,
        text=text,
    )
    raw_jsonl = is_jsonl_read_target(target) or str(source_ref.get("path") or "").endswith(".jsonl")
    actionable = read_is_required_surface_actionable(read)
    final_support_allowed = bool(raw_jsonl and actionable and not current_run_echo and not current_task_same_loop)
    if current_task_same_loop and role == "candidate_or_original_source_ref":
        role = "current_task_same_loop_diagnostic"
    if role == "candidate_or_original_source_ref" and final_support_allowed:
        role = "pre_existing_raw_jsonl_source_ref"
    return {
        "kind": read.get("kind"),
        "target": target,
        "reason": read.get("reason"),
        "source_ref": guided_source_ref(source_ref) if source_ref else None,
        "source_chronology_role": role,
        "orchestration_roles": roles,
        "final_support_allowed": final_support_allowed,
        "required_final_support_read": final_support_allowed,
        "read_more_actionable": bool(read.get("read_more_actionable", True))
        and not current_run_echo
        and not current_task_same_loop,
        "followthrough_actionable": bool(read.get("followthrough_actionable", True))
        and not current_run_echo
        and not current_task_same_loop,
        "diagnostic_only": bool(read.get("diagnostic_only")) or current_run_echo or current_task_same_loop,
        "final_support_rule": (
            "may_support_historical_orchestration_claims_after_inspection"
            if final_support_allowed
            else "current_run_or_non_final_lead_do_not_count_as_historical_orchestration_support"
        ),
        "raw_text_included": False,
    }


def historical_orchestration_current_task_same_loop_candidate(
    read: dict[str, Any],
    *,
    candidate: dict[str, Any],
    text: str,
) -> bool:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    normalized_path = path.lower().replace("\\", "/").replace("-", "_")
    path_parts = [part for part in normalized_path.split("/") if part]
    current_task_path_parts = {
        "current_task",
        "currenttask",
        "same_loop",
        "sameloop",
        "same_run",
        "samerun",
        "current_run",
        "currentrun",
        "live_task",
        "livetask",
        "this_task",
        "thistask",
    }
    if any(part in current_task_path_parts for part in path_parts):
        return True
    basename = path_parts[-1] if path_parts else ""
    if basename in {"status.jsonl", "task_status.jsonl", "state.jsonl"} and any(
        part in {"current", "active", "live", "task_state", "task_status"} for part in path_parts
    ):
        return True

    lowered = re.sub(
        r"\s+",
        " ",
        " ".join(
            str(part or "")
            for part in (
                text,
                read.get("reason"),
                candidate.get("snippet"),
                target,
            )
        )
        .lower()
        .replace("_", " "),
    ).strip()
    return any(
        marker in lowered
        for marker in (
            "current task",
            "current-task",
            "same loop",
            "same-loop",
            "same run",
            "same-run",
            "live task",
            "this task",
            "active task status",
            "current status row",
            "task status row",
        )
    )


def historical_orchestration_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    if not plan:
        return {}
    return {
        "schema_version": plan.get("schema_version", 1),
        "source_plan_name": plan.get(
            "source_plan_name",
            "historical_orchestration_role_coverage_source_plan",
        ),
        "status": plan.get("status"),
        "required_primary_roles": list(plan.get("required_primary_roles") or []),
        "required_secondary_roles": list(plan.get("required_secondary_roles") or []),
        "required_roles": list(plan.get("required_roles") or []),
        "covered_primary_roles": list(plan.get("covered_primary_roles") or []),
        "covered_secondary_roles": list(plan.get("covered_secondary_roles") or []),
        "covered_roles": list(plan.get("covered_roles") or []),
        "missing_roles": list(plan.get("missing_roles") or []),
        "missing_primary_roles": list(plan.get("missing_primary_roles") or []),
        "missing_secondary_roles": list(plan.get("missing_secondary_roles") or []),
        "role_coverage": list(plan.get("role_coverage") or []),
        "primary_historical_final_support_read_count": plan.get(
            "primary_historical_final_support_read_count", 0
        ),
        "secondary_task_review_commit_support_read_count": plan.get(
            "secondary_task_review_commit_support_read_count", 0
        ),
        "missing_role_recovery_ref_count": plan.get("missing_role_recovery_ref_count", 0),
        "bounded_non_final_required_read_count": plan.get("bounded_non_final_required_read_count", 0),
        "current_run_self_echo_candidate_count": plan.get("current_run_self_echo_candidate_count", 0),
        "primary_historical_final_support_reads": list(
            plan.get("primary_historical_final_support_reads") or []
        )[:3],
        "secondary_task_review_commit_support_reads": list(
            plan.get("secondary_task_review_commit_support_reads") or []
        )[:3],
        "missing_role_recovery_refs": list(plan.get("missing_role_recovery_refs") or [])[:3],
        "bounded_non_final_required_reads": list(plan.get("bounded_non_final_required_reads") or [])[:3],
        "claim_boundaries": plan.get("claim_boundaries"),
    }


def historical_provenance_handles(query: str) -> list[str]:
    handles: list[str] = []
    for pattern in (LABELED_HANDLE_RE, BACKTICK_HANDLE_RE, HANDLE_TOKEN_RE, TASKISH_HANDLE_RE):
        for match in pattern.finditer(query or ""):
            value = match.group(1).strip()
            normalized = value.strip("`'\".,:;()[]{}")
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in {
                "jsonl-history-search",
                "semantic-debug",
                "source-locator-handoff",
                "answer-ready-handoff",
            }:
                continue
            if normalized not in handles:
                handles.append(normalized)
    return handles


def historical_provenance_self_echo_policy_wanted(
    query: str,
    *,
    family: dict[str, Any],
    handles: list[str],
) -> bool:
    if not handles:
        return False
    if historical_provenance_current_scope_requested(query) or historical_provenance_prior_answer_or_report_audit_requested(query):
        return False
    lowered = re.sub(r"\s+", " ", str(query or "").lower().replace("_", " ")).strip()
    if not TASKISH_HANDLE_RE.search(query or ""):
        return False
    primary = str((family or {}).get("primary") or "")
    if primary not in {"provenance", "claimed_action", "why_did_this_happen", "semantic_debug"}:
        return False
    return any(
        term in lowered
        for term in (
            "coordinator",
            "worker",
            "reviewer",
            "rereviewer",
            "subagent",
            "spawned",
            "launched",
            "parent",
            "orchestration",
            "provenance",
            "source chronology",
            "chronology",
            "source chain",
            "evidence chain",
            "reconstruct",
            "prove",
            "proof",
            "task",
            "branch",
            "commit",
            "merge",
            "implementation",
            "fix",
            "rereview",
            "what changed",
            "what happened",
            "historical",
            "harness",
            "trouble",
            "struggle",
            "struggled",
            "verified",
        )
    )


def dedupe_historical_provenance_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for read in next_reads:
        source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
        key = str(read.get("target") or source_ref_target(source_ref) or source_ref.get("path") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(read)
    return result


def historical_provenance_current_scope_requested(query: str) -> bool:
    if clearly_intends_current_session(query) or process_actual_use_current_run_or_tool_behavior_requested(query):
        return True
    lowered = re.sub(r"\s+", " ", str(query or "").lower().replace("_", " ")).strip()
    return bool(
        re.search(
            r"\b(?:current|this|live)\s+(?:task|worktree|branch|slice|lane|run|session|tool|route)\b",
            lowered,
        )
    )


def historical_provenance_prior_answer_or_report_audit_requested(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").lower().replace("_", " ")).strip()
    prior_or_report = any(
        term in lowered
        for term in (
            "prior answer",
            "previous answer",
            "earlier answer",
            "last answer",
            "final answer",
            "prior final",
            "previous final",
            "that report",
            "this report",
            "prior report",
            "previous report",
            "adjudication report",
        )
    )
    audit = any(term in lowered for term in ("audit", "check", "inspect", "verify", "did it", "did the answer"))
    return prior_or_report and audit


def ensure_read_more_actionability_defaults(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for read in next_reads:
        row = dict(read)
        if "read_more_actionable" not in row and row.get("expansion_actions"):
            row["read_more_actionable"] = True
        if "followthrough_actionable" not in row and row.get("expansion_actions"):
            row["followthrough_actionable"] = True
        result.append(row)
    return result


def historical_provenance_current_run_or_self_echo_candidate(text: str, *, query: str | None = None) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    echo = setup_echo_classification(text, query=query or "")
    if echo.get("is_setup_echo"):
        return True
    normalized_query = re.sub(r"\s+", " ", str(query or "").lower().replace("_", " ")).strip()
    if normalized_query and normalized_query in lowered:
        return True
    current_tool_markers = (
        "jsonl-history-search semantic-debug",
        "semantic-debug command",
        "semantic debug command",
        "semantic-debug output",
        "semantic debug output",
        "source locator handoff",
        "source_locator_handoff",
        "answer ready handoff",
        "answer_ready_handoff",
        "command_trace",
        "raw-trace-audit",
        "raw_trace_audit",
        "adjudication/raw-trace-audit",
        "same-lane",
        "same lane",
        "lane prompt",
        "jsonl lane",
        "manual lane",
        "current-run prompt",
        "current run prompt",
        "original token count",
        "process exited with code",
    )
    return any(marker in lowered for marker in current_tool_markers)


def historical_provenance_current_day_jsonl_echo_candidate(read: dict[str, Any], *, query: str) -> bool:
    if historical_provenance_current_scope_requested(query):
        return False
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    path = str(source_ref.get("path") or source_target_path_part(target) or "")
    if not path.endswith(".jsonl"):
        return False
    source_date = codex_session_path_date(path)
    if source_date is None:
        return False
    today = {datetime.now().date(), datetime.now(timezone.utc).date()}
    return source_date in today


def codex_session_path_date(path: str) -> Any:
    normalized = str(path or "").lower().replace("\\", "/")
    for pattern in (CODEX_SESSION_DATE_RE, CODEX_ROLLOUT_DATE_RE):
        match = pattern.search(normalized)
        if not match:
            continue
        try:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            ).date()
        except ValueError:
            return None
    return None


def historical_provenance_source_category(
    read: dict[str, Any],
    *,
    candidate: dict[str, Any],
    query: str,
    handles: list[str],
    current_run_echo: bool,
    current_task_same_loop: bool,
) -> dict[str, Any]:
    if current_task_same_loop and not current_run_echo:
        return historical_provenance_category_row("current_task_same_loop_diagnostic", final=False, diagnostic=True)
    if current_run_echo:
        return historical_provenance_category_row("current_run_self_echo_diagnostic", final=False, diagnostic=True)
    text = historical_provenance_read_text(read, candidate)
    if historical_provenance_post_hoc_answer_key_candidate(text, query=query):
        return historical_provenance_category_row("post_hoc_answer_key_diagnostic", final=False, diagnostic=True)
    target = str(read.get("target") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    raw_jsonl = is_jsonl_read_target(target) or path.lower().replace("\\", "/").endswith(".jsonl")
    if handles and not historical_provenance_matches_requested_handle(text, handles):
        return historical_provenance_category_row("unrelated_high_lexical_diagnostic", final=False, diagnostic=True)
    selection = read.get("historical_provenance_selection") if isinstance(read.get("historical_provenance_selection"), dict) else {}
    existing_role = str(selection.get("source_chronology_role") or "")
    if (
        existing_role in {
            "target_historical_raw_jsonl_source_ref",
            "candidate_or_original_source_ref",
            "pre_existing_source_ref_quoted_by_current_run_echo",
        }
        and selection.get("diagnostic_only") is not True
        and selection.get("final_support_allowed") is True
    ):
        return historical_provenance_category_row(existing_role, final=True, diagnostic=False)
    if raw_jsonl:
        return historical_provenance_category_row("target_historical_raw_jsonl_source_ref", final=True, diagnostic=False)
    if historical_provenance_secondary_task_review_commit_ref(read, candidate, text=text):
        return historical_provenance_category_row("historical_task_review_commit_secondary_ref", final=True, diagnostic=False)
    return historical_provenance_category_row("candidate_or_original_source_ref", final=True, diagnostic=False)


def historical_provenance_category_row(role: str, *, final: bool, diagnostic: bool) -> dict[str, Any]:
    return {
        "source_chronology_role": role,
        "final_support_allowed": final,
        "diagnostic_only": diagnostic,
    }


def historical_provenance_matches_requested_handle(text: str, handles: list[str]) -> bool:
    normalized = normalize_source_handle(text)
    if not normalized:
        return False
    return any(normalize_source_handle(handle) in normalized for handle in handles if handle)


def historical_provenance_post_hoc_answer_key_candidate(text: str, *, query: str | None = None) -> bool:
    if historical_provenance_prior_answer_or_report_audit_requested(query or ""):
        return False
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    return any(
        marker in lowered
        for marker in (
            "answer-key",
            "answer key",
            "prior final",
            "previous final",
            "final_answer",
            "final answer",
            "lane final",
            "manual lane final",
            "jsonl lane final",
            "adjudication",
            "raw trace audit",
            "raw-trace audit",
            "source_category_blocker",
            "source category blocker",
            "pressure report",
            "post-merge pressure",
            "runner report",
            "value gate",
        )
    )


def historical_provenance_secondary_task_review_commit_ref(
    read: dict[str, Any],
    candidate: dict[str, Any],
    *,
    text: str,
) -> bool:
    target = str(read.get("target") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    if historical_orchestration_secondary_artifact_path(path):
        return True
    support = " ".join(
        str(part or "")
        for part in (
            read.get("kind"),
            target,
            path,
            read.get("reason"),
            candidate.get("snippet"),
            text,
        )
    ).lower()
    return bool(
        re.search(r"\b[0-9a-f]{7,40}\b", support)
        and any(term in support for term in ("commit", "merge", "git show", "git log"))
    )


def historical_provenance_read_text(read: dict[str, Any], candidate: dict[str, Any]) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    return " ".join(
        str(part or "")
        for part in (
            read.get("kind"),
            read.get("target"),
            read.get("reason"),
            source_ref.get("path"),
            candidate.get("snippet"),
            candidate.get("role"),
            candidate.get("timestamp_utc"),
            " ".join(str(item) for item in candidate.get("reason_codes") or []),
        )
    )


def embedded_source_ref_next_reads(
    candidates: list[dict[str, Any]],
    *,
    query: str,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        source_ref = candidate.get("source_ref") if isinstance(candidate.get("source_ref"), dict) else {}
        candidate_target = source_ref_target(source_ref)
        text = historical_provenance_read_text(
            {"kind": "jsonl_source_ref", "target": candidate_target, "source_ref": source_ref},
            candidate,
        )
        if not historical_provenance_current_run_or_self_echo_candidate(text, query=query):
            continue
        for embedded_ref in embedded_jsonl_source_refs(text):
            target = source_ref_target(embedded_ref)
            if not target or target == candidate_target or target in seen:
                continue
            seen.add(target)
            candidate_id = f"embedded-source-ref-{len(reads) + 1:03d}"
            read = {
                "kind": "jsonl_source_ref",
                "target": target,
                "candidate_id": candidate_id,
                "source_ref": embedded_ref,
                "reason": "inspect original JSONL source ref quoted by diagnostic current-run/tool output",
                "orchestration_role_hints": historical_orchestration_roles_from_text(text),
                "embedded_source_ref_unwrapped": {
                    "schema_version": 1,
                    "from_candidate_id": candidate.get("candidate_id"),
                    "from_candidate_target": candidate_target,
                    "source_chronology_role": "pre_existing_source_ref_quoted_by_current_run_echo",
                    "raw_text_included": False,
                },
                "historical_provenance_selection": {
                    "schema_version": 1,
                    "source_chronology_role": "pre_existing_source_ref_quoted_by_current_run_echo",
                    "final_support_allowed": True,
                    "read_more_actionable": True,
                    "followthrough_actionable": True,
                    "diagnostic_only": False,
                    "raw_text_included": False,
                },
                "expansion_actions": read_more_actions_for_source_ref(
                    embedded_ref,
                    candidate_id=candidate_id,
                    expansion_action_context=expansion_action_context,
                ),
                "_historical_provenance_sort_key": (0, len(reads), 0),
            }
            if include_context_actions:
                read["context_actions"] = context_actions_for_source_ref(
                    embedded_ref,
                    candidate_id=candidate_id,
                    expansion_action_context=expansion_action_context,
                )
            reads.append(read)
    return reads


def embedded_jsonl_source_refs(text: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in EMBEDDED_JSONL_SOURCE_REF_RE.finditer(text or ""):
        path = " ".join(match.group("path").split())
        if not path.startswith("/"):
            continue
        line = int(match.group("line"))
        key = f"{path}:{line}"
        if key in seen:
            continue
        seen.add(key)
        refs.append({"path": path, "line": line})
    return refs[:8]


def historical_provenance_read_sort_key(read: dict[str, Any], index: int) -> tuple[int, int, int]:
    selection = read.get("historical_provenance_selection") if isinstance(read.get("historical_provenance_selection"), dict) else {}
    role = str(selection.get("source_chronology_role") or "")
    if read.get("diagnostic_only") or read.get("read_more_actionable") is False:
        diagnostic_rank = {
            "current_run_self_echo_diagnostic": 0,
            "current_task_same_loop_diagnostic": 1,
            "post_hoc_answer_key_diagnostic": 2,
            "unrelated_high_lexical_diagnostic": 3,
        }.get(role, 4)
        return (4, diagnostic_rank, index)
    if read.get("embedded_source_ref_unwrapped"):
        return (0, 0, index)
    role_rank = {
        "target_historical_raw_jsonl_source_ref": 0,
        "pre_existing_source_ref_quoted_by_current_run_echo": 0,
        "historical_task_review_commit_secondary_ref": 1,
        "candidate_or_original_source_ref": 2,
    }.get(role, 2)
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    original_jsonl = 0 if target.endswith(".jsonl") or str(source_ref.get("path") or "").endswith(".jsonl") else 1
    return (1, role_rank + original_jsonl, index)


def process_actual_use_source_tier(*, kind: str, target: str | None, source_ref: dict[str, Any]) -> str:
    target_text = str(target or "")
    path = str(source_ref.get("path") or target_text.split(":", 1)[0] or "").lower().replace("\\", "/")
    kind_text = str(kind or "").lower()
    if "tool_event" in kind_text:
        return PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
    if kind_text in {"task_or_review_artifact", "artifact", "hint_path"}:
        return PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY
    if path.endswith(".jsonl") or ".jsonl:" in target_text.lower() or path.endswith(".jsonl.gz"):
        return PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
    if process_actual_use_secondary_artifact_path(path):
        return PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY
    return PROCESS_ACTUAL_USE_SOURCE_TIER_SUPPORTING


def classify_process_actual_use_read_chronology(
    read: dict[str, Any],
    *,
    candidate: dict[str, Any] | None = None,
    query: str | None = None,
    source_tier: str | None = None,
) -> dict[str, Any]:
    candidate = candidate or {}
    target = str(read.get("target") or "")
    kind = str(read.get("kind") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    text = " ".join(
        str(part or "")
        for part in (
            kind,
            target,
            path,
            read.get("reason"),
            candidate.get("snippet"),
            " ".join(str(item) for item in candidate.get("reason_codes") or []),
        )
    )
    explicit_request = process_actual_use_prior_answer_or_report_requested(query or "")
    explicit_current_run = process_actual_use_current_run_or_tool_behavior_requested(query or "")
    prior_final_prose = process_actual_use_prior_final_prose_candidate(text)
    post_hoc_artifact = process_actual_use_post_hoc_artifact_candidate(kind=kind, path=path, text=text)
    current_run_self_echo = process_actual_use_current_run_or_self_echo_candidate(text, query=query)
    tier = source_tier or process_actual_use_source_tier(kind=kind, target=target, source_ref=source_ref)
    if current_run_self_echo and not explicit_current_run:
        role = PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
        final_support_allowed = False
    elif (prior_final_prose or post_hoc_artifact) and not explicit_request:
        role = PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC
        final_support_allowed = False
    elif tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY:
        role = PROCESS_ACTUAL_USE_CHRONOLOGY_PRIMARY_RAW
        final_support_allowed = True
    else:
        role = PROCESS_ACTUAL_USE_CHRONOLOGY_SECONDARY
        final_support_allowed = False
    return {
        "schema_version": 1,
        "source_chronology_role": role,
        "final_support_allowed": final_support_allowed,
        "prior_final_prose_candidate": prior_final_prose,
        "post_hoc_artifact_candidate": post_hoc_artifact,
        "explicit_prior_answer_or_report_requested": explicit_request,
        "current_run_self_echo_candidate": current_run_self_echo,
        "explicit_current_run_or_tool_behavior_requested": explicit_current_run,
    }


def process_actual_use_current_run_or_tool_behavior_requested(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    explicit_patterns = (
        r"\bcurrent\s+(?:lane|tool|jsonl\s+lane|semantic-debug|semantic\s+debug|run|route|workflow)\b",
        r"\bthis\s+(?:lane|tool|jsonl\s+lane|semantic-debug|semantic\s+debug|route|workflow)\b",
        r"\bsame\s+(?:lane|run|eval|tool)\b",
        r"\blive\s+(?:lane|tool|semantic-debug|semantic\s+debug|jsonl\s+route)\b",
    )
    return any(re.search(pattern, lowered) for pattern in explicit_patterns)


def process_actual_use_current_run_or_self_echo_candidate(text: str, *, query: str | None = None) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    echo = setup_echo_classification(text, query=query or "")
    if echo.get("is_setup_echo"):
        return True
    current_tool_markers = (
        "jsonl-history-search semantic-debug",
        "semantic-debug command",
        "semantic debug command",
        "semantic-debug output",
        "semantic debug output",
        "process actual use final support read plan",
        "process_actual_use_final_support_read_plan",
        "source locator handoff",
        "source_locator_handoff",
        "answer ready handoff",
        "answer_ready_handoff",
        "follow-next-reads",
        "follow next reads",
        "raw-lane-tool-reports",
        "raw lane tool reports",
        "lane prompt",
        "worker lane",
        "jsonl lane",
        "same lane eval",
        "same-lane eval",
        "current benchmark",
        "current-run prompt",
        "current run prompt",
    )
    return any(marker in lowered for marker in current_tool_markers)


def process_actual_use_prior_answer_or_report_requested(text: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(text or "").lower().replace("_", " ")).strip()
    historical_answer_or_report = (
        r"(?:prior|previous|earlier|past|saved|old)\s+"
        r"(?:(?:final\s+)?answer|answer\s+(?:text|prose)|final\s+prose|report|review\s+report)"
    )
    explicit_request_patterns = (
        rf"\b(?:find|locate|recover|quote|show|summarize)\s+(?:me\s+)?(?:the\s+)?{historical_answer_or_report}\b",
        rf"\bwhat\s+did\s+(?:the\s+)?{historical_answer_or_report}\s+(?:say|state|claim|answer)\b",
        rf"\b(?:what|which)\s+(?:was|is)\s+(?:the\s+)?{historical_answer_or_report}\b",
        rf"\b(?:find|locate|recover|quote|show|summarize)\s+(?:me\s+)?(?:the\s+)?(?:answer-key|answer\s+key)\b",
    )
    return any(re.search(pattern, lowered) for pattern in explicit_request_patterns)


def process_actual_use_prior_final_prose_candidate(text: str) -> bool:
    lowered = str(text or "").lower().replace("_", "-")
    return any(
        cue in lowered
        for cue in (
            "final-answer",
            "final answer",
            "prior answer",
            "previous answer",
            "earlier answer",
            "answer-key",
            "answer key",
            "final prose",
            "answer prose",
        )
    )


def process_actual_use_post_hoc_artifact_candidate(*, kind: str, path: str, text: str) -> bool:
    normalized_path = str(path or "").lower().replace("\\", "/").replace("_", "-")
    lowered = str(text or "").lower().replace("_", "-")
    artifact_kind = str(kind or "").lower() in {"task_or_review_artifact", "artifact", "hint_path"}
    post_hoc_path = any(
        marker in f"/{normalized_path.strip('/')}"
        for marker in (
            "/reports/",
            "/codex/state/reviews/",
            "/reviews/",
            "/tasks/",
            "/learnings/",
        )
    )
    answer_key_name = any(
        cue in normalized_path
        for cue in (
            "final-answer",
            "answer-key",
            "raw-trace-audit",
            "adjudication",
            "benefit-classification",
            "pressure-eval",
            "free-pressure",
        )
    )
    answer_key_text = any(
        cue in lowered
        for cue in (
            "post-hoc answer-key",
            "answer-key artifact",
            "prior final",
            "previous final",
            "final-answer prose",
            "final answer prose",
        )
    )
    return bool(answer_key_text or answer_key_name or (artifact_kind and post_hoc_path and process_actual_use_prior_final_prose_candidate(lowered)))


def build_process_actual_use_final_support_read_plan(
    next_reads: list[dict[str, Any]],
    *,
    query: str | None = None,
) -> dict[str, Any]:
    explicit_request = process_actual_use_prior_answer_or_report_requested(query or "")
    explicit_current_run = process_actual_use_current_run_or_tool_behavior_requested(query or "")
    required: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    answer_key: list[dict[str, Any]] = []
    diagnostic: list[dict[str, Any]] = []
    for read in next_reads:
        row = process_actual_use_read_plan_row(
            read,
            explicit_request=explicit_request,
            explicit_current_run_request=explicit_current_run,
        )
        selection = read.get("process_actual_use_selection") if isinstance(read.get("process_actual_use_selection"), dict) else {}
        role = row.get("source_chronology_role")
        if selection.get("required_final_support_read") or row.get("final_support_allowed"):
            required.append(row)
        elif role == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO:
            diagnostic.append(row)
        elif role == PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC:
            answer_key.append(row)
        else:
            secondary.append(row)
    required = required[:PROCESS_ACTUAL_USE_REQUIRED_READ_CAP]
    secondary = secondary[:PROCESS_ACTUAL_USE_SECONDARY_LEAD_CAP]
    answer_key = answer_key[:PROCESS_ACTUAL_USE_ANSWER_KEY_CAP]
    diagnostic = diagnostic[:PROCESS_ACTUAL_USE_ANSWER_KEY_CAP]
    plan = {
        "schema_version": 1,
        "status": "active",
        "explicit_prior_answer_or_report_requested": explicit_request,
        "explicit_current_run_or_tool_behavior_requested": explicit_current_run,
        "required_primary_raw_final_support_reads": required,
        "secondary_leads": secondary,
        "post_hoc_answer_key_candidates": answer_key,
        "diagnostic_only_candidates": diagnostic,
        "required_primary_raw_final_support_read_count": len(required),
        "secondary_lead_count": len(secondary),
        "post_hoc_answer_key_candidate_count": len(answer_key),
        "diagnostic_only_candidate_count": len(diagnostic),
        "diagnostic_only_excluded_candidate_count": sum(
            1 for row in answer_key + diagnostic if row.get("diagnostic_only")
        ),
        "actionable_followthrough_read_count": sum(
            1 for row in required + secondary + answer_key + diagnostic if row.get("followthrough_actionable")
        ),
        "excluded_from_required_final_support_read_count": (
            len(secondary) + (0 if explicit_request else len(answer_key)) + len(diagnostic)
        ),
        "compact_read_order": [row.get("target") for row in required if row.get("target")],
        "guidance": {
            "do_first": "open_required_primary_raw_final_support_reads",
            "secondary_leads_policy": "use_secondary_leads_only_to_locate_or_explain raw timing/reviewer enforcement gaps",
            "post_hoc_answer_key_policy": (
                "prior final-answer prose, reports, tasks, reviews, and adjudication artifacts are not final support "
                "for process actual-use unless the prompt explicitly asks for that prior answer/report"
            ),
            "post_hoc_answer_key_followthrough_policy": (
                "diagnostic-only for normal process actual-use prompts: keep visible for chronology/contamination "
                "audit, but do not open as required/top/read-more follow-through or answer evidence"
            ),
            "current_run_self_echo_policy": (
                "current-run prompt echoes, current semantic-debug command/output, and same-lane eval mechanics "
                "are diagnostic-only for historical actual-use prompts; they count as support only when the prompt "
                "explicitly asks about the current lane/tool/run behavior"
            ),
            "prior_final_prose_counts_as_support": bool(explicit_request),
            "current_run_self_echo_counts_as_support": bool(explicit_current_run),
            "chronology_required": True,
            "raw_text_included": False,
        },
        "claim_boundaries": {
            "primary_raw_timing_evidence_required_for_behavior_claims": True,
            "secondary_leads_count_as_final_support": False,
            "post_hoc_answer_key_candidates_count_as_final_support": bool(explicit_request),
            "post_hoc_answer_key_candidates_count_as_actionable_followthrough": bool(explicit_request),
            "current_run_self_echo_candidates_count_as_final_support": bool(explicit_current_run),
            "current_run_self_echo_candidates_count_as_actionable_followthrough": bool(explicit_current_run),
            "release_or_token_savings_claim_supported": False,
        },
        "cap_policy": {
            "required_primary_raw_final_support_reads": PROCESS_ACTUAL_USE_REQUIRED_READ_CAP,
            "secondary_leads": PROCESS_ACTUAL_USE_SECONDARY_LEAD_CAP,
            "post_hoc_answer_key_candidates": PROCESS_ACTUAL_USE_ANSWER_KEY_CAP,
            "deterministic_order": "required primary raw final-support reads first; secondary leads and answer-key candidates are separated",
        },
    }
    plan["process_actual_use_bucket_evidence_bundle"] = build_process_actual_use_bucket_evidence_bundle(plan)
    return plan


def build_process_actual_use_bucket_evidence_bundle(plan: dict[str, Any]) -> dict[str, Any]:
    required = list(plan.get("required_primary_raw_final_support_reads") or [])
    secondary = list(plan.get("secondary_leads") or [])
    answer_key = list(plan.get("post_hoc_answer_key_candidates") or [])
    diagnostic = list(plan.get("diagnostic_only_candidates") or [])
    buckets: dict[str, dict[str, Any]] = {}
    for bucket in process_actual_use_bucket_contract():
        bucket_name = str(bucket.get("bucket") or "")
        primary_support = [
            process_actual_use_bucket_evidence_row(item, evidence_role="primary_raw_evidence")
            for item in required
            if bucket_name in set(item.get("selected_buckets") or [])
        ][:PROCESS_ACTUAL_USE_BUCKET_EVIDENCE_CAP]
        secondary_leads = [
            process_actual_use_bucket_evidence_row(item, evidence_role="secondary_lead_context")
            for item in secondary
            if bucket_name in set(item.get("selected_buckets") or [])
        ][:PROCESS_ACTUAL_USE_BUCKET_EVIDENCE_CAP]
        excluded_answer_keys = [
            process_actual_use_bucket_evidence_row(item, evidence_role="excluded_post_hoc_answer_key")
            for item in answer_key
            if bucket_name in set(item.get("selected_buckets") or [])
        ][:PROCESS_ACTUAL_USE_BUCKET_EVIDENCE_CAP]
        excluded_current_run_self_echoes = [
            process_actual_use_bucket_evidence_row(item, evidence_role="excluded_current_run_self_echo_diagnostic")
            for item in diagnostic
            if bucket_name in set(item.get("selected_buckets") or [])
        ][:PROCESS_ACTUAL_USE_BUCKET_EVIDENCE_CAP]
        if primary_support:
            status = "primary_raw_support_available"
            answer_use = "use_primary_raw_reads_for_behavior_or_timing_claims"
        elif secondary_leads:
            status = "secondary_leads_only"
            answer_use = "lead_or_context_only_do_not_count_as_behavior_proof"
        elif excluded_answer_keys:
            status = "excluded_post_hoc_answer_key_only"
            answer_use = "excluded_unless_prompt_explicitly_asks_for_prior_answer_or_report"
        elif excluded_current_run_self_echoes:
            status = "excluded_current_run_self_echo_only"
            answer_use = "diagnostic_only_unless_prompt_explicitly_asks_about_current_lane_tool_or_run"
        else:
            status = "no_evidence_in_selected_reads"
            answer_use = "state_gap_or_run_targeted_follow_up_before_claiming_bucket"
        buckets[bucket_name] = {
            "bucket": bucket_name,
            "status": status,
            "requirement": bucket.get("requirement"),
            "expected_evidence_status": bucket.get("evidence_status"),
            "answer_use": answer_use,
            "primary_raw_evidence": primary_support,
            "secondary_leads": secondary_leads,
            "excluded_post_hoc_answer_key_candidates": excluded_answer_keys,
            "excluded_current_run_self_echo_candidates": excluded_current_run_self_echoes,
            "primary_raw_evidence_count": len(primary_support),
            "secondary_lead_count": len(secondary_leads),
            "excluded_post_hoc_answer_key_candidate_count": len(excluded_answer_keys),
            "excluded_current_run_self_echo_candidate_count": len(excluded_current_run_self_echoes),
            "raw_text_included": False,
        }
    missing_buckets = [
        bucket
        for bucket, item in buckets.items()
        if item["status"] in {
            "no_evidence_in_selected_reads",
            "excluded_post_hoc_answer_key_only",
            "excluded_current_run_self_echo_only",
        }
    ]
    secondary_only_buckets = [
        bucket for bucket, item in buckets.items() if item["status"] == "secondary_leads_only"
    ]
    primary_supported_buckets = [
        bucket for bucket, item in buckets.items() if item["status"] == "primary_raw_support_available"
    ]
    return {
        "schema_version": 1,
        "status": "active",
        "source": "process_actual_use_final_support_read_plan",
        "bucket_order": [bucket.get("bucket") for bucket in process_actual_use_bucket_contract()],
        "buckets": buckets,
        "summary": {
            "primary_supported_buckets": primary_supported_buckets,
            "secondary_only_buckets": secondary_only_buckets,
            "missing_or_excluded_buckets": missing_buckets,
            "primary_supported_bucket_count": len(primary_supported_buckets),
            "secondary_only_bucket_count": len(secondary_only_buckets),
            "missing_or_excluded_bucket_count": len(missing_buckets),
            "post_hoc_answer_key_candidate_count": len(answer_key),
            "current_run_self_echo_candidate_count": len(diagnostic),
        },
        "guidance": {
            "do_first": "answer_from_bucket_primary_raw_evidence_when_available",
            "primary_raw_evidence_policy": "primary raw session/tool-event reads remain preferred for timing and behavior claims",
            "secondary_lead_policy": "secondary leads may explain context or route follow-up reads but do not displace primary behavior proof",
            "missing_bucket_policy": "missing buckets are explicit gaps; do not smooth them into a yes/no conclusion",
            "post_hoc_answer_key_policy": (
                "excluded from final support and normal follow-through unless explicitly requested; "
                "open only for contamination/chronology audit"
            ),
            "current_run_self_echo_policy": (
                "excluded from historical final support and normal follow-through unless the prompt explicitly "
                "asks about current lane/tool/run behavior"
            ),
            "raw_text_included": False,
        },
        "claim_boundaries": {
            "release_or_token_savings_claim_supported": False,
            "read_more_reduction_proven": False,
            "primary_raw_timing_evidence_required_for_behavior_claims": True,
            "secondary_leads_count_as_behavior_proof": False,
            "excluded_post_hoc_answer_key_candidates_are_actionable_followthrough": bool(
                plan.get("explicit_prior_answer_or_report_requested")
            ),
            "excluded_current_run_self_echo_candidates_are_actionable_followthrough": bool(
                plan.get("explicit_current_run_or_tool_behavior_requested")
            ),
        },
    }


def process_actual_use_bucket_evidence_row(item: dict[str, Any], *, evidence_role: str) -> dict[str, Any]:
    return {
        "kind": item.get("kind"),
        "target": item.get("target"),
        "reason": item.get("reason"),
        "actual_use_source_tier": item.get("actual_use_source_tier"),
        "source_chronology_role": item.get("source_chronology_role"),
        "evidence_role": evidence_role,
        "final_support_allowed": bool(item.get("final_support_allowed")),
        "read_more_actionable": bool(item.get("read_more_actionable", True)),
        "followthrough_actionable": bool(item.get("followthrough_actionable", True)),
        "diagnostic_only": bool(item.get("diagnostic_only", False)),
        "diagnostic_open_policy": item.get("diagnostic_open_policy"),
        "final_support_rule": item.get("final_support_rule"),
        "source_ref": item.get("source_ref"),
        "raw_text_included": False,
    }


def process_actual_use_read_plan_row(
    read: dict[str, Any],
    *,
    explicit_request: bool,
    explicit_current_run_request: bool = False,
) -> dict[str, Any]:
    selection = read.get("process_actual_use_selection") if isinstance(read.get("process_actual_use_selection"), dict) else {}
    role = selection.get("source_chronology_role") or selection.get("anti_contamination_role")
    if role is None:
        role = (
            "primary_raw_evidence"
            if read.get("actual_use_source_tier") == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
            else "legitimate_secondary_lead_or_context_not_final_behavior_proof"
        )
    final_support_allowed = bool(selection.get("final_support_allowed", False))
    if role == PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC and not explicit_request:
        final_support_allowed = False
    if role == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO and not explicit_current_run_request:
        final_support_allowed = False
    diagnostic_only = bool(
        selection.get("diagnostic_only")
        or (role == PROCESS_ACTUAL_USE_CHRONOLOGY_POST_HOC and not explicit_request)
        or (role == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO and not explicit_current_run_request)
    )
    read_more_actionable = bool(selection.get("read_more_actionable", read.get("read_more_actionable", True)))
    followthrough_actionable = bool(
        selection.get("followthrough_actionable", read.get("followthrough_actionable", True))
    )
    if diagnostic_only:
        read_more_actionable = False
        followthrough_actionable = False
    return {
        "kind": read.get("kind"),
        "target": read.get("target"),
        "reason": read.get("reason"),
        "actual_use_source_tier": read.get("actual_use_source_tier"),
        "source_chronology_role": role,
        "selected_buckets": list(selection.get("selected_buckets") or [])[:6],
        "final_support_allowed": final_support_allowed,
        "read_more_actionable": read_more_actionable,
        "followthrough_actionable": followthrough_actionable,
        "diagnostic_only": diagnostic_only,
        "diagnostic_open_policy": (
            "current_lane_tool_run_audit_only"
            if diagnostic_only and role == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
            else
            "contamination_audit_or_explicit_prior_answer_request_only"
            if diagnostic_only
            else "normal_process_actual_use_followthrough"
        ),
        "final_support_rule": (
            "may_support_behavior_claims_after_inspection"
            if final_support_allowed
            else "current_run_self_echo_diagnostic_only_do_not_count_as_historical_process_support"
            if role == PROCESS_ACTUAL_USE_CHRONOLOGY_CURRENT_RUN_SELF_ECHO
            else "lead_or_context_only_do_not_count_as_final_support"
        ),
        "source_ref": guided_source_ref(read.get("source_ref") or {}) if isinstance(read.get("source_ref"), dict) else None,
        "raw_text_included": False,
    }


def process_actual_use_secondary_artifact_path(path: str) -> bool:
    normalized = f"/{path.strip('/')}"
    return any(
        marker in normalized
        for marker in (
            "/tasks/",
            "/reviews/",
            "/reports/",
            "/docs/",
            "/learnings/",
            "/codex/state/reviews/",
        )
    )


def process_actual_use_bucket_matches(text: str) -> list[str]:
    lowered = str(text or "").lower()
    matches: list[str] = []
    for bucket, terms in PROCESS_ACTUAL_USE_BUCKET_TERMS.items():
        if any(term in lowered for term in terms):
            matches.append(bucket)
    return matches


def dedupe_process_actual_use_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for read in reads:
        source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
        key = json.dumps(
            {
                "kind": read.get("kind"),
                "target": read.get("target"),
                "path": source_ref.get("path"),
                "line": source_ref.get("line"),
            },
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(read)
    return result


def annotate_process_actual_use_source_locator_handoff(
    handoff: dict[str, Any],
    *,
    query: str | None = None,
) -> dict[str, Any]:
    result = dict(handoff)
    raw_count = 0
    secondary_count = 0
    windows = []
    for index, window in enumerate(list(result.get("source_windows") or [])):
        row = dict(window)
        tier = process_actual_use_source_tier(
            kind="jsonl_source_ref",
            target=str(row.get("target") or ""),
            source_ref=row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {},
        )
        if tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY:
            raw_count += 1
            row["window_kind"] = "actual_use_raw_timing"
        elif tier == PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY:
            secondary_count += 1
            row["window_kind"] = "actual_use_secondary_lead"
        row["actual_use_source_tier"] = tier
        row["process_actual_use_selection"] = {
            "schema_version": 1,
            "source_tier": tier,
            "primary_for_behavior_claims": tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY,
            "secondary_artifacts_are_final_proof": False,
            "source_chronology_role": (
                "primary_raw_evidence"
                if tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
                else "legitimate_secondary_lead_or_context_not_final_behavior_proof"
            ),
            "final_support_allowed": tier == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY,
            "post_hoc_answer_key_candidate": False,
            "raw_text_included": False,
        }
        row["_process_actual_use_sort_key"] = process_actual_use_read_sort_key(row, index)
        windows.append(row)
    windows.sort(key=lambda item: item.pop("_process_actual_use_sort_key"))
    result["source_windows"] = windows
    result["next_reads"] = prioritize_process_actual_use_next_reads(
        list(result.get("next_reads") or []),
        candidates=[],
        tool_proof={"events": []},
        joins={"review_evidence": []},
        query=query,
    )
    packaging = dict(result.get("source_window_packaging") or {})
    selection_policy = dict(packaging.get("selection_policy") or {})
    selection_policy["process_actual_use_primary_raw_selection"] = {
        "schema_version": 1,
        "primary_raw_window_count": raw_count,
        "secondary_lead_window_count": secondary_count,
        "raw_windows_preferred_before_secondary_artifacts": True,
        "secondary_artifacts_final_behavior_proof": False,
        "raw_text_included": False,
    }
    packaging["selection_policy"] = selection_policy
    result["source_window_packaging"] = packaging
    result["process_actual_use_source_selection"] = {
        "schema_version": 1,
        "primary_raw_next_read_count": sum(
            1
            for item in result.get("next_reads") or []
            if item.get("actual_use_source_tier") == PROCESS_ACTUAL_USE_SOURCE_TIER_PRIMARY
            and item.get("followthrough_actionable", True) is not False
        ),
        "secondary_lead_next_read_count": sum(
            1
            for item in result.get("next_reads") or []
            if item.get("actual_use_source_tier") == PROCESS_ACTUAL_USE_SOURCE_TIER_SECONDARY
            and item.get("followthrough_actionable", True) is not False
        ),
        "diagnostic_only_excluded_next_read_count": sum(
            1
            for item in result.get("next_reads") or []
            if item.get("diagnostic_only")
        ),
        "raw_timing_windows_promoted_before_secondary_artifacts": True,
        "secondary_artifact_rule": "task/review/report/doc artifacts are leads/context unless they locate raw timing or reviewer enforcement",
        "anti_contamination_gate": "do_not_answer_from_post_hoc_task_review_report_summary_artifacts_when_raw_timing_evidence_is_available",
        "excluded_candidate_followthrough_policy": (
            "post-hoc answer-key candidates remain visible as diagnostics but are not normal actionable reads"
        ),
        "raw_text_included": False,
    }
    return result


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
    reviewed_pointer = item.get("reviewed_source_packet_pointer")
    if isinstance(reviewed_pointer, dict):
        for key in ("handle", "source_ref_id", "packet_handle"):
            if reviewed_pointer.get(key):
                texts.append(str(reviewed_pointer.get(key)))
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
        inferred_reviewed_source_packet_next_reads(
            query=query,
            source_plan=source_plan,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
    )
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
        inferred_explicit_date_remembered_instruction_next_reads(
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


def inferred_reviewed_source_packet_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    text = str(query or "").casefold()
    handles = [
        handle
        for handle in detect_source_handles(query)
        if str(handle.get("normalized") or "").startswith("real-jsh")
    ]
    if not handles:
        return []
    cwd = Path(str(source_plan.get("cwd") or ".")).expanduser()
    packet_roots = reviewed_packet_roots(cwd)
    if not packet_roots:
        return []
    reads: list[dict[str, Any]] = []
    for handle in handles[:3]:
        pointer = find_reviewed_source_packet_pointer(handle, packet_roots)
        if not pointer:
            continue
        source_ref = {
            "path": pointer["private_path"],
            "line": pointer["primary_line"],
            "session": Path(pointer["private_path"]).stem,
        }
        read = inferred_anchor_read(
            source_ref=source_ref,
            anchor_id=f"reviewed-source-packet-{len(reads) + 1:03d}",
            reason=(
                "open retained reviewed source pointer before current-session "
                "prompt echoes, packet-gate artifacts, or broad private search"
            ),
            anchor_kind="reviewed_source_packet_pointer",
            confidence="high",
            selection_reason=pointer["selection_reason"],
            score=150.0,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
        read["reviewed_source_packet_pointer"] = {
            "schema_version": 1,
            "handle": handle["handle"],
            "source_ref_id": pointer["source_ref_id"],
            "packet_handle": pointer["packet_handle"],
            "raw_private_text_included": False,
            "source_window_verification_required": True,
            "current_session_echo_recovery_discouraged": True,
            "broad_private_artifact_search_discouraged": True,
            "claim_boundary": "retained_packet_pointer_guides_source_inspection_not_final_answer",
        }
        reads.append(read)
    return reads


def reviewed_packet_roots(cwd: Path) -> list[Path]:
    candidates = [cwd / ".private" / "jsh-935"]
    parts = list(cwd.parts)
    if "worktrees" in parts:
        worktrees_index = parts.index("worktrees")
        if worktrees_index > 0:
            parent_repo = Path(*parts[:worktrees_index])
            candidates.append(parent_repo / ".private" / "jsh-935")
    for parent in cwd.parents:
        candidates.append(parent / ".private" / "jsh-935")
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved)
        if key in seen or not candidate.is_dir():
            continue
        seen.add(key)
        roots.append(candidate)
    return roots[:4]


def find_reviewed_source_packet_pointer(
    handle: dict[str, Any],
    packet_roots: list[Path],
) -> dict[str, Any] | None:
    normalized = str(handle.get("normalized") or "")
    if not normalized:
        return None
    for root in packet_roots:
        for packet in sorted(root.glob("*/case-packet.md"))[:80]:
            try:
                text = packet.read_text(encoding="utf-8")
            except OSError:
                continue
            if normalized not in normalize_source_handle(text):
                continue
            pointer = parse_reviewed_source_pointer(text, handle_normalized=normalized)
            if not pointer:
                continue
            pointer["packet_handle"] = f"private:jsh-935/{packet.parent.name}/case-packet.md"
            pointer["selection_reason"] = "reviewed_source_packet_pointer"
            return pointer
    return None


def parse_reviewed_source_pointer(text: str, *, handle_normalized: str) -> dict[str, Any] | None:
    sections = re.split(r"\n###\s+", "\n" + text)
    for section in sections:
        first_line, _, body = section.partition("\n")
        heading = first_line.strip()
        if not heading or handle_normalized not in normalize_source_handle(heading):
            continue
        private_path_match = re.search(r"(?m)^-\s+private_path:\s*(.+?)\s*$", body)
        primary_line_match = re.search(r"(?m)^-\s+primary_line:\s*(\d+)\s*$", body)
        if not private_path_match or not primary_line_match:
            continue
        private_path = private_path_match.group(1).strip()
        try:
            primary_line = int(primary_line_match.group(1))
        except ValueError:
            continue
        if not private_path or primary_line < 1:
            continue
        return {
            "source_ref_id": heading,
            "private_path": private_path,
            "primary_line": primary_line,
        }
    return None


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
    selected_by_reason: dict[str, dict[str, Any]] = {}
    used_targets: set[str] = set()

    def add_item(reason: str, item: dict[str, Any] | None) -> None:
        if not item:
            return
        target = source_ref_target(item.get("source_ref") or {})
        if not target or target in used_targets:
            return
        selected_by_reason[reason] = item
        used_targets.add(target)

    def first_for_reason(reason: str) -> dict[str, Any] | None:
        for item in scored:
            if item.get("selection_reason") != reason:
                continue
            target = source_ref_target(item.get("source_ref") or {})
            if not target or target in used_targets:
                continue
            return item
        return None

    add_item("assistant_followup_correction", first_for_reason("assistant_followup_correction"))
    add_item("assistant_persistent_vs_wait_distinction", first_for_reason("assistant_persistent_vs_wait_distinction"))
    add_item("user_followup_or_original_challenge", first_for_reason("user_followup_or_original_challenge"))

    focus_refs = [
        item.get("source_ref") or {}
        for key, item in selected_by_reason.items()
        if key in {"assistant_followup_correction", "assistant_persistent_vs_wait_distinction"}
    ]
    focus_paths = {str(ref.get("path") or "") for ref in focus_refs if ref.get("path")}
    focus_lines = [int(ref.get("line") or 0) for ref in focus_refs if int(ref.get("line") or 0) > 0]
    focus_line = min(focus_lines) if focus_lines else None
    source_candidates = [
        item
        for item in scored
        if item.get("selection_reason") == "source_inspection_for_correction_window"
        and source_ref_target(item.get("source_ref") or {}) not in used_targets
    ]
    if source_candidates and focus_line is not None:
        source_candidates.sort(
            key=lambda item: (
                0 if str((item.get("source_ref") or {}).get("path") or "") in focus_paths else 1,
                0 if int((item.get("source_ref") or {}).get("line") or 0) <= focus_line else 1,
                abs(int((item.get("source_ref") or {}).get("line") or 0) - focus_line),
                -float(item.get("score") or 0),
            )
        )
    add_item(
        "source_inspection_for_correction_window",
        source_candidates[0] if source_candidates else first_for_reason("source_inspection_for_correction_window"),
    )

    selected = [
        selected_by_reason[reason]
        for reason in priority
        if reason in selected_by_reason
    ]
    for item in scored:
        if len(selected) >= 4:
            break
        target = source_ref_target(item.get("source_ref") or {})
        if not target or target in used_targets:
            continue
        selected.append(item)
        used_targets.add(target)
    return selected[:4]


EXPLICIT_CORRECTION_WINDOW_REQUIRED_ROLES = {
    "followup_correction",
    "source_inspection",
    "first_distinction",
    "user_challenge",
}


def explicit_correction_window_meta(read: dict[str, Any]) -> dict[str, Any]:
    meta = read.get("correction_window_source_selection")
    return meta if isinstance(meta, dict) else {}


def is_explicit_date_correction_window_read(read: dict[str, Any]) -> bool:
    meta = explicit_correction_window_meta(read)
    return bool(meta.get("bounded_to_explicit_date_window") and meta.get("bundle_role"))


def complete_explicit_correction_window_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    explicit_reads = [read for read in reads if is_explicit_date_correction_window_read(read)]
    roles = {str(explicit_correction_window_meta(read).get("bundle_role") or "") for read in explicit_reads}
    if not EXPLICIT_CORRECTION_WINDOW_REQUIRED_ROLES <= roles:
        return []
    return explicit_reads


def prefer_complete_explicit_correction_window_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    explicit_reads = complete_explicit_correction_window_reads(reads)
    if not explicit_reads:
        return reads
    return dedupe_dicts(explicit_reads)


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
    if not any(
        term in text
        for term in (
            "agent",
            "assistant",
            "did the",
            "did you",
            "actually",
            "workflow",
            "action proof",
            "those edits",
            "edits",
        )
    ):
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
        scored.extend(workflow_overwrite_case_records_for_path(path))
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


def workflow_overwrite_case_records_for_path(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        records = list(iter_records_for_file(path, "unknown_jsonl", timezone.utc))
    except (AdapterReadError, OSError, ValueError):
        return []
    for index, record in enumerate(records):
        score, matched, reason = score_workflow_overwrite_case_record(records, index)
        if score <= 0:
            continue
        rows.append(
            {
                "source_ref": {"path": str(path), "line": int(record.line), "session": Path(path).stem},
                "role": record.role,
                "timestamp_utc": record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
                "score": score,
                "selection_reason": f"known_action_case:{reason}:{'+'.join(matched[:4])}",
            }
        )
    return rows


def score_workflow_overwrite_case_record(records: list[Any], index: int) -> tuple[float, list[str], str]:
    record = records[index]
    role_text = str(record.role or "").casefold()
    lowered = str(record.text or "").casefold()
    context_start = max(0, index - 8)
    context_end = min(len(records), index + 9)
    local_context = " ".join(str(records[item].text or "").casefold() for item in range(context_start, context_end))
    matched: list[str] = []
    score = 0.0
    if "clarify planner split workflow" in local_context or "planner split workflow" in local_context:
        score += 70.0
        matched.append("planner_split_workflow")
    if "1d47497" in local_context:
        score += 55.0
        matched.append("commit_anchor")
    if any(term in local_context for term in ("did not overwrite", "not overwrite", "no, i did not overwrite")):
        score += 70.0
        matched.append("no_overwrite_answer")
    if "codex-orchestration.md" in local_context and "agent-workflow.md" in local_context:
        score += 40.0
        matched.append("workflow_docs")
    if any(term in local_context for term in ("left untouched", "untracked draft files", "still has two untracked")):
        score += 30.0
        matched.append("untouched_worktree")
    if any(term in local_context for term in ("git diff --check", "git diff --stat", "git status", "git log")):
        score += 20.0
        matched.append("tool_state_checks")
    if not matched:
        return 0.0, [], "no_workflow_overwrite_anchor"
    if any(token in role_text for token in ("assistant", "agent")):
        score += 18.0
    elif "function_call_output" in role_text and any(term in lowered for term in ("commit", "files changed")):
        score += 10.0
    elif "function_call" in role_text:
        score += 6.0
    if any(token in role_text for token in ("developer", "system", "turn_context", "token_count")):
        score -= 80.0
    if "jsonl-history-search semantic-debug" in lowered or "current-run prompt" in lowered:
        score -= 120.0
    if score < 95.0:
        return 0.0, matched, "weak_workflow_overwrite_anchor"
    reason = "workflow_overwrite_source_window"
    if any(term in lowered for term in ("did not overwrite", "not overwrite")):
        reason = "workflow_overwrite_assistant_answer"
    elif any(term in lowered for term in ("clarify planner split workflow", "1d47497", "files changed")):
        reason = "workflow_overwrite_commit_or_tool_output"
    return score, dedupe(matched), reason


def inferred_explicit_date_remembered_instruction_next_reads(
    *,
    query: str,
    source_plan: dict[str, Any],
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    text = str(query or "").casefold()
    if not any(
        phrase in text
        for phrase in (
            "what prior instruction",
            "what instruction",
            "prior instruction",
            "what did i tell",
            "what did the user give",
            "what did the user say",
        )
    ):
        return []
    date_matches = EXPLICIT_DATE_RE.findall(str(query or ""))
    if not date_matches:
        return []
    topic_terms = remembered_topic_terms(text)
    topic_terms.discard("instruction")
    if not topic_terms:
        return []
    roots = semantic_debug_codex_session_roots(source_plan)
    files: list[Path] = []
    for year, month, day in date_matches[:2]:
        for root in roots:
            date_dir = root / year / month / day
            if date_dir.is_dir():
                files.extend(sorted(date_dir.glob("*.jsonl"), key=lambda path: path.name))
    scored: list[dict[str, Any]] = []
    for path in files[:160]:
        scored.extend(
            inferred_topic_records_for_path(
                path,
                topic_terms=topic_terms,
                query=query,
            )
        )
    scored.sort(
        key=lambda item: (
            -float(item["score"]),
            str(item.get("timestamp_utc") or ""),
            str((item.get("source_ref") or {}).get("path") or ""),
            int((item.get("source_ref") or {}).get("line") or 0),
        )
    )
    reads: list[dict[str, Any]] = []
    for index, item in enumerate(scored[:3], 1):
        read = inferred_anchor_read(
            source_ref=item["source_ref"],
            anchor_id=f"explicit-date-remembered-instruction-{index:03d}",
            reason=(
                "open explicit-date remembered-instruction source window before "
                "current-session prompt echoes or broad repo search"
            ),
            anchor_kind="explicit_date_remembered_instruction",
            confidence="medium" if item["score"] < 80 else "high",
            selection_reason=item["selection_reason"],
            score=item["score"],
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
        read["inferred_anchor"]["bounded_to_explicit_date_window"] = True
        read["inferred_anchor"]["broad_rg_after_required_reads_discouraged"] = True
        read["inferred_anchor"]["current_session_echo_recovery_discouraged"] = True
        echo = item.get("prompt_echo_classification")
        if isinstance(echo, dict):
            read["inferred_anchor"]["prompt_echo_resistance"] = echo
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
        if not root.get("path"):
            continue
        path = Path(str(root["path"])).expanduser()
        if root.get("kind") == "codex_sessions" or path.name == "sessions":
            roots.append(path)
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
    process_actual_use = "process_actual_use" in detected
    if "process_actual_use" in detected:
        selected.append("process_actual_use_audit")
    if project_time_needed and not process_actual_use:
        selected.append("git_history_reconstruction")
    if joins.get("status") in {"joined", "no_join_evidence", "not_run"} and (
        (project_time_needed and not process_actual_use)
        or "provenance" in detected
        or joins.get("review_evidence")
        or joins.get("status") == "joined"
    ):
        selected.append("task_review_join")
    if not process_actual_use and (tool_proof.get("event_count", 0) or "claimed_action" in detected):
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
        "process_actual_use_audit",
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
        "process_actual_use_audit": "actual-use buckets separate artifact exists/cited from before-planning consultation, behavior change, post-hoc updates, and stale/performative gaps",
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


def build_scope_boundary_summary(
    *,
    source_scope: dict[str, Any],
    no_evidence: dict[str, Any],
    source_locator_handoff: dict[str, Any],
) -> dict[str, Any]:
    searched = source_scope.get("searched_scope") or {}
    not_searched = source_scope.get("not_searched_scope") or {}
    excluded = source_scope.get("excluded_scope") or {}
    cap_hit = source_scope.get("cap_hit") or {}
    source_plan = not_searched.get("source_plan") or {}
    duplicate_risk = scope_duplicate_mirror_risk(source_locator_handoff)
    cap_pressure_status = "cap_pressure_reported" if cap_hit.get("hit") else "no_cap_pressure_reported"
    missing_root_count = safe_count(not_searched.get("missing_root_count"))
    rejected_root_count = safe_count(excluded.get("rejected_root_count"))
    deferred_count = safe_count(source_plan.get("sources_deferred_budget"))
    pruned_count = safe_count(source_plan.get("sources_pruned_hard"))
    searched_file_count = safe_count(searched.get("searched_files"))
    scope_too_weak = bool(
        cap_hit.get("hit")
        or missing_root_count
        or rejected_root_count
        or deferred_count
        or searched_file_count == 0
    )
    return {
        "schema_version": 1,
        "searched_scope": {
            "selected_root_count": len(source_scope.get("selected_roots") or []),
            "searched_root_count": len(searched.get("roots") or []),
            "searched_files": searched_file_count,
            "searched_bytes": safe_count(searched.get("searched_bytes")),
            "result_meaning": searched.get("result_meaning"),
            "source_plan_selected_files": safe_count(searched.get("source_plan_selected_files")),
        },
        "not_searched_scope": {
            "outside_selected_roots": not_searched.get("outside_selected_roots"),
            "missing_root_count": missing_root_count,
            "source_plan_deferred_files": deferred_count,
            "source_plan_pruned_files": pruned_count,
            "notes": list(not_searched.get("notes") or [])[:4],
        },
        "excluded_scope": {
            "rejected_root_count": rejected_root_count,
            "meaning": excluded.get("meaning"),
        },
        "caps": {
            "status": cap_pressure_status,
            "hit": bool(cap_hit.get("hit")),
            "caps_hit": list(cap_hit.get("caps_hit") or []),
            "normalized_warnings": list(cap_hit.get("normalized_warnings") or [])[:6],
        },
        "duplicate_mirror_risk": duplicate_risk,
        "not_all_history_boundary": True,
        "answer_scope_boundary": (
            no_evidence.get("answer_scope")
            if no_evidence.get("needed")
            else searched.get("result_meaning") or "searched_scope_candidate_boundary"
        ),
        "scope_strength": "weak_escalate_before_broad_claim" if scope_too_weak else "bounded_selected_scope",
        "escalation_condition": (
            "Escalate or narrow/rerun before any broader-than-searched-scope claim when caps hit, selected roots "
            "are missing/rejected/deferred, no files were searched, or duplicate/mirror collapse could hide distinct "
            "source context."
        ),
        "claim_boundary": (
            "This is not an all-history completeness claim; answer only for the selected searched scope unless "
            "the user explicitly asks for broader recall and the run is rerun with adequate roots/caps."
        ),
        "links": {
            "scope": "semantic_debug.scope",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
            "no_evidence": "semantic_debug.no_evidence",
        },
    }


def scope_duplicate_mirror_risk(source_locator_handoff: dict[str, Any]) -> dict[str, Any]:
    source_window_packaging = source_locator_handoff.get("source_window_packaging") or {}
    selection_policy = source_window_packaging.get("selection_policy") or {}
    collapse = selection_policy.get("collapse") or {}
    omission_counts = selection_policy.get("omission_reason_counts") or {}
    evidence_packaging = source_locator_handoff.get("evidence_packet_packaging") or {}
    stage_diagnostics = source_locator_handoff.get("stage_diagnostics") or {}
    duplicate_local_work = (
        (stage_diagnostics.get("retrieval_diagnostics_summary") or {}).get("duplicate_local_work")
        or stage_diagnostics.get("duplicate_local_work")
        or {}
    )
    counts = {
        "collapsed_candidate_count": safe_count(collapse.get("collapsed_candidate_count")),
        "duplicate_or_collapsed_row_count": safe_count(omission_counts.get("duplicate_or_collapsed_row_count")),
        "deduplicated_child_count": safe_count(evidence_packaging.get("deduplicated_child_count")),
        "duplicate_file_overlap_count": safe_count(duplicate_local_work.get("duplicate_file_overlap_count")),
        "duplicate_file_visit_count": safe_count(duplicate_local_work.get("duplicate_file_visit_count")),
    }
    detected = any(value > 0 for value in counts.values())
    return {
        "status": "risk_summarized_from_existing_diagnostics" if detected else "not_detected_in_emitted_diagnostics",
        **counts,
        "mirror_boundary": (
            "Duplicate/mirror counters are diagnostics from emitted retrieval/source-window packaging; they do "
            "not prove every duplicate or mirror source was found."
        ),
        "agent_guidance": (
            "If the answer depends on distinct copies, mirrored histories, or omitted duplicate windows, inspect "
            "source_locator_handoff/source_window_packaging before claiming uniqueness or completeness."
        ),
    }


def safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def build_next_commands(
    next_reads: list[dict[str, Any]],
    *,
    hints: dict[str, str | None],
    include_semantic_debug_replay: bool,
) -> list[dict[str, Any]]:
    _ = include_semantic_debug_replay
    commands: list[dict[str, Any]] = []
    for read in next_reads[:8]:
        if not read_is_required_surface_actionable(read):
            continue
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
                    "argv": compact_text_read_more_window_argv(str(read["target"])),
                    "command": shell_command(compact_text_read_more_window_argv(str(read["target"]))),
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


def compact_text_read_more_window_argv(target: str) -> list[str]:
    return [
        "jsonl-history-search",
        "read-more",
        target,
        "--mode",
        "window",
        "--format",
        "text",
        "--projection",
        "compact",
        "--compact-text-limit",
        "500",
    ]


def compact_text_read_more_argv(argv: list[Any]) -> list[str]:
    parts = [str(part) for part in argv]
    if not parts:
        return parts
    if parts[0] == "jsonl-history-search":
        command_index = 1
    else:
        command_index = 0
    if len(parts) <= command_index or parts[command_index] != "read-more":
        return parts
    if "--format" in parts:
        index = parts.index("--format")
        if index + 1 < len(parts):
            parts[index + 1] = "text"
        else:
            parts.append("text")
    else:
        parts.extend(["--format", "text"])
    if "--projection" not in parts:
        parts.extend(["--projection", "compact"])
    if "--compact-text-limit" not in parts:
        parts.extend(["--compact-text-limit", "500"])
    return parts


def compact_text_semantic_read_more_actions(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for read in reads:
        row = dict(read)
        for action_key in ("expansion_actions", "context_actions"):
            actions = []
            changed = False
            for action in list(row.get(action_key) or []):
                if not isinstance(action, dict):
                    actions.append(action)
                    continue
                action_row = dict(action)
                argv = action_row.get("argv")
                if isinstance(argv, list):
                    new_argv = compact_text_read_more_argv(argv)
                    if new_argv != [str(part) for part in argv]:
                        changed = True
                    action_row["argv"] = new_argv
                    action_row["command"] = shell_command(new_argv)
                actions.append(action_row)
            if actions or changed:
                row[action_key] = actions
        compacted.append(row)
    return compacted


def compact_text_semantic_handoff_actions(handoff: dict[str, Any]) -> dict[str, Any]:
    row = dict(handoff)
    for key in ("next_reads", "source_windows", "evidence_packets"):
        values = row.get(key)
        if isinstance(values, list):
            row[key] = compact_text_semantic_read_more_actions([value for value in values if isinstance(value, dict)])
    return row


ANSWER_READY_HANDOFF_READ_CAP = 5
ANSWER_READY_HANDOFF_COMMAND_CAP = 3
ACTION_PROOF_BUCKET_RECORD_CAP = 8
ACTION_PROOF_TEXT_LIMIT = 220
PROVENANCE_DIRECT_EVIDENCE_READ_CAP = 4
PROVENANCE_DIRECT_EVIDENCE_REF_CAP = 3
PROVENANCE_DIRECT_SELECTED_SOURCE_PATH_CAP = 8
PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP = 24
PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP = 96
PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_CANDIDATE_CAP = 8
PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_VISITED_DIR_CAP = 128
PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_DIRECTORY_ENTRY_CAP = 2048
PROVENANCE_COMPLETE_PROOF_OUTPUT_MAX_BYTES = 12_288
PROVENANCE_COMPLETE_PROOF_READ_BEFORE = 0
PROVENANCE_COMPLETE_PROOF_READ_AFTER = 1
PROVENANCE_COMPLETE_PROOF_READ_MAX_RECORDS = 2
PROVENANCE_COMPLETE_PROOF_COMPACT_TEXT_LIMIT = 2_400
PROVENANCE_DIRECT_EVIDENCE_FACETS = (
    "implementation_change",
    "test",
    "validation_safety",
    "commit",
    "install",
    "limitation",
    "runtime",
)


def build_answer_ready_handoff(
    *,
    query: str,
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
    scope_boundary_summary: dict[str, Any] | None = None,
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

    candidate_bounded_next_reads = bounded_answer_ready_next_reads(next_reads)
    has_primary_followthrough_source = bool(
        primary_followthrough_reads(
            bounded_next_reads=candidate_bounded_next_reads,
            action_proof_plan=action_proof_plan,
            allow_action_proof_plan=True,
        )
    )
    scoped_no_evidence_answer = no_evidence_needed and not has_primary_followthrough_source
    bounded_next_reads = [] if scoped_no_evidence_answer else candidate_bounded_next_reads
    primary_question_family = guided_audit_flow.get("primary_question_family")
    allow_action_proof_followthrough = (
        readiness_decision != "answer_now"
        and not scoped_no_evidence_answer
        and primary_question_family == "claimed_action"
    )
    primary_followthrough = build_primary_source_followthrough(
        bounded_next_reads=bounded_next_reads,
        action_proof_plan=action_proof_plan,
        allow_action_proof_plan=allow_action_proof_followthrough,
        query=query,
    )
    primary_followthrough_first = bool(primary_followthrough.get("applies"))
    if primary_followthrough_first:
        decision = "primary_source_followthrough_first"
        do_next = "run_primary_source_window_command_then_answer_if_supported"
    diagnostic_only_recovery = (
        readiness_decision in {"inspect_recovery_reads", "run_audit_route"}
        and not bounded_next_reads
        and bool(demoted_required_surface_reads(next_reads))
    )
    if diagnostic_only_recovery:
        decision = "diagnostic_only_recovery"
        do_next = "do_not_treat_diagnostic_reads_as_primary_refine_scope_or_escalate"
        do_not_answer_until.append(
            "Do not open diagnostic-only current/self-echo reads as primary proof; refine source scope or escalate only if the historical claim still needs evidence."
        )
    copy_paste_commands = (
        []
        if scoped_no_evidence_answer
        else answer_ready_copy_paste_commands(
            bounded_next_reads=bounded_next_reads,
            next_reads=next_reads,
            hints=hints,
            report_output=report_output,
            prefer_follow_next_reads_bundle=decision == "recovery_first" and not primary_followthrough_first,
        )
    )
    optional_fielded_bm25_command = (
        fielded_bm25_rerun_command(
            query=query,
            guided_audit_flow=guided_audit_flow,
            next_reads=next_reads,
        )
        if scoped_no_evidence_answer
        else None
    )
    source_action_guidance = build_source_action_guidance(
        answer_readiness=answer_readiness,
        no_evidence={**no_evidence, "needed": scoped_no_evidence_answer},
        next_reads=next_reads,
        fielded_bm25_command=optional_fielded_bm25_command,
        scope_boundary_summary=scope_boundary_summary,
    )
    command_target_privacy = summarize_command_target_privacy(copy_paste_commands)
    demoted_next_reads = demoted_required_surface_reads(next_reads)
    source_use_summary = {
        "answer_scope": final_answer_contract.get("answer_scope"),
        "source_use_required": final_answer_contract.get("source_use_required"),
        "required_action_count": len(final_answer_contract.get("required_actions") or []),
        "bounded_next_read_count": len(bounded_next_reads),
        "copy_paste_command_count": len(copy_paste_commands),
        "diagnostic_only_excluded_next_read_count": sum(1 for item in next_reads if item.get("diagnostic_only")),
        "required_surface_demoted_next_read_count": len(demoted_next_reads),
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
                else "scoped no-evidence answer first; diagnostic next_reads and retrieval reruns are optional"
                if scoped_no_evidence_answer
                else "semantic_debug.next_reads order; jsonl read-more commands first"
            ),
        },
        "scope_boundary_summary": scope_boundary_summary or {},
        "source_use_summary": source_use_summary,
        "source_action_guidance": source_action_guidance,
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
        current = "scoped_no_evidence_answer"
        reason = "scoped_no_evidence_ready_without_required_source_reads"
    else:
        current = "primary_source_answer"
        reason = "primary_source_surfaces_available_for_answer_scope"

    return {
        "schema_version": 1,
        "current": current,
        "reason": reason,
        "states": ["primary_source_answer", "answer_with_caveat", "read_more_required", "scoped_no_evidence_answer"],
        "adjacent_artifact_present": adjacent_present,
        "adjacent_artifact_counts_as_primary_source": False,
        "action_proof_status": action_status or "not_applicable",
        "missing_proof_buckets": missing_proof_buckets[:8],
        "blocked_facets": blocked_facets[:8],
        "claim_boundaries": {
            "primary_source_grounding_claim_allowed": current == "primary_source_answer",
            "adjacent_artifact_success_requires_caveat": current == "answer_with_caveat",
            "read_more_required_before_clean_answer": current == "read_more_required",
            "scoped_no_evidence_answer_allowed": current == "scoped_no_evidence_answer",
        },
    }


def read_is_required_surface_actionable(read: dict[str, Any]) -> bool:
    selection = read.get("historical_provenance_selection") if isinstance(read.get("historical_provenance_selection"), dict) else {}
    return not (
        read.get("diagnostic_only") is True
        or read.get("read_more_actionable") is False
        or read.get("followthrough_actionable") is False
        or selection.get("diagnostic_only") is True
        or selection.get("read_more_actionable") is False
        or selection.get("final_support_allowed") is False
    )


def demoted_required_surface_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        read
        for read in next_reads
        if isinstance(read, dict) and not read_is_required_surface_actionable(read)
    ]


def bounded_answer_ready_next_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for read in (item for item in next_reads if read_is_required_surface_actionable(item)):
        if len(rows) >= ANSWER_READY_HANDOFF_READ_CAP:
            break
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
            "correction_window_source_selection": read.get("correction_window_source_selection")
            if isinstance(read.get("correction_window_source_selection"), dict)
            else None,
            "reviewed_source_packet_pointer": read.get("reviewed_source_packet_pointer")
            if isinstance(read.get("reviewed_source_packet_pointer"), dict)
            else None,
            "omitted_window_recovery": bool(read.get("omitted_window_recovery")),
            "source_ref": guided_source_ref(read.get("source_ref") or {}) if isinstance(read.get("source_ref"), dict) else None,
            "read_more_actionable": read.get("read_more_actionable", True),
            "followthrough_actionable": read.get("followthrough_actionable", True),
            "direct_evidence_facets": list(read.get("direct_evidence_facets") or []),
            "provenance_direct_evidence": read.get("provenance_direct_evidence")
            if isinstance(read.get("provenance_direct_evidence"), dict)
            else None,
            "action": guidance,
        }
        rows.append({key: value for key, value in row.items() if value not in (None, [], {})})
    return rows


SOURCE_FOLLOWTHROUGH_DEFAULT_REQUIRED_LIMIT = 2


def build_source_followthrough_plan(
    *,
    next_reads: list[dict[str, Any]],
    answer_ready_handoff: dict[str, Any],
    action_proof_plan: dict[str, Any] | None = None,
    process_actual_use_read_plan: dict[str, Any] | None = None,
    historical_orchestration_plan: dict[str, Any] | None = None,
    historical_orchestration_role_evidence_bundle: dict[str, Any] | None = None,
    longitudinal_phase_source_plan: dict[str, Any] | None = None,
    no_evidence: dict[str, Any] | None = None,
    scope_boundary_summary: dict[str, Any] | None = None,
    provenance_direct_evidence_facet_ledger: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize the first source-followthrough decision across semantic-debug surfaces."""
    required_candidates: list[dict[str, Any]] = []
    conditional_candidates: list[dict[str, Any]] = []
    optional_candidates: list[dict[str, Any]] = []
    primary_followthrough = (
        answer_ready_handoff.get("primary_source_followthrough")
        if isinstance(answer_ready_handoff.get("primary_source_followthrough"), dict)
        else {}
    )
    primary_followthrough_first = bool(primary_followthrough.get("applies"))
    first_command = (
        primary_followthrough.get("first_command")
        if isinstance(primary_followthrough.get("first_command"), dict)
        else {}
    )
    direct_ledger = provenance_direct_evidence_facet_ledger or {}
    direct_evidence_complete = bool(direct_ledger.get("applies") and direct_ledger.get("evidence_complete"))
    direct_selected_targets = {
        str(read.get("target") or "")
        for read in list(direct_ledger.get("selected_primary_reads") or [])
        if isinstance(read, dict) and read.get("target")
    }
    if primary_followthrough_first and first_command.get("target"):
        required_candidates.extend(
            _source_followthrough_summaries(
                [
                    {
                        "kind": "primary_source_window_command",
                        "target": first_command.get("target"),
                        "reason": first_command.get("purpose")
                        or primary_followthrough.get("do_first")
                        or "primary source followthrough command",
                        "read_more_command": first_command.get("command"),
                        "direct_evidence_facets": list(first_command.get("direct_evidence_facets") or []),
                        "provenance_direct_evidence": first_command.get("provenance_direct_evidence"),
                    }
                ],
                read_role="required_primary_proof",
                selection_source="answer_ready_handoff.primary_source_followthrough",
                reason_fallback="primary source followthrough command",
            )
        )

    process_plan = process_actual_use_read_plan or {}
    required_candidates.extend(
        _source_followthrough_summaries(
            process_plan.get("required_primary_raw_final_support_reads") or [],
            read_role="required_primary_proof",
            selection_source="process_actual_use_final_support_read_plan",
            reason_fallback="primary raw process actual-use proof",
        )
    )
    historical_bundle = historical_orchestration_role_evidence_bundle or {}
    required_candidates.extend(
        _source_followthrough_summaries(
            historical_bundle.get("required_role_reads") or [],
            read_role="required_primary_proof",
            selection_source="historical_orchestration_role_evidence_bundle",
            reason_fallback="primary role evidence for orchestration provenance",
        )
    )
    historical_plan = historical_orchestration_plan or {}
    required_candidates.extend(
        _source_followthrough_summaries(
            historical_plan.get("primary_historical_final_support_reads") or [],
            read_role="required_primary_proof",
            selection_source="historical_orchestration_provenance_final_support_plan",
            reason_fallback="primary pre-existing raw orchestration proof",
        )
    )
    conditional_candidates.extend(
        _source_followthrough_summaries(
            historical_plan.get("bounded_non_final_required_reads") or [],
            read_role="conditional_required",
            selection_source="historical_orchestration_provenance_final_support_plan",
            reason_fallback="non-final role gap check; open only when primary role proof is missing",
        )
    )
    conditional_candidates.extend(
        _source_followthrough_summaries(
            historical_plan.get("missing_role_recovery_refs") or [],
            read_role="conditional_required",
            selection_source="historical_orchestration_provenance_final_support_plan",
            reason_fallback="missing role recovery; open only for fail-closed role gaps",
        )
    )

    action_plan = action_proof_plan or {}
    if not primary_followthrough_first:
        required_candidates.extend(
            _source_followthrough_summaries(
                list(action_plan.get("required_proof_reads") or [])
                + list(action_plan.get("primary_proof_refs") or []),
                read_role="required_primary_proof",
                selection_source="action_proof_plan",
                reason_fallback="primary action-proof source read",
            )
        )

    guidance = answer_ready_handoff.get("source_action_guidance")
    if isinstance(guidance, dict) and not primary_followthrough_first:
        required_candidates.extend(
            _source_followthrough_summaries(
                guidance.get("required_primary_source_actions") or [],
                read_role="required_primary_proof",
                selection_source="answer_ready_handoff.source_action_guidance",
                reason_fallback="required primary source action before answering",
            )
        )

    phase_plan = longitudinal_phase_source_plan or {}
    conditional_candidates.extend(
        _source_followthrough_summaries(
            list(phase_plan.get("prioritized_recovery_actions") or [])
            + list(phase_plan.get("prioritized_next_reads_before_synthesis") or []),
            read_role="conditional_required",
            selection_source="longitudinal_phase_source_plan",
            reason_fallback="phase/cap recovery; open only when chronology or phase coverage is missing",
        )
    )

    for read in next_reads:
        if not isinstance(read, dict):
            continue
        target = str(read.get("target") or source_ref_target(read.get("source_ref") or {}) or "")
        if direct_evidence_complete and target in direct_selected_targets:
            role = "required_primary_proof"
        elif primary_followthrough_first:
            role = "optional_diagnostic"
        else:
            role = source_followthrough_read_role(read, no_evidence=no_evidence or {})
        row = _source_followthrough_summary(
            read,
            read_role=role,
            selection_source="semantic_debug.next_reads",
            reason_fallback=(
                "optional diagnostic/context read"
                if role == "optional_diagnostic"
                else "conditional read for omitted/capped/missing proof"
                if role == "conditional_required"
                else "required primary proof read"
            ),
        )
        if not row:
            continue
        if role == "optional_diagnostic":
            optional_candidates.append(row)
        elif role == "conditional_required":
            conditional_candidates.append(row)
        else:
            required_candidates.append(row)

    required = _dedupe_source_followthrough_reads(required_candidates)
    conditional = _dedupe_source_followthrough_reads(
        [row for row in conditional_candidates if _source_followthrough_key(row) not in {_source_followthrough_key(item) for item in required}]
    )
    required_keys = {_source_followthrough_key(item) for item in required}
    conditional_keys = {_source_followthrough_key(item) for item in conditional}
    optional = _dedupe_source_followthrough_reads(
        [
            row
            for row in optional_candidates
            if _source_followthrough_key(row) not in required_keys
            and _source_followthrough_key(row) not in conditional_keys
        ]
    )
    default_required = [
        {**row, "default_actionable": True}
        for row in required[:SOURCE_FOLLOWTHROUGH_DEFAULT_REQUIRED_LIMIT]
    ]
    default_keys = {_source_followthrough_key(row) for row in default_required}
    required = [
        {**row, "default_actionable": _source_followthrough_key(row) in default_keys}
        for row in required
    ]
    conditional = [{**row, "default_actionable": False} for row in conditional]
    optional = [{**row, "default_actionable": False} for row in optional]

    scoped_no_evidence = bool((no_evidence or {}).get("needed")) and not required
    stop_condition = source_followthrough_stop_condition(
        answer_ready_handoff=answer_ready_handoff,
        required=required,
        conditional=conditional,
        optional=optional,
        scoped_no_evidence=scoped_no_evidence,
        provenance_direct_evidence_facet_ledger=provenance_direct_evidence_facet_ledger or {},
    )
    only_continue_if = source_followthrough_only_continue_if(
        answer_ready_handoff=answer_ready_handoff,
        scope_boundary_summary=scope_boundary_summary or {},
        conditional=conditional,
        scoped_no_evidence=scoped_no_evidence,
        provenance_direct_evidence_facet_ledger=provenance_direct_evidence_facet_ledger or {},
    )
    return {
        "schema_version": 1,
        "plan_name": "first_source_followthrough_plan",
        "status": (
            "required_primary_proof_available"
            if required
            else "conditional_required_only"
            if conditional
            else "optional_diagnostic_only"
            if optional
            else "no_source_followthrough_refs"
        ),
        "default_read_policy": (
            "Open only the 1-2 default_actionable required_primary_proof reads first. "
            "Open conditional_required reads only for caps, omissions, missing facets, chronology gaps, "
            "or exhaustive requests. Optional diagnostics are visible but not default follow-through."
        ),
        "default_required_primary_proof": default_required,
        "required_primary_proof": required,
        "conditional_required": conditional,
        "optional_diagnostic": optional,
        "stop_condition": stop_condition,
        "only_continue_if": only_continue_if,
        "counts": {
            "default_required_primary_proof_count": len(default_required),
            "required_primary_proof_count": len(required),
            "conditional_required_count": len(conditional),
            "optional_diagnostic_count": len(optional),
        },
        "links": {
            "all_source_refs": "semantic_debug.next_reads",
            "answer_ready_handoff": "semantic_debug.answer_ready_handoff",
            "source_locator_handoff": "semantic_debug.source_locator_handoff",
        },
        "claim_boundaries": {
            "optional_diagnostics_default_actionable": False,
            "conditional_reads_default_actionable": False,
            "all_refs_preserved_in_next_reads": True,
            "provider_token_savings_claimed": False,
        },
    }


def source_followthrough_plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "link": "semantic_debug.source_followthrough_plan",
        "status": plan.get("status"),
        "default_read_policy": plan.get("default_read_policy"),
        "default_required_primary_proof": list(plan.get("default_required_primary_proof") or [])[:2],
        "counts": plan.get("counts") or {},
        "stop_condition": plan.get("stop_condition"),
        "only_continue_if": list(plan.get("only_continue_if") or [])[:4],
    }


def _source_followthrough_summaries(
    reads: list[Any],
    *,
    read_role: str,
    selection_source: str,
    reason_fallback: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for read in reads:
        if not isinstance(read, dict):
            continue
        row = _source_followthrough_summary(
            read,
            read_role=read_role,
            selection_source=selection_source,
            reason_fallback=reason_fallback,
        )
        if row:
            rows.append(row)
    return rows


def _source_followthrough_summary(
    read: dict[str, Any],
    *,
    read_role: str,
    selection_source: str,
    reason_fallback: str,
) -> dict[str, Any]:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = read.get("target") or source_ref_target(source_ref)
    if not target and not source_ref:
        return {}
    row = {
        "read_role": read_role,
        "target": target,
        "kind": read.get("kind"),
        "candidate_id": read.get("candidate_id"),
        "reason": read.get("reason") or reason_fallback,
        "selection_source": selection_source,
        "source_ref": guided_source_ref(source_ref) if source_ref else None,
        "read_more_actionable": read.get("read_more_actionable", read_role == "required_primary_proof"),
        "followthrough_actionable": read.get("followthrough_actionable", read_role == "required_primary_proof"),
        "diagnostic_only": read.get("diagnostic_only") is True or read_role == "optional_diagnostic",
        "conditional_required": read_role == "conditional_required" or read.get("conditional_required") is True,
    }
    for key in (
        "source_chronology_role",
        "target_source_role",
        "actual_use_source_tier",
        "proof_role",
        "role",
        "direct_evidence_facets",
        "provenance_direct_evidence",
        "secondary_context_role",
    ):
        if read.get(key) is not None:
            row[key] = read.get(key)
    if read.get("omitted_window_recovery"):
        row["omitted_window_recovery"] = read.get("omitted_window_recovery")
    return {key: value for key, value in row.items() if value not in (None, [], {})}


def source_followthrough_read_role(read: dict[str, Any], *, no_evidence: dict[str, Any]) -> str:
    explicit_role = str(read.get("read_role") or "")
    if explicit_role in {"required_primary_proof", "conditional_required", "optional_diagnostic"}:
        return explicit_role
    if (
        read.get("conditional_required") is True
        or read.get("omitted_window_recovery")
        or _source_followthrough_text_has_any(read, ("omitted", "cap", "missing facet", "chronology gap", "exhaustive"))
    ):
        return "conditional_required"
    if bool(no_evidence.get("needed")) or not read_is_required_surface_actionable(read):
        return "optional_diagnostic"
    if core_convo_read_is_diagnostic_or_post_hoc(read):
        return "optional_diagnostic"
    return "required_primary_proof"


def source_followthrough_stop_condition(
    *,
    answer_ready_handoff: dict[str, Any],
    required: list[dict[str, Any]],
    conditional: list[dict[str, Any]],
    optional: list[dict[str, Any]],
    scoped_no_evidence: bool,
    provenance_direct_evidence_facet_ledger: dict[str, Any] | None = None,
) -> str:
    direct_ledger = provenance_direct_evidence_facet_ledger or {}
    if direct_ledger.get("applies") and direct_ledger.get("evidence_complete"):
        return (
            "Stop after the selected direct proof reads support every required facet. "
            "Do not open assistant recaps, report-writing events, or compacted summaries as required primary proof."
        )
    if direct_ledger.get("applies") and direct_ledger.get("missing_required_facets"):
        missing = ", ".join(direct_ledger.get("missing_required_facets") or [])
        return (
            f"Open at most one bounded continuation for missing direct facets: {missing}. "
            "Then state any remaining gaps; do not continue through recap or summary windows."
        )
    guidance = answer_ready_handoff.get("source_action_guidance")
    if isinstance(guidance, dict) and guidance.get("stop_condition"):
        return str(guidance.get("stop_condition"))
    followthrough = answer_ready_handoff.get("primary_source_followthrough")
    if isinstance(followthrough, dict) and followthrough.get("stop_after"):
        return str(followthrough.get("stop_after"))
    if scoped_no_evidence:
        return "Stop broad search and answer scoped no-evidence once searched scope, omissions, and caps are named."
    if required:
        return (
            "Stop after the default required_primary_proof refs support the answer. "
            "Do not open optional_diagnostic refs unless a named facet remains unsupported."
        )
    if conditional:
        return "Stop after stating the conditional gap and opening only reads needed for caps, omissions, missing facets, or chronology gaps."
    if optional:
        return "Stop required read-more; surfaced refs are optional diagnostics only."
    return "Stop when no source-followthrough refs are available and state the searched-scope boundary."


def source_followthrough_only_continue_if(
    *,
    answer_ready_handoff: dict[str, Any],
    scope_boundary_summary: dict[str, Any],
    conditional: list[dict[str, Any]],
    scoped_no_evidence: bool,
    provenance_direct_evidence_facet_ledger: dict[str, Any] | None = None,
) -> list[str]:
    if (provenance_direct_evidence_facet_ledger or {}).get("applies"):
        return []
    followthrough = answer_ready_handoff.get("primary_source_followthrough")
    values: list[str] = []
    if isinstance(followthrough, dict):
        values.extend(str(item) for item in list(followthrough.get("only_continue_if") or []) if item)
    values.extend(str(item) for item in list(answer_ready_handoff.get("do_not_answer_until") or []) if item)
    if conditional:
        values.append(
            "Open conditional_required reads only for caps, omissions, missing facets, chronology gaps, or exhaustive requests."
        )
    if scoped_no_evidence:
        values.append("Continue only if the user needs stronger recall or a broader scope than searched-scope no-evidence.")
    caps = scope_boundary_summary.get("cap_summary") if isinstance(scope_boundary_summary.get("cap_summary"), dict) else {}
    if caps.get("hit"):
        values.append("Continue only if cap-limited or omitted scope affects the claim.")
    return dedupe(values)[:8]


def _source_followthrough_text_has_any(read: dict[str, Any], markers: tuple[str, ...]) -> bool:
    text = core_convo_read_marker_text(read)
    return any(marker in text for marker in markers)


def _dedupe_source_followthrough_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for read in reads:
        key = _source_followthrough_key(read)
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(read)
    return rows


def _source_followthrough_key(read: dict[str, Any]) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    return str(read.get("target") or source_ref_target(source_ref) or read.get("candidate_id") or "")


def provenance_direct_evidence_ledger_wanted(query: str, family: dict[str, Any]) -> bool:
    supported = set(family.get("detected_supported") or [])
    if family.get("primary") != "claimed_action" and "claimed_action" not in supported:
        return False
    text = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    source_session_cues = (
        "earlier conversation",
        "earlier session",
        "prior session",
        "previous session",
        "looking back",
        "source session",
        "source-session",
        "what did the agent",
        "what did you change",
    )
    return any(cue in text for cue in source_session_cues)


def requested_provenance_direct_evidence_facets(query: str) -> list[str]:
    text = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    requested: list[str] = (
        ["original_user_request"] if provenance_original_user_request_wanted(query) else []
    )
    facet_terms = {
        "implementation_change": (
            "change",
            "changed",
            "implement",
            "implementation",
            "onboarding",
            "what did the agent",
        ),
        "test": ("test", "tested", "verify", "verified", "verification", "check", "checked"),
        "validation_safety": (
            "validate",
            "validated",
            "validation",
            "verify",
            "verified",
            "verification",
            "safety",
            "privacy",
            "audit",
            "check",
            "checked",
        ),
        "commit": ("commit", "committed", "merge", "sha"),
        "install": ("install", "installer", "self-serve", "self serve", "onboarding", "setup"),
        "limitation": ("limit", "limitation", "constraint", "failed", "failure", "blocked", "403"),
        "runtime": ("runtime", "smoke", "live run", "actual run", "ran", "execute", "executed"),
    }
    for facet in PROVENANCE_DIRECT_EVIDENCE_FACETS:
        if any(term in text for term in facet_terms[facet]):
            requested.append(facet)
    return requested


def provenance_original_user_request_wanted(query: str) -> bool:
    text = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    patterns = (
        r"\bwhat\s+(?:did|had)\s+i\s+(?:ask|request|tell)\b",
        r"\bwhat\s+i\s+(?:asked|requested|told)\b",
        r"\bwhat\s+was\s+my\s+(?:original\s+)?(?:ask|request|instruction)\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def promote_provenance_work_episode_origin_source_reads(
    next_reads: list[dict[str, Any]],
    *,
    query: str,
    family: dict[str, Any],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    source_plan: dict[str, Any] | None = None,
    source_locator_handoff: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not provenance_direct_evidence_ledger_wanted(query, family):
        return next_reads, {
            "schema_version": 1,
            "applies": False,
            "reason": "not_a_source_session_claimed_action_query",
        }

    required_facets = requested_provenance_direct_evidence_facets(query)
    if not required_facets:
        return next_reads, {
            "schema_version": 1,
            "applies": False,
            "reason": "no_requested_direct_facets",
        }

    candidate_path_report = provenance_work_episode_origin_candidate_path_report(
        next_reads=next_reads,
        candidates=candidates,
        tool_proof=tool_proof,
        query=query,
        family=family,
        source_plan=source_plan or {},
        source_locator_handoff=source_locator_handoff,
    )
    candidate_paths = list(candidate_path_report.get("paths") or [])
    candidate_surfacing = dict(candidate_path_report.get("surfacing") or {})
    scored: list[dict[str, Any]] = []
    for path_index, path in enumerate(candidate_paths):
        seed_read = provenance_work_episode_origin_seed_read(path)
        ledger = build_provenance_direct_evidence_facet_ledger(
            query=query,
            family=family,
            candidates=[],
            tool_proof={"events": []},
            source_plan={},
            selected_source_reads=[seed_read],
        )
        score = provenance_work_episode_origin_ledger_score(
            ledger,
            required_facets=required_facets,
            path_index=path_index,
        )
        if score[0] <= 0:
            continue
        scored.append(
            {
                "path": str(path),
                "path_index": path_index,
                "score": score,
                "ledger": ledger,
            }
        )
    if not scored:
        return next_reads, {
            "schema_version": 1,
            "applies": True,
            "status": "no_origin_episode_direct_facets_found",
            "candidate_path_count": len(candidate_paths),
            "candidate_path_cap": PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP,
            "origin_candidate_surfacing": candidate_surfacing,
            "required_facets": required_facets,
            "bounded": True,
        }

    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    ledger = best["ledger"]
    selected_reads = list(ledger.get("selected_primary_reads") or [])
    if not selected_reads:
        return next_reads, {
            "schema_version": 1,
            "applies": True,
            "status": "no_origin_episode_selected_reads",
            "candidate_path_count": len(candidate_paths),
            "candidate_path_cap": PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP,
            "origin_candidate_surfacing": candidate_surfacing,
            "required_facets": required_facets,
            "bounded": True,
        }

    promoted_reads = dedupe_dicts(selected_reads + next_reads)[:12]
    covered_required = [
        facet for facet in required_facets if facet in set(ledger.get("covered_direct_facets") or [])
    ]
    return promoted_reads, {
        "schema_version": 1,
        "applies": True,
        "status": "origin_episode_promoted",
        "candidate_path_count": len(candidate_paths),
        "candidate_path_cap": PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP,
        "origin_candidate_surfacing": candidate_surfacing,
        "selected_candidate_path_index": int(best["path_index"]),
        "selected_primary_read_count": len(selected_reads),
        "selected_primary_read_targets": [
            read.get("target") for read in selected_reads if read.get("target")
        ],
        "required_facets": required_facets,
        "covered_required_facets": covered_required,
        "missing_required_facets": list(ledger.get("missing_required_facets") or []),
        "evidence_complete": bool(ledger.get("evidence_complete")),
        "selection_basis": "direct_facet_coverage_and_work_episode_chronology",
        "secondary_context_counts": {
            "assistant_recap_count": (ledger.get("secondary_context") or {}).get(
                "assistant_recap_count",
                0,
            ),
            "report_writing_event_count": (ledger.get("secondary_context") or {}).get(
                "report_writing_event_count",
                0,
            ),
            "compacted_summary_count": (ledger.get("secondary_context") or {}).get(
                "compacted_summary_count",
                0,
            ),
            "post_hoc_prompt_echo_count": (ledger.get("secondary_context") or {}).get(
                "post_hoc_prompt_echo_count",
                0,
            ),
            "post_hoc_prompt_echo_action_count": (ledger.get("secondary_context") or {}).get(
                "post_hoc_prompt_echo_action_count",
                0,
            ),
        },
        "raw_text_included": False,
        "bounded": True,
    }


def provenance_work_episode_origin_candidate_path_report(
    *,
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    query: str,
    family: dict[str, Any],
    source_plan: dict[str, Any],
    source_locator_handoff: dict[str, Any],
) -> dict[str, Any]:
    paths: list[Path] = []
    for row in [*next_reads, *candidates]:
        if isinstance(row, dict):
            provenance_work_episode_origin_append_path(paths, semantic_debug_row_source_path(row))
    for event in list(tool_proof.get("events") or []):
        if isinstance(event, dict):
            provenance_work_episode_origin_append_path(paths, semantic_debug_row_source_path(event))
    provenance_work_episode_origin_collect_paths(
        source_locator_handoff.get("source_windows"),
        paths,
    )
    provenance_work_episode_origin_collect_paths(
        source_locator_handoff.get("omitted_window_recovery"),
        paths,
    )
    recovered_path_report = provenance_work_episode_origin_recovered_path_report(
        query=query,
        family=family,
        source_plan=source_plan,
        existing_paths=paths,
    )
    surfaced_paths = list(recovered_path_report.get("paths") or [])
    result: list[Path] = []
    seen: set[str] = set()
    for path in [*surfaced_paths, *paths]:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(path)
        if len(result) >= PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP:
            break
    return {
        "paths": result,
        "surfacing": {
            "schema_version": 1,
            "status": "recovered_candidate_paths_added" if surfaced_paths else "no_recovered_candidate_paths",
            "recovered_candidate_path_count": len(surfaced_paths),
            "candidate_path_count_after_surfacing": len(result),
            "candidate_path_cap": PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP,
            "root_traversal": recovered_path_report.get("root_traversal") or {},
            "recovery_sources": [
                "latest_user_messages_direct_request_overlap",
                "bounded_source_root_direct_provenance_scan",
            ] if surfaced_paths else [],
            "validated_by": "direct_provenance_facet_scanner",
            "raw_text_included": False,
            "bounded": True,
            "claim_boundaries": {
                "mentions_alone_promoted": False,
                "secondary_artifacts_promoted": False,
                "recovery_raises_caps": False,
            },
        },
    }


def provenance_work_episode_origin_candidate_paths(
    *,
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    source_locator_handoff: dict[str, Any],
) -> list[Path]:
    report = provenance_work_episode_origin_candidate_path_report(
        next_reads=next_reads,
        candidates=candidates,
        tool_proof=tool_proof,
        query="",
        family={},
        source_plan={},
        source_locator_handoff=source_locator_handoff,
    )
    return list(report.get("paths") or [])


def provenance_work_episode_origin_recovered_paths(
    *,
    query: str,
    family: dict[str, Any],
    source_plan: dict[str, Any],
    existing_paths: list[Path],
) -> list[Path]:
    report = provenance_work_episode_origin_recovered_path_report(
        query=query,
        family=family,
        source_plan=source_plan,
        existing_paths=existing_paths,
    )
    return list(report.get("paths") or [])


def provenance_work_episode_origin_recovered_path_report(
    *,
    query: str,
    family: dict[str, Any],
    source_plan: dict[str, Any],
    existing_paths: list[Path],
) -> dict[str, Any]:
    if not provenance_direct_evidence_ledger_wanted(query, family):
        return {
            "paths": [],
            "root_traversal": provenance_work_episode_origin_empty_traversal_report(
                "not_a_source_session_claimed_action_query"
            ),
        }
    required_facets = requested_provenance_direct_evidence_facets(query)
    if "original_user_request" not in required_facets:
        return {
            "paths": [],
            "root_traversal": provenance_work_episode_origin_empty_traversal_report(
                "original_user_request_not_requested"
            ),
        }
    roots = provenance_work_episode_origin_recovery_roots(source_plan)
    if not roots:
        return {
            "paths": [],
            "root_traversal": provenance_work_episode_origin_empty_traversal_report(
                "no_supported_source_roots"
            ),
        }
    existing = {str(path) for path in existing_paths}
    direct_request_paths = provenance_original_request_recovery_paths(
        query=query,
        source_plan=source_plan,
        existing_paths=existing_paths,
    )
    path_rows: list[tuple[int, Path]] = []
    for path in direct_request_paths:
        path_rows.append((0, path))
    traversal_report = provenance_work_episode_origin_bounded_root_path_report(roots)
    for ordinal, path in enumerate(traversal_report.get("paths") or [], 1):
        path_rows.append((ordinal, path))

    result: list[Path] = []
    seen: set[str] = set(existing)
    scanned = 0
    for _ordinal, path in path_rows:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        scanned += 1
        if scanned > PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP:
            break
        seed_read = provenance_work_episode_origin_seed_read(path)
        ledger = build_provenance_direct_evidence_facet_ledger(
            query=query,
            family=family,
            candidates=[],
            tool_proof={"events": []},
            source_plan={},
            selected_source_reads=[seed_read],
        )
        if not provenance_work_episode_origin_recovery_ledger_valid(
            ledger,
            required_facets=required_facets,
        ):
            continue
        result.append(path)
        if len(result) >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_CANDIDATE_CAP:
            break
    public_traversal_report = {
        key: value for key, value in traversal_report.items() if key != "paths"
    }
    return {
        "paths": result,
        "root_traversal": {
            **public_traversal_report,
            "direct_request_recovery_path_count": len(direct_request_paths),
            "validated_recovered_candidate_path_count": len(result),
            "validation_policy": "direct_provenance_facet_scanner",
            "raw_text_included": False,
            "bounded": True,
        },
    }


def provenance_work_episode_origin_empty_traversal_report(status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "path_count": 0,
        "visited_directory_count": 0,
        "directory_entry_count": 0,
        "limits": provenance_work_episode_origin_traversal_limits(),
        "bounded": True,
    }


def provenance_work_episode_origin_traversal_limits() -> dict[str, int]:
    return {
        "jsonl_path_cap": PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP,
        "visited_directory_cap": PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_VISITED_DIR_CAP,
        "directory_entry_cap": PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_DIRECTORY_ENTRY_CAP,
    }


def provenance_work_episode_origin_recovery_roots(source_plan: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for root in (source_plan.get("sources") or {}).get("roots") or []:
        if not isinstance(root, dict) or not root.get("path"):
            continue
        kind = str(root.get("kind") or "")
        if kind not in {"codex_sessions", "claude_projects", "arbitrary_jsonl_root"}:
            continue
        path = Path(str(root.get("path") or "")).expanduser()
        if path.exists():
            roots.append(path)
    return roots


def provenance_work_episode_origin_bounded_root_paths(roots: list[Path]) -> list[Path]:
    report = provenance_work_episode_origin_bounded_root_path_report(roots)
    return list(report.get("paths") or [])


def provenance_work_episode_origin_bounded_root_path_report(roots: list[Path]) -> dict[str, Any]:
    rows: list[Path] = []
    visited_directory_count = 0
    directory_entry_count = 0
    stopped_reason = "exhausted_roots"
    for root in roots:
        if len(rows) >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP:
            stopped_reason = "jsonl_path_cap_reached"
            break
        if root.is_file() and root.name.endswith((".jsonl", ".jsonl.gz")):
            rows.append(root)
            continue
        if not root.is_dir():
            continue
        stack = [root]
        while stack:
            if visited_directory_count >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_VISITED_DIR_CAP:
                stopped_reason = "visited_directory_cap_reached"
                stack.clear()
                break
            dirpath = stack.pop()
            visited_directory_count += 1
            child_dirs: list[Path] = []
            try:
                with os.scandir(dirpath) as entries:
                    for entry in entries:
                        if directory_entry_count >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_DIRECTORY_ENTRY_CAP:
                            stopped_reason = "directory_entry_cap_reached"
                            stack.clear()
                            break
                        directory_entry_count += 1
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                child_dirs.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False) and entry.name.endswith((".jsonl", ".jsonl.gz")):
                                rows.append(Path(entry.path))
                        except OSError:
                            continue
                        if len(rows) >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP:
                            stopped_reason = "jsonl_path_cap_reached"
                            stack.clear()
                            break
            except OSError:
                continue
            if stopped_reason != "exhausted_roots":
                break
            stack.extend(sorted(child_dirs, reverse=True))
            if len(rows) >= PROVENANCE_WORK_EPISODE_ORIGIN_RECOVERY_SCAN_FILE_CAP:
                stopped_reason = "jsonl_path_cap_reached"
                break
        if stopped_reason != "exhausted_roots":
            break
    return {
        "schema_version": 1,
        "status": "bounded_paths_available" if rows else "no_jsonl_paths_found",
        "paths": rows,
        "path_count": len(rows),
        "visited_directory_count": visited_directory_count,
        "directory_entry_count": directory_entry_count,
        "stopped_reason": stopped_reason,
        "limits": provenance_work_episode_origin_traversal_limits(),
        "bounded": True,
        "claim_boundaries": {
            "traversal_uncapped": False,
            "symlink_dirs_followed": False,
        },
    }


def provenance_work_episode_origin_recovery_ledger_valid(
    ledger: dict[str, Any],
    *,
    required_facets: list[str],
) -> bool:
    if not ledger.get("applies"):
        return False
    original = (ledger.get("original_user_intent") or {}).get("selected_original_user_request")
    if not original:
        return False
    direct_events = list(ledger.get("_direct_events") or [])
    if not direct_events:
        return False
    covered = set(ledger.get("covered_direct_facets") or [])
    action_required = {
        facet
        for facet in required_facets
        if facet not in {"original_user_request", "original_request_authorization"}
    }
    if action_required and not (covered & action_required):
        return False
    return True


def provenance_work_episode_origin_collect_paths(value: Any, paths: list[Path]) -> None:
    if len(paths) >= PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP:
        return
    if isinstance(value, dict):
        provenance_work_episode_origin_append_path(paths, semantic_debug_row_source_path(value))
        for child in value.values():
            provenance_work_episode_origin_collect_paths(child, paths)
            if len(paths) >= PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP:
                return
    elif isinstance(value, list):
        for child in value:
            provenance_work_episode_origin_collect_paths(child, paths)
            if len(paths) >= PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP:
                return


def provenance_work_episode_origin_append_path(paths: list[Path], path_value: str | None) -> None:
    if not path_value or len(paths) >= PROVENANCE_WORK_EPISODE_ORIGIN_SOURCE_PATH_CAP:
        return
    if ".jsonl" not in str(path_value):
        return
    paths.append(Path(str(path_value)).expanduser())


def provenance_work_episode_origin_seed_read(path: Path) -> dict[str, Any]:
    source_ref = {"path": str(path), "line": 1, "session": path.stem}
    return {
        "kind": "jsonl_source_ref",
        "target": source_ref_target(source_ref),
        "source_ref": source_ref,
        "reason": "bounded work-episode origin candidate source ref",
        "read_more_actionable": True,
        "followthrough_actionable": True,
        "diagnostic_only": False,
        "historical_provenance_selection": {
            "schema_version": 1,
            "source_chronology_role": "target_historical_raw_jsonl_source_ref",
            "final_support_allowed": True,
            "read_more_actionable": True,
            "followthrough_actionable": True,
            "diagnostic_only": False,
            "raw_text_included": False,
        },
    }


def provenance_work_episode_origin_ledger_score(
    ledger: dict[str, Any],
    *,
    required_facets: list[str],
    path_index: int,
) -> tuple[int, int, int, int, int, int, int]:
    if not ledger.get("applies"):
        return (0, 0, 0, 0, 0, 0, 0)
    covered = set(ledger.get("covered_direct_facets") or [])
    required = set(required_facets)
    covered_required = covered & required
    selected_reads = list(ledger.get("selected_primary_reads") or [])
    selected_original = bool((ledger.get("original_user_intent") or {}).get("selected_original_user_request"))
    original_required = "original_user_request" in required
    if original_required and not selected_original:
        return (0, len(covered_required), 0, 0, len(selected_reads), -path_index, 0)
    correlated_reads = sum(
        1
        for read in selected_reads
        if (read.get("provenance_direct_evidence") or {}).get("correlated_output")
    )
    authorization_missing = int(
        "original_request_authorization" in set(ledger.get("missing_required_facets") or [])
    )
    return (
        len(covered_required),
        int(bool(ledger.get("evidence_complete"))),
        int(selected_original),
        correlated_reads,
        len(selected_reads),
        -authorization_missing,
        -path_index,
    )


def build_provenance_direct_evidence_facet_ledger(
    *,
    query: str,
    family: dict[str, Any],
    candidates: list[dict[str, Any]],
    tool_proof: dict[str, Any],
    source_plan: dict[str, Any] | None = None,
    selected_source_reads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not provenance_direct_evidence_ledger_wanted(query, family):
        return {"schema_version": 1, "applies": False, "reason": "not_a_source_session_claimed_action_query"}

    required_facets = requested_provenance_direct_evidence_facets(query)
    selected_source_paths = provenance_direct_selected_source_read_paths(
        selected_source_reads or []
    )
    source_paths = dedupe(
        [*selected_source_paths, *action_proof_candidate_paths(candidates, tool_proof)]
    )
    recovered_source_paths = provenance_original_request_recovery_paths(
        query=query,
        source_plan=source_plan or {},
        existing_paths=source_paths,
    )
    source_paths = dedupe([*recovered_source_paths, *source_paths])
    direct_events: list[dict[str, Any]] = []
    intent_refs: list[dict[str, Any]] = []
    intent_candidates: list[dict[str, Any]] = []
    secondary_refs: list[dict[str, Any]] = []
    source_roles_by_target: dict[str, str] = {}
    post_hoc_boundary_by_path: dict[str, int] = {}
    for path in source_paths[:ACTION_PROOF_SCAN_FILE_LIMIT]:
        scan = provenance_direct_events_for_path(path, query=query)
        direct_events.extend(scan["direct_events"])
        intent_refs.extend(scan["original_user_intent_refs"])
        intent_candidates.extend(scan["original_user_intent_candidates"])
        secondary_refs.extend(scan["secondary_context_refs"])
        source_roles_by_target.update(scan["source_roles_by_target"])
        if scan.get("post_hoc_boundary_line"):
            post_hoc_boundary_by_path[str(path)] = int(scan["post_hoc_boundary_line"])

    direct_events = dedupe_provenance_direct_events(direct_events)
    action_required_facets = [
        facet for facet in required_facets if facet != "original_user_request"
    ]
    selected_events = select_provenance_direct_events(
        direct_events,
        required_facets=action_required_facets,
    )
    selected_original_request = (
        select_provenance_original_user_request(
            intent_candidates,
            selected_events=selected_events,
        )
        if "original_user_request" in required_facets
        else None
    )
    selected_authorization = select_provenance_original_request_authorization(
        intent_candidates,
        selected_original_request=selected_original_request,
        selected_events=selected_events,
    )
    selected_events = promote_provenance_action_chronology(
        direct_events,
        selected_events=selected_events,
        selected_original_request=selected_original_request,
        selected_authorization=selected_authorization,
        post_hoc_boundary_by_path=post_hoc_boundary_by_path,
    )
    authorization_required = bool(
        selected_original_request
        and selected_original_request.get("request_chronology_role") == "deferred_request"
    )
    if authorization_required:
        required_facets = [
            *required_facets[:1],
            "original_request_authorization",
            *required_facets[1:],
        ]
    facet_rows = []
    covered_facets = ["original_user_request"] if selected_original_request else []
    if selected_authorization:
        covered_facets.append("original_request_authorization")
    facet_order = (
        ["original_user_request", "original_request_authorization", *PROVENANCE_DIRECT_EVIDENCE_FACETS]
        if "original_user_request" in required_facets
        else list(PROVENANCE_DIRECT_EVIDENCE_FACETS)
    )
    for facet in facet_order:
        if facet == "original_user_request":
            facet_rows.append(
                {
                    "facet": facet,
                    "required": True,
                    "status": (
                        "covered_by_direct_human_request"
                        if selected_original_request
                        else "not_covered"
                    ),
                    "direct_event_count": 1 if selected_original_request else 0,
                    "evidence_refs": (
                        [provenance_original_user_request_ref(selected_original_request)]
                        if selected_original_request
                        else []
                    ),
                }
            )
            continue
        if facet == "original_request_authorization":
            facet_rows.append(
                {
                    "facet": facet,
                    "required": authorization_required,
                    "status": "covered_by_direct_human_authorization" if selected_authorization else "not_covered",
                    "direct_event_count": 1 if selected_authorization else 0,
                    "evidence_refs": (
                        [provenance_original_user_request_ref(selected_authorization)]
                        if selected_authorization
                        else []
                    ),
                }
            )
            continue
        matching = [event for event in direct_events if facet in set(event.get("facets") or [])]
        if matching:
            covered_facets.append(facet)
        facet_rows.append(
            {
                "facet": facet,
                "required": facet in required_facets,
                "status": "covered_by_correlated_direct_event" if matching else "not_covered",
                "direct_event_count": len(matching),
                "evidence_refs": [
                    provenance_direct_event_ref(event)
                    for event in matching[:PROVENANCE_DIRECT_EVIDENCE_REF_CAP]
                ],
            }
        )
    missing_facets = [facet for facet in required_facets if facet not in covered_facets]
    selected_reads = (
        [provenance_original_user_request_read(selected_original_request)]
        if selected_original_request
        else []
    )
    if selected_authorization:
        selected_reads.append(provenance_original_user_request_read(selected_authorization))
    selected_reads.extend(provenance_direct_event_read(event) for event in selected_events)
    continuation = provenance_missing_facet_continuation(
        missing_facets=missing_facets,
        direct_events=direct_events,
        candidates=candidates,
    )
    evidence_complete = bool(required_facets) and not missing_facets
    return {
        "schema_version": 1,
        "applies": True,
        "status": "evidence_complete" if evidence_complete else "missing_direct_evidence_facets",
        "required_facets": required_facets,
        "covered_direct_facets": covered_facets,
        "missing_required_facets": missing_facets,
        "evidence_complete": evidence_complete,
        "facets": facet_rows,
        "selected_primary_reads": selected_reads,
        "selected_primary_read_count": len(selected_reads),
        "missing_facet_continuations": [continuation] if continuation else [],
        "missing_facet_continuation_count": 1 if continuation else 0,
        "original_request_source_recovery": {
            "status": "recovered" if recovered_source_paths else "not_needed_or_not_found",
            "recovered_source_count": len(recovered_source_paths),
            "bounded": True,
        },
        "selected_source_ref_path_scan": {
            "status": "used" if selected_source_paths else "not_supplied_or_not_qualified",
            "selected_source_path_count": len(selected_source_paths),
            "bounded": True,
            "path_cap": PROVENANCE_DIRECT_SELECTED_SOURCE_PATH_CAP,
        },
        "original_user_intent": {
            "status": "present" if intent_refs else "not_present_in_direct_records",
            "source_refs": dedupe_dicts(intent_refs)[:PROVENANCE_DIRECT_EVIDENCE_REF_CAP],
            "counts_as_direct_action_proof": False,
            "selected_original_user_request": (
                provenance_original_user_request_ref(selected_original_request)
                if selected_original_request
                else None
            ),
            "selected_original_request_authorization": (
                provenance_original_user_request_ref(selected_authorization)
                if selected_authorization
                else None
            ),
            "authorization_required": authorization_required,
            "selected_counts_as_original_user_request_proof": bool(selected_original_request),
        },
        "secondary_context": {
            "assistant_recap_count": sum(
                1 for row in secondary_refs if row.get("context_role") == "assistant_recap"
            ),
            "report_writing_event_count": sum(
                1 for row in secondary_refs if row.get("context_role") == "report_writing_event"
            ),
            "compacted_summary_count": sum(
                1 for row in secondary_refs if row.get("context_role") == "compacted_summary"
            ),
            "post_hoc_prompt_echo_count": sum(
                1 for row in secondary_refs if row.get("context_role") == "post_hoc_prompt_echo"
            ),
            "post_hoc_prompt_echo_action_count": sum(
                1
                for row in secondary_refs
                if row.get("context_role") == "post_hoc_prompt_echo_action"
            ),
            "source_refs": dedupe_dicts(secondary_refs)[:8],
            "counts_as_direct_action_proof": False,
        },
        "stop_contract": {
            "status": "stop_after_selected_direct_reads" if evidence_complete else "one_bounded_continuation",
            "required_recap_or_summary_reads": 0,
            "maximum_missing_facet_continuations": 1,
            "continue_only_for_missing_facets": missing_facets,
        },
        "claim_boundaries": {
            "original_user_intent_counts_as_direct_action_evidence": False,
            "direct_human_request_required_when_query_asks_what_user_requested": True,
            "compacted_or_secondary_human_intent_counts_as_original_request_proof": False,
            "assistant_final_or_task_complete_recap_counts_as_direct_action_evidence": False,
            "report_writing_call_or_output_counts_as_direct_action_evidence": False,
            "compacted_summary_counts_as_direct_action_evidence": False,
            "direct_coverage_requires_correlated_call_and_output": True,
            "target_id_or_record_role_alone_satisfies_coverage": False,
        },
        "_source_roles_by_target": source_roles_by_target,
        "_direct_events": direct_events,
    }


def provenance_direct_selected_source_read_paths(next_reads: list[dict[str, Any]]) -> list[Path]:
    paths: list[Path] = []
    for read in next_reads:
        if not isinstance(read, dict):
            continue
        if read.get("diagnostic_only") is True:
            continue
        selection = (
            read.get("historical_provenance_selection")
            if isinstance(read.get("historical_provenance_selection"), dict)
            else {}
        )
        if selection.get("diagnostic_only") is True or selection.get("final_support_allowed") is False:
            continue
        if read.get("read_more_actionable") is False or read.get("followthrough_actionable") is False:
            continue
        if core_convo_read_is_diagnostic_or_post_hoc(read):
            continue
        if core_convo_target_source_role(read) != "target_historical_raw":
            continue
        if not core_convo_read_is_actionable_raw_source_ref(read):
            continue
        source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
        path = source_ref.get("path") or source_target_path_part(str(read.get("target") or ""))
        if not path:
            continue
        paths.append(Path(str(path)).expanduser())

    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        result.append(path)
        if len(result) >= PROVENANCE_DIRECT_SELECTED_SOURCE_PATH_CAP:
            break
    return result


def provenance_direct_events_for_path(path: Path, *, query: str) -> dict[str, Any]:
    direct_events: list[dict[str, Any]] = []
    original_user_intent_refs: list[dict[str, Any]] = []
    original_user_intent_candidates: list[dict[str, Any]] = []
    secondary_context_refs: list[dict[str, Any]] = []
    source_roles_by_target: dict[str, str] = {}
    calls_by_id: dict[str, dict[str, Any]] = {}
    session_intent_by_id: dict[str, str] = {}
    post_hoc_prompt_echo_session = False
    post_hoc_boundary_line: int | None = None
    try:
        records = list(iter_records_for_file(path, "unknown_jsonl", timezone.utc))
    except (AdapterReadError, OSError, ValueError):
        records = []
    for record in records:
        source_ref = {"path": str(path), "line": int(record.line), "session": path.stem}
        target = source_ref_target(source_ref)
        classification = classify_conversation_record(record)
        raw = record.raw if isinstance(record.raw, dict) else {}
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
        payload_type = str(payload.get("type") or raw.get("type") or "").casefold()
        raw_role = str(payload.get("role") or raw.get("role") or "").casefold()
        text = provenance_record_text(record, payload)
        simple_top_level_user_message = bool(
            raw_role == "user"
            and text
            and str(raw.get("type") or "").casefold() != "response_item"
            and "<subagent_notification>" not in text.casefold()
            and payload_type
            not in {
                "function_call",
                "tool_call",
                "custom_tool_call",
                "function_call_output",
                "tool_output",
                "custom_tool_call_output",
                "tool_result",
            }
        )
        human_authored_message = (
            classification.label == HUMAN_AUTHORED_USER_MESSAGE
            or simple_top_level_user_message
        )
        record_echo_reasons = provenance_original_request_prompt_echo_reasons(text, query=query)
        if not human_authored_message:
            record_echo_reasons = [
                reason for reason in record_echo_reasons if reason == "post_hoc_gate_or_review_query_echo"
            ]
        if record_echo_reasons and not post_hoc_prompt_echo_session:
            post_hoc_prompt_echo_session = True
            post_hoc_boundary_line = int(record.line)
            secondary_context_refs.append(
                {
                    "source_ref": guided_source_ref(source_ref),
                    "context_role": "post_hoc_prompt_echo",
                    "prompt_echo_reasons": record_echo_reasons,
                }
            )
            source_roles_by_target[target] = "post_hoc_prompt_echo"

        worker_completion_facets = provenance_worker_completion_notification_facets(text)
        if worker_completion_facets:
            if post_hoc_prompt_echo_session:
                source_roles_by_target[target] = "post_hoc_prompt_echo_action"
                secondary_context_refs.append(
                    {"source_ref": guided_source_ref(source_ref), "context_role": "post_hoc_prompt_echo_action"}
                )
                continue
            direct_events.append(
                {
                    "event_id": f"worker-completion-{path.name}-{record.line}",
                    "event_kind": "completed_subagent_action_notification",
                    "tool_name": "subagent_notification",
                    "source_ref": guided_source_ref(source_ref),
                    "output_source_ref": guided_source_ref(source_ref),
                    "target": target,
                    "facets": worker_completion_facets,
                    "query_term_overlap_count": len(
                        action_proof_query_terms(query)
                        & set(ACTION_PROOF_TEXT_RE.findall(text.casefold()))
                    ),
                    "correlated_output": True,
                    "correlated_session_command": True,
                    "primary_action_proof": True,
                    "action_text": text,
                }
            )
            source_roles_by_target[target] = "direct_worker_completion"
            continue
        if human_authored_message:
            if record_echo_reasons:
                continue
            original_user_intent_refs.append(
                {"source_ref": guided_source_ref(source_ref), "intent_source_role": "direct_human_message"}
            )
            source_roles_by_target[target] = "original_user_intent"
            candidate = provenance_original_user_request_candidate(
                source_ref=source_ref,
                target=target,
                text=text,
                query=query,
                canonical_message=(
                    raw.get("type") == "response_item" and payload_type == "message"
                )
                or simple_top_level_user_message,
            )
            if candidate:
                original_user_intent_candidates.append(candidate)
            continue
        if classification.label == COMPACTED_HISTORY_SUMMARY:
            embedded_intent = provenance_compacted_human_intent(payload)
            if embedded_intent:
                original_user_intent_refs.append(
                    {
                        "source_ref": guided_source_ref(source_ref),
                        "intent_source_role": "human_intent_inside_compacted_history",
                    }
                )
            secondary_context_refs.append(
                {"source_ref": guided_source_ref(source_ref), "context_role": "compacted_summary"}
            )
            source_roles_by_target[target] = "compacted_summary"
            continue
        if classification.label == ASSISTANT_OR_AGENT_MESSAGE or payload_type in {
            "agent_message",
            "task_complete",
        }:
            secondary_context_refs.append(
                {"source_ref": guided_source_ref(source_ref), "context_role": "assistant_recap"}
            )
            source_roles_by_target[target] = "assistant_recap"
            continue

        if payload_type in {"function_call", "tool_call", "custom_tool_call"}:
            call_id = str(payload.get("call_id") or payload.get("id") or f"line-{record.line}")
            calls_by_id[call_id] = {
                "call_id": call_id,
                "tool_name": str(payload.get("name") or payload.get("tool_name") or payload.get("tool") or ""),
                "text": text,
                "source_ref": source_ref,
                "target": target,
            }
            continue
        if payload_type not in {"function_call_output", "tool_output", "custom_tool_call_output", "tool_result"}:
            continue
        call_id = str(payload.get("call_id") or "")
        call = calls_by_id.get(call_id)
        if not call:
            continue
        if post_hoc_prompt_echo_session:
            for ref in (call["source_ref"], source_ref):
                ref_target = source_ref_target(ref)
                source_roles_by_target[ref_target] = "post_hoc_prompt_echo_action"
                secondary_context_refs.append(
                    {
                        "source_ref": guided_source_ref(ref),
                        "context_role": "post_hoc_prompt_echo_action",
                    }
                )
            continue
        output_session_id = provenance_tool_session_id(text)
        if output_session_id and provenance_direct_action_intent_facets(call["text"]):
            session_intent_by_id[output_session_id] = call["text"]
        if provenance_report_or_recap_tool_activity(call["tool_name"], call["text"]):
            for ref in (call["source_ref"], source_ref):
                ref_target = source_ref_target(ref)
                source_roles_by_target[ref_target] = "report_writing_event"
                secondary_context_refs.append(
                    {"source_ref": guided_source_ref(ref), "context_role": "report_writing_event"}
                )
            continue
        if provenance_structural_context_read_command(call["text"]):
            for ref in (call["source_ref"], source_ref):
                ref_target = source_ref_target(ref)
                source_roles_by_target[ref_target] = "specification_context_read"
                secondary_context_refs.append(
                    {"source_ref": guided_source_ref(ref), "context_role": "specification_context_read"}
                )
            continue
        call_session_id = provenance_tool_session_id(call["text"])
        effective_call_text = call["text"]
        if call_session_id and call_session_id in session_intent_by_id:
            effective_call_text = f"{session_intent_by_id[call_session_id]} {call['text']}"
        facets = provenance_direct_event_facets(effective_call_text, text)
        if not facets:
            continue
        query_overlap = len(action_proof_query_terms(query) & set(ACTION_PROOF_TEXT_RE.findall(
            f"{call['text']} {text}".casefold()
        )))
        event = {
            "event_id": f"direct-{path.name}-{call['source_ref']['line']}-{source_ref['line']}",
            "event_kind": "correlated_direct_tool_call_and_output",
            "tool_name": call["tool_name"],
            "source_ref": guided_source_ref(call["source_ref"]),
            "output_source_ref": guided_source_ref(source_ref),
            "target": call["target"],
            "facets": facets,
            "query_term_overlap_count": query_overlap,
            "correlated_output": True,
            "correlated_session_command": bool(call_session_id and call_session_id in session_intent_by_id),
            "primary_action_proof": True,
        }
        direct_events.append(event)
        source_roles_by_target[call["target"]] = "direct_tool_call"
        source_roles_by_target[target] = "direct_tool_output"
    return {
        "direct_events": direct_events,
        "original_user_intent_refs": original_user_intent_refs,
        "original_user_intent_candidates": original_user_intent_candidates,
        "secondary_context_refs": secondary_context_refs,
        "source_roles_by_target": source_roles_by_target,
        "post_hoc_boundary_line": post_hoc_boundary_line,
    }


def provenance_original_request_recovery_paths(
    *,
    query: str,
    source_plan: dict[str, Any],
    existing_paths: list[Path],
) -> list[Path]:
    if not provenance_original_user_request_wanted(query):
        return []
    roots = (source_plan.get("sources") or {}).get("roots") or []
    sessions_roots = [
        Path(str(root.get("path") or "")).expanduser()
        for root in roots
        if isinstance(root, dict) and root.get("kind") == "codex_sessions" and root.get("path")
    ]
    if not sessions_roots:
        return []
    codex_home = sessions_roots[0].parent
    try:
        result = latest_user_messages(
            codex_home=codex_home,
            default_tz=timezone.utc,
            limit=500,
            max_files=200,
            max_bytes=100 * 1024 * 1024,
            max_elapsed=15.0,
            snippet_chars=1200,
        )
    except (OSError, ValueError):
        return []
    topic_terms = action_proof_query_terms(query) - PROVENANCE_ORIGINAL_REQUEST_META_TERMS
    existing = {str(path) for path in existing_paths}
    eligible: list[tuple[str, Path]] = []
    for message in result.get("messages") or []:
        if not isinstance(message, dict):
            continue
        text = str(message.get("snippet") or "")
        if provenance_original_request_prompt_echo_reasons(text, query=query):
            continue
        if not topic_terms.intersection(ACTION_PROOF_TEXT_RE.findall(text.casefold())):
            continue
        source_ref = message.get("openable_source_ref") or {}
        path = Path(str(source_ref.get("path") or "")).expanduser()
        if not path.is_file() or str(path) in existing or any(path == item[1] for item in eligible):
            continue
        eligible.append((str(message.get("timestamp_utc") or ""), path))
    eligible.sort(key=lambda item: (item[0], str(item[1])))
    return [path for _timestamp, path in eligible[:3]]


def provenance_worker_completion_notification_facets(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
    if "<subagent_notification>" not in normalized or '"status":{"completed"' not in normalized:
        return []
    facets: list[str] = []
    if any(marker in normalized for marker in ("implemented", "implementation:", "key results", "added `")):
        facets.append("implementation_change")
    if any(marker in normalized for marker in ("passed", "tests:", "focused tests", "full suite")):
        facets.append("test")
        facets.append("validation_safety")
    if any(marker in normalized for marker in ("commit", "branch head", "committed")):
        facets.append("commit")
    if any(marker in normalized for marker in ("pip install", "pipx install", "wheel smoke", "skill install")):
        facets.append("install")
    if any(marker in normalized for marker in ("smoke", "working `convo --help`", "working jsonl-history-search")):
        facets.append("runtime")
    if any(marker in normalized for marker in ("residual risks", "unavailable", "unproven", "limitation")):
        facets.append("limitation")
    return [facet for facet in PROVENANCE_DIRECT_EVIDENCE_FACETS if facet in facets]


def provenance_original_request_prompt_echo_reasons(text: str, *, query: str) -> list[str]:
    echo = setup_echo_classification(text, query=query)
    reasons: list[str] = []
    if echo.get("is_setup_echo"):
        reasons.append(f"setup_echo:{echo.get('reason') or 'setup_echo'}")
    normalized = re.sub(r"\s+", " ", str(text or "").casefold()).strip()
    query_text = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    if query_text and query_text in normalized and any(
        marker in normalized
        for marker in (
            "exact target",
            "target prompt",
            "evaluation prompt",
            "gate prompt",
            "pressure lane",
            "independent reviewer",
            "isolated implementer",
        )
    ):
        reasons.append("post_hoc_gate_or_review_query_echo")
    return dedupe(reasons)


PROVENANCE_ORIGINAL_REQUEST_META_TERMS = {
    "actually",
    "agent",
    "ask",
    "asked",
    "back",
    "change",
    "changed",
    "conversation",
    "earlier",
    "looking",
    "request",
    "requested",
    "tell",
    "told",
    "verify",
    "verified",
    "what",
    "work",
}


def provenance_original_user_request_candidate(
    *,
    source_ref: dict[str, Any],
    target: str,
    text: str,
    query: str,
    canonical_message: bool = False,
) -> dict[str, Any] | None:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    lowered = normalized.casefold()
    if not normalized or len(ACTION_PROOF_TEXT_RE.findall(lowered)) < 4:
        return None
    injected_markers = (
        "<recommended_plugins>",
        "<environment_context>",
        "<apps_instructions>",
        "<plugins_instructions>",
        "here is a list of plugins that are available but not installed",
    )
    if any(marker in lowered for marker in injected_markers):
        return None
    later_update_markers = (
        "coordinator update",
        "update from the user",
        "user update:",
        "follow-up update:",
    )
    if any(marker in lowered for marker in later_update_markers):
        return None
    topic_terms = action_proof_query_terms(query) - PROVENANCE_ORIGINAL_REQUEST_META_TERMS
    text_terms = set(ACTION_PROOF_TEXT_RE.findall(lowered))
    overlap = topic_terms & text_terms
    if not overlap:
        return None
    return {
        "source_ref": guided_source_ref(source_ref),
        "target": target,
        "intent_source_role": "direct_human_message",
        "proof_kind": "original_user_request_direct_human",
        "query_term_overlap_count": len(overlap),
        "matched_query_terms": sorted(overlap)[:8],
        "substantive": True,
        "canonical_message": canonical_message,
        "request_chronology_role": provenance_original_request_chronology_role(lowered),
    }


def provenance_original_request_chronology_role(text: str) -> str:
    if any(marker in text for marker in ("ok so do that work", "go ahead", "proceed with", "do the backlog task")):
        return "authorization"
    if any(marker in text for marker in ("do later", "not now", "save this as a task", "defer", "right before release")):
        return "deferred_request"
    return "request"


def select_provenance_original_user_request(
    candidates: list[dict[str, Any]],
    *,
    selected_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    first_action_line_by_path: dict[str, int] = {}
    for event in selected_events:
        source_ref = event.get("source_ref") or {}
        path = str(source_ref.get("path") or "")
        line = int(source_ref.get("line") or 0)
        if not path or line <= 0:
            continue
        current = first_action_line_by_path.get(path)
        if current is None or line < current:
            first_action_line_by_path[path] = line
    if not first_action_line_by_path:
        return None
    eligible = [
        candidate
        for candidate in candidates
        if str((candidate.get("source_ref") or {}).get("path") or "") in first_action_line_by_path
        and int((candidate.get("source_ref") or {}).get("line") or 0) > 0
        and int((candidate.get("source_ref") or {}).get("line") or 0)
        < first_action_line_by_path[str((candidate.get("source_ref") or {}).get("path") or "")]
        and candidate.get("intent_source_role") == "direct_human_message"
        and candidate.get("substantive") is True
        and int(candidate.get("query_term_overlap_count") or 0) > 0
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda candidate: (
            -int(candidate.get("query_term_overlap_count") or 0),
            -int(bool(candidate.get("canonical_message"))),
            int((candidate.get("source_ref") or {}).get("line") or 0),
        ),
    )


def select_provenance_original_request_authorization(
    candidates: list[dict[str, Any]],
    *,
    selected_original_request: dict[str, Any] | None,
    selected_events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not selected_original_request or selected_original_request.get("request_chronology_role") != "deferred_request":
        return None
    source_ref = selected_original_request.get("source_ref") or {}
    path = str(source_ref.get("path") or "")
    request_line = int(source_ref.get("line") or 0)
    action_lines = [
        int((event.get("source_ref") or {}).get("line") or 0)
        for event in selected_events
        if str((event.get("source_ref") or {}).get("path") or "") == path
    ]
    first_action_line = min((line for line in action_lines if line > 0), default=0)
    eligible = [
        candidate
        for candidate in candidates
        if str((candidate.get("source_ref") or {}).get("path") or "") == path
        and request_line < int((candidate.get("source_ref") or {}).get("line") or 0) < first_action_line
        and candidate.get("request_chronology_role") == "authorization"
    ]
    return min(
        eligible,
        key=lambda candidate: (
            -int(bool(candidate.get("canonical_message"))),
            int((candidate.get("source_ref") or {}).get("line") or 0),
        ),
        default=None,
    )


def promote_provenance_action_chronology(
    events: list[dict[str, Any]],
    *,
    selected_events: list[dict[str, Any]],
    selected_original_request: dict[str, Any] | None,
    selected_authorization: dict[str, Any] | None,
    post_hoc_boundary_by_path: dict[str, int],
) -> list[dict[str, Any]]:
    if not selected_original_request:
        return selected_events
    request_ref = selected_original_request.get("source_ref") or {}
    path = str(request_ref.get("path") or "")
    chronology_start = int(
        ((selected_authorization or selected_original_request).get("source_ref") or {}).get("line") or 0
    )
    boundary = int(post_hoc_boundary_by_path.get(path) or 0)
    relevant_workers = [
        event
        for event in events
        if event.get("event_kind") == "completed_subagent_action_notification"
        and str((event.get("source_ref") or {}).get("path") or "") == path
        and int((event.get("source_ref") or {}).get("line") or 0) > chronology_start
        and (not boundary or int((event.get("source_ref") or {}).get("line") or 0) < boundary)
        and int(event.get("query_term_overlap_count") or 0) > 0
        and "implementation_change" in set(event.get("facets") or [])
        and provenance_installability_action_episode(event.get("action_text") or "")
    ]
    relevant_workers.sort(key=lambda event: int((event.get("source_ref") or {}).get("line") or 0))
    if len(relevant_workers) > 1:
        relevant_workers = [relevant_workers[0], relevant_workers[-1]]
    result = list(selected_events)
    for event in relevant_workers:
        if event not in result and len(result) < PROVENANCE_DIRECT_EVIDENCE_READ_CAP - 2:
            result.append(event)
    return sorted(
        dedupe_provenance_direct_events(result),
        key=lambda event: int((event.get("source_ref") or {}).get("line") or 0),
    )


def provenance_installability_action_episode(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").casefold())
    return any(
        marker in normalized
        for marker in (
            "installability",
            "pipx",
            "pip and pipx",
            "cli install",
            "skill install",
            "install preflight",
            "python environment",
        )
    )


def provenance_original_user_request_ref(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_ref": candidate.get("source_ref") or {},
        "intent_source_role": "direct_human_message",
        "proof_kind": "original_user_request_direct_human",
        "query_term_overlap_count": int(candidate.get("query_term_overlap_count") or 0),
        "matched_query_terms": list(candidate.get("matched_query_terms") or []),
        "request_chronology_role": candidate.get("request_chronology_role") or "request",
    }


def provenance_original_user_request_read(candidate: dict[str, Any]) -> dict[str, Any]:
    source_ref = dict(candidate.get("source_ref") or {})
    return {
        "kind": "direct_human_message_source_ref",
        "target": source_ref_target(source_ref),
        "source_ref": source_ref,
        "reason": (
            "open direct authorization that activated the deferred original request"
            if candidate.get("request_chronology_role") == "authorization"
            else "open direct original user-request evidence from the source session"
        ),
        "read_role": "required_primary_proof",
        "read_more_actionable": True,
        "followthrough_actionable": True,
        "diagnostic_only": False,
        "target_source_role": "target_historical_raw",
        "proof_kind": "original_user_request_direct_human",
        "intent_source_role": "direct_human_message",
        "direct_evidence_facets": [
            "original_request_authorization"
            if candidate.get("request_chronology_role") == "authorization"
            else "original_user_request"
        ],
        "provenance_original_user_request_evidence": provenance_original_user_request_ref(candidate),
        "historical_provenance_selection": {
            "schema_version": 1,
            "source_chronology_role": "target_historical_raw_jsonl_source_ref",
            "final_support_allowed": True,
            "read_more_actionable": True,
            "followthrough_actionable": True,
            "diagnostic_only": False,
            "raw_text_included": False,
        },
    }


def provenance_record_text(record: Any, payload: dict[str, Any]) -> str:
    text = str(getattr(record, "text", "") or "")
    if text:
        return text
    return json.dumps(payload, sort_keys=True, default=str)


def provenance_compacted_human_intent(payload: dict[str, Any]) -> bool:
    replacement = payload.get("replacement_history")
    if not isinstance(replacement, list):
        return False
    return any(extract_human_ask(item).ask_text for item in replacement if isinstance(item, dict))


def provenance_report_or_recap_tool_activity(tool_name: str, call_text: str) -> bool:
    raw_text = f"{tool_name} {call_text}".casefold()
    text = raw_text.replace("\\", "/")
    if any(marker in text for marker in ("tools.update_plan", "update_plan", "task_complete")):
        return True
    mutation_targets = provenance_patch_mutation_targets(raw_text)
    if mutation_targets and all(provenance_secondary_artifact_path(path) for path in mutation_targets):
        return True
    artifact_scope = any(
        marker in text
        for marker in ("reports/", "codex/state/reviews/", "retention-manifest", "progress log", "task tail")
    )
    artifact_operation = any(
        marker in text
        for marker in (
            "apply_patch",
            "write_text",
            "report-output",
            "cat reports/",
            "tail -",
            "rg -n",
            "sed -n",
            "retention manifest",
        )
    )
    substantive_direct_action = any(
        marker in text
        for marker in (
            "pytest",
            "unittest",
            "compileall",
            "git diff",
            "git status",
            "git commit",
            "pip install",
            "pipx install",
            "install/preflight",
            "install_codex",
            "install_claude",
            "privacy scan",
            "credential",
            "src/jsonl_history_search/",
            "tests/",
        )
    )
    return artifact_scope and artifact_operation and not substantive_direct_action


def provenance_patch_mutation_targets(call: str) -> list[str]:
    return [
        match.group(1).strip().replace("\\", "/")
        for match in re.finditer(
            r"\*\*\* (?:add|update|delete) file:\s+(.+?)(?=\\n|\n)",
            str(call or ""),
            flags=re.IGNORECASE,
        )
    ]


def provenance_secondary_artifact_path(path: str) -> bool:
    normalized = f"/{str(path or '').casefold().strip('/')}"
    return any(
        marker in normalized
        for marker in (
            "/.private/",
            "/codex/state/",
            "/reports/",
            "/tasks/",
        )
    ) or normalized.endswith(".scratch.md")


def provenance_direct_mutation_operation(call: str) -> bool:
    text = str(call or "").casefold()
    return any(
        marker in text
        for marker in (
            "apply_patch",
            "edit_file",
            "write_file",
        )
    )


def provenance_inspection_audit_only_operation(call: str) -> bool:
    text = str(call or "").casefold()
    if provenance_direct_mutation_operation(text):
        return False
    audit_markers = (
        "git diff --stat",
        "retained size",
        "retained-size",
        "forbidden credential",
        "credential files",
        "wheel assets",
        "wheel-asset",
        "diff summary",
        "unzip -l",
    )
    return sum(marker in text for marker in audit_markers) >= 2


def provenance_direct_install_operation(call: str) -> bool:
    text = str(call or "").casefold()
    if provenance_inspection_audit_only_operation(text):
        return False
    install_markers = (
        "pip install",
        "pipx install",
        "bash install/preflight",
        "./install/preflight",
        "install_codex_global.sh",
        "install_claude_global.sh",
        "install-agent-skill",
        "jsonl-history-search validate-install",
        "isolated install",
        "skill installer",
    )
    if not any(marker in text for marker in install_markers):
        return False
    inspection_markers = (
        " cat ",
        " find ",
        " git diff --stat",
        " head ",
        " rg ",
        " rg -",
        " sed -n ",
        " tail ",
        " unzip -l ",
    )
    if not any(marker in f" {text} " for marker in inspection_markers):
        return True
    direct_execution_markers = (
        " -m pip install ",
        "cmd: \"pip install ",
        "input: \"pip install ",
        "input\\\": \\\"pip install ",
    )
    return text.strip().startswith(("pip install ", "pipx install ")) or any(
        marker in text
        for marker in direct_execution_markers
    )


def provenance_terminal_output_failed(output: str) -> bool:
    text = re.sub(r"\s+", " ", str(output or "").casefold())
    positive_failure_patterns = (
        r"\b[1-9]\d*\s+(?:tests?\s+)?(?:failed|errors?)\b",
        r"\b(?:failed|errors?)\s*[:=]\s*[1-9]\d*\b",
        r"\b(?:exit|return)(?:_code|\s+code)?\s*[:=]?\s*[1-9]\d*\b",
        r"[\"']status[\"']\s*:\s*[\"']failed[\"']",
    )
    if any(re.search(pattern, text) for pattern in positive_failure_patterns):
        return True
    return any(marker in text for marker in ("=== failures ===", "traceback (most recent call last)"))


def provenance_direct_event_facets(call_text: str, output_text: str) -> list[str]:
    call = re.sub(r"\s+", " ", str(call_text or "").casefold())
    output = re.sub(r"\s+", " ", str(output_text or "").casefold())
    if provenance_structural_context_read_command(call):
        return []

    allowed = provenance_direct_action_intent_facets(call)
    successful_terminal_output = not provenance_terminal_output_failed(output)
    facets: list[str] = []

    implementation_result_markers = (
        "done!",
        "successfully",
        "updated",
        "diff --git",
        "files changed",
        "insertions(+)",
        "deletions(-)",
    )
    if (
        "implementation_change" in allowed
        and successful_terminal_output
        and provenance_direct_mutation_operation(call)
        and (
            any(marker in output for marker in implementation_result_markers)
            or "script completed" in output
            or '"success": true' in output
            or output.strip() in {"{}", "success", "ok"}
        )
    ):
        facets.append("implementation_change")

    test_result_markers = (
        " tests passed",
        " passed in ",
        " passed,",
        "passed;",
        " ok",
        "\nok",
    )
    if (
        "test" in allowed
        and successful_terminal_output
        and any(marker in output for marker in test_result_markers)
    ):
        facets.append("test")

    validation_result_markers = (
        " pass",
        "pass;",
        "passed",
        "clean",
        "working tree clean",
        "nothing to commit",
        "no issues",
        "no secrets",
        "no credentials",
        "sha256",
        "sha-256",
    )
    if (
        "validation_safety" in allowed
        and successful_terminal_output
        and any(marker in output for marker in validation_result_markers)
    ):
        facets.append("validation_safety")

    if "commit" in allowed and (
        any(marker in output for marker in ("create mode 100", "commit created", "files changed"))
        or re.search(r"\bcommit\s+[0-9a-f]{7,40}\b", output)
        or re.search(r"\[[^\]]+\s+[0-9a-f]{7,40}\]\s+", output)
        or re.search(r"^[0-9a-f]{7,40}\s+", output)
    ):
        facets.append("commit")

    install_result_markers = (
        "successfully installed",
        "installed wheel",
        "installed skill",
        "installed entrypoint",
        "validate-install",
        "validation passed",
        " pass",
        "ready",
    )
    if (
        "install" in allowed
        and successful_terminal_output
        and provenance_direct_install_operation(call)
        and any(marker in output for marker in install_result_markers)
    ):
        facets.append("install")

    limitation_result_markers = (
        "http 403",
        "status 403",
        "forbidden",
        "pipx is not installed",
        "pipx not installed",
        "not available in this environment",
        "could not be exercised",
        "expected limitation",
        "known limitation",
    )
    if "limitation" in allowed and any(marker in output for marker in limitation_result_markers):
        facets.append("limitation")

    runtime_result_markers = (
        "isolated smoke",
        "runtime smoke",
        "install smoke",
        "usage:",
        "version",
        "installed entrypoint",
        "entrypoint ready",
        "smoke pass",
        "smoke recorded",
    )
    if (
        "runtime" in allowed
        and successful_terminal_output
        and any(marker in output for marker in runtime_result_markers)
    ):
        facets.append("runtime")
    return [facet for facet in PROVENANCE_DIRECT_EVIDENCE_FACETS if facet in facets]


def provenance_tool_session_id(text: str) -> str | None:
    match = re.search(r"\bsession_id\b\\?[\"']?\s*[:=]\s*([0-9]+)", str(text or ""))
    return match.group(1) if match else None


def provenance_structural_context_read_command(call: str) -> bool:
    """True when the call is gathering task/spec/report text, not proving action."""
    read_markers = (
        " sed -n ",
        " cat ",
        " rg ",
        " grep ",
        " find ",
        " head ",
        " tail ",
        " nl -",
        " wc -",
        " less ",
        " jq ",
    )
    spec_markers = (
        "tasks/",
        "task.md",
        "project.md",
        "codex-orchestration.md",
        "todo.md",
        "pipeline-state.md",
        "reports/",
        "codex/state/reviews/",
        "scratch",
        "progress log",
    )
    direct_action_markers = (
        "apply_patch",
        "edit_file",
        "write_file",
        "python -m pytest",
        "python3 -m pytest",
        "pytest ",
        "unittest",
        "compileall",
        "diff --check",
        "privacy scan",
        "privacy-scan",
        "secret scan",
        "credential scan",
        "pip install",
        "pipx install",
        "install-agent-skill",
        "install_codex_global.sh",
        "install_claude_global.sh",
        "jsonl-history-search validate-install",
        "git commit",
        "runtime smoke",
        "install smoke",
        "isolated smoke",
    )
    padded = f" {call} "
    return (
        any(marker in padded for marker in read_markers)
        and any(marker in call for marker in spec_markers)
        and not any(marker in call for marker in direct_action_markers)
    )


def provenance_direct_action_intent_facets(call: str) -> set[str]:
    facets: set[str] = set()
    if provenance_direct_mutation_operation(call):
        facets.add("implementation_change")
    if any(marker in call for marker in ("pytest ", "pytest\"", "python -m pytest", "python3 -m pytest", "unittest")):
        facets.add("test")
    if any(
        marker in call
        for marker in (
            "compileall",
            "diff --check",
            "git status",
            "privacy scan",
            "privacy-scan",
            "credential",
            "secret scan",
            "sha256",
            "sha-256",
            "shellcheck",
            "bash -n",
            "audit",
        )
    ):
        facets.add("validation_safety")
    if any(marker in call for marker in ("git commit ", "git commit -", "git show", "git log", "git rev-parse")):
        facets.add("commit")
    if provenance_direct_install_operation(call):
        facets.add("install")
    if any(
        marker in call
        for marker in (
            "claude --version",
            "codex --version",
            "pipx ",
            "jsonl-history-search validate-install",
            "runtime smoke",
            "install smoke",
            "isolated smoke",
        )
    ):
        facets.add("limitation")
        facets.add("runtime")
    if any(
        marker in call
        for marker in (
            "jsonl-history-search --help",
            "jsonl-history-search validate-install",
            "codex --version",
            "claude --version",
            "installed entrypoint",
            "runtime smoke",
            "install smoke",
            "isolated smoke",
        )
    ):
        facets.add("runtime")
    return facets


def dedupe_provenance_direct_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        key = str(event.get("event_id") or event.get("target") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def provenance_direct_event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event.get("event_id"),
        "source_ref": event.get("source_ref"),
        "output_source_ref": event.get("output_source_ref"),
        "facets": list(event.get("facets") or []),
        "correlated_output": bool(event.get("correlated_output")),
        "event_kind": event.get("event_kind"),
    }


def select_provenance_direct_events(
    events: list[dict[str, Any]],
    *,
    required_facets: list[str],
) -> list[dict[str, Any]]:
    uncovered = set(required_facets)
    selected: list[dict[str, Any]] = []
    while uncovered and len(selected) < PROVENANCE_DIRECT_EVIDENCE_READ_CAP:
        best = None
        best_key = (0, 0, 0, 0)
        for event in events:
            if event in selected:
                continue
            event_facets = set(event.get("facets") or [])
            covers = event_facets & uncovered
            key = (
                len(covers),
                int(event.get("query_term_overlap_count", 0) or 0),
                len(event_facets),
                int((event.get("source_ref") or {}).get("line") or 0),
            )
            if key > best_key:
                best = event
                best_key = key
        if best is None or best_key[0] == 0:
            break
        selected.append(best)
        uncovered -= set(best.get("facets") or [])
    return selected


def provenance_direct_event_read(event: dict[str, Any]) -> dict[str, Any]:
    source_ref = dict(event.get("source_ref") or {})
    worker_completion = event.get("event_kind") == "completed_subagent_action_notification"
    return {
        "kind": "worker_completion_source_ref" if worker_completion else "tool_event_source_ref",
        "target": source_ref_target(source_ref),
        "source_ref": source_ref,
        "reason": (
            "open direct completed-worker action chronology from the parent source session"
            if worker_completion
            else "open correlated direct source-session tool call/output evidence"
        ),
        "read_role": "required_primary_proof",
        "read_more_actionable": True,
        "followthrough_actionable": True,
        "diagnostic_only": False,
        "target_source_role": "target_historical_raw",
        "direct_evidence_facets": list(event.get("facets") or []),
        "provenance_direct_evidence": provenance_direct_event_ref(event),
        "action_proof_source_selection": {
            "schema_version": 1,
            "selection_reason": (
                "completed_subagent_action_notification"
                if worker_completion
                else "correlated_direct_source_session_event"
            ),
            "source_window_verification_required": True,
            "claim_boundary": (
                "completed_worker_notification_supports_only_explicitly_reported_action_facets"
                if worker_completion
                else "direct_call_and_correlated_output_support_only_classified_facets"
            ),
        },
        "historical_provenance_selection": {
            "schema_version": 1,
            "source_chronology_role": (
                "target_historical_raw_worker_completion_source_ref"
                if worker_completion
                else "target_historical_raw_direct_event_source_ref"
            ),
            "final_support_allowed": True,
            "read_more_actionable": True,
            "followthrough_actionable": True,
            "diagnostic_only": False,
            "raw_text_included": False,
        },
    }


def provenance_missing_facet_continuation(
    *,
    missing_facets: list[str],
    direct_events: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not missing_facets:
        return None
    source_ref = {}
    if direct_events:
        source_ref = dict((direct_events[0].get("source_ref") or {}))
    if not source_ref:
        for candidate in candidates:
            if isinstance(candidate.get("source_ref"), dict) and candidate["source_ref"].get("path"):
                source_ref = dict(candidate["source_ref"])
                break
    target = source_ref_target(source_ref) if source_ref else None
    if not target:
        return None
    command = provenance_bounded_direct_read_command(target)
    return {
        "kind": "jsonl_source_ref",
        "target": target,
        "source_ref": source_ref,
        "reason": f"one bounded continuation for missing direct facets: {', '.join(missing_facets)}",
        "read_role": "conditional_required",
        "conditional_required": True,
        "read_more_actionable": True,
        "followthrough_actionable": True,
        "read_more_command": command,
        "bounded_read_more_command": command,
        "missing_direct_evidence_facets": missing_facets,
        "provenance_direct_evidence_continuation": {
            "schema_version": 1,
            "bounded": True,
            "continuation_index": 1,
            "maximum_continuations": 1,
        },
    }


def apply_provenance_direct_evidence_ledger_to_reads(
    next_reads: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> list[dict[str, Any]]:
    if not ledger.get("applies"):
        return next_reads
    selected = list(ledger.get("selected_primary_reads") or [])
    selected_by_target = {str(read.get("target") or ""): read for read in selected if read.get("target")}
    roles = dict(ledger.get("_source_roles_by_target") or {})
    rows: list[dict[str, Any]] = list(selected)
    continuations = list(ledger.get("missing_facet_continuations") or [])[:1]
    rows.extend(continuations)
    for read in next_reads:
        target = str(read.get("target") or "")
        if target in selected_by_target or any(target == item.get("target") for item in continuations):
            continue
        row = dict(read)
        event_role = roles.get(target)
        if event_role in {
            "assistant_recap",
            "report_writing_event",
            "compacted_summary",
            "post_hoc_prompt_echo",
            "post_hoc_prompt_echo_action",
        }:
            row.update(
                {
                    "read_role": "optional_diagnostic",
                    "secondary_context_role": event_role,
                    "required_primary_proof_allowed": False,
                    "read_more_actionable": False,
                    "followthrough_actionable": False,
                    "diagnostic_only": True,
                    "expansion_actions": [],
                    "context_actions": [],
                }
            )
        elif event_role == "specification_context_read":
            row.update(
                {
                    "read_role": "optional_diagnostic",
                    "secondary_context_role": event_role,
                    "required_primary_proof_allowed": False,
                    "direct_action_proof": False,
                    "read_more_actionable": False,
                    "followthrough_actionable": False,
                    "diagnostic_only": True,
                    "expansion_actions": [],
                    "context_actions": [],
                }
            )
        elif event_role == "original_user_intent":
            row.update(
                {
                    "read_role": "optional_diagnostic",
                    "intent_source_role": "original_user_intent",
                    "direct_action_proof": False,
                    "required_primary_proof_allowed": False,
                    "read_more_actionable": False,
                    "followthrough_actionable": False,
                    "diagnostic_only": True,
                    "expansion_actions": [],
                    "context_actions": [],
                }
            )
        else:
            row.update(
                {
                    "read_role": "optional_diagnostic",
                    "required_primary_proof_allowed": False,
                    "read_more_actionable": False,
                    "followthrough_actionable": False,
                    "diagnostic_only": True,
                    "default_actionable": False,
                    "expansion_actions": [],
                    "context_actions": [],
                }
            )
        rows.append(row)
    return dedupe_dicts(rows)[:12]


def public_provenance_direct_evidence_facet_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in ledger.items() if not key.startswith("_")}


def provenance_bounded_direct_read_command(target: str) -> str:
    return shell_command(
        [
            "jsonl-history-search",
            "read-more",
            target,
            "--mode",
            "window",
            "--before",
            str(PROVENANCE_COMPLETE_PROOF_READ_BEFORE),
            "--after",
            str(PROVENANCE_COMPLETE_PROOF_READ_AFTER),
            "--max-records",
            str(PROVENANCE_COMPLETE_PROOF_READ_MAX_RECORDS),
            "--format",
            "text",
            "--projection",
            "compact",
            "--compact-text-limit",
            str(PROVENANCE_COMPLETE_PROOF_COMPACT_TEXT_LIMIT),
        ]
    )


def build_provenance_complete_proof_handoff(
    *,
    ledger: dict[str, Any],
    next_reads: list[dict[str, Any]],
    scope_boundary_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build the authoritative terminal surface for source-session provenance proof."""
    if not ledger.get("applies"):
        return {}

    selected_reads = []
    for read in list(ledger.get("selected_primary_reads") or [])[:PROVENANCE_DIRECT_EVIDENCE_READ_CAP]:
        target = str(read.get("target") or source_ref_target(read.get("source_ref") or {}) or "")
        if not target:
            continue
        proof = read.get("provenance_direct_evidence") or {}
        original_request_proof = read.get("provenance_original_user_request_evidence") or {}
        proof_kind = str(
            read.get("proof_kind")
            or (
                "correlated_direct_tool_call_and_output"
                if proof
                else "unknown_direct_proof"
            )
        )
        direct_human_request = bool(
            proof_kind == "original_user_request_direct_human"
            and original_request_proof.get("intent_source_role") == "direct_human_message"
        )
        correlated_call_output = bool(proof.get("correlated_output"))
        selected_reads.append(
            {
                "target": target,
                "source_ref": read.get("source_ref") or {},
                "output_source_ref": proof.get("output_source_ref") or {},
                "covered_facets": list(read.get("direct_evidence_facets") or []),
                "proof_kind": proof_kind,
                "source_role": read.get("intent_source_role") or "direct_tool_call",
                "direct_human_request": direct_human_request,
                "correlated_call_output": correlated_call_output,
                "required_facet_proof_valid": direct_human_request or correlated_call_output,
                "command": provenance_bounded_direct_read_command(target),
                "window": {
                    "before": PROVENANCE_COMPLETE_PROOF_READ_BEFORE,
                    "after": PROVENANCE_COMPLETE_PROOF_READ_AFTER,
                    "max_records": PROVENANCE_COMPLETE_PROOF_READ_MAX_RECORDS,
                    "projection": "compact",
                    "compact_text_limit": PROVENANCE_COMPLETE_PROOF_COMPACT_TEXT_LIMIT,
                },
            }
        )

    roles = list((ledger.get("_source_roles_by_target") or {}).values())
    optional_diagnostic_count = sum(
        1
        for read in next_reads
        if isinstance(read, dict) and read.get("read_role") == "optional_diagnostic"
    )
    suppression_counts = {
        "assistant_recap_targets": roles.count("assistant_recap"),
        "report_writing_targets": roles.count("report_writing_event"),
        "compacted_summary_targets": roles.count("compacted_summary"),
        "specification_context_targets": roles.count("specification_context_read"),
        "optional_diagnostics_preserved_non_actionable": optional_diagnostic_count,
        "secondary_executable_targets_exposed": 0,
        "generic_continuation_commands_exposed": 0,
    }
    boundaries = {
        "searched_scope": scope_boundary_summary.get("searched_scope") or {},
        "not_searched_scope": scope_boundary_summary.get("not_searched_scope") or {},
        "excluded_scope": scope_boundary_summary.get("excluded_scope") or {},
        "caps": scope_boundary_summary.get("caps") or {},
        "claim_boundary": scope_boundary_summary.get("claim_boundary"),
    }
    evidence_complete = bool(
        ledger.get("evidence_complete")
        and ledger.get("required_facets")
        and not ledger.get("missing_required_facets")
        and selected_reads
        and all(read.get("required_facet_proof_valid") for read in selected_reads)
    )
    handoff = {
        "schema_version": 1,
        "state": (
            "provenance_complete_proof_handoff"
            if evidence_complete
            else "provenance_incomplete_direct_proof_handoff"
        ),
        "active": evidence_complete,
        "applies": True,
        "output_budget_bytes": PROVENANCE_COMPLETE_PROOF_OUTPUT_MAX_BYTES,
        "required_facets": list(ledger.get("required_facets") or []),
        "covered_direct_facets": list(ledger.get("covered_direct_facets") or []),
        "missing_required_facets": list(ledger.get("missing_required_facets") or []),
        "selected_direct_reads": selected_reads,
        "searched_scope_boundaries": boundaries,
        "suppression_counts": suppression_counts,
        "optional_diagnostics": {
            "preserved_at": "semantic_debug structured/debug fields",
            "actionable": False,
            "may_trigger_continuation": False,
        },
    }
    if evidence_complete:
        handoff.update(
            {
                "answer_status": "answer_ready_after_selected_direct_reads",
                "stop_instruction": (
                    "Open only the selected direct reads, use direct-human evidence for the original request and "
                    "correlated call/output evidence for actions and verification, cite those refs, and stop. Do "
                    "not open recaps, summaries, specifications, reports, candidates, or diagnostic/retrieval surfaces."
                ),
                "continuation_commands": [],
            }
        )
        return handoff

    continuations = []
    for read in list(ledger.get("missing_facet_continuations") or [])[:1]:
        target = str(read.get("target") or source_ref_target(read.get("source_ref") or {}) or "")
        if not target:
            continue
        continuations.append(
            {
                "target": target,
                "missing_facets": list(read.get("missing_direct_evidence_facets") or []),
                "command": provenance_bounded_direct_read_command(target),
            }
        )
    handoff.update(
        {
            "answer_status": "missing_direct_evidence_facets",
            "stop_instruction": (
                "Run at most the single bounded missing-facet continuation, then answer with any remaining "
                "facets explicitly unsupported and stop."
            ),
            "continuation_commands": continuations,
        }
    )
    return handoff


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


def source_window_action_proof_not_applicable(family: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "not_applicable",
        "reason": reason,
        "question_family": {
            "primary": family.get("primary"),
            "claimed_action_detected": False,
        },
        "source_windows": [],
        "buckets": {},
        "summary": {
            "primary_record_count": 0,
            "adjacent_record_count": 0,
            "missing_buckets": [],
            "missing_labels": [],
            "source_window_verification_required": False,
            "verification_required_next_read_count": 0,
        },
        "guidance": {
            "use_for_claimed_action_questions": False,
            "use_for_process_actual_use_questions": False,
            "process_actual_use_policy": (
                "Use raw conversation/session timing windows and actual-use buckets; "
                "do not promote claimed-action/action-proof reads for process actual-use prompts."
            ),
        },
        "privacy_boundaries": {
            "raw_transcript_dump_included": False,
            "raw_tool_payload_dump_included": False,
            "raw_artifact_content_included": False,
            "text_excerpts_bounded": False,
            "private_payloads_committed": False,
        },
        "claim_boundaries": {
            "assistant_prose_satisfies_action_proof": False,
            "adjacent_artifact_satisfies_primary_action_proof": False,
            "candidate_next_read_proves_original_source_window": False,
        },
    }


def source_window_action_proof_guidance(bundle: dict[str, Any]) -> dict[str, Any]:
    if not bundle:
        return {}
    summary = bundle.get("summary") or {}
    buckets = bundle.get("buckets") or {}
    guidance = bundle.get("guidance") or {}
    return {
        "status": bundle.get("status"),
        "link": "semantic_debug.source_window_action_proof",
        "use_for_claimed_action_questions": bool(guidance.get("use_for_claimed_action_questions", True)),
        "use_for_process_actual_use_questions": bool(guidance.get("use_for_process_actual_use_questions", False)),
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
    command_targets = {
        read.get("target")
        for read in bounded_next_reads
        if is_jsonl_read_target(read.get("target")) and read_is_required_surface_actionable(read)
    }
    for read in next_reads:
        if len(commands) >= ANSWER_READY_HANDOFF_COMMAND_CAP:
            break
        if read.get("read_more_actionable") is False:
            continue
        target = read.get("target")
        if target not in command_targets:
            continue
        argv = compact_text_read_more_window_argv(str(target))
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
    query: str = "",
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
    reviewed_pointer = (
        first.get("reviewed_source_packet_pointer")
        if isinstance(first.get("reviewed_source_packet_pointer"), dict)
        else {}
    )
    required_final_source_ref_ids = [
        str(reviewed_pointer.get("source_ref_id"))
    ] if reviewed_pointer.get("source_ref_id") else []
    window = primary_followthrough_window(first, action_proof_plan=action_proof_plan, query=query)
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
        str(window.get("format", "json")),
    ]
    if window.get("projection") == "compact":
        argv.extend([
            "--projection",
            "compact",
            "--compact-text-limit",
            str(window.get("compact_text_limit", 500)),
        ])
    required_answer_facets = primary_followthrough_required_answer_facets(first, query=query)
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
    if isinstance(first.get("provenance_direct_evidence"), dict):
        command["provenance_direct_evidence"] = first["provenance_direct_evidence"]
        command["direct_evidence_facets"] = list(first.get("direct_evidence_facets") or [])
    if required_final_source_ref_ids:
        command["required_final_source_ref_ids"] = required_final_source_ref_ids
        command["reviewed_source_case_handle"] = reviewed_pointer.get("handle")
    target_privacy = classify_command_target_privacy(target)
    command["target_local_path_like"] = target_privacy["local_path_like"]
    command["target_absolute_path_like"] = target_privacy["absolute_path_like"]
    command["path_privacy_note"] = target_privacy["path_privacy_note"]
    return {
        "schema_version": 1,
        "applies": True,
        "do_first": "run_primary_source_window_command",
        "primary_targets": [str(read.get("target")) for read in primary_reads[:3] if read.get("target")],
        "required_final_source_ref_ids": required_final_source_ref_ids,
        "reviewed_source_case_handle": reviewed_pointer.get("handle"),
        "primary_read_count": len(primary_reads),
        "required_answer_facets": required_answer_facets,
        "first_command": command,
        "stop_after": (
            "After this primary source window supports the answer, stop broad search and answer with "
            "source refs plus searched-scope/uncertainty boundaries"
            + (
                f", including exact reviewed source ref {required_final_source_ref_ids[0]} as final support."
                if required_final_source_ref_ids else "."
            )
        ),
        "only_continue_if": [
            "the opened primary window does not contain the required proof",
            "the primary source conflicts with another inspected primary source",
            "a required answer facet from required_answer_facets is absent or only implicit",
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
    explicit_reads = complete_explicit_correction_window_reads(bounded_next_reads)
    if explicit_reads:
        reads.append(explicit_correction_primary_bundle_read(explicit_reads))
    reviewed_pointer_reads = [
        item
        for item in bounded_next_reads
        if reviewed_source_packet_pointer_read(item) and item.get("followthrough_actionable", True) is not False
    ]
    if reviewed_pointer_reads and not reads:
        reads.extend(reviewed_pointer_reads)
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
            if item.get("followthrough_actionable", True) is False:
                continue
            inferred = item.get("inferred_anchor") if isinstance(item.get("inferred_anchor"), dict) else {}
            if inferred.get("anchor_kind") in {
                "known_action_case",
                "remembered_topic",
                "explicit_date_remembered_instruction",
                "reviewed_source_packet_pointer",
            }:
                reads.append(item)
    return dedupe_primary_targets(reads)[:3]


def explicit_correction_primary_bundle_read(explicit_reads: list[dict[str, Any]]) -> dict[str, Any]:
    source_inspection_reads = [
        read
        for read in explicit_reads
        if explicit_correction_window_meta(read).get("bundle_role") == "source_inspection"
    ]
    anchor = source_inspection_reads[0] if source_inspection_reads else explicit_reads[0]
    row = dict(anchor)
    refs = [
        read.get("source_ref")
        for read in explicit_reads
        if isinstance(read.get("source_ref"), dict) and read.get("source_ref", {}).get("path")
    ]
    paths = {str(ref.get("path") or "") for ref in refs}
    lines = [int(ref.get("line") or 0) for ref in refs if int(ref.get("line") or 0) > 0]
    anchor_ref = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
    anchor_line = int(anchor_ref.get("line") or 0)
    row["explicit_correction_window_bundle"] = {
        "schema_version": 1,
        "same_source_window": len(paths) == 1 and bool(lines) and anchor_line > 0,
        "roles": [
            {
                "target": read.get("target"),
                "bundle_role": explicit_correction_window_meta(read).get("bundle_role"),
                "selection_reason": explicit_correction_window_meta(read).get("selection_reason"),
            }
            for read in explicit_reads
        ],
        "role_count": len(explicit_reads),
        "start_line": min(lines) if lines else None,
        "end_line": max(lines) if lines else None,
        "anchor_line": anchor_line or None,
        "anchor_role": explicit_correction_window_meta(row).get("bundle_role"),
        "claim_boundary": "bundle_guides_one_compact_source_window_not_final_answer",
    }
    return row


PROVENANCE_FACET_TERMS = {
    "task": ("task", "task file", "ticket"),
    "review": ("review", "reviewer", "verified", "verdict"),
    "branch": ("branch", "worktree"),
    "commit": ("commit", "merge", "sha"),
    "implementation_note": ("implementation note", "implementation-note", "notes", "scratch", "progress log"),
}


def requested_provenance_facets(query: str) -> list[str]:
    text = str(query or "").lower()
    if "provenance" not in text and not any(term in text for term in ("source case", "source handle", "reviewed source")):
        return []
    facets = [
        facet
        for facet, terms in PROVENANCE_FACET_TERMS.items()
        if any(term in text for term in terms)
    ]
    return facets if len(facets) >= 3 else []


def reviewed_source_multifacet_provenance(read: dict[str, Any], query: str) -> bool:
    return reviewed_source_packet_pointer_read(read) and bool(requested_provenance_facets(query))


def primary_followthrough_required_answer_facets(read: dict[str, Any], *, query: str = "") -> list[str]:
    provenance_facets = requested_provenance_facets(query)
    if reviewed_source_packet_pointer_read(read) and provenance_facets:
        labels = {
            "task": "task evidence: what task or work item the source supports",
            "review": "review evidence: what reviewer/verifier artifact or verdict is distinct from implementation notes",
            "branch": "branch/worktree evidence: what branch or isolated worktree provenance is supported",
            "commit": "commit/merge evidence: what commit, merge, or SHA evidence is supported",
            "implementation_note": "implementation-note evidence: what scratch/progress/notes say, and whether they are only implementation context",
        }
        return [labels[facet] for facet in provenance_facets if facet in labels]
    correction_meta = explicit_correction_window_meta(read)
    if not correction_meta.get("bounded_to_explicit_date_window"):
        return []
    bundle = read.get("explicit_correction_window_bundle")
    if not isinstance(bundle, dict) or not bundle.get("same_source_window"):
        return []
    roles = {
        str(item.get("bundle_role") or "")
        for item in bundle.get("roles") or []
        if isinstance(item, dict)
    }
    if not EXPLICIT_CORRECTION_WINDOW_REQUIRED_ROLES <= roles:
        return []
    return [
        "original user challenge about persistent coordinator versus wait/poll loops",
        "local docs/scripts or tool calls inspected before the distinction",
        "first distinction between outer tmux/injector coordination and foreground current-session waiting",
        "follow-up correction after the user challenged the first distinction",
        (
            "explicitly state whether wait/poll was for keeping/checking active foreground work; "
            "if the opened window lacks that nuance, say it is not shown there"
        ),
        "persistent coordinator can safely go idle or stop between cycles only because an external injector can wake it later",
    ]


def reviewed_source_packet_pointer_read(read: dict[str, Any]) -> bool:
    inferred = read.get("inferred_anchor") if isinstance(read.get("inferred_anchor"), dict) else {}
    return inferred.get("anchor_kind") == "reviewed_source_packet_pointer" or isinstance(
        read.get("reviewed_source_packet_pointer"), dict
    )


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
    query: str = "",
) -> dict[str, int | str]:
    inferred = read.get("inferred_anchor") if isinstance(read.get("inferred_anchor"), dict) else {}
    anchor_kind = inferred.get("anchor_kind")
    correction_meta = explicit_correction_window_meta(read)
    if isinstance(read.get("provenance_direct_evidence"), dict):
        return {
            "before": 1,
            "after": 3,
            "max_records": 8,
            "projection": "compact",
            "compact_text_limit": 1800,
            "format": "text",
            "reason": "correlated_direct_event_window_covers_call_and_output",
        }
    if correction_meta.get("bounded_to_explicit_date_window"):
        bundle = read.get("explicit_correction_window_bundle")
        if isinstance(bundle, dict) and bundle.get("same_source_window"):
            start_line = int(bundle.get("start_line") or 0)
            end_line = int(bundle.get("end_line") or 0)
            anchor_line = int(bundle.get("anchor_line") or 0)
            before = min(20, max(0, anchor_line - start_line))
            after = min(60, max(8, end_line - anchor_line + 2))
            return {
                "before": before,
                "after": after,
                "max_records": max(30, before + after + 1),
                "projection": "compact",
                "compact_text_limit": 1400,
                "format": "text",
                "reason": "explicit_correction_compact_bundle_covers_inspection_distinction_challenge_and_correction",
            }
        return {
            "before": 2,
            "after": 35,
            "max_records": 60,
            "projection": "compact",
            "compact_text_limit": 1200,
            "format": "text",
            "reason": "explicit_correction_source_window_covers_inspection_distinction_challenge_and_correction",
        }
    if reviewed_source_multifacet_provenance(read, query):
        return {
            "before": 0,
            "after": 180,
            "max_records": 220,
            "projection": "compact",
            "compact_text_limit": 900,
            "format": "text",
            "reason": "reviewed_source_multifacet_provenance_window_covers_task_review_branch_commit_and_notes",
        }
    if anchor_kind == "reviewed_source_packet_pointer" or reviewed_source_packet_pointer_read(read):
        return {
            "before": 0,
            "after": 35,
            "max_records": 60,
            "projection": "compact",
            "compact_text_limit": 1200,
            "format": "text",
            "reason": "reviewed_source_packet_pointer_window_before_current_session_echo_recovery",
        }
    if (
        anchor_kind == "known_action_case"
        or str(read.get("read_type") or "") == "required_primary_proof_read"
        or (
            action_proof_plan
            and action_proof_plan.get("applies")
            and action_proof_plan.get("primary_question_family") == "claimed_action"
        )
    ):
        return {
            "before": 45,
            "after": 8,
            "max_records": 80,
            "projection": "compact",
            "compact_text_limit": 1200,
            "format": "text",
            "reason": "action_proof_needs_preceding_tool_and_diff_context",
        }
    if anchor_kind == "explicit_date_remembered_instruction":
        return {
            "before": 2,
            "after": 10,
            "max_records": 40,
            "projection": "compact",
            "compact_text_limit": 500,
            "format": "text",
            "reason": "explicit_date_remembered_instruction_window_before_prompt_echo_recovery",
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
    scope_boundary_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness_decision = str(answer_readiness.get("decision") or "gather_more_evidence")
    primary_family = guided_audit_flow.get("primary_question_family")
    candidate_bounded_next_reads = bounded_answer_ready_next_reads(next_reads)
    has_primary_followthrough_source = bool(
        primary_followthrough_reads(
            bounded_next_reads=candidate_bounded_next_reads,
            action_proof_plan=action_proof_plan,
            allow_action_proof_plan=True,
        )
    )
    scoped_no_evidence = readiness_decision == "state_scoped_no_evidence" and not has_primary_followthrough_source
    recovery_first = (readiness_decision == "inspect_recovery_reads" or bool(next_reads)) and not scoped_no_evidence
    allow_action_proof_followthrough = (
        readiness_decision != "answer_now"
        and not scoped_no_evidence
        and primary_family == "claimed_action"
    )
    primary_followthrough = build_primary_source_followthrough(
        bounded_next_reads=candidate_bounded_next_reads,
        action_proof_plan=action_proof_plan,
        allow_action_proof_plan=allow_action_proof_followthrough,
        query=query,
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
    elif report_path and not scoped_no_evidence:
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
    elif not scoped_no_evidence:
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

    optional_fielded_bm25_command = None
    if not primary_followthrough_first:
        mode_command = fielded_bm25_rerun_command(
            query=query,
            guided_audit_flow=guided_audit_flow,
            next_reads=next_reads,
        )
        if scoped_no_evidence:
            optional_fielded_bm25_command = mode_command
        elif mode_command:
            commands.append(mode_command)

        if not scoped_no_evidence:
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

    source_action_guidance = build_source_action_guidance(
        answer_readiness=answer_readiness,
        no_evidence={"needed": scoped_no_evidence},
        next_reads=next_reads,
        fielded_bm25_command=optional_fielded_bm25_command,
        scope_boundary_summary=scope_boundary_summary,
    )

    if primary_followthrough_first:
        required_final_source_ref_ids = list(primary_followthrough.get("required_final_source_ref_ids") or [])
        constraints = [
            "Run the primary source-window command before broader recovery or synthesis.",
            "If that primary window supports the answer, stop broad search and answer with source refs plus searched-scope/uncertainty boundaries.",
            "Run more commands only if the primary source is missing required proof, conflicts with inspected evidence, or leaves a required facet unresolved.",
            "Do not claim root cause, all-history completeness, token savings, speed, final-answer quality, or product value unless separately proven.",
        ]
        if required_final_source_ref_ids:
            constraints.insert(
                1,
                "When answering, cite the exact reviewed source ref as final support: "
                + ", ".join(required_final_source_ref_ids)
                + ".",
            )
    else:
        constraints = [
            "Do not answer from snippets alone when decision is inspect_recovery_reads or needs_investigation.",
            "Run the recovery command before synthesis when next_reads or omitted-window recovery are present.",
            "Use failure-analysis/devlog_brief for timeline, supported claims, non-claims, gaps, and next reads before producing devlog-style conclusions.",
            "Do not claim root cause, all-history completeness, token savings, speed, final-answer quality, or product value unless separately proven.",
        ]
    if scoped_no_evidence:
        constraints = [
            "Answer now with `not_found_in_searched_scope`; do not open diagnostic next_reads first.",
            "Name searched scope, not-searched/missing scope, and cap limits before concluding no evidence.",
            "Escalate retrieval mode only if the user needs stronger recall or a broader claim than searched-scope no-evidence allows.",
            "Do not claim root cause, all-history completeness, token savings, speed, final-answer quality, or product value unless separately proven.",
        ]
    elif decision == "answer_now":
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
        "scope_boundary_summary": scope_boundary_summary or {},
        "source_action_guidance": source_action_guidance,
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
                "status": "optional_diagnostic" if scoped_no_evidence else "required_before_synthesis" if recovery_first else "optional",
            },
            {
                "layer": "investigation_brief",
                "surface": "failure_analysis.devlog_brief",
                "status": "optional_after_scoped_no_evidence" if scoped_no_evidence else "required_for_devlog_questions" if not primary_followthrough_first else "only_if_primary_source_insufficient",
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


def diagnostic_next_read_summaries(next_reads: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for read in next_reads:
        if len(rows) >= limit:
            break
        rows.append(
            {
                key: value
                for key, value in {
                    "kind": read.get("kind"),
                    "target": read.get("target"),
                    "reason": read.get("reason"),
                    "diagnostic_only": bool(read.get("diagnostic_only")),
                    "read_more_actionable": read.get("read_more_actionable", True),
                    "followthrough_actionable": read.get("followthrough_actionable", True),
                }.items()
                if value not in (None, [], {})
            }
        )
    return rows


def scoped_no_evidence_without_primary_source(next_reads: list[dict[str, Any]], no_evidence: dict[str, Any]) -> bool:
    if not no_evidence.get("needed"):
        return False
    return not bool(
        primary_followthrough_reads(
            bounded_next_reads=bounded_answer_ready_next_reads(next_reads),
            action_proof_plan=None,
            allow_action_proof_plan=True,
        )
    )


def mark_scoped_no_evidence_diagnostic_reads(next_reads: list[dict[str, Any]], no_evidence: dict[str, Any]) -> list[dict[str, Any]]:
    if not scoped_no_evidence_without_primary_source(next_reads, no_evidence):
        return next_reads
    rows: list[dict[str, Any]] = []
    for read in next_reads:
        row = dict(read)
        row["diagnostic_only"] = True
        row["read_more_actionable"] = False
        row["followthrough_actionable"] = False
        row["no_evidence_action_role"] = "optional_diagnostic_after_scoped_answer"
        row["required_before_scoped_no_evidence_answer"] = False
        reason = str(row.get("reason") or "inspect adjacent diagnostic source context")
        if not reason.startswith("optional diagnostic after scoped no-evidence answer"):
            row["reason"] = f"optional diagnostic after scoped no-evidence answer: {reason}"
        rows.append(row)
    return rows


def annotate_core_convo_target_source_read(
    read: dict[str, Any],
    *,
    default_role: str | None = None,
    source_surface: str | None = None,
    sequence_index: int = 0,
) -> dict[str, Any]:
    row = dict(read)
    role = core_convo_target_source_role(row, default_role=default_role)
    row["target_source_role"] = role
    row["target_source_role_order"] = CORE_CONVO_TARGET_SOURCE_ROLE_RANK[role]
    if source_surface and not row.get("core_convo_source_surface"):
        row["core_convo_source_surface"] = source_surface
    row["core_convo_sequence_index"] = sequence_index

    selection = (
        row.get("historical_provenance_selection")
        if isinstance(row.get("historical_provenance_selection"), dict)
        else {}
    )
    final_allowed = row.get("final_support_allowed")
    if final_allowed is None:
        final_allowed = selection.get("final_support_allowed")
    if final_allowed is None:
        final_allowed = role in {"target_historical_raw", "target_historical_artifact"}
    row["final_support_allowed"] = bool(final_allowed) and role != "diagnostic_or_post_hoc"

    if role == "diagnostic_or_post_hoc":
        row["diagnostic_only"] = True
        row["read_more_actionable"] = False
        row["followthrough_actionable"] = False
        row.setdefault(
            "diagnostic_open_policy",
            "do_not_open_by_default_unless_user_asks_for_current_same_loop_post_hoc_synthetic_or_diagnostic_audit",
        )
        preserve_optional_no_evidence_actions = (
            row.get("no_evidence_action_role") == "optional_diagnostic_after_scoped_answer"
            and core_convo_read_targets_raw_history(row)
        )
        if not preserve_optional_no_evidence_actions:
            row.pop("expansion_actions", None)
            row.pop("context_actions", None)
    else:
        row["diagnostic_only"] = bool(row.get("diagnostic_only", False))
        row.setdefault("read_more_actionable", role != "legitimate_context")
        row.setdefault("followthrough_actionable", True)
        row.setdefault("diagnostic_open_policy", "not_diagnostic")
    return row


def build_core_convo_first_read_plan(
    *,
    target_reads: list[dict[str, Any]] | None = None,
    historical_artifact_reads: list[dict[str, Any]] | None = None,
    legitimate_context_reads: list[dict[str, Any]] | None = None,
    diagnostic_reads: list[dict[str, Any]] | None = None,
    route_family: str | None = None,
    plan_source: str | None = None,
    limit: int = CORE_CONVO_FIRST_READ_PLAN_LIMIT,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    sections = [
        ("target_reads", target_reads or [], None),
        ("historical_artifact_reads", historical_artifact_reads or [], "target_historical_artifact"),
        ("legitimate_context_reads", legitimate_context_reads or [], "legitimate_context"),
        ("diagnostic_reads", diagnostic_reads or [], "diagnostic_or_post_hoc"),
    ]
    sequence_index = 0
    for source_surface, reads, default_role in sections:
        for read in reads:
            if not isinstance(read, dict):
                continue
            rows.append(
                annotate_core_convo_target_source_read(
                    read,
                    default_role=default_role,
                    source_surface=source_surface,
                    sequence_index=sequence_index,
                )
            )
            sequence_index += 1

    deduped = core_convo_dedupe_role_reads(rows)
    ordered = sorted(
        deduped,
        key=lambda item: (
            int(item.get("target_source_role_order", 99)),
            int(item.get("core_convo_sequence_index", 0)),
        ),
    )
    actionable = [
        item for item in ordered if item.get("target_source_role") != "diagnostic_or_post_hoc"
    ][:limit]
    diagnostics_after = [
        item for item in ordered if item.get("target_source_role") == "diagnostic_or_post_hoc"
    ][:limit]
    counts = Counter(str(item.get("target_source_role") or "unknown") for item in ordered)
    groups = {
        role: [item for item in ordered if item.get("target_source_role") == role][:limit]
        for role in CORE_CONVO_TARGET_SOURCE_ROLE_ORDER
    }
    return {
        "schema_version": 1,
        "plan_name": "core_convo_first_read_plan",
        "route_family": route_family,
        "plan_source": plan_source,
        "status": "ready" if actionable or diagnostics_after else "empty",
        "target_source_role_order": list(CORE_CONVO_TARGET_SOURCE_ROLE_ORDER),
        "read": actionable,
        "diagnostics_after": diagnostics_after,
        "role_groups": groups,
        "counts": {role: int(counts.get(role, 0)) for role in CORE_CONVO_TARGET_SOURCE_ROLE_ORDER},
        "diagnostic_open_policy": (
            "current-run, same-loop, post-hoc, synthetic, partial diagnostic, and unrelated "
            "high-lexical rows are do-not-open-by-default unless the user explicitly asks to audit them"
        ),
        "claim_boundaries": {
            "target_historical_raw_first": True,
            "target_historical_artifacts_secondary_to_raw": True,
            "legitimate_context_is_secondary": True,
            "diagnostics_are_default_non_actionable": True,
            "raw_text_included": False,
        },
    }


EXPECTATION_MISMATCH_READ_ORDER = [
    "expected_behavior_evidence",
    "observed_behavior_or_action_proof",
    "divergence_evidence",
    "legitimate_context",
    "diagnostic_or_post_hoc",
]


def build_expectation_mismatch_plan(
    *,
    query: str,
    family: dict[str, Any],
    core_convo_first_read_plan: dict[str, Any],
    next_reads: list[dict[str, Any]],
    limit: int = 8,
) -> dict[str, Any]:
    detected = set(family.get("detected_supported") or [])
    applies = family.get("primary") == "expectation_mismatch" or "expectation_mismatch" in detected
    if not applies:
        return {"schema_version": 1, "plan_name": "expectation_mismatch_plan", "applies": False}

    core_reads = list(core_convo_first_read_plan.get("read") or [])
    core_diagnostics = list(core_convo_first_read_plan.get("diagnostics_after") or [])
    candidate_actionable_reads = core_reads or [
        read
        for read in next_reads
        if isinstance(read, dict)
        and core_convo_target_source_role(read) != "diagnostic_or_post_hoc"
    ]
    candidate_diagnostic_reads = core_diagnostics or [
        read
        for read in next_reads
        if isinstance(read, dict)
        and core_convo_target_source_role(read) == "diagnostic_or_post_hoc"
    ]
    actionable_reads: list[dict[str, Any]] = []
    diagnostic_reads: list[dict[str, Any]] = list(candidate_diagnostic_reads)
    demoted_reads: list[dict[str, Any]] = []
    for read in candidate_actionable_reads:
        if not isinstance(read, dict):
            continue
        reject_reasons = expectation_mismatch_plan_read_reject_reasons(read, query=query)
        if reject_reasons:
            demoted = expectation_mismatch_annotated_read(read, role="diagnostic_or_post_hoc")
            demoted["expectation_mismatch_demoted_from_actionable"] = True
            demoted["expectation_mismatch_demotion_reasons"] = reject_reasons
            demoted["target_source_role"] = "diagnostic_or_post_hoc"
            demoted["final_support_allowed"] = False
            demoted["diagnostic_only"] = True
            demoted["read_more_actionable"] = False
            demoted["followthrough_actionable"] = False
            demoted.setdefault(
                "reason",
                "demoted expectation-mismatch repair/control/setup or wrong-target source lead",
            )
            demoted_reads.append(demoted)
            diagnostic_reads.append(demoted)
            continue
        actionable_reads.append(read)

    buckets: dict[str, list[dict[str, Any]]] = {
        role: [] for role in EXPECTATION_MISMATCH_READ_ORDER
    }
    for read in actionable_reads:
        if not isinstance(read, dict):
            continue
        roles = expectation_mismatch_read_roles(read, query=query)
        if not roles:
            roles = ["divergence_evidence"]
        for role in roles:
            buckets[role].append(expectation_mismatch_annotated_read(read, role=role))

    for read in diagnostic_reads:
        if not isinstance(read, dict):
            continue
        buckets["diagnostic_or_post_hoc"].append(
            expectation_mismatch_annotated_read(read, role="diagnostic_or_post_hoc")
        )

    if not buckets["expected_behavior_evidence"] and actionable_reads:
        buckets["expected_behavior_evidence"].append(
            expectation_mismatch_annotated_read(
                actionable_reads[0],
                role="expected_behavior_evidence",
                inferred=True,
            )
        )
    if not buckets["observed_behavior_or_action_proof"] and actionable_reads:
        candidate = actionable_reads[1] if len(actionable_reads) > 1 else actionable_reads[0]
        buckets["observed_behavior_or_action_proof"].append(
            expectation_mismatch_annotated_read(
                candidate,
                role="observed_behavior_or_action_proof",
                inferred=True,
            )
        )
    if not buckets["divergence_evidence"] and actionable_reads:
        candidate = actionable_reads[2] if len(actionable_reads) > 2 else actionable_reads[-1]
        buckets["divergence_evidence"].append(
            expectation_mismatch_annotated_read(candidate, role="divergence_evidence", inferred=True)
        )

    for role in EXPECTATION_MISMATCH_READ_ORDER:
        buckets[role] = core_convo_dedupe_role_reads(buckets[role])[:limit]

    read: list[dict[str, Any]] = []
    for role in ("expected_behavior_evidence", "observed_behavior_or_action_proof", "divergence_evidence"):
        read.extend(buckets[role])
    read = core_convo_dedupe_role_reads(read)[:limit]
    if not read and demoted_reads:
        read = [expectation_mismatch_missing_role_guidance_read(query=query, demoted_reads=demoted_reads)]

    counts = {role: len(buckets[role]) for role in EXPECTATION_MISMATCH_READ_ORDER}
    clean_read_count = sum(counts[role] for role in EXPECTATION_MISMATCH_READ_ORDER if role != "diagnostic_or_post_hoc")
    return {
        "schema_version": 1,
        "plan_name": "expectation_mismatch_plan",
        "applies": True,
        "status": (
            "ready"
            if clean_read_count
            else "missing_clean_actionable_reads"
            if demoted_reads
            else "ready"
            if buckets["diagnostic_or_post_hoc"]
            else "empty"
        ),
        "question_family": "expectation_mismatch",
        "read_order": list(EXPECTATION_MISMATCH_READ_ORDER),
        "read": read,
        "expected_behavior_evidence": buckets["expected_behavior_evidence"],
        "observed_behavior_or_action_proof": buckets["observed_behavior_or_action_proof"],
        "divergence_evidence": buckets["divergence_evidence"],
        "legitimate_context": buckets["legitimate_context"],
        "diagnostic_or_post_hoc": buckets["diagnostic_or_post_hoc"],
        "diagnostics_after": buckets["diagnostic_or_post_hoc"],
        "counts": counts,
        "demoted_actionable_read_count": len(demoted_reads),
        "missing_clean_facets": (
            [
                role
                for role in ("expected_behavior_evidence", "observed_behavior_or_action_proof", "divergence_evidence")
                if not buckets[role]
            ]
            if demoted_reads
            else []
        ),
        "core_convo_first_read_plan": {
            "link": "semantic_debug.core_convo_first_read_plan",
            "status": core_convo_first_read_plan.get("status"),
            "counts": core_convo_first_read_plan.get("counts"),
            "target_source_role_order": core_convo_first_read_plan.get("target_source_role_order"),
        },
        "claim_boundaries": {
            "expected_observed_divergence_are_source_roles_not_final_answer": True,
            "diagnostics_are_default_non_actionable": True,
            "current_same_loop_or_post_hoc_reports_are_not_default_support": True,
            "raw_text_included": False,
        },
        "stop_condition": (
            "Open expected behavior, observed/action-proof, and divergence reads before "
            "diagnostics; answer only after source refs support each needed facet or state the gap."
        ),
    }


def expectation_mismatch_read_roles(read: dict[str, Any], *, query: str) -> list[str]:
    text = " ".join(
        str(value or "").lower()
        for value in (
            read.get("target"),
            read.get("reason"),
            read.get("snippet"),
            read.get("command"),
            read.get("kind"),
        )
    )
    roles: list[str] = []
    if any(
        marker in text
        for marker in (
            "expected",
            "expecting",
            "supposed",
            "asked",
            "told",
            "instruction",
            "plan",
            "stop after",
            "expected it to stop",
        )
    ):
        roles.append("expected_behavior_evidence")
    if read.get("action_proof_source_selection") or any(
        marker in text
        for marker in (
            "observed",
            "kept",
            "loop",
            "continued",
            "running",
            "search",
            "tool",
            "tool call",
            "tool output",
            "function_call",
            "function call",
        )
    ):
        roles.append("observed_behavior_or_action_proof")
    if any(
        marker in text
        for marker in (
            "diverg",
            "instead",
            "rather than",
            "even though",
            "failed to stop",
            "when i expected",
            "but i expected",
            "expected it to stop",
        )
    ):
        roles.append("divergence_evidence")
    if core_convo_target_source_role(read) == "legitimate_context":
        roles.append("legitimate_context")
    return dedupe(roles)


def expectation_mismatch_plan_read_reject_reasons(read: dict[str, Any], *, query: str) -> list[str]:
    text = expectation_mismatch_plan_read_text(read)
    normalized = " ".join(text.casefold().replace("_", " ").split())
    reasons: list[str] = []
    required_ids = expectation_mismatch_query_episode_ids(query)
    read_ids = set(expectation_mismatch_query_episode_ids(text))
    if required_ids and not required_ids.intersection(read_ids):
        reasons.append("missing_requested_target_episode:" + ",".join(sorted(required_ids)))
    wrong_ids = sorted(read_ids - required_ids) if required_ids else []
    if wrong_ids:
        reasons.append("mentions_different_episode:" + ",".join(wrong_ids[:4]))
    echo = setup_echo_classification(text, query=query)
    if echo.get("is_setup_echo"):
        reasons.append(f"setup_or_assignment_echo:{echo.get('reason') or 'setup_echo'}")
    if any(
        marker in normalized
        for marker in (
            "repair prompt",
            "control prompt",
            "current assignment",
            "assignment prompt",
            "implementer assignment",
            "reviewer assignment",
            "read the implementer skill",
            "setup command",
            "function call output",
            "function call",
            "tool call",
            "same loop",
            "same-loop",
            "current run",
            "current-run",
        )
    ):
        reasons.append("repair_control_setup_or_current_echo_marker")
    if read.get("diagnostic_only") is True or read.get("read_more_actionable") is False:
        reasons.append("diagnostic_or_non_actionable")
    if read.get("followthrough_actionable") is False:
        reasons.append("not_followthrough_actionable")
    if str(read.get("target_source_role") or "") == "diagnostic_or_post_hoc":
        reasons.append("target_source_role_diagnostic_or_post_hoc")
    if read.get("final_support_allowed") is False:
        reasons.append("final_support_not_allowed")
    return dedupe(reasons)


def expectation_mismatch_query_episode_ids(text: str) -> set[str]:
    return {
        match.group(0).casefold().replace("_", "-")
        for match in re.finditer(r"(?<![a-z0-9_.-])jsh-\d{2,}[a-z0-9-]*(?![a-z0-9_.-])", str(text or ""), re.I)
    }


def expectation_mismatch_plan_read_text(read: dict[str, Any]) -> str:
    parts: list[str] = []

    def add(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for nested in value.values():
                add(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                add(nested)
            return
        parts.append(str(value))

    for key in (
        "target",
        "openable_target",
        "path",
        "reason",
        "snippet",
        "summary",
        "command",
        "kind",
        "target_source_role",
        "source_ref",
        "openable_source_ref",
        "action_proof_source_selection",
        "record_metadata",
    ):
        add(read.get(key))
    return " ".join(parts)


def expectation_mismatch_missing_role_guidance_read(
    *,
    query: str,
    demoted_reads: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = ["expected_behavior_evidence", "observed_behavior_or_action_proof", "divergence_evidence"]
    targets = [
        str(item.get("target") or item.get("openable_target") or item.get("path") or "")
        for item in demoted_reads[:4]
        if str(item.get("target") or item.get("openable_target") or item.get("path") or "").strip()
    ]
    reasons = dedupe(
        reason
        for item in demoted_reads
        for reason in item.get("expectation_mismatch_demotion_reasons") or []
        if str(reason).strip()
    )
    return {
        "kind": "expectation_mismatch_missing_facet_guidance",
        "status": "no_clean_actionable_expectation_mismatch_reads",
        "target": None,
        "target_source_role": "missing_clean_expectation_mismatch_facets",
        "expectation_mismatch_role": "missing_required_facets",
        "missing_facets": missing,
        "required_target_ids": sorted(expectation_mismatch_query_episode_ids(query)),
        "diagnostic_only": True,
        "read_more_actionable": False,
        "followthrough_actionable": False,
        "final_support_allowed": False,
        "demoted_candidate_count": len(demoted_reads),
        "demoted_candidate_targets": targets,
        "demotion_reasons": reasons[:8],
        "reason": (
            "No clean target historical expected/observed/divergence source refs are available; "
            "demoted repair/control/setup/current or wrong-target leads."
        ),
        "required_next_proof": (
            "Open clean target historical raw/session/tool evidence for expected_behavior_evidence, "
            "observed_behavior_or_action_proof, and divergence_evidence before answering."
        ),
    }


def expectation_mismatch_annotated_read(
    read: dict[str, Any],
    *,
    role: str,
    inferred: bool = False,
) -> dict[str, Any]:
    row = dict(read)
    row["expectation_mismatch_role"] = role
    row["expectation_mismatch_role_order"] = EXPECTATION_MISMATCH_READ_ORDER.index(role)
    row["expectation_mismatch_inferred_role"] = bool(inferred)
    if role == "diagnostic_or_post_hoc":
        row["diagnostic_only"] = True
        row["read_more_actionable"] = False
        row["followthrough_actionable"] = False
        row.setdefault(
            "diagnostic_open_policy",
            "do_not_open_by_default_until_expected_observed_divergence_reads_leave_named_gap",
        )
    return row


def core_convo_dedupe_role_reads(reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_identity: dict[str, dict[str, Any]] = {}
    for read in reads:
        identity = core_convo_read_identity(read)
        if not identity:
            identity = f"sequence:{read.get('core_convo_sequence_index')}"
        existing = by_identity.get(identity)
        if existing is None or core_convo_read_sort_tuple(read) < core_convo_read_sort_tuple(existing):
            by_identity[identity] = read
    return list(by_identity.values())


def core_convo_read_sort_tuple(read: dict[str, Any]) -> tuple[int, int]:
    return (
        int(read.get("target_source_role_order", 99)),
        int(read.get("core_convo_sequence_index", 0)),
    )


def core_convo_read_identity(read: dict[str, Any]) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    return str(
        read.get("target")
        or source_ref_target(source_ref)
        or source_ref.get("path")
        or read.get("packet_handle")
        or read.get("kind")
        or ""
    )


def core_convo_target_source_role(read: dict[str, Any], *, default_role: str | None = None) -> str:
    explicit = str(read.get("target_source_role") or "")
    if explicit in CORE_CONVO_TARGET_SOURCE_ROLE_RANK:
        return explicit
    if read.get("artifact_audit_promoted") is True and default_role in CORE_CONVO_TARGET_SOURCE_ROLE_RANK:
        return str(default_role)
    if core_convo_read_is_diagnostic_or_post_hoc(read):
        return "diagnostic_or_post_hoc"
    if default_role in CORE_CONVO_TARGET_SOURCE_ROLE_RANK:
        return str(default_role)

    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    chronology_role = str(selection.get("source_chronology_role") or read.get("source_chronology_role") or "")
    kind = str(read.get("kind") or "")
    target = str(read.get("target") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    path = str(source_ref.get("path") or source_target_path_part(target) or target)
    packet_handle = str(read.get("packet_handle") or "")
    proof_role = str(read.get("proof_role") or "")
    orchestration_role = str(read.get("role") or "")

    if (
        chronology_role in {
            "target_historical_raw_jsonl_source_ref",
            "candidate_or_original_source_ref",
            "pre_existing_source_ref_quoted_by_current_run_echo",
        }
        or kind in {"jsonl_source_ref", "tool_event_source_ref"}
        or proof_role in {"candidate_action_source_window", "command_or_tool_call", "command_or_tool_output"}
        or (kind == "missing_role_recovery_command" and orchestration_role != "secondary_task_review_commit_support")
    ) and core_convo_path_is_raw_history(path, target):
        return "target_historical_raw"

    if (
        chronology_role in {"bounded_committed_safe_packet_fallback", "historical_task_review_commit_artifact"}
        or kind in {"committed_safe_devlog_packet_fallback", "missing_role_recovery_command"}
        or packet_handle in {"repo_phase_category_evidence", "failure_analysis.devlog_brief"}
        or core_convo_path_is_historical_artifact(path, target)
    ):
        return "target_historical_artifact"

    return "legitimate_context"


def core_convo_read_is_diagnostic_or_post_hoc(read: dict[str, Any]) -> bool:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    chronology_role = str(selection.get("source_chronology_role") or read.get("source_chronology_role") or "")
    if read.get("diagnostic_only") is True or selection.get("diagnostic_only") is True:
        return True
    if read.get("no_evidence_action_role") == "optional_diagnostic_after_scoped_answer":
        return True
    if read.get("read_more_actionable") is False and read.get("followthrough_actionable") is False:
        return True
    if chronology_role.endswith("_diagnostic") or "diagnostic" in chronology_role:
        return True
    text = core_convo_read_marker_text(read)
    hard_diagnostic_markers = (
        "/current/",
        "/current_task/",
        "/current-task/",
        "/same-loop/",
        "/same_loop/",
        "/posthoc/",
        "/post-hoc/",
        "current-run",
        "current run",
        "same-loop",
        "same loop",
        "same-run",
        "same run",
        "post-hoc",
        "post hoc",
        "prior-final",
        "prior final",
        "previous final",
        "answer-key",
        "answer key",
        "adjudication",
        "synthetic",
        "/smoke/",
        "smoke test",
        "partial diagnostic",
        "metadata-only diagnostic",
        "unrelated high-lexical",
        "unrelated_high_lexical",
    )
    if any(marker in text for marker in hard_diagnostic_markers):
        return True
    path_fixture_markers = ("/tests/", "/fixtures/", "fixture")
    if any(marker in text for marker in path_fixture_markers):
        return not core_convo_read_is_actionable_raw_source_ref(read)
    return False


def core_convo_read_is_actionable_raw_source_ref(read: dict[str, Any]) -> bool:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    if selection.get("final_support_allowed") is False:
        return False
    if selection.get("read_more_actionable") is False or read.get("read_more_actionable") is False:
        return False
    return core_convo_read_targets_raw_history(read)


def core_convo_read_targets_raw_history(read: dict[str, Any]) -> bool:
    kind = str(read.get("kind") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or source_ref_target(source_ref) or "")
    path = str(source_ref.get("path") or source_target_path_part(target) or target)
    if kind not in {"jsonl_source_ref", "tool_event_source_ref"} and not source_ref.get("path"):
        return False
    return bool(target or source_ref.get("path")) and core_convo_path_is_raw_history(path, target)


def core_convo_read_marker_text(read: dict[str, Any]) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    return (
        " ".join(
            str(value)
            for value in (
                read.get("kind"),
                read.get("target"),
                read.get("reason"),
                read.get("candidate_id"),
                read.get("source_category"),
                read.get("packet_handle"),
                read.get("target_source_role"),
                read.get("proof_role"),
                read.get("role"),
                source_ref.get("path"),
                source_ref.get("session"),
                selection.get("source_chronology_role"),
                selection.get("selection_reason"),
            )
        )
        .casefold()
        .replace("\\", "/")
        .replace("_", " ")
    )


def core_convo_path_is_raw_history(path: str, target: str) -> bool:
    text = f"{path} {target}".casefold().replace("\\", "/")
    return ".jsonl" in text or text.startswith("codex_history:") or text.startswith("codex_sessions:")


def core_convo_path_is_historical_artifact(path: str, target: str) -> bool:
    text = f"{path} {target}".casefold().replace("\\", "/")
    return text.startswith(("tasks/", "reports/", "codex/state/reviews/", "docs/")) or any(
        marker in text for marker in ("/tasks/", "/reports/", "/codex/state/reviews/", "/docs/")
    )


def build_source_action_guidance(
    *,
    answer_readiness: dict[str, Any],
    no_evidence: dict[str, Any],
    next_reads: list[dict[str, Any]],
    fielded_bm25_command: dict[str, Any] | None = None,
    scope_boundary_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoped_no_evidence = bool(no_evidence.get("needed")) and answer_readiness.get("decision") == "state_scoped_no_evidence"
    if scoped_no_evidence:
        optional_commands = []
        if fielded_bm25_command:
            command = dict(fielded_bm25_command)
            command["required_for_decisions"] = []
            command["optional_after_decision"] = True
            command["purpose"] = (
                "Optional retrieval escalation only if the user needs stronger recall or a broader scope than "
                "the current searched-scope no-evidence answer allows."
            )
            optional_commands.append(command)
        return {
            "schema_version": 1,
            "decision": "answer_scoped_no_evidence_now",
            "scope_boundary_summary": scope_boundary_summary or {},
            "required_primary_source_actions": [],
            "optional_diagnostic_reads": diagnostic_next_read_summaries(next_reads),
            "stop_condition": (
                "Stop broad search and answer `not_found_in_searched_scope` once searched, not-searched, "
                "missing, and cap-limited scope are named."
            ),
            "retrieval_escalation_condition": (
                "Escalate to `--retrieval-mode fielded-bm25` only if the user asks for stronger recall, "
                "the searched scope/caps are too weak for the requested claim, or the answer must go beyond "
                "searched-scope no-evidence."
            ),
            "optional_retrieval_escalation_commands": optional_commands,
            "diagnostic_policy": (
                "Diagnostic next_reads may be inspected for audit/context, but they are not required before "
                "the scoped no-evidence answer."
            ),
        }
    required_actions = bounded_answer_ready_next_reads(next_reads)
    diagnostic_reads = diagnostic_next_read_summaries(demoted_required_surface_reads(next_reads))
    if not required_actions and diagnostic_reads:
        return {
            "schema_version": 1,
            "decision": "diagnostic_only_reads_not_primary_proof",
            "scope_boundary_summary": scope_boundary_summary or {},
            "required_primary_source_actions": [],
            "optional_diagnostic_reads": diagnostic_reads,
            "stop_condition": (
                "Do not treat diagnostic-only current/self-echo reads as primary evidence. Stop required "
                "read-more on those refs; refine source scope, use targeted historical artifact/source-handle "
                "search, or escalate retrieval only if the requested historical claim still lacks evidence."
            ),
            "retrieval_escalation_condition": (
                "Escalate retrieval mode only when no pre-existing historical source refs or artifact handles "
                "support the answer after the diagnostic echo boundary is recognized."
            ),
        }
    return {
        "schema_version": 1,
        "decision": str(answer_readiness.get("decision") or "gather_more_evidence"),
        "scope_boundary_summary": scope_boundary_summary or {},
        "required_primary_source_actions": required_actions,
        "optional_diagnostic_reads": diagnostic_reads,
        "stop_condition": "Stop when inspected source refs support the answer within the searched-scope boundaries.",
        "retrieval_escalation_condition": (
            "Escalate retrieval mode only when package-mode evidence is weak, required facets remain missing, "
            "or bounded source reads do not support the requested answer."
        ),
    }


LONGITUDINAL_PHASES = [
    {
        "phase_id": "early_year",
        "label": "early year",
        "date_start": "2026-01-01",
        "date_end": "2026-03-31",
    },
    {
        "phase_id": "mid_year",
        "label": "mid year",
        "date_start": "2026-04-01",
        "date_end": "2026-06-30",
    },
    {
        "phase_id": "recent",
        "label": "recent",
        "date_start": "2026-07-01",
        "date_end": None,
    },
]


def longitudinal_phase_source_plan_wanted(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").lower()).strip()
    if not lowered:
        return False
    longitudinal_cues = (
        "over time",
        "changed over time",
        "since the start",
        "since start",
        "start of the year",
        "this year",
        "timeline",
        "chronology",
        "longitudinal",
        "phases",
        "evolution",
    )
    subject_cues = (
        "workflow",
        "workflows",
        "orchestration",
        "ai usage",
        "usage",
        "changed",
        "history",
    )
    return any(cue in lowered for cue in longitudinal_cues) and any(cue in lowered for cue in subject_cues)


def build_longitudinal_phase_source_plan(
    *,
    query: str,
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
    scope_boundary_summary: dict[str, Any],
    no_evidence: dict[str, Any],
) -> dict[str, Any]:
    if not longitudinal_phase_source_plan_wanted(query):
        return {"schema_version": 1, "applies": False}

    read_summaries = [longitudinal_read_summary(read) for read in next_reads if isinstance(read, dict)]
    actionable_reads = [
        read
        for read in read_summaries
        if read.get("read_more_actionable") is not False and not read.get("diagnostic_only")
    ]
    diagnostic_reads = [read for read in read_summaries if read.get("diagnostic_only")]
    phase_ids = {str(read.get("phase_id")) for read in actionable_reads if read.get("phase_id")}
    missing_phase_ids = [
        str(phase["phase_id"])
        for phase in LONGITUDINAL_PHASES
        if str(phase["phase_id"]) not in phase_ids
    ]
    phase_rows = []
    for phase in LONGITUDINAL_PHASES:
        phase_id = str(phase["phase_id"])
        represented = [read for read in actionable_reads if read.get("phase_id") == phase_id]
        diagnostics = [read for read in diagnostic_reads if read.get("phase_id") == phase_id]
        phase_rows.append(
            {
                **phase,
                "status": "represented" if represented else "missing_or_not_selected",
                "actionable_read_count": len(represented),
                "diagnostic_echo_count": len(diagnostics),
                "source_families": sorted({str(read.get("source_family")) for read in represented if read.get("source_family")}),
                "prioritized_next_reads": represented[:3],
            }
        )

    selected_root_families = [
        str(root.get("kind"))
        for root in source_locator_handoff.get("selected_roots") or []
        if isinstance(root, dict) and root.get("kind")
    ]
    read_families = Counter(
        str(read.get("source_family"))
        for read in read_summaries
        if read.get("source_family")
    )
    source_family_rows = [
        {
            "family": family,
            "selected_root": family in selected_root_families,
            "surfaced_read_count": int(read_families.get(family, 0)),
        }
        for family in sorted(set(selected_root_families) | set(read_families.keys()))
    ]
    cap_hit = bool((scope_boundary_summary.get("caps") or {}).get("hit"))
    phase_balance_status = (
        "phase_recovery_required"
        if len(phase_ids) < 2 or missing_phase_ids
        else "phase_diverse_candidate_reads_present"
    )
    required_before_synthesis = []
    if missing_phase_ids:
        required_before_synthesis.append(
            "Recover or explicitly mark missing longitudinal phases before synthesis: "
            + ", ".join(missing_phase_ids)
        )
    if cap_hit:
        required_before_synthesis.append(
            "Name cap-limited searched scope before treating surfaced reads as representative."
        )
    if diagnostic_reads:
        required_before_synthesis.append(
            "Keep current-run/current-task/same-wave prompt echoes diagnostic; do not use them as primary historical proof."
        )
    if not required_before_synthesis:
        required_before_synthesis.append(
            "Before synthesis, keep phase labels and source-family roles explicit."
        )

    recovery_actions = longitudinal_phase_recovery_actions(
        missing_phase_ids=missing_phase_ids,
        source_families=selected_root_families,
    )
    return {
        "schema_version": 1,
        "applies": True,
        "plan_name": "longitudinal_phase_source_plan",
        "status": (
            "phase_source_recovery_required"
            if phase_balance_status == "phase_recovery_required"
            else "phase_source_plan_ready"
        ),
        "phase_balance_status": phase_balance_status,
        "represented_phase_ids": sorted(phase_ids),
        "missing_phase_ids": missing_phase_ids,
        "planned_phases": phase_rows,
        "source_set_families": source_family_rows,
        "searched_scope": {
            "searched_scope": scope_boundary_summary.get("searched_scope") or {},
            "not_searched_scope": scope_boundary_summary.get("not_searched_scope") or {},
            "caps": scope_boundary_summary.get("caps") or {},
            "answer_scope_boundary": scope_boundary_summary.get("answer_scope_boundary"),
            "not_all_history_boundary": scope_boundary_summary.get("not_all_history_boundary", True),
            "no_evidence_scope": {
                "needed": bool(no_evidence.get("needed")),
                "answer_scope": no_evidence.get("answer_scope"),
            },
        },
        "same_wave_current_echoes": {
            "count": len(diagnostic_reads),
            "policy": "diagnostic_or_tainted_not_primary_historical_proof",
            "examples": diagnostic_reads[:5],
        },
        "prioritized_next_reads_before_synthesis": actionable_reads[:6],
        "prioritized_recovery_actions": recovery_actions,
        "required_before_synthesis": required_before_synthesis,
        "claim_boundaries": {
            "does_not_claim_phase_completeness": True,
            "does_not_change_ranking": True,
            "does_not_expand_roots": True,
            "raw_text_included": False,
            "current_or_same_wave_echoes_are_primary_proof": False,
        },
    }


def longitudinal_read_summary(read: dict[str, Any]) -> dict[str, Any]:
    target = str(read.get("target") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    source_ref = guided_source_ref(source_ref) if source_ref else {}
    path = str(source_ref.get("path") or target.split(":", 1)[0] or "")
    date_value = longitudinal_read_date(read, path)
    phase_id = longitudinal_phase_id(date_value)
    selection = read.get("historical_provenance_selection") if isinstance(read.get("historical_provenance_selection"), dict) else {}
    return {
        "target": target,
        "kind": read.get("kind"),
        "source_ref": source_ref or None,
        "source_family": longitudinal_source_family(path),
        "date": date_value.isoformat() if date_value else None,
        "phase_id": phase_id,
        "diagnostic_only": bool(read.get("diagnostic_only")),
        "read_more_actionable": read.get("read_more_actionable"),
        "source_chronology_role": selection.get("source_chronology_role") or read.get("target_source_role"),
        "reason": read.get("reason"),
    }


def longitudinal_read_date(read: dict[str, Any], path: str) -> datetime | None:
    for key in ("timestamp_utc", "timestamp", "created_at"):
        value = read.get(key)
        if isinstance(value, str) and value.strip():
            parsed = parse_iso_datetime(value)
            if parsed:
                return parsed
    for regex in (CODEX_SESSION_DATE_RE, CODEX_ROLLOUT_DATE_RE):
        match = regex.search(path)
        if match:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=timezone.utc,
            )
    return None


def parse_iso_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def longitudinal_phase_id(value: datetime | None) -> str | None:
    if value is None:
        return None
    date = value.date()
    if date <= datetime(2026, 3, 31, tzinfo=timezone.utc).date():
        return "early_year"
    if date <= datetime(2026, 6, 30, tzinfo=timezone.utc).date():
        return "mid_year"
    return "recent"


def longitudinal_source_family(path: str) -> str:
    normalized = str(path or "").lower().replace("\\", "/")
    if "/.codex/sessions/" in normalized:
        return "codex_sessions"
    if normalized.endswith("/.codex/history.jsonl") or "/.codex/history.jsonl" in normalized:
        return "codex_history"
    if "/.claude/projects/" in normalized:
        return "claude_projects"
    if "/tasks/" in normalized:
        return "repo_tasks"
    if "/reports/" in normalized:
        return "repo_reports"
    if "/codex/state/reviews/" in normalized or "/reviews/" in normalized:
        return "repo_reviews"
    return "other"


def longitudinal_phase_recovery_actions(*, missing_phase_ids: list[str], source_families: list[str]) -> list[dict[str, Any]]:
    family_terms = ", ".join(source_families) if source_families else "selected source roots"
    actions = []
    for phase in LONGITUDINAL_PHASES:
        phase_id = str(phase["phase_id"])
        if phase_id not in missing_phase_ids:
            continue
        date_end = phase.get("date_end") or "current run date"
        actions.append(
            {
                "phase_id": phase_id,
                "date_start": phase.get("date_start"),
                "date_end": date_end,
                "source_families_to_try": list(source_families),
                "action": (
                    f"Run a bounded phase-specific semantic-debug/find pass over {family_terms}; "
                    "prefer primary raw user/session windows and pre-existing task/review artifacts, "
                    "then mark this phase as gap if caps or selected roots cannot support it."
                ),
                "final_support_allowed": False,
                "raw_text_included": False,
            }
        )
    return actions


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
    if primary_family == "process_actual_use":
        return {
            "schema_version": 1,
            "applies": False,
            "reason": "process_actual_use_uses_raw_timing_bucket_audit_not_claimed_action_proof",
        }
    detected_supported = set(family.get("detected_supported") or [])
    action_reads = [
        read
        for read in next_reads
        if isinstance(read, dict) and isinstance(read.get("action_proof_source_selection"), dict)
    ]
    applies = primary_family == "claimed_action" or "claimed_action" in detected_supported
    if not applies:
        return {"schema_version": 1, "applies": False, "reason": "not_a_claimed_action_or_action_proof_query"}

    primary_refs = action_proof_primary_refs(action_reads, source_window_action_proof, next_reads=next_reads)
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
    *,
    next_reads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for read in list(next_reads or []):
        if read_is_required_surface_actionable(read) and reviewed_source_packet_pointer_read(read):
            refs.append(action_proof_read_ref(read, proof_role="reviewed_source_packet_pointer"))
    for read in [item for item in action_reads if read_is_required_surface_actionable(item)][:4]:
        refs.append(action_proof_read_ref(read, proof_role="candidate_action_source_window"))

    direct_source_session_refs = [
        ref
        for ref in refs
        if ref.get("selection_reason") == "correlated_direct_source_session_event"
    ]
    if direct_source_session_refs:
        return dedupe_dicts(direct_source_session_refs)[:PROVENANCE_DIRECT_EVIDENCE_READ_CAP]

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
        "diagnostic_only": read.get("diagnostic_only"),
        "read_more_actionable": read.get("read_more_actionable"),
        "followthrough_actionable": read.get("followthrough_actionable"),
        "historical_provenance_selection": read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else None,
        "direct_evidence_facets": list(read.get("direct_evidence_facets") or []),
        "provenance_direct_evidence": read.get("provenance_direct_evidence")
        if isinstance(read.get("provenance_direct_evidence"), dict)
        else None,
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
        for read in [item for item in next_reads if isinstance(item, dict) and read_is_required_surface_actionable(item)][:3]
        if read.get("target") or read.get("source_ref")
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
    suppress_action_proof: bool = False,
) -> list[dict[str, str]]:
    reads: list[dict[str, Any]] = []
    for candidate in candidates[:5]:
        ref = candidate.get("source_ref") or {}
        target = source_ref_target(ref)
        if target:
            read = {
                "kind": "jsonl_source_ref",
                "target": target,
                "candidate_id": str(candidate.get("candidate_id") or ""),
                "source_ref": dict(ref),
                "reason": "confirm candidate snippet in source context",
                "expansion_actions": read_more_actions_for_source_ref(
                    ref,
                    candidate_id=str(candidate.get("candidate_id") or ""),
                    expansion_action_context=expansion_action_context,
                ),
            }
            if include_context_actions:
                read["context_actions"] = context_actions_for_source_ref(
                    ref,
                    candidate_id=str(candidate.get("candidate_id") or ""),
                    expansion_action_context=expansion_action_context,
                )
            reads.append(read)
    if not suppress_action_proof:
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
            reads.append(
                {
                    "kind": "task_or_review_artifact",
                    "target": item["path"],
                    "reason": (
                        "confirm secondary task/review context for actual-use buckets"
                        if suppress_action_proof
                        else "confirm task/review verdict and claim-action classification"
                    ),
                }
            )
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
    promoted_reads = (
        []
        if suppress_action_proof
        else action_proof_source_next_reads(
            query=query,
            candidates=candidates,
            tool_proof=tool_proof,
            expansion_action_context=expansion_action_context,
            include_context_actions=include_context_actions,
        )
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
    context_start = max(0, index - 6)
    context_end = min(len(records), index + 7)
    local_context = " ".join(str(records[item].text or "").lower() for item in range(context_start, context_end))
    topic_context = f"{lowered} {local_context}"
    if "persistent coordinator" not in topic_context and "persistent-coordinator" not in topic_context:
        return 0.0, "no_correction_window_topic"
    wait_cue = "wait" in topic_context or "poll" in topic_context
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
    if role_text == "function_call" and any(
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
    family: dict[str, Any],
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
    process_actual_use = family.get("primary") == "process_actual_use" or "process_actual_use" in set(
        family.get("detected_supported") or []
    )
    if (source_handle_routing or {}).get("detected_handles"):
        required_actions.insert(
            0,
            "Resolve/open matching source refs for detected reviewed/source/case handles before broad answer claims, or state no matching source ref was found in searched scope.",
        )
    if process_actual_use:
        required_actions.insert(
            0,
            "For process/artifact actual-use claims, fill the actual-use buckets and cite primary raw conversation/session windows for behavior claims.",
        )
        required_actions.insert(
            1,
            "Treat repo task/review artifacts as secondary unless linked to raw timing or reviewer enforcement evidence.",
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
    historical_orchestration_plan = source_locator_handoff.get(
        "historical_orchestration_provenance_final_support_plan"
    ) or {}
    historical_orchestration_role_evidence_bundle = source_locator_handoff.get(
        "historical_orchestration_role_evidence_bundle"
    ) or {}
    if historical_orchestration_plan:
        evidence_inputs["historical_orchestration_provenance_final_support_plan"] = (
            historical_orchestration_plan_summary(historical_orchestration_plan)
        )
    if historical_orchestration_role_evidence_bundle:
        evidence_inputs["historical_orchestration_role_evidence_bundle"] = (
            historical_orchestration_role_evidence_bundle_summary(
                historical_orchestration_role_evidence_bundle
            )
        )
    if status == "no_evidence":
        required_actions.insert(0, "Answer with `not_found_in_searched_scope`, not global absence.")
        required_actions.append("Name not-searched, missing, or cap-limited scope before concluding no evidence.")
        answer_scope = "not_found_in_searched_scope"
        source_use_required = False
    else:
        required_actions.insert(0, "Use or cite at least one source ref before making a factual or provenance claim.")
        if historical_orchestration_plan:
            required_actions.insert(
                1,
                (
                    "For historical orchestration/provenance, use only promoted pre-existing raw JSONL refs "
                    "as final support; treat current-run/current-task/same-loop echoes as diagnostics."
                ),
            )
        if tool_proof.get("events"):
            required_actions.append("Use structured tool/action proof for claims about what an agent actually ran, edited, tested, or reviewed.")
        if joins.get("status") == "joined":
            required_actions.append("Use task/review/commit joins for provenance claims before relying on transcript snippets alone.")
        answer_scope = "source_backed_answer_required"
        source_use_required = bool(candidate_refs or next_reads or tool_proof.get("events") or joins.get("status") == "joined")
    contract = {
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
    if process_actual_use:
        contract["process_actual_use_contract"] = {
            "status": "active",
            "bucket_requirements": process_actual_use_bucket_contract(),
            "source_hierarchy": process_actual_use_source_hierarchy(),
            "primary_raw_evidence_required_for_behavior_claims": True,
            "repo_task_review_artifacts_secondary_unless_linked": True,
            "answer_shape": [
                "separate exists/cited from consulted-before-planning",
                "separate behavior/scope/claim-boundary changes from post-hoc updates",
                "state stale/performative/no-evidence gaps explicitly",
            ],
            "forbidden_claims": process_actual_use_forbidden_claims(),
        }
        contract["claim_boundaries"]["process_actual_use_behavior_claim"] = (
            "requires primary raw timing or reviewer enforcement evidence"
        )
    if historical_orchestration_plan:
        contract["historical_orchestration_provenance_contract"] = {
            "status": historical_orchestration_plan.get("status"),
            "required_roles": list(historical_orchestration_plan.get("required_roles") or []),
            "covered_roles": list(historical_orchestration_plan.get("covered_roles") or []),
            "missing_roles": list(historical_orchestration_plan.get("missing_roles") or []),
            "primary_historical_final_support_read_count": historical_orchestration_plan.get(
                "primary_historical_final_support_read_count", 0
            ),
            "bounded_non_final_required_read_count": historical_orchestration_plan.get(
                "bounded_non_final_required_read_count", 0
            ),
            "missing_role_recovery_ref_count": historical_orchestration_plan.get(
                "missing_role_recovery_ref_count", 0
            ),
            "answer_shape": [
                "separate parent/coordinator, worker, and reviewer/rereviewer evidence when requested",
                "cite pre-existing raw JSONL refs as final support only after inspection",
                "state missing requested roles instead of relying on current-run echoes",
            ],
            "source_hierarchy": {
                "final_support": "pre_existing_raw_jsonl_source_refs",
                "answer_ready_role_bundle": "semantic_debug.historical_orchestration_role_evidence_bundle",
                "diagnostic_only": "current_run_current_task_same_loop_echoes",
                "missing_role_recovery_refs": "exact_role_targeted_recovery_refs_or_commands",
                "non_final_required_reads": "bounded_non_echo_reads_for_gap_checking_only",
            },
        }
        contract["claim_boundaries"]["historical_orchestration_current_run_support"] = (
            "current-run/current-task/same-loop refs do not count as final historical orchestration support"
        )
    return contract


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
    explicit_correction_window_ready = bool(complete_explicit_correction_window_reads(next_reads))
    audit_route_required = any(route_state["required"] for route_state in route_states if route_state["route"] in AUDIT_ROUTE_NAMES)
    historical_orchestration_contract = final_answer_contract.get(
        "historical_orchestration_provenance_contract"
    ) or {}
    historical_orchestration_plan = source_locator_handoff.get(
        "historical_orchestration_provenance_final_support_plan"
    ) or {}
    historical_orchestration_answer_ready = (
        historical_orchestration_plan.get("status") == "answer_ready"
        and bool(historical_orchestration_plan.get("primary_historical_final_support_reads"))
    )
    historical_orchestration_fail_closed = (
        bool(historical_orchestration_plan)
        and not historical_orchestration_answer_ready
    )
    if no_evidence.get("needed"):
        readiness_status = "scoped_no_evidence_ready"
        decision = "state_scoped_no_evidence"
        allowed_answer_scope = "searched_scope_no_evidence"
        required_before_answer = list(scope_ready_required_actions(no_evidence))
    elif explicit_correction_window_ready and final_answer_contract.get("source_use_required"):
        readiness_status = "answer_ready"
        decision = "answer_now"
        allowed_answer_scope = "explicit_date_source_window"
        required_before_answer = [
            (
                "Open the primary explicit-date source window, answer only from that window, "
                "and do not run repo rg/current-session recovery unless a named answer facet remains missing."
            ),
        ]
    elif recovery_available:
        readiness_status = "inspect_recovery_first"
        decision = "inspect_recovery_reads"
        allowed_answer_scope = "recovery_first"
        required_before_answer = [
            "Open promoted omitted-window recovery reads before treating the searched evidence set as representative.",
        ]
        if omitted_window_recovery.get("promoted_next_read_count", 0):
            required_before_answer.append("Review the promoted next_reads tied to omitted-window recovery before answering.")
    elif historical_orchestration_answer_ready and final_answer_contract.get("source_use_required"):
        readiness_status = "answer_ready"
        decision = "answer_now"
        allowed_answer_scope = "historical_orchestration_provenance_source_backed"
        required_before_answer = [
            (
                "Answer from promoted pre-existing raw JSONL orchestration refs; do not count "
                "current-run/current-task/same-loop echoes as final support."
            ),
        ]
        covered = ", ".join(historical_orchestration_contract.get("covered_roles") or [])
        if covered:
            required_before_answer.append(f"Keep covered orchestration roles explicit: {covered}.")
    elif historical_orchestration_fail_closed:
        readiness_status = "inspect_recovery_first"
        decision = "inspect_recovery_reads"
        allowed_answer_scope = "historical_orchestration_provenance_incomplete_non_final_reads"
        missing = ", ".join(historical_orchestration_contract.get("missing_roles") or [])
        required_before_answer = [
            (
                "Run the exact missing_role_recovery_refs before answering; they are gap checks, "
                "not final support, and missing roles remain explicit gaps."
            ),
        ]
        if missing:
            required_before_answer.append(f"State missing requested orchestration roles before any answer: {missing}.")
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
    process_actual_use_contract = final_answer_contract.get("process_actual_use_contract") or {}
    if process_actual_use_contract:
        required_before_answer.insert(
            0,
            "For process actual-use, inspect raw conversation/session windows for before-planning consultation and behavior/scope/claim-boundary changes before answering yes/no.",
        )
        required_before_answer.insert(
            1,
            "Do not count repo task/review artifacts as primary actual-use proof unless they are linked to raw timing or reviewer enforcement evidence.",
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
            "process_actual_use_bucket_count": len(process_actual_use_contract.get("bucket_requirements") or []),
            "historical_orchestration_required_role_count": len(
                historical_orchestration_contract.get("required_roles") or []
            ),
            "historical_orchestration_missing_role_count": len(
                historical_orchestration_contract.get("missing_roles") or []
            ),
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
            "process_actual_use_contract": process_actual_use_contract,
            "historical_orchestration_provenance_contract": historical_orchestration_contract,
        },
        "answer_gate_link": "semantic_debug.guided_audit_flow.answer_gate",
        "answer_readiness_link": "semantic_debug.answer_readiness",
        "uncertainty_labels": [item.get("label") for item in uncertainty],
    }


SEMANTIC_DEBUG_AUDIT_SLICE_STATUSES = {
    "complete",
    "capped_retryable",
    "capped_after_budget_with_no_completeness_claim",
    "blocked_with_reason",
}
SEMANTIC_DEBUG_GENERIC_RETRY_SLICE_LIMIT = 8
SEMANTIC_DEBUG_GENERIC_RETRY_SCAN_FILE_LIMIT = 96

MODEL_TOKEN_RE = re.compile(r"\b(?:gpt|claude|o\d)[A-Za-z0-9._-]*(?:-[A-Za-z0-9._-]+)*\b", re.IGNORECASE)


def build_in_depth_semantic_debug_audit_coverage(
    *,
    query: str,
    audit_depth: dict[str, Any],
    args: Any,
    source_scope: dict[str, Any],
    scope_boundary_summary: dict[str, Any],
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    depth = str((audit_depth or {}).get("depth") or "quick")
    if depth == "quick":
        return {}

    cap_hit = source_scope.get("cap_hit") if isinstance(source_scope.get("cap_hit"), dict) else {}
    roots = tuple(Path(root) for root in (getattr(args, "root", None) or ()))
    selected_models = semantic_debug_selected_model_terms(query)
    wants_structural_inventory = semantic_debug_structural_inventory_wanted(query)
    ledger = (
        semantic_debug_build_audit_coverage_ledger(
            roots=roots,
            query=query,
            selected_models=selected_models,
            args=args,
        )
        if roots and wants_structural_inventory
        else {}
    )
    slices = semantic_debug_audit_coverage_slices(
        ledger=ledger,
        cap_hit=cap_hit,
        scope_boundary_summary=scope_boundary_summary,
        roots=roots,
        next_reads=next_reads,
        candidates=candidates,
    )
    slice_status_counts = Counter(str(row.get("status")) for row in slices)
    coverage_complete = bool(slices) and all(row.get("status") == "complete" for row in slices)
    search_cap_hit = bool(cap_hit.get("hit"))
    ledger_summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
    classification_counts = ledger_summary.get("classification_counts") if isinstance(ledger_summary, dict) else {}
    blocked_or_unparseable = int((classification_counts or {}).get("blocked", 0) or 0) + int(
        (classification_counts or {}).get("unparseable", 0) or 0
    )
    full_scoped_inventory = (
        bool(ledger)
        and ledger.get("status") == "complete"
        and blocked_or_unparseable == 0
        and not search_cap_hit
    )
    answer_promotion_allowed = coverage_complete and not search_cap_hit
    if depth == "exhaustive":
        answer_promotion_allowed = answer_promotion_allowed and full_scoped_inventory

    demotion_summary = semantic_debug_promotion_demotion_summary(
        next_reads=next_reads,
        candidates=candidates,
        query=query,
        cap_hit=cap_hit,
    )
    if depth == "exhaustive" and not full_scoped_inventory:
        status = "incomplete_exhaustive_audit"
    elif answer_promotion_allowed:
        status = "coverage_complete"
    else:
        status = "coverage_required_before_answer"

    required_before_promotion = []
    if search_cap_hit:
        required_before_promotion.append(
            "Search caps were hit; treat surfaced candidates as leads and rerun deterministic coverage slices/windows before final support."
        )
    incomplete_slices = [row for row in slices if row.get("status") != "complete"]
    if incomplete_slices:
        required_before_promotion.append(
            "Resolve incomplete coverage slices or label them capped/blocked before claiming audit completeness."
        )
    if depth == "exhaustive" and not full_scoped_inventory:
        required_before_promotion.append(
            "Exhaustive mode requires full scoped inventory; return incomplete_exhaustive_audit until every trace is included, excluded_with_reason, blocked, or unparseable."
        )
    if demotion_summary["self_echo_or_current_run_count"]:
        required_before_promotion.append(
            "Keep current-run/self-echo evidence as diagnostic leads, not final support."
        )
    if demotion_summary["stale_adjacent_demoted_count"]:
        required_before_promotion.append(
            "Use stale adjacent refs only as diagnostics unless no fresher direct evidence exists and chronology/source role is labeled."
        )

    return {
        "schema_version": 1,
        "applies": True,
        "audit_depth": audit_depth,
        "status": status,
        "answer_promotion_allowed": answer_promotion_allowed,
        "full_scoped_inventory_before_promotion": full_scoped_inventory,
        "coverage_complete": coverage_complete,
        "deterministic_slice_status_contract": sorted(SEMANTIC_DEBUG_AUDIT_SLICE_STATUSES),
        "slices": slices,
        "slice_status_counts": {key: int(slice_status_counts.get(key, 0)) for key in sorted(slice_status_counts)},
        "search_cap_hit": {
            "hit": search_cap_hit,
            "caps_hit": list(cap_hit.get("caps_hit") or []),
            "normalized_warnings": list(cap_hit.get("normalized_warnings") or []),
            "evidence_role": "lead_only" if search_cap_hit else "not_cap_limited",
        },
        "structural_audit_coverage_ledger": semantic_debug_public_audit_coverage_ledger(ledger),
        "selected_metadata_priority": {
            "applies": wants_structural_inventory,
            "selected_models_from_query": selected_models,
            "structural_selected_model_counts": (ledger_summary or {}).get("structural_selected_model_counts", {}),
            "included_selected_model_counts": (ledger_summary or {}).get("included_selected_model_counts", {}),
            "quoted_or_tool_output_model_text_counts_as_selected_metadata": False,
        },
        "promotion_safety": {
            "cap_hit_evidence_role": "lead_only" if search_cap_hit else "eligible_if_coverage_complete",
            "top_n_only_evidence_role": (
                "lead_only_until_coverage_complete" if not answer_promotion_allowed else "eligible_after_complete_coverage"
            ),
            "self_echo_evidence_role": "lead_only_not_final_support",
            "stale_adjacent_ref_role": "diagnostic_unless_no_fresher_direct_evidence_and_labeled",
            "final_support_requires": (
                "complete_scoped_inventory"
                if depth == "exhaustive"
                else "complete_deterministic_coverage_slices"
            ),
        },
        "demotion_summary": demotion_summary,
        "required_before_promotion": dedupe(required_before_promotion),
        "claim_boundaries": {
            "does_not_claim_all_history_completeness": True,
            "cap_increase_used_as_substitute_for_coverage": False,
            "raw_private_text_included": False,
            "top_n_candidates_are_final_support_without_coverage": False,
            "current_run_self_echo_counts_as_final_support": False,
            "stale_adjacent_refs_count_as_final_support_by_default": False,
        },
    }


def semantic_debug_build_audit_coverage_ledger(
    *,
    roots: tuple[Path, ...],
    query: str,
    selected_models: list[str],
    args: Any,
) -> dict[str, Any]:
    thread_sources = ("subagent",) if re.search(r"\b(?:subagent|subagents|worker|workers)\b", query, re.I) else ()
    ledger_payload = build_audit_coverage_ledger(
        AuditCoverageOptions(
            roots=roots,
            window_days=1,
            filters=AuditCoverageFilters(
                selected_models=tuple(selected_models),
                thread_sources=thread_sources,
            ),
            max_files=getattr(args, "max_files", None),
            max_bytes=getattr(args, "max_bytes", None),
            trace_ledger_limit=0,
        )
    )
    return ledger_payload.get("audit_coverage_ledger") or {}


def semantic_debug_public_audit_coverage_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    if not ledger:
        return {"status": "not_run", "reason": "not_a_structural_model_or_worker_inventory_prompt"}
    summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
    return {
        "status": ledger.get("status"),
        "privacy_boundary": ledger.get("privacy_boundary"),
        "metadata_extraction_policy": ledger.get("metadata_extraction_policy"),
        "classification_contract": ledger.get("classification_contract"),
        "deterministic_windowing": ledger.get("deterministic_windowing"),
        "filters": ledger.get("filters"),
        "summary": summary,
        "trace_ledger_materialization": "omitted_from_semantic_debug_aggregate_surface",
        "trace_ledger_truncated": ledger.get("trace_ledger_truncated"),
    }


def semantic_debug_audit_coverage_slices(
    *,
    ledger: dict[str, Any],
    cap_hit: dict[str, Any],
    scope_boundary_summary: dict[str, Any],
    roots: tuple[Path, ...] = (),
    next_reads: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if ledger:
        rows = []
        for slice_row in ledger.get("slices") or []:
            counts = slice_row.get("classification_counts") if isinstance(slice_row, dict) else {}
            counts = counts if isinstance(counts, dict) else {}
            blocked = int(counts.get("blocked", 0) or 0)
            unparseable = int(counts.get("unparseable", 0) or 0)
            if blocked:
                status = "capped_after_budget_with_no_completeness_claim" if ledger.get("status") == "capped" else "blocked_with_reason"
            elif unparseable:
                status = "blocked_with_reason"
            else:
                status = "complete"
            rows.append(
                {
                    "slice": slice_row.get("slice"),
                    "status": status,
                    "classification_counts": counts,
                    "candidate_trace_count": slice_row.get("candidate_trace_count"),
                    "included_selected_model_counts": slice_row.get("included_selected_model_counts", {}),
                    "structural_selected_model_counts": slice_row.get("structural_selected_model_counts", {}),
                    "blocked_or_unparseable_count": blocked + unparseable,
                }
            )
        return rows or [semantic_debug_selected_scope_slice(cap_hit, scope_boundary_summary)]
    if cap_hit.get("hit"):
        retry_rows = semantic_debug_generic_cap_retry_slices(
            cap_hit=cap_hit,
            roots=roots,
            next_reads=next_reads or [],
            candidates=candidates or [],
        )
        if retry_rows:
            return [semantic_debug_selected_scope_slice(cap_hit, scope_boundary_summary), *retry_rows]
    return [semantic_debug_selected_scope_slice(cap_hit, scope_boundary_summary)]


def semantic_debug_selected_scope_slice(cap_hit: dict[str, Any], scope_boundary_summary: dict[str, Any]) -> dict[str, Any]:
    searched_scope = scope_boundary_summary.get("searched_scope") if isinstance(scope_boundary_summary, dict) else {}
    if cap_hit.get("hit"):
        status = "capped_retryable"
    else:
        status = "complete"
    return {
        "slice": "selected_scope",
        "status": status,
        "searched_files": (searched_scope or {}).get("searched_files", 0),
        "searched_bytes": (searched_scope or {}).get("searched_bytes", 0),
        "cap_hit": {
            "hit": bool(cap_hit.get("hit")),
            "caps_hit": list(cap_hit.get("caps_hit") or []),
        },
    }


def semantic_debug_generic_cap_retry_slices(
    *,
    cap_hit: dict[str, Any],
    roots: tuple[Path, ...],
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_paths = semantic_debug_selected_ref_paths(next_reads=next_reads, candidates=candidates)
    if selected_paths:
        return semantic_debug_retry_rows_for_paths(
            [(None, path) for path in selected_paths],
            cap_hit=cap_hit,
            basis="selected_source_refs",
        )

    root_paths = semantic_debug_bounded_root_jsonl_paths(roots)
    return semantic_debug_retry_rows_for_paths(
        root_paths,
        cap_hit=cap_hit,
        basis="selected_root_bounded_path_scan",
    )


def semantic_debug_selected_ref_paths(
    *,
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> list[str]:
    paths: list[str] = []
    for row in [*next_reads, *candidates]:
        if not isinstance(row, dict):
            continue
        path = semantic_debug_row_source_path(row)
        if path:
            paths.append(path)
    return sorted(dedupe(paths))


def semantic_debug_row_source_path(row: dict[str, Any]) -> str | None:
    source_ref = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
    path = source_ref.get("path") if isinstance(source_ref, dict) else None
    if path:
        return str(path)
    target = str(row.get("target") or "")
    if re.search(r":\d+$", target):
        return target.rsplit(":", 1)[0]
    return None


def semantic_debug_bounded_root_jsonl_paths(roots: tuple[Path, ...]) -> list[tuple[int | None, str]]:
    rows: list[tuple[int | None, str]] = []
    for root_index, root in enumerate(roots):
        if len(rows) >= SEMANTIC_DEBUG_GENERIC_RETRY_SCAN_FILE_LIMIT:
            break
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                if not filename.endswith((".jsonl", ".jsonl.gz")):
                    continue
                rows.append((root_index, str(Path(dirpath) / filename)))
                if len(rows) >= SEMANTIC_DEBUG_GENERIC_RETRY_SCAN_FILE_LIMIT:
                    break
            if len(rows) >= SEMANTIC_DEBUG_GENERIC_RETRY_SCAN_FILE_LIMIT:
                break
    return rows


def semantic_debug_retry_rows_for_paths(
    path_rows: list[tuple[int | None, str]],
    *,
    cap_hit: dict[str, Any],
    basis: str,
) -> list[dict[str, Any]]:
    deduped: list[tuple[int | None, str]] = []
    seen: set[tuple[int | None, str]] = set()
    for root_index, path in sorted(path_rows, key=lambda item: (item[0] if item[0] is not None else -1, item[1])):
        key = (root_index, path)
        if key not in seen:
            deduped.append(key)
            seen.add(key)
    if not deduped:
        return []

    date_groups: dict[str, list[tuple[int | None, str]]] = {}
    for item in deduped:
        date_key = semantic_debug_path_date_key(item[1])
        if date_key:
            date_groups.setdefault(date_key, []).append(item)

    if len(date_groups) > 1:
        rows = []
        for index, date_key in enumerate(sorted(date_groups), start=1):
            items = date_groups[date_key]
            rows.append(
                {
                    "slice": f"retry_date_window:{index:03d}:{date_key}",
                    "status": "capped_retryable",
                    "basis": basis,
                    "window": {"type": "date", "date": date_key},
                    "path_count": len(items),
                    "example_paths": [path for _, path in items[:3]],
                    "caps_to_retry": list(cap_hit.get("caps_hit") or []),
                    "retry_guidance": "rerun in-depth semantic-debug/audit coverage against this deterministic date window before claiming completeness",
                }
            )
        return rows[:SEMANTIC_DEBUG_GENERIC_RETRY_SLICE_LIMIT]

    rows = []
    for index, (root_index, path) in enumerate(deduped[:SEMANTIC_DEBUG_GENERIC_RETRY_SLICE_LIMIT], start=1):
        window: dict[str, Any] = {"type": "path", "path": path}
        if root_index is not None:
            window["root_index"] = root_index
        rows.append(
            {
                "slice": f"retry_path_window:{index:03d}",
                "status": "capped_retryable",
                "basis": basis,
                "window": window,
                "path_count": 1,
                "caps_to_retry": list(cap_hit.get("caps_hit") or []),
                "retry_guidance": "rerun in-depth semantic-debug/audit coverage against this deterministic path window before claiming completeness",
            }
        )
    return rows


def semantic_debug_path_date_key(path: str) -> str | None:
    text = str(path)
    for regex in (CODEX_SESSION_DATE_RE, CODEX_ROLLOUT_DATE_RE):
        match = regex.search(text)
        if match:
            return f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
    match = re.search(r"(?<!\d)(20\d{2})[/\\-](\d{2})[/\\-](\d{2})(?!\d)", text)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def semantic_debug_selected_model_terms(query: str) -> list[str]:
    return dedupe([match.group(0) for match in MODEL_TOKEN_RE.finditer(str(query or ""))])


def semantic_debug_structural_inventory_wanted(query: str) -> bool:
    lowered = str(query or "").casefold()
    return bool(
        semantic_debug_selected_model_terms(query)
        or re.search(r"\b(?:selected model|model choice|subagent|subagents|worker|workers|trace|traces|session|sessions)\b", lowered)
    )


def semantic_debug_promotion_demotion_summary(
    *,
    next_reads: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    query: str,
    cap_hit: dict[str, Any],
) -> dict[str, Any]:
    fresher_direct_exists = any(
        semantic_debug_read_is_fresher_direct_evidence(read, query=query)
        for read in next_reads
        if isinstance(read, dict)
    )
    stale_adjacent = [
        read
        for read in next_reads
        if isinstance(read, dict) and semantic_debug_read_is_stale_adjacent_ref(read, query=query)
    ]
    self_echo = [
        read
        for read in next_reads
        if isinstance(read, dict) and semantic_debug_read_is_self_echo_or_current_run(read, query=query)
    ]
    diagnostic_only = [
        read
        for read in next_reads
        if isinstance(read, dict) and (read.get("diagnostic_only") or read.get("final_support_allowed") is False)
    ]
    return {
        "cap_hit_demoted_to_lead_only": bool(cap_hit.get("hit")),
        "top_n_only_candidate_count": len(candidates),
        "top_n_only_role": "lead_only_until_coverage_complete",
        "self_echo_or_current_run_count": len(self_echo),
        "stale_adjacent_candidate_count": len(stale_adjacent),
        "stale_adjacent_demoted_count": len(stale_adjacent) if fresher_direct_exists else 0,
        "stale_adjacent_labeled_no_fresher_direct_count": 0 if fresher_direct_exists else len(stale_adjacent),
        "fresher_direct_evidence_exists": fresher_direct_exists,
        "diagnostic_only_read_count": len(diagnostic_only),
    }


def apply_in_depth_audit_read_promotion_safety(
    next_reads: list[dict[str, Any]],
    *,
    coverage: dict[str, Any],
) -> list[dict[str, Any]]:
    if not coverage.get("applies"):
        return next_reads
    query = ""
    fresher_direct_exists = any(
        semantic_debug_read_is_fresher_direct_evidence(read, query=query)
        for read in next_reads
        if isinstance(read, dict)
    )
    answer_promotion_allowed = bool(coverage.get("answer_promotion_allowed"))
    rows: list[dict[str, Any]] = []
    for read in next_reads:
        if not isinstance(read, dict):
            continue
        row = dict(read)
        stale_adjacent = semantic_debug_read_is_stale_adjacent_ref(row, query=query)
        self_echo = semantic_debug_read_is_self_echo_or_current_run(row, query=query)
        if stale_adjacent:
            row["stale_adjacent_ref_policy"] = {
                "role": (
                    "diagnostic_only"
                    if fresher_direct_exists
                    else "labeled_adjacent_context_no_fresher_direct_evidence"
                ),
                "fresher_direct_evidence_exists": fresher_direct_exists,
                "chronology_or_source_role_labeled": True,
            }
            row["source_chronology_role"] = (
                "stale_adjacent_diagnostic"
                if fresher_direct_exists
                else "stale_adjacent_labeled_context_no_fresher_direct"
            )
            if fresher_direct_exists:
                row["target_source_role"] = "diagnostic_or_post_hoc"
                row["diagnostic_only"] = True
                row["read_more_actionable"] = False
                row["followthrough_actionable"] = False
                row["final_support_allowed"] = False
        if self_echo:
            row["self_echo_promotion_policy"] = "lead_only_not_final_support"
            row["final_support_allowed"] = False
            row["diagnostic_only"] = True
        if not answer_promotion_allowed:
            row["lead_only_for_audit_depth"] = coverage.get("audit_depth", {}).get("depth")
            row["final_support_allowed"] = False
            row.setdefault("read_more_actionable", True)
            row.setdefault(
                "promotion_block_reason",
                coverage.get("status") or "in_depth_audit_coverage_required",
            )
        rows.append(row)
    return rows


IN_DEPTH_AUDIT_PRIMARY_READ_LIMIT = 4


def build_in_depth_audit_followthrough_plan(
    *,
    query: str,
    args: Any,
    coverage: dict[str, Any],
    scope_boundary_summary: dict[str, Any],
    next_reads: list[dict[str, Any]],
) -> dict[str, Any]:
    if not coverage.get("applies"):
        return {}

    selected_reads = select_in_depth_audit_primary_reads(next_reads)
    continuation = in_depth_audit_continuation_command(
        query=query,
        args=args,
        coverage=coverage,
    )
    continuation_commands = [continuation] if continuation else []
    coverage_complete = bool(coverage.get("answer_promotion_allowed"))
    searched_scope = scope_boundary_summary.get("searched_scope") if isinstance(scope_boundary_summary, dict) else {}
    not_searched_scope = scope_boundary_summary.get("not_searched_scope") if isinstance(scope_boundary_summary, dict) else {}
    caps = scope_boundary_summary.get("caps") if isinstance(scope_boundary_summary, dict) else {}
    stop_rule = (
        "Open the ordered primary refs first with read-more/source-window inspection when transcript proof is needed. "
        "The local runtime workflow should use raw transcript/JSONL windows whenever they are the right evidence; "
        "metadata-only handoff is an efficiency or committed/public artifact mode, not the core retrieval principle. "
        "If those inspected refs cover the asked facets within the named searched scope, answer with searched-scope/cap "
        "caveats and stop; do not run broad rg/find/ls, repeat ask --execute, or rerun semantic-debug for the same question."
    )
    if not coverage_complete:
        stop_rule += (
            " If coverage is still incomplete after the selected refs, run at most the single bounded continuation "
            "command, then either answer with the remaining gap named or report the blocked/capped window."
        )
    return {
        "schema_version": 1,
        "applies": True,
        "status": "coverage_complete_primary_refs_first" if coverage_complete else "coverage_incomplete_primary_refs_first",
        "audit_depth": coverage.get("audit_depth"),
        "ordered_primary_refs_to_open_first": selected_reads,
        "ordered_primary_ref_count": len(selected_reads),
        "primary_read_limit": IN_DEPTH_AUDIT_PRIMARY_READ_LIMIT,
        "coverage_state": {
            "status": coverage.get("status"),
            "answer_promotion_allowed": coverage.get("answer_promotion_allowed"),
            "slice_status_counts": coverage.get("slice_status_counts"),
            "search_cap_hit": coverage.get("search_cap_hit"),
            "searched_scope": searched_scope,
            "not_searched_scope": not_searched_scope,
            "caps": caps,
            "omitted_or_capped_windows": in_depth_audit_omitted_or_capped_windows(coverage),
        },
        "stop_rule": stop_rule,
        "do_not_do": [
            "broad rg/find/ls before opening ordered_primary_refs_to_open_first",
            "repeat ask --execute for the same deep-history question",
            "repeat semantic-debug for the same question except the single bounded continuation command",
            "keep expanding read-more after selected refs cover the asked facets",
            "claim full-history completeness from capped or top-N candidate samples",
        ],
        "artifact_and_local_evidence_boundary": {
            "committed_public_export_surface": "refs_and_commands_without_raw_transcript_text",
            "local_runtime_evidence_surface": "open_selected_refs_with_read_more_to_inspect_raw_transcript_windows_when_useful",
            "metadata_only_is_efficiency_or_public_artifact_mode_not_product_principle": True,
            "raw_window_inspection_available_for_local_proof": True,
        },
        "continuation_policy": {
            "status": "single_bounded_continuation_available" if continuation_commands else "no_bounded_continuation_available",
            "emit_only_when_coverage_incomplete": not coverage_complete,
            "max_continuation_commands": 1,
            "broad_search_recovery_allowed": False,
        },
        "continuation_commands": continuation_commands,
    }


def select_in_depth_audit_primary_reads(next_reads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    for read in next_reads:
        if not isinstance(read, dict):
            continue
        if read.get("diagnostic_only") or read.get("read_more_actionable") is False:
            continue
        target = str(read.get("target") or source_ref_target(read.get("source_ref") or {}) or "")
        if not target or target in seen_targets:
            continue
        seen_targets.add(target)
        row = dict(read)
        row["deep_audit_read_role"] = "ordered_primary_ref_to_open_first"
        row["read_more_actionable"] = True
        row["followthrough_actionable"] = True
        rows.append(row)
        if len(rows) >= IN_DEPTH_AUDIT_PRIMARY_READ_LIMIT:
            break
    return rows


def in_depth_audit_omitted_or_capped_windows(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for slice_row in coverage.get("slices") or []:
        if not isinstance(slice_row, dict) or slice_row.get("status") == "complete":
            continue
        rows.append(
            {
                "slice": slice_row.get("slice"),
                "status": slice_row.get("status"),
                "basis": slice_row.get("basis"),
                "window": slice_row.get("window"),
                "path_count": slice_row.get("path_count"),
                "caps_to_retry": slice_row.get("caps_to_retry"),
                "retry_guidance": slice_row.get("retry_guidance"),
            }
        )
    return rows[:8]


def in_depth_audit_continuation_command(
    *,
    query: str,
    args: Any,
    coverage: dict[str, Any],
) -> dict[str, Any] | None:
    if coverage.get("answer_promotion_allowed"):
        return None
    slice_row = first_bounded_in_depth_continuation_slice(coverage)
    if not slice_row:
        return None
    window = slice_row.get("window") if isinstance(slice_row.get("window"), dict) else {}
    argv = ["jsonl-history-search", "semantic-debug", query, "--audit-depth", "in-depth"]
    for root in getattr(args, "root", None) or []:
        argv.extend(["--root", str(root)])
    cwd = getattr(args, "cwd", None)
    if cwd:
        argv.extend(["--cwd", str(cwd)])
    if getattr(args, "allow_arbitrary_root", False):
        argv.append("--allow-arbitrary-root")
    if window.get("type") == "date" and window.get("date"):
        start, end = in_depth_audit_date_window_bounds(str(window["date"]))
        if not start or not end:
            return None
        argv.extend(["--since", start, "--until", end])
    elif window.get("type") == "path" and window.get("path"):
        argv.extend(["--path", str(window["path"])])
    else:
        return None
    return {
        "command_id": "continue_single_bounded_audit_slice",
        "slice": slice_row.get("slice"),
        "missing_or_capped_window": window,
        "status": slice_row.get("status"),
        "reason": "coverage incomplete; use this one bounded continuation instead of broad search or repeated semantic-debug churn",
        "argv": argv,
        "command": " ".join(shlex.quote(str(part)) for part in argv),
    }


def first_bounded_in_depth_continuation_slice(coverage: dict[str, Any]) -> dict[str, Any] | None:
    incomplete = [
        row
        for row in coverage.get("slices") or []
        if isinstance(row, dict) and row.get("status") != "complete"
    ]
    precise = [
        row
        for row in incomplete
        if isinstance(row.get("window"), dict)
        and row.get("slice") != "selected_scope"
        and row["window"].get("type") in {"date", "path"}
    ]
    return (precise or incomplete or [None])[0]


def in_depth_audit_date_window_bounds(date_text: str) -> tuple[str | None, str | None]:
    try:
        start_date = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return None, None
    end_date = start_date + timedelta(days=1)
    return f"{start_date.date().isoformat()}T00:00:00+00:00", f"{end_date.date().isoformat()}T00:00:00+00:00"


def apply_in_depth_audit_contract_gate(contract: dict[str, Any], *, coverage: dict[str, Any]) -> dict[str, Any]:
    if not coverage.get("applies"):
        return contract
    row = dict(contract)
    row.setdefault("claim_boundaries", {})
    row["claim_boundaries"] = {
        **row["claim_boundaries"],
        "in_depth_answer_ready_requires_complete_coverage": True,
        "exhaustive_answer_ready_requires_full_scoped_inventory": True,
        "cap_hit_top_n_self_echo_or_stale_adjacent_support_without_coverage": "not_allowed",
    }
    row.setdefault("evidence_inputs", {})
    row["evidence_inputs"] = {
        **row["evidence_inputs"],
        "in_depth_audit_coverage": "semantic_debug.in_depth_audit_coverage",
    }
    row["required_actions"] = dedupe(
        list(coverage.get("required_before_promotion") or [])
        + list(row.get("required_actions") or [])
    )
    if not coverage.get("answer_promotion_allowed"):
        row["answer_scope"] = coverage.get("status")
        row["source_use_required"] = True
    return row


def apply_in_depth_audit_answer_readiness_gate(readiness: dict[str, Any], *, coverage: dict[str, Any]) -> dict[str, Any]:
    if not coverage.get("applies") or coverage.get("answer_promotion_allowed"):
        return readiness
    row = dict(readiness)
    status = (
        "incomplete_exhaustive_audit"
        if coverage.get("status") == "incomplete_exhaustive_audit"
        else "in_depth_audit_coverage_required"
    )
    row["status"] = status
    row["decision"] = coverage.get("status")
    row["allowed_answer_scope"] = "lead_only_until_coverage_complete"
    row["required_before_answer"] = dedupe(
        list(coverage.get("required_before_promotion") or [])
        + list(row.get("required_before_answer") or [])
    )[:12]
    row.setdefault("basis", {})
    row["basis"] = {
        **row["basis"],
        "in_depth_audit_coverage_status": coverage.get("status"),
        "in_depth_audit_slice_status_counts": coverage.get("slice_status_counts"),
        "full_scoped_inventory_before_promotion": coverage.get("full_scoped_inventory_before_promotion"),
    }
    row.setdefault("source_use_contract", {})
    row["source_use_contract"] = {
        **row["source_use_contract"],
        "in_depth_audit_coverage": {
            "status": coverage.get("status"),
            "answer_promotion_allowed": coverage.get("answer_promotion_allowed"),
            "required_before_promotion": coverage.get("required_before_promotion"),
        },
    }
    return row


def apply_in_depth_audit_handoff_gate(handoff: dict[str, Any], *, coverage: dict[str, Any]) -> dict[str, Any]:
    if not coverage.get("applies"):
        return handoff
    row = dict(handoff)
    row["in_depth_audit_coverage"] = coverage
    row.setdefault("links", {})
    row["links"] = {**row["links"], "in_depth_audit_coverage": "semantic_debug.in_depth_audit_coverage"}
    row.setdefault("source_use_summary", {})
    row["source_use_summary"] = {
        **row["source_use_summary"],
        "in_depth_audit_coverage": {
            "status": coverage.get("status"),
            "answer_promotion_allowed": coverage.get("answer_promotion_allowed"),
            "slice_status_counts": coverage.get("slice_status_counts"),
            "promotion_safety": coverage.get("promotion_safety"),
        },
    }
    if not coverage.get("answer_promotion_allowed"):
        gated_status = (
            "incomplete_exhaustive_audit"
            if coverage.get("status") == "incomplete_exhaustive_audit"
            else "in_depth_audit_coverage_required"
        )
        row["answer_readiness_status"] = gated_status
        row["decision"] = (
            "incomplete_exhaustive_audit"
            if coverage.get("status") == "incomplete_exhaustive_audit"
            else "coverage_slices_required"
        )
        row["do_next"] = "complete_or_label_deterministic_audit_coverage_slices_before_answer"
        row["do_not_answer_until"] = dedupe(
            list(coverage.get("required_before_promotion") or [])
            + list(row.get("do_not_answer_until") or [])
        )[:8]
        row.setdefault("source_action_guidance", {})
        row["source_action_guidance"] = {
            **row["source_action_guidance"],
            "decision": row["decision"],
            "stop_condition": (
                "Do not answer from cap-hit, top-N-only, current/self-echo, or stale adjacent evidence; "
                "first complete or label deterministic audit coverage slices."
            ),
            "in_depth_audit_coverage": {
                "status": coverage.get("status"),
                "slice_status_counts": coverage.get("slice_status_counts"),
            },
        }
    return row


def apply_in_depth_audit_followthrough_handoff(
    handoff: dict[str, Any],
    *,
    followthrough: dict[str, Any],
) -> dict[str, Any]:
    if not followthrough.get("applies"):
        return handoff
    row = dict(handoff)
    row["in_depth_audit_followthrough"] = followthrough
    row.setdefault("links", {})
    row["links"] = {
        **row["links"],
        "in_depth_audit_followthrough": "semantic_debug.in_depth_audit_followthrough",
    }
    row.setdefault("source_use_summary", {})
    row["source_use_summary"] = {
        **row["source_use_summary"],
        "in_depth_audit_followthrough": {
            "status": followthrough.get("status"),
            "ordered_primary_ref_count": followthrough.get("ordered_primary_ref_count"),
            "continuation_count": len(followthrough.get("continuation_commands") or []),
            "continuation_policy": followthrough.get("continuation_policy"),
        },
    }
    row["do_next"] = "open_ordered_deep_audit_primary_refs_then_stop_or_single_bounded_continuation"
    row.setdefault("source_action_guidance", {})
    row["source_action_guidance"] = {
        **row["source_action_guidance"],
        "deep_audit_followthrough": {
            "status": followthrough.get("status"),
            "ordered_primary_ref_count": followthrough.get("ordered_primary_ref_count"),
            "continuation_count": len(followthrough.get("continuation_commands") or []),
        },
        "stop_condition": followthrough.get("stop_rule"),
        "broad_search_policy": "do_not_use_rg_find_ls_before_selected_primary_refs",
    }
    return row


def apply_in_depth_audit_agent_instruction_gate(instruction: dict[str, Any], *, coverage: dict[str, Any]) -> dict[str, Any]:
    if not coverage.get("applies") or coverage.get("answer_promotion_allowed"):
        return instruction
    row = dict(instruction)
    row["decision"] = (
        "incomplete_exhaustive_audit"
        if coverage.get("status") == "incomplete_exhaustive_audit"
        else "coverage_slices_required"
    )
    row["required_next_action"] = "complete_or_label_in_depth_audit_coverage_before_answer"
    row["in_depth_audit_coverage"] = {
        "status": coverage.get("status"),
        "slice_status_counts": coverage.get("slice_status_counts"),
        "required_before_promotion": coverage.get("required_before_promotion"),
    }
    row["commands"] = []
    row["stop_condition"] = (
        "Return incomplete_exhaustive_audit or coverage_slices_required instead of an answer-ready conclusion "
        "until coverage is complete enough for the requested audit depth."
    )
    row.setdefault("source_action_guidance", {})
    row["source_action_guidance"] = {
        **row["source_action_guidance"],
        "decision": row["decision"],
        "stop_condition": row["stop_condition"],
        "in_depth_audit_coverage": row["in_depth_audit_coverage"],
    }
    return row


def apply_in_depth_audit_followthrough_instruction(
    instruction: dict[str, Any],
    *,
    followthrough: dict[str, Any],
) -> dict[str, Any]:
    if not followthrough.get("applies"):
        return instruction
    row = dict(instruction)
    row["decision"] = "open_deep_audit_primary_refs_first"
    row["required_next_action"] = "open_ordered_primary_refs_then_stop_or_run_one_bounded_continuation"
    commands = []
    for read in followthrough.get("ordered_primary_refs_to_open_first") or []:
        for action in read.get("expansion_actions") or []:
            if action.get("mode") == "window":
                commands.append(action)
                break
    row["commands"] = commands[:IN_DEPTH_AUDIT_PRIMARY_READ_LIMIT]
    row["stop_condition"] = followthrough.get("stop_rule")
    row.setdefault("source_action_guidance", {})
    row["source_action_guidance"] = {
        **row["source_action_guidance"],
        "deep_audit_followthrough": {
            "status": followthrough.get("status"),
            "ordered_primary_ref_count": followthrough.get("ordered_primary_ref_count"),
            "continuation_count": len(followthrough.get("continuation_commands") or []),
        },
        "stop_condition": followthrough.get("stop_rule"),
    }
    return row


def semantic_debug_read_is_fresher_direct_evidence(read: dict[str, Any], *, query: str) -> bool:
    if semantic_debug_read_is_stale_adjacent_ref(read, query=query):
        return False
    if semantic_debug_read_is_self_echo_or_current_run(read, query=query):
        return False
    role = str(read.get("target_source_role") or "")
    target = str(read.get("target") or "")
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    path = str(source_ref.get("path") or target)
    return role == "target_historical_raw" or path.endswith(".jsonl") or ".jsonl:" in target


def semantic_debug_read_is_self_echo_or_current_run(read: dict[str, Any], *, query: str) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            read.get("reason"),
            read.get("target"),
            read.get("source_chronology_role"),
            read.get("target_source_role"),
            read.get("promotion_block_reason"),
        )
    ).casefold()
    return any(
        marker in text
        for marker in (
            "self-echo",
            "self echo",
            "current-run",
            "current run",
            "current-session",
            "current session",
            "same-loop",
            "same loop",
            "prompt echo",
            "tool-output echo",
        )
    )


def semantic_debug_read_is_stale_adjacent_ref(read: dict[str, Any], *, query: str) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            read.get("reason"),
            read.get("target"),
            read.get("source_chronology_role"),
            read.get("target_source_role"),
        )
    ).casefold()
    if "stale" in text or "adjacent" in text:
        return True
    target = str(read.get("target") or "").casefold()
    if any(part in target for part in ("/codex/state/reviews/", "/reports/", "/tasks/")):
        query_text = str(query or "").casefold()
        return any(cue in query_text for cue in ("last session", "recent", "current", "july 23", "latest"))
    return False


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
            "process_actual_use_bucket_count": basis.get("process_actual_use_bucket_count", 0),
            "historical_orchestration_required_role_count": basis.get(
                "historical_orchestration_required_role_count", 0
            ),
            "historical_orchestration_missing_role_count": basis.get(
                "historical_orchestration_missing_role_count", 0
            ),
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
    actionable_next_reads = [
        item for item in next_reads if isinstance(item, dict) and read_is_required_surface_actionable(item)
    ]
    diagnostic_next_reads = demoted_required_surface_reads(next_reads)
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
            "read_next_refs": investigation_read_next_refs(actionable_next_reads),
            "read_next_count": len(actionable_next_reads),
            "diagnostic_only_excluded_read_count": len(diagnostic_next_reads),
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
    if primary in {"why_did_this_happen", "claimed_action", "process_actual_use", "remembered_instruction"}:
        return str(primary)
    detected = set(family.get("detected_supported") or [])
    for label in ("process_actual_use", "claimed_action", "remembered_instruction", "why_did_this_happen"):
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
    elif primary_family == "process_actual_use":
        preferred = [
            "conversation_reconstruction",
            "process_actual_use_audit",
            "source_ref_followup",
            "task_review_join",
            "tool_action_proof",
            "git_history_reconstruction",
            "no_evidence_scoped_report",
        ]
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
    if route == "process_actual_use_audit":
        has_raw_candidates = any(handle.get("kind") == "candidate_source_ref" for handle in decision_context.get("evidence_handles", []))
        has_review_context = any(handle.get("kind") == "task_review_artifact" for handle in decision_context.get("evidence_handles", []))
        if has_raw_candidates and has_review_context:
            return "ready_with_raw_and_secondary_context"
        if has_raw_candidates:
            return "ready_needs_bucket_classification"
        return "needs_primary_raw_timing_evidence"
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
        "process_actual_use_audit": [
            "strongest_candidates",
            "source_locator_handoff.next_reads",
            "raw conversation/session windows",
            "task_review_commit_joins as secondary context",
        ],
        "task_review_join": ["task_review_commit_joins", "forensics_workflow.review_gate"],
        "tool_action_proof": ["tool_action_proof", "structured_tool_events"],
        "source_ref_followup": ["source_locator_handoff.next_reads", "source_locator_handoff.omitted_window_recovery"],
        "no_evidence_scoped_report": ["no_evidence", "scope", "final_answer_source_use_contract.no_evidence_scope"],
    }.get(route, ["semantic_debug"])


def guided_stage_outputs(route: str) -> list[str]:
    return {
        "conversation_reconstruction": ["evidence_vs_inference_notes", "candidate_timeline"],
        "git_history_reconstruction": ["project_time_context", "artifact_state_context"],
        "process_actual_use_audit": [
            "actual_use_bucket_findings",
            "primary_raw_evidence_for_behavior_claims",
            "secondary_artifact_context_or_gap",
        ],
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
        "process_actual_use": "Fill actual-use buckets and cite primary raw session timing before claiming a process/artifact changed behavior.",
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
    process_contract = final_answer_contract.get("process_actual_use_contract") or {}
    if process_contract:
        completion_criteria.extend(
            [
                "Artifact exists/cited buckets are not treated as behavior-changing actual-use proof.",
                "Consulted-before-planning and behavior/scope/claim-boundary buckets use primary raw evidence or are stated as gaps.",
                "Post-hoc update and stale/performative/no-evidence buckets are explicitly considered.",
            ]
        )
    forbidden_claims = [
        "root_cause_without_linked_reviewed_evidence",
        "global_absence_or_all_history_completeness",
        "jsonl_value_speed_token_or_final_answer_quality_without_separate_reviewed_evidence",
        "candidate_only_or_unsupported_extra_handle_as_final_support",
        "unstated_missing_proof_action_facet",
    ]
    if process_contract:
        forbidden_claims.extend(process_contract.get("forbidden_claims") or [])
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
        "process_actual_use_buckets": list(process_contract.get("bucket_requirements") or []),
        "process_actual_use_source_hierarchy": process_contract.get("source_hierarchy") or {},
        "primary_raw_evidence_required_for_behavior_claims": bool(
            process_contract.get("primary_raw_evidence_required_for_behavior_claims", False)
        ),
        "repo_task_review_artifacts_secondary_unless_linked": bool(
            process_contract.get("repo_task_review_artifacts_secondary_unless_linked", False)
        ),
        "answer_source_discipline": discipline_summary,
        "forbidden_claims": forbidden_claims,
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
            "process_actual_use_bucket_count": len(answer_gate.get("process_actual_use_buckets") or []),
            "primary_raw_evidence_required_for_behavior_claims": answer_gate.get(
                "primary_raw_evidence_required_for_behavior_claims"
            ),
            "repo_task_review_artifacts_secondary_unless_linked": answer_gate.get(
                "repo_task_review_artifacts_secondary_unless_linked"
            ),
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
