# Semantic Debugging And Provenance

Use for historical cause, decision, failure, or source-provenance questions.

```bash
jsonl-history-search semantic-debug "<question>" --cwd "$PWD" --format text
```

Open primary conversation/session/tool refs from `source_locator_handoff` and
`next_reads` before relying on summaries. Separate observed evidence from
inference, and say `candidate` or `likely` when the chain is incomplete. Later
artifacts may explain chronology but do not replace raw evidence for what a
user, agent, or tool said or did. Fail closed when required primary refs are
missing, unopened, capped, ambiguous, or contradicted.
