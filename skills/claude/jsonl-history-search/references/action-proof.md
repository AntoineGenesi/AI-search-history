# Action Proof

Use when the user asks what an agent actually ran, changed, tested, or claimed.

Start with the router. If a direct rerun is needed:

```bash
jsonl-history-search semantic-debug "did the agent actually <action>" --cwd "$PWD" --format text
```

Inspect `source_window_action_proof`, `tool_action_proof`, and the primary
source follow-through. Tool calls plus their outputs, diffs, commits, and test
results can prove actions. Assistant prose and later summaries are leads, not
action proof. Stop once primary evidence answers the named action; widen only
for a specific missing facet.
