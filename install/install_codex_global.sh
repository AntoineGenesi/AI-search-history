#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/jsonl-history-search"

mkdir -p "$SKILL_DIR/scripts"
cp -a "$ROOT/skills/codex/jsonl-history-search/SKILL.md" "$SKILL_DIR/SKILL.md"
cp -a "$ROOT/scripts/search_jsonl_history.py" "$SKILL_DIR/scripts/search_jsonl_history.py"
chmod +x "$SKILL_DIR/scripts/search_jsonl_history.py"

echo "Installed Codex JSONL history search skill to: $SKILL_DIR"
echo "Validate with:"
echo "  python3 \"$SKILL_DIR/scripts/search_jsonl_history.py\" \"test\" --latest --recent-files 1 --limit 1"
