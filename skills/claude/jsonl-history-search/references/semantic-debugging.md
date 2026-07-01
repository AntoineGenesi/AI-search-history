# Semantic Debugging And Provenance

Use when the user asks why something happened, which assumption was wrong, how
a symptom relates to earlier work, or which conversation/task/review/commit
explains an outcome.

## First Command

```bash
jsonl-history-search semantic-debug "why did this happen?" --cwd "$PWD" --format json
```

Add handles when known:

```bash
jsonl-history-search semantic-debug "why did this happen?" \
  --task <task-slug> \
  --path <path> \
  --commit <commit> \
  --around "<timestamp>" \
  --cwd "$PWD" \
  --format json
```

## Read Order

1. `source_locator_handoff`
2. `strongest_candidates`
3. `next_reads`
4. `no_evidence`
5. `uncertainty`
6. relevant source refs

Keep evidence separate from inference. Say `likely contributing assumption` or
`candidate cause` unless linked evidence supports stronger language.

Treat no-evidence as searched-scope evidence, not all-history absence.
