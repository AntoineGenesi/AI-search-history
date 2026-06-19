from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INLINE_CANDIDATE_LIMIT = 12
INLINE_CONTEXT_BYTE_LIMIT = 24_000
SOURCE_WINDOW_LIMIT = 5
SOURCE_WINDOW_REASON_CODE_LIMIT = 8
SOURCE_LOCATOR_TOP_CANDIDATES_LIMIT = 5
COMPACT_PROVENANCE_CANDIDATE_LIMIT = 8
COMPACT_PROVENANCE_SOURCE_REF_LIMIT = 12
COMPACT_PROVENANCE_NEXT_READ_LIMIT = 8
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
        next_reads=next_reads_from_candidates(candidates, expansion_action_context=expansion_action_context),
        source_family="local_ai_conversation_jsonl",
        question_family={
            "primary": "agent_find",
        },
        product_path="find",
        no_evidence_report=no_evidence_report,
        retrieval_pressure=agent_find.get("retrieval_pressure"),
        expansion_action_context=expansion_action_context,
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
        "read-more": "Bounded provenance expansion for an exact source_ref: record, window, or next records without rerunning broad search.",
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
        next_read_count_considered=len(next_reads or []),
        next_reads_included=len(compact_next_reads),
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
            "raw_private_text_or_snippets_included": False,
            "source_window_text_included": False,
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
    no_evidence_report = no_evidence_report or {}
    compact_output_budget = (compact_context_budget or {}).get("output_budget") or {}
    compact_output_counts = compact_output_budget.get("counts") or {}
    compact_output_limits = compact_output_budget.get("limits") or {}
    fielded = pressure.get("fielded_bm25") or {}
    query_streams = fielded.get("query_streams") or {}
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
                    (fielded.get("index_summary") or {}).get("planned_file_enumeration_reused", False)
                ),
                "query_stream_count": int(query_streams.get("stream_count") or 0),
                "stream_hit_limit_count": int(query_streams.get("stream_hit_limit_count") or 0),
                "saturated_stream_count": int(query_streams.get("saturated_stream_count") or 0),
                "row_count_before_dedupe": int(query_streams.get("row_count_before_dedupe") or 0),
                "row_count_after_dedupe": int(query_streams.get("row_count_after_dedupe") or 0),
                "rows_dropped_by_stream_cap_total": int(query_streams.get("rows_dropped_by_stream_cap_total") or 0),
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
                "source_count_considered": int(grouping.get("source_count_considered") or 0),
                "source_count_selected": int(grouping.get("source_count_selected") or 0),
                "stream_head_rows_selected": int(grouping.get("stream_head_rows_selected") or 0),
                "source_head_rows_selected": int(grouping.get("source_head_rows_selected") or 0),
                "global_tail_rows_selected": int(grouping.get("global_tail_rows_selected") or 0),
                "group_interleaved_rows_selected": int(grouping.get("group_interleaved_rows_selected") or 0),
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
            "source_count_considered": int(fielded_source_grouping.get("source_count_considered") or 0),
            "source_count_selected": int(fielded_source_grouping.get("source_count_selected") or 0),
            "dominant_source_share_before": float(
                fielded_source_grouping.get("dominant_source_share_before") or 0.0
            ),
            "dominant_source_share_after": float(fielded_source_grouping.get("dominant_source_share_after") or 0.0),
            "stream_head_rows_selected": int(fielded_source_grouping.get("stream_head_rows_selected") or 0),
            "source_head_rows_selected": int(fielded_source_grouping.get("source_head_rows_selected") or 0),
            "global_tail_rows_selected": int(fielded_source_grouping.get("global_tail_rows_selected") or 0),
            "group_interleaved_rows_selected": int(fielded_source_grouping.get("group_interleaved_rows_selected") or 0),
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
    fielded_source_grouping = diagnostics.get("fielded_source_grouping") or {}
    fielded_context_neighborhood = diagnostics.get("fielded_context_neighborhood") or fielded_bm25.get("context_neighborhood") or {}
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
            "context_neighborhood": sanitize_context_neighborhood(fielded_context_neighborhood),
            "materialization": fielded_bm25.get("materialization") or {},
            "index_summary": fielded_bm25.get("index_summary") or {},
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_session_ids_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "fielded_context_neighborhood": sanitize_context_neighborhood(fielded_context_neighborhood),
        "fielded_source_grouping": {
            "enabled": bool(fielded_source_grouping.get("enabled", False)),
            "strategy": str(fielded_source_grouping.get("strategy") or "disabled"),
            "candidate_rows_considered": int(fielded_source_grouping.get("candidate_rows_considered") or 0),
            "candidate_rows_selected": int(fielded_source_grouping.get("candidate_rows_selected") or 0),
            "source_count_considered": int(fielded_source_grouping.get("source_count_considered") or 0),
            "source_count_selected": int(fielded_source_grouping.get("source_count_selected") or 0),
            "dominant_source_share_before": float(
                fielded_source_grouping.get("dominant_source_share_before") or 0.0
            ),
            "dominant_source_share_after": float(fielded_source_grouping.get("dominant_source_share_after") or 0.0),
            "stream_head_rows_selected": int(fielded_source_grouping.get("stream_head_rows_selected") or 0),
            "source_head_rows_selected": int(fielded_source_grouping.get("source_head_rows_selected") or 0),
            "global_tail_rows_selected": int(fielded_source_grouping.get("global_tail_rows_selected") or 0),
            "group_interleaved_rows_selected": int(fielded_source_grouping.get("group_interleaved_rows_selected") or 0),
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
        },
    }


def sanitize_query_stream_pressure(value: Any) -> dict[str, Any]:
    streams = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(streams.get("enabled", False)),
        "strategy": str(streams.get("strategy") or "disabled"),
        "stream_count": int(streams.get("stream_count") or 0),
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
    next_read_count_considered: int,
    next_reads_included: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "measurement_basis": "compact_provenance_handoff_packet_only",
        "limits": {
            "candidates": COMPACT_PROVENANCE_CANDIDATE_LIMIT,
            "source_refs": COMPACT_PROVENANCE_SOURCE_REF_LIMIT,
            "source_windows": SOURCE_WINDOW_LIMIT,
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
        },
        "field_actions": {
            "candidates": "included_metadata_capped",
            "source_refs": "included_exact_capped",
            "source_windows": "reused_from_source_locator_handoff",
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
    )
    source_windows = source_window_result["windows"]
    source_window_selection_policy = source_window_result["selection_policy"]
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
            "ordering_basis": "query_plan_intent_tiered_source_head_then_existing_order_fill",
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
            "source_window_text_included": False,
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
    stage_diagnostics = build_stage_diagnostics(
        product_path=product_path,
        query_plan=query_plan,
        search_result=search_result,
        retrieval_pressure=retrieval_pressure,
        source_window_packaging=source_window_packaging,
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
) -> list[dict[str, Any]]:
    return build_source_window_package(
        candidates=candidates,
        next_reads=next_reads,
        query_plan=query_plan,
        expansion_action_context=expansion_action_context,
    )["windows"]


def build_source_window_package(
    *,
    candidates: list[dict[str, Any]] | list[CandidateRow],
    next_reads: list[dict[str, Any]],
    query_plan: dict[str, Any] | None,
    expansion_action_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    next_read_by_candidate = {
        str(read.get("candidate_id") or ""): read for read in next_reads if read.get("candidate_id") is not None
    }
    primary_intent = (query_plan or {}).get("primary_intent") or "generic_history"
    window_needs = list((query_plan or {}).get("source_window_needs") or [])
    normalized_candidates = normalize_source_window_candidates(candidates)
    selected_candidates = select_source_window_candidates(
        normalized_candidates,
        query_plan=query_plan or {},
        primary_intent=primary_intent,
    )
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
        next_read = {key: value for key, value in next_read.items() if key != "expansion_actions"}
        windows.append(
            {
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
        )
    selected_ids = {str(item["data"].get("candidate_id") or "") for item in selected_candidates}
    selected_sources = {str(item["source_key"]) for item in selected_candidates}
    selection_policy = {
        "schema_version": 1,
        "ordering_basis": "query_plan_intent_tiered_source_head_then_existing_order_fill",
        "candidate_count_considered": len(normalized_candidates),
        "distinct_source_count_considered": len({str(item["source_key"]) for item in normalized_candidates}),
        "distinct_source_count_selected": len(selected_sources),
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
    }
    return {"windows": windows, "selection_policy": selection_policy}


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
            }
        )
    return normalized


def select_source_window_candidates(
    normalized_candidates: list[dict[str, Any]],
    *,
    query_plan: dict[str, Any],
    primary_intent: str,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_source_keys: set[str] = set()
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
        source_key = str(item["source_key"])
        if source_key in selected_source_keys:
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
        selected_source_keys.add(source_key)

    for item in normalized_candidates:
        if len(selected) >= SOURCE_WINDOW_LIMIT:
            break
        candidate_id = str(item["data"].get("candidate_id") or "")
        if candidate_id in selected_ids:
            continue
        selected.append({**item, "selection_reason_code": "existing_order_fill"})
        selected_ids.add(candidate_id)

    return selected


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
            reads.append(
                {
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
            )
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
