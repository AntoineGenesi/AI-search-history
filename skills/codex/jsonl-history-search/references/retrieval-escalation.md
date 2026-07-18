# Retrieval Escalation

Start with the default package retrieval. Escalate only when primary evidence
is still missing.

```bash
jsonl-history-search semantic-debug "<same question>" --retrieval-mode fielded-bm25 --cwd "$PWD" --format text
```

Use `rrf-fusion` only for a hard, high-recall search after default and fielded
retrieval each leave a material gap. Repeated broad searches or adjacent-window
reads are a stop signal: inspect the primary refs already returned, refine one
missing facet, or report the uncertainty.
