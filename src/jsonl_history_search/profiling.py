from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, TextIO

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


ASK_EXECUTE_PHASE_EVENT = "jsonl_history_search.phase"
ASK_EXECUTE_PHASES = (
    "source_planning",
    "retrieval",
    "tool_event_scanning",
    "packet_assembly",
    "rendering",
    "report_writing",
)


@dataclass(frozen=True)
class AskExecutePhaseToken:
    name: str
    started_monotonic_ns: int


class AskExecutePhaseEmitter:
    """Emit privacy-safe JSONL liveness records without touching stdout."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream
        self._command_started_monotonic_ns = time.monotonic_ns()

    def start(self, name: str) -> AskExecutePhaseToken:
        if name not in ASK_EXECUTE_PHASES:
            raise ValueError(f"unsupported ask-execute phase: {name}")
        now = time.monotonic_ns()
        self._write(name=name, state="start", now=now)
        return AskExecutePhaseToken(name=name, started_monotonic_ns=now)

    def complete(self, token: AskExecutePhaseToken) -> None:
        now = time.monotonic_ns()
        self._write(
            name=token.name,
            state="complete",
            now=now,
            phase_elapsed_ms=round((now - token.started_monotonic_ns) / 1_000_000, 3),
        )

    def _write(
        self,
        *,
        name: str,
        state: str,
        now: int,
        phase_elapsed_ms: float | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "schema_version": 1,
            "event": ASK_EXECUTE_PHASE_EVENT,
            "command": "ask_execute",
            "phase": name,
            "state": state,
            "monotonic_ns": now,
            "command_elapsed_ms": round(
                (now - self._command_started_monotonic_ns) / 1_000_000,
                3,
            ),
        }
        if phase_elapsed_ms is not None:
            record["phase_elapsed_ms"] = phase_elapsed_ms
        print(
            json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
            file=self._stream if self._stream is not None else sys.stderr,
            flush=True,
        )


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
    rss_current, rss_current_reason = current_rss_bytes()
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
        "rss_current_bytes": rss_current,
        "rss_current_unavailable_reason": rss_current_reason,
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


def current_rss_bytes() -> tuple[int | None, str | None]:
    if not sys.platform.startswith("linux"):
        return None, "current_rss_supported_on_linux_procfs_only"
    status_rss, status_reason = _current_rss_bytes_from_proc_status()
    if status_rss is not None:
        return status_rss, None
    statm_rss, statm_reason = _current_rss_bytes_from_proc_statm()
    if statm_rss is not None:
        return statm_rss, None
    return None, f"{status_reason};{statm_reason}"


def _current_rss_bytes_from_proc_status() -> tuple[int | None, str]:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("VmRSS:"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    return None, "proc_status_vmrss_malformed"
                try:
                    return int(parts[1]) * 1024, ""
                except ValueError:
                    return None, "proc_status_vmrss_not_integer"
    except FileNotFoundError:
        return None, "proc_status_unavailable"
    except OSError as exc:  # pragma: no cover - platform guard
        return None, type(exc).__name__
    return None, "proc_status_vmrss_absent"


def _current_rss_bytes_from_proc_statm() -> tuple[int | None, str]:
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as handle:
            parts = handle.read().split()
    except FileNotFoundError:
        return None, "proc_statm_unavailable"
    except OSError as exc:  # pragma: no cover - platform guard
        return None, type(exc).__name__
    if len(parts) < 2:
        return None, "proc_statm_malformed"
    try:
        resident_pages = int(parts[1])
    except ValueError:
        return None, "proc_statm_resident_not_integer"
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None, "page_size_unavailable"
    if resident_pages < 0 or page_size <= 0:
        return None, "proc_statm_negative_or_invalid"
    return resident_pages * page_size, ""
