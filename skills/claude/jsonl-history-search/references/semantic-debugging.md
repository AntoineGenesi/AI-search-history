# Semantic Debugging And Provenance

Use when the user asks why something happened, which assumption was wrong, how
a symptom relates to earlier work, or which conversation/task/review/commit
explains an outcome.

## First Command

```bash
jsonl-history-search semantic-debug "why did this happen?" --cwd "$PWD" --format text
```

Add handles when known:

```bash
jsonl-history-search semantic-debug "why did this happen?" \
  --task <task-slug> \
  --path <path> \
  --commit <commit> \
  --around "<timestamp>" \
  --cwd "$PWD" \
  --format text
```

Use text first for receiving-agent answers. It prints the required source refs
and copy-paste `read-more` commands without exposing the full structured packet
to the model. Use `--format json --report-output <path>` only when you need to
save the packet, inspect hidden fields, or debug the tool itself.

## Read Order

1. `source_locator_handoff`
2. `strongest_candidates`
3. `next_reads`
4. `no_evidence`
5. `uncertainty`
6. relevant source refs

If the route says `answer-after-required-reads`, open the required source refs
first and answer once the named facets are covered. Do not run broad `rg` over a
large raw JSONL transcript unless a required facet is still missing after those
reads.

Keep evidence separate from inference. Say `likely contributing assumption` or
`candidate cause` unless linked evidence supports stronger language.

Treat no-evidence as searched-scope evidence, not all-history absence.
