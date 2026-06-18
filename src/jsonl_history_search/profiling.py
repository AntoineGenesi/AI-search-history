from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover - exercised on platforms without resource
    resource = None  # type: ignore[assignment]


@dataclass(frozen=True)
class PhaseToken:
    name: str
    started: float
    rss_start_max_bytes: int | None
    rss_unavailable_reason: str | None


def start_phase_profile(name: str) -> PhaseToken:
    rss_start, reason = _rss_max_bytes()
    return PhaseToken(
        name=name,
        started=time.perf_counter(),
        rss_start_max_bytes=rss_start,
        rss_unavailable_reason=reason,
    )


def finish_phase_profile(token: PhaseToken, **counts: int | bool) -> dict[str, Any]:
    rss_end, end_reason = _rss_max_bytes()
    unavailable_reason = token.rss_unavailable_reason or end_reason
    rss_available = token.rss_start_max_bytes is not None and rss_end is not None
    profile: dict[str, Any] = {
        "phase": token.name,
        "elapsed_ms": round((time.perf_counter() - token.started) * 1000, 3),
        "rss_available": rss_available,
        "rss_units": "bytes",
        "rss_start_max_bytes": token.rss_start_max_bytes,
        "rss_end_max_bytes": rss_end,
        "rss_max_bytes": rss_end,
        "rss_delta_bytes": (rss_end - token.rss_start_max_bytes) if rss_available else None,
        "rss_unavailable_reason": None if rss_available else (unavailable_reason or "rss_unavailable"),
        "rss_current_bytes": None,
        "rss_current_unavailable_reason": "resource_ru_maxrss_reports_process_peak_not_current",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "line_numbers_included": False,
        "session_ids_included": False,
        "vectors_or_persisted_indexes_included": False,
    }
    for key, value in counts.items():
        if isinstance(value, bool):
            profile[key] = value
        else:
            profile[key] = int(value)
    return profile


def _rss_max_bytes() -> tuple[int | None, str | None]:
    if resource is None:
        return None, "resource_module_unavailable"
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF)
    except Exception as exc:  # pragma: no cover - defensive platform guard
        return None, type(exc).__name__
    raw_value = getattr(usage, "ru_maxrss", None)
    if raw_value is None:
        return None, "ru_maxrss_unavailable"
    try:
        rss = int(raw_value)
    except (TypeError, ValueError):
        return None, "ru_maxrss_not_integer"
    if rss < 0:
        return None, "ru_maxrss_negative"
    if sys.platform == "darwin":
        return rss, None
    return rss * 1024, None
