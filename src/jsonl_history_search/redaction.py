from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Pattern


PROFILE_ALIASES = {
    "api-key-like": "api_keys",
    "api_key": "api_keys",
    "api_keys": "api_keys",
    "email": "emails",
    "emails": "emails",
    "phone": "phones",
    "phones": "phones",
}
REDACTION_MODES = {"production", "audit", "debug"}


@dataclass(frozen=True)
class RedactionRule:
    name: str
    pattern: Pattern[str]
    replacement_label: str
    validator: Callable[[str], bool] | None = None


@dataclass
class RedactionStats:
    mode: str
    profiles: list[str] = field(default_factory=list)
    redacted_total: int = 0
    debug_visible: int = 0
    counts_by_profile: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def record(self, profile: str, *, debug_visible: bool = False) -> None:
        if debug_visible:
            self.debug_visible += 1
            return
        self.redacted_total += 1
        self.counts_by_profile[profile] = self.counts_by_profile.get(profile, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "profiles": sorted(set(self.profiles)),
            "redacted_total": self.redacted_total,
            "debug_visible": self.debug_visible,
            "counts_by_profile": dict(sorted(self.counts_by_profile.items())),
            "warnings": list(self.warnings),
            "canonical_transcripts_mutated": False,
            "scope": "future_copied_context_snippets_only",
        }


@dataclass
class RedactionContext:
    rules: tuple[RedactionRule, ...] = ()
    stats: RedactionStats = field(default_factory=lambda: RedactionStats(mode="production"))

    @classmethod
    def disabled(cls, mode: str = "production") -> "RedactionContext":
        return cls(stats=RedactionStats(mode=_normalize_mode(mode)))

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any] | None, *, mode: str = "production") -> "RedactionContext":
        requested_mode = str(profile.get("mode", mode)) if isinstance(profile, Mapping) else mode
        normalized_mode = _normalize_mode(requested_mode)
        stats = RedactionStats(mode=normalized_mode)
        if requested_mode != normalized_mode:
            stats.warnings.append(f"Unknown redaction mode ignored: {requested_mode}")
        if profile is None:
            return cls(stats=stats)
        if not isinstance(profile, Mapping):
            stats.warnings.append("Redaction profile ignored: profile must be an object")
            return cls(stats=stats)

        profile_names = _profile_names(profile.get("profiles"), stats)
        rules = [_builtin_rule(name) for name in profile_names]
        rules.extend(_custom_rules(profile.get("custom_patterns"), stats))
        stats.profiles = [rule.name for rule in rules]
        if not rules:
            stats.warnings.append("Redaction profile has no usable rules")
        return cls(rules=tuple(rules), stats=stats)

    @classmethod
    def from_path(cls, path: Path, *, mode: str = "production") -> "RedactionContext":
        stats = RedactionStats(mode=_normalize_mode(mode))
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            stats.warnings.append(f"Redaction profile ignored: {exc}")
            return cls(stats=stats)
        context = cls.from_profile(raw, mode=mode)
        return context

    def redact_text(self, text: str) -> str:
        if not self.rules:
            return text
        matches = _collect_matches(text, self.rules)
        if self.stats.mode == "debug":
            for rule, _, _, _ in matches:
                self.stats.record(rule.name, debug_visible=True)
            return text

        rendered = text
        for rule, start, end, value in reversed(matches):
            rendered = rendered[:start] + _replacement(rule, value, mode=self.stats.mode) + rendered[end:]
            self.stats.record(rule.name)
        return rendered


def _profile_names(raw: Any, stats: RedactionStats) -> list[str]:
    if raw is None:
        return []
    values = raw if isinstance(raw, list) else [raw]
    names: list[str] = []
    for item in values:
        alias = PROFILE_ALIASES.get(str(item))
        if alias is None:
            stats.warnings.append(f"Unknown redaction profile ignored: {item}")
            continue
        if alias not in names:
            names.append(alias)
    return names


def _builtin_rule(name: str) -> RedactionRule:
    if name == "emails":
        return RedactionRule(
            name="emails",
            pattern=re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            replacement_label="email",
        )
    if name == "phones":
        return RedactionRule(
            name="phones",
            pattern=re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)"),
            replacement_label="phone",
            validator=_looks_phone_like,
        )
    return RedactionRule(
        name="api_keys",
        pattern=re.compile(
            r"\b(?:AKIA[0-9A-Z]{16}|"
            r"sk-[A-Za-z0-9_-]{20,}|"
            r"gh[pousr]_[A-Za-z0-9_]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}|"
            r"(?:api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{16,})\b",
            re.IGNORECASE,
        ),
        replacement_label="api_key",
    )


def _custom_rules(raw: Any, stats: RedactionStats) -> list[RedactionRule]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        stats.warnings.append("custom_patterns ignored: must be a list")
        return []
    rules: list[RedactionRule] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            name = f"custom_{index + 1}"
            pattern = item
        elif isinstance(item, Mapping):
            name = str(item.get("name") or f"custom_{index + 1}")
            pattern = item.get("pattern")
        else:
            stats.warnings.append(f"custom_patterns[{index}] ignored: must be a string or object")
            continue
        if not isinstance(pattern, str) or not pattern:
            stats.warnings.append(f"custom_patterns[{index}] ignored: pattern is required")
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            stats.warnings.append(f"custom_patterns[{index}] ignored: invalid regex: {exc}")
            continue
        rules.append(RedactionRule(name=f"custom:{name}", pattern=compiled, replacement_label=name))
    return rules


def _collect_matches(text: str, rules: tuple[RedactionRule, ...]) -> list[tuple[RedactionRule, int, int, str]]:
    candidates: list[tuple[int, int, int, RedactionRule, str]] = []
    for order, rule in enumerate(rules):
        for match in rule.pattern.finditer(text):
            value = match.group(0)
            if rule.validator is not None and not rule.validator(value):
                continue
            candidates.append((match.start(), -(match.end() - match.start()), order, rule, value))

    selected: list[tuple[RedactionRule, int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for start, negative_length, _, rule, value in sorted(candidates):
        end = start - negative_length
        if any(start < existing_end and end > existing_start for existing_start, existing_end in occupied):
            continue
        occupied.append((start, end))
        selected.append((rule, start, end, value))
    return selected


def _replacement(rule: RedactionRule, value: str, *, mode: str) -> str:
    if mode == "audit":
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        return f"[REDACTED:{rule.replacement_label}:len={len(value)}:sha256={digest}]"
    return f"[REDACTED:{rule.replacement_label}]"


def _looks_phone_like(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 9 <= len(digits) <= 15


def _normalize_mode(mode: str) -> str:
    if mode not in REDACTION_MODES:
        return "production"
    return mode
