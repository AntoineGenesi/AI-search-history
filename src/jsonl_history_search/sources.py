from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
            "scope_label": "selected_root",
            "privacy_label": "private_root",
        }


@dataclass
class RejectedRoot:
    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason, "scope_label": "excluded_root"}


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
            "selected_root_count": len(self.roots),
            "private_root_count": len(self.roots),
            "rejected_root_count": len(self.rejected_roots),
            "scope_terms": [
                "selected_root",
                "private_root",
                "searched_scope",
                "excluded_scope",
                "not_searched_scope",
                "cap_hit",
            ],
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
            if danger:
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


def build_jsonl_candidate_discovery(plan: SourcePlan, *, explicit_roots_provided: bool) -> dict:
    warnings: list[str] = []
    if not explicit_roots_provided:
        warnings.append("Discovery requires at least one explicit --root; no default transcript roots were traversed.")
        return {
            "jsonl_candidate_discovery": {
                "schema_version": 1,
                "status": "no_roots",
                "summary": discovery_summary(
                    candidate_count=0,
                    candidate_bytes=0,
                    selected_root_count=0,
                    rejected_root_count=0,
                    cap_hits=empty_cap_hits(),
                ),
                "scope": {
                    "selected_roots": [],
                    "rejected_roots": [],
                    "not_searched_scope": [
                        "No roots were approved for this discovery run.",
                        "Default Codex/Claude transcript roots are not searched by discover-jsonl unless passed with --root.",
                    ],
                },
                "candidates": [],
                "warnings": warnings,
                "privacy_warnings": discovery_privacy_warnings(),
                "limits": plan.limits.to_dict(),
                "limitations": discovery_limitations(),
                "source_locator_handoff": discovery_source_locator_handoff(
                    plan,
                    selected_roots=[],
                    rejected_roots=[],
                    candidates=[],
                    cap_hits=empty_cap_hits(),
                    warnings=warnings,
                ),
            }
        }

    selected_roots = sanitized_selected_roots(plan)
    rejected_roots = sanitized_rejected_roots(plan)
    candidates: list[dict] = []
    candidate_bytes = 0
    observed_warnings = list(plan.warnings)

    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        observed_warnings = list(enum_warnings)
        candidate_bytes += size
        candidates.append(
            {
                "candidate_id": f"candidate_{len(candidates) + 1:03d}",
                "root_label": root_label_for_path(path, plan),
                "source_kind": source_kind_for_path(path, plan),
                "path_label": path_label(path),
                "path_hash": path_hash(path),
                "suffix": path.suffix.lower(),
                "file_type": "jsonl" if path.suffix.lower() == ".jsonl" else "json",
                "file_size_bytes": size,
                "mtime": stat_mtime(path),
                "mtime_utc": mtime_utc(path),
            }
        )

    observed_warnings.extend(plan.warnings)
    warnings.extend(sanitized_discovery_warnings(observed_warnings, plan))
    cap_hits = cap_hits_from_warnings(observed_warnings)
    status = discovery_status(
        selected_count=len(selected_roots),
        rejected_count=len(rejected_roots),
        candidate_count=len(candidates),
    )
    return {
        "jsonl_candidate_discovery": {
            "schema_version": 1,
            "status": status,
            "summary": discovery_summary(
                candidate_count=len(candidates),
                candidate_bytes=candidate_bytes,
                selected_root_count=len(selected_roots),
                rejected_root_count=len(rejected_roots),
                cap_hits=cap_hits,
            ),
            "scope": {
                "selected_roots": selected_roots,
                "rejected_roots": rejected_roots,
                "not_searched_scope": not_searched_scope(plan, cap_hits),
            },
            "candidates": candidates,
            "warnings": warnings,
            "privacy_warnings": discovery_privacy_warnings(plan),
            "limits": plan.limits.to_dict(),
            "limitations": discovery_limitations(),
            "source_locator_handoff": discovery_source_locator_handoff(
                plan,
                selected_roots=selected_roots,
                rejected_roots=rejected_roots,
                candidates=candidates,
                cap_hits=cap_hits,
                warnings=warnings,
            ),
        }
    }


def discovery_status(*, selected_count: int, rejected_count: int, candidate_count: int) -> str:
    if selected_count == 0 and rejected_count > 0:
        return "root_rejected"
    if selected_count == 0:
        return "no_roots"
    if candidate_count == 0:
        return "no_candidates"
    return "ok"


def discovery_summary(
    *,
    candidate_count: int,
    candidate_bytes: int,
    selected_root_count: int,
    rejected_root_count: int,
    cap_hits: dict,
) -> dict:
    return {
        "candidate_count": candidate_count,
        "candidate_bytes": candidate_bytes,
        "selected_root_count": selected_root_count,
        "rejected_root_count": rejected_root_count,
        "cap_hits": cap_hits,
    }


def empty_cap_hits() -> dict:
    return {"file": False, "byte": False, "elapsed": False}


def cap_hits_from_warnings(warnings: list[str]) -> dict:
    lowered = "\n".join(warnings).lower()
    return {
        "file": "max file cap" in lowered,
        "byte": "max byte cap" in lowered,
        "elapsed": "max elapsed cap" in lowered,
    }


def sanitized_selected_roots(plan: SourcePlan) -> list[dict]:
    roots = []
    for index, root in enumerate(plan.roots, 1):
        roots.append(
            {
                "root_label": f"root_{index:03d}",
                "root_hash": path_hash(root.path),
                "kind": root.kind,
                "reason": root.reason,
                "recursive": root.recursive,
                "known": root.known,
                "exists": root.path.expanduser().exists(),
                "scope_label": "selected_root",
                "privacy_label": "private_root",
            }
        )
    return roots


def sanitized_rejected_roots(plan: SourcePlan) -> list[dict]:
    rejected = []
    for index, root in enumerate(plan.rejected_roots, 1):
        rejected.append(
            {
                "root_label": f"rejected_root_{index:03d}",
                "root_hash": hash_text(root.path),
                "reason": root.reason,
                "scope_label": "excluded_root",
            }
        )
    return rejected


def root_label_for_path(path: Path, plan: SourcePlan) -> str:
    for index, root in enumerate(plan.roots, 1):
        base = root.path.expanduser()
        if base.is_file() and safe_resolve(path) == safe_resolve(base):
            return f"root_{index:03d}"
        if base.is_dir() and path_is_inside(path, base):
            return f"root_{index:03d}"
    return "root_unknown"


def path_label(path: Path) -> str:
    return f"path:{safe_path_hash(path)[:16]}"


def path_hash(path: Path) -> str:
    return f"sha256:{safe_path_hash(path)}"


def safe_path_hash(path: Path) -> str:
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def mtime_utc(path: Path) -> str | None:
    mtime = stat_mtime(path)
    if not mtime:
        return None
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def sanitized_discovery_warnings(warnings: list[str], plan: SourcePlan) -> list[str]:
    sanitized = []
    for warning in warnings:
        normalized = sanitize_discovery_warning(warning)
        if normalized and normalized not in sanitized:
            sanitized.append(normalized)
    for root in plan.roots:
        if is_mounted_drive_path(root.path):
            mounted = "Selected root is under a mounted drive; discovery is bounded to the explicit root and caps."
            if mounted not in sanitized:
                sanitized.append(mounted)
    for rejected in plan.rejected_roots:
        if "mounted drive" in rejected.reason:
            mounted = "Mounted-drive root was rejected before traversal."
            if mounted not in sanitized:
                sanitized.append(mounted)
    return sanitized


def sanitize_discovery_warning(warning: str) -> str | None:
    if not warning:
        return None
    if "Arbitrary root enabled" in warning:
        return "Arbitrary root enabled for an explicit selected root; strict hard caps apply."
    if "Stopped enumeration at max file cap" in warning:
        return "Stopped enumeration at max file cap."
    if "Stopped enumeration at max byte cap" in warning:
        return "Stopped enumeration at max byte cap."
    if "Stopped enumeration at max elapsed cap" in warning:
        return "Stopped enumeration at max elapsed cap."
    if "Skipped symlink escape" in warning:
        return "Skipped a symlink escape outside the selected root."
    if "Could not enumerate" in warning:
        return "Could not enumerate one selected directory; some candidate files may be missing."
    return "Discovery warning omitted raw local path details."


def is_mounted_drive_path(path: Path) -> bool:
    return str(safe_resolve(path)).startswith("/mnt/")


def discovery_privacy_warnings(plan: SourcePlan | None = None) -> list[str]:
    warnings = [
        "Discovery is metadata-only: transcript contents are not opened or parsed.",
        "Raw local paths, filenames, session IDs, and transcript text are omitted from the discovery payload.",
        "Candidate discovery only covers explicit approved roots; deleted, unmounted, inaccessible, and unapproved logs remain not searched.",
    ]
    if plan and any(is_mounted_drive_path(root.path) for root in plan.roots):
        warnings.append("Mounted-drive discovery can expose cross-device/private backup shape; keep hard caps narrow.")
    return warnings


def not_searched_scope(plan: SourcePlan, cap_hits: dict) -> list[str]:
    scope = [
        "Roots outside this explicit discovery source plan were not searched.",
        "Deleted, unmounted, inaccessible, and unapproved logs cannot be discovered by this run.",
    ]
    if plan.rejected_roots:
        scope.append("Rejected roots were excluded before traversal.")
    if any(cap_hits.values()):
        scope.append("Enumeration stopped at one or more caps; remaining files under selected roots may be undiscovered.")
    for root in plan.roots:
        if not root.path.expanduser().exists():
            scope.append("A selected root was missing or inaccessible.")
            break
    return scope


def discovery_limitations() -> list[str]:
    return [
        "This command finds JSONL candidate files under selected roots; it does not prove all conversation logs exist.",
        "It does not prove deleted, unmounted, inaccessible, or unapproved logs are absent.",
        "It does not parse transcript contents or classify sessions by provider beyond source-plan/root metadata.",
    ]


def discovery_source_locator_handoff(
    plan: SourcePlan,
    *,
    selected_roots: list[dict],
    rejected_roots: list[dict],
    candidates: list[dict],
    cap_hits: dict,
    warnings: list[str],
) -> dict:
    return {
        "schema_version": 1,
        "source_family": {
            "primary": "jsonl_candidate_discovery",
            "selected_kinds": sorted({root.get("kind", "unknown") for root in selected_roots}),
        },
        "question_family": {
            "primary": "source_location",
            "detected_supported": ["candidate_file_discovery", "root_safety", "bounded_not_searched_scope"],
        },
        "selected_roots": selected_roots,
        "rejected_roots": rejected_roots,
        "caps": {
            "hit": any(cap_hits.values()),
            "caps_hit": sorted(name for name, hit in cap_hits.items() if hit),
            "raw": cap_hits,
            "limits": plan.limits.to_dict(),
        },
        "warnings": warnings,
        "blind_spots": discovery_handoff_blind_spots(plan, cap_hits, candidates),
        "not_searched_scope": not_searched_scope(plan, cap_hits),
        "candidate_ordering": {
            "strategy": "bounded depth-first explicit-root enumeration; per-directory names sorted newest-ish by descending filename",
            "candidate_count": len(candidates),
            "metadata_only": True,
            "top_candidates": [
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "root_label": candidate.get("root_label"),
                    "source_kind": candidate.get("source_kind"),
                    "path_label": candidate.get("path_label"),
                    "path_hash": candidate.get("path_hash"),
                    "file_size_bytes": candidate.get("file_size_bytes"),
                    "mtime_utc": candidate.get("mtime_utc"),
                }
                for candidate in candidates[:8]
            ],
        },
        "next_reads": [
            {
                "kind": "candidate_jsonl_file",
                "target": candidate.get("path_label"),
                "reason": "open this candidate only if its root label and metadata match the source-location question",
            }
            for candidate in candidates[:5]
        ],
        "no_evidence_meaning": {
            "answer_scope": "no_candidate_files_found_or_retained_in_discovery_scope",
            "not_all_history_absence": True,
            "meaning": "No candidates here means no JSONL files were discovered under the explicit approved roots before caps; other roots, deleted logs, unmounted drives, and transcript contents were not searched.",
        },
        "privacy_boundaries": {
            "metadata_only": True,
            "raw_local_paths_filenames_session_ids_or_transcript_text_included": False,
            "raw_private_bridge_payload_included": False,
            "vectors_indexes_or_timestamp_sidecars_created": False,
        },
        "claim_boundaries": {
            "root_cause_claim": "not_claimed",
            "no_broad_value_speed_token_user_benefit_or_jsonl_beats_default_claim": True,
            "measurement_deferred_to": ["jsh-601c", "jsh-601d", "jsh-601e"],
            "no_evidence_is_scope_limited": True,
        },
    }


def discovery_handoff_blind_spots(plan: SourcePlan, cap_hits: dict, candidates: list[dict]) -> list[str]:
    labels: set[str] = set()
    if not plan.roots:
        labels.add("no_selected_roots")
    if plan.rejected_roots:
        labels.add("some_requested_roots_rejected_before_traversal")
    if any(cap_hits.values()):
        labels.add("selected_scope_limited_by_caps")
    if not candidates:
        labels.add("no_candidate_files_in_discovery_scope")
    if any(not root.path.expanduser().exists() for root in plan.roots):
        labels.add("some_selected_roots_missing_or_inaccessible")
    return sorted(labels)


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
