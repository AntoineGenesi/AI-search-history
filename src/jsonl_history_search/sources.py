from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from .expansion import task_slug_variants
from .records import parse_record


PATH_DATE_PART_RE = re.compile(r"^(\d{4})(?:[-_](\d{2})(?:[-_](\d{2}))?)?$")
YEAR_PART_RE = re.compile(r"^\d{4}$")
MONTH_OR_DAY_PART_RE = re.compile(r"^\d{2}$")
LATEST_SESSION_TIMESTAMP_TAIL_BYTES = 64 * 1024
LATEST_SESSION_TIMESTAMP_MAX_LINES = 120
WINDOWS_DRIVE_PATH_RE = re.compile(r"^([A-Za-z]):[\\/]*(.*)$")
WINDOWS_ENV_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


@dataclass
class Limits:
    max_files: int = 200
    max_bytes: int = 100 * 1024 * 1024
    max_matches: int = 40
    max_elapsed: float = 15.0
    hard_cap_files: int | None = None
    hard_cap_bytes: int | None = None
    bm25_max_candidate_rows: int = 5000
    bm25_query_timeout: float | None = None

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
            "bm25_max_candidate_rows": self.bm25_max_candidate_rows,
            "bm25_query_timeout": self.bm25_query_timeout,
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
    source_role: str = "transcript_root"
    provider: str = ""
    app: str = ""
    platform: str = ""
    root_format: str = ""

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
            "source_role": self.source_role,
            "provider": self.provider,
            "app": self.app,
            "platform": self.platform,
            "root_format": self.root_format,
        }


@dataclass
class RejectedRoot:
    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason, "scope_label": "excluded_root"}


@dataclass(frozen=True)
class RootCandidate:
    kind: str
    path: Path
    reason: str
    source_role: str
    provider: str
    app: str
    platform: str
    root_format: str
    recursive: bool = True
    known: bool = True
    include_when_missing: bool = True

    def planned_root(self) -> PlannedRoot:
        return PlannedRoot(
            kind=self.kind,
            path=self.path,
            reason=self.reason,
            recursive=self.recursive,
            known=self.known,
            source_role=self.source_role,
            provider=self.provider,
            app=self.app,
            platform=self.platform,
            root_format=self.root_format,
        )


@dataclass
class RootDiagnostic:
    kind: str
    path: Path
    reason: str
    source_role: str
    provider: str
    app: str
    platform: str
    root_format: str
    exists: bool
    recursive: bool = True
    known: bool = True
    status: str = "detected"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "reason": self.reason,
            "source_role": self.source_role,
            "provider": self.provider,
            "app": self.app,
            "platform": self.platform,
            "root_format": self.root_format,
            "exists": self.exists,
            "recursive": self.recursive,
            "known": self.known,
            "status": self.status,
            "scope_label": "diagnostic_root" if self.source_role == "diagnostic_only" else "candidate_root",
            "privacy_label": "private_root",
        }


@dataclass
class SourcePlan:
    roots: list[PlannedRoot] = field(default_factory=list)
    rejected_roots: list[RejectedRoot] = field(default_factory=list)
    diagnostic_roots: list[RootDiagnostic] = field(default_factory=list)
    missing_roots: list[RootDiagnostic] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blind_spots: list[str] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    scope_resolution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "roots": [root.to_dict() for root in self.roots],
            "rejected_roots": [root.to_dict() for root in self.rejected_roots],
            "diagnostic_roots": [root.to_dict() for root in self.diagnostic_roots],
            "missing_roots": [root.to_dict() for root in self.missing_roots],
            "warnings": self.warnings,
            "blind_spots": self.blind_spots,
            "limits": self.limits.to_dict(),
            "scope_resolution": self.scope_resolution,
            "selected_root_count": len(self.roots),
            "private_root_count": len(self.roots),
            "rejected_root_count": len(self.rejected_roots),
            "diagnostic_root_count": len(self.diagnostic_roots),
            "missing_root_count": len(self.missing_roots),
            "root_role_counts": root_role_counts(self),
            "scope_terms": [
                "selected_root",
                "private_root",
                "searched_scope",
                "excluded_scope",
                "not_searched_scope",
                "cap_hit",
            ],
        }


@dataclass(frozen=True)
class PlannedJsonlFiles:
    files: tuple[tuple[Path, int], ...]
    warnings: tuple[str, ...]
    elapsed_ms: float
    source: str = "shared_per_command_source_enumeration"

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def byte_count(self) -> int:
        return sum(size for _path, size in self.files)

    def to_diagnostics(self) -> dict:
        return {
            "source": self.source,
            "file_count": self.file_count,
            "byte_count": self.byte_count,
            "elapsed_ms": self.elapsed_ms,
            "cap_hits": cap_hits_from_warnings(list(self.warnings)),
            "cap_labels": cap_labels_from_warnings(list(self.warnings)),
            "raw_paths_included": False,
        }


def build_command_local_source_catalog(plan: SourcePlan, planned_jsonl_files: PlannedJsonlFiles) -> dict:
    source_kind_counts: dict[str, int] = {}
    for path, _size in planned_jsonl_files.files:
        source_kind = source_kind_for_path(path, plan)
        source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
    cap_labels = cap_labels_from_warnings(list(planned_jsonl_files.warnings))
    return {
        "schema_version": 1,
        "source": "command_local_source_catalog",
        "basis": planned_jsonl_files.source,
        "file_count": planned_jsonl_files.file_count,
        "byte_count": planned_jsonl_files.byte_count,
        "source_kind_counts": dict(sorted(source_kind_counts.items())),
        "cap_labels": cap_labels,
        "cap_hits": cap_hits_from_warnings(list(planned_jsonl_files.warnings)),
        "selected_file_count": planned_jsonl_files.file_count,
        "skipped_file_count": 0,
        "selected_root_count": len(plan.roots),
        "rejected_root_count": len(plan.rejected_roots),
        "warning_count": len(planned_jsonl_files.warnings),
        "elapsed_ms": planned_jsonl_files.elapsed_ms,
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "persistent_index_or_cache_used": False,
    }


def build_command_local_source_plan(
    plan: SourcePlan,
    planned_jsonl_files: PlannedJsonlFiles,
    intent: Any,
) -> tuple[PlannedJsonlFiles, dict]:
    date_eligible: list[tuple[int, Path, int, str]] = []
    pruned_hard = 0
    prune_reasons: dict[str, int] = {}

    for ordinal, (path, size) in enumerate(planned_jsonl_files.files):
        date_bucket = path_date_bucket(path, intent)
        if date_bucket is None:
            date_eligible.append((ordinal, path, size, "unknown_or_undated"))
            continue
        bucket_start, bucket_end = date_bucket
        if date_bucket_disjoint_from_intent(bucket_start, bucket_end, intent):
            pruned_hard += 1
            reason = "path_date_bucket_disjoint_from_intent_date_window"
            prune_reasons[reason] = prune_reasons.get(reason, 0) + 1
            continue
        date_eligible.append((ordinal, path, size, "date_overlaps_intent"))

    tiered = assign_budgeted_source_tiers(plan, date_eligible, intent)
    selected_files = [(path, size) for _ordinal, path, size, _tier in tiered["selected"]]
    deferred_budget = len(tiered["deferred"])
    selected_ordinals = {ordinal for ordinal, _path, _size, _tier in tiered["selected"]}
    unknown_or_undated_selected = sum(
        1 for ordinal, _path, _size, date_status in date_eligible if date_status == "unknown_or_undated" and ordinal in selected_ordinals
    )

    selected = PlannedJsonlFiles(
        files=tuple(selected_files),
        warnings=planned_jsonl_files.warnings,
        elapsed_ms=planned_jsonl_files.elapsed_ms,
        source="command_local_source_plan_selected_files",
    )
    cap_hits = cap_hits_from_warnings(list(planned_jsonl_files.warnings))
    diagnostics = {
        "schema_version": 1,
        "source": "command_local_source_plan",
        "basis": "command_local_source_catalog",
        "selection_policy": "date_disjoint_hard_prune_then_deterministic_budgeted_source_tiers",
        "sources_total": planned_jsonl_files.file_count,
        "sources_selected": selected.file_count,
        "sources_pruned_hard": pruned_hard,
        "sources_deferred_budget": deferred_budget,
        "sources_unknown_or_undated_selected": unknown_or_undated_selected,
        "byte_count_total": planned_jsonl_files.byte_count,
        "byte_count_selected": selected.byte_count,
        "prune_reasons": dict(sorted(prune_reasons.items())),
        "tier_order": SOURCE_TIER_ORDER,
        "source_tiers": tiered["source_tiers"],
        "deferred_reason_counts": tiered["deferred_reason_counts"],
        "budget_basis": tiered["budget_basis"],
        "retry_guidance": tiered["retry_guidance"],
        "materialization_budget": {
            "max_files": plan.limits.max_files,
            "max_bytes": plan.limits.max_bytes,
            "max_elapsed": plan.limits.max_elapsed,
            "effective_files": plan.limits.effective_files(),
            "effective_bytes": plan.limits.effective_bytes(),
        },
        "exhaustive_available": not any(cap_hits.values()) and deferred_budget == 0,
        "date_window": {
            "present": bool(getattr(intent, "date_window_start", None) and getattr(intent, "date_window_end", None)),
            "start": getattr(intent, "date_window_start", None).isoformat()
            if getattr(intent, "date_window_start", None)
            else None,
            "end": getattr(intent, "date_window_end", None).isoformat()
            if getattr(intent, "date_window_end", None)
            else None,
            "raw_query_text_included": False,
        },
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_session_ids_included": False,
        "raw_cwd_included": False,
        "privacy_flags": {
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
        },
        "persistent_index_or_cache_used": False,
    }
    return selected, diagnostics


SOURCE_TIER_ORDER = [
    "hard_required",
    "structural_exact",
    "lexical_probe",
    "fallback_recent_or_broad",
    "deferred",
]


def assign_budgeted_source_tiers(
    plan: SourcePlan,
    eligible_files: list[tuple[int, Path, int, str]],
    intent: Any,
) -> dict[str, Any]:
    budget_basis = source_tier_budget_basis(plan, eligible_files)
    max_selected_files = int(budget_basis["max_selected_files"])
    max_selected_bytes = int(budget_basis["max_selected_bytes"])
    tier_buckets: dict[str, list[tuple[int, Path, int]]] = {tier: [] for tier in SOURCE_TIER_ORDER if tier != "deferred"}
    date_overlap_counts = {"date_overlaps_intent": 0, "unknown_or_undated": 0}

    for ordinal, path, size, date_status in eligible_files:
        date_overlap_counts[date_status] = date_overlap_counts.get(date_status, 0) + 1
        tier = source_tier_for_path(path, plan, intent)
        tier_buckets[tier].append((ordinal, path, size))

    selected: list[tuple[int, Path, int, str]] = []
    deferred: list[tuple[int, Path, int, str]] = []
    selected_bytes = 0
    for tier in SOURCE_TIER_ORDER:
        if tier in {"deferred", "lexical_probe"}:
            continue
        for ordinal, path, size in sorted(tier_buckets.get(tier, []), key=lambda item: item[0]):
            if len(selected) >= max_selected_files or selected_bytes + size > max_selected_bytes:
                deferred.append((ordinal, path, size, tier))
                continue
            selected.append((ordinal, path, size, tier))
            selected_bytes += size

    deferred_reason_counts: dict[str, int] = {}
    for _ordinal, _path, _size, tier in deferred:
        reason = f"budget_exhausted_after_{tier}"
        deferred_reason_counts[reason] = deferred_reason_counts.get(reason, 0) + 1

    selected_tier_counts: dict[str, int] = {tier: 0 for tier in SOURCE_TIER_ORDER}
    deferred_from_tier_counts: dict[str, int] = {tier: 0 for tier in SOURCE_TIER_ORDER}
    eligible_tier_counts: dict[str, int] = {tier: 0 for tier in SOURCE_TIER_ORDER}
    for tier, items in tier_buckets.items():
        eligible_tier_counts[tier] = len(items)
    for _ordinal, _path, _size, tier in selected:
        selected_tier_counts[tier] += 1
    for _ordinal, _path, _size, tier in deferred:
        deferred_from_tier_counts[tier] += 1
    eligible_tier_counts["deferred"] = len(deferred)
    selected_tier_counts["deferred"] = 0
    deferred_from_tier_counts["deferred"] = len(deferred)

    return {
        "selected": selected,
        "deferred": deferred,
        "source_tiers": {
            "order": SOURCE_TIER_ORDER,
            "selection_order": [tier for tier in SOURCE_TIER_ORDER if tier not in {"lexical_probe", "deferred"}],
            "stable_ordering": "planned_source_enumeration_order_within_each_tier",
            "lexical_probe": {
                "enabled": False,
                "reason": "no_already_built_cheap_source_probe_available",
                "selected_count": 0,
            },
            "eligible_counts": eligible_tier_counts,
            "selected_counts": selected_tier_counts,
            "deferred_counts": deferred_from_tier_counts,
            "date_overlap_counts": dict(sorted(date_overlap_counts.items())),
            "raw_paths_included": False,
            "raw_text_included": False,
            "raw_query_text_included": False,
            "raw_session_ids_included": False,
            "raw_cwd_included": False,
        },
        "deferred_reason_counts": dict(sorted(deferred_reason_counts.items())),
        "budget_basis": budget_basis,
        "retry_guidance": source_tier_retry_guidance(len(deferred)),
    }


def source_tier_budget_basis(plan: SourcePlan, eligible_files: list[tuple[int, Path, int, str]]) -> dict[str, Any]:
    eligible_count = len(eligible_files)
    effective_files = plan.limits.effective_files()
    effective_bytes = plan.limits.effective_bytes()
    max_matches = max(1, int(plan.limits.max_matches or 1))
    return {
        "policy": "min_effective_file_cap_and_max_matches_source_budget",
        "max_selected_files": max(0, min(eligible_count, effective_files, max_matches)),
        "max_selected_bytes": effective_bytes,
        "eligible_source_count_after_hard_prune": eligible_count,
        "effective_files": effective_files,
        "effective_bytes": effective_bytes,
        "max_matches": int(plan.limits.max_matches),
        "uses_scoring_weights": False,
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def source_tier_retry_guidance(deferred_count: int) -> dict[str, Any]:
    return {
        "needed": deferred_count > 0,
        "deferred_source_count": deferred_count,
        "guidance": (
            "Rerun with a narrower artifact/task/date/root hint or raise safe --max-matches/--max-files caps "
            "before treating no-evidence as exhaustive."
            if deferred_count > 0
            else "No budget-deferred source retry needed for this source plan."
        ),
        "raw_paths_included": False,
        "raw_text_included": False,
    }


def source_tier_for_path(path: Path, plan: SourcePlan, intent: Any) -> str:
    path_text = path.as_posix().lower()
    source_kind = source_kind_for_path(path, plan).lower()
    hard_terms = hard_required_source_terms(intent)
    if any(term and term in path_text for term in hard_terms):
        return "hard_required"

    structural_terms = structural_source_terms(intent)
    structural_space = f"{path_text} {source_kind} {root_label_for_path(path, plan).lower()}"
    if any(term and term in structural_space for term in structural_terms):
        return "structural_exact"

    return "fallback_recent_or_broad"


def hard_required_source_terms(intent: Any) -> list[str]:
    terms: list[str] = []
    for hint in getattr(intent, "artifact_hints", []) or []:
        normalized = str(hint).lower().strip()
        if "/" in normalized or "." in normalized:
            terms.extend(source_hint_variants(normalized))
        terms.extend(task_slug_variants(normalized))
    for value in [getattr(intent, "original", ""), getattr(intent, "normalized", "")]:
        terms.extend(task_slug_variants(str(value)))
    return unique_source_terms(terms)


def structural_source_terms(intent: Any) -> list[str]:
    terms: list[str] = []
    query_plan = getattr(intent, "query_plan", None)
    if query_plan is not None:
        terms.extend(str(term).lower().strip() for term in getattr(query_plan, "anchor_terms", []) or [])
        field_hints = getattr(query_plan, "field_hints", {}) or {}
        for hint in field_hints.get("artifact_paths", []) or []:
            terms.extend(source_hint_variants(str(hint).lower().strip()))
            terms.extend(task_slug_variants(str(hint)))
    terms.extend(str(term).lower().strip() for term in getattr(intent, "base_terms", []) or [])
    terms.extend(str(term).lower().strip() for term in getattr(intent, "artifact_hints", []) or [])
    return unique_source_terms(terms, min_length=4)


def source_hint_variants(value: str) -> list[str]:
    normalized = value.lower().strip()
    if not normalized:
        return []
    path = Path(normalized)
    variants = {normalized, path.name}
    if path.stem:
        variants.add(path.stem)
        variants.add(path.stem.replace("-", "_"))
        variants.add(path.stem.replace("_", "-"))
    return sorted(variants)


def unique_source_terms(values: list[str], *, min_length: int = 1) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = " ".join(str(value).lower().strip().split())
        if len(normalized) < min_length or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def path_date_bucket(path: Path, intent: Any) -> tuple[datetime, datetime] | None:
    window_start = getattr(intent, "date_window_start", None)
    if window_start is None:
        return None
    tzinfo = window_start.tzinfo
    parts = path.parts
    for index, part in enumerate(parts):
        if YEAR_PART_RE.fullmatch(part):
            year = int(part)
            if index + 1 < len(parts) and MONTH_OR_DAY_PART_RE.fullmatch(parts[index + 1]):
                month = int(parts[index + 1])
                if index + 2 < len(parts) and MONTH_OR_DAY_PART_RE.fullmatch(parts[index + 2]):
                    day = int(parts[index + 2])
                    bucket = _safe_bucket(year, month, day, tzinfo=tzinfo)
                    if bucket is not None:
                        return bucket
                bucket = _safe_bucket(year, month, None, tzinfo=tzinfo)
                if bucket is not None:
                    return bucket
            bucket = _safe_bucket(year, None, None, tzinfo=tzinfo)
            if bucket is not None:
                return bucket
        match = PATH_DATE_PART_RE.fullmatch(part) or PATH_DATE_PART_RE.fullmatch(Path(part).stem)
        if match:
            year = int(match.group(1))
            month = int(match.group(2)) if match.group(2) else None
            day = int(match.group(3)) if match.group(3) else None
            bucket = _safe_bucket(year, month, day, tzinfo=tzinfo)
            if bucket is not None:
                return bucket
    return None


def _safe_bucket(year: int, month: int | None, day: int | None, *, tzinfo) -> tuple[datetime, datetime] | None:
    if year < 1900 or year > 2100:
        return None
    try:
        if month is None:
            start = datetime(year, 1, 1, tzinfo=tzinfo)
            return start, datetime(year + 1, 1, 1, tzinfo=tzinfo)
        if day is None:
            start = datetime(year, month, 1, tzinfo=tzinfo)
            end = datetime(year + 1, 1, 1, tzinfo=tzinfo) if month == 12 else datetime(year, month + 1, 1, tzinfo=tzinfo)
            return start, end
        start = datetime(year, month, day, tzinfo=tzinfo)
        return start, start + timedelta(days=1)
    except ValueError:
        return None


def date_bucket_disjoint_from_intent(bucket_start: datetime, bucket_end: datetime, intent: Any) -> bool:
    window_start = getattr(intent, "date_window_start", None)
    window_end = getattr(intent, "date_window_end", None)
    if window_start is None or window_end is None:
        return False
    local_start = bucket_start.astimezone(window_start.tzinfo)
    local_end = bucket_end.astimezone(window_start.tzinfo)
    return local_end <= window_start or local_start >= window_end


def build_source_plan(
    *,
    cwd: Path,
    codex_home: Path,
    claude_home: Path,
    roots: list[Path] | None,
    allow_arbitrary_root: bool,
    limits: Limits,
    current_session_only: bool = False,
    provider_roots: dict[str, Path] | None = None,
) -> SourcePlan:
    plan = SourcePlan(limits=limits)
    codex_home = normalize_local_path(codex_home)
    claude_home = normalize_local_path(claude_home)
    normalized_roots = [normalize_local_path(root) for root in roots] if roots is not None else None
    provider_roots = {key: normalize_local_path(value) for key, value in (provider_roots or {}).items()}
    candidates = known_root_candidates(codex_home, claude_home, cwd=cwd, provider_roots=provider_roots)
    known = known_roots(codex_home, claude_home, cwd=cwd, candidates=candidates, provider_roots=provider_roots)
    record_root_inventory(plan, candidates)
    if normalized_roots is None and current_session_only:
        latest, latest_warning = latest_known_session_file(codex_home, claude_home, root_candidates=candidates)
        if latest is None:
            plan.scope_resolution = {
                "status": "ambiguous_current_session",
                "strategy": "no_default_broad_history_sweep",
                "reason": "current-session wording was detected, but no known Codex/Claude session JSONL file was discoverable",
            }
            plan.blind_spots.append(
                "Current-session wording detected, but no known session JSONL file was found; rerun with an exact --root session JSONL."
            )
            if latest_warning:
                plan.warnings.append(latest_warning)
            return plan
        normalized_roots = [latest]
        plan.scope_resolution = {
            "status": "scoped_to_latest_known_session",
            "strategy": "latest_known_codex_or_claude_session_file",
            "reason": "current-session wording detected; broad default history sweep was avoided",
        }
        plan.warnings.append("Current-session wording detected; scoped to the latest known session file by default.")
        if latest_warning:
            plan.warnings.append(latest_warning)
    elif normalized_roots is None:
        normalized_roots = [candidate.path for candidate in candidates if candidate.source_role == "transcript_root"]

    if allow_arbitrary_root and (limits.hard_cap_files is None or limits.hard_cap_bytes is None):
        plan.rejected_roots.extend(
            RejectedRoot(str(root), "arbitrary root override requires --hard-cap-files and --hard-cap-bytes")
            for root in normalized_roots
        )
        return plan

    for requested_root in normalized_roots:
        for root in normalize_requested_root(requested_root, codex_home, claude_home, provider_roots=provider_roots):
            if requested_root_uses_symlinked_provider_root(root, codex_home, claude_home):
                plan.rejected_roots.append(RejectedRoot(str(root), "symlinked known transcript root rejected before traversal"))
                continue
            resolved = safe_resolve(root)
            candidate = known_candidate_for_resolved_path(resolved, known)
            kind = candidate.kind if candidate else None
            if candidate and candidate.source_role != "transcript_root":
                plan.rejected_roots.append(
                    RejectedRoot(str(root), "known cache/log/diagnostic-only root; not selected as a transcript root")
                )
                continue
            danger = dangerous_reason(resolved, cwd)
            if danger and not (kind and danger == "cache/model directory rejected before traversal"):
                plan.rejected_roots.append(RejectedRoot(str(root), danger))
                continue
            if not kind and not allow_arbitrary_root:
                plan.rejected_roots.append(RejectedRoot(str(root), "not a known provider transcript root; pass --allow-arbitrary-root with explicit hard caps"))
                continue
            if allow_arbitrary_root and not kind:
                plan.warnings.append(f"Arbitrary root enabled for {root}; strict hard caps apply.")
            if candidate:
                plan.roots.append(candidate.planned_root())
            else:
                plan.roots.append(
                    PlannedRoot(
                        kind=arbitrary_root_kind(root),
                        path=root,
                        reason="explicit arbitrary root override",
                        recursive=root.suffix not in {".jsonl", ".json", ".md"},
                        known=False,
                        source_role="transcript_root",
                        provider="unknown",
                        app="unknown",
                        platform=platform_for_path(root),
                        root_format="json" if root.suffix == ".json" else "markdown" if root.suffix == ".md" else "jsonl",
                    )
                )

    if not plan.roots:
        plan.blind_spots.append("No transcript roots selected; search will not open history files.")
    for root in plan.roots:
        if not root.path.exists():
            plan.blind_spots.append(f"Missing transcript root: {root.path}")
    return plan


def root_role_counts(plan: SourcePlan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for root in plan.roots:
        counts[root.source_role] = counts.get(root.source_role, 0) + 1
    for root in plan.diagnostic_roots:
        counts[root.source_role] = counts.get(root.source_role, 0) + 1
    for root in plan.missing_roots:
        key = f"missing_{root.source_role}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def record_root_inventory(plan: SourcePlan, candidates: list[RootCandidate]) -> None:
    for candidate in candidates:
        exists = candidate.path.expanduser().exists()
        if candidate.source_role == "diagnostic_only":
            if exists:
                plan.diagnostic_roots.append(root_diagnostic(candidate, exists=True, status="detected"))
            continue
        if not exists and candidate.include_when_missing:
            plan.missing_roots.append(root_diagnostic(candidate, exists=False, status="missing"))


def root_diagnostic(candidate: RootCandidate, *, exists: bool, status: str) -> RootDiagnostic:
    return RootDiagnostic(
        kind=candidate.kind,
        path=candidate.path,
        reason=candidate.reason,
        source_role=candidate.source_role,
        provider=candidate.provider,
        app=candidate.app,
        platform=candidate.platform,
        root_format=candidate.root_format,
        exists=exists,
        recursive=candidate.recursive,
        known=candidate.known,
        status=status,
    )


def known_root_candidates(
    codex_home: Path,
    claude_home: Path,
    *,
    cwd: Path,
    provider_roots: dict[str, Path] | None = None,
) -> list[RootCandidate]:
    codex_home = normalize_local_path(codex_home)
    claude_home = normalize_local_path(claude_home)
    candidates: list[RootCandidate] = []
    candidates.extend(codex_root_candidates(codex_home, platform=platform_for_path(codex_home)))
    candidates.extend(claude_code_root_candidates(claude_home, platform=platform_for_path(claude_home)))
    roots = provider_roots or {}
    if "gemini" in roots:
        candidates.append(
            RootCandidate(
                kind="gemini_sessions",
                path=roots["gemini"],
                reason="known Gemini CLI session root",
                source_role="transcript_root",
                provider="gemini",
                app="gemini_cli",
                platform=platform_for_path(roots["gemini"]),
                root_format="json_or_jsonl",
            )
        )
    if "grok" in roots:
        grok_sessions = roots["grok"] if roots["grok"].name == "sessions" else roots["grok"] / "sessions"
        candidates.append(
            RootCandidate(
                kind="grok_sessions",
                path=grok_sessions,
                reason="known Grok Build compound session root",
                source_role="transcript_root",
                provider="grok",
                app="grok_build",
                platform=platform_for_path(grok_sessions),
                root_format="compound_jsonl",
            )
        )
    if "aider" in roots:
        candidates.append(
            RootCandidate(
                kind="aider_history",
                path=roots["aider"],
                reason="known Aider Markdown history root",
                source_role="transcript_root",
                provider="aider",
                app="aider",
                platform=platform_for_path(roots["aider"]),
                root_format="markdown",
                recursive=roots["aider"].suffix != ".md",
            )
        )
    if "cursor" in roots:
        cursor_root = roots["cursor"]
        candidates.append(
            RootCandidate(
                kind="cursor_storage",
                path=cursor_root,
                reason="known Cursor Agent JSONL and Composer SQLite root",
                source_role="transcript_root",
                provider="cursor",
                app="cursor",
                platform=platform_for_path(cursor_root),
                root_format="jsonl_or_sqlite",
                recursive=cursor_root.suffix.lower() not in {".jsonl", ".vscdb", ".db"},
            )
        )
        if safe_resolve(cursor_root) == safe_resolve(Path.home() / ".cursor"):
            config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            for user_root in (
                config_home / "Cursor" / "User",
                Path.home() / "Library" / "Application Support" / "Cursor" / "User",
            ):
                if user_root.exists():
                    candidates.append(
                        RootCandidate(
                            kind="cursor_storage",
                            path=user_root,
                            reason="detected Cursor User root with Composer SQLite state",
                            source_role="transcript_root",
                            provider="cursor",
                            app="cursor",
                            platform=platform_for_path(user_root),
                            root_format="sqlite",
                            include_when_missing=False,
                        )
                    )
    if "opencode" in roots:
        opencode_root = roots["opencode"]
        candidates.append(
            RootCandidate(
                kind="opencode_storage",
                path=opencode_root,
                reason="known OpenCode SQLite or legacy JSON storage root",
                source_role="transcript_root",
                provider="opencode",
                app="opencode_cli",
                platform=platform_for_path(opencode_root),
                root_format="sqlite_or_json",
                recursive=opencode_root.suffix.lower() not in {".db", ".sqlite", ".sqlite3", ".json"},
            )
        )
        default_data_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "opencode"
        if safe_resolve(opencode_root) == safe_resolve(default_data_root):
            config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode"
            if config_root.exists():
                candidates.append(
                    RootCandidate(
                        kind="opencode_storage",
                        path=config_root,
                        reason="detected OpenCode config-root database candidate",
                        source_role="transcript_root",
                        provider="opencode",
                        app="opencode_cli",
                        platform=platform_for_path(config_root),
                        root_format="sqlite_or_json",
                        include_when_missing=False,
                    )
                )

    default_codex = is_default_provider_home(codex_home, "codex")
    default_claude = is_default_provider_home(claude_home, "claude")
    explicit_windows_profiles = windows_user_profile_candidates(cwd=cwd, include_auto=False)
    auto_windows_profiles = windows_user_profile_candidates(
        cwd=cwd,
        include_auto=default_codex or default_claude,
    )
    windows_profiles = unique_paths(explicit_windows_profiles + auto_windows_profiles)
    include_windows_codex = default_codex or bool(explicit_windows_profiles)
    include_windows_claude = default_claude or bool(explicit_windows_profiles)
    if include_windows_codex:
        for profile in windows_profiles:
            candidates.extend(
                codex_root_candidates(
                    profile / ".codex",
                    platform="windows_wsl_mount",
                    kind_prefix="windows_",
                    include_posix_history=False,
                )
            )
            candidates.extend(codex_app_diagnostic_candidates(profile))
    if include_windows_claude:
        for profile in windows_profiles:
            candidates.extend(
                claude_code_root_candidates(
                    profile / ".claude",
                    platform="windows_wsl_mount",
                    kind_prefix="windows_",
                )
            )
            candidates.extend(claude_desktop_json_candidates(profile))

    candidates.extend(claude_desktop_json_candidates(Path.home(), platform=platform_for_path(Path.home())))
    return unique_candidates(candidates)


def codex_root_candidates(
    codex_home: Path,
    *,
    platform: str,
    kind_prefix: str = "",
    include_posix_history: bool = True,
) -> list[RootCandidate]:
    app = "codex_app" if kind_prefix == "windows_" else "codex_cli"
    candidates = [
        RootCandidate(
            kind=f"{kind_prefix}codex_sessions",
            path=codex_home / "sessions",
            reason="known Codex transcript root",
            source_role="transcript_root",
            provider="codex",
            app=app,
            platform=platform,
            root_format="jsonl",
        ),
    ]
    if kind_prefix == "windows_":
        candidates.extend(
            [
                RootCandidate(
                    kind="windows_codex_archived_sessions",
                    path=codex_home / "archived_sessions",
                    reason="known Windows Codex archived transcript root",
                    source_role="transcript_root",
                    provider="codex",
                    app=app,
                    platform=platform,
                    root_format="jsonl",
                ),
                RootCandidate(
                    kind="windows_codex_transcription_history",
                    path=codex_home / "transcription-history.jsonl",
                    reason="known Windows Codex transcription history JSONL",
                    source_role="transcript_root",
                    provider="codex",
                    app=app,
                    platform=platform,
                    root_format="jsonl",
                    recursive=False,
                ),
                RootCandidate(
                    kind="windows_codex_session_index",
                    path=codex_home / "session_index.jsonl",
                    reason="Windows Codex session index metadata; diagnostic only until parsed as transcript evidence",
                    source_role="diagnostic_only",
                    provider="codex",
                    app=app,
                    platform=platform,
                    root_format="jsonl_metadata",
                    recursive=False,
                    include_when_missing=False,
                ),
            ]
        )
    if include_posix_history:
        candidates.insert(
            1,
            RootCandidate(
                kind=f"{kind_prefix}codex_history",
                path=codex_home / "history.jsonl",
                reason="known Codex prompt history JSONL",
                source_role="transcript_root",
                provider="codex",
                app=app,
                platform=platform,
                root_format="jsonl",
                recursive=False,
            ),
        )
    candidates.extend(codex_state_diagnostic_candidates(codex_home, platform=platform, app=app, kind_prefix=kind_prefix))
    return candidates


def claude_code_root_candidates(claude_home: Path, *, platform: str, kind_prefix: str = "") -> list[RootCandidate]:
    return [
        RootCandidate(
            kind=f"{kind_prefix}claude_projects",
            path=claude_home / "projects",
            reason="known Claude Code transcript root",
            source_role="transcript_root",
            provider="claude",
            app="claude_code",
            platform=platform,
            root_format="jsonl",
        )
    ]


def codex_state_diagnostic_candidates(
    codex_home: Path,
    *,
    platform: str,
    app: str,
    kind_prefix: str = "",
) -> list[RootCandidate]:
    candidates: list[RootCandidate] = []
    for name in (
        "state.sqlite",
        "state_5.sqlite",
        "goals.sqlite",
        "goals_1.sqlite",
        "logs.sqlite",
        "logs_2.sqlite",
    ):
        path = codex_home / name
        if not path.exists():
            continue
        candidates.append(
            RootCandidate(
                kind=f"{kind_prefix}codex_state_db",
                path=path,
                reason="Codex SQLite state/cache metadata; diagnostic only, not transcript JSONL",
                source_role="diagnostic_only",
                provider="codex",
                app=app,
                platform=platform,
                root_format="sqlite",
                recursive=False,
                include_when_missing=False,
            )
        )
    return candidates


def codex_app_diagnostic_candidates(profile: Path) -> list[RootCandidate]:
    candidates: list[RootCandidate] = []
    for path, kind, root_format in (
        (profile / "AppData" / "Roaming" / "Codex" / "logs", "windows_codex_app_logs", "logs"),
        (profile / "AppData" / "Local" / "Codex" / "logs", "windows_codex_app_logs", "logs"),
        (profile / "AppData" / "Local" / "Codex" / "cache", "windows_codex_app_cache", "cache"),
        (profile / "AppData" / "Roaming" / "OpenAI" / "Codex" / "logs", "windows_codex_app_logs", "logs"),
        (profile / "AppData" / "Local" / "OpenAI" / "Codex" / "cache", "windows_codex_app_cache", "cache"),
    ):
        if not path.exists():
            continue
        candidates.append(
            RootCandidate(
                kind=kind,
                path=path,
                reason="Codex desktop app cache/log location detected; diagnostic only, not a transcript root",
                source_role="diagnostic_only",
                provider="codex",
                app="codex_app",
                platform="windows_wsl_mount",
                root_format=root_format,
                include_when_missing=False,
            )
        )
    return candidates


def claude_desktop_json_candidates(profile: Path, *, platform: str = "windows_wsl_mount") -> list[RootCandidate]:
    roots = []
    configured_home = os.environ.get("JSONL_HISTORY_CLAUDE_DESKTOP_HOME")
    configured_json = os.environ.get("JSONL_HISTORY_CLAUDE_DESKTOP_JSON")
    for raw in (configured_json, configured_home):
        if raw:
            roots.append(normalize_local_path(Path(raw)))
    roots.extend(
        [
            profile / "AppData" / "Local" / "Claude-3p" / "local-agent-mode-sessions",
            profile / "AppData" / "Roaming" / "Claude-3p" / "local-agent-mode-sessions",
            profile / "Library" / "Application Support" / "Claude-3p" / "local-agent-mode-sessions",
        ]
    )
    candidates: list[RootCandidate] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_dir():
            candidates.append(
                RootCandidate(
                    kind="claude_desktop_json_directory",
                    path=root,
                    reason="Claude Desktop 3P local-agent-mode-sessions directory detected; local_*.json files are parsed as transcript state",
                    source_role="transcript_root",
                    provider="claude",
                    app="claude_desktop",
                    platform=platform_for_path(root) if platform == "windows_wsl_mount" else platform,
                    root_format="json",
                    include_when_missing=False,
                )
            )
        elif root.suffix == ".json":
            candidates.append(
                RootCandidate(
                    kind="claude_desktop_json_sessions",
                    path=root,
                    reason="Claude Desktop 3P local session JSON file detected",
                    source_role="transcript_root",
                    provider="claude",
                    app="claude_desktop",
                    platform=platform_for_path(root) if platform == "windows_wsl_mount" else platform,
                    root_format="json",
                    recursive=False,
                    include_when_missing=False,
                )
            )
    return candidates


def unique_candidates(candidates: list[RootCandidate]) -> list[RootCandidate]:
    seen: set[tuple[str, Path, str]] = set()
    unique: list[RootCandidate] = []
    for candidate in candidates:
        key = (candidate.kind, safe_resolve(candidate.path), candidate.source_role)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def known_roots(
    codex_home: Path,
    claude_home: Path,
    *,
    cwd: Path | None = None,
    candidates: list[RootCandidate] | None = None,
    provider_roots: dict[str, Path] | None = None,
) -> dict[Path, RootCandidate]:
    roots = candidates or known_root_candidates(
        codex_home,
        claude_home,
        cwd=cwd or Path.cwd(),
        provider_roots=provider_roots,
    )
    return {safe_resolve(candidate.path): candidate for candidate in roots}


def known_kind_for_resolved_path(resolved: Path, known: dict[Path, RootCandidate]) -> str | None:
    candidate = known_candidate_for_resolved_path(resolved, known)
    return candidate.kind if candidate else None


def known_candidate_for_resolved_path(resolved: Path, known: dict[Path, RootCandidate]) -> RootCandidate | None:
    direct = known.get(resolved)
    if direct:
        return direct
    if resolved.suffix.lower() not in {".jsonl", ".json", ".md", ".db", ".sqlite", ".sqlite3", ".vscdb"}:
        return None
    for root, candidate in known.items():
        if candidate.source_role != "transcript_root":
            continue
        if candidate.kind in {"codex_history", "codex_transcription_history", "claude_desktop_json_sessions"}:
            continue
        if resolved_path_is_inside(resolved, root):
            kind = candidate.kind
            if kind in {"codex_sessions", "codex_archived_sessions", "windows_codex_sessions", "windows_codex_archived_sessions"}:
                kind = "codex_session_file"
            elif kind in {"claude_projects", "windows_claude_projects"}:
                kind = "claude_session_file"
            elif kind == "claude_desktop_json_directory":
                kind = "claude_desktop_json_sessions"
            elif kind == "gemini_sessions":
                kind = "gemini_session_file"
            elif kind == "grok_sessions":
                kind = "grok_session_file"
            elif kind == "aider_history":
                kind = "aider_history_file"
            elif kind == "cursor_storage":
                kind = "cursor_storage_file"
            elif kind == "opencode_storage":
                kind = "opencode_storage_file"
            return replace(
                candidate,
                kind=kind,
                path=resolved,
                recursive=False,
                root_format=(
                    "json" if resolved.suffix == ".json"
                    else "sqlite" if resolved.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".vscdb"}
                    else candidate.root_format
                ),
            )
    return None


def requested_root_uses_symlinked_provider_root(root: Path, codex_home: Path, claude_home: Path) -> bool:
    lexical_root = root.expanduser().absolute()
    directory_roots = (codex_home / "sessions", claude_home / "projects")
    for provider_root in directory_roots:
        lexical_provider_root = provider_root.expanduser().absolute()
        if provider_root.expanduser().is_symlink() and (
            lexical_root == lexical_provider_root or resolved_path_is_inside(lexical_root, lexical_provider_root)
        ):
            return True
    history = (codex_home / "history.jsonl").expanduser()
    if history.is_symlink() and lexical_root == history.absolute():
        return True
    return False


def resolved_path_is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def latest_known_session_file(
    codex_home: Path,
    claude_home: Path,
    *,
    root_candidates: list[RootCandidate] | None = None,
) -> tuple[Path | None, str | None]:
    session_candidates: list[tuple[int, float, float, str, Path]] = []
    warning: str | None = None
    started = time.monotonic()
    visited = 0
    max_candidates = 5000
    max_elapsed = 2.0
    root_candidates = root_candidates or known_root_candidates(codex_home, claude_home, cwd=Path.cwd())
    bases = [
        candidate.path
        for candidate in root_candidates
        if candidate.source_role == "transcript_root" and candidate.recursive and candidate.root_format == "jsonl"
    ]
    for base in bases:
        base = base.expanduser()
        if base.is_symlink():
            continue
        if not base.exists() or not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() - started > max_elapsed:
                warning = "Stopped current-session discovery at max elapsed cap; rerun with explicit --root if the latest session is missing."
                break
            dirnames[:] = [
                name
                for name in sorted(dirnames, reverse=True)
                if name not in {".git", ".cache", "node_modules", "__pycache__"}
            ]
            for filename in filenames:
                if not filename.endswith(".jsonl"):
                    continue
                path = Path(dirpath) / filename
                mtime = stat_mtime(path)
                latest_event = latest_session_event_timestamp(path)
                rank_timestamp = latest_event.timestamp() if latest_event is not None else mtime
                session_candidates.append(
                    (1 if latest_event is not None else 0, rank_timestamp, mtime, path.as_posix(), path)
                )
                visited += 1
                if visited >= max_candidates:
                    warning = "Stopped current-session discovery at metadata cap; rerun with explicit --root if the latest session is missing."
                    break
            if warning:
                break
        if warning:
            break
    if not session_candidates:
        return None, warning
    _has_event_timestamp, _rank_timestamp, _mtime, _label, path = max(
        session_candidates,
        key=lambda item: (item[0], item[1], item[2], item[3]),
    )
    return path, warning


def latest_session_event_timestamp(path: Path) -> datetime | None:
    tail_lines = _tail_jsonl_lines(
        path,
        max_bytes=LATEST_SESSION_TIMESTAMP_TAIL_BYTES,
        max_lines=LATEST_SESSION_TIMESTAMP_MAX_LINES,
    )
    for line in reversed(tail_lines):
        if not line.strip():
            continue
        record = parse_record(line, path=path, line=0, default_tz=timezone.utc)
        if record.timestamp is not None:
            return record.timestamp.astimezone(timezone.utc)
    return None


def _tail_jsonl_lines(path: Path, *, max_bytes: int, max_lines: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            offset = max(size - max_bytes, 0)
            handle.seek(offset)
            chunk = handle.read(max_bytes)
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        lines = lines[1:]
    return lines[-max_lines:]


def normalize_requested_root(
    root: Path,
    codex_home: Path,
    claude_home: Path,
    *,
    provider_roots: dict[str, Path] | None = None,
) -> list[Path]:
    root = normalize_local_path(root)
    resolved = safe_resolve(root)
    if resolved == safe_resolve(codex_home):
        return [
            candidate.path
            for candidate in codex_root_candidates(codex_home, platform=platform_for_path(codex_home))
            if candidate.source_role == "transcript_root"
        ]
    if resolved == safe_resolve(claude_home):
        return [claude_home / "projects"]
    for provider, provider_root in (provider_roots or {}).items():
        if resolved != safe_resolve(provider_root):
            continue
        if provider == "grok" and provider_root.name != "sessions":
            return [provider_root / "sessions"]
        return [provider_root]
    return [root]


def arbitrary_root_kind(root: Path) -> str:
    if root.suffix == ".json":
        return "arbitrary_json_file"
    return "arbitrary_jsonl_root"


def normalize_local_path(path: Path) -> Path:
    text = os.path.expanduser(str(path))
    text = expand_windows_environment_markers(text)
    windows_path = windows_path_to_wsl_path(text)
    return windows_path if windows_path is not None else Path(text)


def expand_windows_environment_markers(value: str) -> str:
    def replace_var(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(0))

    return WINDOWS_ENV_VAR_RE.sub(replace_var, value)


def windows_path_to_wsl_path(value: str, *, mount_root: Path = Path("/mnt")) -> Path | None:
    match = WINDOWS_DRIVE_PATH_RE.match(value.strip())
    if not match:
        return None
    drive = match.group(1).lower()
    remainder = match.group(2)
    parts = [part for part in PureWindowsPath(remainder).parts if part not in {"\\", "/"}]
    return mount_root / drive / Path(*parts)


def windows_user_profile_candidates(*, cwd: Path, include_auto: bool) -> list[Path]:
    profiles: list[Path] = []
    explicit = os.environ.get("JSONL_HISTORY_WINDOWS_USERPROFILE")
    if explicit:
        profiles.append(normalize_local_path(Path(explicit)))
    if not include_auto:
        return unique_paths(profiles)
    raw_userprofile = os.environ.get("USERPROFILE")
    if raw_userprofile and looks_like_windows_profile(raw_userprofile):
        profiles.append(normalize_local_path(Path(raw_userprofile)))
    home_drive = os.environ.get("HOMEDRIVE")
    home_path = os.environ.get("HOMEPATH")
    if home_drive and home_path:
        normalized = windows_path_to_wsl_path(f"{home_drive}{home_path}")
        if normalized is not None:
            profiles.append(normalized)
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if user and (Path("/mnt/c/Users") / user).exists():
        profiles.append(Path("/mnt/c/Users") / user)
    mounted_from_cwd = windows_user_profile_from_mounted_path(cwd)
    if mounted_from_cwd is not None:
        profiles.append(mounted_from_cwd)
    return unique_paths(profiles)


def looks_like_windows_profile(value: str) -> bool:
    if WINDOWS_DRIVE_PATH_RE.match(value.strip()):
        return True
    parts = Path(value).parts
    return len(parts) >= 5 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[3].lower() == "users"


def windows_user_profile_from_mounted_path(path: Path) -> Path | None:
    parts = safe_resolve(path).parts
    lowered = [part.lower() for part in parts]
    if len(parts) >= 5 and parts[1] == "mnt" and len(parts[2]) == 1 and lowered[3] == "users":
        return Path(*parts[:5])
    return None


def unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = safe_resolve(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def is_default_provider_home(path: Path, provider: str) -> bool:
    return safe_resolve(path) == safe_resolve(Path.home() / f".{provider}")


def platform_for_path(path: Path) -> str:
    text = str(path)
    if WINDOWS_DRIVE_PATH_RE.match(text):
        return "windows_path"
    parts = safe_resolve(path).parts
    if len(parts) >= 4 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[3].lower() == "users":
        return "windows_wsl_mount"
    return "posix_wsl"


def safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
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


def enumerate_jsonl_files(plan: SourcePlan, *, preferred_terms: list[str] | None = None, budget=None,
                          measured_byte_admission: bool = False):
    started = time.monotonic()
    files_seen = 0
    bytes_seen = 0
    warnings = plan.warnings
    for root in plan.roots:
        base = root.path.expanduser()
        if base.is_file():
            candidates = [base] if base.suffix.lower() in {".jsonl", ".json", ".md", ".db", ".sqlite", ".sqlite3", ".vscdb"} else []
        elif base.is_dir():
            candidates = iter_directory_jsonl_files(
                base,
                plan,
                started,
                warnings,
                source_kind=root.kind,
                preferred_terms=preferred_terms,
                budget=budget,
            )
        else:
            continue
        for path in candidates:
            if budget is not None and not budget.available("enumeration"):
                warnings.append("Stopped enumeration at operation max elapsed cap.")
                return warnings
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
            if not measured_byte_admission and bytes_seen + size > plan.limits.effective_bytes():
                warnings.append("Stopped enumeration at max byte cap.")
                return warnings
            files_seen += 1
            bytes_seen += size
            yield path, size, warnings


def materialize_planned_jsonl_files(
    plan: SourcePlan,
    *,
    preferred_terms: list[str] | None = None,
    source: str = "shared_per_command_source_enumeration",
    budget=None,
) -> PlannedJsonlFiles:
    started = time.perf_counter()
    files: list[tuple[Path, int]] = []
    observed_warnings = list(plan.warnings)
    for path, size, enum_warnings in enumerate_jsonl_files(plan, preferred_terms=preferred_terms, budget=budget):
        observed_warnings = list(enum_warnings)
        files.append((path, size))
    observed_warnings.extend(plan.warnings)
    return PlannedJsonlFiles(
        files=tuple(files),
        warnings=tuple(sorted(set(observed_warnings))),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
        source=source,
    )


def cap_labels_from_warnings(warnings: list[str]) -> list[str]:
    labels: set[str] = set()
    hits = cap_hits_from_warnings(warnings)
    if hits.get("elapsed"):
        labels.add("max_elapsed")
    if hits.get("file"):
        labels.add("max_files")
    if hits.get("byte"):
        labels.add("max_bytes")
    lowered = "\n".join(str(warning).lower() for warning in warnings)
    if "max match cap" in lowered or "retention-capped" in lowered:
        labels.add("max_match")
    if "reservoir-capped" in lowered:
        labels.add("reservoir")
    if "bm25 candidate row" in lowered:
        labels.add("bm25_candidate_rows")
    if "bm25 query/materialization timeout" in lowered:
        labels.add("bm25_timeout")
    return sorted(labels)


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


def iter_directory_jsonl_files(
    base: Path,
    plan: SourcePlan,
    started: float,
    warnings: list[str],
    *,
    source_kind: str = "",
    preferred_terms: list[str] | None = None,
    budget=None,
):
    pending = [base]
    while pending:
        if budget is not None and not budget.available("enumeration"):
            warnings.append("Stopped enumeration at operation max elapsed cap.")
            return
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
            if budget is not None and not budget.available("enumeration"):
                warnings.append("Stopped enumeration at operation max elapsed cap.")
                return
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(Path(entry.path))
                elif (
                    entry.is_file(follow_symlinks=False)
                    and entry.name.endswith(directory_file_suffixes(source_kind))
                    and directory_file_allowed(Path(entry.path), source_kind)
                ):
                    files.append(Path(entry.path))
            except OSError:
                continue
        # Default transcript roots preserve predictable newest-ish traversal for
        # date-shaped trees. Explicit arbitrary roots are usually curated fixture
        # sets, so prefer filenames matching query terms before applying caps.
        if source_kind == "arbitrary_jsonl_root":
            files.sort(key=lambda item: (-filename_preference_score(item, preferred_terms or []), item.name))
        else:
            files.sort(key=lambda item: item.name, reverse=True)
        dirs.sort(key=lambda item: item.name)
        pending.extend(dirs)
        for path in files:
            if time.monotonic() - started > plan.limits.max_elapsed:
                warnings.append("Stopped enumeration at max elapsed cap.")
                return
            yield path


def filename_preference_score(path: Path, preferred_terms: list[str]) -> int:
    if not preferred_terms:
        return 0
    name = path.stem.lower()
    name_tokens = re.findall(r"[a-z0-9]+", name)
    score = 0
    for term in preferred_terms:
        normalized = term.lower()
        if len(normalized) <= 2:
            continue
        if normalized in name:
            score += 1
            continue
        term_stems = filename_term_stems(normalized)
        if any(stem and stem in token for stem in term_stems for token in name_tokens):
            score += 1
    return score


def directory_file_suffixes(source_kind: str) -> tuple[str, ...]:
    if source_kind == "claude_desktop_json_directory":
        return (".json",)
    if source_kind in {"gemini_sessions", "gemini_session_file"}:
        return (".jsonl", ".json")
    if source_kind in {"aider_history", "aider_history_file"}:
        return (".md",)
    if source_kind in {"cursor_storage", "cursor_storage_file"}:
        return (".jsonl", ".vscdb", ".db")
    if source_kind in {"opencode_storage", "opencode_storage_file"}:
        return (".db", ".sqlite", ".sqlite3", ".json")
    return (".jsonl",)


def directory_file_allowed(path: Path, source_kind: str) -> bool:
    if source_kind == "claude_desktop_json_directory":
        return path.name.startswith("local_") and path.suffix == ".json"
    if source_kind in {"gemini_sessions", "gemini_session_file"}:
        return path.name.startswith("session-") and path.parent.name == "chats"
    if source_kind in {"grok_sessions", "grok_session_file"}:
        if path.name == "updates.jsonl":
            return True
        return path.name == "chat_history.jsonl" and not (path.parent / "updates.jsonl").is_file()
    if source_kind in {"aider_history", "aider_history_file"}:
        return path.name == ".aider.chat.history.md"
    if source_kind in {"cursor_storage", "cursor_storage_file"}:
        if path.suffix.lower() == ".jsonl":
            return "agent-transcripts" in path.parts
        return path.name == "state.vscdb"
    if source_kind in {"opencode_storage", "opencode_storage_file"}:
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            return path.name == "opencode.db" or source_kind == "opencode_storage_file"
        return any(part in {"session", "message", "part"} for part in path.parts)
    return True


def filename_term_stems(term: str) -> list[str]:
    stems = {term}
    for suffix in ("ing", "ed", "es", "s"):
        if len(term) > len(suffix) + 3 and term.endswith(suffix):
            stems.add(term[: -len(suffix)])
    if len(term) > 5:
        stems.add(term[:5])
    return sorted(stems, key=len, reverse=True)


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
