from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .gittools import last_change_for_path


ARTIFACT_EXTENSIONS = (
    "csv",
    "html",
    "json",
    "jsonl",
    "md",
    "pdf",
    "py",
    "toml",
    "txt",
    "yaml",
    "yml",
)

ARTIFACT_PREFIXES = (
    "codex/state/reviews",
    "docs",
    "reports",
    "src",
    "tasks",
    "tests",
)

_EXT_PATTERN = "|".join(re.escape(ext) for ext in ARTIFACT_EXTENSIONS)
_PREFIX_PATTERN = "|".join(re.escape(prefix) for prefix in ARTIFACT_PREFIXES)
_CANDIDATE_RE = re.compile(
    rf"""
    (?P<path>
        (?:[~./A-Za-z0-9_-][~./A-Za-z0-9_-]*\.(?:{_EXT_PATTERN}))
        |
        (?:(?:{_PREFIX_PATTERN})/[~./A-Za-z0-9_-]+)
    )
    """,
    re.VERBOSE,
)
_TRAILING_PUNCTUATION = " \t\r\n`'\"()[]{}<>,;:"


@dataclass(frozen=True)
class ArtifactCandidate:
    raw: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactStatus:
    raw: str
    path: str
    absolute_path: str | None
    root: str | None
    exists: bool
    is_file: bool
    inside_root: bool
    reason: str | None = None
    git_last_change: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def extract_artifact_paths(snippet: str) -> list[ArtifactCandidate]:
    seen: set[str] = set()
    candidates: list[ArtifactCandidate] = []
    for match in _CANDIDATE_RE.finditer(snippet):
        if "://" in snippet[max(0, match.start() - 8) : match.start()]:
            continue
        raw = match.group("path").strip(_TRAILING_PUNCTUATION)
        if not raw or "://" in raw or raw.startswith("//") or snippet[max(0, match.start() - 1) : match.start()] == ":":
            continue
        cleaned = _clean_candidate(raw)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        candidates.append(ArtifactCandidate(raw=raw, path=cleaned))
    return candidates


def inspect_artifacts(
    snippet_or_candidates: str | Iterable[str | ArtifactCandidate],
    *,
    cwd: str | Path,
    repo: str | Path | None = None,
    include_git: bool = True,
) -> list[ArtifactStatus]:
    if isinstance(snippet_or_candidates, str):
        candidates = extract_artifact_paths(snippet_or_candidates)
    else:
        candidates = [
            item if isinstance(item, ArtifactCandidate) else ArtifactCandidate(raw=str(item), path=str(item))
            for item in snippet_or_candidates
        ]
    roots = _trusted_roots(cwd, repo)
    return [_inspect_one(candidate, roots, include_git=include_git) for candidate in candidates]


def resolve_artifact_path(
    path: str,
    *,
    cwd: str | Path,
    repo: str | Path | None = None,
    include_git: bool = True,
) -> ArtifactStatus:
    return inspect_artifacts([path], cwd=cwd, repo=repo, include_git=include_git)[0]


def _clean_candidate(raw: str) -> str:
    while raw.startswith(("./", ".\\")):
        raw = raw[2:]
    return raw.replace("\\", "/")


def _trusted_roots(cwd: str | Path, repo: str | Path | None) -> list[Path]:
    roots: list[Path] = []
    for value in (cwd, repo):
        if value is None:
            continue
        root = Path(value).resolve(strict=False)
        if root not in roots:
            roots.append(root)
    return roots


def _inspect_one(candidate: ArtifactCandidate, roots: list[Path], *, include_git: bool) -> ArtifactStatus:
    unsafe = _unsafe_reason(candidate.path)
    if unsafe is not None:
        return _unsafe_status(candidate, unsafe)
    if not roots:
        return _unsafe_status(candidate, "no trusted cwd or repo root")

    raw_path = Path(candidate.path)
    safe_misses: list[tuple[Path, Path]] = []
    for root in roots:
        target = raw_path if raw_path.is_absolute() else root / raw_path
        resolved = target.resolve(strict=False)
        if not _is_relative_to(resolved, root):
            continue
        if resolved.exists():
            return _status_for(candidate, resolved, root, include_git=include_git)
        safe_misses.append((resolved, root))

    if safe_misses:
        resolved, root = safe_misses[0]
        return _status_for(candidate, resolved, root, include_git=False)
    return _unsafe_status(candidate, "path is outside trusted cwd/repo")


def _status_for(candidate: ArtifactCandidate, resolved: Path, root: Path, *, include_git: bool) -> ArtifactStatus:
    git_change = None
    if include_git and resolved.exists():
        git_change = last_change_for_path(resolved, repo=root) or last_change_for_path(resolved)
    return ArtifactStatus(
        raw=candidate.raw,
        path=candidate.path,
        absolute_path=str(resolved),
        root=str(root),
        exists=resolved.exists(),
        is_file=resolved.is_file(),
        inside_root=True,
        git_last_change=git_change.to_dict() if git_change is not None else None,
    )


def _unsafe_status(candidate: ArtifactCandidate, reason: str) -> ArtifactStatus:
    return ArtifactStatus(
        raw=candidate.raw,
        path=candidate.path,
        absolute_path=None,
        root=None,
        exists=False,
        is_file=False,
        inside_root=False,
        reason=reason,
    )


def _unsafe_reason(path: str) -> str | None:
    if "\x00" in path:
        return "path contains NUL byte"
    parsed = Path(path)
    if any(part == ".." for part in parsed.parts):
        return "path traversal is not allowed"
    if any(part.startswith("~") for part in parsed.parts):
        return "home-relative paths are not allowed"
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
