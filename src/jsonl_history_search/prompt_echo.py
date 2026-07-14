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
    "manual_default lane",
    "manual default lane",
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
    "<skill>",
    "</skill>",
    "read the attached reviewer skill",
    "independent reviewer for",
    "review against task acceptance criteria",
    "write review artifact",
    "review artifact format",
    "allowed verdicts",
    "if you find a fixable issue",
    "same-task fix findings",
    "orchestrator reviewer",
    "orchestrator implementer",
    "product fsm orchestration",
    "current state + event + guard result -> next state",
    "review failed fixable",
    "fix review findings",
)

ORCHESTRATION_ECHO_MARKERS = (
    "required startup",
    "required proof before handoff",
    "before final response",
    "implementation constraints",
    "acceptance criteria",
    "reviewer focus",
    "proof uses live installed",
    "raw/internal traces",
    "rerun the losing lanes",
    "you are the isolated implementer",
    "you are the cwd-verified isolated implementer",
    "task orchestration",
    "branch `task/",
    "task branch:",
    "current branch checkpoint:",
    "task file:",
    "scratch file:",
    "worktree `",
    "local path:",
    "session id",
)

GATE_ASSIGNMENT_ROLE_MARKERS = (
    "you are a read-only",
    "you are the read-only",
    "you are read-only slice",
    "you are the read-only slice",
    "you are read-only agent",
    "read-only pressure agent",
    "you are worker",
    "you are implementer",
    "you are reviewer",
    "you are coordinator",
    "you are the isolated implementer",
    "use the reviewer skill",
    "you are the implementation/gate worker",
    "you are the implementer worker",
    "you are the implementation worker",
    "you are the gate worker",
    "you are the cwd-verified isolated implementer worker",
    "you are the evaluated fresh receiving agent",
)

GATE_ASSIGNMENT_SETUP_MARKERS = (
    "work only in this isolated worktree",
    "work only in this worktree",
    "you are not alone in the codebase",
    "parent checkout must remain untouched",
    "do not work in the parent checkout",
    "do not edit the parent checkout",
    "do not edit product code",
    "do not modify product code",
    "review only",
    "tracked files",
    "you may create files only under",
    "implement/run one task only",
    "implement one task only",
    "implement exactly one task",
    "task: execute",
    "do not review your own work",
    "do not mark the task",
    "read tasks/",
    "read reports/",
    "read codex/state/reviews/",
    "required reading:",
    "process/fsm:",
    "fsm/process:",
    "acceptance/proof:",
    "proof to run:",
    "artifacts to commit",
    "run live cli prompts",
    "inspect opened refs/source evidence",
    "judge final correctness",
    "save artifacts",
    "write .private/",
    "blocker ranking",
    "no implementation",
    "required lanes:",
    "run the bounded post",
    "run the bounded gate",
    "bounded post-jsh",
    "bounded clean workflow",
    "use raw lane traces for adjudication",
    "select exactly one blocker",
    "do not claim broad release readiness",
    "do not run the full bounded gate",
    "leave ready for independent review",
    "run branch-code smokes",
    "write scratch notes",
    "update the task progress log",
    "final response must include commit sha",
    "write an independent review artifact",
    "scope to check",
)

GATE_ASSIGNMENT_INTENT_MARKERS = (
    "semantic-wrapup",
    "semantic wrap-up",
    "what did i complain",
    "complain about that needs fixing",
    "needs fixing from this session",
    "failure modes should we fix",
    "remembered wording prompts",
    "semanticwrapup/current correction",
    "semantic-wrapup/current correction",
    "ambient root-handoff",
    "fix suggestions",
    "blocker ranking",
    "latest gate/eval/lane prompt",
    "gate/eval prompt",
    "lane prompt",
)

EXPLICIT_SCAFFOLD_QUERY_MARKERS = (
    "assignment prompt",
    "assigned prompt",
    "worker assignment",
    "implementer assignment",
    "reviewer assignment",
    "coordinator assignment",
    "task prompt",
    "gate prompt",
    "current gate prompt",
    "scaffold",
    "scaffolding",
    "setup prompt",
    "system prompt",
    "developer prompt",
    "prompt itself",
    "tasking prompt",
)


def clearly_intends_current_session(query: str) -> bool:
    normalized = normalize_text(query)
    return any(marker in normalized for marker in CURRENT_SESSION_INTENT_MARKERS)


def setup_echo_classification(text: str, *, query: str = "") -> dict:
    normalized = normalize_text(text)
    normalized_query = normalize_text(query)
    explicitly_requested = explicitly_requests_prompt_scaffold(query)
    strong = [marker for marker in STRONG_SETUP_ECHO_MARKERS if marker in normalized]
    orchestration = [marker for marker in ORCHESTRATION_ECHO_MARKERS if marker in normalized]
    gate_roles = [marker for marker in GATE_ASSIGNMENT_ROLE_MARKERS if marker in normalized]
    gate_setup = [marker for marker in GATE_ASSIGNMENT_SETUP_MARKERS if marker in normalized]
    gate_intent = [marker for marker in GATE_ASSIGNMENT_INTENT_MARKERS if marker in normalized]
    query_embedded = bool(normalized_query and normalized_query in normalized)
    gate_assignment_echo = bool(
        gate_roles
        or (len(gate_setup) >= 2 and gate_intent)
        or (query_embedded and gate_setup)
    )
    is_echo = bool(
        not explicitly_requested
        and (strong or gate_assignment_echo or (query_embedded and orchestration))
    )
    reason = "none"
    if explicitly_requested and (strong or gate_assignment_echo or (query_embedded and orchestration)):
        reason = "explicit_prompt_or_assignment_query"
    elif strong:
        reason = "fresh_prompt_or_benchmark_setup_marker"
    elif gate_assignment_echo:
        reason = "gate_eval_or_lane_assignment_setup_marker"
    elif query_embedded and orchestration:
        reason = "query_embedded_in_orchestration_prompt"
    return {
        "is_setup_echo": is_echo,
        "reason": reason,
        "markers": (strong + gate_roles + gate_setup + gate_intent + orchestration)[:8],
        "query_embedded": query_embedded,
        "current_session_intended": clearly_intends_current_session(query),
        "explicit_prompt_scaffold_query": explicitly_requested,
    }


def explicitly_requests_prompt_scaffold(query: str) -> bool:
    normalized = normalize_text(query)
    return any(marker in normalized for marker in EXPLICIT_SCAFFOLD_QUERY_MARKERS)


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
