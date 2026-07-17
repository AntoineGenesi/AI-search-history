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

## Source Followthrough Protocol

For explicit product/history questions such as `what happened at JSH-1274?`,
`why did JSH-1272 fail closed?`, or `which source session proves the route
repair?`, use this staged answer protocol:

1. Route first with `ask`, then run the printed `semantic-debug` command.
2. Open primary raw/session/tool source refs from the first `semantic-debug`
   output: `source_locator_handoff.next_reads`, promoted source refs,
   source-window reads, tool/action proof refs, or required follow-through
   reads.
3. Use secondary task/review/report/commit artifacts only after primary refs
   have been opened. These artifacts can explain chronology, merges, review
   verdicts, and task state, but they do not replace primary source evidence for
   what the user, agent, tool, or session actually did or said.
   If a retained read returns `missing-source-session-stop`, do not open task,
   review, scratch, report, or summary artifacts to recover that facet. Report
   independently proved Git/task/review facts separately and leave the
   source-session facet missing until role-valid raw/session proof is opened.
4. Treat post-hoc answer-key artifacts, current-run/same-loop echoes,
   prior-final summaries, adjudication material, and reports created after the
   target work as diagnostics unless the user explicitly asks to audit that
   artifact.
5. Answer only the facets covered by inspected primary refs plus necessary
   secondary context. Keep inference labels such as `likely` or `candidate`
   when the source chain is incomplete.
6. Fail closed when primary refs are absent, weak, capped, contradicted, or not
   opened. Say what was searched, which primary source role is missing, which
   secondary artifacts were only leads, and the next bounded read or escalation
   route.

If the route says `answer-after-required-reads`, open the required source refs
first and answer once the named facets are covered. Do not answer from task,
review, report, or commit artifacts before opening the primary source refs from
the first route output. Do not run broad `rg` over a large raw JSONL transcript
unless a required facet is still missing after those reads.

Keep evidence separate from inference. Say `likely contributing assumption` or
`candidate cause` unless linked evidence supports stronger language.

Treat no-evidence as searched-scope evidence, not all-history absence.

## No-Evidence Stop Rule

For explicit no-evidence questions with a known bounded root or source scope,
prefer the bounded no-evidence route after ask-first routing:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-no-evidence-report.json --format text
```

This keeps stdout compact, saves the full report for review/debugging, and
prints the scoped no-evidence answer/stop contract. After the command, read
`references/no-evidence.md` only if a scope/cap field needs interpretation. If
the promoted contract says `answer_scope: not_found_in_searched_scope`,
positive searched files/bytes, no missing root, no cap hit,
`not_all_history_absence: true`, and no required read-more, answer from the
searched scope. If the report says `candidate_interpretation:
weak_context_only`, the surfaced candidates matched
context or partial query terms only. If no `next_reads` are listed, that is an
intentional stop signal; do not manufacture or run `read-more` commands from
source refs before a searched-scope no-evidence answer. Preserve
`answer_scope: not_found_in_searched_scope` and do not claim all-history
absence.
