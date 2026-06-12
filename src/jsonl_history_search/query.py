from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .expansion import SEMANTIC_LOCAL, expand_query_terms, task_slug_variants
from .timeutil import local_day_window

MAX_BASE_TERMS = 64

STOPWORDS = {
    "a",
    "about",
    "after",
    "again",
    "all",
    "also",
    "another",
    "and",
    "any",
    "are",
    "around",
    "ask",
    "asked",
    "at",
    "back",
    "be",
    "because",
    "been",
    "before",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "done",
    "for",
    "find",
    "from",
    "had",
    "has",
    "have",
    "help",
    "here",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "just",
    "last",
    "like",
    "look",
    "looking",
    "make",
    "me",
    "my",
    "new",
    "not",
    "now",
    "of",
    "on",
    "one",
    "only",
    "or",
    "our",
    "out",
    "please",
    "prior",
    "really",
    "remember",
    "remembered",
    "search",
    "said",
    "same",
    "see",
    "should",
    "show",
    "some",
    "something",
    "that",
    "the",
    "their",
    "then",
    "there",
    "thing",
    "things",
    "this",
    "to",
    "told",
    "up",
    "use",
    "used",
    "using",
    "want",
    "way",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "you",
    "your",
}

ARTIFACT_WORDS = {"artifact", "file", "report", "review", "doc", "docs", "html", "markdown", "csv", "json"}
ROLE_HINT_WORDS = {"agent", "assistant", "human", "role", "source", "user"}
DATE_TOKEN_RE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b")
DATE_METADATA_REASONS = {"date window", "duration window", "last run", "runtime", "same day", "time window", "timeline", "timestamp"}


@dataclass
class QueryIntent:
    original: str
    normalized: str
    terms: list[str]
    role: str | None = None
    role_filter_explicit: bool = False
    date_window_start: datetime | None = None
    date_window_end: datetime | None = None
    date_reason: str | None = None
    date_window_filter: bool = False
    artifact_hints: list[str] = field(default_factory=list)
    base_terms: list[str] = field(default_factory=list)
    expansion_reasons: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "terms": self.terms,
            "base_terms": self.base_terms,
            "expansion_reasons": self.expansion_reasons,
            "role": self.role,
            "role_filter_explicit": self.role_filter_explicit,
            "date_window": {
                "start": self.date_window_start.isoformat() if self.date_window_start else None,
                "end": self.date_window_end.isoformat() if self.date_window_end else None,
                "reason": self.date_reason,
                "filter": self.date_window_filter,
            },
            "artifact_hints": self.artifact_hints,
            "warnings": self.warnings,
        }


def infer_query(query: str, *, now: datetime, role: str | None = None) -> QueryIntent:
    normalized = " ".join(query.lower().split())
    inferred_role = role or infer_role(normalized)
    role_filter_explicit = role is not None
    start, end, reason = infer_date_window(normalized, now)
    date_window_filter = infer_date_window_filter(normalized, reason)
    artifact_hints = infer_artifacts(normalized)
    base_terms = infer_terms(normalized, artifact_hints)
    expanded = expand_query_terms(
        normalized=normalized,
        base_terms=base_terms,
        artifact_hints=artifact_hints,
        date_window_start=start,
        date_window_end=end,
        date_reason=reason,
    )
    terms = expanded.terms
    warnings: list[str] = []
    if not terms and not (start and end):
        warnings.append("No concrete terms or date window inferred; search may be broad within selected transcript roots.")
    return QueryIntent(
        original=query,
        normalized=normalized,
        terms=terms,
        role=inferred_role,
        role_filter_explicit=role_filter_explicit,
        date_window_start=start,
        date_window_end=end,
        date_reason=reason,
        date_window_filter=date_window_filter,
        artifact_hints=artifact_hints,
        base_terms=base_terms,
        expansion_reasons=expanded.reasons,
        warnings=warnings,
    )


def infer_role(normalized: str) -> str | None:
    if re.search(r"\b(i said|i told you|what i told|my message|user said)\b", normalized):
        return "user"
    if re.search(
        r"\b(you said|you decided|your plan|assistant said|agent said|did you|did the agent|agent do|you do|you run|you create|you implement)\b",
        normalized,
    ):
        return "assistant"
    return None


def infer_date_window(normalized: str, now: datetime):
    today = now.date()
    if "yesterday" in normalized:
        return (*local_day_window(today - timedelta(days=1), now.tzinfo), "yesterday")
    if "last night" in normalized:
        start = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo).replace(hour=18)
        end = datetime.combine(today, datetime.min.time(), tzinfo=now.tzinfo).replace(hour=6)
        return start, end, "last night"
    if "last week" in normalized:
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start
        return (
            datetime.combine(start, datetime.min.time(), tzinfo=now.tzinfo),
            datetime.combine(end, datetime.min.time(), tzinfo=now.tzinfo),
            "last week",
        )
    if "few days ago" in normalized or "a few days ago" in normalized:
        return (*local_day_window(today - timedelta(days=3), now.tzinfo), "a few days ago")
    if "recent" in normalized or "recently" in normalized:
        return now - timedelta(days=7), now, "recent"
    for label in sorted(DATE_METADATA_REASONS):
        if re.search(rf"\b{re.escape(label)}\b", normalized):
            return None, None, label
    return None, None, None


def infer_date_window_filter(normalized: str, reason: str | None) -> bool:
    if reason is None:
        return False
    if reason in DATE_METADATA_REASONS:
        return False
    if len(normalized) > 500:
        return False
    if re.search(r"\b(earlier|previously|remember|remembered|prior instruction|vague)\b", normalized):
        return False
    return True


def infer_artifacts(normalized: str) -> list[str]:
    hints: list[str] = []
    for word in sorted(ARTIFACT_WORDS):
        if re.search(rf"\b{re.escape(word)}s?\b", normalized):
            hints.append(word)
    for pattern in (
        r"[\w./-]+\.(?:md|html|json|jsonl|csv|txt|py|sh)",
        r"(?:codex/state/reviews|docs|reports|tasks|reviews|src|tests)/[\w./-]+",
    ):
        hints.extend(re.findall(pattern, normalized))
    return _unique_ordered(hints)[:40]


def infer_terms(normalized: str, artifact_hints: list[str]) -> list[str]:
    words = re.findall(r"[a-z0-9_.:/-]+", normalized)
    priority_terms: list[str] = []
    for hint in artifact_hints:
        priority_terms.append(hint)

    handle_terms: list[str] = []
    preference_terms: list[str] = []
    semantic_terms: list[str] = []
    concrete_terms: list[str] = []
    for word in words:
        if len(word) <= 2 or word in STOPWORDS or DATE_TOKEN_RE.fullmatch(word):
            continue
        if _is_path_or_handle(word):
            handle_terms.append(word)
            continue
        if word in ROLE_HINT_WORDS:
            handle_terms.append(word)
            continue
        if word in {"avoid", "dislike", "disliked", "liked", "prefer", "preferred", "preference", "preferences", "style", "taste"}:
            preference_terms.append(word)
            continue
        if word in SEMANTIC_LOCAL:
            semantic_terms.append(word)
            continue
        if len(word) >= 4:
            concrete_terms.append(word)

    ordered = _unique_ordered([*priority_terms, *handle_terms, *preference_terms, *semantic_terms, *concrete_terms])
    return ordered[:MAX_BASE_TERMS]


def _is_path_or_handle(word: str) -> bool:
    if word.isdigit():
        return False
    if any(separator in word for separator in ("/", ".", "_", ":", "-")):
        return True
    return any(char.isdigit() for char in word)


def _unique_ordered(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered
