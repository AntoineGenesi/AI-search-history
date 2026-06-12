from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .query import QueryIntent
from .sources import SourcePlan, dangerous_reason, safe_resolve


RG_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    size: int


@dataclass(frozen=True)
class RgPrefilterResult:
    files: list[PlannedFile]
    stats: dict


def filter_planned_files(
    *,
    plan: SourcePlan,
    intent: QueryIntent,
    files: Iterable[tuple[Path, int]],
    cwd: str | None = None,
) -> RgPrefilterResult:
    planned = [PlannedFile(path=path, size=size) for path, size in files]
    stats = _base_stats(planned)
    root_warning = _dangerous_root_warning(plan, cwd)
    if root_warning:
        stats.update({"status": "fallback", "fallback_reason": root_warning})
        return RgPrefilterResult(files=planned, stats=stats)

    needles = _prefilter_needles(intent)
    stats["needle_policy"] = "literal_anchors"
    stats["needle_count"] = len(needles)
    if not needles:
        stats.update({"status": "fallback", "fallback_reason": "ineligible_query"})
        return RgPrefilterResult(files=planned, stats=stats)
    stats["eligible"] = True

    rg_path = shutil.which("rg")
    if not rg_path:
        stats.update({"status": "fallback", "fallback_reason": "rg_missing", "available": False})
        return RgPrefilterResult(files=planned, stats=stats)
    stats["available"] = True
    stats["attempted"] = bool(planned)
    if not planned:
        stats.update({"status": "used", "fallback_reason": None})
        return RgPrefilterResult(files=[], stats=stats)

    by_resolved = {_safe_key(item.path): item for item in planned}
    command = [
        rg_path,
        "--files-with-matches",
        "--fixed-strings",
        "--ignore-case",
        "--no-messages",
    ]
    for needle in needles:
        command.extend(["--regexp", needle])
    command.extend(["--", *[str(item.path) for item in planned]])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=RG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        stats.update({"status": "fallback", "fallback_reason": "rg_timeout"})
        return RgPrefilterResult(files=planned, stats=stats)
    except OSError:
        stats.update({"status": "fallback", "fallback_reason": "rg_exec_error"})
        return RgPrefilterResult(files=planned, stats=stats)

    if completed.returncode not in {0, 1}:
        stats.update({"status": "fallback", "fallback_reason": "rg_failure"})
        return RgPrefilterResult(files=planned, stats=stats)

    matched: list[PlannedFile] = []
    seen: set[Path] = set()
    malformed = 0
    for line in completed.stdout.splitlines():
        path_text = line.strip()
        if not path_text:
            continue
        key = _safe_key(Path(path_text))
        item = by_resolved.get(key)
        if item is None:
            malformed += 1
            continue
        if key not in seen:
            matched.append(item)
            seen.add(key)
    if malformed:
        stats.update({"status": "fallback", "fallback_reason": "rg_untrusted_paths"})
        return RgPrefilterResult(files=planned, stats=stats)

    stats.update(_reduction_stats(planned, matched))
    stats.update({"status": "used", "fallback_reason": None})
    return RgPrefilterResult(files=matched, stats=stats)


def _base_stats(planned: list[PlannedFile]) -> dict:
    candidate_bytes = sum(item.size for item in planned)
    return {
        "enabled": True,
        "available": None,
        "attempted": False,
        "eligible": False,
        "status": "pending",
        "fallback_reason": None,
        "candidate_files": len(planned),
        "candidate_bytes": candidate_bytes,
        "matched_files": len(planned),
        "matched_bytes": candidate_bytes,
        "files_reduced": 0,
        "bytes_reduced": 0,
        "files_reduction_ratio": 0.0,
        "bytes_reduction_ratio": 0.0,
        "needle_policy": None,
        "needle_count": 0,
        "used_as_final_evidence": False,
    }


def _reduction_stats(planned: list[PlannedFile], matched: list[PlannedFile]) -> dict:
    candidate_files = len(planned)
    candidate_bytes = sum(item.size for item in planned)
    matched_files = len(matched)
    matched_bytes = sum(item.size for item in matched)
    files_reduced = max(0, candidate_files - matched_files)
    bytes_reduced = max(0, candidate_bytes - matched_bytes)
    return {
        "matched_files": matched_files,
        "matched_bytes": matched_bytes,
        "files_reduced": files_reduced,
        "bytes_reduced": bytes_reduced,
        "files_reduction_ratio": _ratio(files_reduced, candidate_files),
        "bytes_reduction_ratio": _ratio(bytes_reduced, candidate_bytes),
    }


def _prefilter_needles(intent: QueryIntent) -> list[str]:
    anchors: set[str] = set()
    for value in [*intent.artifact_hints, *intent.base_terms, *intent.terms]:
        item = " ".join(value.strip().split())
        if item and _literal_prefilter_eligible(item):
            anchors.add(item)
    if not anchors and _literal_prefilter_eligible(intent.normalized):
        anchors.add(" ".join(intent.normalized.strip().split()))
    return sorted(anchors, key=lambda item: (-len(item), item))


def _literal_prefilter_eligible(value: str) -> bool:
    if not _fixed_term_safe(value) or len(value) < 4:
        return False
    if re.search(r"(?:^|/)(?:tasks|docs|reports|reviews|src|tests)/[\w./-]+", value):
        return True
    if re.search(r"\.(?:md|html|json|jsonl|csv|txt|py|sh)$", value):
        return True
    if re.fullmatch(r"[a-z]{2,12}-\d+[a-z]?(?:-[a-z0-9]+){1,12}", value):
        return True
    return False


def _fixed_term_safe(value: str) -> bool:
    return 2 < len(value) <= 240 and "\x00" not in value


def _dangerous_root_warning(plan: SourcePlan, cwd: str | None) -> str | None:
    cwd_path = Path(cwd).expanduser() if cwd else Path.cwd()
    for root in plan.roots:
        reason = dangerous_reason(safe_resolve(root.path), cwd_path)
        if reason:
            return _safe_root_reason(reason)
    return None


def _safe_root_reason(reason: str) -> str:
    lowered = reason.lower()
    if "dangerous broad root" in lowered:
        return "root_rejected_dangerous_broad_root"
    if "mounted drive root" in lowered:
        return "root_rejected_mounted_drive_root"
    if "drive root" in lowered:
        return "root_rejected_drive_root"
    if "repo root" in lowered:
        return "root_rejected_repo_root"
    if "cache/model" in lowered:
        return "root_rejected_cache_or_model_root"
    return "root_rejected_unsafe"


def _safe_key(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0
