# Provider And Storage Matrix

Choose a provider and storage family before searching. These are discovery
candidates, not guarantees about every installed version. Prefer user-specified
roots, inspect actual structure, and keep provider/session/workspace IDs.

## Core Providers

| Provider | Likely roots or selected files | Storage and useful structure | Scope and reference |
| --- | --- | --- | --- |
| Codex | `$CODEX_HOME` or `~/.codex`: `sessions/**/*.jsonl`, `history.jsonl` | JSONL: `session_meta`, `turn_context`, `payload.role/type`, `cwd`, `timestamp`, `call_id`; history rows can mirror user turns. | Select dated/project sessions; use richer session evidence for actions. [Codex](codex-history.md). |
| Claude Code | Non-empty `$CLAUDE_CONFIG_DIR/projects`; otherwise `$XDG_CONFIG_HOME/claude-code/projects` when set and `~/.claude/projects` | JSONL, JSON, `.claude`: nested `message.content[]`, `tool_use.id`, `tool_result.tool_use_id`, `cwd`, session/parent IDs. Some desktop JSON is metadata-only. | Select project/session; do not infer human authorship from user-role tool results. [Claude](claude-history.md). |
| Gemini CLI | `$GEMINI_HOME` or `~/.gemini/tmp`, then `<project>/chats/session-*` | Legacy session JSON or current event JSONL: `kind: main`, `$set`, `messages`, message IDs/roles/time. | Inspect format; replay patches before treating a message as current. [File stores](file-backed-providers.md#gemini-cli). |
| Grok Build/CLI | `$GROK_HOME/sessions` or `~/.grok/sessions`, then encoded cwd/session directory | ACP `updates.jsonl`; `summary.json` metadata; fallback `chat_history.jsonl`. Preserve update types, chunk order and tool IDs/status. | Prefer authoritative updates, coalesce chunks and exclude synthetic fallback prompts. [File stores](file-backed-providers.md#grok-buildcli). |
| Cursor | OS Cursor `User` roots (`globalStorage`/`workspaceStorage`) and `~/.cursor/projects` | SQLite `state.vscdb`: inspect `cursorDiskKV`/`ItemTable`; or `<project>/agent-transcripts/<sid>/<sid>.jsonl`. Native conversation/message/workspace keys. | Select workspace/session before querying; JSONL support is reporter-documented. [Mixed stores](sqlite-and-mixed-providers.md#cursor). |
| OpenCode | `$OPENCODE_SQLITE_DB`; XDG data/config `opencode.db`; legacy `$OPENCODE_STORAGE_ROOT` or `opencode/storage` | SQLite v2: (`session_v2` or later `session`) + `session_message`, with embedded `data` JSON and native `seq`; v1: `session` + `message` + `part`; legacy JSON trees. | Discover schema, select session, retain native row IDs; decode v2 data or join v1 parts. [Mixed stores](sqlite-and-mixed-providers.md#opencode). |
| Aider | Selected project's `.aider.chat.history.md` | Markdown: quoted user lines, following unquoted assistant blocks, system preamble; no reliable per-message timestamps. | Select project file and line blocks; mtime is weak conversation-level evidence. [File stores](file-backed-providers.md#aider). |

OpenCode candidates commonly include `$XDG_DATA_HOME/opencode/opencode.db`
(`~/.local/share/opencode/opencode.db` fallback), config-root variants and
legacy `$XDG_DATA_HOME/opencode/storage`. Confirm paths before querying.
Cursor OS `User` roots commonly sit under Linux `~/.config/Cursor`, macOS
`~/Library/Application Support/Cursor`, or Windows `%APPDATA%/Cursor`; alternate
profiles and builds require explicit local discovery, not a browser-profile scan.

## Confidence And Source Boundary

The six non-Codex core contracts are based on pinned public connector source
`franken_agent_detection` revision `f3c00b742756692567539037c48745dfc3e87f5a`:
[Claude](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/claude_code.rs),
[Gemini](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/gemini.rs),
[Grok](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/grok.rs),
[Cursor](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/cursor.rs),
[OpenCode](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/opencode.rs),
[Aider](https://github.com/Dicklesworthstone/franken_agent_detection/blob/f3c00b742756692567539037c48745dfc3e87f5a/src/connectors/aider.rs).
Codex guidance describes the existing local log contract and must likewise be
checked against actual records. Connector behavior is a source-backed parsing
guide, not vendor specification or proof that a local store was inspected.
Grok behavior is empirical for grok 0.2.103; Cursor agent JSONL was reported,
not validated against real transcripts by that pinned connector.

## Cautious Second-Wave Catalog

These are format leads from the research, not fully specified adapters. Use
only a user-selected transcript/export root; inspect roles, IDs, branch order
and timestamps before applying [normalization](jsonl-record-shapes.md).

| Provider | Candidate storage; useful inspection hints |
| --- | --- |
| Pi-Agent | JSONL; retain branch/parent IDs. SQLite/native-v2 variants are not covered by this guide. |
| Copilot CLI | JSONL or legacy JSON; identify session/event records before attributing roles. |
| Qwen | Session JSON; inspect actual message arrays and tool blocks. |
| Kimi | Wire-event transcripts; reconstruct message/tool updates using native IDs. |
| Muse | Reported XDG JSONL; confirm the local transcript root and schema. |
| Factory | JSONL; inspect native session/message/tool fields. |
| Kiro CLI | JSONL; do not assume all Kiro products share CLI storage. |
| OpenHands | Per-event JSON files; preserve filename/event IDs and native order. |

Do not infer default-root completeness or support for encrypted/secret stores
from this catalog. `CASS_*` environment variables are connector settings, not
vendor configuration; this skill does not need CASS or provider CLI commands.
Missing, malformed, metadata-only, locked or unsupported sources remain explicit
coverage boundaries, not negative evidence. See [root selection](where-history-lives.md).
