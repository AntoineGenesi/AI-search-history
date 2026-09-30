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
Inspect transcript tables before choosing a schema:

- Current v2 uses `session_message` joined to `session_v2`, or to `session` in
  later 2.x builds. Message and tool blocks are embedded in `session_message.data`
  JSON. Select project/session first, retrieve bounded rows in native `seq`
  order, and retain each native row ID and JSON pointer. A `session` table alone
  does not identify v1.
- Legacy v1 uses `session` + `message` + `part`. Resolve message/part joins
  using actual native keys and retain both row locators. Only this SQLite
  schema requires the separate message/part join.

Do not substitute v1 queries when v2 content is unavailable or malformed.
Preserve text, reasoning and tool blocks separately with tool IDs, arguments,
results/status; message text alone may omit execution evidence. Missing tables
or unknown relationships make coverage partial/unsupported rather than empty.

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
first inspect `PRAGMA table_info('session_message')`. If present, inspect
`session_v2` or the later `session` table and resolve the actual relationship
before reading bounded `data` JSON in native `seq` order. If `session_message`
is absent, verify v1 `session`, `message` and `part` columns before constructing
that join. Unknown/mixed schemas require explicit inspection, not a blind fallback.

## Locators And Failure Boundaries

Cite `state.vscdb#cursorDiskKV:composerData:<native-id>` plus a nested pointer
or message ID. For OpenCode v2 cite `opencode.db#session_message:<native-id>`
and the pointer within `data`, retaining session ID and native `seq` for order.
For v1 cite `opencode.db#message:<native-id>` and supporting `#part:<native-id>`
rows. These are native row locators, never invented text lines. Retain the key
map for any session/sequence shorthand.

Missing tables, unknown schemas, malformed row JSON, locked/unreadable stores,
missing linked parts, metadata-only records and query caps must be reported.
A live read may observe changing state; identify the read scope/time and limit
latest-status claims accordingly. Valid rows in one source remain usable when
another source fails. No results from an incomplete query prove only absence
in that inspected scope. See [normalization](jsonl-record-shapes.md),
[action proof](action-proof.md), [latest status](latest-status.md),
[no evidence](no-evidence.md) and [privacy](privacy.md).
