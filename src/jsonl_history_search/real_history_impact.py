from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .metric_contract import add_metric_contract, real_history_impact_metrics
from .query import infer_query
from .real_history_bridge import validate_bridge_files
from .scanner import search
from .sources import Limits, SourcePlan, build_source_plan, enumerate_jsonl_files
from .structural_sidecar import build_structural_sidecar_index, search_with_structural_sidecar
from .timeutil import parse_now, timezone_from_args


DEFAULT_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")
DEFAULT_PRIVATE_BRIDGE = Path(".private/jsh-049/bridge.json")
DEFAULT_RAW_OUTPUT_DIR = Path(".private/jsh-049/raw-runs")
DEFAULT_BASELINES = (
    "raw_rg_literal",
    "terminal_rg_then_open",
    "manual_newest_first_open",
    "package_search",
    "package_search_rg_prefilter",
)
VALID_OUTCOMES = {"evidence_found", "ambiguous_candidates", "no_evidence"}
GATE_STATUSES = {"pass", "fail", "inconclusive", "not_evaluated"}


@dataclass(frozen=True)
class RealHistoryFile:
    path: Path
    label: str
    size: int
    record_count: int
    mtime_ns: int


@dataclass(frozen=True)
class Candidate:
    path: Path | None
    line: int | None
    byte_count: int


def build_real_history_impact_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    phase_started = time.perf_counter()
    manifest_path = Path(args.manifest).expanduser()
    bridge_path = Path(args.private_bridge).expanduser()
    try:
        bridge_result = validate_bridge_files(manifest_path, bridge_path)
    except (OSError, json.JSONDecodeError, ValueError):
        report = _invalid_report("bridge_or_manifest_unreadable", started)
        return {"real_history_impact_benchmark": report}
    bridge_validation_ms = _elapsed_ms(phase_started)
    if not bridge_result.ok:
        report = _invalid_report("bridge_validation_failed", started, bridge_result.report)
        report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms
        return {"real_history_impact_benchmark": report}

    manifest = _read_json(manifest_path)
    bridge = _read_json(bridge_path)
    case_bundle = _case_bundle(manifest, bridge)
    manifest_case_count = _manifest_case_count(manifest)
    bundle_errors = _case_bundle_errors(case_bundle, expected_case_count=manifest_case_count)
    if bundle_errors:
        report = _invalid_report("case_bundle_invalid", started, bridge_result.report, bundle_errors)
        report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms
        return {"real_history_impact_benchmark": report}

    selected_cases = case_bundle[: args.case_limit if args.case_limit and args.case_limit > 0 else None]
    limits = _limits_from_args(args)
    roots = _selected_roots(args, selected_cases)
    root_labels = [f"root_{index + 1:03d}" for index, _root in enumerate(roots)]
    base_report = _base_report(
        selected_cases=selected_cases,
        manifest_case_count=manifest_case_count,
        limits=limits,
        root_labels=root_labels,
        started=started,
        bridge_report=bridge_result.report,
    )
    base_report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms

    plan = build_source_plan(
        cwd=Path(args.cwd).expanduser().resolve(),
        codex_home=Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex",
        claude_home=Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude",
        roots=roots,
        allow_arbitrary_root=True,
        limits=limits,
    )
    if plan.rejected_roots or not plan.roots:
        warnings = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
        return {"real_history_impact_benchmark": _finish_without_scan(base_report, "root_rejected", warnings, started)}

    phase_started = time.perf_counter()
    files, enum_warnings = _enumerate_files(plan)
    base_report["phase_durations_ms"]["source_enumeration"] = _elapsed_ms(phase_started)
    safe_warnings = _safe_warnings(enum_warnings)
    cap_hit_labels = _cap_hit_labels(safe_warnings)
    if safe_warnings:
        base_report["warnings"] = safe_warnings

    selected = _selected_baselines(args.baseline)
    structural_sidecar_index = None
    if any(
        baseline in selected
        for baseline in (
            "package_search_structural_sidecar",
            "package_search_structural_hybrid",
            "package_search_structural_hybrid_budgeted",
        )
    ):
        phase_started = time.perf_counter()
        structural_sidecar_index = build_structural_sidecar_index(
            plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        )
        base_report["phase_durations_ms"]["structural_sidecar_index_build"] = _elapsed_ms(phase_started)
        base_report["structural_sidecar_index"] = {
            **structural_sidecar_index.sanitized_summary(),
            "build_bytes": int(structural_sidecar_index.metrics.get("build_bytes", 0)),
            "build_elapsed_ms": structural_sidecar_index.metrics.get("build_elapsed_ms", 0.0),
        }
    rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    for case in selected_cases:
        for baseline in selected:
            runner = {
                "raw_rg_literal": _run_raw_rg_literal,
                "terminal_rg_then_open": _run_terminal_rg_then_open,
                "manual_newest_first_open": _run_manual_newest_first_open,
                "package_search": _run_package_search,
                "package_search_rg_prefilter": _run_package_search_rg_prefilter,
                "package_search_structural_sidecar": _run_package_search_structural_sidecar,
                "package_search_structural_hybrid": _run_package_search_structural_hybrid,
                "package_search_structural_hybrid_budgeted": _run_package_search_structural_hybrid_budgeted,
            }[baseline]
            for repeat_index in range(max(1, args.repeat)):
                runner_kwargs = {
                    "case": case,
                    "files": files,
                    "plan": plan,
                    "args": args,
                    "repeat_index": repeat_index,
                    "cap_hit_labels": cap_hit_labels,
                }
                if baseline in {
                    "package_search_structural_sidecar",
                    "package_search_structural_hybrid",
                    "package_search_structural_hybrid_budgeted",
                }:
                    runner_kwargs["structural_sidecar_index"] = structural_sidecar_index
                row, raw_row = runner(**runner_kwargs)
                rows.append(row)
                raw_rows.append(raw_row)

    row_warnings = [warning for row in rows for warning in row.get("warnings", [])]
    safe_warnings = _safe_warnings(safe_warnings + row_warnings)
    cap_hit_labels = sorted({label for row in rows for label in row.get("cap_hit_labels", [])} | set(_cap_hit_labels(safe_warnings)))
    if safe_warnings:
        base_report["warnings"] = safe_warnings
    base_report["status"] = "capped" if cap_hit_labels else "ok"
    base_report["caps_hit"] = bool(cap_hit_labels)
    base_report["cap_hit_labels"] = cap_hit_labels
    base_report["rows"] = rows
    base_report["summary"] = _summarize(rows, selected)
    base_report["aggregate"] = _aggregate(rows)
    structural_diagnostic = _structural_sidecar_diagnostic(rows)
    if structural_diagnostic is not None:
        base_report["structural_sidecar_diagnostic"] = structural_diagnostic
    base_report["headline_findings"] = _headline_findings(base_report["summary"], base_report["aggregate"], cap_hit_labels)
    base_report.update(_evaluate_gate_statuses(rows, base_report["summary"], base_report.get("warnings", [])))

    phase_started = time.perf_counter()
    raw_dir = Path(args.raw_output_dir).expanduser()
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{base_report['run_id']}.json"
    raw_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": base_report["run_id"],
                "privacy_note": "Ignored raw private benchmark evidence. Do not commit.",
                "rows": raw_rows,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    base_report["phase_durations_ms"]["raw_evidence_write"] = _elapsed_ms(phase_started)
    base_report["raw_private_evidence"] = {
        "stored": True,
        "storage": "ignored_local_raw_run_artifacts",
        "run_file_label": f"{base_report['run_id']}.json",
        "committed": False,
    }
    base_report["total_wall_time_ms"] = _elapsed_ms(started)
    _add_metric_sections(base_report)
    return {"real_history_impact_benchmark": base_report}


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
        "case_count_floor_status": _case_count_floor_status(0),
        "root_labels": [],
        "limits": {},
        "baseline_contract": _baseline_contract(),
        "comparison_scope": _comparison_scope(),
        "privacy_contract": _privacy_contract(),
        "phase_durations_ms": {"bridge_validation": 0, "source_enumeration": 0, "raw_evidence_write": 0},
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [reason],
        "fixture_errors": fixture_errors or [],
        "bridge_validation": _safe_bridge_summary(bridge_report),
        "caps_hit": False,
        "cap_hit_labels": [],
        "raw_private_evidence": {"stored": False, "storage": "ignored_local_raw_run_artifacts", "committed": False},
        "rows": [],
        "summary": {},
        "aggregate": {},
        "ranking_quality_gate": _gate_status("not_evaluated", [reason, "no_benchmark_rows"]),
        "rg_prefilter_gate": _gate_status("not_evaluated", [reason, "no_benchmark_rows"]),
    }
    return _add_metric_sections(report)


def _base_report(
    *,
    selected_cases: list[dict[str, Any]],
    manifest_case_count: int,
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
        "manifest_case_count": manifest_case_count,
        "case_count": len(selected_cases),
        "case_families": sorted({case["family"] for case in selected_cases}),
        "historical_original_case_count": 18,
        "case_count_floor_status": _case_count_floor_status(manifest_case_count),
        "root_labels": root_labels,
        "limits": limits.to_dict(),
        "baseline_contract": _baseline_contract(),
        "comparison_scope": _comparison_scope(),
        "privacy_contract": _privacy_contract(),
        "bridge_validation": _safe_bridge_summary(bridge_report),
        "phase_durations_ms": {"bridge_validation": 0, "source_enumeration": 0, "raw_evidence_write": 0},
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [],
        "caps_hit": False,
        "cap_hit_labels": [],
        "raw_private_evidence": {"stored": False, "storage": "ignored_local_raw_run_artifacts", "committed": False},
        "rows": [],
        "summary": {},
        "aggregate": {},
        "ranking_quality_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
        "rg_prefilter_gate": _gate_status("not_evaluated", ["benchmark_not_run_yet"]),
    }


def _finish_without_scan(report: dict[str, Any], status: str, warnings: list[str], started: float) -> dict[str, Any]:
    report["status"] = status
    report["total_wall_time_ms"] = _elapsed_ms(started)
    report["warnings"] = warnings
    report["caps_hit"] = False
    report["cap_hit_labels"] = _cap_hit_labels(warnings)
    report["ranking_quality_gate"] = _gate_status(status="not_evaluated", reasons=[f"benchmark_status_{status}", "no_benchmark_rows"])
    report["rg_prefilter_gate"] = _gate_status(status="not_evaluated", reasons=[f"benchmark_status_{status}", "no_benchmark_rows"])
    return _add_metric_sections(report)


def _add_metric_sections(report: dict[str, Any]) -> dict[str, Any]:
    return add_metric_contract(report, real_history_impact_metrics(report))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object")
    return payload


def _manifest_case_count(manifest: dict[str, Any]) -> int:
    cases = manifest.get("cases")
    return len(cases) if isinstance(cases, list) else 0


def _case_count_floor_status(case_count: int) -> str:
    if case_count < 20:
        return "below_old_20_50_target_floor"
    if case_count <= 50:
        return "within_old_20_50_target_band"
    return "above_old_20_50_target_band"


def _case_bundle(manifest: dict[str, Any], bridge: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_by_id = {
        case.get("case_id"): case
        for case in manifest.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    bundle: list[dict[str, Any]] = []
    for bridge_case in bridge.get("cases", []):
        if not isinstance(bridge_case, dict):
            continue
        case_id = bridge_case.get("case_id")
        manifest_case = manifest_by_id.get(case_id)
        if not manifest_case:
            continue
        refs = [ref for ref in bridge_case.get("refs", []) if isinstance(ref, dict)]
        query_text = next((ref.get("query_text") for ref in refs if isinstance(ref.get("query_text"), str) and ref.get("query_text")), None)
        expected_refs = []
        for ref in refs:
            path_value = ref.get("path")
            line_value = ref.get("line")
            ref_id = ref.get("ref_id")
            if isinstance(path_value, str) and isinstance(line_value, int) and isinstance(ref_id, str):
                expected_refs.append({"ref_id": ref_id, "path": Path(path_value), "line": line_value})
        bundle.append(
            {
                "case_id": case_id,
                "family": manifest_case.get("family"),
                "difficulty": manifest_case.get("difficulty"),
                "expected_outcome": _normalize_outcome(manifest_case.get("expected_outcome")),
                "stage_hits": list(manifest_case.get("stage_hits") or []),
                "timestamp_applicable": bool(
                    (manifest_case.get("downstream_applicability") or {}).get("timestamp_seek")
                ),
                "query_text": query_text,
                "expected_refs": expected_refs,
                "must_find_ref_ids": [
                    ref.get("ref_id")
                    for ref in manifest_case.get("must_find_refs", [])
                    if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
                ],
            }
        )
    return sorted(bundle, key=lambda item: str(item["case_id"]))


def _case_bundle_errors(cases: list[dict[str, Any]], *, expected_case_count: int) -> list[str]:
    errors = []
    if len(cases) != expected_case_count:
        errors.append("case_bundle_must_match_manifest_case_count")
    for case in cases:
        if not case.get("query_text"):
            errors.append(f"{case.get('case_id')}: missing_private_query_text")
        if not case.get("expected_refs"):
            errors.append(f"{case.get('case_id')}: missing_expected_refs")
    return errors


def _normalize_outcome(value: Any) -> str:
    if value in VALID_OUTCOMES:
        return str(value)
    return "evidence_found"


def _selected_roots(args, cases: list[dict[str, Any]]) -> list[Path]:
    if args.root:
        return [Path(item).expanduser() for item in args.root]
    roots = sorted({ref["path"] for case in cases for ref in case.get("expected_refs", [])})
    return roots


def _limits_from_args(args) -> Limits:
    return Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files or args.max_files,
        hard_cap_bytes=args.hard_cap_bytes or args.max_bytes,
    )


def _enumerate_files(plan: SourcePlan) -> tuple[list[RealHistoryFile], list[str]]:
    files: list[RealHistoryFile] = []
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
        files.append(
            RealHistoryFile(
                path=path,
                label=f"file_{len(files) + 1:03d}",
                size=size,
                record_count=_line_count(path),
                mtime_ns=path.stat().st_mtime_ns if path.exists() else 0,
            )
        )
    return files, warnings


def _line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _line in handle)
    except OSError:
        return 0


def _run_raw_rg_literal(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    files = kwargs["files"]
    plan = kwargs["plan"]
    started = time.perf_counter()
    candidates, files_opened, bytes_scanned, subprocess_calls = _literal_candidates(str(case["query_text"]), files)
    candidates = candidates[: plan.limits.max_matches]
    return _row_pair(
        case=case,
        baseline="raw_rg_literal",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=1,
        subprocess_calls=subprocess_calls,
        files_opened=files_opened,
        records_parsed=0,
        bytes_scanned=bytes_scanned,
        bytes_parsed=0,
        output_context_bytes=sum(candidate.byte_count for candidate in candidates),
        candidates=candidates,
        cap_hit_labels=kwargs["cap_hit_labels"],
        notes=[],
    )


def _run_terminal_rg_then_open(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    files = kwargs["files"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    started = time.perf_counter()
    candidates, files_opened, bytes_scanned, subprocess_calls = _literal_candidates(str(case["query_text"]), files)
    candidates = candidates[: plan.limits.max_matches]
    opened = _opened_context(candidates[: args.terminal_open_limit], context_lines=args.context_lines)
    return _row_pair(
        case=case,
        baseline="terminal_rg_then_open",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=1 + opened["unique_files_opened"],
        subprocess_calls=subprocess_calls,
        files_opened=files_opened + opened["unique_files_opened"],
        records_parsed=0,
        bytes_scanned=bytes_scanned,
        bytes_parsed=0,
        output_context_bytes=opened["context_bytes"],
        candidates=candidates,
        cap_hit_labels=kwargs["cap_hit_labels"],
        notes=[],
    )


def _run_manual_newest_first_open(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    files = kwargs["files"]
    args = kwargs["args"]
    started = time.perf_counter()
    opened_files = sorted(files, key=lambda item: (item.mtime_ns, str(item.path)), reverse=True)[: args.manual_open_limit]
    opened_paths = {item.path for item in opened_files}
    candidates = [
        Candidate(path=ref["path"], line=ref["line"], byte_count=_context_bytes(ref["path"], ref["line"], args.context_lines))
        for ref in case.get("expected_refs", [])
        if ref["path"] in opened_paths
    ]
    return _row_pair(
        case=case,
        baseline="manual_newest_first_open",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=len(opened_files),
        subprocess_calls=0,
        files_opened=len(opened_files),
        records_parsed=sum(item.record_count for item in opened_files),
        bytes_scanned=sum(item.size for item in opened_files),
        bytes_parsed=sum(item.size for item in opened_files),
        output_context_bytes=sum(item.size for item in opened_files),
        candidates=candidates,
        cap_hit_labels=kwargs["cap_hit_labels"],
        notes=["modeled_newest_first_file_open_with_oracle_gold_recognition_after_open"],
    )


def _run_package_search(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_package_search_variant(baseline="package_search", use_rg_prefilter=False, **kwargs)


def _run_package_search_rg_prefilter(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_package_search_variant(baseline="package_search_rg_prefilter", use_rg_prefilter=True, **kwargs)


def _run_package_search_structural_sidecar(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    index = kwargs.get("structural_sidecar_index")
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    if index is None:
        index = build_structural_sidecar_index(plan, default_tz=default_tz)
    result = search_with_structural_sidecar(
        index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        expected_ref_diagnostics=case.get("expected_refs") if getattr(args, "capture_expected_ref_diagnostics", False) else None,
    )
    structural_warnings = _safe_warnings(result.get("warnings") or [])
    row_cap_hit_labels = sorted(set(kwargs["cap_hit_labels"] or []) | set(_cap_hit_labels(structural_warnings)))
    candidates: list[Candidate] = []
    ranking_reason_label_count = 0
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            path_value = match.get("path")
            line_value = match.get("line")
            ranking_reason_label_count += len(match.get("reasons") or [])
            candidates.append(
                Candidate(
                    path=Path(path_value) if isinstance(path_value, str) else None,
                    line=int(line_value) if isinstance(line_value, int) else None,
                    byte_count=len(json.dumps(match, ensure_ascii=False).encode("utf-8")),
                )
            )
    row, raw_row = _row_pair(
        case=case,
        baseline="package_search_structural_sidecar",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=1,
        subprocess_calls=0,
        files_opened=int(result.get("searched_files", 0)),
        records_parsed=int(result.get("records_considered", 0)),
        bytes_scanned=int(result.get("searched_bytes", 0)),
        bytes_parsed=int(result.get("searched_bytes", 0)),
        output_context_bytes=len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        cap_hit_labels=row_cap_hit_labels,
        ranking_reason_label_count=ranking_reason_label_count,
        warnings=structural_warnings,
        notes=["opt_in_structural_codex_claude_sidecar_probe"],
    )
    row["structural_sidecar"] = _structural_sidecar_row_stats(result)
    expected_diagnostics = _expected_ref_diagnostics_row(result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["structural_sidecar_result"] = result
    return row, raw_row


def _run_package_search_structural_hybrid(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_package_search_structural_hybrid_variant(
        baseline="package_search_structural_hybrid",
        gate_candidate_threshold=None,
        supplement_limit=max(10, kwargs["plan"].limits.max_matches // 2),
        note="opt_in_package_preserving_structural_supplement_probe",
        **kwargs,
    )


def _run_package_search_structural_hybrid_budgeted(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_package_search_structural_hybrid_variant(
        baseline="package_search_structural_hybrid_budgeted",
        gate_candidate_threshold=10,
        supplement_limit=10,
        note="opt_in_budgeted_structural_supplement_probe",
        **kwargs,
    )


def _run_package_search_structural_hybrid_variant(
    *,
    baseline: str,
    gate_candidate_threshold: int | None,
    supplement_limit: int,
    note: str,
    **kwargs,
) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    index = kwargs.get("structural_sidecar_index")
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    expected_refs = case.get("expected_refs") if getattr(args, "capture_expected_ref_diagnostics", False) else None
    package_result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        use_rg_prefilter=False,
        expected_ref_diagnostics=expected_refs,
    )
    package_candidate_count = sum(len(group.get("matches") or []) for group in package_result.get("groups", []))
    gate = _structural_supplement_gate(
        package_candidate_count=package_candidate_count,
        threshold=gate_candidate_threshold,
    )
    structural_result: dict[str, Any] | None = None
    if gate["supplement_enabled"]:
        if index is None:
            index = build_structural_sidecar_index(plan, default_tz=default_tz)
        structural_result = search_with_structural_sidecar(
            index,
            plan,
            intent,
            default_tz=default_tz,
            cwd=str(Path(args.cwd).expanduser().resolve()),
            expected_ref_diagnostics=expected_refs,
        )
    hybrid_result = _hybrid_package_structural_result(
        package_result,
        structural_result,
        supplement_limit=supplement_limit,
        gate=gate,
    )
    warnings = _safe_warnings((package_result.get("warnings") or []) + ((structural_result or {}).get("warnings") or []))
    row_cap_hit_labels = sorted(set(kwargs["cap_hit_labels"] or []) | set(_cap_hit_labels(warnings)))
    candidates, ranking_reason_label_count = _candidates_from_groups(hybrid_result.get("groups", []))
    package_searched_bytes = int(package_result.get("searched_bytes", 0))
    structural_searched_bytes = int((structural_result or {}).get("searched_bytes", 0))
    row, raw_row = _row_pair(
        case=case,
        baseline=baseline,
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=2 if gate["supplement_enabled"] else 1,
        subprocess_calls=0,
        files_opened=int(package_result.get("searched_files", 0)) + int((structural_result or {}).get("searched_files", 0)),
        records_parsed=_estimated_package_records_parsed(
            kwargs["files"],
            package_searched_bytes,
            _prefilter_row_stats({}, enabled=False),
            False,
        )
        + int((structural_result or {}).get("records_considered", 0)),
        bytes_scanned=package_searched_bytes + structural_searched_bytes,
        bytes_parsed=package_searched_bytes + structural_searched_bytes,
        output_context_bytes=len(json.dumps(hybrid_result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        cap_hit_labels=row_cap_hit_labels,
        ranking_reason_label_count=ranking_reason_label_count,
        warnings=warnings,
        notes=[note],
    )
    row["structural_hybrid"] = _structural_hybrid_row_stats(hybrid_result)
    expected_diagnostics = _expected_ref_diagnostics_row(package_result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["hybrid_result"] = hybrid_result
    return row, raw_row


def _run_package_search_variant(
    *,
    baseline: str,
    use_rg_prefilter: bool,
    case: dict[str, Any],
    files: list[RealHistoryFile],
    plan: SourcePlan,
    args,
    repeat_index: int,
    cap_hit_labels: list[str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        use_rg_prefilter=use_rg_prefilter,
        expected_ref_diagnostics=case.get("expected_refs") if getattr(args, "capture_expected_ref_diagnostics", False) else None,
    )
    package_warnings = _safe_warnings(result.get("warnings") or [])
    row_cap_hit_labels = sorted(set(cap_hit_labels or []) | set(_cap_hit_labels(package_warnings)))
    candidates: list[Candidate] = []
    ranking_reason_label_count = 0
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            path_value = match.get("path")
            line_value = match.get("line")
            ranking_reason_label_count += len(match.get("reasons") or [])
            candidates.append(
                Candidate(
                    path=Path(path_value) if isinstance(path_value, str) else None,
                    line=int(line_value) if isinstance(line_value, int) else None,
                    byte_count=len(json.dumps(match, ensure_ascii=False).encode("utf-8")),
                )
            )
    prefilter = _prefilter_row_stats(result, enabled=use_rg_prefilter)
    searched_bytes = int(result.get("searched_bytes", 0))
    records_parsed = _estimated_package_records_parsed(files, searched_bytes, prefilter, use_rg_prefilter)
    row, raw_row = _row_pair(
        case=case,
        baseline=baseline,
        repeat_index=repeat_index,
        started=started,
        tool_calls=1,
        subprocess_calls=1 if prefilter.get("attempted") else 0,
        files_opened=int(result.get("searched_files", 0)),
        records_parsed=records_parsed,
        bytes_scanned=searched_bytes,
        bytes_parsed=searched_bytes,
        output_context_bytes=len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        cap_hit_labels=row_cap_hit_labels,
        rg_prefilter=prefilter,
        ranking_reason_label_count=ranking_reason_label_count,
        warnings=package_warnings,
        notes=[],
    )
    row["scan_order"] = _scan_order_row(result)
    expected_diagnostics = _expected_ref_diagnostics_row(result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["package_result"] = result
    return row, raw_row


def _candidates_from_groups(groups: list[dict[str, Any]]) -> tuple[list[Candidate], int]:
    candidates: list[Candidate] = []
    ranking_reason_label_count = 0
    for group in groups:
        for match in group.get("matches", []):
            path_value = match.get("path")
            line_value = match.get("line")
            ranking_reason_label_count += len(match.get("reasons") or [])
            candidates.append(
                Candidate(
                    path=Path(path_value) if isinstance(path_value, str) else None,
                    line=int(line_value) if isinstance(line_value, int) else None,
                    byte_count=len(json.dumps(match, ensure_ascii=False).encode("utf-8")),
                )
            )
    return candidates, ranking_reason_label_count


def _hybrid_package_structural_result(
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
        "package_warnings": _safe_warnings(package_result.get("warnings") or []),
        "structural_warnings": _safe_warnings((effective_structural_result or {}).get("warnings") or []),
        "privacy": {
            "raw_paths_included_in_summary": False,
            "raw_text_included_in_summary": False,
            "expected_refs_used_for_retrieval": False,
        },
    }


def _structural_supplement_gate(*, package_candidate_count: int, threshold: int | None) -> dict[str, Any]:
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


def _structural_hybrid_row_stats(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": True,
        "status": str(result.get("status") or "unknown"),
        "strategy": str(result.get("strategy") or "unknown"),
        "package_candidate_count": int(result.get("package_candidate_count") or 0),
        "structural_supplement_candidate_count": int(result.get("structural_supplement_candidate_count") or 0),
        "supplement_limit": int(result.get("supplement_limit") or 0),
        "gate": result.get("gate") or {},
        "supplement_enabled": bool((result.get("gate") or {}).get("supplement_enabled")),
        "package_searched_bytes": int(result.get("package_searched_bytes") or 0),
        "structural_searched_bytes": int(result.get("structural_searched_bytes") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "expected_refs_used_for_retrieval": False,
    }


def _literal_candidates(query: str, files: list[RealHistoryFile]) -> tuple[list[Candidate], int, int, int]:
    if shutil.which("rg"):
        return _rg_literal_candidates(query, files)
    candidates, files_opened, bytes_scanned = _python_literal_candidates(query, files)
    return candidates, files_opened, bytes_scanned, 0


def _rg_literal_candidates(query: str, files: list[RealHistoryFile]) -> tuple[list[Candidate], int, int, int]:
    if not files:
        return [], 0, 0, 1
    command = ["rg", "--json", "--fixed-strings", "--ignore-case", "--", query]
    command.extend(str(item.path) for item in files)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode not in {0, 1}:
        candidates, files_opened, bytes_scanned = _python_literal_candidates(query, files)
        return candidates, files_opened, bytes_scanned, 0
    candidates: list[Candidate] = []
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = ((data.get("path") or {}).get("text"))
        line_number = data.get("line_number")
        text = ((data.get("lines") or {}).get("text") or "")
        if isinstance(path_text, str) and isinstance(line_number, int):
            candidates.append(Candidate(path=Path(path_text), line=line_number, byte_count=len(text.encode("utf-8"))))
    candidates.sort(key=lambda item: (str(item.path), item.line or 0))
    return candidates, len(files), sum(item.size for item in files), 1


def _python_literal_candidates(query: str, files: list[RealHistoryFile]) -> tuple[list[Candidate], int, int]:
    needle = query.lower()
    candidates: list[Candidate] = []
    files_opened = 0
    bytes_scanned = 0
    for item in files:
        try:
            lines = item.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        files_opened += 1
        bytes_scanned += item.size
        for line_number, line in enumerate(lines, start=1):
            if needle in line.lower():
                candidates.append(Candidate(path=item.path, line=line_number, byte_count=len(line.encode("utf-8"))))
    return candidates, files_opened, bytes_scanned


def _opened_context(candidates: list[Candidate], *, context_lines: int) -> dict[str, int]:
    opened_paths = {candidate.path for candidate in candidates if candidate.path}
    context_bytes = sum(
        _context_bytes(candidate.path, candidate.line, context_lines)
        for candidate in candidates
        if candidate.path is not None and candidate.line is not None
    )
    return {"unique_files_opened": len(opened_paths), "context_bytes": context_bytes}


def _context_bytes(path: Path | None, line: int | None, context_lines: int) -> int:
    if path is None or line is None:
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    start = max(0, line - context_lines - 1)
    end = min(len(lines), line + context_lines)
    return len("\n".join(lines[start:end]).encode("utf-8"))


def _row_pair(
    *,
    case: dict[str, Any],
    baseline: str,
    repeat_index: int,
    started: float,
    tool_calls: int,
    subprocess_calls: int,
    files_opened: int,
    records_parsed: int | None,
    bytes_scanned: int,
    bytes_parsed: int,
    output_context_bytes: int,
    candidates: list[Candidate],
    cap_hit_labels: list[str] | None,
    rg_prefilter: dict[str, Any] | None = None,
    ranking_reason_label_count: int = 0,
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = {(ref["path"], ref["line"]): ref["ref_id"] for ref in case.get("expected_refs", [])}
    must_ids = set(case.get("must_find_ref_ids") or [])
    candidate_ref_ids = [expected[(candidate.path, candidate.line)] for candidate in candidates if (candidate.path, candidate.line) in expected]
    expected_no_evidence = case["expected_outcome"] == "no_evidence"
    first_pass_hit = bool(candidate_ref_ids and candidate_ref_ids[0] in must_ids)
    no_evidence_correct = bool(expected_no_evidence and not candidates)
    row_warnings = _safe_warnings(warnings or [])
    labels = sorted(set(cap_hit_labels or []) | set(_cap_hit_labels(row_warnings)))
    row = {
        "case_id": case["case_id"],
        "baseline": baseline,
        "repeat_index": repeat_index,
        "legitimacy_status": "reviewed_real_history_gold",
        "source_set_label": "private_bridge_selected_roots",
        "expected_outcome": case["expected_outcome"],
        "family": case["family"],
        "difficulty": case["difficulty"],
        "timestamp_applicable": case["timestamp_applicable"],
        "wall_time_ms": _elapsed_ms(started),
        "tool_calls": tool_calls,
        "subprocess_calls": subprocess_calls,
        "files_opened": files_opened,
        "records_parsed": records_parsed,
        "bytes_scanned": bytes_scanned,
        "bytes_parsed": bytes_parsed,
        "output_bytes": output_context_bytes,
        "context_bytes": output_context_bytes,
        "output_context_bytes": output_context_bytes,
        "candidate_count": len(candidates),
        "first_pass_hit": first_pass_hit,
        "recall_at_1": _recall_at(candidate_ref_ids, must_ids, 1, expected_no_evidence),
        "recall_at_3": _recall_at(candidate_ref_ids, must_ids, 3, expected_no_evidence),
        "recall_at_10": _recall_at(candidate_ref_ids, must_ids, 10, expected_no_evidence),
        "mrr": _mrr(candidate_ref_ids, must_ids, expected_no_evidence),
        "no_evidence_correct": no_evidence_correct if expected_no_evidence else None,
        "source_ref_equivalence_basis": "sanitized_bridge_ref_id",
        "top_candidate_ref_ids": candidate_ref_ids[:10],
        "top_candidate_source_refs": [f"sanitized_ref:{ref_id}" for ref_id in candidate_ref_ids[:10]],
        "ranking_reason_label_count": ranking_reason_label_count,
        "caps_hit": bool(labels),
        "cap_hit_labels": labels,
        "warnings": row_warnings,
        "rg_prefilter": rg_prefilter or _prefilter_row_stats({}, enabled=False),
        "notes": notes or [],
    }
    row["counter_availability"] = _counter_availability(row, expected_no_evidence=expected_no_evidence)
    raw_row = {
        "case_id": case["case_id"],
        "baseline": baseline,
        "repeat_index": repeat_index,
        "query_text": case["query_text"],
        "expected_refs": [
            {"ref_id": ref["ref_id"], "path": str(ref["path"]), "line": ref["line"]} for ref in case.get("expected_refs", [])
        ],
        "candidate_refs": [
            {"path": str(candidate.path) if candidate.path else None, "line": candidate.line, "byte_count": candidate.byte_count}
            for candidate in candidates[:50]
        ],
        "sanitized_row": row,
    }
    return row, raw_row


def _scan_order_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy": str(result.get("scan_order_strategy") or "unknown"),
        "record_quantum": int(result.get("scan_record_quantum") or 0),
        "planned_files_count": int(result.get("planned_files_count") or 0),
        "opened_files_count": int(result.get("opened_files_count") or result.get("searched_files") or 0),
        "records_read": int(result.get("records_read") or 0),
        "records_rejected_by_should_consider": int(result.get("records_rejected_by_should_consider") or 0),
        "candidate_records_scored": int(result.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(result.get("candidate_records_deduped") or 0),
        "dedupe_strategy": str(result.get("dedupe_strategy") or "unknown"),
        "raw_paths_included": False,
    }


def _structural_sidecar_row_stats(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    query_profile = result.get("query_profile") if isinstance(result.get("query_profile"), dict) else {}
    return {
        "enabled": True,
        "status": str(result.get("status") or "unknown"),
        "fallback_reason": result.get("fallback_reason"),
        "candidate_file_count": int(result.get("candidate_files") or 0),
        "candidate_entry_count": int(result.get("candidate_entries") or 0),
        "opened_file_count": int(result.get("searched_files") or 0),
        "records_considered": int(result.get("records_considered") or 0),
        "records_rejected_by_should_consider": int(result.get("records_rejected_by_should_consider") or 0),
        "candidate_records_scored": int(result.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(result.get("candidate_records_deduped") or 0),
        "searched_bytes": int(result.get("searched_bytes") or 0),
        "query_profile": query_profile,
        "index_version": summary.get("version"),
        "indexed_entry_count": summary.get("indexed_entry_count"),
        "indexed_file_count": summary.get("indexed_file_count"),
        "event_family_counts": summary.get("event_family_counts") or {},
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
    }


def _expected_ref_diagnostics_row(result: dict[str, Any]) -> dict[str, Any] | None:
    diagnostics = result.get("expected_ref_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    refs = []
    for ref in diagnostics.get("refs") or []:
        if not isinstance(ref, dict):
            continue
        refs.append(
            {
                "ref_id": str(ref.get("ref_id") or ""),
                "expected_file_label": ref.get("expected_file_label"),
                "expected_file_open_order": ref.get("expected_file_open_order"),
                "expected_file_records_read_before_stop": int(ref.get("expected_file_records_read_before_stop") or 0),
                "expected_file_progress_bucket": str(ref.get("expected_file_progress_bucket") or "unknown"),
                "expected_file_selected_by_source_plan": bool(ref.get("expected_file_selected_by_source_plan")),
                "expected_file_queued_for_scan": bool(ref.get("expected_file_queued_for_scan")),
                "expected_file_opened_before_scan_stop": bool(ref.get("expected_file_opened_before_scan_stop")),
                "expected_record_line_reached_before_elapsed_stop": bool(
                    ref.get("expected_record_line_reached_before_elapsed_stop")
                ),
                "line_reach_status": str(ref.get("line_reach_status") or "unknown"),
                "would_pass_should_consider": ref.get("would_pass_should_consider"),
                "deduped_before_scoring": bool(ref.get("deduped_before_scoring")),
                "expected_record_present_before_reservoir_retention": bool(
                    ref.get("expected_record_present_before_reservoir_retention")
                ),
                "expected_record_present_in_pre_final_candidate_state": bool(
                    ref.get("expected_record_present_in_pre_final_candidate_state")
                ),
                "expected_record_present_in_final_candidate_refs": bool(
                    ref.get("expected_record_present_in_final_candidate_refs")
                ),
                "expected_record_present_in_final_candidate_groups": bool(
                    ref.get("expected_record_present_in_final_candidate_groups")
                ),
                "candidate_strength": ref.get("candidate_strength"),
                "should_consider_phase_label": str(ref.get("should_consider_phase_label") or "unknown"),
                "dedupe_phase_label": str(ref.get("dedupe_phase_label") or "unknown"),
                "reservoir_phase_label": str(ref.get("reservoir_phase_label") or "unknown"),
                "final_cap_phase_label": str(ref.get("final_cap_phase_label") or "unknown"),
                "drop_phase_label": ref.get("drop_phase_label"),
                "pre_reservoir_status": str(ref.get("pre_reservoir_status") or "unknown"),
                "pre_final_status": str(ref.get("pre_final_status") or "unknown"),
                "final_status": str(ref.get("final_status") or "unknown"),
                "expected_file_indexed_by_structural_sidecar": _optional_bool(
                    ref.get("expected_file_indexed_by_structural_sidecar")
                ),
                "expected_record_indexed_by_structural_sidecar": _optional_bool(
                    ref.get("expected_record_indexed_by_structural_sidecar")
                ),
                "expected_file_selected_as_candidate_file": _optional_bool(
                    ref.get("expected_file_selected_as_candidate_file")
                ),
                "expected_record_selected_as_candidate_entry": _optional_bool(
                    ref.get("expected_record_selected_as_candidate_entry")
                ),
                "structural_candidate_status": (
                    str(ref.get("structural_candidate_status"))
                    if ref.get("structural_candidate_status") is not None
                    else None
                ),
            }
        )
    opened_files = []
    for item in diagnostics.get("opened_files") or []:
        if not isinstance(item, dict):
            continue
        opened_files.append(
            {
                "file_label": str(item.get("file_label") or ""),
                "opened_order": int(item.get("opened_order") or 0),
                "records_read": int(item.get("records_read") or 0),
                "records_read_bucket": str(item.get("records_read_bucket") or "unknown"),
                "expected_ref_file": bool(item.get("expected_ref_file")),
            }
        )
    return {
        "enabled": bool(diagnostics.get("enabled")),
        "raw_paths_included": False,
        "line_numbers_included": False,
        "ref_count": int(diagnostics.get("ref_count") or len(refs)),
        "opened_file_count": int(diagnostics.get("opened_file_count") or len(opened_files)),
        "opened_files": opened_files,
        "line_reached_count": int(diagnostics.get("line_reached_count") or 0),
        "pre_reservoir_present_count": int(diagnostics.get("pre_reservoir_present_count") or 0),
        "pre_final_present_count": int(diagnostics.get("pre_final_present_count") or 0),
        "final_candidate_ref_present_count": int(diagnostics.get("final_candidate_ref_present_count") or 0),
        "final_candidate_group_present_count": int(diagnostics.get("final_candidate_group_present_count") or 0),
        "structural_sidecar_enabled": bool(diagnostics.get("structural_sidecar_enabled")),
        "indexed_expected_record_count": int(diagnostics.get("indexed_expected_record_count") or 0),
        "candidate_file_expected_ref_count": int(diagnostics.get("candidate_file_expected_ref_count") or 0),
        "candidate_entry_expected_ref_count": int(diagnostics.get("candidate_entry_expected_ref_count") or 0),
        "refs": refs,
    }


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _recall_at(candidate_ref_ids: list[str], must_ids: set[str], k: int, expected_no_evidence: bool) -> float:
    if expected_no_evidence:
        return 1.0 if not candidate_ref_ids else 0.0
    if not must_ids:
        return 0.0
    return round(len(set(candidate_ref_ids[:k]) & must_ids) / len(must_ids), 3)


def _mrr(candidate_ref_ids: list[str], must_ids: set[str], expected_no_evidence: bool) -> float | None:
    if expected_no_evidence:
        return None
    for index, ref_id in enumerate(candidate_ref_ids, start=1):
        if ref_id in must_ids:
            return round(1 / index, 3)
    return 0.0


def _estimated_package_records_parsed(
    files: list[RealHistoryFile],
    searched_bytes: int,
    prefilter: dict[str, Any],
    use_rg_prefilter: bool,
) -> int | None:
    if not use_rg_prefilter:
        return sum(item.record_count for item in files)
    if prefilter.get("status") == "used":
        matched_bytes = int(prefilter.get("matched_bytes", 0))
        if searched_bytes == matched_bytes:
            return None
    if prefilter.get("status") in {"fallback", "not_attempted"}:
        return sum(item.record_count for item in files)
    return None


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


def _summarize(rows: list[dict[str, Any]], baselines: tuple[str, ...]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for baseline in baselines:
        selected = [row for row in rows if row["baseline"] == baseline]
        if not selected:
            continue
        no_evidence_rows = [row for row in selected if row["expected_outcome"] == "no_evidence"]
        summary[baseline] = {
            "runs": len(selected),
            "cases": len({row["case_id"] for row in selected}),
            "first_pass_hits": sum(1 for row in selected if row["first_pass_hit"]),
            "first_pass_hit_rate": round(sum(1 for row in selected if row["first_pass_hit"]) / len(selected), 3),
            "mean_recall_at_1": _mean(row["recall_at_1"] for row in selected),
            "mean_recall_at_3": _mean(row["recall_at_3"] for row in selected),
            "mean_recall_at_10": _mean(row["recall_at_10"] for row in selected),
            "mean_mrr": _mean(row["mrr"] for row in selected if row["mrr"] is not None),
            "no_evidence_correct": sum(1 for row in no_evidence_rows if row["no_evidence_correct"]),
            "no_evidence_cases": len(no_evidence_rows),
            "median_wall_time_ms": _median(row["wall_time_ms"] for row in selected),
            "median_tool_calls": _median(row["tool_calls"] for row in selected),
            "median_subprocess_calls": _median(row["subprocess_calls"] for row in selected),
            "median_files_opened": _median(row["files_opened"] for row in selected),
            "median_records_parsed": _median(row["records_parsed"] for row in selected if row["records_parsed"] is not None),
            "records_parsed_available_runs": sum(1 for row in selected if row["records_parsed"] is not None),
            "median_bytes_scanned": _median(row["bytes_scanned"] for row in selected),
            "median_bytes_parsed": _median(row["bytes_parsed"] for row in selected),
            "median_output_context_bytes": _median(row["output_context_bytes"] for row in selected),
            "median_output_context_tokens_approx": _ceil_div4(_median(row["output_context_bytes"] for row in selected)),
            "median_candidate_count": _median(row["candidate_count"] for row in selected),
            "median_ranking_reason_label_count": _median(row["ranking_reason_label_count"] for row in selected),
            "caps_hit_runs": sum(1 for row in selected if row["caps_hit"]),
            "cap_hit_labels": sorted({label for row in selected for label in row.get("cap_hit_labels", [])}),
            "cap_hit_rows_by_label": _cap_hit_row_counts(selected),
            "median_prefilter_files_reduced": _median(row["rg_prefilter"]["files_reduced"] for row in selected),
            "median_prefilter_bytes_reduced": _median(row["rg_prefilter"]["bytes_reduced"] for row in selected),
            "median_prefilter_files_reduction_ratio": _median(
                row["rg_prefilter"]["files_reduction_ratio"] for row in selected
            ),
            "median_prefilter_bytes_reduction_ratio": _median(
                row["rg_prefilter"]["bytes_reduction_ratio"] for row in selected
            ),
            "rg_prefilter_statuses": _counts((row.get("rg_prefilter") or {}).get("status") for row in selected),
        }
    if "package_search" in summary and "package_search_rg_prefilter" in summary:
        summary["package_search_rg_prefilter"]["equivalence_vs_package_search"] = _package_equivalence(rows)
    return summary


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": len(rows),
        "cases": len({row["case_id"] for row in rows}),
        "baselines": sorted({row["baseline"] for row in rows}),
        "files_opened": sum(int(row.get("files_opened") or 0) for row in rows),
        "records_parsed": sum(int(row.get("records_parsed") or 0) for row in rows),
        "records_parsed_available_runs": sum(1 for row in rows if row.get("records_parsed") is not None),
        "bytes_scanned": sum(int(row.get("bytes_scanned") or 0) for row in rows),
        "bytes_parsed": sum(int(row.get("bytes_parsed") or 0) for row in rows),
        "tool_calls": sum(int(row.get("tool_calls") or 0) for row in rows),
        "subprocess_calls": sum(int(row.get("subprocess_calls") or 0) for row in rows),
        "output_context_bytes": sum(int(row.get("output_context_bytes") or 0) for row in rows),
        "cap_hit_labels": sorted({label for row in rows for label in row.get("cap_hit_labels", [])}),
        "cap_hit_rows_by_label": _cap_hit_row_counts(rows),
        "cap_hit_rows_by_baseline": {
            baseline: _cap_hit_row_counts([row for row in rows if row["baseline"] == baseline])
            for baseline in sorted({row["baseline"] for row in rows})
        },
        "source_ref_equivalence": _package_equivalence(rows),
        "no_evidence_correct": {
            "cases": sum(1 for row in rows if row.get("expected_outcome") == "no_evidence"),
            "correct": sum(1 for row in rows if row.get("no_evidence_correct") is True),
        },
    }


def _structural_sidecar_diagnostic(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    structural_rows = {
        (row["case_id"], row["repeat_index"]): row
        for row in rows
        if row["baseline"] == "package_search_structural_sidecar"
    }
    if not structural_rows:
        return None
    package_rows = {
        (row["case_id"], row["repeat_index"]): row
        for row in rows
        if row["baseline"] == "package_search"
    }
    diagnostic_rows = []
    for key, structural in sorted(structural_rows.items()):
        package = package_rows.get(key)
        ref = _first_expected_ref_diag(structural)
        package_ref = _first_expected_ref_diag(package) if package else {}
        structural_hit = bool(structural.get("first_pass_hit"))
        package_hit = bool(package.get("first_pass_hit")) if package else None
        diagnostic_rows.append(
            {
                "case_id": structural["case_id"],
                "family": structural.get("family"),
                "structural_first_pass_hit": structural_hit,
                "package_first_pass_hit": package_hit,
                "comparison_class": _structural_comparison_class(structural_hit, package_hit),
                "stage": _structural_stage_from_ref(ref),
                "structural_expected_ref": {
                    "indexed_file": ref.get("expected_file_indexed_by_structural_sidecar"),
                    "indexed_record": ref.get("expected_record_indexed_by_structural_sidecar"),
                    "candidate_file_selected": ref.get("expected_file_selected_as_candidate_file"),
                    "candidate_entry_selected": ref.get("expected_record_selected_as_candidate_entry"),
                    "opened": ref.get("expected_file_opened_before_scan_stop"),
                    "record_line_reached": ref.get("expected_record_line_reached_before_elapsed_stop"),
                    "candidate_created": ref.get("expected_record_present_before_reservoir_retention"),
                    "should_consider": ref.get("would_pass_should_consider"),
                    "scored": ref.get("expected_record_present_before_reservoir_retention"),
                    "retained_pre_final": ref.get("expected_record_present_in_pre_final_candidate_state"),
                    "final_kept": ref.get("expected_record_present_in_final_candidate_refs"),
                    "structural_candidate_status": ref.get("structural_candidate_status"),
                    "should_consider_phase_label": ref.get("should_consider_phase_label"),
                    "dedupe_phase_label": ref.get("dedupe_phase_label"),
                    "reservoir_phase_label": ref.get("reservoir_phase_label"),
                    "final_cap_phase_label": ref.get("final_cap_phase_label"),
                },
                "package_expected_ref": {
                    "opened": package_ref.get("expected_file_opened_before_scan_stop"),
                    "record_line_reached": package_ref.get("expected_record_line_reached_before_elapsed_stop"),
                    "candidate_created": package_ref.get("expected_record_present_before_reservoir_retention"),
                    "retained_pre_final": package_ref.get("expected_record_present_in_pre_final_candidate_state"),
                    "final_kept": package_ref.get("expected_record_present_in_final_candidate_refs"),
                    "line_reach_status": package_ref.get("line_reach_status"),
                    "reservoir_phase_label": package_ref.get("reservoir_phase_label"),
                    "final_cap_phase_label": package_ref.get("final_cap_phase_label"),
                },
                "cost": {
                    "structural_files": int(structural.get("files_opened") or 0),
                    "package_files": int(package.get("files_opened") or 0) if package else None,
                    "structural_records": structural.get("records_parsed"),
                    "package_records": package.get("records_parsed") if package else None,
                    "structural_bytes_parsed": int(structural.get("bytes_parsed") or 0),
                    "package_bytes_parsed": int(package.get("bytes_parsed") or 0) if package else None,
                    "structural_output_context_bytes": int(structural.get("output_context_bytes") or 0),
                    "package_output_context_bytes": (
                        int(package.get("output_context_bytes") or 0) if package else None
                    ),
                    "structural_wall_time_ms": int(structural.get("wall_time_ms") or 0),
                    "package_wall_time_ms": int(package.get("wall_time_ms") or 0) if package else None,
                    "structural_cap_labels": list(structural.get("cap_hit_labels") or []),
                    "package_cap_labels": list(package.get("cap_hit_labels") or []) if package else [],
                },
            }
        )
    misses = [row for row in diagnostic_rows if not row["structural_first_pass_hit"]]
    comparison_counts = _counts(row["comparison_class"] for row in diagnostic_rows)
    stage_counts = _counts(row["stage"] for row in misses)
    decision = _structural_diagnostic_decision(diagnostic_rows, stage_counts)
    return {
        "schema_version": 1,
        "status": "ok",
        "basis": "same_run_sanitized_rows_with_expected_ref_diagnostics",
        "structural_cases": len(diagnostic_rows),
        "structural_misses": len(misses),
        "comparison_counts": comparison_counts,
        "miss_stage_counts": stage_counts,
        "cost_buckets": {
            "local_tool_work": "files, records, bytes parsed/scanned, elapsed time",
            "model_facing_context_proxy": "output_context_bytes from benchmark result payloads",
            "full_agent_loop_cost": "unavailable in this first-pass offline benchmark",
        },
        "privacy": {
            "raw_paths_included": False,
            "raw_text_included": False,
            "line_numbers_included": False,
            "raw_session_ids_included": False,
            "expected_refs_used_for_retrieval": False,
        },
        "rows": diagnostic_rows,
        "decision": decision,
    }


def _first_expected_ref_diag(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    diagnostics = row.get("expected_ref_diagnostics") or {}
    refs = diagnostics.get("refs") or []
    return refs[0] if refs and isinstance(refs[0], dict) else {}


def _structural_comparison_class(structural_hit: bool, package_hit: bool | None) -> str:
    if package_hit is None:
        return "package_not_run"
    if structural_hit and package_hit:
        return "both_hit"
    if structural_hit and not package_hit:
        return "structural_only_hit"
    if package_hit:
        return "package_only_hit"
    return "both_miss"


def _structural_stage_from_ref(ref: dict[str, Any]) -> str:
    if not ref:
        return "expected_ref_diagnostic_unavailable"
    if ref.get("expected_record_present_in_final_candidate_refs"):
        return "final_kept"
    if ref.get("expected_record_present_in_pre_final_candidate_state"):
        return "dropped_before_final_output"
    if ref.get("expected_record_present_before_reservoir_retention"):
        return "dropped_before_pre_final_retention"
    if ref.get("deduped_before_scoring"):
        return "deduped_before_scoring"
    if ref.get("would_pass_should_consider") is False:
        return "rejected_by_should_consider"
    if ref.get("expected_record_line_reached_before_elapsed_stop"):
        return "line_reached_but_not_scored"
    if ref.get("expected_file_opened_before_scan_stop"):
        return "opened_expected_file_but_line_not_reached"
    if ref.get("expected_record_selected_as_candidate_entry"):
        return "candidate_entry_selected_but_not_reached"
    if ref.get("expected_file_selected_as_candidate_file"):
        return "candidate_file_selected_but_record_not_candidate"
    if ref.get("expected_record_indexed_by_structural_sidecar"):
        return "indexed_record_not_selected"
    if ref.get("expected_file_indexed_by_structural_sidecar"):
        return "indexed_file_without_expected_record"
    return "expected_file_not_indexed"


def _structural_diagnostic_decision(
    rows: list[dict[str, Any]],
    stage_counts: dict[str, int],
) -> dict[str, Any]:
    package_only = sum(1 for row in rows if row["comparison_class"] == "package_only_hit")
    structural_only = sum(1 for row in rows if row["comparison_class"] == "structural_only_hit")
    next_action = "jsh-770-package-plus-structural-hybrid-candidate-routing-probe"
    rationale = (
        "Structural sidecar recovers at least one package miss while package still recovers multiple "
        "structural misses; a hybrid probe is the next bounded behavior test."
    )
    if structural_only == 0:
        next_action = "pivot_to_agent_loop_cost_benchmark_or_non_sidecar_retrieval_repair"
        rationale = "Structural sidecar does not add unique first-pass recall in this diagnostic surface."
    elif package_only == 0:
        next_action = "consider_structural_sidecar_promotion_gate_after_held_out_validation"
        rationale = "Structural sidecar has no package-only losses in this diagnostic surface."
    if stage_counts.get("indexed_record_not_selected", 0) or stage_counts.get("candidate_file_selected_but_record_not_candidate", 0):
        repair_family = "candidate_routing_or_candidate_entry_retention"
    elif stage_counts.get("opened_expected_file_but_line_not_reached", 0):
        repair_family = "within_file_record_selection_window"
    elif stage_counts.get("dropped_before_pre_final_retention", 0) or stage_counts.get("dropped_before_final_output", 0):
        repair_family = "retention_or_final_truncation"
    else:
        repair_family = "hybrid_package_structural_routing"
    return {
        "repair_family": repair_family,
        "next_value_action": next_action,
        "rationale": rationale,
        "default_promotion_supported": False,
        "token_savings_claim_supported": False,
    }


def _headline_findings(
    summary: dict[str, Any],
    aggregate: dict[str, Any],
    cap_hit_labels: list[str] | None,
) -> list[str]:
    findings: list[str] = []
    package = summary.get("package_search") or {}
    raw = summary.get("raw_rg_literal") or {}
    manual = summary.get("manual_newest_first_open") or {}
    rg = summary.get("package_search_rg_prefilter") or {}
    hybrid = summary.get("package_search_structural_hybrid") or {}
    if package:
        package_hits = int(package.get("first_pass_hits", 0) or 0)
        package_cases = int(package.get("cases", 0) or 0)
        if package_hits:
            findings.append(
                f"package_search recovered {package_hits}/{package_cases} sanitized bridge refs in this bounded real-history run"
            )
        else:
            findings.append(
                "package_search returned compact handoff packets but did not recover the sanitized bridge refs in this bounded real-history run"
            )
    if raw:
        findings.append(
            f"raw_rg_literal recovered {raw.get('first_pass_hits', 0)}/{raw.get('cases', 0)} cases with zero parsed bytes but only works for exact literal overlap"
        )
    if manual:
        findings.append(
            f"manual_newest_first_open recovered {manual.get('first_pass_hits', 0)}/{manual.get('cases', 0)} cases while handing off full opened-file content"
        )
    equivalence = rg.get("equivalence_vs_package_search") or aggregate.get("source_ref_equivalence") or {}
    if equivalence and int(equivalence.get("compared_runs") or 0) > 0:
        findings.append(
            "package_search_rg_prefilter preserved source-ref equivalence versus package_search, but most rows fell back or had no reduction"
        )
    if hybrid:
        findings.append(
            f"package_search_structural_hybrid recovered {hybrid.get('first_pass_hits', 0)}/{hybrid.get('cases', 0)} cases as an opt-in package-preserving structural supplement"
        )
    if cap_hit_labels:
        findings.append(f"cap hits affected this run: {', '.join(cap_hit_labels)}")
    findings.append("ranking and rg prefilter gates remain inconclusive and unpromoted")
    return findings


def _package_equivalence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    python_rows = {
        (row["case_id"], row["repeat_index"]): row
        for row in rows
        if row["baseline"] == "package_search"
    }
    rg_rows = [
        row
        for row in rows
        if row["baseline"] == "package_search_rg_prefilter" and (row["case_id"], row["repeat_index"]) in python_rows
    ]
    doc_equivalent = 0
    ref_equivalent = 0
    for row in rg_rows:
        baseline = python_rows[(row["case_id"], row["repeat_index"])]
        if row["top_candidate_ref_ids"] == baseline["top_candidate_ref_ids"]:
            doc_equivalent += 1
        if row["top_candidate_source_refs"] == baseline["top_candidate_source_refs"]:
            ref_equivalent += 1
    compared = len(rg_rows)
    return {
        "compared_runs": compared,
        "equivalent_top_candidate_ref_ids": doc_equivalent,
        "equivalent_top_candidate_source_refs": ref_equivalent,
        "all_equivalent": bool(compared and doc_equivalent == compared and ref_equivalent == compared),
    }


def _evaluate_gate_statuses(rows: list[dict[str, Any]], summary: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    evaluated_case_count = len({row["case_id"] for row in rows})
    case_count_reason = (
        "real_history_case_count_below_old_floor_20"
        if evaluated_case_count < 20
        else "real_history_case_count_within_old_20_50_target_band"
        if evaluated_case_count <= 50
        else "real_history_case_count_above_old_20_50_target_band"
    )
    reasons = ["jsh_049b_does_not_promote_feature_gates", case_count_reason]
    if warnings:
        reasons.append("benchmark_warnings_or_caps_present")
    return {
        "ranking_quality_gate": _gate_status(
            "inconclusive" if rows else "not_evaluated",
            reasons if rows else ["no_benchmark_rows"],
            evaluated_case_count=evaluated_case_count,
            counters=_required_counter_summary(rows),
            metrics={
                "package_first_pass_hit_rate": (summary.get("package_search") or {}).get("first_pass_hit_rate"),
                "package_mean_mrr": (summary.get("package_search") or {}).get("mean_mrr"),
            },
        ),
        "rg_prefilter_gate": _gate_status(
            "inconclusive" if rows else "not_evaluated",
            reasons
            + [
                "rg_prefilter_remains_optional_unpromoted",
                "no_exact_path_or_identifier_gate_decision_in_this_child",
            ]
            if rows
            else ["no_benchmark_rows"],
            evaluated_case_count=len({row["case_id"] for row in rows if row["baseline"] == "package_search_rg_prefilter"}),
            counters=_required_counter_summary([row for row in rows if row["baseline"] == "package_search_rg_prefilter"] or rows),
            metrics={
                "equivalence_vs_package_search": (summary.get("package_search_rg_prefilter") or {}).get(
                    "equivalence_vs_package_search"
                ),
                "median_prefilter_files_reduction_ratio": (summary.get("package_search_rg_prefilter") or {}).get(
                    "median_prefilter_files_reduction_ratio"
                ),
                "median_prefilter_bytes_reduction_ratio": (summary.get("package_search_rg_prefilter") or {}).get(
                    "median_prefilter_bytes_reduction_ratio"
                ),
            },
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


def _counter_availability(row: dict[str, Any], *, expected_no_evidence: bool) -> dict[str, dict[str, str]]:
    availability: dict[str, dict[str, str]] = {}
    for key in _required_counter_keys():
        if key == "mrr" and expected_no_evidence:
            availability[key] = {"status": "unavailable", "reason": "mrr_not_defined_for_no_evidence_case"}
        elif key == "no_evidence_correct" and not expected_no_evidence:
            availability[key] = {"status": "unavailable", "reason": "not_a_no_evidence_case"}
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
        "records_parsed",
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
        "historical_original_case_count",
        "case_count_floor_status",
        "privacy_contract",
    }
    return {key: payload.get(key) for key in sorted(allowed) if key in payload}


def _baseline_contract() -> dict[str, str]:
    return {
        "raw_rg_literal": "fixed-string rg/Python literal scan over identical capped selected roots; no JSONL parsing or package ranking",
        "terminal_rg_then_open": "fixed-string rg/Python literal scan plus bounded context opens from matched lines",
        "manual_newest_first_open": "modeled no-extension newest-first file-open workflow over identical selected roots; parses opened files and counts full-file handoff cost",
        "package_search": "current JSONL infer_query/search path over the same selected roots and caps",
        "package_search_rg_prefilter": "optional rg prefilter where eligible, followed by Python JSON parsing for evidence; fallback is labeled per row",
        "package_search_structural_sidecar": "opt-in structural Codex/Claude JSONL sidecar probe; indexes sanitized event/role/timestamp/session tags plus offsets, then passes reopened records through the existing ranking path",
        "package_search_structural_hybrid": "opt-in package-preserving hybrid probe; keeps package candidates first and appends a bounded non-duplicate structural sidecar supplement",
        "package_search_structural_hybrid_budgeted": "opt-in budgeted hybrid probe; keeps package candidates first and appends a bounded structural supplement only when package candidate count is at or below the non-oracle threshold",
    }


def _comparison_scope() -> dict[str, Any]:
    return {
        "root_selection": "private bridge ref files unless --root overrides them",
        "caps": "the same effective files, bytes, matches, and elapsed caps are used for all baselines; manual open limit is an additional modeled workflow cap",
        "mismatch_labels": {
            "manual_newest_first_open": "uses an additional manual_open_limit because the modeled workflow opens a bounded newest-first file set instead of scanning every selected file",
            "package_search_rg_prefilter": "may parse fewer files only when rg prefilter is eligible and used; fallback status is reported",
        },
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
        "raw_private_evidence_ignored": True,
    }


def _selected_baselines(requested: list[str]) -> tuple[str, ...]:
    return tuple(requested or DEFAULT_BASELINES)


def _mean(values: Any) -> float:
    items = list(values)
    return round(sum(items) / len(items), 3) if items else 0.0


def _median(values: Any) -> float:
    items = list(values)
    return round(float(statistics.median(items)), 3) if items else 0.0


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _cap_hit_row_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for label in set(row.get("cap_hit_labels", [])):
            counts[str(label)] = counts.get(str(label), 0) + 1
    return dict(sorted(counts.items()))


def _ceil_div4(value: float | int) -> int:
    return int((int(value) + 3) // 4)


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _safe_warnings(warnings: list[str]) -> list[str]:
    safe: list[str] = []
    for warning in warnings:
        lowered = warning.lower()
        if lowered.startswith(
            (
                "root_rejected_",
                "stopped_at_",
                "candidate_retention_capped_at_max_match_cap",
                "missing_root",
                "arbitrary_root_enabled_with_hard_caps",
                "weak_partial_candidates_reservoir_capped",
            )
        ):
            safe.append(warning)
        elif "arbitrary root enabled" in lowered:
            safe.append("arbitrary_root_enabled_with_hard_caps")
        elif "max elapsed cap" in lowered:
            safe.append("stopped_at_max_elapsed_cap")
        elif "max file cap" in lowered:
            safe.append("stopped_at_max_file_cap")
        elif "max byte cap" in lowered:
            safe.append("stopped_at_max_byte_cap")
        elif "max match cap" in lowered:
            if "retention-capped" in lowered or "final candidates were capped" in lowered:
                safe.append("candidate_retention_capped_at_max_match_cap")
            else:
                safe.append("stopped_at_max_match_cap")
        elif "weak partial generic candidates were reservoir-capped" in lowered:
            safe.append("weak_partial_candidates_reservoir_capped")
        elif "dangerous broad root rejected" in lowered:
            safe.append("root_rejected_dangerous_broad_root")
        elif "missing transcript root" in lowered:
            safe.append("missing_root")
        elif "arbitrary root override requires" in lowered:
            safe.append("root_rejected_missing_arbitrary_root_hard_caps")
        else:
            safe.append("warning_redacted")
    return sorted(set(safe))


def _cap_hit_labels(warnings: list[str]) -> list[str]:
    labels = set()
    for warning in warnings:
        if warning == "candidate_retention_capped_at_max_match_cap":
            labels.add("max_match")
            continue
        if not warning.startswith("stopped_at_"):
            continue
        label = warning[len("stopped_at_") :]
        if label.endswith("_cap"):
            label = label[: -len("_cap")]
        labels.add(label)
    return sorted(labels)
