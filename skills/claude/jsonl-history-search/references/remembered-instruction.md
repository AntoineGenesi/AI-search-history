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
5. If primary user-history roots were searched and a user-authored source ref
   is still missing, stop. Do not use `retrieval-escalation.md`, broad repo
   search, TODO/task/report/review files, or project guidance to reconstruct
   what the user said.

## Answer Shape

- concise remembered instruction/preference;
- exact or near-exact wording when useful;
- source refs with paths/lines when practical;
- uncertainty and searched-scope limits.

Do not answer from a downstream summary when the original user turn is
available. If it is unavailable, fail closed with the searched scope and ask
for a quote, approximate timestamp/session anchor, or access to a primary
Codex/Claude history root. Secondary project artifacts are not proof of a
user-authored statement.
