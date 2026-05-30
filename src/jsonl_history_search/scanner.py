from __future__ import annotations

import time

from .query import QueryIntent
from .ranking import Match, group_matches, make_snippet, score_record, should_consider
from .records import parse_record
from .sources import SourcePlan, enumerate_jsonl_files
from .masking import MaskContext


def search(
    plan: SourcePlan,
    intent: QueryIntent,
    *,
    default_tz,
    cwd: str | None = None,
    mask_context: MaskContext | None = None,
) -> dict:
    started = time.monotonic()
    warnings = list(plan.warnings)
    blind_spots = list(plan.blind_spots)
    matches: list[Match] = []
    dedupe: set[tuple[str, str, str | None]] = set()
    searched_files = 0
    searched_bytes = 0
    stopped = False

    for enumerated in enumerate_jsonl_files(plan):
        if time.monotonic() - started > plan.limits.max_elapsed:
            warnings.append("Stopped before opening next file at max elapsed cap.")
            stopped = True
            break
        path, size, enum_warnings = enumerated
        warnings = enum_warnings
        searched_files += 1
        searched_bytes += size
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"Could not open {path}: {exc}")
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                if time.monotonic() - started > plan.limits.max_elapsed:
                    warnings.append("Stopped scanning at max elapsed cap.")
                    stopped = True
                    break
                record = parse_record(line, path=path, line=line_number, default_tz=default_tz)
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
                matches.append(
                    Match(
                        path=record.path,
                        line=record.line,
                        role=record.role,
                        timestamp=record.timestamp.isoformat() if record.timestamp else None,
                        cwd=record.cwd,
                        snippet=snippet_text,
                        score=round(score, 3),
                        reasons=reasons,
                        artifacts=artifacts,
                    )
                )
                if len(matches) >= plan.limits.max_matches:
                    warnings.append("Stopped scanning at max match cap.")
                    stopped = True
                    break
            if stopped:
                break

    matches.sort(key=lambda item: (item.score, item.timestamp or "", item.path, -item.line), reverse=True)
    payload = {
        "searched_files": searched_files,
        "searched_bytes": searched_bytes,
        "groups": group_matches(matches),
        "warnings": sorted(set(warnings + plan.warnings)),
        "blind_spots": sorted(set(blind_spots)),
    }
    if mask_context is not None and mask_context.stats is not None:
        payload["masking"] = mask_context.stats.to_dict()
        payload["warnings"] = sorted(set(payload["warnings"] + mask_context.stats.warnings))
    return payload
