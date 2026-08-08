#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${CODEX_HOME:-}" ]]; then
  AGENT_HOME="$CODEX_HOME"
  ROUTE_NOTE="Legacy package compatibility path CODEX_HOME/skills or ~/.codex/skills selected explicitly through CODEX_HOME."
else
  AGENT_HOME="$HOME/.agents"
  ROUTE_NOTE="Public Codex user route: $HOME/.agents/skills."
fi
SKILL_DIR="$AGENT_HOME/skills/jsonl-history-search"

echo "SKILL-ONLY INSTALLER: this does not install the Python CLI."
echo "It will not create the jsonl-history-search or convo console commands."
echo "$ROUTE_NOTE"
echo "Codex plugins or connectors: this helper does not install those."
echo "Install the CLI first with pipx, then prefer: jsonl-history-search install-agent-skill --agent codex"
echo "Legacy alias: jsonl-history-search install-codex-skill --codex-home /path/to/legacy-home"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m jsonl_history_search.cli \
  install-agent-skill --agent codex --agent-home "$AGENT_HOME" --format text

echo "Validate with:"
echo "  env -u PYTHONPATH jsonl-history-search validate-install --codex-home \"$AGENT_HOME\" --format text"
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
