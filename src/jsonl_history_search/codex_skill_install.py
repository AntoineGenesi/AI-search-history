from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any


SKILL_NAME = "jsonl-history-search"
SHARE_ASSET_SUFFIX = Path("share") / "jsonl-history-search" / "codex-skill"


@dataclass(frozen=True)
class CodexSkillAssets:
    root: Path
    skill: Path
    references: Path
    search_helper: Path
    ask_first_helper: Path
    source: str


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def resolve_codex_skill_assets(package_root: Path | None = None) -> CodexSkillAssets:
    candidates: list[tuple[Path, str]] = []
    if package_root is not None:
        candidates.append((package_root.expanduser().resolve(), "source_checkout"))
    module_root = Path(__file__).resolve().parents[2]
    if not candidates or candidates[0][0] != module_root:
        candidates.append((module_root, "source_checkout"))

    for root, source in candidates:
        assets = _source_checkout_assets(root, source=source)
        if assets is not None:
            return assets

    installed_root = _installed_asset_root()
    if installed_root is not None:
        assets = _assets_from_root(installed_root, source="installed_package")
        if _assets_complete(assets):
            return assets

    raise FileNotFoundError(
        "Codex skill assets are unavailable. Reinstall the Python package before "
        "running `jsonl-history-search install-codex-skill`."
    )


def install_codex_skill(
    *,
    codex_home: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, Any]:
    target_home = (codex_home or default_codex_home()).expanduser().resolve()
    assets = resolve_codex_skill_assets(package_root)
    skill_root = target_home / "skills" / SKILL_NAME
    scripts_root = skill_root / "scripts"
    references_root = skill_root / "references"

    scripts_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assets.skill, skill_root / "SKILL.md")
    if references_root.exists():
        shutil.rmtree(references_root)
    shutil.copytree(assets.references, references_root)
    _copy_executable(assets.search_helper, scripts_root / "search_jsonl_history.py")
    _copy_executable(assets.ask_first_helper, scripts_root / "jsonl_ask_first.py")

    return {
        "codex_skill_install": {
            "status": "installed",
            "python_cli": {
                "status": "available",
                "detail": (
                    "The Python package is available because this installer is a "
                    "jsonl-history-search CLI command."
                ),
                "commands": ["jsonl-history-search", "convo"],
            },
            "codex_skill": {
                "status": "installed",
                "optional": True,
                "path": str(skill_root),
                "asset_source": assets.source,
            },
            "privacy": {
                "conversation_logs_read": False,
                "conversation_logs_copied": False,
            },
            "next_commands": [
                f'jsonl-history-search validate-install --codex-home "{target_home}" --format text',
                "convo --help",
            ],
        }
    }


def render_codex_skill_install(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    install = payload["codex_skill_install"]
    cli = install["python_cli"]
    skill = install["codex_skill"]
    lines = [
        "Python CLI install: AVAILABLE",
        f"  {cli['detail']}",
        "Optional Codex skill install: INSTALLED",
        f"  Path: {skill['path']}",
        f"  Asset source: {skill['asset_source']}",
        "Privacy: no conversation logs were read or copied.",
        "Validate both layers:",
    ]
    lines.extend(f"  {command}" for command in install["next_commands"])
    return "\n".join(lines)


def _source_checkout_assets(root: Path, *, source: str) -> CodexSkillAssets | None:
    skill_root = root / "skills" / "codex" / SKILL_NAME
    assets = CodexSkillAssets(
        root=skill_root,
        skill=skill_root / "SKILL.md",
        references=skill_root / "references",
        search_helper=root / "scripts" / "search_jsonl_history.py",
        ask_first_helper=root / "scripts" / "jsonl_ask_first.py",
        source=source,
    )
    return assets if _assets_complete(assets) else None


def _assets_from_root(root: Path, *, source: str) -> CodexSkillAssets:
    return CodexSkillAssets(
        root=root,
        skill=root / "SKILL.md",
        references=root / "references",
        search_helper=root / "scripts" / "search_jsonl_history.py",
        ask_first_helper=root / "scripts" / "jsonl_ask_first.py",
        source=source,
    )


def _assets_complete(assets: CodexSkillAssets) -> bool:
    return (
        assets.skill.is_file()
        and assets.references.is_dir()
        and any(assets.references.glob("*.md"))
        and assets.search_helper.is_file()
        and assets.ask_first_helper.is_file()
    )


def _installed_asset_root() -> Path | None:
    try:
        distribution = metadata.distribution("jsonl-history-search")
    except metadata.PackageNotFoundError:
        return None
    suffix = (SHARE_ASSET_SUFFIX / "SKILL.md").as_posix()
    for entry in distribution.files or []:
        if str(entry).replace("\\", "/").endswith(suffix):
            return Path(distribution.locate_file(entry)).resolve().parent
    return None


def _copy_executable(source: Path, target: Path) -> None:
    shutil.copy2(source, target)
    target.chmod(target.stat().st_mode | 0o111)
