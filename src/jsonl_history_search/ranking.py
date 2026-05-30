from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .clustering import cluster_matches
from .query import QueryIntent
from .records import Record


@dataclass
class Match:
    path: str
    line: int
    role: str
    timestamp: str | None
    cwd: str | None
    snippet: str
    score: float
    reasons: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    session: str | None = None
    tasks: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def score_record(record: Record, intent: QueryIntent, cwd: str | None) -> tuple[float, list[str], list[str]]:
    score = 0.0
    reasons: list[str] = []
    lowered = record.text.lower()
    match_terms = active_match_terms(intent)
    for term in match_terms:
        if term.lower() in lowered:
            score += 10
            reasons.append(f"text:{term}")
    if not match_terms:
        score += 1
        reasons.append("bounded-no-term")
    if intent.role and role_matches(record.role, intent.role):
        score += 8
        reasons.append(f"role:{intent.role}")
    if intent.date_window_start and intent.date_window_end and record.timestamp:
        if intent.date_window_start <= record.timestamp.astimezone(intent.date_window_start.tzinfo) < intent.date_window_end:
            score += 8
            reasons.append("date-window")
    if cwd and record.cwd and cwd in record.cwd:
        score += 6
        reasons.append("cwd")
    artifacts = artifact_candidates(record.text)
    if intent.artifact_hints and artifacts:
        score += 5
        reasons.append("artifact")
    if record.timestamp:
        age_days = max(0.0, (datetime.now(timezone.utc) - record.timestamp.astimezone(timezone.utc)).total_seconds() / 86400)
        score += max(0.0, 3.0 - min(age_days / 30.0, 3.0))
    return score, reasons, artifacts


def role_matches(actual: str, wanted: str) -> bool:
    actual = (actual or "").lower()
    wanted = wanted.lower()
    if wanted == "assistant":
        return any(token in actual for token in ("assistant", "agent"))
    if wanted == "user":
        return "user" in actual or actual == "human"
    return wanted in actual


def should_consider(record: Record, intent: QueryIntent) -> bool:
    if intent.role and not role_matches(record.role, intent.role):
        return False
    if intent.date_window_start and intent.date_window_end:
        if record.timestamp is None:
            return False
        local_ts = record.timestamp.astimezone(intent.date_window_start.tzinfo)
        if not (intent.date_window_start <= local_ts < intent.date_window_end):
            return False
    terms = active_match_terms(intent)
    if terms:
        lowered = record.text.lower()
        return any(term.lower() in lowered for term in terms)
    return True


def active_match_terms(intent: QueryIntent) -> list[str]:
    active: list[str] = []
    for term in intent.terms:
        reasons = set(intent.expansion_reasons.get(term, []))
        if reasons and reasons <= {"date_window"}:
            continue
        active.append(term)
    return active


def make_snippet(text: str, terms: list[str], context: int = 180) -> str:
    clean = " ".join(text.split())
    lowered = clean.lower()
    indices = [lowered.find(term.lower()) for term in terms if term and lowered.find(term.lower()) >= 0]
    if not indices:
        return clean[: context * 2]
    index = min(indices)
    start = max(0, index - context)
    end = min(len(clean), index + context)
    return clean[start:end]


def artifact_candidates(text: str) -> list[str]:
    candidates = re.findall(r"(?:docs|reports|tasks|codex/state/reviews)/[\w./-]+|\b[\w./-]+\.(?:md|html|json|jsonl|csv|txt|py)\b", text)
    return sorted(set(candidates))


def group_matches(matches: list[Match]) -> list[dict]:
    return cluster_matches(matches)
