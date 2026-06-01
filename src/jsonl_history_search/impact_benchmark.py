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

from .benchmark import find_private_pattern_hits
from .query import infer_query
from .records import parse_record
from .scanner import search
from .sources import Limits, SourcePlan, build_source_plan, dangerous_reason, enumerate_jsonl_files, safe_resolve
from .timeutil import parse_now, timezone_from_args


DEFAULT_FIXTURE = Path("benchmarks/impact_gold/synthetic_cases.json")
DEFAULT_SYNTHETIC_ROOT = Path("tests/fixtures/impact_benchmark/synthetic_logs")
DEFAULT_BASELINES = ("raw_rg_literal", "terminal_rg_then_open", "package_search", "package_search_rg_prefilter")
VALID_LEGITIMACY = {"synthetic_committed"}
VALID_OUTCOMES = {"evidence_found", "no_evidence"}
VALID_FEATURE_FAMILIES = {
    "broad_generic",
    "date_timeline",
    "exact_path",
    "no_evidence",
    "paraphrase",
}
REQUIRED_CASE_KEYS = {
    "case_id",
    "legitimacy_status",
    "source_set_label",
    "allowed_query_wordings",
    "gold_refs",
    "gold_doc_ids",
    "expected_outcome",
    "success_criteria",
    "protected_feature_family",
}


@dataclass(frozen=True)
class ImpactFile:
    path: Path
    label: str
    size: int


@dataclass(frozen=True)
class LiteralCandidate:
    doc_id: str | None
    file_label: str
    line: int
    byte_count: int


def load_impact_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_impact_fixture(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if payload.get("data_source") != "synthetic_committed_only":
        errors.append("data_source must be synthetic_committed_only")
    if payload.get("raw_private_text_committed") is not False:
        errors.append("raw_private_text_committed must be false")

    source_sets = payload.get("source_sets")
    if not isinstance(source_sets, list) or not source_sets:
        errors.append("source_sets must be a non-empty list")
    else:
        for index, source_set in enumerate(source_sets):
            label = source_set.get("label") if isinstance(source_set, dict) else None
            if not isinstance(label, str) or not label:
                errors.append(f"source_sets[{index}].label is required")
            roots = source_set.get("fixture_roots") if isinstance(source_set, dict) else None
            if not isinstance(roots, list) or not all(isinstance(root, str) and root for root in roots):
                errors.append(f"source_sets[{index}].fixture_roots must be a non-empty string list")

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    seen_families: set[str] = set()
    source_set_items = source_sets if isinstance(source_sets, list) else []
    source_set_labels = {
        source_set.get("label")
        for source_set in source_set_items
        if isinstance(source_set, dict)
    }
    for index, case in enumerate(cases):
        label = case.get("case_id") if isinstance(case, dict) else f"case[{index}]"
        if not isinstance(case, dict):
            errors.append(f"case[{index}] must be an object")
            continue
        missing = REQUIRED_CASE_KEYS - set(case)
        if missing:
            errors.append(f"{label}: missing required keys {sorted(missing)}")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            errors.append(f"{label}: case_id is required")
        elif case_id in seen_ids:
            errors.append(f"{label}: duplicate case_id")
        else:
            seen_ids.add(case_id)
        if case.get("legitimacy_status") not in VALID_LEGITIMACY:
            errors.append(f"{label}: legitimacy_status must be synthetic_committed")
        if case.get("source_set_label") not in source_set_labels:
            errors.append(f"{label}: source_set_label must reference source_sets")
        wordings = case.get("allowed_query_wordings")
        if not isinstance(wordings, list) or not wordings or not all(isinstance(item, str) and item for item in wordings):
            errors.append(f"{label}: allowed_query_wordings must be a non-empty string list")
        outcome = case.get("expected_outcome")
        if outcome not in VALID_OUTCOMES:
            errors.append(f"{label}: expected_outcome must be one of {sorted(VALID_OUTCOMES)}")
        family = case.get("protected_feature_family")
        if family not in VALID_FEATURE_FAMILIES:
            errors.append(f"{label}: protected_feature_family must be one of {sorted(VALID_FEATURE_FAMILIES)}")
        else:
            seen_families.add(family)
        criteria = case.get("success_criteria")
        if not isinstance(criteria, list) or not criteria or not all(isinstance(item, str) and item for item in criteria):
            errors.append(f"{label}: success_criteria must be a non-empty string list")
        gold_doc_ids = case.get("gold_doc_ids")
        if outcome == "no_evidence":
            if gold_doc_ids not in ([], None):
                errors.append(f"{label}: no_evidence cases must not declare gold_doc_ids")
        elif not isinstance(gold_doc_ids, list) or not gold_doc_ids or not all(isinstance(item, str) and item for item in gold_doc_ids):
            errors.append(f"{label}: evidence cases require gold_doc_ids")
        refs = case.get("gold_refs")
        if outcome == "no_evidence":
            if refs not in ([], None):
                errors.append(f"{label}: no_evidence cases must not declare gold_refs")
        elif not isinstance(refs, list) or not refs:
            errors.append(f"{label}: evidence cases require gold_refs")
        else:
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, dict):
                    errors.append(f"{label}.gold_refs[{ref_index}] must be an object")
                    continue
                if ref.get("doc_id") not in set(gold_doc_ids or []):
                    errors.append(f"{label}.gold_refs[{ref_index}] doc_id must be in gold_doc_ids")
                if not isinstance(ref.get("source_ref"), str) or not ref["source_ref"]:
                    errors.append(f"{label}.gold_refs[{ref_index}] source_ref is required")

    missing_families = VALID_FEATURE_FAMILIES - seen_families
    if missing_families:
        errors.append(f"missing protected feature family coverage: {sorted(missing_families)}")
    for hit in find_private_pattern_hits(payload):
        errors.append(f"private pattern found at {hit}")
    return errors


def build_impact_benchmark_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    fixture_path = Path(args.fixture).expanduser()
    try:
        fixture = load_impact_fixture(fixture_path)
    except OSError:
        return {"impact_benchmark": _invalid_report("fixture could not be read", started)}
    except json.JSONDecodeError:
        return {"impact_benchmark": _invalid_report("fixture is not valid JSON", started)}
    fixture_errors = validate_impact_fixture(fixture)
    if fixture_errors:
        return {"impact_benchmark": _invalid_report("fixture validation failed", started, fixture_errors)}

    explicit_roots = [Path(item).expanduser() for item in args.root]
    roots = explicit_roots or _default_fixture_roots(fixture)
    limits = _limits_from_args(args, default_roots=not explicit_roots)
    cwd = Path(args.cwd).expanduser().resolve()
    root_labels = [f"root_{index + 1:03d}" for index, _root in enumerate(roots)]
    base_report = _base_report(fixture, limits, root_labels, started)
    preflight_warnings = _preflight_root_warnings(roots, cwd)
    if preflight_warnings:
        return {"impact_benchmark": _finish_without_scan(base_report, "root_rejected", preflight_warnings, started)}

    plan = build_source_plan(
        cwd=cwd,
        codex_home=Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex",
        claude_home=Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude",
        roots=roots,
        allow_arbitrary_root=bool(args.allow_arbitrary_root or not explicit_roots),
        limits=limits,
    )
    if plan.rejected_roots or not plan.roots:
        warnings = _safe_warnings(plan.warnings + [root.reason for root in plan.rejected_roots] + plan.blind_spots)
        return {"impact_benchmark": _finish_without_scan(base_report, "root_rejected", warnings, started)}

    selected = _selected_baselines(args.baseline)
    files, enum_warnings = _enumerate_impact_files(plan)
    if enum_warnings:
        warnings = _safe_warnings(enum_warnings)
        if not explicit_roots:
            warnings = [warning for warning in warnings if warning != "arbitrary_root_enabled_with_hard_caps"]
        base_report["warnings"] = warnings
    doc_index = _build_doc_index(files, timezone_from_args(args.timezone, args.timezone_offset))
    rows: list[dict[str, Any]] = []
    for case in fixture["cases"][: args.case_limit if args.case_limit > 0 else None]:
        for baseline in selected:
            runner = {
                "raw_rg_literal": _run_raw_rg_literal,
                "terminal_rg_then_open": _run_terminal_rg_then_open,
                "package_search": _run_package_search,
                "package_search_rg_prefilter": _run_package_search_rg_prefilter,
            }[baseline]
            for repeat_index in range(max(1, args.repeat)):
                rows.append(
                    runner(
                        case=case,
                        files=files,
                        doc_index=doc_index,
                        plan=plan,
                        args=args,
                        repeat_index=repeat_index,
                    )
                )

    base_report["status"] = "ok"
    base_report["rows"] = rows
    base_report["summary"] = _summarize(rows, selected)
    base_report["total_wall_time_ms"] = _elapsed_ms(started)
    return {"impact_benchmark": base_report}


def _invalid_report(reason: str, started: float, fixture_errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "invalid_fixture",
        "data_source": "synthetic_committed_only",
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [reason],
        "fixture_errors": fixture_errors or [],
        "rows": [],
        "summary": {},
        "privacy_contract": _privacy_contract(),
    }


def _base_report(fixture: dict[str, Any], limits: Limits, root_labels: list[str], started: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex[:12],
        "status": "pending",
        "data_source": "synthetic_committed_only",
        "source_set_labels": sorted({case["source_set_label"] for case in fixture["cases"]}),
        "root_labels": root_labels,
        "limits": limits.to_dict(),
        "baseline_contract": {
            "raw_rg_literal": "fixed literal scan over capped fixture files; no JSH query expansion or ranking",
            "terminal_rg_then_open": "fixed literal scan plus bounded top-line file opens; no JSH query expansion or ranking",
            "package_search": "current JSONL compatibility CLI infer_query/search path",
            "package_search_rg_prefilter": "optional rg prefilter over capped planned JSONL files, followed by Python JSON parsing for evidence",
        },
        "privacy_contract": _privacy_contract(),
        "total_wall_time_ms": _elapsed_ms(started),
        "warnings": [],
        "rows": [],
        "summary": {},
    }


def _finish_without_scan(report: dict[str, Any], status: str, warnings: list[str], started: float) -> dict[str, Any]:
    report["status"] = status
    report["total_wall_time_ms"] = _elapsed_ms(started)
    report["warnings"] = _safe_warnings(warnings)
    return report


def _default_fixture_roots(fixture: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    for source_set in fixture.get("source_sets", []):
        for root in source_set.get("fixture_roots", []):
            roots.append(Path(root))
    return roots or [DEFAULT_SYNTHETIC_ROOT]


def _limits_from_args(args, *, default_roots: bool) -> Limits:
    hard_cap_files = args.hard_cap_files
    hard_cap_bytes = args.hard_cap_bytes
    if default_roots:
        hard_cap_files = hard_cap_files or args.max_files
        hard_cap_bytes = hard_cap_bytes or args.max_bytes
    return Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=hard_cap_files,
        hard_cap_bytes=hard_cap_bytes,
    )


def _preflight_root_warnings(roots: list[Path], cwd: Path) -> list[str]:
    warnings = []
    for index, root in enumerate(roots):
        reason = dangerous_reason(safe_resolve(root), cwd)
        if reason:
            warnings.append(f"root_{index + 1:03d} rejected: {reason}")
    return warnings


def _enumerate_impact_files(plan: SourcePlan) -> tuple[list[ImpactFile], list[str]]:
    files: list[ImpactFile] = []
    warnings: list[str] = []
    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = list(enum_warnings)
        files.append(ImpactFile(path=path, label=f"file_{len(files) + 1:03d}", size=size))
    return files, warnings


def _build_doc_index(files: list[ImpactFile], default_tz) -> dict[tuple[str, int], str]:
    index: dict[tuple[str, int], str] = {}
    for item in files:
        try:
            lines = item.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            record = parse_record(line, path=item.path, line=line_number, default_tz=default_tz)
            doc_id = record.raw.get("doc_id") if isinstance(record.raw, dict) else None
            if isinstance(doc_id, str) and doc_id:
                index[(str(item.path), line_number)] = doc_id
    return index


def _run_raw_rg_literal(
    *,
    case: dict[str, Any],
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
    plan: SourcePlan,
    args,
    repeat_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    query = _case_query(case)
    candidates, files_opened, bytes_scanned, subprocess_calls = _literal_candidates(query, files, doc_index)
    return _row(
        case=case,
        baseline="raw_rg_literal",
        repeat_index=repeat_index,
        started=started,
        tool_calls=1,
        subprocess_calls=subprocess_calls,
        files_opened=files_opened,
        bytes_scanned=bytes_scanned,
        bytes_parsed=0,
        output_context_bytes=sum(candidate.byte_count for candidate in candidates[: plan.limits.max_matches]),
        candidates=candidates[: plan.limits.max_matches],
    )


def _run_terminal_rg_then_open(
    *,
    case: dict[str, Any],
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
    plan: SourcePlan,
    args,
    repeat_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    query = _case_query(case)
    candidates, files_opened, bytes_scanned, subprocess_calls = _literal_candidates(query, files, doc_index)
    opened = _opened_context_bytes(candidates[: args.terminal_open_limit], files, context_lines=args.context_lines)
    return _row(
        case=case,
        baseline="terminal_rg_then_open",
        repeat_index=repeat_index,
        started=started,
        tool_calls=1 + opened["unique_files_opened"],
        subprocess_calls=subprocess_calls,
        files_opened=files_opened + opened["unique_files_opened"],
        bytes_scanned=bytes_scanned,
        bytes_parsed=0,
        output_context_bytes=opened["context_bytes"],
        candidates=candidates[: plan.limits.max_matches],
    )


def _run_package_search(
    *,
    case: dict[str, Any],
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
    plan: SourcePlan,
    args,
    repeat_index: int,
) -> dict[str, Any]:
    return _run_package_search_variant(
        case=case,
        files=files,
        doc_index=doc_index,
        plan=plan,
        args=args,
        repeat_index=repeat_index,
        baseline="package_search",
        use_rg_prefilter=False,
    )


def _run_package_search_rg_prefilter(
    *,
    case: dict[str, Any],
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
    plan: SourcePlan,
    args,
    repeat_index: int,
) -> dict[str, Any]:
    return _run_package_search_variant(
        case=case,
        files=files,
        doc_index=doc_index,
        plan=plan,
        args=args,
        repeat_index=repeat_index,
        baseline="package_search_rg_prefilter",
        use_rg_prefilter=True,
    )


def _run_package_search_variant(
    *,
    case: dict[str, Any],
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
    plan: SourcePlan,
    args,
    repeat_index: int,
    baseline: str,
    use_rg_prefilter: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    default_tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, default_tz)
    intent = infer_query(_case_query(case), now=now)
    result = search(
        plan,
        intent,
        default_tz=default_tz,
        cwd=str(Path(args.cwd).expanduser().resolve()),
        use_rg_prefilter=use_rg_prefilter,
    )
    prefilter = _prefilter_row_stats(result, enabled=use_rg_prefilter)
    candidates: list[LiteralCandidate] = []
    for group in result.get("groups", []):
        for match in group.get("matches", []):
            doc_id = doc_index.get((match.get("path", ""), int(match.get("line", 0))))
            candidates.append(
                LiteralCandidate(
                    doc_id=doc_id,
                    file_label="file_redacted",
                    line=int(match.get("line", 0)),
                    byte_count=len(json.dumps(match, ensure_ascii=False).encode("utf-8")),
                )
            )
    return _row(
        case=case,
        baseline=baseline,
        repeat_index=repeat_index,
        started=started,
        tool_calls=1,
        subprocess_calls=1 if prefilter.get("attempted") else 0,
        files_opened=int(result.get("searched_files", 0)),
        bytes_scanned=int(result.get("searched_bytes", 0)),
        bytes_parsed=int(result.get("searched_bytes", 0)),
        output_context_bytes=len(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        candidates=candidates,
        rg_prefilter=prefilter,
    )


def _literal_candidates(
    query: str,
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
) -> tuple[list[LiteralCandidate], int, int, int]:
    if shutil.which("rg"):
        candidates, files_opened, bytes_scanned, subprocess_calls = _rg_literal_candidates(query, files, doc_index)
        return _stable_literal_candidates(candidates), files_opened, bytes_scanned, subprocess_calls
    candidates, files_opened, bytes_scanned = _python_literal_candidates(query, files, doc_index)
    return _stable_literal_candidates(candidates), files_opened, bytes_scanned, 0


def _stable_literal_candidates(candidates: list[LiteralCandidate]) -> list[LiteralCandidate]:
    return sorted(candidates, key=lambda item: (item.file_label, item.line, item.doc_id or ""))


def _rg_literal_candidates(
    query: str,
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
) -> tuple[list[LiteralCandidate], int, int, int]:
    if not files:
        return [], 0, 0, 1
    command = ["rg", "--line-number", "--fixed-strings", "--ignore-case", "--", query]
    command.extend(str(item.path) for item in files)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode not in {0, 1}:
        candidates, files_opened, bytes_scanned = _python_literal_candidates(query, files, doc_index)
        return candidates, files_opened, bytes_scanned, 0
    file_by_path = {str(item.path): item for item in files}
    candidates: list[LiteralCandidate] = []
    for line in completed.stdout.splitlines():
        path_text, line_text, match_text = _parse_rg_line(line)
        item = file_by_path.get(path_text)
        if item is None or line_text is None:
            continue
        candidates.append(
            LiteralCandidate(
                doc_id=doc_index.get((path_text, line_text)),
                file_label=item.label,
                line=line_text,
                byte_count=len(match_text.encode("utf-8")),
            )
        )
    return candidates, len(files), sum(item.size for item in files), 1


def _parse_rg_line(line: str) -> tuple[str, int | None, str]:
    path_text, sep, rest = line.partition(":")
    if not sep:
        return line, None, ""
    line_text, sep, match_text = rest.partition(":")
    if not sep:
        return path_text, None, rest
    try:
        return path_text, int(line_text), match_text
    except ValueError:
        return path_text, None, match_text


def _python_literal_candidates(
    query: str,
    files: list[ImpactFile],
    doc_index: dict[tuple[str, int], str],
) -> tuple[list[LiteralCandidate], int, int]:
    needle = query.lower()
    candidates: list[LiteralCandidate] = []
    files_opened = 0
    bytes_scanned = 0
    for item in files:
        try:
            lines = item.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        files_opened += 1
        bytes_scanned += item.size
        for line_number, line in enumerate(lines, start=1):
            if needle in line.lower():
                candidates.append(
                    LiteralCandidate(
                        doc_id=doc_index.get((str(item.path), line_number)),
                        file_label=item.label,
                        line=line_number,
                        byte_count=len(line.encode("utf-8")),
                    )
                )
    return candidates, files_opened, bytes_scanned


def _opened_context_bytes(candidates: list[LiteralCandidate], files: list[ImpactFile], *, context_lines: int) -> dict[str, int]:
    files_by_label = {item.label: item for item in files}
    opened_labels = []
    context_bytes = 0
    for candidate in candidates:
        item = files_by_label.get(candidate.file_label)
        if item is None:
            continue
        opened_labels.append(candidate.file_label)
        try:
            lines = item.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        start = max(0, candidate.line - context_lines - 1)
        end = min(len(lines), candidate.line + context_lines)
        context_bytes += len("\n".join(lines[start:end]).encode("utf-8"))
    return {"unique_files_opened": len(set(opened_labels)), "context_bytes": context_bytes}


def _row(
    *,
    case: dict[str, Any],
    baseline: str,
    repeat_index: int,
    started: float,
    tool_calls: int,
    subprocess_calls: int,
    files_opened: int,
    bytes_scanned: int,
    bytes_parsed: int,
    output_context_bytes: int,
    candidates: list[LiteralCandidate],
    rg_prefilter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gold = set(case.get("gold_doc_ids") or [])
    candidate_doc_ids = [candidate.doc_id for candidate in candidates if candidate.doc_id]
    expected_no_evidence = case["expected_outcome"] == "no_evidence"
    first_pass_hit = bool(candidate_doc_ids and candidate_doc_ids[0] in gold)
    no_evidence_correct = bool(expected_no_evidence and not candidates)
    return {
        "case_id": case["case_id"],
        "baseline": baseline,
        "repeat_index": repeat_index,
        "legitimacy_status": case["legitimacy_status"],
        "source_set_label": case["source_set_label"],
        "expected_outcome": case["expected_outcome"],
        "protected_feature_family": case["protected_feature_family"],
        "success_criteria": list(case.get("success_criteria") or []),
        "wall_time_ms": _elapsed_ms(started),
        "tool_calls": tool_calls,
        "subprocess_calls": subprocess_calls,
        "files_opened": files_opened,
        "bytes_scanned": bytes_scanned,
        "bytes_parsed": bytes_parsed,
        "output_context_bytes": output_context_bytes,
        "candidate_count": len(candidates),
        "first_pass_hit": first_pass_hit,
        "recall_at_1": _recall_at(candidate_doc_ids, gold, 1, expected_no_evidence),
        "recall_at_3": _recall_at(candidate_doc_ids, gold, 3, expected_no_evidence),
        "recall_at_10": _recall_at(candidate_doc_ids, gold, 10, expected_no_evidence),
        "no_evidence_correct": no_evidence_correct if expected_no_evidence else None,
        "top_candidate_doc_ids": candidate_doc_ids[:10],
        "top_candidate_source_refs": _sanitized_source_refs(candidates[:10]),
        "rg_prefilter": rg_prefilter or _prefilter_row_stats({}, enabled=False),
    }


def _recall_at(candidate_doc_ids: list[str], gold: set[str], k: int, expected_no_evidence: bool) -> float:
    if expected_no_evidence:
        return 1.0 if not candidate_doc_ids else 0.0
    if not gold:
        return 0.0
    return round(len(set(candidate_doc_ids[:k]) & gold) / len(gold), 3)


def _sanitized_source_refs(candidates: list[LiteralCandidate]) -> list[str]:
    return [f"{candidate.doc_id or 'unknown'}:{candidate.line}" for candidate in candidates]


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
            "no_evidence_correct": sum(1 for row in no_evidence_rows if row["no_evidence_correct"]),
            "no_evidence_cases": len(no_evidence_rows),
            "median_wall_time_ms": _median(row["wall_time_ms"] for row in selected),
            "median_tool_calls": _median(row["tool_calls"] for row in selected),
            "median_subprocess_calls": _median(row["subprocess_calls"] for row in selected),
            "median_files_opened": _median(row["files_opened"] for row in selected),
            "median_bytes_scanned": _median(row["bytes_scanned"] for row in selected),
            "median_bytes_parsed": _median(row["bytes_parsed"] for row in selected),
            "median_output_context_bytes": _median(row["output_context_bytes"] for row in selected),
            "median_candidate_count": _median(row["candidate_count"] for row in selected),
            "median_prefilter_files_reduced": _median(row["rg_prefilter"]["files_reduced"] for row in selected),
            "median_prefilter_bytes_reduced": _median(row["rg_prefilter"]["bytes_reduced"] for row in selected),
            "median_prefilter_files_reduction_ratio": _median(
                row["rg_prefilter"]["files_reduction_ratio"] for row in selected
            ),
            "median_prefilter_bytes_reduction_ratio": _median(
                row["rg_prefilter"]["bytes_reduction_ratio"] for row in selected
            ),
        }
    if "package_search" in summary and "package_search_rg_prefilter" in summary:
        summary["package_search_rg_prefilter"]["equivalence_vs_package_search"] = _package_equivalence(rows)
    return summary


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
        if row["top_candidate_doc_ids"] == baseline["top_candidate_doc_ids"]:
            doc_equivalent += 1
        if row["top_candidate_source_refs"] == baseline["top_candidate_source_refs"]:
            ref_equivalent += 1
    compared = len(rg_rows)
    return {
        "compared_runs": compared,
        "equivalent_top_candidate_doc_ids": doc_equivalent,
        "equivalent_top_candidate_source_refs": ref_equivalent,
        "all_equivalent": bool(compared and doc_equivalent == compared and ref_equivalent == compared),
    }


def _selected_baselines(requested: list[str]) -> tuple[str, ...]:
    return tuple(requested or DEFAULT_BASELINES)


def _case_query(case: dict[str, Any]) -> str:
    return str(case["allowed_query_wordings"][0])


def _mean(values: Any) -> float:
    items = list(values)
    return round(sum(items) / len(items), 3) if items else 0.0


def _median(values: Any) -> float:
    items = list(values)
    return round(float(statistics.median(items)), 3) if items else 0.0


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def _safe_warnings(warnings: list[str]) -> list[str]:
    safe: list[str] = []
    for warning in warnings:
        lowered = warning.lower()
        if lowered.startswith(("root_rejected_", "stopped_at_", "missing_root")):
            safe.append(warning)
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
        elif "arbitrary root enabled" in lowered:
            safe.append("arbitrary_root_enabled_with_hard_caps")
        elif "not a known codex/claude transcript root" in lowered:
            safe.append("root_rejected_unknown_without_override")
        elif "max elapsed cap" in lowered:
            safe.append("stopped_at_max_elapsed_cap")
        elif "max file cap" in lowered:
            safe.append("stopped_at_max_file_cap")
        elif "max byte cap" in lowered:
            safe.append("stopped_at_max_byte_cap")
        elif "missing transcript root" in lowered:
            safe.append("missing_root")
        else:
            safe.append("warning_redacted")
    return sorted(set(safe))


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_paths_included": False,
        "local_usernames_included": False,
        "synthetic_committed_only": True,
    }
