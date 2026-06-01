from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark import load_benchmark_fixture, validate_benchmark_fixture
from .query import infer_query
from .scanner import search
from .sources import Limits, SourcePlan, build_source_plan, dangerous_reason, safe_resolve
from .timeutil import parse_now, timezone_from_args


DEFAULT_FIXTURE = Path("benchmarks/retrieval_gold/sanitized_cases.json")
GATE_STATUSES = {"pass", "fail", "inconclusive", "not_evaluated"}


@dataclass
class MeasuredProbe:
    query_text: str
    row_id: str
    query_source: str


def build_measured_benchmark_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    run_id = _run_id()
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    root_items = [Path(item).expanduser() for item in args.root]
    root_labels = [f"root_{index + 1:03d}" for index, _root in enumerate(root_items)]

    base_report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "measured_local",
        "data_source": "live_local_roots",
        "status": "pending",
        "root_labels": root_labels,
        "limits": _limits_payload(limits, case_limit=args.case_limit),
        "rows": [],
        "aggregate": _empty_aggregate(run_id, limits, root_labels, args.case_limit),
        "ranking_quality_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "rg_prefilter_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "privacy_contract": _privacy_contract(),
    }

    preflight_warnings = _preflight_root_warnings(root_items, cwd)
    if not root_items:
        return _finish_without_scan(
            base_report,
            status="no_roots",
            warnings=["no explicit --root supplied; measured benchmark did not scan"],
            started=started,
        )
    if preflight_warnings:
        return _finish_without_scan(base_report, status="root_rejected", warnings=preflight_warnings, started=started)

    plan = build_source_plan(
        cwd=cwd,
        codex_home=Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex",
        claude_home=Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude",
        roots=root_items,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    if not plan.roots:
        warnings = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
        return _finish_without_scan(
            base_report,
            status="no_roots",
            warnings=warnings or ["no roots selected; measured benchmark did not scan"],
            started=started,
        )

    probes, probe_errors = _load_probes(args)
    if probe_errors:
        return _finish_without_scan(base_report, status="invalid_fixture", warnings=probe_errors, started=started)
    if not probes:
        return _finish_without_scan(
            base_report,
            status="no_queries",
            warnings=["no benchmark probes selected; measured benchmark did not scan"],
            started=started,
        )

    rows: list[dict[str, Any]] = []
    for probe in probes:
        row = _run_probe(
            probe=probe,
            run_id=run_id,
            plan=plan,
            default_tz=tz,
            now=now,
            cwd=str(cwd),
            use_rg_prefilter=getattr(args, "rg_prefilter", False),
        )
        rows.append(row)

    aggregate = _aggregate_rows(
        rows=rows,
        run_id=run_id,
        limits=limits,
        root_labels=root_labels,
        case_limit=args.case_limit,
        total_elapsed_ms=_elapsed_ms(started),
    )
    base_report["status"] = aggregate["overall_status"]
    base_report["rows"] = rows
    base_report["aggregate"] = aggregate
    base_report.update(_evaluate_gate_statuses(rows, aggregate))
    return {"measured_benchmark": base_report}


def _run_probe(
    *,
    probe: MeasuredProbe,
    run_id: str,
    plan: SourcePlan,
    default_tz,
    now,
    cwd: str,
    use_rg_prefilter: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    intent = infer_query(probe.query_text, now=now)
    result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        use_rg_prefilter=use_rg_prefilter,
    )
    warnings = _safe_warnings(result.get("warnings", []))
    candidate_count = sum(len(group.get("matches", [])) for group in result.get("groups", []))
    prefilter = _prefilter_row_stats(result, enabled=use_rg_prefilter)
    cap_hit_labels = _cap_hit_labels(warnings)
    row = {
        "run_id": run_id,
        "case_id": probe.row_id,
        "query_source": probe.query_source,
        "elapsed_ms": _elapsed_ms(started),
        "wall_time_ms": 0,
        "tool_calls": 1,
        "subprocess_calls": 1 if prefilter.get("attempted") else 0,
        "searched_files": int(result.get("searched_files", 0)),
        "files_opened": int(result.get("searched_files", 0)),
        "python_opened_files": int(result.get("searched_files", 0)),
        "searched_bytes": int(result.get("searched_bytes", 0)),
        "bytes_scanned": int(result.get("searched_bytes", 0)),
        "bytes_parsed": int(result.get("searched_bytes", 0)),
        "candidate_count": candidate_count,
        "first_pass_hit": None,
        "recall_at_1": None,
        "recall_at_3": None,
        "recall_at_10": None,
        "mrr": None,
        "no_evidence_correct": None,
        "caps_hit": bool(cap_hit_labels),
        "cap_hit_labels": cap_hit_labels,
        "result_status": _result_status(candidate_count, warnings),
        "rg_prefilter": prefilter,
        "output_bytes": 0,
        "context_bytes": 0,
        "output_context_bytes": 0,
        "warnings": warnings,
    }
    row["wall_time_ms"] = row["elapsed_ms"]
    row["output_context_bytes"] = len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    row["output_bytes"] = row["output_context_bytes"]
    row["context_bytes"] = row["output_context_bytes"]
    row["counter_availability"] = _measured_counter_availability(row)
    return row


def _load_probes(args) -> tuple[list[MeasuredProbe], list[str]]:
    if args.queries:
        return (
            [
                MeasuredProbe(query_text=query, row_id=f"query_{index + 1:03d}", query_source="explicit_query")
                for index, query in enumerate(args.queries)
            ],
            [],
        )
    if args.case_limit <= 0:
        return [], []
    fixture_path = Path(args.fixture).expanduser()
    try:
        fixture = load_benchmark_fixture(fixture_path)
    except OSError:
        return [], ["fixture could not be read"]
    except json.JSONDecodeError:
        return [], ["fixture is not valid JSON"]
    errors = validate_benchmark_fixture(fixture)
    if errors:
        return [], [f"fixture validation failed: {error}" for error in errors[:5]]
    probes = []
    for case in fixture.get("cases", [])[: args.case_limit]:
        case_id = str(case.get("case_id") or f"case_{len(probes) + 1:03d}")
        query_text = str(case.get("query_paraphrase") or "")
        if not query_text:
            probes.append(MeasuredProbe(query_text=_fallback_query(case_id), row_id=case_id, query_source="fixture_case"))
            continue
        probes.append(MeasuredProbe(query_text=query_text, row_id=case_id, query_source="fixture_case"))
    return probes, []


def _fallback_query(case_id: str) -> str:
    return hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]


def _aggregate_rows(
    *,
    rows: list[dict[str, Any]],
    run_id: str,
    limits: Limits,
    root_labels: list[str],
    case_limit: int,
    total_elapsed_ms: int,
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    warnings: set[str] = set()
    for row in rows:
        statuses[row["result_status"]] = statuses.get(row["result_status"], 0) + 1
        warnings.update(row.get("warnings", []))
    prefilter = _aggregate_prefilter(rows)
    return {
        "run_id": run_id,
        "overall_status": "capped" if statuses.get("capped") else "ok",
        "total_elapsed_ms": total_elapsed_ms,
        "wall_time_ms": total_elapsed_ms,
        "tool_calls": sum(int(row.get("tool_calls", 0)) for row in rows),
        "subprocess_calls": sum(int(row.get("subprocess_calls", 0)) for row in rows),
        "searched_files": sum(int(row.get("searched_files", 0)) for row in rows),
        "files_opened": sum(int(row.get("files_opened", 0)) for row in rows),
        "python_opened_files": sum(int(row.get("python_opened_files", 0)) for row in rows),
        "searched_bytes": sum(int(row.get("searched_bytes", 0)) for row in rows),
        "bytes_scanned": sum(int(row.get("bytes_scanned", 0)) for row in rows),
        "bytes_parsed": sum(int(row.get("bytes_parsed", 0)) for row in rows),
        "candidate_count": sum(int(row.get("candidate_count", 0)) for row in rows),
        "output_bytes": sum(int(row.get("output_bytes", 0)) for row in rows),
        "context_bytes": sum(int(row.get("context_bytes", 0)) for row in rows),
        "output_context_bytes": sum(int(row.get("output_context_bytes", 0)) for row in rows),
        "caps_hit": bool(statuses.get("capped")),
        "cap_hit_labels": sorted({label for row in rows for label in row.get("cap_hit_labels", [])}),
        "required_counters": _required_counter_summary(rows),
        "statuses": statuses,
        "limits": _limits_payload(limits, case_limit=case_limit),
        "root_labels": root_labels,
        "warnings": sorted(warnings),
        "rg_prefilter": prefilter,
    }


def _empty_aggregate(run_id: str, limits: Limits, root_labels: list[str], case_limit: int) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "overall_status": "pending",
        "total_elapsed_ms": 0,
        "wall_time_ms": 0,
        "tool_calls": 0,
        "subprocess_calls": 0,
        "searched_files": 0,
        "files_opened": 0,
        "python_opened_files": 0,
        "searched_bytes": 0,
        "bytes_scanned": 0,
        "bytes_parsed": 0,
        "candidate_count": 0,
        "output_bytes": 0,
        "context_bytes": 0,
        "output_context_bytes": 0,
        "caps_hit": False,
        "cap_hit_labels": [],
        "required_counters": _required_counter_summary([]),
        "statuses": {},
        "limits": _limits_payload(limits, case_limit=case_limit),
        "root_labels": root_labels,
        "warnings": [],
        "rg_prefilter": _aggregate_prefilter([]),
    }


def _finish_without_scan(report: dict[str, Any], *, status: str, warnings: list[str], started: float) -> dict[str, Any]:
    safe = _safe_warnings(warnings)
    report["status"] = status
    report["aggregate"] = {
        **report["aggregate"],
        "overall_status": status,
        "total_elapsed_ms": _elapsed_ms(started),
        "wall_time_ms": _elapsed_ms(started),
        "statuses": {status: 1},
        "warnings": safe,
    }
    report["ranking_quality_gate"] = _gate_status(
        "not_evaluated",
        [f"benchmark_status_{status}", "no_benchmark_rows"],
        counters=report["aggregate"].get("required_counters"),
    )
    report["rg_prefilter_gate"] = _gate_status(
        "not_evaluated",
        [f"benchmark_status_{status}", "no_benchmark_rows"],
        counters=report["aggregate"].get("required_counters"),
    )
    return {"measured_benchmark": report}


def _prefilter_row_stats(result: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    stats = result.get("prefilter") if enabled else None
    if not isinstance(stats, dict):
        return {
            "enabled": bool(enabled),
            "available": None,
            "attempted": False,
            "status": "disabled" if not enabled else "not_attempted",
            "fallback_reason": None,
            "candidate_files": 0,
            "candidate_bytes": 0,
            "matched_files": 0,
            "matched_bytes": 0,
            "files_reduced": 0,
            "bytes_reduced": 0,
            "files_reduction_ratio": 0.0,
            "bytes_reduction_ratio": 0.0,
        }
    return {
        "enabled": bool(stats.get("enabled")),
        "available": stats.get("available"),
        "attempted": bool(stats.get("attempted")),
        "status": str(stats.get("status") or "unknown"),
        "fallback_reason": stats.get("fallback_reason"),
        "candidate_files": int(stats.get("candidate_files", 0)),
        "candidate_bytes": int(stats.get("candidate_bytes", 0)),
        "matched_files": int(stats.get("matched_files", 0)),
        "matched_bytes": int(stats.get("matched_bytes", 0)),
        "files_reduced": int(stats.get("files_reduced", 0)),
        "bytes_reduced": int(stats.get("bytes_reduced", 0)),
        "files_reduction_ratio": float(stats.get("files_reduction_ratio", 0.0)),
        "bytes_reduction_ratio": float(stats.get("bytes_reduction_ratio", 0.0)),
    }


def _aggregate_prefilter(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    fallback_reasons: dict[str, int] = {}
    aggregate = {
        "enabled_rows": 0,
        "attempted_rows": 0,
        "available_rows": 0,
        "statuses": statuses,
        "fallback_reasons": fallback_reasons,
        "fallback_count": 0,
        "candidate_files": 0,
        "candidate_bytes": 0,
        "matched_files": 0,
        "matched_bytes": 0,
        "files_reduced": 0,
        "bytes_reduced": 0,
        "files_reduction_ratio": 0.0,
        "bytes_reduction_ratio": 0.0,
    }
    for row in rows:
        stats = row.get("rg_prefilter") or {}
        if stats.get("enabled"):
            aggregate["enabled_rows"] += 1
        if stats.get("attempted"):
            aggregate["attempted_rows"] += 1
        if stats.get("available") is True:
            aggregate["available_rows"] += 1
        status = str(stats.get("status") or "missing")
        statuses[status] = statuses.get(status, 0) + 1
        reason = stats.get("fallback_reason")
        if reason:
            aggregate["fallback_count"] += 1
            fallback_reasons[str(reason)] = fallback_reasons.get(str(reason), 0) + 1
        for key in ("candidate_files", "candidate_bytes", "matched_files", "matched_bytes", "files_reduced", "bytes_reduced"):
            aggregate[key] += int(stats.get(key, 0))
    aggregate["files_reduction_ratio"] = _ratio(int(aggregate["files_reduced"]), int(aggregate["candidate_files"]))
    aggregate["bytes_reduction_ratio"] = _ratio(int(aggregate["bytes_reduced"]), int(aggregate["candidate_bytes"]))
    return aggregate


def _evaluate_gate_statuses(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "ranking_quality_gate": _evaluate_ranking_quality_gate(rows, aggregate),
        "rg_prefilter_gate": _evaluate_rg_prefilter_gate(rows, aggregate),
    }


def _evaluate_ranking_quality_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return _gate_status(
            "not_evaluated",
            ["no_benchmark_rows"],
            counters=aggregate.get("required_counters"),
        )
    return _gate_status(
        "inconclusive",
        [
            "measured_rows_have_no_committed_gold_refs",
            "verified_case_status_unavailable",
            "first_pass_recall_mrr_unavailable",
        ],
        evaluated_case_count=0,
        counters=aggregate.get("required_counters"),
        metrics={
            "row_count": len(rows),
            "candidate_count": aggregate.get("candidate_count", 0),
            "no_evidence_correct_available": False,
        },
    )


def _evaluate_rg_prefilter_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return _gate_status(
            "not_evaluated",
            ["no_benchmark_rows"],
            counters=aggregate.get("required_counters"),
        )
    prefilter = aggregate.get("rg_prefilter") or {}
    if not prefilter.get("enabled_rows"):
        return _gate_status(
            "not_evaluated",
            ["rg_prefilter_not_enabled"],
            counters=aggregate.get("required_counters"),
            metrics={"row_count": len(rows)},
        )
    reasons = [
        "source_ref_equivalence_unavailable_in_measured_rows",
        "verified_exact_path_case_status_unavailable",
    ]
    if not prefilter.get("available_rows"):
        reasons.append("rg_unavailable_or_not_used")
    if aggregate.get("caps_hit"):
        reasons.append("cap_hits_present")
    return _gate_status(
        "inconclusive",
        reasons,
        evaluated_case_count=0,
        counters=aggregate.get("required_counters"),
        metrics={
            "enabled_rows": prefilter.get("enabled_rows", 0),
            "available_rows": prefilter.get("available_rows", 0),
            "fallback_count": prefilter.get("fallback_count", 0),
            "files_reduction_ratio": prefilter.get("files_reduction_ratio", 0.0),
            "bytes_reduction_ratio": prefilter.get("bytes_reduction_ratio", 0.0),
        },
    )


def _gate_status(
    status: str,
    reasons: list[str],
    *,
    evaluated_case_count: int = 0,
    counters: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in GATE_STATUSES:
        status = "inconclusive"
        reasons = ["invalid_gate_status_normalized"] + reasons
    payload: dict[str, Any] = {
        "status": status,
        "reasons": sorted(set(reasons)),
        "evaluated_case_count": evaluated_case_count,
    }
    if counters is not None:
        payload["required_counters"] = counters
    if metrics is not None:
        payload["metrics"] = metrics
    return payload


def _measured_counter_availability(row: dict[str, Any]) -> dict[str, dict[str, str]]:
    unavailable = {
        "first_pass_hit": "no_committed_gold_refs_for_measured_probe",
        "recall_at_1": "no_committed_gold_refs_for_measured_probe",
        "recall_at_3": "no_committed_gold_refs_for_measured_probe",
        "recall_at_10": "no_committed_gold_refs_for_measured_probe",
        "mrr": "no_committed_gold_refs_for_measured_probe",
        "no_evidence_correct": "no_committed_expected_outcome_for_measured_probe",
    }
    availability: dict[str, dict[str, str]] = {}
    for key in _required_counter_keys():
        if key in unavailable:
            availability[key] = {"status": "unavailable", "reason": unavailable[key]}
        elif row.get(key) is None:
            availability[key] = {"status": "unavailable", "reason": "counter_not_available"}
        else:
            availability[key] = {"status": "available"}
    return availability


def _required_counter_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in _required_counter_keys():
        available = 0
        unavailable_reasons: set[str] = set()
        for row in rows:
            state = (row.get("counter_availability") or {}).get(key) or {}
            if state.get("status") == "available":
                available += 1
            else:
                unavailable_reasons.add(str(state.get("reason") or "counter_not_available"))
        summary[key] = {
            "available_rows": available,
            "unavailable_rows": max(0, len(rows) - available),
            "unavailable_reasons": sorted(unavailable_reasons),
        }
    return summary


def _required_counter_keys() -> tuple[str, ...]:
    return (
        "wall_time_ms",
        "tool_calls",
        "subprocess_calls",
        "files_opened",
        "bytes_scanned",
        "bytes_parsed",
        "output_bytes",
        "context_bytes",
        "output_context_bytes",
        "candidate_count",
        "first_pass_hit",
        "recall_at_1",
        "recall_at_3",
        "recall_at_10",
        "mrr",
        "no_evidence_correct",
        "caps_hit",
    )


def _cap_hit_labels(warnings: list[str]) -> list[str]:
    labels = set()
    for warning in warnings:
        if not warning.startswith("stopped_at_"):
            continue
        label = warning[len("stopped_at_") :]
        if label.endswith("_cap"):
            label = label[: -len("_cap")]
        labels.add(label)
    return sorted(labels)


def _limits_payload(limits: Limits, *, case_limit: int) -> dict[str, Any]:
    payload = limits.to_dict()
    payload["case_limit"] = case_limit
    return payload


def _preflight_root_warnings(roots: list[Path], cwd: Path) -> list[str]:
    warnings = []
    for index, root in enumerate(roots):
        reason = dangerous_reason(safe_resolve(root), cwd)
        if reason:
            warnings.append(f"root_{index + 1:03d} rejected: {reason}")
    return warnings


def _safe_warnings(warnings: list[str]) -> list[str]:
    safe: list[str] = []
    for warning in warnings:
        lowered = warning.lower()
        if "max elapsed cap" in lowered:
            safe.append("stopped_at_max_elapsed_cap")
        elif "max file cap" in lowered:
            safe.append("stopped_at_max_file_cap")
        elif "max byte cap" in lowered:
            safe.append("stopped_at_max_byte_cap")
        elif "max match cap" in lowered:
            safe.append("stopped_at_max_match_cap")
        elif "dangerous broad root rejected" in lowered:
            safe.append("root_rejected_dangerous_broad_root")
        elif "drive root rejected" in lowered:
            safe.append("root_rejected_drive_root")
        elif "mounted drive root rejected" in lowered:
            safe.append("root_rejected_mounted_drive_root")
        elif "repo root rejected" in lowered:
            safe.append("root_rejected_repo_root")
        elif "cache/model directory rejected" in lowered:
            safe.append("root_rejected_cache_or_model_root")
        elif "arbitrary root override requires" in lowered:
            safe.append("root_rejected_missing_arbitrary_root_hard_caps")
        elif "not a known codex/claude transcript root" in lowered:
            safe.append("root_rejected_unknown_without_override")
        elif "arbitrary root enabled" in lowered:
            safe.append("arbitrary_root_enabled_with_hard_caps")
        elif "missing transcript root" in lowered:
            safe.append("missing_root")
        elif "no transcript roots selected" in lowered:
            safe.append("no_roots_selected")
        elif "could not enumerate" in lowered:
            safe.append("enumeration_error")
        elif "could not open" in lowered:
            safe.append("read_error")
        elif "skipped symlink escape" in lowered:
            safe.append("skipped_symlink_escape")
        else:
            safe.append(_opaque_warning(warning))
    return sorted(set(safe))


def _opaque_warning(warning: str) -> str:
    digest = hashlib.sha256(warning.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"warning_{digest}"


def _result_status(candidate_count: int, warnings: list[str]) -> str:
    if any(item.startswith("stopped_at_max_") for item in warnings):
        return "capped"
    if candidate_count:
        return "found"
    return "no_evidence"


def _privacy_contract() -> dict[str, Any]:
    return {
        "stdout_raw_query_text_included": False,
        "stdout_raw_private_text_included": False,
        "stdout_transcript_paths_included": False,
        "report_requires_explicit_output_path": True,
        "candidate_rows_included": False,
        "root_paths_replaced_with_labels": True,
    }


def _run_id() -> str:
    return f"measured_{uuid.uuid4().hex[:12]}"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0
