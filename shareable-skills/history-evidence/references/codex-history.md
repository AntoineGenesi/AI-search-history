# Codex History

Use this reference for Codex CLI/App style local logs.

## Likely Roots

```text
~/.codex/sessions/YYYY/MM/DD/*.jsonl
~/.codex/history.jsonl
$CODEX_HOME/sessions/YYYY/MM/DD/*.jsonl
$CODEX_HOME/history.jsonl
```

`history.jsonl` can mirror or index higher-level conversation/user rows. Session JSONL files usually contain richer per-turn records, tool calls, tool outputs, context records, and session metadata.

Roots/session parsing are described in the [pinned provider matrix](provider-matrix.md). Treat `history.jsonl` as a fallback lead, not a complete action log. Inspect `session_meta`/`turn_context` and top-level or payload `cwd`, session ID and parent metadata; storage varies by version. Missing sessions in one runtime profile do not prove absence from another.

## Common Fields

Codex records may expose:

```text
payload.type
payload.role
payload.message
payload.content
payload.name
payload.arguments
payload.output
cwd
project
session.cwd
timestamp
```

Common `payload.type` values:

```text
message
user_message
agent_message
assistant_message
function_call
function_call_output
turn_context
session_meta
```

Recent Codex/OpenAI-style rows may use `{"type":"response_item","payload":{...}}`. User and assistant messages can be nested under `payload.role` and `payload.content[]`; function calls and outputs can be separate `payload.type` rows linked by `call_id`.

## Practical Rules

- For "what did I say/tell you?", start with user/user_message records.
- For "what happened since my last message?", identify the user anchor first, then inspect session/tool activity after it.
- For "did the agent run/edit/test X?", use function/tool call and function/tool output records, not final assistant prose.
- Treat duplicated user text in `history.jsonl` and `sessions/**/*.jsonl` as mirror evidence; prefer the richer session source when available while retaining both refs if chronology matters.
- Do not confuse `<environment_context>` or injected runtime metadata with user-authored instructions.
- Do not confuse the current conversation's injected skill instructions with historical user instructions.
- If the session file is unavailable but `history.jsonl` names a session id or path, report the session as missing rather than upgrading the index row to full proof.

## Useful Searches

```bash
rg -n --fixed-strings --max-count 5 "exact user phrase" "selected/session.jsonl"
rg -n --max-count 10 'function_call|function_call_output|tool_call|tool_output|apply_patch|pytest|npm test' "selected/session.jsonl"
```
