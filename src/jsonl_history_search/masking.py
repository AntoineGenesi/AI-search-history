from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple


MASK_MODES = {"production_collapse", "production_omit", "debug_full"}
MASKABLE_OUTCOMES = {"detector_false_positive", "false_positive"}
NON_MASKABLE_OUTCOMES = {
    "accepted_true_positive",
    "true_positive",
    "useful_but_wrong_action",
    "uncertain",
    "unknown",
    "stale_ack",
}
VALID_ACK_STATUSES = {
    "false_positive",
    "accepted_true_positive",
    "useful_but_wrong_action",
    "uncertain",
    "stale",
    "unknown",
}


class MaskManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Span:
    source_path: str
    index: int
    field: str = "text"
    start: Optional[int] = None
    end: Optional[int] = None
    kind: str = "span"


@dataclass(frozen=True)
class Entry:
    intervention_id: str
    label: str
    framework: str
    reason: str
    semantic_outcome: str
    ack_status: str
    stale_ack: bool
    production_mode: str
    spans: Tuple[Span, ...]

    def maskable(self) -> bool:
        return (
            self.semantic_outcome in MASKABLE_OUTCOMES
            and self.ack_status == "false_positive"
            and not self.stale_ack
        )


@dataclass
class MaskStats:
    mode: str
    manifest_path: Optional[str] = None
    active_context_masked: int = 0
    active_context_collapsed: int = 0
    transcript_retained: int = 0
    debug_visible: int = 0
    skipped: int = 0
    missing_spans: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "manifest_path": self.manifest_path,
            "active_context_masked": self.active_context_masked,
            "active_context_collapsed": self.active_context_collapsed,
            "transcript_retained": self.transcript_retained,
            "debug_visible": self.debug_visible,
            "skipped": self.skipped,
            "missing_spans": self.missing_spans,
            "warnings": list(self.warnings),
            "canonical_transcripts_mutated": False,
            "scope": "future_copied_context_snippets_only",
            "model_input_stripped": self.mode != "debug_full" and self.active_context_masked > 0,
        }


@dataclass
class MaskContext:
    mode: str
    manifest_path: Optional[Path] = None
    entries: Tuple[Entry, ...] = ()
    stats: Optional[MaskStats] = None

    @classmethod
    def disabled(cls, mode: str = "debug_full") -> "MaskContext":
        return cls(mode=mode, stats=MaskStats(mode=mode))

    @classmethod
    def from_path(cls, path: Optional[Path], mode: str) -> "MaskContext":
        if mode not in MASK_MODES:
            raise ValueError(f"mask mode must be one of {sorted(MASK_MODES)}")
        stats = MaskStats(mode=mode, manifest_path=str(path) if path else None)
        if path is None:
            return cls(mode=mode, stats=stats)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = tuple(_load_entries(raw))
        except Exception as exc:
            stats.warnings.append(f"Mask manifest ignored: {exc}")
            return cls(mode=mode, manifest_path=path, stats=stats)
        return cls(mode=mode, manifest_path=path, entries=entries, stats=stats)

    def mask_text(self, text: str, *, source_path: str, source_index: int) -> str:
        if self.stats is None:
            self.stats = MaskStats(mode=self.mode, manifest_path=str(self.manifest_path) if self.manifest_path else None)
        if not self.entries:
            return text
        if self.mode == "debug_full":
            for entry in self.entries:
                for span in entry.spans:
                    if _same_source(span, source_path, source_index):
                        self.stats.debug_visible += 1
                        self.stats.transcript_retained += 1
            return text

        replacements: List[Tuple[int, int, str, Entry]] = []
        for entry in self.entries:
            for span in entry.spans:
                if not _same_source(span, source_path, source_index):
                    continue
                if not entry.maskable():
                    self.stats.skipped += 1
                    continue
                bounds = _bounds(span, text)
                if bounds is None:
                    self.stats.missing_spans += 1
                    continue
                start, end = bounds
                replacement = "" if self.mode == "production_omit" else _placeholder(entry, span)
                replacements.append((start, end, replacement, entry))

        rendered = text
        for start, end, replacement, entry in sorted(replacements, key=lambda item: item[0], reverse=True):
            rendered = rendered[:start] + replacement + rendered[end:]
            self.stats.active_context_masked += 1
            self.stats.transcript_retained += 1
            if self.mode == "production_collapse":
                self.stats.active_context_collapsed += 1
        return rendered


def _load_entries(raw: Mapping[str, Any]) -> List[Entry]:
    if not isinstance(raw, Mapping):
        raise MaskManifestError("manifest must be an object")
    if raw.get("schema_version") != 1:
        raise MaskManifestError("schema_version must be 1")
    interventions = raw.get("interventions")
    if isinstance(interventions, Mapping):
        items = interventions.items()
    elif isinstance(raw.get("entries"), list):
        items = [("", item) for item in raw["entries"]]
    else:
        raise MaskManifestError("manifest must contain interventions or entries")
    entries = [_load_entry(str(key), value) for key, value in items]
    if not entries:
        raise MaskManifestError("manifest has no entries")
    return entries


def _load_entry(key: str, raw: Any) -> Entry:
    if not isinstance(raw, Mapping):
        raise MaskManifestError(f"entry {key!r} must be an object")
    intervention_id = _required_str(raw, "intervention_id")
    if key and key != intervention_id:
        raise MaskManifestError(f"entry key {key!r} does not match intervention_id")
    transcript_path = _required_str(raw, "transcript_path")
    ack_path = _required_str(raw, "ack_path")
    transcript_index = _required_int(raw, "transcript_index")
    ack_index = _required_int(raw, "ack_index")
    policy = raw.get("masking_policy")
    if not isinstance(policy, Mapping):
        raise MaskManifestError("masking_policy must be an object")
    production_mode = str(policy.get("production_mode") or "")
    if production_mode not in {"omit", "collapse"}:
        raise MaskManifestError("masking_policy.production_mode must be omit or collapse")
    if policy.get("mask_intervention") is not True:
        raise MaskManifestError("masking_policy.mask_intervention must be true")
    if policy.get("mask_ack") is not True:
        raise MaskManifestError("masking_policy.mask_ack must be true")
    semantic_outcome = str(raw.get("semantic_outcome") or "detector_false_positive")
    ack_status = str(raw.get("ack_status") or "false_positive")
    if semantic_outcome not in MASKABLE_OUTCOMES | NON_MASKABLE_OUTCOMES:
        raise MaskManifestError(f"unsupported semantic_outcome: {semantic_outcome}")
    if ack_status not in VALID_ACK_STATUSES:
        raise MaskManifestError(f"unsupported ack_status: {ack_status}")
    _validate_debug_policy(raw.get("debug_policy"))
    _validate_evidence_retention(raw.get("evidence_retention"))
    spans = (
        _load_span(raw.get("intervention_span"), transcript_path, transcript_index, "intervention"),
        _load_span(raw.get("ack_span"), ack_path, ack_index, "ack"),
    )
    return Entry(
        intervention_id=intervention_id,
        label=_required_str(raw, "label"),
        framework=_required_str(raw, "framework"),
        reason=str(raw.get("reason") or ""),
        semantic_outcome=semantic_outcome,
        ack_status=ack_status,
        stale_ack=bool(raw.get("stale_ack", False)),
        production_mode=production_mode,
        spans=spans,
    )


def _load_span(raw: Any, fallback_path: str, fallback_index: int, kind: str) -> Span:
    data = raw if isinstance(raw, Mapping) else {}
    index = data.get("index", fallback_index)
    if not isinstance(index, int):
        raise MaskManifestError(f"{kind}_span.index must be an integer")
    field = str(data.get("field") or "text")
    if not field:
        raise MaskManifestError(f"{kind}_span.field is required")
    start = data.get("start")
    end = data.get("end")
    if (start is None) != (end is None):
        raise MaskManifestError(f"{kind}_span start and end must appear together")
    if start is not None and (not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start):
        raise MaskManifestError(f"{kind}_span has invalid offsets")
    return Span(
        source_path=str(data.get("path") or fallback_path),
        index=index,
        field=field,
        start=start,
        end=end,
        kind=kind,
    )


def _validate_debug_policy(raw: Any) -> None:
    if not isinstance(raw, Mapping):
        raise MaskManifestError("debug_policy must be an object")
    if raw.get("mode") != "debug_full":
        raise MaskManifestError("debug_policy.mode must be debug_full")
    if raw.get("retain_full_text") is not True:
        raise MaskManifestError("debug_policy.retain_full_text must be true")


def _validate_evidence_retention(raw: Any) -> None:
    if not isinstance(raw, Mapping):
        raise MaskManifestError("evidence_retention must be an object")
    if raw.get("status") != "retained":
        raise MaskManifestError("evidence_retention.status must be retained")
    if raw.get("canonical_transcript_retained") is not True:
        raise MaskManifestError("evidence_retention.canonical_transcript_retained must be true")
    compact_fields = raw.get("compact_evidence_fields")
    if not isinstance(compact_fields, list) or not compact_fields:
        raise MaskManifestError("evidence_retention.compact_evidence_fields must be a non-empty list")


def _required_str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MaskManifestError(f"{key} is required")
    return value


def _required_int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise MaskManifestError(f"{key} must be an integer")
    return value


def _same_source(span: Span, source_path: str, source_index: int) -> bool:
    return span.source_path == source_path and span.index == source_index and span.field == "text"


def _bounds(span: Span, text: str) -> Optional[Tuple[int, int]]:
    if span.start is None and span.end is None:
        return 0, len(text)
    assert span.start is not None
    assert span.end is not None
    if span.end > len(text):
        return None
    return span.start, span.end


def _placeholder(entry: Entry, span: Span) -> str:
    reason = " ".join(entry.reason.split())[:96]
    return (
        "[Semantic Linter false-positive masked: "
        f"intervention_id={entry.intervention_id} label={entry.label} "
        f"framework={entry.framework} span={span.kind} ack_status={entry.ack_status} "
        f"reason={reason} transcript_retained=true]"
    )
