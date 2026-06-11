from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath


TASK_SLUG_RE = re.compile(r"\b([a-z]{1,8})[-_](\d{2,6}[a-z]?)(?:[-_]([a-z0-9]{1,20}))?\b")
SEPARATED_WORD_RE = re.compile(r"[a-z0-9]+")
MAX_EXACT_PHRASE_CHARS = 240
MAX_EXACT_PHRASE_TOKENS = 16
MAX_EXPANDED_TERMS = 110

SYNONYMS: dict[str, tuple[str, ...]] = {
    "artifact": ("artifacts", "doc", "docs", "file", "output", "report"),
    "artifacts": ("artifact", "doc", "docs", "file", "output", "report"),
    "bug": ("defect", "error", "failure", "fix", "issue", "regression"),
    "closeout": ("recap", "summary", "wrap", "wrapup"),
    "cluster": ("clustering", "group", "grouped", "grouping"),
    "clustering": ("cluster", "group", "grouped", "grouping"),
    "csv": ("spreadsheet", "table"),
    "doc": ("artifact", "docs", "document", "file", "markdown", "note", "report"),
    "docs": ("artifact", "doc", "document", "file", "markdown", "note", "report"),
    "document": ("doc", "docs", "file", "handoff", "note", "report"),
    "edit": ("changed", "edited", "modified", "patched", "refactored", "tuned", "updated"),
    "edited": ("changed", "edit", "modified", "patched", "refactored", "tuned", "updated"),
    "file": ("artifact", "doc", "docs", "output", "path", "report"),
    "handoff": ("continuation", "handover", "next-session", "note", "transfer"),
    "handover": ("continuation", "handoff", "next-session", "note", "transfer"),
    "html": ("report", "webpage"),
    "implement": ("build", "built", "implemented", "implementation"),
    "implemented": ("build", "built", "implement", "implementation"),
    "issue": ("bug", "defect", "error", "failure", "fix", "regression"),
    "json": ("jsonl", "structured"),
    "jsonl": ("json", "transcript"),
    "library": ("module", "package", "system"),
    "markdown": ("doc", "docs", "md", "report"),
    "note": ("doc", "document", "handoff", "handover", "notes"),
    "notes": ("doc", "document", "handoff", "handover", "note"),
    "preference": ("preferences", "prefer", "preferred", "style", "taste"),
    "preferences": ("preference", "prefer", "preferred", "style", "taste"),
    "prefer": ("preference", "preferences", "preferred", "style", "taste"),
    "preferred": ("preference", "preferences", "prefer", "style", "taste"),
    "report": ("artifact", "doc", "docs", "file", "html", "markdown", "reports"),
    "reports": ("artifact", "doc", "docs", "file", "html", "markdown", "report"),
    "retry": ("attempt", "attempts", "retried", "retries", "retrying"),
    "retries": ("attempt", "attempts", "retried", "retry", "retrying"),
    "search": ("find", "lookup", "retrieval", "retrieve"),
    "searched": ("find", "lookup", "retrieval", "retrieve", "search"),
    "summary": ("closeout", "recap", "wrap", "wrapup"),
    "test": ("pytest", "regression", "tests", "verification"),
    "tests": ("pytest", "regression", "test", "verification"),
    "wrap": ("closeout", "recap", "summary", "wrapup"),
    "wrapup": ("closeout", "recap", "summary", "wrap"),
}

SEMANTIC_LOCAL: dict[str, tuple[str, ...]] = {
    "edit": ("change", "patch", "refactor", "tune"),
    "edited": ("change", "patch", "refactor", "tune"),
    "library": ("codebase", "module", "source", "subsystem"),
    "physics": ("collision", "collisions", "engine", "integration", "physics.ts", "rigid-body", "rigidbody"),
    "session": ("closeout", "continuation", "handoff", "summary"),
    "skill": ("capability", "practice", "workflow"),
    "workflow": ("automation", "process", "routine", "sequence"),
}

PREFERENCE_WORDS = {
    "avoid",
    "dislike",
    "disliked",
    "like",
    "liked",
    "prefer",
    "preferred",
    "preference",
    "preferences",
    "style",
    "taste",
}

WORKTREE_CLEANUP_STATUS_TERMS = ("stale", "obsolete", "abandoned")
WORKTREE_CLEANUP_WORKTREE_TERMS = (
    "worktree",
    "worktrees",
    "checkout",
    "checkouts",
    "worker",
    "worker tree",
    "worker-tree",
    "worker_tree",
)
WORKTREE_CLEANUP_ACTION_TERMS = (
    "clean",
    "cleaned",
    "cleanup",
    "remove",
    "removed",
    "prune",
    "pruned",
)

RETRIEVAL_MISS_CONTEXT_TERMS = (
    "retrieval",
    "retrieve",
    "retrieved",
    "search",
    "searched",
    "lookup",
    "lookups",
    "no evidence",
    "no-evidence",
    "no_evidence",
)
RETRIEVAL_MISS_FAILURE_TERMS = (
    "fail",
    "failed",
    "failing",
    "failure",
    "miss",
    "missed",
    "misses",
    "missing",
    "not found",
    "not-found",
    "not_found",
)
RETRIEVAL_MISS_EXPANSION_TERMS = (
    "command",
    "evidence",
    "fail",
    "failed",
    "failure",
    "lookup",
    "miss",
    "missed",
    "missing",
    "no evidence",
    "no-evidence",
    "no_evidence",
    "not found",
    "not-found",
    "not_found",
    "retrieval",
    "search",
    "source",
)

SOURCE_SELECTION_FAILURE_EXPANSION_TERMS = (
    "candidate",
    "dropped",
    "expected",
    "file",
    "opened",
    "opening",
    "relevant",
    "retention",
    "root",
    "row",
    "selected",
)

ACTION_PROOF_AGENT_TERMS = ("agent", "assistant")
ACTION_PROOF_ACTION_TERMS = ("action", "proof")
ACTION_PROOF_EXECUTION_TERMS = (
    "command",
    "commit",
    "committed",
    "execute",
    "executed",
    "execution",
    "implement",
    "implementation",
    "pytest",
    "ran",
    "run",
    "test",
    "tested",
    "tests",
)
ACTION_PROOF_EXPANSION_TERMS = (
    "action",
    "agent",
    "command",
    "committed",
    "executed",
    "implementation",
    "pytest",
    "ran",
    "run",
    "tested",
    "tests",
)

CHANGE_SUMMARY_ADD_OR_CHANGE_ACTION_TERMS = (
    "add",
    "added",
    "change",
    "changed",
    "changes",
    "fixed",
    "implement",
    "implemented",
)
CHANGE_SUMMARY_TOOL_CONTEXT_TERMS = (
    "expansion",
    "helper",
    "jsonl",
    "output",
    "package",
    "query",
    "queries",
    "ranking",
    "retrieval",
    "scanner",
    "search",
    "tool",
    "tools",
    "wording",
)
CHANGE_SUMMARY_EXPANSION_TERMS = (
    "added",
    "fixed",
    "implemented",
    "improved",
    "output",
    "recover",
    "recovered",
    "recovers",
    "tool",
    "wording",
)

STALE_REFERENCE_STATE_STATUS_TERMS = ("stale", "old", "obsolete", "superseded")
STALE_REFERENCE_STATE_REFERENCE_TERMS = (
    "mention",
    "mentioned",
    "reference",
    "referenced",
    "referencing",
)
STALE_REFERENCE_STATE_CONTEXT_TERMS = (
    "active",
    "active-state",
    "handoff",
    "old",
    "obsolete",
    "pointer",
    "queue",
    "referenced",
    "state",
    "superseded",
)

NEXT_WORK_HANDOFF_EXPANSION_TERMS = (
    "choose",
    "cleanup",
    "handoff",
    "merge",
    "post-merge",
    "proceed",
    "queue",
    "retrieval",
    "reviewer",
    "selected",
    "worktree",
)

CLAIM_BOUNDARY_CLAIM_TERMS = ("claim", "claimed", "claims", "overclaim", "overclaimed")
CLAIM_BOUNDARY_QUALITY_TERMS = (
    "answer",
    "evidence",
    "reviewed",
    "speed",
    "token efficiency",
    "token-efficiency",
    "unsupported",
    "value",
)


@dataclass
class ExpansionResult:
    terms: list[str]
    reasons: dict[str, list[str]] = field(default_factory=dict)


def expand_query_terms(
    *,
    normalized: str,
    base_terms: list[str],
    artifact_hints: list[str],
    date_window_start: datetime | None,
    date_window_end: datetime | None,
    date_reason: str | None,
) -> ExpansionResult:
    reasons: dict[str, set[str]] = {}

    def add(term: str, reason: str) -> None:
        cleaned = " ".join(term.lower().split())
        if len(cleaned) <= 1:
            return
        reasons.setdefault(cleaned, set()).add(reason)

    for phrase in exact_phrase_terms(normalized):
        add(phrase, "exact_phrase")

    for term in base_terms:
        add(term, "normalized_terms")

    for slug in task_slug_variants(normalized):
        add(slug, "task_slug")
    for hint in artifact_hints:
        for term in artifact_variants(hint):
            add(term, "artifact_path")
        for slug in task_slug_variants(hint):
            add(slug, "task_slug")

    expansion_seeds = list(base_terms)
    for term in expansion_seeds:
        for synonym in SYNONYMS.get(term, ()):
            add(synonym, "synonym_paraphrase")
        for semantic_term in SEMANTIC_LOCAL.get(term, ()):
            add(semantic_term, "semantic_local")
        if term in PREFERENCE_WORDS:
            for preference_term in sorted(PREFERENCE_WORDS):
                add(preference_term, "preference")

    if has_worktree_cleanup_intent(normalized, base_terms):
        for term in worktree_cleanup_terms():
            add(term, "synonym_paraphrase")

    if has_retrieval_miss_failure_intent(normalized, base_terms):
        for term in retrieval_miss_failure_terms():
            add(term, "synonym_paraphrase")

    if has_source_selection_failure_intent(normalized, base_terms):
        for term in source_selection_failure_terms():
            add(term, "synonym_paraphrase")

    if has_action_proof_intent(normalized, base_terms):
        for term in action_proof_terms():
            add(term, "synonym_paraphrase")

    if has_change_summary_improvement_intent(normalized, base_terms):
        for term in change_summary_improvement_terms():
            add(term, "synonym_paraphrase")

    if has_stale_reference_state_intent(normalized, base_terms):
        for term in stale_reference_state_terms():
            add(term, "synonym_paraphrase")

    if has_next_work_handoff_intent(normalized, base_terms):
        for term in next_work_handoff_terms():
            add(term, "synonym_paraphrase")

    if has_claim_boundary_intent(normalized, base_terms):
        for term in claim_boundary_terms():
            add(term, "synonym_paraphrase")

    for term in date_window_terms(date_window_start, date_window_end, date_reason):
        add(term, "date_window")

    bounded = bounded_reasons(reasons)
    return ExpansionResult(
        terms=sorted(bounded),
        reasons={term: sorted(term_reasons) for term, term_reasons in sorted(bounded.items())},
    )


def has_worktree_cleanup_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    worktree_intent = bool(
        tokens & {"worktree", "worktrees", "checkout", "checkouts"}
        or base & {"worktree", "worktrees", "checkout", "checkouts", "worker-tree", "worker_tree"}
        or re.search(r"\bworker[\s_-]+tree\b", normalized)
    )
    cleanup_intent = bool(tokens & {"clean", "cleaned", "cleanup", "remove", "removed", "prune", "pruned"})
    return worktree_intent and cleanup_intent


def worktree_cleanup_terms() -> list[str]:
    return sorted(
        {
            *WORKTREE_CLEANUP_STATUS_TERMS,
            *WORKTREE_CLEANUP_WORKTREE_TERMS,
            *WORKTREE_CLEANUP_ACTION_TERMS,
        }
    )


def has_retrieval_miss_failure_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    context_terms = set(RETRIEVAL_MISS_CONTEXT_TERMS)
    failure_terms = set(RETRIEVAL_MISS_FAILURE_TERMS)
    context_intent = bool(
        tokens & context_terms
        or base & context_terms
        or re.search(r"\bno[\s_-]+evidence\b", normalized)
    )
    failure_intent = bool(
        tokens & failure_terms
        or base & failure_terms
        or re.search(r"\bnot[\s_-]+found\b", normalized)
    )
    return context_intent and failure_intent


def retrieval_miss_failure_terms() -> list[str]:
    return sorted(RETRIEVAL_MISS_EXPANSION_TERMS)


def has_source_selection_failure_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    source_selection_context = bool(
        re.search(r"\bsource[\s_-]+selection\b", normalized)
        or ("source" in token_context and token_context & {"selection", "selected", "selecting"})
    )
    failure_context = bool(
        token_context & {"fail", "failed", "failure", "failing"}
        or re.search(r"\bwhy\s+did\s+source[\s_-]+selection\s+fail\b", normalized)
    )
    question_context = bool(
        re.search(r"\bwhy\s+did\b", normalized)
        or re.search(r"\bwhat\s+(?:caused|made)\b", normalized)
        or "failure" in token_context
        or "fail" in token_context
    )

    return source_selection_context and failure_context and question_context


def source_selection_failure_terms() -> list[str]:
    return sorted(SOURCE_SELECTION_FAILURE_EXPANSION_TERMS)


def has_action_proof_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    if re.search(r"\b(?:did\s+it\s+)?actually\s+do\s+it\b", normalized):
        return True

    agent_context = bool(token_context & set(ACTION_PROOF_AGENT_TERMS))
    action_context = bool(token_context & set(ACTION_PROOF_ACTION_TERMS))
    execution_context = bool(token_context & set(ACTION_PROOF_EXECUTION_TERMS))
    command_execution_context = bool(
        "command" in token_context
        and (
            token_context & {"execute", "executed", "execution", "ran", "run"}
            or re.search(r"\bcommand\s+(?:was\s+)?(?:executed|run)\b", normalized)
        )
    )
    run_execution_context = bool(
        "run" in token_context
        and (
            agent_context
            or re.search(r"\bwhat\s+did\s+(?:the\s+)?agent\s+run\b", normalized)
        )
    )

    return (
        (agent_context and execution_context)
        or (action_context and execution_context)
        or command_execution_context
        or run_execution_context
    )


def action_proof_terms() -> list[str]:
    return sorted(ACTION_PROOF_EXPANSION_TERMS)


def has_change_summary_improvement_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    if re.search(r"\bwhat\s+did\s+we\s+improve\b", normalized):
        return True

    improvement_intent = bool(token_context & {"improve", "improved", "improvement", "improvements"})
    add_or_change_action_intent = bool(token_context & set(CHANGE_SUMMARY_ADD_OR_CHANGE_ACTION_TERMS))
    tool_work_context = bool(token_context & set(CHANGE_SUMMARY_TOOL_CONTEXT_TERMS))
    summary_question = bool(
        re.search(r"\bwhat\s+changed\b", normalized)
        or re.search(r"\bwhat\s+improvements?\b", normalized)
        or re.search(r"\bwhat\s+(?:was|were)\s+(?:added|implemented|fixed|improved)\b", normalized)
    )

    if summary_question and (improvement_intent or tool_work_context):
        return True
    if improvement_intent and add_or_change_action_intent:
        return True
    return improvement_intent and tool_work_context


def change_summary_improvement_terms() -> list[str]:
    return sorted(CHANGE_SUMMARY_EXPANSION_TERMS)


def has_stale_reference_state_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    stale_status_intent = bool(token_context & set(STALE_REFERENCE_STATE_STATUS_TERMS))
    reference_intent = bool(
        token_context & set(STALE_REFERENCE_STATE_REFERENCE_TERMS)
        or re.search(r"\breferenc(?:e|ed|ing)\b", normalized)
    )
    state_context = bool(
        token_context & {"active", "handoff", "pointer", "queue", "state"}
        or re.search(r"\bactive[\s_-]+state\b", normalized)
    )
    question_or_contrast = bool(
        re.search(r"\b(?:what|which)\s+(?:was|were|is|are)\b", normalized)
        or re.search(r"\bstill\s+(?:mentioned|referenced)\b", normalized)
        or re.search(r"\b(?:stale|old|obsolete|superseded)\s+but\s+(?:still\s+)?(?:mentioned|referenced)\b", normalized)
    )

    return stale_status_intent and reference_intent and (state_context or question_or_contrast)


def stale_reference_state_terms() -> list[str]:
    return sorted(
        {
            *STALE_REFERENCE_STATE_STATUS_TERMS,
            *STALE_REFERENCE_STATE_REFERENCE_TERMS,
            *STALE_REFERENCE_STATE_CONTEXT_TERMS,
        }
    )


def has_next_work_handoff_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    next_task_question = bool(
        "next" in token_context
        and (
            re.search(r"\bwhat\s+(?:is|are|was|were)\s+(?:the\s+)?next\s+(?:task|work|step|action)\b", normalized)
            or re.search(r"\bwhich\s+(?:task|work|step|action)\s+(?:is|are|was|were)\s+next\b", normalized)
        )
    )
    after_this_question = bool(
        re.search(r"\bwhat\s+(?:should\s+)?happen(?:s|ed)?\s+after\s+this\b", normalized)
        or re.search(r"\bwhat\s+(?:should\s+)?come(?:s)?\s+after\s+this\b", normalized)
    )
    what_next_question = bool(
        "next" in token_context
        and re.search(r"\bwhat\s+(?:should\s+)?(?:we|i|you)\s+do\s+next\b", normalized)
    )

    return next_task_question or after_this_question or what_next_question


def next_work_handoff_terms() -> list[str]:
    return sorted(NEXT_WORK_HANDOFF_EXPANSION_TERMS)


def has_claim_boundary_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    overclaim_intent = bool(
        token_context & {"overclaim", "overclaimed", "overclaims", "overclaiming"}
        or re.search(r"\bover[\s_-]*claim(?:ed|s|ing)?\b", normalized)
    )
    if overclaim_intent:
        return True

    claim_intent = bool(
        token_context & {"claim", "claimed", "claims", "claiming"}
        or re.search(r"\bclaim[\s_-]+boundar(?:y|ies)\b", normalized)
    )
    boundary_intent = bool(
        token_context & {"boundary", "boundaries", "evidence", "reviewed", "unsupported"}
        or re.search(r"\bnot[\s_-]+allowed\b", normalized)
        or re.search(r"\bwithout\s+(?:separate\s+)?reviewed\s+evidence\b", normalized)
        or re.search(r"\bnot\s+(?:backed|supported|allowed)\b", normalized)
    )
    question_context = bool(
        re.search(r"\b(?:what|which)\s+(?:claim|claims)\b", normalized)
        or re.search(r"\b(?:what|which)\s+(?:was|were|is|are)\s+.*\bclaim", normalized)
    )

    return claim_intent and boundary_intent and (question_context or "boundary" in token_context or "boundaries" in token_context)


def claim_boundary_terms() -> list[str]:
    return sorted({*CLAIM_BOUNDARY_CLAIM_TERMS, *CLAIM_BOUNDARY_QUALITY_TERMS})


def exact_phrase_terms(normalized: str) -> list[str]:
    phrases: list[str] = []
    if _eligible_exact_phrase(normalized):
        phrases.append(normalized)
    for quoted in re.findall(r'"([^"]+)"|\'([^\']+)\'', normalized):
        phrase = next((item for item in quoted if item), "")
        cleaned = " ".join(phrase.split())
        if _eligible_exact_phrase(cleaned):
            phrases.append(cleaned)
    return sorted(set(phrases))


def _eligible_exact_phrase(value: str) -> bool:
    if len(value) <= 2 or len(value) > MAX_EXACT_PHRASE_CHARS:
        return False
    tokens = SEPARATED_WORD_RE.findall(value)
    return 2 <= len(tokens) <= MAX_EXACT_PHRASE_TOKENS


def bounded_reasons(reasons: dict[str, set[str]]) -> dict[str, set[str]]:
    if len(reasons) <= MAX_EXPANDED_TERMS:
        return reasons
    ordered = sorted(reasons.items(), key=lambda item: (_reason_priority(item[1]), item[0]))
    return dict(ordered[:MAX_EXPANDED_TERMS])


def _reason_priority(term_reasons: set[str]) -> tuple[int, int]:
    priority_order = (
        "exact_phrase",
        "artifact_path",
        "task_slug",
        "preference",
        "date_window",
        "normalized_terms",
        "semantic_local",
        "synonym_paraphrase",
    )
    priority = min((priority_order.index(reason) for reason in term_reasons if reason in priority_order), default=len(priority_order))
    return priority, len(term_reasons)


def task_slug_variants(text: str) -> list[str]:
    variants: set[str] = set()
    for match in TASK_SLUG_RE.finditer(text.lower()):
        prefix, number, suffix = match.groups()
        canonical = f"{prefix}-{number}"
        variants.update(
            {
                canonical,
                canonical.replace("-", "_"),
                canonical.replace("-", ""),
                canonical.replace("-", " "),
                number,
                f"task {canonical}",
            }
        )
        if suffix:
            full = f"{canonical}-{suffix}"
            variants.update({full, full.replace("-", "_"), full.replace("-", ""), full.replace("-", " ")})
    return sorted(variants)


def artifact_variants(value: str) -> list[str]:
    normalized = value.lower().strip()
    if not normalized:
        return []
    path = PurePosixPath(normalized)
    name = path.name
    stem = path.stem
    variants = {normalized, name}
    if stem:
        variants.add(stem)
        variants.add(stem.replace("-", "_"))
        variants.add(stem.replace("_", "-"))
        split = " ".join(SEPARATED_WORD_RE.findall(stem))
        if split:
            variants.add(split)
            variants.update(part for part in split.split() if len(part) > 2)
    if path.suffix:
        variants.add(path.suffix.lstrip("."))
    return sorted(variants)


def date_window_terms(
    start: datetime | None,
    end: datetime | None,
    reason: str | None,
) -> list[str]:
    terms: set[str] = set()
    if reason:
        terms.add(reason)
    if not start or not end:
        return sorted(terms)

    start_day = start.date()
    end_day = end.date()
    if end.time() == datetime.min.time() and end_day > start_day:
        end_day -= timedelta(days=1)
    span = (end_day - start_day).days
    if span < 0:
        return sorted(terms)
    for offset in range(min(span, 13) + 1):
        day = start_day + timedelta(days=offset)
        iso = day.isoformat()
        terms.add(iso)
        terms.add(iso.replace("-", "/"))
        terms.add(f"{day.strftime('%b')} {day.day}".lower())
        terms.add(f"{day.strftime('%B')} {day.day}".lower())
    return sorted(terms)
