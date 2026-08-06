from __future__ import annotations

import time
from typing import Any

from .fielded_bm25 import build_fielded_bm25_index, search_with_fielded_bm25
from .profiling import finish_phase_profile, start_phase_profile
from .rrf_fusion import DEFAULT_RRF_RANK_CONSTANT, RrfLane, fuse_ranked_groups
from .scanner import search
from .sources import (
    build_command_local_source_catalog,
    build_command_local_source_plan,
    materialize_planned_jsonl_files,
)


RETRIEVAL_MODE_PACKAGE = "package"
RETRIEVAL_MODE_RRF_FUSION = "rrf-fusion"
RETRIEVAL_MODE_FIELDED_BM25 = "fielded-bm25"
RETRIEVAL_MODES = (RETRIEVAL_MODE_PACKAGE, RETRIEVAL_MODE_RRF_FUSION, RETRIEVAL_MODE_FIELDED_BM25)
RRF_LANE_NAMES = ("package_search", "package_search_fielded_bm25")
QUERY_PLAN_MODE_AUTO = "auto"
QUERY_PLAN_MODE_OFF = "off"
QUERY_PLAN_MODE_DEBUG = "debug"
QUERY_PLAN_MODES = (QUERY_PLAN_MODE_AUTO, QUERY_PLAN_MODE_OFF, QUERY_PLAN_MODE_DEBUG)


def run_retrieval_mode(
    *,
    retrieval_mode: str,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
    use_rg_prefilter: bool = False,
    fielded_bm25_index_dir: str | None = None,
    query_plan_mode: str = QUERY_PLAN_MODE_AUTO,
    continuation_ledger: bool = False,
) -> dict[str, Any]:
    if retrieval_mode == RETRIEVAL_MODE_PACKAGE:
        return search(
            plan,
            intent,
            default_tz=default_tz,
            cwd=cwd,
            mask_context=mask_context,
            use_rg_prefilter=use_rg_prefilter,
        )
    if retrieval_mode == RETRIEVAL_MODE_RRF_FUSION:
        return run_rrf_fusion_retrieval(
            plan=plan,
            intent=intent,
            default_tz=default_tz,
            cwd=cwd,
            mask_context=mask_context,
            use_rg_prefilter=use_rg_prefilter,
            fielded_bm25_index_dir=fielded_bm25_index_dir,
        )
    if retrieval_mode == RETRIEVAL_MODE_FIELDED_BM25:
        return run_fielded_bm25_retrieval(
            plan=plan,
            intent=intent,
            default_tz=default_tz,
            cwd=cwd,
            mask_context=mask_context,
            fielded_bm25_index_dir=fielded_bm25_index_dir,
            query_plan_mode=query_plan_mode,
            continuation_ledger=continuation_ledger,
        )
    raise ValueError(f"unsupported retrieval mode: {retrieval_mode}")


def run_fielded_bm25_retrieval(
    *,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
    fielded_bm25_index_dir: str | None = None,
    query_plan_mode: str = QUERY_PLAN_MODE_AUTO,
    continuation_ledger: bool = False,
) -> dict[str, Any]:
    total_profile_token = start_phase_profile("total_fielded_bm25_retrieval")
    enumeration_profile_token = start_phase_profile("shared_source_enumeration")
    planned_jsonl_files = materialize_planned_jsonl_files(plan, preferred_terms=intent.base_terms)
    enumeration_profile = finish_phase_profile(
        enumeration_profile_token,
        file_count=planned_jsonl_files.file_count,
        byte_count=planned_jsonl_files.byte_count,
    )
    enumeration_elapsed_ms = float(enumeration_profile["elapsed_ms"])
    source_catalog = build_command_local_source_catalog(plan, planned_jsonl_files)
    selected_jsonl_files, source_plan = build_command_local_source_plan(plan, planned_jsonl_files, intent)

    fielded_index_profile_token = start_phase_profile("fielded_bm25_index_build")
    fielded_index = build_fielded_bm25_index(
        plan,
        default_tz=default_tz,
        planned_jsonl_files=planned_jsonl_files,
        persistent_index_dir=fielded_bm25_index_dir,
    )
    fielded_index_profile = finish_phase_profile(
        fielded_index_profile_token,
        indexed_file_count=int(fielded_index.metrics.get("indexed_files", 0)),
        indexed_record_count=int(fielded_index.metrics.get("indexed_records", 0)),
        indexed_chunk_count=int(fielded_index.metrics.get("indexed_chunks", 0)),
        build_bytes=int(fielded_index.metrics.get("build_bytes", 0)),
    )
    fielded_index_elapsed_ms = float(fielded_index_profile["elapsed_ms"])

    fielded_started = start_phase_profile("fielded_bm25_search_materialization")
    fielded_result = search_with_fielded_bm25(
        fielded_index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        include_private_diagnostics=True,
        query_plan_mode=query_plan_mode,
        continuation_ledger=continuation_ledger,
    )
    fielded_count = _candidate_count(fielded_result)
    fielded_profile = finish_phase_profile(
        fielded_started,
        candidate_count=fielded_count,
        candidate_rows=int(fielded_result.get("candidate_rows_generated", 0) or 0),
        searched_files=int(fielded_result.get("searched_files", 0)),
        searched_bytes=int(fielded_result.get("searched_bytes", 0)),
        records_considered=int(fielded_result.get("records_considered", 0)),
    )
    fielded_elapsed_ms = float(fielded_profile["elapsed_ms"])
    fielded_result.pop("_searched_file_paths", None)
    warnings = sorted(set(fielded_result.get("warnings", []) or []))
    total_profile = finish_phase_profile(
        total_profile_token,
        candidate_count=fielded_count,
        planned_file_count=planned_jsonl_files.file_count,
        selected_file_count=selected_jsonl_files.file_count,
        searched_files=int(fielded_result.get("searched_files", 0)),
        searched_bytes=int(fielded_result.get("searched_bytes", 0)),
    )

    fielded_result.update(
        {
            "retrieval_mode": RETRIEVAL_MODE_FIELDED_BM25,
            "warnings": warnings,
            "retrieval_diagnostics": build_fielded_bm25_retrieval_diagnostics(
                fielded_result=fielded_result,
                fielded_index_summary=fielded_index.sanitized_summary(),
                fielded_index_elapsed_ms=fielded_index_elapsed_ms,
                fielded_elapsed_ms=fielded_elapsed_ms,
                enumeration_elapsed_ms=enumeration_elapsed_ms,
                planned_jsonl_file_diagnostics=planned_jsonl_files.to_diagnostics(),
                source_catalog=source_catalog,
                source_plan=source_plan,
                warnings=warnings,
                profiles={
                    "total_fielded_bm25_retrieval": total_profile,
                    "shared_source_enumeration": enumeration_profile,
                    "fielded_bm25_index_build": fielded_index_profile,
                    "fielded_bm25_search_materialization": fielded_profile,
                },
            ),
        }
    )
    if mask_context is not None and getattr(mask_context, "stats", None) is not None:
        fielded_result["masking"] = mask_context.stats.to_dict()
        fielded_result["warnings"] = sorted(set(fielded_result["warnings"] + mask_context.stats.warnings))
    return fielded_result


def run_rrf_fusion_retrieval(
    *,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
    use_rg_prefilter: bool = False,
    fielded_bm25_index_dir: str | None = None,
) -> dict[str, Any]:
    total_profile_token = start_phase_profile("total_explicit_rrf_retrieval")
    enumeration_profile_token = start_phase_profile("shared_source_enumeration")
    planned_jsonl_files = materialize_planned_jsonl_files(plan, preferred_terms=intent.base_terms)
    enumeration_profile = finish_phase_profile(
        enumeration_profile_token,
        file_count=planned_jsonl_files.file_count,
        byte_count=planned_jsonl_files.byte_count,
    )
    enumeration_elapsed_ms = float(enumeration_profile["elapsed_ms"])
    source_catalog = build_command_local_source_catalog(plan, planned_jsonl_files)
    _selected_jsonl_files, source_plan = build_command_local_source_plan(plan, planned_jsonl_files, intent)

    fielded_index_profile_token = start_phase_profile("fielded_bm25_index_build")
    fielded_index = build_fielded_bm25_index(
        plan,
        default_tz=default_tz,
        planned_jsonl_files=planned_jsonl_files,
        persistent_index_dir=fielded_bm25_index_dir,
    )
    fielded_index_profile = finish_phase_profile(
        fielded_index_profile_token,
        indexed_file_count=int(fielded_index.metrics.get("indexed_files", 0)),
        indexed_record_count=int(fielded_index.metrics.get("indexed_records", 0)),
        indexed_chunk_count=int(fielded_index.metrics.get("indexed_chunks", 0)),
        build_bytes=int(fielded_index.metrics.get("build_bytes", 0)),
    )
    fielded_index_elapsed_ms = float(fielded_index_profile["elapsed_ms"])
    fielded_started = start_phase_profile("fielded_bm25_search_materialization")
    fielded_result = search_with_fielded_bm25(
        fielded_index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        include_private_diagnostics=True,
    )
    fielded_count = _candidate_count(fielded_result)
    fielded_profile = finish_phase_profile(
        fielded_started,
        candidate_count=fielded_count,
        candidate_rows=int(fielded_result.get("candidate_rows_generated", 0) or 0),
        searched_files=int(fielded_result.get("searched_files", 0)),
        searched_bytes=int(fielded_result.get("searched_bytes", 0)),
        records_considered=int(fielded_result.get("records_considered", 0)),
    )
    fielded_elapsed_ms = float(fielded_profile["elapsed_ms"])
    fielded_searched_paths = set(fielded_result.pop("_searched_file_paths", []) or [])

    package_profile_token = start_phase_profile("package_lane")
    package_result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        use_rg_prefilter=use_rg_prefilter,
        planned_jsonl_files=planned_jsonl_files,
        include_private_diagnostics=True,
        prioritized_file_paths=fielded_searched_paths,
    )
    package_count = _candidate_count(package_result)
    package_profile = finish_phase_profile(
        package_profile_token,
        candidate_count=package_count,
        searched_files=int(package_result.get("searched_files", 0)),
        searched_bytes=int(package_result.get("searched_bytes", 0)),
        records_considered=int(package_result.get("records_considered", 0)),
    )
    package_elapsed_ms = float(package_profile["elapsed_ms"])
    package_searched_paths = set(package_result.pop("_searched_file_paths", []) or [])

    fusion_profile_token = start_phase_profile("rrf_fusion")
    fused_result = fuse_ranked_groups(
        [
            RrfLane(RRF_LANE_NAMES[0], package_result.get("groups", [])),
            RrfLane(RRF_LANE_NAMES[1], fielded_result.get("groups", [])),
        ],
        rank_constant=DEFAULT_RRF_RANK_CONSTANT,
        candidate_window_size=plan.limits.max_matches,
        final_limit=plan.limits.max_matches,
        preferred_lane=RRF_LANE_NAMES[0],
    )
    fused_count = _candidate_count(fused_result)
    fusion_profile = finish_phase_profile(fusion_profile_token, candidate_count=fused_count)
    fusion_elapsed_ms = float(fusion_profile["elapsed_ms"])
    total_profile = finish_phase_profile(
        total_profile_token,
        candidate_count=fused_count,
        planned_file_count=planned_jsonl_files.file_count,
        searched_files=int(package_result.get("searched_files", 0)) + int(fielded_result.get("searched_files", 0)),
        searched_bytes=int(package_result.get("searched_bytes", 0)) + int(fielded_result.get("searched_bytes", 0)),
    )

    package_warnings = list(package_result.get("warnings", []) or [])
    fielded_warnings = list(fielded_result.get("warnings", []) or [])
    warnings = sorted(set(package_warnings + fielded_warnings))
    fused_result.update(
        {
            "retrieval_mode": RETRIEVAL_MODE_RRF_FUSION,
            "preserve_match_order": True,
            "warnings": warnings,
            "blind_spots": sorted(
                set((package_result.get("blind_spots", []) or []) + (fielded_result.get("blind_spots", []) or []))
            ),
            "searched_files": int(package_result.get("searched_files", 0))
            + int(fielded_result.get("searched_files", 0)),
            "searched_bytes": int(package_result.get("searched_bytes", 0))
            + int(fielded_result.get("searched_bytes", 0)),
            "records_considered": int(package_result.get("records_considered", 0))
            + int(fielded_result.get("records_considered", 0)),
            "package_result_status": package_result.get("status"),
            "fielded_bm25_result_status": fielded_result.get("status"),
            "retrieval_diagnostics": build_rrf_retrieval_diagnostics(
                fused_result=fused_result,
                package_result=package_result,
                fielded_result=fielded_result,
                fielded_index_summary=fielded_index.sanitized_summary(),
                package_elapsed_ms=package_elapsed_ms,
                fielded_index_elapsed_ms=fielded_index_elapsed_ms,
                fielded_elapsed_ms=fielded_elapsed_ms,
                fusion_elapsed_ms=fusion_elapsed_ms,
                enumeration_elapsed_ms=enumeration_elapsed_ms,
                planned_jsonl_file_diagnostics=planned_jsonl_files.to_diagnostics(),
                source_catalog=source_catalog,
                source_plan=source_plan,
                package_searched_paths=package_searched_paths,
                fielded_searched_paths=fielded_searched_paths,
                warnings=warnings,
                profiles={
                    "total_explicit_rrf_retrieval": total_profile,
                    "shared_source_enumeration": enumeration_profile,
                    "package_lane": package_profile,
                    "fielded_bm25_index_build": fielded_index_profile,
                    "fielded_bm25_search_materialization": fielded_profile,
                    "rrf_fusion": fusion_profile,
                },
            ),
        }
    )
    return fused_result


def build_fielded_bm25_retrieval_diagnostics(
    *,
    fielded_result: dict[str, Any],
    fielded_index_summary: dict[str, Any],
    fielded_index_elapsed_ms: float,
    fielded_elapsed_ms: float,
    enumeration_elapsed_ms: float,
    planned_jsonl_file_diagnostics: dict[str, Any],
    source_catalog: dict[str, Any],
    source_plan: dict[str, Any],
    warnings: list[str],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profiles = profiles if isinstance(profiles, dict) else {}
    cap_labels = cap_labels_from_warnings(warnings)
    fielded_materialization = _fielded_materialization_diagnostics(fielded_result)
    fielded_source_grouping = _fielded_source_grouping_diagnostics(fielded_result)
    fielded_context_neighborhood = _fielded_context_neighborhood_diagnostics(fielded_result)
    fielded_evidence_channels = _fielded_evidence_channel_diagnostics(fielded_result)
    continuation_candidate_ledger = _fielded_continuation_candidate_ledger_diagnostics(
        fielded_result.get("continuation_candidate_ledger")
    )
    child_query_plan = _fielded_child_query_plan_diagnostics(fielded_result)
    branch_fanout = _fielded_branch_fanout_diagnostics(fielded_result)
    fielded_summary = {
        "status": fielded_result.get("status"),
        "fallback_reason": fielded_result.get("fallback_reason"),
        "query_plan_mode": str(fielded_result.get("query_plan_mode") or QUERY_PLAN_MODE_AUTO),
        "child_query_plan": child_query_plan,
        "candidate_count": _candidate_count(fielded_result),
        "candidate_rows": int(fielded_result.get("candidate_rows", 0) or 0),
        "candidate_row_limit": int(fielded_result.get("candidate_row_limit", 0) or 0),
        "candidate_row_pool_limit": int(fielded_result.get("candidate_row_pool_limit", 0) or 0),
        "candidate_rows_generated": int(fielded_result.get("candidate_rows_generated", 0) or 0),
        "candidate_rows_considered": int(fielded_result.get("candidate_rows_considered", 0) or 0),
        "candidate_rows_from_query_streams": int(fielded_result.get("candidate_rows_from_query_streams", 0) or 0),
        "candidate_rows_retained": int(fielded_result.get("candidate_rows_retained", 0) or 0),
        "candidate_rows_surfaced": int(fielded_result.get("candidate_rows_surfaced", 0) or 0),
        "query_streams": _fielded_query_streams_diagnostics(fielded_result),
        "branch_fanout": branch_fanout,
        "evidence_channels": fielded_evidence_channels,
        **(
            {"continuation_candidate_ledger": continuation_candidate_ledger}
            if continuation_candidate_ledger
            else {}
        ),
        "context_neighborhood": fielded_context_neighborhood,
        "searched_files": int(fielded_result.get("searched_files", 0)),
        "searched_bytes": int(fielded_result.get("searched_bytes", 0)),
        "records_considered": int(fielded_result.get("records_considered", 0)),
        "records_rejected_by_existing_ranker": int(fielded_result.get("records_rejected_by_existing_ranker", 0) or 0),
        "candidate_records_scored": int(fielded_result.get("candidate_records_scored", 0) or 0),
        "candidate_records_deduped": int(fielded_result.get("candidate_records_deduped", 0) or 0),
        "bm25_rank_buckets": dict(fielded_result.get("bm25_rank_buckets") or {}),
        "materialization": fielded_materialization,
        "retrieval_budget": _fielded_retrieval_budget_diagnostics(fielded_result),
        "index_summary": fielded_index_summary,
        "scope": _fielded_scope_diagnostics(fielded_result.get("fielded_scope") or fielded_index_summary.get("scope")),
        "elapsed_ms": fielded_elapsed_ms,
        "elapsed_label": elapsed_label(fielded_elapsed_ms),
        "profile": _phase_profile(
            profiles.get("fielded_bm25_search_materialization"),
            "fielded_bm25_search_materialization",
        ),
        "index_build_elapsed_ms": fielded_index_elapsed_ms,
        "index_build_elapsed_label": elapsed_label(fielded_index_elapsed_ms),
        "index_build_profile": _phase_profile(
            profiles.get("fielded_bm25_index_build"),
            "fielded_bm25_index_build",
        ),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }
    fielded_candidate_stage_loss = _fielded_candidate_stage_loss_diagnostics(
        fielded_summary=fielded_summary,
        fielded_index_summary=fielded_index_summary,
        planned_jsonl_file_diagnostics=planned_jsonl_file_diagnostics,
        source_plan=source_plan,
        query_streams=fielded_summary["query_streams"],
        evidence_channels=fielded_evidence_channels,
        source_grouping=fielded_source_grouping,
        cap_labels=cap_labels,
    )
    fielded_summary["candidate_stage_loss"] = fielded_candidate_stage_loss
    if continuation_candidate_ledger:
        fielded_summary["continuation_candidate_ledger"] = continuation_candidate_ledger
    return {
        "retrieval_mode": RETRIEVAL_MODE_FIELDED_BM25,
        "feature_state": "active_explicit_opt_in",
        "strategy": "direct_fielded_bm25_record_retrieval",
        "fielded_mode_state": {
            "explicit_opt_in_required": True,
            "default_behavior_changed": False,
            "capped": bool(cap_labels),
            "cap_labels": cap_labels,
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
            "durable_fts_index_reused": bool(fielded_index_summary.get("durable_fts_index_reused", False)),
            "refresh_status": str(fielded_index_summary.get("refresh_status") or "unknown"),
            "scope_type": str((fielded_index_summary.get("scope") or {}).get("scope_type") or "none"),
            "reason": "fielded_bm25_uses_ephemeral_or_opt_in_durable_sqlite_fts5_over_planned_jsonl_sources",
        },
        "shared_source_enumeration": {
            **planned_jsonl_file_diagnostics,
            "elapsed_ms": enumeration_elapsed_ms,
            "profile": _phase_profile(profiles.get("shared_source_enumeration"), "shared_source_enumeration"),
            "consumed_by_lanes": [RETRIEVAL_MODE_FIELDED_BM25],
        },
        "source_scope_layers": _bm25_source_scope_layers(
            planned_jsonl_file_diagnostics=planned_jsonl_file_diagnostics,
            fielded_index_summary=fielded_index_summary,
            source_plan=source_plan,
            fielded_result=fielded_result,
        ),
        "source_catalog": source_catalog,
        "source_plan": source_plan,
        "fielded_bm25": fielded_summary,
        **(
            {"continuation_candidate_ledger": continuation_candidate_ledger}
            if continuation_candidate_ledger
            else {}
        ),
        "fielded_candidate_stage_loss": fielded_candidate_stage_loss,
        "fielded_evidence_channels": fielded_evidence_channels,
        "fielded_child_query_plan": child_query_plan,
        "fielded_branch_fanout": branch_fanout,
        "fielded_context_neighborhood": fielded_context_neighborhood,
        "fielded_source_grouping": fielded_source_grouping,
        "phase_profiles": {
            "total_fielded_bm25_retrieval": _phase_profile(
                profiles.get("total_fielded_bm25_retrieval"),
                "total_fielded_bm25_retrieval",
            ),
            "shared_source_enumeration": _phase_profile(
                profiles.get("shared_source_enumeration"),
                "shared_source_enumeration",
            ),
            "fielded_bm25_index_build": _phase_profile(
                profiles.get("fielded_bm25_index_build"),
                "fielded_bm25_index_build",
            ),
            "fielded_bm25_search_materialization": _phase_profile(
                profiles.get("fielded_bm25_search_materialization"),
                "fielded_bm25_search_materialization",
            ),
        },
        "cap_labels": cap_labels,
        "claim_boundaries": {
            "default_behavior_changed": False,
            "default_promotion_supported": False,
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "broad_speedup_claim_supported": False,
            "all_history_completeness_claim_supported": False,
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
            "raw_score_blending_used": False,
            "score_normalization_used": False,
            "tuned_lane_weights_used": False,
            "fused_score_threshold_used": False,
            "scanner_scheduling_constants_changed": False,
            "package_lane_scoped_or_skipped": False,
        },
    }


def _fielded_continuation_candidate_ledger_diagnostics(value: Any) -> dict[str, Any] | None:
    ledger = value if isinstance(value, dict) else None
    if not ledger or not bool(ledger.get("enabled", False)):
        return None
    items: list[dict[str, Any]] = []
    for item in ledger.get("items") or []:
        if not isinstance(item, dict):
            continue
        cursor = item.get("cursor") if isinstance(item.get("cursor"), dict) else {}
        items.append(
            {
                "ledger_id": str(item.get("ledger_id") or ""),
                "source_ref_hash": str(item.get("source_ref_hash") or ""),
                "cursor": {
                    "cursor_type": str(cursor.get("cursor_type") or ""),
                    "rank_after": int(cursor.get("rank_after") or 0),
                    "rowid_after_hash": str(cursor.get("rowid_after_hash") or ""),
                    "source_ref_hash_after": str(cursor.get("source_ref_hash_after") or ""),
                    "cursor_hash": str(cursor.get("cursor_hash") or ""),
                    "raw_path_included": False,
                    "raw_text_included": False,
                    "source_line_included": False,
                    "line_numbers_included": False,
                },
                "candidate_rank": int(item.get("candidate_rank") or 0),
                "selected_for_materialization": bool(item.get("selected_for_materialization", False)),
                "present_in_final_packet": bool(item.get("present_in_final_packet", False)),
                "recoverable_omitted": bool(item.get("recoverable_omitted", False)),
                "still_unreached": bool(item.get("still_unreached", False)),
                "evidence_state": str(item.get("evidence_state") or "unknown"),
                "source_kind": str(item.get("source_kind") or "unknown"),
                "role_bucket": str(item.get("role_bucket") or "unknown"),
                "timestamp_bucket_present": bool(item.get("timestamp_bucket_present", False)),
                "day_bucket_present": bool(item.get("day_bucket_present", False)),
                "line_handle_available": bool(item.get("line_handle_available", False)),
                "raw_path_included": False,
                "raw_text_included": False,
                "raw_snippet_included": False,
                "source_line_included": False,
                "line_numbers_included": False,
            }
        )
    next_cursor = ledger.get("next_cursor") if isinstance(ledger.get("next_cursor"), dict) else None
    sanitized_next_cursor = None
    if next_cursor:
        sanitized_next_cursor = {
            "cursor_type": str(next_cursor.get("cursor_type") or ""),
            "rank_after": int(next_cursor.get("rank_after") or 0),
            "rowid_after_hash": str(next_cursor.get("rowid_after_hash") or ""),
            "source_ref_hash_after": str(next_cursor.get("source_ref_hash_after") or ""),
            "cursor_hash": str(next_cursor.get("cursor_hash") or ""),
            "raw_path_included": False,
            "raw_text_included": False,
            "source_line_included": False,
            "line_numbers_included": False,
        }
    return {
        "schema_version": int(ledger.get("schema_version") or 1),
        "version": str(ledger.get("version") or ""),
        "enabled": True,
        "mode": str(ledger.get("mode") or ""),
        "strategy": str(ledger.get("strategy") or ""),
        "bounded": bool(ledger.get("bounded", True)),
        "pass_count": int(ledger.get("pass_count") or 0),
        "retrieval_chunk_count": int(ledger.get("retrieval_chunk_count") or 0),
        "multi_pass": bool(ledger.get("multi_pass", False)),
        "ledger_item_limit": int(ledger.get("ledger_item_limit") or 0),
        "candidate_row_pool_limit": int(ledger.get("candidate_row_pool_limit") or 0),
        "ledger_item_count": int(ledger.get("ledger_item_count") or len(items)),
        "total_candidate_rows_discovered": int(ledger.get("total_candidate_rows_discovered") or 0),
        "candidate_rows_retained_for_materialization": int(
            ledger.get("candidate_rows_retained_for_materialization") or 0
        ),
        "candidate_rows_materialized": int(ledger.get("candidate_rows_materialized") or 0),
        "final_packet_candidate_count": int(ledger.get("final_packet_candidate_count") or 0),
        "final_packet_present_count": int(ledger.get("final_packet_present_count") or 0),
        "recoverable_omitted_count": int(ledger.get("recoverable_omitted_count") or 0),
        "still_unreached_count": int(ledger.get("still_unreached_count") or 0),
        "continuation_available": bool(ledger.get("continuation_available", False)),
        "next_cursor": sanitized_next_cursor,
        "continuation_policy": _fielded_continuation_policy_diagnostics(ledger.get("continuation_policy")),
        "stage_counts": _safe_int_map(ledger.get("stage_counts")),
        "loss_stage_summary": _safe_int_map(ledger.get("loss_stage_summary")),
        "items": items,
        "privacy": {
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_snippets_included": False,
            "source_lines_included": False,
            "line_numbers_included": False,
            "raw_session_ids_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "claim_boundaries": {
            "final_answer_quality_claim_supported": False,
            "token_savings_claim_supported": False,
            "default_promotion_supported": False,
            "all_history_completeness_claim_supported": False,
        },
    }


def _fielded_continuation_policy_diagnostics(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    return {
        "policy_version": str(policy.get("policy_version") or ""),
        "cursor_type": str(policy.get("cursor_type") or ""),
        "resume_semantics": str(policy.get("resume_semantics") or ""),
        "stable_order": str(policy.get("stable_order") or ""),
        "materialization": str(policy.get("materialization") or ""),
        "ledger_item_limit": int(policy.get("ledger_item_limit") or 0),
        "candidate_row_pool_limit": int(policy.get("candidate_row_pool_limit") or 0),
        "default_enabled": bool(policy.get("default_enabled", False)),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_snippets_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "raw_session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _fielded_candidate_stage_loss_diagnostics(
    *,
    fielded_summary: dict[str, Any],
    fielded_index_summary: dict[str, Any],
    planned_jsonl_file_diagnostics: dict[str, Any],
    source_plan: dict[str, Any],
    query_streams: dict[str, Any],
    evidence_channels: dict[str, Any],
    source_grouping: dict[str, Any],
    cap_labels: list[str],
) -> dict[str, Any]:
    planned_files = int(planned_jsonl_file_diagnostics.get("file_count") or 0)
    selected_files = int(source_plan.get("sources_selected") or 0)
    indexed_rows = int(fielded_index_summary.get("indexed_record_count") or 0)
    indexed_chunks = int(fielded_index_summary.get("indexed_chunk_count") or 0)
    raw_rows = int(query_streams.get("row_count_before_dedupe") or 0)
    unique_rows = int(query_streams.get("row_count_after_dedupe") or 0)
    stream_cap_drops = int(query_streams.get("rows_dropped_by_stream_cap_total") or 0)
    saturated_streams = int(query_streams.get("saturated_stream_count") or 0)
    fusion_drops = int(evidence_channels.get("dropped_row_count_after_fusion") or 0)
    grouping_drops = int(source_grouping.get("candidate_rows_dropped") or 0)
    source_cap_drops = int(source_grouping.get("rows_dropped_by_source_cap") or 0)
    grouped_source_windows = (
        source_grouping.get("grouped_source_windows")
        if isinstance(source_grouping.get("grouped_source_windows"), dict)
        else {}
    )
    intent_window_allocation = (
        source_grouping.get("intent_window_allocation")
        if isinstance(source_grouping.get("intent_window_allocation"), dict)
        else {}
    )
    materialized = int((fielded_summary.get("materialization") or {}).get("candidate_rows_materialized") or 0)
    retained = int(fielded_summary.get("candidate_rows_retained") or 0)
    surfaced = int(fielded_summary.get("candidate_rows_surfaced") or 0)
    ranker_or_materialization_drops = max(
        int(fielded_summary.get("candidate_rows_generated") or 0)
        - int(fielded_summary.get("candidate_records_scored") or 0),
        0,
    )
    final_cap_drops = max(retained - surfaced, 0)

    dominant_loss_stage = "none"
    if planned_files > 0 and selected_files == 0:
        dominant_loss_stage = "source_not_planned"
    elif selected_files > 0 and indexed_rows > 0 and unique_rows == 0:
        dominant_loss_stage = "no_first_stage_candidate"
    elif stream_cap_drops > 0 or saturated_streams > 0:
        dominant_loss_stage = "stream_cap_drop"
    elif fusion_drops > 0:
        dominant_loss_stage = "evidence_fusion_drop"
    elif grouping_drops > 0 or source_cap_drops > 0:
        dominant_loss_stage = "source_grouping_drop"
    elif ranker_or_materialization_drops > 0:
        dominant_loss_stage = "materialization_or_ranker_drop"
    elif final_cap_drops > 0 or "max_match" in cap_labels:
        dominant_loss_stage = "final_match_cap"

    return {
        "schema_version": 1,
        "retrieval_mode": RETRIEVAL_MODE_FIELDED_BM25,
        "summary_answer": "candidates_are_generated_by_source_planning_fielded_index_query_streams_evidence_fusion_then_lost_at_the_dominant_loss_stage",
        "dominant_loss_stage": dominant_loss_stage,
        "allowed_dominant_loss_stages": [
            "source_not_planned",
            "no_first_stage_candidate",
            "stream_cap_drop",
            "evidence_fusion_drop",
            "source_grouping_drop",
            "materialization_or_ranker_drop",
            "final_match_cap",
            "passage_window_sparse",
            "none",
        ],
        "source_planning": {
            "planned_file_count": planned_files,
            "selected_file_count": selected_files,
            "deferred_source_count": int(source_plan.get("sources_deferred_budget") or 0),
            "pruned_source_count": int(source_plan.get("sources_pruned_hard") or 0),
            "planned_byte_count": int(planned_jsonl_file_diagnostics.get("byte_count") or 0),
            "indexed_row_count": indexed_rows,
            "indexed_chunk_count": indexed_chunks,
            "build_bytes": int(fielded_index_summary.get("build_bytes") or 0),
        },
        "query_streams": {
            "stream_count": int(query_streams.get("stream_count") or 0),
            "child_stream_count": int(query_streams.get("child_stream_count") or 0),
            "saturated_stream_count": saturated_streams,
            "raw_row_count": raw_rows,
            "unique_row_count": unique_rows,
            "duplicate_row_count": int(query_streams.get("duplicate_row_count") or 0),
            "stream_cap_drop_count": stream_cap_drops,
            "selected_rows_by_stream": _safe_int_map(query_streams.get("selected_rows_by_stream")),
            "rows_dropped_by_stream_cap": _safe_int_map(query_streams.get("rows_dropped_by_stream_cap")),
        },
        "evidence_channels": {
            "channel_count": int(evidence_channels.get("channel_count") or 0),
            "channel_labels": [str(label) for label in evidence_channels.get("channel_labels") or []],
            "channel_kinds": {
                str(label): str(kind) for label, kind in (evidence_channels.get("channel_kinds") or {}).items()
            },
            "channel_row_counts": _safe_int_map(evidence_channels.get("channel_row_counts")),
            "unique_row_count_before_fusion": int(evidence_channels.get("unique_row_count_before_fusion") or 0),
            "selected_row_count_after_fusion": int(evidence_channels.get("selected_row_count_after_fusion") or 0),
            "dropped_row_count_after_fusion": fusion_drops,
            "selected_rows_by_channel_after_fusion": _safe_int_map(
                evidence_channels.get("selected_rows_by_channel_after_fusion")
            ),
            "dropped_rows_by_channel_after_fusion": _safe_int_map(
                evidence_channels.get("dropped_rows_by_channel_after_fusion")
            ),
            "selected_rows_by_channel_after_grouping": _safe_int_map(
                evidence_channels.get("selected_rows_by_channel_after_grouping")
            ),
            "dropped_rows_by_channel_after_grouping": _safe_int_map(
                evidence_channels.get("dropped_rows_by_channel_after_grouping")
            ),
        },
        "source_grouping": {
            "candidate_rows_considered": int(source_grouping.get("candidate_rows_considered") or 0),
            "candidate_rows_selected": int(source_grouping.get("candidate_rows_selected") or 0),
            "candidate_rows_dropped": grouping_drops,
            "source_count_considered": int(source_grouping.get("source_count_considered") or 0),
            "source_count_selected": int(source_grouping.get("source_count_selected") or 0),
            "source_group_count_considered": int(source_grouping.get("source_group_count_considered") or 0),
            "source_group_count_selected": int(source_grouping.get("source_group_count_selected") or 0),
            "source_group_count_omitted": int(source_grouping.get("source_group_count_omitted") or 0),
            "session_group_count_considered": int(source_grouping.get("session_group_count_considered") or 0),
            "session_group_count_selected": int(source_grouping.get("session_group_count_selected") or 0),
            "session_group_count_omitted": int(source_grouping.get("session_group_count_omitted") or 0),
            "day_group_count_considered": int(source_grouping.get("day_group_count_considered") or 0),
            "day_group_count_selected": int(source_grouping.get("day_group_count_selected") or 0),
            "day_group_count_omitted": int(source_grouping.get("day_group_count_omitted") or 0),
            "window_group_count_considered": int(source_grouping.get("window_group_count_considered") or 0),
            "window_group_count_selected": int(source_grouping.get("window_group_count_selected") or 0),
            "window_group_count_omitted": int(source_grouping.get("window_group_count_omitted") or 0),
            "grouped_source_windows": {
                "enabled": bool(grouped_source_windows.get("enabled", False)),
                "selection_version": str(grouped_source_windows.get("selection_version") or ""),
                "strategy": str(grouped_source_windows.get("strategy") or "disabled"),
                "window_identity_components": [
                    str(item) for item in grouped_source_windows.get("window_identity_components") or []
                ],
                "near_contiguous_source_order_gap": int(
                    grouped_source_windows.get("near_contiguous_source_order_gap") or 0
                ),
                "inner_hit_limit_per_window": int(grouped_source_windows.get("inner_hit_limit_per_window") or 0),
                "window_count_considered": int(grouped_source_windows.get("window_count_considered") or 0),
                "window_count_selected": int(grouped_source_windows.get("window_count_selected") or 0),
                "window_count_omitted": int(grouped_source_windows.get("window_count_omitted") or 0),
                "selected_window_head_count": int(grouped_source_windows.get("selected_window_head_count") or 0),
                "selected_inner_hit_count": int(grouped_source_windows.get("selected_inner_hit_count") or 0),
                "omitted_inner_hit_count": int(grouped_source_windows.get("omitted_inner_hit_count") or 0),
                "max_inner_hit_count": int(grouped_source_windows.get("max_inner_hit_count") or 0),
                "merged_adjacent_window_count": int(grouped_source_windows.get("merged_adjacent_window_count") or 0),
                "merged_adjacent_inner_hit_count": int(grouped_source_windows.get("merged_adjacent_inner_hit_count") or 0),
                "distinct_sources_considered": int(grouped_source_windows.get("distinct_sources_considered") or 0),
                "distinct_sessions_considered": int(grouped_source_windows.get("distinct_sessions_considered") or 0),
                "distinct_days_considered": int(grouped_source_windows.get("distinct_days_considered") or 0),
                "distinct_sources_selected": int(grouped_source_windows.get("distinct_sources_selected") or 0),
                "distinct_sessions_selected": int(grouped_source_windows.get("distinct_sessions_selected") or 0),
                "distinct_days_selected": int(grouped_source_windows.get("distinct_days_selected") or 0),
                "distinct_windows_selected": int(grouped_source_windows.get("distinct_windows_selected") or 0),
                "omission_reason_counts": _safe_int_map(grouped_source_windows.get("omission_reason_counts")),
                "route_window_count_considered": int(grouped_source_windows.get("route_window_count_considered") or 0),
                "route_window_count_selected": int(grouped_source_windows.get("route_window_count_selected") or 0),
                "route_window_head_selection_count": int(
                    grouped_source_windows.get("route_window_head_selection_count") or 0
                ),
                "query_plan_need_window_count_considered": int(
                    grouped_source_windows.get("query_plan_need_window_count_considered") or 0
                ),
                "query_plan_need_window_count_matched": int(
                    grouped_source_windows.get("query_plan_need_window_count_matched") or 0
                ),
                "query_plan_need_window_count_selected": int(
                    grouped_source_windows.get("query_plan_need_window_count_selected") or 0
                ),
                "source_window_needs_considered": [
                    str(item) for item in grouped_source_windows.get("source_window_needs_considered") or []
                ],
                "source_window_needs_matched": [
                    str(item) for item in grouped_source_windows.get("source_window_needs_matched") or []
                ],
                "source_window_needs_selected": [
                    str(item) for item in grouped_source_windows.get("source_window_needs_selected") or []
                ],
                "source_window_needs_omitted": [
                    str(item) for item in grouped_source_windows.get("source_window_needs_omitted") or []
                ],
                "novelty_fill_window_count": int(grouped_source_windows.get("novelty_fill_window_count") or 0),
                "duplicate_window_skip_count": int(grouped_source_windows.get("duplicate_window_skip_count") or 0),
                "duplicate_window_selection_count": int(
                    grouped_source_windows.get("duplicate_window_selection_count") or 0
                ),
                "window_selection": _fielded_grouped_source_window_selection_diagnostics(
                    grouped_source_windows.get("window_selection")
                ),
                "selected_window_details_included": False,
                "raw_window_keys_included": False,
                "raw_paths_included": False,
                "raw_text_included": False,
                "raw_query_text_included": False,
                "raw_session_ids_included": False,
                "raw_cwd_included": False,
                "line_numbers_included": False,
                "source_lines_included": False,
            },
            "intent_window_allocation": _fielded_intent_window_allocation_diagnostics(intent_window_allocation),
            "rows_dropped_by_source_cap": source_cap_drops,
        },
        "materialization_ranking_final_caps": {
            "candidate_rows_materialized": materialized,
            "candidate_rows_generated": int(fielded_summary.get("candidate_rows_generated") or 0),
            "candidate_rows_considered": int(fielded_summary.get("candidate_rows_considered") or 0),
            "candidate_rows_retained": retained,
            "candidate_rows_surfaced": surfaced,
            "records_rejected_by_existing_ranker": int(
                fielded_summary.get("records_rejected_by_existing_ranker") or 0
            ),
            "candidate_records_deduped": int(fielded_summary.get("candidate_records_deduped") or 0),
            "candidate_records_scored": int(fielded_summary.get("candidate_records_scored") or 0),
            "materialization_failed_count": int(
                (fielded_summary.get("materialization") or {}).get("candidate_rows_materialization_failed") or 0
            ),
            "ranker_or_materialization_drop_count": ranker_or_materialization_drops,
            "final_match_cap_drop_count": final_cap_drops,
            "max_match_cap_hit": bool(final_cap_drops > 0 or "max_match" in cap_labels),
        },
        "large_history_memory_pressure_proxy": {
            "planned_byte_count": int(planned_jsonl_file_diagnostics.get("byte_count") or 0),
            "build_bytes": int(fielded_index_summary.get("build_bytes") or 0),
            "indexed_row_count": indexed_rows,
            "query_stream_raw_row_count": raw_rows,
            "evidence_unique_row_count_before_fusion": int(
                evidence_channels.get("unique_row_count_before_fusion") or 0
            ),
            "candidate_row_pool_limit": int(fielded_summary.get("candidate_row_pool_limit") or 0),
            "memory_fix_claim_supported": False,
        },
        "privacy": {
            "raw_text_included": False,
            "raw_paths_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "source_lines_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
        "claim_boundaries": {
            "ranking_or_candidate_generation_constants_changed": False,
            "memory_fix_claim_supported": False,
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "all_history_completeness_claim_supported": False,
        },
    }


def _safe_int_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count or 0) for key, count in sorted(value.items())}


def build_rrf_retrieval_diagnostics(
    *,
    fused_result: dict[str, Any],
    package_result: dict[str, Any],
    fielded_result: dict[str, Any],
    fielded_index_summary: dict[str, Any],
    package_elapsed_ms: float,
    fielded_index_elapsed_ms: float,
    fielded_elapsed_ms: float,
    fusion_elapsed_ms: float,
    enumeration_elapsed_ms: float,
    planned_jsonl_file_diagnostics: dict[str, Any],
    source_catalog: dict[str, Any],
    source_plan: dict[str, Any],
    package_searched_paths: set[str],
    fielded_searched_paths: set[str],
    warnings: list[str],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profiles = profiles if isinstance(profiles, dict) else {}
    package_count = _candidate_count(package_result)
    fielded_count = _candidate_count(fielded_result)
    fused_count = _candidate_count(fused_result)
    overlap = package_searched_paths & fielded_searched_paths
    union = package_searched_paths | fielded_searched_paths
    duplicate_file_visits = int(package_result.get("searched_files", 0)) + int(fielded_result.get("searched_files", 0)) - len(union)
    duplicate_bytes_lower_bound = min(
        int(package_result.get("searched_bytes", 0)),
        int(fielded_result.get("searched_bytes", 0)),
    ) if overlap else 0
    cap_labels = cap_labels_from_warnings(warnings)
    fielded_materialization = _fielded_materialization_diagnostics(fielded_result)
    fielded_source_grouping = _fielded_source_grouping_diagnostics(fielded_result)
    fielded_context_neighborhood = _fielded_context_neighborhood_diagnostics(fielded_result)
    fielded_ordering = _fielded_priority_ordering_diagnostics(
        package_result=package_result,
        planned_file_count=int(planned_jsonl_file_diagnostics.get("file_count") or 0),
    )
    return {
        "retrieval_mode": RETRIEVAL_MODE_RRF_FUSION,
        "feature_state": "active_explicit_opt_in",
        "strategy": "rank_only_reciprocal_rank_fusion",
        "experimental_high_recall_costlier_path": True,
        "high_recall_mode_state": {
            "explicit_opt_in_required": True,
            "default_behavior_changed": False,
            "capped": bool(cap_labels),
            "cap_labels": cap_labels,
            "costlier_than_package_lane": True,
            "fielded_bm25_storage_kind": str(
                fielded_index_summary.get("persistent_storage_kind") or "ephemeral_memory_fts5"
            ),
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
            "durable_fts_index_reused": bool(fielded_index_summary.get("durable_fts_index_reused", False)),
            "refresh_status": str(fielded_index_summary.get("refresh_status") or "unknown"),
            "reason": "rrf_fusion_runs_fielded_bm25_then_package_search_before_rank_fusion_and_uses_file_backed_bm25_when_configured",
        },
        "shared_source_enumeration": {
            **planned_jsonl_file_diagnostics,
            "elapsed_ms": enumeration_elapsed_ms,
            "profile": _phase_profile(profiles.get("shared_source_enumeration"), "shared_source_enumeration"),
            "consumed_by_lanes": list(RRF_LANE_NAMES),
        },
        "source_scope_layers": _bm25_source_scope_layers(
            planned_jsonl_file_diagnostics=planned_jsonl_file_diagnostics,
            fielded_index_summary=fielded_index_summary,
            source_plan=source_plan,
            fielded_result=fielded_result,
        ),
        "source_catalog": source_catalog,
        "source_plan": source_plan,
        "rrf_summary": {
            "rank_constant": int(fused_result.get("rank_constant") or DEFAULT_RRF_RANK_CONSTANT),
            "lane_names": list(fused_result.get("lane_names") or RRF_LANE_NAMES),
            "equal_lane_weights": True,
            "raw_score_inputs_used_for_fusion": False,
            "score_normalization_used": False,
            "fused_score_threshold_used": False,
            "candidate_window_size": fused_result.get("candidate_window_size"),
            "candidate_window_sizes": fused_result.get("candidate_window_sizes", {}),
            "candidate_count": fused_count,
            "overlap_count": fused_result.get("overlap_count", 0),
            "jaccard": fused_result.get("jaccard", 0.0),
            "package_only_count": fused_result.get("package_only_count", 0),
            "fielded_only_count": fused_result.get("fielded_only_count", 0),
            "top_n_fielded_only_promotion_count": fused_result.get("top_n_fielded_only_promotion_count", 0),
        },
        "lanes": [
            {
                "name": RRF_LANE_NAMES[0],
                "candidate_count": package_count,
                "elapsed_ms": package_elapsed_ms,
                "elapsed_label": elapsed_label(package_elapsed_ms),
                "profile": _phase_profile(profiles.get("package_lane"), "package_lane"),
                "searched_files": int(package_result.get("searched_files", 0)),
                "searched_bytes": int(package_result.get("searched_bytes", 0)),
                "planned_file_source": "shared_source_enumeration",
                "cap_labels": cap_labels_from_warnings(package_result.get("warnings", []) or []),
            },
            {
                "name": RRF_LANE_NAMES[1],
                "candidate_count": fielded_count,
                "elapsed_ms": fielded_elapsed_ms,
                "elapsed_label": elapsed_label(fielded_elapsed_ms),
                "profile": _phase_profile(
                    profiles.get("fielded_bm25_search_materialization"),
                    "fielded_bm25_search_materialization",
                ),
                "index_build_elapsed_ms": fielded_index_elapsed_ms,
                "index_build_elapsed_label": elapsed_label(fielded_index_elapsed_ms),
                "index_build_profile": _phase_profile(
                    profiles.get("fielded_bm25_index_build"),
                    "fielded_bm25_index_build",
                ),
                "candidate_rows": int(fielded_result.get("candidate_rows_generated", 0) or 0),
                "candidate_row_limit": int(fielded_result.get("candidate_row_limit", 0) or 0),
                "retrieval_budget": _fielded_retrieval_budget_diagnostics(fielded_result),
                "query_streams": _fielded_query_streams_diagnostics(fielded_result),
                "context_neighborhood": fielded_context_neighborhood,
                "searched_files": int(fielded_result.get("searched_files", 0)),
                "searched_bytes": int(fielded_result.get("searched_bytes", 0)),
                "materialization": fielded_materialization,
                "source_grouping": fielded_source_grouping,
                "planned_file_source": "shared_source_enumeration",
                "cap_labels": cap_labels_from_warnings(fielded_result.get("warnings", []) or []),
                "index_summary": fielded_index_summary,
            },
        ],
        "lane_file_overlap": {
            "raw_paths_included": False,
            "package_opened_file_count": len(package_searched_paths),
            "fielded_opened_file_count": len(fielded_searched_paths),
            "overlap_file_count": len(overlap),
            "union_file_count": len(union),
            "package_only_file_count": len(package_searched_paths - fielded_searched_paths),
            "fielded_only_file_count": len(fielded_searched_paths - package_searched_paths),
            "planned_file_count": int(planned_jsonl_file_diagnostics.get("file_count") or 0),
        },
        "fielded_informed_package_ordering": fielded_ordering,
        "fielded_source_grouping": fielded_source_grouping,
        "fielded_context_neighborhood": fielded_context_neighborhood,
        "duplicate_local_work": {
            "raw_paths_included": False,
            "enumeration_reused": True,
            "avoided_second_enumeration": True,
            "duplicate_file_visit_count": duplicate_file_visits,
            "duplicate_file_overlap_count": len(overlap),
            "duplicate_searched_bytes_lower_bound": duplicate_bytes_lower_bound,
            "fielded_bm25_materialization": fielded_materialization,
            "fielded_bm25_storage_kind": str(
                fielded_index_summary.get("persistent_storage_kind") or "ephemeral_memory_fts5"
            ),
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
            "durable_fts_index_reused": bool(fielded_index_summary.get("durable_fts_index_reused", False)),
            "local_work_only_not_model_tokens": True,
        },
        "fusion": {
            "elapsed_ms": fusion_elapsed_ms,
            "elapsed_label": elapsed_label(fusion_elapsed_ms),
            "profile": _phase_profile(profiles.get("rrf_fusion"), "rrf_fusion"),
            "candidate_count": fused_count,
            "cap_labels": cap_labels_from_warnings(warnings),
        },
        "total_explicit_rrf_retrieval": _phase_profile(
            profiles.get("total_explicit_rrf_retrieval"),
            "total_explicit_rrf_retrieval",
        ),
        "cap_labels": cap_labels,
        "claim_boundaries": {
            "default_behavior_changed": False,
            "default_promotion_supported": False,
            "token_savings_claim_supported": False,
            "final_answer_quality_claim_supported": False,
            "broad_speedup_claim_supported": False,
            "all_history_completeness_claim_supported": False,
            "raw_score_blending_used": False,
            "score_normalization_used": False,
            "tuned_lane_weights_used": False,
            "fused_score_threshold_used": False,
            "scanner_scheduling_constants_changed": False,
            "package_lane_scoped_or_skipped": False,
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
        },
}


def _fielded_retrieval_budget_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    budget = fielded_result.get("retrieval_budget") if isinstance(fielded_result, dict) else {}
    if not isinstance(budget, dict):
        budget = {}
    return {
        "schema_version": int(budget.get("schema_version") or 1),
        "enforced": bool(budget.get("enforced", False)),
        "candidate_row_limit": int(budget.get("candidate_row_limit") or 0),
        "candidate_row_limit_uncapped": int(budget.get("candidate_row_limit_uncapped") or 0),
        "candidate_row_pool_limit": int(budget.get("candidate_row_pool_limit") or 0),
        "candidate_row_pool_limit_uncapped": int(budget.get("candidate_row_pool_limit_uncapped") or 0),
        "max_candidate_rows": int(budget.get("max_candidate_rows") or 0),
        "candidate_row_pool_cap_hit": bool(budget.get("candidate_row_pool_cap_hit", False)),
        "timeout_seconds": float(budget.get("timeout_seconds") or 0.0),
        "timeout_source": str(budget.get("timeout_source") or "unknown"),
        "timed_out": bool(budget.get("timed_out", False)),
        "timeout_reason": budget.get("timeout_reason"),
        "partial_results": bool(budget.get("partial_results", False)),
        "fail_closed_on_timeout": bool(budget.get("fail_closed_on_timeout", False)),
        "deadline_monotonic_included": False,
        "raw_query_text_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def _bm25_source_scope_layers(
    *,
    planned_jsonl_file_diagnostics: dict[str, Any],
    fielded_index_summary: dict[str, Any],
    source_plan: dict[str, Any],
    fielded_result: dict[str, Any],
) -> dict[str, Any]:
    scope = fielded_result.get("fielded_scope") if isinstance(fielded_result.get("fielded_scope"), dict) else {}
    return {
        "schema_version": 1,
        "semantics": "explicit_roots_define_intended_bounded_scope_indexed_scope_is_bm25_index_coverage_query_selected_scope_is_planning_layer",
        "intended_bounded_scope": {
            "basis": "shared_source_enumeration_after_root_safety_and_hard_caps",
            "file_count": int(planned_jsonl_file_diagnostics.get("file_count") or 0),
            "byte_count": int(planned_jsonl_file_diagnostics.get("byte_count") or 0),
            "cap_labels": [str(item) for item in planned_jsonl_file_diagnostics.get("cap_labels") or []],
            "cap_hits": dict(planned_jsonl_file_diagnostics.get("cap_hits") or {}),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "indexed_scope": {
            "basis": "fielded_bm25_sqlite_fts_index_loaded_for_this_command",
            "file_count": int(fielded_index_summary.get("indexed_file_count") or 0),
            "record_count": int(fielded_index_summary.get("indexed_record_count") or 0),
            "chunk_count": int(fielded_index_summary.get("indexed_chunk_count") or 0),
            "byte_count": int(fielded_index_summary.get("build_bytes") or 0),
            "scope_row_count_after_metadata_filters": int(scope.get("scope_row_count") or 0),
            "source_file_scope_count_after_metadata_filters": int(scope.get("source_file_scope_count") or 0),
            "date_window_filter_present": bool(scope.get("date_window_filter_present", False)),
            "persistent_index_or_cache_used": bool(fielded_index_summary.get("persistent_index_enabled", False)),
            "durable_fts_index_reused": bool(fielded_index_summary.get("durable_fts_index_reused", False)),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "query_selected_scope": {
            "basis": "command_local_source_plan_query_layer_not_the_meaning_of_explicit_root_scope",
            "file_count": int(source_plan.get("sources_selected") or 0),
            "sources_total": int(source_plan.get("sources_total") or 0),
            "sources_pruned_hard": int(source_plan.get("sources_pruned_hard") or 0),
            "sources_deferred_budget": int(source_plan.get("sources_deferred_budget") or 0),
            "byte_count_selected": int(source_plan.get("byte_count_selected") or 0),
            "byte_count_total": int(source_plan.get("byte_count_total") or 0),
            "exhaustive_available": bool(source_plan.get("exhaustive_available", False)),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "opened_result_scope": {
            "basis": "candidate_materialization_opened_files",
            "file_count": int(fielded_result.get("searched_files") or 0),
            "byte_count": int(fielded_result.get("searched_bytes") or 0),
            "record_count": int(fielded_result.get("records_considered") or 0),
            "raw_paths_included": False,
            "raw_text_included": False,
        },
        "legacy_count_warning": "Do not compare one ambiguous source_count across modes; compare intended_bounded_scope, indexed_scope, and query_selected_scope separately.",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
    }


def _fielded_source_grouping_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    grouping = fielded_result.get("fielded_source_grouping") if isinstance(fielded_result, dict) else {}
    if not isinstance(grouping, dict):
        grouping = {}
    return {
        "enabled": bool(grouping.get("enabled", False)),
        "strategy": str(grouping.get("strategy") or "disabled"),
        "candidate_rows_considered": int(grouping.get("candidate_rows_considered") or 0),
        "candidate_rows_selected": int(grouping.get("candidate_rows_selected") or 0),
        "candidate_rows_dropped": int(grouping.get("candidate_rows_dropped") or 0),
        "candidate_row_limit": int(grouping.get("candidate_row_limit") or 0),
        "candidate_row_pool_limit": int(grouping.get("candidate_row_pool_limit") or 0),
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
        "window_group_count_considered": int(grouping.get("window_group_count_considered") or 0),
        "window_group_count_selected": int(grouping.get("window_group_count_selected") or 0),
        "window_group_count_omitted": int(grouping.get("window_group_count_omitted") or 0),
        "category_group_count_considered": int(grouping.get("category_group_count_considered") or 0),
        "category_group_count_selected": int(grouping.get("category_group_count_selected") or 0),
        "category_group_count_omitted": int(grouping.get("category_group_count_omitted") or 0),
        "dominant_source_share_before": float(grouping.get("dominant_source_share_before") or 0.0),
        "dominant_source_share_after": float(grouping.get("dominant_source_share_after") or 0.0),
        "source_head_rows_selected": int(grouping.get("source_head_rows_selected") or 0),
        "stream_head_rows_selected": int(grouping.get("stream_head_rows_selected") or 0),
        "global_tail_rows_selected": int(grouping.get("global_tail_rows_selected") or 0),
        "group_interleaved_rows_selected": int(grouping.get("group_interleaved_rows_selected") or 0),
        "representative_reason_counts": _safe_int_counts(grouping.get("representative_reason_counts")),
        "selected_representative_reason_counts": _safe_int_counts(
            grouping.get("selected_representative_reason_counts")
        ),
        "representative_reason_categories": [
            str(category) for category in grouping.get("representative_reason_categories") or []
        ],
        "group_key_categories": [str(category) for category in grouping.get("group_key_categories") or []],
        "raw_group_keys_included": False,
        "adjacent_run_collapse": _fielded_adjacent_run_collapse_diagnostics(grouping.get("adjacent_run_collapse")),
        "group_interleaving": _fielded_group_interleaving_diagnostics(grouping.get("group_interleaving")),
        "grouped_source_windows": _fielded_grouped_source_windows_diagnostics(
            grouping.get("grouped_source_windows")
        ),
        "intent_window_allocation": _fielded_intent_window_allocation_diagnostics(
            grouping.get("intent_window_allocation")
        ),
        "selection_policy": _fielded_selection_policy_diagnostics(grouping.get("selection_policy")),
        "rows_dropped_by_source_cap": int(grouping.get("rows_dropped_by_source_cap") or 0),
        "stream_selection": _fielded_stream_selection_diagnostics(grouping.get("stream_selection")),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_intent_window_allocation_diagnostics(value: Any) -> dict[str, Any]:
    allocation = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(allocation.get("enabled", False)),
        "strategy": str(allocation.get("strategy") or "disabled"),
        "primary_intent": str(allocation.get("primary_intent") or "generic_history"),
        "source_window_needs": [str(item) for item in allocation.get("source_window_needs") or []],
        "source_window_needs_considered": [
            str(item) for item in allocation.get("source_window_needs_considered") or []
        ],
        "source_window_needs_matched": [str(item) for item in allocation.get("source_window_needs_matched") or []],
        "source_window_needs_selected": [str(item) for item in allocation.get("source_window_needs_selected") or []],
        "source_window_needs_omitted": [str(item) for item in allocation.get("source_window_needs_omitted") or []],
        "query_plan_need_window_count_considered": int(
            allocation.get("query_plan_need_window_count_considered") or 0
        ),
        "query_plan_need_window_count_matched": int(allocation.get("query_plan_need_window_count_matched") or 0),
        "query_plan_need_window_count_selected": int(allocation.get("query_plan_need_window_count_selected") or 0),
        "intent_window_count_considered": int(allocation.get("intent_window_count_considered") or 0),
        "selected_intent_window_count": int(allocation.get("selected_intent_window_count") or 0),
        "selected_route_window_count": int(allocation.get("selected_route_window_count") or 0),
        "selected_intent_window_head_count": int(allocation.get("selected_intent_window_head_count") or 0),
        "distinct_intent_sources_selected": int(allocation.get("distinct_intent_sources_selected") or 0),
        "novelty_fill_window_count": int(allocation.get("novelty_fill_window_count") or 0),
        "duplicate_window_skip_count": int(allocation.get("duplicate_window_skip_count") or 0),
        "duplicate_window_selection_count": int(allocation.get("duplicate_window_selection_count") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_selection_policy_diagnostics(value: Any) -> dict[str, Any]:
    policy = value if isinstance(value, dict) else {}
    return {
        "selection_policy_version": str(policy.get("selection_policy_version") or ""),
        "selection_strategy": str(policy.get("selection_strategy") or "disabled"),
        "primary_intent": str(policy.get("primary_intent") or "generic_history"),
        "top_exact_rows_selected": int(policy.get("top_exact_rows_selected") or 0),
        "child_channel_rows_selected": int(policy.get("child_channel_rows_selected") or 0),
        "metadata_match_rows_selected": int(policy.get("metadata_match_rows_selected") or 0),
        "nearby_context_rows_selected": int(policy.get("nearby_context_rows_selected") or 0),
        "diversity_fill_rows_selected": int(policy.get("diversity_fill_rows_selected") or 0),
        "duplicate_or_collapsed_row_count": int(policy.get("duplicate_or_collapsed_row_count") or 0),
        "role_group_count_considered": int(policy.get("role_group_count_considered") or 0),
        "role_group_count_selected": int(policy.get("role_group_count_selected") or 0),
        "role_group_count_omitted": int(policy.get("role_group_count_omitted") or 0),
        "omission_reason_counts": _safe_int_counts(policy.get("omission_reason_counts")),
        "selected_channel_labels": [str(label) for label in policy.get("selected_channel_labels") or []],
        "omitted_channel_labels": [str(label) for label in policy.get("omitted_channel_labels") or []],
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
        "index_payloads_included": False,
    }


def _fielded_context_neighborhood_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    neighborhood = fielded_result.get("fielded_context_neighborhood") if isinstance(fielded_result, dict) else {}
    if not isinstance(neighborhood, dict):
        neighborhood = {}
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


def _fielded_evidence_channel_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    channels = fielded_result.get("fielded_evidence_channels") if isinstance(fielded_result, dict) else {}
    if not isinstance(channels, dict):
        channels = {}
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
        "channel_row_counts": _safe_int_counts(channels.get("channel_row_counts")),
        "unique_row_count_before_fusion": int(channels.get("unique_row_count_before_fusion") or 0),
        "selected_row_count_after_fusion": int(channels.get("selected_row_count_after_fusion") or 0),
        "dropped_row_count_after_fusion": int(channels.get("dropped_row_count_after_fusion") or 0),
        "selected_rows_by_channel_after_fusion": _safe_int_counts(
            channels.get("selected_rows_by_channel_after_fusion")
        ),
        "dropped_rows_by_channel_after_fusion": _safe_int_counts(channels.get("dropped_rows_by_channel_after_fusion")),
        "selected_rows_by_channel_after_grouping": _safe_int_counts(
            channels.get("selected_rows_by_channel_after_grouping")
        ),
        "dropped_rows_by_channel_after_grouping": _safe_int_counts(
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


def _safe_int_counts(value: Any) -> dict[str, int]:
    counts = value if isinstance(value, dict) else {}
    return {str(key): int(count or 0) for key, count in sorted(counts.items())}


def _fielded_adjacent_run_collapse_diagnostics(value: Any) -> dict[str, Any]:
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


def _fielded_group_interleaving_diagnostics(value: Any) -> dict[str, Any]:
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


def _fielded_grouped_source_windows_diagnostics(value: Any) -> dict[str, Any]:
    windows = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(windows.get("enabled", False)),
        "selection_version": str(windows.get("selection_version") or ""),
        "strategy": str(windows.get("strategy") or "disabled"),
        "window_identity_components": [str(item) for item in windows.get("window_identity_components") or []],
        "near_contiguous_source_order_gap": int(windows.get("near_contiguous_source_order_gap") or 0),
        "inner_hit_limit_per_window": int(windows.get("inner_hit_limit_per_window") or 0),
        "window_count_considered": int(windows.get("window_count_considered") or 0),
        "window_count_selected": int(windows.get("window_count_selected") or 0),
        "window_count_omitted": int(windows.get("window_count_omitted") or 0),
        "selected_window_head_count": int(windows.get("selected_window_head_count") or 0),
        "selected_inner_hit_count": int(windows.get("selected_inner_hit_count") or 0),
        "omitted_inner_hit_count": int(windows.get("omitted_inner_hit_count") or 0),
        "max_inner_hit_count": int(windows.get("max_inner_hit_count") or 0),
        "merged_adjacent_window_count": int(windows.get("merged_adjacent_window_count") or 0),
        "merged_adjacent_inner_hit_count": int(windows.get("merged_adjacent_inner_hit_count") or 0),
        "distinct_sources_considered": int(windows.get("distinct_sources_considered") or 0),
        "distinct_sessions_considered": int(windows.get("distinct_sessions_considered") or 0),
        "distinct_days_considered": int(windows.get("distinct_days_considered") or 0),
        "distinct_sources_selected": int(windows.get("distinct_sources_selected") or 0),
        "distinct_sessions_selected": int(windows.get("distinct_sessions_selected") or 0),
        "distinct_days_selected": int(windows.get("distinct_days_selected") or 0),
        "distinct_windows_selected": int(windows.get("distinct_windows_selected") or 0),
        "omission_reason_counts": _safe_int_counts(windows.get("omission_reason_counts")),
        "route_window_count_considered": int(windows.get("route_window_count_considered") or 0),
        "route_window_count_selected": int(windows.get("route_window_count_selected") or 0),
        "route_window_head_selection_count": int(windows.get("route_window_head_selection_count") or 0),
        "query_plan_need_window_count_considered": int(windows.get("query_plan_need_window_count_considered") or 0),
        "query_plan_need_window_count_matched": int(windows.get("query_plan_need_window_count_matched") or 0),
        "query_plan_need_window_count_selected": int(windows.get("query_plan_need_window_count_selected") or 0),
        "source_window_needs_considered": [str(item) for item in windows.get("source_window_needs_considered") or []],
        "source_window_needs_matched": [str(item) for item in windows.get("source_window_needs_matched") or []],
        "source_window_needs_selected": [str(item) for item in windows.get("source_window_needs_selected") or []],
        "source_window_needs_omitted": [str(item) for item in windows.get("source_window_needs_omitted") or []],
        "novelty_fill_window_count": int(windows.get("novelty_fill_window_count") or 0),
        "duplicate_window_skip_count": int(windows.get("duplicate_window_skip_count") or 0),
        "duplicate_window_selection_count": int(windows.get("duplicate_window_selection_count") or 0),
        "window_selection": _fielded_grouped_source_window_selection_diagnostics(
            windows.get("window_selection")
        ),
        "selected_window_details_included": False,
        "raw_window_keys_included": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_grouped_source_window_selection_diagnostics(value: Any) -> dict[str, Any]:
    selection = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(selection.get("enabled", False)),
        "strategy": str(selection.get("strategy") or "disabled"),
        "route_window_count_considered": int(selection.get("route_window_count_considered") or 0),
        "route_window_count_selected": int(selection.get("route_window_count_selected") or 0),
        "route_window_head_selection_count": int(selection.get("route_window_head_selection_count") or 0),
        "query_plan_need_window_count_considered": int(
            selection.get("query_plan_need_window_count_considered") or 0
        ),
        "query_plan_need_window_count_matched": int(selection.get("query_plan_need_window_count_matched") or 0),
        "query_plan_need_window_count_selected": int(selection.get("query_plan_need_window_count_selected") or 0),
        "source_window_needs_considered": [
            str(item) for item in selection.get("source_window_needs_considered") or []
        ],
        "source_window_needs_matched": [str(item) for item in selection.get("source_window_needs_matched") or []],
        "source_window_needs_selected": [str(item) for item in selection.get("source_window_needs_selected") or []],
        "source_window_needs_omitted": [str(item) for item in selection.get("source_window_needs_omitted") or []],
        "novelty_fill_window_count": int(selection.get("novelty_fill_window_count") or 0),
        "duplicate_window_skip_count": int(selection.get("duplicate_window_skip_count") or 0),
        "duplicate_window_selection_count": int(selection.get("duplicate_window_selection_count") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_query_streams_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    streams = fielded_result.get("fielded_query_streams") if isinstance(fielded_result, dict) else {}
    if not isinstance(streams, dict):
        streams = {}
    stream_items: list[dict[str, Any]] = []
    for item in streams.get("streams") or []:
        if not isinstance(item, dict):
            continue
        stream_items.append(
            {
                "label": str(item.get("label") or "unknown"),
                "channel": str(item.get("channel") or item.get("label") or "unknown"),
                "intent": str(item.get("intent") or "generic_history"),
                "kind": str(item.get("kind") or "base"),
                "child": bool(item.get("child", False)),
                "reason_codes": [str(reason) for reason in item.get("reason_codes") or []],
                "skip_reason_code": item.get("skip_reason_code")
                if item.get("skip_reason_code") is None
                else str(item.get("skip_reason_code")),
                "fields": [str(field) for field in item.get("fields") or []],
                "status": str(item.get("status") or "unknown"),
                "row_count": int(item.get("row_count") or 0),
                "deduped_row_count": int(item.get("deduped_row_count") or 0),
                "stream_limit": int(item.get("stream_limit") or 0),
                "hit_limit": bool(item.get("hit_limit", False)),
                "error_type": item.get("error_type") if item.get("error_type") is None else str(item.get("error_type")),
                "scope_type": str(item.get("scope_type") or "active_selected_source_scope"),
                "scope_row_count": int(item.get("scope_row_count") or 0),
                "scope_hit_count": int(item.get("scope_hit_count") or 0),
                "scope_enforced": bool(item.get("scope_enforced", True)),
                "raw_query_text_included": False,
                "raw_query_terms_included": False,
                "raw_paths_included": False,
                "raw_text_included": False,
                "raw_session_ids_included": False,
                "raw_cwd_included": False,
                "source_lines_included": False,
                "line_numbers_included": False,
            }
        )
    return {
        "enabled": bool(streams.get("enabled", False)),
        "strategy": str(streams.get("strategy") or "disabled"),
        "query_plan_mode": str(streams.get("query_plan_mode") or QUERY_PLAN_MODE_AUTO),
        "stream_count": int(streams.get("stream_count") or len(stream_items)),
        "streams": stream_items,
        "child_stream_count": int(streams.get("child_stream_count") or 0),
        "child_stream_labels": [str(label) for label in streams.get("child_stream_labels") or []],
        "candidate_row_pool_limit_per_stream": int(streams.get("candidate_row_pool_limit_per_stream") or 0),
        "per_stream_limit": int(streams.get("per_stream_limit") or 0),
        "stream_hit_limit_count": int(streams.get("stream_hit_limit_count") or 0),
        "saturated_stream_count": int(streams.get("saturated_stream_count") or 0),
        "saturated_stream_labels": [str(label) for label in streams.get("saturated_stream_labels") or []],
        "row_count_before_dedupe": int(streams.get("row_count_before_dedupe") or 0),
        "row_count_after_dedupe": int(streams.get("row_count_after_dedupe") or 0),
        "deduped_row_count_from_streams": int(streams.get("deduped_row_count_from_streams") or 0),
        "duplicate_row_count": int(streams.get("duplicate_row_count") or 0),
        "selected_rows_by_stream": _safe_int_dict(streams.get("selected_rows_by_stream")),
        "rows_dropped_by_stream_cap": _safe_int_dict(streams.get("rows_dropped_by_stream_cap")),
        "rows_dropped_by_stream_cap_total": int(streams.get("rows_dropped_by_stream_cap_total") or 0),
        "scope_type": str(streams.get("scope_type") or "active_selected_source_scope"),
        "scope_enforced": bool(streams.get("scope_enforced", True)),
        "scope_row_count": int(streams.get("scope_row_count") or 0),
        "scope_hit_count_total": int(streams.get("scope_hit_count_total") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _fielded_scope_diagnostics(value: Any) -> dict[str, Any]:
    scope = value if isinstance(value, dict) else {}
    return {
        "schema_version": int(scope.get("schema_version") or 1),
        "scope_type": str(scope.get("scope_type") or "none"),
        "scope_enforced": bool(scope.get("scope_enforced", False)),
        "scope_row_count": int(scope.get("scope_row_count") or 0),
        "indexed_row_count_loaded_for_scope": int(scope.get("indexed_row_count_loaded_for_scope") or 0),
        "source_file_scope_count": int(scope.get("source_file_scope_count") or 0),
        "source_kind_counts": _safe_int_dict(scope.get("source_kind_counts")),
        "session_scope_count": int(scope.get("session_scope_count") or 0),
        "day_scope_count": int(scope.get("day_scope_count") or 0),
        "date_window_filter_present": bool(scope.get("date_window_filter_present", False)),
        "date_window_unknown_day_policy": str(
            scope.get("date_window_unknown_day_policy")
            or "include_unknown_day_exclude_known_outside_window"
        ),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
    }


def _fielded_branch_fanout_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    fanout = fielded_result.get("fielded_branch_fanout") if isinstance(fielded_result, dict) else {}
    if not isinstance(fanout, dict):
        fanout = {}
    return {
        "branches_considered": int(fanout.get("branches_considered") or 0),
        "branches_emitted": int(fanout.get("branches_emitted") or 0),
        "branches_capped": int(fanout.get("branches_capped") or 0),
        "rows_by_branch": _safe_int_dict(fanout.get("rows_by_branch")),
        "capped_rows_by_branch": _safe_int_dict(fanout.get("capped_rows_by_branch")),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
    }


def _fielded_child_query_plan_diagnostics(fielded_result: dict[str, Any]) -> dict[str, Any]:
    plan = fielded_result.get("fielded_child_query_plan") if isinstance(fielded_result, dict) else {}
    if not isinstance(plan, dict):
        plan = {}
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
        "query_plan_mode": str(plan.get("query_plan_mode") or QUERY_PLAN_MODE_AUTO),
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


def _fielded_stream_selection_diagnostics(value: Any) -> dict[str, Any]:
    selection = value if isinstance(value, dict) else {}
    return {
        "enabled": bool(selection.get("enabled", False)),
        "selected_rows_by_stream": _safe_int_dict(selection.get("selected_rows_by_stream")),
        "rows_dropped_by_stream_cap": _safe_int_dict(selection.get("rows_dropped_by_stream_cap")),
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


def _safe_int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(count or 0) for key, count in sorted(value.items())}


def _fielded_materialization_diagnostics(fielded_result: dict[str, Any]) -> dict[str, int | bool]:
    materialization = fielded_result.get("materialization") if isinstance(fielded_result, dict) else {}
    if not isinstance(materialization, dict):
        materialization = {}
    return {
        "candidate_rows_materialized": int(materialization.get("candidate_rows_materialized") or 0),
        "candidate_rows_materialized_from_in_memory_raw": int(
            materialization.get("candidate_rows_materialized_from_in_memory_raw") or 0
        ),
        "candidate_rows_materialized_from_cached_record": int(
            materialization.get("candidate_rows_materialized_from_cached_record") or 0
        ),
        "candidate_rows_materialized_from_file_reopen": int(
            materialization.get("candidate_rows_materialized_from_file_reopen") or 0
        ),
        "candidate_rows_materialization_failed": int(
            materialization.get("candidate_rows_materialization_failed") or 0
        ),
        "records_materialized_from_in_memory_raw": int(
            materialization.get("records_materialized_from_in_memory_raw") or 0
        ),
        "records_materialized_from_cached_record": int(
            materialization.get("records_materialized_from_cached_record") or 0
        ),
        "records_materialized_from_file_reopen": int(
            materialization.get("records_materialized_from_file_reopen") or 0
        ),
        "file_reopen_attempts": int(materialization.get("file_reopen_attempts") or 0),
        "file_reopen_failures": int(materialization.get("file_reopen_failures") or 0),
        "in_memory_raw_materialization_available": bool(
            materialization.get("in_memory_raw_materialization_available", False)
        ),
        "raw_text_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _fielded_priority_ordering_diagnostics(
    *,
    package_result: dict[str, Any],
    planned_file_count: int,
) -> dict[str, Any]:
    ordering = package_result.get("fielded_priority_scan_order") if isinstance(package_result, dict) else {}
    if not isinstance(ordering, dict):
        ordering = {}
    opened_prioritized = [
        {
            "file_label": str(item.get("file_label") or ""),
            "opened_order": int(item.get("opened_order") or 0),
        }
        for item in (ordering.get("opened_prioritized_files") or [])
        if isinstance(item, dict) and item.get("file_label")
    ]
    return {
        "enabled": bool(ordering.get("enabled", False)),
        "status": str(ordering.get("status") or "disabled"),
        "priority_source": str(ordering.get("priority_source") or "fielded_bm25_candidate_or_opened_files"),
        "planned_file_count": planned_file_count,
        "package_planned_file_count": int(ordering.get("planned_file_count") or 0),
        "prioritized_file_count": int(ordering.get("prioritized_file_count") or 0),
        "non_prioritized_file_count": int(ordering.get("non_prioritized_file_count") or 0),
        "package_opened_prioritized_file_count": int(ordering.get("opened_prioritized_file_count") or 0),
        "package_opened_prioritized_files": opened_prioritized,
        "prioritized_file_labels": list(ordering.get("prioritized_file_labels") or []),
        "raw_paths_included": False,
        "raw_text_included": False,
        "opened_order_labels_include_raw_paths": False,
        "all_planned_package_files_remain_queued": True,
        "complete_planned_file_set_preserved": bool(ordering.get("all_planned_files_preserved", True)),
        "fielded_priority_excludes_non_prioritized_files": False,
        "no_exclusion_semantics": True,
        "package_lane_scoped": False,
        "package_lane_skipped": False,
    }


def _phase_profile(value: Any, phase: str) -> dict[str, Any]:
    profile = value if isinstance(value, dict) else {}
    rss_available = bool(profile.get("rss_available", False))
    return {
        "phase": str(profile.get("phase") or phase),
        "elapsed_ms": float(profile.get("elapsed_ms") or 0.0),
        "rss_available": rss_available,
        "rss_units": "bytes",
        "rss_start_max_bytes": _optional_int(profile.get("rss_start_max_bytes")),
        "rss_end_max_bytes": _optional_int(profile.get("rss_end_max_bytes")),
        "rss_max_bytes": _optional_int(profile.get("rss_max_bytes")),
        "rss_delta_bytes": _optional_int(profile.get("rss_delta_bytes")),
        "rss_unavailable_reason": None if rss_available else str(profile.get("rss_unavailable_reason") or "not_profiled"),
        "rss_current_bytes": None,
        "rss_current_unavailable_reason": "resource_ru_maxrss_reports_process_peak_not_current",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cap_labels_from_warnings(warnings: list[str]) -> list[str]:
    labels: set[str] = set()
    for warning in warnings:
        lowered = str(warning).lower()
        if "max elapsed cap" in lowered:
            labels.add("max_elapsed")
        if "max match cap" in lowered or "retention-capped" in lowered:
            labels.add("max_match")
        if "max file cap" in lowered:
            labels.add("max_files")
        if "max byte cap" in lowered:
            labels.add("max_bytes")
        if "reservoir-capped" in lowered:
            labels.add("reservoir")
        if "bm25 candidate row" in lowered:
            labels.add("bm25_candidate_rows")
        if "bm25 query/materialization timeout" in lowered:
            labels.add("bm25_timeout")
    return sorted(labels)


def elapsed_label(elapsed_ms: float) -> str:
    if elapsed_ms >= 10_000:
        return "10s_plus"
    if elapsed_ms >= 1_000:
        return "1s_to_10s"
    if elapsed_ms >= 100:
        return "100ms_to_1s"
    return "under_100ms"


def _candidate_count(result: dict[str, Any]) -> int:
    return sum(
        len(group.get("matches", []))
        for group in result.get("groups", [])
        if isinstance(group, dict)
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
