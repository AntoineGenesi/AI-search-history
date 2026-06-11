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

    for term in date_window_terms(date_window_start, date_window_end, date_reason):
        add(term, "date_window")

    bounded = bounded_reasons(reasons)
    return ExpansionResult(
        terms=sorted(bounded),
        reasons={term: sorted(term_reasons) for term, term_reasons in sorted(bounded.items())},
    )


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
