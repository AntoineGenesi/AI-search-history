from __future__ import annotations

import json
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import Manifest, build_manifest, shortlist_manifest
from .metric_contract import add_metric_contract, real_history_timestamp_seek_metrics
from .query import infer_query
from .real_history_bridge import validate_bridge_files
from .scanner import search
from .sources import Limits, PlannedRoot, SourcePlan, build_source_plan, enumerate_jsonl_files, stat_mtime, stat_size
from .timeutil import parse_now, timezone_from_args
from .timestamp_seek import TimestampSeekIndex, build_timestamp_seek_index, search_with_timestamp_seek


DEFAULT_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")
DEFAULT_PRIVATE_BRIDGE = Path(".private/jsh-049/bridge.json")
DEFAULT_RAW_OUTPUT_DIR = Path(".private/jsh-050/raw-runs")
GATE_STATUSES = {"pass", "fail", "inconclusive", "not_evaluated"}


@dataclass(frozen=True)
class SelectedFile:
    path: Path
    size: int
    record_count: int


def build_real_history_timestamp_seek_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    phase_started = time.perf_counter()
    manifest_path = Path(args.manifest).expanduser()
    bridge_path = Path(args.private_bridge).expanduser()
    try:
        bridge_result = validate_bridge_files(manifest_path, bridge_path)
    except (OSError, json.JSONDecodeError, ValueError):
        report = _invalid_report("bridge_or_manifest_unreadable", started)
        return {"real_history_timestamp_seek_benchmark": report}
    bridge_validation_ms = _elapsed_ms(phase_started)
    if not bridge_result.ok:
        report = _invalid_report("bridge_validation_failed", started, bridge_result.report)
        report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms
        return {"real_history_timestamp_seek_benchmark": report}

    manifest = _read_json(manifest_path)
    bridge = _read_json(bridge_path)
    cases = _timestamp_cases(manifest, bridge)
    case_errors = _timestamp_case_errors(cases)
    if case_errors:
        report = _invalid_report("timestamp_case_selection_invalid", started, bridge_result.report, case_errors)
        report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms
        return {"real_history_timestamp_seek_benchmark": report}

    limits = _limits_from_args(args)
    roots = _selected_roots(args, cases)
    root_labels = [f"root_{index + 1:03d}" for index, _root in enumerate(roots)]
    report = _base_report(
        selected_cases=cases,
        limits=limits,
        root_labels=root_labels,
        started=started,
        bridge_report=bridge_result.report,
    )
    report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms

    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    cwd = Path(args.cwd).expanduser().resolve()
    plan = build_source_plan(
        cwd=cwd,
        codex_home=Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex",
        claude_home=Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude",
        roots=roots,
        allow_arbitrary_root=True,
        limits=limits,
    )
    if plan.rejected_roots or not plan.roots:
        warnings = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
        return {"real_history_timestamp_seek_benchmark": _finish_without_scan(report, "root_rejected", warnings, started)}

    phase_started = time.perf_counter()
    files, enum_warnings = _enumerate_files(plan)
    report["phase_durations_ms"]["source_enumeration"] = _elapsed_ms(phase_started)
    safe_warnings = _safe_warnings(enum_warnings)

    phase_started = time.perf_counter()
    initial_manifest = build_manifest(plan, default_tz=default_tz)
    refreshed_manifest = build_manifest(plan, default_tz=default_tz, previous=initial_manifest)
    report["phase_durations_ms"]["manifest_build_and_refresh"] = _elapsed_ms(phase_started)

    phase_started = time.perf_counter()
    initial_timestamp_index = build_timestamp_seek_index(plan, default_tz=default_tz)
    refreshed_timestamp_index = build_timestamp_seek_index(plan, default_tz=default_tz, previous=initial_timestamp_index)
    report["phase_durations_ms"]["timestamp_index_build_and_refresh"] = _elapsed_ms(phase_started)

    rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    for case in cases:
        for repeat_index in range(max(1, args.repeat)):
            row, raw_row = _run_case_repeat(
                case=case,
                repeat_index=repeat_index,
                plan=plan,
                files=files,
                manifest_pair=(initial_manifest, refreshed_manifest),
                timestamp_index_pair=(initial_timestamp_index, refreshed_timestamp_index),
                default_tz=default_tz,
                now=now,
                cwd=str(cwd),
                base_cap_labels=_cap_hit_labels(safe_warnings),
            )
            rows.append(row)
            raw_rows.append(raw_row)

    row_warnings = [warning for row in rows for warning in row.get("warnings", [])]
    safe_warnings = _safe_warnings(safe_warnings + row_warnings)
    cap_hit_labels = sorted({label for row in rows for label in row.get("cap_hit_labels", [])} | set(_cap_hit_labels(safe_warnings)))
    report["warnings"] = safe_warnings
    report["rows"] = rows
    report["summary"] = _summarize(rows)
    report["aggregate"] = _aggregate(rows, report["phase_durations_ms"])
    report["status"] = "capped" if cap_hit_labels else "ok"
    report["caps_hit"] = bool(cap_hit_labels)
    report["cap_hit_labels"] = cap_hit_labels
    report["headline_findings"] = _headline_findings(report["summary"], report["aggregate"], cap_hit_labels)
    report["timestamp_sidecar_gate"] = _evaluate_timestamp_sidecar_gate(rows, report["summary"], report["aggregate"], cap_hit_labels)

    phase_started = time.perf_counter()
    raw_dir = Path(args.raw_output_dir).expanduser()
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{report['run_id']}.json"
    raw_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": report["run_id"],
                "privacy_note": "Ignored raw private timestamp seek benchmark evidence. Do not commit.",
                "rows": _jsonable_private(raw_rows),
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    report["phase_durations_ms"]["raw_evidence_write"] = _elapsed_ms(phase_started)
    report["raw_private_evidence"] = {
        "stored": True,
        "storage": "ignored_local_raw_run_artifacts",
        "run_file_label": f"{report['run_id']}.json",
        "committed": False,
    }
    report["total_wall_time_ms"] = _elapsed_ms(started)
    _add_metric_sections(report)
    return {"real_history_timestamp_seek_benchmark": report}


def _run_case_repeat(
    *,
    case: dict[str, Any],
    repeat_index: int,
    plan: SourcePlan,
    files: list[SelectedFile],
    manifest_pair: tuple[Manifest, Manifest],
    timestamp_index_pair: tuple[TimestampSeekIndex, TimestampSeekIndex],
    default_tz,
    now,
    cwd: str,
    base_cap_labels: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    row_started = time.perf_counter()
    intent = infer_query(str(case["query_text"]), now=now)
    expected_map = {(ref["path"], ref["line"]): ref["ref_id"] for ref in case.get("expected_refs", [])}
    must_ids = list(case.get("must_find_ref_ids") or [])

    baseline_started = time.perf_counter()
    baseline_result = search(plan, intent, default_tz=default_tz, cwd=cwd)
    baseline = _mode_stats(
        mode="baseline_full_scan",
        status="full_scan",
        started=baseline_started,
        result=baseline_result,
        expected_map=expected_map,
        expected_ref_ids=must_ids,
        files_opened=int(baseline_result.get("searched_files", 0)),
        records_parsed=sum(item.record_count for item in files),
        bytes_scanned=int(baseline_result.get("searched_bytes", 0)),
        bytes_parsed=int(baseline_result.get("searched_bytes", 0)),
        fallback_reason=None,
        extra={},
    )

    manifest = _manifest_planned_stats(
        manifest_pair=manifest_pair,
        intent=intent,
        plan=plan,
        default_tz=default_tz,
        cwd=cwd,
        baseline=baseline,
        baseline_result=baseline_result,
        expected_map=expected_map,
        expected_ref_ids=must_ids,
    )
    timestamp = _timestamp_seek_stats(
        timestamp_index_pair=timestamp_index_pair,
        manifest_pair=manifest_pair,
        intent=intent,
        plan=plan,
        default_tz=default_tz,
        cwd=cwd,
        baseline=baseline,
        manifest=manifest,
        baseline_result=baseline_result,
        expected_map=expected_map,
        expected_ref_ids=must_ids,
    )
    warnings = _safe_warnings(
        list(baseline.get("warnings", [])) + list(manifest.get("warnings", [])) + list(timestamp.get("warnings", []))
    )
    cap_labels = sorted(set(base_cap_labels) | set(_cap_hit_labels(warnings)) | set(manifest.get("cap_hit_labels", [])) | set(timestamp.get("cap_hit_labels", [])))
    row = {
        "case_id": case["case_id"],
        "repeat_index": repeat_index,
        "legitimacy_status": "reviewed_real_history_gold",
        "source_set_label": "private_bridge_selected_roots",
        "expected_outcome": case["expected_outcome"],
        "family": case["family"],
        "difficulty": case["difficulty"],
        "timestamp_applicable": True,
        "date_window_status": "present" if intent.date_window_start and intent.date_window_end else "missing",
        "expected_ref_ids": must_ids,
        "wall_time_ms": _elapsed_ms(row_started),
        "baseline_full_scan": baseline,
        "manifest_planned_scan": manifest,
        "timestamp_seek": timestamp,
        "source_ref_order_equivalence": {
            "manifest_vs_baseline": _ordered_equivalence(manifest, baseline),
            "timestamp_vs_baseline": _ordered_equivalence(timestamp, baseline),
            "timestamp_vs_expected": _expected_order_status(timestamp, must_ids),
            "baseline_vs_expected": _expected_order_status(baseline, must_ids),
        },
        "caps_hit": bool(cap_labels),
        "cap_hit_labels": cap_labels,
        "warnings": warnings,
    }
    row["counter_availability"] = _counter_availability(row)
    raw_row = {
        "case_id": case["case_id"],
        "repeat_index": repeat_index,
        "query_text": case["query_text"],
        "expected_refs": [
            {"ref_id": ref["ref_id"], "path": str(ref["path"]), "line": ref["line"]} for ref in case.get("expected_refs", [])
        ],
        "baseline_result": baseline_result,
        "manifest_raw": manifest.get("_raw_result"),
        "timestamp_raw": timestamp.get("_raw_result"),
        "sanitized_row": _strip_raw_keys(row),
    }
    return _strip_raw_keys(row), raw_row


def _manifest_planned_stats(
    *,
    manifest_pair: tuple[Manifest, Manifest],
    intent,
    plan: SourcePlan,
    default_tz,
    cwd: str,
    baseline: dict[str, Any],
    baseline_result: dict[str, Any],
    expected_map: dict[tuple[Path, int], str],
    expected_ref_ids: list[str],
) -> dict[str, Any]:
    initial, refreshed = manifest_pair
    shortlist = shortlist_manifest(initial, intent, cwd=cwd)
    selected_entries = shortlist.get("selected_entries") or []
    metrics = shortlist.get("metrics") or {}
    warnings = _safe_warnings(list(initial.warnings) + list(refreshed.warnings))
    cap_labels = _cap_hit_labels(warnings)
    base_extra = {
        "build_bytes": int(initial.metrics.get("bytes_parsed", 0)),
        "build_elapsed_ms": initial.metrics.get("elapsed_ms", 0),
        "refresh_elapsed_ms": refreshed.metrics.get("elapsed_ms", 0),
        "selected_files": int(metrics.get("selected_files", 0)),
        "excluded_files": int(metrics.get("excluded_files", 0)),
        "selected_bytes": int(metrics.get("selected_bytes", 0)),
        "excluded_bytes": int(metrics.get("excluded_bytes", 0)),
        "file_reduction_ratio": float(metrics.get("file_reduction_ratio", 0.0)),
        "byte_reduction_ratio": float(metrics.get("byte_reduction_ratio", 0.0)),
        "cap_hit_labels": cap_labels,
    }
    fallback_reason = _manifest_fallback_reason(initial, shortlist)
    if fallback_reason is None and not _selected_entries_current(selected_entries):
        fallback_reason = "manifest_stale_or_changed"
    if fallback_reason is None and not _selected_entries_allowed(selected_entries, plan):
        fallback_reason = "manifest_selection_rejected"
    if fallback_reason is not None:
        stats = dict(baseline)
        stats.update(base_extra)
        stats.update(
            {
                "mode": "manifest_planned_scan",
                "status": "fallback",
                "fallback_reason": fallback_reason,
                "source_ref_equivalence_vs_baseline": "fallback_not_compared",
                "order_equivalence_vs_baseline": "fallback_not_compared",
                "warnings": warnings,
            }
        )
        return stats
    narrowed_plan = _source_plan_from_manifest_selection(plan, selected_entries)
    started = time.perf_counter()
    result = search(narrowed_plan, intent, default_tz=default_tz, cwd=cwd)
    stats = _mode_stats(
        mode="manifest_planned_scan",
        status="planned_scan",
        started=started,
        result=result,
        expected_map=expected_map,
        expected_ref_ids=expected_ref_ids,
        files_opened=int(result.get("searched_files", 0)),
        records_parsed=sum(int((item.get("entry") or {}).get("record_count", 0)) for item in selected_entries),
        bytes_scanned=int(result.get("searched_bytes", 0)),
        bytes_parsed=int(result.get("searched_bytes", 0)),
        fallback_reason=None,
        extra=base_extra,
    )
    stats["warnings"] = _safe_warnings(warnings + list(result.get("warnings", [])))
    stats["cap_hit_labels"] = sorted(set(stats.get("cap_hit_labels", [])) | set(_cap_hit_labels(stats["warnings"])))
    stats["source_ref_equivalence_vs_baseline"] = _ordered_equivalence(stats, baseline)
    stats["order_equivalence_vs_baseline"] = _ordered_equivalence(stats, baseline)
    stats["_raw_result"] = result
    return stats


def _timestamp_seek_stats(
    *,
    timestamp_index_pair: tuple[TimestampSeekIndex, TimestampSeekIndex],
    manifest_pair: tuple[Manifest, Manifest],
    intent,
    plan: SourcePlan,
    default_tz,
    cwd: str,
    baseline: dict[str, Any],
    manifest: dict[str, Any],
    baseline_result: dict[str, Any],
    expected_map: dict[tuple[Path, int], str],
    expected_ref_ids: list[str],
) -> dict[str, Any]:
    initial, refreshed = timestamp_index_pair
    selected_paths, planning_source, selection_reason = _timestamp_seek_selected_paths(
        manifest_pair=manifest_pair,
        intent=intent,
        plan=plan,
        cwd=cwd,
    )
    warnings = _safe_warnings(list(initial.warnings) + list(refreshed.warnings))
    cap_labels = _cap_hit_labels(warnings)
    extra = {
        "planning_source": planning_source,
        "selection_reason": selection_reason,
        "build_bytes": int(initial.metrics.get("build_bytes", 0)),
        "build_elapsed_ms": initial.metrics.get("build_elapsed_ms", 0),
        "no_change_refresh_elapsed_ms": refreshed.metrics.get("refresh_elapsed_ms", 0),
        "append_refresh_elapsed_ms": 0,
        "refresh_count": int(refreshed.metrics.get("refresh_count", 0)),
        "no_change_refresh_count": int(refreshed.metrics.get("no_change_refresh_count", 0)),
        "append_refresh_count": int(refreshed.metrics.get("append_refresh_count", 0)),
        "stale_refresh_count": int(refreshed.metrics.get("stale_refresh_count", 0)),
        "sidecar_hits": 0,
        "sidecar_misses": 0,
        "records_considered": 0,
        "cap_hit_labels": cap_labels,
    }
    fallback_reason = None
    if not intent.date_window_start or not intent.date_window_end:
        fallback_reason = "timestamp_window_absent"
    elif not refreshed.files:
        fallback_reason = "timestamp_index_absent"
    elif selected_paths is not None and not selected_paths:
        fallback_reason = "timestamp_selection_empty"
    if fallback_reason is not None:
        stats = dict(baseline)
        stats.update(extra)
        stats.update(
            {
                "mode": "timestamp_seek",
                "status": "fallback",
                "fallback_reason": fallback_reason,
                "source_ref_equivalence_vs_baseline": "fallback_not_compared",
                "order_equivalence_vs_baseline": "fallback_not_compared",
                "warnings": warnings,
            }
        )
        _add_reductions(stats, baseline=baseline, manifest=manifest)
        return stats
    started = time.perf_counter()
    result = search_with_timestamp_seek(
        refreshed,
        plan,
        intent,
        default_tz=default_tz,
        cwd=cwd,
        selected_paths=selected_paths,
    )
    if result.get("fallback_reason"):
        stats = dict(baseline)
        stats.update(extra)
        stats.update(
            {
                "mode": "timestamp_seek",
                "status": "fallback",
                "fallback_reason": result.get("fallback_reason"),
                "elapsed_ms": int(result.get("elapsed_ms", 0)),
                "source_ref_equivalence_vs_baseline": "fallback_not_compared",
                "order_equivalence_vs_baseline": "fallback_not_compared",
                "warnings": _safe_warnings(warnings + list(result.get("warnings", []))),
            }
        )
        _add_reductions(stats, baseline=baseline, manifest=manifest)
        return stats
    extra.update(
        {
            "sidecar_hits": int(result.get("sidecar_hits", 0)),
            "sidecar_misses": int(result.get("sidecar_misses", 0)),
            "records_considered": int(result.get("records_considered", 0)),
        }
    )
    stats = _mode_stats(
        mode="timestamp_seek",
        status="planned_seek",
        started=started,
        result=result,
        expected_map=expected_map,
        expected_ref_ids=expected_ref_ids,
        files_opened=int(result.get("searched_files", 0)),
        records_parsed=int(result.get("records_considered", 0)),
        bytes_scanned=int(result.get("searched_bytes", 0)),
        bytes_parsed=int(result.get("searched_bytes", 0)),
        fallback_reason=None,
        extra=extra,
    )
    stats["elapsed_ms"] = int(result.get("elapsed_ms", stats.get("elapsed_ms", 0)))
    stats["warnings"] = _safe_warnings(warnings + list(result.get("warnings", [])))
    stats["cap_hit_labels"] = sorted(set(stats.get("cap_hit_labels", [])) | set(_cap_hit_labels(stats["warnings"])))
    stats["source_ref_equivalence_vs_baseline"] = _ordered_equivalence(stats, baseline)
    stats["order_equivalence_vs_baseline"] = _ordered_equivalence(stats, baseline)
    stats["_raw_result"] = result
    _add_reductions(stats, baseline=baseline, manifest=manifest)
    return stats


def _mode_stats(
    *,
    mode: str,
    status: str,
    started: float,
    result: dict[str, Any],
    expected_map: dict[tuple[Path, int], str],
    expected_ref_ids: list[str],
    files_opened: int,
    records_parsed: int,
    bytes_scanned: int,
    bytes_parsed: int,
    fallback_reason: str | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    candidate_ref_ids, unmapped_count, candidate_count = _candidate_ref_ids(result, expected_map)
    matched_expected = [ref_id for ref_id in candidate_ref_ids if ref_id in set(expected_ref_ids)]
    output_bytes = len(json.dumps(_sanitized_result_shape(result, candidate_ref_ids), ensure_ascii=False, sort_keys=True).encode("utf-8"))
    stats = {
        "mode": mode,
        "status": status,
        "elapsed_ms": _elapsed_ms(started),
        "files_opened": files_opened,
        "records_parsed": records_parsed,
        "bytes_scanned": bytes_scanned,
        "bytes_parsed": bytes_parsed,
        "output_bytes": output_bytes,
        "context_bytes": output_bytes,
        "output_context_bytes": output_bytes,
        "candidate_count": candidate_count,
        "mapped_candidate_count": len(candidate_ref_ids),
        "unmapped_candidate_count": unmapped_count,
        "top_candidate_ref_ids": candidate_ref_ids[:10],
        "top_candidate_source_refs": [f"sanitized_ref:{ref_id}" for ref_id in candidate_ref_ids[:10]],
        "expected_ref_match_count": len(set(matched_expected)),
        "expected_ref_order_status": _expected_order_status({"top_candidate_ref_ids": candidate_ref_ids}, expected_ref_ids),
        "fallback_reason": fallback_reason,
        "warnings": _safe_warnings(result.get("warnings", [])),
        "cap_hit_labels": _cap_hit_labels(_safe_warnings(result.get("warnings", []))),
    }
    stats.update(extra)
    return stats


def _candidate_ref_ids(result: dict[str, Any], expected_map: dict[tuple[Path, int], str]) -> tuple[list[str], int, int]:
    ids: list[str] = []
    unmapped = 0
    count = 0
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            count += 1
            path_value = match.get("path")
            line_value = match.get("line")
            key = (Path(path_value), int(line_value)) if isinstance(path_value, str) and isinstance(line_value, int) else None
            if key is not None and key in expected_map:
                ids.append(expected_map[key])
            else:
                unmapped += 1
    return ids, unmapped, count


def _add_reductions(stats: dict[str, Any], *, baseline: dict[str, Any], manifest: dict[str, Any]) -> None:
    stats["parsed_byte_reduction_ratio_vs_baseline"] = _reduction_ratio(stats.get("bytes_parsed"), baseline.get("bytes_parsed"))
    stats["parsed_byte_reduction_ratio_vs_manifest"] = _reduction_ratio(stats.get("bytes_parsed"), manifest.get("bytes_parsed"))
    stats["elapsed_reduction_ratio_vs_baseline"] = _reduction_ratio(stats.get("elapsed_ms"), baseline.get("elapsed_ms"))
    stats["elapsed_reduction_ratio_vs_manifest"] = _reduction_ratio(stats.get("elapsed_ms"), manifest.get("elapsed_ms"))


def _timestamp_seek_selected_paths(
    *,
    manifest_pair: tuple[Manifest, Manifest],
    intent,
    plan: SourcePlan,
    cwd: str,
) -> tuple[set[str] | None, str, str]:
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
    if not manifest.entries:
        return "manifest_empty"
    if _cap_hit_labels(_safe_warnings(list(manifest.warnings))):
        return "manifest_build_capped"
    if int(metrics.get("selected_files", 0)) <= 0:
        return "manifest_empty_selection"
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
    allowed_paths = {str(path) for path, _size, _warnings in enumerate_jsonl_files(plan)}
    selected_paths = {str((item.get("entry") or {}).get("path")) for item in selected_entries if isinstance(item, dict)}
    return selected_paths <= allowed_paths


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
        hard_cap_files=max(1, len(roots)),
        hard_cap_bytes=max(1, selected_bytes),
    )
    return SourcePlan(roots=roots, rejected_roots=[], warnings=list(plan.warnings), blind_spots=list(plan.blind_spots), limits=limits)


def _timestamp_cases(manifest: dict[str, Any], bridge: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_by_id = {
        case.get("case_id"): case
        for case in manifest.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    bridge_by_id = {
        case.get("case_id"): case for case in bridge.get("cases", []) if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    selected: list[dict[str, Any]] = []
    for case_id, manifest_case in manifest_by_id.items():
        if (manifest_case.get("downstream_applicability") or {}).get("timestamp_seek") is not True:
            continue
        bridge_case = bridge_by_id.get(case_id) or {}
        refs = [ref for ref in bridge_case.get("refs", []) if isinstance(ref, dict)]
        query_text = next((ref.get("query_text") for ref in refs if isinstance(ref.get("query_text"), str) and ref.get("query_text")), None)
        expected_refs = []
        for ref in refs:
            path_value = ref.get("path")
            line_value = ref.get("line")
            ref_id = ref.get("ref_id")
            if isinstance(path_value, str) and isinstance(line_value, int) and isinstance(ref_id, str):
                expected_refs.append({"ref_id": ref_id, "path": Path(path_value), "line": line_value})
        selected.append(
            {
                "case_id": case_id,
                "family": manifest_case.get("family"),
                "difficulty": manifest_case.get("difficulty"),
                "expected_outcome": _normalize_outcome(manifest_case.get("expected_outcome")),
                "query_text": query_text,
                "expected_refs": expected_refs,
                "must_find_ref_ids": [
                    ref.get("ref_id")
                    for ref in manifest_case.get("must_find_refs", [])
                    if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
                ],
            }
        )
    return sorted(selected, key=lambda item: str(item["case_id"]))


def _timestamp_case_errors(cases: list[dict[str, Any]]) -> list[str]:
    errors = []
    if len(cases) != 6:
        errors.append("timestamp_seek_case_count_must_be_6")
    for case in cases:
        if not case.get("query_text"):
            errors.append(f"{case.get('case_id')}: missing_private_query_text")
        if not case.get("expected_refs"):
            errors.append(f"{case.get('case_id')}: missing_expected_refs")
        if not case.get("must_find_ref_ids"):
            errors.append(f"{case.get('case_id')}: missing_must_find_refs")
    return errors


def _selected_roots(args, cases: list[dict[str, Any]]) -> list[Path]:
    if args.root:
        return [Path(item).expanduser() for item in args.root]
    return sorted({ref["path"] for case in cases for ref in case.get("expected_refs", [])})


def _enumerate_files(plan: SourcePlan) -> tuple[list[SelectedFile], list[str]]:
    files: list[SelectedFile] = []
    warnings: list[str] = []
    iterator = enumerate_jsonl_files(plan)
    while True:
        try:
            path, size, enum_warnings = next(iterator)
        except StopIteration as stopped:
            if stopped.value is not None:
                warnings = list(stopped.value)
            break
        warnings = list(enum_warnings)
        files.append(SelectedFile(path=path, size=size, record_count=_line_count(path)))
    return files, warnings


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _line in handle)
    except OSError:
        return 0


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode, key in (
        ("baseline_full_scan", "baseline_full_scan"),
        ("manifest_planned_scan", "manifest_planned_scan"),
        ("timestamp_seek", "timestamp_seek"),
    ):
        items = [row[key] for row in rows]
        if not items:
            continue
        summary[mode] = {
            "runs": len(items),
            "cases": len({row["case_id"] for row in rows}),
            "status_counts": _counts(item.get("status") for item in items),
            "median_elapsed_ms": _median(item.get("elapsed_ms") for item in items),
            "p90_elapsed_ms": _p90(item.get("elapsed_ms") for item in items),
            "median_files_opened": _median(item.get("files_opened") for item in items),
            "median_records_parsed": _median(item.get("records_parsed") for item in items),
            "median_bytes_scanned": _median(item.get("bytes_scanned") for item in items),
            "median_bytes_parsed": _median(item.get("bytes_parsed") for item in items),
            "p90_bytes_parsed": _p90(item.get("bytes_parsed") for item in items),
            "median_output_context_bytes": _median(item.get("output_context_bytes") for item in items),
            "median_output_context_tokens_approx": _ceil_div4(_median(item.get("output_context_bytes") for item in items)),
            "median_candidate_count": _median(item.get("candidate_count") for item in items),
            "expected_ref_match_runs": sum(1 for item in items if item.get("expected_ref_match_count", 0) > 0),
            "expected_ref_order_status": _counts(item.get("expected_ref_order_status") for item in items),
            "cap_hit_labels": sorted({label for item in items for label in item.get("cap_hit_labels", [])}),
        }
        if mode == "timestamp_seek":
            summary[mode].update(
                {
                    "median_parsed_byte_reduction_ratio_vs_baseline": _median(
                        item.get("parsed_byte_reduction_ratio_vs_baseline") for item in items
                    ),
                    "p90_parsed_byte_reduction_ratio_vs_baseline": _p90(
                        item.get("parsed_byte_reduction_ratio_vs_baseline") for item in items
                    ),
                    "median_elapsed_reduction_ratio_vs_baseline": _median(
                        item.get("elapsed_reduction_ratio_vs_baseline") for item in items
                    ),
                    "p90_elapsed_reduction_ratio_vs_baseline": _p90(
                        item.get("elapsed_reduction_ratio_vs_baseline") for item in items
                    ),
                    "median_parsed_byte_reduction_ratio_vs_manifest": _median(
                        item.get("parsed_byte_reduction_ratio_vs_manifest") for item in items
                    ),
                    "p90_parsed_byte_reduction_ratio_vs_manifest": _p90(
                        item.get("parsed_byte_reduction_ratio_vs_manifest") for item in items
                    ),
                    "median_elapsed_reduction_ratio_vs_manifest": _median(
                        item.get("elapsed_reduction_ratio_vs_manifest") for item in items
                    ),
                    "p90_elapsed_reduction_ratio_vs_manifest": _p90(item.get("elapsed_reduction_ratio_vs_manifest") for item in items),
                    "sidecar_hits": sum(int(item.get("sidecar_hits", 0)) for item in items),
                    "sidecar_misses": sum(int(item.get("sidecar_misses", 0)) for item in items),
                    "refresh_count": sum(int(item.get("refresh_count", 0)) for item in items),
                    "no_change_refresh_count": sum(int(item.get("no_change_refresh_count", 0)) for item in items),
                    "append_refresh_count": sum(int(item.get("append_refresh_count", 0)) for item in items),
                    "stale_refresh_count": sum(int(item.get("stale_refresh_count", 0)) for item in items),
                    "fallback_reasons": _counts(item.get("fallback_reason") for item in items if item.get("fallback_reason")),
                    "planning_sources": _counts(item.get("planning_source") for item in items),
                }
            )
    summary["equivalence"] = {
        "manifest_vs_baseline": _counts((row.get("source_ref_order_equivalence") or {}).get("manifest_vs_baseline") for row in rows),
        "timestamp_vs_baseline": _counts((row.get("source_ref_order_equivalence") or {}).get("timestamp_vs_baseline") for row in rows),
        "timestamp_vs_expected": _counts((row.get("source_ref_order_equivalence") or {}).get("timestamp_vs_expected") for row in rows),
        "baseline_vs_expected": _counts((row.get("source_ref_order_equivalence") or {}).get("baseline_vs_expected") for row in rows),
    }
    return summary


def _aggregate(rows: list[dict[str, Any]], phase_durations: dict[str, Any]) -> dict[str, Any]:
    return {
        "runs": len(rows),
        "cases": len({row["case_id"] for row in rows}),
        "phase_durations_ms": dict(phase_durations),
        "modes": {
            mode: {
                "files_opened": sum(int(row[key].get("files_opened", 0)) for row in rows),
                "records_parsed": sum(int(row[key].get("records_parsed", 0)) for row in rows),
                "bytes_scanned": sum(int(row[key].get("bytes_scanned", 0)) for row in rows),
                "bytes_parsed": sum(int(row[key].get("bytes_parsed", 0)) for row in rows),
                "output_context_bytes": sum(int(row[key].get("output_context_bytes", 0)) for row in rows),
                "candidate_count": sum(int(row[key].get("candidate_count", 0)) for row in rows),
            }
            for mode, key in (
                ("baseline_full_scan", "baseline_full_scan"),
                ("manifest_planned_scan", "manifest_planned_scan"),
                ("timestamp_seek", "timestamp_seek"),
            )
        },
        "fallback_reasons": _counts(row["timestamp_seek"].get("fallback_reason") for row in rows if row["timestamp_seek"].get("fallback_reason")),
        "caps_hit": any(row.get("caps_hit") for row in rows),
        "cap_hit_labels": sorted({label for row in rows for label in row.get("cap_hit_labels", [])}),
        "required_counters": _required_counter_summary(rows),
        "source_ref_order_equivalence": {
            "manifest_vs_baseline": _counts((row.get("source_ref_order_equivalence") or {}).get("manifest_vs_baseline") for row in rows),
            "timestamp_vs_baseline": _counts((row.get("source_ref_order_equivalence") or {}).get("timestamp_vs_baseline") for row in rows),
            "timestamp_vs_expected": _counts((row.get("source_ref_order_equivalence") or {}).get("timestamp_vs_expected") for row in rows),
            "baseline_vs_expected": _counts((row.get("source_ref_order_equivalence") or {}).get("baseline_vs_expected") for row in rows),
        },
        "no_evidence_scope_equivalence": _no_evidence_scope_equivalence(rows),
        "memory": {"status": "not_measured", "reason": "rss_not_measured_by_benchmark_real_history_timestamp_seek"},
    }


def _evaluate_timestamp_sidecar_gate(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    aggregate: dict[str, Any],
    cap_hit_labels: list[str],
) -> dict[str, Any]:
    counters = aggregate.get("required_counters")
    if not rows:
        return _gate_status("not_evaluated", ["no_benchmark_rows"], counters=counters)
    eq = aggregate.get("source_ref_order_equivalence") or {}
    timestamp_vs_baseline = eq.get("timestamp_vs_baseline") or {}
    timestamp_vs_expected = eq.get("timestamp_vs_expected") or {}
    reasons = ["real_history_case_count_below_old_floor_20", "six_case_timestamp_slice_below_promotion_floor"]
    if timestamp_vs_baseline.get("not_equivalent"):
        return _gate_status(
            "fail",
            ["timestamp_seek_source_ref_or_order_regression_vs_baseline"] + reasons,
            evaluated_case_count=len({row["case_id"] for row in rows}),
            counters=counters,
            metrics=_gate_metrics(summary, aggregate),
        )
    timestamp = summary.get("timestamp_seek") or {}
    fallback_rows = int((timestamp.get("status_counts") or {}).get("fallback", 0))
    if fallback_rows:
        reasons.append("timestamp_seek_fallback_rows_present")
    if cap_hit_labels:
        reasons.append("cap_hits_present")
    if any(row.get("date_window_status") == "missing" for row in rows):
        reasons.append("missing_date_windows_present")
    if timestamp_vs_expected.get("missing_expected_refs") or timestamp_vs_expected.get("partial_expected_refs"):
        reasons.append("expected_sanitized_bridge_refs_not_recovered_in_all_rows")
    baseline_expected = ((summary.get("equivalence") or {}).get("baseline_vs_expected") or {})
    if baseline_expected.get("missing_expected_refs") or baseline_expected.get("partial_expected_refs"):
        reasons.append("baseline_correctness_against_bridge_refs_not_established")
    median_ok = (
        float(timestamp.get("median_parsed_byte_reduction_ratio_vs_baseline", 0.0)) >= 0.5
        or float(timestamp.get("median_elapsed_reduction_ratio_vs_baseline", 0.0)) >= 0.5
    )
    p90_ok = (
        float(timestamp.get("p90_parsed_byte_reduction_ratio_vs_baseline", 0.0)) >= 0.3
        or float(timestamp.get("p90_elapsed_reduction_ratio_vs_baseline", 0.0)) >= 0.3
    )
    manifest_median_ok = (
        float(timestamp.get("median_parsed_byte_reduction_ratio_vs_manifest", 0.0)) >= 0.5
        or float(timestamp.get("median_elapsed_reduction_ratio_vs_manifest", 0.0)) >= 0.5
    )
    manifest_p90_ok = (
        float(timestamp.get("p90_parsed_byte_reduction_ratio_vs_manifest", 0.0)) >= 0.3
        or float(timestamp.get("p90_elapsed_reduction_ratio_vs_manifest", 0.0)) >= 0.3
    )
    if not median_ok:
        reasons.append("median_reduction_threshold_vs_baseline_not_met")
    if not p90_ok:
        reasons.append("p90_reduction_threshold_vs_baseline_not_met")
    if not manifest_median_ok:
        reasons.append("median_reduction_threshold_vs_manifest_not_met")
    if not manifest_p90_ok:
        reasons.append("p90_reduction_threshold_vs_manifest_not_met")
    all_equivalent = timestamp_vs_baseline.get("equivalent") == len(rows)
    no_blockers = not fallback_rows and not cap_hit_labels and not any(
        reason
        for reason in reasons
        if reason
        not in {
            "real_history_case_count_below_old_floor_20",
            "six_case_timestamp_slice_below_promotion_floor",
        }
    )
    if all_equivalent and median_ok and p90_ok and manifest_median_ok and manifest_p90_ok and no_blockers:
        return _gate_status(
            "pass",
            reasons + ["narrow_six_case_selected_history_pass_only_not_default_sidecar"],
            evaluated_case_count=6,
            counters=counters,
            metrics=_gate_metrics(summary, aggregate),
        )
    return _gate_status(
        "inconclusive",
        reasons + ["no_default_or_persistent_sidecar_promoted"],
        evaluated_case_count=len({row["case_id"] for row in rows}),
        counters=counters,
        metrics=_gate_metrics(summary, aggregate),
    )


def _gate_metrics(summary: dict[str, Any], aggregate: dict[str, Any]) -> dict[str, Any]:
    timestamp = summary.get("timestamp_seek") or {}
    return {
        "timestamp_summary": timestamp,
        "source_ref_order_equivalence": aggregate.get("source_ref_order_equivalence", {}),
        "no_evidence_scope_equivalence": aggregate.get("no_evidence_scope_equivalence", {}),
        "fallback_reasons": aggregate.get("fallback_reasons", {}),
        "cap_hit_labels": aggregate.get("cap_hit_labels", []),
    }


def _headline_findings(summary: dict[str, Any], aggregate: dict[str, Any], cap_hit_labels: list[str]) -> list[str]:
    timestamp = summary.get("timestamp_seek") or {}
    findings = [
        "timestamp seek was evaluated only on the six reviewed real date/timeline cases from the final manifest",
        "committed outputs identify candidates only by sanitized bridge ref IDs; raw query text, paths, snippets, and package results stay in ignored raw artifacts",
    ]
    if timestamp:
        findings.append(
            "timestamp seek median reductions vs baseline: "
            f"bytes={timestamp.get('median_parsed_byte_reduction_ratio_vs_baseline')} "
            f"elapsed={timestamp.get('median_elapsed_reduction_ratio_vs_baseline')}"
        )
        findings.append(
            "timestamp seek median reductions vs manifest-planned scan: "
            f"bytes={timestamp.get('median_parsed_byte_reduction_ratio_vs_manifest')} "
            f"elapsed={timestamp.get('median_elapsed_reduction_ratio_vs_manifest')}"
        )
    eq = (aggregate.get("source_ref_order_equivalence") or {}).get("timestamp_vs_baseline") or {}
    if eq.get("not_equivalent"):
        findings.append("timestamp seek changed sanitized source-ref order versus baseline in at least one row")
    if cap_hit_labels:
        findings.append(f"cap hits affected this run: {', '.join(cap_hit_labels)}")
    findings.append("no default or persistent timestamp sidecar is promoted by this diagnostic")
    return findings


def _invalid_report(
    reason: str,
    started: float,
    bridge_report: dict[str, Any] | None = None,
    fixture_errors: list[str] | None = None,
) -> dict[str, Any]:
    report = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex[:12],
        "status": "invalid_bridge" if "bridge" in reason else "invalid_fixture",
        "data_source": "private_real_history_sanitized_aggregate",
        "manifest_case_count": 0,
        "case_count": 0,
        "below_floor_case_count_caveat": "18 reviewed cases is below the old 20-50 target floor; this task uses only the 6 timestamp-applicable cases.",
        "root_labels": [],
        "limits": {},
        "comparison_scope": _comparison_scope(),
        "privacy_contract": _privacy_contract(),
        "bridge_validation": _safe_bridge_summary(bridge_report),
        "phase_durations_ms": _empty_phase_durations(),
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [reason],
        "fixture_errors": fixture_errors or [],
        "caps_hit": False,
        "cap_hit_labels": [],
        "raw_private_evidence": {"stored": False, "storage": "ignored_local_raw_run_artifacts", "committed": False},
        "rows": [],
        "summary": {},
        "aggregate": {},
        "timestamp_sidecar_gate": _gate_status("not_evaluated", [reason, "no_benchmark_rows"]),
    }
    return _add_metric_sections(report)


def _base_report(
    *,
    selected_cases: list[dict[str, Any]],
    limits: Limits,
    root_labels: list[str],
    started: float,
    bridge_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex[:12],
        "status": "pending",
        "data_source": "private_real_history_sanitized_aggregate",
        "manifest_case_count": 18,
        "case_count": len(selected_cases),
        "case_families": sorted({case["family"] for case in selected_cases}),
        "below_floor_case_count_caveat": "18 reviewed cases is below the old 20-50 target floor; this task uses only the 6 timestamp-applicable cases.",
        "root_labels": root_labels,
        "limits": limits.to_dict(),
        "comparison_scope": _comparison_scope(),
        "privacy_contract": _privacy_contract(),
        "bridge_validation": _safe_bridge_summary(bridge_report),
        "phase_durations_ms": _empty_phase_durations(),
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [],
        "caps_hit": False,
        "cap_hit_labels": [],
        "raw_private_evidence": {"stored": False, "storage": "ignored_local_raw_run_artifacts", "committed": False},
        "rows": [],
        "summary": {},
        "aggregate": {},
        "timestamp_sidecar_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
    }


def _finish_without_scan(report: dict[str, Any], status: str, warnings: list[str], started: float) -> dict[str, Any]:
    report["status"] = status
    report["total_wall_time_ms"] = _elapsed_ms(started)
    report["warnings"] = warnings
    report["caps_hit"] = False
    report["cap_hit_labels"] = _cap_hit_labels(warnings)
    report["timestamp_sidecar_gate"] = _gate_status(status="not_evaluated", reasons=[f"benchmark_status_{status}", "no_benchmark_rows"])
    return _add_metric_sections(report)


def _add_metric_sections(report: dict[str, Any]) -> dict[str, Any]:
    return add_metric_contract(report, real_history_timestamp_seek_metrics(report))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _limits_from_args(args) -> Limits:
    return Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files or args.max_files,
        hard_cap_bytes=args.hard_cap_bytes or args.max_bytes,
    )


def _normalize_outcome(value: Any) -> str:
    return str(value) if value in {"evidence_found", "ambiguous_candidates", "no_evidence"} else "evidence_found"


def _comparison_scope() -> dict[str, Any]:
    return {
        "case_selection": "exactly the 6 final manifest cases where downstream_applicability.timestamp_seek is true",
        "root_selection": "private bridge expected-ref files unless --root overrides them",
        "comparison_modes": {
            "baseline_full_scan": "current JSONL package scan over selected private roots",
            "manifest_planned_scan": "manifest-shortlisted package scan when the manifest selection is current and allowed",
            "timestamp_seek": "transient in-memory timestamp/line/byte-offset table seek using manifest selection where safe",
        },
        "promotion_scope": "diagnostic gate only; no default or persistent timestamp sidecar is created",
    }


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "local_usernames_included": False,
        "raw_session_ids_included": False,
        "source_lines_included": False,
        "vectors_or_indexes_included": False,
        "default_or_persistent_sidecar_created": False,
        "raw_private_evidence_ignored": True,
    }


def _safe_bridge_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    payload = (report or {}).get("real_history_benchmark_bridge") or {}
    allowed = {
        "schema_version",
        "status",
        "manifest_case_count",
        "benchmarkable_reviewed_case_count",
        "expected_case_count",
        "bridge_case_count",
        "accepted_case_count",
        "quarantined_case_count",
        "rejected_case_count",
        "resolved_ref_count",
        "source_structure_counts",
        "classifier_confidence_counts",
        "query_text_check_counts",
        "rejection_counts",
        "error_count",
        "below_floor_case_count_caveat",
        "privacy_contract",
    }
    return {key: payload.get(key) for key in sorted(allowed) if key in payload}


def _counter_availability(row: dict[str, Any]) -> dict[str, dict[str, str]]:
    availability = {}
    for key in _required_counter_keys():
        mode, _, subkey = key.partition(".")
        value = (row.get(mode) or {}).get(subkey) if subkey else row.get(mode)
        if value is None:
            availability[key] = {"status": "unavailable", "reason": "counter_not_available"}
        else:
            availability[key] = {"status": "available"}
    return availability


def _required_counter_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {}
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
    mode_keys = []
    for mode in ("baseline_full_scan", "manifest_planned_scan", "timestamp_seek"):
        for key in (
            "elapsed_ms",
            "files_opened",
            "records_parsed",
            "bytes_scanned",
            "bytes_parsed",
            "output_bytes",
            "context_bytes",
            "output_context_bytes",
            "candidate_count",
            "expected_ref_match_count",
            "fallback_reason",
            "cap_hit_labels",
        ):
            mode_keys.append(f"{mode}.{key}")
    mode_keys.extend(
        [
            "source_ref_order_equivalence",
            "caps_hit",
            "cap_hit_labels",
        ]
    )
    return tuple(mode_keys)


def _no_evidence_scope_equivalence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    no_evidence_rows = [row for row in rows if row.get("expected_outcome") == "no_evidence"]
    if not no_evidence_rows:
        return {
            "status": "not_applicable",
            "reason": "selected_timestamp_slice_has_no_no_evidence_cases",
            "evaluated_rows": 0,
        }
    return {
        "status": "available",
        "evaluated_rows": len(no_evidence_rows),
        "timestamp_vs_baseline": _counts(
            (row.get("source_ref_order_equivalence") or {}).get("timestamp_vs_baseline") for row in no_evidence_rows
        ),
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
        "evidence_basis": "private_real_history_sanitized_aggregate",
    }
    if counters is not None:
        payload["required_counters"] = counters
    if metrics is not None:
        payload["metrics"] = metrics
    return payload


def _ordered_equivalence(left: dict[str, Any], right: dict[str, Any]) -> str:
    left_ids = left.get("top_candidate_ref_ids") or []
    right_ids = right.get("top_candidate_ref_ids") or []
    if not left_ids and not right_ids:
        return "equivalent_no_refs"
    return "equivalent" if left_ids == right_ids else "not_equivalent"


def _expected_order_status(stats: dict[str, Any], expected_ref_ids: list[str]) -> str:
    candidate_ids = stats.get("top_candidate_ref_ids") or []
    expected = list(expected_ref_ids)
    if not expected:
        return "no_expected_refs"
    if candidate_ids[: len(expected)] == expected:
        return "expected_refs_first_in_order"
    if set(expected).issubset(set(candidate_ids)):
        return "expected_refs_present_out_of_order"
    if set(candidate_ids) & set(expected):
        return "partial_expected_refs"
    return "missing_expected_refs"


def _sanitized_result_shape(result: dict[str, Any], candidate_ref_ids: list[str]) -> dict[str, Any]:
    return {
        "searched_files": result.get("searched_files", 0),
        "searched_bytes": result.get("searched_bytes", 0),
        "candidate_ref_ids": candidate_ref_ids[:10],
        "candidate_count": sum(len(group.get("matches", [])) for group in result.get("groups", [])),
        "warnings": _safe_warnings(result.get("warnings", [])),
    }


def _strip_raw_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_raw_keys(item) for key, item in value.items() if not key.startswith("_raw")}
    if isinstance(value, list):
        return [_strip_raw_keys(item) for item in value]
    return value


def _jsonable_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable_private(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_private(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable_private(item) for item in value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _empty_phase_durations() -> dict[str, int]:
    return {
        "bridge_validation": 0,
        "source_enumeration": 0,
        "manifest_build_and_refresh": 0,
        "timestamp_index_build_and_refresh": 0,
        "raw_evidence_write": 0,
    }


def _safe_warnings(warnings: list[str]) -> list[str]:
    safe: list[str] = []
    for warning in warnings:
        lowered = str(warning).lower()
        if lowered.startswith(("stopped_at_", "missing_root", "root_rejected_")):
            safe.append(str(warning))
        elif "arbitrary root enabled" in lowered:
            safe.append("arbitrary_root_enabled_with_hard_caps")
        elif "max elapsed cap" in lowered:
            safe.append("stopped_at_max_elapsed_cap")
        elif "max file cap" in lowered:
            safe.append("stopped_at_max_file_cap")
        elif "max byte cap" in lowered:
            safe.append("stopped_at_max_byte_cap")
        elif "max match cap" in lowered:
            safe.append("stopped_at_max_match_cap")
        elif "weak partial generic candidates were reservoir-capped" in lowered:
            safe.append("weak_partial_candidates_reservoir_capped")
        elif "missing transcript root" in lowered:
            safe.append("missing_root")
        elif "dangerous broad root rejected" in lowered:
            safe.append("root_rejected_dangerous_broad_root")
        else:
            safe.append("warning_redacted")
    return sorted(set(safe))


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


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _median(values: Any) -> float:
    items = [float(item or 0) for item in values]
    return round(float(statistics.median(items)), 3) if items else 0.0


def _p90(values: Any) -> float:
    items = sorted(float(item or 0) for item in values)
    if not items:
        return 0.0
    index = max(0, min(len(items) - 1, int((0.9 * len(items) + 0.999999) - 1)))
    return round(float(items[index]), 3)


def _ceil_div4(value: float | int) -> int:
    return int((int(value) + 3) // 4)


def _reduction_ratio(current: Any, baseline: Any) -> float:
    try:
        current_int = int(current or 0)
        baseline_int = int(baseline or 0)
    except (TypeError, ValueError):
        return 0.0
    if baseline_int <= 0:
        return 0.0
    return round(max(0, baseline_int - current_int) / baseline_int, 3)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
