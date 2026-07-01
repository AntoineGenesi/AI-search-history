# Action Proof And Source Inspection

Use when the user asks whether an agent actually ran, edited, tested, reviewed,
overwrote, preserved, merged, or touched something.

## First Command

```bash
jsonl-history-search semantic-debug "did the agent actually <action>" --cwd "$PWD" --format text
```

Add `--task`, `--path`, `--commit`, or `--around` when known.

Use `--format json --report-output <path>` only when another command must
consume the full structured report. For ordinary answering, prefer text output
so the primary source followthrough command and stop rule are visible without
JQ/report probing.

## What To Inspect

Read these before broad search:

- `semantic_debug.answer_ready_handoff.primary_source_followthrough` for the
  first source-window command to run and the stop/continue rule;
- `semantic_debug.action_proof_plan` for primary proof refs, required reads,
  optional diagnostics, stop condition, and retrieval-mode escalation;
- `semantic_debug.source_window_action_proof`;
- `tool_action_proof`;
- `task_review_commit_joins`;
- `source_locator_handoff.next_reads`;
- top source refs.

Classify evidence:

- primary proof: tool call/output, diff, commit, test output, review verdict;
- adjacent lead: task/report prose, later summary, same-file nearby window;
- diagnostic: useful context but not proof of the action.

## Stop Rule

If primary proof answers the action question, stop broad searching and answer
with source refs. Run more commands only for a missing required facet, such as
which file, which commit, whether unrelated work was untouched, or whether a
claim conflicts with tool output.

If the agent keeps searching or the output only offers adjacent leads, read
`retrieval-escalation.md`.
