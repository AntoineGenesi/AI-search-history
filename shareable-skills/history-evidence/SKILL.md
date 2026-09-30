---
name: history-evidence
description: Read local AI conversation history across file and SQLite stores as evidence for prior-chat, remembered-instruction, provenance, action-proof, latest-status, and scoped no-evidence questions without a dedicated search CLI.
---

# History Evidence

Use this skill when the user asks about prior AI conversation history, old decisions, remembered instructions, what happened after an anchor message, whether an agent actually did something, why a prior workflow failed, or whether local history contains evidence for a claim.

Do not use it for ordinary current repo/Git status, generic writing/editing, or a bare "what happened?" with no event, message, time, thread, task, path, commit, history, or source target. For a bare ambiguous prompt, ask one short clarification and inspect nothing.

## Core Rule

Treat AI conversation history as evidence, not memory. Search hits, summaries, task notes, and assistant recaps are leads. Primary proof comes from original user turns, raw assistant/session records, structured tool calls, tool outputs, file edits/diffs, test output, timestamps, and bounded source windows.

## Reference Router

Read only the references needed for the current question:

- first local-history lookup or uncertain roots: [where-history-lives.md](references/where-history-lives.md)
- provider/storage selection and source confidence: [provider-matrix.md](references/provider-matrix.md)
- normalization and medium-specific source refs: [jsonl-record-shapes.md](references/jsonl-record-shapes.md)
- Codex logs: [codex-history.md](references/codex-history.md)
- Claude Code logs: [claude-history.md](references/claude-history.md)
- Gemini CLI, Grok Build, Aider, or other file transcripts: [file-backed-providers.md](references/file-backed-providers.md)
- Cursor or OpenCode SQLite/mixed stores: [sqlite-and-mixed-providers.md](references/sqlite-and-mixed-providers.md)
- no dedicated search tool/package is available: [search-without-tool.md](references/search-without-tool.md)
- "did the agent run/edit/test/review/use X?": [action-proof.md](references/action-proof.md)
- "what happened since..." or latest status: [latest-status.md](references/latest-status.md)
- "is there evidence..." or absence claims: [no-evidence.md](references/no-evidence.md)
- publishing, sharing, or quoting history: [privacy.md](references/privacy.md)

## Evidence Hierarchy

Primary evidence:

- original user-authored messages for instructions, preferences, feedback, or decisions;
- raw assistant/session records for what the agent said at the time;
- structured tool/function calls and their arguments;
- structured tool/function outputs, command output, test output, git output, diffs, and changed files;
- timestamps, cwd/project metadata, and source refs that tie evidence to the relevant work episode.

Secondary evidence:

- assistant summaries, final answers, task files, plans, reports, reviewer notes, commit messages, and later recaps.

Use secondary evidence as a map only unless the user asks about that artifact itself. Do not prove a user instruction from an assistant paraphrase. Do not prove an action from a plan, report, or final message.

## Default Loop

1. Classify the request: remembered instruction, action proof, latest status, provenance/why, scoped no-evidence, or broad audit.
2. Choose the smallest plausible local history scope.
3. Inventory the selected provider roots and storage family, then find candidate records with role, time, project/path, and phrase terms. Use the matching reference rather than assuming every file is JSONL.
4. Open bounded context around the best candidates using the source locator for that medium before answering. Replay event streams and resolve database/message parts when the format requires it.
5. Separate primary evidence from secondary leads.
6. Decide one status: `confirmed`, `claimed_only`, `not_found_in_searched_scope`, `not_answerable_from_inspected_scope`, `ambiguous`, or `capped`.
7. Stop when inspected primary evidence answers the named question.
8. Fail closed when required evidence is missing; name the missing source or scope instead of inventing continuity.

## Source Ref Convention

Use `path:line` for JSONL or Markdown, `path:messages[zero_based_index]` (or a stable object pointer/id) for JSON, and `path#table:primary-key` for SQLite. Include nested message/part IDs where needed. Keep provider-native session, workspace, event, and tool-call IDs; never cite a database as invented text lines. Read the normalization reference for replay and composite refs.

A source window is a few adjacent logical records, typically 2-5, plus the linked call/result or reconstruction inputs needed for the claim. A reconstructed message must retain refs to the original events or rows.

## Coverage Boundaries

Record missing roots, permission/lock errors, malformed or unknown records, metadata-only sessions, unsupported formats, omitted child sessions, and file/row/byte/time caps. Partial failure in one source does not erase valid evidence from another. Use weak timestamps only as ordering hints; file mtime or a session title cannot establish a user anchor or action outcome.

The provider matrix is a pinned source guide, not a claim that every installed version or CASS provider is supported. Inspect actual local structure before applying a schema. This skill uses ordinary local file/JSON/SQLite tools; it requires no CASS installation, provider CLI invocation, history-search package, or external upload.

Final answers should include the conclusion, the primary evidence used, the searched scope, and any important boundary such as missing roots, incomplete interval coverage, caps, ambiguity, or secondary-only support.
