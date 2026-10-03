"""Native evidence identity and openable refs, independent of display ordinals.

Physical JSONL lines retain path/line identity. Structured stores carry a native
locator; callers keep source-plan validation at the filesystem read boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def evidence_ref(value: Any, *, session: str | None = None) -> dict[str, Any]:
    get = (
        value.get
        if isinstance(value, Mapping)
        else lambda key, default=None: getattr(value, key, default)
    )
    try:
        line = int(get("line") or 0)
    except (ValueError, TypeError):
        line = 0
    ref = {"path": str(get("path") or ""), "line": line}
    native = get("native_ref") or get("source_ref")
    if isinstance(native, str) and native:
        ref["native_ref"] = native
    for key, item in (
        ("session", get("conversation_id") or get("session") or session),
        ("provider", get("provider")),
        ("native_id", get("native_id")),
    ):
        if item:
            ref[key] = str(item)
    return ref


def evidence_identity(value: Any) -> tuple[str, str]:
    ref = evidence_ref(value)
    return (ref["path"], str(ref.get("native_ref") or ref["line"]))


def native_locator_path(target: str) -> str | None:
    # Providers use a JSON pointer or a fixed SQLite table suffix after '#'.
    # Decode only the path: never interpret the suffix as executable SQL.
    head, marker, _suffix = target.partition("#")
    return head if marker else None


def ref_target(ref: Mapping[str, Any]) -> str | None:
    if ref.get("native_ref"):
        return str(ref["native_ref"])
    path, line = ref.get("path"), ref.get("line")
    return f"{path}:{line}" if path and line else str(path) if path else None
