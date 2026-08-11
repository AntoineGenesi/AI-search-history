# Semantic Debugging And Provenance

Use for `long_range_history`, `deep_history_audit`, `provenance`, or
`orchestration_provenance` cause/decision/failure questions.

Default is quick. Use `--audit-depth in-depth` for deep audits;
`--audit-depth exhaustive` is for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close.

Open `source_locator_handoff`/`next_reads` before summaries. Separate observed evidence from
inference. Fail closed on missing/unopened/capped/ambiguous refs.

Inspect `in_depth_audit_coverage`: cap statuses, trace counts, omitted windows,
`answer_promotion_allowed`, residual uncertainty. `answer_ready=false`: stop.
Run its emitted `audit-coverage` command; report blockers. Never `sed`/`rg`/`jq`
transcript homes. Treat task/report/review outputs as manual leads only after raw
refs. Capped/top-N samples do not
claim completeness.

For broad/pre-compaction history, run its target-preserving continuation first;
if absent, return a searched-scope/missing-facet fail-closed packet without a user question.

Method packets: `required_surfaces`/`read_order`; raw refs before artifacts.
Continue only for named gaps/caps/omissions.
