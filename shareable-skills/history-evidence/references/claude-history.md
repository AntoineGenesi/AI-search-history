# Claude Code History

Use this reference for Claude Code style local project logs.

## Likely Roots

```text
~/.claude/projects/**/*.jsonl
$CLAUDE_CONFIG_DIR/projects/**/*.{jsonl,json,claude}
$XDG_CONFIG_HOME/claude-code/projects/**/*.{jsonl,json,claude}
```

The pinned connector uses a non-empty `CLAUDE_CONFIG_DIR` override; otherwise it considers XDG (when set) and the home root additively. `CLAUDE_HOME` is not this source's documented override. Claude project paths are often encoded into directory names under `projects`; confirm with `cwd` or record metadata rather than guessing a decoding. JSON/`.claude` exports need structural inspection; some desktop JSON contains only metadata. See the [source-backed matrix](provider-matrix.md) before widening to another profile or desktop store.

## Common Shapes

Claude-style records may place messages under:

```text
message.role
message.content
content
type
role
```

Tool use and results may be nested inside `message.content[]`:

```json
{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}
{"type":"tool_result","content":"3 passed"}
```

They may also appear as top-level tool-like records:

```json
{"type":"tool_use","name":"Edit","input":{"file_path":"app.py"}}
{"type":"tool_result","content":"updated"}
```

## Practical Rules

- Inspect nested `message.content[]` items, not just top-level `role` and `content`.
- Pair nearby `tool_use` and `tool_result` records/items before claiming a command succeeded.
- Prefer exact `tool_use.id`/`tool_result.tool_use_id` matches over adjacency. A user-role record carrying a tool result is not necessarily a human instruction.
- If a result is missing, say the tool call was found but output was not found in the inspected window.
- Prefer original user turns for instructions and feedback.
- Treat project/task/review files as secondary context for conversation-history questions.
- Project directory names may encode absolute paths; use them for scoping, but avoid exposing them in shareable reports.
- A `tool_result` may contain text, structured JSON, or a list of content blocks. Inspect it before summarizing success/failure.

## Useful Searches

```bash
rg -n --fixed-strings --max-count 5 "exact user phrase" "selected/project/session.jsonl"
rg -n --max-count 10 'tool_use|tool_result|Bash|Edit|Write|pytest|npm test|git diff' "selected/project/session.jsonl"
```
