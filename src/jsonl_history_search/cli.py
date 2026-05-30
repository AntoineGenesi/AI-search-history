from __future__ import annotations

import argparse
from pathlib import Path

from .agent_contract import build_agent_find_payload
from .evaluation import build_efficiency_eval
from .forensics import build_forensics_payload
from .install_validation import validate_install
from .manifest import build_manifest_report
from .output import render
from .query import infer_query
from .redaction import RedactionContext
from .scanner import search
from .sources import Limits, build_source_plan
from .timeutil import parse_now, timezone_from_args
from .masking import MASK_MODES, MaskContext


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jsonl-history-search")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "search"):
        sub = subparsers.add_parser(name)
        sub.add_argument("query", nargs="?", default="")
        add_common_args(sub)
    for name in ("find", "plan-sources", "broaden-query", "find-candidates", "timeline", "cluster-candidates", "no-evidence-report"):
        find = subparsers.add_parser(name)
        find.add_argument("query", nargs="?", default="")
        add_common_args(find)
        find.add_argument(
            "--include-manifest",
            action="store_true",
            help="Include optional local manifest shortlist metadata for no-evidence and source-planning sections.",
        )
        find.add_argument(
            "--report-output",
            default=None,
            help="Optional path where the full find report should be written as JSON or Markdown.",
        )
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("query", nargs="?", default="")
    add_common_args(manifest)
    manifest.add_argument(
        "--shortlist-limit",
        type=int,
        default=None,
        help="Optional maximum manifest entries to keep in the shortlist report.",
    )
    forensics = subparsers.add_parser("forensics")
    add_common_args(forensics)
    forensics.add_argument("--commit", default=None)
    forensics.add_argument("--path", default=None)
    forensics.add_argument("--task", default=None)
    forensics.add_argument("--branch", default=None)
    forensics.add_argument("--around", default=None)
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument(
        "--fixture",
        default="benchmarks/retrieval_gold/sanitized_cases.json",
        help="Sanitized retrieval benchmark fixture to evaluate.",
    )
    benchmark.add_argument("--format", choices=("json", "markdown"), default="markdown")
    benchmark.add_argument(
        "--strategy",
        action="append",
        choices=("package_search", "helper_script", "raw_rg", "naive_broad_scan"),
        default=[],
        help="Strategy to include; may be passed more than once. Defaults to all strategies.",
    )
    benchmark.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Exit non-zero when large-impact thresholds fail.",
    )
    validate = subparsers.add_parser("validate-install")
    validate.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    validate.add_argument(
        "--package-root",
        default=None,
        help="Package checkout root to validate; defaults to the installed/source package root.",
    )
    validate.add_argument(
        "--codex-home",
        default=None,
        help="Optional Codex home whose global jsonl-history-search skill install should be checked read-only.",
    )
    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--claude-home", default=None)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--role", default=None)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    parser.add_argument("--max-matches", type=int, default=40)
    parser.add_argument("--max-elapsed", type=float, default=15.0)
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--timezone-offset", default="+00:00")
    parser.add_argument("--now", default=None)
    parser.add_argument("--allow-arbitrary-root", action="store_true")
    parser.add_argument("--hard-cap-files", type=int, default=None)
    parser.add_argument("--hard-cap-bytes", type=int, default=None)
    parser.add_argument(
        "--mask-manifest",
        default=None,
        help="False-positive masking manifest for future copied-context search snippets",
    )
    parser.add_argument("--mask-mode", choices=sorted(MASK_MODES), default="production_collapse")
    parser.add_argument(
        "--redaction-profile",
        default=None,
        help="JSON profile for read-only snippet redaction of common PII/secrets and custom regexes",
    )


def payload_from_args(args: argparse.Namespace) -> tuple[dict, object, object]:
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    intent = infer_query(args.query, now=now, role=args.role)
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path(item).expanduser() for item in args.root] if args.root else None,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "now": now.isoformat(),
    }
    if args.mask_manifest:
        source_plan["masking"] = {
            "manifest_path": str(Path(args.mask_manifest).expanduser()) if args.mask_manifest else None,
            "mode": args.mask_mode,
            "scope": "future_copied_context_snippets_only",
            "canonical_transcripts_mutated": False,
        }
    if args.redaction_profile:
        redaction_path = Path(args.redaction_profile).expanduser()
        source_plan["redaction"] = _redaction_source_metadata(
            redaction_path,
            RedactionContext.from_path(redaction_path),
        )
    return source_plan, intent, plan


def _redaction_source_metadata(path: Path, context: RedactionContext) -> dict:
    profiles = [name for name in context.stats.profiles if not name.startswith("custom:")]
    custom_count = sum(1 for name in context.stats.profiles if name.startswith("custom:"))
    return {
        "profile_path": str(path),
        "mode": context.stats.mode,
        "profiles": sorted(set(profiles)),
        "custom_count": custom_count,
        "warnings": list(context.stats.warnings),
        "scope": "future_copied_context_snippets_only",
        "canonical_transcripts_mutated": False,
    }


def _mask_context_from_args(args: argparse.Namespace) -> MaskContext | None:
    if not args.mask_manifest and not args.redaction_profile:
        return None
    mask_context = (
        MaskContext.from_path(Path(args.mask_manifest).expanduser(), args.mask_mode)
        if args.mask_manifest
        else MaskContext.disabled(mode=args.mask_mode)
    )
    if args.redaction_profile:
        redaction_context = RedactionContext.from_path(Path(args.redaction_profile).expanduser())
        mask_context.redaction_context = redaction_context
        if mask_context.stats is not None:
            mask_context.stats.redaction = redaction_context.stats.to_dict()
            for warning in redaction_context.stats.warnings:
                if warning not in mask_context.stats.warnings:
                    mask_context.stats.warnings.append(warning)
    return mask_context


def _configuration_warnings(source_plan: dict) -> list[str]:
    redaction = source_plan.get("redaction")
    if not isinstance(redaction, dict):
        return []
    warnings = redaction.get("warnings")
    return list(warnings) if isinstance(warnings, list) else []


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-install":
        payload = validate_install(
            package_root=Path(args.package_root).expanduser() if args.package_root else None,
            codex_home=Path(args.codex_home).expanduser() if args.codex_home else None,
        )
        print(render(payload, args.format))
        return 1 if payload["install_validation"]["overall_status"] == "fail" else 0
    if args.command == "forensics":
        print(render(build_forensics_payload(args), args.format))
        return 0
    if args.command == "benchmark":
        payload = build_efficiency_eval(
            Path(args.fixture).expanduser(),
            strategies=tuple(args.strategy) if args.strategy else None,
        )
        print(render(payload, args.format))
        eval_payload = payload["retrieval_efficiency_eval"]
        thresholds = eval_payload.get("large_impact_thresholds", {})
        if args.fail_on_threshold and thresholds.get("status") == "fail":
            return 1
        return 1 if eval_payload.get("status") == "invalid_fixture" else 0
    source_plan, intent, plan = payload_from_args(args)
    if args.command == "manifest":
        payload = build_manifest_report(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            shortlist_limit=args.shortlist_limit,
        )
        payload["source_plan"] = source_plan
    elif args.command in {"find", "plan-sources", "broaden-query", "find-candidates", "timeline", "cluster-candidates", "no-evidence-report"}:
        mask_context = _mask_context_from_args(args)
        result = search(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            mask_context=mask_context,
        )
        result["warnings"] = sorted(set(result["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
        manifest_report = (
            build_manifest_report(
                plan,
                intent,
                default_tz=timezone_from_args(args.timezone, args.timezone_offset),
                cwd=source_plan["cwd"],
            )
            if args.include_manifest
            else None
        )
        payload = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            manifest_report=manifest_report,
            output_format=args.format,
            report_output=args.report_output,
        )
    elif args.command == "plan":
        payload = {
            "source_plan": source_plan,
            "searched_files": 0,
            "searched_bytes": 0,
            "groups": [],
            "warnings": sorted(set(intent.warnings + plan.warnings + _configuration_warnings(source_plan))),
            "blind_spots": plan.blind_spots,
        }
    else:
        mask_context = _mask_context_from_args(args)
        result = search(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            mask_context=mask_context,
        )
        payload = {"source_plan": source_plan, **result}
        payload["warnings"] = sorted(set(payload["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
    rendered = render(payload, args.format)
    if getattr(args, "report_output", None):
        Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        if "agent_find" in payload:
            payload = _short_report_written_payload(payload, args.report_output)
            rendered = render(payload, args.format)
    print(rendered)
    return 0


def _short_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["agent_find"]
    return {
        "agent_find": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "query": report["query"],
            "report_written": str(Path(report_output).expanduser()),
            "candidate_count": report["token_efficiency"]["candidate_count"],
            "searched_files": report["token_efficiency"]["searched_files"],
            "searched_bytes": report["token_efficiency"]["searched_bytes"],
            "top_candidates": report["find_candidates"][:3],
            "next_search_suggestions": report["next_search_suggestions"],
            "message": "Full find report written to report_output; stdout is a compact handoff map.",
        }
    }


if __name__ == "__main__":
    raise SystemExit(main())
