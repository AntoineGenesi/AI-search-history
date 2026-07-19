from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_skill_install import (
    AgentSkillAssets,
    default_agent_home,
    install_agent_skill,
    render_agent_skill_install,
    resolve_agent_skill_assets,
)


CodexSkillAssets = AgentSkillAssets


def default_codex_home() -> Path:
    return default_agent_home("codex")


def resolve_codex_skill_assets(package_root: Path | None = None) -> CodexSkillAssets:
    return resolve_agent_skill_assets("codex", package_root)


def install_codex_skill(
    *,
    codex_home: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, Any]:
    generic = install_agent_skill(
        agent="codex",
        agent_home=codex_home,
        package_root=package_root,
    )["agent_skill_install"]
    legacy = dict(generic)
    legacy["codex_skill"] = legacy.pop("agent_skill")
    legacy.pop("agent", None)
    return {"codex_skill_install": legacy}


def render_codex_skill_install(payload: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, indent=2, sort_keys=True)
    legacy = dict(payload["codex_skill_install"])
    generic = dict(legacy)
    generic["agent"] = "codex"
    generic["agent_skill"] = generic.pop("codex_skill")
    return render_agent_skill_install({"agent_skill_install": generic}, output_format)
