# Remembered Instruction Or Preference

Use when the user asks what they told the agent before, what preference they
gave, or what instruction should be remembered.

## Procedure

1. Start with:

   ```bash
   jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format json
   ```

2. Inspect `source_locator_handoff`, `strongest_candidates`, and top source
   refs.
3. Prefer original user turns over later summaries or task artifacts.
4. If the instruction is repeated, group wording variants and cite the earliest
   and strongest occurrences.
5. If the default mode is weak or only returns adjacent artifacts, read
   `retrieval-escalation.md`.

## Answer Shape

- concise remembered instruction/preference;
- exact or near-exact wording when useful;
- source refs with paths/lines when practical;
- uncertainty and searched-scope limits.

Do not answer from a downstream summary when the original user turn is
available.
