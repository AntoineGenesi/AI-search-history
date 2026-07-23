# Retrieval Escalation

Start with the default package retrieval. Escalate only when primary evidence
is still missing.

```bash
jsonl-history-search semantic-debug "<same question>" --retrieval-mode fielded-bm25 --cwd "$PWD" --format text
```

For audit prompts, choose depth before breadth: quick by default,
`--audit-depth in-depth` for explicit deep audits, and `--audit-depth
exhaustive` for scoped all/every worker/subagent/session/trace/conversation
asks.

Use `rrf-fusion` only for a hard, high-recall search after default and fielded
retrieval each leave a material gap. Repeated broad searches or adjacent-window
reads are a stop signal: inspect the primary refs already returned, refine one
missing facet, or report the uncertainty.
