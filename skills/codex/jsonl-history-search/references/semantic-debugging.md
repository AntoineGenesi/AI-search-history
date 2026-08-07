# Semantic Debugging And Provenance

Use for historical cause/decision/failure/source-provenance, `long_range_history`,
`deep_history_audit`, `provenance`, or `orchestration_provenance` questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Default is quick. Use `--audit-depth in-depth` for explicit deep audit asks. Use `--audit-depth exhaustive` for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close.

Open `source_locator_handoff`/`next_reads` refs before summaries. Separate observed evidence from inference; say `candidate`/`likely` when incomplete. Fail closed when primary refs are missing/unopened/capped/ambiguous/contradicted.

For in-depth/exhaustive output, check `audit_continuation_workflow` first when
present, then `in_depth_audit_coverage`: windows/slices, cap statuses, trace
counts, `answer_promotion_allowed`. `audit_continuation_workflow.answer_ready=false`
means hard stop / not answerable yet. Open
`ordered_primary_refs_to_open_first`, then run the emitted scoped continuation
or refinement commands for each pending date/path/cap slice that affects the
claim. Only answer after those slices are covered or explicitly labeled
capped/blocked; otherwise fail closed with the remaining gaps.

Treat task/report/review/Git outputs as secondary joins or manual leads after
raw transcript/source refs, not as a replacement for capped JSONL continuation.
Do not abandon JSONL for broad Git/rg/task artifact inspection unless the user
explicitly asks for local artifacts. Do not claim completeness from capped or
top-N samples.

Method packets: `required_surfaces`/`read_order`; raw refs before artifacts/echoes/recaps. Continue only for packet-named gap/cap/omission/contradiction.
