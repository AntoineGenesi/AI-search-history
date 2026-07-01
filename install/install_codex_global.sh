#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/jsonl-history-search"

mkdir -p "$SKILL_DIR/scripts"
cp -a "$ROOT/skills/codex/jsonl-history-search/SKILL.md" "$SKILL_DIR/SKILL.md"
if [ -d "$ROOT/skills/codex/jsonl-history-search/references" ]; then
  rm -rf "$SKILL_DIR/references"
  cp -a "$ROOT/skills/codex/jsonl-history-search/references" "$SKILL_DIR/references"
fi
cp -a "$ROOT/scripts/search_jsonl_history.py" "$SKILL_DIR/scripts/search_jsonl_history.py"
chmod +x "$SKILL_DIR/scripts/search_jsonl_history.py"

echo "Installed Codex JSONL history search skill to: $SKILL_DIR"
echo "This installed only the Codex skill/helper copy, not the Python package."
echo "Install the Python CLI separately with: python3 -m venv .venv && . .venv/bin/activate && python -m pip install -e ."
echo "Validate with:"
echo "  PYTHONPATH=\"$ROOT/src\" python3 -m jsonl_history_search.cli validate-install --codex-home \"${CODEX_HOME:-$HOME/.codex}\" --format text"
echo "  python3 \"$SKILL_DIR/scripts/search_jsonl_history.py\" --latest-user-messages --limit 1"
echo ""
echo "Inspect capability status with the read-only diagnostic:"
echo "  PYTHONPATH=\"$ROOT/src\" python3 -m jsonl_history_search.cli diagnose-capabilities --format text"
echo "  PYTHONPATH=\"$ROOT/src\" python3 -m jsonl_history_search.cli diagnose-capabilities --format json --report-output capabilities.json"
echo ""
echo "Capability statuses:"
echo "  available       Ready to use now; read the benefit and fallback fields for what it enables."
echo "  degraded        Usable through a fallback; expect the reported slowdown or lost feature."
echo "  missing         Not found locally; use suggested_next_action to recover manually if needed."
echo "  blocked         Do not rely on it until the reported privacy, safety, or environment issue is fixed."
echo "  not_implemented Future capability; the installer does not enable it."
echo ""
echo "Core retrieval needs package import, CLI invocation, source planning, and Python JSON scanning."
echo "Optional acceleration or future paths include rg, manifests, embeddings, timestamp sidecars, and native parsers."
