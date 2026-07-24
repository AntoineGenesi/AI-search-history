from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def history_home_from_args(args: Any | None, *, provider: str) -> Path:
    """Resolve the configured local history root for a Codex/Claude provider."""
    provider_upper = provider.upper()
    explicit_arg = getattr(args, f"{provider}_home", None) if args is not None else None
    configured = explicit_arg or os.environ.get(f"JSONL_HISTORY_{provider_upper}_HOME")
    if provider == "codex" and not configured:
        configured = os.environ.get("JSONL_HISTORY_SOURCE_ROOT")
    configured = configured or os.environ.get(f"{provider_upper}_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / f".{provider}"
