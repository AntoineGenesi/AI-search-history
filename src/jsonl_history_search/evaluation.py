from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark import load_benchmark_fixture, validate_benchmark_fixture
from .embedding_spike import DEFAULT_EMBEDDING_SPIKE_FIXTURE, build_embedding_spike_eval


DEFAULT_FIXTURE = Path("benchmarks/retrieval_gold/sanitized_cases.json")
DEFAULT_STRATEGIES = ("package_search", "helper_script", "raw_rg", "naive_broad_scan")
BYTES_PER_FILE_MODEL = 64 * 1024


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    label: str
    description: str
    stages: frozenset[str]
    failure_controls: frozenset[str]
    tool_call_multiplier: float
    file_multiplier: float
    parsed_byte_multiplier: float
    context_multiplier: float
    candidate_multiplier: float
    elapsed_ms_per_file: float
    elapsed_ms_per_tool_call: float
    first_pass_floor: bool = False


STRATEGY_PROFILES: dict[str, StrategyProfile] = {
    "package_search": StrategyProfile(
        name="package_search",
        label="Current package search",
        description="Bounded source planning, JSONL parsing, role/date/project signals, ranking, grouping, and compact snippets.",
        stages=frozenset(
            {
                "artifact_path",
                "cwd_project",
                "date_window",
                "exact_phrase",
                "normalized_terms",
                "role_filter",
                "semantic_local",
                "synonym_paraphrase",
                "typo_fuzzy",
                "newest_first_shortlist",
            }
        ),
        failure_controls=frozenset(
            {
                "false_confident_answer",
                "exact_phrase_miss",
                "ignored_chat_project_hint",
                "over_reading",
                "too_many_tool_calls",
                "wrong_root",
            }
        ),
        tool_call_multiplier=0.75,
        file_multiplier=0.75,
        parsed_byte_multiplier=0.75,
        context_multiplier=0.55,
        candidate_multiplier=1.0,
        elapsed_ms_per_file=10.0,
        elapsed_ms_per_tool_call=45.0,
        first_pass_floor=False,
    ),
    "helper_script": StrategyProfile(
        name="helper_script",
        label="Helper script",
        description=(
            "The global-skill scripts/search_jsonl_history.py helper with newest-first, timestamp windows, "
            "role aliases, exact phrase search, broad term-coverage fallback, and explicit empty-root warnings."
        ),
        stages=frozenset(
            {
                "artifact_path",
                "cwd_project",
                "date_window",
                "exact_phrase",
                "newest_first_shortlist",
                "normalized_terms",
                "role_filter",
            }
        ),
        failure_controls=frozenset({"exact_phrase_miss", "over_reading", "too_many_tool_calls"}),
        tool_call_multiplier=1.0,
        file_multiplier=1.15,
        parsed_byte_multiplier=1.1,
        context_multiplier=1.0,
        candidate_multiplier=1.5,
        elapsed_ms_per_file=8.0,
        elapsed_ms_per_tool_call=35.0,
        first_pass_floor=False,
    ),
    "raw_rg": StrategyProfile(
        name="raw_rg",
        label="Raw rg",
        description="Direct ripgrep over selected JSONL roots without JSON parsing, role/date/project ranking, or transcript-aware grouping.",
        stages=frozenset({"exact_phrase", "artifact_path"}),
        failure_controls=frozenset(),
        tool_call_multiplier=1.2,
        file_multiplier=2.5,
        parsed_byte_multiplier=2.2,
        context_multiplier=1.8,
        candidate_multiplier=2.5,
        elapsed_ms_per_file=3.0,
        elapsed_ms_per_tool_call=30.0,
        first_pass_floor=False,
    ),
    "naive_broad_scan": StrategyProfile(
        name="naive_broad_scan",
        label="Naive broad scan",
        description="Open broad transcript roots and feed large chunks to the agent, maximizing recall at high context cost.",
        stages=frozenset(
            {
                "artifact_path",
                "cwd_project",
                "date_window",
                "exact_phrase",
                "normalized_terms",
                "role_filter",
                "semantic_local",
                "synonym_paraphrase",
                "typo_fuzzy",
                "newest_first_shortlist",
            }
        ),
        failure_controls=frozenset({"broad_recursive_walk"}),
        tool_call_multiplier=2.5,
        file_multiplier=6.0,
        parsed_byte_multiplier=6.0,
        context_multiplier=8.0,
        candidate_multiplier=6.0,
        elapsed_ms_per_file=18.0,
        elapsed_ms_per_tool_call=55.0,
        first_pass_floor=True,
    ),
}


def build_efficiency_eval(
    fixture_path: Path = DEFAULT_FIXTURE,
    *,
    strategies: tuple[str, ...] | None = DEFAULT_STRATEGIES,
    include_embedding_spike: bool = False,
    embedding_fixture_path: Path = DEFAULT_EMBEDDING_SPIKE_FIXTURE,
) -> dict[str, Any]:
    fixture = load_benchmark_fixture(fixture_path)
    errors = validate_benchmark_fixture(fixture)
    if errors:
        payload = {
            "retrieval_efficiency_eval": {
                "schema_version": 1,
                "status": "invalid_fixture",
                "fixture": str(fixture_path),
                "fixture_errors": errors,
            }
        }
        if include_embedding_spike:
            payload["retrieval_efficiency_eval"]["embedding_spike"] = build_embedding_spike_eval(embedding_fixture_path)
        return payload

    profiles = [STRATEGY_PROFILES[name] for name in (strategies or DEFAULT_STRATEGIES)]
    case_results = [evaluate_case(case, profiles) for case in fixture["cases"]]
    summary = summarize_results(case_results, profiles)
    thresholds = evaluate_thresholds(summary)
    report = {
        "retrieval_efficiency_eval": {
            "schema_version": 1,
            "status": "ok",
            "fixture": str(fixture_path),
            "data_source": "sanitized_fixture_model",
            "privacy": fixture.get("privacy", {}),
            "measurement_note": (
                "Metrics are derived from sanitized benchmark budgets, observed stage metadata, "
                "and deterministic strategy profiles. They are suitable for regression gating and "
                "feature-prioritization decisions, but they do not expose or benchmark raw private transcripts."
            ),
            "metrics": metric_definitions(),
            "strategies": [profile_to_dict(profile) for profile in profiles],
            "cases": case_results,
            "summary": summary,
            "large_impact_thresholds": thresholds,
            "coverage_needs_followup": fixture.get("coverage_needs_followup", {}),
        }
    }
    if include_embedding_spike:
        report["retrieval_efficiency_eval"]["embedding_spike"] = build_embedding_spike_eval(embedding_fixture_path)
    return report


def evaluate_case(case: dict[str, Any], profiles: list[StrategyProfile]) -> dict[str, Any]:
    results = [evaluate_strategy(case, profile) for profile in profiles]
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected_outcome": case["expected_outcome"],
        "evidence_status": case["evidence_status"],
        "private_triage_status": case.get("private_triage_status"),
        "required_stages": case["candidate_generation_stages"],
        "failure_modes": case["failure_modes"],
        "budget": case["budgets"],
        "gold_ref_count": len(case["sanitized_gold_refs"]),
        "strategy_results": results,
    }


def evaluate_strategy(case: dict[str, Any], profile: StrategyProfile) -> dict[str, Any]:
    budgets = case["budgets"]
    missing_stages = sorted(set(case["candidate_generation_stages"]) - profile.stages)
    unguarded_failures = sorted(set(case["failure_modes"]) - profile.failure_controls)
    supported = not missing_stages
    guarded = not unguarded_failures
    first_pass_hit = bool((supported and guarded) or profile.first_pass_floor)
    if case["expected_outcome"] == "no_evidence" and "false_confident_answer" not in profile.failure_controls:
        first_pass_hit = False

    tool_calls = max(1, math.ceil(budgets["max_tool_calls"] * profile.tool_call_multiplier))
    files_opened = max(1, math.ceil(budgets["max_files_opened"] * profile.file_multiplier))
    bytes_parsed = max(files_opened, math.ceil(files_opened * BYTES_PER_FILE_MODEL * profile.parsed_byte_multiplier))
    prompt_context_bytes = max(512, math.ceil(budgets["max_context_bytes"] * profile.context_multiplier))
    candidates_returned = max(1, math.ceil(budgets["target_top_k"] * profile.candidate_multiplier))
    elapsed_ms = round(files_opened * profile.elapsed_ms_per_file + tool_calls * profile.elapsed_ms_per_tool_call, 3)
    gold_ref_count = max(1, len(case["sanitized_gold_refs"]))

    return {
        "strategy": profile.name,
        "metric_basis": "modeled_from_sanitized_case_metadata",
        "prompt_context_bytes": prompt_context_bytes,
        "estimated_tokens_returned": estimate_tokens(prompt_context_bytes),
        "files_opened": files_opened,
        "bytes_parsed": bytes_parsed,
        "tool_calls": tool_calls,
        "elapsed_ms": elapsed_ms,
        "candidate_snippets_returned": candidates_returned,
        "first_pass_hit": first_pass_hit,
        "recall_at_1": recall_at(1, candidates_returned, gold_ref_count, first_pass_hit),
        "recall_at_3": recall_at(3, candidates_returned, gold_ref_count, first_pass_hit),
        "recall_at_10": recall_at(10, candidates_returned, gold_ref_count, first_pass_hit),
        "missing_stages": missing_stages,
        "unguarded_failure_modes": unguarded_failures,
        "fit": "hit" if first_pass_hit else "miss",
        "fit_basis": (
            "Strategy profile covers the case's sanitized required stages and failure controls."
            if first_pass_hit
            else "Strategy profile misses required sanitized stages or failure controls."
        ),
    }


def recall_at(k: int, candidates_returned: int, gold_ref_count: int, first_pass_hit: bool) -> float:
    if not first_pass_hit:
        return 0.0
    effective_candidates = min(k, candidates_returned)
    return round(min(1.0, effective_candidates / gold_ref_count), 3)


def estimate_tokens(byte_count: int) -> int:
    return max(1, math.ceil(byte_count / 4))


def summarize_results(case_results: list[dict[str, Any]], profiles: list[StrategyProfile]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for profile in profiles:
        rows = [find_strategy_result(case, profile.name) for case in case_results]
        summary[profile.name] = {
            "cases": len(rows),
            "first_pass_hits": sum(1 for row in rows if row["first_pass_hit"]),
            "first_pass_hit_rate": round(sum(1 for row in rows if row["first_pass_hit"]) / len(rows), 3),
            "median_prompt_context_bytes": median_int(row["prompt_context_bytes"] for row in rows),
            "median_estimated_tokens_returned": median_int(row["estimated_tokens_returned"] for row in rows),
            "median_files_opened": median_int(row["files_opened"] for row in rows),
            "median_bytes_parsed": median_int(row["bytes_parsed"] for row in rows),
            "median_tool_calls": median_int(row["tool_calls"] for row in rows),
            "median_elapsed_ms": median_float(row["elapsed_ms"] for row in rows),
            "mean_recall_at_10": round(sum(row["recall_at_10"] for row in rows) / len(rows), 3),
        }
    add_relative_metrics(summary, "package_search", "naive_broad_scan")
    add_relative_metrics(summary, "package_search", "raw_rg")
    add_relative_metrics(summary, "package_search", "helper_script")
    return summary


def find_strategy_result(case: dict[str, Any], strategy: str) -> dict[str, Any]:
    for row in case["strategy_results"]:
        if row["strategy"] == strategy:
            return row
    raise KeyError(strategy)


def median_int(values: Any) -> int:
    return int(statistics.median(list(values)))


def median_float(values: Any) -> float:
    return round(float(statistics.median(list(values))), 3)


def add_relative_metrics(summary: dict[str, Any], strategy: str, baseline: str) -> None:
    if strategy not in summary or baseline not in summary:
        return
    current = summary[strategy]
    base = summary[baseline]
    current[f"context_reduction_vs_{baseline}"] = reduction_ratio(
        current["median_prompt_context_bytes"],
        base["median_prompt_context_bytes"],
    )
    current[f"files_reduction_vs_{baseline}"] = reduction_ratio(
        current["median_files_opened"],
        base["median_files_opened"],
    )
    current[f"tool_call_reduction_vs_{baseline}"] = reduction_ratio(
        current["median_tool_calls"],
        base["median_tool_calls"],
    )


def reduction_ratio(value: int | float, baseline: int | float) -> float:
    if baseline <= 0:
        return 0.0
    return round(max(0.0, 1 - (value / baseline)), 3)


def evaluate_thresholds(summary: dict[str, Any]) -> dict[str, Any]:
    package = summary.get("package_search", {})
    raw = summary.get("raw_rg", {})
    thresholds = [
        {
            "metric": "package_search.context_reduction_vs_naive_broad_scan",
            "operator": ">=",
            "target": 0.60,
            "actual": package.get("context_reduction_vs_naive_broad_scan"),
        },
        {
            "metric": "package_search.files_reduction_vs_naive_broad_scan",
            "operator": ">=",
            "target": 0.50,
            "actual": package.get("files_reduction_vs_naive_broad_scan"),
        },
        {
            "metric": "package_search.first_pass_hit_rate",
            "operator": ">=",
            "target": 0.66,
            "actual": package.get("first_pass_hit_rate"),
        },
        {
            "metric": "package_search.mean_recall_at_10",
            "operator": ">=",
            "target": 0.80,
            "actual": package.get("mean_recall_at_10"),
        },
        {
            "metric": "package_search.first_pass_hit_rate_minus_raw_rg",
            "operator": ">=",
            "target": 0.0,
            "actual": round(package.get("first_pass_hit_rate", 0) - raw.get("first_pass_hit_rate", 0), 3),
        },
    ]
    for item in thresholds:
        item["passed"] = bool(item["actual"] is not None and item["actual"] >= item["target"])
    return {
        "status": "pass" if all(item["passed"] for item in thresholds) else "fail",
        "purpose": "Gate expensive retrieval features on a large token-efficiency impact without lowering first-pass correctness.",
        "items": thresholds,
    }


def metric_definitions() -> dict[str, str]:
    return {
        "prompt_context_bytes": "Bytes returned to the calling agent as candidate snippets/report context.",
        "estimated_tokens_returned": "Approximate tokens returned to the agent using bytes / 4.",
        "files_opened": "Estimated JSONL files opened by the workflow.",
        "bytes_parsed": "Estimated transcript bytes parsed or scanned before returning candidates.",
        "tool_calls": "Estimated command/tool calls needed for the retrieval attempt.",
        "elapsed_ms": "Deterministic elapsed-time model for comparing strategies within the same fixture.",
        "first_pass_hit": "Whether the strategy can produce the expected outcome from the first candidate set.",
        "metric_basis": "The public fixture uses modeled metrics because committed cases contain private evidence IDs rather than transcript paths/text.",
        "recall_at_1_3_10": "Fraction of sanitized gold refs expected within the top-k candidate set.",
    }


def profile_to_dict(profile: StrategyProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "label": profile.label,
        "description": profile.description,
        "covered_stages": sorted(profile.stages),
        "failure_controls": sorted(profile.failure_controls),
    }
