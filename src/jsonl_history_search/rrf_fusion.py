from __future__ import annotations

from .source_refs import evidence_identity

import hashlib
import json
from dataclasses import dataclass
from math import inf
from pathlib import Path
from typing import Any


DEFAULT_RRF_RANK_CONSTANT = 60
RRF_FUSION_STORAGE_FILE = "file"
RRF_FUSION_STORAGE_MEMORY = "memory"
RRF_FUSION_STORAGE_MODES = (RRF_FUSION_STORAGE_FILE, RRF_FUSION_STORAGE_MEMORY)
RRF_MATERIALIZATION_SCHEMA_VERSION = 2
RRF_MATERIALIZATION_LIMIT = 10_000


@dataclass(frozen=True)
class RrfLane:
    name: str
    groups: list[dict[str, Any]]


@dataclass
class _FusedCandidate:
    identity: tuple[str, str]
    preferred_match: dict[str, Any]
    lane_matches: dict[str, dict[str, Any]]
    lane_ranks: dict[str, int]
    lane_contributions: dict[str, float]

    @property
    def fused_score(self) -> float:
        return sum(self.lane_contributions.values())


def fuse_ranked_groups(
    lanes: list[RrfLane],
    *,
    rank_constant: int = DEFAULT_RRF_RANK_CONSTANT,
    candidate_window_size: int | None = None,
    final_limit: int | None = None,
    preferred_lane: str = "package_search",
) -> dict[str, Any]:
    if rank_constant <= 0:
        raise ValueError("rank_constant must be positive")
    if candidate_window_size is not None and candidate_window_size < 0:
        raise ValueError("candidate_window_size must be non-negative")
    if final_limit is not None and final_limit < 0:
        raise ValueError("final_limit must be non-negative")

    lane_windows: dict[str, int] = {}
    lane_unique_counts: dict[str, int] = {}
    candidates: dict[tuple[str, str], _FusedCandidate] = {}
    for lane in lanes:
        ranked = _ranked_unique_matches(lane.groups, candidate_window_size)
        lane_windows[lane.name] = len(ranked)
        lane_unique_counts[lane.name] = len(ranked)
        for rank, match in ranked:
            identity = _match_identity(match)
            contribution = 1.0 / float(rank_constant + rank)
            existing = candidates.get(identity)
            if existing is None:
                candidates[identity] = _FusedCandidate(
                    identity=identity,
                    preferred_match=dict(match),
                    lane_matches={lane.name: dict(match)},
                    lane_ranks={lane.name: rank},
                    lane_contributions={lane.name: contribution},
                )
                continue
            existing.lane_matches[lane.name] = dict(match)
            existing.lane_ranks[lane.name] = rank
            existing.lane_contributions[lane.name] = contribution
            if lane.name == preferred_lane:
                existing.preferred_match = dict(match)

    fused = sorted(
        candidates.values(),
        key=lambda item: (
            -item.fused_score,
            item.lane_ranks.get(preferred_lane, inf),
            item.lane_ranks.get("package_search_fielded_bm25", inf),
            item.identity[0],
            item.identity[1],
        ),
    )
    if final_limit is not None:
        fused = fused[:final_limit]

    matches = [_materialize_fused_match(item, lanes) for item in fused]
    lane_names = [lane.name for lane in lanes]
    package_ids = set(_lane_identities(lanes, "package_search", candidate_window_size))
    fielded_ids = set(_lane_identities(lanes, "package_search_fielded_bm25", candidate_window_size))
    overlap = package_ids & fielded_ids
    union = package_ids | fielded_ids
    top_fielded_only = [
        item.identity
        for item in fused
        if "package_search_fielded_bm25" in item.lane_ranks and "package_search" not in item.lane_ranks
    ]
    return {
        "status": "planned_rrf_fusion",
        "strategy": "rank_only_reciprocal_rank_fusion",
        "rank_constant": rank_constant,
        "lane_count": len(lanes),
        "lane_names": lane_names,
        "candidate_window_size": candidate_window_size,
        "candidate_window_sizes": lane_windows,
        "lane_unique_candidate_counts": lane_unique_counts,
        "candidate_count": len(matches),
        "overlap_count": len(overlap),
        "jaccard": round(len(overlap) / len(union), 6) if union else 0.0,
        "package_only_count": len(package_ids - fielded_ids),
        "fielded_only_count": len(fielded_ids - package_ids),
        "top_n_fielded_only_promotion_count": len(top_fielded_only),
        "groups": [{"group_key": "rrf_fusion", "matches": matches}] if matches else [],
        "privacy": {
            "raw_score_inputs_used_for_fusion": False,
            "score_normalization_used": False,
            "fused_score_threshold_used": False,
            "benchmark_tuned_weights_used": False,
            "equal_lane_weights": True,
            "raw_paths_included": False,
            "raw_private_text_included": False,
            "raw_query_text_included": False,
            "source_lines_included": False,
            "vectors_or_persisted_indexes_included": False,
        },
    }


def materialize_rrf_fusion(
    lanes: list[RrfLane],
    fused_result: dict[str, Any],
    *,
    storage_mode: str = RRF_FUSION_STORAGE_MEMORY,
    materialization_dir: str | Path | None = None,
    candidate_window_size: int | None = None,
    final_limit: int | None = None,
) -> dict[str, Any]:
    if storage_mode not in RRF_FUSION_STORAGE_MODES:
        raise ValueError(f"unsupported RRF fusion storage mode: {storage_mode}")
    payload = _rrf_materialization_payload(
        lanes,
        fused_result,
        candidate_window_size=candidate_window_size,
        final_limit=final_limit,
    )
    byte_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    row_count = int(payload["row_count"])
    lane_row_count = int(payload["lane_candidate_row_count"])
    if storage_mode == RRF_FUSION_STORAGE_MEMORY:
        return _rrf_storage_diagnostics(
            storage_mode=storage_mode,
            effective_backend="process_memory_only",
            materialization_path_category="none_memory_only",
            materialization_handle=None,
            bytes_written=0,
            bytes_read=0,
            row_count=row_count,
            lane_candidate_row_count=lane_row_count,
            reuse_status="memory_only_not_persisted",
            refresh_status="not_applicable_memory_only",
            fusion_used_in_memory=True,
            fusion_used_file_backed=False,
            materialization_bounded=bool(payload["bounded"]),
        )

    if materialization_dir is None:
        return _rrf_storage_diagnostics(
            storage_mode=storage_mode,
            effective_backend="in_memory_fallback_after_missing_materialization_dir",
            materialization_path_category="missing_materialization_dir",
            materialization_handle=None,
            bytes_written=0,
            bytes_read=0,
            row_count=row_count,
            lane_candidate_row_count=lane_row_count,
            reuse_status="file_mode_unavailable",
            refresh_status="not_refreshed_missing_materialization_dir",
            fusion_used_in_memory=True,
            fusion_used_file_backed=False,
            materialization_bounded=bool(payload["bounded"]),
        )

    root = Path(materialization_dir).expanduser()
    digest = hashlib.sha256(byte_payload).hexdigest()
    path = root / f"rrf-fusion-{digest[:16]}.json"
    handle = f"rrf-materializations/{path.name}"
    try:
        root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() == digest:
                return _rrf_storage_diagnostics(
                    storage_mode=storage_mode,
                    effective_backend="json_file_materialization",
                    materialization_path_category="fielded_bm25_index_dir_child_rrf_materializations",
                    materialization_handle=handle,
                    bytes_written=0,
                    bytes_read=len(existing),
                    row_count=row_count,
                    lane_candidate_row_count=lane_row_count,
                    reuse_status="reused_existing_materialization",
                    refresh_status="warm_reused",
                    fusion_used_in_memory=False,
                    fusion_used_file_backed=True,
                    materialization_bounded=bool(payload["bounded"]),
                )
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_bytes(byte_payload)
        tmp_path.replace(path)
    except OSError as exc:
        return _rrf_storage_diagnostics(
            storage_mode=storage_mode,
            effective_backend="in_memory_fallback_after_file_error",
            materialization_path_category="fielded_bm25_index_dir_child_rrf_materializations",
            materialization_handle=handle,
            bytes_written=0,
            bytes_read=0,
            row_count=row_count,
            lane_candidate_row_count=lane_row_count,
            reuse_status="file_mode_unavailable",
            refresh_status="not_refreshed_file_error",
            fusion_used_in_memory=True,
            fusion_used_file_backed=False,
            materialization_bounded=bool(payload["bounded"]),
            error_type=type(exc).__name__,
        )
    return _rrf_storage_diagnostics(
        storage_mode=storage_mode,
        effective_backend="json_file_materialization",
        materialization_path_category="fielded_bm25_index_dir_child_rrf_materializations",
        materialization_handle=handle,
        bytes_written=len(byte_payload),
        bytes_read=0,
        row_count=row_count,
        lane_candidate_row_count=lane_row_count,
        reuse_status="written_new_materialization",
        refresh_status="cold_written",
        fusion_used_in_memory=False,
        fusion_used_file_backed=True,
        materialization_bounded=bool(payload["bounded"]),
    )


def _ranked_unique_matches(
    groups: list[dict[str, Any]],
    candidate_window_size: int | None,
) -> list[tuple[int, dict[str, Any]]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for match in _flat_matches(groups):
        identity = _match_identity(match)
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        ranked.append((len(ranked) + 1, match))
        if candidate_window_size is not None and len(ranked) >= candidate_window_size:
            break
    return ranked


def _flat_matches(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        match
        for group in groups
        if isinstance(group, dict)
        for match in group.get("matches", [])
        if isinstance(match, dict)
    ]


def _match_identity(match: dict[str, Any]) -> tuple[str, str]:
    return evidence_identity(match)


def _lane_identities(
    lanes: list[RrfLane],
    lane_name: str,
    candidate_window_size: int | None,
) -> list[tuple[str, str]]:
    for lane in lanes:
        if lane.name == lane_name:
            return [
                _match_identity(match)
                for _rank, match in _ranked_unique_matches(lane.groups, candidate_window_size)
            ]
    return []


def _materialize_fused_match(item: _FusedCandidate, lanes: list[RrfLane]) -> dict[str, Any]:
    match = dict(item.preferred_match)
    match["rrf_fused_score"] = round(item.fused_score, 12)
    match["rrf_lane_ranks"] = {lane.name: item.lane_ranks.get(lane.name) for lane in lanes}
    match["rrf_lane_contributions"] = {
        lane.name: round(item.lane_contributions.get(lane.name, 0.0), 12) for lane in lanes
    }
    match["rrf_lane_present"] = {lane.name: lane.name in item.lane_ranks for lane in lanes}
    match["rrf_raw_lane_scores"] = {
        lane.name: item.lane_matches[lane.name].get("score")
        for lane in lanes
        if lane.name in item.lane_matches
    }
    return match


def _rrf_materialization_payload(
    lanes: list[RrfLane],
    fused_result: dict[str, Any],
    *,
    candidate_window_size: int | None,
    final_limit: int | None,
) -> dict[str, Any]:
    lane_rows: list[dict[str, Any]] = []
    for lane in lanes:
        ranked = _ranked_unique_matches(lane.groups, candidate_window_size)
        for rank, match in ranked[:RRF_MATERIALIZATION_LIMIT]:
            lane_rows.append(
                {
                    "lane": lane.name,
                    "rank": int(rank),
                    "candidate_ref_hash": _candidate_ref_hash(match),
                    "source_kind": str(match.get("source_kind") or match.get("source") or "unknown"),
                    "role": str(match.get("role") or "unknown"),
                    "timestamp_present": bool(match.get("timestamp")),
                    "score_present": "score" in match,
                    "raw_path_included": False,
                    "line_number_included": False,
                    "raw_text_included": False,
                }
            )
    fused_rows = []
    for rank, match in enumerate(_flat_matches(fused_result.get("groups", [])), start=1):
        if rank > RRF_MATERIALIZATION_LIMIT:
            break
        fused_rows.append(
            {
                "rank": rank,
                "candidate_ref_hash": _candidate_ref_hash(match),
                "rrf_fused_score": match.get("rrf_fused_score"),
                "lane_ranks": dict(match.get("rrf_lane_ranks") or {}),
                "lane_present": dict(match.get("rrf_lane_present") or {}),
                "raw_path_included": False,
                "line_number_included": False,
                "raw_text_included": False,
            }
        )
    bounded = len(lane_rows) < RRF_MATERIALIZATION_LIMIT and len(fused_rows) < RRF_MATERIALIZATION_LIMIT
    return {
        "schema_version": RRF_MATERIALIZATION_SCHEMA_VERSION,
        "artifact_kind": "sanitized_rrf_fusion_materialization",
        "strategy": str(fused_result.get("strategy") or "rank_only_reciprocal_rank_fusion"),
        "rank_constant": int(fused_result.get("rank_constant") or DEFAULT_RRF_RANK_CONSTANT),
        "candidate_window_size": candidate_window_size,
        "final_limit": final_limit,
        "lane_names": [lane.name for lane in lanes],
        "candidate_window_sizes": dict(fused_result.get("candidate_window_sizes") or {}),
        "lane_unique_candidate_counts": dict(fused_result.get("lane_unique_candidate_counts") or {}),
        "lane_candidate_row_count": len(lane_rows),
        "row_count": len(fused_rows),
        "candidate_count": int(fused_result.get("candidate_count") or len(fused_rows)),
        "overlap_count": int(fused_result.get("overlap_count") or 0),
        "package_only_count": int(fused_result.get("package_only_count") or 0),
        "fielded_only_count": int(fused_result.get("fielded_only_count") or 0),
        "bounded": bounded,
        "row_limit": RRF_MATERIALIZATION_LIMIT,
        "lane_candidates": lane_rows,
        "fused_candidates": fused_rows,
        "privacy": _rrf_materialization_privacy(),
        "claim_boundaries": _rrf_materialization_claim_boundaries(),
    }


def _candidate_ref_hash(match: dict[str, Any]) -> str:
    path, line = _match_identity(match)
    return hashlib.sha256(f"{path}\0{line}".encode("utf-8", errors="replace")).hexdigest()


def _rrf_storage_diagnostics(
    *,
    storage_mode: str,
    effective_backend: str,
    materialization_path_category: str,
    materialization_handle: str | None,
    bytes_written: int,
    bytes_read: int,
    row_count: int,
    lane_candidate_row_count: int,
    reuse_status: str,
    refresh_status: str,
    fusion_used_in_memory: bool,
    fusion_used_file_backed: bool,
    materialization_bounded: bool,
    error_type: str | None = None,
) -> dict[str, Any]:
    diagnostics = {
        "schema_version": 1,
        "storage_mode": storage_mode,
        "effective_backend": effective_backend,
        "materialization_path_category": materialization_path_category,
        "materialization_handle": materialization_handle,
        "materialization_path_included": False,
        "bytes_written": int(bytes_written),
        "bytes_read": int(bytes_read),
        "row_count": int(row_count),
        "candidate_count": int(row_count),
        "lane_candidate_row_count": int(lane_candidate_row_count),
        "reuse_status": reuse_status,
        "refresh_status": refresh_status,
        "fusion_used_in_memory_materialization": fusion_used_in_memory,
        "fusion_used_file_backed_materialization": fusion_used_file_backed,
        "bounded": materialization_bounded,
        "privacy": _rrf_materialization_privacy(),
        "claim_boundaries": _rrf_materialization_claim_boundaries(),
    }
    if error_type:
        diagnostics["error_type"] = error_type
    return diagnostics


def _rrf_materialization_privacy() -> dict[str, bool]:
    return {
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "candidate_ref_hashes_included": True,
        "vectors_or_persisted_indexes_included": False,
    }


def _rrf_materialization_claim_boundaries() -> dict[str, bool]:
    return {
        "ranking_semantics_changed": False,
        "default_promotion_supported": False,
        "cap_triggered_escalation_enabled": False,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "broad_speedup_claim_supported": False,
        "all_history_completeness_claim_supported": False,
    }
