---
name: convo
description: Explicit alias for local prior-conversation history: `use the convo tool`, latest-message status, provenance, or agent action proof. Route to `jsonl-history-search`, not connector discovery or `convo --help`. Do not use for bare `what happened?`, local status, or ordinary writing/editing.
metadata:
  short-description: Alias to JSONL history search
---

# Convo Alias

Use this alias only for explicit local conversation-history retrieval.

## First Move

For any latest-message ask paired with an explicit alias, including
parent/main-thread variants, remove only the alias and run the matching
`what-happened-since` command before repo search,
`which/command -v`, `convo --help`, connector discovery, plugin install, Git,
or visible-context synthesis:

```bash
jsonl-history-search what-happened-since "what has happened since my last message?" --cwd "$PWD" --format text
```

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

Do not invoke JSONL for ordinary writing/editing, local repo status, branch
status, current file/task status, or bare ambiguous `what happened?` prompts.
Ask one short clarifying question when the user has not asked for prior
conversation history, provenance, action proof, or latest-message status.
