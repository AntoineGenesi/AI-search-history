# Latest Messages And Parent Status

```bash
jsonl-history-search latest-user-messages --format json --max-matches 10
jsonl-history-search latest-user-messages --cwd "$PWD" --limit 200 --snippet-chars 500 --format text
jsonl-history-search find "<terms>" --role user --cwd "$PWD" --format text
```

Use the router first. Main/parent wording requires raw lineage/history evidence. Visible context and local repository state are not substitutes.

`anchor_ambiguous`: ask its clarification and stop; text exposes no refs, commands, or snippets.

Mine user turns/corrections/reactions as leads, not proof. message timestamp is not capture/report/mtime.
Open selected assistant/tool detail with bounded `read-more`; validate with
`semantic-debug`, `follow-next-reads`, or `audit-coverage`.

Open required refs and reconstruct the anchored window. If the parent or anchor
is ambiguous, fail closed. Treat partial interval coverage or
`answer_ready=false` as a hard stop. Do not synthesize from the anchor, snippets,
or summaries. Stop only at a complete raw window; otherwise report the gap.
