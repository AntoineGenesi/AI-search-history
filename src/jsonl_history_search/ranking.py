from __future__ import annotations

import re
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import PurePath

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
        stage = stage_for_term(intent, term)
        matched = term.lower() in lowered
        phrase_like = False
        if not matched and stage == "exact_phrase":
            phrase_like = phrase_like_match(term, lowered)
            matched = phrase_like
        if matched:
            score += TEXT_STAGE_WEIGHTS.get(stage, 8.0)
            reasons.append(f"text:{stage}:{term}")
            if phrase_like:
                reasons.append(f"phrase:separator_insensitive:{term}")
            elif stage == "exact_phrase" and len(term_tokens(term)) > 1:
                reasons.append(f"phrase:exact:{term}")

    coverage = base_term_coverage(record.text, intent)
    base_count = len(coverage["base_terms"])
    covered_count = len(coverage["covered"])
    if base_count:
        reasons.append(f"coverage:base_terms:{covered_count}/{base_count}")
        if covered_count == base_count and base_count > 1:
            score += 9.0
        elif covered_count > 1:
            score += min(6.0, covered_count * 2.0)

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
    artifacts = artifact_candidates(record.text)
    project_score, project_reasons = project_evidence(record, cwd, artifacts)
    if project_score:
        score += project_score
        reasons.extend(project_reasons)
    if intent.artifact_hints and artifacts:
        score += 5
        reasons.append("artifact:artifact_path")
    if is_partial_generic_multi_token(reasons, intent):
        score -= 4.0
        reasons.append("penalty:partial_generic_multi_token")
    if record.timestamp:
        age_days = max(0.0, (datetime.now(timezone.utc) - record.timestamp.astimezone(timezone.utc)).total_seconds() / 86400)
        score += max(0.0, 3.0 - min(age_days / 30.0, 3.0))
    return max(score, 0.0), sorted(set(reasons), key=reasons.index), artifacts


def role_matches(actual: str, wanted: str) -> bool:
    actual = (actual or "").lower()
    wanted = wanted.lower()
    if wanted == "assistant":
        return any(token in actual for token in ("assistant", "agent"))
    if wanted == "user":
        return "user" in actual or actual == "human"
    return wanted in actual


def should_consider(record: Record, intent: QueryIntent) -> bool:
    if intent.role and intent.role_filter_explicit and not role_matches(record.role, intent.role):
        return False
    if intent.date_window_filter and intent.date_window_start and intent.date_window_end:
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
        if any(stage_for_term(intent, term) == "exact_phrase" and phrase_like_match(term, lowered) for term in terms):
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


def is_weak_partial_generic(reasons: list[str]) -> bool:
    reason_set = set(reasons)
    if "penalty:partial_generic_multi_token" not in reason_set:
        return False
    strong_prefixes = (
        "phrase:",
        "artifact:",
        "text:artifact_path:",
        "text:task_slug:",
    )
    return not any(reason.startswith(strong_prefixes) for reason in reason_set)


def is_partial_generic_multi_token(reasons: list[str], intent: QueryIntent) -> bool:
    base_terms = concrete_base_terms(intent)
    if len(base_terms) < 2 or not has_multi_token_exact_phrase(intent):
        return False
    if any(reason.startswith("phrase:") for reason in reasons):
        return False
    coverage_reason = next((reason for reason in reasons if reason.startswith("coverage:base_terms:")), "")
    covered_count, total_count = parse_coverage_reason(coverage_reason)
    if total_count == len(base_terms) and covered_count == total_count:
        return False
    generic_hits = [
        reason
        for reason in reasons
        if reason.startswith("text:normalized_terms:")
        or reason.startswith("text:synonym_paraphrase:")
        or reason.startswith("text:semantic_local:")
        or reason.startswith("fuzzy:typo_fuzzy:")
    ]
    return bool(generic_hits)


def has_multi_token_exact_phrase(intent: QueryIntent) -> bool:
    for term in intent.terms:
        if "exact_phrase" in set(intent.expansion_reasons.get(term, [])) and len(term_tokens(term)) > 1:
            return True
    return False


def parse_coverage_reason(reason: str) -> tuple[int, int]:
    if not reason:
        return 0, 0
    value = reason.rsplit(":", 1)[-1]
    try:
        covered_text, total_text = value.split("/", 1)
        return int(covered_text), int(total_text)
    except ValueError:
        return 0, 0


def base_term_coverage(text: str, intent: QueryIntent) -> dict[str, list[str]]:
    terms = concrete_base_terms(intent)
    if not terms:
        return {"base_terms": [], "covered": []}
    tokens = token_set(text)
    lowered = text.lower()
    covered = []
    for term in terms:
        normalized = term.lower()
        if normalized in tokens or normalized in lowered or phrase_like_match(normalized, lowered):
            covered.append(term)
    return {"base_terms": terms, "covered": sorted(set(covered))}


def concrete_base_terms(intent: QueryIntent) -> list[str]:
    return sorted({term.lower() for term in intent.base_terms if len(term) > 2})


def phrase_like_match(term: str, lowered_text: str) -> bool:
    tokens = term_tokens(term)
    if len(tokens) < 2:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\W_./:-]+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])"
    return re.search(pattern, lowered_text) is not None


def term_tokens(term: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", term.lower())


def project_evidence(record: Record, cwd: str | None, artifacts: list[str]) -> tuple[float, list[str]]:
    if not cwd:
        return 0.0, []
    reasons: list[str] = []
    score = 0.0
    cwd_text = cwd.lower().replace("\\", "/").rstrip("/")
    record_cwd = (record.cwd or "").lower().replace("\\", "/").rstrip("/")
    lowered_text = record.text.lower()

    if record_cwd and record_cwd == cwd_text:
        score += 5.0
        reasons.extend(["cwd", "project:cwd_exact"])
    elif record_cwd and cwd_text and record_cwd.startswith(f"{cwd_text}/"):
        score += 3.0
        reasons.extend(["cwd", "project:cwd_exact"])

    basename = PurePath(cwd_text).name if cwd_text else ""
    slug_variants = {basename, basename.replace("-", " "), basename.replace("_", " "), basename.replace("-", "_"), basename.replace("_", "-")}
    if basename and any(variant and (variant in record_cwd or variant in lowered_text) for variant in slug_variants):
        score += 2.0
        reasons.append("project:cwd_basename")

    if task_or_doc_path_evidence(lowered_text, artifacts):
        score += 3.0
        reasons.append("project:task_or_doc_path")

    if score > 8.0:
        score = 8.0
    return score, sorted(set(reasons), key=reasons.index)


def task_or_doc_path_evidence(lowered_text: str, artifacts: list[str]) -> bool:
    projectish_paths = tuple(artifact for artifact in artifacts if artifact.startswith(("tasks/", "docs/", "reports/", "codex/state/reviews/")))
    if projectish_paths:
        return True
    return bool(re.search(r"\b(?:tasks|docs|reports|codex/state/reviews)/[\w./-]+", lowered_text))


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
