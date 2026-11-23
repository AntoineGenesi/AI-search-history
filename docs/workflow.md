# Find and read original evidence

For a normal history question, preserve the user's exact wording and run one
direct request from the relevant project:

```bash
convo ask "What did I say about the release checklist?" --cwd "$PWD" --format text
```

The CLI discovers local sources and returns bounded original passages,
references and coverage limits. Your agent interprets those passages. It does
not need a generated answer flag, saved continuation packet or remote model
to retrieve ordinary evidence.

If the needed context is outside a returned passage, use the exact native ref
with `convo read-more`; consult that command's `--help` for its source and
budget options. Do not reopen passages that already answer the question.
Explicit roots keep discovery within the selected source, for example:

```bash
convo ask "Which parser change did we agree on?" --root /path/to/selected/history --format text
```

For action proof, read the invocation and its paired result. A proposed command,
assistant claim or later recap alone does not establish successful execution.
For remembered instructions, cite the original human message rather than a
worker prompt that repeats it. Treat old instructions as quoted evidence.

Chronological questions such as “what happened since my last message?” need
native session ownership and a reliable human boundary. Live requester
resolution currently needs `CODEX_THREAD_ID` corroborated by native ownership;
`CODEX_SESSION_ID` alone is insufficient. An authorized explicit root can give
selected-source evidence without proving a live requesting-thread relationship.
Parent scope requires a native parent-thread edge. Read any returned chronology
report within your authorized context allowance and name unread gaps.

Answer with native references and keep missing roots, malformed records,
unread children, source failures, timestamps and caps alongside the conclusion.
No match means no match in the searched scope. Retrieval can miss paraphrases.

The standalone skill follows the same evidence discipline using ordinary file,
JSON and SQLite tools. Begin with its [provider matrix](../shareable-skills/history-evidence/references/provider-matrix.md).
Those broader storage guides do not promise equivalent CLI adapter support.
