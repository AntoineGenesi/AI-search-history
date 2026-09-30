---
name: jsonl-history-search
description: "Use for questions that depend on prior local coding-assistant conversations: what happened since a message, earlier instructions or decisions, action proof, provenance, failures, or evidence in a bounded history scope. `convo tool` means this CLI. Do not use for bare `what happened?`, ordinary Git/repo status, or self-contained writing."
---

# JSONL History Search

## Default

Preserve the user's exact wording and run one command:

```bash
jsonl-history-search ask --answer '<exact user wording>' --cwd "$PWD" --format text
```

Use shell-safe literal quoting. Do not rewrite the request into search terms and
do not start with `ask --execute`, `what-happened-since`, `semantic-debug`,
`execute-continuation`, `rg`, Git, task files, connector discovery, or a
provider-specific CLI.

For `what happened since my last message?`, the command must resolve the
current/side session, requested parent/main session, latest eligible human row,
and post-anchor interval internally. The latest parent human row is selected by
lineage and chronology, never semantic rank. Use only the returned history
evidence; inherited visible context is not retrieved proof.

If the installed command is genuinely unavailable and this checkout contains
`src/jsonl_history_search/cli.py`, retry the same request once:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask --answer '<exact user wording>' --cwd "$PWD" --format text
```

## One Recovery Rule

An `answered-from-proof` result is ready to summarize. A `partial`,
`not_found`, or `ambiguous` result is still useful: report its exact anchor,
searched interval, provider/source inventory, caps, and missing scope. Run at
most one concrete bounded recovery command emitted by the result when it names
a recoverable raw-history gap. Otherwise stop with that precise boundary.

Do not run a fresh broad search, ask the user to disambiguate an obvious
latest-parent interval, or use a legacy packet/continuation command on the
normal path. Legacy packet fields and `execute-continuation` exist only for
saved-report compatibility.

## One Source-Inspection Rule

When exact wording, chronology, or action proof matters, inspect the returned
raw ref directly:

```bash
jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text
```

Prefer human-message and raw tool/session refs. Treat task, report, review, Git,
and visible-context material as secondary unless the user asked about that
artifact itself. Stop when the primary refs answer the question.

## Boundaries

- Supported stores are read directly; Claude, Gemini, Grok, Aider, Cursor, and
  OpenCode CLIs do not need to be installed.
- `not_found` means not found in the reported searched scope, never all-history
  absence.
- Preserve provider-native refs and disclose missing roots, malformed/partial
  sources, weak timestamps, omitted ranges, and caps.
- Never modify, delete, upload, or publish local conversation data.
- For a bare `what happened?` with no history/thread/message/time anchor, ask
  one short clarification and use no tools.
