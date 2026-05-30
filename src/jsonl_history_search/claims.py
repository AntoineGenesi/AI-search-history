from __future__ import annotations

import re
from dataclasses import asdict, dataclass


CLAIMED = "claimed"
CHANGED = "changed"
VERIFIED = "verified"
MERGED = "merged"
UNSUPPORTED = "unsupported"

CLAIM_ACTIONS = {CLAIMED, CHANGED, VERIFIED, MERGED, UNSUPPORTED}

_MERGED_PATTERNS = [
    re.compile(r"\b(?:merged|merge commit|fast-forward(?:ed)?|landed)\b", re.IGNORECASE),
]
_VERIFIED_PATTERNS = [
    re.compile(r"\b(?:verified|validated|reviewed)\b", re.IGNORECASE),
    re.compile(r"\b(?:tests?|pytest|git diff --check|lint|typecheck)\s+(?:passes|passed|clean|ok)\b", re.IGNORECASE),
    re.compile(r"\b(?:passes|passed)\s+(?:tests?|pytest|git diff --check|lint|typecheck)\b", re.IGNORECASE),
]
_CHANGED_PATTERNS = [
    re.compile(
        r"\b(?:added|changed|created|deleted|edited|fixed|implemented|modified|patched|removed|updated|wrote)\b",
        re.IGNORECASE,
    ),
]
_CLAIMED_PATTERNS = [
    re.compile(r"\b(?:done|complete|completed|finished|ready)\b", re.IGNORECASE),
    re.compile(r"\bI\s+(?:handled|finished|completed)\b", re.IGNORECASE),
]
_NEGATION_RE = re.compile(r"\b(?:cannot|didn't|failed|no|not|never|unverified|without)\b", re.IGNORECASE)

_VERDICT_LABEL_RE = re.compile(
    r"""
    ^\s*
    (?:[-*]\s*)?
    (?:\*\*)?
    (?:(?:review|final)\s+)?
    (?:verdict|result|status)
    (?:\*\*)?
    \s*:\s*
    (?P<value>[A-Za-z][A-Za-z _-]*)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_STANDALONE_VERDICT_RE = re.compile(
    r"^\s*(?P<value>VERIFIED|APPROVED|PASS|PASSED|FAILED|FAIL|REJECTED|BLOCKED|MERGED|UNSUPPORTED)\s*$"
)

_VERDICT_ALIASES = {
    "approved": VERIFIED,
    "pass": VERIFIED,
    "passed": VERIFIED,
    "verified": VERIFIED,
    "merged": MERGED,
    "blocked": UNSUPPORTED,
    "fail": UNSUPPORTED,
    "failed": UNSUPPORTED,
    "needs work": UNSUPPORTED,
    "needs_work": UNSUPPORTED,
    "rejected": UNSUPPORTED,
    "unsupported": UNSUPPORTED,
}


@dataclass(frozen=True)
class ClaimClassification:
    action: str
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewVerdict:
    action: str
    verdict: str
    line: str

    def to_dict(self) -> dict:
        return asdict(self)


def classify_claim_action(text: str) -> ClaimClassification:
    verdict = parse_review_verdict(text)
    if verdict is not None:
        return ClaimClassification(verdict.action, f"review-verdict:{verdict.verdict}")

    for action, patterns in (
        (MERGED, _MERGED_PATTERNS),
        (VERIFIED, _VERIFIED_PATTERNS),
        (CHANGED, _CHANGED_PATTERNS),
        (CLAIMED, _CLAIMED_PATTERNS),
    ):
        if _has_positive_match(text, patterns):
            return ClaimClassification(action, f"text:{action}")
    return ClaimClassification(UNSUPPORTED, "no supported claim/action signal")


def parse_review_verdict(text: str) -> ReviewVerdict | None:
    for raw_line in text.splitlines():
        line = raw_line.strip().replace("**", "")
        if not line:
            continue
        match = _VERDICT_LABEL_RE.match(line) or _STANDALONE_VERDICT_RE.match(line)
        if match is None:
            continue
        normalized = _normalize_verdict(match.group("value"))
        if normalized is None:
            continue
        return ReviewVerdict(action=normalized, verdict=match.group("value").strip(), line=line)
    return None


def _normalize_verdict(value: str) -> str | None:
    collapsed = " ".join(value.replace("-", " ").replace("_", " ").lower().split())
    return _VERDICT_ALIASES.get(collapsed) or _VERDICT_ALIASES.get(collapsed.replace(" ", "_"))


def _has_positive_match(text: str, patterns: list[re.Pattern[str]]) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            if not _is_negated(text, match.start()):
                return True
    return False


def _is_negated(text: str, start: int) -> bool:
    window = text[max(0, start - 32) : start]
    return bool(_NEGATION_RE.search(window))
