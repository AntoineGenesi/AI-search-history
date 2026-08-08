from __future__ import annotations

import importlib
import json
import os
import hashlib
import re
import shutil
import sys
import tempfile
from subprocess import PIPE, run as run_subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent_command_cookbook import agent_question_cookbook
from .agent_skill_install import AgentSkillAssets, resolve_agent_skill_assets
from .codex_skill_install import CodexSkillAssets, resolve_codex_skill_assets
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
    claude_home: Path | None = None,
) -> dict[str, Any]:
    root = (package_root or default_package_root()).expanduser().resolve()
    source_checkout = _is_source_checkout(root)
    try:
        skill_assets = resolve_codex_skill_assets(root)
    except FileNotFoundError:
        skill_assets = None
    try:
        claude_skill_assets = resolve_agent_skill_assets("claude", root)
    except FileNotFoundError:
        claude_skill_assets = None
    checks = [
        _check_cli_import(),
        _check_console_script(root),
        _check_convo_console_alias(root),
        _check_package_skill(root, source_checkout=source_checkout, assets=skill_assets),
        _check_package_agent_alias_skill(
            root,
            agent="codex",
            source_checkout=source_checkout,
            assets=skill_assets,
        ),
        _check_package_agent_skill(
            root,
            agent="claude",
            source_checkout=source_checkout,
            assets=claude_skill_assets,
        ),
        _check_package_agent_alias_skill(
            root,
            agent="claude",
            source_checkout=source_checkout,
            assets=claude_skill_assets,
        ),
        _check_package_helpers(root, source_checkout=source_checkout, assets=skill_assets),
        _check_package_helper_behavior(root, source_checkout=source_checkout),
        _check_package_ask_first_launcher_behavior(root, source_checkout=source_checkout),
        _check_safe_root_rejection(root),
    ]
    checks.extend(_check_redaction_and_fixture_search())
    if codex_home is not None:
        checks.extend(
            _check_global_codex_install(
                codex_home.expanduser(),
                root,
                source_checkout=source_checkout,
                assets=skill_assets,
            )
        )
    if claude_home is not None:
        checks.extend(
            _check_global_agent_install(
                claude_home.expanduser(),
                root,
                agent="claude",
                id_prefix="claude_global_",
                source_checkout=source_checkout,
                assets=claude_skill_assets,
            )
        )
    checks.extend(_check_install_currentness(root, source_checkout=source_checkout))

    counts = _status_counts(checks)
    failed = counts.get("fail", 0) > 0
    preflight = _agent_preflight(
        checks,
        codex_home=codex_home,
        claude_home=claude_home,
    )
    payload = {
        "install_validation": {
            "overall_status": "fail" if failed else "pass",
            "read_only": True,
            "user_home_mutated": False,
            "package_root": str(root),
            "codex_home": str(codex_home.expanduser()) if codex_home is not None else None,
            "claude_home": str(claude_home.expanduser()) if claude_home is not None else None,
            "currentness": _currentness_summary(root, checks, source_checkout=source_checkout, preflight=preflight),
            "agent_preflight": preflight,
            "summary": counts,
            "checks": [check.to_dict() for check in checks],
            "warnings": [check.detail for check in checks if check.status == "warn"],
            "recovery_suggestions": _recovery_suggestions(checks),
        }
    }
    return payload


def _agent_preflight(
    checks: list[ValidationCheck],
    *,
    codex_home: Path | None,
    claude_home: Path | None,
) -> dict[str, Any]:
    codex_checks = [check for check in checks if check.id.startswith("global_")]
    claude_checks = [check for check in checks if check.id.startswith("claude_global_")]
    codex_freshness, codex_stale = _skill_freshness(codex_checks, checked=codex_home is not None)
    claude_freshness, claude_stale = _skill_freshness(claude_checks, checked=claude_home is not None)
    by_id = {check.id: check for check in checks}
    cli_statuses = [by_id[check_id].status for check_id in ("cli_import", "console_script", "console_alias_convo")]
    if "fail" in cli_statuses:
        cli_status = "missing_or_broken"
    elif "warn" in cli_statuses:
        cli_status = "source_mode_only"
    else:
        cli_status = "installed"
    codex_skill_status = _layer_skill_status(codex_freshness)
    claude_skill_status = _layer_skill_status(claude_freshness)
    return {
        "purpose": (
            "Run before free-agent or user-facing JSONL workflows that rely on "
            "an installed Codex or Claude skill."
        ),
        "global_skill_freshness": codex_freshness,
        "must_refresh_global_skill": codex_freshness == "fail",
        "stale_or_missing_global_checks": codex_stale,
        "agent_skill_freshness": {
            "codex": codex_freshness,
            "claude": claude_freshness,
        },
        "stale_or_missing_agent_checks": {
            "codex": codex_stale,
            "claude": claude_stale,
        },
        "install_layers": {
            "python_cli": {
                "status": cli_status,
                "required": True,
                "install_command": "pipx install /path/to/jsonl-history-search",
                "detail": "The Python package provides the jsonl-history-search and convo commands.",
            },
            "codex_skill": {
                "status": codex_skill_status,
                "required": False,
                "install_command": "jsonl-history-search install-codex-skill",
                "detail": (
                    "The optional Codex skill adds agent routing guidance under the public "
                    "$HOME/.agents/skills route; it does not provide the CLI. Explicit "
                    "CODEX_HOME/skills or ~/.codex/skills compatibility routes are legacy."
                ),
            },
            "claude_skill": {
                "status": claude_skill_status,
                "required": False,
                "install_command": "jsonl-history-search install-agent-skill --agent claude",
                "detail": "The optional Claude skill adds agent routing guidance; it does not provide the CLI.",
            },
        },
        "refresh_command": "jsonl-history-search install-codex-skill",
        "validate_global_command": (
            "jsonl-history-search validate-install --codex-home ~/.agents --format text"
        ),
        "validate_agent_commands": {
            "codex": "jsonl-history-search validate-install --codex-home ~/.agents --format text",
            "claude": "jsonl-history-search validate-install --claude-home ~/.claude --format text",
        },
        "agent_skill_routes": {
            "codex": {
                "implemented_route": "$HOME/.agents/skills",
                "boundary": (
                    "An explicit --agent-home or --codex-home can select a project or legacy "
                    "compatibility root; .codex/skills is not the public default."
                ),
            },
            "claude": {
                "implemented_route": "CLAUDE_HOME/skills, ~/.claude/skills, or project .claude/skills via --agent-home",
                "boundary": "Claude Code skill files only; does not provide Claude Desktop plugin/MCP/connector integration.",
            },
        },
        "agent_question_cookbook": agent_question_cookbook(),
        "jsh1098_route_demos": _jsh1098_route_demos(),
        "claim_boundary": (
            "validate-install proves local package/global-skill health only. "
            "It makes no remote network calls; no remote/tag/version warning "
            "does not prove latest upstream, latest tagged release, public "
            "release currentness; JSH-1098 showed sampled lower-burden "
            "source-window routing, not broad release readiness, provider-token "
            "savings, all-history completeness, or universal superiority."
        ),
    }


def _skill_freshness(
    checks: list[ValidationCheck],
    *,
    checked: bool,
) -> tuple[str, list[str]]:
    if not checked:
        return "not_checked", []
    stale = [check.id for check in checks if check.status == "fail"]
    return ("fail" if stale else "pass"), stale


def _layer_skill_status(freshness: str) -> str:
    if freshness == "not_checked":
        return "not_checked"
    return "installed" if freshness == "pass" else "missing_or_stale"


def _jsh1098_route_demos() -> list[dict[str, str]]:
    return [
        {
            "id": "date_timeline_exact_ref",
            "label": "Date/timeline exact-ref recovery",
            "prompt_shape": "In the session dated <date>, what happened with <event>?",
            "first_move": 'jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text',
            "followthrough": "Open the promoted source window with compact read-more before broad repo search.",
        },
        {
            "id": "heldout_vague_paraphrase",
            "label": "Vague remembered-history retrieval",
            "prompt_shape": "What did I say before about making this clearly valuable upfront?",
            "first_move": 'jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text',
            "followthrough": "Use semantic-debug/source-window follow-through before artifact recovery.",
        },
        {
            "id": "agent_action_overwrite_proof",
            "label": "Agent action/proof verification",
            "prompt_shape": "Did the agent overwrite those workflow/planner edits?",
            "first_move": 'jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text',
            "followthrough": "Inspect tool/action proof, command output, diffs, commits, or review metadata.",
        },
        {
            "id": "decision_rationale_source_window",
            "label": "Decision-rationale/source-window provenance",
            "prompt_shape": "Why did we distinguish persistent coordinator from wait loops?",
            "first_move": 'jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text',
            "followthrough": "Answer from the reviewed source window and preserve corrections/uncertainty.",
        },
    ]


def default_package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_source_checkout(package_root: Path) -> bool:
    return (
        (package_root / "pyproject.toml").is_file()
        and (package_root / "src" / "jsonl_history_search").is_dir()
    )


def _check_install_currentness(package_root: Path, *, source_checkout: bool = True) -> list[ValidationCheck]:
    return [
        _check_git_remote_availability(package_root, source_checkout=source_checkout),
        _check_git_tag_distance(package_root, source_checkout=source_checkout),
        _check_version_metadata(package_root, source_checkout=source_checkout),
    ]


def _check_git_remote_availability(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    if not source_checkout:
        return ValidationCheck(
            id="git_remote_currentness",
            name="Git remote availability",
            status="skip",
            detail="Git remote currentness is skipped outside a source checkout.",
            metadata={"source_checkout": False},
        )
    if not _git_available(package_root):
        return ValidationCheck(
            id="git_remote_currentness",
            name="Git remote availability",
            status="skip",
            detail="Git remote currentness is skipped because the package root is not inside a git worktree.",
            metadata={"source_checkout": True, "git_worktree": False},
        )
    result = _run_git(package_root, "remote", "-v")
    if result.returncode != 0:
        return ValidationCheck(
            id="git_remote_currentness",
            name="Git remote availability",
            status="warn",
            detail=f"Git remote metadata could not be read locally: {result.stderr.strip() or result.stdout.strip()}",
            recovery=["Inspect local git metadata before making upstream/latest-release claims."],
            metadata={"git_returncode": result.returncode},
        )
    remotes = _parse_git_remote_names(result.stdout)
    if remotes:
        return ValidationCheck(
            id="git_remote_currentness",
            name="Git remote availability",
            status="pass",
            detail=(
                "Git remotes are configured locally. validate-install still does not fetch or compare "
                "remote refs, so upstream/latest-release currentness remains unproven."
            ),
            metadata={"remote_names": remotes, "remote_count": len(remotes), "remote_currentness_checked": False},
        )
    return ValidationCheck(
        id="git_remote_currentness",
        name="Git remote availability",
        status="warn",
        detail=(
            "No git remotes are configured in this checkout; validate-install cannot prove upstream "
            "or latest-release currentness."
        ),
        recovery=["Configure or inspect a git remote separately before making upstream/latest-release claims."],
        metadata={"remote_names": [], "remote_count": 0, "remote_currentness_checked": False},
    )


def _check_git_tag_distance(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    if not source_checkout:
        return ValidationCheck(
            id="git_tag_distance",
            name="Git describe/tag distance",
            status="skip",
            detail="Git tag distance is skipped outside a source checkout.",
            metadata={"source_checkout": False},
        )
    if not _git_available(package_root):
        return ValidationCheck(
            id="git_tag_distance",
            name="Git describe/tag distance",
            status="skip",
            detail="Git tag distance is skipped because the package root is not inside a git worktree.",
            metadata={"source_checkout": True, "git_worktree": False},
        )
    result = _run_git(package_root, "describe", "--tags", "--always", "--dirty", "--long")
    if result.returncode != 0:
        return ValidationCheck(
            id="git_tag_distance",
            name="Git describe/tag distance",
            status="warn",
            detail=f"Git describe/tag distance could not be read locally: {result.stderr.strip() or result.stdout.strip()}",
            recovery=["Inspect local tags separately before making latest-tagged-release claims."],
            metadata={"git_returncode": result.returncode},
        )
    describe = result.stdout.strip()
    parsed = _parse_git_describe_long(describe)
    if parsed is None or parsed.get("tag") is None:
        return ValidationCheck(
            id="git_tag_distance",
            name="Git describe/tag distance",
            status="warn",
            detail=(
                f"Git describe did not identify a nearest local tag (`{describe}`); tagged-release "
                "currentness is unproven."
            ),
            recovery=["Create or inspect local tags separately before making tagged-release currentness claims."],
            metadata={"git_describe": describe, "tag_found": False},
        )
    distance = int(parsed["distance"])
    dirty = bool(parsed["dirty"])
    metadata = {
        "git_describe": describe,
        "nearest_tag": parsed["tag"],
        "commits_after_tag": distance,
        "dirty": dirty,
    }
    if distance or dirty:
        dirty_note = " and has uncommitted changes" if dirty else ""
        return ValidationCheck(
            id="git_tag_distance",
            name="Git describe/tag distance",
            status="warn",
            detail=(
                f"Checkout is {distance} commits after nearest local tag `{parsed['tag']}`{dirty_note}; "
                "the installed checkout is not the same as the latest local tag described here."
            ),
            recovery=["Do not describe this install as latest tagged release without a separate release/tag decision."],
            metadata=metadata,
        )
    return ValidationCheck(
        id="git_tag_distance",
        name="Git describe/tag distance",
        status="pass",
        detail=(
            f"Checkout is exactly at nearest local tag `{parsed['tag']}`. This is local tag metadata only; "
            "validate-install does not prove upstream/latest-release currentness."
        ),
        metadata=metadata,
    )


def _check_version_metadata(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    pyproject_version = _read_pyproject_version(package_root / "pyproject.toml") if source_checkout else None
    module_version = _read_module_version(package_root / "src" / "jsonl_history_search" / "__init__.py")
    metadata = {
        "pyproject_version": pyproject_version,
        "module_version": module_version,
        "source_checkout": source_checkout,
    }
    paths = [str(package_root / "pyproject.toml"), str(package_root / "src" / "jsonl_history_search" / "__init__.py")]
    if pyproject_version is None and module_version is None:
        return ValidationCheck(
            id="version_metadata",
            name="Version metadata consistency",
            status="skip",
            detail="Version metadata consistency is skipped because pyproject and module __version__ were not found.",
            paths=paths,
            metadata=metadata,
        )
    if pyproject_version is None or module_version is None:
        return ValidationCheck(
            id="version_metadata",
            name="Version metadata consistency",
            status="warn",
            detail=(
                f"Version metadata is incomplete: pyproject version={pyproject_version!r}, "
                f"module __version__={module_version!r}."
            ),
            paths=paths,
            recovery=["Keep pyproject.toml and src/jsonl_history_search/__init__.py version metadata aligned."],
            metadata=metadata,
        )
    if pyproject_version != module_version:
        return ValidationCheck(
            id="version_metadata",
            name="Version metadata consistency",
            status="warn",
            detail=(
                f"pyproject version `{pyproject_version}` differs from module __version__ `{module_version}`; "
                "release/version metadata has drift."
            ),
            paths=paths,
            recovery=["Align pyproject.toml and src/jsonl_history_search/__init__.py before publishing or claiming release currentness."],
            metadata=metadata,
        )
    return ValidationCheck(
        id="version_metadata",
        name="Version metadata consistency",
        status="pass",
        detail=f"pyproject version and module __version__ both report `{pyproject_version}`.",
        paths=paths,
        metadata=metadata,
    )


def _currentness_summary(
    package_root: Path,
    checks: list[ValidationCheck],
    *,
    source_checkout: bool,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    by_id = {check.id: check for check in checks}
    remote = by_id.get("git_remote_currentness")
    tag = by_id.get("git_tag_distance")
    version = by_id.get("version_metadata")
    version_metadata = version.metadata if version else {}
    tag_metadata = tag.metadata if tag else {}
    remote_metadata = remote.metadata if remote else {}
    warning_ids = [
        check_id
        for check_id in ("git_remote_currentness", "git_tag_distance", "version_metadata")
        if by_id.get(check_id) is not None and by_id[check_id].status == "warn"
    ]
    global_skill = preflight.get("global_skill_freshness", "unknown")
    return {
        "local_install_freshness": {
            "package_root": str(package_root),
            "editable_project_root": str(package_root) if source_checkout else None,
            "source_checkout": source_checkout,
            "global_skill_freshness": global_skill,
            "local_install_claim": (
                "Local package checkout and optional global skill parity are validated by local checks only."
            ),
        },
        "git_remote": {
            "status": remote.status if remote else "not_checked",
            "remote_names": remote_metadata.get("remote_names"),
            "remote_count": remote_metadata.get("remote_count"),
            "remote_currentness_checked": remote_metadata.get("remote_currentness_checked", False),
            "detail": remote.detail if remote else None,
        },
        "git_describe": {
            "status": tag.status if tag else "not_checked",
            "describe": tag_metadata.get("git_describe"),
            "nearest_tag": tag_metadata.get("nearest_tag"),
            "commits_after_tag": tag_metadata.get("commits_after_tag"),
            "dirty": tag_metadata.get("dirty"),
            "detail": tag.detail if tag else None,
        },
        "version_metadata": {
            "status": version.status if version else "not_checked",
            "pyproject_version": version_metadata.get("pyproject_version"),
            "module_version": version_metadata.get("module_version"),
            "detail": version.detail if version else None,
        },
        "claim_boundary": (
            "validate-install is a local diagnostic. It does not fetch remote refs or query package indexes; "
            "absence of remote/tag/version warnings does not prove latest upstream or latest public release."
        ),
        "upstream_latest_release_currentness": "unproven",
        "warning_check_ids": warning_ids,
    }


def _git_available(package_root: Path) -> bool:
    return _run_git(package_root, "rev-parse", "--is-inside-work-tree").returncode == 0


def _run_git(package_root: Path, *args: str):
    return run_subprocess(
        ["git", "-C", str(package_root), *args],
        text=True,
        stdout=PIPE,
        stderr=PIPE,
        check=False,
    )


def _parse_git_remote_names(output: str) -> list[str]:
    return sorted({line.split()[0] for line in output.splitlines() if line.split()})


def _parse_git_describe_long(describe: str) -> dict[str, Any] | None:
    dirty = describe.endswith("-dirty")
    clean = describe[:-6] if dirty else describe
    match = re.match(r"^(?P<tag>.+)-(?P<distance>\d+)-g(?P<sha>[0-9a-f]+)$", clean)
    if not match:
        return None
    return {
        "tag": match.group("tag"),
        "distance": int(match.group("distance")),
        "sha": match.group("sha"),
        "dirty": dirty,
    }


def _read_pyproject_version(pyproject: Path) -> str | None:
    try:
        lines = pyproject.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    in_project = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project:
            match = re.match(r"version\s*=\s*['\"]([^'\"]+)['\"]", line)
            if match:
                return match.group(1)
    return None


def _read_module_version(init_file: Path) -> str | None:
    try:
        text = init_file.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", text, re.MULTILINE)
    return match.group(1) if match else None


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
    return _check_console_command(
        package_root,
        command="jsonl-history-search",
        check_id="console_script",
        name="Console script availability",
    )


def _check_convo_console_alias(package_root: Path) -> ValidationCheck:
    return _check_console_command(
        package_root,
        command="convo",
        check_id="console_alias_convo",
        name="Console alias availability",
    )


def _check_console_command(package_root: Path, *, command: str, check_id: str, name: str) -> ValidationCheck:
    script = shutil.which(command)
    declared = _pyproject_declares_console_script(package_root, command)
    if script:
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"`{command}` is available on PATH at {script}.",
            paths=[script],
            metadata={
                "pyproject_entrypoint_declared": declared,
                "entrypoint": "jsonl_history_search.cli:main",
            },
        )
    status = "warn" if declared else "fail"
    recovery = ["Install the package editable from this checkout: `python3 -m pip install -e .`."]
    if command == "convo":
        recovery.append("Refresh stale editable installs so the `convo` console alias is generated.")
    detail = f"`{command}` is not on PATH."
    if declared:
        detail += " The package declares the console entry point, so source-mode validation can continue."
    else:
        detail += " The package entry point declaration was not found in pyproject.toml."
    return ValidationCheck(
        id=check_id,
        name=name,
        status=status,
        detail=detail,
        recovery=recovery,
        metadata={
            "pyproject_entrypoint_declared": declared,
            "entrypoint": "jsonl_history_search.cli:main",
            "jsonl_history_search_on_path": shutil.which("jsonl-history-search") is not None,
        },
    )


def _pyproject_declares_console_script(package_root: Path, command: str = "jsonl-history-search") -> bool:
    pyproject = package_root / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'{command} = "jsonl_history_search.cli:main"' in text


def _check_package_skill(
    package_root: Path,
    *,
    source_checkout: bool = True,
    assets: CodexSkillAssets | None = None,
) -> ValidationCheck:
    path = package_root / "skills" / "codex" / "jsonl-history-search" / "SKILL.md"
    if path.is_file():
        return ValidationCheck(
            id="package_skill",
            name="Package Codex skill file",
            status="pass",
            detail="Package Codex skill file is present.",
            paths=[str(path)],
        )
    if not source_checkout and assets is not None:
        return ValidationCheck(
            id="package_skill",
            name="Packaged Codex skill assets",
            status="pass",
            detail="Optional Codex skill assets are bundled with the installed Python package.",
            paths=[str(assets.skill)],
            metadata={"source_checkout": False, "asset_source": assets.source},
        )
    if not source_checkout:
        return ValidationCheck(
            id="package_skill",
            name="Package Codex skill file",
            status="warn",
            detail=(
                "Optional Codex skill assets are missing from the installed Python package."
            ),
            paths=[str(path)],
            recovery=["Reinstall the Python package before running `jsonl-history-search install-codex-skill`."],
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


def _check_package_agent_skill(
    package_root: Path,
    *,
    agent: str,
    source_checkout: bool,
    assets: AgentSkillAssets | None,
) -> ValidationCheck:
    path = package_root / "skills" / agent / "jsonl-history-search" / "SKILL.md"
    check_id = f"package_{agent}_skill"
    label = agent.title()
    if path.is_file():
        return ValidationCheck(
            id=check_id,
            name=f"Package {label} skill file",
            status="pass",
            detail=f"Package {label} skill file is present.",
            paths=[str(path)],
        )
    if not source_checkout and assets is not None:
        return ValidationCheck(
            id=check_id,
            name=f"Packaged {label} skill assets",
            status="pass",
            detail=f"Optional {label} skill assets are bundled with the installed Python package.",
            paths=[str(assets.skill)],
            metadata={"source_checkout": False, "asset_source": assets.source},
        )
    status = "warn" if not source_checkout else "fail"
    return ValidationCheck(
        id=check_id,
        name=f"Package {label} skill file",
        status=status,
        detail=f"Optional {label} skill assets are missing from the Python package.",
        paths=[str(path)],
        recovery=[
            f"Reinstall the Python package before running `jsonl-history-search install-agent-skill --agent {agent}`."
        ],
        metadata={"source_checkout": source_checkout},
    )


def _check_package_agent_alias_skill(
    package_root: Path,
    *,
    agent: str,
    source_checkout: bool,
    assets: AgentSkillAssets | None,
) -> ValidationCheck:
    path = package_root / "skills" / agent / "convo" / "SKILL.md"
    check_id = "package_convo_skill" if agent == "codex" else f"package_{agent}_convo_skill"
    label = "Codex" if agent == "codex" else agent.title()
    if path.is_file():
        return ValidationCheck(
            id=check_id,
            name=f"Package {label} Convo alias skill file",
            status="pass",
            detail=f"Package {label} Convo alias skill file is present.",
            paths=[str(path)],
        )
    if not source_checkout and assets is not None:
        return ValidationCheck(
            id=check_id,
            name=f"Packaged {label} Convo alias skill assets",
            status="pass",
            detail=f"Optional {label} Convo alias skill assets are bundled with the installed Python package.",
            paths=[str(assets.alias_skill)],
            metadata={"source_checkout": False, "asset_source": assets.source},
        )
    status = "warn" if not source_checkout else "fail"
    return ValidationCheck(
        id=check_id,
        name=f"Package {label} Convo alias skill file",
        status=status,
        detail=f"Optional {label} Convo alias skill asset is missing from the Python package.",
        paths=[str(path)],
        recovery=[
            f"Restore `skills/{agent}/convo/SKILL.md` or reinstall the package before running the agent skill installer."
        ],
        metadata={"source_checkout": source_checkout},
    )


def _check_package_helpers(
    package_root: Path,
    *,
    source_checkout: bool = True,
    assets: CodexSkillAssets | None = None,
) -> ValidationCheck:
    helpers = [
        package_root / "scripts" / "search_jsonl_history.py",
        package_root / "scripts" / "jsonl_ask_first.py",
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
    if not source_checkout and assets is not None:
        return ValidationCheck(
            id="package_helpers",
            name="Packaged Codex skill helpers",
            status="pass",
            detail="Optional Codex skill helper assets are bundled with the installed Python package.",
            paths=[str(assets.search_helper), str(assets.ask_first_helper)],
            metadata={"source_checkout": False, "asset_source": assets.source},
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
        recovery.append("Run `chmod +x scripts/search_jsonl_history.py scripts/jsonl_ask_first.py scripts/git_jsonl_forensics.py`.")
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
    result = run_subprocess(
        [sys.executable, str(helper), query, "--roots", str(root), "--limit", "5"],
        text=True,
        stdout=PIPE,
        stderr=PIPE,
        check=False,
    )
    payload: dict[str, Any] = {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        pass
    return {"returncode": result.returncode, "payload": payload}


def _check_package_ask_first_launcher_behavior(package_root: Path, *, source_checkout: bool = True) -> ValidationCheck:
    launcher = package_root / "scripts" / "jsonl_ask_first.py"
    if not source_checkout:
        return ValidationCheck(
            id="package_ask_first_launcher_smoke",
            name="Package ask-first launcher smoke",
            status="skip",
            detail="Package ask-first launcher smoke is skipped outside a source checkout.",
            paths=[str(launcher)],
            metadata={"source_checkout": False},
        )
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        return ValidationCheck(
            id="package_ask_first_launcher_smoke",
            name="Package ask-first launcher smoke",
            status="skip",
            detail="Package ask-first launcher smoke is skipped because the launcher is missing or not executable.",
            paths=[str(launcher)],
            recovery=["Restore executable `scripts/jsonl_ask_first.py` before running the behavior smoke."],
        )

    env = os.environ.copy()
    route_result = run_subprocess(
        [sys.executable, str(launcher), "why", "did", "I", "mention", "the", "router?"],
        text=True,
        stdout=PIPE,
        stderr=PIPE,
        check=False,
        cwd=str(package_root),
        env=env,
    )
    route_stdout = route_result.stdout
    installed_first_command = "First command: jsonl-history-search semantic-debug"
    source_first_command = (
        "First command: PYTHONPATH=src python3 -m jsonl_history_search.cli semantic-debug"
    )
    if installed_first_command in route_stdout:
        first_command_mode = "installed_console"
    elif source_first_command in route_stdout:
        first_command_mode = "source_checkout"
    else:
        first_command_mode = "unresolved"
    bounded_payload: dict[str, Any] = {}
    bounded_returncode: int | None = None
    bounded_stderr = ""
    with tempfile.TemporaryDirectory(prefix="jsonl-ask-first-smoke-") as tmp:
        tmp_path = Path(tmp)
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "session.jsonl").write_text(
            '{"timestamp":"2026-05-27T09:00:00+00:00","role":"user","text":"alpha only"}\n',
            encoding="utf-8",
        )
        report_output = tmp_path / "ask-first-no-evidence.json"
        bounded_result = run_subprocess(
            [
                sys.executable,
                str(launcher),
                "did",
                "the",
                "searched",
                "scope",
                "contain",
                "unmatched",
                "phrase?",
                "--cwd",
                str(tmp_path),
                "--root",
                str(root),
                "--allow-arbitrary-root",
                "--hard-cap-files",
                "5",
                "--hard-cap-bytes",
                "10000",
                "--report-output",
                str(report_output),
                "--format",
                "json",
            ],
            text=True,
            stdout=PIPE,
            stderr=PIPE,
            check=False,
            cwd=str(package_root),
            env=env,
        )
        bounded_returncode = bounded_result.returncode
        bounded_stderr = bounded_result.stderr
        try:
            bounded_payload = json.loads(bounded_result.stdout).get("ask_execute", {})
        except json.JSONDecodeError:
            bounded_payload = {}
    ok = (
        route_result.returncode == 0
        and "Ask Route" in route_stdout
        and first_command_mode in {"installed_console", "source_checkout"}
        and "--cwd \"$PWD\"" in route_stdout
        and "--format text" in route_stdout
        and "Read: references/remembered-topic.md" in route_stdout
        and bounded_returncode == 0
        and bounded_payload.get("execution_status") == "executed"
        and bounded_payload.get("selected_route", {}).get("route_family") == "bounded_no_evidence"
        and bounded_payload.get("execution", {}).get("command_family") == "no-evidence-report"
        and bounded_payload.get("answer_action_contract", {}).get("answer_status") == "answer-scoped-no-evidence-now"
    )
    return ValidationCheck(
        id="package_ask_first_launcher_smoke",
        name="Package ask-first launcher smoke",
        status="pass" if ok else "fail",
        detail=(
            "Package ask-first launcher keeps non-bounded prompts route-only and auto-executes bounded no-evidence."
            if ok
            else "Package ask-first launcher did not satisfy the route-only plus bounded no-evidence auto-execute contract."
        ),
        paths=[str(launcher)],
        recovery=[] if ok else ["Run `python3 scripts/jsonl_ask_first.py \"did the searched scope contain unmatched phrase?\" --root <fixture-root> --allow-arbitrary-root --format json` from the package checkout."],
        metadata={
            "route_returncode": route_result.returncode,
            "bounded_returncode": bounded_returncode,
            "stdout_contains_ask_route": "Ask Route" in route_stdout,
            "stdout_contains_first_command": first_command_mode != "unresolved",
            "first_command_mode": first_command_mode,
            "launcher_prefers_installed_console": first_command_mode == "installed_console",
            "non_bounded_route_only": "Ask Execute" not in route_stdout,
            "bounded_execution_status": bounded_payload.get("execution_status"),
            "bounded_command_family": bounded_payload.get("execution", {}).get("command_family"),
            "bounded_answer_status": bounded_payload.get("answer_action_contract", {}).get("answer_status"),
            "stderr": (route_result.stderr + "\n" + bounded_stderr).strip()[:500],
        },
    )


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


def _check_global_codex_install(
    codex_home: Path,
    package_root: Path,
    *,
    source_checkout: bool = True,
    assets: CodexSkillAssets | None = None,
) -> list[ValidationCheck]:
    skill_root = codex_home / "skills" / "jsonl-history-search"
    alias_skill = codex_home / "skills" / "convo" / "SKILL.md"
    skill = skill_root / "SKILL.md"
    references = skill_root / "references"
    helper = skill_root / "scripts" / "search_jsonl_history.py"
    ask_first_launcher = skill_root / "scripts" / "jsonl_ask_first.py"
    package_skill_root = assets.root if assets is not None else package_root / "skills" / "codex" / "jsonl-history-search"
    package_skill = assets.skill if assets is not None else package_skill_root / "SKILL.md"
    package_alias_skill = assets.alias_skill if assets is not None else package_root / "skills" / "codex" / "convo" / "SKILL.md"
    package_references = assets.references if assets is not None else package_skill_root / "references"
    package_helper = assets.search_helper if assets is not None else package_root / "scripts" / "search_jsonl_history.py"
    package_ask_first_launcher = assets.ask_first_helper if assets is not None else package_root / "scripts" / "jsonl_ask_first.py"
    compare_packaged = assets is not None or source_checkout
    checks: list[ValidationCheck] = []
    checks.append(_check_global_backup_skill_dirs(codex_home))
    if skill.is_file():
        checks.append(_global_copy_check("global_skill", "Global Codex skill file", skill, package_skill, compare_packaged=compare_packaged))
    else:
        checks.append(
            ValidationCheck(
                id="global_skill",
                name="Global Codex skill file",
                status="fail",
                detail="Global Codex skill file is missing at the provided --codex-home.",
                paths=[str(skill)],
                recovery=["Run `jsonl-history-search install-codex-skill`."],
            )
        )
    if alias_skill.is_file():
        checks.append(
            _global_copy_check(
                "global_convo_alias_skill",
                "Global Codex Convo alias skill file",
                alias_skill,
                package_alias_skill,
                compare_packaged=compare_packaged,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id="global_convo_alias_skill",
                name="Global Codex Convo alias skill file",
                status="fail",
                detail="Global Codex Convo alias skill file is missing at the provided --codex-home.",
                paths=[str(alias_skill)],
                recovery=["Run `jsonl-history-search install-codex-skill`."],
            )
        )
    if references.is_dir():
        checks.append(
            _global_tree_check(
                "global_references",
                "Global Codex skill references",
                references,
                package_references,
                compare_packaged=compare_packaged,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id="global_references",
                name="Global Codex skill references",
                status="fail",
                detail="Global Codex skill references folder is missing at the provided --codex-home.",
                paths=[str(references)],
                recovery=["Run `jsonl-history-search install-codex-skill` to install the routed references folder."],
            )
        )
    if helper.is_file() and os.access(helper, os.X_OK):
        checks.append(_global_copy_check("global_helper", "Global Codex helper script", helper, package_helper, compare_packaged=compare_packaged))
    else:
        checks.append(
            ValidationCheck(
                id="global_helper",
                name="Global Codex helper executability",
                status="fail",
                detail="Global Codex helper is missing or not executable at the provided --codex-home.",
                paths=[str(helper)],
                recovery=["Run `jsonl-history-search install-codex-skill` to install the helper scripts."],
            )
        )
    if ask_first_launcher.is_file() and os.access(ask_first_launcher, os.X_OK):
        checks.append(
            _global_copy_check(
                "global_ask_first_launcher",
                "Global Codex ask-first launcher",
                ask_first_launcher,
                package_ask_first_launcher,
                compare_packaged=compare_packaged,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id="global_ask_first_launcher",
                name="Global Codex ask-first launcher executability",
                status="fail",
                detail="Global Codex ask-first launcher is missing or not executable at the provided --codex-home.",
                paths=[str(ask_first_launcher)],
                recovery=["Run `jsonl-history-search install-codex-skill` to install the helper scripts."],
            )
        )
    return checks


def _check_global_agent_install(
    agent_home: Path,
    package_root: Path,
    *,
    agent: str,
    id_prefix: str,
    source_checkout: bool,
    assets: AgentSkillAssets | None,
) -> list[ValidationCheck]:
    label = agent.title()
    install_command = f"jsonl-history-search install-agent-skill --agent {agent}"
    skill_root = agent_home / "skills" / "jsonl-history-search"
    alias_skill = agent_home / "skills" / "convo" / "SKILL.md"
    package_skill_root = assets.root if assets is not None else package_root / "skills" / agent / "jsonl-history-search"
    package_alias_skill = assets.alias_skill if assets is not None else package_root / "skills" / agent / "convo" / "SKILL.md"
    targets = {
        "skill": (skill_root / "SKILL.md", package_skill_root / "SKILL.md"),
        "convo_alias_skill": (alias_skill, package_alias_skill),
        "references": (skill_root / "references", package_skill_root / "references"),
        "helper": (
            skill_root / "scripts" / "search_jsonl_history.py",
            assets.search_helper if assets is not None else package_root / "scripts" / "search_jsonl_history.py",
        ),
        "ask_first_launcher": (
            skill_root / "scripts" / "jsonl_ask_first.py",
            assets.ask_first_helper if assets is not None else package_root / "scripts" / "jsonl_ask_first.py",
        ),
    }
    if assets is not None:
        targets["skill"] = (targets["skill"][0], assets.skill)
        targets["references"] = (targets["references"][0], assets.references)
    compare_packaged = assets is not None or source_checkout
    checks = [
        _check_agent_backup_skill_dirs(
            agent_home,
            agent=agent,
            check_id=f"{id_prefix}backup_skill_dirs",
            install_command=install_command,
        )
    ]
    installed, packaged = targets["skill"]
    if installed.is_file():
        checks.append(
            _global_copy_check(
                f"{id_prefix}skill",
                f"Global {label} skill file",
                installed,
                packaged,
                compare_packaged=compare_packaged,
                recovery_command=install_command,
                agent_label=label,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id=f"{id_prefix}skill",
                name=f"Global {label} skill file",
                status="fail",
                detail=f"Global {label} skill file is missing at the provided --{agent}-home.",
                paths=[str(installed)],
                recovery=[f"Run `{install_command}`."],
            )
        )
    installed, packaged = targets["convo_alias_skill"]
    if installed.is_file():
        checks.append(
            _global_copy_check(
                f"{id_prefix}convo_alias_skill",
                f"Global {label} Convo alias skill file",
                installed,
                packaged,
                compare_packaged=compare_packaged,
                recovery_command=install_command,
                agent_label=label,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id=f"{id_prefix}convo_alias_skill",
                name=f"Global {label} Convo alias skill file",
                status="fail",
                detail=f"Global {label} Convo alias skill file is missing at the provided --{agent}-home.",
                paths=[str(installed)],
                recovery=[f"Run `{install_command}`."],
            )
        )
    installed, packaged = targets["references"]
    if installed.is_dir():
        checks.append(
            _global_tree_check(
                f"{id_prefix}references",
                f"Global {label} skill references",
                installed,
                packaged,
                compare_packaged=compare_packaged,
                recovery_command=install_command,
                agent_label=label,
            )
        )
    else:
        checks.append(
            ValidationCheck(
                id=f"{id_prefix}references",
                name=f"Global {label} skill references",
                status="fail",
                detail=f"Global {label} skill references folder is missing at the provided --{agent}-home.",
                paths=[str(installed)],
                recovery=[f"Run `{install_command}` to install the routed references folder."],
            )
        )
    for key, title in (
        ("helper", "helper script"),
        ("ask_first_launcher", "ask-first launcher"),
    ):
        installed, packaged = targets[key]
        check_id = f"{id_prefix}{key}"
        if installed.is_file() and os.access(installed, os.X_OK):
            checks.append(
                _global_copy_check(
                    check_id,
                    f"Global {label} {title}",
                    installed,
                    packaged,
                    compare_packaged=compare_packaged,
                    recovery_command=install_command,
                    agent_label=label,
                )
            )
        else:
            checks.append(
                ValidationCheck(
                    id=check_id,
                    name=f"Global {label} {title} executability",
                    status="fail",
                    detail=f"Global {label} {title} is missing or not executable at the provided --{agent}-home.",
                    paths=[str(installed)],
                    recovery=[f"Run `{install_command}` to install the helper scripts."],
                )
            )
    return checks


def _check_agent_backup_skill_dirs(
    agent_home: Path,
    *,
    agent: str,
    check_id: str,
    install_command: str,
) -> ValidationCheck:
    label = agent.title()
    skills_dir = agent_home / "skills"
    if not skills_dir.is_dir():
        return ValidationCheck(
            id=check_id,
            name=f"Global {label} backup skill directories",
            status="pass",
            detail=f"No {label} skills directory exists at the provided --{agent}-home.",
            paths=[str(skills_dir)],
        )
    backup_dirs = sorted(
        path
        for path in skills_dir.iterdir()
        if path.is_dir()
        and path.name != "jsonl-history-search"
        and path.name.startswith("jsonl-history-search")
    )
    if not backup_dirs:
        return ValidationCheck(
            id=check_id,
            name=f"Global {label} backup skill directories",
            status="pass",
            detail="No duplicate or backup jsonl-history-search skill directories are discoverable.",
            paths=[str(skills_dir)],
        )
    return ValidationCheck(
        id=check_id,
        name=f"Global {label} backup skill directories",
        status="fail",
        detail=(
            f"Duplicate or backup jsonl-history-search skill directories are discoverable beside the active {label} skill."
        ),
        paths=[str(path) for path in backup_dirs],
        recovery=[
            f"Move backup directories out of `{agent_home}/skills`, rerun `{install_command}`, then validate again."
        ],
        metadata={"backup_dir_names": [path.name for path in backup_dirs]},
    )


def _check_global_backup_skill_dirs(codex_home: Path) -> ValidationCheck:
    skills_dir = codex_home / "skills"
    if not skills_dir.is_dir():
        return ValidationCheck(
            id="global_backup_skill_dirs",
            name="Global Codex backup skill directories",
            status="pass",
            detail="No Codex skills directory exists at the provided --codex-home.",
            paths=[str(skills_dir)],
        )

    backup_dirs = sorted(
        path
        for path in skills_dir.iterdir()
        if path.is_dir()
        and path.name != "jsonl-history-search"
        and path.name.startswith("jsonl-history-search")
    )
    if not backup_dirs:
        return ValidationCheck(
            id="global_backup_skill_dirs",
            name="Global Codex backup skill directories",
            status="pass",
            detail="No duplicate or backup jsonl-history-search skill directories are discoverable.",
            paths=[str(skills_dir)],
        )

    return ValidationCheck(
        id="global_backup_skill_dirs",
        name="Global Codex backup skill directories",
        status="fail",
        detail=(
            "Duplicate or backup jsonl-history-search skill directories are discoverable "
            "beside the active global skill; receiving agents may read stale guidance first."
        ),
        paths=[str(path) for path in backup_dirs],
        recovery=[
            (
                "Move backup directories out of the immediate Codex skills directory, "
                "then rerun validation against the same explicit --codex-home root."
            )
        ],
        metadata={
            "backup_dir_names": [path.name for path in backup_dirs],
            "active_skill_dir": str(skills_dir / "jsonl-history-search"),
        },
    )


def _global_copy_check(
    check_id: str,
    name: str,
    installed: Path,
    packaged: Path,
    *,
    compare_packaged: bool,
    recovery_command: str = "jsonl-history-search install-codex-skill",
    agent_label: str = "Codex",
) -> ValidationCheck:
    paths = [str(installed)]
    if not compare_packaged or not packaged.is_file():
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"{name} is present.",
            paths=paths,
            metadata={"package_reference_available": compare_packaged},
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
            detail=f"{name} matches the bundled package copy.",
            paths=paths,
            metadata=metadata,
        )
    return ValidationCheck(
        id=check_id,
        name=name,
        status="fail",
        detail=f"{name} is installed but stale relative to the bundled package copy.",
        paths=paths,
        recovery=[f"Run `{recovery_command}` to refresh the optional {agent_label} skill."],
        metadata=metadata,
    )


def _global_tree_check(
    check_id: str,
    name: str,
    installed: Path,
    packaged: Path,
    *,
    compare_packaged: bool,
    recovery_command: str = "jsonl-history-search install-codex-skill",
    agent_label: str = "Codex",
) -> ValidationCheck:
    paths = [str(installed)]
    if not compare_packaged or not packaged.is_dir():
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"{name} folder is present.",
            paths=paths,
            metadata={"package_reference_available": compare_packaged},
        )

    paths.append(str(packaged))
    installed_hashes = _sha256_tree(installed)
    packaged_hashes = _sha256_tree(packaged)
    metadata = {
        "installed_files": sorted(installed_hashes),
        "package_files": sorted(packaged_hashes),
        "matches_package": installed_hashes == packaged_hashes,
    }
    if installed_hashes == packaged_hashes:
        return ValidationCheck(
            id=check_id,
            name=name,
            status="pass",
            detail=f"{name} folder matches the bundled package copy.",
            paths=paths,
            metadata=metadata,
        )
    return ValidationCheck(
        id=check_id,
        name=name,
        status="fail",
        detail=f"{name} folder is installed but stale relative to the bundled package copy.",
        paths=paths,
        recovery=[f"Run `{recovery_command}` to refresh the optional {agent_label} skill references."],
        metadata=metadata,
    )


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sha256_tree(path: Path) -> dict[str, str]:
    return {
        str(child.relative_to(path)): _sha256_file(child)
        for child in sorted(item for item in path.rglob("*") if item.is_file())
    }


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
