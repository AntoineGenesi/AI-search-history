# Latest Messages And Parent Status

For user wording/correction leads:

```bash
jsonl-history-search latest-user-messages --format json --max-matches 10
jsonl-history-search latest-user-messages --cwd "$PWD" --limit 200 --snippet-chars 500 --format text
jsonl-history-search find "<terms>" --role user --cwd "$PWD" --format text
```

For status asks, use the router first. For main/parent wording, require raw lineage/history evidence. Visible context and local repository state are not substitutes.

Mine user turns/corrections/reactions as leads, not proof. message timestamp is not capture/report/mtime.
Open selected assistant/tool detail with bounded `read-more`; validate with
`semantic-debug`, `follow-next-reads`, or `audit-coverage`.

Open required refs, state the anchor, and reconstruct only that window. If the parent or anchor
is ambiguous, fail closed. Stop once the raw window answers.
