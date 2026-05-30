from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitChange:
    commit: str
    commit_time: str
    author: str
    subject: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _existing_probe_path(path: Path) -> Path:
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return probe


def git_root_for(path: str | Path) -> Path | None:
    probe = _existing_probe_path(Path(path))
    result = _run_git(probe, ["rev-parse", "--show-toplevel"])
    if result is None or result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def last_change_for_path(path: str | Path, *, repo: str | Path | None = None) -> GitChange | None:
    artifact_path = Path(path).resolve(strict=False)
    repo_root = Path(repo).resolve(strict=False) if repo is not None else git_root_for(artifact_path)
    if repo_root is None or not (repo_root / ".git").exists():
        return None
    repo_root = repo_root.resolve(strict=False)
    if not _is_relative_to(artifact_path, repo_root):
        return None

    rel_path = artifact_path.relative_to(repo_root).as_posix()
    result = _run_git(
        repo_root,
        ["log", "-1", "--format=%H%x00%cI%x00%an%x00%s", "--", rel_path],
    )
    if result is None or result.returncode != 0:
        return None
    payload = result.stdout.rstrip("\n")
    if not payload:
        return None
    parts = payload.split("\x00", 3)
    if len(parts) != 4 or not parts[0]:
        return None
    return GitChange(
        commit=parts[0],
        commit_time=parts[1],
        author=parts[2],
        subject=parts[3],
        path=rel_path,
    )
