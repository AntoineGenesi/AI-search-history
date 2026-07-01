# Semantic Wrap-Up / Failure-Mode Mining

Use this specialization when the user asks what should be learned from a
session, what went wrong, what complaints/corrections should become fixes, or
for a wrap-up that should improve future agent behavior.

Trigger on asks such as:

- "semantic wrap-up this run"
- "what did I complain about that needs fixing?"
- "what should we learn from this session?"
- "find the workflow failures and suggest fixes"
- "mine this session for failure modes"

First command:

```bash
jsonl-history-search semantic-wrapup \
  "what failure modes should we fix from this session?" \
  --cwd "$PWD" \
  --format json
```

If a specific transcript/session root is known, pass it with `--root`.

Rules:

1. Treat frustration, corrections, and repeated objections as signals for
   actionable failure mining, not as sentiment summary.
2. Group related complaints into failure modes with source refs.
3. Separate evidence from inferred cause chains:
   `failure_modes[*].cause_chain.evidence_refs` are source-backed, while
   `failure_modes[*].cause_chain.inferred_steps` are hypotheses to inspect.
4. Detect the local behavior system: `AGENTS.md`, `CLAUDE.md`, task files,
   TODO/backlog docs, skills, learning indexes, tickets, or project-specific
   rules.
5. Present user-selectable fix suggestions with `destination_source_refs`,
   `destination_rationale`, and `selection_options` of accept, reject, or
   defer.
6. Do not auto-edit behavior rules, skills, task files, tickets, or memory
   docs from this route.
7. Do not assume everyone has contributor's `wrap-up` skill.

After running:

- inspect `failure_modes[*].source_refs`;
- treat `inferred_steps` as bounded hypotheses, not deep root-cause certainty;
- review `fix_suggestions`;
- ask which suggestions to accept, reject, or defer;
- implement selected fixes through the normal task/FSM workflow.
