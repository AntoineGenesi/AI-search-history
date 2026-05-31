from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_contract import build_agent_find_payload, estimate_tokens, reduction_ratio
from .benchmark import (
    REQUIRED_CATEGORIES,
    REQUIRED_FAILURE_MODES,
    REQUIRED_LINEAGE_RELATIONSHIPS,
    REQUIRED_STAGES,
    load_benchmark_fixture,
)
from .masking import MaskContext
from .query import infer_query
from .scanner import search
from .sources import SourcePlan
from .tool_events import scan_tool_events
from .workflow_repeat import build_workflow_repeat_payload


SCHEMA_VERSION = 1
TRIAGE_COLUMNS = [
    "evidence_id",
    "category",
    "stage_hits",
    "failure_modes",
    "source_kind",
    "source_path",
    "source_line",
    "timestamp_utc",
    "role",
    "session",
    "cwd_fingerprint",
    "artifact_hints",
    "query_family",
    "candidate_score",
    "triage_status",
    "reviewer_notes",
]
SAFE_TRIAGE_STATUS = "candidate_needs_context_review"
DEFAULT_BUDGETS = {
    "max_tool_calls": 5,
    "max_files_opened": 10,
    "max_context_bytes": 12000,
    "target_top_k": 5,
}


QUERY_FAMILY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "retrieval-memory": (
        "find where I said",
        "where did we discuss retrieval benchmark evidence",
    ),
    "artifact-anchored": (
        "benchmark fixture report task csv json evidence",
        "private evidence candidates report output",
    ),
    "project-cwd": (
        "project worktree cwd task branch",
        "current project retrieval history",
    ),
    "date-window": (
        "recent benchmark mining yesterday",
        "last week retrieval evidence",
    ),
    "role-filter": (
        "I said retrieval benchmark",
        "you said benchmark evidence",
    ),
    "failure-mode": (
        "wrong root ignored date no role filter",
        "too many tool calls over reading exact phrase miss",
    ),
    "lineage": (
        "source project extracted project sibling project",
        "renamed duplicate project lineage",
    ),
    "skill-audit": (
        "skill usage audit evidence",
        "SKILL.md read helper invocation",
    ),
    "workflow-repeat": (
        "repeat workflow automation suggestion",
        "same process review failed retry",
    ),
}


def build_private_benchmark_mine_payload(
    *,
    source_plan: dict,
    plan: SourcePlan,
    default_tz,
    fixture_path: Path,
    output_dir: Path,
    queries: list[str],
    query_families: list[str],
    target_categories: list[str],
    target_stages: list[str],
    target_failure_modes: list[str],
    candidate_limit: int = 100,
    per_query_max_matches: int = 20,
    context_lines: int = 3,
    include_tool_events: bool = True,
    include_workflow_repeat: bool = True,
    mask_context: MaskContext | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    candidate_limit = max(1, candidate_limit)
    per_query_max_matches = max(1, per_query_max_matches)
    context_lines = max(0, context_lines)
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    unsupported_families = sorted(set(query_families) - set(QUERY_FAMILY_DEFAULTS))
    if unsupported_families:
        warnings.append(f"Unsupported query families skipped: {', '.join(unsupported_families)}")
    normalized_families = [family for family in query_families if family in QUERY_FAMILY_DEFAULTS]
    query_specs = build_query_specs(queries, normalized_families)
    if not query_specs:
        warnings.append("No explicit --query values or supported --query-family defaults were provided.")

    fixture = load_fixture_or_empty(fixture_path, warnings)
    rows: list[dict[str, Any]] = []
    searched_files = 0
    searched_bytes = 0

    search_plan = with_max_matches(plan, per_query_max_matches)
    for query_spec in query_specs:
        if len(rows) >= candidate_limit:
            break
        intent = infer_query(query_spec["query"], now=now_from_source_plan(source_plan, default_tz), role=None)
        result = search(
            search_plan,
            intent,
            default_tz=default_tz,
            cwd=source_plan.get("cwd"),
            mask_context=mask_context,
        )
        searched_files = max(searched_files, int(result.get("searched_files", 0)))
        searched_bytes = max(searched_bytes, int(result.get("searched_bytes", 0)))
        warnings.extend(result.get("warnings", []))
        find_payload = build_agent_find_payload(
            source_plan={**source_plan, "query": intent.to_dict()},
            search_result=result,
            output_format=output_format,
            report_output=report_output,
        )
        for candidate in find_payload["agent_find"].get("find_candidates", []):
            rows.append(
                row_from_find_candidate(
                    candidate,
                    category=category_for_index(len(rows), target_categories),
                    query_family=query_spec["family"],
                )
            )
            if len(rows) >= candidate_limit:
                break

    if include_tool_events and len(rows) < candidate_limit:
        tool_scan = scan_tool_events(plan, default_tz=default_tz, mask_context=mask_context)
        searched_files = max(searched_files, int(tool_scan.get("searched_files", 0)))
        searched_bytes = max(searched_bytes, int(tool_scan.get("searched_bytes", 0)))
        warnings.extend(tool_scan.get("warnings", []))
        for event in tool_scan.get("events", []):
            rows.append(
                row_from_tool_event(
                    event.to_dict(),
                    category=category_for_index(len(rows), target_categories),
                    query_family="structured-tool-events",
                )
            )
            if len(rows) >= candidate_limit:
                break

    workflow_summary: dict[str, Any] = {"enabled": include_workflow_repeat, "cluster_count": 0}
    if include_workflow_repeat and len(rows) < candidate_limit:
        workflow_payload = build_workflow_repeat_payload(
            source_plan=source_plan,
            plan=plan,
            default_tz=default_tz,
            query=" ".join(queries) if queries else " ".join(normalized_families),
            min_instances=2,
            window_records=8,
            cluster_limit=min(12, candidate_limit),
            include_suggestions=True,
            mask_context=mask_context,
            output_format=output_format,
            report_output=report_output,
        )
        workflow_report = workflow_payload.get("workflow_repeat_finder", {})
        workflow_summary["cluster_count"] = workflow_report.get("token_efficiency", {}).get("cluster_count", 0)
        warnings.extend(workflow_payload.get("warnings", []))
        for cluster in workflow_report.get("workflow_clusters", []):
            rows.append(
                row_from_workflow_cluster(
                    cluster,
                    category=category_for_index(len(rows), target_categories),
                    query_family="workflow-repeat",
                )
            )
            if len(rows) >= candidate_limit:
                break

    assign_evidence_ids(rows)
    triage_csv = output_dir / "private-candidate-triage.csv"
    notes_md = output_dir / "private-evidence-candidates.md"
    suggestions_json = output_dir / "sanitized-fixture-suggestions.json"
    coverage_json = output_dir / "coverage-gap-report.json"
    report_json = Path(report_output).expanduser() if report_output else output_dir / "private-benchmark-mine-report.json"

    write_triage_csv(triage_csv, rows)
    write_private_notes(notes_md, rows, context_lines=context_lines)
    suggestions = build_sanitized_fixture_suggestions(
        fixture_path=fixture_path,
        rows=rows,
        target_categories=target_categories,
    )
    coverage = build_coverage_gap_report(
        fixture=fixture,
        rows=rows,
        target_categories=target_categories,
        target_stages=target_stages,
        target_failure_modes=target_failure_modes,
    )
    suggestions_json.write_text(json.dumps(suggestions, indent=2, sort_keys=True), encoding="utf-8")
    coverage_json.write_text(json.dumps(coverage, indent=2, sort_keys=True), encoding="utf-8")

    candidate_summary = summarize_rows(rows)
    inline_summary_bytes = len(json.dumps(candidate_summary, sort_keys=True).encode("utf-8"))
    status = "candidates_found" if rows else "no_candidates"
    checker_command = (
        "PYTHONPATH=src python3 scripts/check_private_benchmark_evidence.py "
        f"--fixture {safe_display_path(fixture_path)} --private-csv {safe_display_path(triage_csv)}"
    )
    public_report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "command_surface": {
            "private-benchmark-mine": "Mines private local transcript candidates into ignored triage artifacts, sanitized fixture suggestions, and coverage-gap reports without mutating benchmark fixtures.",
        },
        "inputs": {
            "query_families": normalized_families,
            "query_count": len(queries),
            "target_categories": target_categories,
            "target_stages": target_stages,
            "target_failure_modes": target_failure_modes,
            "candidate_limit": candidate_limit,
            "per_query_max_matches": per_query_max_matches,
            "context_lines": context_lines,
            "include_tool_events": include_tool_events,
            "include_workflow_repeat": include_workflow_repeat,
            "fixture": safe_display_path(fixture_path),
        },
        "output_files": {
            "private_triage_csv": safe_display_path(triage_csv),
            "private_evidence_notes": safe_display_path(notes_md),
            "sanitized_fixture_suggestions": safe_display_path(suggestions_json),
            "coverage_gap_report": safe_display_path(coverage_json),
            "private_report": safe_display_path(report_json),
        },
        "candidate_summary": candidate_summary,
        "coverage_gap_report": compact_coverage(coverage),
        "sanitized_fixture_suggestions": compact_suggestions(suggestions),
        "privacy_contract": {
            "stdout_raw_private_text_included": False,
            "stdout_local_paths_included": False,
            "triage_csv_private_ignored": True,
            "fixture_suggestions_safe_to_commit_after_review": True,
            "auto_applies_fixture_changes": False,
            "network_calls": False,
        },
        "checker": {
            "command": checker_command,
            "validates_private_refs": True,
            "prints_private_paths": False,
        },
        "token_efficiency": {
            "candidate_count": len(rows),
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "inline_context_bytes": inline_summary_bytes,
        },
        "context_budget": {
            "payload_bytes": inline_summary_bytes,
            "estimated_tokens": estimate_tokens(inline_summary_bytes),
            "raw_baseline_bytes": searched_bytes,
            "estimated_reduction_ratio": reduction_ratio(inline_summary_bytes, searched_bytes),
            "report_written": safe_display_path(report_json),
            "format": output_format,
        },
        "workflow_repeat": workflow_summary,
        "warnings": sanitized_warnings(warnings),
    }
    payload = {
        "private_benchmark_mine": public_report,
        "source_plan": sanitized_source_plan(source_plan),
    }
    private_report = {
        **payload,
        "private_candidate_rows": rows,
        "private_output_dir": str(output_dir),
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(private_report, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def build_query_specs(queries: list[str], families: list[str]) -> list[dict[str, str]]:
    specs = [{"family": "explicit", "query": query} for query in queries if query.strip()]
    for family in families:
        for query in QUERY_FAMILY_DEFAULTS[family]:
            specs.append({"family": family, "query": query})
    return specs


def now_from_source_plan(source_plan: dict, default_tz) -> datetime:
    value = source_plan.get("now")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(default_tz)


def with_max_matches(plan: SourcePlan, max_matches: int) -> SourcePlan:
    limits = replace(plan.limits, max_matches=max_matches)
    return replace(plan, limits=limits)


def row_from_find_candidate(
    candidate: dict,
    *,
    category: str,
    query_family: str,
) -> dict[str, Any]:
    source_ref = candidate.get("source_ref") or {}
    stages = normalize_stages(candidate.get("stages") or [])
    artifacts = sorted(set((candidate.get("artifacts") or []) + (candidate.get("tasks") or [])))
    return {
        "category": category,
        "stage_hits": stages,
        "failure_modes": [],
        "source_kind": "find_candidate",
        "source_path": source_ref.get("path") or candidate.get("path") or "",
        "source_line": int(source_ref.get("line") or candidate.get("line") or 0),
        "timestamp_utc": candidate.get("timestamp_utc"),
        "role": candidate.get("role") or "",
        "session": source_ref.get("session") or candidate.get("session") or "",
        "cwd_fingerprint": fingerprint(candidate.get("cwd")),
        "artifact_hints": artifacts[:12],
        "query_family": query_family,
        "candidate_score": float(candidate.get("score") or 0.0),
        "triage_status": SAFE_TRIAGE_STATUS,
        "reviewer_notes": "Review private source locally; no transcript excerpt copied by miner.",
    }


def row_from_tool_event(
    event: dict,
    *,
    category: str,
    query_family: str,
) -> dict[str, Any]:
    source_ref = event.get("source_ref") or {}
    artifacts = [target.get("value", "") for target in event.get("targets", []) if isinstance(target, dict)]
    artifacts.extend(event.get("output_paths") or [])
    stages = ["newest_first_shortlist"]
    if artifacts:
        stages.append("artifact_path")
    if event.get("cwd") or event.get("project"):
        stages.append("cwd_project")
    if event.get("timestamp_utc"):
        stages.append("date_window")
    stages = normalize_stages(stages)
    return {
        "category": category,
        "stage_hits": stages,
        "failure_modes": [],
        "source_kind": f"tool_event:{event.get('event_type') or 'unknown'}",
        "source_path": source_ref.get("path") or "",
        "source_line": int(source_ref.get("line") or 0),
        "timestamp_utc": event.get("timestamp_utc"),
        "role": event.get("role") or "",
        "session": source_ref.get("session") or "",
        "cwd_fingerprint": fingerprint(event.get("cwd") or event.get("project")),
        "artifact_hints": sorted(set(artifacts))[:12],
        "query_family": query_family,
        "candidate_score": 8.0 if event.get("confidence") == "high" else 5.0,
        "triage_status": SAFE_TRIAGE_STATUS,
        "reviewer_notes": "Structured event candidate; inspect private source locally before fixture changes.",
    }


def row_from_workflow_cluster(
    cluster: dict,
    *,
    category: str,
    query_family: str,
) -> dict[str, Any]:
    instance = (cluster.get("representative_instances") or [{}])[0]
    source_ref = instance.get("source_ref") or {}
    stages = normalize_stages(["semantic_local", "newest_first_shortlist"])
    failure_modes = workflow_failure_modes(cluster)
    return {
        "category": category,
        "stage_hits": stages,
        "failure_modes": failure_modes,
        "source_kind": "workflow_repeat_cluster",
        "source_path": source_ref.get("path") or "",
        "source_line": int(source_ref.get("line") or 0),
        "timestamp_utc": instance.get("timestamp_utc") or cluster.get("last_seen_utc"),
        "role": instance.get("role") or "",
        "session": source_ref.get("session") or "",
        "cwd_fingerprint": fingerprint(cluster.get("project_fingerprint") or cluster.get("label")),
        "artifact_hints": [cluster.get("workflow_kind") or "workflow_repeat"],
        "query_family": query_family,
        "candidate_score": float(cluster.get("score") or 0.0),
        "triage_status": SAFE_TRIAGE_STATUS,
        "reviewer_notes": "Workflow-repeat candidate summarized from structured events; review private refs locally.",
    }


def normalize_stages(stages: list[str]) -> list[str]:
    aliases = {"role": "role_filter", "task_slug": "normalized_terms", "preference": "synonym_paraphrase"}
    normalized = [aliases.get(stage, stage) for stage in stages]
    if normalized and "newest_first_shortlist" not in normalized:
        normalized.append("newest_first_shortlist")
    return sorted(stage for stage in set(normalized) if stage in REQUIRED_STAGES)


def normalize_failure_modes(modes: list[str]) -> list[str]:
    return sorted(mode for mode in set(modes) if mode in REQUIRED_FAILURE_MODES)


def workflow_failure_modes(cluster: dict) -> list[str]:
    metrics = cluster.get("ranking_metrics") if isinstance(cluster, dict) else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    modes: list[str] = []
    if int(metrics.get("tool_call_cost") or 0) >= 3:
        modes.append("too_many_tool_calls")
    if int(metrics.get("manual_repetition") or 0) or int(metrics.get("frequency") or 0) >= 2:
        modes.append("broad_recursive_walk")
    return normalize_failure_modes(modes)


def category_for_index(index: int, target_categories: list[str]) -> str:
    valid = [category for category in target_categories if category in REQUIRED_CATEGORIES]
    if not valid:
        return "success"
    return valid[index % len(valid)]


def assign_evidence_ids(rows: list[dict[str, Any]]) -> None:
    counts: Counter[str] = Counter()
    for row in rows:
        category_slug = str(row["category"]).replace("_", "-")
        counts[category_slug] += 1
        row["evidence_id"] = f"real-jsh013-{category_slug}-{counts[category_slug]:03d}"


def write_triage_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRIAGE_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(csv_row(row))


def csv_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        "evidence_id": str(row.get("evidence_id") or ""),
        "category": str(row.get("category") or ""),
        "stage_hits": join_values(row.get("stage_hits") or []),
        "failure_modes": join_values(row.get("failure_modes") or []),
        "source_kind": str(row.get("source_kind") or ""),
        "source_path": str(row.get("source_path") or ""),
        "source_line": str(row.get("source_line") or ""),
        "timestamp_utc": str(row.get("timestamp_utc") or ""),
        "role": str(row.get("role") or ""),
        "session": str(row.get("session") or ""),
        "cwd_fingerprint": str(row.get("cwd_fingerprint") or ""),
        "artifact_hints": join_values(row.get("artifact_hints") or []),
        "query_family": str(row.get("query_family") or ""),
        "candidate_score": str(row.get("candidate_score") or 0),
        "triage_status": str(row.get("triage_status") or SAFE_TRIAGE_STATUS),
        "reviewer_notes": str(row.get("reviewer_notes") or ""),
    }


def write_private_notes(path: Path, rows: list[dict[str, Any]], *, context_lines: int) -> None:
    lines = [
        "# Private Benchmark Mine Evidence Candidates",
        "",
        "These notes intentionally omit transcript excerpts. Use the local path/line refs in the private CSV for review.",
        f"Context lines requested for reviewer lookup: {context_lines}",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"## {row.get('evidence_id')}",
                "",
                f"- Category: {row.get('category')}",
                f"- Source kind: {row.get('source_kind')}",
                f"- Local ref: {row.get('source_path')}:{row.get('source_line')}",
                f"- Stage hits: {', '.join(row.get('stage_hits') or []) or 'none'}",
                f"- Failure modes: {', '.join(row.get('failure_modes') or []) or 'none'}",
                f"- Triage: {row.get('triage_status')}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_sanitized_fixture_suggestions(
    *,
    fixture_path: Path,
    rows: list[dict[str, Any]],
    target_categories: list[str],
) -> dict[str, Any]:
    suggested_cases = []
    for index, row in enumerate(rows[:20], 1):
        stage = first_or_default(row.get("stage_hits") or [], "normalized_terms")
        category = row.get("category") or first_or_default(target_categories, "success")
        suggested_cases.append(
            {
                "case_id": f"retrieval_{str(category).replace('_', '-')}_{stage}_{index:03d}".replace("-", "_"),
                "category": category,
                "evidence_status": "private_mined",
                "query_paraphrase": "Paraphrased private retrieval ask; review local evidence before committing.",
                "transcript_pattern_paraphrase": "Paraphrased private source pattern with no exact transcript text.",
                "candidate_generation_stages": list(row.get("stage_hits") or [stage]),
                "failure_modes": list(row.get("failure_modes") or []),
                "expected_outcome": expected_outcome_for_category(str(category)),
                "budgets": dict(DEFAULT_BUDGETS),
                "sanitized_gold_refs": [
                    {
                        "ref_id": f"private-evidence-candidates.md#{row.get('evidence_id')}",
                        "ref_kind": "private_local_path_line",
                    }
                ],
                "private_triage_status": row.get("triage_status") or SAFE_TRIAGE_STATUS,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "private-benchmark-mine",
        "fixture": safe_display_path(fixture_path),
        "privacy": {
            "raw_private_text_included": False,
            "local_paths_included": False,
            "suggestions_safe_to_commit_after_review": True,
        },
        "suggested_cases": suggested_cases,
        "patch_preview": {
            "target_file": safe_display_path(fixture_path),
            "mode": "manual_review_required",
            "notes": ["Do not auto-apply until private refs are checked locally."],
        },
    }


def build_coverage_gap_report(
    *,
    fixture: dict[str, Any],
    rows: list[dict[str, Any]],
    target_categories: list[str],
    target_stages: list[str],
    target_failure_modes: list[str],
) -> dict[str, Any]:
    fixture_cases = fixture.get("cases") if isinstance(fixture, dict) else []
    fixture_cases = fixture_cases if isinstance(fixture_cases, list) else []
    followup = fixture.get("coverage_needs_followup") if isinstance(fixture, dict) else {}
    followup = followup if isinstance(followup, dict) else {}

    fixture_private = [case for case in fixture_cases if case.get("evidence_status") == "private_mined"]
    fixture_followup = [case for case in fixture_cases if case.get("evidence_status") == "needs_followup"]
    private_categories = {case.get("category") for case in fixture_private} | {row.get("category") for row in rows}
    private_stages = set().union(
        *(set(case.get("candidate_generation_stages") or []) for case in fixture_private),
        *(set(row.get("stage_hits") or []) for row in rows),
    )
    private_modes = set().union(
        *(set(case.get("failure_modes") or []) for case in fixture_private),
        *(set(row.get("failure_modes") or []) for row in rows),
    )
    followup_stages = set(followup.get("candidate_generation_stages") or []) | set().union(
        *(set(case.get("candidate_generation_stages") or []) for case in fixture_followup)
    )
    followup_modes = set(followup.get("failure_modes") or []) | set().union(*(set(case.get("failure_modes") or []) for case in fixture_followup))

    missing_categories = sorted(REQUIRED_CATEGORIES - {str(item) for item in private_categories if item})
    missing_stages = sorted(REQUIRED_STAGES - private_stages - followup_stages)
    missing_modes = sorted(REQUIRED_FAILURE_MODES - private_modes - followup_modes)
    weak_categories = sorted(category for category in REQUIRED_CATEGORIES if count_category(rows, category) == 0)
    review_needed = sorted(row["evidence_id"] for row in rows if row.get("triage_status") == SAFE_TRIAGE_STATUS)
    return {
        "schema_version": SCHEMA_VERSION,
        "required": {
            "categories": sorted(REQUIRED_CATEGORIES),
            "candidate_generation_stages": sorted(REQUIRED_STAGES),
            "failure_modes": sorted(REQUIRED_FAILURE_MODES),
            "lineage_relationships": sorted(REQUIRED_LINEAGE_RELATIONSHIPS),
        },
        "private_mined_counts": {
            "rows": len(rows),
            "categories": dict(Counter(str(row.get("category") or "") for row in rows)),
            "stages": dict(Counter(str(stage) for row in rows for stage in row.get("stage_hits", []))),
            "failure_modes": dict(Counter(str(mode) for row in rows for mode in row.get("failure_modes", []))),
        },
        "missing_categories": missing_categories,
        "missing_stages": missing_stages,
        "missing_failure_modes": missing_modes,
        "weak_categories": weak_categories,
        "supported_only_by_needs_followup": {
            "stages": sorted((REQUIRED_STAGES & followup_stages) - private_stages),
            "failure_modes": sorted((REQUIRED_FAILURE_MODES & followup_modes) - private_modes),
        },
        "candidate_needs_context_review": review_needed,
        "targeted_labels": {
            "categories": target_categories,
            "stages": target_stages,
            "failure_modes": target_failure_modes,
        },
        "suggested_next_mining_passes": next_mining_passes(missing_categories, missing_stages, missing_modes),
    }


def compact_coverage(coverage: dict[str, Any]) -> dict[str, Any]:
    return {
        "missing_categories": coverage.get("missing_categories", []),
        "missing_stages": coverage.get("missing_stages", []),
        "missing_failure_modes": coverage.get("missing_failure_modes", []),
        "weak_categories": coverage.get("weak_categories", []),
        "candidate_needs_context_review_count": len(coverage.get("candidate_needs_context_review", [])),
        "suggested_next_mining_passes": coverage.get("suggested_next_mining_passes", [])[:5],
    }


def compact_suggestions(suggestions: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": suggestions.get("schema_version"),
        "suggested_case_count": len(suggestions.get("suggested_cases") or []),
        "patch_preview": suggestions.get("patch_preview", {}),
        "privacy": suggestions.get("privacy", {}),
    }


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_count": len(rows),
        "by_category": dict(Counter(str(row.get("category") or "") for row in rows)),
        "by_stage": dict(Counter(str(stage) for row in rows for stage in row.get("stage_hits", []))),
        "by_failure_mode": dict(Counter(str(mode) for row in rows for mode in row.get("failure_modes", []))),
        "by_source_kind": dict(Counter(str(row.get("source_kind") or "") for row in rows)),
    }


def sanitized_source_plan(source_plan: dict) -> dict[str, Any]:
    sources = source_plan.get("sources", {})
    return {
        "query": {
            "original": "<private-mining-query-set>",
            "terms_count": len(source_plan.get("query", {}).get("terms", [])) if isinstance(source_plan.get("query"), dict) else 0,
        },
        "sources": {
            "roots_selected": len(sources.get("roots", [])),
            "rejected_roots": [
                {"path": "<redacted-root>", "reason": item.get("reason", "")}
                for item in sources.get("rejected_roots", [])
                if isinstance(item, dict)
            ],
            "limits": sources.get("limits", {}),
        },
        "now": source_plan.get("now"),
    }


def safe_display_path(path: Path | str) -> str:
    text = str(path)
    normalized = text.replace("\\", "/")
    marker = "/.private/"
    if marker in normalized:
        return ".private/" + normalized.split(marker, 1)[1]
    if normalized.startswith(".private/") or normalized.startswith("benchmarks/"):
        return normalized
    name = Path(text).name
    return f"<private-output>/{name}" if name else "<private-output>"


def sanitized_warnings(warnings: list[str]) -> list[str]:
    cleaned = []
    for warning in sorted(set(warnings)):
        text = str(warning)
        text = text.replace(str(Path.home()), "<home>")
        text = text.replace("\\", "/")
        parts = []
        for token in text.split():
            if token.startswith("/") or token.startswith("~") or "/.private/" in token:
                parts.append("<path>")
            else:
                parts.append(token)
        cleaned.append(" ".join(parts))
    return cleaned


def fingerprint(value: Any) -> str:
    if not value:
        return ""
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]
    return f"cwd-{digest}"


def load_fixture_or_empty(path: Path, warnings: list[str]) -> dict[str, Any]:
    try:
        return load_benchmark_fixture(path)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Could not load benchmark fixture metadata: {type(exc).__name__}")
        return {}


def first_or_default(values: list[str], default: str) -> str:
    return values[0] if values else default


def expected_outcome_for_category(category: str) -> str:
    return {
        "success": "gold_evidence",
        "partial_ambiguous": "ambiguous_evidence_with_uncertainty",
        "no_evidence": "not_found_yet",
        "failure": "retrieval_failure_case",
    }.get(category, "gold_evidence")


def count_category(rows: list[dict[str, Any]], category: str) -> int:
    return sum(1 for row in rows if row.get("category") == category)


def next_mining_passes(missing_categories: list[str], missing_stages: list[str], missing_modes: list[str]) -> list[dict[str, Any]]:
    passes: list[dict[str, Any]] = []
    for category in missing_categories[:4]:
        passes.append(
            {"query_family": "retrieval-memory", "target_category": category, "reason": "missing private-mined category"}
        )
    for stage in missing_stages[:6]:
        family = {
            "artifact_path": "artifact-anchored",
            "cwd_project": "project-cwd",
            "date_window": "date-window",
            "role_filter": "role-filter",
            "semantic_local": "retrieval-memory",
            "newest_first_shortlist": "retrieval-memory",
        }.get(stage, "retrieval-memory")
        passes.append({"query_family": family, "target_stage": stage, "reason": "missing stage coverage"})
    for mode in missing_modes[:6]:
        passes.append(
            {"query_family": "failure-mode", "target_failure_mode": mode, "reason": "missing failure-mode coverage"}
        )
    return passes


def join_values(values: list[Any]) -> str:
    cleaned = [str(value) for value in values if str(value)]
    return ";".join(cleaned) if cleaned else "none"
