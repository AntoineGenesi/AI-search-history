from __future__ import annotations

import time
from typing import Any

from .fielded_bm25 import build_fielded_bm25_index, search_with_fielded_bm25
from .profiling import finish_phase_profile, start_phase_profile
from .rrf_fusion import DEFAULT_RRF_RANK_CONSTANT, RrfLane, fuse_ranked_groups
from .scanner import search
from .sources import build_command_local_source_catalog, materialize_planned_jsonl_files


RETRIEVAL_MODE_PACKAGE = "package"
RETRIEVAL_MODE_RRF_FUSION = "rrf-fusion"
RETRIEVAL_MODE_FIELDED_BM25 = "fielded-bm25"
RETRIEVAL_MODES = (RETRIEVAL_MODE_PACKAGE, RETRIEVAL_MODE_RRF_FUSION, RETRIEVAL_MODE_FIELDED_BM25)
RRF_LANE_NAMES = ("package_search", "package_search_fielded_bm25")


def run_retrieval_mode(
    *,
    retrieval_mode: str,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
    use_rg_prefilter: bool = False,
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
        )
    if retrieval_mode == RETRIEVAL_MODE_FIELDED_BM25:
        return run_fielded_bm25_retrieval(
            plan=plan,
            intent=intent,
            default_tz=default_tz,
            cwd=cwd,
            mask_context=mask_context,
        )
    raise ValueError(f"unsupported retrieval mode: {retrieval_mode}")


def run_fielded_bm25_retrieval(
    *,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
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

    fielded_index_profile_token = start_phase_profile("fielded_bm25_index_build")
    fielded_index = build_fielded_bm25_index(plan, default_tz=default_tz, planned_jsonl_files=planned_jsonl_files)
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
    fielded_result.pop("_searched_file_paths", None)
    warnings = sorted(set(fielded_result.get("warnings", []) or []))
    total_profile = finish_phase_profile(
        total_profile_token,
        candidate_count=fielded_count,
        planned_file_count=planned_jsonl_files.file_count,
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

    fielded_index_profile_token = start_phase_profile("fielded_bm25_index_build")
    fielded_index = build_fielded_bm25_index(plan, default_tz=default_tz, planned_jsonl_files=planned_jsonl_files)
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
    warnings: list[str],
    profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profiles = profiles if isinstance(profiles, dict) else {}
    cap_labels = cap_labels_from_warnings(warnings)
    fielded_materialization = _fielded_materialization_diagnostics(fielded_result)
    fielded_source_grouping = _fielded_source_grouping_diagnostics(fielded_result)
    fielded_summary = {
        "status": fielded_result.get("status"),
        "fallback_reason": fielded_result.get("fallback_reason"),
        "candidate_count": _candidate_count(fielded_result),
        "candidate_rows": int(fielded_result.get("candidate_rows", 0) or 0),
        "candidate_row_limit": int(fielded_result.get("candidate_row_limit", 0) or 0),
        "candidate_row_pool_limit": int(fielded_result.get("candidate_row_pool_limit", 0) or 0),
        "candidate_rows_generated": int(fielded_result.get("candidate_rows_generated", 0) or 0),
        "candidate_rows_considered": int(fielded_result.get("candidate_rows_considered", 0) or 0),
        "candidate_rows_retained": int(fielded_result.get("candidate_rows_retained", 0) or 0),
        "candidate_rows_surfaced": int(fielded_result.get("candidate_rows_surfaced", 0) or 0),
        "query_streams": _fielded_query_streams_diagnostics(fielded_result),
        "searched_files": int(fielded_result.get("searched_files", 0)),
        "searched_bytes": int(fielded_result.get("searched_bytes", 0)),
        "records_considered": int(fielded_result.get("records_considered", 0)),
        "records_rejected_by_existing_ranker": int(fielded_result.get("records_rejected_by_existing_ranker", 0) or 0),
        "candidate_records_scored": int(fielded_result.get("candidate_records_scored", 0) or 0),
        "candidate_records_deduped": int(fielded_result.get("candidate_records_deduped", 0) or 0),
        "bm25_rank_buckets": dict(fielded_result.get("bm25_rank_buckets") or {}),
        "materialization": fielded_materialization,
        "index_summary": fielded_index_summary,
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
    return {
        "retrieval_mode": RETRIEVAL_MODE_FIELDED_BM25,
        "feature_state": "active_explicit_opt_in",
        "strategy": "direct_fielded_bm25_record_retrieval",
        "fielded_mode_state": {
            "explicit_opt_in_required": True,
            "default_behavior_changed": False,
            "capped": bool(cap_labels),
            "cap_labels": cap_labels,
            "persistent_index_or_cache_used": False,
            "reason": "fielded_bm25_builds_in_memory_sqlite_fts5_index_from_planned_jsonl_sources",
        },
        "shared_source_enumeration": {
            **planned_jsonl_file_diagnostics,
            "elapsed_ms": enumeration_elapsed_ms,
            "profile": _phase_profile(profiles.get("shared_source_enumeration"), "shared_source_enumeration"),
            "consumed_by_lanes": [RETRIEVAL_MODE_FIELDED_BM25],
        },
        "source_catalog": source_catalog,
        "fielded_bm25": fielded_summary,
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
            "persistent_index_or_cache_used": False,
            "raw_score_blending_used": False,
            "score_normalization_used": False,
            "tuned_lane_weights_used": False,
            "fused_score_threshold_used": False,
            "scanner_scheduling_constants_changed": False,
            "package_lane_scoped_or_skipped": False,
        },
    }


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
            "reason": "rrf_fusion_runs_fielded_bm25_then_package_search_before_rank_fusion",
        },
        "shared_source_enumeration": {
            **planned_jsonl_file_diagnostics,
            "elapsed_ms": enumeration_elapsed_ms,
            "profile": _phase_profile(profiles.get("shared_source_enumeration"), "shared_source_enumeration"),
            "consumed_by_lanes": list(RRF_LANE_NAMES),
        },
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
                "query_streams": _fielded_query_streams_diagnostics(fielded_result),
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
        "duplicate_local_work": {
            "raw_paths_included": False,
            "enumeration_reused": True,
            "avoided_second_enumeration": True,
            "duplicate_file_visit_count": duplicate_file_visits,
            "duplicate_file_overlap_count": len(overlap),
            "duplicate_searched_bytes_lower_bound": duplicate_bytes_lower_bound,
            "fielded_bm25_materialization": fielded_materialization,
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
        },
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
        "candidate_row_limit": int(grouping.get("candidate_row_limit") or 0),
        "candidate_row_pool_limit": int(grouping.get("candidate_row_pool_limit") or 0),
        "source_count_considered": int(grouping.get("source_count_considered") or 0),
        "source_count_selected": int(grouping.get("source_count_selected") or 0),
        "dominant_source_share_before": float(grouping.get("dominant_source_share_before") or 0.0),
        "dominant_source_share_after": float(grouping.get("dominant_source_share_after") or 0.0),
        "source_head_rows_selected": int(grouping.get("source_head_rows_selected") or 0),
        "global_tail_rows_selected": int(grouping.get("global_tail_rows_selected") or 0),
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
                "fields": [str(field) for field in item.get("fields") or []],
                "status": str(item.get("status") or "unknown"),
                "row_count": int(item.get("row_count") or 0),
                "deduped_row_count": int(item.get("deduped_row_count") or 0),
                "error_type": item.get("error_type") if item.get("error_type") is None else str(item.get("error_type")),
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
        "stream_count": int(streams.get("stream_count") or len(stream_items)),
        "streams": stream_items,
        "candidate_row_pool_limit_per_stream": int(streams.get("candidate_row_pool_limit_per_stream") or 0),
        "row_count_before_dedupe": int(streams.get("row_count_before_dedupe") or 0),
        "row_count_after_dedupe": int(streams.get("row_count_after_dedupe") or 0),
        "deduped_row_count_from_streams": int(streams.get("deduped_row_count_from_streams") or 0),
        "duplicate_row_count": int(streams.get("duplicate_row_count") or 0),
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
