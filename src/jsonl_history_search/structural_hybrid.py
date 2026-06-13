from __future__ import annotations

from typing import Any

from .masking import MaskContext
from .query import QueryIntent
from .scanner import search
from .sources import SourcePlan
from .structural_sidecar import (
    StructuralSidecarIndex,
    build_structural_sidecar_index,
    search_with_structural_sidecar,
)


BUDGETED_HYBRID_GATE_CANDIDATE_THRESHOLD = 10
BUDGETED_HYBRID_SUPPLEMENT_LIMIT = 10


def search_structural_hybrid_budgeted(
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None,
    mask_context: MaskContext | None = None,
    structural_sidecar_index: StructuralSidecarIndex | None = None,
    gate_candidate_threshold: int = BUDGETED_HYBRID_GATE_CANDIDATE_THRESHOLD,
    supplement_limit: int = BUDGETED_HYBRID_SUPPLEMENT_LIMIT,
    expected_ref_diagnostics: list[dict] | None = None,
) -> dict[str, Any]:
    package_result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        mask_context=mask_context,
        use_rg_prefilter=False,
        expected_ref_diagnostics=expected_ref_diagnostics,
    )
    package_candidate_count = sum(len(group.get("matches") or []) for group in package_result.get("groups", []))
    gate = structural_supplement_gate(
        package_candidate_count=package_candidate_count,
        threshold=gate_candidate_threshold,
    )
    structural_result: dict[str, Any] | None = None
    if gate["supplement_enabled"]:
        index = structural_sidecar_index or build_structural_sidecar_index(plan, default_tz=default_tz)
        structural_result = search_with_structural_sidecar(
            index,
            plan,
            intent,
            default_tz=default_tz,
            cwd=cwd,
            expected_ref_diagnostics=expected_ref_diagnostics,
        )
    result = hybrid_package_structural_result(
        package_result,
        structural_result,
        supplement_limit=supplement_limit,
        gate=gate,
    )
    result["warnings"] = sorted(
        set((package_result.get("warnings") or []) + ((structural_result or {}).get("warnings") or []))
    )
    result["blind_spots"] = sorted(set(package_result.get("blind_spots") or []))
    result["command_surface"] = {
        "name": "find-structural-hybrid-budgeted",
        "installed_cli": "python3 -m jsonl_history_search.cli find-structural-hybrid-budgeted",
        "algorithm": "normal_package_search_first_then_bounded_structural_sidecar_supplement",
        "default_find_behavior_changed": False,
        "default_semantic_debug_behavior_changed": False,
        "live_send_capable": False,
        "private_bridge_required": False,
        "expected_refs_required": False,
    }
    return result


def hybrid_package_structural_result(
    package_result: dict[str, Any],
    structural_result: dict[str, Any] | None,
    *,
    supplement_limit: int,
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gate = gate or {"supplement_enabled": True, "gate_reason": "ungated", "gate_candidate_threshold": None}
    package_groups = list(package_result.get("groups") or [])
    package_keys = _match_keys(package_groups)
    effective_structural_result = structural_result if gate["supplement_enabled"] else None
    structural_supplement: list[dict[str, Any]] = []
    if effective_structural_result is not None:
        for match in _flat_matches(effective_structural_result.get("groups") or []):
            key = _match_key(match)
            if key in package_keys:
                continue
            structural_supplement.append(match)
            package_keys.add(key)
            if len(structural_supplement) >= supplement_limit:
                break
    groups = list(package_groups)
    if structural_supplement:
        groups.append(
            {
                "group_key": "structural_sidecar_supplement",
                "matches": structural_supplement,
                "source": "package_search_structural_hybrid",
            }
        )
    return {
        "status": "planned_package_structural_hybrid",
        "strategy": "package_first_bounded_structural_supplement",
        "gate": gate,
        "groups": groups,
        "package_candidate_count": sum(len(group.get("matches") or []) for group in package_groups),
        "structural_supplement_candidate_count": len(structural_supplement),
        "supplement_limit": supplement_limit,
        "searched_files": int(package_result.get("searched_files", 0))
        + int((effective_structural_result or {}).get("searched_files", 0)),
        "searched_bytes": int(package_result.get("searched_bytes", 0))
        + int((effective_structural_result or {}).get("searched_bytes", 0)),
        "package_searched_bytes": int(package_result.get("searched_bytes", 0)),
        "structural_searched_bytes": int((effective_structural_result or {}).get("searched_bytes", 0)),
        "package_warnings": list(package_result.get("warnings") or []),
        "structural_warnings": list((effective_structural_result or {}).get("warnings") or []),
        "privacy": {
            "raw_paths_included_in_summary": False,
            "raw_text_included_in_summary": False,
            "expected_refs_used_for_retrieval": False,
        },
    }


def structural_supplement_gate(*, package_candidate_count: int, threshold: int | None) -> dict[str, Any]:
    if threshold is None:
        return {
            "supplement_enabled": True,
            "gate_reason": "ungated",
            "gate_candidate_threshold": None,
            "package_candidate_count": package_candidate_count,
            "oracle_inputs_used": False,
        }
    enabled = package_candidate_count <= threshold
    return {
        "supplement_enabled": enabled,
        "gate_reason": (
            "package_candidate_count_at_or_below_threshold"
            if enabled
            else "package_candidate_count_above_threshold"
        ),
        "gate_candidate_threshold": threshold,
        "package_candidate_count": package_candidate_count,
        "oracle_inputs_used": False,
    }


def _flat_matches(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [match for group in groups for match in group.get("matches", []) if isinstance(match, dict)]


def _match_keys(groups: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {_match_key(match) for match in _flat_matches(groups)}


def _match_key(match: dict[str, Any]) -> tuple[str, int]:
    return (str(match.get("path") or ""), int(match.get("line") or 0))
