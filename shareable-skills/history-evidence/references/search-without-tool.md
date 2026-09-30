# Search Without A Dedicated Tool

Use this reference when no specialized history-search package is installed. These commands are candidate discovery, not final proof.

Select storage using [provider-matrix.md](provider-matrix.md) and inventory
using [where-history-lives.md](where-history-lives.md). Ordinary local tools
are enough; this workflow does not invoke provider CLIs. Set selected sources
explicitly, keeping database files out of text searches:

```bash
HISTORY_ROOT='selected/provider/project-or-date-directory'
HISTORY_FILE='selected/provider/session.jsonl'
```

## Find Candidate Files

```bash
rg --files --hidden "$HISTORY_ROOT" \
  -g '*.jsonl' -g '*.json' -g '*.claude' -g '.aider.chat.history.md' \
  | sed -n '1,200p'
```

Narrow large roots before searching:

```bash
rg --files "${CODEX_HOME:-$HOME/.codex}/sessions/2026/01/01" -g '*.jsonl'
```

Display limits do not bound the scan. Record truncated inventories, missing
roots and diagnostics; select a smaller directory or explicit file list before
content reads. Use `CLAUDE_CONFIG_DIR/projects` when overridden, otherwise
consider home and configured XDG candidates independently.

## Search For Candidate Lines

Prefer `rg` for a focused phrase, path, command, task id, or distinctive wording:

```bash
rg -n --fixed-strings --max-count 5 "exact phrase" "$HISTORY_FILE"
rg -n --max-count 10 "pytest|npm test|ruff|mypy" "$HISTORY_FILE"
```

For remembered user wording, include role hints:

```bash
rg -n --max-count 10 '"role":"user"|"role": "user"|user_message|payload.*user' "$HISTORY_FILE"
```

For action proof, search both command words and tool-shape words:

```bash
rg -n --max-count 10 'function_call|tool_use|tool_result|function_call_output|pytest|npm test|git diff|apply_patch' "$HISTORY_FILE"
```

`--max-count` caps matching lines per file, not line length, bytes, total files
or time. Check file sizes and bound source selection first. These role/tool
hints suit Codex/Claude; inspect other provider schemas before using them.

For "since my last message", find the anchor first with exact wording or a nearby distinctive phrase, then inspect lines after that anchor in the same session.

## Open A Bounded Source Window

For JSONL, physical line numbers identify raw records; event-stream lines may
be patches/chunks rather than complete turns. If a hit is `path:LINE`, open a
small window:

```bash
FILE="path/to/session.jsonl"
LINE=123
START=$(( LINE > 3 ? LINE - 3 : 1 ))
END=$(( LINE + 3 ))
nl -ba "$FILE" | sed -n "${START},${END}p"
```

Use a wider window for paired tool call/output evidence:

```bash
START=$(( LINE > 8 ? LINE - 8 : 1 ))
END=$(( LINE + 12 ))
nl -ba "$FILE" | sed -n "${START},${END}p"
```

If a record is hard to read, pretty-print only the selected line:

```bash
sed -n "${LINE}p" "$FILE" | jq .
```

If `jq` fails, keep the raw window and state that the record could not be parsed cleanly.

## JSON, Markdown And SQLite

For an inspected JSON `messages` array, select adjacent entries without
inventing refs from pretty-print lines:

```bash
jq '.messages | to_entries[] | select(.key >= 3 and .key <= 5)' "selected/session.json"
```

Cite `selected/session.json:messages[3]` and the native ID. `jq` loads the JSON
file, so bound file size first. Gemini JSONL requires patch replay; Grok needs
chunk/tool-update reconstruction; Aider uses Markdown role/block boundaries.
Read [file-backed-providers.md](file-backed-providers.md) for those rules.

For SQLite use [sqlite-and-mixed-providers.md](sqlite-and-mixed-providers.md)
to inspect schema and query selected rows read-only. Cite table/native keys
and message/part refs, never grep binary databases or invent `path:line`.
Do not treat a metadata sidecar, missing part or unsupported schema as a
complete transcript. Preserve raw reconstruction inputs and report gaps.

## Stop Rules

- Search results are candidates only.
- Open source windows before answering.
- Do not run repeated broad searches after a primary source window answers the question.
- If roots are missing or the result is capped/partial, say so. Valid evidence from other readable sources still stands.
- Do not publish, commit, or upload raw private transcript text.

## Minimal Answer Ledger

Before finalizing, make a tiny mental ledger:

```text
question_type:
searched_scope:
primary_refs:
secondary_refs:
status:
missing_or_capped_scope:
```

If `primary_refs` is empty for an action-proof or remembered-instruction question, do not answer as confirmed.
