---
name: convo
description: Explicit alias for local coding-assistant conversation history. `use the convo tool` routes directly to `jsonl-history-search`; do not discover connectors, install plugins, or run `convo --help`.
metadata:
  short-description: Alias to direct history answer
---

# Convo Alias

Preserve the exact request, including parent/main/current-thread wording, and
run one command:

```bash
jsonl-history-search ask --answer '<exact user wording>' --cwd "$PWD" --format text
```

This is also the default for `what happened since my last message?`. Parent
selection and bounded interval recovery happen inside that invocation. Do not
replace missing history with visible context, Git, TODO, task/report summaries,
or another semantic search.

If the result names one recoverable missing raw-history range, run only its one
bounded recovery command. Otherwise answer from the returned raw refs or state
the precise missing/ambiguous boundary. Use `read-more <source-ref> --mode
window` only when the answer needs exact wording, chronology, or action proof.

Legacy `what-happened-since`, packet, and `execute-continuation` surfaces are
compatibility tools, not the normal workflow.

Do not invoke conversation history for ordinary repo/Git/current-file status or
a bare unanchored `what happened?`; ask one short clarification in that case.
