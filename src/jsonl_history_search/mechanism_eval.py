from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .generated_metadata import build_generated_metadata_discovery_report
from .impact_benchmark import DEFAULT_FIXTURE, DEFAULT_SYNTHETIC_ROOT, validate_impact_fixture
from .metric_contract import add_metric_contract, impact_benchmark_metrics
from .query import infer_query
from .records import parse_record
from .scanner import search
from .sources import Limits, SourcePlan, build_source_plan, enumerate_jsonl_files
from .timeutil import parse_now, timezone_from_args


DEFAULT_BASELINES = ("package_search", "package_search_rg_prefilter")
METADATA_SUPPLEMENT_BASELINE = "package_search_metadata_supplement"
SAFE_METADATA_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,48}")


def build_mechanism_eval_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    fixture_path = Path(args.fixture).expanduser()
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    except OSError:
        return {"mechanism_eval": _invalid_report("fixture could not be read", started)}
    except json.JSONDecodeError:
        return {"mechanism_eval": _invalid_report("fixture is not valid JSON", started)}
    fixture_errors = validate_impact_fixture(fixture)
    if fixture_errors:
        return {"mechanism_eval": _invalid_report("fixture validation failed", started, fixture_errors)}

    roots = [Path(item).expanduser() for item in args.root] if args.root else _default_fixture_roots(fixture)
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files or args.max_files,
        hard_cap_bytes=args.hard_cap_bytes or args.max_bytes,
    )
    cwd = Path(args.cwd).expanduser().resolve()
    plan = build_source_plan(
        cwd=cwd,
        codex_home=Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex",
        claude_home=Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude",
        roots=roots,
        allow_arbitrary_root=bool(args.allow_arbitrary_root or not args.root),
        limits=limits,
    )
    if plan.rejected_roots or not plan.roots:
        report = _base_report(fixture, limits, started)
        report["status"] = "root_rejected"
        report["warnings"] = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
        report["total_wall_time_ms"] = _elapsed_ms(started)
        return {"mechanism_eval": _add_metrics(report)}

    files = _enumerate_files(plan)
    doc_index = _build_doc_index(files, timezone_from_args(args.timezone, args.timezone_offset))
    metadata = build_generated_metadata_discovery_report(
        plan,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        min_count=args.metadata_min_count,
        max_candidates=args.metadata_max_candidates,
    )["generated_metadata_discovery"]
    metadata_summary = _metadata_summary(metadata)
    rows = []
    baselines = _selected_baselines(args)
    cases = fixture["cases"][: args.case_limit if args.case_limit > 0 else None]
    for case in cases:
        for baseline in baselines:
            rows.append(
                _run_search_row(
                    case=case,
                    baseline=baseline,
                    plan=plan,
                    args=args,
                    doc_index=doc_index,
                    metadata_summary=metadata_summary,
                )
            )
    _annotate_metadata_supplement_outcomes(rows)
    report = _base_report(fixture, limits, started)
    report["status"] = "ok"
    report["source_summary"] = {
        "selected_root_count": len(plan.roots),
        "file_count": len(files),
        "source_kinds": sorted({item["source_kind"] for item in files}),
    }
    report["metadata_discovery"] = metadata_summary
    report["rows"] = rows
    report["summary"] = _summarize(rows)
    report["mechanism_eval_gate"] = _mechanism_gate(rows, metadata_summary)
    report["total_wall_time_ms"] = _elapsed_ms(started)
    return {"mechanism_eval": _add_metrics(report)}


def _default_fixture_roots(fixture: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for source_set in fixture.get("source_sets", []):
        for root in source_set.get("fixture_roots", []):
            roots.append(Path(root))
    return roots or [DEFAULT_SYNTHETIC_ROOT]


def _base_report(fixture: dict[str, Any], limits: Limits, started: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "pending",
        "data_source": "synthetic_committed_tiny_slice",
        "fixture_case_count": len(fixture.get("cases", [])),
        "limits": limits.to_dict(),
        "baselines": {
            "package_search": "current Python JSONL package search path",
            "package_search_rg_prefilter": "optional rg prefilter before the same Python JSONL evidence parser",
            "generated_metadata": "report-only candidate counters; not consumed by default search or ranking",
            METADATA_SUPPLEMENT_BASELINE: (
                "opt-in tiny-eval baseline that appends sanitized generated metadata kind/label terms to the query "
                "before calling the same Python JSONL package search path"
            ),
        },
        "privacy_contract": {
            "raw_private_text_committed": False,
            "raw_source_paths_committed": False,
            "raw_session_ids_committed": False,
            "local_usernames_committed": False,
        },
        "promotion_boundary": {
            "default_search_uses_rg_prefilter": False,
            "default_search_uses_generated_metadata": False,
            "sidecar_or_index_created": False,
            "background_watcher_created": False,
            "ranking_change_claimed": False,
            "token_efficiency_claimed": False,
            "final_answer_quality_claimed": False,
            "product_value_claimed": False,
        },
        "warnings": [],
        "rows": [],
        "summary": {},
        "mechanism_eval_gate": {
            "status": "not_evaluated",
            "reasons": ["eval_not_run_yet"],
            "evidence_basis": "synthetic_committed_tiny_slice",
        },
        "total_wall_time_ms": _elapsed_ms(started),
    }


def _invalid_report(reason: str, started: float, fixture_errors: list[str] | None = None) -> dict[str, Any]:
    report = _base_report({"cases": []}, Limits(), started)
    report["status"] = "invalid_fixture"
    report["warnings"] = [reason]
    report["fixture_errors"] = fixture_errors or []
    report["mechanism_eval_gate"] = {
        "status": "not_evaluated",
        "reasons": ["invalid_fixture", "no_eval_rows"],
        "evidence_basis": "synthetic_committed_tiny_slice",
    }
    return _add_metrics(report)


def _enumerate_files(plan: SourcePlan) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path, size, _warnings in enumerate_jsonl_files(plan):
        files.append({"path": path, "size": size, "source_kind": _source_kind(path, plan)})
    return files


def _source_kind(path: Path, plan: SourcePlan) -> str:
    from .sources import source_kind_for_path

    return source_kind_for_path(path, plan)


def _build_doc_index(files: list[dict[str, Any]], default_tz) -> dict[tuple[str, int], str]:
    index: dict[tuple[str, int], str] = {}
    for item in files:
        path = item["path"]
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
            doc_id = record.raw.get("doc_id") if isinstance(record.raw, dict) else None
            if isinstance(doc_id, str) and doc_id:
                index[(str(path), line_number)] = doc_id
    return index


def _metadata_summary(report: dict[str, Any]) -> dict[str, Any]:
    candidates = report.get("candidates", [])
    return {
        "status": report.get("status"),
        "candidate_count": len(candidates),
        "candidate_kinds": sorted({candidate.get("kind") for candidate in candidates if candidate.get("kind")}),
        "top_candidates": [
            {
                "kind": candidate.get("kind"),
                "label": candidate.get("label"),
                "confidence": candidate.get("confidence"),
                "entry_count": candidate.get("evidence", {}).get("entry_count", 0),
                "raw_value_count": candidate.get("evidence", {}).get("raw_value_count", 0),
            }
            for candidate in candidates[:5]
        ],
        "supplement": {
            "term_count": len(_metadata_supplement_terms(report)),
            "terms_are_sanitized_kind_label_only": True,
            "raw_values_used": False,
            "source_paths_used": False,
        },
        "promotion_boundary": report.get("promotion_boundary", {}),
    }


def _run_search_row(
    *,
    case: dict[str, Any],
    baseline: str,
    plan: SourcePlan,
    args,
    doc_index: dict[tuple[str, int], str],
    metadata_summary: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    query_text = _case_query(case)
    supplement_terms = _metadata_supplement_terms_for_baseline(baseline, metadata_summary)
    metadata_supplement = _metadata_supplement_for_baseline(
        baseline,
        metadata_summary,
        term_count=len(supplement_terms),
    )
    if metadata_supplement["enabled"] and supplement_terms:
        query_text = f"{query_text} {' '.join(supplement_terms)}"
    intent = infer_query(query_text, now=parse_now(args.now, default_tz))
    use_rg = baseline == "package_search_rg_prefilter"
    result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        use_rg_prefilter=use_rg,
    )
    doc_ids = _top_doc_ids(result, doc_index)
    expected = set(case.get("gold_doc_ids") or [])
    expected_outcome = case.get("expected_outcome")
    recall_at_1 = _recall(doc_ids[:1], expected)
    recall_at_3 = _recall(doc_ids[:3], expected)
    no_evidence_correct = expected_outcome == "no_evidence" and not doc_ids
    source_ref_status = "not_expected"
    if expected_outcome == "evidence_found":
        if recall_at_1:
            source_ref_status = "top1_expected_ref"
        elif recall_at_3:
            source_ref_status = "top3_expected_ref"
        else:
            source_ref_status = "expected_ref_not_found"
    elif no_evidence_correct:
        source_ref_status = "no_evidence_correct"
    row = {
        "case_id": case["case_id"],
        "baseline": baseline,
        "query_wording": "synthetic_case_wording_001",
        "expected_outcome": expected_outcome,
        "source_ref_status": source_ref_status,
        "top_candidate_doc_ids": doc_ids[:5],
        "candidate_count": len(doc_ids),
        "recall_at_1": recall_at_1,
        "recall_at_3": recall_at_3,
        "no_evidence_correct": no_evidence_correct,
        "searched_files": int(result.get("searched_files", 0)),
        "searched_bytes": int(result.get("searched_bytes", 0)),
        "elapsed_ms": _elapsed_ms(started),
        "metadata_candidate_count": metadata_summary["candidate_count"],
        "metadata_candidate_kinds": metadata_summary["candidate_kinds"],
        "metadata_status": metadata_summary["status"],
        "metadata_supplement": metadata_supplement,
        "metadata_supplement_outcome": "not_applicable",
        "rg_prefilter": _sanitize_prefilter(result.get("prefilter") if use_rg else None, enabled=use_rg),
    }
    row["case_status"] = _case_status(row)
    return row


def _case_query(case: dict[str, Any]) -> str:
    return str((case.get("allowed_query_wordings") or [""])[0])


def _selected_baselines(args) -> tuple[str, ...]:
    if bool(getattr(args, "include_metadata_supplement", False)):
        return DEFAULT_BASELINES + (METADATA_SUPPLEMENT_BASELINE,)
    return DEFAULT_BASELINES


def _metadata_supplement_terms_for_baseline(baseline: str, metadata_summary: dict[str, Any]) -> list[str]:
    if baseline != METADATA_SUPPLEMENT_BASELINE:
        return []
    return _metadata_supplement_terms_from_summary(metadata_summary)


def _metadata_supplement_for_baseline(
    baseline: str,
    metadata_summary: dict[str, Any],
    *,
    term_count: int,
) -> dict[str, Any]:
    enabled = baseline == METADATA_SUPPLEMENT_BASELINE
    return {
        "enabled": enabled,
        "term_count": term_count,
        "candidate_count": metadata_summary.get("candidate_count", 0) if enabled else 0,
        "candidate_kinds": metadata_summary.get("candidate_kinds", []) if enabled else [],
        "terms_source": "sanitized_generated_metadata_kind_and_label" if enabled else None,
        "terms_emitted": False,
        "raw_values_used": False,
        "source_paths_used": False,
    }


def _metadata_supplement_terms(report: dict[str, Any]) -> list[str]:
    candidates = report.get("candidates", [])
    summary = {"top_candidates": candidates[:5]}
    return _metadata_supplement_terms_from_summary(summary)


def _metadata_supplement_terms_from_summary(metadata_summary: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for candidate in metadata_summary.get("top_candidates", []):
        for raw in (candidate.get("kind"), candidate.get("label")):
            for term in _safe_metadata_terms(raw):
                if term not in seen:
                    seen.add(term)
                    terms.append(term)
                if len(terms) >= 12:
                    return terms
    return terms


def _safe_metadata_terms(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    normalized = value.lower().replace("_", " ").replace("/", " ")
    matches = SAFE_METADATA_TERM_RE.findall(normalized)
    safe = []
    for match in matches:
        term = match.strip(".-_")
        if len(term) >= 2:
            safe.append(term)
    return safe


def _top_doc_ids(result: dict[str, Any], doc_index: dict[tuple[str, int], str]) -> list[str]:
    ids: list[str] = []
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            line = match.get("line")
            try:
                line_number = int(line)
            except (TypeError, ValueError):
                continue
            doc_id = doc_index.get((str(match.get("path")), line_number))
            if doc_id and doc_id not in ids:
                ids.append(doc_id)
    return ids


def _recall(doc_ids: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.0
    return round(len(set(doc_ids) & expected) / len(expected), 3)


def _sanitize_prefilter(stats: dict[str, Any] | None, *, enabled: bool) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {
            "enabled": bool(enabled),
            "status": "disabled" if not enabled else "not_attempted",
            "fallback_reason": None,
            "needle_policy": None,
            "needle_count": 0,
            "anchor_kind_counts": {},
            "files_reduced": 0,
            "bytes_reduced": 0,
        }
    return {
        "enabled": bool(stats.get("enabled")),
        "status": str(stats.get("status") or "unknown"),
        "fallback_reason": stats.get("fallback_reason"),
        "needle_policy": stats.get("needle_policy"),
        "needle_count": int(stats.get("needle_count", 0)),
        "anchor_kind_counts": _safe_count_dict(stats.get("anchor_kind_counts")),
        "files_reduced": int(stats.get("files_reduced", 0)),
        "bytes_reduced": int(stats.get("bytes_reduced", 0)),
        "files_reduction_ratio": float(stats.get("files_reduction_ratio", 0.0)),
        "bytes_reduction_ratio": float(stats.get("bytes_reduction_ratio", 0.0)),
    }


def _case_status(row: dict[str, Any]) -> str:
    if row["expected_outcome"] == "no_evidence":
        return "pass" if row["no_evidence_correct"] else "fail"
    if row["recall_at_1"]:
        return "pass"
    if row["recall_at_3"]:
        return "partial"
    return "fail"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for baseline in DEFAULT_BASELINES + (METADATA_SUPPLEMENT_BASELINE,):
        selected = [row for row in rows if row["baseline"] == baseline]
        if not selected:
            continue
        summary[baseline] = {
            "row_count": len(selected),
            "pass_count": sum(1 for row in selected if row["case_status"] == "pass"),
            "partial_count": sum(1 for row in selected if row["case_status"] == "partial"),
            "fail_count": sum(1 for row in selected if row["case_status"] == "fail"),
            "total_searched_files": sum(row["searched_files"] for row in selected),
            "total_searched_bytes": sum(row["searched_bytes"] for row in selected),
            "rg_needle_policy_counts": _policy_counts(selected),
            "rg_anchor_kind_counts": _anchor_kind_counts(selected),
            "rg_fallback_reason_counts": _fallback_reason_counts(selected),
            "metadata_candidate_count_max": max((row["metadata_candidate_count"] for row in selected), default=0),
            "metadata_supplement_term_count_max": max(
                (row.get("metadata_supplement", {}).get("term_count", 0) for row in selected),
                default=0,
            ),
            "metadata_supplement_outcome_counts": _outcome_counts(selected),
        }
    return summary


def _annotate_metadata_supplement_outcomes(rows: list[dict[str, Any]]) -> None:
    baseline_by_case = {
        row["case_id"]: row
        for row in rows
        if row.get("baseline") == "package_search"
    }
    for row in rows:
        if row.get("baseline") != METADATA_SUPPLEMENT_BASELINE:
            row["metadata_supplement_outcome"] = "not_applicable"
            continue
        baseline = baseline_by_case.get(row["case_id"])
        row["metadata_supplement_outcome"] = _metadata_supplement_outcome(baseline, row)


def _metadata_supplement_outcome(baseline: dict[str, Any] | None, supplemented: dict[str, Any]) -> str:
    if baseline is None:
        return "inconclusive"
    if supplemented.get("metadata_supplement", {}).get("term_count", 0) <= 0:
        return "inconclusive"
    baseline_rank = _status_rank(baseline.get("case_status"))
    supplement_rank = _status_rank(supplemented.get("case_status"))
    if baseline_rank < 0 or supplement_rank < 0:
        return "inconclusive"
    if supplement_rank > baseline_rank:
        return "helped"
    if supplement_rank < baseline_rank:
        return "harmed"
    baseline_quality = (baseline.get("recall_at_1", 0.0), baseline.get("recall_at_3", 0.0))
    supplement_quality = (supplemented.get("recall_at_1", 0.0), supplemented.get("recall_at_3", 0.0))
    if supplement_quality > baseline_quality:
        return "helped"
    if supplement_quality < baseline_quality:
        return "harmed"
    return "neutral"


def _status_rank(status: Any) -> int:
    return {"fail": 0, "partial": 1, "pass": 2}.get(str(status), -1)


def _outcome_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        outcome = row.get("metadata_supplement_outcome")
        if outcome in {"helped", "neutral", "harmed", "inconclusive"}:
            counts[str(outcome)] = counts.get(str(outcome), 0) + 1
    return counts


def _policy_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        policy = row.get("rg_prefilter", {}).get("needle_policy")
        if policy:
            counts[str(policy)] = counts.get(str(policy), 0) + 1
    return counts


def _anchor_kind_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for kind, count in (row.get("rg_prefilter", {}).get("anchor_kind_counts") or {}).items():
            counts[str(kind)] = counts.get(str(kind), 0) + int(count)
    return counts


def _fallback_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("rg_prefilter", {}).get("fallback_reason")
        if reason:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


def _safe_count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key, count in value.items():
        label = str(key)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,48}", label):
            continue
        try:
            safe[label] = int(count)
        except (TypeError, ValueError):
            continue
    return safe


def _mechanism_gate(rows: list[dict[str, Any]], metadata_summary: dict[str, Any]) -> dict[str, Any]:
    reasons = ["synthetic_tiny_slice_cannot_promote"]
    if not rows:
        reasons.append("no_eval_rows")
    if metadata_summary.get("candidate_count", 0) <= 0:
        reasons.append("metadata_candidates_absent_or_below_threshold")
    if any(row["baseline"] == "package_search_rg_prefilter" for row in rows):
        reasons.append("rg_prefilter_measured_only_as_optional_variant")
    if any(row["baseline"] == METADATA_SUPPLEMENT_BASELINE for row in rows):
        reasons.append("metadata_supplement_measured_only_as_opt_in_variant")
    return {
        "status": "inconclusive",
        "reasons": reasons,
        "evidence_basis": "synthetic_committed_tiny_slice",
        "promotion_allowed": False,
        "learning_reusable": True,
    }


def _safe_warnings(warnings: list[str]) -> list[str]:
    safe = []
    for warning in warnings:
        lowered = str(warning).lower()
        if "dangerous broad root" in lowered:
            safe.append("root_rejected_dangerous_broad_root")
        elif "mounted drive root" in lowered:
            safe.append("root_rejected_mounted_drive_root")
        elif "repo root" in lowered:
            safe.append("root_rejected_repo_root")
        else:
            safe.append("root_warning_sanitized")
    return sorted(set(safe))


def _add_metrics(report: dict[str, Any]) -> dict[str, Any]:
    compatibility = {
        "rows": [
            {
                "bytes_parsed": row.get("searched_bytes", 0),
                "output_context_bytes": 0,
                "searched_files": row.get("searched_files", 0),
                "searched_bytes": row.get("searched_bytes", 0),
            }
            for row in report.get("rows", [])
        ]
    }
    metric_source = {**report, **compatibility}
    metrics = impact_benchmark_metrics(metric_source)
    report["metric_contract"] = add_metric_contract({}, metrics)["metric_contract"]
    report["performance_metrics"] = metrics
    report["performance_metrics"]["workload"] = "mechanism_eval_tiny_slice"
    return report


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
