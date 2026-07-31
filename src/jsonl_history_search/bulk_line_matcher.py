from __future__ import annotations

import time
import re
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Iterable

from .agent_contract import source_ref_target
from .records import parse_record
from .ranking import make_snippet
from .sources import SourcePlan, cap_hits_from_warnings, cap_labels_from_warnings, enumerate_jsonl_files, source_kind_for_path


VALIDATION_GUIDANCE = (
    "Bulk line matches are candidate leads, not final provenance. Validate selected "
    "source refs with semantic-debug, read-more, follow-next-reads, and audit-coverage "
    "before making provenance claims."
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9_./:+-]{1,}", re.IGNORECASE)
STOP_WORDS = {
    "the",
    "and",
    "that",
    "this",
    "with",
    "from",
    "your",
    "have",
    "what",
    "when",
    "where",
    "there",
    "here",
    "about",
}
FUZZY_RECALL_THRESHOLD = 0.65
STRONG_MATCH_MIN_INDEPENDENT_FRAGMENTS = 2


@dataclass(frozen=True)
class PhraseSpec:
    phrase_id: str
    text: str


@dataclass(frozen=True)
class PhraseFragment:
    phrase_id: str
    fragment_id: str
    text: str
    tokens: tuple[str, ...]
    anchors: tuple[str, ...]
    source: str


def load_phrase_specs(*, phrases: Iterable[str], phrases_file: Path | None = None) -> list[PhraseSpec]:
    rows: list[str] = []
    rows.extend(str(phrase).strip() for phrase in phrases if str(phrase).strip())
    if phrases_file is not None:
        loaded = phrases_file.expanduser().read_text(encoding="utf-8").splitlines()
        rows.extend(line.strip() for line in loaded if line.strip() and not line.lstrip().startswith("#"))
    return [PhraseSpec(phrase_id=f"phrase_{index:03d}", text=text) for index, text in enumerate(rows, start=1)]


def build_bulk_line_match_payload(
    *,
    plan: SourcePlan,
    phrases: list[PhraseSpec],
    case_sensitive: bool = False,
    snippet_chars: int = 260,
) -> dict:
    started = time.monotonic()
    warnings = list(plan.warnings)
    matches: list[dict] = []
    searched_files = 0
    searched_bytes = 0
    searched_lines = 0
    records_parsed = 0
    stopped = False
    max_matches = plan.limits.max_matches

    if not phrases:
        return {
            "bulk_line_match": {
                "schema_version": "bulk_line_match.v1",
                "status": "blocked",
                "reason": "no_phrases_supplied",
                "candidate_role": "leads_not_proof",
                "validation_guidance": VALIDATION_GUIDANCE,
                "phrase_count": 0,
                "phrases": [],
                "candidates": [],
                "searched_scope": _searched_scope(plan, warnings, searched_files, searched_bytes, searched_lines, records_parsed, stopped),
                "privacy": _privacy_contract(),
            }
        }

    fragments = _build_phrase_fragments(phrases)
    anchor_index = _anchor_index(fragments)
    phrase_by_id = {phrase.phrase_id: phrase for phrase in phrases}
    best_by_phrase: dict[str, dict] = {}
    for path, size, enum_warnings in enumerate_jsonl_files(plan, preferred_terms=[phrase.text for phrase in phrases]):
        warnings = list(enum_warnings)
        if time.monotonic() - started > plan.limits.max_elapsed:
            warnings.append("Stopped scanning at max elapsed cap.")
            stopped = True
            break
        searched_files += 1
        searched_bytes += size
        source_kind = source_kind_for_path(path, plan)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    searched_lines += 1
                    if time.monotonic() - started > plan.limits.max_elapsed:
                        warnings.append("Stopped scanning at max elapsed cap.")
                        stopped = True
                        break
                    line_tokens = set(_tokens(raw_line))
                    fragment_indexes = sorted(
                        {
                            index
                            for token in line_tokens
                            for index in anchor_index.get(token, ())
                        }
                    )
                    if not fragment_indexes:
                        continue
                    record = parse_record(raw_line, path=path, line=line_number, default_tz=timezone.utc)
                    by_phrase: dict[str, list[dict]] = {}
                    for fragment_index in fragment_indexes:
                        fragment = fragments[fragment_index]
                        fragment_match = _match_fragment(
                            fragment,
                            raw_line=raw_line,
                            parsed_text=record.text or "",
                            case_sensitive=case_sensitive,
                        )
                        if fragment_match is None:
                            continue
                        by_phrase.setdefault(fragment.phrase_id, []).append(fragment_match)
                    if not by_phrase:
                        continue
                    records_parsed += 1
                    for phrase_id, fragment_matches in sorted(by_phrase.items()):
                        phrase = phrase_by_id[phrase_id]
                        matches.append(
                            _candidate_row(
                                phrase=phrase,
                                record=record,
                                source_kind=source_kind,
                                fragment_matches=fragment_matches,
                                snippet_chars=snippet_chars,
                            )
                        )
                        best_by_phrase[phrase_id] = _better_phrase_result(
                            best_by_phrase.get(phrase_id),
                            matches[-1],
                        )
                        if len(matches) >= max_matches:
                            warnings.append("Final candidates were capped at max match cap.")
                            stopped = True
                            break
                    if stopped:
                        break
        except OSError as exc:
            warnings.append(f"Could not read {path}: {exc}")
        if stopped:
            break

    warnings = sorted(set(warnings + plan.warnings))
    return {
        "bulk_line_match": {
            "schema_version": "bulk_line_match.v1",
            "status": "ok" if matches else "no_matches",
            "candidate_role": "leads_not_proof",
            "validation_guidance": VALIDATION_GUIDANCE,
            "match_policy": {
                "strategy": "single_pass_anchor_shortlisted_fragment_phrase_scan_over_selected_jsonl_roots",
                "case_sensitive": case_sensitive,
                "fragment_policy": "source_phrases_split_to_line_and_token_window_fragments",
                "anchor_shortlisting": "candidate_jsonl_rows_compare_only_fragments_sharing_anchor_tokens",
                "fuzzy_token_recall_threshold": FUZZY_RECALL_THRESHOLD,
                "strong_match_min_independent_fragments": STRONG_MATCH_MIN_INDEPENDENT_FRAGMENTS,
                "no_embeddings": True,
                "no_network_or_upload": True,
                "final_provenance_written": False,
            },
            "phrase_count": len(phrases),
            "fragment_count": len(fragments),
            "phrases": [
                {
                    "phrase_id": phrase.phrase_id,
                    "text": phrase.text,
                    "fragment_count": sum(1 for fragment in fragments if fragment.phrase_id == phrase.phrase_id),
                }
                for phrase in phrases
            ],
            "phrase_results": [
                _phrase_result(phrase, best_by_phrase.get(phrase.phrase_id))
                for phrase in phrases
            ],
            "candidate_count": len(matches),
            "candidates": matches,
            "searched_scope": _searched_scope(
                plan,
                warnings,
                searched_files,
                searched_bytes,
                searched_lines,
                records_parsed,
                stopped,
            ),
            "validation_commands": [
                'jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text',
                'jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text',
                'jsonl-history-search follow-next-reads --from-report <semantic-debug.json> --cwd "$PWD" --format text',
                'jsonl-history-search audit-coverage --cwd "$PWD" --format text',
            ],
            "privacy": _privacy_contract(),
        }
    }


def _candidate_row(
    *,
    phrase: PhraseSpec,
    record,
    source_kind: str,
    fragment_matches: list[dict],
    snippet_chars: int,
) -> dict:
    text = record.text
    fragment_ids = sorted({str(item["fragment_id"]) for item in fragment_matches})
    independent_fragment_count = len(fragment_ids)
    field_set = {field for item in fragment_matches for field in item.get("matched_fields", [])}
    fields = [field for field in ("raw_line", "parsed_text") if field in field_set]
    best = max(fragment_matches, key=lambda item: (float(item.get("score") or 0), str(item.get("match_strength") or "")))
    exact_fragment = any(str(item.get("match_strength") or "").startswith("exact_") for item in fragment_matches)
    match_status = "strong_match" if exact_fragment or independent_fragment_count >= STRONG_MATCH_MIN_INDEPENDENT_FRAGMENTS else "possible_match"
    source_ref = {"path": record.path, "line": record.line, "source_kind": source_kind}
    return {
        "phrase_id": phrase.phrase_id,
        "phrase_text": phrase.text,
        "source_ref": source_ref,
        "target": source_ref_target(source_ref),
        "source_range": {"path": record.path, "start_line": record.line, "end_line": record.line},
        "source_kind": source_kind,
        "path": record.path,
        "line": record.line,
        "role": record.role or None,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "match_status": match_status,
        "match_strength": best.get("match_strength"),
        "score": best.get("score"),
        "independent_fragment_count": independent_fragment_count,
        "matched_fragments": fragment_matches,
        "matched_fields": fields,
        "snippet": _snippet(text, phrase.text, snippet_chars=snippet_chars),
    }


def _build_phrase_fragments(phrases: list[PhraseSpec]) -> list[PhraseFragment]:
    fragments: list[PhraseFragment] = []
    seen: set[tuple[str, str]] = set()
    for phrase in phrases:
        fragment_texts: list[tuple[str, str]] = []
        for line in phrase.text.splitlines() or [phrase.text]:
            normalized = " ".join(line.split())
            if normalized:
                fragment_texts.append(("line", normalized))
                tokens = _tokens(normalized)
                if len(tokens) > 8:
                    for start in range(0, len(tokens), 4):
                        window = tokens[start : start + 8]
                        if len(window) >= 3:
                            fragment_texts.append(("token_window", " ".join(window)))
        for source, text in fragment_texts:
            key = (phrase.phrase_id, text.casefold())
            if key in seen:
                continue
            seen.add(key)
            tokens = tuple(_tokens(text))
            if not tokens:
                continue
            anchors = tuple(_anchors(tokens))
            fragments.append(
                PhraseFragment(
                    phrase_id=phrase.phrase_id,
                    fragment_id=f"{phrase.phrase_id}_fragment_{len(fragments) + 1:03d}",
                    text=text,
                    tokens=tokens,
                    anchors=anchors,
                    source=source,
                )
            )
    return fragments


def _anchor_index(fragments: list[PhraseFragment]) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for fragment_index, fragment in enumerate(fragments):
        for anchor in fragment.anchors:
            index.setdefault(anchor, []).append(fragment_index)
    return index


def _match_fragment(
    fragment: PhraseFragment,
    *,
    raw_line: str,
    parsed_text: str,
    case_sensitive: bool,
) -> dict | None:
    raw_haystack = raw_line if case_sensitive else raw_line.casefold()
    parsed_haystack = parsed_text if case_sensitive else parsed_text.casefold()
    needle = fragment.text if case_sensitive else fragment.text.casefold()
    matched_fields = []
    if needle in raw_haystack:
        matched_fields.append("raw_line")
    if needle in parsed_haystack:
        matched_fields.append("parsed_text")
    if matched_fields:
        exact_case = fragment.text in raw_line or fragment.text in parsed_text
        strength = "exact_case_sensitive" if exact_case else "exact_case_insensitive"
        return {
            "fragment_id": fragment.fragment_id,
            "fragment_text": fragment.text,
            "fragment_source": fragment.source,
            "match_strength": strength,
            "score": 1.0 if strength == "exact_case_sensitive" else 0.9,
            "matched_fields": matched_fields,
            "token_recall": 1.0,
            "anchor_tokens": list(fragment.anchors),
        }

    haystack_tokens = set(_tokens(raw_line + "\n" + parsed_text))
    if not haystack_tokens:
        return None
    matched_tokens = [token for token in fragment.tokens if token in haystack_tokens]
    recall = len(set(matched_tokens)) / max(1, len(set(fragment.tokens)))
    if recall < FUZZY_RECALL_THRESHOLD:
        return None
    if len(set(fragment.tokens)) <= 2 and recall < 1.0:
        return None
    return {
        "fragment_id": fragment.fragment_id,
        "fragment_text": fragment.text,
        "fragment_source": fragment.source,
        "match_strength": "token_recall",
        "score": round(recall * 0.8, 3),
        "matched_fields": ["raw_line"],
        "token_recall": round(recall, 3),
        "matched_token_count": len(set(matched_tokens)),
        "fragment_token_count": len(set(fragment.tokens)),
        "anchor_tokens": list(fragment.anchors),
    }


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in WORD_RE.finditer(text)]


def _anchors(tokens: tuple[str, ...] | list[str]) -> list[str]:
    candidates = [token for token in tokens if len(token) >= 4 and token not in STOP_WORDS]
    if not candidates:
        candidates = [token for token in tokens if len(token) >= 2]
    return sorted(set(candidates), key=lambda token: (-len(token), token))[:4]


def _better_phrase_result(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    current_key = (
        current.get("match_status") == "strong_match",
        int(current.get("independent_fragment_count") or 0),
        float(current.get("score") or 0),
    )
    candidate_key = (
        candidate.get("match_status") == "strong_match",
        int(candidate.get("independent_fragment_count") or 0),
        float(candidate.get("score") or 0),
    )
    return candidate if candidate_key > current_key else current


def _phrase_result(phrase: PhraseSpec, best: dict | None) -> dict:
    if best is None:
        return {
            "phrase_id": phrase.phrase_id,
            "phrase_text": phrase.text,
            "match_status": "unmatched",
            "best_target": None,
            "independent_fragment_count": 0,
        }
    return {
        "phrase_id": phrase.phrase_id,
        "phrase_text": phrase.text,
        "match_status": best.get("match_status"),
        "best_target": best.get("target"),
        "best_source_ref": best.get("source_ref"),
        "best_source_range": best.get("source_range"),
        "best_score": best.get("score"),
        "independent_fragment_count": best.get("independent_fragment_count"),
    }


def _snippet(text: str, phrase: str, *, snippet_chars: int) -> str:
    if snippet_chars <= 0:
        return text
    snippet = make_snippet(text, [phrase])
    if len(snippet) <= snippet_chars:
        return snippet
    return snippet[: max(0, snippet_chars - 3)].rstrip() + "..."


def _searched_scope(
    plan: SourcePlan,
    warnings: list[str],
    searched_files: int,
    searched_bytes: int,
    searched_lines: int,
    records_parsed: int,
    stopped: bool,
) -> dict:
    return {
        "selected_roots": [root.to_dict() for root in plan.roots],
        "rejected_roots": [root.to_dict() for root in plan.rejected_roots],
        "limits": plan.limits.to_dict(),
        "warnings": sorted(set(warnings)),
        "blind_spots": sorted(set(plan.blind_spots)),
        "cap_hits": cap_hits_from_warnings(warnings),
        "cap_labels": cap_labels_from_warnings(warnings),
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "searched_lines": searched_lines,
        "records_parsed_for_matches": records_parsed,
        "stopped_early": stopped,
    }


def _privacy_contract() -> dict:
    return {
        "local_only": True,
        "network_upload": False,
        "writes_final_provenance": False,
        "candidate_rows_are_proof": False,
    }
