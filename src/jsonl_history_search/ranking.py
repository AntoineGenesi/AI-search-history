from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .clustering import cluster_matches
from .query import QueryIntent
from .records import Record

WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.:/-]*")

TEXT_STAGE_WEIGHTS = {
    "exact_phrase": 14.0,
    "artifact_path": 12.0,
    "normalized_terms": 10.0,
    "task_slug": 10.0,
    "preference": 8.0,
    "synonym_paraphrase": 8.0,
    "semantic_local": 6.0,
}
FUZZY_STAGE_WEIGHT = 7.0


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
            stage = stage_for_term(intent, term)
            score += TEXT_STAGE_WEIGHTS.get(stage, 8.0)
            reasons.append(f"text:{stage}:{term}")
    for fuzzy in fuzzy_match_terms(record.text, intent):
        if any(reason.endswith(f":{fuzzy['query']}") for reason in reasons):
            continue
        score += FUZZY_STAGE_WEIGHT
        reasons.append(f"fuzzy:typo_fuzzy:{fuzzy['query']}~{fuzzy['matched']}")
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
        reasons.append("artifact:artifact_path")
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
        if any(term.lower() in lowered for term in terms):
            return True
        return bool(fuzzy_match_terms(record.text, intent))
    return True


def active_match_terms(intent: QueryIntent) -> list[str]:
    active: list[str] = []
    for term in intent.terms:
        reasons = set(intent.expansion_reasons.get(term, []))
        if reasons and reasons <= {"date_window"}:
            continue
        active.append(term)
    return active


def stage_for_term(intent: QueryIntent, term: str) -> str:
    reasons = set(intent.expansion_reasons.get(term, []))
    for stage in ("exact_phrase", "artifact_path", "normalized_terms", "task_slug", "synonym_paraphrase", "semantic_local", "preference"):
        if stage in reasons:
            return stage
    if "synonym" in reasons:
        return "synonym_paraphrase"
    if "artifact" in reasons:
        return "artifact_path"
    if "base" in reasons:
        return "normalized_terms"
    return "normalized_terms"


def fuzzy_match_terms(text: str, intent: QueryIntent, *, limit: int = 8) -> list[dict[str, str]]:
    query_terms = fuzzy_query_terms(intent)
    if not query_terms:
        return []
    record_tokens = token_set(text)
    if not record_tokens:
        return []
    matches: list[dict[str, str]] = []
    for query in query_terms:
        if query in record_tokens:
            continue
        best = best_fuzzy_token(query, record_tokens)
        if best:
            matches.append({"query": query, "matched": best})
            if len(matches) >= limit:
                break
    return matches


def fuzzy_query_terms(intent: QueryIntent) -> list[str]:
    terms: list[str] = []
    for term in intent.base_terms:
        normalized = term.lower()
        if len(normalized) < 5:
            continue
        if not re.fullmatch(r"[a-z0-9_-]+", normalized):
            continue
        terms.append(normalized)
    return sorted(set(terms))


def token_set(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in WORD_RE.findall(text.lower()):
        if len(token) < 4:
            continue
        tokens.add(token)
        for part in re.split(r"[_.:/-]+", token):
            if len(part) >= 4:
                tokens.add(part)
    return tokens


def best_fuzzy_token(query: str, tokens: set[str]) -> str | None:
    threshold = fuzzy_threshold(query)
    best_token: str | None = None
    best_score = 0.0
    for token in tokens:
        if abs(len(query) - len(token)) > max(2, len(query) // 3):
            continue
        score = SequenceMatcher(None, query, token).ratio()
        if score > best_score:
            best_score = score
            best_token = token
    return best_token if best_token and best_score >= threshold else None


def fuzzy_threshold(term: str) -> float:
    if len(term) <= 5:
        return 0.86
    if len(term) <= 8:
        return 0.82
    return 0.78


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
