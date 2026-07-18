# Conversation Failure-Mode Mining

Use when the user asks what repeated corrections, complaints, or failures in a
conversation should teach future work.

```bash
jsonl-history-search semantic-wrapup "what failure modes should we learn from this session?" --cwd "$PWD" --format json
```

Inspect required primary source refs before presenting grouped failure modes.
Keep evidence separate from inferred causes, ignore setup/prompt echoes, and
present suggested changes as choices rather than editing anything
automatically. Stop after the required refs support the returned themes.
