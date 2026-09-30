# JSONL Record Shapes

Use this reference to normalize file transcripts, event streams, or database rows without a purpose-built parser. There is no single universal schema. Select the provider/storage family from [provider-matrix.md](provider-matrix.md), then keep original refs and structure while extracting candidates.

## Portable Normalized Record

For each record, try to derive:

```text
provider: identified provider, or unknown
storage_kind: jsonl | json | markdown | sqlite | event_stream | unknown
source_ref: locator in the original file/database
supporting_refs: original events/rows needed for replay, chunks, or part joins
native_ids: session/conversation, message/event/part, parent, tool-call IDs when present
role: user | assistant | tool_call | tool_output | system/context | unknown
text: human-readable content or compact tool payload
timestamp: raw value, parsed value, units/timezone and confidence when known
sequence: native ordinal/branch order when present
cwd/project: local project marker if present
raw: original JSON object
```

Keep unknown roles and times unknown. Synthetic/derived normalized indices are not source identities. Preserve raw arguments, result/status, and branch/workspace relationships rather than reducing everything to text.

## Source Locators By Medium

| Medium | Portable locator | What to retain |
| --- | --- | --- |
| JSONL | `session.jsonl:123`, optionally with a JSON pointer such as `/message/content/1` | Original physical line (1-based) plus native event/message/call ID. |
| JSON array | `session.json:messages[3]` | Array indices are 0-based; record the actual array/object pointer and native ID. Pretty-print line numbers are not original refs. |
| JSON object/event file | `event.json#/action` | Original file, JSON pointer, event ID/ordinal. |
| Markdown | `.aider.chat.history.md:42` | 1-based block start and end lines; role attribution is format-derived. |
| SQLite | `state.vscdb#cursorDiskKV:composerData:sample-id`; OpenCode v2 `opencode.db#session_message:sample-id`; v1 `opencode.db#message:sample-id` plus `#part:sample-id` | Exact table/native key and JSON pointer; v2 embedded `data`, session ID and native `seq`, or v1 supporting part IDs. A `db#session-id:seq` locator also needs the table/key map and native sequence to be reproducible. |

These are evidence locators, not necessarily clickable editor links. If a message is assembled from several chunks, cite the original line range or multiple refs. For replayed JSONL snapshots cite the header/patch line and array pointer supplying the message; retain relevant later patches. For joined database parts cite both message and part keys. Explain reconstruction instead of inventing a synthetic path or line.

## Discovery Before Normalization

Inventory only selected roots and recognizable transcript/database names, without opening auth/config files. Record existence, format, provider, session/workspace hints and why the source was selected. Confirm format from bounded structural inspection; suffix alone is insufficient. A metadata sidecar may guide scope but is not a transcript.

Apply provider-specific reconstruction before inferring chronology or roles: Gemini `$set.messages` reseeds, Grok chunks/tool updates, Cursor row JSON, OpenCode v2 `session_message.data` blocks in native `seq` order or v1 message/part joins, and branching Pi records are not interchangeable flat messages. Keep copied prompts, injected context and model-input echoes distinct from original human turns.

## Partial Failure

Retain valid evidence from readable sources while noting unreadable files, malformed row/line locations, unknown event/schema types, missing parts/results/parents, metadata-only sessions, and caps. Do not count ignored unknown records as irrelevant without inspecting their purpose. If skipping a corrupt patch prevents reliable replay, label reconstructed state incomplete; a valid raw event can still support only its own narrow claim. Missing/locked/unsupported stores are not empty stores.

## Text Extraction

Content may be a string, list, object, nested `message`, or tool output. Extract text recursively from these keys when present:

```text
text
content
message
prompt
output
result
delta
arguments
input
name
```

Recursive extraction is for candidate discovery only. Keep text, thinking, tool arguments and tool output as separate blocks with their roles/IDs. If no text exists but there is a structured tool payload, use compact JSON to find candidates, then inspect the raw object before making claims. Do not assign human authorship from a field name alone.

## Role Normalization

Role may appear in several places:

```text
role
type
payload.role
payload.type
message.role
content[].type
```

Useful role-like values include:

```text
user, user_message
assistant, assistant_message, agent_message
function_call, tool_call, tool_use
function_call_output, tool_output, tool_result
turn_context, environment_context, session_meta, system
```

Do not treat injected context, environment context, or system reminders as original human messages.

## Tool Pairing

Tool calls and tool outputs are often separate records or separate nested items. For an action claim, pair the call and output by nearby chronology, call id, tool name, or surrounding assistant turn. If you find only one side, answer with that boundary.

Look for these fields:

```text
call_id
tool_call_id
id
payload.call_id
name
arguments
input.command
output
result
status
exit_code
```

## Timestamp Normalization

Try these fields:

```text
timestamp
created_at
time
ts
payload.timestamp
message.timestamp
```

Prefer message timestamps over file modification time. Preserve raw units/timezone; provider values may use seconds, milliseconds or microseconds. Use file modification time only as a weak file-ordering hint. Native sequence can establish relative order when individual message times are missing; do not invent wall-clock times.

## Quick Structural Checks

To inspect a single JSONL line safely:

```bash
sed -n 'LINEp' "path/to/session.jsonl" | jq .
```

If `jq` is unavailable or the line is not valid JSON, use `nl -ba` and inspect the raw line without pretending it is parsed. Do not use regex alone to decide whether a hit is a user message, tool call, or tool output.

## Common Top-Level Shapes

Simple message:

```json
{"role":"user","content":"Please remember this.","timestamp":"2026-01-01T10:00:00Z"}
```

Payload message:

```json
{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"text","text":"Please remember this."}]}}
```

Nested message:

```json
{"message":{"role":"assistant","content":[{"type":"text","text":"I will do that."}]}}
```

Tool call:

```json
{"payload":{"type":"function_call","name":"shell","arguments":{"cmd":"pytest -q"}}}
```

Tool output:

```json
{"payload":{"type":"function_call_output","output":"3 passed in 0.42s"}}
```

Claude-style nested tool item:

```json
{"message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}},{"type":"tool_result","content":"3 passed"}]}}
```

Generic JSON export:

```json
{"conversation_id":"abc","messages":[{"id":"1","role":"user","created_at":"2026-01-01T10:00:00Z","content":"..."}]}
```

OpenAI/Codex response item with function call:

```json
{"type":"response_item","payload":{"type":"function_call","name":"shell","arguments":"{\"cmd\":\"pytest -q\"}","call_id":"call_1"}}
```

OpenAI/Codex response item with function output:

```json
{"type":"response_item","payload":{"type":"function_call_output","call_id":"call_1","output":"3 passed in 0.42s"}}
```

## Evidence Warning

Flattened text search can hide structure. Before saying an action happened, inspect whether the hit is a real tool call/output or just assistant prose describing a planned action. Before saying a user instructed something, inspect whether the row is truly user-authored rather than an assistant recap, prompt echo, copied task text, or injected context.
