from __future__ import annotations

import json
import re
import shutil
import statistics
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_contract import build_agent_find_payload
from .fielded_bm25 import build_fielded_bm25_index, search_with_fielded_bm25
from .metric_contract import add_metric_contract, real_history_impact_metrics
from .profiling import current_rss_bytes
from .query import QueryIntent, infer_query
from .real_history_bridge import validate_bridge_files
from .retrieval_modes import build_fielded_bm25_retrieval_diagnostics, run_rrf_fusion_retrieval
from .retrieve_expand_answer_use import ALLOWED_BOTTLENECK_LABELS
from .rrf_fusion import DEFAULT_RRF_RANK_CONSTANT
from .scanner import search
from .sources import (
    Limits,
    SourcePlan,
    build_command_local_source_catalog,
    build_command_local_source_plan,
    build_source_plan,
    enumerate_jsonl_files,
    materialize_planned_jsonl_files,
)
from .structural_hybrid import (
    hybrid_package_structural_result as _shared_hybrid_package_structural_result,
    search_structural_hybrid_budgeted,
    structural_supplement_gate as _shared_structural_supplement_gate,
)
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
COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT = 10
COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT = 5
COMPACT_PACKAGE_SOURCE_WINDOW_BYTE_LIMIT = 640


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


class BenchmarkCheckpointWriter:
    def __init__(self, output: str | None, *, run_id: str, started: float) -> None:
        self.output = Path(output).expanduser() if output else None
        self.run_id = run_id
        self.started = started
        self.count = 0
        self.last_stage: str | None = None
        if self.output is not None:
            self.output.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.output is not None

    def metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "record_count": self.count,
            "last_stage": self.last_stage,
            "output_path_included": False,
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "source_lines_included": False,
            "session_ids_included": False,
            "vectors_or_persisted_indexes_included": False,
        }

    def write(self, stage: str, *, status: str, **counts: Any) -> None:
        if self.output is None:
            return
        rss_current, rss_reason = current_rss_bytes()
        self.count += 1
        self.last_stage = stage
        payload = {
            "schema_version": 1,
            "run_id": self.run_id,
            "sequence": self.count,
            "stage": _safe_checkpoint_label(stage),
            "status": _safe_checkpoint_label(status),
            "elapsed_ms": _elapsed_ms(self.started),
            "rss_current_bytes": rss_current,
            "rss_current_unavailable_reason": rss_reason,
            "counts": _safe_checkpoint_counts(counts),
            "privacy": {
                "raw_paths_included": False,
                "raw_text_included": False,
                "raw_query_text_included": False,
                "source_lines_included": False,
                "line_numbers_included": False,
                "session_ids_included": False,
                "cwd_included": False,
                "usernames_included": False,
                "vectors_or_persisted_indexes_included": False,
            },
        }
        with self.output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")


def _safe_checkpoint_label(value: Any) -> str:
    text = str(value or "unknown")
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", text):
        return text
    return "label_redacted"


def _safe_checkpoint_counts(values: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        safe_key = _safe_checkpoint_label(key)
        if isinstance(value, bool):
            safe[safe_key] = value
        elif isinstance(value, int):
            safe[safe_key] = value
        elif isinstance(value, float):
            safe[safe_key] = round(value, 3)
        elif value is None:
            safe[safe_key] = None
        elif isinstance(value, str):
            safe[safe_key] = _safe_checkpoint_label(value)
    return safe


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

    selected_cases, subset_selection = _select_cases(case_bundle, args)
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
        subset_selection=subset_selection,
    )
    base_report["phase_durations_ms"]["bridge_validation"] = bridge_validation_ms
    checkpoint_writer = BenchmarkCheckpointWriter(
        getattr(args, "checkpoint_output", None),
        run_id=str(base_report["run_id"]),
        started=started,
    )
    base_report["checkpointing"] = checkpoint_writer.metadata()

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
    checkpoint_writer.write(
        "source_enumeration",
        status="ok",
        selected_case_count=len(selected_cases),
        file_count=len(files),
        byte_count=sum(int(item.size) for item in files),
        warning_count=len(enum_warnings),
    )
    guard_stop = _memory_guard_stop(args, checkpoint_writer, stage="after_source_enumeration")
    if guard_stop is not None:
        return {
            "real_history_impact_benchmark": _finish_memory_guard_stop(
                base_report,
                rows=[],
                selected_baselines=[],
                checkpoint_writer=checkpoint_writer,
                started=started,
                guard_stop=guard_stop,
            )
        }
    safe_warnings = _safe_warnings(enum_warnings)
    cap_hit_labels = _cap_hit_labels(safe_warnings)
    if safe_warnings:
        base_report["warnings"] = safe_warnings

    selected = _selected_baselines(args.baseline)
    structural_sidecar_index = None
    fielded_bm25_index = None
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
    if "package_search_fielded_bm25" in selected:
        phase_started = time.perf_counter()
        fielded_bm25_index = build_fielded_bm25_index(
            plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        )
        base_report["phase_durations_ms"]["fielded_bm25_index_build"] = _elapsed_ms(phase_started)
        base_report["fielded_bm25_index"] = {
            **fielded_bm25_index.sanitized_summary(),
            "build_bytes": int(fielded_bm25_index.metrics.get("build_bytes", 0)),
            "build_elapsed_ms": fielded_bm25_index.metrics.get("build_elapsed_ms", 0.0),
            "profile": _phase_profile_stats(
                fielded_bm25_index.metrics.get("profile"),
                "fielded_bm25_index_build",
            ),
        }
        checkpoint_writer.write(
            "fielded_bm25_index_build",
            status="ok" if fielded_bm25_index.available else "unavailable",
            indexed_file_count=int(fielded_bm25_index.metrics.get("indexed_files", 0)),
            indexed_record_count=int(fielded_bm25_index.metrics.get("indexed_records", 0)),
            indexed_chunk_count=int(fielded_bm25_index.metrics.get("indexed_chunks", 0)),
            build_bytes=int(fielded_bm25_index.metrics.get("build_bytes", 0)),
            sources_reused=int(fielded_bm25_index.metrics.get("sources_reused", 0)),
            sources_reindexed=int(fielded_bm25_index.metrics.get("sources_reindexed", 0)),
        )
        guard_stop = _memory_guard_stop(args, checkpoint_writer, stage="after_fielded_bm25_index_build")
        if guard_stop is not None:
            return {
                "real_history_impact_benchmark": _finish_memory_guard_stop(
                    base_report,
                    rows=[],
                    selected_baselines=selected,
                    checkpoint_writer=checkpoint_writer,
                    started=started,
                    guard_stop=guard_stop,
                )
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
                "package_search_fielded_bm25": _run_package_search_fielded_bm25,
                "package_search_rrf_fusion": _run_package_search_rrf_fusion,
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
                if baseline == "package_search_fielded_bm25":
                    runner_kwargs["fielded_bm25_index"] = fielded_bm25_index
                row, raw_row = runner(**runner_kwargs)
                rows.append(row)
                raw_rows.append(raw_row)
                if baseline == "package_search_fielded_bm25":
                    checkpoint_writer.write(
                        "package_search_fielded_bm25_row",
                        status="ok",
                        row_count=len(rows),
                        case_sequence_index=selected_cases.index(case) + 1,
                        repeat_index=repeat_index,
                        output_context_bytes=int(row.get("output_context_bytes") or 0),
                        candidate_count=len(row.get("candidate_ref_ids", []) or []),
                        warning_count=len(row.get("warnings", []) or []),
                    )
                    guard_stop = _memory_guard_stop(args, checkpoint_writer, stage="after_package_search_fielded_bm25_row")
                    if guard_stop is not None:
                        return {
                            "real_history_impact_benchmark": _finish_memory_guard_stop(
                                base_report,
                                rows=rows,
                                selected_baselines=selected,
                                checkpoint_writer=checkpoint_writer,
                                started=started,
                                guard_stop=guard_stop,
                            )
                        }

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
    base_report["rg_prefilter_coverage"] = _rg_prefilter_coverage(rows)
    base_report["rg_prefilter_workflow_decision"] = _rg_prefilter_workflow_decision(
        base_report["rg_prefilter_coverage"],
        base_report["summary"],
    )
    base_report["compact_package_handoff"] = _compact_package_handoff_summary(rows)
    base_report["compact_package_handoff_decision"] = _compact_package_handoff_decision(
        base_report["compact_package_handoff"]
    )
    if getattr(args, "agent_compact_provenance_handoff", False):
        base_report["agent_compact_provenance_handoff"] = _agent_compact_provenance_handoff_summary(
            rows,
            manifest_case_count=manifest_case_count,
            evaluated_case_count=len(selected_cases),
            requested_case_limit=int(getattr(args, "case_limit", 0) or 0),
        )
        base_report["agent_compact_provenance_handoff_decision"] = _agent_compact_provenance_handoff_decision(
            base_report["agent_compact_provenance_handoff"]
        )
        base_report["aggregate"]["agent_compact_provenance_handoff"] = base_report[
            "agent_compact_provenance_handoff"
        ]
    if getattr(args, "source_tier_answerability_pressure", False):
        base_report["source_tier_answerability_pressure"] = _source_tier_answerability_pressure_summary(
            rows,
            manifest_case_count=manifest_case_count,
            evaluated_case_count=len(selected_cases),
            requested_case_limit=int(getattr(args, "case_limit", 0) or 0),
        )
        base_report["large_history_stage_attribution_pressure"] = (
            _large_history_stage_attribution_pressure_summary(
                base_report["source_tier_answerability_pressure"],
                base_report=base_report,
            )
        )
        base_report["source_tier_answerability_pressure_decision"] = (
            _source_tier_answerability_pressure_decision(base_report["source_tier_answerability_pressure"])
        )
        base_report["aggregate"]["source_tier_answerability_pressure"] = base_report[
            "source_tier_answerability_pressure"
        ]
        base_report["aggregate"]["large_history_stage_attribution_pressure"] = base_report[
            "large_history_stage_attribution_pressure"
        ]
    structural_diagnostic = _structural_sidecar_diagnostic(rows)
    if structural_diagnostic is not None:
        base_report["structural_sidecar_diagnostic"] = structural_diagnostic
    base_report["headline_findings"] = _headline_findings(base_report["summary"], base_report["aggregate"], cap_hit_labels)
    base_report.update(_evaluate_gate_statuses(rows, base_report["summary"], base_report.get("warnings", [])))
    checkpoint_writer.write(
        "final_aggregation",
        status=str(base_report.get("status") or "unknown"),
        row_count=len(rows),
        evaluated_case_count=len({row["case_id"] for row in rows}),
        cap_hit_label_count=len(base_report.get("cap_hit_labels", []) or []),
    )

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
    if getattr(args, "aggregate_only_report", False):
        base_report["report_scope"] = {
            "mode": "aggregate_only_report",
            "requested_case_limit": int(getattr(args, "case_limit", 0) or 0),
            "selected_case_families": list(getattr(args, "case_family", []) or []),
            "evaluated_case_count": len({row["case_id"] for row in rows}),
            "limitation": (
                "bounded aggregate-only diagnostic; subset selection, smaller case limits, and cap hits are "
                "coverage/report-surface evidence only and do not support full-manifest prevalence, default rg, "
                "universal rg, token-savings, final-answer quality, or product-value claims"
            ),
        }
    _add_metric_sections(base_report)
    if getattr(args, "aggregate_only_report", False):
        _apply_aggregate_only_report(base_report, row_count=len(rows))
    base_report["checkpointing"] = checkpoint_writer.metadata()
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
    subset_selection: dict[str, Any],
) -> dict[str, Any]:
    manifest_floor_status = _case_count_floor_status(manifest_case_count)
    evaluated_floor_status = _case_count_floor_status(len(selected_cases))
    return {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex[:12],
        "status": "pending",
        "data_source": "private_real_history_sanitized_aggregate",
        "manifest_case_count": manifest_case_count,
        "case_count": len(selected_cases),
        "case_families": sorted({case["family"] for case in selected_cases}),
        "historical_original_case_count": 18,
        "case_count_floor_status": evaluated_floor_status,
        "evaluated_case_count_floor_status": evaluated_floor_status,
        "manifest_case_count_floor_status": manifest_floor_status,
        "subset_selection": subset_selection,
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


def _memory_guard_stop(
    args,
    checkpoint_writer: BenchmarkCheckpointWriter,
    *,
    stage: str,
) -> dict[str, Any] | None:
    threshold = int(getattr(args, "memory_guard_rss_bytes", 0) or 0)
    if threshold <= 0:
        return None
    rss_current, rss_reason = current_rss_bytes()
    if rss_current is None:
        checkpoint_writer.write(
            "memory_guard_check",
            status="rss_unavailable",
            guard_stage=stage,
            memory_guard_rss_bytes=threshold,
        )
        return None
    if rss_current < threshold:
        return None
    checkpoint_writer.write(
        "memory_guard_stop",
        status="memory_guard_stop",
        guard_stage=stage,
        rss_current_bytes=rss_current,
        memory_guard_rss_bytes=threshold,
    )
    return {
        "stage": _safe_checkpoint_label(stage),
        "rss_current_bytes": rss_current,
        "rss_current_unavailable_reason": rss_reason,
        "memory_guard_rss_bytes": threshold,
    }


def _finish_memory_guard_stop(
    report: dict[str, Any],
    *,
    rows: list[dict[str, Any]],
    selected_baselines: list[str],
    checkpoint_writer: BenchmarkCheckpointWriter,
    started: float,
    guard_stop: dict[str, Any],
) -> dict[str, Any]:
    warnings = _safe_warnings(list(report.get("warnings") or []) + ["memory_guard_stop"])
    report["status"] = "memory_guard_stop"
    report["warnings"] = warnings
    report["caps_hit"] = bool(_cap_hit_labels(warnings))
    report["cap_hit_labels"] = _cap_hit_labels(warnings)
    report["rows"] = rows
    report["summary"] = _summarize(rows, selected_baselines) if rows else {}
    report["aggregate"] = _aggregate(rows) if rows else {}
    report["total_wall_time_ms"] = _elapsed_ms(started)
    report["memory_guard_stop"] = {
        **guard_stop,
        "decision": "stopped_before_success_to_avoid_memory_pressure",
        "checkpoint_evidence_emitted": checkpoint_writer.enabled,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "session_ids_included": False,
    }
    report["checkpointing"] = checkpoint_writer.metadata()
    report["raw_private_evidence"] = {
        "stored": False,
        "storage": "ignored_local_raw_run_artifacts",
        "committed": False,
        "reason": "memory_guard_stop_before_raw_evidence_write",
    }
    report["ranking_quality_gate"] = _gate_status("not_evaluated", ["memory_guard_stop", "benchmark_incomplete"])
    report["rg_prefilter_gate"] = _gate_status("not_evaluated", ["memory_guard_stop", "benchmark_incomplete"])
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


def _select_cases(cases: list[dict[str, Any]], args) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested_families = [str(item) for item in (getattr(args, "case_family", []) or []) if item]
    selected = cases
    mode = "all_manifest_cases"
    if requested_families:
        allowed = set(requested_families)
        selected = [case for case in cases if case.get("family") in allowed]
        mode = "sanitized_manifest_family_subset"
    if getattr(args, "case_limit", 0) and args.case_limit > 0:
        selected = selected[: args.case_limit]
    return selected, {
        "mode": mode,
        "requested_family_labels": sorted(set(requested_families)),
        "available_case_count": len(cases),
        "selected_case_count": len(selected),
        "selected_family_counts": _counts(case.get("family") for case in selected),
        "selection_basis": (
            "sanitized manifest family labels only; no raw query text, private paths, source lines, "
            "session IDs, local usernames, exact case IDs, expected refs, target lines, or raw anchor needles"
        ),
        "subset_prevalence_claim": False,
        "full_manifest_prevalence_claim": not bool(requested_families),
    }


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


def _run_package_search_fielded_bm25(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    index = kwargs.get("fielded_bm25_index")
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    if index is None:
        index = build_fielded_bm25_index(plan, default_tz=default_tz)
    result = search_with_fielded_bm25(
        index,
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        continuation_ledger=bool(getattr(args, "continuation_ledger", False)),
        expected_ref_diagnostics=case.get("expected_refs")
        if (
            getattr(args, "capture_expected_ref_diagnostics", False)
            or getattr(args, "source_tier_answerability_pressure", False)
        )
        else None,
    )
    fielded_warnings = _safe_warnings(result.get("warnings") or [])
    row_cap_hit_labels = sorted(set(kwargs["cap_hit_labels"] or []) | set(_cap_hit_labels(fielded_warnings)))
    candidates, ranking_reason_label_count = _candidates_from_groups(result.get("groups", []))
    row, raw_row = _row_pair(
        case=case,
        baseline="package_search_fielded_bm25",
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
        warnings=fielded_warnings,
        notes=["opt_in_fielded_sqlite_fts5_bm25_candidate_generator_probe"],
    )
    row["fielded_bm25"] = _fielded_bm25_row_stats(result)
    if getattr(args, "source_tier_answerability_pressure", False):
        _attach_source_tier_answerability_retrieval_diagnostics(
            result,
            index=index,
            plan=plan,
            intent=intent,
            warnings=fielded_warnings,
        )
        source_plan = _agent_find_source_plan(plan=plan, intent=intent, args=args, now=now)
        agent_find = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            manifest_report=None,
            output_format="json",
            report_output=None,
            compact_provenance_handoff=True,
        )["agent_find"]
        compact_handoff = agent_find["compact_provenance_handoff"]
        row["source_tier_answerability_pressure"] = _source_tier_answerability_pressure_row_stats(
            compact_handoff,
            row=row,
            full_result_output_context_bytes=int(row["output_context_bytes"]),
        )
        raw_row["source_tier_answerability_pressure"] = compact_handoff
    expected_diagnostics = _expected_ref_diagnostics_row(result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["fielded_bm25_result"] = result
    return row, raw_row


def _run_package_search_rrf_fusion(**kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    case = kwargs["case"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    cwd = str(Path(args.cwd).expanduser().resolve())
    fused_result = run_rrf_fusion_retrieval(
        plan=plan,
        intent=intent,
        default_tz=default_tz,
        cwd=cwd,
    )
    diagnostics = fused_result.get("retrieval_diagnostics") if isinstance(fused_result.get("retrieval_diagnostics"), dict) else {}
    warnings = _safe_warnings(fused_result.get("warnings") or [])
    row_cap_hit_labels = sorted(set(kwargs["cap_hit_labels"] or []) | set(_cap_hit_labels(warnings)))
    candidates, ranking_reason_label_count = _candidates_from_groups(fused_result.get("groups", []))
    row, raw_row = _row_pair(
        case=case,
        baseline="package_search_rrf_fusion",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=2,
        subprocess_calls=0,
        files_opened=int(fused_result.get("searched_files", 0)),
        records_parsed=int(fused_result.get("records_considered", 0)),
        bytes_scanned=int(fused_result.get("searched_bytes", 0)),
        bytes_parsed=int(fused_result.get("searched_bytes", 0)),
        output_context_bytes=len(json.dumps(fused_result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        cap_hit_labels=row_cap_hit_labels,
        ranking_reason_label_count=ranking_reason_label_count,
        warnings=warnings,
        notes=["opt_in_report_only_shared_retrieval_mode_rrf_fusion_probe"],
    )
    row["rrf_fusion"] = _rrf_fusion_row_stats(fused_result, diagnostics=diagnostics)
    row["fielded_bm25"] = _fielded_bm25_row_stats_from_rrf_diagnostics(diagnostics)
    shared_source_enumeration = _shared_source_enumeration_row_stats(diagnostics)
    if shared_source_enumeration is not None:
        row["shared_source_enumeration"] = shared_source_enumeration
    duplicate_local_work = _duplicate_local_work_row_stats(diagnostics)
    if duplicate_local_work is not None:
        row["duplicate_local_work"] = duplicate_local_work
    expected_diagnostics = _fused_expected_ref_diagnostics(case, fused_result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["rrf_fusion_result"] = fused_result
    return row, raw_row


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
    case = kwargs["case"]
    args = kwargs["args"]
    plan = kwargs["plan"]
    index = kwargs.get("structural_sidecar_index")
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(str(case["query_text"]), now=now)
    expected_refs = case.get("expected_refs") if getattr(args, "capture_expected_ref_diagnostics", False) else None
    hybrid_result = search_structural_hybrid_budgeted(
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        structural_sidecar_index=index,
        expected_ref_diagnostics=expected_refs,
    )
    warnings = _safe_warnings(hybrid_result.get("warnings") or [])
    row_cap_hit_labels = sorted(set(kwargs["cap_hit_labels"] or []) | set(_cap_hit_labels(warnings)))
    candidates, ranking_reason_label_count = _candidates_from_groups(hybrid_result.get("groups", []))
    package_searched_bytes = int(hybrid_result.get("package_searched_bytes", 0))
    structural_searched_bytes = int(hybrid_result.get("structural_searched_bytes", 0))
    gate = hybrid_result.get("gate") or {}
    row, raw_row = _row_pair(
        case=case,
        baseline="package_search_structural_hybrid_budgeted",
        repeat_index=kwargs["repeat_index"],
        started=started,
        tool_calls=2 if gate.get("supplement_enabled") else 1,
        subprocess_calls=0,
        files_opened=int(hybrid_result.get("searched_files", 0)),
        records_parsed=_estimated_package_records_parsed(
            kwargs["files"],
            package_searched_bytes,
            _prefilter_row_stats({}, enabled=False),
            False,
        )
        + int(hybrid_result.get("structural_records_considered") or 0),
        bytes_scanned=package_searched_bytes + structural_searched_bytes,
        bytes_parsed=package_searched_bytes + structural_searched_bytes,
        output_context_bytes=len(json.dumps(hybrid_result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        cap_hit_labels=row_cap_hit_labels,
        ranking_reason_label_count=ranking_reason_label_count,
        warnings=warnings,
        notes=["opt_in_budgeted_structural_supplement_probe"],
    )
    row["structural_hybrid"] = _structural_hybrid_row_stats(
        hybrid_result,
        query_intent_summary=_query_intent_summary(intent, gate=gate),
    )
    expected_diagnostics = _expected_ref_diagnostics_row(hybrid_result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["hybrid_result"] = hybrid_result
    return row, raw_row


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
    records_parsed = _package_records_parsed_from_result(
        result,
        files=files,
        searched_bytes=searched_bytes,
        prefilter=prefilter,
        use_rg_prefilter=use_rg_prefilter,
    )
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
    if getattr(args, "package_compact_handoff", False):
        compact_package = _build_compact_package_handoff(
            result,
            candidates,
            context_lines=int(getattr(args, "context_lines", 0) or 0),
        )
        row["compact_package_handoff"] = _compact_package_row_stats(
            compact_package,
            row=row,
            expected_refs=case.get("expected_refs", []),
            legacy_top_candidate_ref_ids=_candidate_ref_ids(
                candidates,
                case.get("expected_refs", []),
                limit=COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
            ),
            legacy_source_window_ref_ids=_candidate_ref_ids(
                candidates,
                case.get("expected_refs", []),
                limit=COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
            ),
            full_result_output_context_bytes=int(row["output_context_bytes"]),
        )
        raw_row["compact_package_handoff"] = compact_package
    if baseline == "package_search" and getattr(args, "agent_compact_provenance_handoff", False):
        source_plan = _agent_find_source_plan(plan=plan, intent=intent, args=args, now=now)
        agent_find = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            manifest_report=None,
            output_format="json",
            report_output=None,
            compact_provenance_handoff=True,
        )["agent_find"]
        compact_handoff = agent_find["compact_provenance_handoff"]
        row["agent_compact_provenance_handoff"] = _agent_compact_provenance_row_stats(
            compact_handoff,
            row=row,
            full_result_output_context_bytes=int(row["output_context_bytes"]),
        )
        raw_row["agent_compact_provenance_handoff"] = compact_handoff
    row["pre_parse"] = _pre_parse_row_stats(result)
    row["scan_order"] = _scan_order_row(result)
    expected_diagnostics = _expected_ref_diagnostics_row(result)
    if expected_diagnostics is not None:
        row["expected_ref_diagnostics"] = expected_diagnostics
    raw_row["sanitized_row"] = row
    raw_row["package_result"] = result
    return row, raw_row


def _agent_find_source_plan(
    *,
    plan: SourcePlan,
    intent: QueryIntent,
    args,
    now: Any,
) -> dict[str, Any]:
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    return {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "now": now.isoformat(),
    }


def _attach_source_tier_answerability_retrieval_diagnostics(
    result: dict[str, Any],
    *,
    index: Any,
    plan: SourcePlan,
    intent: QueryIntent,
    warnings: list[str],
) -> None:
    planned_jsonl_files = materialize_planned_jsonl_files(plan, preferred_terms=intent.base_terms)
    source_catalog = build_command_local_source_catalog(plan, planned_jsonl_files)
    _selected_jsonl_files, source_plan = build_command_local_source_plan(plan, planned_jsonl_files, intent)
    index_summary = index.sanitized_summary() if index is not None and hasattr(index, "sanitized_summary") else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    result["retrieval_mode"] = "fielded-bm25"
    result["retrieval_diagnostics"] = build_fielded_bm25_retrieval_diagnostics(
        fielded_result=result,
        fielded_index_summary=index_summary,
        fielded_index_elapsed_ms=float(summary.get("build_elapsed_ms") or 0.0),
        fielded_elapsed_ms=float((result.get("profile") or {}).get("elapsed_ms") or 0.0),
        enumeration_elapsed_ms=float(planned_jsonl_files.elapsed_ms),
        planned_jsonl_file_diagnostics=planned_jsonl_files.to_diagnostics(),
        source_catalog=source_catalog,
        source_plan=source_plan,
        warnings=warnings,
        profiles={
            "fielded_bm25_index_build": summary.get("profile") if isinstance(summary.get("profile"), dict) else {},
            "fielded_bm25_search_materialization": result.get("profile")
            if isinstance(result.get("profile"), dict)
            else {},
        },
    )


def _build_compact_package_handoff(
    result: dict[str, Any],
    candidates: list[Candidate],
    *,
    context_lines: int,
) -> dict[str, Any]:
    matches = _flat_matches(result.get("groups", []))
    top_matches = matches[:COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT]
    top_candidates = []
    for index, match in enumerate(top_matches, start=1):
        source_ref = _match_source_ref(match)
        top_candidates.append(
            {
                "candidate_id": f"c{index:03d}",
                "rank": index,
                "source_ref": source_ref,
                "timestamp": match.get("timestamp"),
                "role": match.get("role"),
                "score": match.get("score"),
                "reason_label_count": len(match.get("reasons") or []),
            }
        )
    source_windows = []
    for index, match in enumerate(matches[:COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT], start=1):
        line_value = match.get("line")
        if not isinstance(line_value, int):
            continue
        window = _match_source_window(match)
        source_windows.append(
            {
                "candidate_id": f"c{index:03d}",
                "source_ref": _match_source_ref(match),
                "context_radius": 0,
                "requested_context_radius": context_lines,
                "window_basis": "candidate_snippet_clipped",
                "start_line": window["start_line"],
                "end_line": window["end_line"],
                "line_count": window["line_count"],
                "byte_count": window["byte_count"],
                "text": window["text"],
            }
        )
    payload = {
        "schema_version": 1,
        "package_kind": "compact_package_source_locator_source_window",
        "candidate_count": len(candidates),
        "top_candidate_limit": COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
        "source_window_limit": COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
        "top_candidates": top_candidates,
        "source_windows": source_windows,
        "no_evidence_meaning": {
            "answer_scope": "not_found_in_searched_scope",
            "not_all_history_absence": True,
        },
        "privacy_note": "Ignored raw private compact handoff detail. Do not commit.",
    }
    locator_payload = dict(payload)
    locator_payload["source_windows"] = []
    return {
        "payload": payload,
        "compact_context_bytes": _json_bytes(payload),
        "locator_context_bytes": _json_bytes(locator_payload),
        "source_window_text_bytes": sum(int(window.get("byte_count") or 0) for window in source_windows),
        "source_window_count": len(source_windows),
        "top_candidate_count": len(top_candidates),
    }


def _match_source_ref(match: dict[str, Any]) -> dict[str, Any]:
    source_ref = {
        "path": match.get("path") or "",
        "line": int(match.get("line") or 0),
    }
    if match.get("session"):
        source_ref["session"] = match.get("session")
    return source_ref


def _match_source_window(match: dict[str, Any]) -> dict[str, Any]:
    line = int(match.get("line") or 0)
    text = str(match.get("snippet") or "")
    encoded = text.encode("utf-8")
    if len(encoded) > COMPACT_PACKAGE_SOURCE_WINDOW_BYTE_LIMIT:
        text = encoded[:COMPACT_PACKAGE_SOURCE_WINDOW_BYTE_LIMIT].decode("utf-8", errors="ignore")
        text = text.rstrip() + "..."
    return {
        "start_line": line,
        "end_line": line,
        "line_count": 1 if text else 0,
        "byte_count": len(text.encode("utf-8")),
        "text": text,
    }


def _compact_package_row_stats(
    compact_package: dict[str, Any],
    *,
    row: dict[str, Any],
    expected_refs: list[dict[str, Any]],
    legacy_top_candidate_ref_ids: list[str],
    legacy_source_window_ref_ids: list[str],
    full_result_output_context_bytes: int,
) -> dict[str, Any]:
    compact_context_bytes = int(compact_package.get("compact_context_bytes") or 0)
    ratio = round(compact_context_bytes / full_result_output_context_bytes, 6) if full_result_output_context_bytes else 0.0
    payload = compact_package.get("payload") if isinstance(compact_package.get("payload"), dict) else {}
    compact_top_candidate_ref_ids = _compact_payload_ref_ids(
        payload.get("top_candidates"),
        expected_refs,
        limit=COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
    )
    compact_top_candidate_source_refs = [f"sanitized_ref:{ref_id}" for ref_id in compact_top_candidate_ref_ids]
    compact_source_window_ref_ids = _compact_payload_ref_ids(
        payload.get("source_windows"),
        expected_refs,
        limit=COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
    )
    legacy_top_candidate_source_refs = [f"sanitized_ref:{ref_id}" for ref_id in legacy_top_candidate_ref_ids]
    compact_source_window_source_refs = [f"sanitized_ref:{ref_id}" for ref_id in compact_source_window_ref_ids]
    legacy_source_window_source_refs = [f"sanitized_ref:{ref_id}" for ref_id in legacy_source_window_ref_ids]
    return {
        "enabled": True,
        "package_kind": "compact_package_source_locator_source_window",
        "candidate_count": int(row.get("candidate_count") or 0),
        "top_candidate_count": int(compact_package.get("top_candidate_count") or 0),
        "source_window_count": int(compact_package.get("source_window_count") or 0),
        "top_candidate_limit": COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
        "source_window_limit": COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
        "full_result_output_context_bytes": full_result_output_context_bytes,
        "compact_context_bytes": compact_context_bytes,
        "locator_context_bytes": int(compact_package.get("locator_context_bytes") or 0),
        "source_window_text_bytes": int(compact_package.get("source_window_text_bytes") or 0),
        "compact_to_full_result_byte_ratio": ratio,
        "compact_byte_delta_vs_full_result": compact_context_bytes - full_result_output_context_bytes,
        "legacy_output_context_bytes_preserved": True,
        "compact_bytes_measured_separately": True,
        "equivalence_scope": {
            "top_candidate_scope": "same_ordered_top_candidate_limit",
            "top_candidate_limit": COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
            "source_window_scope": "same_ordered_source_window_limit",
            "source_window_limit": COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
            "comparison_basis": "compact_payload_source_refs_vs_legacy_full_result_source_refs",
        },
        "top_candidate_ref_ids_equivalent": compact_top_candidate_ref_ids == legacy_top_candidate_ref_ids,
        "top_candidate_source_refs_equivalent": compact_top_candidate_source_refs == legacy_top_candidate_source_refs,
        "source_window_ref_ids_equivalent": compact_source_window_ref_ids == legacy_source_window_ref_ids,
        "source_window_source_refs_equivalent": compact_source_window_source_refs == legacy_source_window_source_refs,
        "top_candidate_ref_id_count": len(legacy_top_candidate_ref_ids),
        "top_candidate_source_ref_count": len(legacy_top_candidate_source_refs),
        "compact_payload_top_candidate_ref_id_count": len(compact_top_candidate_ref_ids),
        "compact_payload_source_window_ref_id_count": len(compact_source_window_ref_ids),
        "legacy_source_window_ref_id_count": len(legacy_source_window_ref_ids),
        "privacy": _compact_package_handoff_privacy(),
    }


def _candidate_ref_ids(
    candidates: list[Candidate],
    expected_refs: list[dict[str, Any]],
    *,
    limit: int,
) -> list[str]:
    expected = {(ref["path"], ref["line"]): ref["ref_id"] for ref in expected_refs}
    return [
        expected[(candidate.path, candidate.line)]
        for candidate in candidates[:limit]
        if (candidate.path, candidate.line) in expected
    ]


def _compact_payload_ref_ids(
    items: Any,
    expected_refs: list[dict[str, Any]],
    *,
    limit: int,
) -> list[str]:
    expected = {(str(ref["path"]), int(ref["line"])): ref["ref_id"] for ref in expected_refs}
    ref_ids: list[str] = []
    for item in list(items or [])[:limit]:
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref")
        if not isinstance(ref, dict):
            continue
        path = ref.get("path")
        line = ref.get("line")
        try:
            key = (str(path), int(line))
        except (TypeError, ValueError):
            continue
        if key in expected:
            ref_ids.append(expected[key])
    return ref_ids


def _json_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


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
    result = _shared_hybrid_package_structural_result(
        package_result,
        structural_result,
        supplement_limit=supplement_limit,
        gate=gate,
    )
    result["package_warnings"] = _safe_warnings(result.get("package_warnings") or [])
    result["structural_warnings"] = _safe_warnings(result.get("structural_warnings") or [])
    return result


def _structural_supplement_gate(
    *,
    package_candidate_count: int,
    threshold: int | None,
    intent: QueryIntent | None = None,
    gate_profile: str = "candidate_count",
) -> dict[str, Any]:
    return _shared_structural_supplement_gate(
        package_candidate_count=package_candidate_count,
        threshold=threshold,
        intent=intent,
        gate_profile=gate_profile,
    )


def _flat_matches(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [match for group in groups for match in group.get("matches", []) if isinstance(match, dict)]


def _match_keys(groups: list[dict[str, Any]]) -> set[tuple[str, int]]:
    return {_match_key(match) for match in _flat_matches(groups)}


def _match_key(match: dict[str, Any]) -> tuple[str, int]:
    return (str(match.get("path") or ""), int(match.get("line") or 0))


def _structural_hybrid_row_stats(
    result: dict[str, Any],
    *,
    query_intent_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record_window = result.get("record_window") if isinstance(result.get("record_window"), dict) else {}
    stats = {
        "enabled": True,
        "status": str(result.get("status") or "unknown"),
        "strategy": str(result.get("strategy") or "unknown"),
        "package_candidate_count": int(result.get("package_candidate_count") or 0),
        "structural_supplement_candidate_count": int(result.get("structural_supplement_candidate_count") or 0),
        "supplement_limit": int(result.get("supplement_limit") or 0),
        "candidate_selection_profile": str(result.get("candidate_selection_profile") or "default"),
        "gate": result.get("gate") or {},
        "supplement_enabled": bool((result.get("gate") or {}).get("supplement_enabled")),
        "package_searched_bytes": int(result.get("package_searched_bytes") or 0),
        "structural_searched_bytes": int(result.get("structural_searched_bytes") or 0),
        "structural_records_considered": int(result.get("structural_records_considered") or 0),
        "record_window": record_window,
        "record_window_enabled": bool(result.get("record_window_enabled")),
        "record_window_radius": int(result.get("record_window_radius") or 0),
        "record_window_seed_entry_count": int(result.get("record_window_seed_entry_count") or 0),
        "record_window_extra_entry_count": int(result.get("record_window_extra_entry_count") or 0),
        "record_window_entry_count": int(result.get("record_window_entry_count") or 0),
        "record_window_max_extra_entries_per_file": int(
            result.get("record_window_max_extra_entries_per_file") or 0
        ),
        "record_window_max_extra_entries_total": int(result.get("record_window_max_extra_entries_total") or 0),
        "oracle_inputs_used": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "expected_refs_used_for_retrieval": False,
    }
    if query_intent_summary is not None:
        stats["query_intent_summary"] = query_intent_summary
    return stats


def _query_intent_summary(intent: QueryIntent, *, gate: dict[str, Any]) -> dict[str, Any]:
    date_or_timeline_present = bool(
        intent.date_reason or intent.date_window_start is not None or intent.date_window_end is not None
    )
    temporal_question_reason = _temporal_question_reason_label(intent.temporal_question_reason)
    role_label = str(intent.role) if intent.role in {"user", "assistant"} else None
    high_candidate_labels: list[str] = []
    if date_or_timeline_present:
        high_candidate_labels.append("date_or_timeline_intent")
    if role_label is not None:
        high_candidate_labels.append("role_intent")
    return {
        "schema_version": 1,
        "diagnostic_scope": "package_search_structural_hybrid_budgeted",
        "date_or_timeline_intent_present": date_or_timeline_present,
        "date_window_present": bool(intent.date_window_start is not None or intent.date_window_end is not None),
        "date_window_filter_present": bool(intent.date_window_filter),
        "date_reason_label": _date_reason_label(intent.date_reason),
        "temporal_question_intent_present": temporal_question_reason is not None,
        "temporal_question_reason_label": temporal_question_reason,
        "temporal_question_feeds_structural_gate": False,
        "role_intent_present": role_label is not None,
        "role_label": role_label,
        "role_filter_explicit": bool(intent.role_filter_explicit),
        "artifact_hint_count": len(intent.artifact_hints),
        "artifact_hint_count_bucket": _count_bucket(len(intent.artifact_hints)),
        "base_term_count": len(intent.base_terms),
        "base_term_count_bucket": _count_bucket(len(intent.base_terms)),
        "query_term_count": len(intent.terms),
        "query_term_count_bucket": _count_bucket(len(intent.terms)),
        "expansion_reason_count": len(intent.expansion_reasons),
        "expansion_reason_count_bucket": _count_bucket(len(intent.expansion_reasons)),
        "warning_count": len(intent.warnings),
        "intent_structural_high_candidate_count_signal_labels": high_candidate_labels,
        "intent_structural_high_candidate_count_could_trigger": bool(high_candidate_labels),
        "gate_profile": _gate_profile_label(gate.get("gate_profile")),
        "gate_reason": _gate_reason_label(gate.get("gate_reason")),
        "gate_supplement_enabled": bool(gate.get("supplement_enabled")),
        "gate_candidate_threshold": gate.get("gate_candidate_threshold"),
        "gate_package_candidate_count": int(gate.get("package_candidate_count") or 0),
        "gate_intent_override_enabled": bool(gate.get("intent_structural_high_candidate_count_enabled")),
        "gate_intent_override_reason_labels": _intent_reason_labels(
            gate.get("intent_structural_high_candidate_count_reasons")
        ),
        "oracle_inputs_used": False,
        "privacy": {
            "counts_and_labels_only": True,
            "raw_query_text_included": False,
            "raw_query_terms_included": False,
            "raw_base_terms_included": False,
            "raw_expansion_text_included": False,
            "raw_artifact_hints_included": False,
            "raw_private_text_included": False,
            "raw_snippets_included": False,
            "raw_paths_included": False,
            "local_usernames_included": False,
            "raw_session_ids_included": False,
            "source_lines_included": False,
            "target_lines_included": False,
        },
    }


def _gate_profile_label(value: Any) -> str:
    profile = str(value or "candidate_count")
    if profile in {"candidate_count", "intent_structural_high_candidate_count"}:
        return profile
    return "unknown_gate_profile"


def _gate_reason_label(value: Any) -> str:
    reason = str(value or "unknown")
    if reason in {
        "ungated",
        "package_candidate_count_at_or_below_threshold",
        "package_candidate_count_above_threshold",
        "intent_structural_high_candidate_count",
    }:
        return reason
    return "unknown_gate_reason"


def _intent_reason_labels(value: Any) -> list[str]:
    labels = value if isinstance(value, list) else []
    return [
        str(label)
        for label in labels
        if label in {"date_or_timeline_intent", "role_intent"}
    ]


def _date_reason_label(reason: str | None) -> str | None:
    if reason is None:
        return None
    if reason in {"yesterday", "last night", "last week", "a few days ago", "recent"}:
        return "relative_date_window"
    return "timeline_or_date_metadata"


def _temporal_question_reason_label(reason: str | None) -> str | None:
    if reason in {"when_question", "what_date_time_question", "which_date_time_question"}:
        return reason
    return None


def _count_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count <= 3:
        return "1-3"
    if count <= 10:
        return "4-10"
    if count <= 40:
        return "11-40"
    return "41_plus"


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
    pre_parse = _pre_parse_row_stats(result)
    return {
        "strategy": str(result.get("scan_order_strategy") or "unknown"),
        "record_quantum": int(result.get("scan_record_quantum") or 0),
        "planned_files_count": int(result.get("planned_files_count") or 0),
        "opened_files_count": int(result.get("opened_files_count") or result.get("searched_files") or 0),
        "records_read": int(result.get("records_read") or 0),
        "raw_lines_examined": int(result.get("raw_lines_examined") or 0),
        "raw_lines_skipped_before_full_json_parse": int(
            result.get("raw_lines_skipped_before_full_json_parse") or 0
        ),
        "full_json_records_parsed": int(result.get("full_json_records_parsed") or 0),
        "pre_parse": pre_parse,
        "records_rejected_by_should_consider": int(result.get("records_rejected_by_should_consider") or 0),
        "candidate_records_scored": int(result.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(result.get("candidate_records_deduped") or 0),
        "candidate_strength": _candidate_strength_row_stats(result),
        "dedupe_strategy": str(result.get("dedupe_strategy") or "unknown"),
        "raw_paths_included": False,
    }


def _pre_parse_row_stats(result: dict[str, Any]) -> dict[str, Any]:
    stats = result.get("pre_parse")
    if not isinstance(stats, dict):
        return {
            "enabled": False,
            "eligible": False,
            "status": "unavailable",
            "strategy": None,
            "applies_to": "line_oriented_jsonl_records_only",
            "needle_policy": None,
            "needle_count": 0,
            "anchor_kind_counts": {},
            "raw_lines_examined": int(result.get("raw_lines_examined") or 0),
            "raw_lines_skipped_before_full_json_parse": int(
                result.get("raw_lines_skipped_before_full_json_parse") or 0
            ),
            "full_json_records_parsed": int(result.get("full_json_records_parsed") or 0),
            "raw_anchor_needles_included": False,
        }
    return {
        "enabled": bool(stats.get("enabled")),
        "eligible": bool(stats.get("eligible")),
        "status": str(stats.get("status") or "unknown"),
        "strategy": stats.get("strategy"),
        "applies_to": str(stats.get("applies_to") or "line_oriented_jsonl_records_only"),
        "needle_policy": stats.get("needle_policy"),
        "needle_count": int(stats.get("needle_count") or 0),
        "anchor_kind_counts": _safe_count_dict(stats.get("anchor_kind_counts")),
        "raw_lines_examined": int(stats.get("raw_lines_examined") or 0),
        "raw_lines_skipped_before_full_json_parse": int(stats.get("raw_lines_skipped_before_full_json_parse") or 0),
        "full_json_records_parsed": int(stats.get("full_json_records_parsed") or 0),
        "raw_anchor_needles_included": False,
    }


def _candidate_strength_row_stats(result: dict[str, Any]) -> dict[str, Any]:
    stats = result.get("candidate_strength")
    if not isinstance(stats, dict):
        return {
            "status": "unavailable",
            "strategy": None,
            "strong_reservoir_limit": 0,
            "weak_reservoir_limit": 0,
            "strong_candidates": 0,
            "weak_candidates": 0,
            "weak_partial_generic_candidates": 0,
            "weak_broad_unanchored_low_evidence_candidates": 0,
            "strong_reservoir_retained": 0,
            "strong_reservoir_replaced": 0,
            "strong_reservoir_cap_dropped": 0,
            "strong_high_evidence_candidates": 0,
            "strong_high_evidence_reservoir_retained": 0,
            "strong_high_evidence_reservoir_cap_dropped": 0,
            "weak_reservoir_retained": 0,
            "weak_reservoir_replaced": 0,
            "weak_reservoir_cap_dropped": 0,
            "raw_private_text_included": False,
            "raw_paths_included": False,
        }
    return {
        "status": "evaluated",
        "strategy": stats.get("strategy"),
        "strong_reservoir_limit": int(stats.get("strong_reservoir_limit") or 0),
        "weak_reservoir_limit": int(stats.get("weak_reservoir_limit") or 0),
        "strong_candidates": int(stats.get("strong_candidates") or 0),
        "weak_candidates": int(stats.get("weak_candidates") or 0),
        "weak_partial_generic_candidates": int(stats.get("weak_partial_generic_candidates") or 0),
        "weak_broad_unanchored_low_evidence_candidates": int(
            stats.get("weak_broad_unanchored_low_evidence_candidates") or 0
        ),
        "strong_reservoir_retained": int(stats.get("strong_reservoir_retained") or 0),
        "strong_reservoir_replaced": int(stats.get("strong_reservoir_replaced") or 0),
        "strong_reservoir_cap_dropped": int(stats.get("strong_reservoir_cap_dropped") or 0),
        "strong_high_evidence_candidates": int(stats.get("strong_high_evidence_candidates") or 0),
        "strong_high_evidence_reservoir_retained": int(
            stats.get("strong_high_evidence_reservoir_retained") or 0
        ),
        "strong_high_evidence_reservoir_cap_dropped": int(
            stats.get("strong_high_evidence_reservoir_cap_dropped") or 0
        ),
        "weak_reservoir_retained": int(stats.get("weak_reservoir_retained") or 0),
        "weak_reservoir_replaced": int(stats.get("weak_reservoir_replaced") or 0),
        "weak_reservoir_cap_dropped": int(stats.get("weak_reservoir_cap_dropped") or 0),
        "raw_private_text_included": False,
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


def _fielded_bm25_row_stats(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    fts = result.get("fts") if isinstance(result.get("fts"), dict) else {}
    query_profile = result.get("query_profile") if isinstance(result.get("query_profile"), dict) else {}
    privacy = result.get("privacy") if isinstance(result.get("privacy"), dict) else {}
    materialization = _fielded_bm25_materialization_stats(result.get("materialization"))
    query_streams = _fielded_bm25_query_stream_stats(result.get("fielded_query_streams"))
    branch_fanout = _fielded_bm25_branch_fanout_stats(result.get("fielded_branch_fanout"))
    source_grouping = _fielded_bm25_source_grouping_stats(result.get("fielded_source_grouping"))
    phase_profile = _phase_profile_stats(result.get("profile"), "fielded_bm25_search_materialization")
    index_build_profile = _phase_profile_stats(summary.get("profile"), "fielded_bm25_index_build")
    return {
        "enabled": True,
        "status": str(result.get("status") or "unknown"),
        "fallback_reason": result.get("fallback_reason"),
        "fts_available": bool(fts.get("available")),
        "fts_engine": str(fts.get("engine") or "unknown"),
        "score_function": str(fts.get("score_function") or "unknown"),
        "indexed_file_count": int(summary.get("indexed_file_count") or 0),
        "indexed_record_count": int(summary.get("indexed_record_count") or 0),
        "indexed_chunk_count": int(summary.get("indexed_chunk_count") or 0),
        "candidate_rows": int(result.get("candidate_rows") or 0),
        "candidate_row_limit": int(result.get("candidate_row_limit") or 0),
        "candidate_rows_generated": int(result.get("candidate_rows_generated") or 0),
        "candidate_rows_retained": int(result.get("candidate_rows_retained") or 0),
        "candidate_rows_surfaced": int(result.get("candidate_rows_surfaced") or 0),
        "opened_file_count": int(result.get("opened_files") or result.get("searched_files") or 0),
        "records_considered": int(result.get("records_considered") or 0),
        "records_rejected_by_existing_ranker": int(result.get("records_rejected_by_existing_ranker") or 0),
        "candidate_records_scored": int(result.get("candidate_records_scored") or 0),
        "candidate_records_deduped": int(result.get("candidate_records_deduped") or 0),
        "bm25_rank_buckets": _safe_count_dict(result.get("bm25_rank_buckets")),
        "query_streams": query_streams,
        "branch_fanout": branch_fanout,
        "source_grouping": source_grouping,
        "materialization": materialization,
        "continuation_candidate_ledger": _fielded_bm25_continuation_ledger_row_stats(
            result.get("continuation_candidate_ledger")
        ),
        "phase_profile": phase_profile,
        "index_build_profile": index_build_profile,
        "query_profile": query_profile,
        "expected_refs_used_for_retrieval": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": bool(privacy.get("vectors_or_persisted_indexes_included", False)),
    }


def _fielded_bm25_continuation_ledger_row_stats(value: Any) -> dict[str, Any]:
    ledger = value if isinstance(value, dict) else {}
    if not ledger:
        return {
            "enabled": False,
            "status": "not_enabled",
            "mode": "not_enabled",
            "pass_count": 0,
            "retrieval_chunk_count": 0,
            "multi_pass": False,
            "ledger_item_limit": 0,
            "ledger_item_count": 0,
            "candidate_row_pool_limit": 0,
            "total_candidate_rows_discovered": 0,
            "candidate_rows_retained_for_materialization": 0,
            "candidate_rows_materialized": 0,
            "final_packet_candidate_count": 0,
            "final_packet_present_count": 0,
            "recoverable_omitted_count": 0,
            "still_unreached_count": 0,
            "continuation_available": False,
            "classification_counts": {
                "present_final": 0,
                "recoverable_omitted": 0,
                "still_unreached": 0,
            },
            "stage_counts": {},
            "loss_stage_summary": {},
            "privacy_status": "not_evaluated",
            "privacy": _fielded_bm25_continuation_ledger_privacy(),
            "claim_boundaries": _fielded_bm25_continuation_ledger_claim_boundaries(),
        }
    privacy = ledger.get("privacy") if isinstance(ledger.get("privacy"), dict) else {}
    policy = ledger.get("continuation_policy") if isinstance(ledger.get("continuation_policy"), dict) else {}
    claim_boundaries = (
        ledger.get("claim_boundaries") if isinstance(ledger.get("claim_boundaries"), dict) else {}
    )
    raw_payload_flags = {
        "raw_paths_included": bool(
            privacy.get("raw_paths_included")
            or policy.get("raw_paths_included")
            or policy.get("raw_path_included")
        ),
        "raw_text_included": bool(privacy.get("raw_text_included") or policy.get("raw_text_included")),
        "raw_snippets_included": bool(
            privacy.get("raw_snippets_included") or policy.get("raw_snippets_included")
        ),
        "source_lines_included": bool(
            privacy.get("source_lines_included")
            or policy.get("source_lines_included")
            or policy.get("source_line_included")
        ),
        "line_numbers_included": bool(
            privacy.get("line_numbers_included") or policy.get("line_numbers_included")
        ),
        "raw_session_ids_included": bool(
            privacy.get("raw_session_ids_included") or policy.get("raw_session_ids_included")
        ),
        "vectors_or_persisted_indexes_included": bool(
            privacy.get("vectors_or_persisted_indexes_included")
            or policy.get("vectors_or_persisted_indexes_included")
        ),
    }
    privacy_status = (
        "aggregate_safe"
        if not any(raw_payload_flags.values())
        else "privacy_boundary_violation_detected"
    )
    final_present = int(ledger.get("final_packet_present_count") or 0)
    recoverable = int(ledger.get("recoverable_omitted_count") or 0)
    unreached = int(ledger.get("still_unreached_count") or 0)
    return {
        "enabled": bool(ledger.get("enabled", True)),
        "status": "evaluated",
        "mode": str(ledger.get("mode") or "fielded_bm25_opt_in_continuation_candidate_ledger"),
        "version": str(ledger.get("version") or ""),
        "bounded": bool(ledger.get("bounded", False)),
        "pass_count": int(ledger.get("pass_count") or 0),
        "retrieval_chunk_count": int(ledger.get("retrieval_chunk_count") or 0),
        "multi_pass": bool(ledger.get("multi_pass", False)),
        "ledger_item_limit": int(ledger.get("ledger_item_limit") or 0),
        "ledger_item_count": int(ledger.get("ledger_item_count") or 0),
        "candidate_row_pool_limit": int(ledger.get("candidate_row_pool_limit") or 0),
        "total_candidate_rows_discovered": int(ledger.get("total_candidate_rows_discovered") or 0),
        "candidate_rows_retained_for_materialization": int(
            ledger.get("candidate_rows_retained_for_materialization") or 0
        ),
        "candidate_rows_materialized": int(ledger.get("candidate_rows_materialized") or 0),
        "final_packet_candidate_count": int(ledger.get("final_packet_candidate_count") or 0),
        "final_packet_present_count": final_present,
        "recoverable_omitted_count": recoverable,
        "still_unreached_count": unreached,
        "continuation_available": bool(ledger.get("continuation_available", False)),
        "classification_counts": {
            "present_final": final_present,
            "recoverable_omitted": recoverable,
            "still_unreached": unreached,
        },
        "stage_counts": _safe_count_dict(ledger.get("stage_counts")),
        "loss_stage_summary": _safe_count_dict(ledger.get("loss_stage_summary")),
        "policy": {
            "cursor_type": str(policy.get("cursor_type") or "none"),
            "resume_semantics": str(policy.get("resume_semantics") or "none"),
            "stable_order": str(policy.get("stable_order") or "none"),
            "materialization": str(policy.get("materialization") or "none"),
            "default_enabled": bool(policy.get("default_enabled", True)),
        },
        "privacy_status": privacy_status,
        **raw_payload_flags,
        "privacy": {
            **_fielded_bm25_continuation_ledger_privacy(),
            **raw_payload_flags,
            "aggregate_only": True,
            "ledger_items_included": False,
            "cursors_included": False,
            "source_ref_hashes_included": False,
        },
        "claim_boundaries": {
            **_fielded_bm25_continuation_ledger_claim_boundaries(),
            "final_answer_quality_claim_supported": bool(
                claim_boundaries.get("final_answer_quality_claim_supported", False)
            ),
            "token_savings_claim_supported": bool(claim_boundaries.get("token_savings_claim_supported", False)),
            "default_promotion_supported": bool(claim_boundaries.get("default_promotion_supported", False)),
            "all_history_completeness_claim_supported": bool(
                claim_boundaries.get("all_history_completeness_claim_supported", False)
            ),
        },
    }


def _rrf_fusion_row_stats(
    result: dict[str, Any],
    *,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    privacy = result.get("privacy") if isinstance(result.get("privacy"), dict) else {}
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    rrf_summary = diagnostics.get("rrf_summary") if isinstance(diagnostics.get("rrf_summary"), dict) else {}
    package_lane = _rrf_diagnostic_lane(diagnostics, "package_search")
    fielded_lane = _rrf_diagnostic_lane(diagnostics, "package_search_fielded_bm25")
    fusion = diagnostics.get("fusion") if isinstance(diagnostics.get("fusion"), dict) else {}
    phase_profiles = _rrf_phase_profiles(diagnostics)
    return {
        "enabled": True,
        "feature_state": str(diagnostics.get("feature_state") or "shadow_report_only"),
        "status": str(result.get("status") or "unknown"),
        "strategy": str(result.get("strategy") or "unknown"),
        "rank_constant": int(result.get("rank_constant") or 0),
        "lane_count": int(result.get("lane_count") or 0),
        "lane_names": [str(item) for item in result.get("lane_names", [])],
        "equal_lane_weights": bool(privacy.get("equal_lane_weights")),
        "candidate_window_size": result.get("candidate_window_size"),
        "candidate_window_sizes": _safe_count_dict(result.get("candidate_window_sizes")),
        "lane_unique_candidate_counts": _safe_count_dict(result.get("lane_unique_candidate_counts")),
        "overlap_count": int(result.get("overlap_count") or 0),
        "jaccard": float(result.get("jaccard") or 0.0),
        "package_only_count": int(result.get("package_only_count") or 0),
        "fielded_only_count": int(result.get("fielded_only_count") or 0),
        "top_n_fielded_only_promotion_count": int(result.get("top_n_fielded_only_promotion_count") or 0),
        "candidate_count": int(result.get("candidate_count") or 0),
        "package_candidate_count": int(package_lane.get("candidate_count") or 0),
        "fielded_candidate_count": int(fielded_lane.get("candidate_count") or 0),
        "package_latency_ms": float(package_lane.get("elapsed_ms") or 0.0),
        "fielded_latency_ms": float(fielded_lane.get("elapsed_ms") or 0.0),
        "fusion_latency_ms": float(fusion.get("elapsed_ms") or result.get("fusion_elapsed_ms") or 0.0),
        "phase_profiles": phase_profiles,
        "shared_retrieval_mode_used": result.get("retrieval_mode") == "rrf-fusion",
        "shared_source_enumeration_used": bool((diagnostics.get("shared_source_enumeration") or {}).get("consumed_by_lanes")),
        "duplicate_local_work_diagnostics_present": isinstance(diagnostics.get("duplicate_local_work"), dict),
        "diagnostic_candidate_count": int(rrf_summary.get("candidate_count") or result.get("candidate_count") or 0),
        "raw_score_inputs_used_for_fusion": bool(privacy.get("raw_score_inputs_used_for_fusion", True)),
        "score_normalization_used": bool(privacy.get("score_normalization_used", True)),
        "fused_score_threshold_used": bool(privacy.get("fused_score_threshold_used", True)),
        "benchmark_tuned_weights_used": bool(privacy.get("benchmark_tuned_weights_used", True)),
        "raw_paths_included": False,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "expected_refs_used_for_retrieval": False,
        "privacy": _rrf_fusion_privacy(),
    }


def _rrf_diagnostic_lane(diagnostics: dict[str, Any], name: str) -> dict[str, Any]:
    lanes = diagnostics.get("lanes") if isinstance(diagnostics.get("lanes"), list) else []
    for lane in lanes:
        if isinstance(lane, dict) and lane.get("name") == name:
            return lane
    return {}


def _fielded_bm25_row_stats_from_rrf_diagnostics(diagnostics: dict[str, Any]) -> dict[str, Any]:
    fielded_lane = _rrf_diagnostic_lane(diagnostics, "package_search_fielded_bm25")
    index_summary = fielded_lane.get("index_summary") if isinstance(fielded_lane.get("index_summary"), dict) else {}
    materialization = _fielded_bm25_materialization_stats(fielded_lane.get("materialization"))
    query_streams = _fielded_bm25_query_stream_stats(fielded_lane.get("query_streams"))
    source_grouping = _fielded_bm25_source_grouping_stats(fielded_lane.get("source_grouping"))
    phase_profile = _phase_profile_stats(
        fielded_lane.get("profile"),
        "fielded_bm25_search_materialization",
    )
    index_build_profile = _phase_profile_stats(
        fielded_lane.get("index_build_profile"),
        "fielded_bm25_index_build",
    )
    return {
        "enabled": True,
        "status": "shared_rrf_fusion_lane",
        "fallback_reason": None,
        "fts_available": bool(index_summary.get("available")),
        "fts_engine": str(index_summary.get("engine") or "unknown"),
        "score_function": "bm25",
        "indexed_file_count": int(index_summary.get("indexed_file_count") or 0),
        "indexed_record_count": int(index_summary.get("indexed_record_count") or 0),
        "indexed_chunk_count": int(index_summary.get("indexed_chunk_count") or 0),
        "candidate_rows": int(fielded_lane.get("candidate_rows") or 0),
        "candidate_row_limit": int(fielded_lane.get("candidate_row_limit") or 0),
        "candidate_rows_generated": int(fielded_lane.get("candidate_rows") or 0),
        "candidate_rows_retained": int(fielded_lane.get("candidate_count") or 0),
        "candidate_rows_surfaced": int(fielded_lane.get("candidate_count") or 0),
        "opened_file_count": int(fielded_lane.get("searched_files") or 0),
        "records_considered": 0,
        "records_rejected_by_existing_ranker": 0,
        "candidate_records_scored": 0,
        "candidate_records_deduped": 0,
        "bm25_rank_buckets": {},
        "query_streams": query_streams,
        "branch_fanout": _fielded_bm25_branch_fanout_stats({}),
        "source_grouping": source_grouping,
        "materialization": materialization,
        "phase_profile": phase_profile,
        "index_build_profile": index_build_profile,
        "query_profile": {},
        "expected_refs_used_for_retrieval": False,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _fielded_bm25_materialization_stats(value: Any) -> dict[str, Any]:
    materialization = value if isinstance(value, dict) else {}
    return {
        "candidate_rows_materialized": int(materialization.get("candidate_rows_materialized") or 0),
        "candidate_rows_materialized_from_in_memory_raw": int(
            materialization.get("candidate_rows_materialized_from_in_memory_raw") or 0
        ),
        "candidate_rows_materialized_from_cached_record": int(
            materialization.get("candidate_rows_materialized_from_cached_record") or 0
        ),
        "candidate_rows_materialized_from_file_reopen": int(
            materialization.get("candidate_rows_materialized_from_file_reopen") or 0
        ),
        "candidate_rows_materialization_failed": int(
            materialization.get("candidate_rows_materialization_failed") or 0
        ),
        "records_materialized_from_in_memory_raw": int(
            materialization.get("records_materialized_from_in_memory_raw") or 0
        ),
        "records_materialized_from_cached_record": int(
            materialization.get("records_materialized_from_cached_record") or 0
        ),
        "records_materialized_from_file_reopen": int(
            materialization.get("records_materialized_from_file_reopen") or 0
        ),
        "file_reopen_attempts": int(materialization.get("file_reopen_attempts") or 0),
        "file_reopen_failures": int(materialization.get("file_reopen_failures") or 0),
        "in_memory_raw_materialization_available": bool(
            materialization.get("in_memory_raw_materialization_available", False)
        ),
        "raw_text_included": bool(materialization.get("raw_text_included", False)),
        "raw_paths_included": bool(materialization.get("raw_paths_included", False)),
    }


def _fielded_bm25_query_stream_stats(value: Any) -> dict[str, Any]:
    streams = value if isinstance(value, dict) else {}
    selected_by_stream = _safe_count_dict(streams.get("selected_rows_by_stream"))
    dropped_by_stream = _safe_count_dict(streams.get("rows_dropped_by_stream_cap"))
    return {
        "enabled": bool(streams.get("enabled", False)),
        "stream_count": int(streams.get("stream_count") or 0),
        "per_stream_limit": int(
            streams.get("per_stream_limit") or streams.get("candidate_row_pool_limit_per_stream") or 0
        ),
        "stream_hit_limit_count": int(streams.get("stream_hit_limit_count") or 0),
        "saturated_stream_count": int(streams.get("saturated_stream_count") or 0),
        "saturated_stream_labels": [str(label) for label in streams.get("saturated_stream_labels") or []],
        "row_count_before_dedupe": int(streams.get("row_count_before_dedupe") or 0),
        "row_count_after_dedupe": int(streams.get("row_count_after_dedupe") or 0),
        "duplicate_row_count": int(streams.get("duplicate_row_count") or 0),
        "selected_rows_by_stream": selected_by_stream,
        "rows_dropped_by_stream_cap": dropped_by_stream,
        "rows_dropped_by_stream_cap_total": int(streams.get("rows_dropped_by_stream_cap_total") or 0),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
    }


def _fielded_bm25_branch_fanout_stats(value: Any) -> dict[str, Any]:
    fanout = value if isinstance(value, dict) else {}
    return {
        "branches_considered": int(fanout.get("branches_considered") or 0),
        "branches_emitted": int(fanout.get("branches_emitted") or 0),
        "branches_capped": int(fanout.get("branches_capped") or 0),
        "rows_by_branch": _safe_count_dict(fanout.get("rows_by_branch")),
        "capped_rows_by_branch": _safe_count_dict(fanout.get("capped_rows_by_branch")),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_bm25_source_grouping_stats(value: Any) -> dict[str, Any]:
    grouping = value if isinstance(value, dict) else {}
    collapse = grouping.get("adjacent_run_collapse") if isinstance(grouping.get("adjacent_run_collapse"), dict) else {}
    interleaving = grouping.get("group_interleaving") if isinstance(grouping.get("group_interleaving"), dict) else {}
    windows = (
        grouping.get("grouped_source_windows")
        if isinstance(grouping.get("grouped_source_windows"), dict)
        else {}
    )
    return {
        "enabled": bool(grouping.get("enabled", False)),
        "strategy": str(grouping.get("strategy") or "disabled"),
        "candidate_rows_considered": int(grouping.get("candidate_rows_considered") or 0),
        "candidate_rows_selected": int(grouping.get("candidate_rows_selected") or 0),
        "source_count_considered": int(grouping.get("source_count_considered") or 0),
        "source_count_selected": int(grouping.get("source_count_selected") or 0),
        "dominant_source_share_before": float(grouping.get("dominant_source_share_before") or 0.0),
        "dominant_source_share_after": float(grouping.get("dominant_source_share_after") or 0.0),
        "group_interleaved_rows_selected": int(grouping.get("group_interleaved_rows_selected") or 0),
        "rows_dropped_by_source_cap": int(grouping.get("rows_dropped_by_source_cap") or 0),
        "window_group_count_considered": int(grouping.get("window_group_count_considered") or 0),
        "window_group_count_selected": int(grouping.get("window_group_count_selected") or 0),
        "window_group_count_omitted": int(grouping.get("window_group_count_omitted") or 0),
        "adjacent_run_collapse": {
            "enabled": bool(collapse.get("enabled", False)),
            "collapsed_adjacent_run_count": int(collapse.get("collapsed_adjacent_run_count") or 0),
            "suppressed_adjacent_row_count": int(collapse.get("suppressed_adjacent_row_count") or 0),
            "max_adjacent_run_size": int(collapse.get("max_adjacent_run_size") or 0),
            "suppressed_rows_are_aggregate_counters_only": bool(
                collapse.get("suppressed_rows_are_aggregate_counters_only", True)
            ),
        },
        "group_interleaving": {
            "enabled": bool(interleaving.get("enabled", False)),
            "group_count_considered": int(interleaving.get("group_count_considered") or 0),
            "group_count_selected": int(interleaving.get("group_count_selected") or 0),
            "distinct_sources_considered": int(interleaving.get("distinct_sources_considered") or 0),
            "distinct_sessions_considered": int(interleaving.get("distinct_sessions_considered") or 0),
            "distinct_days_considered": int(interleaving.get("distinct_days_considered") or 0),
            "distinct_sources_selected": int(interleaving.get("distinct_sources_selected") or 0),
            "distinct_sessions_selected": int(interleaving.get("distinct_sessions_selected") or 0),
            "distinct_days_selected": int(interleaving.get("distinct_days_selected") or 0),
            "dominant_group_share_before": float(interleaving.get("dominant_group_share_before") or 0.0),
            "dominant_group_share_after": float(interleaving.get("dominant_group_share_after") or 0.0),
        },
        "grouped_source_windows": {
            "enabled": bool(windows.get("enabled", False)),
            "selection_version": str(windows.get("selection_version") or ""),
            "window_count_considered": int(windows.get("window_count_considered") or 0),
            "window_count_selected": int(windows.get("window_count_selected") or 0),
            "window_count_omitted": int(windows.get("window_count_omitted") or 0),
            "selected_window_head_count": int(windows.get("selected_window_head_count") or 0),
            "selected_inner_hit_count": int(windows.get("selected_inner_hit_count") or 0),
            "omitted_inner_hit_count": int(windows.get("omitted_inner_hit_count") or 0),
            "merged_adjacent_window_count": int(windows.get("merged_adjacent_window_count") or 0),
            "merged_adjacent_inner_hit_count": int(windows.get("merged_adjacent_inner_hit_count") or 0),
            "distinct_sources_selected": int(windows.get("distinct_sources_selected") or 0),
            "distinct_sessions_selected": int(windows.get("distinct_sessions_selected") or 0),
            "distinct_days_selected": int(windows.get("distinct_days_selected") or 0),
            "distinct_windows_selected": int(windows.get("distinct_windows_selected") or 0),
            "omission_reason_counts": _safe_count_dict(windows.get("omission_reason_counts")),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
            "source_lines_included": False,
            "line_numbers_included": False,
        },
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
    }


def _phase_profile_stats(value: Any, phase: str) -> dict[str, Any]:
    profile = value if isinstance(value, dict) else {}
    rss_available = bool(profile.get("rss_available", False))
    return {
        "phase": str(profile.get("phase") or phase),
        "elapsed_ms": float(profile.get("elapsed_ms") or 0.0),
        "rss_available": rss_available,
        "rss_units": "bytes",
        "rss_start_max_bytes": _optional_int(profile.get("rss_start_max_bytes")),
        "rss_end_max_bytes": _optional_int(profile.get("rss_end_max_bytes")),
        "rss_max_bytes": _optional_int(profile.get("rss_max_bytes")),
        "rss_delta_bytes": _optional_int(profile.get("rss_delta_bytes")),
        "rss_unavailable_reason": None if rss_available else str(profile.get("rss_unavailable_reason") or "not_profiled"),
        "rss_current_bytes": None,
        "rss_current_unavailable_reason": "resource_ru_maxrss_reports_process_peak_not_current",
        "candidate_count": _optional_int(profile.get("candidate_count")),
        "candidate_rows": _optional_int(profile.get("candidate_rows")),
        "candidate_rows_materialized": _optional_int(profile.get("candidate_rows_materialized")),
        "file_count": _optional_int(profile.get("file_count")),
        "byte_count": _optional_int(profile.get("byte_count")),
        "searched_files": _optional_int(profile.get("searched_files")),
        "searched_bytes": _optional_int(profile.get("searched_bytes")),
        "records_considered": _optional_int(profile.get("records_considered")),
        "indexed_file_count": _optional_int(profile.get("indexed_file_count")),
        "indexed_record_count": _optional_int(profile.get("indexed_record_count")),
        "indexed_chunk_count": _optional_int(profile.get("indexed_chunk_count")),
        "build_bytes": _optional_int(profile.get("build_bytes")),
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _rrf_phase_profiles(diagnostics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    shared = diagnostics.get("shared_source_enumeration") if isinstance(diagnostics.get("shared_source_enumeration"), dict) else {}
    package_lane = _rrf_diagnostic_lane(diagnostics, "package_search")
    fielded_lane = _rrf_diagnostic_lane(diagnostics, "package_search_fielded_bm25")
    fusion = diagnostics.get("fusion") if isinstance(diagnostics.get("fusion"), dict) else {}
    return {
        "total_explicit_rrf_retrieval": _phase_profile_stats(
            diagnostics.get("total_explicit_rrf_retrieval"),
            "total_explicit_rrf_retrieval",
        ),
        "shared_source_enumeration": _phase_profile_stats(shared.get("profile"), "shared_source_enumeration"),
        "package_lane": _phase_profile_stats(package_lane.get("profile"), "package_lane"),
        "fielded_bm25_index_build": _phase_profile_stats(
            fielded_lane.get("index_build_profile"),
            "fielded_bm25_index_build",
        ),
        "fielded_bm25_search_materialization": _phase_profile_stats(
            fielded_lane.get("profile"),
            "fielded_bm25_search_materialization",
        ),
        "rrf_fusion": _phase_profile_stats(fusion.get("profile"), "rrf_fusion"),
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shared_source_enumeration_row_stats(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    shared = diagnostics.get("shared_source_enumeration")
    if not isinstance(shared, dict):
        return None
    return {
        "status": "evaluated",
        "file_count": int(shared.get("file_count") or 0),
        "byte_count": int(shared.get("byte_count") or 0),
        "elapsed_ms": float(shared.get("elapsed_ms") or 0.0),
        "consumed_by_lanes": [str(item) for item in shared.get("consumed_by_lanes", [])],
        "raw_paths_included": bool(shared.get("raw_paths_included", False)),
    }


def _duplicate_local_work_row_stats(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    duplicate = diagnostics.get("duplicate_local_work")
    if not isinstance(duplicate, dict):
        return None
    return {
        "status": "evaluated",
        "enumeration_reused": bool(duplicate.get("enumeration_reused")),
        "avoided_second_enumeration": bool(duplicate.get("avoided_second_enumeration")),
        "duplicate_file_visit_count": int(duplicate.get("duplicate_file_visit_count") or 0),
        "duplicate_file_overlap_count": int(duplicate.get("duplicate_file_overlap_count") or 0),
        "duplicate_searched_bytes_lower_bound": int(duplicate.get("duplicate_searched_bytes_lower_bound") or 0),
        "local_work_only_not_model_tokens": bool(duplicate.get("local_work_only_not_model_tokens")),
        "raw_paths_included": bool(duplicate.get("raw_paths_included", False)),
    }


def _fused_expected_ref_diagnostics(case: dict[str, Any], fused_result: dict[str, Any]) -> dict[str, Any] | None:
    expected_refs = case.get("expected_refs") or []
    if not expected_refs:
        return None
    final_keys = _match_keys(fused_result.get("groups", []))
    refs = []
    for ref in expected_refs:
        ref_id = ref.get("ref_id")
        path = ref.get("path")
        line = ref.get("line")
        if not isinstance(ref_id, str) or path is None or not isinstance(line, int):
            continue
        refs.append(
            {
                "ref_id": ref_id,
                "expected_record_present_in_final_candidate_groups": (str(path), line) in final_keys,
                "final_status": "present_in_fused_final_candidates"
                if (str(path), line) in final_keys
                else "not_present_in_fused_final_candidates",
            }
        )
    return {
        "diagnostic_scope": "package_search_rrf_fusion_final_candidates",
        "refs": refs,
        "expected_ref_count": len(refs),
        "final_candidate_expected_ref_count": sum(
            1 for ref in refs if ref["expected_record_present_in_final_candidate_groups"]
        ),
        "expected_refs_used_for_retrieval": False,
        "raw_paths_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
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
                "expected_file_raw_lines_examined_before_stop": int(
                    ref.get("expected_file_raw_lines_examined_before_stop") or 0
                ),
                "expected_file_progress_bucket": str(ref.get("expected_file_progress_bucket") or "unknown"),
                "expected_file_raw_progress_bucket": str(ref.get("expected_file_raw_progress_bucket") or "unknown"),
                "expected_file_selected_by_source_plan": bool(ref.get("expected_file_selected_by_source_plan")),
                "expected_file_queued_for_scan": bool(ref.get("expected_file_queued_for_scan")),
                "expected_file_opened_before_scan_stop": bool(ref.get("expected_file_opened_before_scan_stop")),
                "expected_record_line_reached_before_elapsed_stop": bool(
                    ref.get("expected_record_line_reached_before_elapsed_stop")
                ),
                "expected_record_skipped_before_full_json_parse": bool(
                    ref.get("expected_record_skipped_before_full_json_parse")
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
                "candidate_strength_label": ref.get("candidate_strength_label"),
                "pre_parse_phase_label": str(ref.get("pre_parse_phase_label") or "unknown"),
                "should_consider_phase_label": str(ref.get("should_consider_phase_label") or "unknown"),
                "dedupe_phase_label": str(ref.get("dedupe_phase_label") or "unknown"),
                "reservoir_phase_label": str(ref.get("reservoir_phase_label") or "unknown"),
                "final_cap_phase_label": str(ref.get("final_cap_phase_label") or "unknown"),
                "drop_phase_label": ref.get("drop_phase_label"),
                "pre_parse_status": str(ref.get("pre_parse_status") or "unknown"),
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
                "candidate_file_rank_label": ref.get("candidate_file_rank_label"),
                "candidate_file_entry_count_bucket": ref.get("candidate_file_entry_count_bucket"),
                "candidate_entry_rank_label": ref.get("candidate_entry_rank_label"),
                "candidate_entry_rank_bucket": ref.get("candidate_entry_rank_bucket"),
                "candidate_entry_selection_reason": ref.get("candidate_entry_selection_reason"),
                "candidate_entry_score_profile": ref.get("candidate_entry_score_profile"),
                "candidate_entry_selection_limit": int(ref.get("candidate_entry_selection_limit") or 0),
                "candidate_file_selection_limit": int(ref.get("candidate_file_selection_limit") or 0),
                "candidate_file_truncation_label": str(ref.get("candidate_file_truncation_label") or "unknown"),
                "candidate_entry_truncation_label": str(ref.get("candidate_entry_truncation_label") or "unknown"),
                "score_label_count": ref.get("score_label_count"),
                "artifact_label_count": ref.get("artifact_label_count"),
                "pre_final_rank_label": ref.get("pre_final_rank_label"),
                "pre_final_rank_bucket": ref.get("pre_final_rank_bucket"),
                "final_rank_label": ref.get("final_rank_label"),
                "final_rank_bucket": ref.get("final_rank_bucket"),
                "expected_file_indexed_by_fielded_bm25": _optional_bool(
                    ref.get("expected_file_indexed_by_fielded_bm25")
                ),
                "expected_record_indexed_by_fielded_bm25": _optional_bool(
                    ref.get("expected_record_indexed_by_fielded_bm25")
                ),
                "expected_record_selected_as_bm25_candidate_row": _optional_bool(
                    ref.get("expected_record_selected_as_bm25_candidate_row")
                ),
                "fielded_candidate_status": (
                    str(ref.get("fielded_candidate_status"))
                    if ref.get("fielded_candidate_status") is not None
                    else None
                ),
                "bm25_candidate_rank_label": ref.get("bm25_candidate_rank_label"),
                "bm25_candidate_rank_bucket": ref.get("bm25_candidate_rank_bucket"),
                "expected_ref_reach_stage": str(ref.get("expected_ref_reach_stage") or "not_reached"),
                "first_lost_stage": str(ref.get("first_lost_stage") or "not_reached"),
                "pre_grouping_candidate_presence": bool(ref.get("pre_grouping_candidate_presence")),
                "pre_grouping_candidate_count": int(ref.get("pre_grouping_candidate_count") or 0),
                "selected_candidate_proof": _safe_reach_proof(ref.get("selected_candidate_proof")),
                "omitted_candidate_proof": _safe_reach_proof(ref.get("omitted_candidate_proof")),
                "stage_presence_counts": _safe_count_dict(ref.get("stage_presence_counts")),
                "dropped_by_stage_counts": _safe_count_dict(ref.get("dropped_by_stage_counts")),
                "cap_lower_bound_caveats": [
                    str(item) for item in ref.get("cap_lower_bound_caveats") or [] if _safe_counter_label(str(item))
                ],
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
        "fielded_bm25_enabled": bool(diagnostics.get("fielded_bm25_enabled")),
        "fielded_bm25_indexed_expected_record_count": int(
            diagnostics.get("fielded_bm25_indexed_expected_record_count") or 0
        ),
        "fielded_bm25_candidate_expected_ref_count": int(
            diagnostics.get("fielded_bm25_candidate_expected_ref_count") or 0
        ),
        "expected_ref_reach_stage_counts": _safe_count_dict(diagnostics.get("expected_ref_reach_stage_counts")),
        "pre_grouping_candidate_presence_count": int(
            diagnostics.get("pre_grouping_candidate_presence_count") or 0
        ),
        "refs": refs,
    }


def _safe_reach_proof(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    proof: dict[str, Any] = {}
    for key, item in sorted(value.items()):
        safe_key = str(key)
        if not _safe_counter_label(safe_key):
            continue
        if isinstance(item, bool):
            proof[safe_key] = item
        elif isinstance(item, int):
            proof[safe_key] = item
        elif isinstance(item, str):
            proof[safe_key] = _safe_agent_compact_label(item)
        elif isinstance(item, list):
            proof[safe_key] = [
                _safe_agent_compact_label(str(entry))
                for entry in item
                if _safe_counter_label(str(entry))
            ]
    return proof


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


def _package_records_parsed_from_result(
    result: dict[str, Any],
    *,
    files: list[RealHistoryFile],
    searched_bytes: int,
    prefilter: dict[str, Any],
    use_rg_prefilter: bool,
) -> int | None:
    if "full_json_records_parsed" in result:
        return int(result.get("full_json_records_parsed") or 0)
    return _estimated_package_records_parsed(files, searched_bytes, prefilter, use_rg_prefilter)


def _prefilter_row_stats(result: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    stats = result.get("prefilter") if enabled else None
    if not isinstance(stats, dict):
        return {
            "enabled": bool(enabled),
            "available": None,
            "attempted": False,
            "eligible": False,
            "status": "disabled" if not enabled else "not_attempted",
            "fallback_reason": None,
            "needle_policy": None,
            "needle_count": 0,
            "anchor_kind_counts": {},
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
        "eligible": bool(stats.get("eligible")),
        "status": str(stats.get("status") or "unknown"),
        "fallback_reason": stats.get("fallback_reason"),
        "needle_policy": stats.get("needle_policy"),
        "needle_count": int(stats.get("needle_count", 0)),
        "anchor_kind_counts": _safe_count_dict(stats.get("anchor_kind_counts")),
        "candidate_files": int(stats.get("candidate_files", 0)),
        "candidate_bytes": int(stats.get("candidate_bytes", 0)),
        "matched_files": int(stats.get("matched_files", 0)),
        "matched_bytes": int(stats.get("matched_bytes", 0)),
        "files_reduced": int(stats.get("files_reduced", 0)),
        "bytes_reduced": int(stats.get("bytes_reduced", 0)),
        "files_reduction_ratio": float(stats.get("files_reduction_ratio", 0.0)),
        "bytes_reduction_ratio": float(stats.get("bytes_reduction_ratio", 0.0)),
    }


def _pre_parse_anchor_kind_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for kind, count in ((row.get("pre_parse") or {}).get("anchor_kind_counts") or {}).items():
            counts[str(kind)] = counts.get(str(kind), 0) + int(count or 0)
    return dict(sorted(counts.items()))


def _pre_parse_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if isinstance(row.get("pre_parse"), dict)]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "active_runs": 0,
            "raw_lines_examined": 0,
            "raw_lines_skipped_before_full_json_parse": 0,
            "full_json_records_parsed": 0,
            "anchor_kind_counts": {},
            "privacy": {"raw_anchor_needles_included": False},
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        "active_runs": sum(1 for row in selected if (row.get("pre_parse") or {}).get("status") == "active"),
        "status_counts": _counts((row.get("pre_parse") or {}).get("status") for row in selected),
        "raw_lines_examined": sum(int((row.get("pre_parse") or {}).get("raw_lines_examined") or 0) for row in selected),
        "raw_lines_skipped_before_full_json_parse": sum(
            int((row.get("pre_parse") or {}).get("raw_lines_skipped_before_full_json_parse") or 0)
            for row in selected
        ),
        "full_json_records_parsed": sum(
            int((row.get("pre_parse") or {}).get("full_json_records_parsed") or 0) for row in selected
        ),
        "anchor_kind_counts": _pre_parse_anchor_kind_counts(selected),
        "privacy": {"raw_anchor_needles_included": False},
    }


def _candidate_strength_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        (row.get("scan_order") or {}).get("candidate_strength")
        for row in rows
        if isinstance((row.get("scan_order") or {}).get("candidate_strength"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "strategy_counts": {},
            "strong_candidates": 0,
            "weak_candidates": 0,
            "weak_partial_generic_candidates": 0,
            "weak_broad_unanchored_low_evidence_candidates": 0,
            "strong_reservoir_retained": 0,
            "strong_reservoir_replaced": 0,
            "strong_reservoir_cap_dropped": 0,
            "strong_high_evidence_candidates": 0,
            "strong_high_evidence_reservoir_retained": 0,
            "strong_high_evidence_reservoir_cap_dropped": 0,
            "weak_reservoir_retained": 0,
            "weak_reservoir_replaced": 0,
            "weak_reservoir_cap_dropped": 0,
            "privacy": _candidate_strength_privacy(),
        }
    counter_fields = (
        "strong_candidates",
        "weak_candidates",
        "weak_partial_generic_candidates",
        "weak_broad_unanchored_low_evidence_candidates",
        "strong_reservoir_retained",
        "strong_reservoir_replaced",
        "strong_reservoir_cap_dropped",
        "strong_high_evidence_candidates",
        "strong_high_evidence_reservoir_retained",
        "strong_high_evidence_reservoir_cap_dropped",
        "weak_reservoir_retained",
        "weak_reservoir_replaced",
        "weak_reservoir_cap_dropped",
    )
    return {
        "status": "evaluated",
        "runs": len(selected),
        "strategy_counts": _counts(item.get("strategy") for item in selected),
        "median_strong_reservoir_limit": _median(item.get("strong_reservoir_limit") for item in selected),
        "median_weak_reservoir_limit": _median(item.get("weak_reservoir_limit") for item in selected),
        **{field: sum(int(item.get(field) or 0) for item in selected) for field in counter_fields},
        "privacy": _candidate_strength_privacy(),
    }


def _fielded_bm25_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row.get("fielded_bm25") for row in rows if isinstance(row.get("fielded_bm25"), dict)]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "fts_available_runs": 0,
            "candidate_rows_generated": 0,
            "candidate_rows_retained": 0,
            "candidate_rows_surfaced": 0,
            "bm25_rank_buckets": {},
            "query_streams": _fielded_bm25_query_stream_aggregate([]),
            "branch_fanout": _fielded_bm25_branch_fanout_aggregate([]),
            "continuation_candidate_ledger": _fielded_bm25_continuation_ledger_aggregate([]),
            "expected_ref_reach": _expected_ref_reach_aggregate([]),
            "source_grouping": _fielded_bm25_source_grouping_aggregate([]),
            "materialization": _fielded_bm25_materialization_aggregate([]),
            "phase_profiles": _phase_profiles_aggregate([]),
            "privacy": _fielded_bm25_privacy(),
        }
    rank_buckets: dict[str, int] = {}
    for item in selected:
        for bucket, count in (item.get("bm25_rank_buckets") or {}).items():
            rank_buckets[str(bucket)] = rank_buckets.get(str(bucket), 0) + int(count or 0)
    return {
        "status": "evaluated",
        "runs": len(selected),
        "status_counts": _counts(item.get("status") for item in selected),
        "fts_available_runs": sum(1 for item in selected if item.get("fts_available")),
        "median_indexed_file_count": _median(item.get("indexed_file_count") for item in selected),
        "median_indexed_record_count": _median(item.get("indexed_record_count") for item in selected),
        "median_indexed_chunk_count": _median(item.get("indexed_chunk_count") for item in selected),
        "candidate_rows_generated": sum(int(item.get("candidate_rows_generated") or 0) for item in selected),
        "candidate_rows_retained": sum(int(item.get("candidate_rows_retained") or 0) for item in selected),
        "candidate_rows_surfaced": sum(int(item.get("candidate_rows_surfaced") or 0) for item in selected),
        "records_considered": sum(int(item.get("records_considered") or 0) for item in selected),
        "candidate_records_scored": sum(int(item.get("candidate_records_scored") or 0) for item in selected),
        "candidate_records_deduped": sum(int(item.get("candidate_records_deduped") or 0) for item in selected),
        "bm25_rank_buckets": dict(sorted(rank_buckets.items())),
        "query_streams": _fielded_bm25_query_stream_aggregate(selected),
        "branch_fanout": _fielded_bm25_branch_fanout_aggregate(selected),
        "continuation_candidate_ledger": _fielded_bm25_continuation_ledger_aggregate(selected),
        "expected_ref_reach": _expected_ref_reach_aggregate(rows),
        "source_grouping": _fielded_bm25_source_grouping_aggregate(selected),
        "materialization": _fielded_bm25_materialization_aggregate(selected),
        "phase_profiles": _fielded_bm25_phase_profiles_aggregate(selected),
        "privacy": _fielded_bm25_privacy(),
    }


def _fielded_bm25_query_stream_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("query_streams")
        for row in rows
        if isinstance(row.get("query_streams"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "stream_hit_limit_runs": 0,
            "saturated_stream_total": 0,
            "row_count_before_dedupe": 0,
            "row_count_after_dedupe": 0,
            "duplicate_row_count": 0,
            "selected_rows_by_stream": {},
            "rows_dropped_by_stream_cap": {},
            "rows_dropped_by_stream_cap_total": 0,
            "privacy": _fielded_bm25_privacy(),
        }
    selected_by_stream: dict[str, int] = {}
    dropped_by_stream: dict[str, int] = {}
    saturated_labels: dict[str, int] = {}
    for item in selected:
        for label, count in (item.get("selected_rows_by_stream") or {}).items():
            selected_by_stream[str(label)] = selected_by_stream.get(str(label), 0) + int(count or 0)
        for label, count in (item.get("rows_dropped_by_stream_cap") or {}).items():
            dropped_by_stream[str(label)] = dropped_by_stream.get(str(label), 0) + int(count or 0)
        for label in item.get("saturated_stream_labels") or []:
            saturated_labels[str(label)] = saturated_labels.get(str(label), 0) + 1
    return {
        "status": "evaluated",
        "runs": len(selected),
        "stream_hit_limit_runs": sum(1 for item in selected if int(item.get("stream_hit_limit_count") or 0) > 0),
        "saturated_stream_total": sum(int(item.get("saturated_stream_count") or 0) for item in selected),
        "saturated_stream_label_counts": dict(sorted(saturated_labels.items())),
        "row_count_before_dedupe": sum(int(item.get("row_count_before_dedupe") or 0) for item in selected),
        "row_count_after_dedupe": sum(int(item.get("row_count_after_dedupe") or 0) for item in selected),
        "duplicate_row_count": sum(int(item.get("duplicate_row_count") or 0) for item in selected),
        "selected_rows_by_stream": dict(sorted(selected_by_stream.items())),
        "rows_dropped_by_stream_cap": dict(sorted(dropped_by_stream.items())),
        "rows_dropped_by_stream_cap_total": sum(
            int(item.get("rows_dropped_by_stream_cap_total") or 0) for item in selected
        ),
        "privacy": _fielded_bm25_privacy(),
    }


def _fielded_bm25_branch_fanout_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("branch_fanout")
        for row in rows
        if isinstance(row.get("branch_fanout"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "branches_considered": 0,
            "branches_emitted": 0,
            "branches_capped": 0,
            "rows_by_branch": {},
            "capped_rows_by_branch": {},
            "privacy": _fielded_bm25_privacy(),
        }
    rows_by_branch: dict[str, int] = {}
    capped_rows_by_branch: dict[str, int] = {}
    for item in selected:
        for label, count in (item.get("rows_by_branch") or {}).items():
            rows_by_branch[str(label)] = rows_by_branch.get(str(label), 0) + int(count or 0)
        for label, count in (item.get("capped_rows_by_branch") or {}).items():
            capped_rows_by_branch[str(label)] = capped_rows_by_branch.get(str(label), 0) + int(count or 0)
    return {
        "status": "evaluated",
        "runs": len(selected),
        "branches_considered": sum(int(item.get("branches_considered") or 0) for item in selected),
        "branches_emitted": sum(int(item.get("branches_emitted") or 0) for item in selected),
        "branches_capped": sum(int(item.get("branches_capped") or 0) for item in selected),
        "rows_by_branch": dict(sorted(rows_by_branch.items())),
        "capped_rows_by_branch": dict(sorted(capped_rows_by_branch.items())),
        "privacy": _fielded_bm25_privacy(),
    }


def _fielded_bm25_continuation_ledger_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("continuation_candidate_ledger")
        for row in rows
        if isinstance(row.get("continuation_candidate_ledger"), dict)
    ]
    enabled = [item for item in selected if item.get("enabled") is True]
    if not enabled:
        return {
            "status": "not_evaluated",
            "basis": "continuation_ledger_flag_not_enabled_or_fielded_bm25_rows_not_run",
            "runs": 0,
            "fielded_bm25_rows": len(selected),
            "enabled_rows": 0,
            "default_off_rows": len([item for item in selected if item.get("enabled") is False]),
            "continuation_available_rows": 0,
            "classification_counts": {
                "present_final": 0,
                "recoverable_omitted": 0,
                "still_unreached": 0,
            },
            "stage_counts": {},
            "loss_stage_summary": {},
            "privacy_status_counts": {},
            "privacy": _fielded_bm25_continuation_ledger_privacy(),
            "claim_boundaries": _fielded_bm25_continuation_ledger_claim_boundaries(),
        }
    classification_counts = _sum_count_dicts(item.get("classification_counts") for item in enabled)
    stage_counts = _sum_count_dicts(item.get("stage_counts") for item in enabled)
    loss_stage_summary = _sum_count_dicts(item.get("loss_stage_summary") for item in enabled)
    raw_payload_violation_rows = sum(
        1
        for item in enabled
        if any(
            bool(item.get(flag))
            for flag in (
                "raw_paths_included",
                "raw_text_included",
                "raw_snippets_included",
                "source_lines_included",
                "line_numbers_included",
                "raw_session_ids_included",
                "vectors_or_persisted_indexes_included",
            )
        )
    )
    return {
        "status": "evaluated",
        "basis": "opt_in_package_search_fielded_bm25_continuation_ledger_rows",
        "runs": len(enabled),
        "fielded_bm25_rows": len(selected),
        "enabled_rows": len(enabled),
        "default_off_rows": len([item for item in selected if item.get("enabled") is False]),
        "status_counts": _counts(item.get("status") for item in selected),
        "mode_counts": _counts(item.get("mode") for item in enabled),
        "version_counts": _counts(item.get("version") for item in enabled),
        "bounded_rows": sum(1 for item in enabled if item.get("bounded") is True),
        "multi_pass_rows": sum(1 for item in enabled if item.get("multi_pass") is True),
        "continuation_available_rows": sum(1 for item in enabled if item.get("continuation_available") is True),
        "total_candidate_rows_discovered": sum(
            int(item.get("total_candidate_rows_discovered") or 0) for item in enabled
        ),
        "ledger_item_count": sum(int(item.get("ledger_item_count") or 0) for item in enabled),
        "candidate_rows_retained_for_materialization": sum(
            int(item.get("candidate_rows_retained_for_materialization") or 0) for item in enabled
        ),
        "candidate_rows_materialized": sum(int(item.get("candidate_rows_materialized") or 0) for item in enabled),
        "final_packet_candidate_count": sum(int(item.get("final_packet_candidate_count") or 0) for item in enabled),
        "final_packet_present_count": sum(int(item.get("final_packet_present_count") or 0) for item in enabled),
        "recoverable_omitted_count": sum(int(item.get("recoverable_omitted_count") or 0) for item in enabled),
        "still_unreached_count": sum(int(item.get("still_unreached_count") or 0) for item in enabled),
        "classification_counts": classification_counts,
        "stage_counts": stage_counts,
        "loss_stage_summary": loss_stage_summary,
        "median_ledger_item_limit": _median(item.get("ledger_item_limit") for item in enabled),
        "median_ledger_item_count": _median(item.get("ledger_item_count") for item in enabled),
        "median_candidate_row_pool_limit": _median(item.get("candidate_row_pool_limit") for item in enabled),
        "median_pass_count": _median(item.get("pass_count") for item in enabled),
        "median_retrieval_chunk_count": _median(item.get("retrieval_chunk_count") for item in enabled),
        "policy": {
            "default_enabled_rows": sum(
                1 for item in enabled if ((item.get("policy") or {}).get("default_enabled") is True)
            ),
            "cursor_type_counts": _counts((item.get("policy") or {}).get("cursor_type") for item in enabled),
            "resume_semantics_counts": _counts(
                (item.get("policy") or {}).get("resume_semantics") for item in enabled
            ),
        },
        "privacy_status_counts": _counts(item.get("privacy_status") for item in enabled),
        "aggregate_privacy_status": (
            "aggregate_safe" if raw_payload_violation_rows == 0 else "privacy_boundary_violation_detected"
        ),
        "raw_payload_violation_rows": raw_payload_violation_rows,
        "privacy": _fielded_bm25_continuation_ledger_privacy(),
        "claim_boundaries": _fielded_bm25_continuation_ledger_claim_boundaries(),
    }


def _expected_ref_reach_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    stage_counts: dict[str, int] = {}
    first_lost_counts: dict[str, int] = {}
    dropped_by_stage_counts: dict[str, int] = {}
    stage_presence_counts: dict[str, int] = {}
    caveat_counts: dict[str, int] = {}
    for row in rows:
        diagnostics = row.get("expected_ref_diagnostics")
        if not isinstance(diagnostics, dict):
            continue
        for ref in diagnostics.get("refs") or []:
            if not isinstance(ref, dict):
                continue
            label = f"expected_ref_{len(refs) + 1:03d}"
            stage = _safe_agent_compact_label(ref.get("expected_ref_reach_stage") or "not_reached")
            first_lost = _safe_agent_compact_label(ref.get("first_lost_stage") or "not_reached")
            dropped = _safe_count_dict(ref.get("dropped_by_stage_counts"))
            presence = _safe_count_dict(ref.get("stage_presence_counts"))
            caveats = [
                _safe_agent_compact_label(str(item))
                for item in ref.get("cap_lower_bound_caveats") or []
                if _safe_counter_label(str(item))
            ]
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
            first_lost_counts[first_lost] = first_lost_counts.get(first_lost, 0) + 1
            for dropped_stage, count in dropped.items():
                dropped_by_stage_counts[dropped_stage] = dropped_by_stage_counts.get(dropped_stage, 0) + int(count)
            for presence_stage, count in presence.items():
                stage_presence_counts[presence_stage] = stage_presence_counts.get(presence_stage, 0) + int(count)
            for caveat in caveats:
                caveat_counts[caveat] = caveat_counts.get(caveat, 0) + 1
            refs.append(
                {
                    "expected_ref_label": label,
                    "expected_ref_reach_stage": stage,
                    "first_lost_stage": first_lost,
                    "pre_grouping_candidate_presence": bool(ref.get("pre_grouping_candidate_presence")),
                    "pre_grouping_candidate_count": int(ref.get("pre_grouping_candidate_count") or 0),
                    "selected_candidate_proof": _safe_reach_proof(ref.get("selected_candidate_proof")),
                    "omitted_candidate_proof": _safe_reach_proof(ref.get("omitted_candidate_proof")),
                    "stage_presence_counts": presence,
                    "dropped_by_stage_counts": dropped,
                    "cap_lower_bound_caveats": caveats,
                }
            )
    if not refs:
        return {
            "status": "not_evaluated",
            "expected_ref_count": 0,
            "refs": [],
            "expected_ref_reach_stage_counts": {},
            "first_lost_stage_counts": {},
            "pre_grouping_candidate_presence_count": 0,
            "dropped_by_stage_counts": {},
            "stage_presence_counts": {},
            "cap_lower_bound_caveats": {},
            "privacy": _expected_ref_reach_privacy(),
        }
    return {
        "status": "evaluated",
        "expected_ref_count": len(refs),
        "refs": refs,
        "expected_ref_reach_stage_counts": dict(sorted(stage_counts.items())),
        "first_lost_stage_counts": dict(sorted(first_lost_counts.items())),
        "pre_grouping_candidate_presence_count": sum(
            1 for ref in refs if ref.get("pre_grouping_candidate_presence")
        ),
        "dropped_by_stage_counts": dict(sorted(dropped_by_stage_counts.items())),
        "stage_presence_counts": dict(sorted(stage_presence_counts.items())),
        "cap_lower_bound_caveats": dict(sorted(caveat_counts.items())),
        "distinguishes_upstream_from_downstream_loss": True,
        "privacy": _expected_ref_reach_privacy(),
    }


def _expected_ref_reach_privacy() -> dict[str, bool]:
    return {
        **_fielded_bm25_privacy(),
        "aggregate_safe_expected_ref_labels_only": True,
        "case_ids_included": False,
        "raw_expected_ref_ids_included": False,
        "line_numbers_included": False,
        "source_lines_included": False,
    }


def _fielded_bm25_source_grouping_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("source_grouping")
        for row in rows
        if isinstance(row.get("source_grouping"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "suppressed_adjacent_row_count": 0,
            "collapsed_adjacent_run_count": 0,
            "group_interleaved_rows_selected": 0,
            "window_count_considered_total": 0,
            "window_count_selected_total": 0,
            "window_count_omitted_total": 0,
            "selected_inner_hit_count_total": 0,
            "omitted_inner_hit_count_total": 0,
            "merged_adjacent_window_count_total": 0,
            "distinct_windows_selected_total": 0,
            "privacy": _fielded_bm25_privacy(),
        }
    collapse_rows = [
        item.get("adjacent_run_collapse")
        for item in selected
        if isinstance(item.get("adjacent_run_collapse"), dict)
    ]
    interleaving_rows = [
        item.get("group_interleaving")
        for item in selected
        if isinstance(item.get("group_interleaving"), dict)
    ]
    window_rows = [
        item.get("grouped_source_windows")
        for item in selected
        if isinstance(item.get("grouped_source_windows"), dict)
    ]
    return {
        "status": "evaluated",
        "runs": len(selected),
        "candidate_rows_considered": sum(int(item.get("candidate_rows_considered") or 0) for item in selected),
        "candidate_rows_selected": sum(int(item.get("candidate_rows_selected") or 0) for item in selected),
        "source_count_considered_total": sum(int(item.get("source_count_considered") or 0) for item in selected),
        "source_count_selected_total": sum(int(item.get("source_count_selected") or 0) for item in selected),
        "rows_dropped_by_source_cap": sum(int(item.get("rows_dropped_by_source_cap") or 0) for item in selected),
        "group_interleaved_rows_selected": sum(
            int(item.get("group_interleaved_rows_selected") or 0) for item in selected
        ),
        "collapsed_adjacent_run_count": sum(
            int(item.get("collapsed_adjacent_run_count") or 0) for item in collapse_rows
        ),
        "suppressed_adjacent_row_count": sum(
            int(item.get("suppressed_adjacent_row_count") or 0) for item in collapse_rows
        ),
        "max_adjacent_run_size": max((int(item.get("max_adjacent_run_size") or 0) for item in collapse_rows), default=0),
        "group_count_considered_total": sum(
            int(item.get("group_count_considered") or 0) for item in interleaving_rows
        ),
        "group_count_selected_total": sum(
            int(item.get("group_count_selected") or 0) for item in interleaving_rows
        ),
        "distinct_sources_selected_total": sum(
            int(item.get("distinct_sources_selected") or 0) for item in interleaving_rows
        ),
        "distinct_sessions_selected_total": sum(
            int(item.get("distinct_sessions_selected") or 0) for item in interleaving_rows
        ),
        "distinct_days_selected_total": sum(
            int(item.get("distinct_days_selected") or 0) for item in interleaving_rows
        ),
        "window_group_count_considered_total": sum(
            int(item.get("window_group_count_considered") or 0) for item in selected
        ),
        "window_group_count_selected_total": sum(
            int(item.get("window_group_count_selected") or 0) for item in selected
        ),
        "window_group_count_omitted_total": sum(
            int(item.get("window_group_count_omitted") or 0) for item in selected
        ),
        "window_count_considered_total": sum(
            int(item.get("window_count_considered") or 0) for item in window_rows
        ),
        "window_count_selected_total": sum(
            int(item.get("window_count_selected") or 0) for item in window_rows
        ),
        "window_count_omitted_total": sum(
            int(item.get("window_count_omitted") or 0) for item in window_rows
        ),
        "selected_window_head_count_total": sum(
            int(item.get("selected_window_head_count") or 0) for item in window_rows
        ),
        "selected_inner_hit_count_total": sum(
            int(item.get("selected_inner_hit_count") or 0) for item in window_rows
        ),
        "omitted_inner_hit_count_total": sum(
            int(item.get("omitted_inner_hit_count") or 0) for item in window_rows
        ),
        "merged_adjacent_window_count_total": sum(
            int(item.get("merged_adjacent_window_count") or 0) for item in window_rows
        ),
        "merged_adjacent_inner_hit_count_total": sum(
            int(item.get("merged_adjacent_inner_hit_count") or 0) for item in window_rows
        ),
        "distinct_windows_selected_total": sum(
            int(item.get("distinct_windows_selected") or 0) for item in window_rows
        ),
        "privacy": _fielded_bm25_privacy(),
    }


def _fielded_bm25_materialization_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("materialization")
        for row in rows
        if isinstance(row.get("materialization"), dict)
    ]
    counter_fields = (
        "candidate_rows_materialized",
        "candidate_rows_materialized_from_in_memory_raw",
        "candidate_rows_materialized_from_cached_record",
        "candidate_rows_materialized_from_file_reopen",
        "candidate_rows_materialization_failed",
        "records_materialized_from_in_memory_raw",
        "records_materialized_from_cached_record",
        "records_materialized_from_file_reopen",
        "file_reopen_attempts",
        "file_reopen_failures",
    )
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            **{field: 0 for field in counter_fields},
            "in_memory_raw_materialization_available_runs": 0,
            "raw_text_included": False,
            "raw_paths_included": False,
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        **{field: sum(int(item.get(field) or 0) for item in selected) for field in counter_fields},
        "in_memory_raw_materialization_available_runs": sum(
            1 for item in selected if item.get("in_memory_raw_materialization_available")
        ),
        "raw_text_included": any(bool(item.get("raw_text_included")) for item in selected),
        "raw_paths_included": any(bool(item.get("raw_paths_included")) for item in selected),
    }


def _fielded_bm25_phase_profiles_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "fielded_bm25_index_build": _phase_profiles_aggregate(
            [row.get("index_build_profile") for row in rows if isinstance(row.get("index_build_profile"), dict)]
        ),
        "fielded_bm25_search_materialization": _phase_profiles_aggregate(
            [row.get("phase_profile") for row in rows if isinstance(row.get("phase_profile"), dict)]
        ),
    }


def _rrf_phase_profiles_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    phase_names = (
        "total_explicit_rrf_retrieval",
        "shared_source_enumeration",
        "package_lane",
        "fielded_bm25_index_build",
        "fielded_bm25_search_materialization",
        "rrf_fusion",
    )
    return {
        phase: _phase_profiles_aggregate(
            [
                item.get("phase_profiles", {}).get(phase)
                for item in rows
                if isinstance(item.get("phase_profiles"), dict)
                and isinstance(item.get("phase_profiles", {}).get(phase), dict)
            ]
        )
        for phase in phase_names
    }


def _phase_profiles_aggregate(profiles: list[Any]) -> dict[str, Any]:
    selected = [profile for profile in profiles if isinstance(profile, dict)]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "rss_available_runs": 0,
            "rss_unavailable_reason_counts": {},
            "median_elapsed_ms": 0.0,
            "max_rss_max_bytes": None,
            "median_rss_delta_bytes": None,
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "source_lines_included": False,
            "line_numbers_included": False,
            "session_ids_included": False,
            "vectors_or_persisted_indexes_included": False,
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        "phase_counts": _counts(item.get("phase") for item in selected),
        "rss_available_runs": sum(1 for item in selected if item.get("rss_available")),
        "rss_unavailable_reason_counts": _counts(
            item.get("rss_unavailable_reason") for item in selected if not item.get("rss_available")
        ),
        "median_elapsed_ms": _median(item.get("elapsed_ms") for item in selected),
        "max_rss_max_bytes": _max_optional(item.get("rss_max_bytes") for item in selected),
        "median_rss_delta_bytes": _median(item.get("rss_delta_bytes") for item in selected if item.get("rss_delta_bytes") is not None),
        "raw_paths_included": any(bool(item.get("raw_paths_included")) for item in selected),
        "raw_text_included": any(bool(item.get("raw_text_included")) for item in selected),
        "raw_query_text_included": any(bool(item.get("raw_query_text_included")) for item in selected),
        "source_lines_included": any(bool(item.get("source_lines_included")) for item in selected),
        "line_numbers_included": any(bool(item.get("line_numbers_included")) for item in selected),
        "session_ids_included": any(bool(item.get("session_ids_included")) for item in selected),
        "vectors_or_persisted_indexes_included": any(
            bool(item.get("vectors_or_persisted_indexes_included")) for item in selected
        ),
    }


def _max_optional(values: Any) -> int | None:
    selected = [int(value) for value in values if value is not None]
    return max(selected) if selected else None


def _rrf_fusion_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row.get("rrf_fusion") for row in rows if isinstance(row.get("rrf_fusion"), dict)]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "rank_constant": DEFAULT_RRF_RANK_CONSTANT,
            "overlap_count": 0,
            "package_only_count": 0,
            "fielded_only_count": 0,
            "top_n_fielded_only_promotion_count": 0,
            "privacy": _rrf_fusion_privacy(),
            "phase_profiles": _phase_profiles_aggregate([]),
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        "feature_state_counts": _counts(item.get("feature_state") for item in selected),
        "status_counts": _counts(item.get("status") for item in selected),
        "rank_constant": DEFAULT_RRF_RANK_CONSTANT,
        "equal_lane_weights_runs": sum(1 for item in selected if item.get("equal_lane_weights")),
        "median_candidate_window_size": _median(item.get("candidate_window_size") for item in selected),
        "median_overlap_count": _median(item.get("overlap_count") for item in selected),
        "median_jaccard": _median(item.get("jaccard") for item in selected),
        "package_only_count": sum(int(item.get("package_only_count") or 0) for item in selected),
        "fielded_only_count": sum(int(item.get("fielded_only_count") or 0) for item in selected),
        "top_n_fielded_only_promotion_count": sum(
            int(item.get("top_n_fielded_only_promotion_count") or 0) for item in selected
        ),
        "raw_score_inputs_used_for_fusion_runs": sum(
            1 for item in selected if item.get("raw_score_inputs_used_for_fusion")
        ),
        "score_normalization_used_runs": sum(1 for item in selected if item.get("score_normalization_used")),
        "fused_score_threshold_used_runs": sum(1 for item in selected if item.get("fused_score_threshold_used")),
        "benchmark_tuned_weights_used_runs": sum(1 for item in selected if item.get("benchmark_tuned_weights_used")),
        "shared_retrieval_mode_used_runs": sum(1 for item in selected if item.get("shared_retrieval_mode_used")),
        "shared_source_enumeration_used_runs": sum(1 for item in selected if item.get("shared_source_enumeration_used")),
        "duplicate_local_work_diagnostics_present_runs": sum(
            1 for item in selected if item.get("duplicate_local_work_diagnostics_present")
        ),
        "median_package_latency_ms": _median(item.get("package_latency_ms") for item in selected),
        "median_fielded_latency_ms": _median(item.get("fielded_latency_ms") for item in selected),
        "median_fusion_latency_ms": _median(item.get("fusion_latency_ms") for item in selected),
        "phase_profiles": _rrf_phase_profiles_aggregate(selected),
        "privacy": _rrf_fusion_privacy(),
    }


def _shared_source_enumeration_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("shared_source_enumeration")
        for row in rows
        if isinstance(row.get("shared_source_enumeration"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "raw_paths_included": False,
            "privacy": _shared_source_enumeration_privacy(),
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        "median_file_count": _median(item.get("file_count") for item in selected),
        "median_byte_count": _median(item.get("byte_count") for item in selected),
        "median_elapsed_ms": _median(item.get("elapsed_ms") for item in selected),
        "lane_consumer_counts": _counts(
            lane for item in selected for lane in (item.get("consumed_by_lanes") or [])
        ),
        "raw_paths_included": any(bool(item.get("raw_paths_included")) for item in selected),
        "privacy": _shared_source_enumeration_privacy(),
    }


def _duplicate_local_work_aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row.get("duplicate_local_work")
        for row in rows
        if isinstance(row.get("duplicate_local_work"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "runs": 0,
            "raw_paths_included": False,
            "privacy": _duplicate_local_work_privacy(),
        }
    return {
        "status": "evaluated",
        "runs": len(selected),
        "enumeration_reused_runs": sum(1 for item in selected if item.get("enumeration_reused")),
        "avoided_second_enumeration_runs": sum(1 for item in selected if item.get("avoided_second_enumeration")),
        "duplicate_file_visit_count": sum(int(item.get("duplicate_file_visit_count") or 0) for item in selected),
        "duplicate_file_overlap_count": sum(int(item.get("duplicate_file_overlap_count") or 0) for item in selected),
        "duplicate_searched_bytes_lower_bound": sum(
            int(item.get("duplicate_searched_bytes_lower_bound") or 0) for item in selected
        ),
        "local_work_only_not_model_tokens": all(
            bool(item.get("local_work_only_not_model_tokens")) for item in selected
        ),
        "raw_paths_included": any(bool(item.get("raw_paths_included")) for item in selected),
        "privacy": _duplicate_local_work_privacy(),
    }


def _fielded_bm25_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }


def _fielded_bm25_continuation_ledger_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "ledger_items_included": False,
        "cursors_included": False,
        "source_ref_hashes_included": False,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "raw_query_terms_included": False,
        "raw_paths_included": False,
        "raw_snippets_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "raw_session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
        "persisted_index_payloads_included": False,
    }


def _fielded_bm25_continuation_ledger_claim_boundaries() -> dict[str, bool]:
    return {
        "final_answer_quality_claim_supported": False,
        "token_savings_claim_supported": False,
        "speed_claim_supported": False,
        "default_promotion_supported": False,
        "all_history_completeness_claim_supported": False,
    }


def _rrf_fusion_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "raw_score_inputs_used_for_fusion": False,
        "score_normalization_used": False,
        "fused_score_threshold_used": False,
        "benchmark_tuned_weights_used": False,
    }


def _shared_source_enumeration_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_paths_included": False,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "session_ids_included": False,
    }


def _duplicate_local_work_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_paths_included": False,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "session_ids_included": False,
        "local_work_only_not_model_tokens": True,
    }


def _candidate_strength_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_private_text_included": False,
        "raw_query_text_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
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
            "rg_needle_policy_counts": _prefilter_policy_counts(selected),
            "rg_anchor_kind_counts": _prefilter_anchor_kind_counts(selected),
            "rg_fallback_reason_counts": _prefilter_fallback_reason_counts(selected),
            "rg_prefilter_used_runs": sum(
                1 for row in selected if (row.get("rg_prefilter") or {}).get("status") == "used"
            ),
            "rg_prefilter_eligible_runs": sum(1 for row in selected if (row.get("rg_prefilter") or {}).get("eligible")),
            "pre_parse_statuses": _counts((row.get("pre_parse") or {}).get("status") for row in selected),
            "pre_parse_active_runs": sum(1 for row in selected if (row.get("pre_parse") or {}).get("status") == "active"),
            "pre_parse_anchor_kind_counts": _pre_parse_anchor_kind_counts(selected),
            "raw_lines_examined": sum(int((row.get("pre_parse") or {}).get("raw_lines_examined") or 0) for row in selected),
            "raw_lines_skipped_before_full_json_parse": sum(
                int((row.get("pre_parse") or {}).get("raw_lines_skipped_before_full_json_parse") or 0)
                for row in selected
            ),
            "full_json_records_parsed": sum(
                int((row.get("pre_parse") or {}).get("full_json_records_parsed") or 0) for row in selected
            ),
            "candidate_strength": _candidate_strength_aggregate(selected),
            "fielded_bm25": _fielded_bm25_aggregate(selected),
            "rrf_fusion": _rrf_fusion_aggregate(selected),
            "shared_source_enumeration": _shared_source_enumeration_aggregate(selected),
            "duplicate_local_work": _duplicate_local_work_aggregate(selected),
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
        "compact_package_handoff": _compact_package_handoff_summary(rows),
        "pre_parse": _pre_parse_aggregate(rows),
        "candidate_strength": _candidate_strength_aggregate(rows),
        "fielded_bm25": _fielded_bm25_aggregate(rows),
        "rrf_fusion": _rrf_fusion_aggregate(rows),
        "shared_source_enumeration": _shared_source_enumeration_aggregate(rows),
        "duplicate_local_work": _duplicate_local_work_aggregate(rows),
        "no_evidence_correct": {
            "cases": sum(1 for row in rows if row.get("expected_outcome") == "no_evidence"),
            "correct": sum(1 for row in rows if row.get("no_evidence_correct") is True),
        },
    }


def _rg_prefilter_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if row["baseline"] == "package_search_rg_prefilter"]
    if not selected:
        return {
            "status": "not_evaluated",
            "basis": "package_search_rg_prefilter_not_run",
            "runs": 0,
            "cases": 0,
            "used_runs": 0,
            "eligible_runs": 0,
            "anchor_kind_counts": {},
            "fallback_reason_counts": {},
            "status_counts": {},
            "needle_policy_counts": {},
            "privacy": _rg_prefilter_coverage_privacy(),
        }
    return {
        "status": "evaluated",
        "basis": "sanitized_package_search_rg_prefilter_rows",
        "runs": len(selected),
        "cases": len({row["case_id"] for row in selected}),
        "used_runs": sum(1 for row in selected if (row.get("rg_prefilter") or {}).get("status") == "used"),
        "eligible_runs": sum(1 for row in selected if (row.get("rg_prefilter") or {}).get("eligible")),
        "attempted_runs": sum(1 for row in selected if (row.get("rg_prefilter") or {}).get("attempted")),
        "anchor_kind_counts": _prefilter_anchor_kind_counts(selected),
        "fallback_reason_counts": _prefilter_fallback_reason_counts(selected),
        "status_counts": _counts((row.get("rg_prefilter") or {}).get("status") for row in selected),
        "needle_policy_counts": _prefilter_policy_counts(selected),
        "median_files_reduction_ratio": _median(
            (row.get("rg_prefilter") or {}).get("files_reduction_ratio", 0.0) for row in selected
        ),
        "median_bytes_reduction_ratio": _median(
            (row.get("rg_prefilter") or {}).get("bytes_reduction_ratio", 0.0) for row in selected
        ),
        "privacy": _rg_prefilter_coverage_privacy(),
    }


def _rg_prefilter_coverage_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_anchor_needles_included": False,
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "local_usernames_included": False,
        "case_ids_required": False,
    }


def _rg_prefilter_workflow_decision(
    coverage: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    anchor_total = sum(int(count) for count in (coverage.get("anchor_kind_counts") or {}).values())
    used_runs = int(coverage.get("used_runs") or 0)
    evaluated_runs = int(coverage.get("runs") or 0)
    equivalence = (summary.get("package_search_rg_prefilter") or {}).get("equivalence_vs_package_search") or {}
    if anchor_total and used_runs:
        decision = "keep_opt_in_exact_anchor_rg_prefilter_and_document_when_to_try_it"
        rationale = (
            "Real-history aggregate rows include high-signal exact anchors and at least one used rg prefilter run, "
            "so agents may be nudged to opt in when the user prompt already contains a path, task slug, commit hash, "
            "or code/module identifier."
        )
    elif evaluated_runs:
        decision = "keep_report_diagnostic_only_no_workflow_nudge"
        rationale = (
            "The evaluated real-history rg-prefilter rows did not show used high-signal anchor coverage, "
            "so a skill/workflow nudge is not justified by this aggregate."
        )
    else:
        decision = "not_evaluated"
        rationale = "package_search_rg_prefilter was not run in this benchmark payload."
    return {
        "decision": decision,
        "rationale": rationale,
        "default_search_behavior_changed": False,
        "rg_default_promotion_supported": False,
        "universal_rg_supported": False,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "product_value_claim_supported": False,
        "evidence_basis": coverage.get("basis"),
        "source_ref_equivalence": equivalence,
        "next_action": "review_aggregate_coverage_before_any_runtime_routing_change",
    }


def _compact_package_handoff_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if row.get("baseline") in {"package_search", "package_search_rg_prefilter"}
        and isinstance(row.get("compact_package_handoff"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "basis": "package_compact_handoff_flag_not_enabled_or_package_rows_not_run",
            "runs": 0,
            "cases": 0,
            "baseline_counts": {},
            "full_result_output_context_bytes": 0,
            "compact_context_bytes": 0,
            "locator_context_bytes": 0,
            "source_window_text_bytes": 0,
            "privacy": _compact_package_handoff_privacy(),
            "equivalence": _compact_package_empty_equivalence(),
            "legacy_output_context_bytes_preserved": True,
            "compact_bytes_measured_separately": True,
        }
    stats = [row["compact_package_handoff"] for row in selected]
    full_bytes = sum(int(item.get("full_result_output_context_bytes") or 0) for item in stats)
    compact_bytes = sum(int(item.get("compact_context_bytes") or 0) for item in stats)
    locator_bytes = sum(int(item.get("locator_context_bytes") or 0) for item in stats)
    window_bytes = sum(int(item.get("source_window_text_bytes") or 0) for item in stats)
    compared = len(stats)
    ref_equivalent = sum(1 for item in stats if item.get("top_candidate_ref_ids_equivalent") is True)
    source_equivalent = sum(1 for item in stats if item.get("top_candidate_source_refs_equivalent") is True)
    window_ref_equivalent = sum(1 for item in stats if item.get("source_window_ref_ids_equivalent") is True)
    window_source_equivalent = sum(1 for item in stats if item.get("source_window_source_refs_equivalent") is True)
    return {
        "status": "evaluated",
        "basis": "opt_in_package_search_compact_source_locator_source_window_rows",
        "runs": len(selected),
        "cases": len({row["case_id"] for row in selected}),
        "baseline_counts": _counts(row["baseline"] for row in selected),
        "full_result_output_context_bytes": full_bytes,
        "compact_context_bytes": compact_bytes,
        "locator_context_bytes": locator_bytes,
        "source_window_text_bytes": window_bytes,
        "compact_context_tokens_approx": _ceil_div4(compact_bytes),
        "full_result_output_context_tokens_approx": _ceil_div4(full_bytes),
        "compact_to_full_result_byte_ratio": round(compact_bytes / full_bytes, 6) if full_bytes else 0.0,
        "median_full_result_output_context_bytes": _median(
            item.get("full_result_output_context_bytes") for item in stats
        ),
        "median_compact_context_bytes": _median(item.get("compact_context_bytes") for item in stats),
        "median_locator_context_bytes": _median(item.get("locator_context_bytes") for item in stats),
        "median_source_window_text_bytes": _median(item.get("source_window_text_bytes") for item in stats),
        "source_window_count": sum(int(item.get("source_window_count") or 0) for item in stats),
        "top_candidate_count": sum(int(item.get("top_candidate_count") or 0) for item in stats),
        "equivalence": {
            "compared_runs": compared,
            "comparison_basis": "compact_payload_source_refs_vs_legacy_full_result_source_refs",
            "top_candidate_scope": "same_ordered_top_candidate_limit",
            "top_candidate_limit": COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
            "source_window_scope": "same_ordered_source_window_limit",
            "source_window_limit": COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
            "top_candidate_ref_ids_equivalent_runs": ref_equivalent,
            "top_candidate_source_refs_equivalent_runs": source_equivalent,
            "source_window_ref_ids_equivalent_runs": window_ref_equivalent,
            "source_window_source_refs_equivalent_runs": window_source_equivalent,
            "all_top_candidate_ref_ids_equivalent": bool(compared and ref_equivalent == compared),
            "all_top_candidate_source_refs_equivalent": bool(compared and source_equivalent == compared),
            "all_source_window_ref_ids_equivalent": bool(compared and window_ref_equivalent == compared),
            "all_source_window_source_refs_equivalent": bool(compared and window_source_equivalent == compared),
        },
        "privacy": _compact_package_handoff_privacy(),
        "legacy_output_context_bytes_preserved": all(
            item.get("legacy_output_context_bytes_preserved") is True for item in stats
        ),
        "compact_bytes_measured_separately": all(item.get("compact_bytes_measured_separately") is True for item in stats),
    }


def _compact_package_empty_equivalence() -> dict[str, Any]:
    return {
        "compared_runs": 0,
        "comparison_basis": "compact_payload_source_refs_vs_legacy_full_result_source_refs",
        "top_candidate_scope": "same_ordered_top_candidate_limit",
        "top_candidate_limit": COMPACT_PACKAGE_TOP_CANDIDATE_LIMIT,
        "source_window_scope": "same_ordered_source_window_limit",
        "source_window_limit": COMPACT_PACKAGE_SOURCE_WINDOW_LIMIT,
        "top_candidate_ref_ids_equivalent_runs": 0,
        "top_candidate_source_refs_equivalent_runs": 0,
        "source_window_ref_ids_equivalent_runs": 0,
        "source_window_source_refs_equivalent_runs": 0,
        "all_top_candidate_ref_ids_equivalent": False,
        "all_top_candidate_source_refs_equivalent": False,
        "all_source_window_ref_ids_equivalent": False,
        "all_source_window_source_refs_equivalent": False,
    }


def _compact_package_handoff_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_compact_handoff_detail_committed": False,
        "raw_source_windows_committed": False,
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "local_usernames_included": False,
        "case_ids_required": False,
        "vectors_or_indexes_included": False,
    }


def _compact_package_handoff_decision(summary: dict[str, Any]) -> dict[str, Any]:
    status = summary.get("status")
    if status == "evaluated":
        decision = "keep_opt_in_diagnostic_only_no_default_promotion"
        rationale = (
            "The compact source-locator/source-window package is measured separately from full scanner-result "
            "output_context_bytes and needs independent review before any workflow or default change."
        )
    else:
        decision = "not_evaluated"
        rationale = "Run benchmark-real-history-impact with --package-compact-handoff to evaluate compact counters."
    return {
        "decision": decision,
        "rationale": rationale,
        "default_search_behavior_changed": False,
        "scanner_ranking_source_selection_changed": False,
        "legacy_output_context_bytes_redefined": False,
        "compact_bytes_measured_separately": True,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "product_value_claim_supported": False,
        "default_promotion_supported": False,
        "next_action": "independent_review_compact_package_counters_before_any_runtime_or_workflow_change",
    }


def _agent_compact_provenance_row_stats(
    compact_handoff: dict[str, Any],
    *,
    row: dict[str, Any],
    full_result_output_context_bytes: int,
) -> dict[str, Any]:
    context_budget = compact_handoff.get("context_budget") if isinstance(compact_handoff.get("context_budget"), dict) else {}
    compact_context_bytes = int(context_budget.get("payload_bytes") or _json_bytes(compact_handoff))
    ratio = round(compact_context_bytes / full_result_output_context_bytes, 6) if full_result_output_context_bytes else 0.0
    no_evidence = compact_handoff.get("no_evidence") if isinstance(compact_handoff.get("no_evidence"), dict) else {}
    privacy = compact_handoff.get("privacy_boundaries") if isinstance(compact_handoff.get("privacy_boundaries"), dict) else {}
    claims = compact_handoff.get("claim_boundaries") if isinstance(compact_handoff.get("claim_boundaries"), dict) else {}
    return {
        "enabled": True,
        "package_kind": "agent_compact_provenance_handoff",
        "measurement_basis": context_budget.get("measurement_basis") or "compact_provenance_handoff_packet_only",
        "command_surface": compact_handoff.get("command_surface"),
        "status": compact_handoff.get("status"),
        "candidate_count": int(compact_handoff.get("candidate_count") or 0),
        "compact_candidate_count": len(compact_handoff.get("candidates") or []),
        "source_ref_count": len(compact_handoff.get("source_refs") or []),
        "source_window_count": len(compact_handoff.get("source_windows") or []),
        "next_read_count": len(compact_handoff.get("next_reads") or []),
        "full_result_output_context_bytes": full_result_output_context_bytes,
        "compact_context_bytes": compact_context_bytes,
        "compact_to_full_result_byte_ratio": ratio,
        "compact_byte_delta_vs_full_result": compact_context_bytes - full_result_output_context_bytes,
        "legacy_output_context_bytes_preserved": True,
        "compact_bytes_measured_separately": True,
        "no_evidence": {
            "needed": bool(no_evidence.get("needed")),
            "answer_scope_label": _safe_agent_compact_label(no_evidence.get("answer_scope")),
            "not_all_history_absence": bool(no_evidence.get("not_all_history_absence")),
            "scope_limited_semantics_preserved": (
                no_evidence.get("answer_scope") == "not_found_in_searched_scope"
                and no_evidence.get("not_all_history_absence") is True
            ),
        },
        "cap_hit": {
            "labels": list(row.get("cap_hit_labels") or []),
            "hit": bool(row.get("cap_hit_labels")),
        },
        "privacy": _agent_compact_provenance_privacy(privacy),
        "claim_boundaries": _agent_compact_provenance_claim_boundaries(claims),
    }


def _agent_compact_provenance_handoff_summary(
    rows: list[dict[str, Any]],
    *,
    manifest_case_count: int,
    evaluated_case_count: int,
    requested_case_limit: int,
) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if row.get("baseline") == "package_search"
        and isinstance(row.get("agent_compact_provenance_handoff"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "basis": "agent_compact_provenance_handoff_flag_not_enabled_or_package_search_rows_not_run",
            "runs": 0,
            "cases": 0,
            "manifest_case_count": manifest_case_count,
            "evaluated_case_count": evaluated_case_count,
            "requested_case_limit": requested_case_limit,
            "full_manifest_requested": requested_case_limit == 0,
            "full_manifest_run": False,
            "full_manifest_20_case_run": False,
            "baseline_counts": {},
            "full_result_output_context_bytes": 0,
            "compact_context_bytes": 0,
            "privacy": _agent_compact_provenance_privacy(),
            "claim_boundaries": _agent_compact_provenance_claim_boundaries(),
            "legacy_output_context_bytes_preserved": True,
            "compact_bytes_measured_separately": True,
        }
    stats = [row["agent_compact_provenance_handoff"] for row in selected]
    full_bytes = sum(int(item.get("full_result_output_context_bytes") or 0) for item in stats)
    compact_bytes = sum(int(item.get("compact_context_bytes") or 0) for item in stats)
    status_counts = _counts(item.get("status") for item in stats)
    no_evidence_rows = [item.get("no_evidence") or {} for item in stats]
    cap_rows = [item.get("cap_hit") or {} for item in stats]
    full_manifest_run = requested_case_limit == 0 and evaluated_case_count == manifest_case_count
    aggregate_privacy = _agent_compact_provenance_aggregate_privacy(stats)
    return {
        "status": "evaluated",
        "basis": "opt_in_agent_find_compact_provenance_handoff_rows",
        "runs": len(selected),
        "cases": len({row["case_id"] for row in selected}),
        "manifest_case_count": manifest_case_count,
        "evaluated_case_count": evaluated_case_count,
        "requested_case_limit": requested_case_limit,
        "full_manifest_requested": requested_case_limit == 0,
        "full_manifest_run": full_manifest_run,
        "full_manifest_20_case_run": bool(full_manifest_run and manifest_case_count == 20 and evaluated_case_count == 20),
        "baseline_counts": _counts(row["baseline"] for row in selected),
        "status_counts": status_counts,
        "full_result_output_context_bytes": full_bytes,
        "compact_context_bytes": compact_bytes,
        "compact_context_tokens_approx": _ceil_div4(compact_bytes),
        "full_result_output_context_tokens_approx": _ceil_div4(full_bytes),
        "compact_to_full_result_byte_ratio": round(compact_bytes / full_bytes, 6) if full_bytes else 0.0,
        "median_full_result_output_context_bytes": _median(
            item.get("full_result_output_context_bytes") for item in stats
        ),
        "median_compact_context_bytes": _median(item.get("compact_context_bytes") for item in stats),
        "candidate_count": sum(int(item.get("candidate_count") or 0) for item in stats),
        "compact_candidate_count": sum(int(item.get("compact_candidate_count") or 0) for item in stats),
        "source_ref_count": sum(int(item.get("source_ref_count") or 0) for item in stats),
        "source_window_count": sum(int(item.get("source_window_count") or 0) for item in stats),
        "next_read_count": sum(int(item.get("next_read_count") or 0) for item in stats),
        "median_source_ref_count": _median(item.get("source_ref_count") for item in stats),
        "median_next_read_count": _median(item.get("next_read_count") for item in stats),
        "no_evidence": {
            "rows": len(no_evidence_rows),
            "needed_rows": sum(1 for item in no_evidence_rows if item.get("needed") is True),
            "scope_limited_semantics_preserved_rows": sum(
                1 for item in no_evidence_rows if item.get("scope_limited_semantics_preserved") is True
            ),
            "not_all_history_absence_rows": sum(
                1 for item in no_evidence_rows if item.get("not_all_history_absence") is True
            ),
            "answer_scope_counts": _counts(item.get("answer_scope_label") for item in no_evidence_rows),
        },
        "cap_hit": {
            "rows": sum(1 for item in cap_rows if item.get("hit") is True),
            "rows_by_label": _cap_hit_row_counts(selected),
        },
        "privacy": aggregate_privacy,
        "claim_boundaries": _agent_compact_provenance_claim_boundaries(),
        "legacy_output_context_bytes_preserved": all(
            item.get("legacy_output_context_bytes_preserved") is True for item in stats
        ),
        "compact_bytes_measured_separately": all(item.get("compact_bytes_measured_separately") is True for item in stats),
    }


def _safe_agent_compact_label(value: Any) -> str:
    label = str(value or "unavailable")
    return label if _safe_counter_label(label) else "other"


def _agent_compact_provenance_privacy(source: dict[str, Any] | None = None) -> dict[str, bool]:
    source = source or {}
    return {
        "aggregate_only": True,
        "raw_agent_compact_handoff_detail_committed": False,
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "local_usernames_included": False,
        "case_ids_required": False,
        "vectors_or_indexes_included": False,
        "raw_private_text_or_snippets_included": bool(source.get("raw_private_text_or_snippets_included", False)),
        "source_window_text_included": bool(source.get("source_window_text_included", False)),
        "source_refs_may_point_to_local_transcripts": bool(source.get("source_refs_may_point_to_local_transcripts", False)),
    }


def _agent_compact_provenance_aggregate_privacy(stats: list[dict[str, Any]]) -> dict[str, bool]:
    privacy_rows = [item.get("privacy") or {} for item in stats]
    return _agent_compact_provenance_privacy(
        {
            "raw_private_text_or_snippets_included": any(
                item.get("raw_private_text_or_snippets_included") is True for item in privacy_rows
            ),
            "source_window_text_included": any(item.get("source_window_text_included") is True for item in privacy_rows),
            "source_refs_may_point_to_local_transcripts": any(
                item.get("source_refs_may_point_to_local_transcripts") is True for item in privacy_rows
            ),
        }
    )


def _agent_compact_provenance_claim_boundaries(source: dict[str, Any] | None = None) -> dict[str, bool]:
    source = source or {}
    return {
        "compact_handoff_is_opt_in": bool(source.get("compact_handoff_is_opt_in", True)),
        "default_behavior_changed": bool(source.get("default_behavior_changed", False)),
        "scanner_ranking_or_source_selection_changed": bool(
            source.get("scanner_ranking_or_source_selection_changed", False)
        ),
        "token_savings_claim_supported": bool(source.get("token_savings_claim_supported", False)),
        "final_answer_quality_claim_supported": bool(source.get("final_answer_quality_claim_supported", False)),
        "product_value_claim_supported": bool(source.get("product_value_claim_supported", False)),
        "default_promotion_supported": bool(source.get("default_promotion_supported", False)),
    }


def _agent_compact_provenance_handoff_decision(summary: dict[str, Any]) -> dict[str, Any]:
    status = summary.get("status")
    if status == "evaluated":
        if summary.get("full_manifest_20_case_run"):
            decision = "record_full_manifest_pressure_aggregate_only_keep_opt_in"
            rationale = (
                "The actual agent-facing compact_provenance_handoff packet was measured through the find "
                "contract on the full 20-case manifest; this is pressure evidence only and remains opt-in."
            )
            next_action = "independent_review_agent_compact_full_manifest_pressure_before_any_follow_up"
        elif summary.get("full_manifest_requested"):
            decision = "record_non_20_or_partial_manifest_pressure_keep_opt_in"
            rationale = (
                "The actual agent-facing compact_provenance_handoff packet was measured through the find "
                "contract, but the run was not a full 20-case manifest result."
            )
            next_action = "fix_or_record_full_manifest_pressure_bottleneck"
        else:
            decision = "debug_subset_only_keep_opt_in"
            rationale = (
                "The actual agent-facing compact_provenance_handoff packet was measured on a subset only; "
                "subset results are debug evidence and do not support product or default-promotion claims."
            )
            next_action = "run_case_limit_0_full_manifest_before_product_interpretation"
    else:
        decision = "not_evaluated"
        rationale = (
            "Run benchmark-real-history-impact with --agent-compact-provenance-handoff to evaluate the "
            "actual JSH-804 agent-facing packet."
        )
        next_action = "run_agent_compact_provenance_handoff_pressure"
    return {
        "decision": decision,
        "rationale": rationale,
        "default_search_behavior_changed": False,
        "scanner_ranking_source_selection_changed": False,
        "legacy_output_context_bytes_redefined": False,
        "compact_bytes_measured_separately": True,
        "measured_packet": "agent_find.compact_provenance_handoff",
        "measured_builder": "build_agent_find_payload(compact_provenance_handoff=True)",
        "old_compact_package_diagnostic_used_as_measured_packet": False,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "product_value_claim_supported": False,
        "default_promotion_supported": False,
        "next_action": next_action,
    }


def _source_tier_answerability_pressure_row_stats(
    compact_handoff: dict[str, Any],
    *,
    row: dict[str, Any],
    full_result_output_context_bytes: int,
) -> dict[str, Any]:
    evidence = (
        compact_handoff.get("evidence_sufficiency")
        if isinstance(compact_handoff.get("evidence_sufficiency"), dict)
        else {}
    )
    context_budget = (
        compact_handoff.get("context_budget") if isinstance(compact_handoff.get("context_budget"), dict) else {}
    )
    output_budget = compact_handoff.get("output_budget") if isinstance(compact_handoff.get("output_budget"), dict) else {}
    no_evidence = compact_handoff.get("no_evidence") if isinstance(compact_handoff.get("no_evidence"), dict) else {}
    stage_diagnostics = (
        compact_handoff.get("stage_diagnostics")
        if isinstance(compact_handoff.get("stage_diagnostics"), dict)
        else compact_handoff.get("stage_diagnostics_summary")
        if isinstance(compact_handoff.get("stage_diagnostics_summary"), dict)
        else {}
    )
    source_catalog = stage_diagnostics.get("source_catalog") if isinstance(stage_diagnostics.get("source_catalog"), dict) else {}
    source_plan = stage_diagnostics.get("source_plan") if isinstance(stage_diagnostics.get("source_plan"), dict) else {}
    source_tiers = source_plan.get("source_tiers") if isinstance(source_plan.get("source_tiers"), dict) else {}
    retry_guidance = source_plan.get("retry_guidance") if isinstance(source_plan.get("retry_guidance"), dict) else {}
    searched_scope_support = (
        evidence.get("searched_scope_support") if isinstance(evidence.get("searched_scope_support"), dict) else {}
    )
    source_tier_boundary = (
        evidence.get("source_tier_boundary") if isinstance(evidence.get("source_tier_boundary"), dict) else {}
    )
    no_evidence_boundary = (
        evidence.get("no_evidence_boundary") if isinstance(evidence.get("no_evidence_boundary"), dict) else {}
    )
    next_read_recovery = (
        evidence.get("next_read_recovery") if isinstance(evidence.get("next_read_recovery"), dict) else {}
    )
    related_policy = (
        evidence.get("related_candidate_policy") if isinstance(evidence.get("related_candidate_policy"), dict) else {}
    )
    counts = output_budget.get("counts") if isinstance(output_budget.get("counts"), dict) else {}
    limits = output_budget.get("limits") if isinstance(output_budget.get("limits"), dict) else {}
    compact_context_bytes = int(context_budget.get("payload_bytes") or _json_bytes(compact_handoff))
    return {
        "enabled": True,
        "baseline": row.get("baseline"),
        "package_kind": "source_tier_answerability_pressure",
        "measurement_basis": "fielded_bm25_result_to_live_agent_find_compact_provenance_handoff",
        "command_surface": compact_handoff.get("command_surface"),
        "packet_status": compact_handoff.get("status"),
        "answerability_status": _safe_agent_compact_label(
            compact_handoff.get("answerability_status") or evidence.get("support_class")
        ),
        "evidence_sufficiency": {
            "support_class": _safe_agent_compact_label(evidence.get("support_class")),
            "answerable_from_packet": bool(evidence.get("answerable_from_packet", False)),
            "grounding_state": _safe_agent_compact_label(evidence.get("grounding_state")),
            "candidate_presence_implies_answerability": bool(
                evidence.get("candidate_presence_implies_answerability", False)
            ),
            "searched_scope_support": {
                "candidate_count": int(searched_scope_support.get("candidate_count") or 0),
                "strong_candidate_count": int(searched_scope_support.get("strong_candidate_count") or 0),
                "weak_or_related_candidate_count": int(
                    searched_scope_support.get("weak_or_related_candidate_count") or 0
                ),
                "diagnostic_candidates_preserved": bool(
                    searched_scope_support.get("diagnostic_candidates_preserved", False)
                ),
            },
            "source_tier_boundary": {
                "sources_selected": int(source_tier_boundary.get("sources_selected") or 0),
                "sources_deferred_budget": int(source_tier_boundary.get("sources_deferred_budget") or 0),
                "retry_guidance_needed": bool(source_tier_boundary.get("retry_guidance_needed", False)),
                "exhaustive_available": bool(source_tier_boundary.get("exhaustive_available", False)),
                "hard_required_selected": int(source_tier_boundary.get("hard_required_selected") or 0),
                "raw_paths_included": False,
                "raw_text_included": False,
            },
            "next_read_recovery": {
                "recoverable_by_opening_next_reads": bool(
                    next_read_recovery.get("recoverable_by_opening_next_reads", False)
                ),
                "recovery_kind": _safe_agent_compact_label(next_read_recovery.get("recovery_kind")),
                "candidate_window_count": int(next_read_recovery.get("candidate_window_count") or 0),
                "next_read_count": int(next_read_recovery.get("next_read_count") or 0),
                "candidate_window_limit": int(next_read_recovery.get("candidate_window_limit") or 0),
                "candidate_window_omitted_count": int(
                    next_read_recovery.get("candidate_window_omitted_count") or 0
                ),
                "source_grouping_selection_changed": bool(
                    next_read_recovery.get("source_grouping_selection_changed", False)
                ),
                "collapsed_adjacent_run_count": int(next_read_recovery.get("collapsed_adjacent_run_count") or 0),
                "suppressed_adjacent_row_count": int(next_read_recovery.get("suppressed_adjacent_row_count") or 0),
                "rows_dropped_by_source_cap": int(next_read_recovery.get("rows_dropped_by_source_cap") or 0),
                "raw_paths_included": False,
                "raw_text_included": False,
                "source_lines_included": False,
                "line_numbers_included": False,
            },
            "no_evidence_boundary": {
                "needed": bool(no_evidence_boundary.get("needed", False)),
                "answer_scope": _safe_agent_compact_label(no_evidence_boundary.get("answer_scope")),
                "not_all_history_absence": bool(no_evidence_boundary.get("not_all_history_absence", True)),
                "deferred_source_count": int(no_evidence_boundary.get("deferred_source_count") or 0),
            },
            "related_candidate_policy": {
                "weak_candidates_remain_as_diagnostics": bool(
                    related_policy.get("weak_candidates_remain_as_diagnostics", False)
                ),
                "source_refs_and_windows_preserved": bool(
                    related_policy.get("source_refs_and_windows_preserved", False)
                ),
                "do_not_promote_weak_candidates_to_answer": bool(
                    related_policy.get("do_not_promote_weak_candidates_to_answer", False)
                ),
            },
        },
        "source_catalog": {
            "present": bool(source_catalog),
            "file_count": int(source_catalog.get("file_count") or 0),
            "byte_count": int(source_catalog.get("byte_count") or 0),
            "selected_file_count": int(source_catalog.get("selected_file_count") or 0),
            "skipped_file_count": int(source_catalog.get("skipped_file_count") or 0),
            "cap_labels": [label for label in source_catalog.get("cap_labels") or [] if _safe_counter_label(str(label))],
            "raw_paths_included": False,
            "raw_text_included": False,
            "persistent_index_or_cache_used": False,
        },
        "source_plan": {
            "present": bool(source_plan),
            "sources_total": int(source_plan.get("sources_total") or 0),
            "sources_selected": int(source_plan.get("sources_selected") or 0),
            "sources_pruned_hard": int(source_plan.get("sources_pruned_hard") or 0),
            "sources_deferred_budget": int(source_plan.get("sources_deferred_budget") or 0),
            "exhaustive_available": bool(source_plan.get("exhaustive_available", False)),
            "tier_order": [
                str(item) for item in source_plan.get("tier_order") or [] if _safe_counter_label(str(item))
            ],
            "selected_counts": _safe_count_dict(source_tiers.get("selected_counts")),
            "deferred_counts": _safe_count_dict(source_tiers.get("deferred_counts")),
            "deferred_reason_counts": _safe_count_dict(source_plan.get("deferred_reason_counts")),
            "retry_guidance_needed": bool(retry_guidance.get("needed", False)),
            "retry_deferred_source_count": int(retry_guidance.get("deferred_source_count") or 0),
            "retry_guidance_present": bool(retry_guidance.get("guidance")),
            "raw_paths_included": False,
            "raw_text_included": False,
            "uses_scoring_weights": bool((source_plan.get("budget_basis") or {}).get("uses_scoring_weights", False)),
        },
        "source_window_packaging": _source_tier_stage_counters(stage_diagnostics, "source_window_packaging"),
        "compact_context_budget": {
            "payload_bytes": compact_context_bytes,
            "estimated_tokens": int(context_budget.get("estimated_tokens") or _ceil_div4(compact_context_bytes)),
            "measurement_basis": context_budget.get("measurement_basis") or "compact_provenance_handoff_packet_only",
            "full_result_output_context_bytes": full_result_output_context_bytes,
            "compact_to_full_result_byte_ratio": (
                round(compact_context_bytes / full_result_output_context_bytes, 6)
                if full_result_output_context_bytes
                else 0.0
            ),
            "output_counts": {str(key): int(value or 0) for key, value in sorted(counts.items())},
            "output_limits": {str(key): int(value or 0) for key, value in sorted(limits.items())},
        },
        "no_evidence": {
            "needed": bool(no_evidence.get("needed")),
            "answer_scope_label": (
                _safe_agent_compact_label(no_evidence_boundary.get("answer_scope"))
                if bool(no_evidence_boundary.get("needed", False))
                else "not_applicable"
            ),
            "not_all_history_absence": bool(no_evidence_boundary.get("needed", False))
            and bool(no_evidence.get("not_all_history_absence")),
        },
        "stage_diagnostics": {
            "present": bool(stage_diagnostics),
            "dominant_pressure_stage": _safe_agent_compact_label(stage_diagnostics.get("dominant_pressure_stage")),
            "stage_status_counts": _stage_status_counts(stage_diagnostics),
            "stage_next_fix_hint_counts": _stage_next_fix_hint_counts(stage_diagnostics),
        },
        "cap_hit": {
            "labels": list(row.get("cap_hit_labels") or []),
            "hit": bool(row.get("cap_hit_labels")),
        },
        "privacy": _source_tier_answerability_pressure_privacy(),
        "claim_boundaries": _source_tier_answerability_pressure_claim_boundaries(),
    }


def _source_tier_answerability_pressure_summary(
    rows: list[dict[str, Any]],
    *,
    manifest_case_count: int,
    evaluated_case_count: int,
    requested_case_limit: int,
) -> dict[str, Any]:
    selected = [
        row
        for row in rows
        if row.get("baseline") == "package_search_fielded_bm25"
        and isinstance(row.get("source_tier_answerability_pressure"), dict)
    ]
    if not selected:
        return {
            "status": "not_evaluated",
            "basis": "source_tier_answerability_pressure_flag_not_enabled_or_fielded_bm25_rows_not_run",
            "runs": 0,
            "cases": 0,
            "manifest_case_count": manifest_case_count,
            "evaluated_case_count": evaluated_case_count,
            "requested_case_limit": requested_case_limit,
            "full_manifest_requested": requested_case_limit == 0,
            "full_manifest_run": False,
            "full_manifest_20_case_run": False,
            "baseline_counts": {},
            "answerability_status_counts": {},
            "evidence_sufficiency": {},
            "source_catalog": {},
            "source_plan": {},
            "stage_bottlenecks": {},
            "privacy": _source_tier_answerability_pressure_privacy(),
            "claim_boundaries": _source_tier_answerability_pressure_claim_boundaries(),
        }
    stats = [row["source_tier_answerability_pressure"] for row in selected]
    evidence_rows = [item.get("evidence_sufficiency") or {} for item in stats]
    recovery_rows = [item.get("next_read_recovery") or {} for item in evidence_rows]
    source_plans = [item.get("source_plan") or {} for item in stats]
    source_catalogs = [item.get("source_catalog") or {} for item in stats]
    budgets = [item.get("compact_context_budget") or {} for item in stats]
    no_evidence_rows = [item.get("no_evidence") or {} for item in stats]
    stage_rows = [item.get("stage_diagnostics") or {} for item in stats]
    cap_rows = [item.get("cap_hit") or {} for item in stats]
    full_manifest_run = requested_case_limit == 0 and evaluated_case_count == manifest_case_count
    return {
        "status": "evaluated",
        "basis": "opt_in_fielded_bm25_live_agent_compact_answerability_rows",
        "runs": len(selected),
        "cases": len({row["case_id"] for row in selected}),
        "manifest_case_count": manifest_case_count,
        "evaluated_case_count": evaluated_case_count,
        "requested_case_limit": requested_case_limit,
        "full_manifest_requested": requested_case_limit == 0,
        "full_manifest_run": full_manifest_run,
        "full_manifest_20_case_run": bool(full_manifest_run and manifest_case_count == 20 and evaluated_case_count == 20),
        "baseline_counts": _counts(row["baseline"] for row in selected),
        "answerability_status_counts": _counts(item.get("answerability_status") for item in stats),
        "answerable_from_packet_rows": sum(
            1 for item in evidence_rows if item.get("answerable_from_packet") is True
        ),
        "candidate_presence_implies_answerability_rows": sum(
            1 for item in evidence_rows if item.get("candidate_presence_implies_answerability") is True
        ),
        "diagnostic_candidate_rows": sum(
            1
            for item in evidence_rows
            if (item.get("searched_scope_support") or {}).get("diagnostic_candidates_preserved") is True
        ),
        "weak_candidate_diagnostic_rows": sum(
            1
            for item in evidence_rows
            if int((item.get("searched_scope_support") or {}).get("weak_or_related_candidate_count") or 0) > 0
        ),
        "next_read_recovery": {
            "recoverable_rows": sum(
                1 for item in recovery_rows if item.get("recoverable_by_opening_next_reads") is True
            ),
            "recovery_kind_counts": _counts(item.get("recovery_kind") for item in recovery_rows),
            "candidate_window_count_total": sum(int(item.get("candidate_window_count") or 0) for item in recovery_rows),
            "next_read_count_total": sum(int(item.get("next_read_count") or 0) for item in recovery_rows),
            "candidate_window_omitted_total": sum(
                int(item.get("candidate_window_omitted_count") or 0) for item in recovery_rows
            ),
            "source_grouping_selection_changed_rows": sum(
                1 for item in recovery_rows if item.get("source_grouping_selection_changed") is True
            ),
            "collapsed_adjacent_run_count_total": sum(
                int(item.get("collapsed_adjacent_run_count") or 0) for item in recovery_rows
            ),
            "suppressed_adjacent_row_count_total": sum(
                int(item.get("suppressed_adjacent_row_count") or 0) for item in recovery_rows
            ),
            "rows_dropped_by_source_cap_total": sum(
                int(item.get("rows_dropped_by_source_cap") or 0) for item in recovery_rows
            ),
            "raw_paths_included": any(item.get("raw_paths_included") is True for item in recovery_rows),
            "raw_text_included": any(item.get("raw_text_included") is True for item in recovery_rows),
            "source_lines_included": any(item.get("source_lines_included") is True for item in recovery_rows),
            "line_numbers_included": any(item.get("line_numbers_included") is True for item in recovery_rows),
        },
        "source_catalog": {
            "present_rows": sum(1 for item in source_catalogs if item.get("present") is True),
            "median_file_count": _median(item.get("file_count") for item in source_catalogs),
            "median_selected_file_count": _median(item.get("selected_file_count") for item in source_catalogs),
            "cap_label_counts": _counts(
                label for item in source_catalogs for label in (item.get("cap_labels") or [])
            ),
            "raw_paths_included": any(item.get("raw_paths_included") is True for item in source_catalogs),
            "raw_text_included": any(item.get("raw_text_included") is True for item in source_catalogs),
            "persistent_index_or_cache_used": any(
                item.get("persistent_index_or_cache_used") is True for item in source_catalogs
            ),
        },
        "source_plan": {
            "present_rows": sum(1 for item in source_plans if item.get("present") is True),
            "median_sources_total": _median(item.get("sources_total") for item in source_plans),
            "median_sources_selected": _median(item.get("sources_selected") for item in source_plans),
            "sources_selected_total": sum(int(item.get("sources_selected") or 0) for item in source_plans),
            "sources_pruned_hard_total": sum(int(item.get("sources_pruned_hard") or 0) for item in source_plans),
            "sources_deferred_budget_total": sum(
                int(item.get("sources_deferred_budget") or 0) for item in source_plans
            ),
            "deferred_rows": sum(1 for item in source_plans if int(item.get("sources_deferred_budget") or 0) > 0),
            "retry_guidance_rows": sum(1 for item in source_plans if item.get("retry_guidance_needed") is True),
            "retry_guidance_present_rows": sum(
                1 for item in source_plans if item.get("retry_guidance_present") is True
            ),
            "selected_tier_counts": _sum_count_dicts(item.get("selected_counts") for item in source_plans),
            "deferred_tier_counts": _sum_count_dicts(item.get("deferred_counts") for item in source_plans),
            "deferred_reason_counts": _sum_count_dicts(item.get("deferred_reason_counts") for item in source_plans),
            "uses_scoring_weights_rows": sum(1 for item in source_plans if item.get("uses_scoring_weights") is True),
            "raw_paths_included": any(item.get("raw_paths_included") is True for item in source_plans),
            "raw_text_included": any(item.get("raw_text_included") is True for item in source_plans),
        },
        "no_evidence": {
            "needed_rows": sum(1 for item in no_evidence_rows if item.get("needed") is True),
            "not_all_history_absence_rows": sum(
                1 for item in no_evidence_rows if item.get("not_all_history_absence") is True
            ),
            "answer_scope_counts": _counts(item.get("answer_scope_label") for item in no_evidence_rows),
        },
        "compact_context_budget": {
            "compact_context_bytes": sum(int(item.get("payload_bytes") or 0) for item in budgets),
            "compact_context_tokens_approx": _ceil_div4(sum(int(item.get("payload_bytes") or 0) for item in budgets)),
            "full_result_output_context_bytes": sum(
                int(item.get("full_result_output_context_bytes") or 0) for item in budgets
            ),
            "median_compact_context_bytes": _median(item.get("payload_bytes") for item in budgets),
            "median_full_result_output_context_bytes": _median(
                item.get("full_result_output_context_bytes") for item in budgets
            ),
            "measurement_basis_counts": _counts(item.get("measurement_basis") for item in budgets),
            "output_count_totals": _sum_count_dicts(item.get("output_counts") for item in budgets),
            "output_limit_maxima": _max_count_dicts(item.get("output_limits") for item in budgets),
        },
        "stage_bottlenecks": {
            "dominant_pressure_stage_counts": _counts(item.get("dominant_pressure_stage") for item in stage_rows),
            "stage_status_counts": _sum_nested_count_dicts(item.get("stage_status_counts") for item in stage_rows),
            "stage_next_fix_hint_counts": _sum_nested_count_dicts(
                item.get("stage_next_fix_hint_counts") for item in stage_rows
            ),
        },
        "cap_hit": {
            "rows": sum(1 for item in cap_rows if item.get("hit") is True),
            "rows_by_label": _cap_hit_row_counts(selected),
        },
        "privacy": _source_tier_answerability_pressure_privacy(),
        "claim_boundaries": _source_tier_answerability_pressure_claim_boundaries(),
    }


def _source_tier_answerability_pressure_decision(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("status") != "evaluated":
        decision = "not_evaluated"
        rationale = (
            "Run benchmark-real-history-impact with --baseline package_search_fielded_bm25 "
            "--source-tier-answerability-pressure to evaluate the JSH-841 answerability path under real-history pressure."
        )
        next_action = "run_source_tier_answerability_pressure_full_manifest"
    elif not summary.get("full_manifest_run"):
        decision = "record_subset_or_partial_pressure_only"
        rationale = "The source-tier answerability pressure section did not cover the full manifest."
        next_action = "rerun_full_manifest_or_record_exact_stage_bottleneck"
    elif int((summary.get("cap_hit") or {}).get("rows") or 0) > 0:
        decision = "improve_fielded_bm25_large_history_cap_pressure"
        rationale = "Full-manifest source-tier answerability pressure still hit configured caps."
        next_action = "target_dominant_stage_bottleneck_before_answer_quality_claims"
    elif int((summary.get("source_plan") or {}).get("deferred_rows") or 0) > 0:
        decision = "improve_deferred_source_tier_retry_or_budget_behavior"
        rationale = "Source-tier answerability stayed non-exhaustive because selected runs deferred sources."
        next_action = "inspect_deferred_source_tier_rows_privately_and_choose_source_budget_or_retry_guidance_delta"
    elif int(summary.get("candidate_presence_implies_answerability_rows") or 0) > 0:
        decision = "repair_candidate_presence_answerability_boundary"
        rationale = "At least one compact packet implied answerability from candidate presence."
        next_action = "fix_answerability_boundary_before_more_pressure_reporting"
    else:
        decision = "record_full_manifest_answerability_pressure_keep_opt_in"
        rationale = (
            "The live fielded-BM25 compact handoff answerability path produced aggregate-only pressure "
            "counters without changing defaults or supporting final-answer/token claims."
        )
        next_action = "independent_review_then_choose_next_product_delta_from_stage_bottleneck_counts"
    return {
        "selected_next_product_decision": decision,
        "decision": decision,
        "rationale": rationale,
        "next_action": next_action,
        "default_search_behavior_changed": False,
        "scanner_ranking_source_selection_changed": True,
        "legacy_output_context_bytes_redefined": False,
        "measured_packet": "agent_find.compact_provenance_handoff",
        "measured_builder": "build_agent_find_payload(compact_provenance_handoff=True)",
        "measured_baseline": "package_search_fielded_bm25",
        "uses_live_fielded_bm25_result": True,
        "uses_handcrafted_packets": False,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "product_value_claim_supported": False,
        "default_promotion_supported": False,
    }


def _large_history_stage_attribution_pressure_summary(
    source_summary: dict[str, Any],
    *,
    base_report: dict[str, Any],
) -> dict[str, Any]:
    if source_summary.get("status") != "evaluated":
        no_decision_reason = "source_tier_answerability_pressure_not_evaluated"
        return {
            "status": "not_evaluated",
            "basis": "requires_source_tier_answerability_pressure",
            "allowed_bottleneck_labels": ALLOWED_BOTTLENECK_LABELS,
            "selected_next_bottleneck": "no_decision",
            "stage_bottleneck_summary": _large_history_stage_bottleneck_summary({}, {}, no_decision_reason),
            "selected_next_tool_delta": _large_history_tool_delta("no_decision", no_decision_reason),
            "no_decision_reason": no_decision_reason,
            "latency_indexing_boundary": _large_history_latency_indexing_boundary(source_summary, base_report),
            "privacy": _large_history_stage_attribution_privacy(),
            "claim_boundaries": _large_history_stage_attribution_claim_boundaries(),
        }

    stage_scores = _large_history_stage_scores(source_summary, base_report)
    selected = _select_large_history_bottleneck(stage_scores)
    latency_boundary = _large_history_latency_indexing_boundary(source_summary, base_report)
    if selected == "indexing_latency" and not latency_boundary["indexing_latency_selection_supported"]:
        stage_scores["indexing_latency"] = 0
        selected = _select_large_history_bottleneck(stage_scores)
    no_decision_reason = _large_history_no_decision_reason(source_summary, stage_scores, latency_boundary)
    return {
        "status": "evaluated",
        "basis": "aggregate_safe_source_tier_answerability_pressure_stage_mapping",
        "allowed_bottleneck_labels": ALLOWED_BOTTLENECK_LABELS,
        "selected_next_bottleneck": selected,
        "stage_bottleneck_summary": _large_history_stage_bottleneck_summary(
            stage_scores,
            _large_history_stage_signal_counts(source_summary, base_report),
            no_decision_reason,
        ),
        "selected_next_tool_delta": _large_history_tool_delta(selected, no_decision_reason),
        **({"no_decision_reason": no_decision_reason} if selected == "no_decision" else {}),
        "latency_indexing_boundary": latency_boundary,
        "privacy": _large_history_stage_attribution_privacy(),
        "claim_boundaries": _large_history_stage_attribution_claim_boundaries(),
    }


def _large_history_stage_scores(source_summary: dict[str, Any], base_report: dict[str, Any]) -> dict[str, int]:
    signals = _large_history_stage_signal_counts(source_summary, base_report)
    scores = {label: 0 for label in ALLOWED_BOTTLENECK_LABELS}
    scores["source_window_choice"] = (
        signals["deferred_source_needed_rows"]
        + signals["source_plan_deferred_rows"]
        + signals["source_plan_retry_guidance_rows"]
        + signals["source_grouping_selection_changed_rows"]
        + signals["candidate_window_omitted_total"]
        + signals["rows_dropped_by_source_cap_total"]
        + signals["source_window_stage_pressure_rows"]
    )
    within_source_depth = signals["next_read_recoverable_rows"] - signals["source_grouping_selection_changed_rows"]
    scores["within_source_depth"] = max(0, within_source_depth) + signals["next_read_omission_total"]
    scores["query_planning"] = signals["query_plan_hint_rows"]
    scores["output_behavior"] = (
        signals["candidate_presence_implies_answerability_rows"]
        + signals["compact_output_omission_total"]
        + signals["compact_output_stage_pressure_rows"]
    )
    if _large_history_latency_indexing_boundary(
        source_summary,
        base_report,
    )["indexing_latency_selection_supported"]:
        scores["indexing_latency"] = signals["cap_hit_rows"] + signals["fielded_bm25_slow_phase_count"]
    return scores


def _large_history_stage_signal_counts(source_summary: dict[str, Any], base_report: dict[str, Any]) -> dict[str, int]:
    answerability = source_summary.get("answerability_status_counts") or {}
    source_plan = source_summary.get("source_plan") or {}
    recovery = source_summary.get("next_read_recovery") or {}
    compact_budget = source_summary.get("compact_context_budget") or {}
    stage_bottlenecks = source_summary.get("stage_bottlenecks") or {}
    stage_status = stage_bottlenecks.get("stage_status_counts") or {}
    stage_hints = stage_bottlenecks.get("stage_next_fix_hint_counts") or {}
    output_totals = compact_budget.get("output_count_totals") or {}
    cap_hit = source_summary.get("cap_hit") or {}
    fielded_phase_profiles = (
        (base_report.get("aggregate") or {}).get("fielded_bm25", {}).get("phase_profiles") or {}
    )
    return {
        "deferred_source_needed_rows": int(answerability.get("deferred_source_needed") or 0),
        "searched_scope_no_evidence_rows": int(answerability.get("searched_scope_no_evidence") or 0),
        "supported_in_searched_scope_rows": int(answerability.get("supported_in_searched_scope") or 0),
        "source_plan_deferred_rows": int(source_plan.get("deferred_rows") or 0),
        "source_plan_retry_guidance_rows": int(source_plan.get("retry_guidance_rows") or 0),
        "source_grouping_selection_changed_rows": int(
            recovery.get("source_grouping_selection_changed_rows") or 0
        ),
        "next_read_recoverable_rows": int(recovery.get("recoverable_rows") or 0),
        "candidate_window_omitted_total": int(recovery.get("candidate_window_omitted_total") or 0),
        "rows_dropped_by_source_cap_total": int(recovery.get("rows_dropped_by_source_cap_total") or 0),
        "next_read_omission_total": int(output_totals.get("next_reads_omitted") or 0),
        "compact_output_omission_total": sum(
            int(output_totals.get(key) or 0)
            for key in ("candidates_omitted", "source_refs_omitted", "source_windows_omitted")
        ),
        "candidate_presence_implies_answerability_rows": int(
            source_summary.get("candidate_presence_implies_answerability_rows") or 0
        ),
        "source_window_stage_pressure_rows": _large_history_stage_status_signal(
            stage_status,
            stage_names={"source_window_packaging"},
            pressure_statuses={"empty", "capped", "omitted", "not_packaged"},
        ),
        "compact_output_stage_pressure_rows": _large_history_stage_status_signal(
            stage_status,
            stage_names={"compact_handoff_output"},
            pressure_statuses={"empty", "capped", "omitted", "not_requested"},
        ),
        "query_plan_hint_rows": _large_history_stage_hint_signal(
            stage_hints,
            needles=("query", "intent", "plan"),
        ),
        "cap_hit_rows": int(cap_hit.get("rows") or 0),
        "fielded_bm25_slow_phase_count": _large_history_slow_fielded_phase_count(fielded_phase_profiles),
    }


def _large_history_stage_status_signal(
    stage_status: dict[str, Any],
    *,
    stage_names: set[str],
    pressure_statuses: set[str],
) -> int:
    total = 0
    for stage_name in stage_names:
        statuses = stage_status.get(stage_name) if isinstance(stage_status.get(stage_name), dict) else {}
        for status, count in statuses.items():
            if str(status) in pressure_statuses:
                total += int(count or 0)
    return total


def _large_history_stage_hint_signal(stage_hints: dict[str, Any], *, needles: tuple[str, ...]) -> int:
    total = 0
    for hints in stage_hints.values():
        if not isinstance(hints, dict):
            continue
        for hint, count in hints.items():
            hint_text = str(hint)
            if any(needle in hint_text for needle in needles):
                total += int(count or 0)
    return total


def _large_history_slow_fielded_phase_count(phase_profiles: dict[str, Any]) -> int:
    total = 0
    for profile in phase_profiles.values():
        if not isinstance(profile, dict) or profile.get("status") != "evaluated":
            continue
        if float(profile.get("median_elapsed_ms") or 0.0) >= 1000.0:
            total += int(profile.get("runs") or 1)
    return total


def _select_large_history_bottleneck(stage_scores: dict[str, int]) -> str:
    non_decision = {
        label: int(stage_scores.get(label) or 0)
        for label in ALLOWED_BOTTLENECK_LABELS
        if label != "no_decision"
    }
    if not any(count > 0 for count in non_decision.values()):
        return "no_decision"
    highest = max(non_decision.values())
    for label in ALLOWED_BOTTLENECK_LABELS:
        if label != "no_decision" and non_decision.get(label) == highest:
            return label
    return "no_decision"


def _large_history_latency_indexing_boundary(
    source_summary: dict[str, Any],
    base_report: dict[str, Any],
) -> dict[str, Any]:
    source_catalog = source_summary.get("source_catalog") if isinstance(source_summary.get("source_catalog"), dict) else {}
    signals = _large_history_stage_signal_counts_without_latency(source_summary, base_report)
    cap_hit_rows = signals["cap_hit_rows"]
    supported_or_deferred = (
        signals["supported_in_searched_scope_rows"] + signals["deferred_source_needed_rows"]
    )
    cases = int(source_summary.get("cases") or 0)
    fielded_phase_profiles = (
        (base_report.get("aggregate") or {}).get("fielded_bm25", {}).get("phase_profiles") or {}
    )
    slow_phase_count = _large_history_slow_fielded_phase_count(fielded_phase_profiles)
    persistent_index_used = bool(source_catalog.get("persistent_index_or_cache_used"))
    retrieval_usefulness_acceptable = cases > 0 and supported_or_deferred >= cases
    cap_or_latency_prevents_stage_reach = bool(
        (signals["searched_scope_no_evidence_rows"] > 0 and cap_hit_rows > 0) or slow_phase_count > 0
    )
    return {
        "selection_rule": (
            "select_indexing_latency_only_when_p95_p99_or_cap_exits_prevent_evidence_quality_stage_reach_or_"
            "materialization_dominates_runtime_while_retrieval_usefulness_is_acceptable"
        ),
        "cap_hit_rows": cap_hit_rows,
        "supported_or_deferred_rows": supported_or_deferred,
        "case_count": cases,
        "retrieval_usefulness_acceptable": retrieval_usefulness_acceptable,
        "cap_or_latency_prevents_stage_reach": cap_or_latency_prevents_stage_reach,
        "fielded_bm25_slow_phase_count": slow_phase_count,
        "persistent_index_or_cache_used": persistent_index_used,
        "indexing_latency_selection_supported": bool(
            retrieval_usefulness_acceptable and cap_or_latency_prevents_stage_reach
        ),
        "indexing_necessity_claim_supported": False,
    }


def _large_history_stage_signal_counts_without_latency(
    source_summary: dict[str, Any],
    base_report: dict[str, Any],
) -> dict[str, int]:
    answerability = source_summary.get("answerability_status_counts") or {}
    cap_hit = source_summary.get("cap_hit") or {}
    return {
        "cap_hit_rows": int(cap_hit.get("rows") or 0),
        "searched_scope_no_evidence_rows": int(answerability.get("searched_scope_no_evidence") or 0),
        "supported_in_searched_scope_rows": int(answerability.get("supported_in_searched_scope") or 0),
        "deferred_source_needed_rows": int(answerability.get("deferred_source_needed") or 0),
    }


def _large_history_stage_bottleneck_summary(
    stage_scores: dict[str, int],
    signal_counts: dict[str, int],
    no_decision_reason: str | None,
) -> dict[str, Any]:
    counts = {label: int(stage_scores.get(label) or 0) for label in ALLOWED_BOTTLENECK_LABELS}
    if no_decision_reason and not any(counts[label] for label in counts if label != "no_decision"):
        counts["no_decision"] = 1
    return {
        "bottleneck_stage_counts": {key: value for key, value in counts.items() if value > 0},
        "signal_counts": dict(sorted(signal_counts.items())),
        "allowed_bottleneck_labels": ALLOWED_BOTTLENECK_LABELS,
        "selection_basis": (
            "aggregate_safe_source_tier_signals_mapped_to_jsh_849_labels_then_"
            "dominant_non_no_decision_by_count_and_allowed_order"
        ),
        **({"no_decision_reason": no_decision_reason} if no_decision_reason else {}),
    }


def _large_history_no_decision_reason(
    source_summary: dict[str, Any],
    stage_scores: dict[str, int],
    latency_boundary: dict[str, Any],
) -> str | None:
    if any(int(stage_scores.get(label) or 0) > 0 for label in ALLOWED_BOTTLENECK_LABELS if label != "no_decision"):
        return None
    if not source_summary.get("full_manifest_run"):
        return "non_full_manifest_pressure_cannot_select_large_history_bottleneck"
    if latency_boundary.get("cap_hit_rows") and not latency_boundary.get("indexing_latency_selection_supported"):
        return "cap_pressure_present_but_aggregate_counters_do_not_show_indexing_latency_as_dominant"
    return "no_dominant_privacy_safe_stage_signal"


def _large_history_tool_delta(selected: str, no_decision_reason: str | None) -> dict[str, Any]:
    mapping = {
        "source_window_choice": {
            "next_tool_delta": "improve_source_window_choice_under_large_history_pressure",
            "implementation_included": False,
        },
        "within_source_depth": {
            "next_tool_delta": "improve_bounded_within_source_depth_or_read_more_coverage",
            "implementation_included": False,
        },
        "query_planning": {
            "next_tool_delta": "improve_query_planning_for_large_history_source_selection",
            "implementation_included": False,
        },
        "output_behavior": {
            "next_tool_delta": "improve_compact_output_behavior_and_defer_boundaries",
            "implementation_included": False,
        },
        "indexing_latency": {
            "next_tool_delta": "evaluate_incremental_or_cached_indexing_for_repeated_large_history_pressure",
            "implementation_included": False,
        },
        "no_decision": {
            "next_tool_delta": "add_missing_privacy_safe_stage_counter_before_tool_delta",
            "implementation_included": False,
        },
    }
    result = dict(mapping.get(selected, mapping["no_decision"]))
    if selected == "no_decision":
        result["reason"] = no_decision_reason or "no_dominant_privacy_safe_stage_signal"
    return result


def _large_history_stage_attribution_privacy() -> dict[str, bool]:
    privacy = _source_tier_answerability_pressure_privacy()
    return {
        **privacy,
        "aggregate_stage_counts_only": True,
        "raw_stage_rows_committed": False,
    }


def _large_history_stage_attribution_claim_boundaries() -> dict[str, bool]:
    return {
        **_source_tier_answerability_pressure_claim_boundaries(),
        "large_history_stage_attribution_only": True,
        "selected_tool_delta_implemented": False,
        "indexing_necessity_claim_supported": False,
        "llm_judge_used": False,
    }


def _source_tier_stage_counters(stage_diagnostics: dict[str, Any], stage_name: str) -> dict[str, Any]:
    for stage in stage_diagnostics.get("stages") or []:
        if isinstance(stage, dict) and stage.get("stage") == stage_name:
            counters = (
                stage.get("counters")
                if isinstance(stage.get("counters"), dict)
                else stage.get("key_counters")
                if isinstance(stage.get("key_counters"), dict)
                else {}
            )
            return {
                "status": _safe_agent_compact_label(stage.get("status")),
                "candidate_window_count": int(counters.get("candidate_window_count") or 0),
                "candidate_window_limit": int(counters.get("candidate_window_limit") or 0),
                "candidate_window_omitted_count": int(counters.get("candidate_window_omitted_count") or 0),
                "distinct_source_count_selected": int(counters.get("distinct_source_count_selected") or 0),
                "source_window_text_included": bool(counters.get("source_window_text_included", False)),
            }
    return {
        "status": "not_evaluated",
        "candidate_window_count": 0,
        "candidate_window_limit": 0,
        "candidate_window_omitted_count": 0,
        "distinct_source_count_selected": 0,
        "source_window_text_included": False,
    }


def _stage_status_counts(stage_diagnostics: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for stage in stage_diagnostics.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        name = _safe_agent_compact_label(stage.get("stage"))
        status = _safe_agent_compact_label(stage.get("status"))
        counts.setdefault(name, {})[status] = counts.setdefault(name, {}).get(status, 0) + 1
    return counts


def _stage_next_fix_hint_counts(stage_diagnostics: dict[str, Any]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for stage in stage_diagnostics.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        name = _safe_agent_compact_label(stage.get("stage"))
        hint = _safe_agent_compact_label(stage.get("next_fix_hint"))
        counts.setdefault(name, {})[hint] = counts.setdefault(name, {}).get(hint, 0) + 1
    return counts


def _sum_count_dicts(values: Any) -> dict[str, int]:
    totals: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            label = str(key)
            if not _safe_counter_label(label):
                continue
            totals[label] = totals.get(label, 0) + int(count or 0)
    return dict(sorted(totals.items()))


def _max_count_dicts(values: Any) -> dict[str, int]:
    maxima: dict[str, int] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, count in value.items():
            label = str(key)
            if not _safe_counter_label(label):
                continue
            maxima[label] = max(maxima.get(label, 0), int(count or 0))
    return dict(sorted(maxima.items()))


def _sum_nested_count_dicts(values: Any) -> dict[str, dict[str, int]]:
    totals: dict[str, dict[str, int]] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for outer_key, inner in value.items():
            outer_label = str(outer_key)
            if not _safe_counter_label(outer_label) or not isinstance(inner, dict):
                continue
            bucket = totals.setdefault(outer_label, {})
            for inner_key, count in inner.items():
                inner_label = str(inner_key)
                if not _safe_counter_label(inner_label):
                    continue
                bucket[inner_label] = bucket.get(inner_label, 0) + int(count or 0)
    return {key: dict(sorted(value.items())) for key, value in sorted(totals.items())}


def _source_tier_answerability_pressure_privacy() -> dict[str, bool]:
    return {
        "aggregate_only": True,
        "raw_source_tier_answerability_detail_committed": False,
        "raw_agent_compact_handoff_detail_committed": False,
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "raw_source_refs_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "local_usernames_included": False,
        "case_ids_required": False,
        "payloads_included": False,
        "vectors_or_indexes_included": False,
    }


def _source_tier_answerability_pressure_claim_boundaries() -> dict[str, bool]:
    return {
        "benchmark_result_only": True,
        "compact_handoff_is_opt_in": True,
        "fielded_bm25_is_explicit_opt_in": True,
        "default_behavior_changed": False,
        "scanner_ranking_or_source_selection_changed": True,
        "token_savings_claim_supported": False,
        "final_answer_quality_claim_supported": False,
        "product_value_claim_supported": False,
        "default_promotion_supported": False,
        "all_history_completeness_claim_supported": False,
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


def _prefilter_policy_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        policy = (row.get("rg_prefilter") or {}).get("needle_policy")
        if isinstance(policy, str) and _safe_counter_label(policy):
            counts[policy] = counts.get(policy, 0) + 1
    return counts


def _prefilter_anchor_kind_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for kind, count in _safe_count_dict((row.get("rg_prefilter") or {}).get("anchor_kind_counts")).items():
            counts[kind] = counts.get(kind, 0) + count
    return counts


def _prefilter_fallback_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        reason = (row.get("rg_prefilter") or {}).get("fallback_reason")
        if isinstance(reason, str) and _safe_counter_label(reason):
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _safe_count_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for key, count in value.items():
        label = str(key)
        if not _safe_counter_label(label):
            continue
        try:
            safe[label] = int(count)
        except (TypeError, ValueError):
            continue
    return safe


def _safe_counter_label(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(ch.islower() or ch.isdigit() or ch == "_" for ch in value)


def _apply_aggregate_only_report(report: dict[str, Any], *, row_count: int) -> None:
    report["rows"] = []
    report["rows_omitted"] = {
        "omitted": True,
        "row_count": row_count,
        "reason": "aggregate_only_report_requested",
        "raw_private_evidence_storage": (report.get("raw_private_evidence") or {}).get("storage"),
        "case_ids_committed": False,
        "raw_queries_committed": False,
        "raw_private_paths_committed": False,
        "raw_anchor_needles_committed": False,
        "raw_compact_handoff_detail_committed": False,
        "raw_agent_compact_handoff_detail_committed": False,
        "raw_source_tier_answerability_detail_committed": False,
        "raw_source_windows_committed": False,
    }
    diagnostic = report.get("structural_sidecar_diagnostic")
    if isinstance(diagnostic, dict) and diagnostic.get("rows"):
        diagnostic["rows_omitted"] = {
            "omitted": True,
            "row_count": len(diagnostic.get("rows") or []),
            "reason": "aggregate_only_report_requested",
        }
        diagnostic["rows"] = []


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
        "package_search_fielded_bm25": "opt-in default-off SQLite FTS5/BM25 record/chunk candidate generator; indexes source-ref-preserving Codex/Claude fields in memory, queries escaped infer_query terms, then passes materialized records through existing Match/grouping conventions; no RRF or default promotion",
        "package_search_rrf_fusion": "opt-in report-only Reciprocal Rank Fusion lane over package_search and package_search_fielded_bm25 ranked candidates; rank-only with rank_constant=60, equal lane weights, stable source-record identity dedupe, no raw-score blending, no score normalization, no fused-score thresholds, and no default promotion",
        "package_search_structural_sidecar": "opt-in structural Codex/Claude JSONL sidecar probe; indexes sanitized event/role/timestamp/session tags plus offsets, then passes reopened records through the existing ranking path",
        "package_search_structural_hybrid": "opt-in package-preserving hybrid probe; keeps package candidates first and appends a bounded non-duplicate structural sidecar supplement",
        "package_search_structural_hybrid_budgeted": "opt-in default-off budgeted hybrid probe; keeps package candidates first and appends a bounded structural supplement when the budgeted gate allows it: package candidate count at or below the non-oracle threshold, or the explicit intent_structural_high_candidate_count gate profile detects sanitized date/timeline/date-window or role intent; does not change caps",
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
        "raw_source_windows_included": False,
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
                "weak_low_evidence_candidates_reservoir_capped",
                "memory_guard_stop",
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
        elif "weak low-evidence candidates were reservoir-capped" in lowered:
            safe.append("weak_low_evidence_candidates_reservoir_capped")
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
