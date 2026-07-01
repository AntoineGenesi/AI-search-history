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
4. If the latest/current session is dominated by fresh receiving-agent prompts,
   benchmark/lane setup text, or nearby task-orchestration echoes, fail closed
   or rerun with the parent/session `--root`; do not turn setup echoes into the
   remembered complaint set.
5. If an explicit root or a substantive current session includes real
   orchestration, inspect `orchestration_evidence` as first-class causal
   evidence: user corrections, main-agent FSM/product-loop commitments,
   worker/subagent outputs, reviewer proof and claim boundaries, same-task
   repair logic, product-delta integration/currentness findings, and
   product-moving versus process/report drift.
6. Detect the local behavior system: `AGENTS.md`, `CLAUDE.md`, task files,
   TODO/backlog docs, skills, learning indexes, tickets, or project-specific
   rules.
7. Present user-selectable fix suggestions with `destination_source_refs`,
   `destination_rationale`, and `selection_options` of accept, reject, or
   defer.
8. Do not auto-edit behavior rules, skills, task files, tickets, or memory
   docs from this route.
9. Do not assume everyone has contributor's `wrap-up` skill.

After running:

- inspect `failure_modes[*].source_refs`;
- inspect substantive `orchestration_evidence.items[*].source_ref` before
  deciding what worker/reviewer/subagent behavior mattered;
- use `signal_classification` to separate substantive session evidence from
  setup/prompt echoes;
- treat `inferred_steps` as bounded hypotheses, not deep root-cause certainty;
- review `fix_suggestions`;
- ask which suggestions to accept, reject, or defer;
- implement selected fixes through the normal task/FSM workflow.
