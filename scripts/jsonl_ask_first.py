#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import shutil
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
    command, env = _cli_command()
    return subprocess.run([*command, "ask", *args], check=False, env=env).returncode


def _cli_command() -> tuple[list[str], dict[str, str]]:
    env = os.environ.copy()
    if (Path.cwd() / 'src/jsonl_history_search/cli.py').is_file():
        return [sys.executable, '-m', 'jsonl_history_search.cli'], _source_checkout_env()
    if shutil.which("jsonl-history-search"):
        return ["jsonl-history-search"], env
    return [sys.executable, "-m", "jsonl_history_search.cli"], _source_checkout_env()


def _source_checkout_env() -> dict[str, str]:
    env = os.environ.copy()
    source_root = Path.cwd() / "src"
    if not (source_root / "jsonl_history_search" / "cli.py").is_file():
        return env
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), existing] if existing else [str(source_root)]
    )
    return env


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
