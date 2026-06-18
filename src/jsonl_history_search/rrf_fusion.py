from __future__ import annotations

from dataclasses import dataclass
from math import inf
from typing import Any


DEFAULT_RRF_RANK_CONSTANT = 60


@dataclass(frozen=True)
class RrfLane:
    name: str
    groups: list[dict[str, Any]]


@dataclass
class _FusedCandidate:
    identity: tuple[str, int]
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
    candidates: dict[tuple[str, int], _FusedCandidate] = {}
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


def _ranked_unique_matches(
    groups: list[dict[str, Any]],
    candidate_window_size: int | None,
) -> list[tuple[int, dict[str, Any]]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, int]] = set()
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


def _match_identity(match: dict[str, Any]) -> tuple[str, int]:
    line = match.get("line")
    try:
        line_int = int(line)
    except (TypeError, ValueError):
        line_int = 0
    return (str(match.get("path") or ""), line_int)


def _lane_identities(
    lanes: list[RrfLane],
    lane_name: str,
    candidate_window_size: int | None,
) -> list[tuple[str, int]]:
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
