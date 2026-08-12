from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .latest_user_messages import latest_user_messages
from .worktree_currentness import build_worktree_currentness_report, is_worktree_cleanup_currentness_query


SCHEMA_VERSION = 1

ARTIFACT_SUFFIXES = {".md", ".txt", ".rst", ".json", ".yaml", ".yml"}
ARTIFACT_DIRS = (
    "tasks",
    "docs",
    "reports",
    "reviews",
    "codex/state",
    "codex/docs",
    "state",
    "plans",
    "tickets",
    "issues",
)
TOP_LEVEL_ARTIFACTS = (
    "TODO.md",
    "TASKS.md",
    "PLAN.md",
    "PLANS.md",
    "ROADMAP.md",
    "BACKLOG.md",
    "PROJECT.md",
    "README.md",
    "CODEX-ORCHESTRATION.md",
)
FRONT_DOOR_PATHS = (
    "codex/state/pipeline-state.md",
    "state/current.md",
    "CURRENT.md",
    "current.md",
    "docs/current.md",
    "TODO.md",
    "TASKS.md",
    "PLAN.md",
    "ROADMAP.md",
    "BACKLOG.md",
    "PROJECT.md",
    "CODEX-ORCHESTRATION.md",
    "README.md",
)
NATURAL_CURRENTNESS_MARKERS = (
    "which plan",
    "which todo",
    "which task",
    "what's next",
    "what is next",
    "next real task",
    "current plan",
    "is this still",
    "still what",
    "why are there",
    "so many plans",
    "three next",
    "stale",
    "superseded",
    "current direction",
    "what are we doing",
    "still current",
    "ticket current",
    "issue current",
)
FIELD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?[\"']?([A-Za-z][A-Za-z0-9 _-]{1,40})[\"']?\s*[:=]\s*(.+?)\s*,?\s*$",
    re.IGNORECASE,
)
CLAIM_FIELD_NAMES = {
    "assignee",
    "assignees",
    "closed",
    "closed at",
    "cycle",
    "issue state",
    "label",
    "labels",
    "milestone",
    "phase",
    "priority",
    "project",
    "resolution",
    "state",
    "status",
    "team",
    "ticket state",
    "updated at",
    "workflow",
    "workflow state",
}
HEADING_RE = re.compile(r"^\s{0,3}#{1,3}\s+(.+?)\s*$")
PATHISH_RE = re.compile(r"(^|[\s`'\"])([./\w-]+/(?:[./\w-]+)|[./\w-]+\.(?:md|txt|rst|json|ya?ml))($|[\s`'\"])", re.IGNORECASE)
NAMED_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,15})-(\d{1,12})(?![A-Za-z0-9])")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "do",
    "does",
    "for",
    "from",
    "here",
    "is",
    "it",
    "of",
    "or",
    "should",
    "still",
    "the",
    "there",
    "this",
    "three",
    "to",
    "what",
    "which",
    "why",
    "with",
}


def build_artifact_currentness_payload(args: Any) -> dict:
    requested_cwd = Path(args.cwd).expanduser().resolve()
    query = str(getattr(args, "artifact_or_query", "") or "").strip()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    cwd, workspace_resolution = _resolve_cleanup_workspace(
        query=query,
        requested_cwd=requested_cwd,
        codex_home=codex_home,
        max_files=min(getattr(args, "max_files", 200) or 200, 50),
    )
    started = time.monotonic()
    latest_limit = min(getattr(args, "max_matches", 40) or 40, 8)
    latest = {}
    try:
        latest = latest_user_messages(
            codex_home=codex_home,
            default_tz=_timezone_from_args(args),
            limit=latest_limit,
            max_files=min(getattr(args, "max_files", 200) or 200, 50),
            max_bytes=min(getattr(args, "max_bytes", 100 * 1024 * 1024) or 0, 5 * 1024 * 1024),
            max_elapsed=min(getattr(args, "max_elapsed", 15.0) or 15.0, 2.0),
            snippet_chars=240,
        )
    except Exception as exc:  # pragma: no cover - defensive around optional local history shapes.
        latest = {"messages": [], "warnings": [f"latest-user-messages unavailable: {exc}"]}
    report = artifact_currentness_report(
        query=query,
        cwd=cwd,
        latest=latest,
        started=started,
    )
    report["workspace_resolution"] = workspace_resolution
    return {"artifact_currentness": report}


def _resolve_cleanup_workspace(
    *,
    query: str,
    requested_cwd: Path,
    codex_home: Path,
    max_files: int,
) -> tuple[Path, dict]:
    base = {
        "status": "requested_workspace_used",
        "requested_cwd": str(requested_cwd),
        "resolved_cwd": str(requested_cwd),
        "source_role": "workspace_navigation",
        "causal_proof_allowed": False,
    }
    if not is_worktree_cleanup_currentness_query(query) or _has_local_cleanup_surface(requested_cwd):
        return requested_cwd, base

    sessions_root = codex_home / "sessions"
    if not sessions_root.is_dir():
        return requested_cwd, {**base, "status": "historical_workspace_not_found"}
    session_paths = sorted(
        sessions_root.rglob("*.jsonl"),
        key=lambda path: _mtime(path),
        reverse=True,
    )[:max_files]
    for session_path in session_paths:
        for line_number, candidate in _session_workspace_candidates(session_path):
            for root in _git_checkout_roots(candidate):
                if not _query_names_checkout(query, root):
                    continue
                return root, {
                    **base,
                    "status": "historical_session_workspace_recovered",
                    "resolved_cwd": str(root),
                    "navigation_source": f"codex_session_metadata:{session_path.name}:{line_number}",
                    "boundary": (
                        "Session cwd metadata resolved the checkout only; it is navigation, not cleanup-causality proof."
                    ),
                }
    return requested_cwd, {**base, "status": "historical_workspace_not_found"}


def _has_local_cleanup_surface(cwd: Path) -> bool:
    if any((cwd / path).exists() for path in ("reports", "tasks", "docs", "codex/state", "TODO.md", "TASKS.md")):
        return True
    result = _run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    return result.returncode == 0


def _session_workspace_candidates(path: Path) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                if line_number > 12:
                    break
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                for value in (row.get("cwd"), payload.get("cwd")):
                    if isinstance(value, str) and value.strip():
                        candidates.append((line_number, Path(value).expanduser()))
    except OSError:
        return []
    return candidates


def _git_checkout_roots(candidate: Path) -> list[Path]:
    roots: list[Path] = []
    top = _run(["git", "-C", str(candidate), "rev-parse", "--show-toplevel"])
    if top.returncode != 0:
        return roots
    top_path = Path(top.stdout.strip()).resolve()
    roots.append(top_path)
    common = _run(["git", "-C", str(candidate), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common.returncode == 0:
        common_path = Path(common.stdout.strip()).resolve()
        parent_checkout = common_path.parent if common_path.name == ".git" else None
        if parent_checkout and parent_checkout not in roots:
            roots.append(parent_checkout)
    return roots


def _query_names_checkout(query: str, root: Path) -> bool:
    query_tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
    root_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", root.name.casefold())
        if len(token) >= 3
    }
    return len(root_tokens) >= 2 and root_tokens <= query_tokens


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr=str(exc))


def artifact_currentness_report(*, query: str, cwd: Path, latest: dict | None = None, started: float | None = None) -> dict:
    latest = latest or {}
    detected_intent = detect_artifact_currentness_intent(query, cwd)
    if detected_intent == "worktree_cleanup_currentness":
        worktree_report = build_worktree_currentness_report(query, cwd)
        elapsed = None if started is None else round(time.monotonic() - started, 6)
        chain = _worktree_evidence_chain(worktree_report)
        cleanup = worktree_report.get("cleanup_causality") or {}
        causal_why = cleanup.get("causal_summary") or (
            "The query combines worktree/checkout/branch identity with cleanup/currentness wording, so the answer target is worktree state rather than a current-plan artifact."
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "query": query,
            "detected_intent": detected_intent,
            "artifact_candidates": _worktree_candidates(worktree_report),
            "selected_artifact_or_topic": {
                "kind": "worktree_cleanup_currentness",
                "topic": query,
                "selection_reason": "worktree cleanup/currentness intent; generic artifact selection bypassed",
            },
            "currentness_verdict": "worktree_cleanup_currentness",
            "why": causal_why,
            "evidence_chain": chain,
            "status_field_assessment": {
                "status_fields": [],
                "claim_present": False,
                "assessment": "worktree_mode_uses_git_artifact_and_private_evidence_not_status_field_only",
            },
            "current_front_door": _current_front_door(cwd),
            "next_reads": _worktree_next_reads(worktree_report, chain),
            "blind_spots": _worktree_blind_spots(worktree_report, latest),
            "worktree_currentness": worktree_report,
            "summary": {
                "artifact_count": 0,
                "reference_count": len(chain),
                "latest_jsonl_evidence_count": 0,
                "worktree_classification_count": len(worktree_report.get("classifications") or []),
                "elapsed_seconds": elapsed,
            },
        }
    artifacts = _discover_artifacts(cwd)
    explicit_path = _explicit_path(query, cwd)
    front_door = _current_front_door(cwd)
    named_targets = _named_identifiers(query)
    named_artifacts = _named_target_artifacts(named_targets, artifacts, cwd)
    if named_targets and not explicit_path and not named_artifacts:
        return _named_target_missing_report(
            query=query,
            cwd=cwd,
            artifacts=artifacts,
            named_targets=named_targets,
            front_door=front_door,
            latest=latest,
            started=started,
            detected_intent=detected_intent,
        )
    selection_artifacts = named_artifacts or artifacts
    selected = _select_artifact_or_topic(query, cwd, selection_artifacts, explicit_path, front_door)
    candidates = _rank_candidates(query, cwd, selection_artifacts, selected, front_door)
    selected_path = selected.get("path")
    status_assessment = _status_field_assessment(selected_path, cwd)
    references = _later_references(query, cwd, artifacts, selected, front_door)
    git = _git_evidence(cwd, selected_path)
    latest_evidence = _latest_jsonl_evidence(query, selected, latest)
    verdict, why = _verdict_and_why(
        query=query,
        selected=selected,
        front_door=front_door,
        status_assessment=status_assessment,
        references=references,
        git=git,
    )
    status_assessment["assessment"] = _status_assessment_label(status_assessment, verdict, references, front_door, selected)
    chain = _evidence_chain(selected, status_assessment, git, references, front_door, latest_evidence)
    next_reads = _next_reads(selected, front_door, chain)
    blind_spots = _blind_spots(latest, git, explicit_path, selected, references)
    elapsed = None if started is None else round(time.monotonic() - started, 6)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if selected else "unknown",
        "query": query,
        "detected_intent": detected_intent,
        "artifact_candidates": candidates[:12],
        "selected_artifact_or_topic": selected,
        "named_target_gate": {
            "status": "matched" if named_targets else "not_applicable",
            "named_targets": named_targets,
            "matching_artifact_count": len(named_artifacts),
        },
        "currentness_verdict": verdict,
        "why": why,
        "evidence_chain": chain,
        "status_field_assessment": status_assessment,
        "current_front_door": front_door,
        "next_reads": next_reads,
        "blind_spots": blind_spots,
        "summary": {
            "artifact_count": len(artifacts),
            "reference_count": len(references),
            "latest_jsonl_evidence_count": len(latest_evidence),
            "elapsed_seconds": elapsed,
        },
    }


def _named_identifiers(text: str) -> list[str]:
    return sorted(
        {
            f"{match.group(1).upper()}-{match.group(2)}"
            for match in NAMED_IDENTIFIER_RE.finditer(str(text or ""))
        }
    )


def _named_target_artifacts(named_targets: list[str], artifacts: list[Path], cwd: Path) -> list[Path]:
    if not named_targets:
        return []
    targets = set(named_targets)
    matched = []
    for path in artifacts:
        rel = _rel(path, cwd)
        heading = str(_first_heading(path, cwd)[1] or "")
        artifact_ids = set(_named_identifiers(f"{rel} {heading}"))
        if targets & artifact_ids:
            matched.append(path)
    return matched


def _named_target_missing_report(
    *,
    query: str,
    cwd: Path,
    artifacts: list[Path],
    named_targets: list[str],
    front_door: dict,
    latest: dict,
    started: float | None,
    detected_intent: str,
) -> dict:
    target_pattern = "|".join(named_targets)
    proof_command = (
        "rg -n -i --glob '*.md' --glob '*.txt' --glob '*.rst' --glob '*.json' "
        f"--glob '*.yaml' --glob '*.yml' {json.dumps(target_pattern)} tasks docs reports reviews codex/state state plans tickets issues"
    )
    elapsed = None if started is None else round(time.monotonic() - started, 6)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "blocked_named_target_missing",
        "query": query,
        "detected_intent": detected_intent,
        "artifact_candidates": [],
        "selected_artifact_or_topic": {
            "kind": "named_target_missing",
            "topic": ", ".join(named_targets),
            "selection_reason": "no artifact path or heading matches an explicit named target; adjacent topical artifacts were not selected",
        },
        "named_target_gate": {
            "status": "blocked_named_target_missing",
            "named_targets": named_targets,
            "matching_artifact_count": 0,
            "required_next_proof": "locate or restore an artifact whose path or heading names the explicit target",
            "next_proof_command": proof_command,
        },
        "currentness_verdict": "unknown",
        "why": f"No artifact path or heading matched the explicit named target(s): {', '.join(named_targets)}. Adjacent topical artifacts were excluded.",
        "evidence_chain": [],
        "status_field_assessment": {
            "status_fields": [],
            "claim_present": False,
            "assessment": "named_target_missing_no_status_claim_used",
        },
        "current_front_door": front_door,
        "next_reads": [
            {
                "source_ref": ", ".join(named_targets),
                "reason": "copyable next proof step for the missing named target",
                "read_more_command": proof_command,
            }
        ],
        "blind_spots": _blind_spots(latest, {}, None, {}, []),
        "summary": {
            "artifact_count": len(artifacts),
            "reference_count": 0,
            "latest_jsonl_evidence_count": 0,
            "elapsed_seconds": elapsed,
        },
    }


def detect_artifact_currentness_intent(query: str, cwd: Path | None = None) -> str:
    normalized = _normalize(query)
    if is_worktree_cleanup_currentness_query(query):
        return "worktree_cleanup_currentness"
    if cwd and _explicit_path(query, cwd):
        return "explicit_artifact_check"
    if any(marker in normalized for marker in ("why are there", "so many", "three next", "multiple", "many plans")):
        return "repeated_topic_currentness"
    if "ticket" in normalized or "issue" in normalized:
        return "ticket_currentness"
    if any(marker in normalized for marker in NATURAL_CURRENTNESS_MARKERS):
        return "natural_direction_currentness"
    return "artifact_or_topic_currentness"


def is_artifact_currentness_question(query: str) -> bool:
    normalized = _normalize(query)
    return (
        is_worktree_cleanup_currentness_query(query)
        or any(marker in normalized for marker in NATURAL_CURRENTNESS_MARKERS)
        or (
            any(marker in normalized for marker in ("ticket", "issue", "github", "linear"))
            and any(marker in normalized for marker in ("current", "stale", "superseded", "obsolete", "still open", "still active"))
        )
        or bool(PATHISH_RE.search(query))
    )


def _discover_artifacts(cwd: Path) -> list[Path]:
    paths: list[Path] = []
    for name in TOP_LEVEL_ARTIFACTS:
        path = cwd / name
        if path.is_file():
            paths.append(path)
    for rel in ARTIFACT_DIRS:
        root = cwd / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES:
                paths.append(path)
    return sorted(set(paths), key=lambda item: _rel(item, cwd))


def _explicit_path(query: str, cwd: Path) -> Path | None:
    cleaned = query.strip().strip("`'\"")
    if _safe_direct_path_query(cleaned):
        direct = (cwd / cleaned).resolve()
        if _is_file_inside(direct, cwd):
            return direct
    for match in PATHISH_RE.finditer(query):
        candidate = (cwd / match.group(2)).resolve()
        if _is_file_inside(candidate, cwd):
            return candidate
    return None


def _safe_direct_path_query(cleaned: str) -> bool:
    if not cleaned or len(cleaned) > 240 or "\n" in cleaned:
        return False
    if any(char.isspace() for char in cleaned) and not PATHISH_RE.fullmatch(cleaned):
        return False
    return any(marker in cleaned for marker in ("/", "\\")) or Path(cleaned).suffix.lower() in ARTIFACT_SUFFIXES


def _is_file_inside(path: Path, cwd: Path) -> bool:
    try:
        return path.is_file() and _inside(path, cwd)
    except OSError:
        return False


def _select_artifact_or_topic(query: str, cwd: Path, artifacts: list[Path], explicit_path: Path | None, front_door: dict) -> dict:
    if explicit_path:
        return _candidate_item(explicit_path, cwd, "explicit query path", score=1000)
    front_door_first = _front_door_first_query(query)
    if not front_door_first:
        ranked = _rank_candidates(query, cwd, artifacts, {}, {})
        if ranked and ranked[0].get("score", 0) > 0:
            item = dict(ranked[0])
            item["selection_reason"] = "highest-scoring topic artifact candidate"
            return item
    controlling = front_door.get("controlling_artifact") or front_door.get("current_plan")
    if controlling:
        return {
            "kind": _artifact_kind(cwd / controlling["path"], cwd) if controlling.get("path") else "topic",
            "topic": query,
            "path": controlling.get("path"),
            "source_ref": controlling.get("source_ref"),
            "selection_reason": controlling.get("relationship") or "active current front-door item",
            "currentness_role": controlling.get("currentness_role"),
            "role_label": controlling.get("role_label"),
        }
    if front_door.get("source_refs"):
        first = front_door["source_refs"][0]
        return {
            "kind": "topic",
            "topic": query,
            "path": first.get("path"),
            "source_ref": first.get("source_ref"),
            "selection_reason": "current front-door state",
        }
    ranked = _rank_candidates(query, cwd, artifacts, {}, front_door)
    if ranked:
        item = dict(ranked[0])
        item["selection_reason"] = "highest-scoring local artifact candidate"
        return item
    return {"kind": "topic", "topic": query, "selection_reason": "no local artifact candidate selected"}


def _front_door_first_query(query: str) -> bool:
    normalized = _normalize(query)
    return any(
        marker in normalized
        for marker in (
            "which plan",
            "current plan",
            "which todo",
            "which task",
            "what's next",
            "what is next",
            "next real task",
            "what are we doing",
            "why are there",
            "so many plans",
            "three next",
        )
    )


def _rank_candidates(query: str, cwd: Path, artifacts: list[Path], selected: dict, front_door: dict) -> list[dict]:
    terms = _query_terms(query)
    query_normalized = _normalize(query)
    ticketish_query = any(marker in query_normalized for marker in ("ticket", "issue", "github", "linear"))
    candidates = []
    selected_rel = selected.get("path")
    for path in artifacts:
        rel = _rel(path, cwd)
        text = _small_text(path)
        normalized = _normalize(f"{rel} {text[:2000]}")
        score = 0
        if rel == selected_rel:
            score += 1000
        if rel in {item.get("path") for item in front_door.get("source_refs", [])}:
            score += 120
        if (front_door.get("current_plan") or {}).get("path") == rel:
            score += 180
        name = path.name.casefold()
        rel_normalized = _normalize(rel)
        heading = str(_first_heading(path, cwd)[1] or "")
        heading_normalized = _normalize(heading)
        kind = _artifact_kind(path, cwd)
        if ticketish_query and kind in {"ticket", "issue"}:
            score += 70
        if any(word in name for word in ("plan", "todo", "task", "roadmap", "backlog", "ticket", "issue", "state")):
            score += 25
        for term in terms:
            if term in normalized:
                score += 8
            if term in rel_normalized:
                score += 45
            if term in heading_normalized:
                score += 30
        if score <= 0 and len(candidates) >= 30:
            continue
        candidates.append(_candidate_item(path, cwd, "local artifact discovery", score=score))
    candidates.sort(key=lambda item: (item["score"], item["path"]), reverse=True)
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def _candidate_item(path: Path, cwd: Path, reason: str, score: int = 0) -> dict:
    rel = _rel(path, cwd)
    heading_ref, heading = _first_heading(path, cwd)
    statuses = _status_fields(path, cwd)
    kind = _artifact_kind(path, cwd)
    source_ref = heading_ref or (statuses[0]["source_ref"] if statuses else rel)
    return {
        "kind": kind,
        "artifact_type": kind,
        "path": rel,
        "source_ref": source_ref,
        "title": heading,
        "status_fields": statuses[:3],
        "reason": reason,
        "score": score,
    }


def _current_front_door(cwd: Path) -> dict:
    refs = []
    order = 0
    for rel in FRONT_DOOR_PATHS:
        path = cwd / rel
        if not path.is_file():
            continue
        lines = _read_lines(path)
        for line_no, line in enumerate(lines[:200], 1):
            normalized = _normalize(line)
            next_line = _next_nonempty_line(lines, line_no)
            has_path = bool(_extract_existing_paths(line, cwd) or _extract_existing_paths(next_line, cwd))
            if not (
                (line.strip().casefold().endswith("plan:") and has_path)
                or any(
                marker in normalized
                for marker in (
                    "active_task",
                    "current product task",
                    "current next-loop plan",
                    "current next loop plan",
                    "current plan",
                    "current ticket",
                    "current issue",
                    "active issue",
                    "active ticket",
                    "current task",
                    "current todo",
                    "active ticket",
                    "active task",
                    "active issue",
                    "source of truth",
                    "front door",
                    "current state",
                    "supersedes",
                    "superseded by",
                    "replaces",
                )
                )
            ):
                continue
            context_line = line
            if next_line and not _extract_existing_path(context_line, cwd):
                context_line = f"{line} {next_line}"
            role, priority, label = _front_door_role(rel, context_line)
            extracted_paths = _extract_existing_paths(context_line, cwd)
            primary_path = extracted_paths[0] if extracted_paths else None
            item = {
                "path": rel,
                "source_ref": f"{rel}:{line_no}",
                "snippet": _compact(context_line),
                "relationship": role,
                "currentness_role": role,
                "role_label": label,
                "role_priority": priority,
                "_source_order": order,
            }
            if primary_path:
                item["artifact_path"] = _rel(primary_path, cwd)
                item["path"] = _rel(primary_path, cwd)
                item["front_door_path"] = rel
                primary_rel = _rel(primary_path, cwd)
                if role == "active_current" and (
                    primary_rel.startswith("codex/state/")
                    or primary_rel.startswith("state/")
                    or primary_rel in {"CURRENT.md", "current.md", "docs/current.md"}
                ):
                    role, priority, label = "active_current_state", 90, "Active current state"
                    item["relationship"] = role
                    item["currentness_role"] = role
                    item["role_label"] = label
                    item["role_priority"] = priority
            elif role == "active_current":
                role, priority, label = "current_front_door", 40, "Current front-door evidence"
                item["relationship"] = role
                item["currentness_role"] = role
                item["role_label"] = label
                item["role_priority"] = priority
            refs.append(item)
            order += 1
    refs.sort(key=lambda item: (-int(item.get("role_priority", 0)), int(item.get("_source_order", 0))))
    for item in refs:
        item.pop("_source_order", None)
    controlling = next(
        (item for item in refs if item.get("artifact_path") and item.get("currentness_role") == "active_current"),
        None,
    )
    if controlling is None:
        controlling = next((item for item in refs if item.get("path") and item.get("currentness_role") == "active_current_state"), None)
    if controlling is None:
        controlling = next(
            (item for item in refs if item.get("artifact_path") and item.get("currentness_role") == "current_gate_plan"),
            None,
        )
    if controlling is None:
        controlling = next((item for item in refs if item.get("path") and item.get("currentness_role") == "current_plan_pointer"), None)
    active = [
        item
        for item in refs
        if item.get("currentness_role") in {"active_current", "active_current_state", "current_gate_plan"}
        and (item.get("artifact_path") or item.get("currentness_role") == "active_current_state")
    ]
    supporting = [item for item in refs if item.get("currentness_role") in {"current_plan_pointer", "supporting_context"}]
    provenance = [item for item in refs if item.get("currentness_role") == "historical_provenance"]
    return {
        "status": "found" if refs else "not_found",
        "source_refs": refs[:8],
        "current_plan": controlling,
        "controlling_artifact": controlling,
        "active_current": active[:6],
        "supporting_context": supporting[:6],
        "provenance": provenance[:6],
    }


def _front_door_role(front_door_path: str, context_line: str) -> tuple[str, int, str]:
    normalized = _normalize(context_line)
    if any(normalized.startswith(prefix) for prefix in ("attempt_learning_record:", "evidence_required_before_next_task:", "support_work_inside_task:")):
        return "supporting_context", 35, "Supporting context"
    if any(
        marker in normalized
        for marker in (
            "active_task",
            "current product task",
            "active task",
            "current task",
            "current todo",
            "current ticket",
            "active ticket",
            "current issue",
            "active issue",
        )
    ):
        return "active_current", 100, "Active current task/artifact"
    if "current fsm" in normalized or "current state" in normalized:
        return "active_current_state", 90, "Active current state"
    if "gate plan" in normalized or re.search(r"\b[a-z0-9 -]*plan:\s+`?[./\w-]+", normalized):
        return "current_gate_plan", 95, "Active current/gate plan"
    if any(marker in normalized for marker in ("current next-loop plan", "current next loop plan", "current plan")):
        return "current_plan_pointer", 70, "Supporting plan pointer"
    if any(marker in normalized for marker in ("source of truth", "front door")):
        return "active_current_state", 65, "Current front-door state"
    if any(marker in normalized for marker in ("current coordination plan", "current handoff", "product direction", "mechanisms")):
        return "supporting_context", 50, "Supporting context"
    if any(marker in normalized for marker in ("historical/provenance", "historical provenance", "older ", "previous completed")):
        return "historical_provenance", 10, "Historical/provenance"
    if front_door_path in {"TODO.md", "TASKS.md"} and "plan" in normalized:
        return "supporting_context", 45, "Supporting context"
    return "current_front_door", 40, "Current front-door evidence"


def _later_references(query: str, cwd: Path, artifacts: list[Path], selected: dict, front_door: dict) -> list[dict]:
    selected_path = selected.get("path")
    tokens = _reference_tokens(query, selected)
    refs = []
    if not tokens:
        return refs
    selected_mtime = _mtime(cwd / selected_path) if selected_path else None
    for path in artifacts:
        rel = _rel(path, cwd)
        if rel == selected_path:
            continue
        frontdoor = any(item.get("path") == rel for item in front_door.get("source_refs", []))
        for line_no, line in _line_matches(path, tuple(tokens)):
            relationship = _reference_relationship(line, frontdoor=frontdoor)
            if relationship == "mention" and not frontdoor:
                continue
            is_later = selected_mtime is None or frontdoor or _mtime(path) >= selected_mtime
            refs.append(
                {
                    "path": rel,
                    "source_ref": f"{rel}:{line_no}",
                    "snippet": _compact(line),
                    "relationship": relationship,
                    "is_later_or_front_door": is_later,
                }
            )
    refs.sort(key=lambda item: (not item["is_later_or_front_door"], item["path"], item["source_ref"]))
    return refs[:20]


def _status_field_assessment(path_rel: str | None, cwd: Path) -> dict:
    if not path_rel:
        return {
            "status_fields": [],
            "claim_present": False,
            "assessment": "no_artifact_status_field",
            "artifact_type": None,
            "claim_field_names": [],
            "claim_source": "none",
        }
    fields = _status_fields(cwd / path_rel, cwd)
    artifact_type = _artifact_kind(cwd / path_rel, cwd)
    claim_names = sorted({field.get("name", "") for field in fields if field.get("name")})
    ticketish = artifact_type in {"ticket", "issue"} or any(
        name in claim_names
        for name in ("issue state", "ticket state", "workflow state", "labels", "milestone", "project")
    )
    return {
        "status_fields": fields,
        "claim_present": bool(fields),
        "artifact_type": artifact_type,
        "claim_field_names": claim_names,
        "claim_source": "exported_ticket_fields" if ticketish and fields else ("artifact_status_fields" if fields else "none"),
        "assessment": "claim_only_not_authoritative" if fields else "no_artifact_status_field",
    }


def _status_assessment_label(status: dict, verdict: str, references: list[dict], front_door: dict, selected: dict) -> str:
    if not status.get("claim_present"):
        return "no_artifact_status_field"
    values = " ".join(field.get("value", "") for field in status.get("status_fields", [])).casefold()
    selected_path = selected.get("path")
    front_points_elsewhere = _front_door_points_elsewhere(front_door, selected_path)
    has_superseding_override = any(ref.get("relationship") in {"supersedes", "current_pointer"} for ref in references)
    has_completed_support = any(ref.get("relationship") == "completed_or_closed" for ref in references)
    if front_points_elsewhere or has_superseding_override:
        return "status_claim_conflicts_with_later_evidence"
    if has_completed_support and any(value in values for value in ("done", "closed", "complete", "completed", "merged")):
        return "status_claim_supported_as_historical_by_later_evidence"
    if verdict == "current":
        return "status_claim_supported_by_front_door_or_later_evidence"
    if status.get("claim_source") == "exported_ticket_fields":
        return "exported_ticket_fields_are_claims_not_truth"
    return "status_claim_not_authoritative"


def _git_evidence(cwd: Path, selected_path: str | None) -> dict:
    if not selected_path:
        return {"available": False, "artifact_commits": [], "newer_repo_commits": []}
    artifact_commits = _git_log(cwd, ["log", "--follow", "--max-count=4", "--format=%H%x09%cI%x09%s", "--", selected_path])
    newer = []
    if artifact_commits:
        newest_date = artifact_commits[0].get("committed_at")
        if newest_date:
            newer = _git_log(cwd, ["log", "--max-count=5", "--format=%H%x09%cI%x09%s", f"--since={newest_date}", "--", "."])
            newer = [item for item in newer if item.get("hash") != artifact_commits[0].get("hash")]
    return {"available": bool(artifact_commits or newer), "artifact_commits": artifact_commits, "newer_repo_commits": newer}


def _git_log(cwd: Path, args: list[str]) -> list[dict]:
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], check=False, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        entries.append({"hash": parts[0][:12], "committed_at": parts[1], "subject": parts[2]})
    return entries


def _latest_jsonl_evidence(query: str, selected: dict, latest: dict) -> list[dict]:
    messages = latest.get("messages") or []
    terms = set(_query_terms(query) + _reference_tokens(query, selected))
    if not terms:
        terms = {"current", "plan", "stale", "superseded"}
    evidence = []
    for message in messages:
        snippet = str(message.get("snippet") or "")
        normalized = _normalize(snippet)
        if not any(term in normalized for term in terms):
            continue
        evidence.append(
            {
                "source_ref": message.get("source_ref"),
                "timestamp_utc": message.get("timestamp_utc"),
                "snippet": _compact(snippet),
                "relationship": "latest_jsonl_message",
            }
        )
    return evidence[:3]


def _verdict_and_why(*, query: str, selected: dict, front_door: dict, status_assessment: dict, references: list[dict], git: dict) -> tuple[str, str]:
    selected_path = selected.get("path")
    if _front_door_points_to_selected(front_door, selected_path):
        return "current", "Current front-door state points at this artifact/topic as the active plan or task."
    superseding = [ref for ref in references if ref.get("relationship") in {"supersedes", "current_pointer"}]
    completed = [ref for ref in references if ref.get("relationship") == "completed_or_closed"]
    if superseding:
        return "superseded", "Later local artifact/front-door evidence points to a different current direction or explicitly supersedes the selected artifact/topic."
    if selected_path and _front_door_points_elsewhere(front_door, selected_path):
        return "superseded", "The artifact may have a status claim, but the current front-door source of truth points elsewhere."
    if completed:
        return "historical_provenance", "Later task/review/report evidence treats the selected artifact/topic as completed or historical provenance."
    if not selected_path and front_door.get("source_refs"):
        return "current", "The natural currentness query resolves to the current front-door state instead of older candidate artifacts."
    if status_assessment.get("claim_present"):
        return "unknown", "The artifact has a status field, but no later source was found to verify whether that claim is still true."
    return "unknown", "No decisive current front-door, supersession, completion, or contradiction evidence was found in the local artifact scope."


def _evidence_chain(selected: dict, status: dict, git: dict, references: list[dict], front_door: dict, latest_evidence: list[dict]) -> list[dict]:
    chain = []
    if selected.get("source_ref"):
        chain.append(
            {
                "evidence_type": "selected_artifact_or_topic",
                "source_ref": selected["source_ref"],
                "relationship": selected.get("selection_reason", "selected target"),
                "snippet": selected.get("title") or selected.get("topic") or selected.get("path"),
            }
        )
    for field in status.get("status_fields", [])[:3]:
        chain.append(
            {
                "evidence_type": "artifact_status_field",
                "source_ref": field["source_ref"],
                "relationship": "status_is_claim_not_truth",
                "snippet": field["raw"],
            }
        )
    for commit in git.get("artifact_commits", [])[:2]:
        chain.append(
            {
                "evidence_type": "git_artifact_history",
                "source_ref": commit["hash"],
                "relationship": "artifact_commit",
                "snippet": f"{commit['committed_at']} {commit['subject']}",
            }
        )
    for commit in git.get("newer_repo_commits", [])[:3]:
        chain.append(
            {
                "evidence_type": "git_later_repo_history",
                "source_ref": commit["hash"],
                "relationship": "newer_repo_commit_after_artifact",
                "snippet": f"{commit['committed_at']} {commit['subject']}",
            }
        )
    for ref in references[:6]:
        chain.append(
            {
                "evidence_type": "later_artifact_reference",
                "source_ref": ref["source_ref"],
                "relationship": ref["relationship"],
                "snippet": ref["snippet"],
            }
        )
    for ref in front_door.get("source_refs", [])[:4]:
        chain.append(
            {
                "evidence_type": "current_front_door",
                "source_ref": ref["source_ref"],
                "relationship": ref["relationship"],
                "snippet": ref["snippet"],
                "currentness_role": ref.get("currentness_role"),
                "role_label": ref.get("role_label"),
            }
        )
    for item in latest_evidence:
        chain.append(
            {
                "evidence_type": "latest_jsonl_message",
                "source_ref": item.get("source_ref"),
                "relationship": item["relationship"],
                "snippet": item["snippet"],
            }
        )
    return _dedupe_chain(chain)


def _next_reads(selected: dict, front_door: dict, chain: list[dict]) -> list[dict]:
    reads = []
    for ref in front_door.get("active_current", [])[:3]:
        reads.append(
            {
                "source_ref": ref["source_ref"],
                "reason": f"Inspect {ref.get('role_label', 'active current front-door evidence')} before broad plan pointers.",
                "currentness_role": ref.get("currentness_role"),
                "role_label": ref.get("role_label"),
            }
        )
    if selected.get("source_ref"):
        reads.append(
            {
                "source_ref": selected["source_ref"],
                "reason": "Inspect selected artifact/topic before trusting it.",
                "currentness_role": selected.get("currentness_role"),
                "role_label": selected.get("role_label"),
            }
        )
    for ref in front_door.get("supporting_context", [])[:3]:
        reads.append(
            {
                "source_ref": ref["source_ref"],
                "reason": f"Inspect {ref.get('role_label', 'supporting context')}; do not treat it as controlling if active current refs differ.",
                "currentness_role": ref.get("currentness_role"),
                "role_label": ref.get("role_label"),
            }
        )
    for ref in front_door.get("source_refs", [])[:3]:
        reads.append(
            {
                "source_ref": ref["source_ref"],
                "reason": "Inspect current front-door source of truth.",
                "currentness_role": ref.get("currentness_role"),
                "role_label": ref.get("role_label"),
            }
        )
    for item in chain:
        if item["evidence_type"] == "artifact_status_field":
            reads.append({"source_ref": item["source_ref"], "reason": "Inspect this status/ticket field as a claim, not truth."})
        if item["evidence_type"] in {"later_artifact_reference", "git_later_repo_history", "latest_jsonl_message"}:
            reads.append({"source_ref": item["source_ref"], "reason": f"Inspect {item['relationship']} evidence."})
    return _dedupe_reads(reads)[:10]


def _blind_spots(latest: dict, git: dict, explicit_path: Path | None, selected: dict, references: list[dict]) -> list[str]:
    blind = []
    if not git.get("available"):
        blind.append("Git artifact history was unavailable or had no commits for the selected artifact.")
    if not (latest.get("messages") or []):
        blind.append("No compact latest JSONL evidence was found in the bounded local-history scope.")
    if selected.get("kind") == "topic" and not selected.get("path"):
        blind.append("Natural topic did not resolve to one concrete local artifact.")
    if explicit_path and not references:
        blind.append("No later local task/review/report/front-door references were found for the explicit artifact.")
    blind.append("External ticket systems were not queried; only local/exported artifacts were inspected.")
    return blind


def _worktree_candidates(worktree_report: dict) -> list[dict]:
    candidates = []
    for index, item in enumerate(worktree_report.get("classifications") or [], 1):
        candidates.append(
            {
                "kind": "worktree",
                "path": item.get("path") or item.get("identifier"),
                "source_ref": item.get("identifier"),
                "title": item.get("branch") or item.get("identifier"),
                "status_fields": [],
                "reason": item.get("classification"),
                "score": 1000 - index,
                "rank": index,
            }
        )
    return candidates


def _worktree_evidence_chain(worktree_report: dict) -> list[dict]:
    chain = []
    cleanup = worktree_report.get("cleanup_causality") or {}
    for ref in cleanup.get("operational_records") or []:
        chain.append(
            {
                "evidence_type": "worktree_operational_record",
                "source_role": ref.get("source_role") or "operational_record",
                "source_ref": ref.get("source_ref"),
                "relationship": ref.get("relationship"),
                "snippet": ref.get("snippet"),
            }
        )
    for item in worktree_report.get("classifications") or []:
        chain.append(
            {
                "evidence_type": "worktree_classification",
                "source_role": "operational_record",
                "source_ref": item.get("identifier"),
                "relationship": item.get("classification"),
                "snippet": item.get("why"),
            }
        )
        status = item.get("status") or {}
        if status.get("available"):
            chain.append(
                {
                    "evidence_type": "git_worktree_status",
                    "source_role": "git_state",
                    "source_ref": item.get("path") or item.get("identifier"),
                    "relationship": "dirty" if status.get("dirty") else "clean",
                    "snippet": status.get("branch_line") or "git status --short --branch inspected",
                }
            )
        private = item.get("private_artifacts") or {}
        if private.get("has_private_artifacts"):
            chain.append(
                {
                    "evidence_type": "worktree_private_artifacts",
                    "source_role": "private_retention_evidence",
                    "source_ref": f"{item.get('path') or item.get('identifier')}/.private",
                    "relationship": "preserve_before_prune",
                    "snippet": f"{private.get('count', 0)} private artifact paths found",
                }
            )
        for ref in item.get("source_refs", [])[:5]:
            chain.append(
                {
                    "evidence_type": "worktree_source_ref",
                    "source_role": ref.get("source_role") or "operational_record",
                    "source_ref": ref.get("source_ref"),
                    "relationship": ref.get("relationship"),
                    "snippet": ref.get("snippet"),
                }
            )
    return _dedupe_chain(chain)


def _worktree_next_reads(worktree_report: dict, chain: list[dict]) -> list[dict]:
    reads = []
    cleanup = worktree_report.get("cleanup_causality") or {}
    target_binding = cleanup.get("target_binding") or {}
    if target_binding.get("status") == "blocked_named_target_missing":
        for command in worktree_report.get("next_commands") or []:
            reads.append(
                {
                    "source_ref": command.get("command"),
                    "source_role": "git_state",
                    "phase": "current_state_after_chronology_declared_missing",
                    "reason": (
                        f"Run target-bound {command.get('kind')} proof, then stop if the exact "
                        "named target remains absent."
                    ),
                }
            )
        return _dedupe_reads(reads)
    for ref in cleanup.get("operational_records") or []:
        reads.append(
            {
                "source_ref": ref.get("source_ref"),
                "source_role": ref.get("source_role") or "operational_record",
                "phase": "historical_causality",
                "relationship": ref.get("relationship"),
                "reason": f"Inspect {ref.get('relationship')} operational record before worktree commands.",
            }
        )
    for item in chain:
        if item.get("evidence_type") == "worktree_source_ref":
            reads.append(
                {
                    "source_ref": item.get("source_ref"),
                    "source_role": item.get("source_role") or "operational_record",
                    "phase": "historical_causality",
                    "relationship": item.get("relationship"),
                    "reason": f"Inspect {item.get('relationship')} evidence.",
                }
            )
    for command in worktree_report.get("next_commands") or []:
        reads.append(
            {
                "source_ref": command.get("command"),
                "source_role": "git_state" if command.get("kind", "").startswith(("worktree", "git")) else "operational_record",
                "phase": "current_state_after_chronology",
                "reason": f"Run {command.get('kind')} for worktree currentness inspection after operational records.",
            }
        )
    for item in worktree_report.get("classifications") or []:
        for command in item.get("next_commands", [])[:6]:
            reads.append(
                {
                    "source_ref": command.get("command"),
                    "source_role": "git_state" if command.get("kind", "").startswith(("worktree", "status", "branch", "git")) else "private_retention_evidence",
                    "phase": "current_state_after_chronology",
                    "reason": f"{item.get('identifier')}: {command.get('kind')}",
                }
            )
    return _dedupe_reads(reads)[:18]


def _worktree_blind_spots(worktree_report: dict, latest: dict) -> list[str]:
    blind = [
        "This route does not execute cleanup and keeps branch refs retained unless explicitly scoped otherwise.",
        "External issue trackers were not queried; only local/exported artifacts and git worktree state were inspected.",
    ]
    if not (worktree_report.get("discovered_worktrees") or []):
        blind.append("No physical git worktrees were discovered from git worktree list or ./worktrees/.")
    if not (latest.get("messages") or []):
        blind.append("No compact latest JSONL evidence was found in the bounded local-history scope.")
    return blind


def _reference_tokens(query: str, selected: dict) -> list[str]:
    tokens = []
    path = selected.get("path")
    if path:
        rel = str(path)
        tokens.append(rel.casefold())
        stem = Path(rel).stem.casefold()
        tokens.append(stem)
        match = re.search(r"\b([a-z]+-\d+[a-z]?)\b", stem, re.IGNORECASE)
        if match:
            tokens.append(match.group(1).casefold())
    title = selected.get("title")
    if title:
        tokens.extend(_query_terms(title)[:5])
    tokens.extend(_query_terms(query)[:6])
    return sorted({token for token in tokens if len(token) >= 3})


def _reference_relationship(line: str, *, frontdoor: bool) -> str:
    normalized = _normalize(line)
    if any(
        marker in normalized
        for marker in (
            "supersede",
            "obsolete",
            "stale",
            "replaced",
            "replaces",
            "no longer",
            "abandoned",
            "deprecated",
            "historical/provenance",
        )
    ):
        return "supersedes"
    if any(marker in normalized for marker in ("current", "active", "source of truth", "front door", "next-loop", "next loop")):
        return "current_pointer" if frontdoor else "current_related"
    if any(marker in normalized for marker in ("completed", "complete", "closed", "done", "merged", "reviewed")):
        return "completed_or_closed"
    return "current_pointer" if frontdoor else "mention"


def _front_door_points_to_selected(front_door: dict, selected_path: str | None) -> bool:
    if not selected_path:
        return False
    if (front_door.get("current_plan") or {}).get("path") == selected_path:
        return True
    if any(
        ref.get("path") == selected_path
        and ref.get("currentness_role") in {"active_current", "active_current_state", "current_gate_plan", "current_plan_pointer"}
        for ref in front_door.get("source_refs", [])
    ):
        return True
    for ref in front_door.get("source_refs", []):
        snippet = _normalize(ref.get("snippet", ""))
        if selected_path in ref.get("snippet", "") and not any(
            marker in snippet for marker in ("supersede", "obsolete", "stale", "replaced", "no longer")
        ):
            return True
    return False


def _front_door_points_elsewhere(front_door: dict, selected_path: str | None) -> bool:
    if not selected_path or front_door.get("status") != "found":
        return False
    current_plan = front_door.get("current_plan") or {}
    if current_plan.get("path") and current_plan.get("path") != selected_path:
        return True
    return any(selected_path not in ref.get("snippet", "") for ref in front_door.get("source_refs", []))


def _status_fields(path: Path, cwd: Path) -> list[dict]:
    fields = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fields
    lines = text.splitlines()
    rel = _rel(path, cwd)
    for index, line in enumerate(lines[:80], 1):
        match = FIELD_RE.match(line)
        if not match:
            continue
        raw_name = match.group(1).strip()
        name = _claim_field_name(raw_name)
        if not _is_claim_field_name(name):
            continue
        fields.append(
            {
                "name": name,
                "value": _clean_claim_value(match.group(2)),
                "raw": _compact(line),
                "source_ref": f"{rel}:{index}",
            }
        )
    fields.extend(_json_status_fields(path, cwd, text, fields))
    return _dedupe_status_fields(fields)


def _json_status_fields(path: Path, cwd: Path, text: str, existing: list[dict]) -> list[dict]:
    if path.suffix.lower() != ".json":
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    existing_keys = {(field.get("name"), field.get("source_ref")) for field in existing}
    rel = _rel(path, cwd)
    fields = []
    for raw_name, value in _walk_json_claims(data):
        name = _claim_field_name(raw_name)
        source_ref = _json_key_source_ref(text, rel, raw_name)
        if (name, source_ref) in existing_keys:
            continue
        fields.append(
            {
                "name": name,
                "value": _clean_claim_value(value),
                "raw": f"{raw_name}: {_clean_claim_value(value)}",
                "source_ref": source_ref,
            }
        )
    return fields


def _walk_json_claims(data: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            name = _claim_field_name(str(key))
            if _is_claim_field_name(name):
                found.append((str(key), value))
            if isinstance(value, (dict, list)):
                found.extend(_walk_json_claims(value))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)):
                found.extend(_walk_json_claims(item))
    return found[:20]


def _json_key_source_ref(text: str, rel: str, raw_name: str) -> str:
    key_re = re.compile(rf'^\s*"{re.escape(raw_name)}"\s*:', re.IGNORECASE)
    for index, line in enumerate(text.splitlines(), 1):
        if key_re.search(line):
            return f"{rel}:{index}"
    return f"{rel}:1"


def _claim_field_name(raw_name: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(raw_name))
    spaced = spaced.replace("_", " ").replace("-", " ")
    return " ".join(spaced.casefold().split())


def _is_claim_field_name(name: str) -> bool:
    return name in CLAIM_FIELD_NAMES


def _clean_claim_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return _compact(json.dumps(value, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError):
            return _compact(str(value))
    return _compact(str(value).strip().rstrip(","))


def _dedupe_status_fields(fields: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for field in fields:
        key = (field.get("name"), field.get("source_ref"), field.get("value"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(field)
    return deduped[:20]


def _first_heading(path: Path, cwd: Path) -> tuple[str | None, str | None]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, None
    rel = _rel(path, cwd)
    for index, line in enumerate(lines[:40], 1):
        match = HEADING_RE.match(line)
        if match:
            return f"{rel}:{index}", _compact(match.group(1))
    return None, None


def _line_matches(path: Path, terms: tuple[str, ...]) -> list[tuple[int, str]]:
    lines = _read_lines(path)
    wanted = tuple(_normalize(term) for term in terms if term)
    matches = []
    for index, line in enumerate(lines, 1):
        normalized = _normalize(line)
        if any(term in normalized for term in wanted):
            matches.append((index, line))
    return matches


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _next_nonempty_line(lines: list[str], current_line_no: int) -> str:
    for line in lines[current_line_no : current_line_no + 4]:
        if line.strip():
            return line
    return ""


def _extract_existing_path(line: str, cwd: Path) -> Path | None:
    paths = _extract_existing_paths(line, cwd)
    return paths[0] if paths else None


def _extract_existing_paths(line: str, cwd: Path) -> list[Path]:
    paths = []
    seen = set()
    for match in PATHISH_RE.finditer(line):
        candidate = (cwd / match.group(2)).resolve()
        if candidate.is_file() and _inside(candidate, cwd) and candidate not in seen:
            paths.append(candidate)
            seen.add(candidate)
    return paths


def _query_terms(query: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", query.casefold())
    return [word for word in words if word not in STOP_WORDS]


def _artifact_kind(path: Path, cwd: Path) -> str:
    rel = _rel(path, cwd)
    first = rel.split("/", 1)[0]
    name = path.name.casefold()
    if "todo" in name:
        return "todo"
    if "roadmap" in name:
        return "roadmap"
    if "plan" in name:
        return "plan"
    if "status" in name or "state" in name or "current" in name:
        return "status"
    if first == "plans":
        return "plan"
    if first in {"tickets", "issues"}:
        return "ticket" if first != "issues" else "issue"
    if first in {"tasks", "reports", "reviews", "docs", "state"}:
        return first[:-1] if first.endswith("s") else first
    if rel.startswith("codex/state/"):
        return "state"
    if re.search(r"\b(?:gh|github|linear|ticket|issue|[a-z]+-\d+)\b", name):
        return "ticket"
    return "artifact"


def _small_text(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _inside(path: Path, cwd: Path) -> bool:
    try:
        path.resolve().relative_to(cwd.resolve())
        return True
    except ValueError:
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _rel(path: Path, cwd: Path) -> str:
    try:
        return str(path.resolve().relative_to(cwd.resolve()))
    except (OSError, ValueError):
        return str(path)


def _compact(text: str, limit: int = 240) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _normalize(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _dedupe_chain(chain: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for item in chain:
        key = (item.get("evidence_type"), item.get("source_ref"), item.get("snippet"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _dedupe_reads(reads: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for read in reads:
        ref = read.get("source_ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        deduped.append(read)
    return deduped


def _timezone_from_args(args: Any):
    from .timeutil import timezone_from_args

    return timezone_from_args(args.timezone, args.timezone_offset)
