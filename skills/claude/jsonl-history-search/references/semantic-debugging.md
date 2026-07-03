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

## No-Evidence Stop Rule

For explicit no-evidence questions with a known bounded root or source scope,
prefer the bounded no-evidence route after ask-first routing:

```bash
jsonl-history-search no-evidence-report "<user wording>" --cwd "$PWD" --report-output .private/jsonl/no-evidence-report.json --format json
```

This keeps stdout compact and saves the full report for review/debugging. After
the command, read `references/no-evidence.md` and inspect searched scope,
answer scope, weak/context interpretation, and read-more stop fields. If the
report says
`candidate_interpretation: weak_context_only`, the surfaced candidates matched
context or partial query terms only. If no `next_reads` are listed, that is an
intentional stop signal; do not manufacture or run `read-more` commands from
source refs before a searched-scope no-evidence answer. Preserve
`answer_scope: not_found_in_searched_scope` and do not claim all-history
absence.
