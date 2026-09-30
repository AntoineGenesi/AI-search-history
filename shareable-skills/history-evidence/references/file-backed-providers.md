# File-Backed Providers

Use the [provider matrix](provider-matrix.md) for roots and pinned-source
confidence. Inventory selected project/session files before reading content.
JSONL physical lines, JSON object pointers and Markdown block lines are different
locators; preserve raw inputs when constructing a message from several events.

## Gemini CLI

Under the selected Gemini root, inspect `<project>/chats/session-*`. Legacy
JSON contains a session/message collection; cite the actual message array
index or object pointer and native ID, not pretty-printed line numbers.
Current JSONL is an event stream. Identify the `kind: main` header and replay
`$set` patches in physical order before reading typed messages. A
`$set.messages` patch replaces that message collection; do not append it or
count older copies as separate human turns.

Retain session/project metadata, message IDs, roles, raw timestamps and tool
structure. Cite the header/patch line and message pointer that supplies a
message plus later relevant patches. A text hit inside a replaced snapshot
can prove what that event contained, but does not establish the final state.
Malformed/unknown patches make reconstructed state partial; record skipped
line numbers and do not invent missing chronology.

## Grok Build/CLI

Select the encoded cwd/session directory under the Grok sessions root.
`updates.jsonl` is the authoritative ACP stream in the pinned empirical format.
`summary.json` is metadata for discovery, not action proof. Use
`chat_history.jsonl` only when the authoritative stream is unavailable, and
label that fallback coverage.

Coalesce consecutive message chunks for the same native message/turn, keeping
each original line reference. Preserve tool update IDs, arguments, results and
status; a started tool update alone does not prove completion. Do not turn
synthetic fallback prompts or prompt echoes into original human instructions.
Avoid double-counting content mirrored across updates and fallback history.
Missing chunks/results or unknown update types limit reconstruction; retain
valid raw events for their narrower claims.

## Aider

Read only the selected project's `.aider.chat.history.md`. The pinned parser
treats quoted lines as user turns, following unquoted blocks as assistant
content, and the preamble as system context. Confirm local delimiters and block
boundaries; prose/code quotation may be ambiguous outside this format.
Record block start/end lines and the raw text before attributing authorship.

Per-message timestamps are unavailable. File mtime is weak conversation-level
evidence and cannot establish when a particular instruction or command happened.
Command-looking Markdown and assistant claims are not structured execution
proof. For action questions, find raw tool/output evidence elsewhere in the
authorized scope or answer `claimed_only`/`not_answerable_from_inspected_scope`.
`CASS_AIDER_DATA_ROOT` is a connector setting, not an Aider vendor override.

## Other Selected Exports

The [cautious catalog](provider-matrix.md#cautious-second-wave-catalog) covers
additional format leads. Inspect JSON message arrays, per-event objects or
JSONL records with bounded file/record caps. Preserve branch/parent IDs when
present; physical order alone may not be conversation order. Never assume
a generic `content` or `prompt` field is human-authored.

## Minimal Inspection

For selected JSONL or Markdown candidates:

```bash
rg -n --fixed-strings --max-count 5 "exact phrase" "selected/session.jsonl"
nl -ba "selected/.aider.chat.history.md" | sed -n '40,65p'
```

For a confirmed legacy JSON `messages` array:

```bash
jq '.messages | to_entries[] | select(.key >= 3 and .key <= 5)' "selected/session.json"
```

These are structural reads, not a generic replay implementation. Bound selected
file sizes before parsing JSON or replaying events. If available tools cannot
reconstruct a store reliably, report partial/unsupported scope instead of
promoting grep hits to final evidence. Retain the [action-proof](action-proof.md),
[latest-status](latest-status.md), [absence](no-evidence.md) and [privacy](privacy.md)
rules for every format.
