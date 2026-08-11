---
name: convo
description: Explicit alias for local prior-conversation history: `use the convo tool`, latest-message status, provenance, or agent action proof. Route to `jsonl-history-search`, not connector discovery or `convo --help`. Do not use for bare `what happened?`, local status, or self-contained writing/editing; history-dependent writing is allowed.
metadata:
  short-description: Alias to JSONL history search
---

# Convo Alias

Use this alias only for explicit local conversation-history retrieval.

## First Move

For a parent/main latest-message ask paired with an explicit alias, remove only
the alias, preserve parent scope with `--parent-thread`, and run the matching
`what-happened-since` command before repo search,
`which/command -v`, `convo --help`, connector discovery, plugin install, Git,
or visible-context synthesis:

```bash
jsonl-history-search what-happened-since "what has happened since my last message?" --parent-thread --cwd "$PWD" --complete-interval --format answer
```

For unscoped latest-message wording, omit `--parent-thread` and
`--complete-interval`, and use `--format text`.

For explicit history-tool aliases that are not latest-message asks, immediately
run:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
```

Then follow the returned refs and stop rules from the JSONL history output.
For latest-status, inspect `post_anchor_interval_reconstruction` coverage and
its `next_read_guidance`; do not answer from the selected anchor, visible
context, or secondary task/report/Git evidence alone.

## Non-Triggers

Do not invoke JSONL for self-contained writing/editing, local repo status,
branch status, current file/task status, or bare ambiguous `what happened?`
prompts. Writing/editing that depends on prior conversation remains in scope.
Ask one short clarifying question when the user has not asked for prior
conversation history, provenance, action proof, or latest-message status.
