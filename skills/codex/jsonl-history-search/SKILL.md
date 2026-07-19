---
name: jsonl-history-search
description: Use for obvious requests to search prior Codex or Claude conversations, recover what the user previously said, verify what an agent actually did, inspect source provenance, debug a decision from conversation history, or explain why a cleanup/process was deferred or failed. For a named `tasks`/`...md`, checkout/HEAD/current-Git intent uses `--evidence-source local`, conversation/source-session intent uses `--evidence-source conversation-history`, and unresolved scope requires one local-versus-history question with zero tools. In the default implicit mode, `what has happened since my last message in the parent thread?` is an exact trigger. Bare `what happened?`, `what changed in this task file?`, ordinary branch/cleanliness/local-status prompts, local repo/task/artifact status prompts, and generic questions not anchored to prior conversation history are non-triggers. A leading `convo` explicitly triggers the skill but is not itself an executable command. Route through the installed-or-source `ask --execute` command before answering from visible context.
metadata:
  short-description: Search local AI conversation history
---

# JSONL History Search

This skill is a low-context router for local Codex and Claude conversation
history. Let the CLI choose the detailed route, then inspect the returned source
references before answering.

## Activation Gate

Trigger this skill even when the user does not say JSONL or `convo` when the
request clearly depends on prior AI conversation history. Examples include:

- what the user previously said, requested, corrected, or preferred;
- what an agent actually ran, changed, tested, or claimed;
- why a prior decision or failure happened;
- why a cleanup or multi-step process was deferred, repeated, or failed;
- which conversation is the source behind a summary or outcome;
- `what happened since my last message?`; and
- the exact automatic trigger `what has happened since my last message in the
  parent thread?`, plus the narrow main-thread/parent-thread variants.

The trigger is intentionally narrow. Do not trigger for bare `what happened?`,
`what changed in this task file?`, ordinary branch/cleanliness/local-status
prompts, local repo/task/artifact status prompts, or generic questions not
anchored to prior conversation history. Ask one short clarifying question and
use zero tools when the event or evidence source is ambiguous.

If a user or project instruction sets `JSONL activation mode: explicit-only`,
natural wording alone is a non-trigger; require a leading `convo`.

## First Move

For a named `tasks`/`...md`, decide scope before any tool:

- checkout/HEAD/current-Git intent, including ancestry or local review/status:
  add exactly `--evidence-source local` after `"<user wording>"`;
- conversation/source-session intent: add exactly
  `--evidence-source conversation-history` there; or
- unresolved scope: ask "Do you want current local Git/task evidence or prior
  conversation history?" and use zero tools.

Preserve the user's wording as one quoted argument. Run this installed-or-source
resolution block before repository search, Git inspection, or synthesis from
visible context:

```bash
if command -v jsonl-history-search >/dev/null 2>&1; then
  jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
elif [ -f src/jsonl_history_search/cli.py ]; then
  PYTHONPATH=src python3 -m jsonl_history_search.cli ask --execute "<user wording>" --cwd "$PWD" --format text
else
  printf '%s\n' 'jsonl-history-search is not installed and this directory is not a source checkout' >&2
  exit 127
fi
```

The `command -v` check is non-executing resolution. In a source checkout where
the console command is absent, the first useful product command must be the
source branch's `PYTHONPATH=src python3 -m jsonl_history_search.cli ask
--execute ...`. Do not first call `convo`, `convo --help`,
`jsonl-history-search`, or `jsonl-history-search --help` and wait for failure.

## Follow The Route

The `ask --execute` result names the selected route, what to inspect, and when
to stop. Read at most the one matching file under `references/` when more route
guidance is needed:

- remembered wording or preference: `references/remembered-instruction.md`;
- remembered topic or rationale: `references/remembered-topic.md`;
- recent or parent-thread status: `references/latest-status.md`;
- action verification: `references/action-proof.md`;
- provenance or causal debugging: `references/semantic-debugging.md`;
- weak/no evidence: `references/no-evidence.md`;
- difficult retrieval: `references/retrieval-escalation.md`;
- repeated corrections or session lessons: `references/semantic-wrapup.md`;
- named skill-use audit: `references/skill-audit.md`.

## Source And Stop Rules

1. Treat CLI summaries as navigation, not as primary proof. Inspect the top
   source refs or `source_locator_handoff`/`next_reads` named by the result.
2. Use the emitted command, or open a source window with:

   ```bash
   jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text
   ```

3. Prefer original user turns for remembered wording and tool calls, tool
   outputs, diffs, or commits for action claims. Later summaries are leads.
4. State the searched scope and uncertainty. `not_found_in_searched_scope`
   never means the evidence does not exist anywhere.
5. Stop when inspected primary sources answer the question. Widen or escalate
   only for a named missing facet. If primary evidence is absent, capped,
   ambiguous, or contradictory, fail closed and say what remains unproved.
6. Never modify, delete, publish, or upload conversation data as part of a
   search request.
