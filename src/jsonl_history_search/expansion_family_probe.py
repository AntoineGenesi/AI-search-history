from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .expansion import (
    CONTEXTUAL_EXPANSION_FAMILIES,
    CONTEXTUAL_EXPANSION_FAMILY_METADATA,
    contextual_expansion_family_audit,
    expand_query_terms,
)
from .query import infer_artifacts, infer_date_window, infer_terms
from .real_history_bridge import load_json, validate_bridge


SCHEMA_VERSION = 1
DEFAULT_FAMILY = "process_detour_lesson"
DEFAULT_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")


def supported_family_names() -> tuple[str, ...]:
    return tuple(sorted(row["name"] for row in contextual_expansion_family_audit()))


def build_expansion_family_probe_report(
    *,
    family: str = DEFAULT_FAMILY,
    manifest_path: Path | None = None,
    private_bridge_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    target = _family_for_name(family)
    manifest_file = manifest_path or DEFAULT_MANIFEST
    bridge_file = private_bridge_path or _locate_default_private_bridge()
    now = now or datetime(2026, 6, 18, tzinfo=timezone.utc)

    family_metadata = _family_metadata(target)
    if bridge_file is None:
        return _empty_report(
            family=family,
            family_metadata=family_metadata,
            bridge_status="bridge_unavailable",
            recommendation="retain_current_behavior_until_real_triggering_cases_exist",
            reason="No default ignored private bridge was available for aggregate counting.",
        )

    manifest = load_json(manifest_file)
    bridge = load_json(bridge_file)
    validation = validate_bridge(manifest, bridge)
    bridge_summary, accepted_case_ids = _bridge_summary(manifest, bridge, validation.report)

    manifest_cases = _case_map(manifest)
    trigger_count = 0
    accepted_family_buckets: Counter[str] = Counter()
    accepted_label_buckets: Counter[str] = Counter()
    trigger_family_buckets: Counter[str] = Counter()
    trigger_label_buckets: Counter[str] = Counter()
    current_term_count_buckets: Counter[str] = Counter()
    excluded_term_count_buckets: Counter[str] = Counter()
    term_delta_buckets: Counter[str] = Counter()

    for case_id in sorted(accepted_case_ids):
        manifest_case = manifest_cases.get(case_id, {})
        accepted_family_buckets[_safe_label(manifest_case.get("family"))] += 1
        accepted_label_buckets[_safe_label(manifest_case.get("evidence_type"))] += 1
        query_surfaces = _sanitized_query_surfaces(manifest_case)
        if not query_surfaces:
            continue
        if any(_family_triggers(target, surface) for surface in query_surfaces):
            trigger_count += 1
            trigger_family_buckets[_safe_label(manifest_case.get("family"))] += 1
            trigger_label_buckets[_safe_label(manifest_case.get("evidence_type"))] += 1
            current_count, excluded_count = _term_count_pair(target, " ".join(query_surfaces), now=now)
            current_term_count_buckets[_bucket_count(current_count)] += 1
            excluded_term_count_buckets[_bucket_count(excluded_count)] += 1
            term_delta_buckets[_bucket_count(current_count - excluded_count)] += 1

    if trigger_count == 0:
        verdict = "current_retained_real_history_does_not_exercise_process_detour_lesson"
        recommendation = "retain_current_behavior_until_real_triggering_cases_exist"
        reason = (
            "The current retained real-history bridge has no accepted common cases "
            "that trigger the target family, so exclusion or replacement would not "
            "be a meaningful behavior comparison yet."
        )
        term_count_comparison: dict[str, Any] = {
            "status": "omitted_no_triggering_cases",
            "current_vs_family_excluded_term_count_buckets_included": False,
        }
    else:
        verdict = "current_retained_real_history_exercises_process_detour_lesson"
        recommendation = "run_opt_in_behavior_comparison_before_changing_defaults"
        reason = (
            "At least one accepted common case triggers the target family. A "
            "default-off behavior comparison would be needed before any runtime "
            "suppression, replacement, or generated-metadata promotion decision."
        )
        term_count_comparison = {
            "status": "aggregate_buckets_only",
            "current_vs_family_excluded_term_count_buckets_included": True,
            "current_term_count_buckets": dict(sorted(current_term_count_buckets.items())),
            "family_excluded_term_count_buckets": dict(sorted(excluded_term_count_buckets.items())),
            "term_delta_buckets": dict(sorted(term_delta_buckets.items())),
            "counterfactual_note": (
                "Counts remove target-family terms from the current expanded term set "
                "for diagnostics only; runtime expansion behavior is unchanged."
            ),
        }

    bridge_summary["planner_aggregate_match"]["process_detour_lesson_triggers_0"] = trigger_count == 0

    return {
        "expansion_family_probe": {
            "schema_version": SCHEMA_VERSION,
            "status": "report_only_diagnostic",
            "family": family,
            "verdict": verdict,
            "runtime_retrieval_behavior_changed": False,
            "defaults_unchanged": {
                "default_package_search": True,
                "default_find": True,
                "default_semantic_debug": True,
                "infer_query": True,
                "contextual_expansion_families_default": True,
                "scanner": True,
                "ranking": True,
                "source_caps": True,
                "output_caps": True,
                "sidecars": True,
                "generated_metadata_unpromoted": True,
            },
            "family_metadata": family_metadata,
            "bridge": bridge_summary,
            "counters": {
                "family_trigger_count": trigger_count,
                "accepted_common_case_count": bridge_summary["accepted_common_case_count"],
                "accepted_case_family_buckets": dict(sorted(accepted_family_buckets.items())),
                "accepted_case_label_buckets": dict(sorted(accepted_label_buckets.items())),
                "triggering_case_family_buckets": dict(sorted(trigger_family_buckets.items())),
                "triggering_case_label_buckets": dict(sorted(trigger_label_buckets.items())),
                "term_count_comparison": term_count_comparison,
            },
            "recommendation": {
                "action": recommendation,
                "reason": reason,
                "next_direct_tool_action": "select_from_measured_tool_evidence_not_harness_live_recovery",
                "not_justified_yet": [
                    "default_suppression",
                    "default_replacement",
                    "opt_in_gate_as_default_policy",
                    "generated_metadata_promotion",
                ],
            },
            "privacy_and_oracle_boundary": _privacy_boundary(),
        }
    }


def _empty_report(
    *,
    family: str,
    family_metadata: dict[str, Any],
    bridge_status: str,
    recommendation: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "expansion_family_probe": {
            "schema_version": SCHEMA_VERSION,
            "status": "report_only_diagnostic",
            "family": family,
            "verdict": "bridge_unavailable_no_real_case_probe_counts",
            "runtime_retrieval_behavior_changed": False,
            "defaults_unchanged": {
                "default_package_search": True,
                "default_find": True,
                "default_semantic_debug": True,
                "infer_query": True,
                "contextual_expansion_families_default": True,
                "scanner": True,
                "ranking": True,
                "source_caps": True,
                "output_caps": True,
                "sidecars": True,
                "generated_metadata_unpromoted": True,
            },
            "family_metadata": family_metadata,
            "bridge": {
                "status": bridge_status,
                "manifest_case_count": 0,
                "bridge_case_count": 0,
                "common_case_count": 0,
                "accepted_common_case_count": 0,
                "missing_from_bridge_count": 0,
                "extra_bridge_case_count": 0,
                "validation_error_count": 0,
                "validator_status": "unknown",
                "planner_aggregate_match": {
                    "common_accepted_cases_18": False,
                    "missing_from_newer_manifest_2": False,
                    "process_detour_lesson_triggers_0": False,
                },
            },
            "counters": {
                "family_trigger_count": 0,
                "accepted_common_case_count": 0,
                "accepted_case_family_buckets": {},
                "accepted_case_label_buckets": {},
                "triggering_case_family_buckets": {},
                "triggering_case_label_buckets": {},
                "term_count_comparison": {
                    "status": "omitted_no_triggering_cases",
                    "current_vs_family_excluded_term_count_buckets_included": False,
                },
            },
            "recommendation": {
                "action": recommendation,
                "reason": reason,
                "next_direct_tool_action": "select_from_measured_tool_evidence_not_harness_live_recovery",
                "not_justified_yet": [
                    "default_suppression",
                    "default_replacement",
                    "opt_in_gate_as_default_policy",
                    "generated_metadata_promotion",
                ],
            },
            "privacy_and_oracle_boundary": _privacy_boundary(),
        }
    }


def _family_for_name(name: str):
    for family in CONTEXTUAL_EXPANSION_FAMILIES:
        metadata = CONTEXTUAL_EXPANSION_FAMILY_METADATA[family.has_intent.__name__]
        if metadata["name"] == name:
            return family
    raise ValueError(f"unsupported expansion family: {name}")


def _family_metadata(family) -> dict[str, Any]:
    row = next(
        item
        for item in contextual_expansion_family_audit()
        if item["intent"] == family.has_intent.__name__
    )
    return {
        "name": row["name"],
        "intent": row["intent"],
        "core_scope": row["core_scope"],
        "overfit_risk": row["overfit_risk"],
        "generated_metadata_role": row["generated_metadata_role"],
        "term_count": row["term_count"],
        "flagged_local_or_proper_terms": list(row["flagged_local_or_proper_terms"]),
    }


def _locate_default_private_bridge() -> Path | None:
    cwd = Path.cwd()
    candidates = [
        cwd / ".private" / "jsh-049" / "bridge.json",
        cwd / ".private" / "jsh-715" / "bridge.json",
    ]
    parents = list(cwd.parents)
    if len(parents) >= 2:
        candidates.extend(
            [
                parents[1] / ".private" / "jsh-049" / "bridge.json",
                parents[1] / ".private" / "jsh-715" / "bridge.json",
            ]
        )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _case_map(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = case.get("case_id")
        if isinstance(case_id, str) and case_id:
            rows[case_id] = case
    return rows


def _bridge_summary(
    manifest: Mapping[str, Any],
    bridge: Mapping[str, Any],
    validation_report: Mapping[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    manifest_ids = set(_case_map(manifest))
    bridge_ids = set(_case_map(bridge))
    common_ids = manifest_ids & bridge_ids
    payload = validation_report.get("real_history_benchmark_bridge", {})
    cases = payload.get("cases") if isinstance(payload, Mapping) else []
    accepted_case_ids = {
        case["case_id"]
        for case in cases
        if isinstance(case, dict)
        and case.get("status") == "accepted"
        and isinstance(case.get("case_id"), str)
    }
    missing_count = len(manifest_ids - bridge_ids)
    extra_count = len(bridge_ids - manifest_ids)
    if payload.get("status") == "ok":
        status = "valid_full_intersection"
    elif common_ids:
        status = "partial_intersection"
    else:
        status = "invalid_no_common_cases"
    return (
        {
            "status": status,
            "validator_status": payload.get("status", "unknown"),
            "manifest_case_count": len(manifest_ids),
            "bridge_case_count": len(bridge_ids),
            "common_case_count": len(common_ids),
            "accepted_common_case_count": len(accepted_case_ids),
            "quarantined_common_case_count": payload.get("quarantined_case_count", 0),
            "rejected_common_case_count": payload.get("rejected_case_count", 0),
            "missing_from_bridge_count": missing_count,
            "extra_bridge_case_count": extra_count,
            "validation_error_count": payload.get("error_count", 0),
            "planner_aggregate_match": {
                "common_accepted_cases_18": len(accepted_case_ids) == 18,
                "missing_from_newer_manifest_2": missing_count == 2,
                "process_detour_lesson_triggers_0": None,
            },
        },
        accepted_case_ids,
    )


def _sanitized_query_surfaces(manifest_case: Mapping[str, Any]) -> list[str]:
    surfaces: list[str] = []
    for key in ("ask_pattern_paraphrase", "query_paraphrase", "expected_outcome"):
        value = manifest_case.get(key)
        if isinstance(value, str) and value.strip():
            surfaces.append(value.strip())
    return surfaces


def _family_triggers(family, text: str) -> bool:
    normalized = " ".join(text.lower().split())
    artifacts = infer_artifacts(normalized)
    base_terms = infer_terms(normalized, artifacts)
    return bool(family.has_intent(normalized, base_terms))


def _term_count_pair(family, text: str, *, now: datetime) -> tuple[int, int]:
    normalized = " ".join(text.lower().split())
    artifacts = infer_artifacts(normalized)
    base_terms = infer_terms(normalized, artifacts)
    start, end, reason = infer_date_window(normalized, now)
    current = expand_query_terms(
        normalized=normalized,
        base_terms=base_terms,
        artifact_hints=artifacts,
        date_window_start=start,
        date_window_end=end,
        date_reason=reason,
    ).terms
    excluded_terms = set(family.terms())
    excluded = [term for term in current if term not in excluded_terms]
    return len(current), len(excluded)


def _bucket_count(value: int) -> str:
    if value <= 0:
        return "0"
    if value <= 5:
        return "1_5"
    if value <= 10:
        return "6_10"
    if value <= 20:
        return "11_20"
    if value <= 40:
        return "21_40"
    return "41_plus"


def _safe_label(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _privacy_boundary() -> dict[str, bool]:
    return {
        "committed_safe": True,
        "raw_query_strings_included": False,
        "case_ids_included": False,
        "expected_refs_included": False,
        "paths_included": False,
        "target_lines_included": False,
        "session_ids_included": False,
        "local_usernames_included": False,
        "per_row_expansions_included": False,
        "current_benchmark_miss_ids_included": False,
        "user_specific_runtime_phrasing_included": False,
    }
