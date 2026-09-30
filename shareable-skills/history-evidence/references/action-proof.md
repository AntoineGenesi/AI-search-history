# Action Proof

Use this reference for questions like "did the agent actually run/edit/test/review X?" or "did it only claim it?"

## Primary Proof

An action is supported when inspected primary evidence shows at least one relevant event:

- structured tool/function call with the command, edit, file path, or tool name;
- structured tool/function output for that call;
- command output showing test/git/build/review result;
- file edit/diff/change record tied to the relevant path;
- commit/review artifact when the question is about the commit/review itself.

Assistant prose, task notes, plans, reports, and final answers are not primary proof that the action happened.

## Required Standard

Use the weakest status supported by inspected primary evidence:

```text
confirmed: call/effect/output evidence matches the requested action
called_without_output_found: call found, output not found in inspected window
output_without_call_found: output found, initiating call not found in inspected window
claimed_only: only assistant/report prose claims the action
not_found_in_searched_scope: searched scope lacks primary evidence
ambiguous: chronology, tool pairing, or source identity is unclear
```

## Evidence Checks

For command/test claims, inspect:

```text
tool/function call name
arguments/input command
cwd/project/session
timestamp
nearby tool/function output
exit status or output text if available
```

For edit claims, inspect:

```text
edit/write tool call
file path arguments
diff or changed file output
later git status/diff/test evidence if available
```

For skill/tool-use claims, inspect:

```text
SKILL.md read
referenced-file reads when the skill requires them
helper/script invocation
post-skill behavior matching required workflow
```

Keyword mentions, install notes, and planning text do not prove use.

## Pairing Rules

For commands and tests, try to pair:

```text
tool call -> tool output
edit call -> changed path/diff
test command -> test output/exit status
review command/skill use -> review artifact only if the question is about that artifact
```

If a final answer says "tests passed" but the inspected session lacks the command/output, answer `claimed_only`. If a command was issued but output was truncated or missing, answer `called_without_output_found`, not confirmed.

## Common False Positives

- A plan saying "I will run tests."
- A final answer saying "Tests passed."
- A task report created after the fact.
- A copied prompt that contains a command example.
- A search hit in the current investigation rather than the historical session being investigated.
- A reviewer note summarizing another agent's work without raw refs.
