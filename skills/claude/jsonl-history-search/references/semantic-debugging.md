# Semantic Debugging And Provenance

Use for historical cause/decision/failure/source-provenance, `long_range_history`,
`deep_history_audit`, `provenance`, or `orchestration_provenance` questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Default is quick. Use `--audit-depth in-depth` for explicit deep audit asks. Use `--audit-depth exhaustive` for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close.

Open `source_locator_handoff`/`next_reads` refs before summaries. Separate observed evidence from inference; say `candidate`/`likely` when incomplete. Fail closed when primary refs are missing/unopened/capped/ambiguous/contradicted.

For in-depth/exhaustive output, check `in_depth_audit_coverage`: windows/slices,
cap statuses, trace counts, `answer_promotion_allowed`. Treat task/report/review outputs as manual leads. If capped/incomplete, report residual uncertainty and
next read or `audit-coverage` command; do not
claim completeness.

Method packets: `required_surfaces`/`read_order`; raw refs before artifacts/echoes/recaps. Continue only for packet-named gap/cap/omission/contradiction.
