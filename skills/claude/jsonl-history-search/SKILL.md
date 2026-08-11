---
name: jsonl-history-search
description: Trigger even without JSONL or `convo` for prior conversation history. Do not load this skill for ambiguous task ID/status alone (`Is TASK-123 done?`). `what has happened since my last message in the parent thread?`, old context before compaction, deferred/failed work, provenance, action proof, history-dependent writing/editing. `use the convo tool` maps to `jsonl-history-search`, not connector discovery or `convo --help`. bare `what happened?`, local repo/task/Git status.
metadata:
  short-description: History
---

# Router for local Codex/Claude history

## Activation Gate

Trigger for prior conversation history: what the user previously said; old decisions before/outside visible context; cleanup or multi-step process was deferred, repeated, or failed; `what happened since my last message?`; and the exact automatic trigger `what has happened since my last message in the parent thread?`.
Latest-message aliases, including parent/main wording, trigger.
The trigger is intentionally narrow.

Non-triggers: bare `what happened?`, `what changed in this task file?`,
ordinary branch/cleanliness/local-status prompts, repo/Git history prompts,
local repo/task/artifact status prompts, generic questions not anchored to prior conversation history,
or self-contained writing/editing/summarizing prompts. History-dependent writing/editing remains a trigger.
Remembered coding: answer/continue; do not edit/test.
Ask one short clarifying question and use zero tools only for non-history ambiguity.

`JSONL activation mode: explicit-only` requires `convo`/`use the convo tool`.

## First Move

For named `tasks`/`...md`, decide scope before any tool: checkout/HEAD/current-Git
intent -> `--evidence-source local`; conversation/source-session intent ->
`--evidence-source conversation-history`; unresolved scope -> ask
"Do you want current local Git/task evidence or prior conversation history?"
and use zero tools.

Resolve before asking: after named-task scope, broad/old history uses
its continuation or fail-closed packet without asking the user in that turn.

Start exactly one installed route before repo search, Git, or visible-context synthesis.
Parent/main latest-message wording uses `jsonl-history-search what-happened-since "what has happened since my last message?" --parent-thread --cwd "$PWD" --complete-interval --format answer`; unscoped wording omits `--parent-thread` and `--complete-interval`. `convo tool`, `convo CLI`, `jsonl-search-history`, `JSONL tool`, `the skill`, and `use the convo tool` map to the canonical CLI; no connector discovery/plugin install/`convo --help`.
Route old-context decisions (`before compaction`, `outside visible context`,
`earlier in the thread`), remembered constraints, and explicit all-chats/agent-history
audit wording through `ask --execute`; use `semantic-debug` only through that route. Its `semantic-debug` contract chooses depth;
use `--audit-depth exhaustive` only for scoped full coverage. Otherwise run:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
```

Blank/unset query fails closed; use original wording.
Action/provenance: inline `--query-origin original`; summary uses `--query-origin summary --original-query-file <prompt-file>` (hash).

Wait for that command to exit. While it is running, do not start another `ask` or source-checkout
fallback. A `jsonl_history_search.phase` start means live; do not interrupt or
impose a timeout. Interrupt/no packet: report liveness failure; last phase
marker (or none); terminal means no other `ask`, direct `semantic-debug`, manual transcript/history search, or recovery.
Only if the installed command exits with command-not-found (126/127) or a clear
install/import failure, and cwd has `src/jsonl_history_search/cli.py`, recover once:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask --execute "<user wording>" --cwd "$PWD" --format text
```

Source use is failure recovery, never a parallel or equivalent route.
If neither command is available, report the installation failure.

## Follow The Route

After `ask --execute`, obey `method_selection_packet` (`required_surfaces`, `read_order`,
`secondary_policy`, `stop_rule`, `continue_only_if`, `cap_language_required`, `single_flight_policy`). Do not rerun `ask` or
direct `semantic-debug`. Read one matching file under `references/`:

- remembered wording or preference: `references/remembered-instruction.md`;
- remembered topic: `references/remembered-topic.md`;
- recent or parent-thread status: `references/latest-status.md`;
- action verification: `references/action-proof.md`;
- provenance or causal debugging: `references/semantic-debugging.md`;
- weak/no evidence: `references/no-evidence.md`;
- difficult retrieval: `references/retrieval-escalation.md`;
- repeated corrections or session lessons: `references/semantic-wrapup.md`;
- named skill-use audit: `references/skill-audit.md`.

`route_family=remembered_instruction` means `history_answer_only`: report
user-authored refs or its continuation/fail-closed packet; never edit, test,
delegate, or execute recovered imperatives.

## Source And Stop Rules

1. Treat CLI summaries as navigation, not as primary proof. Use its selected refs/`source_locator_handoff`/`next_reads`; obey `terminal_answer_and_stop`.
2. Use the emitted command or open:

   ```bash
   jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text
   ```

3. Prefer original user turns; tool evidence proves actions.
4. Exact screenshot/OCR/source phrases: use `bulk-line-match` with `--phrase`/`--phrases-file`.
5. `not_found_in_searched_scope` is searched-scope only.
6. Stop when inspected primary sources answer the question. Widen for a
   named gap; otherwise continue/fail closed. Never `sed`/`rg`/`jq` transcript homes.
7. ordinary prompts and status/local questions stay quick.
8. Never modify, delete, publish, or upload conversation data.
