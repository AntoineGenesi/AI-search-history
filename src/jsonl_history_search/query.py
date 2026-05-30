from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .expansion import expand_query_terms
from .timeutil import local_day_window

STOPWORDS = {
    "a",
    "about",
    "again",
    "all",
    "and",
    "before",
    "did",
    "find",
    "from",
    "i",
    "it",
    "last",
    "me",
    "of",
    "said",
    "that",
    "the",
    "thing",
    "this",
    "to",
    "told",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "you",
}

ARTIFACT_WORDS = {"artifact", "file", "report", "doc", "docs", "html", "markdown", "csv", "json"}


@dataclass
class QueryIntent:
    original: str
    normalized: str
    terms: list[str]
    role: str | None = None
    date_window_start: datetime | None = None
    date_window_end: datetime | None = None
    date_reason: str | None = None
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
            "date_window": {
                "start": self.date_window_start.isoformat() if self.date_window_start else None,
                "end": self.date_window_end.isoformat() if self.date_window_end else None,
                "reason": self.date_reason,
            },
            "artifact_hints": self.artifact_hints,
            "warnings": self.warnings,
        }


def infer_query(query: str, *, now: datetime, role: str | None = None) -> QueryIntent:
    normalized = " ".join(query.lower().split())
    inferred_role = role or infer_role(normalized)
    start, end, reason = infer_date_window(normalized, now)
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
        date_window_start=start,
        date_window_end=end,
        date_reason=reason,
        artifact_hints=artifact_hints,
        base_terms=base_terms,
        expansion_reasons=expanded.reasons,
        warnings=warnings,
    )


def infer_role(normalized: str) -> str | None:
    if re.search(r"\b(i said|i told you|what i told|my message|user said)\b", normalized):
        return "user"
    if re.search(r"\b(you said|you decided|your plan|assistant said|agent said)\b", normalized):
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
    return None, None, None


def infer_artifacts(normalized: str) -> list[str]:
    hints = sorted(word for word in ARTIFACT_WORDS if re.search(rf"\b{re.escape(word)}s?\b", normalized))
    for pattern in (r"[\w./-]+\.(?:md|html|json|jsonl|csv|txt|py)", r"(?:docs|reports|tasks)/[\w./-]+"):
        hints.extend(re.findall(pattern, normalized))
    return sorted(set(hints))


def infer_terms(normalized: str, artifact_hints: list[str]) -> list[str]:
    words = re.findall(r"[a-z0-9_.:/-]+", normalized)
    terms = [word for word in words if len(word) > 2 and word not in STOPWORDS]
    for hint in artifact_hints:
        if hint not in terms:
            terms.append(hint)
    return sorted(set(terms))
