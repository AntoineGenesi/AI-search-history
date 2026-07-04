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
   - If the user names an explicit task/window range such as
     `JSH-1141 -> JSH-1143` or `JSH-1141 through JSH-1143`, treat that as the
     target window and prefer its source refs/work products over newer
     current-run recovery or eval artifacts.
   - If the user says `main thread`, `parent thread`, or asks for the last
     message before a side conversation, the anchor must be the human
     `history.jsonl` row when available. Treat session mirrors, compact/current
     context, worker prompts, reviewer prompts, and sidecar handoffs as
     follow-up evidence or rejected alternatives, not as the main-thread
     boundary.
3. Follow `history.jsonl` session IDs into `sessions/**/*.jsonl`, then inspect
   the raw source refs/read-more commands before answering. Treat
   `source_tiers.required_primary` as the must-open first set.
4. Reconstruct the long window from raw JSONL first:
   - conversation actions;
   - tool calls;
   - spawned worker/reviewer/free-agent sessions;
   - compactions or missing logs.
5. Use durable artifacts only after the raw trace. Treat
   `source_tiers.required_secondary` as current-window support, not the anchor:
   - commits and merges as secondary evidence;
   - tasks, reviews, reports, and current repo state as secondary evidence.
6. Treat `source_tiers.diagnostic_optional` as optional diagnostics only.
   Stale demo/example work products, rejected anchors, broad work products, or
   older route examples must not drive the answer-facing summary unless they
   are explicitly selected current-window evidence.
   Same-run pressure artifacts, private run directories, and manual-lane final
   outputs are not required/open-first support unless the user explicitly asks
   to audit that run.
7. Say explicitly when commits/repo state are only supporting evidence.
8. Answer value-first:
   - behavior changes;
   - measurement/results;
   - docs/claim-only changes;
   - unresolved blockers;
   - next real work.

Stop once required primary refs plus any needed required secondary support are
sufficient for the answer. Do not blanket-crawl worker/reviewer side threads;
open spawned worker/reviewer refs only when the status question asks for
delegated work or the selected current window includes those spawned refs.

Avoid commit-only, repo-status-only, summary-only, or process-event-only
answers. If the raw JSONL session trace is missing or partial, say that as an
uncertainty instead of substituting a generic repo status answer.
