from __future__ import annotations

import time

from .adapters import AdapterReadError, iter_records_for_file
from .clustering import facets_for_record
from .masking import MaskContext
from .query import QueryIntent
from .ranking import Match, group_matches, is_weak_partial_generic, make_snippet, score_record, should_consider
from .rg_prefilter import filter_planned_files
from .sources import SourcePlan, enumerate_jsonl_files, source_kind_for_path


def search(
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None = None,
    mask_context: MaskContext | None = None,
    use_rg_prefilter: bool = False,
) -> dict:
    started = time.monotonic()
    warnings = list(plan.warnings)
    blind_spots = list(plan.blind_spots)
    matches: list[Match] = []
    weak_matches: list[Match] = []
    weak_reservoir_limit = max(1, min(plan.limits.max_matches, max(3, plan.limits.max_matches // 4)))
    weak_candidates_skipped = 0
    dedupe: set[tuple[str, str, str | None]] = set()
    searched_files = 0
    searched_bytes = 0
    stopped = False
    prefilter = None
    planned_files = []

    for enumerated in enumerate_jsonl_files(plan):
        path, size, enum_warnings = enumerated
        warnings = enum_warnings
        planned_files.append((path, size))

    if use_rg_prefilter:
        prefilter_result = filter_planned_files(plan=plan, intent=intent, files=planned_files, cwd=cwd)
        prefilter = prefilter_result.stats
        planned_files = [(item.path, item.size) for item in prefilter_result.files]

    for path, size in planned_files:
        if time.monotonic() - started > plan.limits.max_elapsed:
            warnings.append("Stopped before opening next file at max elapsed cap.")
            stopped = True
            break
        searched_files += 1
        searched_bytes += size
        source_kind = source_kind_for_path(path, plan)
        try:
            records = iter_records_for_file(path, source_kind, default_tz)
            for record in records:
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                if not should_consider(record, intent):
                    continue
                key = (" ".join(record.text.split()).lower(), record.raw_timestamp)
                if key in dedupe:
                    continue
                dedupe.add(key)
                score, reasons, artifacts = score_record(record, intent, cwd)
                if mask_context is not None:
                    copied_text = mask_context.mask_text(
                        record.text,
                        source_path=record.path,
                        source_index=record.line,
                    )
                else:
                    copied_text = record.text
                snippet_text = make_snippet(copied_text, intent.terms)
                facets = facets_for_record(path=record.path, text=record.text, artifacts=artifacts)
                match = Match(
                    path=record.path,
                    line=record.line,
                    role=record.role,
                    timestamp=record.timestamp.isoformat() if record.timestamp else None,
                    cwd=record.cwd,
                    snippet=snippet_text,
                    score=round(score, 3),
                    reasons=reasons,
                    artifacts=facets.artifacts,
                    session=facets.session,
                    tasks=facets.tasks,
                    preferences=facets.preferences,
                )
                if is_weak_partial_generic(reasons):
                    if len(weak_matches) < weak_reservoir_limit:
                        weak_matches.append(match)
                    else:
                        weakest_index = min(
                            range(len(weak_matches)),
                            key=lambda index: match_sort_key(weak_matches[index]),
                        )
                        if match_sort_key(match) > match_sort_key(weak_matches[weakest_index]):
                            weak_matches[weakest_index] = match
                        weak_candidates_skipped += 1
                    continue
                matches.append(match)
                if len(matches) >= plan.limits.max_matches:
                    warnings.append("Stopped scanning at max match cap.")
                    stopped = True
                    break
        except AdapterReadError as exc:
            warnings.append(str(exc))
            continue
        if stopped:
            break

    if weak_candidates_skipped:
        warnings.append("Weak partial generic candidates were reservoir-capped before final ranking.")
    matches = (matches + weak_matches)[: plan.limits.max_matches * 2]
    matches.sort(key=match_sort_key, reverse=True)
    matches = matches[: plan.limits.max_matches]
    payload = {
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "groups": group_matches(matches),
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(blind_spots)),
    }
    if prefilter is not None:
        payload["prefilter"] = prefilter
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload


def match_sort_key(match: Match) -> tuple[float, str, str, int]:
    return (match.score, match.timestamp or "", match.path, -match.line)
