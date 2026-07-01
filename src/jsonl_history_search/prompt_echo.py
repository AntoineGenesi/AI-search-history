from __future__ import annotations

import re


WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

CURRENT_SESSION_INTENT_MARKERS = (
    "current session",
    "this session",
    "this run",
    "just now",
    "right now",
    "today",
    "latest session",
    "current run",
)

STRONG_SETUP_ECHO_MARKERS = (
    "<subagent_notification>",
    "agent_path",
    "status\":{",
    "trace path:",
    "final answer summary:",
    "lane verdict:",
    "command/tool count:",
    "fresh receiving-agent",
    "fresh receiving agent",
    "receiving-agent",
    "receiving agent",
    "lane prompt",
    "worker lane",
    "jsonl lane",
    "manual/default",
    "manual comparator",
    "benchmark probe",
    "benchmark setup",
    "current benchmark",
    "run jsonl-history-search",
    "use live installed",
    "live installed jsonl",
    "installed skill/cli",
)

ORCHESTRATION_ECHO_MARKERS = (
    "required startup",
    "implementation constraints",
    "acceptance criteria",
    "reviewer focus",
    "proof uses live installed",
    "raw/internal traces",
    "rerun the losing lanes",
    "you are the isolated implementer",
    "task orchestration",
    "branch `task/",
    "worktree `",
)


def clearly_intends_current_session(query: str) -> bool:
    normalized = normalize_text(query)
    return any(marker in normalized for marker in CURRENT_SESSION_INTENT_MARKERS)


def setup_echo_classification(text: str, *, query: str = "") -> dict:
    normalized = normalize_text(text)
    normalized_query = normalize_text(query)
    strong = [marker for marker in STRONG_SETUP_ECHO_MARKERS if marker in normalized]
    orchestration = [marker for marker in ORCHESTRATION_ECHO_MARKERS if marker in normalized]
    query_embedded = bool(normalized_query and normalized_query in normalized)
    is_echo = bool(strong or (query_embedded and orchestration))
    reason = "none"
    if strong:
        reason = "fresh_prompt_or_benchmark_setup_marker"
    elif query_embedded and orchestration:
        reason = "query_embedded_in_orchestration_prompt"
    return {
        "is_setup_echo": is_echo,
        "reason": reason,
        "markers": (strong + orchestration)[:8],
        "query_embedded": query_embedded,
        "current_session_intended": clearly_intends_current_session(query),
    }


def normalize_text(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("_", " ").split())


def content_terms(text: str) -> set[str]:
    stop = {
        "about",
        "after",
        "agent",
        "answer",
        "before",
        "current",
        "did",
        "from",
        "have",
        "into",
        "just",
        "keep",
        "latest",
        "mention",
        "mentioned",
        "session",
        "that",
        "this",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "work",
        "working",
        "would",
        "wrap",
    }
    return {term for term in WORD_RE.findall(normalize_text(text)) if len(term) >= 4 and term not in stop}
