# Latest Messages And Status Anchors

Use for recent-message lookup and natural status questions.

## Latest User Wording

For "what did I just say?", "find my last message", or exact recent wording:

```bash
jsonl-history-search latest-user-messages --format json --max-matches 10
```

Do not use semantic search first for exact latest-message recovery.

## Status Questions

For "what happened since my last message?", "what did you do while I was away?",
or "what changed after I told you to keep working?", do not blindly anchor to
the immediately previous turn.

Start with the first-class status route:

```bash
jsonl-history-search what-happened-since "what happened since my last message?" --cwd "$PWD" --format json
```

Use `latest-user-messages` only as a lower-level exact wording helper after the
status packet if the selected anchor needs manual inspection.

1. Resolve whether the intended anchor is:
   - latest literal user message;
   - last substantive work instruction;
   - unattended-work boundary;
   - side-thread/main-thread boundary.
2. State the anchor used.
3. Reconstruct from JSONL plus durable artifacts: commits, tasks, reviews,
   reports, and current state.
4. Answer value-first:
   - behavior changes;
   - measurement/results;
   - docs/claim-only changes;
   - unresolved blockers;
   - next real work.

Avoid repo-status-only or process-event-only summaries.
