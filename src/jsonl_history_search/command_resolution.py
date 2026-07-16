from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil


CONSOLE_COMMAND = "jsonl-history-search"
SOURCE_CHECKOUT_COMMAND = "PYTHONPATH=src python3 -m jsonl_history_search.cli"


@dataclass(frozen=True)
class CommandResolution:
    mode: str
    command_prefix: str
    installed_command_available: bool
    source_checkout_available: bool
    source_fallback_command: str | None
    guidance: str

    def as_dict(self) -> dict:
        return asdict(self)


def resolve_command(cwd: str | Path = ".") -> CommandResolution:
    installed_available = shutil.which(CONSOLE_COMMAND) is not None
    source_available = _source_checkout_available(cwd)

    if installed_available:
        return CommandResolution(
            mode="installed_console",
            command_prefix=CONSOLE_COMMAND,
            installed_command_available=True,
            source_checkout_available=source_available,
            source_fallback_command=(SOURCE_CHECKOUT_COMMAND if source_available else None),
            guidance=(
                "Use the installed console command. If it later becomes unavailable in "
                "this package checkout, use the source_fallback_command exactly as shown."
            ),
        )
    if source_available:
        return CommandResolution(
            mode="source_checkout",
            command_prefix=SOURCE_CHECKOUT_COMMAND,
            installed_command_available=False,
            source_checkout_available=True,
            source_fallback_command=SOURCE_CHECKOUT_COMMAND,
            guidance=(
                "The console script is not on PATH. Run the selected source-checkout "
                "command directly; no install, publishing, or repo inventory step is required."
            ),
        )
    return CommandResolution(
        mode="unresolved",
        command_prefix=CONSOLE_COMMAND,
        installed_command_available=False,
        source_checkout_available=False,
        source_fallback_command=None,
        guidance=(
            "The console script is not on PATH and this cwd is not a package source "
            "checkout. Run from a checkout containing src/jsonl_history_search/cli.py "
            "or use an existing installed command; do not guess package setup commands."
        ),
    )


def _source_checkout_available(cwd: str | Path) -> bool:
    root = Path(cwd).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return False
    return (root / "src" / "jsonl_history_search" / "cli.py").is_file()
