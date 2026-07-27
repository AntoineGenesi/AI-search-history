---
name: jsonl-history-search
description: Use for Codex or Claude conversation search, recovering what the user previously said, verifying agent actions, inspecting provenance, debugging decisions, or explaining why a cleanup/process was deferred or failed. For a named `tasks`/`...md`, checkout/HEAD/current-Git intent uses `--evidence-source local`, conversation/source-session intent uses `--evidence-source conversation-history`, and unresolved scope requires one local-versus-history question with zero tools. In default implicit mode, `what has happened since my last message in the parent thread?` is an exact trigger. Explicit "read through all chats/agent history" asks trigger in-depth/exhaustive `semantic-debug`, not quick ask. Bare `what happened?`, `what changed in this task file?`, ordinary branch/cleanliness/local-status prompts, local repo/task/artifact status prompts, and generic questions not anchored to prior conversation history are non-triggers. A leading `convo` explicitly triggers the skill but is not itself an executable command.
metadata:
  short-description: Search local AI conversation history
---

# JSONL History Search

Router for local Codex/Claude history. Let CLI route; inspect refs before
answering.

## Activation Gate

Trigger even without JSONL or `convo` when the request depends on prior AI
conversation history. Examples include:

- what the user previously said, requested, corrected, or preferred;
- what an agent actually ran, changed, tested, or claimed;
- why a prior decision or failure happened;
- why a cleanup or multi-step process was deferred, repeated, or failed;
- which conversation is the source behind a summary or outcome;
- read through all chats/agent history and find when X or Y happened;
- `what happened since my last message?`; and
- the exact automatic trigger `what has happened since my last message in the
  parent thread?`, plus the narrow main-thread/parent-thread variants.

The trigger is intentionally narrow. Do not trigger for bare `what happened?`,
`what changed in this task file?`, ordinary branch/cleanliness/local-status
prompts, local repo/task/artifact status prompts, or generic questions not
anchored to prior conversation history. Ask one short clarifying question and
use zero tools when the event or evidence source is ambiguous.

If `JSONL activation mode: explicit-only` is set, natural wording is a
non-trigger; require a leading `convo`.

## First Move

For a named `tasks`/`...md`, decide scope before any tool:

- checkout/HEAD/current-Git intent, including ancestry or local review/status:
  add exactly `--evidence-source local` after `"<user wording>"`;
- conversation/source-session intent: add exactly
  `--evidence-source conversation-history` there; or
- unresolved scope: ask "Do you want current local Git/task evidence or prior
  conversation history?" and use zero tools.

Preserve user wording as one quoted argument. Start exactly one installed
history route before repo search, Git, or visible-context synthesis. For
explicit all-chats/agent-history audit wording, run:

```bash
jsonl-history-search semantic-debug "<user wording>" --audit-depth in-depth --cwd "$PWD" --format text
```

Use `--audit-depth exhaustive` only for scoped full coverage; keep
coverage/cap/omitted-window caveats and fail closed. Otherwise run:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
```

Wait for that command to exit. While it is running, do not start another `ask`,
`ask --execute`, direct `semantic-debug`, installed helper, or source-checkout
fallback. Only if it exits with command-not-found (126/127) or a clear
install/import failure, and the current directory contains
`src/jsonl_history_search/cli.py`, run this source recovery once:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask --execute "<user wording>" --cwd "$PWD" --format text
```

The source command is failure recovery, never a parallel or equivalent route.
If neither command is available, report the installation failure. Do not probe
with `convo`, `--help`, repository inventory, or another history route.

## Follow The Route

After the one `ask --execute` exits, use its selected refs, inspection contract,
and stop rule. Do not rerun `ask` or direct `semantic-debug` for the same
evidence. Read at most one matching file under `references/`:

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

1. Treat CLI summaries as navigation, not as primary proof. Inspect
   refs or `source_locator_handoff`/`next_reads` named by the result.
2. Use the emitted command, or open a source window with:

   ```bash
   jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text
   ```

3. Prefer original user turns for wording and tool calls, outputs, diffs, or
   commits for action claims. Later summaries are leads.
4. State the searched scope and uncertainty. `not_found_in_searched_scope`
   never means the evidence does not exist anywhere.
5. Stop when inspected primary sources answer the question. Widen or escalate
   only for a named missing facet. If primary evidence is absent, capped,
   ambiguous, or contradictory, fail closed and say what remains unproved.
6. Explicit full/deep history audits must preserve CLI audit-depth coverage
   caveats; ordinary prompts and status/local questions stay quick.
7. Never modify, delete, publish, or upload conversation data.
