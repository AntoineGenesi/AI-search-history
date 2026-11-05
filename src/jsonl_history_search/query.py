from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .expansion import SEMANTIC_LOCAL, expand_query_terms, task_slug_variants
from .timeutil import local_day_window, parse_datetime

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
RECAP_REQUEST_RE = re.compile(
    r'\b(?:what(?: the \w+)? (?:(?:has|have) )?(?:happened|changed)\b|'
    r'what (?:(?:have you|have we) (?:done|been doing)|did (?:you|we) do|has been (?:happening|going on))\b|catch me up\b|'
    r'summari[sz]e\b|recap\b|(?:give|provide) (?:me )?(?:a |an )?(?:recap|summary|update|progress update)\b|update me\b)')


@dataclass
class QueryPlan:
    schema_version: int
    primary_intent: str
    detected_intents: list[str]
    anchor_terms: list[str]
    field_hints: dict
    source_window_needs: list[str]
    privacy: dict

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "primary_intent": self.primary_intent,
            "detected_intents": self.detected_intents,
            "anchor_terms": self.anchor_terms,
            "field_hints": self.field_hints,
            "source_window_needs": self.source_window_needs,
            "privacy": self.privacy,
        }


@dataclass(frozen=True)
class RecordTimeInterval:
    """Exact caller bounds; event wording supplies no native identity proof."""
    start: datetime
    end: datetime
    start_inclusive: bool = True
    end_inclusive: bool = False
    named_event: bool = False

    def __post_init__(self):
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Recap interval bounds need an explicit timezone.")
        object.__setattr__(self, 'start', self.start.astimezone(timezone.utc))
        object.__setattr__(self, 'end', self.end.astimezone(timezone.utc))
        if self.end <= self.start:
            raise ValueError("Recap interval end must be later than its start.")

    def contains(self, timestamp):
        return timestamp is not None and (
            timestamp > self.start or self.start_inclusive and timestamp == self.start
        ) and (timestamp < self.end or self.end_inclusive and timestamp == self.end)

    def to_dict(self):
        result = {'type': 'record_time_interval', 'start': self.start.isoformat(),
                  'end': self.end.isoformat(), 'start_inclusive': self.start_inclusive,
                  'end_inclusive': self.end_inclusive, 'human_anchor_required': False,
                  'event_identity_verified': False}
        if self.named_event:
            result['named_event'] = True
        return result


@dataclass
class QueryIntent:
    original: str
    normalized: str
    terms: list[str]
    role: str | None = None
    role_filter_explicit: bool = False
    temporal_question_reason: str | None = None
    date_window_start: datetime | None = None
    date_window_end: datetime | None = None
    date_reason: str | None = None
    date_window_filter: bool = False
    artifact_hints: list[str] = field(default_factory=list)
    base_terms: list[str] = field(default_factory=list)
    expansion_reasons: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    query_plan: QueryPlan | None = None
    chronology_mode: str | None = None
    interval_origin: str | None = None
    record_time_interval: RecordTimeInterval | None = None

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "terms": self.terms,
            "base_terms": self.base_terms,
            "expansion_reasons": self.expansion_reasons,
            "role": self.role,
            "role_filter_explicit": self.role_filter_explicit,
            "temporal_question_reason": self.temporal_question_reason,
            "date_window": {
                "start": self.date_window_start.isoformat() if self.date_window_start else None,
                "end": self.date_window_end.isoformat() if self.date_window_end else None,
                "reason": self.date_reason,
                "filter": self.date_window_filter,
                **({'start_inclusive': self.record_time_interval.start_inclusive,
                    'end_inclusive': self.record_time_interval.end_inclusive}
                   if self.record_time_interval else {}),
            },
            "artifact_hints": self.artifact_hints,
            "warnings": self.warnings,
            "query_plan": self.query_plan.to_dict() if self.query_plan else None,
            "chronology_mode": self.chronology_mode,
            "interval_origin": self.interval_origin,
        }


def infer_query(
    query: str,
    *,
    now: datetime,
    role: str | None = None,
    date_window_start: datetime | None = None,
    date_window_end: datetime | None = None,
    date_reason: str | None = None,
) -> QueryIntent:
    normalized = " ".join(query.lower().split())
    recap = is_narrative_history_recap(query)
    interval_origin = None
    interval = None
    if recap:
        explicit = (RecordTimeInterval(date_window_start, date_window_end)
                    if date_window_start is not None and date_window_end is not None else None)
        natural = supplied_recap_window(query, now.tzinfo, explicit_interval=explicit)
        interval = natural or explicit
        if interval:
            date_window_start, date_window_end = interval.start, interval.end
            if natural is not None and explicit is None:
                date_reason = "explicit_prose_window"
            interval_origin = date_reason or "supplied_record_time_window"
    inferred_role = role or infer_role(normalized)
    role_filter_explicit = role is not None
    inferred_start, inferred_end, inferred_reason = infer_date_window(normalized, now)
    start = date_window_start if date_window_start is not None else inferred_start
    end = date_window_end if date_window_end is not None else inferred_end
    reason = date_reason if date_window_start is not None and date_window_end is not None else inferred_reason
    temporal_question_reason = infer_temporal_question(normalized)
    date_window_filter = bool(date_window_start is not None and date_window_end is not None) or infer_date_window_filter(
        normalized,
        reason,
    )
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
    query_plan = build_query_plan(
        normalized=normalized,
        base_terms=base_terms,
        artifact_hints=artifact_hints,
        role=inferred_role,
        temporal_question_reason=temporal_question_reason,
        date_reason=reason,
        date_window_start=start,
        date_window_end=end,
    )
    return QueryIntent(
        original=query,
        normalized=normalized,
        terms=terms,
        role=inferred_role,
        role_filter_explicit=role_filter_explicit,
        temporal_question_reason=temporal_question_reason,
        date_window_start=start,
        date_window_end=end,
        date_reason=reason,
        date_window_filter=date_window_filter,
        artifact_hints=artifact_hints,
        base_terms=base_terms,
        expansion_reasons=expanded.reasons,
        warnings=warnings,
        query_plan=query_plan,
        chronology_mode='supplied_interval' if interval_origin else 'latest_human' if is_direct_latest_message_history_query(query) else None,
        interval_origin=interval_origin,
        record_time_interval=interval,
    )


def build_query_plan(
    *,
    normalized: str,
    base_terms: list[str],
    artifact_hints: list[str],
    role: str | None,
    temporal_question_reason: str | None,
    date_reason: str | None,
    date_window_start: datetime | None,
    date_window_end: datetime | None,
) -> QueryPlan:
    detected: list[str] = []
    if re.search(r"\b(when\s+did\s+i\s+(?:say|tell|mention)|when\s+did\s+user\s+say|what\s+date\s+did\s+i\s+say)\b", normalized):
        detected.append("when_user_said")
    if re.search(
        r"\b(where\s+did\b.*\b(?:go\s+wrong|fail)|where\b.*\bworkflow\b.*\bwrong|went\s+wrong|"
        r"workflow\s+failure|failure|failed|regression|error|coordinator\s+rot|context\s+drift|"
        r"process\s+(?:focus\s+)?over\s+product|process\s+drift|wrong\s+layer|rabbit\s+hole|"
        r"rabbit\s+holes?|churn|stalled?|stalling)\b",
        normalized,
    ):
        detected.append("workflow_failure")
    if re.search(r"\b(why|reason|because|cause|caused|decision|decide|decided|rationale)\b", normalized):
        detected.append("why")
    if re.search(r"\b(what\s+was\s+i\s+talking\s+about|what\s+were\s+we\s+(?:talking\s+about|discussing)|talking\s+about\s+then|discussing\s+then)\b", normalized):
        detected.append("what_was_i_talking_about")
    if re.search(r"\b(source\s+refs?|next\s+reads?|open\s+next|what\s+should\s+i\s+open|where\s+should\s+i\s+read)\b", normalized):
        detected.append("source_next_reads")
    if temporal_question_reason and role == "user" and "when_user_said" not in detected:
        detected.append("when_user_said")
    if not detected:
        detected.append("generic_history")
    detected = _unique_ordered(detected)
    primary = detected[0]
    roles = _query_plan_roles(primary, role)
    field_hints = {
        "roles": roles,
        "timestamp": {
            "wanted": bool(temporal_question_reason or date_reason or primary in {"when_user_said", "workflow_failure"}),
            "window": {
                "start": date_window_start.isoformat() if date_window_start else None,
                "end": date_window_end.isoformat() if date_window_end else None,
                "reason": date_reason,
            },
        },
        "cwd_project": {"wanted": True},
        "tool_events": {"wanted": primary in {"why", "workflow_failure", "source_next_reads"}},
        "artifact_paths": list(artifact_hints)[:20],
        "source_refs": {"wanted": True},
    }
    return QueryPlan(
        schema_version=1,
        primary_intent=primary,
        detected_intents=detected,
        anchor_terms=list(base_terms)[:24],
        field_hints=field_hints,
        source_window_needs=_query_plan_source_window_needs(detected),
        privacy={
            "raw_query_text_included": False,
            "private_transcript_text_included": False,
        },
    )


def _query_plan_roles(primary_intent: str, role: str | None) -> list[str]:
    roles: list[str] = []
    if role:
        roles.append(role)
    if primary_intent == "when_user_said":
        roles.append("user")
    elif primary_intent == "workflow_failure":
        roles.extend(["assistant", "tool", "user"])
    elif primary_intent == "why":
        roles.extend(["assistant", "user", "tool"])
    return _unique_ordered(roles)


def _query_plan_source_window_needs(detected_intents: list[str]) -> list[str]:
    needs: list[str] = []
    for intent in detected_intents:
        if intent == "why":
            needs.append("decision_context")
        elif intent == "workflow_failure":
            needs.append("failure_timeline")
        elif intent == "when_user_said":
            needs.append("user_wording")
        elif intent == "what_was_i_talking_about":
            needs.append("topic_context")
        elif intent == "source_next_reads":
            needs.append("next_reads")
    if not needs:
        needs.append("candidate_context")
    return _unique_ordered(needs)


def infer_temporal_question(normalized: str) -> str | None:
    if re.search(
        r"\bwhen\s+(?:did|do|does|was|were|is|are|had|has|have|will|would|can|could|should|"
        r"the|this|that|it|they|we|you|i|agent|assistant|user|task|case|session|conversation|decision|"
        r"run|review|implementation|change|file|path|report|artifact|commit|branch|worktree)\b",
        normalized,
    ):
        return "when_question"
    if re.search(r"\bwhat\s+(?:date|day|time)\b", normalized):
        return "what_date_time_question"
    if re.search(r"\bwhich\s+(?:date|day|time)\b", normalized):
        return "which_date_time_question"
    return None


def infer_role(normalized: str) -> str | None:
    if re.search(r"\b(i said|i told you|what i told|my message|user said)\b", normalized):
        return "user"
    if re.search(
        r"\b(you said|you decided|your plan|assistant said|agent said|did you|did the agent|agent do|you do|you run|you create|you implement)\b",
        normalized,
    ):
        return "assistant"
    return None


def _recap_text(query):
    # Classify only; native request identity always receives the original text.
    text = ' '.join(query.lower().replace('’', "'").split())
    text = re.sub(r"\b(you|we|what)'ve\b", r'\1 have', text)
    text = re.sub(r"\bwhat's\b", 'what has', text)
    recap = RECAP_REQUEST_RE.search(text)
    if not recap:
        return text, False
    # Conversational framing and a target named before the recap are allowed;
    # an implementation/causal question about recaps is a different operation.
    prefix = text[:recap.start()]
    if re.search(r'\b(?:why|how|implement|debug|fix|test|code|routing|predicate)\b', prefix):
        return text, False
    return text, True


def is_narrative_history_recap(query):
    text, recap = _recap_text(query)
    # A request to summarize a cause still asks for topical evidence.
    return recap and not re.search(r'\b(?:why|how)\b', text)


def _unquoted_recap_text(query):
    """Mask classification spans only; the original query remains authoritative."""
    text = list(query)
    delimiters = {'"': '"', "'": "'", '“': '”', '‘': '’', '`': '`'}
    start, closing, escaped = None, None, False
    for index, character in enumerate(query):
        if escaped:
            escaped = False
            continue
        if closing and character == '\\':
            escaped = True
            continue
        before_word = index > 0 and (query[index - 1].isalnum() or query[index - 1] == '_')
        after_word = index + 1 < len(query) and (query[index + 1].isalnum() or query[index + 1] == '_')
        if character in {"'", '’'} and before_word and after_word:
            continue
        if closing is None:
            if character in delimiters and not (character == "'" and before_word):
                start, closing = index, delimiters[character]
        elif character == closing:
            text[start:index + 1] = ' ' * (index + 1 - start)
            start, closing = None, None
    return ''.join(text), closing is not None


def supplied_recap_window(query, default_tz, *, explicit_interval=None):
    """Resolve requested record-time relations without authenticating named events.

    Independently asserted endpoints must agree with CLI bounds even when the
    other prose endpoint is unknown. Quotes supply content, not caller bounds.
    """
    text, unclosed_quote = _unquoted_recap_text(query)
    date = r'\d{4}-\d{2}-\d{2}'
    clock = r'[Tt ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[+-]\d{2}:\d{2})?'
    instant = date + clock
    value = r'(' + date + r'(?:' + clock + r')?)(?![\w:+-]|[.,]\d)'
    relation = r'\b(from|since|after|until|before|through|between|to)(?:\s+|(?=\d))'
    named_relation = r'\b(from|since|after)\s+(?!' + date + r')([^.!?;,\n]+?)\s+at\s*'
    direct = list(re.finditer(relation + value, text, re.I))
    named = list(re.finditer(named_relation + value, text, re.I))
    attempted = list(re.finditer(relation + date, text, re.I)) + list(re.finditer(named_relation + date, text, re.I))
    recap_text, _ = _recap_text(text)
    requested_event = any(re.match(r'\s+(?:after|since)\s+\S', recap_text[match.end():])
                          for match in RECAP_REQUEST_RE.finditer(recap_text))
    relative_endpoint = re.search(relation + r'(?:today|yesterday|tomorrow|(?:last|next|this)\s+(?:day|night|week|month|year|morning|evening)|\d{1,2}:\d{2})\b', text, re.I)
    if unclosed_quote and attempted:
        raise ValueError("Unclosed quoted text leaves recap bounds ambiguous; close the quote or state unquoted record-time bounds.")
    # A documentary date is not a request for activity during that date range.
    if not attempted:
        if is_direct_latest_message_history_query(query) or not (requested_event or relative_endpoint):
            return None
    if re.search(r'\b(?:published|dated|titled)\s*$', text[:min(
            [m.start() for m in direct + named], default=len(text))], re.I):
        return None
    endpoints = [(m.start(), m.end(), m[1].lower(), m[2]) for m in direct]
    endpoints += [(m.start(), m.end(), m[1].lower(), m[3]) for m in named]
    endpoints.sort()
    if {m.start() for m in attempted} != {item[0] for item in endpoints}:
        raise ValueError("Unsupported recap timestamp spelling; supply complete ISO timestamps and explicit offsets without trailing zone names or extra precision.")
    if any(op in {'between', 'to'} for _, _, op, _ in endpoints) or re.search(r'\bbetween\s+', text, re.I):
        raise ValueError("Unresolved recap interval: use from/since/after <ISO timestamp> until/before/through <ISO timestamp>, with explicit endpoint meaning.")
    lower = [item for item in endpoints if item[2] in {'from', 'since', 'after'}]
    upper = [item for item in endpoints if item[2] in {'until', 'before', 'through'}]
    if len(lower) > 1 or len(upper) > 1:
        raise ValueError("Competing recap endpoints are unresolved; provide one interval.")
    endpoint_scope = r'(?:both|each|either|neither|all)'
    endpoint_noun = (r'(?:(?:the|this|that|' + endpoint_scope + r')\s+)?'
                     r'(?:(?:lower|upper|first|last|initial|final)\s+)?'
                     r'(?:(?:start|end)(?:ing)?(?:\s+point)?s?|endpoints?|bounds?|boundar(?:y|ies)|dates?|timestamps?|instants?)\b')
    endpoint_subject = r'(?:' + endpoint_noun + '|' + endpoint_scope + r'|this|that)'
    disposition = r'(?:(?:inclusive|exclusive)(?:ly)?|included|excluded)\b'
    qualification = (r'(?:' + endpoint_subject + r'\s+)?(?:(?:is|are|being)\s+)?'
                     r'(?:' + endpoint_scope + r'\s+)?' + disposition)
    inclusion = (r'(?:' + qualification + r'|(?:including|excluding)\s+(?:'
                 + endpoint_noun + '|' + date + '))')
    explicit_qualification = (r'(?:' + endpoint_subject + r'\s+(?:(?:is|are|being)\s+)?' + disposition
                              + '|' + disposition + r'\s+' + endpoint_noun
                              + r'|(?:including|excluding)\s+(?:' + endpoint_noun + '|' + date + '))')
    if re.search(explicit_qualification, text, re.I):
        raise ValueError("Qualified recap endpoint inclusion is unresolved; state one consistent interval.")
    for _, end, _, _ in endpoints:
        suffix = text[end:]
        if re.match(r'\s*,?\s*(?:or|and)\s+' + date, suffix, re.I):
            raise ValueError("Competing recap endpoints are unresolved; provide one interval.")
        if re.match(r'\s*(?:[,;]\s*)?(?:\(\s*)?(?:(?:and|with)\s+)?' + inclusion, suffix, re.I):
            raise ValueError("Qualified recap endpoint inclusion is unresolved; state one consistent interval without extra inclusion qualifiers.")
    if re.search(relation + r'(?:(?:and\s+)?(?:including|excluding)|inclusive|exclusive)\b', text, re.I):
        raise ValueError("Qualified recap endpoint inclusion is unresolved; state one consistent interval.")
    if relative_endpoint:
        raise ValueError("Relative recap endpoints are unresolved; supply explicit ISO timestamps and offsets.")
    parsed = {}
    for _, _, op, source in endpoints:
        if not re.fullmatch(instant, source, re.I):
            raise ValueError("Recap bounds need full ISO clock timestamps; date-only or relative endpoints are unresolved.")
        if re.search(r'\.\d{7,}', source):
            raise ValueError("Recap timestamps support at most six fractional digits; provide exact supported bounds without rounding.")
        if not re.search(r'(?:Z|[+-]\d{2}:\d{2})$', source, re.I):
            raise ValueError("Each recap prose timestamp must contain Z or an explicit ISO offset; timezone options do not supply prose endpoint offsets.")
        timestamp = parse_datetime(source.replace('t', 'T').replace('z', 'Z'), default_tz)
        if timestamp is None:
            raise ValueError("Recap interval needs valid ISO timestamps with end later than start.")
        inclusive = op in {'from', 'since', 'through'}
        is_lower = op in {'from', 'since', 'after'}
        parsed['start' if is_lower else 'end'] = (timestamp, inclusive)
        if explicit_interval and (timestamp != (explicit_interval.start if is_lower else explicit_interval.end)
                or inclusive != (explicit_interval.start_inclusive if is_lower else explicit_interval.end_inclusive)):
            raise ValueError("Recap prose bounds or inclusion operators conflict with --since/--until; provide one consistent interval.")
    if lower and upper:
        if lower[0][1] >= upper[0][0] or text[lower[0][1]:upper[0][0]].strip():
            raise ValueError("Unresolved recap interval: provide one adjacent lower and upper ISO relation.")
        start, start_inclusive = parsed['start']
        end, end_inclusive = parsed['end']
        return RecordTimeInterval(start, end, start_inclusive, end_inclusive, bool(named))
    if explicit_interval:
        # A named but untimed event can coexist with explicit record-time bounds;
        # any independently supplied prose constraint was checked above.
        if requested_event and not lower:
            return RecordTimeInterval(explicit_interval.start, explicit_interval.end,
                                      explicit_interval.start_inclusive, explicit_interval.end_inclusive, True)
        return explicit_interval
    raise ValueError("Unresolved recap interval: supply explicit ISO start and end with from/since/after and until/before/through, or --since/--until. Event names alone do not establish record-time bounds.")


def is_direct_latest_message_history_query(query):
    text, recap = _recap_text(query)
    if not recap:
        return False
    latest = r'(?:last|latest|most recent|previous)'
    human = r'(?:(?:genuine(?:ly)?|actual|real|human(?:-authored)?|user)\s+)*'
    message = r'(?:message|turn|instruction|prompt)'
    target = (r'(?:(?:the|this|that|my|your|our|its|their)\s+)?'
              r'(?:(?:parent|main|current)\s+)?(?:thread|session|conversation|chat)')
    owner = r'(?:(?:my|the|its|their|your|our)\s+|' + target + r"'s\s+)?"
    boundary = re.search(
        r'\b(?:since|after)\s+(?:' + owner + latest + r'\s+' + human + message +
        r'\b|i (?:last (?:messaged|asked|wrote|spoke|(?:sent|posted) (?:a|my) message)|sent my ' + latest + r' message)\b|'
        r'(?:the )?last time i (?:messaged|asked|wrote|spoke)\b)|\bwhile i was away\b', text)
    return bool(boundary)


def infer_date_window(normalized: str, now: datetime):
    if is_direct_latest_message_history_query(normalized):
        return None, None, None
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
