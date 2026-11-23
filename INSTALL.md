# Install

Choose the standalone skill or the Python CLI. Neither route copies your
conversation history. Install from this checkout; no PyPI release is assumed.

## Standalone skill

Copy the complete directory to a new destination:

```bash
skills_dir="$HOME/.agents/skills"       # Codex
# skills_dir="$HOME/.claude/skills"     # Claude Code
mkdir -p "$skills_dir"
mkdir "$skills_dir/history-evidence" &&
  cp -R shareable-skills/history-evidence/. "$skills_dir/history-evidence/"
```

Start a new agent session and ask it to use `history-evidence`. An existing
destination is left untouched. This route needs ordinary file, JSON or SQLite
tools, rather than the Python package. The [provider guide](shareable-skills/history-evidence/references/provider-matrix.md)
describes where to look and how to preserve native references.

## CLI and agent guidance

The package supports Python 3.8+. Current pipx requires Python 3.10+.

```bash
pipx install .
convo --help
convo install-agent-skill --agent codex
# Or: convo install-agent-skill --agent claude
```

The commands `convo` and `jsonl-history-search` run the same CLI. The optional
installer writes `jsonl-history-search` and its `convo` alias under
`~/.agents/skills` for Codex or `~/.claude/skills` for Claude Code. It updates
those package-owned skill directories. For a custom location:

```bash
convo install-agent-skill --agent claude --agent-home /path/to/project/.claude
```

The selected home receives a `skills/` subdirectory. Avoid keeping duplicate
active Codex copies in both `~/.agents/skills` and legacy `~/.codex/skills`.

Without pipx, use a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
convo --help
```

For source use without installing console commands:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask \
  "What did I say about the release checklist?" --cwd "$PWD" --format text
```

Run installed commands from the project whose history you want to search.
Use an authorized explicit `--root` when automatic discovery does not cover
the intended source. See the [workflow](docs/workflow.md).
