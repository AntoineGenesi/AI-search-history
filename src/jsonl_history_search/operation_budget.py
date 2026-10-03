"""Ephemeral authority for one evidence operation's time and source-read work.

Text bytes are measured at binary reads. SQLite extents are conservative work
reservations, never reported as physical I/O. Legacy callers pass no budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time


class BudgetExceeded(OSError):
    """Recoverable partial coverage, distinct from an unreadable source."""


@dataclass
class OperationBudget:
    limit_bytes: int
    max_elapsed: float
    started: float = field(default_factory=time.monotonic)
    measured_text_bytes: int = 0
    native_store_reserved_bytes: int = 0
    exhausted_reasons: list[str] = field(default_factory=list)
    phases: dict = field(default_factory=dict)
    opened_sources: set[str] = field(default_factory=set)

    @property
    def remaining_bytes(self):
        return max(0, self.limit_bytes - self.measured_text_bytes - self.native_store_reserved_bytes)

    @property
    def remaining_seconds(self):
        return max(0.0, self.max_elapsed - (time.monotonic() - self.started))

    def stop(self, reason):
        if reason not in self.exhausted_reasons:
            self.exhausted_reasons.append(reason)
        raise BudgetExceeded(f"operation budget: {reason}")

    def check(self, phase):
        self.phases.setdefault(phase, {"measured_text_bytes": 0, "native_store_reserved_bytes": 0, "source_passes": 0})
        if self.remaining_seconds <= 0:
            self.stop("max_elapsed")

    def open_source(self, path, phase):
        self.check(phase)
        self.opened_sources.add(str(path))
        self.phases[phase]["source_passes"] += 1

    def charge_text(self, count, phase):
        self.measured_text_bytes += count
        self.phases[phase]["measured_text_bytes"] += count

    def available(self, phase):
        try:
            self.check(phase)
            return True
        except BudgetExceeded:
            return False

    def reserve_native(self, path, phase):
        self.open_source(path, phase)
        extent = path.stat().st_size
        if extent > self.remaining_bytes:
            self.stop("max_bytes")
        self.native_store_reserved_bytes += extent
        self.phases[phase]["native_store_reserved_bytes"] += extent

    def to_dict(self):
        return {"limit_bytes": self.limit_bytes, "max_elapsed": self.max_elapsed,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                "measured_text_bytes": self.measured_text_bytes,
                "native_store_reserved_bytes": self.native_store_reserved_bytes,
                "remaining_bytes": self.remaining_bytes,
                "distinct_source_read_attempts": len(self.opened_sources),
                "exhausted_reasons": list(self.exhausted_reasons), "phases": self.phases,
                "physical_io_measured": False,
                "accounting": "measured text reads plus conservative native-store reservations; cache I/O separate"}


class BudgetTextReader:
    def __init__(self, path, budget, phase):
        budget.open_source(path, phase)
        self.handle = Path(path).open("rb")
        self.size = Path(path).stat().st_size
        self.budget, self.phase = budget, phase

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.handle.close()

    def _read(self, size, line=False):
        self.budget.check(self.phase)
        if self.handle.tell() >= self.size:
            return b""
        if self.budget.remaining_bytes <= 0:
            self.budget.stop("max_bytes")
        count = min(size, self.budget.remaining_bytes)
        value = self.handle.readline(count) if line else self.handle.read(count)
        self.budget.charge_text(len(value), self.phase)
        return value

    def read(self, size=-1):
        pieces = []
        remaining = size
        while remaining != 0:
            chunk = self._read(65536 if remaining < 0 else min(65536, remaining))
            if not chunk:
                break
            pieces.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        return b"".join(pieces).decode("utf-8", errors="replace")

    def __iter__(self):
        return self

    def __next__(self):
        pieces = []
        while True:
            chunk = self._read(65536, line=True)
            if not chunk:
                if not pieces:
                    raise StopIteration
                break
            pieces.append(chunk)
            if chunk.endswith(b"\n"):
                break
        return b"".join(pieces).decode("utf-8", errors="replace")


def open_source_text(path, *, budget=None, phase="retrieval"):
    return BudgetTextReader(path, budget, phase) if budget is not None else path.open("r", encoding="utf-8", errors="replace")


def read_source_text(path, *, budget=None, phase="retrieval"):
    with open_source_text(path, budget=budget, phase=phase) as handle:
        return handle.read()


def iter_source_bytes(path, *, budget=None, phase="fingerprint"):
    if budget is None:
        with path.open("rb") as handle:
            yield from iter(lambda: handle.read(65536), b"")
    else:
        with BudgetTextReader(path, budget, phase) as handle:
            while True:
                chunk = handle._read(65536)
                if not chunk:
                    break
                yield chunk
