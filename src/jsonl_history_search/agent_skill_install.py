from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any


SKILL_NAME = "jsonl-history-search"
SUPPORTED_AGENTS = ("codex", "claude")


@dataclass(frozen=True)
class AgentSkillAssets:
    root: Path
    skill: Path
    policy: Path
    alias_skill: Path
    alias_policy: Path
    references: Path
    ask_first_helper: Path
    source: str


def default_agent_home(agent: str) -> Path:
    _require_agent(agent)
    if agent == "codex":
        return Path.home() / ".agents"
    configured = os.environ.get("CLAUDE_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def resolve_agent_skill_assets(
    agent: str,
    package_root: Path | None = None,
) -> AgentSkillAssets:
    _require_agent(agent)
    candidates: list[tuple[Path, str]] = []
    if package_root is not None:
        candidates.append((package_root.expanduser().resolve(), "source_checkout"))
    module_root = Path(__file__).resolve().parents[2]
    if not candidates or candidates[0][0] != module_root:
        candidates.append((module_root, "source_checkout"))

    for root, source in candidates:
        assets = _source_checkout_assets(root, agent=agent, source=source)
        if assets is not None:
            return assets

    installed_root = _installed_asset_root(agent)
    if installed_root is not None:
        assets = _assets_from_root(installed_root, agent=agent, source="installed_package")
        if _assets_complete(assets):
            return assets

    raise FileNotFoundError(
        f"{agent.title()} skill assets are unavailable. Reinstall the Python package "
        f"before running `jsonl-history-search install-agent-skill --agent {agent}`."
    )


def install_agent_skill(
    *,
    agent: str,
    agent_home: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, Any]:
    _require_agent(agent)
    target_home = (agent_home or default_agent_home(agent)).expanduser().resolve()
    assets = resolve_agent_skill_assets(agent, package_root)
    skill_root = target_home / "skills" / SKILL_NAME
    alias_root = target_home / "skills" / "convo"
    scripts_root = skill_root / "scripts"
    references_root = skill_root / "references"
    policy_root = skill_root / "agents"
    alias_policy_root = alias_root / "agents"

    scripts_root.mkdir(parents=True, exist_ok=True)
    retired_assets = _retire_owned_search_helper(target_home, scripts_root)
    alias_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assets.skill, skill_root / "SKILL.md")
    shutil.copy2(assets.alias_skill, alias_root / "SKILL.md")
    for source, target in (
        (assets.policy.parent, policy_root),
        (assets.alias_policy.parent, alias_policy_root),
    ):
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    if references_root.exists():
        shutil.rmtree(references_root)
    shutil.copytree(assets.references, references_root)
    _copy_executable(assets.ask_first_helper, scripts_root / "jsonl_ask_first.py")
    route_note = _route_note(agent, target_home)

    return {
        "agent_skill_install": {
            "status": "installed",
            "agent": agent,
            "retired_assets": retired_assets,
            "python_cli": {
                "status": "available",
                "detail": (
                    "The Python package is available because this installer is a "
                    "jsonl-history-search CLI command."
                ),
                "commands": ["jsonl-history-search", "convo"],
            },
            "agent_skill": {
                "status": "installed",
                "optional": True,
                "path": str(skill_root),
                "policy_path": str(policy_root / "openai.yaml"),
                "asset_source": assets.source,
                "route_note": route_note,
            },
            "agent_alias_skill": {
                "status": "installed",
                "optional": True,
                "path": str(alias_root),
                "policy_path": str(alias_policy_root / "openai.yaml"),
                "asset_source": assets.source,
            },
            "privacy": {
                "conversation_logs_read": False,
                "conversation_logs_copied": False,
            },
            "next_commands": [
                _validation_command(agent, target_home),
                "convo --help",
            ],
        }
    }


def _retire_owned_search_helper(target_home: Path, scripts_root: Path) -> list[dict]:
    """Remove only the retired executable, after an owner-only recoverable backup."""
    target = scripts_root / 'search_jsonl_history.py'
    if not target.exists() and not target.is_symlink():
        return []
    if target.is_symlink() or not target.is_file():
        raise ValueError('Retired helper is not a regular file; inspect its exact target before upgrading.')
    backups = target_home / 'jsonl-history-search-retired-assets'
    if scripts_root.is_symlink() or backups.is_symlink():
        raise ValueError('Upgrade directories must be regular local directories; inspect symlink targets first.')
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    before = target.stat()
    digest = hashlib.sha256()
    with target.open('rb') as source, tempfile.NamedTemporaryFile(
            dir=backups, prefix='search_jsonl_history-', suffix='.py', delete=False) as destination:
        for chunk in iter(lambda: source.read(65536), b''):
            digest.update(chunk)
            destination.write(chunk)
        destination.flush()
        os.fsync(destination.fileno())
        backup = Path(destination.name)
    after = target.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Retired helper changed during backup; original preserved, inspect before retrying.')
    target.unlink()
    return [{'source': str(target), 'backup': str(backup), 'sha256': digest.hexdigest(),
             'recoverable': True}]


def render_agent_skill_install(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    install = payload["agent_skill_install"]
    agent_label = install["agent"].title()
    cli = install["python_cli"]
    skill = install["agent_skill"]
    alias = install.get("agent_alias_skill") or {}
    lines = [
        "Python CLI install: AVAILABLE",
        f"  {cli['detail']}",
        f"Optional {agent_label} skill install: INSTALLED",
        f"  Path: {skill['path']}",
        f"  Route: {skill.get('route_note')}",
        "Optional Convo alias skill install: INSTALLED",
        f"  Path: {alias.get('path')}",
        f"  Asset source: {skill['asset_source']}",
        "Privacy: no conversation logs were read or copied.",
        "Validate both layers:",
    ]
    lines.extend(f"  {command}" for command in install["next_commands"])
    return "\n".join(lines)


def _route_note(agent: str, target_home: Path) -> str:
    if agent == "codex":
        return (
            f"Codex skill route under {target_home}/skills. The public user default is "
            "$HOME/.agents/skills; an explicit --agent-home or --codex-home selects a custom "
            "or legacy compatibility root."
        )
    return (
        f"Claude Code skill route under {target_home}/skills; use --agent-home with a project .claude "
        "directory for project-scoped skills."
    )


def _validation_command(agent: str, target_home: Path) -> str:
    option = "--codex-home" if agent == "codex" else "--claude-home"
    return f'jsonl-history-search validate-install {option} "{target_home}" --format text'


def _source_checkout_assets(root: Path, *, agent: str, source: str) -> AgentSkillAssets | None:
    skill_root = root / "skills" / agent / SKILL_NAME
    assets = AgentSkillAssets(
        root=skill_root,
        skill=skill_root / "SKILL.md",
        policy=skill_root / "agents" / "openai.yaml",
        alias_skill=root / "skills" / agent / "convo" / "SKILL.md",
        alias_policy=root / "skills" / agent / "convo" / "agents" / "openai.yaml",
        references=skill_root / "references",
        ask_first_helper=root / "scripts" / "jsonl_ask_first.py",
        source=source,
    )
    return assets if _assets_complete(assets) else None


def _assets_from_root(root: Path, *, agent: str, source: str) -> AgentSkillAssets:
    alias_skill = _installed_alias_skill(agent) or root.parent / f"{agent}-convo-skill" / "SKILL.md"
    return AgentSkillAssets(
        root=root,
        skill=root / "SKILL.md",
        policy=root / "agents" / "openai.yaml",
        alias_skill=alias_skill,
        alias_policy=alias_skill.parent / "agents" / "openai.yaml",
        references=root / "references",
        ask_first_helper=root / "scripts" / "jsonl_ask_first.py",
        source=source,
    )


def _assets_complete(assets: AgentSkillAssets) -> bool:
    return (
        assets.skill.is_file()
        and assets.policy.is_file()
        and assets.alias_skill.is_file()
        and assets.alias_policy.is_file()
        and assets.references.is_dir()
        and any(assets.references.glob("*.md"))
        and assets.ask_first_helper.is_file()
    )


def _installed_asset_root(agent: str) -> Path | None:
    try:
        distribution = metadata.distribution("jsonl-history-search")
    except metadata.PackageNotFoundError:
        return None
    suffix = (Path("share") / "jsonl-history-search" / f"{agent}-skill" / "SKILL.md").as_posix()
    for entry in distribution.files or []:
        if str(entry).replace("\\", "/").endswith(suffix):
            return Path(distribution.locate_file(entry)).resolve().parent
    return None


def _installed_alias_skill(agent: str) -> Path | None:
    try:
        distribution = metadata.distribution("jsonl-history-search")
    except metadata.PackageNotFoundError:
        return None
    suffix = (Path("share") / "jsonl-history-search" / f"{agent}-convo-skill" / "SKILL.md").as_posix()
    for entry in distribution.files or []:
        if str(entry).replace("\\", "/").endswith(suffix):
            return Path(distribution.locate_file(entry)).resolve()
    return None


def _copy_executable(source: Path, target: Path) -> None:
    shutil.copy2(source, target)
    target.chmod(target.stat().st_mode | 0o111)


def _require_agent(agent: str) -> None:
    if agent not in SUPPORTED_AGENTS:
        choices = ", ".join(SUPPORTED_AGENTS)
        raise ValueError(f"Unsupported agent {agent!r}; choose one of: {choices}.")
