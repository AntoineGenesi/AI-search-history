#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "ask":
        args = args[1:]
    args = _join_unquoted_query_words(args)
    if not _has_option(args, "--cwd"):
        args.extend(["--cwd", os.getcwd()])
    if not _has_option(args, "--format"):
        args.extend(["--format", "text"])
    if not _has_option(args, "--execute") and not _has_option(args, "--auto-execute-bounded-no-evidence"):
        args.append("--auto-execute-bounded-no-evidence")

    command = [sys.executable, "-m", "jsonl_history_search.cli", "ask", *args]
    return subprocess.run(command, check=False).returncode


def _join_unquoted_query_words(args: list[str]) -> list[str]:
    if not args or args[0].startswith("-"):
        return args

    query_words: list[str] = []
    index = 0
    while index < len(args) and not args[index].startswith("-"):
        query_words.append(args[index])
        index += 1
    if len(query_words) <= 1:
        return args
    return [" ".join(query_words), *args[index:]]


def _has_option(args: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in args)


if __name__ == "__main__":
    raise SystemExit(main())
