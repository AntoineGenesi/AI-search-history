from __future__ import annotations

import re
from typing import Any


AUDIT_DEPTH_CHOICES = ("quick", "in-depth", "in_depth", "deep", "exhaustive")


def normalize_audit_depth(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold().replace("_", "-")
    if normalized == "quick":
        return "quick"
    if normalized in {"in-depth", "deep"}:
        return "in_depth"
    if normalized == "exhaustive":
        return "exhaustive"
    raise ValueError(f"unsupported audit depth: {value}")


def audit_depth_label(depth: str) -> str:
    return "in-depth" if depth == "in_depth" else depth


def resolve_audit_depth(query: str, requested: str | None = None) -> dict[str, Any]:
    explicit_depth = normalize_audit_depth(requested)
    if explicit_depth:
        return _audit_depth_payload(
            explicit_depth,
            source="explicit",
            requested=requested,
            reason="selected by --audit-depth",
        )

    normalized = _normalize_query(query)
    if _looks_like_exhaustive_audit(normalized):
        return _audit_depth_payload(
            "exhaustive",
            source="inferred_strong_audit_wording",
            requested=None,
            reason=(
                "strong audit wording asks for every/all worker, subagent, "
                "session, trace, or conversation in scope"
            ),
        )
    if _looks_like_in_depth_audit(normalized):
        return _audit_depth_payload(
            "in_depth",
            source="inferred_strong_audit_wording",
            requested=None,
            reason="strong audit wording asks for an in-depth/granular audit",
        )
    return _audit_depth_payload(
        "quick",
        source="default",
        requested=None,
        reason="ordinary/default quick history route; no strong audit-depth wording",
    )


def _audit_depth_payload(depth: str, *, source: str, requested: str | None, reason: str) -> dict[str, Any]:
    contract = {
        "quick": "quick_view",
        "in_depth": "in_depth_audit",
        "exhaustive": "exhaustive_scoped_audit",
    }[depth]
    return {
        "depth": depth,
        "label": audit_depth_label(depth),
        "contract": contract,
        "source": source,
        "requested": requested,
        "reason": reason,
    }


def _normalize_query(query: str) -> str:
    lowered = query.casefold()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _looks_like_exhaustive_audit(normalized: str) -> bool:
    if not normalized:
        return False
    if "exhaustive" in normalized and "audit" in normalized:
        return True
    if re.search(r"\bfully audit\b", normalized):
        return True
    if re.search(r"\bfull audit\b", normalized):
        return True
    return bool(
        re.search(
            r"\b(?:audit|inspect|review|check)\b.{0,60}\b(?:every|all)\b.{0,60}"
            r"\b(?:subagent|subagents|worker|workers|agent|agents|trace|traces|conversation|conversations|session|sessions)\b",
            normalized,
        )
        or re.search(
            r"\b(?:every|all)\b.{0,60}"
            r"\b(?:subagent|subagents|worker|workers|agent|agents|trace|traces|conversation|conversations|session|sessions)\b"
            r".{0,60}\b(?:audit|inspect|review|check)\b",
            normalized,
        )
    )


def _looks_like_in_depth_audit(normalized: str) -> bool:
    if not normalized or "audit" not in normalized:
        return False
    return bool(
        re.search(r"\b(?:in depth|deep|granular|thorough|comprehensive)\b.{0,40}\baudit\b", normalized)
        or re.search(r"\baudit\b.{0,40}\b(?:in depth|deep|granular|thorough|comprehensive)\b", normalized)
    )
