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
    return {"measured_benchmark": base_report}


def _run_probe(*, probe: MeasuredProbe, run_id: str, plan: SourcePlan, default_tz, now, cwd: str) -> dict[str, Any]:
    started = time.perf_counter()
    intent = infer_query(probe.query_text, now=now)
    result = search(plan, intent, default_tz=default_tz, cwd=cwd)
    warnings = _safe_warnings(result.get("warnings", []))
    candidate_count = sum(len(group.get("matches", [])) for group in result.get("groups", []))
    row = {
        "run_id": run_id,
        "case_id": probe.row_id,
        "query_source": probe.query_source,
        "elapsed_ms": _elapsed_ms(started),
        "searched_files": int(result.get("searched_files", 0)),
        "searched_bytes": int(result.get("searched_bytes", 0)),
        "candidate_count": candidate_count,
        "result_status": _result_status(candidate_count, warnings),
        "output_context_bytes": 0,
        "warnings": warnings,
    }
    row["output_context_bytes"] = len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8"))
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
    return {
        "run_id": run_id,
        "overall_status": "capped" if statuses.get("capped") else "ok",
        "total_elapsed_ms": total_elapsed_ms,
        "searched_files": sum(int(row.get("searched_files", 0)) for row in rows),
        "searched_bytes": sum(int(row.get("searched_bytes", 0)) for row in rows),
        "candidate_count": sum(int(row.get("candidate_count", 0)) for row in rows),
        "output_context_bytes": sum(int(row.get("output_context_bytes", 0)) for row in rows),
        "statuses": statuses,
        "limits": _limits_payload(limits, case_limit=case_limit),
        "root_labels": root_labels,
        "warnings": sorted(warnings),
    }


def _empty_aggregate(run_id: str, limits: Limits, root_labels: list[str], case_limit: int) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "overall_status": "pending",
        "total_elapsed_ms": 0,
        "searched_files": 0,
        "searched_bytes": 0,
        "candidate_count": 0,
        "output_context_bytes": 0,
        "statuses": {},
        "limits": _limits_payload(limits, case_limit=case_limit),
        "root_labels": root_labels,
        "warnings": [],
    }


def _finish_without_scan(report: dict[str, Any], *, status: str, warnings: list[str], started: float) -> dict[str, Any]:
    safe = _safe_warnings(warnings)
    report["status"] = status
    report["aggregate"] = {
        **report["aggregate"],
        "overall_status": status,
        "total_elapsed_ms": _elapsed_ms(started),
        "statuses": {status: 1},
        "warnings": safe,
    }
    return {"measured_benchmark": report}


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
