from __future__ import annotations

import importlib
import json
import os
import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sources import Limits, build_source_plan


@dataclass
class ValidationCheck:
    id: str
    name: str
    status: str
    detail: str
    recovery: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "recovery": list(self.recovery),
        }
        if self.paths:
            payload["paths"] = list(self.paths)
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


def validate_install(
    *,
    package_root: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    root = (package_root or default_package_root()).expanduser().resolve()
    source_checkout = _is_source_checkout(root)
    checks = [
        _check_cli_import(),
        _check_console_script(root),
        _check_package_skill(root, source_checkout=source_checkout),
        _check_package_helpers(root, source_checkout=source_checkout),
        _check_package_helper_behavior(root, source_checkout=source_checkout),
        _check_safe_root_rejection(root),
    ]
    checks.extend(_check_redaction_and_fixture_search())
    if codex_home is not None:
        checks.extend(_check_global_codex_install(codex_home.expanduser(), root, source_checkout=source_checkout))

    counts = _status_counts(checks)
    failed = counts.get("fail", 0) > 0
    payload = {
        "install_validation": {
            "overall_status": "fail" if failed else "pass",
            "read_only": True,
            "user_home_mutated": False,
            "package_root": str(root),
            "codex_home": str(codex_home.expanduser()) if codex_home is not None else None,
            "summary": counts,
            "checks": [check.to_dict() for check in checks],
            "warnings": [check.detail for check in checks if check.status == "warn"],
            "recovery_suggestions": _recovery_suggestions(checks),
        }
    }
    return payload


def default_package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_source_checkout(package_root: Path) -> bool:
    return (
        (package_root / "pyproject.toml").is_file()
        and (package_root / "src" / "jsonl_history_search").is_dir()
    )


def _check_cli_import() -> ValidationCheck:
    try:
        module = importlib.import_module("jsonl_history_search.cli")
        if not callable(getattr(module, "main", None)) or not callable(getattr(module, "build_parser", None)):
            return ValidationCheck(
                id="cli_import",
                name="CLI import",
                status="fail",
                detail="jsonl_history_search.cli imported, but expected CLI callables are missing.",
                recovery=["Reinstall the package from a clean checkout with `python3 -m pip install -e .`."],
            )
    except Exception as exc:
        return ValidationCheck(
            id="cli_import",
            name="CLI import",
            status="fail",
            detail=f"Could not import jsonl_history_search.cli: {exc}",
            recovery=["Run from the package root with `PYTHONPATH=src`, or install the package editable."],
        )
    return ValidationCheck(
        id="cli_import",
        name="CLI import",
        status="pass",
        detail="jsonl_history_search.cli imports and exposes main/build_parser.",
    )


def _check_console_script(package_root: Path) -> ValidationCheck:
    script = shutil.which("jsonl-history-search")
    declared = _pyproject_declares_console_script(package_root)
    if script:
        return ValidationCheck(
            id="console_script",
            name="Console script availability",
            status="pass",
            detail=f"`jsonl-history-search` is available on PATH at {script}.",
            paths=[script],
            metadata={"pyproject_entrypoint_declared": declared},
        )
    status = "warn" if declared else "fail"
    recovery = ["Install the package editable from this checkout: `python3 -m pip install -e .`."]
    detail = "`jsonl-history-search` is not on PATH."
    if declared:
        detail += " The package declares the console entry point, so source-mode validation can continue."
    else:
        detail += " The package entry point declaration was not found in pyproject.toml."
    return ValidationCheck(
        id="console_script",
        name="Console script availability",
        status=status,
        detail=detail,
        recovery=recovery,
        metadata={"pyproject_entrypoint_declared": declared},
    )


def _pyproject_declares_console_script(package_root: Path) -> bool:
    pyproject = package_root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'jsonl-history-search = "jsonl_history_search.cli:main"' in text


def _check_package_skill(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    path = package_root / "skills" / "codex" / "jsonl-history-search" / "SKILL.md"
    if path.is_file():
        return ValidationCheck(
            id="package_skill",
            name="Package Codex skill file",
            status="pass",
            detail="Package Codex skill file is present.",
            paths=[str(path)],
        )
    if not source_checkout:
        return ValidationCheck(
            id="package_skill",
            name="Package Codex skill file",
            status="warn",
            detail=(
                "Package Codex skill file is not present in the installed Python environment. "
                "This is expected for wheel installs; run from a source checkout to use "
                "`install/install_codex_global.sh`."
            ),
            paths=[str(path)],
            recovery=["Use a source checkout for optional global Codex skill installation."],
            metadata={"source_checkout": False},
        )
    return ValidationCheck(
        id="package_skill",
        name="Package Codex skill file",
        status="fail",
        detail="Package Codex skill file is missing.",
        paths=[str(path)],
        recovery=["Restore `skills/codex/jsonl-history-search/SKILL.md` from the package checkout."],
    )


def _check_package_helpers(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    helpers = [
        package_root / "scripts" / "search_jsonl_history.py",
        package_root / "scripts" / "git_jsonl_forensics.py",
    ]
    missing = [path for path in helpers if not path.is_file()]
    not_executable = [path for path in helpers if path.is_file() and not os.access(path, os.X_OK)]
    if not missing and not not_executable:
        return ValidationCheck(
            id="package_helpers",
            name="Package helper executability",
            status="pass",
            detail="Package helper scripts are present and executable.",
            paths=[str(path) for path in helpers],
        )
    if not source_checkout:
        return ValidationCheck(
            id="package_helpers",
            name="Package helper executability",
            status="warn",
            detail=(
                "Checkout helper scripts are not present in the installed Python environment. "
                "This is expected for wheel installs; the package CLI remains the installed surface."
            ),
            paths=[str(path) for path in helpers],
            recovery=["Use a source checkout if you need helper scripts outside the Python CLI."],
            metadata={"source_checkout": False},
        )
    recovery = []
    if missing:
        recovery.append("Restore missing helper scripts from the package checkout.")
    if not_executable:
        recovery.append("Run `chmod +x scripts/search_jsonl_history.py scripts/git_jsonl_forensics.py`.")
    return ValidationCheck(
        id="package_helpers",
        name="Package helper executability",
        status="fail",
        detail=(
            f"Missing helpers: {_path_list(missing) or '(none)'}; "
            f"not executable: {_path_list(not_executable) or '(none)'}."
        ),
        paths=[str(path) for path in helpers],
        recovery=recovery,
    )


def _check_package_helper_behavior(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    helper = package_root / "scripts" / "search_jsonl_history.py"
    if not source_checkout:
        return ValidationCheck(
            id="package_helper_smoke",
            name="Package helper behavior smoke",
            status="skip",
            detail="Package helper behavior smoke is skipped outside a source checkout.",
            paths=[str(helper)],
            metadata={"source_checkout": False},
        )
    if not helper.is_file() or not os.access(helper, os.X_OK):
        return ValidationCheck(
            id="package_helper_smoke",
            name="Package helper behavior smoke",
            status="skip",
            detail="Package helper behavior smoke is skipped because the helper is missing or not executable.",
            paths=[str(helper)],
            recovery=["Restore executable helper scripts before running the behavior smoke."],
        )

    query = "restart handoff artifact changed next work plan docs cleanup worktree cleanup"
    with tempfile.TemporaryDirectory(prefix="jsonl-history-search-helper-") as raw_tmp:
        tmp = Path(raw_tmp)
        transcript = tmp / "case.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "role": "user",
                    "timestamp": "2026-06-11T01:02:03Z",
                    "text": "handoff document moved into a task worktree; cleanup docs and restart plan changed",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        wrong_root = tmp / "not-json.txt"
        wrong_root.write_text("not jsonl\n", encoding="utf-8")
        recovered = _run_helper_json(helper, query, transcript)
        empty = _run_helper_json(helper, query, wrong_root)

    recovered_ok = (
        recovered.get("returncode") == 0
        and recovered.get("payload", {}).get("searched_files") == 1
        and len(recovered.get("payload", {}).get("matches", [])) == 1
    )
    empty_ok = (
        empty.get("returncode") == 0
        and empty.get("payload", {}).get("searched_files") == 0
        and empty.get("payload", {}).get("matches") == []
    )
    ok = recovered_ok and empty_ok
    return ValidationCheck(
        id="package_helper_smoke",
        name="Package helper behavior smoke",
        status="pass" if ok else "fail",
        detail=(
            "Package helper recovers non-contiguous broad query terms and does not search cwd for empty roots."
            if ok
            else "Package helper behavior smoke failed for broad-query recovery or empty-root handling."
        ),
        paths=[str(helper)],
        recovery=[] if ok else ["Inspect `scripts/search_jsonl_history.py` rg parsing, broad fallback, and empty-root guards."],
        metadata={
            "broad_query_recovered": recovered_ok,
            "empty_root_no_cwd_search": empty_ok,
            "broad_query_returncode": recovered.get("returncode"),
            "empty_root_returncode": empty.get("returncode"),
        },
    )


def _run_helper_json(helper: Path, query: str, root: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(helper), query, "--roots", str(root), "--limit", "5"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    return {"returncode": result.returncode, "payload": payload}


def _check_safe_root_rejection(package_root: Path) -> ValidationCheck:
    plan = build_source_plan(
        cwd=package_root,
        codex_home=package_root / ".tmp-codex-home-for-validation",
        claude_home=package_root / ".tmp-claude-home-for-validation",
        roots=[Path("/")],
        allow_arbitrary_root=False,
        limits=Limits(max_files=1, max_bytes=1024, max_matches=1, max_elapsed=1.0),
    )
    rejected = plan.rejected_roots[0].reason if plan.rejected_roots else ""
    if not plan.roots and "rejected before traversal" in rejected:
        return ValidationCheck(
            id="safe_root_rejection",
            name="Safe-root rejection",
            status="pass",
            detail="Dangerous broad root `/` is rejected by source planning before traversal.",
            metadata={"rejected_reason": rejected},
        )
    return ValidationCheck(
        id="safe_root_rejection",
        name="Safe-root rejection",
        status="fail",
        detail="Dangerous broad root `/` was not rejected before traversal.",
        recovery=["Inspect `jsonl_history_search.sources.dangerous_reason` and source planning root guards."],
        metadata={
            "roots": [root.to_dict() for root in plan.roots],
            "rejected": [root.to_dict() for root in plan.rejected_roots],
        },
    )


def _check_redaction_and_fixture_search() -> list[ValidationCheck]:
    with tempfile.TemporaryDirectory(prefix="jsonl-history-search-validate-") as raw_tmp:
        tmp = Path(raw_tmp)
        codex = tmp / "codex"
        claude = tmp / "claude"
        cwd = tmp / "project"
        transcript = codex / "sessions" / "2026" / "05" / "30" / "install-validation.jsonl"
        profile = tmp / "redaction.json"
        cwd.mkdir(parents=True)
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "payload": {
                                "type": "session_meta",
                                "timestamp": "2026-05-30T00:00:00+00:00",
                                "cwd": str(cwd),
                            }
                        }
                    ),
                    json.dumps(
                        {
                            "payload": {
                                "type": "user_message",
                                "timestamp": "2026-05-30T00:01:00+00:00",
                                "message": "validator needle contact redacted@example.invalid",
                            }
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        original = transcript.read_bytes()
        profile.write_text(json.dumps({"profiles": ["emails"]}), encoding="utf-8")

        try:
            from .cli import _mask_context_from_args, build_parser, payload_from_args
            from .scanner import search
            from .timeutil import timezone_from_args

            args = build_parser().parse_args(
                [
                    "search",
                    "validator needle alice",
                    "--format",
                    "json",
                    "--cwd",
                    str(cwd),
                    "--codex-home",
                    str(codex),
                    "--claude-home",
                    str(claude),
                    "--now",
                    "2026-05-30T00:02:00+00:00",
                    "--timezone-offset",
                    "+00:00",
                    "--max-matches",
                    "5",
                    "--redaction-profile",
                    str(profile),
                ]
            )
            source_plan, intent, plan = payload_from_args(args)
            result = search(
                plan,
                intent,
                default_tz=timezone_from_args(args.timezone, args.timezone_offset),
                cwd=source_plan["cwd"],
                mask_context=_mask_context_from_args(args),
            )
        except Exception as exc:
            return [
                ValidationCheck(
                    id="redaction_availability",
                    name="Redaction availability",
                    status="fail",
                    detail=f"Temp fixture search with --redaction-profile failed: {exc}",
                    recovery=[
                        "Run `PYTHONPATH=src python3 -m pytest tests/test_redaction_profiles.py -q` for focused detail."
                    ],
                ),
                ValidationCheck(
                    id="fixture_search",
                    name="Fixture search",
                    status="fail",
                    detail=f"Temp JSONL fixture search failed: {exc}",
                    recovery=["Run `PYTHONPATH=src python3 -m pytest tests/test_search_fixtures.py -q` for focused detail."],
                ),
            ]

        snippets = "\n".join(
            match["snippet"] for group in result.get("groups", []) for match in group.get("matches", [])
        )
        redaction = (result.get("masking") or {}).get("redaction") or {}
        transcript_mutated = transcript.read_bytes() != original
        redaction_ok = (
            "redacted@example.invalid" not in snippets
            and "[REDACTED:email]" in snippets
            and redaction.get("redacted_total") == 1
        )
        fixture_ok = result.get("searched_files") == 1 and bool(result.get("groups")) and not transcript_mutated
        checks: list[ValidationCheck] = []
        checks.append(
            ValidationCheck(
                id="redaction_availability",
                name="Redaction availability",
                status="pass" if redaction_ok else "fail",
                detail=(
                    "Temp --redaction-profile run redacts copied snippets."
                    if redaction_ok
                    else "Temp --redaction-profile run did not redact copied snippets as expected."
                ),
                recovery=[] if redaction_ok else ["Inspect RedactionContext and CLI redaction-profile wiring."],
                metadata={
                    "redacted_total": redaction.get("redacted_total"),
                    "snippet_contains_raw_email": "redacted@example.invalid" in snippets,
                    "snippet_contains_redaction": "[REDACTED:email]" in snippets,
                },
            )
        )
        checks.append(
            ValidationCheck(
                id="fixture_search",
                name="Fixture search",
                status="pass" if fixture_ok else "fail",
                detail=(
                    "Temp JSONL fixture search returns a match without mutating the fixture."
                    if fixture_ok
                    else "Temp JSONL fixture search did not return a match with read-only behavior."
                ),
                recovery=[] if fixture_ok else ["Inspect source planning, scanner enumeration, and fixture JSONL parsing."],
                metadata={
                    "searched_files": result.get("searched_files"),
                    "match_groups": len(result.get("groups", [])),
                    "fixture_transcript_mutated": transcript_mutated,
                },
            )
        )
        return checks


def _check_global_codex_install(codex_home: Path, package_root: Path, *, source_checkout: bool = True) -> list[ValidationCheck]:
    skill = codex_home / "skills" / "jsonl-history-search" / "SKILL.md"
    helper = codex_home / "skills" / "jsonl-history-search" / "scripts" / "search_jsonl_history.py"
    package_skill = package_root / "skills" / "codex" / "jsonl-history-search" / "SKILL.md"
    package_helper = package_root / "scripts" / "search_jsonl_history.py"
    checks: list[ValidationCheck] = []
    if skill.is_file():
        checks.append(_global_copy_check("global_skill", "Global Codex skill file", skill, package_skill, source_checkout=source_checkout))
    else:
        checks.append(
            ValidationCheck(
                id="global_skill",
                name="Global Codex skill file",
                status="fail",
                detail="Global Codex skill file is missing at the provided --codex-home.",
                paths=[str(skill)],
                recovery=["Run `bash install/install_codex_global.sh` from the package root, or copy the skill file manually."],
            )
        )
    if helper.is_file() and os.access(helper, os.X_OK):
        checks.append(_global_copy_check("global_helper", "Global Codex helper script", helper, package_helper, source_checkout=source_checkout))
    else:
        checks.append(
            ValidationCheck(
                id="global_helper",
                name="Global Codex helper executability",
                status="fail",
                detail="Global Codex helper is missing or not executable at the provided --codex-home.",
                paths=[str(helper)],
                recovery=["Copy `scripts/search_jsonl_history.py` into the skill scripts folder and run `chmod +x` on it."],
            )
        )
    return checks


def _global_copy_check(
    check_id: str,
    name: str,
    installed: Path,
    packaged: Path,
    *,
    source_checkout: bool,
) -> ValidationCheck:
    paths = [str(installed)]
    if not source_checkout or not packaged.is_file():
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"{name} is present.",
            paths=paths,
            metadata={"source_checkout": source_checkout},
        )

    paths.append(str(packaged))
    installed_hash = _sha256_file(installed)
    packaged_hash = _sha256_file(packaged)
    metadata = {
        "installed_sha256": installed_hash,
        "package_sha256": packaged_hash,
        "matches_package": installed_hash == packaged_hash,
    }
    if installed_hash == packaged_hash:
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"{name} matches the package checkout copy.",
            paths=paths,
            metadata=metadata,
        )
    return ValidationCheck(
        id=check_id,
        name=name,
        status="fail",
        detail=f"{name} is installed but stale relative to the package checkout copy.",
        paths=paths,
        recovery=["Run `bash install/install_codex_global.sh` from the package root to refresh the global Codex skill/helper."],
        metadata=metadata,
    )


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _status_counts(checks: list[ValidationCheck]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def _recovery_suggestions(checks: list[ValidationCheck]) -> list[str]:
    suggestions: list[str] = []
    for check in checks:
        for item in check.recovery:
            if item not in suggestions:
                suggestions.append(item)
    return suggestions


def _path_list(paths: list[Path]) -> str:
    return ", ".join(str(path) for path in paths)
