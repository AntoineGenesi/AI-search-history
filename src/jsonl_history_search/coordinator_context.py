from __future__ import annotations

import re
from pathlib import Path


PRIMARY_CONTEXT_PATHS = (
    "TODO.md",
    "codex/state/pipeline-state.md",
    "codex/state/current-context-window.md",
)
STATIC_CONTEXT_PATHS = (
    "PROJECT.md",
    "CODEX-ORCHESTRATION.md",
    "docs/agent-facing-tool-contract.md",
    "docs/product-north-star-semantic-debugging-and-conversation-mining.md",
    "docs/current-agent-search-mechanisms.md",
    "docs/search-mechanism-task-history.md",
    "docs/effectiveness-measurement-and-direction.md",
    "docs/devlog-agentic-workflow-failure-analysis-test-cases.md",
)
DEFAULT_LEARNING_IDS = ("LRN-117", "LRN-080", "LRN-173")
PRIOR_RESULT_PATHS = (
    "reports/jsh-1230-natural-coordinator-context-route-research-plan.md",
    "reports/jsh-1229-post-merge-real-source-window-recovery-pressure.md",
    "reports/jsh-1229-benefit-classification.md",
)
MAX_FILE_CHARS = 260_000
MAX_SNIPPET_CHARS = 220


def build_coordinator_context_payload(*, cwd: str | Path, query: str, report_output: str | Path | None = None) -> dict:
    repo = Path(cwd).expanduser().resolve()
    file_texts = _read_context_files(repo)
    active_task_handles = _active_task_handles(file_texts)
    active_task_paths = tuple(handle for handle in active_task_handles if handle.startswith("tasks/"))
    task_texts = _read_specific_paths(repo, active_task_paths)
    learning_texts = _read_specific_paths(repo, ("learnings/INDEX.md",))
    prior_texts = _read_specific_paths(repo, PRIOR_RESULT_PATHS)

    source_files = _source_file_status(
        PRIMARY_CONTEXT_PATHS
        + active_task_paths
        + ("learnings/INDEX.md",)
        + PRIOR_RESULT_PATHS
        + STATIC_CONTEXT_PATHS,
        repo=repo,
    )
    required_next_reads = _required_next_reads(
        repo=repo,
        active_task_paths=active_task_paths,
    )
    optional_diagnostics = _optional_diagnostics(repo)
    current_state = _current_state(file_texts=file_texts, task_texts=task_texts)
    why_goals = _why_goals_guiding_principles(file_texts=file_texts, task_texts=task_texts)
    learnings = _learning_rows(learning_texts.get("learnings/INDEX.md", ""))
    failure_modes = _failure_mode_watchlist(
        file_texts=file_texts,
        task_texts=task_texts,
        learning_rows=learnings,
        prior_texts=prior_texts,
    )
    report_written = str(Path(report_output).expanduser()) if report_output else None

    return {
        "schema_version": 1,
        "status": "context_package_ready",
        "query": query,
        "report_written": report_written,
        "source_policy": {
            "whitelisted_repo_relative_files_only": True,
            "raw_private_transcript_text_included": False,
            "raw_private_session_ids_included": False,
            "absolute_private_paths_included": False,
            "vectors_or_indexes_included": False,
            "not_all_history_completeness": True,
        },
        "current_state": current_state,
        "active_task_handles": active_task_handles,
        "front_door_handles": [
            "TODO.md",
            "codex/state/pipeline-state.md",
            "codex/state/current-context-window.md",
        ],
        "why_goals_guiding_principles": why_goals,
        "learnings_failure_modes": {
            "learning_rows": learnings,
            "failure_modes_to_watch": failure_modes,
        },
        "required_next_reads": required_next_reads,
        "optional_diagnostics": optional_diagnostics,
        "prior_result_context": _prior_result_context(prior_texts),
        "source_files": source_files,
        "claim_boundaries": [
            "current committed-safe context package, not a final answer",
            "does not prove source-window recovery or all-history completeness",
            "does not claim launch readiness, provider token savings, speed gains, or final answer quality",
            "open required next reads before presenting a substantive handoff or next action",
        ],
        "message": (
            "Coordinator-context package ready; inspect required next reads before answering "
            "or handing work to another agent."
        ),
    }


def _read_context_files(repo: Path) -> dict[str, str]:
    return _read_specific_paths(repo, PRIMARY_CONTEXT_PATHS)


def _read_specific_paths(repo: Path, paths: tuple[str, ...]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for rel in paths:
        if not _allowed_context_path(rel):
            continue
        path = repo / rel
        if not path.is_file():
            continue
        try:
            texts[rel] = path.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_CHARS]
        except OSError:
            continue
    return texts


def _allowed_context_path(rel: str) -> bool:
    if rel.startswith((".private/", "/")) or ".." in Path(rel).parts:
        return False
    return rel.endswith((".md", ".json", ".txt"))


def _source_file_status(paths: tuple[str, ...], *, repo: Path) -> list[dict]:
    rows = []
    seen = set()
    for rel in paths:
        if rel in seen or not _allowed_context_path(rel):
            continue
        seen.add(rel)
        path = repo / rel
        rows.append(
            {
                "path": rel,
                "exists": path.is_file(),
                "role": _source_role(rel),
            }
        )
    return rows


def _source_role(rel: str) -> str:
    if rel in PRIMARY_CONTEXT_PATHS:
        return "required_front_door"
    if rel.startswith("tasks/"):
        return "active_task"
    if rel == "learnings/INDEX.md":
        return "learning_index"
    if rel.startswith("reports/"):
        return "bounded_prior_result_context"
    return "project_context"


def _active_task_handles(file_texts: dict[str, str]) -> list[str]:
    combined = "\n".join(_current_front_door_portion(text) for text in file_texts.values())
    handles: list[str] = []
    active_patterns = (
        r"active_task:\s*`(tasks/[A-Za-z0-9_.\-/]+\.md)`",
        r"Current active\s+task is\s*`(tasks/[A-Za-z0-9_.\-/]+\.md)`",
        r"Active selected task:\s*\n?`(tasks/[A-Za-z0-9_.\-/]+\.md)`",
        r"Active selected task:\s*`(tasks/[A-Za-z0-9_.\-/]+\.md)`",
    )
    for pattern in active_patterns:
        for match in re.finditer(pattern, combined):
            rel = match.group(1)
            if rel not in handles:
                handles.append(rel)
    if not handles:
        for match in re.finditer(r"`?(tasks/[A-Za-z0-9_.\-/]+\.md)`?", combined):
            rel = match.group(1)
            if rel not in handles:
                handles.append(rel)
    if not any(handle.startswith("JSH-") for handle in handles):
        for handle in list(handles):
            match = re.search(r"jsh-(\d{3,4}[a-z]?)", handle, flags=re.IGNORECASE)
            if match:
                task_handle = f"JSH-{match.group(1).upper()}"
                if task_handle not in handles:
                    handles.append(task_handle)
    if not handles:
        for match in re.finditer(r"\bJSH-\d{3,4}[a-z0-9-]*\b", combined, flags=re.IGNORECASE):
            handle = match.group(0).upper()
            if handle not in handles:
                handles.append(handle)
    return handles[:12]


def _current_front_door_portion(text: str) -> str:
    for marker in (
        "Latest closed selected task:",
        "Latest closed task:",
        "\n## Closed",
        "\n## Historical",
        "\nHistorical context",
    ):
        if marker in text:
            return text.split(marker, 1)[0]
    return text


def _required_next_reads(*, repo: Path, active_task_paths: tuple[str, ...]) -> list[dict]:
    reads = []
    for rel, reason in (
        ("TODO.md", "front-door active task map and current queue context"),
        ("codex/state/pipeline-state.md", "active FSM packet, legal transition, and current product bottleneck"),
        ("codex/state/current-context-window.md", "compact current context window for the next agent"),
    ):
        reads.append(_read_row(repo=repo, rel=rel, reason=reason, required=True))
    for rel in active_task_paths[:3]:
        reads.append(_read_row(repo=repo, rel=rel, reason="active task source of truth and progress log", required=True))
    reads.append(
        _read_row(
            repo=repo,
            rel="learnings/INDEX.md",
            reason="reusable lessons and failure modes relevant to status, route, and source-window boundaries",
            required=True,
        )
    )
    return reads


def _optional_diagnostics(repo: Path) -> list[dict]:
    rows = []
    for rel, reason in (
        (
            "reports/jsh-1230-natural-coordinator-context-route-research-plan.md",
            "scope and reviewer-focus context for this route delta",
        ),
        (
            "reports/jsh-1229-post-merge-real-source-window-recovery-pressure.md",
            "prior pressure baseline for unsupported_route and source-window non-claim boundary",
        ),
        (
            "reports/jsh-1229-benefit-classification.md",
            "prior still-blocked classification and mechanism pivot rationale",
        ),
    ):
        rows.append(_read_row(repo=repo, rel=rel, reason=reason, required=False))
    return rows


def _read_row(*, repo: Path, rel: str, reason: str, required: bool) -> dict:
    return {
        "path": rel,
        "exists": (repo / rel).is_file(),
        "read_role": "required_context" if required else "optional_diagnostic",
        "reason": reason,
    }


def _current_state(*, file_texts: dict[str, str], task_texts: dict[str, str]) -> dict:
    terms = (
        "active selected task",
        "current fsm transition",
        "fsm_state:",
        "active_task:",
        "current_substate:",
        "next_allowed_transition:",
        "status:",
        "goal",
    )
    return {
        "status": "read_required_front_doors",
        "summary_lines": _extract_snippets({**file_texts, **task_texts}, terms, limit=10),
    }


def _why_goals_guiding_principles(*, file_texts: dict[str, str], task_texts: dict[str, str]) -> dict:
    why_terms = (
        "goal",
        "why",
        "product_bottleneck:",
        "expected_product_delta:",
        "why_this_counts_as_product_loop:",
        "product outcome",
        "controlling product contract",
        "primary target",
        "non-goals",
    )
    principle_terms = (
        "do not",
        "claim boundaries",
        "forbidden_transitions:",
        "invalid_transition_action:",
        "privacy",
        "source-location first",
        "not a final answer",
    )
    return {
        "why_and_goals": _extract_snippets({**file_texts, **task_texts}, why_terms, limit=10),
        "guiding_principles": _extract_snippets(file_texts, principle_terms, limit=10),
    }


def _learning_rows(text: str) -> list[dict]:
    rows = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not any(learning_id in line for learning_id in DEFAULT_LEARNING_IDS):
            continue
        rows.append(
            {
                "path": "learnings/INDEX.md",
                "line": line_number,
                "learning_id": next((learning_id for learning_id in DEFAULT_LEARNING_IDS if learning_id in line), None),
                "summary": _safe_snippet(line),
            }
        )
    return rows


def _failure_mode_watchlist(
    *,
    file_texts: dict[str, str],
    task_texts: dict[str, str],
    learning_rows: list[dict],
    prior_texts: dict[str, str],
) -> list[dict]:
    terms = (
        "failure mode",
        "failure modes",
        "blocker",
        "unsupported_route",
        "semantic_wrapup",
        "source-window",
        "invalid-artifact",
        "do not",
        "stop_or_pivot_threshold:",
    )
    rows = _extract_snippets({**file_texts, **task_texts, **prior_texts}, terms, limit=12)
    for learning in learning_rows:
        rows.append(
            {
                "path": learning["path"],
                "line": learning["line"],
                "text": learning["summary"],
                "reason": f"relevant reusable learning {learning['learning_id']}",
            }
        )
    return rows[:14]


def _prior_result_context(prior_texts: dict[str, str]) -> list[dict]:
    terms = (
        "unsupported_route",
        "selected_route",
        "recommendation",
        "source-window recovery",
        "still blocked",
        "invalid generated",
        "invalid-artifact",
        "classification",
    )
    rows = _extract_snippets(prior_texts, terms, limit=8)
    for row in rows:
        row["claim_boundary"] = "prior-result context only; not source-window recovery proof"
    return rows


def _extract_snippets(texts: dict[str, str], terms: tuple[str, ...], *, limit: int) -> list[dict]:
    rows = []
    lowered_terms = tuple(term.casefold() for term in terms)
    for rel, text in texts.items():
        if not _allowed_context_path(rel):
            continue
        scan_text = _current_front_door_portion(text) if rel in PRIMARY_CONTEXT_PATHS else text
        for line_number, line in enumerate(scan_text.splitlines(), start=1):
            normalized = line.casefold()
            if not any(term in normalized for term in lowered_terms):
                continue
            rows.append(
                {
                    "path": rel,
                    "line": line_number,
                    "text": _safe_snippet(line),
                }
            )
            if len(rows) >= limit:
                return rows
    return rows


def _safe_snippet(text: str) -> str:
    snippet = " ".join(text.strip().split())
    snippet = re.sub(r"/(?:home|Users|mnt|tmp|var)/[^`'\" )\]]+", "<local-path>", snippet)
    snippet = re.sub(r"\.private/[^`'\" )\]]+", ".private/<redacted>", snippet)
    snippet = re.sub(r"\b[0-9a-f]{32,}\b", "<id>", snippet, flags=re.IGNORECASE)
    snippet = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "<id>",
        snippet,
        flags=re.IGNORECASE,
    )
    if len(snippet) > MAX_SNIPPET_CHARS:
        return snippet[: MAX_SNIPPET_CHARS - 3].rstrip() + "..."
    return snippet
