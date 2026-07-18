# Latest Messages And Parent Status

For exact recent user wording, use:

```bash
jsonl-history-search latest-user-messages --format json --max-matches 10
```

For `what happened since my last message?`, use the router first. If the user
names a main or parent thread, require raw lineage/history evidence for that
relationship. Visible context and local repository state are not substitute
anchors.

Open the required history/session source refs, state which user message became
the anchor, and reconstruct only the requested window. If the parent or anchor
is ambiguous, fail closed. Stop once the required raw window answers the
question; do not crawl unrelated sessions.
