#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_DIR="${CODEX_HOME:-$HOME/.codex}/skills/jsonl-history-search"

echo "SKILL-ONLY INSTALLER: this does not install the Python CLI."
echo "It will not create the jsonl-history-search or convo console commands."
echo "Codex skill route: package compatibility path CODEX_HOME/skills or ~/.codex/skills."
echo "Current Codex docs may describe .agents/skills or plugins; this helper does not install those."
echo "Install the CLI first with pipx, then prefer: jsonl-history-search install-codex-skill"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m jsonl_history_search.cli \
  install-codex-skill --codex-home "${CODEX_HOME:-$HOME/.codex}" --format text

echo "Validate with:"
echo "  env -u PYTHONPATH jsonl-history-search validate-install --codex-home \"${CODEX_HOME:-$HOME/.codex}\" --format text"
echo "  env -u PYTHONPATH convo --help"
echo "  env -u PYTHONPATH jsonl-history-search ask \"which prior conversation mentioned the router?\" --format text"
echo "  (cd \"$ROOT\" && python3 \"$SKILL_DIR/scripts/jsonl_ask_first.py\" \"which prior conversation mentioned the router?\" --format text)"
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
