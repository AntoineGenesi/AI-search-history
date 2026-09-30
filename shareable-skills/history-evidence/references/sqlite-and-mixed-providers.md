# SQLite And Mixed Providers

Use [provider roots](provider-matrix.md) to select a workspace/session store.
Open SQLite read-only; inspect schema before queries. Do not invoke a provider
CLI, repair/upgrade a database, checkpoint its WAL, or create a persistent index.
Keep the native database path/table/key and original JSON parts as evidence.

## Cursor

Look within the selected OS Cursor `User` root's `globalStorage` or specific
`workspaceStorage` directory for `state.vscdb`. Workspace metadata can guide
selection but is not transcript evidence. Inspect both `cursorDiskKV` and
`ItemTable` when present: storage layout and row JSON vary by version. Native
keys such as `composerData:<id>` can lead to conversation data, messages or
linked bubbles; resolve actual references rather than assuming one row contains
the complete conversation. Preserve conversation/message/workspace IDs and
tool call/output structure found in the JSON values.

Newer candidate files live under
`~/.cursor/projects/<project>/agent-transcripts/<sid>/<sid>.jsonl`.
The pinned connector labels that shape reporter-documented rather than
real-transcript verified. Inspect actual roles/content before applying generic
JSONL extraction; keep `path:line` and native IDs. If both forms exist, treat
possible mirrors as leads to resolve, not independent action evidence.

## OpenCode

Start with an explicit `OPENCODE_SQLITE_DB` or selected XDG database candidate.
Inspect whether the schema uses `session` or `session_v2` and how `message` and
`part` relate. Do not assume a v1 query fits v2. Match project/session first,
then retrieve bounded messages and parts using actual native keys/relationships.
Preserve text, reasoning and tool parts separately with tool IDs, arguments,
results/status; message text alone may omit the execution evidence.

Legacy `OPENCODE_STORAGE_ROOT`/XDG `opencode/storage` trees can contain separate
session/message/part JSON files. Resolve references using native IDs, retain
each file/object pointer, and note missing part files. A session JSON metadata
record does not establish that its messages were inspected. Resolve duplicate
legacy/database sources without silently asserting which is complete or newer.

## Read-Only Structural Inspection

Set `HISTORY_DB_URI` to an existing, selected SQLite file URI; encode spaces
and URI-special characters. `mode=ro` prevents writes and avoids creating a
database for a missing filename. Do not use `immutable=1` for a live store:
ignoring WAL state can miss recent evidence.

```bash
HISTORY_DB_URI='file:/selected/path/state.vscdb?mode=ro'
sqlite3 -readonly "$HISTORY_DB_URI" \
  "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 30;"
```

After confirming `cursorDiskKV(key,value)`, list a bounded set of candidate
native keys before reading values:

```bash
sqlite3 -readonly "$HISTORY_DB_URI" \
  "SELECT key, length(value) FROM cursorDiskKV WHERE key LIKE 'composerData:%' ORDER BY key LIMIT 20;"
```

Retrieve only a selected key with bound parameters when scripting. Do not
interpolate user phrases or IDs into SQL. Inspect large row lengths first,
cap rows/bytes/time and record any omitted data. If only `ItemTable` exists,
inspect its columns and actual keys before adapting the query. For OpenCode,
inspect `PRAGMA table_info('message')` and `PRAGMA table_info('part')` before
constructing a schema-specific session/message/part join.

## Locators And Failure Boundaries

Cite `state.vscdb#cursorDiskKV:composerData:<native-id>` plus a nested pointer
or message ID, or `opencode.db#message:<native-id>` and the supporting
`#part:<native-id>` rows. These are native row locators, never invented text
lines. Retain the key map for any session/sequence shorthand.

Missing tables, unknown schemas, malformed row JSON, locked/unreadable stores,
missing linked parts, metadata-only records and query caps must be reported.
A live read may observe changing state; identify the read scope/time and limit
latest-status claims accordingly. Valid rows in one source remain usable when
another source fails. No results from an incomplete query prove only absence
in that inspected scope. See [normalization](jsonl-record-shapes.md),
[action proof](action-proof.md), [latest status](latest-status.md),
[no evidence](no-evidence.md) and [privacy](privacy.md).
