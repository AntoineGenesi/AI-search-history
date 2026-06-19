from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .adapters import AdapterReadError, iter_records_for_file


INLINE_CANDIDATE_LIMIT = 12
INLINE_CONTEXT_BYTE_LIMIT = 24_000
SOURCE_WINDOW_LIMIT = 5
SOURCE_WINDOW_REASON_CODE_LIMIT = 8
SOURCE_WINDOW_OMITTED_FACT_LIMIT = 20
SOURCE_LOCATOR_TOP_CANDIDATES_LIMIT = 5
COMPACT_PROVENANCE_CANDIDATE_LIMIT = 8
COMPACT_PROVENANCE_SOURCE_REF_LIMIT = 12
COMPACT_PROVENANCE_NEXT_READ_LIMIT = 8
EVIDENCE_PACKET_LIMIT = 5
EVIDENCE_PACKET_CHILD_LIMIT = 6
EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT = 8
EVIDENCE_PACKET_MATERIALIZED_CONTEXT_TOTAL_CHAR_LIMIT = 12_000
EVIDENCE_PACKET_MATERIALIZED_CONTEXT_PACKET_CHAR_LIMIT = 4_000
EVIDENCE_PACKET_MATERIALIZED_CONTEXT_RECORD_CHAR_LIMIT = 1_000
ANSWERABILITY_SUPPORTED_IN_SEARCHED_SCOPE = "supported_in_searched_scope"
ANSWERABILITY_DEFERRED_SOURCE_NEEDED = "deferred_source_needed"
ANSWERABILITY_SEARCHED_SCOPE_NO_EVIDENCE = "searched_scope_no_evidence"
ANSWERABILITY_INSUFFICIENT_GROUNDING = "insufficient_grounding"


@dataclass(frozen=True)
class CandidateRow:
    rank: int
    candidate_id: str
    source_ref: dict
    path: str
    line: int
    timestamp: str | None
    timestamp_utc: str | None
    role: str | None
    session: str | None
    cwd: str | None
    snippet: str
    score: float
    confidence: str
    matched_terms: list[str]
    stages: list[str]
    primary_stage: str
    reasons: list[str]
    reason_codes: list[str]
    artifacts: list[str]
    tasks: list[str]

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "candidate_id": self.candidate_id,
            "source_ref": self.source_ref,
            "path": self.path,
            "line": self.line,
            "timestamp": self.timestamp,
            "timestamp_utc": self.timestamp_utc,
            "role": self.role,
            "session": self.session,
            "cwd": self.cwd,
            "snippet": self.snippet,
            "score": self.score,
            "confidence": self.confidence,
            "matched_terms": self.matched_terms,
            "stages": self.stages,
            "stage": self.primary_stage,
            "reasons": self.reasons,
            "reason_codes": self.reason_codes,
            "artifacts": self.artifacts,
            "tasks": self.tasks,
        }


def build_agent_find_payload(
    *,
    source_plan: dict,
    search_result: dict,
    manifest_report: dict | None = None,
    output_format: str = "json",
    report_output: str | None = None,
    compact_provenance_handoff: bool = False,
    evidence_context_window: int = 0,
) -> dict[str, Any]:
    expansion_action_context = expansion_action_context_from_source_plan(source_plan)
    candidates = build_candidate_rows(search_result)
    timeline = build_timeline(candidates)
    candidate_dicts = [candidate.to_dict() for candidate in candidates]
    context_bytes = len(str(candidate_dicts).encode("utf-8"))
    raw_baseline_bytes = search_result.get("searched_bytes", 0)
    estimated_reduction_ratio = reduction_ratio(context_bytes, raw_baseline_bytes)
    status = "found" if candidates else "no_evidence"
    no_evidence_report = build_no_evidence_report(
        status=status,
        source_plan=source_plan,
        search_result=search_result,
        manifest_report=manifest_report,
    )
    agent_find = {
        "schema_version": 1,
        "status": status,
        "query": source_plan["query"],
        "command_surface": command_surface(),
        "plan_sources": build_plan_sources(
            source_plan,
            search_result,
            manifest_report,
            include_source_scope=status == "no_evidence",
        ),
        "broaden_query": build_broaden_query(source_plan["query"]),
        "find_candidates": candidate_dicts[:INLINE_CANDIDATE_LIMIT],
        "timeline": [candidate.to_dict() for candidate in timeline[:INLINE_CANDIDATE_LIMIT]],
        "cluster_candidates": build_cluster_candidates(search_result),
        "evidence_bundles": build_evidence_bundles(search_result),
        "no_evidence_report": no_evidence_report,
        "next_search_suggestions": next_search_suggestions(
            status=status,
            source_plan=source_plan,
            search_result=search_result,
            candidates=candidates,
        ),
        "report_policy": report_policy(
            candidate_count=len(candidates),
            context_bytes=context_bytes,
            raw_baseline_bytes=raw_baseline_bytes,
            estimated_reduction_ratio=estimated_reduction_ratio,
            output_format=output_format,
            report_output=report_output,
        ),
        "context_budget": {
            "payload_bytes": context_bytes,
            "estimated_tokens": estimate_tokens(context_bytes),
            "raw_baseline_bytes": raw_baseline_bytes,
            "estimated_reduction_ratio": estimated_reduction_ratio,
            "report_recommended": context_bytes > INLINE_CONTEXT_BYTE_LIMIT or len(candidates) > INLINE_CANDIDATE_LIMIT,
            "report_written": report_output,
        },
        "token_efficiency": {
            "inline_candidate_limit": INLINE_CANDIDATE_LIMIT,
            "candidate_count": len(candidates),
            "inline_context_bytes": context_bytes,
            "searched_files": search_result.get("searched_files", 0),
            "searched_bytes": search_result.get("searched_bytes", 0),
        },
    }
    if search_result.get("retrieval_diagnostics"):
        agent_find["retrieval_diagnostics"] = search_result["retrieval_diagnostics"]
        retrieval_pressure = build_retrieval_pressure_summary(search_result)
        if retrieval_pressure:
            agent_find["retrieval_pressure"] = retrieval_pressure
    handoff = build_source_locator_handoff(
        source_plan=source_plan,
        search_result=search_result,
        candidates=candidate_dicts,
        next_reads=next_reads_from_candidates(
            candidates,
            expansion_action_context=expansion_action_context,
            include_context_actions=compact_provenance_handoff,
        ),
        source_family="local_ai_conversation_jsonl",
        question_family={
            "primary": "agent_find",
        },
        product_path="find",
        no_evidence_report=no_evidence_report,
        retrieval_pressure=agent_find.get("retrieval_pressure"),
        expansion_action_context=expansion_action_context,
        include_context_actions=compact_provenance_handoff,
        evidence_context_window=evidence_context_window,
    )
    agent_find["source_locator_handoff"] = handoff
    if handoff.get("stage_diagnostics") and agent_find.get("retrieval_pressure"):
        agent_find["retrieval_pressure"]["stage_diagnostics"] = handoff["stage_diagnostics"]
    if compact_provenance_handoff:
        agent_find["compact_provenance_handoff"] = build_compact_provenance_handoff(
            status=status,
            command_surface="find",
            candidates=candidate_dicts,
            source_locator_handoff=handoff,
            next_reads=handoff["next_reads"],
            no_evidence=agent_find["no_evidence_report"],
            retrieval_pressure=agent_find.get("retrieval_pressure"),
        )
        compact_stage_diagnostics = build_stage_diagnostics(
            product_path="find",
            query_plan=handoff.get("query_plan"),
            search_result=search_result,
            retrieval_pressure=agent_find.get("retrieval_pressure"),
            source_window_packaging=handoff.get("source_window_packaging"),
            evidence_packet_summary=handoff.get("evidence_packet_packaging"),
            compact_context_budget=agent_find["compact_provenance_handoff"].get("context_budget"),
            no_evidence_report=no_evidence_report,
            compact_handoff_requested=True,
        )
        if compact_stage_diagnostics:
            handoff["stage_diagnostics"] = compact_stage_diagnostics
            if agent_find.get("retrieval_pressure"):
                agent_find["retrieval_pressure"]["stage_diagnostics"] = compact_stage_diagnostics
                agent_find["compact_provenance_handoff"]["retrieval_pressure"] = agent_find["retrieval_pressure"]
            agent_find["compact_provenance_handoff"]["stage_diagnostics"] = compact_stage_diagnostics
    return {"agent_find": agent_find}


def command_surface() -> dict[str, str]:
    return {
        "read-more": "Bounded provenance expansion for an exact source_ref: record, window, before/next, or source/session/day neighborhood without rerunning broad search.",
        "source-locator-handoff": "Inspect this before broad search: stable handoff with source family, selected/rejected roots, caps, blind spots, candidate ordering, next reads, no-evidence meaning, and privacy/claim boundaries.",
        "plan-sources": "Explains selected/rejected roots, caps, ordering, wrong-root warnings, and optional manifest shortlist metadata.",
        "find-candidates": "Focused fuzzy/typo/synonym/paraphrase candidate recovery with compact source refs, timestamps, roles, snippets, stages, reasons, and confidence.",
        "broaden-query": "Shows exact, normalized, typo/fuzzy, synonym/paraphrase, artifact/path, cwd/project, date-window, role, and local semantic families.",
        "timeline": "Sorts candidate rows by parsed message timestamp with deterministic path/line tie-breaking.",
        "cluster-candidates": "Groups candidates by task, artifact, preference, session, or path for compact AI handoff.",
        "no-evidence-report": "Reports searched-scope-only no-evidence, not all-history/global/permanent absence; includes excluded scope, weak candidates, warnings, and next safe searches.",
    }


def build_compact_provenance_handoff(
    *,
    status: str,
    command_surface: str,
    candidates: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    next_reads: list[dict[str, Any]],
    no_evidence: dict[str, Any],
    retrieval_pressure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an opt-in provenance map from already-emitted agent-facing fields."""
    compact_candidates = compact_provenance_candidates(candidates)
    source_ref_candidates = compact_provenance_source_ref_candidates(
        compact_candidates,
        source_locator_handoff.get("candidate_ordering", {}).get("top_candidates", []),
        next_reads,
    )
    source_refs = source_ref_candidates[:COMPACT_PROVENANCE_SOURCE_REF_LIMIT]
    source_windows = list(source_locator_handoff.get("source_windows") or [])[:SOURCE_WINDOW_LIMIT]
    evidence_packets = list(source_locator_handoff.get("evidence_packets") or [])[:EVIDENCE_PACKET_LIMIT]
    evidence_packet_packaging = source_locator_handoff.get("evidence_packet_packaging") or {}
    evidence_packet_text_included = bool(evidence_packet_packaging.get("raw_text_included", False))
    compact_next_reads = list(next_reads or [])[:COMPACT_PROVENANCE_NEXT_READ_LIMIT]
    answerability = build_compact_answerability(
        status=status,
        candidates=compact_candidates,
        source_locator_handoff=source_locator_handoff,
        no_evidence=no_evidence,
    )
    source_selection_changed = compact_handoff_source_selection_changed(
        source_locator_handoff=source_locator_handoff,
        retrieval_pressure=retrieval_pressure,
    )
    output_budget = build_compact_handoff_output_budget(
        candidate_count=len(candidates),
        candidates_included=len(compact_candidates),
        source_ref_count_considered=len(source_ref_candidates),
        source_refs_included=len(source_refs),
        source_windows_included=len(source_windows),
        evidence_packet_count_considered=len(source_locator_handoff.get("evidence_packets") or []),
        evidence_packets_included=len(evidence_packets),
        next_read_count_considered=len(next_reads or []),
        next_reads_included=len(compact_next_reads),
        evidence_packet_text_included=evidence_packet_text_included,
    )
    packet: dict[str, Any] = {
        "schema_version": 1,
        "status": "no_evidence" if status == "no_evidence" else "candidates_found",
        "command_surface": command_surface,
        "derived_from": [
            "source_locator_handoff",
            "find_candidates" if command_surface == "find" else "strongest_candidates",
            "next_reads",
            "no_evidence",
        ],
        "candidate_count": len(candidates),
        "answerability_status": answerability["support_class"],
        "evidence_sufficiency": answerability,
        "candidates": compact_candidates,
        "source_refs": source_refs,
        "source_windows": source_windows,
        "evidence_packets": evidence_packets,
        "source_window_packaging": source_locator_handoff.get("source_window_packaging", {}),
        "next_reads": compact_next_reads,
        "no_evidence": compact_provenance_no_evidence(no_evidence, source_locator_handoff),
        "output_budget": output_budget,
        "scope": {
            "source_family": source_locator_handoff.get("source_family", {}),
            "selected_roots": source_locator_handoff.get("selected_roots", []),
            "rejected_roots": source_locator_handoff.get("rejected_roots", []),
            "caps": source_locator_handoff.get("caps", {}),
            "blind_spots": source_locator_handoff.get("blind_spots", []),
            "not_searched_scope": source_locator_handoff.get("not_searched_scope", {}),
        },
        "privacy_boundaries": {
            **source_locator_handoff.get("privacy_boundaries", {}),
            "raw_private_text_or_snippets_included": evidence_packet_text_included,
            "source_window_text_included": False,
            "evidence_packet_text_included": evidence_packet_text_included,
            "source_refs_may_point_to_local_transcripts": bool(source_refs),
        },
        "claim_boundaries": {
            **source_locator_handoff.get("claim_boundaries", {}),
            "compact_handoff_is_opt_in": True,
            "default_behavior_changed": False,
            "scanner_ranking_or_source_selection_changed": bool(
                (source_locator_handoff.get("claim_boundaries", {}) or {}).get(
                    "scanner_ranking_or_source_selection_changed", False
                )
                or source_selection_changed
            ),
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "product_value_claim_supported": False,
            "default_promotion_supported": False,
        },
    }
    if retrieval_pressure:
        packet["derived_from"].append("retrieval_pressure")
        packet["retrieval_pressure"] = retrieval_pressure
    packet_bytes = len(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    packet["context_budget"] = {
        "payload_bytes": packet_bytes,
        "estimated_tokens": estimate_tokens(packet_bytes),
        "measurement_basis": "compact_provenance_handoff_packet_only",
        "output_budget": output_budget,
    }
    return packet


def compact_handoff_source_selection_changed(
    *,
    source_locator_handoff: dict[str, Any],
    retrieval_pressure: dict[str, Any] | None,
) -> bool:
    pressure = retrieval_pressure if isinstance(retrieval_pressure, dict) else {}
    grouping = pressure.get("fielded_source_grouping") if isinstance(pressure.get("fielded_source_grouping"), dict) else {}
    if bool(grouping.get("enabled")) and str(grouping.get("strategy") or "disabled") != "disabled":
        return True
    for stage in (source_locator_handoff.get("stage_diagnostics") or {}).get("stages") or []:
        if not isinstance(stage, dict) or stage.get("stage") != "source_grouping_diversity":
            continue
        counters = stage.get("counters") if isinstance(stage.get("counters"), dict) else {}
        if (
            int(counters.get("group_interleaved_rows_selected") or 0) > 0
            or int(counters.get("collapsed_adjacent_run_count") or 0) > 0
            or int(counters.get("suppressed_adjacent_row_count") or 0) > 0
        ):
            return True
    return False


STAGE_DIAGNOSTIC_LABELS = [
    "query_planning",
    "source_enumeration",
    "fielded_index_search",
    "candidate_materialization",
    "source_grouping_diversity",
    "source_window_packaging",
    "compact_handoff_output",
    "searched_scope_no_evidence",
]


def build_stage_diagnostics(
    *,
    product_path: str,
    query_plan: dict[str, Any] | None,
    search_result: dict[str, Any],
    retrieval_pressure: dict[str, Any] | None,
    source_window_packaging: dict[str, Any] | None,
    evidence_packet_summary: dict[str, Any] | None = None,
    compact_context_budget: dict[str, Any] | None = None,
    no_evidence_report: dict[str, Any] | None = None,
    compact_handoff_requested: bool = False,
) -> dict[str, Any] | None:
    diagnostics = search_result.get("retrieval_diagnostics") or {}
    retrieval_mode = diagnostics.get("retrieval_mode") or search_result.get("retrieval_mode")
    if retrieval_mode != "fielded-bm25":
        return None

    pressure = retrieval_pressure or {}
    query_plan = query_plan or {}
    source_window_packaging = source_window_packaging or {}
    evidence_packet_summary = evidence_packet_summary or {}
    no_evidence_report = no_evidence_report or {}
    compact_output_budget = (compact_context_budget or {}).get("output_budget") or {}
    compact_output_counts = compact_output_budget.get("counts") or {}
    compact_output_limits = compact_output_budget.get("limits") or {}
    fielded = pressure.get("fielded_bm25") or {}
    index_summary = fielded.get("index_summary") if isinstance(fielded.get("index_summary"), dict) else {}
    query_streams = fielded.get("query_streams") or {}
    child_query_plan = pressure.get("fielded_child_query_plan") or fielded.get("child_query_plan") or {}
    evidence_channels = pressure.get("fielded_evidence_channels") or fielded.get("evidence_channels") or {}
    context_neighborhood = pressure.get("fielded_context_neighborhood") or fielded.get("context_neighborhood") or {}
    source_enumeration = pressure.get("shared_source_enumeration") or {}
    source_catalog = sanitize_source_catalog(pressure.get("source_catalog") or diagnostics.get("source_catalog") or {})
    source_plan = sanitize_source_plan(pressure.get("source_plan") or diagnostics.get("source_plan") or {})
    grouping = pressure.get("fielded_source_grouping") or {}
    cap_labels = list(pressure.get("cap_labels") or diagnostics.get("cap_labels") or [])
    candidate_count = int(pressure.get("fielded_bm25", {}).get("candidate_count") or 0)
    no_evidence_needed = bool(no_evidence_report.get("needed"))

    stages = [
        stage_diagnostic(
            "query_planning",
            "available" if query_plan else "not_run",
            "anchor_terms_empty" if query_plan and not query_plan.get("anchor_terms") else "none" if query_plan else "query_plan_missing",
            {
                "anchor_term_count": len(query_plan.get("anchor_terms") or []),
                "source_window_need_count": len(query_plan.get("source_window_needs") or []),
                "has_primary_intent": bool(query_plan.get("primary_intent")),
                "query_plan_mode": str(child_query_plan.get("query_plan_mode") or "auto"),
                "supported_child_channel_intent": child_query_plan.get("supported_child_channel_intent"),
                "child_channel_count": int(child_query_plan.get("child_channel_count") or 0),
                "child_channel_labels": list(child_query_plan.get("child_channel_labels") or []),
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
            },
        ),
        stage_diagnostic(
            "source_enumeration",
            "capped" if cap_labels else "available" if int(source_enumeration.get("file_count") or 0) else "empty",
            "none",
            {
                "file_count": int(source_enumeration.get("file_count") or 0),
                "byte_count": int(source_enumeration.get("byte_count") or 0),
                "consumed_by_lane_count": len(source_enumeration.get("consumed_by_lanes") or []),
                "source_catalog": source_catalog,
                "source_plan": source_plan,
            },
        ),
        stage_diagnostic(
            "fielded_index_search",
            "capped" if cap_labels else "available" if fielded.get("status") else "not_run",
            "none",
            {
                "candidate_count": candidate_count,
                "candidate_rows": int(fielded.get("candidate_rows") or 0),
                "candidate_row_limit": int(fielded.get("candidate_row_limit") or 0),
                "candidate_row_pool_limit": int(fielded.get("candidate_row_pool_limit") or 0),
                "candidate_rows_generated": int(fielded.get("candidate_rows_generated") or 0),
                "candidate_rows_from_query_streams": int(fielded.get("candidate_rows_from_query_streams") or 0),
                "records_considered": int(fielded.get("records_considered") or 0),
                "searched_files": int(fielded.get("searched_files") or 0),
                "searched_bytes": int(fielded.get("searched_bytes") or 0),
                "planned_file_enumeration_reused": bool(
                    index_summary.get("planned_file_enumeration_reused", False)
                ),
                "persistent_index_enabled": bool(index_summary.get("persistent_index_enabled", False)),
                "index_schema_version": str(index_summary.get("index_schema_version") or ""),
                "parser_version": str(index_summary.get("parser_version") or ""),
                "chunking_version": str(index_summary.get("chunking_version") or ""),
                "sources_reused": int(index_summary.get("sources_reused") or 0),
                "sources_reindexed": int(index_summary.get("sources_reindexed") or 0),
                "sources_invalidated": int(index_summary.get("sources_invalidated") or 0),
                "sources_deleted": int(index_summary.get("sources_deleted") or 0),
                "segments_loaded": int(index_summary.get("segments_loaded") or 0),
                "segments_written": int(index_summary.get("segments_written") or 0),
                "warm_query_reused_index": bool(index_summary.get("warm_query_reused_index", False)),
                "content_fingerprint_validation_enabled": bool(
                    index_summary.get("content_fingerprint_validation_enabled", False)
                ),
                "content_fingerprint_files": int(index_summary.get("content_fingerprint_files") or 0),
                "content_fingerprint_bytes": int(index_summary.get("content_fingerprint_bytes") or 0),
                "content_fingerprint_strategy": str(index_summary.get("content_fingerprint_strategy") or ""),
                "persistent_reuse_claim": str(
                    index_summary.get("persistent_reuse_claim")
                    or "local_work_reduction_only_not_token_savings_or_final_answer_quality"
                ),
                "query_stream_count": int(query_streams.get("stream_count") or 0),
                "query_plan_mode": str(child_query_plan.get("query_plan_mode") or "auto"),
                "child_channel_count": int(child_query_plan.get("child_channel_count") or 0),
                "child_channel_labels": list(child_query_plan.get("child_channel_labels") or []),
                "stream_hit_limit_count": int(query_streams.get("stream_hit_limit_count") or 0),
                "saturated_stream_count": int(query_streams.get("saturated_stream_count") or 0),
                "row_count_before_dedupe": int(query_streams.get("row_count_before_dedupe") or 0),
                "row_count_after_dedupe": int(query_streams.get("row_count_after_dedupe") or 0),
                "rows_dropped_by_stream_cap_total": int(query_streams.get("rows_dropped_by_stream_cap_total") or 0),
                "evidence_channel_count": int(evidence_channels.get("channel_count") or 0),
                "evidence_channel_labels": [str(label) for label in evidence_channels.get("channel_labels") or []],
                "evidence_channel_rank_only": bool(evidence_channels.get("rank_only", False)),
                "evidence_channel_rank_constant": int(evidence_channels.get("rank_constant") or 0),
                "evidence_channel_raw_scores_used": bool(
                    evidence_channels.get("raw_score_inputs_used_for_fusion", False)
                ),
                "evidence_channel_score_normalization_used": bool(
                    evidence_channels.get("score_normalization_used", False)
                ),
                "evidence_channel_selected_rows_after_fusion": int(
                    evidence_channels.get("selected_row_count_after_fusion") or 0
                ),
                "evidence_channel_dropped_rows_after_fusion": int(
                    evidence_channels.get("dropped_row_count_after_fusion") or 0
                ),
                "evidence_channel_selected_rows_by_channel": sanitize_int_counts(
                    evidence_channels.get("selected_rows_by_channel_after_grouping")
                ),
                "evidence_channel_dropped_rows_by_channel": sanitize_int_counts(
                    evidence_channels.get("dropped_rows_by_channel_after_grouping")
                ),
                "context_neighborhood_intent_detected": bool(context_neighborhood.get("intent_detected", False)),
                "context_neighborhood_anchor_rows": int(context_neighborhood.get("anchor_rows_considered") or 0),
                "context_neighborhood_generated_rows": int(context_neighborhood.get("generated_neighbor_rows") or 0),
                "context_neighborhood_selected_rows": int(context_neighborhood.get("selected_neighbor_rows") or 0),
                "context_neighborhood_omitted_rows": int(context_neighborhood.get("omitted_neighbor_rows") or 0),
            },
        ),
        stage_diagnostic(
            "candidate_materialization",
            "capped" if cap_labels else "available" if int(fielded.get("candidate_rows_retained") or 0) else "empty",
            "none",
            {
                "candidate_rows_considered": int(fielded.get("candidate_rows_considered") or 0),
                "candidate_rows_retained": int(fielded.get("candidate_rows_retained") or 0),
                "candidate_rows_surfaced": int(fielded.get("candidate_rows_surfaced") or 0),
                "materialized_from_in_memory_raw": int(
                    (fielded.get("materialization") or {}).get("candidate_rows_materialized_from_in_memory_raw") or 0
                ),
            },
        ),
        stage_diagnostic(
            "source_grouping_diversity",
            "available" if grouping.get("enabled") else "not_run",
            "none",
            {
                "candidate_rows_considered": int(grouping.get("candidate_rows_considered") or 0),
                "candidate_rows_selected": int(grouping.get("candidate_rows_selected") or 0),
                "candidate_rows_dropped": int(grouping.get("candidate_rows_dropped") or 0),
                "source_count_considered": int(grouping.get("source_count_considered") or 0),
                "source_count_selected": int(grouping.get("source_count_selected") or 0),
                "source_group_count_considered": int(grouping.get("source_group_count_considered") or 0),
                "source_group_count_selected": int(grouping.get("source_group_count_selected") or 0),
                "source_group_count_omitted": int(grouping.get("source_group_count_omitted") or 0),
                "session_group_count_considered": int(grouping.get("session_group_count_considered") or 0),
                "session_group_count_selected": int(grouping.get("session_group_count_selected") or 0),
                "session_group_count_omitted": int(grouping.get("session_group_count_omitted") or 0),
                "day_group_count_considered": int(grouping.get("day_group_count_considered") or 0),
                "day_group_count_selected": int(grouping.get("day_group_count_selected") or 0),
                "day_group_count_omitted": int(grouping.get("day_group_count_omitted") or 0),
                "category_group_count_considered": int(grouping.get("category_group_count_considered") or 0),
                "category_group_count_selected": int(grouping.get("category_group_count_selected") or 0),
                "category_group_count_omitted": int(grouping.get("category_group_count_omitted") or 0),
                "stream_head_rows_selected": int(grouping.get("stream_head_rows_selected") or 0),
                "source_head_rows_selected": int(grouping.get("source_head_rows_selected") or 0),
                "global_tail_rows_selected": int(grouping.get("global_tail_rows_selected") or 0),
                "group_interleaved_rows_selected": int(grouping.get("group_interleaved_rows_selected") or 0),
                "representative_reason_counts": sanitize_int_counts(grouping.get("representative_reason_counts")),
                "selected_representative_reason_counts": sanitize_int_counts(
                    grouping.get("selected_representative_reason_counts")
                ),
                "group_key_categories": [str(category) for category in grouping.get("group_key_categories") or []],
                "raw_group_keys_included": False,
                "suppressed_adjacent_row_count": int(
                    ((grouping.get("adjacent_run_collapse") or {}).get("suppressed_adjacent_row_count")) or 0
                ),
                "collapsed_adjacent_run_count": int(
                    ((grouping.get("adjacent_run_collapse") or {}).get("collapsed_adjacent_run_count")) or 0
                ),
                "group_count_considered": int(
                    ((grouping.get("group_interleaving") or {}).get("group_count_considered")) or 0
                ),
                "group_count_selected": int(
                    ((grouping.get("group_interleaving") or {}).get("group_count_selected")) or 0
                ),
                "group_count_omitted": int(
                    ((grouping.get("group_interleaving") or {}).get("group_count_omitted")) or 0
                ),
                "distinct_sources_selected": int(
                    ((grouping.get("group_interleaving") or {}).get("distinct_sources_selected")) or 0
                ),
                "distinct_sessions_selected": int(
                    ((grouping.get("group_interleaving") or {}).get("distinct_sessions_selected")) or 0
                ),
                "distinct_days_selected": int(
                    ((grouping.get("group_interleaving") or {}).get("distinct_days_selected")) or 0
                ),
                "rows_dropped_by_source_cap": int(grouping.get("rows_dropped_by_source_cap") or 0),
                "rows_dropped_by_stream_cap_total": int(
                    ((grouping.get("stream_selection") or {}).get("rows_dropped_by_stream_cap_total")) or 0
                ),
            },
        ),
        stage_diagnostic(
            "source_window_packaging",
            "packaged" if source_window_packaging.get("status") == "packaged" else "empty",
            "none",
            {
                "candidate_window_count": int(source_window_packaging.get("candidate_window_count") or 0),
                "candidate_window_limit": int(source_window_packaging.get("candidate_window_limit") or 0),
                "candidate_count_considered": int(source_window_packaging.get("candidate_count_considered") or 0),
                "candidate_window_omitted_count": int(
                    source_window_packaging.get("candidate_window_omitted_count") or 0
                ),
                "distinct_source_count_considered": int(
                    (source_window_packaging.get("selection_policy") or {}).get("distinct_source_count_considered")
                    or 0
                ),
                "distinct_source_count_selected": int(
                    (source_window_packaging.get("selection_policy") or {}).get("distinct_source_count_selected") or 0
                ),
                "intent_preferred_candidate_count": int(
                    (source_window_packaging.get("selection_policy") or {}).get("intent_preferred_candidate_count")
                    or 0
                ),
                "intent_preferred_window_count": int(
                    (source_window_packaging.get("selection_policy") or {}).get("intent_preferred_window_count") or 0
                ),
                "source_head_window_count": int(
                    (source_window_packaging.get("selection_policy") or {}).get("source_head_window_count") or 0
                ),
                "fill_window_count": int(
                    (source_window_packaging.get("selection_policy") or {}).get("fill_window_count") or 0
                ),
                "open_next_metadata_only": bool(source_window_packaging.get("open_next_metadata_only", True)),
                "source_window_text_included": bool(
                    source_window_packaging.get("source_window_text_included", False)
                ),
                "evidence_packet_count": int(evidence_packet_summary.get("packet_count") or 0),
                "evidence_packet_limit": int(evidence_packet_summary.get("packet_limit") or EVIDENCE_PACKET_LIMIT),
                "evidence_packet_child_count": int(evidence_packet_summary.get("hit_child_count") or 0),
                "evidence_packet_context_ref_count": int(evidence_packet_summary.get("context_record_count") or 0),
                "evidence_packet_omitted_child_count": int(evidence_packet_summary.get("omitted_child_count") or 0),
                "evidence_packet_deduplicated_child_count": int(
                    evidence_packet_summary.get("deduplicated_child_count") or 0
                ),
                "evidence_packet_materialized_context_window": int(
                    evidence_packet_summary.get("materialized_context_window") or 0
                ),
                "evidence_packet_materialized_context_record_count": int(
                    evidence_packet_summary.get("materialized_context_record_count") or 0
                ),
                "evidence_packet_materialized_context_omitted_or_truncated_count": int(
                    evidence_packet_summary.get("materialized_context_omitted_or_truncated_count") or 0
                ),
                "context_records_served_from_segment_cache": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "context_records_served_from_segment_cache"
                    )
                    or 0
                ),
                "context_records_served_from_in_memory_rows": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "context_records_served_from_in_memory_rows"
                    )
                    or 0
                ),
                "context_source_files_opened": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "context_source_files_opened"
                    )
                    or 0
                ),
                "context_bytes_read_for_fallback": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "context_bytes_read_for_fallback"
                    )
                    or 0
                ),
                "context_fallback_reparses": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "fallback_reparses"
                    )
                    or 0
                ),
                "context_stale_or_invalidated_fallback_count": int(
                    (evidence_packet_summary.get("materialized_context_source_counters") or {}).get(
                        "stale_or_invalidated_fallback_count"
                    )
                    or 0
                ),
                "evidence_packet_text_included": bool(evidence_packet_summary.get("raw_text_included", False)),
            },
        ),
        stage_diagnostic(
            "compact_handoff_output",
            "packaged" if compact_handoff_requested and compact_context_budget else "not_requested",
            "none",
            {
                "payload_bytes": int((compact_context_budget or {}).get("payload_bytes") or 0),
                "estimated_tokens": int((compact_context_budget or {}).get("estimated_tokens") or 0),
                "candidate_limit": int(compact_output_limits.get("candidates") or 0),
                "candidate_count": int(compact_output_counts.get("candidate_count") or 0),
                "candidates_included": int(compact_output_counts.get("candidates_included") or 0),
                "candidates_omitted": int(compact_output_counts.get("candidates_omitted") or 0),
                "source_ref_limit": int(compact_output_limits.get("source_refs") or 0),
                "source_refs_considered": int(compact_output_counts.get("source_refs_considered") or 0),
                "source_refs_included": int(compact_output_counts.get("source_refs_included") or 0),
                "source_refs_omitted": int(compact_output_counts.get("source_refs_omitted") or 0),
                "next_read_limit": int(compact_output_limits.get("next_reads") or 0),
                "next_reads_considered": int(compact_output_counts.get("next_reads_considered") or 0),
                "next_reads_included": int(compact_output_counts.get("next_reads_included") or 0),
                "next_reads_omitted": int(compact_output_counts.get("next_reads_omitted") or 0),
                "source_windows_included": int(compact_output_counts.get("source_windows_included") or 0),
                "evidence_packet_limit": int(compact_output_limits.get("evidence_packets") or 0),
                "evidence_packets_considered": int(compact_output_counts.get("evidence_packets_considered") or 0),
                "evidence_packets_included": int(compact_output_counts.get("evidence_packets_included") or 0),
                "evidence_packets_omitted": int(compact_output_counts.get("evidence_packets_omitted") or 0),
            },
        ),
        stage_diagnostic(
            "searched_scope_no_evidence",
            "available" if no_evidence_needed else "not_run",
            "none",
            {
                "needed": no_evidence_needed,
                "candidate_rows_found": int(no_evidence_report.get("candidate_rows_found") or candidate_count),
                "searched_files": int((no_evidence_report.get("searched_scope") or {}).get("searched_files") or 0),
                "searched_bytes": int((no_evidence_report.get("searched_scope") or {}).get("searched_bytes") or 0),
                "selected_source_count": int(source_plan.get("sources_selected") or 0),
                "pruned_source_count": int(source_plan.get("sources_pruned_hard") or 0),
                "deferred_source_count": int(source_plan.get("sources_deferred_budget") or 0),
                "not_all_history_absence": bool(no_evidence_report.get("not_all_history_absence", True)),
            },
        ),
    ]
    return {
        "schema_version": 1,
        "retrieval_mode": "fielded-bm25",
        "product_path": product_path,
        "stages": stages,
        "dominant_pressure_stage": dominant_stage(
            cap_labels=cap_labels,
            no_evidence_needed=no_evidence_needed,
            source_window_packaging=source_window_packaging,
            candidate_count=candidate_count,
        ),
        "source_catalog": source_catalog,
        "source_plan": source_plan,
        "claim_boundaries": {
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "default_promotion_supported": False,
        },
    }


def stage_diagnostic(stage: str, status: str, next_fix_hint: str, counters: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "next_fix_hint": next_fix_hint,
        "counters": counters,
        "privacy": {
            "raw_text_included": False,
            "raw_paths_included": False,
        },
    }


def dominant_stage(
    *,
    cap_labels: list[Any],
    no_evidence_needed: bool,
    source_window_packaging: dict[str, Any],
    candidate_count: int,
) -> str:
    if no_evidence_needed or candidate_count == 0:
        return "searched_scope_no_evidence"
    if source_window_packaging.get("status") != "packaged":
        return "source_window_packaging"
    if cap_labels:
        return "fielded_index_search"
    return "none"


def build_retrieval_pressure_summary(search_result: dict[str, Any]) -> dict[str, Any] | None:
    diagnostics = search_result.get("retrieval_diagnostics") or {}
    if not isinstance(diagnostics, dict):
        return None
    retrieval_mode = diagnostics.get("retrieval_mode") or search_result.get("retrieval_mode")
    if not retrieval_mode:
        return None
    if retrieval_mode == "fielded-bm25":
        return build_fielded_bm25_retrieval_pressure_summary(diagnostics)

    high_recall_state = diagnostics.get("high_recall_mode_state") or {}
    cap_labels = sorted(str(label) for label in diagnostics.get("cap_labels", []) if label)
    capped = bool(high_recall_state.get("capped", bool(cap_labels)))
    result_completeness = "lower_bound_due_to_caps" if capped else "complete_within_configured_caps"
    shared_source_enumeration = diagnostics.get("shared_source_enumeration") or {}
    duplicate_local_work = diagnostics.get("duplicate_local_work") or {}
    lane_file_overlap = diagnostics.get("lane_file_overlap") or {}
    fielded_ordering = diagnostics.get("fielded_informed_package_ordering") or {}
    fielded_source_grouping = diagnostics.get("fielded_source_grouping") or {}
    claim_boundaries = diagnostics.get("claim_boundaries") or {}

    return {
        "retrieval_mode": retrieval_mode,
        "capped": capped,
        "cap_labels": cap_labels,
        "lower_bound_result": capped,
        "result_completeness": result_completeness,
        "explicit_opt_in_required": bool(high_recall_state.get("explicit_opt_in_required", False)),
        "default_behavior_changed": bool(high_recall_state.get("default_behavior_changed", False)),
        "shared_source_enumeration": {
            "file_count": int(shared_source_enumeration.get("file_count") or 0),
            "byte_count": int(shared_source_enumeration.get("byte_count") or 0),
            "consumed_by_lanes": list(shared_source_enumeration.get("consumed_by_lanes") or []),
            "raw_paths_included": False,
        },
        "duplicate_local_work": {
            "enumeration_reused": bool(duplicate_local_work.get("enumeration_reused", False)),
            "avoided_second_enumeration": bool(duplicate_local_work.get("avoided_second_enumeration", False)),
            "duplicate_file_visit_count": int(duplicate_local_work.get("duplicate_file_visit_count") or 0),
            "duplicate_file_overlap_count": int(duplicate_local_work.get("duplicate_file_overlap_count") or 0),
            "duplicate_searched_bytes_lower_bound": int(
                duplicate_local_work.get("duplicate_searched_bytes_lower_bound") or 0
            ),
            "local_work_only_not_model_tokens": bool(
                duplicate_local_work.get("local_work_only_not_model_tokens", True)
            ),
            "raw_paths_included": False,
        },
        "lane_file_overlap": {
            "package_opened_file_count": int(lane_file_overlap.get("package_opened_file_count") or 0),
            "fielded_opened_file_count": int(lane_file_overlap.get("fielded_opened_file_count") or 0),
            "overlap_file_count": int(lane_file_overlap.get("overlap_file_count") or 0),
            "union_file_count": int(lane_file_overlap.get("union_file_count") or 0),
            "raw_paths_included": False,
        },
        "fielded_informed_package_ordering": {
            "enabled": bool(fielded_ordering.get("enabled", False)),
            "status": str(fielded_ordering.get("status") or "disabled"),
            "planned_file_count": int(fielded_ordering.get("planned_file_count") or 0),
            "prioritized_file_count": int(fielded_ordering.get("prioritized_file_count") or 0),
            "package_opened_prioritized_file_count": int(
                fielded_ordering.get("package_opened_prioritized_file_count") or 0
            ),
            "raw_paths_included": False,
            "complete_planned_file_set_preserved": bool(
                fielded_ordering.get("complete_planned_file_set_preserved", True)
            ),
            "fielded_priority_excludes_non_prioritized_files": False,
            "no_exclusion_semantics": True,
        },
        "fielded_source_grouping": {
            "enabled": bool(fielded_source_grouping.get("enabled", False)),
            "strategy": str(fielded_source_grouping.get("strategy") or "disabled"),
            "candidate_rows_considered": int(fielded_source_grouping.get("candidate_rows_considered") or 0),
            "candidate_rows_selected": int(fielded_source_grouping.get("candidate_rows_selected") or 0),
            "candidate_rows_dropped": int(fielded_source_grouping.get("candidate_rows_dropped") or 0),
            "source_count_considered": int(fielded_source_grouping.get("source_count_considered") or 0),
            "source_count_selected": int(fielded_source_grouping.get("source_count_selected") or 0),
            "source_group_count_considered": int(fielded_source_grouping.get("source_group_count_considered") or 0),
            "source_group_count_selected": int(fielded_source_grouping.get("source_group_count_selected") or 0),
            "source_group_count_omitted": int(fielded_source_grouping.get("source_group_count_omitted") or 0),
            "session_group_count_considered": int(fielded_source_grouping.get("session_group_count_considered") or 0),
            "session_group_count_selected": int(fielded_source_grouping.get("session_group_count_selected") or 0),
            "session_group_count_omitted": int(fielded_source_grouping.get("session_group_count_omitted") or 0),
            "day_group_count_considered": int(fielded_source_grouping.get("day_group_count_considered") or 0),
            "day_group_count_selected": int(fielded_source_grouping.get("day_group_count_selected") or 0),
            "day_group_count_omitted": int(fielded_source_grouping.get("day_group_count_omitted") or 0),
            "category_group_count_considered": int(
                fielded_source_grouping.get("category_group_count_considered") or 0
            ),
            "category_group_count_selected": int(fielded_source_grouping.get("category_group_count_selected") or 0),
            "category_group_count_omitted": int(fielded_source_grouping.get("category_group_count_omitted") or 0),
            "dominant_source_share_before": float(
                fielded_source_grouping.get("dominant_source_share_before") or 0.0
            ),
            "dominant_source_share_after": float(fielded_source_grouping.get("dominant_source_share_after") or 0.0),
            "stream_head_rows_selected": int(fielded_source_grouping.get("stream_head_rows_selected") or 0),
            "source_head_rows_selected": int(fielded_source_grouping.get("source_head_rows_selected") or 0),
            "global_tail_rows_selected": int(fielded_source_grouping.get("global_tail_rows_selected") or 0),
            "group_interleaved_rows_selected": int(fielded_source_grouping.get("group_interleaved_rows_selected") or 0),
            "representative_reason_counts": sanitize_int_counts(
                fielded_source_grouping.get("representative_reason_counts")
            ),
            "selected_representative_reason_counts": sanitize_int_counts(
                fielded_source_grouping.get("selected_representative_reason_counts")
            ),
            "representative_reason_categories": [
                str(category) for category in fielded_source_grouping.get("representative_reason_categories") or []
            ],
            "group_key_categories": [
                str(category) for category in fielded_source_grouping.get("group_key_categories") or []
            ],
            "raw_group_keys_included": False,
            "adjacent_run_collapse": sanitize_adjacent_run_collapse(
                fielded_source_grouping.get("adjacent_run_collapse")
            ),
            "group_interleaving": sanitize_group_interleaving(fielded_source_grouping.get("group_interleaving")),
            "rows_dropped_by_source_cap": int(fielded_source_grouping.get("rows_dropped_by_source_cap") or 0),
            "stream_selection": sanitize_stream_selection(fielded_source_grouping.get("stream_selection")),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_session_ids_included": False,
        },
        "claim_boundaries": {
            "token_savings_claim_supported": bool(claim_boundaries.get("token_savings_claim_supported", False)),
            "final_answer_quality_claim_supported": bool(
                claim_boundaries.get("final_answer_quality_claim_supported", False)
            ),
            "default_promotion_supported": bool(claim_boundaries.get("default_promotion_supported", False)),
            "default_behavior_changed": bool(claim_boundaries.get("default_behavior_changed", False)),
            "scanner_ranking_or_source_selection_changed": bool(
                claim_boundaries.get("scanner_ranking_or_source_selection_changed", False)
                or (
                    bool(fielded_source_grouping.get("enabled", False))
                    and str(fielded_source_grouping.get("strategy") or "disabled") != "disabled"
                )
            ),
            "all_history_completeness_claim_supported": bool(
                claim_boundaries.get("all_history_completeness_claim_supported", False)
            ),
        },
    }


def build_fielded_bm25_retrieval_pressure_summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    fielded_state = diagnostics.get("fielded_mode_state") or {}
    cap_labels = sorted(str(label) for label in diagnostics.get("cap_labels", []) if label)
    capped = bool(fielded_state.get("capped", bool(cap_labels)))
    result_completeness = "lower_bound_due_to_caps" if capped else "complete_within_configured_caps"
    shared_source_enumeration = diagnostics.get("shared_source_enumeration") or {}
    source_catalog = sanitize_source_catalog(diagnostics.get("source_catalog") or {})
    source_plan = sanitize_source_plan(diagnostics.get("source_plan") or {})
    fielded_bm25 = diagnostics.get("fielded_bm25") or {}
    index_summary = fielded_bm25.get("index_summary") if isinstance(fielded_bm25.get("index_summary"), dict) else {}
    fielded_source_grouping = diagnostics.get("fielded_source_grouping") or {}
    fielded_evidence_channels = diagnostics.get("fielded_evidence_channels") or fielded_bm25.get("evidence_channels") or {}
    fielded_child_query_plan = diagnostics.get("fielded_child_query_plan") or fielded_bm25.get("child_query_plan") or {}
    fielded_context_neighborhood = diagnostics.get("fielded_context_neighborhood") or fielded_bm25.get("context_neighborhood") or {}
    fielded_context_source = fielded_bm25.get("context_source_counters") or {}
    phase_profiles = diagnostics.get("phase_profiles") or {}
    claim_boundaries = diagnostics.get("claim_boundaries") or {}

    return {
        "retrieval_mode": "fielded-bm25",
        "capped": capped,
        "cap_labels": cap_labels,
        "lower_bound_result": capped,
        "result_completeness": result_completeness,
        "explicit_opt_in_required": bool(fielded_state.get("explicit_opt_in_required", False)),
        "default_behavior_changed": bool(fielded_state.get("default_behavior_changed", False)),
        "persistent_index_enabled": bool(index_summary.get("persistent_index_enabled", False)),
        "index_schema_version": str(index_summary.get("index_schema_version") or ""),
        "parser_version": str(index_summary.get("parser_version") or ""),
        "chunking_version": str(index_summary.get("chunking_version") or ""),
        "sources_reused": int(index_summary.get("sources_reused") or 0),
        "sources_reindexed": int(index_summary.get("sources_reindexed") or 0),
        "sources_invalidated": int(index_summary.get("sources_invalidated") or 0),
        "sources_deleted": int(index_summary.get("sources_deleted") or 0),
        "segments_loaded": int(index_summary.get("segments_loaded") or 0),
        "segments_written": int(index_summary.get("segments_written") or 0),
        "warm_query_reused_index": bool(index_summary.get("warm_query_reused_index", False)),
        "content_fingerprint_validation_enabled": bool(
            index_summary.get("content_fingerprint_validation_enabled", False)
        ),
        "content_fingerprint_files": int(index_summary.get("content_fingerprint_files") or 0),
        "content_fingerprint_bytes": int(index_summary.get("content_fingerprint_bytes") or 0),
        "content_fingerprint_strategy": str(index_summary.get("content_fingerprint_strategy") or ""),
        "shared_source_enumeration": {
            "file_count": int(shared_source_enumeration.get("file_count") or 0),
            "byte_count": int(shared_source_enumeration.get("byte_count") or 0),
            "consumed_by_lanes": list(shared_source_enumeration.get("consumed_by_lanes") or []),
            "raw_paths_included": False,
        },
        "source_catalog": source_catalog,
        "source_plan": source_plan,
        "fielded_bm25": {
            "status": fielded_bm25.get("status"),
            "candidate_count": int(fielded_bm25.get("candidate_count") or 0),
            "candidate_rows": int(fielded_bm25.get("candidate_rows") or 0),
            "candidate_row_limit": int(fielded_bm25.get("candidate_row_limit") or 0),
            "candidate_row_pool_limit": int(fielded_bm25.get("candidate_row_pool_limit") or 0),
            "candidate_rows_generated": int(fielded_bm25.get("candidate_rows_generated") or 0),
            "candidate_rows_considered": int(fielded_bm25.get("candidate_rows_considered") or 0),
            "candidate_rows_from_query_streams": int(fielded_bm25.get("candidate_rows_from_query_streams") or 0),
            "candidate_rows_retained": int(fielded_bm25.get("candidate_rows_retained") or 0),
            "candidate_rows_surfaced": int(fielded_bm25.get("candidate_rows_surfaced") or 0),
            "searched_files": int(fielded_bm25.get("searched_files") or 0),
            "searched_bytes": int(fielded_bm25.get("searched_bytes") or 0),
            "records_considered": int(fielded_bm25.get("records_considered") or 0),
            "query_streams": sanitize_query_stream_pressure(fielded_bm25.get("query_streams")),
            "child_query_plan": sanitize_child_query_plan_pressure(fielded_child_query_plan),
            "evidence_channels": sanitize_evidence_channel_pressure(fielded_evidence_channels),
            "context_neighborhood": sanitize_context_neighborhood(fielded_context_neighborhood),
            "context_source_counters": sanitize_context_source_counters(fielded_context_source),
            "materialization": fielded_bm25.get("materialization") or {},
            "index_summary": fielded_bm25.get("index_summary") or {},
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_session_ids_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "fielded_evidence_channels": sanitize_evidence_channel_pressure(fielded_evidence_channels),
        "fielded_child_query_plan": sanitize_child_query_plan_pressure(fielded_child_query_plan),
        "fielded_context_neighborhood": sanitize_context_neighborhood(fielded_context_neighborhood),
        "fielded_source_grouping": {
            "enabled": bool(fielded_source_grouping.get("enabled", False)),
            "strategy": str(fielded_source_grouping.get("strategy") or "disabled"),
            "candidate_rows_considered": int(fielded_source_grouping.get("candidate_rows_considered") or 0),
            "candidate_rows_selected": int(fielded_source_grouping.get("candidate_rows_selected") or 0),
            "candidate_rows_dropped": int(fielded_source_grouping.get("candidate_rows_dropped") or 0),
            "source_count_considered": int(fielded_source_grouping.get("source_count_considered") or 0),
            "source_count_selected": int(fielded_source_grouping.get("source_count_selected") or 0),
            "source_group_count_considered": int(fielded_source_grouping.get("source_group_count_considered") or 0),
            "source_group_count_selected": int(fielded_source_grouping.get("source_group_count_selected") or 0),
            "source_group_count_omitted": int(fielded_source_grouping.get("source_group_count_omitted") or 0),
            "session_group_count_considered": int(fielded_source_grouping.get("session_group_count_considered") or 0),
            "session_group_count_selected": int(fielded_source_grouping.get("session_group_count_selected") or 0),
            "session_group_count_omitted": int(fielded_source_grouping.get("session_group_count_omitted") or 0),
            "day_group_count_considered": int(fielded_source_grouping.get("day_group_count_considered") or 0),
            "day_group_count_selected": int(fielded_source_grouping.get("day_group_count_selected") or 0),
            "day_group_count_omitted": int(fielded_source_grouping.get("day_group_count_omitted") or 0),
            "category_group_count_considered": int(
                fielded_source_grouping.get("category_group_count_considered") or 0
            ),
            "category_group_count_selected": int(fielded_source_grouping.get("category_group_count_selected") or 0),
            "category_group_count_omitted": int(fielded_source_grouping.get("category_group_count_omitted") or 0),
            "dominant_source_share_before": float(
                fielded_source_grouping.get("dominant_source_share_before") or 0.0
            ),
            "dominant_source_share_after": float(fielded_source_grouping.get("dominant_source_share_after") or 0.0),
            "stream_head_rows_selected": int(fielded_source_grouping.get("stream_head_rows_selected") or 0),
            "source_head_rows_selected": int(fielded_source_grouping.get("source_head_rows_selected") or 0),
            "global_tail_rows_selected": int(fielded_source_grouping.get("global_tail_rows_selected") or 0),
            "group_interleaved_rows_selected": int(fielded_source_grouping.get("group_interleaved_rows_selected") or 0),
            "representative_reason_counts": sanitize_int_counts(
                fielded_source_grouping.get("representative_reason_counts")
            ),
            "selected_representative_reason_counts": sanitize_int_counts(
                fielded_source_grouping.get("selected_representative_reason_counts")
            ),
            "representative_reason_categories": [
                str(category) for category in fielded_source_grouping.get("representative_reason_categories") or []
            ],
            "group_key_categories": [
                str(category) for category in fielded_source_grouping.get("group_key_categories") or []
            ],
            "raw_group_keys_included": False,
            "adjacent_run_collapse": sanitize_adjacent_run_collapse(
                fielded_source_grouping.get("adjacent_run_collapse")
            ),
            "group_interleaving": sanitize_group_interleaving(fielded_source_grouping.get("group_interleaving")),
            "rows_dropped_by_source_cap": int(fielded_source_grouping.get("rows_dropped_by_source_cap") or 0),
            "stream_selection": sanitize_stream_selection(fielded_source_grouping.get("stream_selection")),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_session_ids_included": False,
        },
        "phase_profiles": {
            "total_fielded_bm25_retrieval": phase_profiles.get("total_fielded_bm25_retrieval") or {},
            "shared_source_enumeration": phase_profiles.get("shared_source_enumeration") or {},
            "fielded_bm25_index_build": phase_profiles.get("fielded_bm25_index_build") or {},
            "fielded_bm25_search_materialization": phase_profiles.get("fielded_bm25_search_materialization") or {},
        },
        "claim_boundaries": {
            "token_savings_claim_supported": bool(claim_boundaries.get("token_savings_claim_supported", False)),
            "final_answer_quality_claim_supported": bool(
                claim_boundaries.get("final_answer_quality_claim_supported", False)
            ),
            "default_promotion_supported": bool(claim_boundaries.get("default_promotion_supported", False)),
            "default_behavior_changed": bool(claim_boundaries.get("default_behavior_changed", False)),
            "scanner_ranking_or_source_selection_changed": bool(
                claim_boundaries.get("scanner_ranking_or_source_selection_changed", False)
                or (
                    bool(fielded_source_grouping.get("enabled", False))
                    and str(fielded_source_grouping.get("strategy") or "disabled") != "disabled"
                )
            ),
            "all_history_completeness_claim_supported": bool(
                claim_boundaries.get("all_history_completeness_claim_supported", False)
            ),
            "persistent_index_or_cache_used": bool(claim_boundaries.get("persistent_index_or_cache_used", False)),
        },
    }


def sanitize_query_stream_pressure(value: Any) -> dict[str, Any]:
    streams = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(streams.get("enabled", False)),
        "strategy": str(streams.get("strategy") or "disabled"),
        "query_plan_mode": str(streams.get("query_plan_mode") or "auto"),
        "stream_count": int(streams.get("stream_count") or 0),
        "child_stream_count": int(streams.get("child_stream_count") or 0),
        "child_stream_labels": [str(label) for label in streams.get("child_stream_labels") or []],
        "candidate_row_pool_limit_per_stream": int(streams.get("candidate_row_pool_limit_per_stream") or 0),
        "per_stream_limit": int(streams.get("per_stream_limit") or 0),
        "stream_hit_limit_count": int(streams.get("stream_hit_limit_count") or 0),
        "saturated_stream_count": int(streams.get("saturated_stream_count") or 0),
        "saturated_stream_labels": [str(label) for label in streams.get("saturated_stream_labels") or []],
        "row_count_before_dedupe": int(streams.get("row_count_before_dedupe") or 0),
        "row_count_after_dedupe": int(streams.get("row_count_after_dedupe") or 0),
        "duplicate_row_count": int(streams.get("duplicate_row_count") or 0),
        "selected_rows_by_stream": sanitize_int_counts(streams.get("selected_rows_by_stream")),
        "rows_dropped_by_stream_cap": sanitize_int_counts(streams.get("rows_dropped_by_stream_cap")),
        "rows_dropped_by_stream_cap_total": int(streams.get("rows_dropped_by_stream_cap_total") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def sanitize_child_query_plan_pressure(value: Any) -> dict[str, Any]:
    plan = value if isinstance(value, dict) else {}
    children: list[dict[str, Any]] = []
    for item in plan.get("children") or []:
        if not isinstance(item, dict):
            continue
        children.append(
            {
                "label": str(item.get("label") or "unknown"),
                "intent": str(item.get("intent") or "generic_history"),
                "kind": str(item.get("kind") or "unknown"),
                "fields": [str(field) for field in item.get("fields") or []],
                "status": str(item.get("status") or "unknown"),
                "skip_reason_code": item.get("skip_reason_code")
                if item.get("skip_reason_code") is None
                else str(item.get("skip_reason_code")),
                "reason_codes": [str(reason) for reason in item.get("reason_codes") or []],
                "row_count": int(item.get("row_count") or 0),
                "deduped_row_count": int(item.get("deduped_row_count") or 0),
                "stream_limit": int(item.get("stream_limit") or 0),
                "hit_limit": bool(item.get("hit_limit", False)),
                "selected_rows_after_grouping": int(item.get("selected_rows_after_grouping") or 0),
                "dropped_rows_after_grouping": int(item.get("dropped_rows_after_grouping") or 0),
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
                "raw_paths_included": False,
                "raw_text_included": False,
                "raw_session_ids_included": False,
                "raw_cwd_included": False,
                "vectors_or_persisted_indexes_included": False,
            }
        )
    skipped: list[dict[str, Any]] = []
    for item in plan.get("skipped") or []:
        if not isinstance(item, dict):
            continue
        skipped.append(
            {
                "intent": str(item.get("intent") or "generic_history"),
                "status": str(item.get("status") or "skipped"),
                "skip_reason_code": str(item.get("skip_reason_code") or "unknown"),
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
            }
        )
    return {
        "query_plan_mode": str(plan.get("query_plan_mode") or "auto"),
        "primary_intent": str(plan.get("primary_intent") or "generic_history"),
        "supported_child_channel_intent": plan.get("supported_child_channel_intent")
        if plan.get("supported_child_channel_intent") is None
        else str(plan.get("supported_child_channel_intent")),
        "enabled": bool(plan.get("enabled", False)),
        "debug": bool(plan.get("debug", False)),
        "child_channel_count": int(plan.get("child_channel_count") or len(children)),
        "child_channel_labels": [str(label) for label in plan.get("child_channel_labels") or []],
        "children": children,
        "skipped": skipped,
        "privacy": {
            "raw_query_text_included": False,
            "raw_query_terms_included": False,
            "private_transcript_text_included": False,
            "raw_paths_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def sanitize_evidence_channel_pressure(value: Any) -> dict[str, Any]:
    channels = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(channels.get("enabled", False)),
        "strategy": str(channels.get("strategy") or "disabled"),
        "fusion_stage": str(channels.get("fusion_stage") or ""),
        "rank_fusion_style": str(channels.get("rank_fusion_style") or ""),
        "rank_constant": int(channels.get("rank_constant") or 0),
        "rank_only": bool(channels.get("rank_only", False)),
        "raw_score_inputs_used_for_fusion": bool(channels.get("raw_score_inputs_used_for_fusion", False)),
        "raw_bm25_scores_used_for_fusion": bool(channels.get("raw_bm25_scores_used_for_fusion", False)),
        "score_normalization_used": bool(channels.get("score_normalization_used", False)),
        "equal_channel_weights": bool(channels.get("equal_channel_weights", True)),
        "candidate_row_pool_limit": int(channels.get("candidate_row_pool_limit") or 0),
        "channel_count": int(channels.get("channel_count") or 0),
        "channel_labels": [str(label) for label in channels.get("channel_labels") or []],
        "channel_kinds": {str(label): str(kind) for label, kind in (channels.get("channel_kinds") or {}).items()},
        "channel_row_counts": sanitize_int_counts(channels.get("channel_row_counts")),
        "unique_row_count_before_fusion": int(channels.get("unique_row_count_before_fusion") or 0),
        "selected_row_count_after_fusion": int(channels.get("selected_row_count_after_fusion") or 0),
        "dropped_row_count_after_fusion": int(channels.get("dropped_row_count_after_fusion") or 0),
        "selected_rows_by_channel_after_fusion": sanitize_int_counts(
            channels.get("selected_rows_by_channel_after_fusion")
        ),
        "dropped_rows_by_channel_after_fusion": sanitize_int_counts(
            channels.get("dropped_rows_by_channel_after_fusion")
        ),
        "selected_rows_by_channel_after_grouping": sanitize_int_counts(
            channels.get("selected_rows_by_channel_after_grouping")
        ),
        "dropped_rows_by_channel_after_grouping": sanitize_int_counts(
            channels.get("dropped_rows_by_channel_after_grouping")
        ),
        "selected_row_reason_label_prefix": str(
            channels.get("selected_row_reason_label_prefix") or "fielded_bm25:evidence_channel:"
        ),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def sanitize_context_neighborhood(value: Any) -> dict[str, Any]:
    neighborhood = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(neighborhood.get("enabled", False)),
        "strategy": str(neighborhood.get("strategy") or "disabled"),
        "intent_detected": bool(neighborhood.get("intent_detected", False)),
        "intent_reason_codes": [str(reason) for reason in neighborhood.get("intent_reason_codes") or []],
        "anchor_rows_considered": int(neighborhood.get("anchor_rows_considered") or 0),
        "anchor_row_limit": int(neighborhood.get("anchor_row_limit") or 0),
        "neighbor_radius_before": int(neighborhood.get("neighbor_radius_before") or 0),
        "neighbor_radius_after": int(neighborhood.get("neighbor_radius_after") or 0),
        "generated_neighbor_rows": int(neighborhood.get("generated_neighbor_rows") or 0),
        "selected_neighbor_rows": int(neighborhood.get("selected_neighbor_rows") or 0),
        "omitted_neighbor_rows": int(neighborhood.get("omitted_neighbor_rows") or 0),
        "deduped_existing_row_count": int(neighborhood.get("deduped_existing_row_count") or 0),
        "deduped_generated_row_count": int(neighborhood.get("deduped_generated_row_count") or 0),
        "participated_in_grouping": bool(neighborhood.get("participated_in_grouping", False)),
        "selected_by_group_interleaving": int(neighborhood.get("selected_by_group_interleaving") or 0),
        "dropped_by_group_interleaving": int(neighborhood.get("dropped_by_group_interleaving") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def sanitize_stream_selection(value: Any) -> dict[str, Any]:
    selection = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(selection.get("enabled", False)),
        "selected_rows_by_stream": sanitize_int_counts(selection.get("selected_rows_by_stream")),
        "rows_dropped_by_stream_cap": sanitize_int_counts(selection.get("rows_dropped_by_stream_cap")),
        "rows_dropped_by_stream_cap_total": int(selection.get("rows_dropped_by_stream_cap_total") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def sanitize_adjacent_run_collapse(value: Any) -> dict[str, Any]:
    collapse = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(collapse.get("enabled", False)),
        "strategy": str(collapse.get("strategy") or "disabled"),
        "rows_before_collapse": int(collapse.get("rows_before_collapse") or 0),
        "representative_rows_after_collapse": int(collapse.get("representative_rows_after_collapse") or 0),
        "adjacent_run_count": int(collapse.get("adjacent_run_count") or 0),
        "collapsed_adjacent_run_count": int(collapse.get("collapsed_adjacent_run_count") or 0),
        "suppressed_adjacent_row_count": int(collapse.get("suppressed_adjacent_row_count") or 0),
        "max_adjacent_run_size": int(collapse.get("max_adjacent_run_size") or 0),
        "suppressed_rows_are_aggregate_counters_only": bool(
            collapse.get("suppressed_rows_are_aggregate_counters_only", True)
        ),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def sanitize_group_interleaving(value: Any) -> dict[str, Any]:
    interleaving = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(interleaving.get("enabled", False)),
        "strategy": str(interleaving.get("strategy") or "disabled"),
        "group_count_considered": int(interleaving.get("group_count_considered") or 0),
        "group_count_selected": int(interleaving.get("group_count_selected") or 0),
        "group_count_omitted": int(interleaving.get("group_count_omitted") or 0),
        "rows_selected_by_interleaving": int(interleaving.get("rows_selected_by_interleaving") or 0),
        "distinct_sources_considered": int(interleaving.get("distinct_sources_considered") or 0),
        "distinct_sessions_considered": int(interleaving.get("distinct_sessions_considered") or 0),
        "distinct_days_considered": int(interleaving.get("distinct_days_considered") or 0),
        "distinct_sources_selected": int(interleaving.get("distinct_sources_selected") or 0),
        "distinct_sessions_selected": int(interleaving.get("distinct_sessions_selected") or 0),
        "distinct_days_selected": int(interleaving.get("distinct_days_selected") or 0),
        "dominant_group_share_before": float(interleaving.get("dominant_group_share_before") or 0.0),
        "dominant_group_share_after": float(interleaving.get("dominant_group_share_after") or 0.0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def sanitize_source_catalog(catalog: Any) -> dict[str, Any]:
    if not isinstance(catalog, dict):
        catalog = {}
    source_kind_counts = catalog.get("source_kind_counts") if isinstance(catalog.get("source_kind_counts"), dict) else {}
    cap_hits = catalog.get("cap_hits") if isinstance(catalog.get("cap_hits"), dict) else {}
    return {
        "schema_version": int(catalog.get("schema_version") or 1),
        "source": str(catalog.get("source") or "command_local_source_catalog"),
        "basis": str(catalog.get("basis") or "shared_per_command_source_enumeration"),
        "file_count": int(catalog.get("file_count") or 0),
        "byte_count": int(catalog.get("byte_count") or 0),
        "source_kind_counts": {str(key): int(value or 0) for key, value in sorted(source_kind_counts.items())},
        "cap_labels": sorted(str(label) for label in catalog.get("cap_labels") or [] if label),
        "cap_hits": {
            "file": bool(cap_hits.get("file", False)),
            "byte": bool(cap_hits.get("byte", False)),
            "elapsed": bool(cap_hits.get("elapsed", False)),
        },
        "selected_file_count": int(catalog.get("selected_file_count") or catalog.get("file_count") or 0),
        "skipped_file_count": int(catalog.get("skipped_file_count") or 0),
        "selected_root_count": int(catalog.get("selected_root_count") or 0),
        "rejected_root_count": int(catalog.get("rejected_root_count") or 0),
        "warning_count": int(catalog.get("warning_count") or 0),
        "elapsed_ms": float(catalog.get("elapsed_ms") or 0.0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "persistent_index_or_cache_used": False,
    }


def sanitize_source_plan(source_plan: Any) -> dict[str, Any]:
    if not isinstance(source_plan, dict):
        source_plan = {}
    prune_reasons = source_plan.get("prune_reasons") if isinstance(source_plan.get("prune_reasons"), dict) else {}
    deferred_reason_counts = (
        source_plan.get("deferred_reason_counts")
        if isinstance(source_plan.get("deferred_reason_counts"), dict)
        else {}
    )
    materialization_budget = (
        source_plan.get("materialization_budget") if isinstance(source_plan.get("materialization_budget"), dict) else {}
    )
    budget_basis = source_plan.get("budget_basis") if isinstance(source_plan.get("budget_basis"), dict) else {}
    source_tiers = source_plan.get("source_tiers") if isinstance(source_plan.get("source_tiers"), dict) else {}
    retry_guidance = source_plan.get("retry_guidance") if isinstance(source_plan.get("retry_guidance"), dict) else {}
    date_window = source_plan.get("date_window") if isinstance(source_plan.get("date_window"), dict) else {}
    return {
        "schema_version": int(source_plan.get("schema_version") or 1),
        "source": str(source_plan.get("source") or "command_local_source_plan"),
        "basis": str(source_plan.get("basis") or "command_local_source_catalog"),
        "selection_policy": str(
            source_plan.get("selection_policy") or "hard_prune_only_path_date_bucket_disjoint_from_intent_date_window"
        ),
        "sources_total": int(source_plan.get("sources_total") or 0),
        "sources_selected": int(source_plan.get("sources_selected") or 0),
        "sources_pruned_hard": int(source_plan.get("sources_pruned_hard") or 0),
        "sources_deferred_budget": int(source_plan.get("sources_deferred_budget") or 0),
        "sources_unknown_or_undated_selected": int(source_plan.get("sources_unknown_or_undated_selected") or 0),
        "byte_count_total": int(source_plan.get("byte_count_total") or 0),
        "byte_count_selected": int(source_plan.get("byte_count_selected") or 0),
        "prune_reasons": {str(key): int(value or 0) for key, value in sorted(prune_reasons.items())},
        "tier_order": [str(item) for item in source_plan.get("tier_order") or []],
        "source_tiers": sanitize_source_tiers(source_tiers),
        "deferred_reason_counts": {str(key): int(value or 0) for key, value in sorted(deferred_reason_counts.items())},
        "budget_basis": {
            "policy": str(budget_basis.get("policy") or ""),
            "max_selected_files": int(budget_basis.get("max_selected_files") or 0),
            "max_selected_bytes": int(budget_basis.get("max_selected_bytes") or 0),
            "eligible_source_count_after_hard_prune": int(
                budget_basis.get("eligible_source_count_after_hard_prune") or 0
            ),
            "effective_files": int(budget_basis.get("effective_files") or 0),
            "effective_bytes": int(budget_basis.get("effective_bytes") or 0),
            "max_matches": int(budget_basis.get("max_matches") or 0),
            "uses_scoring_weights": bool(budget_basis.get("uses_scoring_weights", False)),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "retry_guidance": {
            "needed": bool(retry_guidance.get("needed", False)),
            "deferred_source_count": int(retry_guidance.get("deferred_source_count") or 0),
            "guidance": str(retry_guidance.get("guidance") or ""),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "materialization_budget": {
            "max_files": int(materialization_budget.get("max_files") or 0),
            "max_bytes": int(materialization_budget.get("max_bytes") or 0),
            "max_elapsed": float(materialization_budget.get("max_elapsed") or 0.0),
            "effective_files": int(materialization_budget.get("effective_files") or 0),
            "effective_bytes": int(materialization_budget.get("effective_bytes") or 0),
        },
        "exhaustive_available": bool(source_plan.get("exhaustive_available", False)),
        "date_window": {
            "present": bool(date_window.get("present", False)),
            "start": date_window.get("start") if date_window.get("start") is None else str(date_window.get("start")),
            "end": date_window.get("end") if date_window.get("end") is None else str(date_window.get("end")),
            "raw_query_text_included": False,
        },
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "privacy_flags": {
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
        },
        "persistent_index_or_cache_used": False,
    }


def sanitize_source_tiers(source_tiers: Any) -> dict[str, Any]:
    if not isinstance(source_tiers, dict):
        source_tiers = {}
    lexical_probe = source_tiers.get("lexical_probe") if isinstance(source_tiers.get("lexical_probe"), dict) else {}
    return {
        "order": [str(item) for item in source_tiers.get("order") or []],
        "selection_order": [str(item) for item in source_tiers.get("selection_order") or []],
        "stable_ordering": str(source_tiers.get("stable_ordering") or ""),
        "lexical_probe": {
            "enabled": bool(lexical_probe.get("enabled", False)),
            "reason": str(lexical_probe.get("reason") or "no_already_built_cheap_source_probe_available"),
            "selected_count": int(lexical_probe.get("selected_count") or 0),
        },
        "eligible_counts": sanitize_int_counts(source_tiers.get("eligible_counts")),
        "selected_counts": sanitize_int_counts(source_tiers.get("selected_counts")),
        "deferred_counts": sanitize_int_counts(source_tiers.get("deferred_counts")),
        "date_overlap_counts": sanitize_int_counts(source_tiers.get("date_overlap_counts")),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
    }


def sanitize_int_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count or 0) for key, count in sorted(value.items())}


def compact_provenance_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for candidate in candidates[:COMPACT_PROVENANCE_CANDIDATE_LIMIT]:
        source_ref = candidate.get("source_ref") or {}
        rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "rank": candidate.get("rank"),
                "source_ref": source_ref,
                "target": source_ref_target(source_ref),
                "timestamp_utc": candidate.get("timestamp_utc"),
                "role": candidate.get("role"),
                "confidence": candidate.get("confidence"),
                "stage": candidate.get("stage") or first_value(candidate.get("stages", [])),
                "score": candidate.get("score"),
                "reason_codes": list(candidate.get("reason_codes", []))[:SOURCE_WINDOW_REASON_CODE_LIMIT],
            }
        )
    return rows


def compact_provenance_source_ref_candidates(
    candidates: list[dict[str, Any]],
    locator_candidates: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = []
    for candidate in candidates:
        refs.append(
            {
                "kind": "candidate_source_ref",
                "candidate_id": candidate.get("candidate_id"),
                "source_ref": candidate.get("source_ref") or {},
                "target": candidate.get("target"),
            }
        )
    for candidate in locator_candidates[:SOURCE_LOCATOR_TOP_CANDIDATES_LIMIT]:
        ref = candidate.get("source_ref") or {}
        refs.append(
            {
                "kind": "source_locator_top_candidate_ref",
                "candidate_id": candidate.get("candidate_id"),
                "source_ref": ref,
                "target": source_ref_target(ref),
            }
        )
    for read in next_reads[:COMPACT_PROVENANCE_NEXT_READ_LIMIT]:
        refs.append(
            {
                "kind": read.get("kind", "next_read"),
                "candidate_id": read.get("candidate_id"),
                "source_ref": read.get("source_ref") or {},
                "target": read.get("target"),
            }
        )
    return dedupe_provenance_refs(refs)


def compact_provenance_source_refs(
    candidates: list[dict[str, Any]],
    locator_candidates: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return compact_provenance_source_ref_candidates(candidates, locator_candidates, next_reads)[
        :COMPACT_PROVENANCE_SOURCE_REF_LIMIT
    ]


def build_compact_handoff_output_budget(
    *,
    candidate_count: int,
    candidates_included: int,
    source_ref_count_considered: int,
    source_refs_included: int,
    source_windows_included: int,
    evidence_packet_count_considered: int,
    evidence_packets_included: int,
    next_read_count_considered: int,
    next_reads_included: int,
    evidence_packet_text_included: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "measurement_basis": "compact_provenance_handoff_packet_only",
        "limits": {
            "candidates": COMPACT_PROVENANCE_CANDIDATE_LIMIT,
            "source_refs": COMPACT_PROVENANCE_SOURCE_REF_LIMIT,
            "source_windows": SOURCE_WINDOW_LIMIT,
            "evidence_packets": EVIDENCE_PACKET_LIMIT,
            "hit_children_per_packet": EVIDENCE_PACKET_CHILD_LIMIT,
            "context_records_per_packet": EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT,
            "next_reads": COMPACT_PROVENANCE_NEXT_READ_LIMIT,
            "expansion_actions_per_ref": 3,
            "reason_codes_per_candidate": SOURCE_WINDOW_REASON_CODE_LIMIT,
        },
        "counts": {
            "candidate_count": candidate_count,
            "candidates_included": candidates_included,
            "candidates_omitted": max(0, candidate_count - candidates_included),
            "source_refs_considered": source_ref_count_considered,
            "source_refs_included": source_refs_included,
            "source_refs_omitted": max(0, source_ref_count_considered - source_refs_included),
            "next_reads_considered": next_read_count_considered,
            "next_reads_included": next_reads_included,
            "next_reads_omitted": max(0, next_read_count_considered - next_reads_included),
            "source_windows_included": source_windows_included,
            "evidence_packets_considered": evidence_packet_count_considered,
            "evidence_packets_included": evidence_packets_included,
            "evidence_packets_omitted": max(0, evidence_packet_count_considered - evidence_packets_included),
        },
        "field_actions": {
            "candidates": "included_metadata_capped",
            "source_refs": "included_exact_capped",
            "source_windows": "reused_from_source_locator_handoff",
            "evidence_packets": (
                "reused_from_source_locator_handoff_with_opt_in_materialized_context_capped"
                if evidence_packet_text_included
                else "reused_from_source_locator_handoff_metadata_only_capped"
            ),
            "query_plan": "preserved",
            "stage_diagnostics": "preserved",
            "no_evidence": "preserved_searched_scope",
            "next_reads": "included_exact_capped",
            "expansion_actions": "included_exact_capped",
            "snippets": "omitted",
        },
        "claim_boundaries": {
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "all_history_completeness_claim_supported": False,
        },
    }


def compact_provenance_source_windows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_source_windows(candidates=candidates, next_reads=[], query_plan={})


def compact_provenance_no_evidence(no_evidence: dict[str, Any], source_locator_handoff: dict[str, Any]) -> dict[str, Any]:
    locator_meaning = source_locator_handoff.get("no_evidence_meaning", {})
    return {
        "needed": bool(no_evidence.get("needed")),
        "answer_scope": no_evidence.get("answer_scope") or locator_meaning.get("answer_scope"),
        "not_all_history_absence": locator_meaning.get("not_all_history_absence", True),
        "meaning": locator_meaning.get("meaning"),
        "candidate_rows_found": no_evidence.get("candidate_rows_found"),
        "searched_scope": no_evidence.get("searched_scope"),
        "not_searched_scope": no_evidence.get("not_searched_scope") or source_locator_handoff.get("not_searched_scope"),
        "why_not_confident": no_evidence.get("why_not_confident"),
    }


def build_compact_answerability(
    *,
    status: str,
    candidates: list[dict[str, Any]],
    source_locator_handoff: dict[str, Any],
    no_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Classify answer support separately from retrieval candidate presence."""
    source_plan = compact_answerability_source_plan(source_locator_handoff)
    retry_guidance = source_plan.get("retry_guidance") if isinstance(source_plan.get("retry_guidance"), dict) else {}
    source_tiers = source_plan.get("source_tiers") if isinstance(source_plan.get("source_tiers"), dict) else {}
    selected_counts = source_tiers.get("selected_counts") if isinstance(source_tiers.get("selected_counts"), dict) else {}
    deferred_count = int(source_plan.get("sources_deferred_budget") or 0)
    hard_required_selected = int(selected_counts.get("hard_required") or 0)
    source_plan_present = bool(source_plan.get("source") or source_plan.get("basis") or source_plan.get("source_tiers"))
    recoverability = compact_answerability_next_read_recovery(source_locator_handoff)
    coverage_rows = [candidate_coverage(candidate) for candidate in candidates]
    strongest_coverage = max(coverage_rows, key=lambda item: item["ratio"], default={"matched": 0, "total": 0, "ratio": 0.0})
    strong_candidates = [
        candidate
        for candidate, coverage in zip(candidates, coverage_rows)
        if candidate_is_strong_answerability_signal(candidate, coverage)
    ]
    weak_candidate_count = max(0, len(candidates) - len(strong_candidates))

    if candidates and not source_plan_present:
        support_class = ANSWERABILITY_SUPPORTED_IN_SEARCHED_SCOPE
        answerable = True
        grounding_state = "candidate_support_without_source_tier_plan"
        reasons = ["source_tier_answerability_gate_not_applicable"]
    elif status == "no_evidence" or not candidates:
        support_class = ANSWERABILITY_SEARCHED_SCOPE_NO_EVIDENCE
        answerable = False
        grounding_state = "no_candidate_support_in_searched_scope"
        reasons = ["no_candidate_rows_survived_searched_scope"]
    elif not strong_candidates and recoverability["recoverable_by_opening_next_reads"]:
        support_class = ANSWERABILITY_DEFERRED_SOURCE_NEEDED
        answerable = False
        grounding_state = "selected_representatives_require_source_window_read"
        reasons = [
            "candidate_presence_is_diagnostic_only",
            "distinctive_anchor_coverage_insufficient",
            "selected_source_windows_or_next_reads_available",
        ]
    elif not strong_candidates:
        support_class = ANSWERABILITY_SEARCHED_SCOPE_NO_EVIDENCE
        answerable = False
        grounding_state = "weak_or_broad_candidates_only"
        reasons = ["candidate_presence_is_diagnostic_only", "distinctive_anchor_coverage_insufficient"]
    elif deferred_count > 0 and hard_required_selected == 0:
        support_class = ANSWERABILITY_DEFERRED_SOURCE_NEEDED
        answerable = False
        grounding_state = "searched_scope_support_non_exhaustive_due_to_deferred_sources"
        reasons = ["source_plan_deferred_sources_remain", "no_hard_required_source_selected"]
    elif strong_candidates:
        support_class = ANSWERABILITY_SUPPORTED_IN_SEARCHED_SCOPE
        answerable = True
        grounding_state = "strong_candidate_support_in_selected_sources"
        reasons = ["strong_candidate_coverage_in_searched_scope"]
    else:
        support_class = ANSWERABILITY_INSUFFICIENT_GROUNDING
        answerable = False
        grounding_state = "support_could_not_be_established"
        reasons = ["candidate_presence_does_not_establish_answerability"]

    return {
        "schema_version": 1,
        "support_class": support_class,
        "answerability_status": support_class,
        "answerable_from_packet": answerable,
        "grounding_state": grounding_state,
        "candidate_presence_implies_answerability": False,
        "searched_scope_support": {
            "candidate_count": len(candidates),
            "strong_candidate_count": len(strong_candidates),
            "weak_or_related_candidate_count": weak_candidate_count,
            "strongest_candidate_coverage": {
                "matched_terms": int(strongest_coverage.get("matched") or 0),
                "total_terms": int(strongest_coverage.get("total") or 0),
                "ratio": round(float(strongest_coverage.get("ratio") or 0.0), 3),
            },
            "diagnostic_candidates_preserved": bool(candidates),
        },
        "source_tier_boundary": {
            "sources_selected": int(source_plan.get("sources_selected") or 0),
            "sources_deferred_budget": deferred_count,
            "retry_guidance_needed": bool(retry_guidance.get("needed", False)),
            "exhaustive_available": bool(source_plan.get("exhaustive_available", False)),
            "hard_required_selected": hard_required_selected,
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "next_read_recovery": recoverability,
        "no_evidence_boundary": {
            "needed": support_class == ANSWERABILITY_SEARCHED_SCOPE_NO_EVIDENCE,
            "answer_scope": (
                "not_found_in_searched_scope"
                if support_class == ANSWERABILITY_SEARCHED_SCOPE_NO_EVIDENCE
                else no_evidence.get("answer_scope")
            ),
            "not_all_history_absence": True,
            "deferred_source_count": deferred_count,
        },
        "related_candidate_policy": {
            "weak_candidates_remain_as_diagnostics": weak_candidate_count > 0,
            "source_refs_and_windows_preserved": True,
            "do_not_promote_weak_candidates_to_answer": True,
            "weak_candidates_can_require_next_read_before_answer": bool(
                recoverability["recoverable_by_opening_next_reads"]
            ),
        },
        "reasons": reasons,
        "claim_boundaries": {
            "retrieval_quality_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "all_history_completeness_claim_supported": False,
        },
    }


def compact_answerability_next_read_recovery(source_locator_handoff: dict[str, Any]) -> dict[str, Any]:
    source_windows = source_locator_handoff.get("source_windows") or []
    next_reads = source_locator_handoff.get("next_reads") or []
    packaging = source_locator_handoff.get("source_window_packaging") or {}
    stage_diagnostics = source_locator_handoff.get("stage_diagnostics") or {}
    grouping_counters: dict[str, Any] = {}
    for stage in stage_diagnostics.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage") == "source_grouping_diversity":
            grouping_counters = stage.get("counters") if isinstance(stage.get("counters"), dict) else {}
            break
    collapsed_runs = int(grouping_counters.get("collapsed_adjacent_run_count") or 0)
    suppressed_rows = int(grouping_counters.get("suppressed_adjacent_row_count") or 0)
    rows_dropped_by_source_cap = int(grouping_counters.get("rows_dropped_by_source_cap") or 0)
    source_selection_changed = collapsed_runs > 0 or suppressed_rows > 0 or rows_dropped_by_source_cap > 0
    window_count = int(packaging.get("candidate_window_count") or len(source_windows))
    next_read_count = len(next_reads)
    recoverable = bool(
        (window_count > 0 or next_read_count > 0)
        and (source_selection_changed or rows_dropped_by_source_cap > 0)
    )
    return {
        "recoverable_by_opening_next_reads": recoverable,
        "recovery_kind": "source_window_or_next_read" if recoverable else "none",
        "candidate_window_count": window_count,
        "next_read_count": next_read_count,
        "candidate_window_limit": int(packaging.get("candidate_window_limit") or 0),
        "candidate_window_omitted_count": int(packaging.get("candidate_window_omitted_count") or 0),
        "source_grouping_selection_changed": bool(source_selection_changed),
        "collapsed_adjacent_run_count": collapsed_runs,
        "suppressed_adjacent_row_count": suppressed_rows,
        "rows_dropped_by_source_cap": rows_dropped_by_source_cap,
        "raw_paths_included": False,
        "raw_text_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
    }


def compact_answerability_source_plan(source_locator_handoff: dict[str, Any]) -> dict[str, Any]:
    stage_plan = source_locator_handoff.get("stage_diagnostics", {}).get("source_plan")
    if isinstance(stage_plan, dict) and stage_plan:
        return stage_plan
    not_searched_plan = source_locator_handoff.get("not_searched_scope", {}).get("source_plan")
    if isinstance(not_searched_plan, dict):
        return not_searched_plan
    return {}


def candidate_coverage(candidate: dict[str, Any]) -> dict[str, Any]:
    for code in candidate.get("reason_codes") or []:
        if not isinstance(code, str) or not code.startswith("coverage:base_terms:"):
            continue
        _, _, fraction = code.partition("coverage:base_terms:")
        matched_text, _, total_text = fraction.partition("/")
        try:
            matched = int(matched_text)
            total = int(total_text)
        except ValueError:
            continue
        ratio = (matched / total) if total > 0 else 0.0
        return {"matched": matched, "total": total, "ratio": ratio}
    return {"matched": 0, "total": 0, "ratio": 0.0}


def candidate_is_strong_answerability_signal(candidate: dict[str, Any], coverage: dict[str, Any]) -> bool:
    if "base_term_full_coverage" == candidate.get("stage"):
        return True
    matched = int(coverage.get("matched") or 0)
    total = int(coverage.get("total") or 0)
    ratio = float(coverage.get("ratio") or 0.0)
    if total <= 0:
        return candidate.get("confidence") == "high"
    return matched >= 3 and ratio >= 0.5


def dedupe_provenance_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for ref in refs:
        key = (
            ref.get("kind"),
            ref.get("candidate_id"),
            json.dumps(ref.get("source_ref") or {}, sort_keys=True),
            ref.get("target"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def first_value(values: Any) -> Any:
    if isinstance(values, list) and values:
        return values[0]
    return None


def build_candidate_rows(search_result: dict) -> list[CandidateRow]:
    rows: list[CandidateRow] = []
    for group in search_result.get("groups", []):
        for match in group.get("matches", []):
            reasons = list(match.get("reasons") or [])
            stages = stages_from_reasons(reasons)
            matched_terms = matched_terms_from_reasons(reasons)
            stages = stages or ["unsupported_or_score_only"]
            rows.append(
                CandidateRow(
                    rank=0,
                    candidate_id="",
                    source_ref={
                        "path": match.get("path") or "",
                        "line": int(match.get("line") or 0),
                        "session": match.get("session") or group.get("session"),
                    },
                    path=match.get("path") or "",
                    line=int(match.get("line") or 0),
                    timestamp=match.get("timestamp"),
                    timestamp_utc=timestamp_utc(match.get("timestamp")),
                    role=match.get("role"),
                    session=match.get("session") or group.get("session"),
                    cwd=match.get("cwd"),
                    snippet=match.get("snippet") or "",
                    score=float(match.get("score") or 0),
                    confidence=confidence_for_score(float(match.get("score") or 0), stages, reasons),
                    matched_terms=matched_terms,
                    stages=stages,
                    primary_stage=primary_stage(stages),
                    reasons=reasons,
                    reason_codes=reason_codes_from_reasons(reasons),
                    artifacts=list(match.get("artifacts") or [])[:20],
                    tasks=list(match.get("tasks") or [])[:20],
                )
            )
    if not search_result.get("preserve_match_order"):
        rows.sort(key=lambda item: (item.score, item.timestamp or "", item.path, -item.line), reverse=True)
    return [replace(row, rank=index + 1, candidate_id=f"c{index + 1:03d}") for index, row in enumerate(rows)]


def build_timeline(candidates: list[CandidateRow]) -> list[CandidateRow]:
    return sorted(
        candidates,
        key=lambda item: (timestamp_sort_key(item.timestamp_utc), item.path, item.line),
    )


def timestamp_sort_key(value: str | None) -> str:
    if not value:
        return "9999-12-31T23:59:59+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def timestamp_utc(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def stages_from_reasons(reasons: list[str]) -> list[str]:
    stages: set[str] = set()
    for reason in reasons:
        if reason.startswith("text:"):
            stages.add(stage_from_text_reason(reason))
        elif reason.startswith("fuzzy:"):
            stages.add("typo_fuzzy")
        elif reason.startswith("role:"):
            stages.add("role")
        elif reason == "date-window":
            stages.add("date_window")
        elif reason == "cwd":
            stages.add("cwd_project")
        elif reason.startswith("project:"):
            stages.add("cwd_project")
        elif reason.startswith("coverage:base_terms:"):
            stages.add(coverage_stage(reason))
        elif reason.startswith("phrase:"):
            stages.add("exact_phrase")
        elif reason.startswith("penalty:"):
            stages.add("downgraded_generic")
        elif reason.startswith("artifact"):
            stages.add("artifact_path")
        elif reason == "bounded-no-term":
            stages.add("bounded_no_term")
    return sorted(stages)


def primary_stage(stages: list[str]) -> str:
    for preferred in (
        "exact_phrase",
        "base_term_full_coverage",
        "artifact_path",
        "normalized_terms",
        "synonym_paraphrase",
        "typo_fuzzy",
        "semantic_local",
        "date_window",
        "cwd_project",
        "role",
    ):
        if preferred in stages:
            return preferred
    return stages[0] if stages else "unsupported"


def reason_codes_from_reasons(reasons: list[str]) -> list[str]:
    codes = []
    for reason in reasons:
        if reason.startswith("text:"):
            codes.append(stage_reason_code(stage_from_text_reason(reason)))
        elif reason.startswith("fuzzy:"):
            codes.append("typo_fuzzy")
        elif reason.startswith("role:"):
            codes.append("role_match")
        elif reason == "date-window":
            codes.append("date_window")
        elif reason == "cwd":
            codes.append("project_hint")
        elif reason.startswith("project:"):
            codes.append(reason)
        elif reason.startswith("coverage:base_terms:"):
            codes.append(reason)
        elif reason.startswith("phrase:"):
            codes.append(reason)
        elif reason.startswith("penalty:"):
            codes.append(reason)
        elif reason.startswith("artifact"):
            codes.append("artifact_path")
        elif reason == "bounded-no-term":
            codes.append("bounded_no_term")
        else:
            codes.append(reason)
    return sorted(set(codes))


def matched_terms_from_reasons(reasons: list[str]) -> list[str]:
    terms = []
    for reason in reasons:
        if reason.startswith("text:"):
            parts = reason.split(":", 2)
            terms.append(parts[2] if len(parts) == 3 else parts[1])
        elif reason.startswith("fuzzy:"):
            parts = reason.split(":", 2)
            if len(parts) == 3:
                terms.append(parts[2])
    return sorted(set(terms))


def confidence_for_score(score: float, stages: list[str], reasons: list[str] | None = None) -> str:
    stage_set = set(stages)
    reason_set = set(reasons or [])
    has_generic_penalty = any(reason.startswith("penalty:") for reason in reason_set)
    has_supporting_evidence = bool(
        {"exact_phrase", "base_term_full_coverage", "artifact_path", "cwd_project"} & stage_set
    )
    if has_generic_penalty and not has_supporting_evidence:
        return "low" if score > 0 else "weak"
    if score >= 22 and has_supporting_evidence:
        return "high"
    if score >= 12:
        return "medium"
    if score > 0:
        return "low"
    return "weak"


def stage_from_text_reason(reason: str) -> str:
    parts = reason.split(":", 2)
    if len(parts) == 3:
        return normalize_stage(parts[1])
    return "normalized_terms"


def normalize_stage(stage: str) -> str:
    aliases = {
        "base": "normalized_terms",
        "synonym": "synonym_paraphrase",
        "artifact": "artifact_path",
        "role_filter": "role",
    }
    return aliases.get(stage, stage)


def coverage_stage(reason: str) -> str:
    value = reason.rsplit(":", 1)[-1]
    try:
        covered_text, total_text = value.split("/", 1)
        covered = int(covered_text)
        total = int(total_text)
    except ValueError:
        return "base_term_coverage"
    if total > 1 and covered == total:
        return "base_term_full_coverage"
    if covered > 0:
        return "base_term_partial_coverage"
    return "base_term_no_coverage"


def stage_reason_code(stage: str) -> str:
    codes = {
        "exact_phrase": "exact_phrase",
        "normalized_terms": "normalized_term",
        "synonym_paraphrase": "synonym_paraphrase",
        "artifact_path": "artifact_path",
        "semantic_local": "semantic_local",
        "task_slug": "normalized_term",
        "preference": "synonym_paraphrase",
    }
    return codes.get(stage, stage)


def build_plan_sources(
    source_plan: dict,
    search_result: dict,
    manifest_report: dict | None,
    *,
    include_source_scope: bool = False,
) -> dict:
    sources = source_plan["sources"]
    cap_hit = cap_hit_status(
        warnings=list(search_result.get("warnings", []) or sources.get("warnings", [])),
        limits=sources.get("limits", {}),
    )
    wrong_root_warnings = [
        item["reason"]
        for item in sources.get("rejected_roots", [])
        if "root" in item.get("reason", "") or "dangerous" in item.get("reason", "")
    ]
    payload = {
        "roots": sources.get("roots", []),
        "rejected_roots": sources.get("rejected_roots", []),
        "limits": sources.get("limits", {}),
        "warnings": search_result.get("warnings", []),
        "blind_spots": search_result.get("blind_spots", []),
        "wrong_root_warnings": sorted(set(wrong_root_warnings)),
        "searched_files": search_result.get("searched_files", 0),
        "searched_bytes": search_result.get("searched_bytes", 0),
        "ordering": "scanner score desc, timestamp desc; timeline section is timestamp asc",
    }
    if include_source_scope:
        source_scope = build_source_scope_summary(source_plan, search_result)
        payload["source_scope"] = source_scope
        payload["cap_hit"] = source_scope["cap_hit"]
    elif cap_hit["hit"]:
        payload["cap_hit"] = cap_hit
    if manifest_report:
        report = manifest_report.get("manifest_report", {})
        payload["manifest_shortlist"] = {
            "entry_count": report.get("manifest", {}).get("entry_count", 0),
            "metrics": report.get("shortlist", {}).get("metrics", {}),
            "selected_entries": report.get("shortlist", {}).get("selected_entries", [])[:INLINE_CANDIDATE_LIMIT],
            "excluded_entries": report.get("shortlist", {}).get("excluded_entries", [])[:INLINE_CANDIDATE_LIMIT],
        }
        duplicate_risk = report.get("duplicate_risk")
        if duplicate_risk:
            payload["duplicate_risk"] = {
                "status": duplicate_risk.get("status"),
                "summary": duplicate_risk.get("summary", {}),
                "groups": duplicate_risk.get("groups", [])[:INLINE_CANDIDATE_LIMIT],
                "limitations": duplicate_risk.get("limitations", []),
            }
    return payload


def build_source_locator_handoff(
    *,
    source_plan: dict,
    search_result: dict,
    candidates: list[dict[str, Any]] | list[CandidateRow] | None = None,
    next_reads: list[dict[str, Any]] | None = None,
    source_family: str = "local_ai_conversation_jsonl",
    question_family: dict[str, Any] | None = None,
    product_path: str = "find",
    no_evidence_report: dict[str, Any] | None = None,
    retrieval_pressure: dict[str, Any] | None = None,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
    evidence_context_window: int = 0,
) -> dict[str, Any]:
    """Build the stable agent handoff for deciding where to read next."""
    source_scope = build_source_scope_summary(source_plan, search_result)
    sources = source_plan.get("sources", {})
    cap_hit = compact_handoff_caps(source_scope["cap_hit"])
    candidate_rows = list(candidates or [])
    candidate_count = len(candidate_rows) or sum(len(group.get("matches", [])) for group in search_result.get("groups", []))
    next_read_rows = list(next_reads or [])
    if not next_read_rows:
        next_read_rows = next_reads_from_candidates(
            candidate_rows,
            expansion_action_context=expansion_action_context,
        )
    query_plan = source_plan.get("query", {}).get("query_plan") or {}
    source_window_result = build_source_window_package(
        candidates=candidate_rows,
        next_reads=next_read_rows,
        query_plan=query_plan,
        expansion_action_context=expansion_action_context,
        include_context_actions=include_context_actions,
    )
    source_windows = source_window_result["windows"]
    source_window_selection_policy = source_window_result["selection_policy"]
    evidence_packet_result = build_evidence_packet_package(
        candidates=candidate_rows,
        search_result=search_result,
        source_plan=source_plan,
        evidence_context_window=evidence_context_window,
    )
    evidence_packets = evidence_packet_result["packets"]
    evidence_packet_packaging = evidence_packet_result["packaging"]
    candidate_window_omitted_count = max(0, candidate_count - len(source_windows))
    source_window_packaging = {
        "schema_version": 1,
        "status": "packaged" if source_windows else "no_candidate_windows",
        "candidate_window_count": len(source_windows),
        "candidate_window_limit": SOURCE_WINDOW_LIMIT,
        "candidate_count_considered": candidate_count,
        "candidate_window_omitted_count": candidate_window_omitted_count,
        "builder": "source_locator_handoff",
        "raw_text_included": False,
        "source_window_text_included": False,
        "open_next_metadata_only": True,
        "selection_policy": source_window_selection_policy,
        "budget_policy": {
            "ordering_basis": "collapse_then_group_head_allocate_then_supported_fill",
            "source_windows": "capped",
            "source_refs": "included_exact",
            "next_reads": "included_exact_for_emitted_windows",
            "reason_codes": "capped",
            "text_or_snippets": "omitted",
        },
        "field_budget_diagnostics": [
            {"field": "source_ref", "action": "included", "preserves_exact_refs": True},
            {"field": "target", "action": "included", "preserves_exact_refs": True},
            {"field": "next_read", "action": "included", "preserves_exact_refs": True},
            {"field": "expansion_actions", "action": "included", "preserves_exact_refs": True},
            {
                "field": "context_actions",
                "action": "included" if include_context_actions else "omitted",
                "preserves_exact_refs": True,
                "reason": "compact_provenance_handoff_requested"
                if include_context_actions
                else "default_output_size_guard",
            },
            {"field": "reason_codes", "action": "capped", "limit": SOURCE_WINDOW_REASON_CODE_LIMIT},
            {
                "field": "text_or_snippets",
                "action": "omitted",
                "reason": "metadata_only_source_windows",
            },
        ],
    }
    payload = {
        "schema_version": 1,
        "query_plan": query_plan,
        "source_family": {
            "primary": source_family,
            "selected_kinds": sorted({root.get("kind", "unknown") for root in sources.get("roots", [])}),
        },
        "question_family": question_family
        or {
            "primary": "source_location",
            "detected_supported": ["source_location", "bounded_no_evidence"],
        },
        "selected_roots": source_scope["selected_roots"],
        "rejected_roots": source_scope["excluded_scope"]["rejected_roots"],
        "caps": cap_hit,
        "warnings": sanitized_handoff_messages(search_result.get("warnings", []) or sources.get("warnings", [])),
        "blind_spots": source_locator_blind_spots(source_scope, search_result, sources),
        "not_searched_scope": compact_handoff_not_searched_scope(source_scope["not_searched_scope"], cap_hit),
        "candidate_ordering": {
            "strategy": "score_desc_timestamp_desc; timeline_timestamp_asc",
            "candidate_count": candidate_count,
            "top_candidates": compact_handoff_candidates(candidate_rows),
        },
        "source_windows": source_windows,
        "source_window_packaging": source_window_packaging,
        "next_reads": next_read_rows[:COMPACT_PROVENANCE_NEXT_READ_LIMIT],
        "no_evidence_meaning": {
            "answer_scope": "not_found_in_searched_scope",
            "not_all_history_absence": True,
            "meaning": "No match survived selected roots, caps, filters, and parser scope.",
        },
        "privacy_boundaries": {
            "root_metadata_raw_paths_included": False,
            "raw_private_text_or_bridge_payload_included": False,
            "raw_private_text_or_snippets_in_source_windows_included": False,
            "raw_private_text_or_snippets_in_evidence_packets_included": bool(
                evidence_packet_packaging.get("raw_text_included", False)
            ),
            "source_window_text_included": False,
            "evidence_context_window_requested": max(0, int(evidence_context_window or 0)) > 0,
            "vectors_indexes_or_timestamp_sidecars_created": False,
            "candidate_source_refs_may_point_to_local_transcripts": bool(candidate_count),
        },
        "claim_boundaries": {
            "root_cause_claim": "not_claimed",
            "no_broad_value_speed_token_user_benefit_or_jsonl_beats_default_claim": True,
            "measurement_deferred_to": ["jsh-601c", "jsh-601d", "jsh-601e"],
            "no_evidence_is_scope_limited": True,
        },
        "non_default_routes": ["embeddings:not_run", "timestamp_sidecars:not_run", "semantic_linter_runtime:not_used", "private_bridges:not_included", "broad_drive_scans:guarded"],
    }
    if evidence_packet_packaging.get("status") != "not_applicable":
        payload["evidence_packets"] = evidence_packets
        payload["evidence_packet_packaging"] = evidence_packet_packaging
    stage_diagnostics = build_stage_diagnostics(
        product_path=product_path,
        query_plan=query_plan,
        search_result=search_result,
        retrieval_pressure=retrieval_pressure,
        source_window_packaging=source_window_packaging,
        evidence_packet_summary=evidence_packet_packaging,
        no_evidence_report=no_evidence_report,
        compact_handoff_requested=False,
    )
    if stage_diagnostics:
        payload["stage_diagnostics"] = stage_diagnostics
    return payload


def build_source_windows(
    *,
    candidates: list[dict[str, Any]] | list[CandidateRow],
    next_reads: list[dict[str, Any]],
    query_plan: dict[str, Any] | None,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    return build_source_window_package(
        candidates=candidates,
        next_reads=next_reads,
        query_plan=query_plan,
        expansion_action_context=expansion_action_context,
        include_context_actions=include_context_actions,
    )["windows"]


def build_source_window_package(
    *,
    candidates: list[dict[str, Any]] | list[CandidateRow],
    next_reads: list[dict[str, Any]],
    query_plan: dict[str, Any] | None,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    next_read_by_candidate = {
        str(read.get("candidate_id") or ""): read for read in next_reads if read.get("candidate_id") is not None
    }
    primary_intent = (query_plan or {}).get("primary_intent") or "generic_history"
    window_needs = list((query_plan or {}).get("source_window_needs") or [])
    normalized_candidates = normalize_source_window_candidates(candidates)
    selection_result = select_source_window_candidates(
        normalized_candidates,
        query_plan=query_plan or {},
        primary_intent=primary_intent,
    )
    selected_candidates = selection_result["selected"]
    intent_preferred_ids = {
        str(item["data"].get("candidate_id") or "")
        for item in normalized_candidates
        if source_window_candidate_matches_intent(item["data"], query_plan or {}, primary_intent)
    }
    for index, selected in enumerate(selected_candidates, 1):
        data = selected["data"]
        source_ref = data["source_ref"]
        target = source_ref_target(source_ref)
        candidate_id = str(data.get("candidate_id") or "")
        next_read = next_read_by_candidate.get(candidate_id) or {
            "kind": "jsonl_source_ref",
            "target": target,
            "candidate_id": candidate_id,
            "source_ref": source_ref,
            "reason": "open source_ref target for surrounding transcript context",
        }
        next_read = {key: value for key, value in next_read.items() if key not in {"expansion_actions", "context_actions"}}
        window = {
            "schema_version": 1,
            "window_id": f"sw{index:03d}",
            "candidate_id": candidate_id,
            "source_ref": source_ref,
            "target": target,
            "intent": primary_intent,
            "window_kind": source_window_kind(primary_intent, window_needs),
            "selection_reason_code": selected["selection_reason_code"],
            "role": data.get("role"),
            "timestamp_utc": data.get("timestamp_utc"),
            "cwd": data.get("cwd"),
            "project_hint": project_hint_from_cwd(data.get("cwd")),
            "reason_codes": list(data.get("reason_codes", []))[:SOURCE_WINDOW_REASON_CODE_LIMIT],
            "next_read": next_read,
            "expansion_actions": read_more_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            ),
            "text_included": False,
            "source_window_text_included": False,
            "reason": "open source_ref target for surrounding transcript context",
            "open_instruction": "open source_ref target for surrounding transcript context",
            "searched_scope_boundary": {
                "answer_scope": "candidate_from_searched_scope",
                "not_all_history_absence": True,
            },
        }
        if include_context_actions:
            window["context_actions"] = context_actions_for_source_ref(
                source_ref,
                candidate_id=candidate_id,
                expansion_action_context=expansion_action_context,
            )
        windows.append(window)
    selected_ids = {str(item["data"].get("candidate_id") or "") for item in selected_candidates}
    selected_groups = {str(item["group_key"]) for item in selected_candidates}
    omitted_candidates = selection_result["omitted"]
    omission_reason_counts = dict(Counter(str(item.get("omission_reason") or "omitted") for item in omitted_candidates))
    selection_policy = {
        "schema_version": 2,
        "allocator": "group_aware_source_window_allocator",
        "allocator_version": "jsh-851.v1",
        "ordering_basis": "collapse_then_group_head_allocate_then_supported_fill",
        "candidate_count_considered": len(normalized_candidates),
        "selected_count": len(selected_candidates),
        "omitted_count": len(omitted_candidates),
        "distinct_source_count_considered": len({str(item["source_key"]) for item in normalized_candidates}),
        "distinct_source_count_selected": len({str(item["source_key"]) for item in selected_candidates}),
        "distinct_group_count_considered": len({str(item["group_key"]) for item in normalized_candidates}),
        "distinct_group_count_selected": len(selected_groups),
        "distinct_group_count_omitted": len({str(item["group_key"]) for item in omitted_candidates}),
        "intent_preferred_candidate_count": len(intent_preferred_ids),
        "intent_preferred_window_count": len(selected_ids & intent_preferred_ids),
        "source_head_window_count": sum(
            1
            for item in selected_candidates
            if item["selection_reason_code"] in {"intent_preferred_distinct_source", "distinct_source"}
        ),
        "fill_window_count": sum(
            1 for item in selected_candidates if item["selection_reason_code"] == "existing_order_fill"
        ),
        "dominant_group_second_window_count": selection_result["dominant_group_second_window_count"],
        "omission_reason_counts": omission_reason_counts,
        "collapse": {
            "policy": "metadata_group_key_first_candidate_is_group_head",
            "grouping_fields": ["source", "session", "day"],
            "candidate_count_considered": len(normalized_candidates),
            "distinct_group_count_considered": len({str(item["group_key"]) for item in normalized_candidates}),
            "collapsed_candidate_count": max(
                0, len(normalized_candidates) - len({str(item["group_key"]) for item in normalized_candidates})
            ),
        },
        "allocation_passes": [
            "intent_group_heads" if selection_result["intent_pass_active"] else "group_heads",
            "represented_group_supported_fill",
        ],
        "omitted_candidate_facts": selection_policy_omitted_candidate_facts(omitted_candidates),
        "raw_text_included": False,
        "snippets_included": False,
    }
    return {"windows": windows, "selection_policy": selection_policy}


def build_evidence_packet_package(
    *,
    candidates: list[dict[str, Any]] | list[CandidateRow],
    search_result: dict[str, Any],
    source_plan: dict[str, Any] | None = None,
    evidence_context_window: int = 0,
) -> dict[str, Any]:
    evidence_context_window = max(0, int(evidence_context_window or 0))
    retrieval_diagnostics = search_result.get("retrieval_diagnostics") or {}
    retrieval_mode = retrieval_diagnostics.get("retrieval_mode") or search_result.get("retrieval_mode")
    if retrieval_mode != "fielded-bm25":
        return {
            "packets": [],
            "packaging": {
                "schema_version": 1,
                "status": "not_applicable",
                "retrieval_mode": retrieval_mode or "package",
                "packet_limit": EVIDENCE_PACKET_LIMIT,
                "raw_text_included": False,
                "snippets_included": False,
                "evidence_context_window": 0,
            },
        }

    normalized = normalize_evidence_packet_candidates(candidates)
    groups: dict[str, dict[str, Any]] = {}
    duplicate_child_count = 0
    for item in normalized:
        group_key = str(item["group_key"])
        group = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "source_key": item["source_key"],
                "session": item["session"],
                "day": item["day"],
                "children": [],
                "seen_child_refs": set(),
                "duplicate_child_count": 0,
            },
        )
        child_ref_key = evidence_packet_child_ref_key(item)
        if child_ref_key in group["seen_child_refs"]:
            duplicate_child_count += 1
            group["duplicate_child_count"] += 1
            continue
        group["seen_child_refs"].add(child_ref_key)
        group["children"].append(item)

    ordered_groups = sorted(
        groups.values(),
        key=lambda group: (
            min(int(child["rank"]) for child in group["children"]),
            str(group["source_key"]),
            min(evidence_packet_line_sort_key(child["source_ref"]) for child in group["children"]),
            str(group["group_key"]),
        ),
    )
    packets: list[dict[str, Any]] = []
    omitted_packet_groups = max(0, len(ordered_groups) - EVIDENCE_PACKET_LIMIT)
    omitted_children_by_packet_limit = sum(len(group["children"]) for group in ordered_groups[EVIDENCE_PACKET_LIMIT:])
    omitted_children_by_child_limit = 0
    omitted_context_records = 0
    materialized_total_chars_remaining = EVIDENCE_PACKET_MATERIALIZED_CONTEXT_TOTAL_CHAR_LIMIT
    materialized_context_included = 0
    materialized_context_omitted = 0
    materialized_context_truncated = 0
    materialized_context_read_error_count = 0
    context_source_counters = new_context_source_counters()

    for packet_index, group in enumerate(ordered_groups[:EVIDENCE_PACKET_LIMIT], 1):
        children = sorted(
            group["children"],
            key=lambda item: (
                evidence_packet_line_sort_key(item["source_ref"]),
                int(item["rank"]),
                str(item["candidate_id"]),
            ),
        )
        hit_children = [evidence_packet_hit_child(child) for child in children[:EVIDENCE_PACKET_CHILD_LIMIT]]
        omitted_children_by_child_limit += max(0, len(children) - len(hit_children))
        context_records_all = [evidence_packet_context_record(child) for child in children]
        context_records = context_records_all[:EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT]
        omitted_context_records += max(0, len(context_records_all) - len(context_records))
        materialized_context = materialize_evidence_packet_context(
            hit_children=hit_children,
            source_plan=source_plan or {},
            search_result=search_result,
            evidence_context_window=evidence_context_window,
            total_chars_remaining=materialized_total_chars_remaining,
            context_source_counters=context_source_counters,
        )
        materialized_total_chars_remaining = int(materialized_context.pop("_total_chars_remaining"))
        materialized_counts = materialized_context.get("counts") or {}
        materialized_context_included += int(materialized_counts.get("included_record_count") or 0)
        materialized_context_omitted += int(materialized_counts.get("omitted_or_truncated_count") or 0)
        materialized_context_truncated += int(materialized_counts.get("truncated_text_record_count") or 0)
        if materialized_context.get("status") in {"read_error", "source_not_found_or_out_of_scope"}:
            materialized_context_read_error_count += 1
        line_values = [
            evidence_packet_numeric_line(child["source_ref"].get("line"))
            for child in children
            if evidence_packet_numeric_line(child["source_ref"].get("line")) is not None
        ]
        days = sorted({str(child.get("day")) for child in children if child.get("day")})
        packet = {
            "schema_version": 1,
            "packet_id": f"ep{packet_index:03d}",
            "packet_kind": "source_window_parent",
            "parent_ref": {
                "kind": "metadata_only_source_window",
                "source_key": group["source_key"],
                "target": source_ref_target(children[0]["source_ref"]),
                "session_present": bool(group["session"]),
                "day": days[0] if len(days) == 1 else None,
                "days": days[:EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT],
                "source_order_bounds": {
                    "start_line": min(line_values) if line_values else None,
                    "end_line": max(line_values) if line_values else None,
                    "basis": "selected_child_candidate_source_order",
                },
            },
            "ordering": {
                "best_child_rank": min(int(child["rank"]) for child in children),
                "packet_order_basis": "best_child_rank_then_source_target_then_start_line_then_packet_id",
                "score_weights_used": False,
            },
            "hit_children": hit_children,
            "context_records": context_records,
            "bounds": {
                "packet_limit": EVIDENCE_PACKET_LIMIT,
                "child_limit_per_packet": EVIDENCE_PACKET_CHILD_LIMIT,
                "context_record_limit_per_packet": EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT,
                "included_child_count": len(hit_children),
                "omitted_child_count": max(0, len(children) - len(hit_children)),
                "deduplicated_child_count": int(group.get("duplicate_child_count") or 0),
                "included_context_record_count": len(context_records),
                "omitted_context_record_count": max(0, len(context_records_all) - len(context_records)),
                "source_order_context_metadata_only": True,
                "materialized_context_window": evidence_context_window,
                "materialized_context_record_limit_per_packet": EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT,
                "materialized_context_char_limit_per_packet": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_PACKET_CHAR_LIMIT,
                "materialized_context_char_limit_total": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_TOTAL_CHAR_LIMIT,
                "truncation_reason": evidence_packet_truncation_reason(
                    omitted_children=max(0, len(children) - len(hit_children)),
                    omitted_context_records=max(
                        0,
                        len(context_records_all) - len(context_records),
                    )
                    + int(materialized_counts.get("omitted_or_truncated_count") or 0),
                    omitted_packet_groups=omitted_packet_groups if packet_index == EVIDENCE_PACKET_LIMIT else 0,
                ),
                "continuation_available_via_next_reads": True,
            },
            "privacy": {
                "raw_text_included": bool(materialized_context.get("raw_text_included", False)),
                "snippets_included": False,
                "metadata_only": not bool(materialized_context.get("raw_text_included", False)),
                "materialized_context_opt_in": evidence_context_window > 0,
            },
        }
        if evidence_context_window > 0:
            packet["materialized_context"] = materialized_context
        packets.append(packet)

    hit_child_count = sum(len(packet["hit_children"]) for packet in packets)
    context_record_count = sum(len(packet["context_records"]) for packet in packets)
    omitted_child_count = omitted_children_by_packet_limit + omitted_children_by_child_limit
    packaging = {
        "schema_version": 1,
        "status": "packaged" if packets else "no_candidate_packets",
        "retrieval_mode": "fielded-bm25",
        "packet_count": len(packets),
        "packet_limit": EVIDENCE_PACKET_LIMIT,
        "packet_group_count_considered": len(ordered_groups),
        "packet_group_count_omitted": omitted_packet_groups,
        "hit_child_count": hit_child_count,
        "hit_child_limit_per_packet": EVIDENCE_PACKET_CHILD_LIMIT,
        "context_record_count": context_record_count,
        "context_record_limit_per_packet": EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT,
        "candidate_count_considered": len(normalized),
        "deduplicated_child_count": duplicate_child_count,
        "omitted_child_count": omitted_child_count,
        "omitted_context_record_count": omitted_context_records,
        "materialized_context_window": evidence_context_window,
        "materialized_context_record_count": materialized_context_included,
        "materialized_context_omitted_or_truncated_count": materialized_context_omitted,
        "materialized_context_truncated_text_record_count": materialized_context_truncated,
        "materialized_context_read_error_count": materialized_context_read_error_count,
        "materialized_context_source_counters": sanitize_context_source_counters(context_source_counters),
        "materialized_context_total_char_limit": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_TOTAL_CHAR_LIMIT,
        "materialized_context_packet_char_limit": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_PACKET_CHAR_LIMIT,
        "materialized_context_record_char_limit": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_RECORD_CHAR_LIMIT,
        "ordering_basis": "best_child_rank_then_source_target_then_start_line_then_packet_id",
        "grouping_fields": ["source", "session"],
        "metadata_fields": ["day"],
        "raw_text_included": materialized_context_included > 0,
        "snippets_included": False,
        "source_order_context_metadata_only": evidence_context_window == 0,
        "materialized_context_opt_in": evidence_context_window > 0,
        "claim_boundaries": {
            "ranking_changed": False,
            "score_weights_used": False,
            "retrieval_units_remain_child_hits": True,
            "candidate_generation_changed": False,
            "default_behavior_changed": False,
        },
    }
    return {"packets": packets, "packaging": packaging}


def materialize_evidence_packet_context(
    *,
    hit_children: list[dict[str, Any]],
    source_plan: dict[str, Any],
    search_result: dict[str, Any] | None = None,
    evidence_context_window: int,
    total_chars_remaining: int,
    context_source_counters: dict[str, int | bool] | None = None,
) -> dict[str, Any]:
    evidence_context_window = max(0, int(evidence_context_window or 0))
    base = {
        "schema_version": 1,
        "requested": evidence_context_window > 0,
        "window": evidence_context_window,
        "scope": "same_source",
        "records": [],
        "raw_text_included": False,
        "text_field": "text",
        "hit_children_separated": True,
        "limits": {
            "records_per_packet": EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT,
            "chars_per_record": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_RECORD_CHAR_LIMIT,
            "chars_per_packet": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_PACKET_CHAR_LIMIT,
            "chars_total": EVIDENCE_PACKET_MATERIALIZED_CONTEXT_TOTAL_CHAR_LIMIT,
        },
        "_total_chars_remaining": max(0, int(total_chars_remaining or 0)),
    }
    if evidence_context_window <= 0:
        return {
            **base,
            "status": "not_requested",
            "counts": materialized_context_counts(),
        }
    child_refs = [child.get("source_ref") or {} for child in hit_children if child.get("source_ref")]
    child_lines = [
        line
        for line in (evidence_packet_numeric_line(ref.get("line")) for ref in child_refs)
        if line is not None
    ]
    if not child_refs or not child_lines:
        return {
            **base,
            "status": "missing_child_source_ref",
            "counts": materialized_context_counts(),
        }
    source_path = resolve_evidence_context_source_path(child_refs[0], source_plan=source_plan)
    if source_path is None:
        requested_count = len(
            {
                line
                for child_line in child_lines
                for line in range(child_line - evidence_context_window, child_line + evidence_context_window + 1)
                if line >= 1
            }
        )
        return {
            **base,
            "status": "source_not_found_or_out_of_scope",
            "counts": materialized_context_counts(requested_count=requested_count, omitted_or_truncated_count=requested_count),
        }
    records, source_status = evidence_context_records_from_fielded_cache(
        source_path,
        search_result=search_result or {},
        context_source_counters=context_source_counters,
    )
    if records is None:
        if context_source_counters is not None:
            context_source_counters["context_source_files_opened"] = (
                int(context_source_counters["context_source_files_opened"]) + 1
            )
            context_source_counters["fallback_reparses"] = int(context_source_counters["fallback_reparses"]) + 1
            try:
                context_source_counters["context_bytes_read_for_fallback"] = (
                    int(context_source_counters["context_bytes_read_for_fallback"]) + int(source_path.stat().st_size)
                )
            except OSError:
                pass
        try:
            records = list(iter_records_for_file(source_path, evidence_context_source_kind(source_path, source_plan), timezone.utc))
        except (AdapterReadError, OSError) as exc:
            if context_source_counters is not None:
                context_source_counters["fallback_reparse_failures"] = (
                    int(context_source_counters["fallback_reparse_failures"]) + 1
                )
                context_source_counters["stale_or_invalidated_fallback_count"] = (
                    int(context_source_counters["stale_or_invalidated_fallback_count"]) + 1
                    if source_status in {"missing_source_rows", "empty_source_rows"}
                    else int(context_source_counters["stale_or_invalidated_fallback_count"])
                )
            requested_count = len(
                {
                    line
                    for child_line in child_lines
                    for line in range(child_line - evidence_context_window, child_line + evidence_context_window + 1)
                    if line >= 1
                }
            )
            return {
                **base,
                "status": "read_error",
                "error": str(exc),
                "counts": materialized_context_counts(requested_count=requested_count, omitted_or_truncated_count=requested_count),
            }

    total_records = len(records)
    requested_lines = sorted(
        {
            line
            for child_line in child_lines
            for line in range(
                max(1, child_line - evidence_context_window),
                min(total_records, child_line + evidence_context_window) + 1,
            )
        }
    )
    selected_lines = requested_lines[:EVIDENCE_PACKET_CONTEXT_RECORD_LIMIT]
    omitted_by_record_limit = max(0, len(requested_lines) - len(selected_lines))
    unavailable_before_count = sum(max(0, evidence_context_window - (child_line - 1)) for child_line in child_lines)
    unavailable_after_count = sum(max(0, child_line + evidence_context_window - total_records) for child_line in child_lines)
    child_line_set = set(child_lines)
    record_by_line = {int(record.line): record for record in records}
    packet_chars_remaining = min(
        EVIDENCE_PACKET_MATERIALIZED_CONTEXT_PACKET_CHAR_LIMIT,
        max(0, int(total_chars_remaining or 0)),
    )
    materialized_records: list[dict[str, Any]] = []
    omitted_by_char_limit = 0
    truncated_text_record_count = 0
    for line in selected_lines:
        if packet_chars_remaining <= 0:
            omitted_by_char_limit += 1
            continue
        record = record_by_line.get(line)
        if record is None:
            continue
        context_record = materialized_context_record(
            record,
            hit_child=line in child_line_set,
            char_limit=min(EVIDENCE_PACKET_MATERIALIZED_CONTEXT_RECORD_CHAR_LIMIT, packet_chars_remaining),
        )
        text_len = len(str(context_record.get("text") or ""))
        packet_chars_remaining -= text_len
        if context_record["text_truncated"]:
            truncated_text_record_count += 1
        materialized_records.append(context_record)
    included_chars = sum(len(str(record.get("text") or "")) for record in materialized_records)
    next_total_remaining = max(0, int(total_chars_remaining or 0) - included_chars)
    omitted_or_truncated = omitted_by_record_limit + omitted_by_char_limit + truncated_text_record_count
    if context_source_counters is not None:
        context_source_counters["omitted_or_truncated_context_records"] = (
            int(context_source_counters["omitted_or_truncated_context_records"]) + omitted_or_truncated
        )
    return {
        **base,
        "status": "materialized" if materialized_records else "no_records_materialized",
        "source_ref": {"path": str(source_path), "line": child_lines[0]},
        "source_order_preserved": True,
        "dedupe_key": "source_ref.line",
        "window_bounds": {
            "requested_child_lines": child_lines,
            "requested_line_start": requested_lines[0] if requested_lines else None,
            "requested_line_end": requested_lines[-1] if requested_lines else None,
            "included_line_start": materialized_records[0]["line"] if materialized_records else None,
            "included_line_end": materialized_records[-1]["line"] if materialized_records else None,
            "total_records_in_source": total_records,
            "line_is_one_based": True,
        },
        "counts": materialized_context_counts(
            requested_count=len(requested_lines),
            included_record_count=len(materialized_records),
            neighbor_before_count=sum(1 for line in requested_lines if line < min(child_lines)),
            neighbor_after_count=sum(1 for line in requested_lines if line > max(child_lines)),
            unavailable_before_count=unavailable_before_count,
            unavailable_after_count=unavailable_after_count,
            omitted_by_record_limit=omitted_by_record_limit,
            omitted_by_char_limit=omitted_by_char_limit,
            truncated_text_record_count=truncated_text_record_count,
            omitted_or_truncated_count=omitted_or_truncated,
            included_char_count=included_chars,
        ),
        "records": materialized_records,
        "raw_text_included": bool(materialized_records),
        "_total_chars_remaining": next_total_remaining,
    }


def materialized_context_counts(
    *,
    requested_count: int = 0,
    included_record_count: int = 0,
    neighbor_before_count: int = 0,
    neighbor_after_count: int = 0,
    unavailable_before_count: int = 0,
    unavailable_after_count: int = 0,
    omitted_by_record_limit: int = 0,
    omitted_by_char_limit: int = 0,
    truncated_text_record_count: int = 0,
    omitted_or_truncated_count: int = 0,
    included_char_count: int = 0,
) -> dict[str, int]:
    return {
        "requested_record_count": requested_count,
        "included_record_count": included_record_count,
        "neighbor_before_count": neighbor_before_count,
        "neighbor_after_count": neighbor_after_count,
        "unavailable_before_count": unavailable_before_count,
        "unavailable_after_count": unavailable_after_count,
        "omitted_by_record_limit": omitted_by_record_limit,
        "omitted_by_char_limit": omitted_by_char_limit,
        "truncated_text_record_count": truncated_text_record_count,
        "omitted_or_truncated_count": omitted_or_truncated_count,
        "included_char_count": included_char_count,
    }


def new_context_source_counters() -> dict[str, int | bool]:
    return {
        "context_records_served_from_segment_cache": 0,
        "context_records_served_from_in_memory_rows": 0,
        "context_source_files_opened": 0,
        "context_bytes_read_for_fallback": 0,
        "fallback_reparses": 0,
        "fallback_reparse_failures": 0,
        "omitted_or_truncated_context_records": 0,
        "stale_or_invalidated_fallback_count": 0,
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def sanitize_context_source_counters(counters: dict[str, Any] | None) -> dict[str, int | bool]:
    counters = counters if isinstance(counters, dict) else {}
    return {
        "context_records_served_from_segment_cache": int(
            counters.get("context_records_served_from_segment_cache") or 0
        ),
        "context_records_served_from_in_memory_rows": int(
            counters.get("context_records_served_from_in_memory_rows") or 0
        ),
        "context_source_files_opened": int(counters.get("context_source_files_opened") or 0),
        "context_bytes_read_for_fallback": int(counters.get("context_bytes_read_for_fallback") or 0),
        "fallback_reparses": int(counters.get("fallback_reparses") or 0),
        "fallback_reparse_failures": int(counters.get("fallback_reparse_failures") or 0),
        "omitted_or_truncated_context_records": int(counters.get("omitted_or_truncated_context_records") or 0),
        "stale_or_invalidated_fallback_count": int(counters.get("stale_or_invalidated_fallback_count") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def evidence_context_records_from_fielded_cache(
    source_path: Path,
    *,
    search_result: dict[str, Any],
    context_source_counters: dict[str, int | bool] | None,
) -> tuple[list[Any] | None, str]:
    context_index = search_result.get("_fielded_context_index") if isinstance(search_result, dict) else None
    if not isinstance(context_index, dict):
        return None, "no_context_index"
    records_by_source = context_index.get("records_by_source")
    if not isinstance(records_by_source, dict):
        return None, "invalid_context_index"
    rows = records_by_source.get(str(source_path))
    if rows is None:
        rows = records_by_source.get(str(source_path.expanduser()))
    if not isinstance(rows, list):
        return None, "missing_source_rows"
    if not rows:
        return None, "empty_source_rows"
    records: list[Any] = []
    source_kind = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_kind = str(row.get("source_kind") or source_kind)
        record_payload = row.get("record") if isinstance(row.get("record"), dict) else None
        if record_payload:
            record = evidence_context_record_from_cached_payload(record_payload, source_path=source_path)
            records.append(record)
            continue
        raw_materialization_text = row.get("raw_materialization_text")
        raw_text = raw_materialization_text if raw_materialization_text is not None else row.get("raw_text")
        if raw_text is None:
            continue
        first_line = int(row.get("line") or 1)
        for offset, raw_line in enumerate(str(raw_text).splitlines() or [str(raw_text)]):
            record = evidence_context_record_from_raw_line(
                raw_line,
                source_path=source_path,
                line=first_line + offset,
                source_kind=source_kind,
            )
            records.append(record)
    records.sort(key=lambda record: int(getattr(record, "line", 0) or 0))
    if context_source_counters is not None:
        counter_name = (
            "context_records_served_from_segment_cache"
            if bool(context_index.get("warm_query_reused_index", False))
            else "context_records_served_from_in_memory_rows"
        )
        context_source_counters[counter_name] = int(context_source_counters[counter_name]) + len(records)
    return records, "cache_rows"


def evidence_context_record_from_cached_payload(payload: dict[str, Any], *, source_path: Path) -> Any:
    timestamp = None
    raw_timestamp = payload.get("timestamp")
    if isinstance(raw_timestamp, str) and raw_timestamp:
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            timestamp = None
    return SimpleNamespace(
        path=str(source_path),
        line=int(payload.get("line") or 1),
        role=str(payload.get("role") or ""),
        text=str(payload.get("text") or ""),
        timestamp=timestamp,
        cwd=str(payload.get("cwd")) if payload.get("cwd") else None,
        raw_timestamp=str(payload.get("raw_timestamp")) if payload.get("raw_timestamp") else None,
    )


def evidence_context_record_from_raw_line(
    raw_line: str,
    *,
    source_path: Path,
    line: int,
    source_kind: str,
) -> Any:
    from .records import parse_record

    record = parse_record(raw_line, path=source_path, line=line, default_tz=timezone.utc)
    record.source_format = source_kind
    return record


def materialized_context_record(record: Any, *, hit_child: bool, char_limit: int) -> dict[str, Any]:
    text = str(getattr(record, "text", "") or "")
    limit = max(0, int(char_limit or 0))
    truncated = len(text) > limit
    if truncated:
        text = text[:limit]
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
        "record_kind": "hit_child" if hit_child else "neighbor",
        "text": text,
        "text_included": True,
        "text_truncated": truncated,
    }


def resolve_evidence_context_source_path(source_ref: dict[str, Any], *, source_plan: dict[str, Any]) -> Path | None:
    raw_path_text = str(source_ref.get("path") or "")
    if not raw_path_text:
        return None
    cwd = Path(str(source_plan.get("cwd") or ".")).expanduser()
    raw_path = Path(raw_path_text).expanduser()
    candidates = [raw_path if raw_path.is_absolute() else cwd / raw_path]
    roots = list((source_plan.get("sources") or {}).get("roots") or [])
    for root in roots:
        root_path_text = root.get("path")
        if not root_path_text:
            continue
        root_path = Path(str(root_path_text)).expanduser()
        if root_path.is_dir() and not raw_path.is_absolute():
            candidates.append(root_path / raw_path)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.exists() or not resolved.is_file():
            continue
        if evidence_context_path_allowed(resolved, roots=roots):
            return resolved
    return None


def evidence_context_path_allowed(path: Path, *, roots: list[dict[str, Any]]) -> bool:
    if not roots:
        return True
    for root in roots:
        root_path_text = root.get("path")
        if not root_path_text:
            continue
        base = Path(str(root_path_text)).expanduser()
        try:
            resolved_base = base.resolve()
        except OSError:
            continue
        if resolved_base.is_file() and path == resolved_base:
            return True
        if resolved_base.is_dir() and path_is_inside_evidence_context(path, resolved_base):
            return True
    return False


def path_is_inside_evidence_context(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def evidence_context_source_kind(path: Path, source_plan: dict[str, Any]) -> str:
    for root in (source_plan.get("sources") or {}).get("roots") or []:
        root_path_text = root.get("path")
        if not root_path_text:
            continue
        base = Path(str(root_path_text)).expanduser()
        try:
            resolved_base = base.resolve()
        except OSError:
            continue
        if (resolved_base.is_file() and path == resolved_base) or (
            resolved_base.is_dir() and path_is_inside_evidence_context(path, resolved_base)
        ):
            return str(root.get("kind") or "unknown_jsonl")
    if path.suffix == ".json":
        return "unknown_json"
    return "unknown_jsonl"


def normalize_evidence_packet_candidates(candidates: list[dict[str, Any]] | list[CandidateRow]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates):
        data = evidence_packet_candidate_data(candidate, ordinal=ordinal)
        source_key = source_window_source_key(data, candidate_id=str(data.get("candidate_id") or ""))
        day = source_window_day(data.get("timestamp_utc"))
        session = str(data.get("session") or "")
        normalized.append(
            {
                **data,
                "source_key": source_key,
                "day": day,
                "group_key": "|".join(
                    [
                        f"source={source_key}",
                        f"session={session}" if session else "session=",
                    ]
                ),
            }
        )
    return normalized


def evidence_packet_candidate_data(candidate: dict[str, Any] | CandidateRow, *, ordinal: int) -> dict[str, Any]:
    if isinstance(candidate, CandidateRow):
        return {
            "ordinal": ordinal,
            "candidate_id": candidate.candidate_id,
            "rank": candidate.rank or ordinal + 1,
            "source_ref": dict(candidate.source_ref or {}),
            "target": source_ref_target(candidate.source_ref or {}),
            "line": candidate.line,
            "role": candidate.role,
            "session": candidate.session or (candidate.source_ref or {}).get("session"),
            "timestamp_utc": candidate.timestamp_utc,
            "confidence": candidate.confidence,
            "stages": list(candidate.stages or []),
            "reason_codes": sanitize_evidence_packet_reason_codes(candidate.reason_codes),
            "reasons": sanitize_evidence_packet_reasons(candidate.reasons),
            "matched_terms": list(candidate.matched_terms or [])[:SOURCE_WINDOW_REASON_CODE_LIMIT],
        }
    source_ref = dict(candidate.get("source_ref") or {})
    return {
        "ordinal": ordinal,
        "candidate_id": str(candidate.get("candidate_id") or f"candidate-{ordinal + 1}"),
        "rank": int(candidate.get("rank") or ordinal + 1),
        "source_ref": source_ref,
        "target": source_ref_target(source_ref),
        "line": normalized_source_ref_line(source_ref.get("line")) or int(candidate.get("line") or 0),
        "role": candidate.get("role"),
        "session": candidate.get("session") or source_ref.get("session"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "confidence": candidate.get("confidence"),
        "stages": list(candidate.get("stages") or []),
        "reason_codes": sanitize_evidence_packet_reason_codes(candidate.get("reason_codes") or []),
        "reasons": sanitize_evidence_packet_reasons(candidate.get("reasons") or []),
        "matched_terms": list(candidate.get("matched_terms") or [])[:SOURCE_WINDOW_REASON_CODE_LIMIT],
    }


def evidence_packet_child_ref_key(item: dict[str, Any]) -> tuple[str, int, str]:
    source_ref = item["source_ref"]
    return (
        source_ref_target(source_ref),
        evidence_packet_numeric_line(source_ref.get("line")) or 0,
        str(item.get("role") or ""),
    )


def evidence_packet_line_sort_key(source_ref: dict[str, Any]) -> int:
    line = evidence_packet_numeric_line(source_ref.get("line"))
    return line if line is not None else 0


def evidence_packet_numeric_line(line: Any) -> int | None:
    try:
        parsed = int(line)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def evidence_packet_hit_child(item: dict[str, Any]) -> dict[str, Any]:
    source_ref = dict(item["source_ref"])
    child = {
        "candidate_id": item["candidate_id"],
        "rank": item["rank"],
        "child_order": item["ordinal"] + 1,
        "source_ref": source_ref,
        "target": source_ref_target(source_ref),
        "line": evidence_packet_numeric_line(source_ref.get("line")),
        "role": item.get("role"),
        "timestamp_utc": item.get("timestamp_utc"),
        "confidence": item.get("confidence"),
        "stages": list(item.get("stages") or []),
        "reason_codes": list(item.get("reason_codes") or []),
        "reasons": list(item.get("reasons") or []),
        "reason_count": len(item.get("reasons") or []),
        "evidence_channel_labels": evidence_channel_labels_from_reason_codes(item.get("reason_codes") or []),
    }
    return {key: value for key, value in child.items() if value not in (None, [], {})}


def evidence_packet_context_record(item: dict[str, Any]) -> dict[str, Any]:
    source_ref = dict(item["source_ref"])
    return {
        "source_ref": source_ref,
        "target": source_ref_target(source_ref),
        "line": evidence_packet_numeric_line(source_ref.get("line")),
        "candidate_id": item["candidate_id"],
        "role": item.get("role"),
        "timestamp_utc": item.get("timestamp_utc"),
        "metadata_only": True,
        "raw_text_included": False,
        "snippet_included": False,
    }


def evidence_channel_labels_from_reason_codes(reason_codes: list[Any]) -> list[str]:
    labels: list[str] = []
    for code in reason_codes:
        text = str(code)
        if text.startswith("fielded_"):
            labels.append(text)
        elif text in {"exact_term", "normalized_term", "phrase", "role_match", "path_artifact", "temporal"}:
            labels.append(text)
    return unique_ordered_strings(labels)


def sanitize_evidence_packet_reason_codes(reason_codes: list[Any]) -> list[str]:
    sanitized: list[str] = []
    for code in reason_codes:
        text = str(code)
        if text.startswith("phrase:"):
            sanitized.append("phrase_match")
        elif text.startswith("coverage:base_terms:"):
            sanitized.append("base_term_coverage")
        elif text.startswith("project:"):
            sanitized.append("project_hint")
        elif text.startswith("fielded_bm25:evidence_channel:"):
            sanitized.append(text)
        elif text.startswith("fielded_bm25:"):
            sanitized.append(text)
        elif ":" in text:
            sanitized.append(text.split(":", 1)[0])
        else:
            sanitized.append(text)
    return unique_ordered_strings(sanitized)[:SOURCE_WINDOW_REASON_CODE_LIMIT]


def sanitize_evidence_packet_reasons(reasons: list[Any]) -> list[str]:
    sanitized: list[str] = []
    for reason in reasons:
        text = str(reason)
        if text.startswith("text:"):
            parts = text.split(":", 2)
            sanitized.append(f"text:{parts[1]}" if len(parts) > 1 else "text")
        elif text.startswith("fuzzy:"):
            sanitized.append("fuzzy:typo_fuzzy")
        elif text.startswith("phrase:"):
            sanitized.append("phrase_match")
        elif text.startswith("coverage:base_terms:"):
            sanitized.append("coverage:base_terms")
        elif text.startswith("role:"):
            sanitized.append("role_match")
        elif text.startswith("project:"):
            sanitized.append("project_hint")
        elif text.startswith("fielded_bm25:evidence_channel:"):
            sanitized.append(text)
        elif text.startswith("fielded_bm25:"):
            sanitized.append(text)
        elif ":" in text:
            sanitized.append(text.split(":", 1)[0])
        else:
            sanitized.append(text)
    return unique_ordered_strings(sanitized)[:SOURCE_WINDOW_REASON_CODE_LIMIT]


def unique_ordered_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def evidence_packet_truncation_reason(
    *,
    omitted_children: int,
    omitted_context_records: int,
    omitted_packet_groups: int,
) -> str:
    reasons = []
    if omitted_packet_groups:
        reasons.append("packet_limit")
    if omitted_children:
        reasons.append("child_limit_per_packet")
    if omitted_context_records:
        reasons.append("context_record_limit_per_packet")
    return ",".join(reasons) if reasons else "none"


def normalize_source_window_candidates(
    candidates: list[dict[str, Any]] | list[CandidateRow],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates):
        data = candidate_to_source_window_data(candidate)
        candidate_id = str(data.get("candidate_id") or "")
        normalized.append(
            {
                "ordinal": ordinal,
                "data": data,
                "source_key": source_window_source_key(data, candidate_id=candidate_id),
                "group_key": source_window_group_key(data, candidate_id=candidate_id),
                "metadata": source_window_candidate_metadata(data, ordinal=ordinal, candidate_id=candidate_id),
            }
        )
    return normalized


def select_source_window_candidates(
    normalized_candidates: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any],
    primary_intent: str,
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_group_keys: set[str] = set()
    intent_candidates = [
        item
        for item in normalized_candidates
        if source_window_candidate_matches_intent(item["data"], query_plan, primary_intent)
    ]
    first_pass_candidates = intent_candidates if intent_candidates else normalized_candidates

    for item in first_pass_candidates:
        if len(selected) >= SOURCE_WINDOW_LIMIT:
            break
        candidate_id = str(item["data"].get("candidate_id") or "")
        group_key = str(item["group_key"])
        if group_key in selected_group_keys:
            continue
        selected.append(
            {
                **item,
                "selection_reason_code": "intent_preferred_distinct_source"
                if item in intent_candidates
                else "distinct_source",
            }
        )
        selected_ids.add(candidate_id)
        selected_group_keys.add(group_key)

    for item in normalized_candidates:
        if len(selected) >= SOURCE_WINDOW_LIMIT:
            break
        candidate_id = str(item["data"].get("candidate_id") or "")
        if candidate_id in selected_ids:
            continue
        if not intent_candidates and str(item["group_key"]) not in selected_group_keys:
            continue
        selected.append({**item, "selection_reason_code": "existing_order_fill"})
        selected_ids.add(candidate_id)
        selected_group_keys.add(str(item["group_key"]))

    selected_candidate_ids = {str(item["data"].get("candidate_id") or "") for item in selected}
    omitted = []
    selected_group_counts = Counter(str(item["group_key"]) for item in selected)
    candidate_group_counts = Counter(str(item["group_key"]) for item in normalized_candidates)
    selected_groups = set(selected_group_counts)
    intent_candidate_ids = {str(item["data"].get("candidate_id") or "") for item in intent_candidates}
    for item in normalized_candidates:
        candidate_id = str(item["data"].get("candidate_id") or "")
        if candidate_id in selected_candidate_ids:
            continue
        group_key = str(item["group_key"])
        if len(selected) >= SOURCE_WINDOW_LIMIT:
            reason = "window_limit_after_group_allocation"
        elif group_key not in selected_groups:
            reason = "unrepresented_group_after_intent_allocation"
        elif candidate_group_counts[group_key] > 1:
            reason = "represented_group_over_limit"
        else:
            reason = "not_selected"
        omitted.append({**item, "intent_match": candidate_id in intent_candidate_ids, "omission_reason": reason})

    dominant_group_second_window_count = 0
    if selected_group_counts:
        dominant_group_second_window_count = max(0, max(selected_group_counts.values()) - 1)
    return {
        "selected": selected,
        "omitted": omitted,
        "intent_pass_active": bool(intent_candidates),
        "dominant_group_second_window_count": dominant_group_second_window_count,
    }


def source_window_source_key(data: dict[str, Any], *, candidate_id: str) -> str:
    source_ref = data.get("source_ref") or {}
    path = source_ref.get("path")
    if path:
        return f"path:{path}"
    session_id = source_ref.get("session_id") or data.get("session")
    if session_id:
        return f"session:{session_id}"
    target = source_ref_target(source_ref)
    if target:
        return f"target:{target}"
    return f"candidate:{candidate_id}"


def source_window_group_key(data: dict[str, Any], *, candidate_id: str) -> str:
    source_key = source_window_source_key(data, candidate_id=candidate_id)
    session = str(data.get("session") or "")
    day = source_window_day(data.get("timestamp_utc"))
    return "|".join(
        [
            f"source={source_key}",
            f"session={session}" if session else "session=",
            f"day={day}" if day else "day=",
        ]
    )


def source_window_day(timestamp_utc: Any) -> str | None:
    if not timestamp_utc:
        return None
    value = str(timestamp_utc)
    if len(value) >= 10:
        return value[:10]
    return None


def source_window_candidate_metadata(data: dict[str, Any], *, ordinal: int, candidate_id: str) -> dict[str, Any]:
    source_ref = data.get("source_ref") or {}
    return {
        "ordinal": ordinal,
        "candidate_id": candidate_id,
        "source_ref": source_ref,
        "target": source_ref_target(source_ref),
        "source_key": source_window_source_key(data, candidate_id=candidate_id),
        "group_key": source_window_group_key(data, candidate_id=candidate_id),
        "role": data.get("role"),
        "session_present": bool(data.get("session")),
        "day": source_window_day(data.get("timestamp_utc")),
    }


def selection_policy_omitted_candidate_facts(omitted_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    group_counts = Counter(str(item["group_key"]) for item in omitted_candidates)
    for item in omitted_candidates[:SOURCE_WINDOW_OMITTED_FACT_LIMIT]:
        metadata = dict(item.get("metadata") or {})
        metadata["omission_reason"] = str(item.get("omission_reason") or "omitted")
        metadata["intent_match"] = bool(item.get("intent_match"))
        metadata["omitted_group_count"] = group_counts[str(item["group_key"])]
        metadata["raw_text_included"] = False
        metadata["snippet_included"] = False
        facts.append(metadata)
    return facts


def source_window_candidate_matches_intent(
    data: dict[str, Any],
    query_plan: dict[str, Any],
    primary_intent: str,
) -> bool:
    role = str(data.get("role") or "").lower()
    if primary_intent == "when_user_said":
        return "user" in role
    if primary_intent == "workflow_failure":
        return bool(data.get("timestamp_utc"))
    if primary_intent == "why":
        hinted_roles = [str(item).lower() for item in (query_plan.get("field_hints") or {}).get("roles") or []]
        return bool(hinted_roles) and any(hinted_role in role for hinted_role in hinted_roles)
    if primary_intent == "what_was_i_talking_about":
        return bool(data.get("cwd") or data.get("timestamp_utc"))
    if primary_intent in {"source_next_reads", "next_reads"}:
        return bool(source_ref_target(data.get("source_ref") or {}))
    return False


def candidate_to_source_window_data(candidate: dict[str, Any] | CandidateRow) -> dict[str, Any]:
    if isinstance(candidate, CandidateRow):
        return {
            "candidate_id": candidate.candidate_id,
            "source_ref": candidate.source_ref,
            "role": candidate.role,
            "session": candidate.session,
            "timestamp_utc": candidate.timestamp_utc,
            "cwd": candidate.cwd,
            "reason_codes": list(candidate.reason_codes)[:SOURCE_WINDOW_REASON_CODE_LIMIT],
        }
    return {
        "candidate_id": candidate.get("candidate_id"),
        "source_ref": candidate.get("source_ref") or {},
        "role": candidate.get("role"),
        "session": candidate.get("session"),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "cwd": candidate.get("cwd"),
        "reason_codes": list(candidate.get("reason_codes", []))[:SOURCE_WINDOW_REASON_CODE_LIMIT],
    }


def source_window_kind(primary_intent: str, window_needs: list[str]) -> str:
    for kind in ("decision_context", "failure_timeline", "user_wording", "topic_context", "next_reads"):
        if kind in window_needs:
            return kind
    if primary_intent == "why":
        return "decision_context"
    if primary_intent == "workflow_failure":
        return "failure_timeline"
    if primary_intent == "when_user_said":
        return "user_wording"
    if primary_intent == "what_was_i_talking_about":
        return "topic_context"
    return "candidate_context"


def project_hint_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    return Path(str(cwd)).name or None


def next_reads_from_candidates(
    candidates: list[dict[str, Any]] | list[CandidateRow],
    *,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
) -> list[dict[str, Any]]:
    reads: list[dict[str, Any]] = []
    for candidate in candidates[:COMPACT_PROVENANCE_NEXT_READ_LIMIT]:
        if isinstance(candidate, CandidateRow):
            ref = candidate.source_ref
            candidate_id = candidate.candidate_id
        else:
            ref = candidate.get("source_ref", {})
            candidate_id = candidate.get("candidate_id")
        target = source_ref_target(ref)
        if target:
            source_ref = dict(ref)
            read = {
                "kind": "jsonl_source_ref",
                "target": target,
                "candidate_id": str(candidate_id or ""),
                "source_ref": source_ref,
                "reason": "open candidate source_ref before broad search",
                "expansion_actions": read_more_actions_for_source_ref(
                    source_ref,
                    candidate_id=str(candidate_id or ""),
                    expansion_action_context=expansion_action_context,
                ),
            }
            if include_context_actions:
                read["context_actions"] = context_actions_for_source_ref(
                    source_ref,
                    candidate_id=str(candidate_id or ""),
                    expansion_action_context=expansion_action_context,
                )
            reads.append(read)
    return reads


def expansion_action_context_from_source_plan(source_plan: dict[str, Any]) -> dict[str, Any]:
    sources = source_plan.get("sources") if isinstance(source_plan.get("sources"), dict) else {}
    roots = [root for root in sources.get("roots") or [] if isinstance(root, dict) and root.get("path")]
    limits = sources.get("limits") if isinstance(sources.get("limits"), dict) else {}
    root_paths = [str(root["path"]) for root in roots]
    return {
        "cwd": source_plan.get("cwd") if any(not Path(path).expanduser().is_absolute() for path in root_paths) else None,
        "roots": root_paths,
        "allow_arbitrary_root": any(not bool(root.get("known", False)) for root in roots),
        "hard_cap_files": limits.get("hard_cap_files"),
        "hard_cap_bytes": limits.get("hard_cap_bytes"),
    }


def expansion_action_context_argv(expansion_action_context: dict[str, Any] | None) -> list[str]:
    if not expansion_action_context:
        return []
    argv: list[str] = []
    cwd = expansion_action_context.get("cwd")
    if cwd:
        argv.extend(["--cwd", str(cwd)])
    for root in expansion_action_context.get("roots") or []:
        argv.extend(["--root", str(root)])
    if expansion_action_context.get("allow_arbitrary_root"):
        argv.append("--allow-arbitrary-root")
    for option, key in (
        ("--hard-cap-files", "hard_cap_files"),
        ("--hard-cap-bytes", "hard_cap_bytes"),
    ):
        value = expansion_action_context.get(key)
        if value is not None:
            argv.extend([option, str(value)])
    return argv


def read_more_actions_for_source_ref(
    source_ref: dict[str, Any],
    *,
    candidate_id: str | None = None,
    expansion_action_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target = source_ref_target(source_ref)
    if not target:
        return []
    line = normalized_source_ref_line(source_ref.get("line"))
    common = {
        "kind": "source_expansion",
        "command": "jsonl-history-search read-more",
        "claim_boundaries": {
            "broad_search_rerun": False,
            "ranking_or_cap_changed": False,
        },
    }
    context_argv = expansion_action_context_argv(expansion_action_context)
    actions = [
        {
            **common,
            "mode": "record",
            "argv": ["read-more", target, "--mode", "record", "--format", "json", *context_argv],
        }
    ]
    if line:
        actions.extend(
            [
                {
                    **common,
                    "mode": "window",
                    "argv": [
                        "read-more",
                        target,
                        "--mode",
                        "window",
                        "--before",
                        "2",
                        "--after",
                        "2",
                        "--format",
                        "json",
                        *context_argv,
                    ],
                },
                {
                    **common,
                    "mode": "next",
                    "argv": [
                        "read-more",
                        target,
                        "--mode",
                        "next",
                        "--next-count",
                        "3",
                        "--format",
                        "json",
                        *context_argv,
                    ],
                },
            ]
        )
    return actions


def context_actions_for_source_ref(
    source_ref: dict[str, Any],
    *,
    candidate_id: str | None = None,
    expansion_action_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    target = source_ref_target(source_ref)
    if not target:
        return []
    line = normalized_source_ref_line(source_ref.get("line"))
    context_argv = expansion_action_context_argv(expansion_action_context)
    ladder = [
        context_action(
            source_ref,
            target=target,
            action_id="record",
            label="record",
            mode="record",
            scope="source",
            argv=["read-more", target, "--mode", "record", "--format", "json", *context_argv],
            bounds={"max_records": 1},
            included_semantics="anchor_only",
            omitted_semantics="adjacent_omitted",
            continuation_semantics="use_window_or_neighborhood",
            candidate_id=candidate_id,
        )
    ]
    if not line:
        return ladder
    ladder.extend(
        [
            context_action(
                source_ref,
                target=target,
                action_id="window",
                label="source_window",
                mode="window",
                scope="source",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "window",
                    "--scope",
                    "source",
                    "--before",
                    "2",
                    "--after",
                    "2",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"before": 2, "after": 2, "max_records": 20},
                included_semantics="anchor_plus_bounded_source_before_after",
                omitted_semantics="outside_window_omitted",
                continuation_semantics="continue_from_boundaries",
                candidate_id=candidate_id,
            ),
            context_action(
                source_ref,
                target=target,
                action_id="before",
                label="before",
                mode="before",
                scope="source",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "before",
                    "--scope",
                    "source",
                    "--before",
                    "3",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"before": 3, "after": 0, "max_records": 20},
                included_semantics="bounded_previous_source_rows",
                omitted_semantics="anchor_after_and_earlier_beyond_bound_omitted",
                continuation_semantics="continue_before_from_first_returned",
                candidate_id=candidate_id,
            ),
            context_action(
                source_ref,
                target=target,
                action_id="next",
                label="after",
                mode="next",
                scope="source",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "next",
                    "--scope",
                    "source",
                    "--next-count",
                    "3",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"next_count": 3, "max_records": 20},
                included_semantics="bounded_next_source_rows",
                omitted_semantics="anchor_before_and_later_beyond_bound_omitted",
                continuation_semantics="continue_next_from_last_returned",
                candidate_id=candidate_id,
            ),
            context_action(
                source_ref,
                target=target,
                action_id="source_neighborhood",
                label="collapsed_or_adjacent_window",
                mode="neighborhood",
                scope="source",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "neighborhood",
                    "--scope",
                    "source",
                    "--before",
                    "4",
                    "--after",
                    "4",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"before": 4, "after": 4, "max_records": 20},
                included_semantics="bounded_same_source_neighborhood",
                omitted_semantics="outside_neighborhood_omitted",
                continuation_semantics="continue_with_boundary_reads",
                candidate_id=candidate_id,
            ),
            context_action(
                source_ref,
                target=target,
                action_id="session_neighborhood",
                label="same_session_neighborhood",
                mode="neighborhood",
                scope="session",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "neighborhood",
                    "--scope",
                    "session",
                    "--before",
                    "6",
                    "--after",
                    "6",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"before": 6, "after": 6, "max_records": 20},
                included_semantics="bounded_same_session_neighborhood",
                omitted_semantics="outside_bound_or_session_mismatch_omitted",
                continuation_semantics="fallback_to_source_neighborhood_if_no_session_metadata",
                candidate_id=candidate_id,
            ),
            context_action(
                source_ref,
                target=target,
                action_id="day_neighborhood",
                label="same_day_neighborhood",
                mode="neighborhood",
                scope="day",
                argv=[
                    "read-more",
                    target,
                    "--mode",
                    "neighborhood",
                    "--scope",
                    "day",
                    "--before",
                    "6",
                    "--after",
                    "6",
                    "--format",
                    "json",
                    *context_argv,
                ],
                bounds={"before": 6, "after": 6, "max_records": 20},
                included_semantics="bounded_same_day_neighborhood",
                omitted_semantics="outside_bound_or_day_mismatch_omitted",
                continuation_semantics="continue_by_source_boundaries",
                candidate_id=candidate_id,
            ),
        ]
    )
    return ladder


def context_action(
    source_ref: dict[str, Any],
    *,
    target: str,
    action_id: str,
    label: str,
    mode: str,
    scope: str,
    argv: list[str],
    bounds: dict[str, Any],
    included_semantics: str,
    omitted_semantics: str,
    continuation_semantics: str,
    candidate_id: str | None,
) -> dict[str, Any]:
    normalized_bounds = {
        "scope": scope,
        "max_records": 20,
        **bounds,
    }
    return {
        "action_id": action_id,
        "label": label,
        "mode": mode,
        "scope": scope,
        "target": target,
        "source_ref": dict(source_ref),
        "bounds": normalized_bounds,
        "argv": argv,
        "semantics": {
            "included": included_semantics,
            "omitted": omitted_semantics,
        },
        "continuation": {
            "semantics": continuation_semantics,
            "bounded": True,
        },
        "claim_boundaries": {
            "broad_search_rerun": False,
            "ranking_or_cap_changed": False,
        },
    }


def source_ref_target(source_ref: dict[str, Any]) -> str | None:
    path = source_ref.get("path")
    line = normalized_source_ref_line(source_ref.get("line"))
    if path and line:
        return f"{path}:{line}"
    if path:
        return str(path)
    if line:
        return f"line {line}"
    return None


def normalized_source_ref_line(line: Any) -> str | None:
    if line is None:
        return None
    if isinstance(line, str):
        normalized = line.strip()
        if normalized.lower() in {"", "none", "null"}:
            return None
        return normalized
    return str(line)


def compact_handoff_candidates(candidates: list[dict[str, Any]] | list[CandidateRow]) -> list[dict[str, Any]]:
    compacted = []
    for candidate in candidates[:SOURCE_LOCATOR_TOP_CANDIDATES_LIMIT]:
        if isinstance(candidate, CandidateRow):
            compacted.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "rank": candidate.rank,
                    "source_ref": candidate.source_ref,
                    "timestamp_utc": candidate.timestamp_utc,
                    "confidence": candidate.confidence,
                    "stage": candidate.primary_stage,
                    "score": candidate.score,
                    "reason_codes": candidate.reason_codes[:SOURCE_WINDOW_REASON_CODE_LIMIT],
                }
            )
        else:
            compacted.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "rank": candidate.get("rank"),
                    "source_ref": candidate.get("source_ref"),
                    "timestamp_utc": candidate.get("timestamp_utc"),
                    "confidence": candidate.get("confidence"),
                    "stage": candidate.get("stage"),
                    "score": candidate.get("score"),
                    "reason_codes": list(candidate.get("reason_codes", []))[:SOURCE_WINDOW_REASON_CODE_LIMIT],
                }
            )
    return compacted


def compact_handoff_caps(cap_hit: dict[str, Any]) -> dict[str, Any]:
    limits = cap_hit.get("limits", {})
    return {
        "hit": bool(cap_hit.get("hit")),
        "caps_hit": list(cap_hit.get("caps_hit", [])),
        "normalized_warnings": list(cap_hit.get("normalized_warnings", [])),
        "limits": {
            "effective_files": limits.get("effective_files"),
            "effective_bytes": limits.get("effective_bytes"),
        },
    }


def compact_handoff_not_searched_scope(not_searched: dict[str, Any], caps: dict[str, Any]) -> dict[str, Any]:
    return {
        "missing_root_count": not_searched.get("missing_root_count", 0),
        "missing_roots": list(not_searched.get("missing_roots", [])),
        "cap_limited": {"hit": caps.get("hit", False), "caps_hit": list(caps.get("caps_hit", []))},
        "outside_selected_roots": not_searched.get("outside_selected_roots"),
    }


def source_locator_blind_spots(source_scope: dict[str, Any], search_result: dict, sources: dict) -> list[str]:
    labels: set[str] = set()
    if not source_scope.get("selected_roots"):
        labels.add("no_selected_transcript_roots")
    if source_scope.get("excluded_scope", {}).get("rejected_root_count", 0):
        labels.add("some_requested_roots_rejected_before_traversal")
    if source_scope.get("not_searched_scope", {}).get("missing_root_count", 0):
        labels.add("some_selected_roots_missing_or_inaccessible")
    if source_scope.get("cap_hit", {}).get("hit"):
        labels.add("selected_scope_limited_by_caps")
    if not search_result.get("groups"):
        labels.add("no_candidates_in_searched_scope")
    for item in list(search_result.get("blind_spots", []) or sources.get("blind_spots", [])):
        if "No transcript roots selected" in item:
            labels.add("no_selected_transcript_roots")
        elif "Missing transcript root:" in item:
            labels.add("some_selected_roots_missing_or_inaccessible")
        else:
            labels.add("blind_spot_details_sanitized")
    return sorted(labels)


def sanitized_handoff_messages(messages: list[str]) -> list[str]:
    sanitized: list[str] = []
    for message in messages:
        value = sanitize_handoff_message(message)
        if value and value not in sanitized:
            sanitized.append(value)
    return sanitized


def sanitize_handoff_message(message: str) -> str | None:
    if not message:
        return None
    lowered = message.lower()
    if "max file cap" in lowered:
        return "Stopped at max file cap."
    if "max byte cap" in lowered:
        return "Stopped at max byte cap."
    if "max match cap" in lowered or "retention-capped" in lowered:
        return "Candidate retention or scanning stopped at max match cap."
    if "max elapsed cap" in lowered:
        return "Stopped at max elapsed cap."
    if "reservoir-capped" in lowered:
        return "Weak-candidate reservoir was capped."
    if "missing transcript root" in lowered:
        return "A selected transcript root was missing or inaccessible."
    if "arbitrary root enabled" in lowered:
        return "Arbitrary root enabled for an explicit selected root; strict hard caps apply."
    if "symlink escape" in lowered:
        return "Skipped a symlink escape outside the selected root."
    return "Warning details sanitized; inspect full local run output only when privacy boundaries allow it."


def build_broaden_query(query: dict) -> dict:
    expanded_terms = []
    for term in query.get("terms", []):
        expanded_terms.append(
            {
                "term": term,
                "families": query.get("expansion_reasons", {}).get(term) or ["exact"],
            }
        )
    return {
        "original": query.get("original", ""),
        "normalized": query.get("normalized", ""),
        "base_terms": query.get("base_terms", []),
        "expanded_terms": expanded_terms,
        "artifact_hints": query.get("artifact_hints", []),
        "role": query.get("role"),
        "date_window": query.get("date_window", {}),
        "defined_families": [
            "exact",
            "normalized",
            "typo_fuzzy",
            "synonym_paraphrase",
            "artifact_path",
            "cwd_project",
            "date_window",
            "role",
            "semantic_local",
        ],
        "stage_support": {
            "typo_fuzzy": {
                "status": "supported",
                "method": "bounded local token similarity over query base terms after exact filters miss",
            },
            "semantic_local": {
                "status": "supported",
                "method": "deterministic local paraphrase/concept expansion; no remote embedding calls",
            },
            "semantic_embeddings": {
                "status": "not_run",
                "reason": "persistent local embedding backend is reserved for jsh-005",
            },
        },
    }


def build_cluster_candidates(search_result: dict) -> list[dict]:
    clusters = []
    for index, group in enumerate(search_result.get("groups", []), 1):
        clusters.append(
            {
                "rank": index,
                "cluster_key": group.get("cluster_key"),
                "cluster_type": group.get("cluster_type"),
                "cluster_value": group.get("cluster_value"),
                "top_score": group.get("top_score"),
                "paths": group.get("paths", []),
                "session": group.get("session"),
                "tasks": list(group.get("tasks", []))[:20],
                "artifacts": list(group.get("artifacts", []))[:20],
                "match_count": len(group.get("matches", [])),
            }
        )
    return clusters[:INLINE_CANDIDATE_LIMIT]


def build_evidence_bundles(search_result: dict) -> list[dict]:
    bundles = []
    for index, group in enumerate(search_result.get("groups", []), 1):
        matches = group.get("matches", [])
        top_refs = [
            {
                "path": match.get("path"),
                "line": match.get("line"),
                "timestamp": match.get("timestamp"),
                "role": match.get("role"),
            }
            for match in matches[:3]
        ]
        bundles.append(
            {
                "bundle_id": f"b{index:03d}",
                "cluster_key": group.get("cluster_key"),
                "cluster_type": group.get("cluster_type"),
                "cluster_value": group.get("cluster_value"),
                "top_score": group.get("top_score"),
                "summary": {
                    "match_count": len(matches),
                    "paths": group.get("paths", []),
                    "tasks": list(group.get("tasks", []))[:20],
                    "artifacts": list(group.get("artifacts", []))[:20],
                },
                "top_source_refs": top_refs,
            }
        )
    return bundles[:INLINE_CANDIDATE_LIMIT]


def build_no_evidence_report(
    *,
    status: str,
    source_plan: dict,
    search_result: dict,
    manifest_report: dict | None,
) -> dict:
    if status != "no_evidence":
        return {
            "needed": False,
            "reason": "Candidates were found; inspect find_candidates, timeline, and cluster_candidates.",
        }
    source_scope = build_source_scope_summary(source_plan, search_result)
    weak_candidates = []
    if manifest_report:
        selected = manifest_report.get("manifest_report", {}).get("shortlist", {}).get("selected_entries", [])
        for item in selected[:INLINE_CANDIDATE_LIMIT]:
            weak_candidates.append(
                {
                    "path": item.get("entry", {}).get("path"),
                    "score": item.get("score"),
                    "reasons": item.get("reasons", []),
                }
            )
    return {
        "needed": True,
        "answer": "not_found_yet",
        "answer_scope": "not_found_in_searched_scope",
        "searched_scope": source_scope["searched_scope"],
        "excluded_scope": source_scope["excluded_scope"]["rejected_roots"],
        "excluded_scope_summary": source_scope["excluded_scope"],
        "not_searched_scope": source_scope["not_searched_scope"],
        "selected_roots": source_scope["selected_roots"],
        "private_roots": source_scope["private_roots"],
        "cap_hit": source_scope["cap_hit"],
        "deferred_source_count": source_scope["not_searched_scope"]["source_plan"]["sources_deferred_budget"],
        "retry_guidance": source_scope["not_searched_scope"]["source_plan"]["retry_guidance"],
        "not_all_history_absence": True,
        "weak_candidates": weak_candidates,
        "warnings": search_result.get("warnings", []),
        "blind_spots": search_result.get("blind_spots", []),
        "why_not_confident": (
            "No matching candidate rows survived the searched scope. "
            "This does not prove evidence is absent from excluded roots, missing roots, roots outside the source plan, "
            "budget-deferred sources, or scope beyond any hit caps."
        ),
    }


def build_source_scope_summary(source_plan: dict, search_result: dict) -> dict:
    sources = source_plan.get("sources", {})
    roots = list(sources.get("roots", []))
    rejected_roots = list(sources.get("rejected_roots", []))
    retrieval_diagnostics = search_result.get("retrieval_diagnostics") if isinstance(search_result, dict) else {}
    retrieval_source_plan = sanitize_source_plan(
        retrieval_diagnostics.get("source_plan") if isinstance(retrieval_diagnostics, dict) else {}
    )
    blind_spots = list(search_result.get("blind_spots", []) or sources.get("blind_spots", []))
    missing_roots = [root for root in roots if root_is_missing(root, blind_spots)]
    missing_paths = {root.get("path") for root in missing_roots}
    searched_roots = [root for root in roots if root.get("path") not in missing_paths]
    cap_hit = cap_hit_status(
        warnings=list(search_result.get("warnings", []) or sources.get("warnings", [])),
        limits=sources.get("limits", {}),
    )
    not_searched_notes = [
        "Roots outside the selected source plan were not searched.",
    ]
    if missing_roots:
        not_searched_notes.append("Some selected roots were missing and were not opened.")
    if cap_hit["hit"]:
        not_searched_notes.append("At least one cap stopped enumeration, scanning, or candidate retention before full selected-root coverage.")
    if rejected_roots:
        not_searched_notes.append("Rejected roots are listed separately under excluded_scope and were not traversed.")
    if retrieval_source_plan["sources_pruned_hard"] > 0:
        not_searched_notes.append("Fielded BM25 searched only source-plan selected files; date-disjoint files were pruned before indexing.")
    if retrieval_source_plan["sources_deferred_budget"] > 0:
        not_searched_notes.append("Some source-plan files were deferred by budget and were not searched.")
    return {
        "terms": sources.get("scope_terms", []),
        "selected_roots": [compact_scope_root(root) for root in roots],
        "private_roots": [compact_scope_root(root) for root in roots if root.get("privacy_label") == "private_root"],
        "searched_scope": {
            "roots": [compact_scope_root(root) for root in searched_roots],
            "searched_files": search_result.get("searched_files", 0),
            "searched_bytes": search_result.get("searched_bytes", 0),
            "source_plan_selected_files": retrieval_source_plan["sources_selected"],
            "source_plan_pruned_files": retrieval_source_plan["sources_pruned_hard"],
            "source_plan_deferred_files": retrieval_source_plan["sources_deferred_budget"],
            "result_meaning": "not_found_in_this_scope_only" if not search_result.get("groups") else "candidates_found_in_this_scope",
        },
        "excluded_scope": {
            "rejected_roots": [compact_rejected_root(root) for root in rejected_roots],
            "rejected_root_count": len(rejected_roots),
            "meaning": "requested roots rejected before traversal by source-planning guards",
        },
        "not_searched_scope": {
            "missing_roots": [compact_scope_root(root) for root in missing_roots],
            "missing_root_count": len(missing_roots),
            "cap_limited": cap_hit,
            "outside_selected_roots": "not searched unless explicitly selected with safe caps",
            "source_plan": {
                "sources_total": retrieval_source_plan["sources_total"],
                "sources_selected": retrieval_source_plan["sources_selected"],
                "sources_pruned_hard": retrieval_source_plan["sources_pruned_hard"],
                "sources_deferred_budget": retrieval_source_plan["sources_deferred_budget"],
                "prune_reasons": retrieval_source_plan["prune_reasons"],
                "deferred_reason_counts": retrieval_source_plan["deferred_reason_counts"],
                "source_tiers": retrieval_source_plan["source_tiers"],
                "budget_basis": retrieval_source_plan["budget_basis"],
                "retry_guidance": retrieval_source_plan["retry_guidance"],
                "exhaustive_available": retrieval_source_plan["exhaustive_available"],
                "raw_paths_included": False,
                "raw_text_included": False,
            },
            "notes": not_searched_notes,
        },
        "cap_hit": cap_hit,
    }


def compact_scope_root(root: dict) -> dict:
    return {
        "kind": root.get("kind"),
        "scope_label": root.get("scope_label", "selected_root"),
        "privacy_label": root.get("privacy_label", "private_root"),
        "known": root.get("known"),
        "reason": root.get("reason"),
    }


def compact_rejected_root(root: dict) -> dict:
    return {
        "scope_label": root.get("scope_label", "excluded_root"),
        "reason": root.get("reason"),
    }


def root_is_missing(root: dict, blind_spots: list[str]) -> bool:
    path = root.get("path")
    if not path:
        return False
    return any(item.startswith("Missing transcript root:") and path in item for item in blind_spots)


def cap_hit_status(*, warnings: list[str], limits: dict) -> dict:
    caps_hit: set[str] = set()
    normalized_warnings: set[str] = set()
    for warning in warnings:
        lowered = warning.lower()
        if "max file cap" in lowered:
            caps_hit.add("max_file_cap")
            normalized_warnings.add("stopped_at_max_file_cap")
        if "max byte cap" in lowered:
            caps_hit.add("max_byte_cap")
            normalized_warnings.add("stopped_at_max_byte_cap")
        if "max match cap" in lowered:
            if "retention-capped" in lowered or "final candidates were capped" in lowered:
                caps_hit.add("max_match_retention_cap")
                normalized_warnings.add("candidate_retention_capped_at_max_match_cap")
            else:
                caps_hit.add("max_match_cap")
                normalized_warnings.add("stopped_at_max_match_cap")
        if "max elapsed cap" in lowered:
            caps_hit.add("max_elapsed_cap")
            normalized_warnings.add("stopped_at_max_elapsed_cap")
        if "reservoir-capped" in lowered:
            caps_hit.add("weak_candidate_reservoir_cap")
            normalized_warnings.add("weak_candidates_reservoir_capped")
    return {
        "hit": bool(caps_hit),
        "caps_hit": sorted(caps_hit),
        "normalized_warnings": sorted(normalized_warnings),
        "limits": limits,
    }


def next_search_suggestions(
    *,
    status: str,
    source_plan: dict,
    candidates: list[CandidateRow],
    search_result: dict | None = None,
) -> list[str]:
    suggestions: list[str] = []
    query = source_plan.get("query", {})
    if not query.get("date_window", {}).get("start"):
        suggestions.append("Add an approximate date window if the user remembers when this happened.")
    if not query.get("role"):
        suggestions.append("Add --role user or --role assistant when the remembered speaker matters.")
    if not query.get("artifact_hints"):
        suggestions.append("Add a file, task slug, report name, or project folder hint if available.")
    if status == "no_evidence":
        source_scope = build_source_scope_summary(source_plan, search_result or {})
        if source_scope["not_searched_scope"].get("missing_root_count", 0):
            suggestions.append("Resolve missing selected roots or rerun with an available transcript root before relying on no-evidence.")
        if source_scope["excluded_scope"].get("rejected_root_count", 0):
            suggestions.append("Inspect rejected/excluded roots and rerun with an allowed safe root if that source was intended.")
        if source_scope["cap_hit"].get("hit"):
            suggestions.append("Raise safe caps or narrow the query/source scope before treating this no-evidence result as meaningful.")
        if source_scope["not_searched_scope"]["source_plan"].get("sources_deferred_budget", 0) > 0:
            suggestions.append("Raise safe source budgets or add a stronger artifact/task/date/root hint to search budget-deferred files.")
        suggestions.append("Inspect no_evidence_report and rerun with any extra artifact, project, date, or speaker hint before declaring permanent no evidence.")
    elif len(candidates) > INLINE_CANDIDATE_LIMIT:
        suggestions.append("Use the top clusters or write a report instead of opening every candidate.")
    return suggestions


def report_policy(
    *,
    candidate_count: int,
    context_bytes: int,
    raw_baseline_bytes: int,
    estimated_reduction_ratio: float,
    output_format: str,
    report_output: str | None,
) -> dict:
    should_write = bool(report_output) or candidate_count > INLINE_CANDIDATE_LIMIT or context_bytes > INLINE_CONTEXT_BYTE_LIMIT
    return {
        "inline_candidate_limit": INLINE_CANDIDATE_LIMIT,
        "inline_context_byte_limit": INLINE_CONTEXT_BYTE_LIMIT,
        "candidate_count": candidate_count,
        "estimated_context_bytes": context_bytes,
        "estimated_tokens": estimate_tokens(context_bytes),
        "raw_baseline_bytes": raw_baseline_bytes,
        "estimated_reduction_ratio": estimated_reduction_ratio,
        "should_write_report": should_write,
        "report_output": report_output,
        "format": output_format,
        "rule": "write a report when candidates exceed inline limits, context bytes exceed the budget, or --report-output is provided",
    }


def estimate_tokens(byte_count: int) -> int:
    return max(1, round(byte_count / 4))


def reduction_ratio(value: int, baseline: int) -> float:
    if baseline <= 0:
        return 0.0
    return round(max(0.0, 1 - (value / baseline)), 3)
