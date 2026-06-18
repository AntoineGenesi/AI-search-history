from __future__ import annotations

import time
from typing import Any

from .fielded_bm25 import build_fielded_bm25_index, search_with_fielded_bm25
from .rrf_fusion import DEFAULT_RRF_RANK_CONSTANT, RrfLane, fuse_ranked_groups
from .scanner import search


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
    package_started = time.perf_counter()
    package_result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        use_rg_prefilter=use_rg_prefilter,
    )
    package_elapsed_ms = _elapsed_ms(package_started)

    fielded_index_started = time.perf_counter()
    fielded_index = build_fielded_bm25_index(plan, default_tz=default_tz)
    fielded_index_elapsed_ms = _elapsed_ms(fielded_index_started)
    fielded_started = time.perf_counter()
    fielded_result = search_with_fielded_bm25(
        fielded_index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
    )
    fielded_elapsed_ms = _elapsed_ms(fielded_started)

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
    warnings: list[str],
) -> dict[str, Any]:
    package_count = _candidate_count(package_result)
    fielded_count = _candidate_count(fielded_result)
    fused_count = _candidate_count(fused_result)
    return {
        "retrieval_mode": RETRIEVAL_MODE_RRF_FUSION,
        "feature_state": "active_explicit_opt_in",
        "strategy": "rank_only_reciprocal_rank_fusion",
        "experimental_high_recall_costlier_path": True,
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
                "searched_files": int(package_result.get("searched_files", 0)),
                "searched_bytes": int(package_result.get("searched_bytes", 0)),
                "cap_labels": cap_labels_from_warnings(package_result.get("warnings", []) or []),
            },
            {
                "name": RRF_LANE_NAMES[1],
                "candidate_count": fielded_count,
                "elapsed_ms": fielded_elapsed_ms,
                "index_build_elapsed_ms": fielded_index_elapsed_ms,
                "candidate_rows": int(fielded_result.get("candidate_rows_generated", 0) or 0),
                "candidate_row_limit": int(fielded_result.get("candidate_row_limit", 0) or 0),
                "searched_files": int(fielded_result.get("searched_files", 0)),
                "searched_bytes": int(fielded_result.get("searched_bytes", 0)),
                "cap_labels": cap_labels_from_warnings(fielded_result.get("warnings", []) or []),
                "index_summary": fielded_index_summary,
            },
        ],
        "fusion": {
            "elapsed_ms": fusion_elapsed_ms,
            "candidate_count": fused_count,
            "cap_labels": cap_labels_from_warnings(warnings),
        },
        "cap_labels": cap_labels_from_warnings(warnings),
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


def _candidate_count(result: dict[str, Any]) -> int:
    return sum(
        len(group.get("matches", []))
        for group in result.get("groups", [])
        if isinstance(group, dict)
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
