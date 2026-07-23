# Semantic Debugging And Provenance

Use for historical cause, decision, failure, or source-provenance questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Default is quick. Use `--audit-depth in-depth` only for explicit deep/granular
audit asks. Use `--audit-depth exhaustive` for scoped full asks like "fully
audit everything" or "audit every subagent"; capped/top-N samples cannot close
it.

Open primary conversation/session/tool refs from `source_locator_handoff` and
`next_reads` before relying on summaries. Separate observed evidence from
inference, and say `candidate` or `likely` when the chain is incomplete. Later
artifacts may explain chronology but do not replace raw evidence for what a
user, agent, or tool said or did. Fail closed when required primary refs are
missing, unopened, capped, ambiguous, or contradicted.

For in-depth/exhaustive output, check `in_depth_audit_coverage`: windows/slices,
cap statuses, trace counts, and `answer_promotion_allowed`. Treat
task/report/review outputs as manual leads. If capped/incomplete, report
residual uncertainty and the next read or `audit-coverage` command; do not
claim completeness.
