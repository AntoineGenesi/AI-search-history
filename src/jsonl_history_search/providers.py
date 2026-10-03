"""Provider names, aliases and bounded local-root hints.

Store discovery is separate from provider parsing and native evidence identity.
Rationale: docs/package-map.md#provider-records-and-identity.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ProviderSpec:
    """Small, static description of a supported local history provider."""

    name: str
    aliases: tuple[str, ...]
    formats: tuple[str, ...]
    root_env: tuple[str, ...]
    root_from_args: str
    default_root: Callable[[Path], Path]


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("codex", ("codex-cli", "codex-app"), ("jsonl",), ("JSONL_HISTORY_CODEX_HOME", "JSONL_HISTORY_SOURCE_ROOT", "CODEX_HOME"), "codex_home", lambda _cwd: Path.home() / ".codex"),
    ProviderSpec("claude", ("claude-code",), ("jsonl", "json", "claude"), ("JSONL_HISTORY_CLAUDE_HOME", "CLAUDE_CONFIG_DIR", "CLAUDE_HOME"), "claude_home", lambda _cwd: Path.home() / ".claude"),
    ProviderSpec("gemini", ("gemini-cli",), ("json", "jsonl"), ("JSONL_HISTORY_GEMINI_HOME", "GEMINI_HOME"), "gemini_home", lambda _cwd: Path.home() / ".gemini" / "tmp"),
    ProviderSpec("grok", ("grok-build", "grok-cli"), ("jsonl", "json-sidecar"), ("JSONL_HISTORY_GROK_HOME", "GROK_HOME"), "grok_home", lambda _cwd: Path.home() / ".grok"),
    ProviderSpec("aider", ("aider-chat",), ("markdown",), ("JSONL_HISTORY_AIDER_ROOT", "CASS_AIDER_DATA_ROOT"), "aider_root", lambda cwd: cwd / ".aider.chat.history.md"),
    ProviderSpec("cursor", ("cursor-agent", "cursor-composer"), ("jsonl", "sqlite"), ("JSONL_HISTORY_CURSOR_HOME", "CURSOR_HOME"), "cursor_home", lambda _cwd: Path.home() / ".cursor"),
    ProviderSpec("opencode", ("opencode-cli",), ("sqlite", "legacy-json"), ("JSONL_HISTORY_OPENCODE_ROOT", "OPENCODE_SQLITE_DB", "OPENCODE_STORAGE_ROOT"), "opencode_root", lambda _cwd: Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "opencode"),
)

_BY_ALIAS = {alias: spec for spec in PROVIDERS for alias in (spec.name, *spec.aliases)}


def provider_spec(name: str) -> ProviderSpec:
    try:
        return _BY_ALIAS[name.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"Unsupported history provider: {name}") from exc


def resolve_provider_root(args: Any | None, *, provider: str, cwd: Path) -> Path:
    spec = provider_spec(provider)
    explicit = getattr(args, spec.root_from_args, None) if args is not None else None
    if explicit:
        return Path(explicit).expanduser()
    for variable in spec.root_env:
        value = os.environ.get(variable)
        if value and value.strip():
            return Path(value).expanduser()
    return spec.default_root(cwd).expanduser()


def provider_catalog() -> list[dict[str, object]]:
    return [
        {
            "provider": spec.name,
            "aliases": list(spec.aliases),
            "formats": list(spec.formats),
            "root_env": list(spec.root_env),
        }
        for spec in PROVIDERS
    ]
