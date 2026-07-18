# Skill Use Audit

Use when the user asks whether an agent triggered, read, followed, skipped, or
misapplied a named skill.

```bash
jsonl-history-search skill-check "<skill-name>" --cwd "$PWD" --format text
```

Keyword mentions alone do not prove skill use. Inspect usage instances and
their source refs, then distinguish properly applied, partially applied, missed
trigger, and misapplied behavior. Answer only from source-backed instances.
