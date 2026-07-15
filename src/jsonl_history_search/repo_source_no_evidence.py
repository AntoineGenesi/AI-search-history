from __future__ import annotations

import re
import subprocess
from pathlib import Path


CONTAMINATION_POLICY = "current_prompt_tool_output_self_echo_excluded"

EXCLUDED_ROOTS = (
    ".git",
    ".private",
    "worktrees",
    "reports",
    ".private/jsonl",
    "current runtime transcript roots",
    "current prompt/tool output self-echo sources",
)

SOURCE_ROOTS = (
    "src/",
    "tests/",
    "skills/",
    "install/",
    "scripts/",
)

SOURCE_SUFFIXES = {
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def build_repo_source_no_evidence_payload(
    *,
    query: str,
    cwd: Path,
    report_output: str,
) -> dict | None:
    if not repo_source_no_evidence_fast_path_wanted(query):
        return None
    needle = extract_source_scope_needle(query)
    if not needle:
        return None
    repo_root = _git_root(cwd)
    if repo_root is None:
        return None
    tracked = _tracked_source_files(repo_root)
    if not tracked:
        return None

    searched_files = 0
    searched_bytes = 0
    matches: list[dict] = []
    for rel in tracked:
        path = repo_root / rel
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        searched_files += 1
        searched_bytes += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            column = line.find(needle)
            if column < 0:
                continue
            matches.append(
                {
                    "path": rel,
                    "line": line_number,
                    "column": column + 1,
                    "match": needle,
                    "snippet": _snippet(line, needle),
                }
            )
            if len(matches) >= 5:
                break
        if len(matches) >= 5:
            break

    status = "found" if matches else "no_evidence"
    payload = {
        "schema_version": "repo_source_no_evidence.v1",
        "status": status,
        "query": query,
        "report_written": str(Path(report_output).expanduser()),
        "scope_mode": "repo_source_committed",
        "searched_scope": {
            "scope_mode": "repo_source_committed",
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "result_meaning": (
                "searched committed repo/source files only; absence is scoped to that source set"
            ),
            "roots": [
                {
                    "kind": "repo_source_committed",
                    "privacy_label": "committed_source",
                    "path": _safe_relative(cwd, repo_root),
                }
            ],
            "included_roots": list(SOURCE_ROOTS),
            "included_suffixes": sorted(SOURCE_SUFFIXES),
        },
        "excluded_scope": {
            "excluded_roots": list(EXCLUDED_ROOTS),
            "reason": (
                "runtime transcripts, report outputs, private artifacts, worktrees, and current "
                "prompt/tool-output echoes are outside committed source scope"
            ),
        },
        "scope_counts": {
            "selected_root_count": 1,
            "excluded_root_count": len(EXCLUDED_ROOTS),
            "missing_root_count": 0,
        },
        "cap_hit": {"hit": False, "caps_hit": [], "normalized_warnings": []},
        "contamination_policy": CONTAMINATION_POLICY,
        "command_burden": {
            "structured_execution_count": 1,
            "command_count": 1,
            "read_more_count": 0,
            "wrong_command_count": 0,
            "fallback_count": 0,
            "jsonl_retrieval_count": 0,
            "semantic_debug_fallback_count": 0,
        },
        "execution_path": {
            "kind": "repo_source_committed_absence_fast_path",
            "jsonl_retrieval_used": False,
            "semantic_debug_fallback_used": False,
            "read_more_used": False,
        },
        "needle": needle,
        "message": (
            "Committed repo/source scope searched without JSONL retrieval, semantic-debug fallback, "
            "read-more, report projection, or current prompt/tool-output self-echo."
        ),
    }
    if matches:
        payload["found_evidence"] = {
            "status": "found_in_repo_source_committed",
            "match_count": len(matches),
            "matches": matches,
            "scope_boundary": "committed repo/source files only",
        }
        payload["no_evidence"] = None
    else:
        read_more_policy = {
            "required_before_scoped_no_evidence_answer": False,
            "actionability": "optional_diagnostic_context",
            "reason": (
                "The declared committed source scope was searched directly; JSONL/transcript "
                "read-more is not required for this scoped source absence answer."
            ),
        }
        payload["found_evidence"] = None
        payload["no_evidence"] = {
            "needed": True,
            "answer_scope": "not_found_in_searched_scope",
            "not_all_history_absence": True,
            "candidate_interpretation": "repo_source_committed_absence",
            "contamination_policy": CONTAMINATION_POLICY,
            "read_more_guidance": dict(read_more_policy),
            "read_more_policy": dict(read_more_policy),
        }
    return payload


def repo_source_no_evidence_fast_path_wanted(query: str) -> bool:
    normalized = " ".join(str(query or "").casefold().split())
    if not normalized:
        return False
    source_scope = any(
        phrase in normalized
        for phrase in (
            "searched source scope",
            "source scope",
            "source-scope",
            "repo source",
            "repository source",
            "committed source",
            "tracked source",
            "declared source scope",
        )
    )
    if not source_scope:
        return False
    explicit_history = any(
        phrase in normalized
        for phrase in (
            "jsonl",
            "history root",
            "transcript",
            "conversation log",
            "codex session",
            "claude session",
            "codex home",
            "claude home",
        )
    )
    return not explicit_history


def extract_source_scope_needle(query: str) -> str:
    text = str(query or "").strip()
    patterns = (
        r"\bevidence\s+that\s+(.+?)\s+exists?\b",
        r"\bevidence\s+for\s+(.+?)\s+in\b",
        r"\bcontain(?:ed|s)?\s+(.+?)(?:\?|$|\s+in\b)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _clean_needle(match.group(1))
    token_match = re.search(r"\b[A-Za-z0-9][A-Za-z0-9_.:-]{8,}\b", text)
    return _clean_needle(token_match.group(0)) if token_match else ""


def _clean_needle(value: str) -> str:
    cleaned = value.strip().strip("`'\"()[]{}<>.,;:?! ")
    if len(cleaned) > 180:
        return ""
    return cleaned


def _git_root(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def _tracked_source_files(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--cached"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    files = []
    for rel in result.stdout.splitlines():
        normalized = rel.strip().replace("\\", "/")
        if not normalized or _excluded(normalized):
            continue
        if not normalized.startswith(SOURCE_ROOTS):
            continue
        if Path(normalized).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        files.append(normalized)
    return sorted(files)


def _excluded(rel: str) -> bool:
    return rel.startswith((".git/", ".private/", "worktrees/", "reports/"))


def _snippet(line: str, needle: str) -> str:
    index = line.find(needle)
    if index < 0:
        return line[:180]
    start = max(0, index - 70)
    end = min(len(line), index + len(needle) + 70)
    prefix = "..." if start else ""
    suffix = "..." if end < len(line) else ""
    return prefix + line[start:end].strip() + suffix


def _safe_relative(cwd: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(cwd.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))
