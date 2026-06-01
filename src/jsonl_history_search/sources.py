from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Limits:
    max_files: int = 200
    max_bytes: int = 100 * 1024 * 1024
    max_matches: int = 40
    max_elapsed: float = 15.0
    hard_cap_files: int | None = None
    hard_cap_bytes: int | None = None

    def effective_files(self) -> int:
        return min(self.max_files, self.hard_cap_files) if self.hard_cap_files else self.max_files

    def effective_bytes(self) -> int:
        return min(self.max_bytes, self.hard_cap_bytes) if self.hard_cap_bytes else self.max_bytes

    def to_dict(self) -> dict:
        return {
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "max_matches": self.max_matches,
            "max_elapsed": self.max_elapsed,
            "hard_cap_files": self.hard_cap_files,
            "hard_cap_bytes": self.hard_cap_bytes,
            "effective_files": self.effective_files(),
            "effective_bytes": self.effective_bytes(),
        }


@dataclass
class PlannedRoot:
    kind: str
    path: Path
    reason: str
    recursive: bool = True
    known: bool = True
    estimate: str = "not_enumerated_by_plan"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "reason": self.reason,
            "recursive": self.recursive,
            "known": self.known,
            "estimate": self.estimate,
        }


@dataclass
class RejectedRoot:
    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason}


@dataclass
class SourcePlan:
    roots: list[PlannedRoot] = field(default_factory=list)
    rejected_roots: list[RejectedRoot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)

    def to_dict(self) -> dict:
        return {
            "roots": [root.to_dict() for root in self.roots],
            "rejected_roots": [root.to_dict() for root in self.rejected_roots],
            "warnings": self.warnings,
            "blind_spots": self.blind_spots,
            "limits": self.limits.to_dict(),
        }


def build_source_plan(
    *,
    cwd: Path,
    codex_home: Path,
    claude_home: Path,
    roots: list[Path] | None,
    allow_arbitrary_root: bool,
    limits: Limits,
) -> SourcePlan:
    plan = SourcePlan(limits=limits)
    known = known_roots(codex_home, claude_home)
    requested = roots or [codex_home / "sessions", codex_home / "history.jsonl", claude_home / "projects"]

    if allow_arbitrary_root and (limits.hard_cap_files is None or limits.hard_cap_bytes is None):
        plan.rejected_roots.extend(RejectedRoot(str(root), "arbitrary root override requires --hard-cap-files and --hard-cap-bytes") for root in requested)
        return plan

    for requested_root in requested:
        for root in normalize_requested_root(requested_root, codex_home, claude_home):
            resolved = safe_resolve(root)
            danger = dangerous_reason(resolved, cwd)
            if danger and not allow_arbitrary_root:
                plan.rejected_roots.append(RejectedRoot(str(root), danger))
                continue
            kind = known.get(resolved)
            if not kind and not allow_arbitrary_root:
                plan.rejected_roots.append(RejectedRoot(str(root), "not a known Codex/Claude transcript root; pass --allow-arbitrary-root with explicit hard caps"))
                continue
            if allow_arbitrary_root and not kind:
                plan.warnings.append(f"Arbitrary root enabled for {root}; strict hard caps apply.")
            plan.roots.append(
                PlannedRoot(
                    kind=kind or arbitrary_root_kind(root),
                    path=root,
                    reason="known transcript root" if kind else "explicit arbitrary root override",
                    recursive=root.suffix not in {".jsonl", ".json"},
                    known=bool(kind),
                )
            )

    if not plan.roots:
        plan.blind_spots.append("No transcript roots selected; search will not open JSONL files.")
    for root in plan.roots:
        if not root.path.exists():
            plan.blind_spots.append(f"Missing transcript root: {root.path}")
    return plan


def known_roots(codex_home: Path, claude_home: Path) -> dict[Path, str]:
    return {
        safe_resolve(codex_home / "sessions"): "codex_sessions",
        safe_resolve(codex_home / "history.jsonl"): "codex_history",
        safe_resolve(claude_home / "projects"): "claude_projects",
    }


def normalize_requested_root(root: Path, codex_home: Path, claude_home: Path) -> list[Path]:
    resolved = safe_resolve(root)
    if resolved == safe_resolve(codex_home):
        return [codex_home / "sessions", codex_home / "history.jsonl"]
    if resolved == safe_resolve(claude_home):
        return [claude_home / "projects"]
    return [root]


def arbitrary_root_kind(root: Path) -> str:
    if root.suffix == ".json":
        return "arbitrary_json_file"
    return "arbitrary_jsonl_root"


def safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def dangerous_reason(path: Path, cwd: Path) -> str | None:
    home = Path.home().resolve()
    cwd_resolved = safe_resolve(cwd)
    text = str(path)
    if text in {"/", str(home), "/home", "/mnt", "/mnt/c", "/mnt/d"}:
        return "dangerous broad root rejected before traversal"
    if re.fullmatch(r"[A-Za-z]:[/\\]?", text):
        return "drive root rejected before traversal"
    if text.startswith("/mnt/") and len(Path(text).parts) <= 3:
        return "mounted drive root rejected before traversal"
    if path == cwd_resolved or (path / ".git").exists():
        return "repo root rejected before traversal"
    model_markers = {"cache", ".cache", "models", "huggingface", "torch", "npm", "node_modules"}
    if any(part in model_markers for part in path.parts):
        return "cache/model directory rejected before traversal"
    return None


def path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def enumerate_jsonl_files(plan: SourcePlan):
    started = time.monotonic()
    files_seen = 0
    bytes_seen = 0
    warnings = plan.warnings
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_file():
            candidates = [base] if base.suffix in {".jsonl", ".json"} else []
        elif base.is_dir():
            candidates = iter_directory_jsonl_files(base, plan, started, warnings)
        else:
            continue
        for path in candidates:
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return warnings
            if files_seen >= plan.limits.effective_files():
                warnings.append("Stopped enumeration at max file cap.")
                return warnings
            if not path_is_inside(path, base if base.is_dir() else base.parent):
                warnings.append(f"Skipped symlink escape: {path}")
                continue
            size = stat_size(path)
            if bytes_seen + size > plan.limits.effective_bytes():
                warnings.append("Stopped enumeration at max byte cap.")
                return warnings
            files_seen += 1
            bytes_seen += size
            yield path, size, warnings


def source_kind_for_path(path: Path, plan: SourcePlan) -> str:
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_file() and path == base:
            return root.kind
        if base.is_dir() and path_is_inside(path, base):
            return root.kind
    if path.suffix == ".json":
        return "unknown_json"
    return "unknown_jsonl"


def iter_directory_jsonl_files(base: Path, plan: SourcePlan, started: float, warnings: list[str]):
    pending = [base]
    while pending:
        if time.monotonic() - started > plan.limits.max_elapsed:
            warnings.append("Stopped enumeration at max elapsed cap.")
            return
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                current_entries = list(entries)
        except OSError as exc:
            warnings.append(f"Could not enumerate {directory}: {exc}")
            continue
        dirs = []
        files = []
        for entry in current_entries:
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
                    files.append(Path(entry.path))
            except OSError:
                continue
        # Per-directory ordering preserves predictable newest-ish traversal for
        # date-shaped transcript trees without building a whole-root candidate list.
        files.sort(key=lambda item: item.name, reverse=True)
        dirs.sort(key=lambda item: item.name)
        pending.extend(reversed(dirs))
        for path in files:
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return
            yield path


def stat_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def stat_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
