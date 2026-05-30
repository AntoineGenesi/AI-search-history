from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .expansion import PREFERENCE_WORDS, TASK_SLUG_RE, task_slug_variants


ARTIFACT_RE = re.compile(
    r"(?:docs|reports|tasks|codex/state/reviews)/[\w./-]+|\b[\w./-]+\.(?:md|html|json|jsonl|csv|txt|py)\b"
)


@dataclass
class ClusterFacets:
    session: str | None = None
    path: str | None = None
    tasks: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)


def facets_for_record(*, path: str, text: str, artifacts: list[str]) -> ClusterFacets:
    all_artifacts = sorted(set(artifacts) | set(ARTIFACT_RE.findall(text.lower())))
    return ClusterFacets(
        session=session_key(path),
        path=path,
        tasks=task_slugs_from_text(" ".join([text, " ".join(all_artifacts)])),
        artifacts=all_artifacts,
        preferences=preference_terms(text),
    )


def task_slugs_from_text(text: str) -> list[str]:
    slugs: set[str] = set()
    lowered = text.lower()
    for match in TASK_SLUG_RE.finditer(lowered):
        prefix, number, _suffix = match.groups()
        slugs.add(f"{prefix}-{number}")
    for variant in task_slug_variants(lowered):
        variant_match = TASK_SLUG_RE.fullmatch(variant)
        if variant_match:
            prefix, number, _suffix = variant_match.groups()
            slugs.add(f"{prefix}-{number}")
    return sorted(slugs)


def preference_terms(text: str) -> list[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return sorted(words & PREFERENCE_WORDS)


def session_key(path: str) -> str | None:
    if not path:
        return None
    parsed = PurePosixPath(path)
    if parsed.suffix == ".jsonl":
        return parsed.stem
    return parsed.name or str(parsed)


def cluster_matches(matches: list[Any]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for match in matches:
        facets = facets_from_match(match)
        cluster_type, cluster_value = primary_cluster(facets)
        key = f"{cluster_type}:{cluster_value}"
        group = grouped.setdefault(
            key,
            {
                "path": match.path,
                "cluster_key": key,
                "cluster_type": cluster_type,
                "cluster_value": cluster_value,
                "session": facets.session,
                "paths": [],
                "tasks": [],
                "preferences": [],
                "top_score": match.score,
                "matches": [],
                "artifacts": [],
            },
        )
        group["top_score"] = max(group["top_score"], match.score)
        group["matches"].append(match.to_dict())
        group["paths"] = sorted(set(group["paths"]) | {match.path})
        group["tasks"] = sorted(set(group["tasks"]) | set(facets.tasks))
        group["artifacts"] = sorted(set(group["artifacts"]) | set(facets.artifacts))
        group["preferences"] = sorted(set(group["preferences"]) | set(facets.preferences))
    return sorted(
        grouped.values(),
        key=lambda item: (item["top_score"], item["cluster_type"], item["cluster_value"]),
        reverse=True,
    )


def facets_from_match(match: Any) -> ClusterFacets:
    return ClusterFacets(
        session=getattr(match, "session", None) or session_key(getattr(match, "path", "")),
        path=getattr(match, "path", None),
        tasks=sorted(set(getattr(match, "tasks", []))),
        artifacts=sorted(set(getattr(match, "artifacts", []))),
        preferences=sorted(set(getattr(match, "preferences", []))),
    )


def primary_cluster(facets: ClusterFacets) -> tuple[str, str]:
    if facets.tasks:
        return "task", facets.tasks[0]
    if facets.artifacts:
        return "artifact", facets.artifacts[0]
    if facets.preferences:
        return "preference", facets.preferences[0]
    if facets.session:
        return "session", facets.session
    return "path", facets.path or "unknown"
