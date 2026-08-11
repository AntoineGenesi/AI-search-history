# Conversation Failure-Mode Mining

Use when the user asks what repeated corrections, complaints, or failures should teach future work.

```bash
jsonl-history-search semantic-wrapup "what failure modes should we learn from this session?" --cwd "$PWD" --format json
```

Inspect required primary refs before grouped failure modes. Separate evidence
from inferred causes, ignore setup/prompt echoes, and present choices, not edits.
Stop after required refs support themes.
