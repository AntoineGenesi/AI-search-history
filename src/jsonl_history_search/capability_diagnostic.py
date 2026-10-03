from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_command_cookbook import agent_question_cookbook
from .evaluation import build_efficiency_eval
from .install_validation import default_package_root, validate_install
from .records import parse_record
from .sources import Limits, build_source_plan, cap_hits_from_warnings, enumerate_jsonl_files


SCHEMA_VERSION = 1


@dataclass
class Capability:
    id: str
    name: str
    category: str
    status: str
    benefit: str
    fallback: str
    expected_slowdown_or_lost_feature: str
    suggested_next_action: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_capability_diagnostic(args) -> dict[str, Any]:
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=1,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    requested_roots = [Path(item).expanduser() for item in args.root] if args.root else None
    source_plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=requested_roots,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )

    validation = validate_install(package_root=default_package_root())
    validation_checks = {
        check["id"]: check for check in validation["install_validation"].get("checks", [])
    }
    capabilities = [
        _core_package_capability(),
        _cli_invocation_capability(),
        _source_planning_capability(source_plan, codex_home=codex_home, claude_home=claude_home),
        _archive_protection_capability(),
        _safe_root_rejection_capability(cwd=cwd, codex_home=codex_home, claude_home=claude_home),
        _validation_capability(
            validation_checks,
            check_id="redaction_availability",
            capability_id="redaction",
            name="Read-only redaction",
            category="privacy",
            benefit="Can redact copied snippets before they are placed in reports or prompts.",
            fallback="unredacted_snippets_with_warning",
            lost_feature="Copied result snippets may need manual review before sharing.",
            next_action="Run validate-install or inspect the redaction profile wiring.",
        ),
        _structured_events_capability(),
        _optional_commands_capability(),
        _benchmark_fixture_capability(Path(args.fixture).expanduser()),
        _rg_capability(),
        _python_json_capability(),
        _manifest_capability(),
        _future_module_capability(
            module_name="jsonl_history_search.timestamp_sidecar",
            capability_id="timestamp_sidecar",
            name="Timestamp sidecar",
            category="future_backend",
            benefit="Can seek date-window and timeline records without scanning entire append-only files.",
            fallback="manifest_date_spans_then_python_scan",
            lost_feature="Date and timeline queries may parse more bytes.",
            next_action="Treat this as roadmap unless a timestamp-sidecar module is added and benchmarked.",
        ),
        _future_module_capability(
            module_name="jsonl_history_search.native_parser",
            capability_id="native_parser",
            name="Native or compiled parser",
            category="future_backend",
            benefit="Can reduce parse elapsed time on very large selected JSONL roots.",
            fallback="python_json_parser",
            lost_feature="Large selected roots may run slower, but source refs stay on the correctness path.",
            next_action="Use the Python parser until a native parser has equivalence tests and version reporting.",
        ),
        _embeddings_capability(),
        _platform_capability(),
        _privacy_guardrail_capability(cwd=cwd, codex_home=codex_home, claude_home=claude_home),
        _report_output_capability(getattr(args, "report_output", None)),
    ]
    summary = _status_summary(capabilities)
    backend_selected, fallback_tier = _backend_selection(capabilities)
    payload = {
        "capability_diagnostic": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "user_home_mutated": False,
            "backend_selected": backend_selected,
            "fallback_tier": fallback_tier,
            "summary": summary,
            "capabilities": {capability.id: capability.to_dict() for capability in capabilities},
            "fallback_tiers": _fallback_tiers(),
            "source_plan": _sanitize_source_plan(source_plan, codex_home=codex_home, claude_home=claude_home),
            "archive_protection": _build_archive_protection_diagnostic(
                source_plan,
                codex_home=codex_home,
                claude_home=claude_home,
            ),
            "archive_protection_gate": _archive_protection_gate(),
            "privacy": {
                "remote_upload": False,
                "raw_text_cache_by_default": False,
                "canonical_transcripts_mutated": False,
                "broad_roots_rejected": _safe_root_rejection_ok(cwd, codex_home, claude_home),
                "private_path_policy": "diagnostic source-plan paths are summarized by label; no transcript text is emitted",
            },
            "benchmark_gates": {
                "default_promotion_ready": False,
                "reason": "Optional backend promotion still needs measured token/tool savings and source-ref equivalence.",
                "rg_prefilter_gate": {
                    "status": "unpromoted",
                    "default_enabled": False,
                    "required_evidence": "exact/path/slug reductions with equivalent Python-parsed source refs and safe fallback",
                },
                "missing_evidence": [
                    "measured_token_savings",
                    "source_ref_equivalence",
                    "backend_specific_files_or_bytes_reduction",
                    "no_worse_first_pass_hit_rate",
                ],
            },
            "agent_command_cookbook": agent_question_cookbook(),
            "examples": [
                {
                    "label": "exact/path query",
                    "theoretical_backend": "future rg prefilter followed by Python parse",
                    "fallback": "pure Python bounded scan",
                },
                {
                    "label": "date-window query",
                    "theoretical_backend": "future timestamp sidecar or manifest date spans followed by Python parse",
                    "fallback": "pure Python bounded scan",
                },
            ],
        }
    }
    return payload


def _core_package_capability() -> Capability:
    modules = [
        "jsonl_history_search",
        "jsonl_history_search.cli",
        "jsonl_history_search.records",
        "jsonl_history_search.scanner",
    ]
    failures: list[str] = []
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - defensive detail
            failures.append(f"{module}: {exc}")
    status = "available" if not failures else "missing"
    return Capability(
        id="core_package",
        name="Core package importability",
        category="core",
        status=status,
        benefit="Enables the baseline bounded source planner and Python JSONL scanner.",
        fallback="none",
        expected_slowdown_or_lost_feature="The CLI cannot run if core imports fail.",
        suggested_next_action=(
            "No action needed." if status == "available" else "Run from the checkout with PYTHONPATH=src or reinstall editable."
        ),
        details={"modules_checked": modules, "failures": failures},
    )


def _cli_invocation_capability() -> Capability:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "jsonl_history_search.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception as exc:
        return Capability(
            id="cli_invocation",
            name="CLI module invocation",
            category="core",
            status="missing",
            benefit="Proves `python -m jsonl_history_search.cli` is reachable without relying on shell aliases.",
            fallback="import_only",
            expected_slowdown_or_lost_feature="Users may need to run from source with PYTHONPATH=src or fix the install.",
            suggested_next_action="Run `PYTHONPATH=src python3 -m jsonl_history_search.cli --help` for the raw error.",
            details={"error": str(exc)},
        )
    status = "available" if completed.returncode == 0 and "jsonl-history-search" in completed.stdout else "missing"
    return Capability(
        id="cli_invocation",
        name="CLI module invocation",
        category="core",
        status=status,
        benefit="Proves the module CLI can start and print command help.",
        fallback="package_import_only",
        expected_slowdown_or_lost_feature="Command-line workflows cannot be trusted until invocation works.",
        suggested_next_action="No action needed." if status == "available" else "Inspect stderr from module invocation.",
        details={
            "returncode": completed.returncode,
            "help_mentions_program": "jsonl-history-search" in completed.stdout,
            "stderr_excerpt": completed.stderr.strip()[:200],
        },
    )


def _source_planning_capability(source_plan, *, codex_home: Path, claude_home: Path) -> Capability:
    roots = source_plan.roots
    readable = [root for root in roots if root.path.exists() and root.path.is_dir() and _is_readable(root.path)]
    readable += [root for root in roots if root.path.exists() and root.path.is_file() and _is_readable(root.path)]
    missing = [root for root in roots if not root.path.exists()]
    if roots and readable:
        status = "available"
        next_action = "No action needed."
    elif roots:
        status = "degraded"
        next_action = "Create or point to readable Codex/Claude transcript roots, or pass an explicit capped root."
    else:
        status = "blocked" if source_plan.rejected_roots else "missing"
        next_action = "Use known Codex/Claude homes or pass --allow-arbitrary-root with hard caps for a selected JSONL root."
    return Capability(
        id="source_planning",
        name="Readable default transcript roots",
        category="core",
        status=status,
        benefit="Shows selected, rejected, missing, and capped roots before any transcript traversal.",
        fallback="plan_only",
        expected_slowdown_or_lost_feature="Search falls back to plan-only output when no readable roots are available.",
        suggested_next_action=next_action,
        details={
            "selected_root_count": len(roots),
            "readable_root_count": len(readable),
            "missing_root_count": len(missing),
            "rejected_root_count": len(source_plan.rejected_roots),
            "roots": [_root_label(root.path, codex_home=codex_home, claude_home=claude_home) for root in roots],
            "limits": source_plan.limits.to_dict(),
        },
    )


def _archive_protection_capability() -> Capability:
    return Capability(
        id="archive_protection_status",
        name="Archive/protection planning status",
        category="future_backend",
        status="not_implemented",
        benefit="Can report whether selected transcript sources have archive protection proof before any backup claim.",
        fallback="diagnostic_only_no_archive_protection",
        expected_slowdown_or_lost_feature=(
            "Selected logs remain unprotected from deletion unless a future snapshot/archive command is implemented "
            "and passes restore/export proof."
        ),
        suggested_next_action="Use archive-snapshot-proof only for the committed synthetic fixture; private/local snapshot work remains future-gated.",
        details={
            "diagnostic_read_only": True,
            "archive_writer_present": False,
            "synthetic_fixture_proof_command_present": True,
            "background_job_present": False,
            "restore_command_present": False,
            "synthetic_restore_smoke_present": True,
            "promotion_gate": "archive_protection_gate",
            "protection_status": "not_protected",
        },
    )


def _safe_root_rejection_capability(*, cwd: Path, codex_home: Path, claude_home: Path) -> Capability:
    ok = _safe_root_rejection_ok(cwd, codex_home, claude_home)
    return Capability(
        id="safe_root_rejection",
        name="Safe-root rejection",
        category="privacy",
        status="available" if ok else "blocked",
        benefit="Rejects broad filesystem roots before traversal.",
        fallback="none",
        expected_slowdown_or_lost_feature="Without this guard, fast backends could accidentally scan too broadly.",
        suggested_next_action="No action needed." if ok else "Inspect source planning broad-root guards before using arbitrary roots.",
        details={"broad_root_probe": "/", "rejected_before_traversal": ok},
    )


def _safe_root_rejection_ok(cwd: Path, codex_home: Path, claude_home: Path) -> bool:
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path("/")],
        allow_arbitrary_root=False,
        limits=Limits(max_files=1, max_bytes=1024, max_matches=1, max_elapsed=1.0),
    )
    return bool(plan.rejected_roots) and not plan.roots


def _validation_capability(
    checks: dict[str, dict[str, Any]],
    *,
    check_id: str,
    capability_id: str,
    name: str,
    category: str,
    benefit: str,
    fallback: str,
    lost_feature: str,
    next_action: str,
) -> Capability:
    check = checks.get(check_id, {})
    status_map = {"pass": "available", "warn": "degraded", "fail": "missing", "skip": "degraded"}
    status = status_map.get(check.get("status"), "missing")
    return Capability(
        id=capability_id,
        name=name,
        category=category,
        status=status,
        benefit=benefit,
        fallback=fallback,
        expected_slowdown_or_lost_feature=lost_feature,
        suggested_next_action="No action needed." if status == "available" else next_action,
        details={"validation_check": check_id, "validation_status": check.get("status")},
    )


def _structured_events_capability() -> Capability:
    try:
        module = importlib.import_module("jsonl_history_search.tool_events")
        available = callable(getattr(module, "scan_tool_events", None)) and callable(
            getattr(module, "build_tool_events_payload", None)
        )
    except Exception:
        available = False
    return Capability(
        id="structured_events",
        name="Structured tool-event support",
        category="core",
        status="available" if available else "missing",
        benefit="Enables deterministic extraction of tool calls, command runs, file edits, skills, and artifacts.",
        fallback="plain_text_search",
        expected_slowdown_or_lost_feature="Tool-use questions lose typed event evidence and become weaker text matches.",
        suggested_next_action="No action needed." if available else "Run focused structured-tool-events tests.",
        details={"command": "structured-tool-events", "detectors_importable": available},
    )


def _optional_commands_capability() -> Capability:
    from .cli import build_parser

    parser = build_parser()
    commands: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands = set(action.choices)
            break
    expected = {
        "manifest",
        "structured-tool-events",
        "workflow-repeat-finder",
        "private-benchmark-mine",
        "benchmark",
        "validate-install",
        "archive-snapshot-proof",
    }
    missing = sorted(expected - commands)
    return Capability(
        id="optional_commands",
        name="Optional command surface",
        category="core",
        status="available" if not missing else "degraded",
        benefit="Exposes specialized diagnostics, benchmark, manifest, and workflow evidence commands.",
        fallback="direct_ask_or_native_inspection",
        expected_slowdown_or_lost_feature="Agents may need extra manual searches when specialized command surfaces are absent.",
        suggested_next_action="No action needed." if not missing else "Restore missing CLI parser entries.",
        details={"expected_commands": sorted(expected), "missing_commands": missing},
    )


def _benchmark_fixture_capability(fixture: Path) -> Capability:
    try:
        payload = build_efficiency_eval(fixture)
        eval_payload = payload["retrieval_efficiency_eval"]
        ok = eval_payload.get("status") != "invalid_fixture"
        details = {
            "fixture": _project_relative_or_name(fixture),
            "status": eval_payload.get("status"),
            "case_count": len(eval_payload.get("cases", [])),
        }
    except Exception as exc:
        ok = False
        details = {"fixture": _project_relative_or_name(fixture), "error": str(exc)}
    return Capability(
        id="benchmark_fixture",
        name="Benchmark fixture availability",
        category="benchmark",
        status="available" if ok else "missing",
        benefit="Provides committed-safe cases for token/tool and backend fallback measurement.",
        fallback="diagnostic_only_no_benchmark_claims",
        expected_slowdown_or_lost_feature="Capability claims remain unproven without fixture-backed counters.",
        suggested_next_action="No action needed." if ok else "Restore or fix the sanitized benchmark fixture.",
        details=details,
    )


def _rg_capability() -> Capability:
    path = shutil.which("rg")
    integration_present = importlib.util.find_spec("jsonl_history_search.rg_prefilter") is not None
    if not path:
        return Capability(
            id="external_text_search_rg",
            name="External text search (`rg`)",
            category="optional_backend",
            status="missing",
            benefit="Can optionally prefilter exact/path/slug searches inside planned roots when explicitly enabled.",
            fallback="python_scan",
            expected_slowdown_or_lost_feature="Exact/path searches may parse more selected JSONL bytes.",
            suggested_next_action="Install ripgrep only if exact/path searches are too slow; Python scan remains supported.",
            details={
                "binary": "rg",
                "available": False,
                "integration_module_present": integration_present,
                "default_enabled": False,
                "used_as_final_evidence": False,
                "promotion_gate": "rg_prefilter_gate",
                "promotion_status": "unpromoted",
            },
        )
    version = ""
    status = "available"
    try:
        completed = subprocess.run([path, "--version"], check=False, capture_output=True, text=True, timeout=2)
        version = completed.stdout.splitlines()[0] if completed.stdout else ""
        if completed.returncode != 0:
            status = "degraded"
    except Exception as exc:
        version = f"version probe failed: {exc}"
        status = "degraded"
    return Capability(
        id="external_text_search_rg",
        name="External text search (`rg`)",
        category="optional_backend",
        status=status,
        benefit="Can optionally prefilter exact/path/slug searches inside planned roots when explicitly enabled.",
        fallback="python_scan",
        expected_slowdown_or_lost_feature="If unavailable or disabled, exact/path searches use pure Python scanning.",
        suggested_next_action=(
            "Use --rg-prefilter only for gated equivalence/reduction checks; Python scan remains the selected backend."
            if status == "available"
            else "Use Python scan or inspect the rg install."
        ),
        details={
            "binary": "rg",
            "path_present": True,
            "version": version,
            "integration_module_present": integration_present,
            "default_enabled": False,
            "used_as_final_evidence": False,
            "promotion_gate": "rg_prefilter_gate",
            "promotion_status": "unpromoted",
            "eligible_query_scope": "exact_path_or_task_slug_only",
        },
    )


def _python_json_capability() -> Capability:
    sample = json.dumps({"timestamp": "2026-06-01T00:00:00+00:00", "role": "user", "text": "diagnostic fixture"})
    record = parse_record(sample, path=Path("diagnostic-fixture.jsonl"), line=1, default_tz=timezone.utc)
    ok = record.timestamp is not None and record.role == "user" and "diagnostic" in record.text
    return Capability(
        id="python_json_parser",
        name="Python JSON/parser baseline",
        category="core",
        status="available" if ok else "missing",
        benefit="Provides the correctness-path parser for source refs, roles, timestamps, and snippets.",
        fallback="raw_line_parse",
        expected_slowdown_or_lost_feature="Structured fields and timestamp ordering degrade if JSON parsing fails.",
        suggested_next_action="No action needed." if ok else "Run parser unit tests and inspect Python JSON support.",
        details={
            "python_version": platform.python_version(),
            "requires_python": ">=3.8",
            "json_module": "stdlib json",
            "fixture_parsed": ok,
        },
    )


def _manifest_capability() -> Capability:
    try:
        module = importlib.import_module("jsonl_history_search.manifest")
        available = callable(getattr(module, "build_manifest_report", None))
    except Exception:
        available = False
    return Capability(
        id="manifest_shortlist",
        name="Manifest shortlist",
        category="optional_backend",
        status="available" if available else "missing",
        benefit="Can reduce selected files/bytes for repeated searches while preserving excluded-scope reporting.",
        fallback="python_scan",
        expected_slowdown_or_lost_feature="Repeated searches may reopen and parse more selected files.",
        suggested_next_action="No action needed." if available else "Restore the manifest module and command.",
        details={"command": "manifest", "persistent_default_cache": False, "raw_text_cache": False},
    )


def _future_module_capability(
    *,
    module_name: str,
    capability_id: str,
    name: str,
    category: str,
    benefit: str,
    fallback: str,
    lost_feature: str,
    next_action: str,
) -> Capability:
    present = importlib.util.find_spec(module_name) is not None
    return Capability(
        id=capability_id,
        name=name,
        category=category,
        status="available" if present else "not_implemented",
        benefit=benefit,
        fallback=fallback,
        expected_slowdown_or_lost_feature=lost_feature,
        suggested_next_action="Verify equivalence tests before enabling." if present else next_action,
        details={"module": module_name, "module_present": present},
    )


def _embeddings_capability() -> Capability:
    dependency_names = ["sentence_transformers", "chromadb", "faiss", "sklearn"]
    installed = [name for name in dependency_names if importlib.util.find_spec(name) is not None]
    integration_present = importlib.util.find_spec("jsonl_history_search.embeddings") is not None
    if integration_present:
        status = "available"
        next_action = "Verify local-only indexing and exact-ref recovery before relying on embedding recall."
    elif installed:
        status = "degraded"
        next_action = "Dependencies are present, but JSONL embedding recall for conversation mining is still owned by jsh-005."
    else:
        status = "not_implemented"
        next_action = "Use exact/fuzzy/local semantic stages until jsh-005 proves local embeddings."
    return Capability(
        id="embedding_retrieval",
        name="Embedding retrieval",
        category="future_backend",
        status=status,
        benefit="Can improve recall for vague or paraphrase-heavy queries after exact source refs are recovered.",
        fallback="exact_fuzzy_local_semantic_search",
        expected_slowdown_or_lost_feature="Vague queries rely on deterministic expansion and fuzzy recall rather than vector retrieval.",
        suggested_next_action=next_action,
        details={
            "integration_module_present": integration_present,
            "installed_optional_dependencies": installed,
            "remote_embedding_default": False,
        },
    )


def _platform_capability() -> Capability:
    system = platform.system() or "unknown"
    return Capability(
        id="platform_paths",
        name="Platform and path support",
        category="environment",
        status="available",
        benefit="Reports platform/path assumptions that affect optional binaries and rooted transcript planning.",
        fallback="pathlib_source_planning",
        expected_slowdown_or_lost_feature="Some optional backend paths may need platform-specific setup.",
        suggested_next_action="No action needed.",
        details={
            "platform": system,
            "python_implementation": platform.python_implementation(),
            "path_module": "pathlib",
            "windows_mount_hint_supported": True,
            "drive_root_rejection_supported": True,
        },
    )


def _privacy_guardrail_capability(*, cwd: Path, codex_home: Path, claude_home: Path) -> Capability:
    ok = _safe_root_rejection_ok(cwd, codex_home, claude_home)
    return Capability(
        id="privacy_guardrails",
        name="Privacy guardrails",
        category="privacy",
        status="available" if ok else "blocked",
        benefit="Keeps diagnostics local, read-only, capped, and source-plan first.",
        fallback="do_not_search",
        expected_slowdown_or_lost_feature="Unsafe roots must be fixed before search or fast-path probing.",
        suggested_next_action="No action needed." if ok else "Fix source-planning guardrails before running searches.",
        details={
            "read_only": True,
            "remote_upload": False,
            "raw_transcript_text_emitted": False,
            "broad_roots_rejected": ok,
        },
    )


def _report_output_capability(report_output: str | None) -> Capability:
    if not report_output:
        return Capability(
            id="report_output",
            name="Explicit report output",
            category="reporting",
            status="available",
            benefit="Can write a machine-readable diagnostic JSON report only when explicitly requested.",
            fallback="stdout_only",
            expected_slowdown_or_lost_feature="No durable diagnostic file is written unless --report-output is supplied.",
            suggested_next_action="Pass --report-output PATH when installer or support tooling needs a report file.",
            details={"requested": False, "surprise_files_written": False},
        )
    path = Path(report_output).expanduser()
    parent = path.parent if str(path.parent) else Path(".")
    writable = parent.exists() and parent.is_dir()
    return Capability(
        id="report_output",
        name="Explicit report output",
        category="reporting",
        status="available" if writable else "blocked",
        benefit="Can write a machine-readable diagnostic JSON report only when explicitly requested.",
        fallback="stdout_only",
        expected_slowdown_or_lost_feature="Report writing fails if the parent path is unavailable.",
        suggested_next_action="No action needed." if writable else "Choose an existing writable parent directory.",
        details={"requested": True, "parent_exists": parent.exists(), "surprise_files_written": False},
    )


def _backend_selection(capabilities: list[Capability]) -> tuple[str, int]:
    by_id = {capability.id: capability for capability in capabilities}
    if by_id["source_planning"].status in {"blocked", "missing"}:
        return "plan_only", 0
    if by_id["python_json_parser"].status == "available" and by_id["core_package"].status == "available":
        return "python_scan", 1
    return "plan_only", 0


def _status_summary(capabilities: list[Capability]) -> dict[str, int]:
    summary = {"available": 0, "degraded": 0, "missing": 0, "blocked": 0, "not_implemented": 0}
    for capability in capabilities:
        summary[capability.status] = summary.get(capability.status, 0) + 1
    return summary


def _fallback_tiers() -> list[dict[str, Any]]:
    return [
        {"tier": 0, "backend_status": "plan_only", "capability_state": "no readable roots or diagnostic only"},
        {"tier": 1, "backend_status": "python_scan", "capability_state": "universal pure Python fallback"},
        {"tier": 2, "backend_status": "text_prefilter", "capability_state": "rg dependency may be present; product backend not promoted"},
        {"tier": 3, "backend_status": "manifest_shortlist", "capability_state": "manifest command available; default cache not selected"},
        {"tier": 4, "backend_status": "timestamp_sidecar", "capability_state": "future optional accelerator"},
        {"tier": 5, "backend_status": "native_parser", "capability_state": "future optional parser"},
        {"tier": 6, "backend_status": "embedding_recall", "capability_state": "future jsh-005-backed recall path"},
    ]


def _build_archive_protection_diagnostic(source_plan, *, codex_home: Path, claude_home: Path) -> dict[str, Any]:
    selected_sources = [
        {
            "label": _root_label(root.path, codex_home=codex_home, claude_home=claude_home),
            "kind": root.kind,
            "known": root.known,
            "recursive": root.recursive,
            "exists": root.path.exists(),
            "readable": root.path.exists() and _is_readable(root.path),
            "mounted_drive": _is_mounted_drive_path(root.path),
        }
        for root in source_plan.roots
    ]
    estimated_file_count = 0
    estimated_bytes = 0
    observed_warnings = list(source_plan.warnings)
    for _path, size, warnings in enumerate_jsonl_files(source_plan):
        observed_warnings = list(warnings)
        estimated_file_count += 1
        estimated_bytes += size
    observed_warnings = list(dict.fromkeys(observed_warnings + source_plan.warnings))
    cap_hits = cap_hits_from_warnings(observed_warnings)
    mounted_drive_warnings = [
        (
            f"{source['label']} is on a mounted drive; future archive snapshots should require explicit confirmation "
            "because traversal and copy cost can be high."
        )
        for source in selected_sources
        if source["mounted_drive"]
    ]
    privacy_warnings = [
        "Archive/protection is not configured; selected logs are not protected from deletion by JSONL.",
        "Selected logs means only the approved source-plan roots/files for this run, not every device or backup.",
        "Future archive snapshots would retain raw private transcripts locally and need explicit opt-in plus restore/export proof.",
        "This diagnostic emits source labels and aggregate counters only; it does not emit transcript text, session IDs, or raw paths.",
    ]
    if not selected_sources:
        privacy_warnings.append("No selected source labels exist for this run, so no logs can be protected or estimated.")
    duplicate_risk_notes = [
        "Duplicate risk is not resolved by this diagnostic; future archive snapshots must detect exact content duplicates and likely mirrored sessions.",
        "Duplicate detection is bounded to selected source labels and cannot prove all-history or all-device completeness.",
    ]
    if len(selected_sources) > 1:
        duplicate_risk_notes.append(
            "Multiple selected source labels can contain mirrored conversation copies; future protection proof must deduplicate before claims."
        )
    return {
        "schema_version": 1,
        "status": "not_configured",
        "protection_status": "not_protected",
        "archive_configured": False,
        "read_only": True,
        "background_behavior": False,
        "archive_writer_present": False,
        "restore_command_present": False,
        "selected_source_labels": [source["label"] for source in selected_sources],
        "selected_sources": selected_sources,
        "estimated_selected_file_count": estimated_file_count,
        "estimated_selected_bytes": estimated_bytes,
        "unprotected_estimated_file_count": estimated_file_count,
        "protected_estimated_file_count": 0,
        "source_deleted_but_archived_count": None,
        "source_deleted_but_archived_status": "unknown_without_archive_ledger",
        "cap_status": cap_hits,
        "warnings": [_sanitize_diagnostic_warning(warning) for warning in observed_warnings],
        "mounted_drive_warnings": mounted_drive_warnings,
        "duplicate_risk_notes": duplicate_risk_notes,
        "privacy_warnings": privacy_warnings,
        "not_searched_scope": [
            "Roots outside the source plan were not inspected.",
            "Files beyond any file, byte, or elapsed-time cap were not estimated.",
            "Deleted, unmounted, inaccessible, or unapproved logs cannot be seen by this diagnostic.",
        ],
    }


def _archive_protection_gate() -> dict[str, Any]:
    return {
        "label": "archive_protection_gate",
        "status": "not_evaluated",
        "promotion_ready": False,
        "claim_allowed": False,
        "required_future_evidence": [
            "snapshot_write_proof",
            "hash_verification",
            "duplicate_detection",
            "corruption_detection",
            "source_deleted_but_archived_status",
            "restore_export_smoke",
            "elapsed_time",
            "bytes_copied",
            "bytes_compressed",
            "index_bytes",
            "privacy_checks",
        ],
        "restore_export_acceptance_test": (
            "Create a synthetic selected-root fixture, run a future explicit snapshot, remove or move the source, "
            "then restore or export the archived records and verify byte/hash/source-ref equivalence without emitting private text."
        ),
        "current_blockers": [
            "no_archive_writer",
            "no_archive_ledger",
            "no_restore_or_export_command",
            "no_snapshot_hash_verification",
            "no_corruption_detection",
        ],
    }


def _sanitize_source_plan(source_plan, *, codex_home: Path, claude_home: Path) -> dict[str, Any]:
    return {
        "selected_root_count": len(source_plan.roots),
        "rejected_root_count": len(source_plan.rejected_roots),
        "missing_root_count": sum(1 for root in source_plan.roots if not root.path.exists()),
        "roots": [
            {
                "kind": root.kind,
                "label": _root_label(root.path, codex_home=codex_home, claude_home=claude_home),
                "recursive": root.recursive,
                "known": root.known,
                "exists": root.path.exists(),
                "readable": root.path.exists() and _is_readable(root.path),
            }
            for root in source_plan.roots
        ],
        "rejected_roots": [
            {"label": _generic_root_label(rejected.path), "reason": rejected.reason}
            for rejected in source_plan.rejected_roots
        ],
        "warnings": [_sanitize_diagnostic_warning(item) for item in source_plan.warnings],
        "blind_spots": [_sanitize_blind_spot(item) for item in source_plan.blind_spots],
        "limits": source_plan.limits.to_dict(),
    }


def _root_label(path: Path, *, codex_home: Path, claude_home: Path) -> str:
    resolved = path.expanduser()
    pairs = [
        (codex_home / "sessions", "$CODEX_HOME/sessions"),
        (codex_home / "history.jsonl", "$CODEX_HOME/history.jsonl"),
        (claude_home / "projects", "$CLAUDE_HOME/projects"),
    ]
    for candidate, label in pairs:
        try:
            if resolved.resolve() == candidate.expanduser().resolve():
                return label
        except OSError:
            continue
    return _generic_root_label(str(path))


def _generic_root_label(path: str) -> str:
    if path in {"/", "/home", "/mnt", "/mnt/c", "/mnt/d"}:
        return path
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    return f"explicit_root:{digest}"


def _sanitize_blind_spot(item: str) -> str:
    if item.startswith("Missing transcript root:"):
        path = item.split(":", 1)[1].strip()
        return f"Missing transcript root: {_generic_root_label(path)}"
    return item


def _sanitize_diagnostic_warning(item: str) -> str:
    if "Arbitrary root enabled for " in item:
        return "Arbitrary root enabled for explicit source label; strict hard caps apply."
    if item.startswith("Skipped symlink escape:"):
        return "Skipped symlink escape under selected source label."
    return _sanitize_blind_spot(item)


def _is_mounted_drive_path(path: Path) -> bool:
    text = str(path.expanduser())
    return text.startswith("/mnt/") or bool(
        len(text) >= 3 and text[0].isalpha() and text[1] == ":" and text[2] in {"/", "\\"}
    )


def _is_readable(path: Path) -> bool:
    try:
        return path.exists() and os.access(path, os.R_OK)
    except OSError:
        return False


def _project_relative_or_name(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(default_package_root().resolve()))
    except ValueError:
        return path.name
