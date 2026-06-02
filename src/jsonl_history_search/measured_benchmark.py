from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .benchmark import load_benchmark_fixture, validate_benchmark_fixture
from .manifest import Manifest, build_manifest, shortlist_manifest
from .query import infer_query
from .scanner import search
from .sources import Limits, PlannedRoot, SourcePlan, build_source_plan, dangerous_reason, safe_resolve, stat_mtime, stat_size
from .timestamp_seek import TimestampSeekIndex, build_timestamp_seek_index, search_with_timestamp_seek
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
    timestamp_seek_enabled = bool(getattr(args, "timestamp_seek_prototype", False))

    base_report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "mode": "measured_local",
        "data_source": "live_local_roots",
        "status": "pending",
        "root_labels": root_labels,
        "limits": _limits_payload(limits, case_limit=args.case_limit),
        "rows": [],
        "aggregate": _empty_aggregate(
            run_id,
            limits,
            root_labels,
            args.case_limit,
            include_timestamp_seek=timestamp_seek_enabled,
        ),
        "ranking_quality_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "rg_prefilter_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "manifest_shortlist_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "privacy_contract": _privacy_contract(),
    }
    if timestamp_seek_enabled:
        base_report["timestamp_sidecar_gate"] = _gate_status("not_evaluated", ["benchmark_not_run_yet"])
        base_report["privacy_contract"].update(_timestamp_seek_privacy_contract())

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

    manifest_pair = None
    if getattr(args, "manifest_shortlist", False) or timestamp_seek_enabled:
        initial_manifest = build_manifest(plan, default_tz=tz)
        refreshed_manifest = build_manifest(plan, default_tz=tz, previous=initial_manifest)
        manifest_pair = (initial_manifest, refreshed_manifest)
    timestamp_index_pair = None
    if timestamp_seek_enabled:
        initial_timestamp_index = build_timestamp_seek_index(plan, default_tz=tz)
        refreshed_timestamp_index = build_timestamp_seek_index(plan, default_tz=tz, previous=initial_timestamp_index)
        timestamp_index_pair = (initial_timestamp_index, refreshed_timestamp_index)

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
            manifest_pair=manifest_pair,
            timestamp_index_pair=timestamp_index_pair,
        )
        rows.append(row)

    aggregate = _aggregate_rows(
        rows=rows,
        run_id=run_id,
        limits=limits,
        root_labels=root_labels,
        case_limit=args.case_limit,
        total_elapsed_ms=_elapsed_ms(started),
        include_timestamp_seek=timestamp_seek_enabled,
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
    manifest_pair: tuple[Manifest, Manifest] | None,
    timestamp_index_pair: tuple[TimestampSeekIndex, TimestampSeekIndex] | None,
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
    manifest_stats = _manifest_shortlist_row_stats(
        manifest_pair=manifest_pair,
        intent=intent,
        plan=plan,
        default_tz=default_tz,
        cwd=cwd,
        baseline_result=result,
        baseline_candidate_count=candidate_count,
        use_rg_prefilter=use_rg_prefilter,
    )
    row["manifest_shortlist"] = manifest_stats
    if timestamp_index_pair is not None:
        row["timestamp_seek_prototype"] = _timestamp_seek_row_stats(
            timestamp_index_pair=timestamp_index_pair,
            manifest_pair=manifest_pair,
            intent=intent,
            plan=plan,
            default_tz=default_tz,
            cwd=cwd,
            baseline_result=result,
            baseline_candidate_count=candidate_count,
            baseline_parsed_bytes=int(result.get("searched_bytes", 0)),
            baseline_python_opened_files=int(result.get("searched_files", 0)),
            manifest_stats=manifest_stats,
        )
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
    include_timestamp_seek: bool,
) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    warnings: set[str] = set()
    for row in rows:
        statuses[row["result_status"]] = statuses.get(row["result_status"], 0) + 1
        warnings.update(row.get("warnings", []))
    prefilter = _aggregate_prefilter(rows)
    manifest = _aggregate_manifest_shortlist(rows)
    aggregate = {
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
        "manifest_shortlist": manifest,
    }
    if include_timestamp_seek:
        aggregate["timestamp_seek_prototype"] = _aggregate_timestamp_seek(rows)
    return aggregate


def _empty_aggregate(
    run_id: str,
    limits: Limits,
    root_labels: list[str],
    case_limit: int,
    *,
    include_timestamp_seek: bool = False,
) -> dict[str, Any]:
    aggregate = {
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
        "manifest_shortlist": _aggregate_manifest_shortlist([]),
    }
    if include_timestamp_seek:
        aggregate["timestamp_seek_prototype"] = _aggregate_timestamp_seek([])
    return aggregate


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
    report["manifest_shortlist_gate"] = _gate_status(
        "not_evaluated",
        [f"benchmark_status_{status}", "no_benchmark_rows"],
        counters=_manifest_required_counter_summary([]),
    )
    if "timestamp_sidecar_gate" in report:
        report["timestamp_sidecar_gate"] = _gate_status(
            "not_evaluated",
            [f"benchmark_status_{status}", "no_benchmark_rows"],
            counters=_timestamp_seek_required_counter_summary([]),
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


def _manifest_shortlist_row_stats(
    *,
    manifest_pair: tuple[Manifest, Manifest] | None,
    intent,
    plan: SourcePlan,
    default_tz,
    cwd: str,
    baseline_result: dict[str, Any],
    baseline_candidate_count: int,
    use_rg_prefilter: bool,
) -> dict[str, Any]:
    if manifest_pair is None:
        return _manifest_row_disabled("not_enabled")

    initial, refreshed = manifest_pair
    shortlist = shortlist_manifest(initial, intent, cwd=cwd)
    metrics = shortlist.get("metrics") or {}
    build_warnings = _safe_warnings(list(initial.warnings) + list(refreshed.warnings))
    cap_labels = _cap_hit_labels(build_warnings)
    base = {
        "enabled": True,
        "manifest_status": "ok",
        "status": "not_evaluated",
        "build_bytes": int(initial.metrics.get("bytes_parsed", 0)),
        "build_elapsed_ms": initial.metrics.get("elapsed_ms", 0),
        "refresh_elapsed_ms": refreshed.metrics.get("elapsed_ms", 0),
        "selected_files": int(metrics.get("selected_files", 0)),
        "excluded_files": int(metrics.get("excluded_files", 0)),
        "selected_bytes": int(metrics.get("selected_bytes", 0)),
        "excluded_bytes": int(metrics.get("excluded_bytes", 0)),
        "file_reduction_ratio": float(metrics.get("file_reduction_ratio", 0.0)),
        "byte_reduction_ratio": float(metrics.get("byte_reduction_ratio", 0.0)),
        "weak_candidates_retained": int(metrics.get("weak_candidates_retained", 0)),
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "elapsed_ms": 0,
        "candidate_count": 0,
        "source_ref_equivalence": "not_evaluated",
        "no_evidence_scope_equivalence": "not_evaluated",
        "fallback_reason": None,
        "caps_hit": bool(cap_labels),
        "cap_hit_labels": cap_labels,
        "scope_labels": {
            "selected": "selected_scope",
            "searched": "searched_scope",
            "excluded": "excluded_scope",
            "not_searched": "not_searched_scope",
            "weak_candidate": "weak_candidate",
            "fallback": "fallback",
            "cap": "cap_hit",
        },
    }

    fallback_reason = _manifest_fallback_reason(initial, shortlist)
    selected_entries = shortlist.get("selected_entries") or []
    if fallback_reason is None and not _selected_entries_current(selected_entries):
        fallback_reason = "manifest_stale_or_changed"
    if fallback_reason is None and not _selected_entries_allowed(selected_entries, plan):
        fallback_reason = "manifest_selection_rejected"

    if fallback_reason is not None:
        base.update(
            {
                "status": "fallback",
                "manifest_status": "fallback",
                "fallback_reason": fallback_reason,
                "python_opened_files": int(baseline_result.get("searched_files", 0)),
                "parsed_bytes": int(baseline_result.get("searched_bytes", 0)),
                "elapsed_ms": 0,
                "candidate_count": baseline_candidate_count,
                "source_ref_equivalence": "fallback_not_compared",
                "no_evidence_scope_equivalence": "fallback_scope_reported",
            }
        )
        return base

    narrowed_plan = _source_plan_from_manifest_selection(plan, selected_entries)
    started = time.perf_counter()
    narrowed_result = search(
        narrowed_plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        use_rg_prefilter=use_rg_prefilter,
    )
    manifest_elapsed_ms = _elapsed_ms(started)
    narrowed_warnings = _safe_warnings(narrowed_result.get("warnings", []))
    narrowed_cap_labels = _cap_hit_labels(narrowed_warnings)
    manifest_candidate_count = sum(len(group.get("matches", [])) for group in narrowed_result.get("groups", []))
    cap_labels = sorted(set(cap_labels + narrowed_cap_labels))
    base.update(
        {
            "status": "planned_scan",
            "python_opened_files": int(narrowed_result.get("searched_files", 0)),
            "parsed_bytes": int(narrowed_result.get("searched_bytes", 0)),
            "elapsed_ms": manifest_elapsed_ms,
            "candidate_count": manifest_candidate_count,
            "source_ref_equivalence": _source_ref_equivalence(baseline_result, narrowed_result),
            "no_evidence_scope_equivalence": _no_evidence_scope_equivalence(
                baseline_candidate_count=baseline_candidate_count,
                manifest_candidate_count=manifest_candidate_count,
                excluded_files=int(metrics.get("excluded_files", 0)),
            ),
            "caps_hit": bool(cap_labels),
            "cap_hit_labels": cap_labels,
        }
    )
    return base


def _manifest_row_disabled(reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "manifest_status": "disabled",
        "status": "disabled",
        "build_bytes": 0,
        "build_elapsed_ms": 0,
        "refresh_elapsed_ms": 0,
        "selected_files": 0,
        "excluded_files": 0,
        "selected_bytes": 0,
        "excluded_bytes": 0,
        "file_reduction_ratio": 0.0,
        "byte_reduction_ratio": 0.0,
        "weak_candidates_retained": 0,
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "elapsed_ms": 0,
        "candidate_count": 0,
        "source_ref_equivalence": "not_evaluated",
        "no_evidence_scope_equivalence": "not_evaluated",
        "fallback_reason": reason,
        "caps_hit": False,
        "cap_hit_labels": [],
        "scope_labels": {},
    }


def _timestamp_seek_row_stats(
    *,
    timestamp_index_pair: tuple[TimestampSeekIndex, TimestampSeekIndex] | None,
    manifest_pair: tuple[Manifest, Manifest] | None,
    intent,
    plan: SourcePlan,
    default_tz,
    cwd: str,
    baseline_result: dict[str, Any],
    baseline_candidate_count: int,
    baseline_parsed_bytes: int,
    baseline_python_opened_files: int,
    manifest_stats: dict[str, Any],
) -> dict[str, Any]:
    if timestamp_index_pair is None:
        return _timestamp_seek_row_disabled("not_enabled")

    initial, refreshed = timestamp_index_pair
    selected_paths, planning_source, selection_reason = _timestamp_seek_selected_paths(
        manifest_pair=manifest_pair,
        intent=intent,
        plan=plan,
        cwd=cwd,
    )
    build_warnings = _safe_warnings(list(initial.warnings) + list(refreshed.warnings))
    cap_labels = _cap_hit_labels(build_warnings)
    base = {
        "enabled": True,
        "prototype_status": "ok",
        "status": "not_evaluated",
        "planning_source": planning_source,
        "selection_reason": selection_reason,
        "build_bytes": int(initial.metrics.get("build_bytes", 0)),
        "build_elapsed_ms": initial.metrics.get("build_elapsed_ms", 0),
        "no_change_refresh_elapsed_ms": refreshed.metrics.get("refresh_elapsed_ms", 0),
        "append_refresh_elapsed_ms": 0,
        "query_elapsed_ms": 0,
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "records_considered": 0,
        "sidecar_hits": 0,
        "sidecar_misses": 0,
        "fallback_reason": None,
        "refresh_count": int(refreshed.metrics.get("refresh_count", 0)),
        "no_change_refresh_count": int(refreshed.metrics.get("no_change_refresh_count", 0)),
        "append_refresh_count": int(refreshed.metrics.get("append_refresh_count", 0)),
        "stale_refresh_count": int(refreshed.metrics.get("stale_refresh_count", 0)),
        "ordering_equivalence": "not_evaluated",
        "source_ref_equivalence": "not_evaluated",
        "caps_hit": bool(cap_labels),
        "cap_hit_labels": cap_labels,
        "manifest_planned_parsed_bytes": int(manifest_stats.get("parsed_bytes", 0)),
        "baseline_parsed_bytes": baseline_parsed_bytes,
        "baseline_python_opened_files": baseline_python_opened_files,
        "parsed_byte_reduction_ratio_vs_baseline": 0.0,
        "parsed_byte_reduction_ratio_vs_manifest": 0.0,
        "source_ref_equivalence_basis": "python_parsed_candidate_lines",
    }

    fallback_reason = None
    if not intent.date_window_start or not intent.date_window_end:
        fallback_reason = "timestamp_window_absent"
    elif not refreshed.files:
        fallback_reason = "timestamp_index_absent"
    elif selected_paths is not None and not selected_paths:
        fallback_reason = "timestamp_selection_empty"

    if fallback_reason is None:
        seek_result = search_with_timestamp_seek(
            refreshed,
            plan,
            intent,
            default_tz=default_tz,
            cwd=cwd,
            selected_paths=selected_paths,
        )
        fallback_reason = seek_result.get("fallback_reason")
    else:
        seek_result = {}

    if fallback_reason is not None:
        base.update(
            {
                "status": "fallback",
                "prototype_status": "fallback",
                "fallback_reason": fallback_reason,
                "python_opened_files": baseline_python_opened_files,
                "parsed_bytes": baseline_parsed_bytes,
                "records_considered": baseline_candidate_count,
                "query_elapsed_ms": int(seek_result.get("elapsed_ms", 0)),
                "source_ref_equivalence": "fallback_not_compared",
                "ordering_equivalence": "fallback_not_compared",
            }
        )
        return base

    seek_warnings = _safe_warnings(seek_result.get("warnings", []))
    cap_labels = sorted(set(cap_labels + _cap_hit_labels(seek_warnings)))
    base.update(
        {
            "status": "planned_seek",
            "query_elapsed_ms": int(seek_result.get("elapsed_ms", 0)),
            "python_opened_files": int(seek_result.get("searched_files", 0)),
            "parsed_bytes": int(seek_result.get("searched_bytes", 0)),
            "records_considered": int(seek_result.get("records_considered", 0)),
            "sidecar_hits": int(seek_result.get("sidecar_hits", 0)),
            "sidecar_misses": int(seek_result.get("sidecar_misses", 0)),
            "source_ref_equivalence": _source_ref_equivalence(baseline_result, seek_result),
            "ordering_equivalence": _source_ref_equivalence(baseline_result, seek_result),
            "caps_hit": bool(cap_labels),
            "cap_hit_labels": cap_labels,
        }
    )
    base["parsed_byte_reduction_ratio_vs_baseline"] = _ratio(
        max(0, baseline_parsed_bytes - int(base["parsed_bytes"])),
        baseline_parsed_bytes,
    )
    manifest_bytes = int(manifest_stats.get("parsed_bytes", 0))
    if manifest_bytes:
        base["parsed_byte_reduction_ratio_vs_manifest"] = _ratio(
            max(0, manifest_bytes - int(base["parsed_bytes"])),
            manifest_bytes,
        )
    return base


def _timestamp_seek_row_disabled(reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "prototype_status": "disabled",
        "status": "disabled",
        "planning_source": "disabled",
        "selection_reason": reason,
        "build_bytes": 0,
        "build_elapsed_ms": 0,
        "no_change_refresh_elapsed_ms": 0,
        "append_refresh_elapsed_ms": 0,
        "query_elapsed_ms": 0,
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "records_considered": 0,
        "sidecar_hits": 0,
        "sidecar_misses": 0,
        "fallback_reason": reason,
        "refresh_count": 0,
        "no_change_refresh_count": 0,
        "append_refresh_count": 0,
        "stale_refresh_count": 0,
        "ordering_equivalence": "not_evaluated",
        "source_ref_equivalence": "not_evaluated",
        "caps_hit": False,
        "cap_hit_labels": [],
        "manifest_planned_parsed_bytes": 0,
        "baseline_parsed_bytes": 0,
        "baseline_python_opened_files": 0,
        "parsed_byte_reduction_ratio_vs_baseline": 0.0,
        "parsed_byte_reduction_ratio_vs_manifest": 0.0,
        "source_ref_equivalence_basis": "disabled",
    }


def _timestamp_seek_selected_paths(
    *,
    manifest_pair: tuple[Manifest, Manifest] | None,
    intent,
    plan: SourcePlan,
    cwd: str,
) -> tuple[set[str] | None, str, str]:
    if manifest_pair is None:
        return None, "full_plan", "manifest_not_available"
    initial, _refreshed = manifest_pair
    shortlist = shortlist_manifest(initial, intent, cwd=cwd)
    selected_entries = shortlist.get("selected_entries") or []
    if not selected_entries:
        return set(), "manifest_shortlist", "manifest_empty_selection"
    if not _selected_entries_current(selected_entries):
        return None, "full_plan", "manifest_stale_or_changed"
    if not _selected_entries_allowed(selected_entries, plan):
        return None, "full_plan", "manifest_selection_rejected"
    return (
        {str((item.get("entry") or {}).get("path")) for item in selected_entries if isinstance(item, dict)},
        "manifest_shortlist",
        "manifest_selected_files",
    )


def _manifest_fallback_reason(manifest: Manifest, shortlist: dict[str, Any]) -> str | None:
    metrics = shortlist.get("metrics") or {}
    if manifest is None:
        return "manifest_absent"
    if not manifest.entries:
        return "manifest_empty"
    if _cap_hit_labels(_safe_warnings(list(manifest.warnings))):
        return "manifest_build_capped"
    if int(metrics.get("selected_files", 0)) <= 0:
        return "manifest_empty_selection"
    if float(metrics.get("file_reduction_ratio", 0.0)) <= 0.0 and float(metrics.get("byte_reduction_ratio", 0.0)) <= 0.0:
        return "manifest_unhelpful_no_reduction"
    return None


def _selected_entries_current(selected_entries: list[dict[str, Any]]) -> bool:
    for item in selected_entries:
        entry = item.get("entry") if isinstance(item, dict) else None
        if not isinstance(entry, dict):
            return False
        path = Path(str(entry.get("path") or ""))
        if stat_size(path) != int(entry.get("size", -1)):
            return False
        if stat_mtime(path) != float(entry.get("mtime", -1.0)):
            return False
    return True


def _selected_entries_allowed(selected_entries: list[dict[str, Any]], plan: SourcePlan) -> bool:
    allowed_paths = {str(path) for path, _size, _warnings in _enumerate_planned_file_tuples(plan)}
    selected_paths = {str((item.get("entry") or {}).get("path")) for item in selected_entries if isinstance(item, dict)}
    return selected_paths <= allowed_paths


def _enumerate_planned_file_tuples(plan: SourcePlan):
    from .sources import enumerate_jsonl_files

    yield from enumerate_jsonl_files(plan)


def _source_plan_from_manifest_selection(plan: SourcePlan, selected_entries: list[dict[str, Any]]) -> SourcePlan:
    roots: list[PlannedRoot] = []
    selected_bytes = 0
    for item in selected_entries:
        entry = item.get("entry") or {}
        path = Path(str(entry.get("path")))
        selected_bytes += int(entry.get("size", 0))
        roots.append(
            PlannedRoot(
                kind=str(entry.get("source_kind") or "manifest_selected_jsonl"),
                path=path,
                reason="manifest shortlist selected file",
                recursive=False,
                known=True,
            )
        )
    limits = Limits(
        max_files=max(1, len(roots)),
        max_bytes=max(1, selected_bytes),
        max_matches=plan.limits.max_matches,
        max_elapsed=plan.limits.max_elapsed,
    )
    return SourcePlan(
        roots=roots,
        rejected_roots=[],
        warnings=list(plan.warnings),
        blind_spots=list(plan.blind_spots),
        limits=limits,
    )


def _source_ref_equivalence(baseline_result: dict[str, Any], narrowed_result: dict[str, Any]) -> str:
    baseline_refs = _source_ref_signature(baseline_result)
    narrowed_refs = _source_ref_signature(narrowed_result)
    if not baseline_refs and not narrowed_refs:
        return "equivalent_no_refs"
    return "equivalent" if baseline_refs == narrowed_refs else "not_equivalent"


def _source_ref_signature(result: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            path = str(match.get("path", ""))
            line = str(match.get("line", ""))
            refs.append(hashlib.sha256(f"{path}:{line}".encode("utf-8")).hexdigest())
    return refs[:10]


def _no_evidence_scope_equivalence(*, baseline_candidate_count: int, manifest_candidate_count: int, excluded_files: int) -> str:
    if baseline_candidate_count == 0 and manifest_candidate_count == 0:
        return "equivalent_no_evidence_with_excluded_scope" if excluded_files else "equivalent_no_evidence"
    if baseline_candidate_count == 0 or manifest_candidate_count == 0:
        return "not_equivalent"
    return "not_applicable_found"


def _aggregate_manifest_shortlist(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    fallback_reasons: dict[str, int] = {}
    aggregate = {
        "enabled_rows": 0,
        "planned_rows": 0,
        "fallback_rows": 0,
        "statuses": statuses,
        "fallback_reasons": fallback_reasons,
        "manifest_statuses": {},
        "build_bytes": 0,
        "build_elapsed_ms": 0,
        "refresh_elapsed_ms": 0,
        "selected_files": 0,
        "excluded_files": 0,
        "selected_bytes": 0,
        "excluded_bytes": 0,
        "file_reduction_ratio": 0.0,
        "byte_reduction_ratio": 0.0,
        "weak_candidates_retained": 0,
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "elapsed_ms": 0,
        "candidate_count": 0,
        "source_ref_equivalence": {},
        "no_evidence_scope_equivalence": {},
        "caps_hit": False,
        "cap_hit_labels": [],
        "required_counters": _manifest_required_counter_summary(rows),
    }
    build_counted = False
    for row in rows:
        stats = row.get("manifest_shortlist") or {}
        status = str(stats.get("status") or "missing")
        statuses[status] = statuses.get(status, 0) + 1
        manifest_status = str(stats.get("manifest_status") or "missing")
        aggregate["manifest_statuses"][manifest_status] = aggregate["manifest_statuses"].get(manifest_status, 0) + 1
        if not stats.get("enabled"):
            continue
        aggregate["enabled_rows"] += 1
        if status == "planned_scan":
            aggregate["planned_rows"] += 1
        if status == "fallback":
            aggregate["fallback_rows"] += 1
        reason = stats.get("fallback_reason")
        if reason:
            fallback_reasons[str(reason)] = fallback_reasons.get(str(reason), 0) + 1
        if not build_counted:
            aggregate["build_bytes"] = int(stats.get("build_bytes", 0))
            aggregate["build_elapsed_ms"] = stats.get("build_elapsed_ms", 0)
            aggregate["refresh_elapsed_ms"] = stats.get("refresh_elapsed_ms", 0)
            build_counted = True
        for key in (
            "selected_files",
            "excluded_files",
            "selected_bytes",
            "excluded_bytes",
            "weak_candidates_retained",
            "python_opened_files",
            "parsed_bytes",
            "elapsed_ms",
            "candidate_count",
        ):
            aggregate[key] += int(stats.get(key, 0))
        source_eq = str(stats.get("source_ref_equivalence") or "missing")
        no_evidence_eq = str(stats.get("no_evidence_scope_equivalence") or "missing")
        aggregate["source_ref_equivalence"][source_eq] = aggregate["source_ref_equivalence"].get(source_eq, 0) + 1
        aggregate["no_evidence_scope_equivalence"][no_evidence_eq] = aggregate["no_evidence_scope_equivalence"].get(no_evidence_eq, 0) + 1
        aggregate["caps_hit"] = bool(aggregate["caps_hit"] or stats.get("caps_hit"))
        aggregate["cap_hit_labels"] = sorted(set(aggregate["cap_hit_labels"]) | set(stats.get("cap_hit_labels") or []))
    aggregate["file_reduction_ratio"] = _ratio(int(aggregate["excluded_files"]), int(aggregate["selected_files"]) + int(aggregate["excluded_files"]))
    aggregate["byte_reduction_ratio"] = _ratio(int(aggregate["excluded_bytes"]), int(aggregate["selected_bytes"]) + int(aggregate["excluded_bytes"]))
    return aggregate


def _aggregate_timestamp_seek(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    fallback_reasons: dict[str, int] = {}
    parsed_values: list[int] = []
    elapsed_values: list[int] = []
    reductions_vs_baseline: list[float] = []
    reductions_vs_manifest: list[float] = []
    aggregate = {
        "enabled_rows": 0,
        "planned_rows": 0,
        "fallback_rows": 0,
        "statuses": statuses,
        "fallback_reasons": fallback_reasons,
        "build_bytes": 0,
        "build_elapsed_ms": 0,
        "no_change_refresh_elapsed_ms": 0,
        "append_refresh_elapsed_ms": 0,
        "query_elapsed_ms": 0,
        "python_opened_files": 0,
        "parsed_bytes": 0,
        "records_considered": 0,
        "sidecar_hits": 0,
        "sidecar_misses": 0,
        "refresh_count": 0,
        "no_change_refresh_count": 0,
        "append_refresh_count": 0,
        "stale_refresh_count": 0,
        "parsed_bytes_median": 0,
        "parsed_bytes_p90": 0,
        "elapsed_ms_median": 0,
        "elapsed_ms_p90": 0,
        "parsed_byte_reduction_ratio_vs_baseline_median": 0.0,
        "parsed_byte_reduction_ratio_vs_manifest_median": 0.0,
        "ordering_equivalence": {},
        "source_ref_equivalence": {},
        "caps_hit": False,
        "cap_hit_labels": [],
        "required_counters": _timestamp_seek_required_counter_summary(rows),
    }
    build_counted = False
    for row in rows:
        stats = row.get("timestamp_seek_prototype") or {}
        status = str(stats.get("status") or "missing")
        statuses[status] = statuses.get(status, 0) + 1
        if not stats.get("enabled"):
            continue
        aggregate["enabled_rows"] += 1
        if status == "planned_seek":
            aggregate["planned_rows"] += 1
        if status == "fallback":
            aggregate["fallback_rows"] += 1
        reason = stats.get("fallback_reason")
        if reason:
            fallback_reasons[str(reason)] = fallback_reasons.get(str(reason), 0) + 1
        if not build_counted:
            for key in ("build_bytes", "refresh_count", "no_change_refresh_count", "append_refresh_count", "stale_refresh_count"):
                aggregate[key] = int(stats.get(key, 0))
            for key in ("build_elapsed_ms", "no_change_refresh_elapsed_ms", "append_refresh_elapsed_ms"):
                aggregate[key] = stats.get(key, 0)
            build_counted = True
        for key in ("query_elapsed_ms", "python_opened_files", "parsed_bytes", "records_considered", "sidecar_hits", "sidecar_misses"):
            aggregate[key] += int(stats.get(key, 0))
        parsed_values.append(int(stats.get("parsed_bytes", 0)))
        elapsed_values.append(int(stats.get("query_elapsed_ms", 0)))
        reductions_vs_baseline.append(float(stats.get("parsed_byte_reduction_ratio_vs_baseline", 0.0)))
        reductions_vs_manifest.append(float(stats.get("parsed_byte_reduction_ratio_vs_manifest", 0.0)))
        ordering_eq = str(stats.get("ordering_equivalence") or "missing")
        source_eq = str(stats.get("source_ref_equivalence") or "missing")
        aggregate["ordering_equivalence"][ordering_eq] = aggregate["ordering_equivalence"].get(ordering_eq, 0) + 1
        aggregate["source_ref_equivalence"][source_eq] = aggregate["source_ref_equivalence"].get(source_eq, 0) + 1
        aggregate["caps_hit"] = bool(aggregate["caps_hit"] or stats.get("caps_hit"))
        aggregate["cap_hit_labels"] = sorted(set(aggregate["cap_hit_labels"]) | set(stats.get("cap_hit_labels") or []))
    aggregate["parsed_bytes_median"] = _median_int(parsed_values)
    aggregate["parsed_bytes_p90"] = _p90_int(parsed_values)
    aggregate["elapsed_ms_median"] = _median_int(elapsed_values)
    aggregate["elapsed_ms_p90"] = _p90_int(elapsed_values)
    aggregate["parsed_byte_reduction_ratio_vs_baseline_median"] = _median_float(reductions_vs_baseline)
    aggregate["parsed_byte_reduction_ratio_vs_manifest_median"] = _median_float(reductions_vs_manifest)
    return aggregate


def _evaluate_gate_statuses(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    gates = {
        "ranking_quality_gate": _evaluate_ranking_quality_gate(rows, aggregate),
        "rg_prefilter_gate": _evaluate_rg_prefilter_gate(rows, aggregate),
        "manifest_shortlist_gate": _evaluate_manifest_shortlist_gate(rows, aggregate),
    }
    if "timestamp_seek_prototype" in aggregate:
        gates["timestamp_sidecar_gate"] = _evaluate_timestamp_sidecar_gate(rows, aggregate)
    return gates


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


def _evaluate_manifest_shortlist_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    counters = (aggregate.get("manifest_shortlist") or {}).get("required_counters") or _manifest_required_counter_summary(rows)
    if not rows:
        return _gate_status("not_evaluated", ["no_benchmark_rows"], counters=counters)
    manifest = aggregate.get("manifest_shortlist") or {}
    if not manifest.get("enabled_rows"):
        return _gate_status(
            "not_evaluated",
            ["manifest_shortlist_not_enabled"],
            counters=counters,
            metrics={"row_count": len(rows)},
        )
    source_eq = manifest.get("source_ref_equivalence") or {}
    no_evidence_eq = manifest.get("no_evidence_scope_equivalence") or {}
    reasons = [
        "policy_eligible_aggregate_evidence_unavailable",
        "sanitized_correctness_review_unavailable",
        "synthetic_or_local_rows_do_not_prove_promotion",
    ]
    if manifest.get("fallback_rows"):
        reasons.append("manifest_fallback_rows_present")
    if manifest.get("caps_hit"):
        reasons.append("cap_hits_present")
    if source_eq.get("not_equivalent") or no_evidence_eq.get("not_equivalent"):
        return _gate_status(
            "fail",
            ["manifest_equivalence_regression"] + reasons,
            counters=counters,
            metrics=_manifest_gate_metrics(manifest),
        )
    return _gate_status(
        "inconclusive",
        reasons,
        evaluated_case_count=0,
        counters=counters,
        metrics=_manifest_gate_metrics(manifest),
    )


def _manifest_gate_metrics(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled_rows": manifest.get("enabled_rows", 0),
        "planned_rows": manifest.get("planned_rows", 0),
        "fallback_rows": manifest.get("fallback_rows", 0),
        "file_reduction_ratio": manifest.get("file_reduction_ratio", 0.0),
        "byte_reduction_ratio": manifest.get("byte_reduction_ratio", 0.0),
        "python_opened_files": manifest.get("python_opened_files", 0),
        "parsed_bytes": manifest.get("parsed_bytes", 0),
        "source_ref_equivalence": manifest.get("source_ref_equivalence", {}),
        "no_evidence_scope_equivalence": manifest.get("no_evidence_scope_equivalence", {}),
    }


def _evaluate_timestamp_sidecar_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    timestamp_seek = aggregate.get("timestamp_seek_prototype") or {}
    counters = timestamp_seek.get("required_counters") or _timestamp_seek_required_counter_summary(rows)
    if not rows:
        return _gate_status("not_evaluated", ["no_benchmark_rows"], counters=counters)
    if not timestamp_seek.get("enabled_rows"):
        return _gate_status(
            "not_evaluated",
            ["timestamp_seek_prototype_not_enabled"],
            counters=counters,
            metrics={"row_count": len(rows)},
        )
    ordering_eq = timestamp_seek.get("ordering_equivalence") or {}
    source_eq = timestamp_seek.get("source_ref_equivalence") or {}
    reasons = [
        "policy_eligible_date_timeline_window_evidence_unavailable",
        "sanitized_correctness_review_unavailable",
        "synthetic_or_local_rows_do_not_prove_promotion",
    ]
    if timestamp_seek.get("fallback_rows"):
        reasons.append("timestamp_seek_fallback_rows_present")
    if timestamp_seek.get("caps_hit"):
        reasons.append("cap_hits_present")
    if ordering_eq.get("not_equivalent") or source_eq.get("not_equivalent"):
        return _gate_status(
            "fail",
            ["timestamp_seek_equivalence_regression"] + reasons,
            counters=counters,
            metrics=_timestamp_seek_gate_metrics(timestamp_seek),
        )
    return _gate_status(
        "inconclusive",
        reasons,
        evaluated_case_count=0,
        counters=counters,
        metrics=_timestamp_seek_gate_metrics(timestamp_seek),
    )


def _timestamp_seek_gate_metrics(timestamp_seek: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled_rows": timestamp_seek.get("enabled_rows", 0),
        "planned_rows": timestamp_seek.get("planned_rows", 0),
        "fallback_rows": timestamp_seek.get("fallback_rows", 0),
        "parsed_bytes_median": timestamp_seek.get("parsed_bytes_median", 0),
        "elapsed_ms_median": timestamp_seek.get("elapsed_ms_median", 0),
        "elapsed_ms_p90": timestamp_seek.get("elapsed_ms_p90", 0),
        "parsed_byte_reduction_ratio_vs_baseline_median": timestamp_seek.get(
            "parsed_byte_reduction_ratio_vs_baseline_median", 0.0
        ),
        "parsed_byte_reduction_ratio_vs_manifest_median": timestamp_seek.get(
            "parsed_byte_reduction_ratio_vs_manifest_median", 0.0
        ),
        "ordering_equivalence": timestamp_seek.get("ordering_equivalence", {}),
        "source_ref_equivalence": timestamp_seek.get("source_ref_equivalence", {}),
    }


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


def _manifest_required_counter_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in _manifest_required_counter_keys():
        available = 0
        unavailable_reasons: set[str] = set()
        for row in rows:
            stats = row.get("manifest_shortlist") or {}
            if stats.get("enabled") and key in stats:
                available += 1
            else:
                unavailable_reasons.add("manifest_shortlist_not_enabled" if not stats.get("enabled") else "counter_not_available")
        summary[key] = {
            "available_rows": available,
            "unavailable_rows": max(0, len(rows) - available),
            "unavailable_reasons": sorted(unavailable_reasons),
        }
    return summary


def _manifest_required_counter_keys() -> tuple[str, ...]:
    return (
        "manifest_status",
        "build_bytes",
        "build_elapsed_ms",
        "refresh_elapsed_ms",
        "selected_files",
        "excluded_files",
        "selected_bytes",
        "excluded_bytes",
        "file_reduction_ratio",
        "byte_reduction_ratio",
        "weak_candidates_retained",
        "python_opened_files",
        "parsed_bytes",
        "elapsed_ms",
        "source_ref_equivalence",
        "no_evidence_scope_equivalence",
        "fallback_reason",
        "caps_hit",
        "cap_hit_labels",
    )


def _timestamp_seek_required_counter_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in _timestamp_seek_required_counter_keys():
        available = 0
        unavailable_reasons: set[str] = set()
        for row in rows:
            stats = row.get("timestamp_seek_prototype") or {}
            if stats.get("enabled") and key in stats:
                available += 1
            else:
                unavailable_reasons.add(
                    "timestamp_seek_prototype_not_enabled" if not stats.get("enabled") else "counter_not_available"
                )
        summary[key] = {
            "available_rows": available,
            "unavailable_rows": max(0, len(rows) - available),
            "unavailable_reasons": sorted(unavailable_reasons),
        }
    return summary


def _timestamp_seek_required_counter_keys() -> tuple[str, ...]:
    return (
        "build_bytes",
        "build_elapsed_ms",
        "no_change_refresh_elapsed_ms",
        "append_refresh_elapsed_ms",
        "query_elapsed_ms",
        "python_opened_files",
        "parsed_bytes",
        "records_considered",
        "sidecar_hits",
        "sidecar_misses",
        "fallback_reason",
        "refresh_count",
        "ordering_equivalence",
        "source_ref_equivalence",
        "caps_hit",
        "cap_hit_labels",
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
        "manifest_shortlist_rows_include_raw_paths": False,
        "manifest_shortlist_default_search_promotion": False,
    }


def _timestamp_seek_privacy_contract() -> dict[str, Any]:
    return {
        "timestamp_seek_rows_include_raw_paths": False,
        "timestamp_seek_rows_include_raw_text": False,
        "timestamp_seek_default_search_promotion": False,
    }


def _run_id() -> str:
    return f"measured_{uuid.uuid4().hex[:12]}"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[mid])
    return int(round((ordered[mid - 1] + ordered[mid]) / 2))


def _p90_int(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * 0.9)))
    return int(ordered[index])


def _median_float(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(float(ordered[mid]), 3)
    return round((float(ordered[mid - 1]) + float(ordered[mid])) / 2, 3)
