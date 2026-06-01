from __future__ import annotations

import json
import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .adapters import AdapterReadError, adapter_for_file, iter_records_for_file
from .clustering import facets_for_record, session_key
from .query import QueryIntent, STOPWORDS
from .ranking import active_match_terms, artifact_candidates, role_matches
from .records import Record, parse_record
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path, stat_mtime, stat_size


MAX_FINGERPRINT_TERMS = 80


@dataclass
class ManifestEntry:
    path: str
    source_kind: str
    size: int
    mtime: float
    framework: str
    session: str | None = None
    date_start: str | None = None
    date_end: str | None = None
    roles: list[str] = field(default_factory=list)
    cwd_hints: list[str] = field(default_factory=list)
    task_hints: list[str] = field(default_factory=list)
    artifact_hints: list[str] = field(default_factory=list)
    term_fingerprints: list[str] = field(default_factory=list)
    content_hash: str | None = None
    record_hash: str | None = None
    line_count: int = 0
    record_count: int = 0
    refreshed_from: str = "full_scan"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Manifest:
    schema_version: int = 1
    entries: list[ManifestEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "entries": [entry.to_dict() for entry in self.entries],
            "warnings": self.warnings,
            "metrics": self.metrics,
        }


def build_manifest(plan: SourcePlan, *, default_tz, previous: Manifest | None = None) -> Manifest:
    started = time.monotonic()
    previous_by_path = {entry.path: entry for entry in previous.entries} if previous else {}
    entries: list[ManifestEntry] = []
    warnings = list(plan.warnings)
    files_reused = 0
    files_refreshed = 0
    files_rebuilt = 0
    bytes_parsed = 0

    for path, size, enum_warnings in enumerate_jsonl_files(plan):
        warnings = enum_warnings
        path_text = str(path)
        mtime = stat_mtime(path)
        old = previous_by_path.get(path_text)
        if old and old.size == size and old.mtime == mtime:
            reused = ManifestEntry(**old.to_dict())
            reused.refreshed_from = "unchanged_cache"
            entries.append(reused)
            files_reused += 1
            continue
        source_kind = source_kind_for_path(path, plan)
        adapter = adapter_for_file(path, source_kind)
        if old and adapter.supports_append_only and size >= old.size and old.line_count > 0:
            entry, parsed = refresh_append_only_entry(path, old, size=size, mtime=mtime, default_tz=default_tz)
            entries.append(entry)
            files_refreshed += 1
            bytes_parsed += parsed
        else:
            entry, parsed = scan_manifest_entry(path, source_kind=source_kind, size=size, mtime=mtime, default_tz=default_tz)
            entries.append(entry)
            files_rebuilt += 1
            bytes_parsed += parsed

    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    manifest = Manifest(entries=entries, warnings=sorted(set(warnings)))
    manifest.metrics = {
        "entry_count": len(entries),
        "files_reused": files_reused,
        "files_refreshed_append_only": files_refreshed,
        "files_rebuilt": files_rebuilt,
        "bytes_parsed": bytes_parsed,
        "elapsed_ms": elapsed_ms,
        "estimated_manifest_bytes": len(json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")),
    }
    return manifest


def scan_manifest_entry(path: Path, *, source_kind: str, size: int, mtime: float, default_tz) -> tuple[ManifestEntry, int]:
    accumulator = EntryAccumulator(path=path, source_kind=source_kind, size=size, mtime=mtime)
    adapter = adapter_for_file(path, source_kind)
    try:
        for record in iter_records_for_file(path, source_kind, default_tz):
            accumulator.line_count = max(accumulator.line_count, record.line)
            accumulator.add_record(record)
    except AdapterReadError:
        pass
    refreshed_from = "full_scan" if adapter.line_oriented else "full_scan_whole_file"
    entry = accumulator.to_entry(refreshed_from=refreshed_from)
    entry.content_hash = content_hash_for_path(path)
    return entry, size


def refresh_append_only_entry(path: Path, old: ManifestEntry, *, size: int, mtime: float, default_tz) -> tuple[ManifestEntry, int]:
    accumulator = EntryAccumulator.from_entry(old, size=size, mtime=mtime)
    for line_number, line in iter_appended_jsonl_lines(path, start_byte=old.size, start_line=old.line_count):
        accumulator.line_count = line_number
        record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
        accumulator.add_record(record)
    entry = accumulator.to_entry(refreshed_from="append_only_refresh")
    entry.content_hash = content_hash_for_path(path)
    entry.record_hash = record_hash_for_path(path, source_kind=entry.source_kind, default_tz=default_tz)
    return entry, max(0, size - old.size)


class EntryAccumulator:
    def __init__(self, *, path: Path, source_kind: str, size: int, mtime: float) -> None:
        self.path = str(path)
        self.source_kind = source_kind
        self.size = size
        self.mtime = mtime
        self.framework = infer_framework(source_kind, path)
        self.session = session_key(str(path))
        self.date_start: datetime | None = None
        self.date_end: datetime | None = None
        self.roles: set[str] = set()
        self.cwd_hints: set[str] = set()
        self.task_hints: set[str] = set()
        self.artifact_hints: set[str] = set()
        self.term_fingerprints: set[str] = set()
        self.line_count = 0
        self.record_count = 0
        self._record_digest = hashlib.sha256()

    @classmethod
    def from_entry(cls, entry: ManifestEntry, *, size: int, mtime: float) -> "EntryAccumulator":
        accumulator = cls(path=Path(entry.path), source_kind=entry.source_kind, size=size, mtime=mtime)
        accumulator.framework = entry.framework
        accumulator.session = entry.session
        accumulator.date_start = parse_iso(entry.date_start)
        accumulator.date_end = parse_iso(entry.date_end)
        accumulator.roles = set(entry.roles)
        accumulator.cwd_hints = set(entry.cwd_hints)
        accumulator.task_hints = set(entry.task_hints)
        accumulator.artifact_hints = set(entry.artifact_hints)
        accumulator.term_fingerprints = set(entry.term_fingerprints)
        accumulator.line_count = entry.line_count
        accumulator.record_count = entry.record_count
        return accumulator

    def add_record(self, record: Record) -> None:
        if not record.text and not record.role and not record.timestamp:
            return
        self.record_count += 1
        self._record_digest.update(record_hash_payload(record))
        if record.timestamp is not None:
            self.date_start = min_datetime(self.date_start, record.timestamp)
            self.date_end = max_datetime(self.date_end, record.timestamp)
        if record.role:
            self.roles.add(record.role)
        if record.cwd:
            self.cwd_hints.add(record.cwd)
        artifacts = artifact_candidates(record.text)
        facets = facets_for_record(path=record.path, text=record.text, artifacts=artifacts)
        self.task_hints.update(facets.tasks)
        self.artifact_hints.update(facets.artifacts)
        self.term_fingerprints.update(fingerprint_terms(record.text))

    def to_entry(self, *, refreshed_from: str) -> ManifestEntry:
        return ManifestEntry(
            path=self.path,
            source_kind=self.source_kind,
            size=self.size,
            mtime=self.mtime,
            framework=self.framework,
            session=self.session,
            date_start=self.date_start.isoformat() if self.date_start else None,
            date_end=self.date_end.isoformat() if self.date_end else None,
            roles=sorted(self.roles),
            cwd_hints=sorted(self.cwd_hints)[:20],
            task_hints=sorted(self.task_hints),
            artifact_hints=sorted(self.artifact_hints)[:40],
            term_fingerprints=sorted(self.term_fingerprints)[:MAX_FINGERPRINT_TERMS],
            record_hash=self.record_hash(),
            line_count=self.line_count,
            record_count=self.record_count,
            refreshed_from=refreshed_from,
        )

    def record_hash(self) -> str | None:
        if self.record_count <= 0:
            return None
        return f"sha256:{self._record_digest.hexdigest()}"


def iter_jsonl_lines(path: Path):
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line_number, line in enumerate(handle, 1):
            yield line_number, line


def iter_appended_jsonl_lines(path: Path, *, start_byte: int, start_line: int):
    try:
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        handle.seek(start_byte)
        for offset, raw in enumerate(handle, 1):
            yield start_line + offset, raw.decode("utf-8", errors="replace")


def infer_framework(source_kind: str, path: Path) -> str:
    if source_kind.startswith("codex"):
        return "codex"
    if source_kind.startswith("claude"):
        return "claude"
    if source_kind == "arbitrary_json_file" or path.suffix == ".json":
        return "generic_message_array_json"
    if "claude" in str(path):
        return "claude"
    if "codex" in str(path):
        return "codex"
    return "generic_jsonl"


def fingerprint_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for word in re.findall(r"[a-z0-9_.:/-]+", text.lower()):
        if len(word) <= 2 or word in STOPWORDS:
            continue
        terms.add(fingerprint_term(word))
    return terms


def fingerprint_term(term: str) -> str:
    digest = hashlib.sha256(term.lower().encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def content_hash_for_path(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return f"sha256:{digest.hexdigest()}"


def record_hash_payload(record: Record) -> bytes:
    payload = {
        "role": record.role,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "text": record.text,
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def record_hash_for_path(path: Path, *, source_kind: str, default_tz) -> str | None:
    digest = hashlib.sha256()
    count = 0
    try:
        for record in iter_records_for_file(path, source_kind, default_tz):
            if not record.text and not record.role and not record.timestamp:
                continue
            count += 1
            digest.update(record_hash_payload(record))
    except AdapterReadError:
        return None
    if count <= 0:
        return None
    return f"sha256:{digest.hexdigest()}"


def shortlist_manifest(manifest: Manifest, intent: QueryIntent, *, cwd: str | None = None, limit: int | None = None) -> dict:
    scored = [score_manifest_entry(entry, intent, cwd=cwd) for entry in manifest.entries]
    scored.sort(key=lambda item: (item["score"], item["entry"]["date_end"] or "", item["entry"]["path"]), reverse=True)
    positive = [item for item in scored if item["score"] > 0]
    strong = [item for item in positive if is_strong_shortlist_candidate(item)]
    selected = strong or positive or scored
    if limit is not None:
        selected = selected[:limit]

    total_files = len(scored)
    selected_files = len(selected)
    total_bytes = sum(item["entry"]["size"] for item in scored)
    selected_bytes = sum(item["entry"]["size"] for item in selected)
    return {
        "query": intent.to_dict(),
        "selected_entries": selected,
        "excluded_entries": [item for item in scored if item not in selected],
        "metrics": {
            "total_files": total_files,
            "selected_files": selected_files,
            "excluded_files": max(0, total_files - selected_files),
            "total_bytes": total_bytes,
            "selected_bytes": selected_bytes,
            "excluded_bytes": max(0, total_bytes - selected_bytes),
            "file_reduction_ratio": reduction_ratio(selected_files, total_files),
            "byte_reduction_ratio": reduction_ratio(selected_bytes, total_bytes),
            "weak_candidates_retained": len([item for item in selected if item["score"] <= 0]),
        },
}


def is_strong_shortlist_candidate(item: dict) -> bool:
    reasons = item.get("reasons") or []
    return any(not str(reason).startswith("role:") for reason in reasons)


def score_manifest_entry(entry: ManifestEntry, intent: QueryIntent, *, cwd: str | None = None) -> dict:
    score = 0.0
    reasons: list[str] = []
    terms = set(term.lower() for term in active_match_terms(intent))
    hashed_terms = {fingerprint_term(term) for term in terms}
    entry_terms = set(entry.term_fingerprints) | set(entry.artifact_hints) | set(entry.task_hints)
    term_hits = sorted(terms & entry_terms)
    hashed_term_hits = sorted(hashed_terms & set(entry.term_fingerprints))
    if term_hits or hashed_term_hits:
        score += 8 * (len(term_hits) + len(hashed_term_hits))
        reasons.extend(f"term:{term}" for term in term_hits[:8])
        reasons.extend(f"term_fingerprint:{term}" for term in hashed_term_hits[:8])
    if intent.role and any(role_matches(role, intent.role) for role in entry.roles):
        score += 6
        reasons.append(f"role:{intent.role}")
    if intent.date_window_start and intent.date_window_end and entry_overlaps_date_window(entry, intent):
        score += 7
        reasons.append("date-window")
    if cwd and any(cwd in hint for hint in entry.cwd_hints):
        score += 5
        reasons.append("cwd")
    artifact_hits = sorted(set(intent.artifact_hints) & set(entry.artifact_hints))
    if artifact_hits:
        score += 5
        reasons.extend(f"artifact:{artifact}" for artifact in artifact_hits[:5])
    return {"entry": entry.to_dict(), "score": round(score, 3), "reasons": reasons}


def entry_overlaps_date_window(entry: ManifestEntry, intent: QueryIntent) -> bool:
    if not intent.date_window_start or not intent.date_window_end:
        return False
    start = parse_iso(entry.date_start)
    end = parse_iso(entry.date_end)
    if start is None and end is None:
        return False
    start = start or end
    end = end or start
    if start is None or end is None:
        return False
    local_start = start.astimezone(intent.date_window_start.tzinfo)
    local_end = end.astimezone(intent.date_window_start.tzinfo)
    return local_start < intent.date_window_end and local_end >= intent.date_window_start


def build_manifest_report(plan: SourcePlan, intent: QueryIntent, *, default_tz, cwd: str | None = None, shortlist_limit: int | None = None) -> dict:
    initial = build_manifest(plan, default_tz=default_tz)
    refreshed = build_manifest(plan, default_tz=default_tz, previous=initial)
    shortlist = shortlist_manifest(initial, intent, cwd=cwd, limit=shortlist_limit)
    duplicate_risk = build_duplicate_risk_report(initial, plan)
    return {
        "manifest_report": {
            "schema_version": 1,
            "status": "ok",
            "cache_policy": {
                "local_only": True,
                "optional": True,
                "default_search_uses_cache": False,
                "invalidation": "reuse entries only when path, size, and mtime match; append-only growth refreshes new lines",
                "privacy": "stores metadata, fingerprints, cwd hints, task/artifact hints, roles, and timestamps; does not store raw message text",
            },
            "manifest": {
                "entry_count": len(initial.entries),
                "entries": [entry.to_dict() for entry in initial.entries],
                "build_metrics": initial.metrics,
                "no_change_refresh_metrics": refreshed.metrics,
                "warnings": initial.warnings,
            },
            "shortlist": shortlist,
            "duplicate_risk": duplicate_risk,
            "cost_model": cost_model(initial, refreshed, shortlist),
            "guidance": guidance(shortlist),
        }
    }


def build_duplicate_risk_report(manifest: Manifest, plan: SourcePlan) -> dict:
    exact_groups = exact_duplicate_groups(manifest.entries, plan)
    likely_groups = likely_mirror_groups(manifest.entries, plan)
    groups = exact_groups + likely_groups
    return {
        "schema_version": 1,
        "status": "groups_found" if groups else "no_duplicate_risk_groups",
        "summary": {
            "entry_count": len(manifest.entries),
            "group_count": len(groups),
            "exact_duplicate_groups": len(exact_groups),
            "likely_mirror_groups": len(likely_groups),
        },
        "groups": groups,
        "limitations": [
            "Duplicate-risk reporting only covers entries in the selected manifest source plan.",
            "Exact duplicates require matching content hashes; likely mirrors require at least two matching parsed records and timestamp spans.",
            "Likely mirror groups are probabilistic evidence of copied or mirrored sessions, not proof of all-history completeness.",
        ],
    }


def exact_duplicate_groups(entries: list[ManifestEntry], plan: SourcePlan) -> list[dict]:
    buckets: dict[tuple[str, int], list[ManifestEntry]] = {}
    for entry in entries:
        if not entry.content_hash or entry.record_count <= 0:
            continue
        buckets.setdefault((entry.content_hash, entry.size), []).append(entry)
    groups = []
    for content_key, grouped in sorted(buckets.items(), key=lambda item: item[0]):
        if len(grouped) < 2:
            continue
        groups.append(
            duplicate_group_summary(
                risk_type="exact_duplicate",
                confidence="high",
                reason="same content hash and file size across selected manifest entries",
                group_key=f"exact:{hash_text(':'.join([content_key[0], str(content_key[1])]))}",
                entries=grouped,
                plan=plan,
            )
        )
    return groups


def likely_mirror_groups(entries: list[ManifestEntry], plan: SourcePlan) -> list[dict]:
    buckets: dict[tuple[str, int, str | None, str | None], list[ManifestEntry]] = {}
    for entry in entries:
        if not entry.record_hash or entry.record_count < 2:
            continue
        key = (entry.record_hash, entry.record_count, entry.date_start, entry.date_end)
        buckets.setdefault(key, []).append(entry)
    groups = []
    for record_key, grouped in sorted(buckets.items(), key=lambda item: tuple(str(part) for part in item[0])):
        content_hashes = {entry.content_hash for entry in grouped if entry.content_hash}
        if len(grouped) < 2 or len(content_hashes) <= 1:
            continue
        groups.append(
            duplicate_group_summary(
                risk_type="likely_mirror",
                confidence="medium",
                reason="same parsed record hash, record count, and timestamp span with different file content hashes",
                group_key=f"mirror:{hash_text(':'.join(str(part) for part in record_key))}",
                entries=grouped,
                plan=plan,
            )
        )
    return groups


def duplicate_group_summary(*, risk_type: str, confidence: str, reason: str, group_key: str, entries: list[ManifestEntry], plan: SourcePlan) -> dict:
    members = [duplicate_member_summary(entry, plan) for entry in sorted(entries, key=lambda item: safe_path_hash(item.path))]
    timestamp_starts = [entry.date_start for entry in entries if entry.date_start]
    timestamp_ends = [entry.date_end for entry in entries if entry.date_end]
    mtimes = [entry.mtime for entry in entries]
    line_counts = [entry.line_count for entry in entries]
    record_counts = [entry.record_count for entry in entries]
    return {
        "risk_type": risk_type,
        "confidence": confidence,
        "reason": reason,
        "group_key": group_key,
        "member_count": len(members),
        "source_labels": sorted({member["source_label"] for member in members}),
        "root_labels": sorted({member["root_label"] for member in members}),
        "path_labels": [member["path_label"] for member in members],
        "content_hashes": sorted({member["content_hash"] for member in members if member.get("content_hash")}),
        "record_hashes": sorted({member["record_hash"] for member in members if member.get("record_hash")}),
        "hashed_session_keys": sorted({member["hashed_session_key"] for member in members if member.get("hashed_session_key")}),
        "file_size_bytes": sorted({member["file_size_bytes"] for member in members}),
        "mtime_span": {"start": min(mtimes), "end": max(mtimes)} if mtimes else {"start": None, "end": None},
        "timestamp_span": {
            "start": min(timestamp_starts) if timestamp_starts else None,
            "end": max(timestamp_ends) if timestamp_ends else None,
        },
        "line_count_range": {"min": min(line_counts), "max": max(line_counts)} if line_counts else {"min": 0, "max": 0},
        "record_count_range": {"min": min(record_counts), "max": max(record_counts)} if record_counts else {"min": 0, "max": 0},
        "members": members,
    }


def duplicate_member_summary(entry: ManifestEntry, plan: SourcePlan) -> dict:
    path_hash = safe_path_hash(entry.path)
    return {
        "source_label": entry.source_kind,
        "root_label": root_label_for_entry(entry, plan),
        "path_label": f"path:{path_hash[:16]}",
        "path_hash": f"sha256:{path_hash}",
        "file_size_bytes": entry.size,
        "mtime": entry.mtime,
        "content_hash": entry.content_hash,
        "record_hash": entry.record_hash,
        "hashed_session_key": hash_text(entry.session) if entry.session else None,
        "timestamp_span": {"start": entry.date_start, "end": entry.date_end},
        "line_count": entry.line_count,
        "record_count": entry.record_count,
    }


def root_label_for_entry(entry: ManifestEntry, plan: SourcePlan) -> str:
    entry_path = Path(entry.path)
    for index, root in enumerate(plan.roots, 1):
        root_path = root.path
        if path_is_under(entry_path, root_path):
            return f"{root.kind}:selected_root:{index}"
    return f"{entry.source_kind}:selected_root"


def path_is_under(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        resolved_path = path.absolute()
        resolved_root = root.absolute()
    if resolved_path == resolved_root:
        return True
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return False
    return True


def safe_path_hash(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


def hash_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def cost_model(initial: Manifest, refreshed: Manifest, shortlist: dict) -> dict:
    metrics = shortlist["metrics"]
    return {
        "build_time_ms": initial.metrics.get("elapsed_ms", 0),
        "refresh_time_ms": refreshed.metrics.get("elapsed_ms", 0),
        "manifest_disk_bytes": initial.metrics.get("estimated_manifest_bytes", 0),
        "shortlist_file_reduction_ratio": metrics["file_reduction_ratio"],
        "shortlist_byte_reduction_ratio": metrics["byte_reduction_ratio"],
        "estimated_direct_scan_bytes": metrics["total_bytes"],
        "estimated_shortlist_scan_bytes": metrics["selected_bytes"],
    }


def guidance(shortlist: dict) -> dict:
    metrics = shortlist["metrics"]
    valuable = [
        "large or repeated transcript searches",
        "vague date/project/newest-first lookups",
        "agent workflows with strict context budgets",
    ]
    unnecessary = [
        "tiny transcript roots",
        "one-off exact rg with a known file path",
        "cases where selected_files equals total_files",
    ]
    return {
        "ultra_valuable_when": valuable,
        "unnecessary_when": unnecessary,
        "shortlist_note": (
            "Shortlisting reduces candidate files before find-candidates/no-evidence-report, "
            "but excluded entries remain reportable as searched-scope metadata."
        ),
    }


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def min_datetime(current: datetime | None, candidate: datetime) -> datetime:
    if current is None:
        return candidate
    return min(current, candidate)


def max_datetime(current: datetime | None, candidate: datetime) -> datetime:
    if current is None:
        return candidate
    return max(current, candidate)


def reduction_ratio(selected: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(max(0.0, 1 - (selected / total)), 3)


def manifest_from_entries(entries: Iterable[ManifestEntry]) -> Manifest:
    manifest = Manifest(entries=list(entries))
    manifest.metrics = {
        "entry_count": len(manifest.entries),
        "estimated_manifest_bytes": len(json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")),
    }
    return manifest
