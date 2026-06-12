from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .manifest import ManifestEntry, build_manifest
from .sources import SourcePlan


TASK_SLUG_RE = re.compile(r"\b([a-z]{2,12})-\d+[a-z]?(?:-[a-z0-9]+){0,12}\b")
SAFE_ARTIFACT_ROOTS = {"benchmarks", "codex", "docs", "reports", "reviews", "scripts", "src", "tasks", "tests"}
RECURRING_LABELS = {
    "artifact",
    "benchmark",
    "handoff",
    "manifest",
    "packet",
    "report",
    "review",
    "scratch",
    "task",
}


@dataclass
class CandidateAccumulator:
    kind: str
    label: str
    raw_values: set[str] = field(default_factory=set)
    source_kinds: set[str] = field(default_factory=set)
    entry_paths: set[str] = field(default_factory=set)
    record_count: int = 0
    example_shapes: set[str] = field(default_factory=set)

    def add(self, *, raw_value: str, entry: ManifestEntry, example_shape: str) -> None:
        self.raw_values.add(raw_value)
        self.source_kinds.add(entry.source_kind)
        self.entry_paths.add(entry.path)
        self.record_count += max(0, entry.record_count)
        self.example_shapes.add(example_shape)

    def to_dict(self) -> dict:
        evidence_count = len(self.entry_paths)
        return {
            "candidate_id": f"sha256:{hash_text(f'{self.kind}:{self.label}')[:16]}",
            "kind": self.kind,
            "label": self.label,
            "confidence": confidence_for(evidence_count, len(self.raw_values)),
            "evidence": {
                "entry_count": evidence_count,
                "raw_value_count": len(self.raw_values),
                "record_count": self.record_count,
                "source_kinds": sorted(self.source_kinds),
                "example_shapes": sorted(self.example_shapes)[:5],
                "raw_value_hashes": [f"sha256:{hash_text(value)[:16]}" for value in sorted(self.raw_values)[:5]],
            },
            "privacy": {
                "raw_values_committed": False,
                "source_paths_committed": False,
                "message_text_committed": False,
            },
        }


def build_generated_metadata_discovery_report(
    plan: SourcePlan,
    *,
    default_tz,
    min_count: int = 1,
    max_candidates: int = 40,
) -> dict:
    manifest = build_manifest(plan, default_tz=default_tz)
    candidates: dict[tuple[str, str], CandidateAccumulator] = {}
    for entry in manifest.entries:
        for hint in entry.task_hints:
            collect_task_hint(candidates, hint, entry)
        for hint in entry.artifact_hints:
            collect_artifact_hint(candidates, hint, entry)

    rows = [
        candidate.to_dict()
        for candidate in candidates.values()
        if len(candidate.entry_paths) >= min_count
    ]
    rows.sort(
        key=lambda item: (
            item["evidence"]["entry_count"],
            item["evidence"]["raw_value_count"],
            item["kind"],
            item["label"],
        ),
        reverse=True,
    )
    rows = rows[:max_candidates]
    status = "candidates_found" if rows else "insufficient_signal"
    return {
        "generated_metadata_discovery": {
            "schema_version": 1,
            "status": status,
            "mode": "report_only_prototype",
            "source_summary": {
                "entry_count": len(manifest.entries),
                "warning_count": len(manifest.warnings),
                "source_kinds": sorted({entry.source_kind for entry in manifest.entries}),
                "manifest_metrics": manifest.metrics,
            },
            "summary": {
                "candidate_count": len(rows),
                "candidate_kinds": sorted({row["kind"] for row in rows}),
                "min_count": min_count,
                "max_candidates": max_candidates,
            },
            "candidates": rows,
            "privacy_contract": {
                "raw_private_text_committed": False,
                "raw_source_paths_committed": False,
                "raw_session_ids_committed": False,
                "local_usernames_committed": False,
                "raw_values_exposed": False,
            },
            "mechanism_assessment": mechanism_assessment(rows),
            "promotion_boundary": {
                "default_search_uses_generated_metadata": False,
                "sidecar_or_index_created": False,
                "background_watcher_created": False,
                "runtime_integration_claimed": False,
                "token_efficiency_claimed": False,
                "ranking_or_product_value_claimed": False,
            },
        }
    }


def collect_task_hint(candidates: dict[tuple[str, str], CandidateAccumulator], hint: str, entry: ManifestEntry) -> None:
    normalized = normalize_hint(hint)
    for match in TASK_SLUG_RE.finditer(normalized):
        prefix = match.group(1)
        slug = match.group(0)
        add_candidate(candidates, "task_prefix", prefix, raw_value=slug, entry=entry, example_shape=f"{prefix}-###-...")
        add_candidate(
            candidates,
            "task_slug_shape",
            f"{prefix}-numbered-slug",
            raw_value=slug,
            entry=entry,
            example_shape=f"{prefix}-###-<tokens:{max(0, slug.count('-') - 1)}>",
        )


def collect_artifact_hint(candidates: dict[tuple[str, str], CandidateAccumulator], hint: str, entry: ManifestEntry) -> None:
    normalized = normalize_hint(hint)
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
    if parts:
        root = parts[0]
        if root in SAFE_ARTIFACT_ROOTS:
            add_candidate(candidates, "artifact_root", root, raw_value=normalized, entry=entry, example_shape=f"{root}/<redacted>")
    suffix = PurePosixPath(normalized).suffix.lower()
    if suffix and re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        add_candidate(candidates, "artifact_extension", suffix, raw_value=normalized, entry=entry, example_shape=f"<redacted>{suffix}")
    for label in RECURRING_LABELS:
        if re.search(rf"\b{re.escape(label)}s?\b", normalized):
            add_candidate(candidates, "recurring_label", label, raw_value=normalized, entry=entry, example_shape=label)
    for match in TASK_SLUG_RE.finditer(normalized):
        prefix = match.group(1)
        add_candidate(candidates, "task_prefix", prefix, raw_value=match.group(0), entry=entry, example_shape=f"{prefix}-###-...")


def add_candidate(
    candidates: dict[tuple[str, str], CandidateAccumulator],
    kind: str,
    label: str,
    *,
    raw_value: str,
    entry: ManifestEntry,
    example_shape: str,
) -> None:
    key = (kind, label)
    candidate = candidates.get(key)
    if candidate is None:
        candidate = CandidateAccumulator(kind=kind, label=label)
        candidates[key] = candidate
    candidate.add(raw_value=raw_value, entry=entry, example_shape=example_shape)


def normalize_hint(value: str) -> str:
    return " ".join(value.lower().strip().split()).replace("\\", "/")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def confidence_for(entry_count: int, raw_value_count: int) -> str:
    if entry_count >= 3 and raw_value_count >= 2:
        return "high"
    if entry_count >= 2 or raw_value_count >= 2:
        return "medium"
    return "low"


def mechanism_assessment(rows: list[dict]) -> dict:
    if not rows:
        return {
            "status": "inconclusive",
            "what_worked": [],
            "what_failed_or_is_unknown": [
                "No repeated sanitized metadata candidates met the requested threshold.",
                "This does not rule out generated metadata; retry with a broader or better-labeled source plan.",
            ],
            "next_evidence_needed": [
                "Run on a reviewed sanitized fixture or capped private aggregate.",
                "Compare candidate metadata against actual retrieval misses before runtime integration.",
            ],
        }
    return {
        "status": "report_only_learning",
        "what_worked": [
            "Existing manifest hints can produce sanitized candidate metadata without raw transcript text.",
            "Candidate evidence can be counted and hashed without committing raw source paths or raw values.",
        ],
        "what_failed_or_is_unknown": [
            "This report does not prove retrieval quality, token efficiency, or ranking impact.",
            "Candidate freshness and usefulness still need measurement against retrieval misses.",
        ],
        "next_evidence_needed": [
            "Use a tiny eval slice to test whether these candidates recover source refs or reduce search cost.",
            "Only consider runtime integration after reviewed quality and privacy evidence.",
        ],
    }
