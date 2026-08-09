# Semantic Debugging And Provenance

Use for historical cause/decision/failure/source-provenance, `long_range_history`,
`deep_history_audit`, `provenance`, or `orchestration_provenance` questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Default is quick. Use `--audit-depth in-depth` for explicit deep audit asks. Use `--audit-depth exhaustive` for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close.

Open `source_locator_handoff`/`next_reads` refs before summaries. Separate observed evidence from inference; say `candidate`/`likely` when incomplete. Fail closed when primary refs are missing/unopened/capped/ambiguous/contradicted.

Inspect `audit_continuation_workflow` and `in_depth_audit_coverage`: cap
statuses, trace counts, omitted windows, `answer_promotion_allowed`, and residual
uncertainty. `answer_ready=false`: hard stop. Open primary refs, run scoped
continuation/refinement or the `audit-coverage` command; fail closed on gaps. Use
task/report/review outputs as manual leads only after raw refs. Capped/top-N
samples do not
claim completeness.

Method packets: `required_surfaces`/`read_order`; raw refs before artifacts/echoes/recaps. Continue only for packet-named gap/cap/omission/contradiction.
