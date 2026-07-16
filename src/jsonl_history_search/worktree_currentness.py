from __future__ import annotations

import re
import subprocess
from pathlib import Path


CLASSIFICATIONS = (
    "KEEP_ACTIVE",
    "SAFE_TO_PRUNE",
    "PRESERVE_THEN_PRUNE",
    "PRESERVE_THEN_REMOVE",
    "PRESERVE_THEN_REMOVE_CANDIDATES",
    "DEBUG_ONLY_NO_MERGE",
    "SUPERSEDED",
    "NEEDS_USER_DECISION",
    "UNKNOWN",
)

ID_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+[A-Z0-9]*)\b", re.IGNORECASE)
PATH_RE = re.compile(r"(?<![\w./-])((?:worktrees|task|tasks|tickets|issues|branches)/[A-Za-z0-9._/-]+)")
BRANCH_RE = re.compile(r"(?<![\w./-])((?:task|feature|bugfix|fix|branches)/[A-Za-z0-9._/-]+)")

WORKTREE_WORDS = (
    "worktree",
    "worktrees",
    "checkout",
    "checkouts",
    "branch",
    "branches",
    "task branch",
)
CLEANUP_WORDS = (
    "cleanup",
    "clean up",
    "cleaned up",
    "clean themselves up",
    "clean itself up",
    "not cleaned",
    "not clean",
    "removed",
    "remove",
    "prune",
    "pruned",
    "left behind",
    "left over",
    "left around",
    "stale",
    "old",
    "obsolete",
    "safe",
    "current",
    "currentness",
    "superseded",
    "delete",
    "deletion",
)
CLEANUP_CAUSAL_WORDS = (
    "why did",
    "why was",
    "why were",
    "what caused",
    "what made",
    "left behind",
    "left over",
    "not cleaned",
    "not clean",
)

EVIDENCE_ROOTS = (
    "codex/state",
    "tasks",
    "reports",
    "reviews",
    "docs",
    "state",
    "tickets",
    "issues",
)
EVIDENCE_FILES = ("TODO.md", "TASKS.md", "PROJECT.md", "CODEX-ORCHESTRATION.md", "README.md")


def is_worktree_cleanup_currentness_query(query: str) -> bool:
    normalized = _normalize(query)
    return any(word in normalized for word in WORKTREE_WORDS) and any(word in normalized for word in CLEANUP_WORDS)


def is_worktree_cleanup_causality_query(query: str) -> bool:
    return _is_cleanup_causality_query(query)


def build_worktree_currentness_report(query: str, cwd: Path) -> dict:
    named = extract_named_worktree_targets(query)
    worktrees = _discover_worktrees(cwd)
    evidence_files = _discover_evidence_files(cwd)
    cleanup_causality = _cleanup_causality_report(query, cwd, evidence_files, worktrees)
    if cleanup_causality.get("is_cleanup_causality_query") and not named:
        selected = _select_cleanup_causality_targets(worktrees, cwd)
    else:
        selected = _select_targets(named, worktrees)
    items = [
        _classify_target(
            query,
            cwd,
            target,
            evidence_files,
            cleanup_causality.get("operational_records") or [],
        )
        for target in selected
    ]
    cleanup_causality = _finish_cleanup_causality_report(cleanup_causality, items, worktrees, cwd)
    return {
        "status": "ok",
        "intent": "worktree_cleanup_currentness",
        "classifications": items,
        "classification_values": list(CLASSIFICATIONS),
        "cleanup_causality": cleanup_causality,
        "source_role_order": [
            "operational_record",
            "git_state",
            "private_retention_evidence",
            "raw_agent_behavior",
        ],
        "source_roles_present": _source_roles_present(cleanup_causality, items),
        "named_targets": named,
        "discovered_worktrees": [_public_worktree(item, cwd) for item in worktrees],
        "next_commands": _general_next_commands(named),
        "preservation_policy": (
            "If a worktree-local .private directory contains evidence, copy or archive it under "
            "the parent checkout .private/worktree-cleanup-archive/<slug>/ before any physical "
            "worktree prune decision. Keep branch refs retained unless the user explicitly asks otherwise."
        ),
        "boundary": (
            "This route classifies currentness and emits inspection/preservation commands only. "
            "It does not execute cleanup or recommend branch ref pruning."
        ),
    }


def extract_named_worktree_targets(query: str) -> list[dict]:
    targets: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for match in ID_RE.finditer(query):
        value = match.group(1)
        _append_unique(targets, seen, {"kind": "id", "value": value.upper(), "token": value.casefold()})
    for match in PATH_RE.finditer(query):
        value = match.group(1).rstrip(".,;:)")
        _append_unique(targets, seen, {"kind": "path", "value": value, "token": value.casefold()})
    for match in BRANCH_RE.finditer(query):
        value = match.group(1).rstrip(".,;:)")
        _append_unique(targets, seen, {"kind": "branch", "value": value, "token": value.casefold()})
    return targets


def _append_unique(items: list[dict], seen: set[tuple[str, str]], item: dict) -> None:
    key = (item["kind"], item["value"].casefold())
    if key in seen:
        return
    seen.add(key)
    items.append(item)


def _discover_worktrees(cwd: Path) -> list[dict]:
    discovered = _git_worktree_list(cwd)
    by_path = {item.get("path"): item for item in discovered if item.get("path")}
    physical_root = cwd / "worktrees"
    if physical_root.is_dir():
        for child in sorted(physical_root.iterdir()):
            if not child.is_dir():
                continue
            path = str(child.resolve())
            by_path.setdefault(
                path,
                {
                    "path": path,
                    "relative_path": _rel(child, cwd),
                    "branch": "",
                    "head": "",
                    "source": "physical_worktrees_dir",
                },
            )
    return sorted(by_path.values(), key=lambda item: item.get("relative_path") or item.get("path") or "")


def _git_worktree_list(cwd: Path) -> list[dict]:
    result = _run(["git", "-C", str(cwd), "worktree", "list", "--porcelain"], timeout=5)
    if result.returncode != 0:
        return []
    items: list[dict] = []
    current: dict = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                current["source"] = "git_worktree_list"
                path = Path(current.get("path", ""))
                current["relative_path"] = _rel(path, cwd) if str(path) else ""
                items.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value[:12]
        elif key == "branch":
            prefix = "refs/heads/"
            current["branch"] = value[len(prefix) :] if value.startswith(prefix) else value
        elif key == "detached":
            current["detached"] = True
    if current:
        current["source"] = "git_worktree_list"
        path = Path(current.get("path", ""))
        current["relative_path"] = _rel(path, cwd) if str(path) else ""
        items.append(current)
    return items


def _select_targets(named: list[dict], worktrees: list[dict]) -> list[dict]:
    selected: list[dict] = []
    matched_paths: set[str] = set()
    if named:
        for target in named:
            token = target["token"]
            matches = [item for item in worktrees if _target_matches_worktree(token, item)]
            if matches:
                for match in matches:
                    copy = dict(match)
                    copy["matched_query_target"] = target
                    selected.append(copy)
                    matched_paths.add(copy.get("path", ""))
            else:
                selected.append({"target_kind": target["kind"], "identifier": target["value"], "matched_query_target": target})
        return _dedupe_targets(selected)
    return [dict(item) for item in worktrees]


def _select_cleanup_causality_targets(worktrees: list[dict], cwd: Path) -> list[dict]:
    selected = []
    for item in worktrees:
        path = item.get("path")
        if path:
            try:
                if Path(path).resolve() == cwd.resolve():
                    continue
            except OSError:
                pass
        rel = item.get("relative_path") or ""
        if rel == ".":
            continue
        if rel.startswith("worktrees/"):
            selected.append(dict(item))
    return selected


def _target_matches_worktree(token: str, worktree: dict) -> bool:
    haystack = " ".join(
        str(worktree.get(key, ""))
        for key in ("path", "relative_path", "branch", "head")
    ).casefold()
    return token in haystack or token.replace("-", "_") in haystack or token.replace("_", "-") in haystack


def _classify_target(
    query: str,
    cwd: Path,
    target: dict,
    evidence_files: list[Path],
    operational_records: list[dict] | None = None,
) -> dict:
    identifier = _target_identifier(target)
    slug = _target_slug(identifier, target)
    tokens = _target_tokens(query, target, identifier)
    evidence = _scan_evidence(cwd, evidence_files, tokens)
    if _is_cleanup_causality_query(query):
        evidence = _dedupe_evidence(evidence + list(operational_records or []))
    status = _worktree_status(target)
    private = _private_artifact_summary(target)
    classification, why, secondary = _classification_from_evidence(target, evidence, status, private)
    archive_root = f".private/worktree-cleanup-archive/{slug}"
    item = {
        "identifier": identifier,
        "path": target.get("relative_path") or target.get("path"),
        "absolute_path": target.get("path"),
        "branch": target.get("branch") or _branch_from_identifier(identifier),
        "head": target.get("head") or "",
        "classification": classification,
        "secondary_states": secondary,
        "why": why,
        "status": status,
        "private_artifacts": private,
        "source_refs": evidence[:8],
        "next_commands": _target_next_commands(identifier, target, tokens, archive_root),
        "preservation_guidance": _preservation_guidance(target, private, archive_root),
    }
    return item


def _dedupe_evidence(evidence: list[dict]) -> list[dict]:
    seen: set[str] = set()
    deduped = []
    for item in evidence:
        source_ref = item.get("source_ref")
        if not source_ref or source_ref in seen:
            continue
        seen.add(source_ref)
        deduped.append(item)
    return deduped


def _classification_from_evidence(target: dict, evidence: list[dict], status: dict, private: dict) -> tuple[str, str, list[str]]:
    text = _normalize(" ".join(item.get("snippet", "") for item in evidence))
    secondary: list[str] = []
    has_worktree = bool(target.get("path"))
    is_dirty = bool(status.get("dirty"))
    has_private = bool(private.get("has_private_artifacts"))
    if "preserve_then_remove_candidates" in text:
        secondary.append("PRESERVE_THEN_REMOVE_CANDIDATES")
    elif "preserve_then_remove" in text:
        secondary.append("PRESERVE_THEN_REMOVE")
    if any(term in text for term in ("active_task", "active task", "current product task", "keep_active")):
        return "KEEP_ACTIVE", "Current front-door evidence names this target as active.", secondary
    if any(term in text for term in ("debug-only", "debug only", "no-merge", "no merge", "should not be reviewed for product merge")):
        if has_worktree and has_private:
            secondary.append("PRESERVE_THEN_PRUNE")
        return (
            "DEBUG_ONLY_NO_MERGE",
            "Local evidence says this target is debug-only/no-merge rather than active product work; treat only the physical worktree as a prune candidate after branch history is retained and private artifacts are checked.",
            secondary,
        )
    if any(term in text for term in ("superseded", "supersedes", "replaced", "failed attempt", "retry2", "retry 2", "later successful", "should not be merged")):
        secondary.append("SUPERSEDED")
        if has_private or is_dirty:
            return "PRESERVE_THEN_PRUNE", "Evidence points to superseded or failed-attempt work with local artifacts/diff that should be preserved before pruning.", secondary
        return "SUPERSEDED", "Evidence points to superseded or replaced work.", secondary
    if has_private:
        return "PRESERVE_THEN_PRUNE", "The worktree has local .private artifacts; preserve them under the parent archive before any prune decision.", secondary
    if is_dirty:
        return "NEEDS_USER_DECISION", "The worktree is dirty and no supersession/preservation evidence was found.", secondary
    if has_worktree and any(term in text for term in ("merged", "reviewed", "completed", "complete", "closed")):
        return "SAFE_TO_PRUNE", "Local evidence treats this clean physical worktree as completed/reviewed and no private artifacts were found.", secondary
    if has_worktree and not evidence and not is_dirty:
        return "UNKNOWN", "The physical worktree is clean, but no task/review/report evidence was found in the bounded local artifact scan.", secondary
    return "UNKNOWN", "No decisive worktree currentness evidence was found.", secondary


def _discover_evidence_files(cwd: Path) -> list[Path]:
    paths: list[Path] = []
    for name in EVIDENCE_FILES:
        path = cwd / name
        if path.is_file():
            paths.append(path)
    for rel in EVIDENCE_ROOTS:
        root = cwd / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml"}:
                paths.append(path)
    return sorted(set(paths), key=lambda item: _rel(item, cwd))


def _scan_evidence(cwd: Path, evidence_files: list[Path], tokens: list[str]) -> list[dict]:
    if not tokens:
        return []
    matches: list[dict] = []
    wanted = tuple(_normalize(token) for token in tokens if len(token) >= 3)
    for path in evidence_files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = _rel(path, cwd)
        for line_no, line in enumerate(lines, 1):
            normalized = _normalize(line)
            if not any(token in normalized for token in wanted):
                continue
            matches.append(
                {
                    "source_ref": f"{rel}:{line_no}",
                    "source_role": _source_role_for_evidence(rel, line),
                    "relationship": _relationship(line),
                    "snippet": _compact(line),
                }
            )
            if len(matches) >= 24:
                return matches
    return matches


def _relationship(line: str) -> str:
    normalized = _normalize(line)
    if "preserve_then_remove_candidates" in normalized:
        return "report_only_preserve_then_remove_candidates"
    if "preserve_then_remove" in normalized:
        return "report_only_preserve_then_remove"
    if any(term in normalized for term in ("pre-existing", "pre existing", "not audited", "left for later")) and _mentions_worktree_cleanup_subject(normalized):
        return "handoff_deferred_cleanup"
    if any(term in normalized for term in ("physical worktrees removed", "worktrees were removed", "worktrees removed", "kept 0", "branch refs retained")):
        return "cleanup_execution_report"
    if any(term in normalized for term in ("active", "current product task", "active_task")):
        return "active_or_current"
    if any(term in normalized for term in ("debug-only", "debug only", "no-merge", "no merge")):
        return "debug_only_no_merge"
    if any(term in normalized for term in ("superseded", "supersedes", "replaced", "failed attempt", "retry2", "retry 2")):
        return "superseded_or_failed_attempt"
    if any(term in normalized for term in ("merged", "reviewed", "completed", "closed")):
        return "completed_or_reviewed"
    if ".private" in normalized or "preserv" in normalized or "archive" in normalized:
        return "private_preservation"
    return "mention"


def _cleanup_causality_report(query: str, cwd: Path, evidence_files: list[Path], worktrees: list[dict]) -> dict:
    causal = _is_cleanup_causality_query(query)
    operational_records = _scan_cleanup_operational_records(cwd, evidence_files) if causal else []
    return {
        "is_cleanup_causality_query": causal,
        "debt_status": "unknown",
        "causal_summary": "",
        "operational_records": operational_records,
        "record_count": len(operational_records),
        "physical_worktree_count": len(worktrees),
    }


def _finish_cleanup_causality_report(report: dict, items: list[dict], worktrees: list[dict], cwd: Path) -> dict:
    if not report.get("is_cleanup_causality_query"):
        return report
    records = report.get("operational_records") or []
    relationships = {item.get("relationship") for item in records}
    has_execution = "cleanup_execution_report" in relationships
    has_report_only = bool(
        {
            "report_only_preserve_then_remove",
            "report_only_preserve_then_remove_candidates",
            "report_only_preserve_then_prune",
        }
        & relationships
    )
    has_deferred = "handoff_deferred_cleanup" in relationships
    if has_execution:
        status = "closed_by_cleanup_execution_report"
        summary = (
            "Cleanup debt was closed by a cleanup execution report; earlier report-only "
            "preserve-then-remove classifications and later not-audited handoffs explain why it had been left open."
        )
    elif has_report_only and has_deferred:
        status = "open_cleanup_debt"
        summary = (
            "Cleanup debt is open in the inspected records: an audit classified the old worktrees as "
            "preserve-then-remove/report-only, then a later handoff deferred them as pre-existing or not audited."
        )
    elif has_report_only:
        status = "open_cleanup_debt"
        summary = (
            "Cleanup debt appears open: operational records classify worktrees as preserve-then-remove/report-only, "
            "but no cleanup execution report was found."
        )
    else:
        status = "unknown"
        summary = "No decisive cleanup audit, handoff deferral, or cleanup execution record was found in the bounded artifact scan."
    report.update(
        {
            "debt_status": status,
            "causal_summary": summary,
            "target_selection_note": (
                "For an unnamed cleanup-causality question, operational audit/handoff/cleanup records are the main answer. "
                "The current checkout is not selected as the causal target."
            ),
            "git_state": {
                "source_role": "git_state",
                "discovered_worktrees": [_public_worktree(item, cwd) for item in worktrees],
                "classified_targets": [item.get("path") or item.get("identifier") for item in items],
            },
        }
    )
    return report


def _is_cleanup_causality_query(query: str) -> bool:
    normalized = _normalize(query)
    return (
        any(word in normalized for word in WORKTREE_WORDS)
        and any(word in normalized for word in CLEANUP_WORDS)
        and any(word in normalized for word in CLEANUP_CAUSAL_WORDS)
    )


def _scan_cleanup_operational_records(cwd: Path, evidence_files: list[Path]) -> list[dict]:
    matches: list[dict] = []
    for path in evidence_files:
        rel = _rel(path, cwd)
        if not _looks_like_operational_cleanup_path(rel):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_no, line in enumerate(lines, 1):
            relationship = _cleanup_operational_relationship(line)
            if not relationship:
                continue
            matches.append(
                {
                    "source_ref": f"{rel}:{line_no}",
                    "source_role": "operational_record",
                    "relationship": relationship,
                    "snippet": _compact(line),
                    "priority": _cleanup_operational_priority(rel, relationship),
                }
            )
    matches.sort(key=lambda item: (item["priority"], item["source_ref"]))
    return _featured_operational_records(_dedupe_operational_records(matches))


def _looks_like_operational_cleanup_path(rel: str) -> bool:
    lowered = rel.casefold()
    return (
        lowered in {"todo.md", "tasks.md", "project.md", "codex-orchestration.md"}
        or lowered.startswith(("reports/", "codex/state/", "docs/", "tasks/"))
    )


def _cleanup_operational_relationship(line: str) -> str:
    normalized = _normalize(line)
    if not any(
        term in normalized
        for term in (
            "worktree",
            "worktrees",
            "preserve_then_remove",
            "preserve_then_prune",
            "pre-existing",
            "not audited",
            "branch refs retained",
        )
    ):
        return ""
    if "preserve_then_remove_candidates" in normalized:
        return "report_only_preserve_then_remove_candidates"
    if "preserve_then_remove" in normalized:
        return "report_only_preserve_then_remove"
    if "preserve_then_prune" in normalized:
        return "report_only_preserve_then_prune"
    if any(term in normalized for term in ("pre-existing", "pre existing", "not audited", "left older", "left for later")) and _mentions_worktree_cleanup_subject(normalized):
        return "handoff_deferred_cleanup"
    if any(
        term in normalized
        for term in (
            "physical worktrees removed",
            "worktrees were removed",
            "worktrees removed",
            "kept 0",
            "branch refs retained",
            "git worktree list now shows only",
        )
    ):
        return "cleanup_execution_report"
    if ".private" in normalized or "private evidence" in normalized or "private-retention" in normalized:
        return "private_retention_record"
    if any(term in normalized for term in ("report-only", "report only", "no worktrees or branch refs were removed")):
        return "report_only_cleanup_audit"
    return ""


def _cleanup_operational_priority(rel: str, relationship: str) -> int:
    relationship_priority = {
        "report_only_preserve_then_remove_candidates": 10,
        "report_only_preserve_then_remove": 11,
        "report_only_preserve_then_prune": 12,
        "report_only_cleanup_audit": 13,
        "handoff_deferred_cleanup": 20,
        "cleanup_execution_report": 30,
        "private_retention_record": 40,
    }.get(relationship, 90)
    path_bonus = 0
    lowered = rel.casefold()
    if lowered.startswith("reports/stale-worktree-cleanup"):
        path_bonus = -4
    elif "worktree-subagent-state-audit" in lowered:
        path_bonus = -3
    elif "handoff" in lowered:
        path_bonus = -2
    elif lowered == "todo.md":
        path_bonus = 8
    return relationship_priority + path_bonus


def _mentions_worktree_cleanup_subject(normalized: str) -> bool:
    return any(term in normalized for term in ("worktree", "worktrees", "registered", "older loops", "older-loop", "wrap-up", "closeout"))


def _dedupe_operational_records(records: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped = []
    for record in records:
        path = record.get("source_ref", "").split(":", 1)[0]
        key = (path, record.get("relationship", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({key: value for key, value in record.items() if key != "priority"})
    return deduped


def _featured_operational_records(records: list[dict]) -> list[dict]:
    featured: list[dict] = []
    for relationship in (
        "report_only_preserve_then_remove_candidates",
        "report_only_preserve_then_remove",
        "report_only_preserve_then_prune",
        "report_only_cleanup_audit",
        "handoff_deferred_cleanup",
        "cleanup_execution_report",
        "private_retention_record",
    ):
        for record in records:
            if record.get("relationship") == relationship and record not in featured:
                featured.append(record)
                break
    for record in records:
        if record not in featured:
            featured.append(record)
        if len(featured) >= 16:
            break
    return featured[:16]


def _source_role_for_evidence(rel: str, line: str) -> str:
    normalized = _normalize(f"{rel} {line}")
    if ".private" in normalized and rel.startswith(".private/"):
        return "private_retention_evidence"
    if rel.endswith(".jsonl") or "/sessions/" in rel:
        return "raw_agent_behavior"
    return "operational_record"


def _source_roles_present(cleanup_causality: dict, items: list[dict]) -> list[str]:
    roles: set[str] = set()
    for record in cleanup_causality.get("operational_records") or []:
        if record.get("source_role"):
            roles.add(record["source_role"])
    if cleanup_causality.get("git_state"):
        roles.add("git_state")
    for item in items:
        for ref in item.get("source_refs") or []:
            if ref.get("source_role"):
                roles.add(ref["source_role"])
        if (item.get("private_artifacts") or {}).get("has_private_artifacts"):
            roles.add("private_retention_evidence")
        if (item.get("status") or {}).get("available"):
            roles.add("git_state")
    return [role for role in ("operational_record", "git_state", "private_retention_evidence", "raw_agent_behavior") if role in roles]


def _worktree_status(target: dict) -> dict:
    path = target.get("path")
    if not path:
        return {"available": False, "dirty": False, "short": [], "branch_line": ""}
    result = _run(["git", "-C", path, "status", "--short", "--branch"], timeout=5)
    if result.returncode != 0:
        return {"available": False, "dirty": False, "short": [], "branch_line": "", "error": _compact(result.stderr)}
    lines = result.stdout.splitlines()
    branch_line = lines[0] if lines and lines[0].startswith("##") else ""
    short = [line for line in lines if not line.startswith("##")]
    return {"available": True, "dirty": bool(short), "short": short[:12], "branch_line": branch_line}


def _private_artifact_summary(target: dict) -> dict:
    path = target.get("path")
    if not path:
        return {"has_private_artifacts": False, "paths": [], "count": 0}
    root = Path(path) / ".private"
    if not root.exists():
        return {"has_private_artifacts": False, "paths": [], "count": 0}
    paths = []
    for child in root.rglob("*"):
        if child.is_file():
            paths.append(str(child.relative_to(Path(path))))
            if len(paths) >= 12:
                break
    return {"has_private_artifacts": bool(paths) or root.is_dir(), "paths": paths, "count": len(paths)}


def _target_next_commands(identifier: str, target: dict, tokens: list[str], archive_root: str) -> list[dict]:
    path = target.get("path") or "<worktree-path>"
    branch = target.get("branch") or _branch_from_identifier(identifier) or "<branch>"
    head = target.get("head") or "HEAD"
    grep_pattern = "|".join(re.escape(token) for token in tokens[:6]) or re.escape(identifier)
    return [
        {"kind": "worktree_list", "command": "git worktree list --porcelain"},
        {"kind": "status", "command": f'git -C "{path}" status --short --branch'},
        {"kind": "branch_contains", "command": f'git branch --contains {head}'},
        {"kind": "git_log_grep", "command": f'git log --all --grep="{identifier}" --oneline --decorate --max-count=20'},
        {
            "kind": "targeted_artifact_rg",
            "command": f'rg -n "{grep_pattern}" codex/state tasks reports reviews TODO.md TASKS.md',
        },
        {"kind": "private_artifact_find", "command": f'find "{path}/.private" -maxdepth 4 -type f -print'},
        {"kind": "preserve_private_artifacts", "command": f'mkdir -p "{archive_root}" && cp -a "{path}/.private/." "{archive_root}/"'},
        {"kind": "preserve_local_diff", "command": f'git -C "{path}" diff > "{archive_root}/worktree.diff"'},
        {"kind": "branch_identity", "command": f'git show --no-patch --decorate --oneline {branch}'},
    ]


def _general_next_commands(named: list[dict]) -> list[dict]:
    target = "|".join(re.escape(item["value"]) for item in named[:8]) or "<ticket-or-worktree-id>"
    return [
        {"kind": "worktree_list", "command": "git worktree list --porcelain"},
        {"kind": "targeted_artifact_rg", "command": f'rg -n "{target}" codex/state tasks reports reviews TODO.md TASKS.md'},
        {"kind": "git_log_grep", "command": f'git log --all --grep="{target}" --oneline --decorate --max-count=40'},
    ]


def _preservation_guidance(target: dict, private: dict, archive_root: str) -> str:
    if private.get("has_private_artifacts"):
        return (
            f"Preserve worktree-local .private artifacts under parent {archive_root}/ before physical prune. "
            "Also save any local diff there if the worktree is dirty."
        )
    if target.get("path"):
        return (
            f"Check for worktree-local .private artifacts first. If found, archive them under parent {archive_root}/ "
            "before physical prune."
        )
    return "Resolve the physical worktree path first, then apply the parent .private archive rule before pruning."


def _target_identifier(target: dict) -> str:
    matched = target.get("matched_query_target") or {}
    if matched.get("value"):
        return matched["value"]
    return target.get("relative_path") or target.get("branch") or target.get("identifier") or target.get("path") or "unknown-worktree"


def _target_slug(identifier: str, target: dict) -> str:
    path = target.get("relative_path") or target.get("path") or identifier
    slug = Path(str(path)).name if path else identifier
    return re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-") or "worktree"


def _target_tokens(query: str, target: dict, identifier: str) -> list[str]:
    tokens = [identifier, identifier.casefold()]
    matched = target.get("matched_query_target") or {}
    if matched.get("token"):
        tokens.append(matched["token"])
    for key in ("relative_path", "path", "branch"):
        value = target.get(key)
        if value:
            tokens.append(str(value))
            tokens.append(Path(str(value)).name)
            id_match = ID_RE.search(str(value))
            if id_match:
                tokens.append(id_match.group(1))
    return sorted({token for token in tokens if token and len(token) >= 3}, key=len, reverse=True)


def _branch_from_identifier(identifier: str) -> str:
    if "/" in identifier:
        return identifier
    return ""


def _public_worktree(item: dict, cwd: Path) -> dict:
    return {
        "path": item.get("relative_path") or item.get("path"),
        "branch": item.get("branch") or "",
        "head": item.get("head") or "",
        "source": item.get("source") or "",
    }


def _dedupe_targets(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for item in items:
        key = (item.get("path") or item.get("identifier") or "", str((item.get("matched_query_target") or {}).get("value", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr=str(exc))


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
