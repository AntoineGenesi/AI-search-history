# Coordinator Context

Use this route when the user asks for a current coordinator or receiving-agent
context package: plans, handoff documents, TODO/front-door state, pipeline
state, current context, active task files, why the work is happening, goals,
guiding principles, learnings, and failure modes to watch.

First command:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-coordinator-context.json --format text
```

Inspect:

- `ask_execute.execution_status` is `executed`.
- `ask_execute.selected_route.route_family` is `coordinator_context`.
- `ask_execute.executed_output.required_next_reads` names the front-door files
  to open before answering or handing off.
- `ask_execute.executed_output.current_state`, `active_task_handles`,
  `why_goals_guiding_principles`, and `learnings_failure_modes` provide compact
  committed-safe context.
- `ask_execute.answer_action_contract.claim_boundaries` states what not to
  claim.

Boundaries:

- This is a committed-safe current context/next-read package, not a final
  answer.
- It does not prove source-window recovery, launch readiness, all-history
  completeness, provider token savings, speed gains, or final answer quality.
- Do not use it for actual complaint/failure mining such as "what did I keep
  correcting?" or "what did I complain about that needs fixing?" Use
  semantic-wrapup for those prompts.
- Do not use it for "what happened while I was away?" or parent/main status
  anchoring. Use latest-status/status-anchor for those prompts.
