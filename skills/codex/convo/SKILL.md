---
name: convo
description: Alias entry point for explicit requests to use the local `convo` or JSONL history tool. Use only when the user explicitly says `use the convo tool`, `convo tool`, `convo CLI`, or asks for prior conversation/chat history, source provenance, agent action proof, or what happened since their last message. Do not use for generic writing/editing phrases such as making this convo friendlier.
metadata:
  short-description: Alias to JSONL history search
---

# Convo Alias

Use this alias only for explicit local conversation-history retrieval.

## First Move

For exactly either of these latest-message asks:

`what has happened since my last message? use the convo tool`
`what happened since my last message? use the convo tool`

run the matching `what-happened-since` command before repo search,
`which/command -v`, `convo --help`, connector discovery, plugin install, Git,
or visible-context synthesis:

```bash
jsonl-history-search what-happened-since "what has happened since my last message?" --cwd "$PWD" --format text
```

For other explicit history-tool aliases, immediately run:

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
