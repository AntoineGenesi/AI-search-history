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
BROAD_UNANCHORED_LOW_EVIDENCE_PENALTY = "penalty:broad_unanchored_low_evidence"
PARTIAL_GENERIC_MULTI_TOKEN_PENALTY = "penalty:partial_generic_multi_token"


@dataclass(frozen=True)
class MatchTermProfile:
    term: str
    lowered: str
    stage: str
    phrase_pattern: re.Pattern[str] | None = None


@dataclass(frozen=True)
class QueryMatchProfile:
    active_terms: tuple[MatchTermProfile, ...]
    base_terms: tuple[str, ...]
    fuzzy_terms: tuple[str, ...]
    has_multi_token_exact_phrase: bool


@dataclass
class RecordMatchEvidence:
    lowered_text: str
    should_consider: bool
    tokens: set[str] | None = None
    fuzzy_matches: list[dict[str, str]] | None = None


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
    provider: str | None = None
    source_ref: str | None = None
    source_refs: list[str] = field(default_factory=list)
    native_id: str | None = None
    timestamp_quality: str = "record"
    coverage_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_match_profile(intent: QueryIntent) -> QueryMatchProfile:
    active: list[MatchTermProfile] = []
    for term in active_match_terms(intent):
        stage = stage_for_term(intent, term)
        active.append(
            MatchTermProfile(
                term=term,
                lowered=term.lower(),
                stage=stage,
                phrase_pattern=_phrase_pattern(term) if stage == "exact_phrase" else None,
            )
        )
    active_terms = tuple(active)
    return QueryMatchProfile(
        active_terms=active_terms,
        base_terms=tuple(concrete_base_terms(intent)),
        fuzzy_terms=tuple(fuzzy_query_terms(intent)),
        has_multi_token_exact_phrase=any(
            "exact_phrase" in set(intent.expansion_reasons.get(term, [])) and len(term_tokens(term)) > 1
            for term in intent.terms
        ),
    )


def evaluate_record_match(
    record: Record,
    intent: QueryIntent,
    *,
    profile: QueryMatchProfile | None = None,
) -> RecordMatchEvidence:
    profile = profile or build_match_profile(intent)
    evidence = RecordMatchEvidence(lowered_text=record.text.lower(), should_consider=True)
    if intent.role and intent.role_filter_explicit and not role_matches(record.role, intent.role):
        evidence.should_consider = False
        return evidence
    if intent.date_window_filter and intent.date_window_start and intent.date_window_end:
        if record.timestamp is None:
            evidence.should_consider = False
            return evidence
        local_ts = record.timestamp.astimezone(intent.date_window_start.tzinfo)
        if not (intent.date_window_start <= local_ts < intent.date_window_end):
            evidence.should_consider = False
            return evidence
    if not profile.active_terms:
        return evidence
    if any(term.lowered in evidence.lowered_text for term in profile.active_terms):
        return evidence
    if any(_profile_phrase_like_match(term, evidence.lowered_text) for term in profile.active_terms):
        return evidence
    evidence.fuzzy_matches = fuzzy_match_terms(record.text, intent, profile=profile, evidence=evidence)
    evidence.should_consider = bool(evidence.fuzzy_matches)
    return evidence


def score_record(
    record: Record,
    intent: QueryIntent,
    cwd: str | None,
    *,
    profile: QueryMatchProfile | None = None,
    evidence: RecordMatchEvidence | None = None,
) -> tuple[float, list[str], list[str]]:
    profile = profile or build_match_profile(intent)
    evidence = evidence or RecordMatchEvidence(lowered_text=record.text.lower(), should_consider=True)
    score = 0.0
    reasons: list[str] = []
    lowered = evidence.lowered_text
    for term_profile in profile.active_terms:
        term = term_profile.term
        stage = term_profile.stage
        matched = term_profile.lowered in lowered
        phrase_like = False
        if not matched and stage == "exact_phrase":
            phrase_like = _profile_phrase_like_match(term_profile, lowered)
            matched = phrase_like
        if matched:
            score += TEXT_STAGE_WEIGHTS.get(stage, 8.0)
            reasons.append(f"text:{stage}:{term}")
            if phrase_like:
                reasons.append(f"phrase:separator_insensitive:{term}")
            elif stage == "exact_phrase" and term_profile.phrase_pattern is not None:
                reasons.append(f"phrase:exact:{term}")

    coverage = base_term_coverage(record.text, intent, profile=profile, evidence=evidence)
    base_count = len(coverage["base_terms"])
    covered_count = len(coverage["covered"])
    if base_count:
        reasons.append(f"coverage:base_terms:{covered_count}/{base_count}")
        if covered_count == base_count and base_count > 1:
            score += 9.0
        elif covered_count > 1:
            score += min(6.0, covered_count * 2.0)

    for fuzzy in fuzzy_match_terms(record.text, intent, profile=profile, evidence=evidence):
        if any(reason.endswith(f":{fuzzy['query']}") for reason in reasons):
            continue
        score += FUZZY_STAGE_WEIGHT
        reasons.append(f"fuzzy:typo_fuzzy:{fuzzy['query']}~{fuzzy['matched']}")
    if not profile.active_terms:
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
    if is_partial_generic_multi_token(reasons, intent, profile=profile):
        score -= 4.0
        reasons.append(PARTIAL_GENERIC_MULTI_TOKEN_PENALTY)
    elif is_broad_unanchored_low_evidence(reasons):
        score -= 3.0
        reasons.append(BROAD_UNANCHORED_LOW_EVIDENCE_PENALTY)
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


def should_consider(
    record: Record,
    intent: QueryIntent,
    *,
    profile: QueryMatchProfile | None = None,
) -> bool:
    return evaluate_record_match(record, intent, profile=profile).should_consider


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


def candidate_strength_label(reasons: list[str]) -> str:
    reason_set = set(reasons)
    if has_anchoring_evidence(reasons):
        return "strong"
    if PARTIAL_GENERIC_MULTI_TOKEN_PENALTY in reason_set:
        return "weak_partial_generic"
    if BROAD_UNANCHORED_LOW_EVIDENCE_PENALTY in reason_set:
        return "weak_broad_unanchored_low_evidence"
    return "strong"


def is_weak_partial_generic(reasons: list[str]) -> bool:
    return candidate_strength_label(reasons) != "strong"


def strong_evidence_retention_tier(reasons: list[str]) -> int:
    """General evidence tier used before final output caps choose retained candidates."""
    tier = 0
    for reason in reasons:
        if reason.startswith(("text:artifact_path:", "text:task_slug:", "artifact:")):
            tier = max(tier, 5)
        elif reason.startswith("phrase:"):
            tier = max(tier, 5)
        elif reason in {"date-window", "cwd"} or reason.startswith("project:"):
            tier = max(tier, 4)
        elif reason.startswith("fuzzy:typo_fuzzy:"):
            tier = max(tier, 3)
    covered_count, total_count = coverage_counts(reasons)
    if total_count > 1 and covered_count == total_count:
        tier = max(tier, 4)
    elif covered_count > 1:
        tier = max(tier, 3)
    return tier


def is_partial_generic_multi_token(
    reasons: list[str],
    intent: QueryIntent,
    *,
    profile: QueryMatchProfile | None = None,
) -> bool:
    profile = profile or build_match_profile(intent)
    base_terms = list(profile.base_terms)
    if len(base_terms) < 2 or not profile.has_multi_token_exact_phrase:
        return False
    if has_anchoring_evidence(reasons):
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


def is_broad_unanchored_low_evidence(reasons: list[str]) -> bool:
    if has_anchoring_evidence(reasons):
        return False
    covered_count, total_count = coverage_counts(reasons)
    if total_count < 2 or covered_count > 1 or covered_count == total_count:
        return False
    generic_hits = [
        reason
        for reason in reasons
        if reason.startswith("text:normalized_terms:")
        or reason.startswith("text:synonym_paraphrase:")
        or reason.startswith("text:semantic_local:")
    ]
    return 0 < len(generic_hits) <= 2


def has_anchoring_evidence(reasons: list[str]) -> bool:
    for reason in reasons:
        if reason.startswith(("phrase:", "artifact:", "text:artifact_path:", "text:task_slug:", "role:", "project:")):
            return True
        if reason in {"cwd", "date-window"}:
            return True
    covered_count, total_count = coverage_counts(reasons)
    return bool(total_count and (covered_count == total_count or covered_count > 1))


def coverage_counts(reasons: list[str]) -> tuple[int, int]:
    coverage_reason = next((reason for reason in reasons if reason.startswith("coverage:base_terms:")), "")
    return parse_coverage_reason(coverage_reason)


def has_multi_token_exact_phrase(intent: QueryIntent) -> bool:
    return build_match_profile(intent).has_multi_token_exact_phrase


def parse_coverage_reason(reason: str) -> tuple[int, int]:
    if not reason:
        return 0, 0
    value = reason.rsplit(":", 1)[-1]
    try:
        covered_text, total_text = value.split("/", 1)
        return int(covered_text), int(total_text)
    except ValueError:
        return 0, 0


def base_term_coverage(
    text: str,
    intent: QueryIntent,
    *,
    profile: QueryMatchProfile | None = None,
    evidence: RecordMatchEvidence | None = None,
) -> dict[str, list[str]]:
    profile = profile or build_match_profile(intent)
    terms = list(profile.base_terms)
    if not terms:
        return {"base_terms": [], "covered": []}
    tokens = _tokens_for_text(text, evidence)
    lowered = _lowered_for_text(text, evidence)
    covered = []
    for term in terms:
        normalized = term.lower()
        if normalized in tokens or normalized in lowered or phrase_like_match(normalized, lowered):
            covered.append(term)
    return {"base_terms": terms, "covered": sorted(set(covered))}


def concrete_base_terms(intent: QueryIntent) -> list[str]:
    return sorted({term.lower() for term in intent.base_terms if len(term) > 2})


def phrase_like_match(term: str, lowered_text: str) -> bool:
    pattern = _phrase_pattern(term)
    return bool(pattern and pattern.search(lowered_text))


def _phrase_pattern(term: str) -> re.Pattern[str] | None:
    tokens = term_tokens(term)
    if len(tokens) < 2:
        return None
    pattern = r"(?<![a-z0-9])" + r"[\W_./:-]+".join(re.escape(token) for token in tokens) + r"(?![a-z0-9])"
    return re.compile(pattern)


def _profile_phrase_like_match(term: MatchTermProfile, lowered_text: str) -> bool:
    return bool(term.phrase_pattern and term.phrase_pattern.search(lowered_text))


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


def fuzzy_match_terms(
    text: str,
    intent: QueryIntent,
    *,
    limit: int = 8,
    profile: QueryMatchProfile | None = None,
    evidence: RecordMatchEvidence | None = None,
) -> list[dict[str, str]]:
    if evidence is not None and evidence.fuzzy_matches is not None:
        return evidence.fuzzy_matches[:limit]
    profile = profile or build_match_profile(intent)
    query_terms = list(profile.fuzzy_terms)
    if not query_terms:
        return []
    record_tokens = _tokens_for_text(text, evidence)
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
    if evidence is not None:
        evidence.fuzzy_matches = matches
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


def _tokens_for_text(text: str, evidence: RecordMatchEvidence | None) -> set[str]:
    if evidence is None:
        return token_set(text)
    if evidence.tokens is None:
        evidence.tokens = token_set(text)
    return evidence.tokens


def _lowered_for_text(text: str, evidence: RecordMatchEvidence | None) -> str:
    return evidence.lowered_text if evidence is not None else text.lower()


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
