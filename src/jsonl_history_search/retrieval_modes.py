from __future__ import annotations

import time
from typing import Any

from .fielded_bm25 import build_fielded_bm25_index, search_with_fielded_bm25
from .rrf_fusion import DEFAULT_RRF_RANK_CONSTANT, RrfLane, fuse_ranked_groups
from .scanner import search
from .sources import materialize_planned_jsonl_files


RETRIEVAL_MODE_PACKAGE = "package"
RETRIEVAL_MODE_RRF_FUSION = "rrf-fusion"
RETRIEVAL_MODES = (RETRIEVAL_MODE_PACKAGE, RETRIEVAL_MODE_RRF_FUSION)
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
    raise ValueError(f"unsupported retrieval mode: {retrieval_mode}")


def run_rrf_fusion_retrieval(
    *,
    plan: Any,
    intent: Any,
    default_tz: Any,
    cwd: str | None,
    mask_context: Any = None,
    use_rg_prefilter: bool = False,
) -> dict[str, Any]:
    enumeration_started = time.perf_counter()
    planned_jsonl_files = materialize_planned_jsonl_files(plan, preferred_terms=intent.base_terms)
    enumeration_elapsed_ms = _elapsed_ms(enumeration_started)

    package_started = time.perf_counter()
    package_result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        use_rg_prefilter=use_rg_prefilter,
        planned_jsonl_files=planned_jsonl_files,
        include_private_diagnostics=True,
    )
    package_elapsed_ms = _elapsed_ms(package_started)
    package_searched_paths = set(package_result.pop("_searched_file_paths", []) or [])

    fielded_index_started = time.perf_counter()
    fielded_index = build_fielded_bm25_index(plan, default_tz=default_tz, planned_jsonl_files=planned_jsonl_files)
    fielded_index_elapsed_ms = _elapsed_ms(fielded_index_started)
    fielded_started = time.perf_counter()
    fielded_result = search_with_fielded_bm25(
        fielded_index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        include_private_diagnostics=True,
    )
    fielded_elapsed_ms = _elapsed_ms(fielded_started)
    fielded_searched_paths = set(fielded_result.pop("_searched_file_paths", []) or [])

    fusion_started = time.perf_counter()
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
    fusion_elapsed_ms = _elapsed_ms(fusion_started)

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
            ),
        }
    )
    return fused_result


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
) -> dict[str, Any]:
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
            "reason": "rrf_fusion_runs_package_search_plus_fielded_bm25_before_rank_fusion",
        },
        "shared_source_enumeration": {
            **planned_jsonl_file_diagnostics,
            "elapsed_ms": enumeration_elapsed_ms,
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
                "index_build_elapsed_ms": fielded_index_elapsed_ms,
                "index_build_elapsed_label": elapsed_label(fielded_index_elapsed_ms),
                "candidate_rows": int(fielded_result.get("candidate_rows_generated", 0) or 0),
                "candidate_row_limit": int(fielded_result.get("candidate_row_limit", 0) or 0),
                "searched_files": int(fielded_result.get("searched_files", 0)),
                "searched_bytes": int(fielded_result.get("searched_bytes", 0)),
                "materialization": fielded_materialization,
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
            "candidate_count": fused_count,
            "cap_labels": cap_labels_from_warnings(warnings),
        },
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
        },
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
