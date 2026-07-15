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


@dataclass(frozen=True)
class GitCommit:
    commit: str
    commit_time: str
    author_time: str
    author: str
    committer: str
    subject: str
    decorations: str
    parents: list[str]
    parent_count: int
    contained_branches: list[str]
    changed_files: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GitLifecycleCommit:
    commit: str
    commit_time: str
    author: str
    subject: str

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


def current_branch(repo: str | Path) -> str | None:
    repo_root = Path(repo).resolve(strict=False)
    result = _run_git(repo_root, ["branch", "--show-current"])
    if result is None or result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def commit_metadata(repo: str | Path, commit: str) -> GitCommit | None:
    repo_root = Path(repo).resolve(strict=False)
    result = _run_git(
        repo_root,
        ["show", "-s", "--format=%H%x00%cI%x00%aI%x00%an%x00%cn%x00%s%x00%D%x00%P", commit],
    )
    if result is None or result.returncode != 0:
        return None
    payload = result.stdout.rstrip("\n")
    parts = payload.split("\x00", 7)
    if len(parts) != 8 or not parts[0]:
        return None
    parents = [part for part in parts[7].split() if part]
    return GitCommit(
        commit=parts[0],
        commit_time=parts[1],
        author_time=parts[2],
        author=parts[3],
        committer=parts[4],
        subject=parts[5],
        decorations=parts[6],
        parents=parents,
        parent_count=len(parents),
        contained_branches=branches_containing_commit(repo_root, parts[0]),
        changed_files=commit_changed_files(repo_root, parts[0]),
    )


def branches_containing_commit(repo: str | Path, commit: str) -> list[str]:
    repo_root = Path(repo).resolve(strict=False)
    result = _run_git(repo_root, ["branch", "--contains", commit])
    if result is None or result.returncode != 0:
        return []
    branches = []
    for line in result.stdout.splitlines():
        branch = line.strip().lstrip("*+ ").strip()
        if branch:
            branches.append(branch)
    return sorted(set(branches))


def commit_changed_files(repo: str | Path, commit: str) -> list[dict]:
    repo_root = Path(repo).resolve(strict=False)
    result = _run_git(repo_root, ["show", "--format=", "--name-status", "--no-renames", commit])
    if result is None or result.returncode != 0:
        return []
    files: list[dict] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        files.append({"status": status, "path": path})
    return files


def branch_metadata(repo: str | Path, branch: str) -> dict | None:
    repo_root = Path(repo).resolve(strict=False)
    result = _run_git(repo_root, ["rev-parse", "--verify", branch])
    if result is None or result.returncode != 0:
        return None
    head = result.stdout.strip()
    merged = _run_git(repo_root, ["branch", "--merged", branch])
    merged_branches = []
    if merged is not None and merged.returncode == 0:
        merged_branches = [line.strip().lstrip("* ") for line in merged.stdout.splitlines() if line.strip()]
    return {"name": branch, "head": head, "merged_branches": sorted(set(merged_branches))}


def commit_lifecycle(repo: str | Path, ref: str, *, max_commits: int = 5) -> list[dict]:
    repo_root = Path(repo).resolve(strict=False)
    limit = max(1, min(int(max_commits), 25))
    result = _run_git(
        repo_root,
        ["log", f"--max-count={limit}", "--format=%H%x00%cI%x00%an%x00%s", ref],
    )
    if result is None or result.returncode != 0:
        return []
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00", 3)
        if len(parts) != 4 or not parts[0]:
            continue
        rows.append(
            GitLifecycleCommit(
                commit=parts[0],
                commit_time=parts[1],
                author=parts[2],
                subject=parts[3],
            ).to_dict()
        )
    return rows


def merge_commits_for_task(repo: str | Path, task_id: str, *, max_commits: int = 5) -> list[dict]:
    repo_root = Path(repo).resolve(strict=False)
    normalized_task = str(task_id or "").strip().upper()
    if not normalized_task:
        return []
    limit = max(1, min(int(max_commits), 10))
    result = _run_git(
        repo_root,
        [
            "log",
            "--merges",
            f"--grep={normalized_task}",
            f"--max-count={limit}",
            "--format=%H%x00%cI%x00%an%x00%s",
            "--all",
        ],
    )
    if result is None or result.returncode != 0:
        return []
    rows: list[dict] = []
    for line in result.stdout.splitlines():
        parts = line.split("\x00", 3)
        if len(parts) != 4 or not parts[0]:
            continue
        rows.append(
            GitLifecycleCommit(
                commit=parts[0],
                commit_time=parts[1],
                author=parts[2],
                subject=parts[3],
            ).to_dict()
        )
    return rows


def worktree_metadata(repo: str | Path) -> dict | None:
    repo_root = Path(repo).resolve(strict=False)
    top = _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    if top is None or top.returncode != 0:
        return None
    git_common = _run_git(repo_root, ["rev-parse", "--git-common-dir"])
    is_bare = _run_git(repo_root, ["rev-parse", "--is-bare-repository"])
    worktree = top.stdout.strip()
    common_dir = git_common.stdout.strip() if git_common is not None and git_common.returncode == 0 else ""
    bare = is_bare.stdout.strip() if is_bare is not None and is_bare.returncode == 0 else "false"
    return {
        "path": worktree or str(repo_root),
        "git_common_dir": common_dir or None,
        "is_bare": bare == "true",
        "is_linked_worktree": bool(common_dir and common_dir != ".git" and "worktrees" in Path(common_dir).parts),
    }


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
