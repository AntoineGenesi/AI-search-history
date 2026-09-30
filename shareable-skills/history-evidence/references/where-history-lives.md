# Where History Lives

Use this reference to find local AI conversation history roots before reading records. Prefer explicit user-provided roots over defaults. Never scan `/`, `$HOME`, mounted drives, caches, model folders, browser profiles, or an entire repo unless the user explicitly approves a bounded root and caps.

## Provider Roots

Use the [provider matrix](provider-matrix.md) for storage families, root
variants and source confidence. Select a project/session under a root before
content search; a candidate root is not proof of complete coverage.

Codex:

```text
$CODEX_HOME/sessions/**/*.jsonl
$CODEX_HOME/history.jsonl
~/.codex/sessions/**/*.jsonl
~/.codex/history.jsonl
```

Claude Code:

```text
$CLAUDE_CONFIG_DIR/projects/**/*.{jsonl,json,claude}
$XDG_CONFIG_HOME/claude-code/projects/**/*.{jsonl,json,claude}
~/.claude/projects/**/*.jsonl
```

A non-empty `CLAUDE_CONFIG_DIR` selects its projects root. Otherwise consider
the XDG candidate when set and the home candidate additively. Do not substitute
`CLAUDE_HOME`. Some desktop JSON is metadata-only.

Other providers:

```text
Gemini: $GEMINI_HOME or ~/.gemini/tmp; <project>/chats/session-*
Grok: $GROK_HOME/sessions or ~/.grok/sessions; <encoded-cwd>/<session>/
Cursor: selected OS Cursor User workspaceStorage/globalStorage/state.vscdb
        or ~/.cursor/projects/<project>/agent-transcripts/<sid>/<sid>.jsonl
OpenCode: selected OPENCODE_SQLITE_DB / XDG opencode.db
          or OPENCODE_STORAGE_ROOT / XDG opencode/storage JSON trees
Aider: selected-project/.aider.chat.history.md
```

Route file streams/Markdown to [file-backed-providers.md](file-backed-providers.md)
and databases/mixed trees to [sqlite-and-mixed-providers.md](sqlite-and-mixed-providers.md).
Generic exports and second-wave providers require local structural inspection;
do not guess a provider from `.json` alone.

Generic exports:

```text
*.jsonl
*.json with a top-level {"messages": [...]} array
Chat/export folders containing conversation*.json or message*.jsonl
```

## Root Selection

Use the narrowest source that can answer:

1. If the user named a file/export/root, start there.
2. If the question is project-specific, prefer logs whose path, metadata, `cwd`, `project`, `workspace`, or nearby text matches the project.
3. If the question is time-specific, narrow by dated session folders or reliable metadata before content search. File mtime is only a weak discovery hint, especially for Aider/database stores.
4. If the provider is known, use only that provider first.
5. If provider is unknown, inventory likely roots, then search the smallest matching roots.

Keep these boundaries separate:

```text
selected_scope: roots/files you intend to inspect
searched_scope: roots/files and caps actually inspected
not_searched_scope: missing roots, capped files, unavailable exports, or roots outside the plan
```

Reject broad roots by default:

```text
/
~
/home
/mnt
repo root with many unrelated JSON files
cache/model/package directories
browser profile directories
```

For a project-specific question, prefer logs whose `cwd`, project path, session metadata, filename path, or surrounding prompt mentions the project. For a remembered user instruction, prefer user-authored records even when assistant summaries are easier to find.

## Safe Inventory

Use metadata discovery before content reads when roots may be large. Inventory
each selected root independently so a missing/unreadable root is visible:

```bash
HISTORY_ROOT='selected/provider/project-or-date-directory'
rg --files --hidden "$HISTORY_ROOT" \
  -g '*.jsonl' -g '*.json' -g '*.claude' -g '.aider.chat.history.md' \
  -g 'state.vscdb' -g 'opencode.db' | sed -n '1,200p'
```

The display limit is not a file-scan/time cap. If output reaches 200 entries,
record truncated inventory and narrow further before searching. Record errors
and omitted roots; do not redirect diagnostics away and call that a complete
inventory. For large stores prefer a known date/session directory or explicit
file list. Do not open auth/config files just because they share the root.

Useful narrowing examples:

```bash
rg --files "${CODEX_HOME:-$HOME/.codex}/sessions/2026/01/01" -g '*.jsonl'
HISTORY_CLAUDE_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
rg --files "$HISTORY_CLAUDE_ROOT/selected-encoded-project" -g '*.jsonl'
```

The Claude example chooses one home/override candidate; it does not inspect
the additive XDG candidate. Record that separately. For SQLite, inventory the
selected database and inspect schema read-only rather than applying `rg` to
binary contents.

## Missing Roots

If a default root is absent, that is not evidence that the event did not happen. Report it as `missing_root` or `not_searched_scope`. If all likely roots are missing, answer from that boundary instead of broadening into unsafe locations.
