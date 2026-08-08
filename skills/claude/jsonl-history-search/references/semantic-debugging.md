# Semantic Debugging And Provenance

Use for historical cause/decision/failure/source-provenance, `long_range_history`,
`deep_history_audit`, `provenance`, or `orchestration_provenance` questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Default is quick. Use `--audit-depth in-depth` for explicit deep audit asks. Use `--audit-depth exhaustive` for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close.

Open `source_locator_handoff`/`next_reads` refs before summaries. Separate observed evidence from inference; say `candidate`/`likely` when incomplete. Fail closed when primary refs are missing/unopened/capped/ambiguous/contradicted.

For deep output, check `audit_continuation_workflow`, then
`in_depth_audit_coverage`. Treat `answer_ready=false` as a hard stop: open
ordered primary refs, run its scoped continuation/refinement commands, and
fail closed if caps/gaps remain. Task/report/review/Git are leads after
raw refs; capped/top-N samples cannot prove completeness.

Method packets: `required_surfaces`/`read_order`; raw refs before artifacts/echoes/recaps. Continue only for packet-named gap/cap/omission/contradiction.
