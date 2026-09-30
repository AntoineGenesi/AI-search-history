from __future__ import annotations

from pathlib import Path
from typing import Any

from .providers import resolve_provider_root


def history_home_from_args(args: Any | None, *, provider: str) -> Path:
    """Resolve a provider root through the static provider registry."""
    cwd = Path(getattr(args, "cwd", ".") or ".").expanduser().resolve() if args is not None else Path.cwd()
    return resolve_provider_root(args, provider=provider, cwd=cwd)
