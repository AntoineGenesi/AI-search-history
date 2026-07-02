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
jsonl-history-search what-happened-since "what happened since my last message?" --cwd "$PWD" --format text
```

Use `latest-user-messages` only as a lower-level exact wording helper after the
status packet if the selected anchor needs manual inspection.

1. Resolve whether the intended anchor is:
   - latest literal user message;
   - last substantive work instruction;
   - unattended-work boundary;
   - side-thread/main-thread boundary.
2. State the anchor used.
3. Follow `history.jsonl` session IDs into `sessions/**/*.jsonl`, then inspect
   the raw source refs/read-more commands before answering.
4. Reconstruct the long window from raw JSONL first:
   - conversation actions;
   - tool calls;
   - spawned worker/reviewer/free-agent sessions;
   - compactions or missing logs.
5. Use durable artifacts only after the raw trace:
   - commits and merges as secondary evidence;
   - tasks, reviews, reports, and current repo state as secondary evidence.
6. Say explicitly when commits/repo state are only supporting evidence.
7. Answer value-first:
   - behavior changes;
   - measurement/results;
   - docs/claim-only changes;
   - unresolved blockers;
   - next real work.

Avoid commit-only, repo-status-only, summary-only, or process-event-only
answers. If the raw JSONL session trace is missing or partial, say that as an
uncertainty instead of substituting a generic repo status answer.
