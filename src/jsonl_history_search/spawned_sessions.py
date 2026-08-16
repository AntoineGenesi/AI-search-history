from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .adapters import AdapterReadError, iter_records_for_file
from .agent_contract import read_more_actions_for_source_ref, source_ref_target
from .sources import SourcePlan, enumerate_jsonl_files, stat_size


SPAWN_EVENT_RE = re.compile(
    r"\b(?:spawn(?:ed|ing)?|subagent|sub-agent|worker|reviewer|rereviewer|re-reviewer|child\s+session)\b",
    re.IGNORECASE,
)
PARENT_FALLBACK_SPAWN_EVENT_RE = re.compile(
    r"\b(?:spawn(?:ed|ing)?|launch(?:ed|ing)?|started|created|child\s+(?:session|transcript)|worker\s+transcript|subagent\s+transcript|reviewer\s+transcript)\b",
    re.IGNORECASE,
)
JSONL_PATH_RE = re.compile(
    r"(?P<path>(?:/[^\s`\"'<>]+|[A-Za-z0-9_.~/-]+)?[A-Za-z0-9_.-]+\.jsonl)(?::(?P<line>\d+))?",
    re.IGNORECASE,
)
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
TASK_HANDLE_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+\b")


@dataclass(frozen=True)
class SpawnResolverLimits:
    max_depth: int = 2
    max_children: int = 8
    max_files: int = 120
    max_bytes: int = 5 * 1024 * 1024
    max_elapsed: float = 2.0


@dataclass(frozen=True)
class _ResolvedChild:
    path: Path | None
    line: int
    depth: int
    edge_source: str
    parent_path: Path | None = None
    source_owner_roles: tuple[str, ...] = ()
    task_correlation_matches: tuple[str, ...] = ()


def resolve_spawned_session_transcripts(
    *,
    query: str,
    plan: SourcePlan,
    candidates: list[dict[str, Any]],
    next_reads: list[dict[str, Any]],
    default_tz: Any,
    expansion_action_context: dict[str, Any] | None = None,
    include_context_actions: bool = False,
    limits: SpawnResolverLimits | None = None,
) -> dict[str, Any]:
    resolver = _SpawnedSessionResolver(
        query=query,
        plan=plan,
        candidates=candidates,
        next_reads=next_reads,
        default_tz=default_tz,
        expansion_action_context=expansion_action_context,
        include_context_actions=include_context_actions,
        limits=limits or SpawnResolverLimits(),
    )
    return resolver.resolve()


class _SpawnedSessionResolver:
    def __init__(
        self,
        *,
        query: str,
        plan: SourcePlan,
        candidates: list[dict[str, Any]],
        next_reads: list[dict[str, Any]],
        default_tz: Any,
        expansion_action_context: dict[str, Any] | None,
        include_context_actions: bool,
        limits: SpawnResolverLimits,
    ) -> None:
        self.query = query
        self.plan = plan
        self.candidates = candidates
        self.next_reads = next_reads
        self.default_tz = default_tz
        self.expansion_action_context = expansion_action_context
        self.include_context_actions = include_context_actions
        self.limits = limits
        self.started = time.monotonic()
        self.files_examined = 0
        self.bytes_examined = 0
        self.cap_hits: set[str] = set()
        self.omitted_reasons: list[str] = []
        self.missing: list[dict[str, Any]] = []
        self.detected_events: list[dict[str, Any]] = []
        self.children: list[_ResolvedChild] = []
        self._known_files: list[Path] | None = None
        self._task_handles = tuple(
            dict.fromkeys(match.group(0).casefold() for match in TASK_HANDLE_RE.finditer(query or ""))
        )

    def resolve(self) -> dict[str, Any]:
        query_wants_spawn = bool(SPAWN_EVENT_RE.search(self.query or ""))
        if not query_wants_spawn:
            return self._result(applies=False, status="not_applicable")
        parent_paths = self._parent_paths()

        for child in self._structured_edge_children(parent_paths):
            self._add_child(child)
        if not self.detected_events and self._within_caps():
            self._scan_parent_fallback(parent_paths)

        if not self.detected_events and not self.children and not self.missing and not query_wants_spawn:
            return self._result(applies=False, status="not_applicable")
        status = "spawn_detected" if self.detected_events or self.children or self.missing else "spawn_scope_requested"
        return self._result(applies=True, status=status)

    def _result(self, *, applies: bool, status: str) -> dict[str, Any]:
        child_reads = self._child_next_reads()
        diagnostics = {
            "spawn_detected": len(self.detected_events),
            "child_transcript_inspected": len(child_reads),
            "child_transcript_missing": len(self.missing),
            "child_scope_omitted": len(self.cap_hits) + len(self.omitted_reasons),
        }
        return {
            "schema_version": 1,
            "applies": applies,
            "status": status,
            "diagnostics": diagnostics,
            "diagnostic_labels": [
                label
                for label, count in diagnostics.items()
                if count
            ],
            "next_reads": child_reads,
            "child_transcript_refs": [
                {
                    "target": read.get("target"),
                    "source_ref": read.get("source_ref"),
                    "source_owner_roles": list(read.get("source_owner_roles") or []),
                }
                for read in child_reads
            ],
            "missing_children": self.missing[: self.limits.max_children],
            "limits": {
                "max_depth": self.limits.max_depth,
                "max_children": self.limits.max_children,
                "max_files": self.limits.max_files,
                "max_bytes": self.limits.max_bytes,
                "max_elapsed": self.limits.max_elapsed,
            },
            "observed": {
                "max_depth_seen": max((child.depth for child in self.children), default=0),
                "files_examined": self.files_examined,
                "bytes_examined": self.bytes_examined,
                "child_ref_count": len(child_reads),
                "task_correlated_child_ref_count": sum(
                    bool(child.task_correlation_matches) for child in self.children
                ),
                "edge_source_counts": _count_by(child.edge_source for child in self.children),
            },
            "caps": {
                "hit": bool(self.cap_hits or self.omitted_reasons),
                "caps_hit": sorted(self.cap_hits),
                "omitted_reasons": self.omitted_reasons[:8],
            },
            "privacy": {
                "raw_transcript_text_included": False,
                "raw_session_ids_included": False,
                "raw_rollout_ids_included": False,
                "sqlite_values_included": False,
            },
            "claim_boundaries": {
                "child_refs_are_source_leads_until_opened": True,
                "does_not_claim_exhaustive_child_session_discovery": True,
                "structured_edges_preferred_over_parent_event_fallback": True,
            },
        }

    def _parent_paths(self) -> list[Path]:
        refs: list[dict[str, Any]] = []
        for row in [*self.next_reads, *self.candidates]:
            if isinstance(row, dict) and isinstance(row.get("source_ref"), dict):
                refs.append(row["source_ref"])
        paths: list[Path] = []
        for ref in refs:
            raw = ref.get("path")
            if not raw:
                continue
            path = Path(str(raw)).expanduser()
            if not path.is_absolute():
                path = self._resolve_relative_path(path) or path
            if path.exists() and path.suffix == ".jsonl" and path not in paths:
                paths.append(path)
        if paths:
            return paths
        return self._known_jsonl_files()[: min(5, self.limits.max_files)]

    def _resolve_relative_path(self, path: Path) -> Path | None:
        for root in self._root_paths():
            base = root if root.is_dir() else root.parent
            candidate = base / path
            if candidate.exists():
                return candidate
        return None

    def _root_paths(self) -> list[Path]:
        return [root.path.expanduser() for root in self.plan.roots]

    def _known_jsonl_files(self) -> list[Path]:
        if self._known_files is not None:
            return self._known_files
        files: list[Path] = []
        for path, _size, _warnings in enumerate_jsonl_files(self.plan):
            if not self._within_caps():
                break
            files.append(path)
            if len(files) >= self.limits.max_files:
                self.cap_hits.add("max_files")
                break
        self._known_files = files
        return files

    def _state_db_paths(self) -> list[Path]:
        roots = self._root_paths()
        candidates: list[Path] = []
        for root in roots:
            if not self._within_caps():
                break
            if root.is_file() and root.suffix.lower() in SQLITE_SUFFIXES:
                candidates.append(root)
                continue
            for base in self._candidate_state_db_dirs(root):
                if not self._within_caps():
                    break
                for path in base.iterdir() if base.exists() and base.is_dir() else []:
                    if path.suffix.lower() not in SQLITE_SUFFIXES:
                        continue
                    if self.files_examined >= self.limits.max_files:
                        self.cap_hits.add("max_files")
                        break
                    size = stat_size(path)
                    if self.bytes_examined + size > self.limits.max_bytes:
                        self.cap_hits.add("max_bytes")
                        break
                    self.files_examined += 1
                    self.bytes_examined += size
                    candidates.append(path)
                    if len(candidates) >= 8:
                        self.cap_hits.add("state_db_file_cap")
                        break
        deduped: list[Path] = []
        for path in candidates:
            if path not in deduped:
                deduped.append(path)
        return deduped

    def _candidate_state_db_dirs(self, root: Path) -> list[Path]:
        base = root if root.is_dir() else root.parent
        candidates = [base, base.parent]
        if base.name in {"sessions", "projects"}:
            candidates.append(base.parent)
        deduped: list[Path] = []
        for path in candidates:
            if path not in deduped:
                deduped.append(path)
        return deduped

    def _structured_edge_children(self, parent_paths: list[Path]) -> list[_ResolvedChild]:
        rows: list[_ResolvedChild] = []
        candidate_cap = self._child_candidate_cap()
        for db_path in self._state_db_paths():
            rows.extend(self._structured_edge_children_from_db(db_path, parent_paths))
            if len(rows) >= candidate_cap:
                self.cap_hits.add("max_children")
                break
        prioritized = self._prioritize_children(rows)
        if len(prioritized) > self.limits.max_children:
            self.cap_hits.add("max_children")
        return prioritized[: self.limits.max_children]

    def _structured_edge_children_from_db(self, db_path: Path, parent_paths: list[Path]) -> list[_ResolvedChild]:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            self.omitted_reasons.append("state_db_unreadable")
            return []
        try:
            tables = set(_sqlite_table_names(conn))
            if "threads" not in tables or "thread_spawn_edges" not in tables:
                return []
            thread_rows = _sqlite_select_all(conn, "threads", limit=2000)
            edge_rows = _sqlite_select_all(conn, "thread_spawn_edges", limit=4000)
        except sqlite3.Error:
            self.omitted_reasons.append("state_db_schema_unreadable")
            return []
        finally:
            conn.close()

        thread_id_to_path: dict[str, str] = {}
        rollout_to_thread_ids: dict[str, list[str]] = {}
        for row in thread_rows:
            thread_id = _first_string(row, ("id", "thread_id", "uuid"))
            rollout_path = _first_string(row, ("rollout_path", "path", "transcript_path", "session_path"))
            if not thread_id or not rollout_path:
                continue
            thread_id_to_path[thread_id] = rollout_path
            for key in self._path_match_keys(rollout_path):
                rollout_to_thread_ids.setdefault(key, []).append(thread_id)

        parent_ids: set[str] = set()
        for parent_path in parent_paths:
            for key in self._path_match_keys(str(parent_path)):
                parent_ids.update(rollout_to_thread_ids.get(key, []))
        if not parent_ids and parent_paths:
            return []

        children: list[_ResolvedChild] = []
        frontier = [(parent_id, 0) for parent_id in sorted(parent_ids)]
        visited_threads: set[str] = set()
        candidate_cap = self._child_candidate_cap()
        while frontier and len(children) < candidate_cap and self._within_caps():
            parent_id, depth = frontier.pop(0)
            if parent_id in visited_threads:
                continue
            visited_threads.add(parent_id)
            if depth >= self.limits.max_depth:
                self.cap_hits.add("max_depth")
                continue
            for edge in edge_rows:
                if _edge_parent_id(edge) != parent_id:
                    continue
                child_id = _edge_child_id(edge)
                if not child_id:
                    continue
                self.detected_events.append({"source": "thread_spawn_edges", "depth": depth + 1})
                if child_id == parent_id:
                    self.cap_hits.add("cycle")
                    continue
                should_expand_child = child_id not in visited_threads
                child_rollout = thread_id_to_path.get(child_id)
                if not child_rollout:
                    self.missing.append({"diagnostic": "child_transcript_missing", "source": "thread_spawn_edges"})
                    continue
                child_path = self._resolve_rollout_path(child_rollout)
                if child_path is None or not child_path.exists():
                    self.missing.append({"diagnostic": "child_transcript_missing", "source": "thread_spawn_edges"})
                    continue
                line = self._first_record_line(child_path)
                if line is None:
                    self.missing.append({"diagnostic": "child_transcript_missing", "source": "thread_spawn_edges"})
                    continue
                children.append(
                    _ResolvedChild(
                        path=child_path,
                        line=line,
                        depth=depth + 1,
                        edge_source="thread_spawn_edges",
                        source_owner_roles=_transcript_source_owner_roles(child_path),
                        task_correlation_matches=self._task_correlation_matches(child_path),
                    )
                )
                if should_expand_child:
                    frontier.append((child_id, depth + 1))
                if len(children) >= candidate_cap:
                    self.cap_hits.add("max_children")
                    break
        return children

    def _path_match_keys(self, value: str) -> set[str]:
        path = Path(str(value)).expanduser()
        keys = {str(path), path.name}
        try:
            keys.add(str(path.resolve()))
        except OSError:
            pass
        return {key for key in keys if key}

    def _resolve_rollout_path(self, value: str) -> Path | None:
        raw = Path(value).expanduser()
        candidates = [raw] if raw.is_absolute() else []
        for root in self._root_paths():
            base = root if root.is_dir() else root.parent
            candidates.append(base / raw)
            candidates.append(base / raw.name)
        for known in self._known_jsonl_files():
            if known.name == raw.name or str(known) == str(raw):
                candidates.append(known)
        for candidate in candidates:
            if candidate.exists() and candidate.suffix == ".jsonl":
                return candidate
        return None

    def _scan_parent_fallback(self, parent_paths: list[Path]) -> None:
        queue = [(path, 0) for path in parent_paths]
        visited = {str(path) for path in parent_paths}
        while queue and self._within_caps():
            parent_path, depth = queue.pop(0)
            if depth >= self.limits.max_depth:
                self.cap_hits.add("max_depth")
                continue
            for child in self._parent_event_children(parent_path, depth=depth + 1):
                self._add_child(child)
                if child.path and str(child.path) not in visited:
                    visited.add(str(child.path))
                    queue.append((child.path, child.depth))
                if len(self.children) >= self.limits.max_children:
                    self.cap_hits.add("max_children")
                    return

    def _parent_event_children(self, parent_path: Path, *, depth: int) -> list[_ResolvedChild]:
        rows: list[_ResolvedChild] = []
        if not self._charge_file(parent_path):
            return rows
        try:
            for record in iter_records_for_file(parent_path, "unknown_jsonl", self.default_tz):
                if not self._within_caps():
                    return rows
                text = record.text or ""
                raw = record.raw if isinstance(record.raw, dict) else {}
                if not _record_looks_like_spawn_event(text, raw):
                    continue
                self.detected_events.append(
                    {"source": "parent_transcript_event", "depth": depth, "parent_line": record.line}
                )
                paths = _extract_jsonl_paths_from_event(text, raw)
                if not paths:
                    self.missing.append({"diagnostic": "child_transcript_missing", "source": "parent_transcript_event"})
                    continue
                for raw_path, raw_line in paths:
                    child_path = self._resolve_rollout_path(raw_path)
                    if child_path is None or not child_path.exists():
                        self.missing.append({"diagnostic": "child_transcript_missing", "source": "parent_transcript_event"})
                        continue
                    if raw_line and not self._charge_file(child_path):
                        continue
                    line = raw_line or self._first_record_line(child_path)
                    if line is None:
                        self.missing.append({"diagnostic": "child_transcript_missing", "source": "parent_transcript_event"})
                        continue
                    rows.append(
                        _ResolvedChild(
                            path=child_path,
                            line=line,
                            depth=depth,
                            edge_source="parent_transcript_event",
                            parent_path=parent_path,
                            source_owner_roles=_transcript_source_owner_roles(child_path),
                            task_correlation_matches=self._task_correlation_matches(child_path),
                        )
                    )
                    if len(rows) + len(self.children) >= self.limits.max_children:
                        self.cap_hits.add("max_children")
                        return rows
        except (AdapterReadError, OSError):
            self.omitted_reasons.append("parent_transcript_unreadable")
        return rows

    def _add_child(self, child: _ResolvedChild) -> None:
        if child.path is None:
            return
        if len(self.children) >= self.limits.max_children:
            self.cap_hits.add("max_children")
            return
        key = (str(child.path), int(child.line or 1))
        if any((str(existing.path), int(existing.line or 1)) == key for existing in self.children):
            return
        self.children.append(child)

    def _child_candidate_cap(self) -> int:
        return max(self.limits.max_children, self.limits.max_children * 4)

    def _prioritize_children(self, children: list[_ResolvedChild]) -> list[_ResolvedChild]:
        return sorted(
            children,
            key=lambda child: len(child.task_correlation_matches),
            reverse=True,
        )

    def _task_correlation_matches(self, path: Path) -> tuple[str, ...]:
        if not self._task_handles:
            return ()
        matches = {handle for handle in self._task_handles if handle in path.name.casefold()}
        bytes_read = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    bytes_read += len(line.encode("utf-8", errors="replace"))
                    matches.update(task for task in self._task_handles if task in line.casefold())
                    if bytes_read >= 262144:
                        break
        except OSError:
            return tuple(sorted(matches))
        return tuple(sorted(matches))

    def _first_record_line(self, path: Path) -> int | None:
        if not self._charge_file(path):
            return None
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_no, line in enumerate(handle, 1):
                    if line.strip():
                        return line_no
        except OSError:
            return None
        return None

    def _charge_file(self, path: Path) -> bool:
        if self.files_examined >= self.limits.max_files:
            self.cap_hits.add("max_files")
            return False
        size = stat_size(path)
        if self.bytes_examined + size > self.limits.max_bytes:
            self.cap_hits.add("max_bytes")
            return False
        self.files_examined += 1
        self.bytes_examined += size
        return True

    def _within_caps(self) -> bool:
        if time.monotonic() - self.started > self.limits.max_elapsed:
            self.cap_hits.add("max_elapsed")
            return False
        if self.files_examined > self.limits.max_files:
            self.cap_hits.add("max_files")
            return False
        if self.bytes_examined > self.limits.max_bytes:
            self.cap_hits.add("max_bytes")
            return False
        return True

    def _child_next_reads(self) -> list[dict[str, Any]]:
        reads: list[dict[str, Any]] = []
        for index, child in enumerate(self.children[: self.limits.max_children], 1):
            if child.path is None:
                continue
            source_ref = {"path": str(child.path), "line": child.line}
            target = source_ref_target(source_ref)
            if not target:
                continue
            candidate_id = f"spawned-child-transcript-{index:03d}"
            read: dict[str, Any] = {
                "kind": "spawned_child_transcript_source_ref",
                "target": target,
                "candidate_id": candidate_id,
                "source_ref": source_ref,
                "reason": "inspect spawned child transcript as primary source evidence before relying on parent summaries",
                "target_source_role": "target_historical_raw",
                "final_support_allowed": True,
                "source_owner_roles": list(child.source_owner_roles),
                "spawned_session_resolution": {
                    "diagnostic": "child_transcript_inspected",
                    "edge_source": child.edge_source,
                    "depth": child.depth,
                    "task_correlated": bool(child.task_correlation_matches),
                    "task_correlation_match_count": len(child.task_correlation_matches),
                    "raw_private_text_included": False,
                    "raw_session_ids_included": False,
                },
                "expansion_actions": read_more_actions_for_source_ref(
                    source_ref,
                    candidate_id=candidate_id,
                    expansion_action_context=self.expansion_action_context,
                ),
            }
            if self.include_context_actions:
                from .agent_contract import context_actions_for_source_ref

                read["context_actions"] = context_actions_for_source_ref(
                    source_ref,
                    candidate_id=candidate_id,
                    expansion_action_context=self.expansion_action_context,
                )
            reads.append(read)
        return reads


def _sqlite_table_names(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [str(row[0]) for row in cursor.fetchall()]


def _sqlite_select_all(conn: sqlite3.Connection, table_name: str, *, limit: int) -> list[dict[str, Any]]:
    safe_table = '"' + table_name.replace('"', '""') + '"'
    cursor = conn.execute(f"SELECT * FROM {safe_table} LIMIT ?", (limit,))
    columns = [str(item[0]) for item in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _first_string(row: dict[str, Any], names: tuple[str, ...]) -> str | None:
    lowered = {key.casefold(): value for key, value in row.items()}
    for name in names:
        value = lowered.get(name.casefold())
        if value not in (None, ""):
            return str(value)
    return None


def _transcript_source_owner_roles(path: Path) -> tuple[str, ...]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(raw, dict) or str(raw.get("type") or "") != "session_meta":
                    continue
                payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
                role = _normalized_source_owner_role(
                    payload.get("agent_role") or payload.get("agent_nickname")
                )
                return (role,) if role else ()
    except OSError:
        return ()
    return ()


def _normalized_source_owner_role(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z]+", "_", str(value or "").casefold()).strip("_")
    role_tokens = set(normalized.split("_"))
    if normalized in {"worker", "implementer", "implementation"} or role_tokens.intersection(
        {"worker", "implementer"}
    ):
        return "worker"
    if normalized in {"reviewer", "rereviewer", "re_reviewer"} or role_tokens.intersection(
        {"reviewer", "rereviewer"}
    ):
        return "reviewer"
    if normalized in {"parent", "coordinator", "orchestrator"}:
        return "parent_or_coordinator"
    return None


def _edge_parent_id(row: dict[str, Any]) -> str | None:
    return _first_string(row, ("parent_thread_id", "parent_id", "source_thread_id", "source_id", "from_thread_id"))


def _edge_child_id(row: dict[str, Any]) -> str | None:
    return _first_string(row, ("child_thread_id", "child_id", "target_thread_id", "target_id", "to_thread_id"))


def _record_looks_like_spawn_event(text: str, raw: dict[str, Any]) -> bool:
    if PARENT_FALLBACK_SPAWN_EVENT_RE.search(text or ""):
        return True
    raw_text = json.dumps(raw, ensure_ascii=False, sort_keys=True) if raw else ""
    return bool(PARENT_FALLBACK_SPAWN_EVENT_RE.search(raw_text))


def _extract_jsonl_paths_from_event(text: str, raw: dict[str, Any]) -> list[tuple[str, int | None]]:
    values: list[str] = []
    values.extend(_jsonl_strings_from_value(raw))
    values.extend(match.group("path") + (f":{match.group('line')}" if match.group("line") else "") for match in JSONL_PATH_RE.finditer(text or ""))
    rows: list[tuple[str, int | None]] = []
    seen: set[tuple[str, int | None]] = set()
    for value in values:
        match = JSONL_PATH_RE.search(value)
        if not match:
            continue
        path = match.group("path")
        line = int(match.group("line")) if match.group("line") else None
        key = (path, line)
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
    return rows


def _jsonl_strings_from_value(value: Any) -> list[str]:
    rows: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).casefold()
            if isinstance(child, str) and (child.endswith(".jsonl") or ".jsonl:" in child):
                rows.append(child)
            elif any(token in key_text for token in ("path", "transcript", "rollout", "session")):
                rows.extend(_jsonl_strings_from_value(child))
            elif isinstance(child, (dict, list)):
                rows.extend(_jsonl_strings_from_value(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_jsonl_strings_from_value(child))
    elif isinstance(value, str) and ".jsonl" in value:
        rows.append(value)
    return rows


def _count_by(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
