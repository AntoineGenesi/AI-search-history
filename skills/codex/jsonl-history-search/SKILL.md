---
name: jsonl-history-search
description: cleanup/process was deferred or failed. For named `tasks`/`...md`: `--evidence-source local` for checkout/HEAD/current-Git intent, `--evidence-source conversation-history` for conversation/source-session intent, or zero tools for unresolved scope. Trigger: `what has happened since my last message in the parent thread?`. Bare `what happened?`, `what changed in this task file?`, ordinary branch/cleanliness/local-status prompts, local repo/task/artifact status prompts, and generic questions not anchored to prior conversation history are non-triggers. A leading `convo` explicitly triggers the skill but is not itself an executable command; aliases `convo tool`, `convo CLI`, `jsonl-search-history`, `jsonl-history-search`, `JSONL tool`, `the skill`, or `use the convo tool` maps to `jsonl-history-search`, not connector discovery or `convo --help`.
metadata:
  short-description: History
---

# JSONL History Search

Router for local Codex/Claude history.

## Activation Gate

Trigger even without JSONL or `convo` when the request depends on prior AI
conversation history:

- what the user previously said;
- old decisions before/outside visible context;
- why a cleanup or multi-step process was deferred, repeated, or failed;
- `what happened since my last message?`; and
- the exact automatic trigger `what has happened since my last message in the
  parent thread?`.

The trigger is intentionally narrow. Do not trigger for bare `what happened?`,
`what changed in this task file?`, ordinary branch/cleanliness/local-status
prompts, local repo/task/artifact status prompts, or generic questions not
anchored to prior conversation history. Ask one short clarifying question and
use zero tools when the event or evidence source is ambiguous.

If `JSONL activation mode: explicit-only` is set, natural wording is a
non-trigger; require an explicit history-tool alias such as leading `convo` or
`use the convo tool`.

## First Move

For a named `tasks`/`...md`, decide scope before any tool:

- checkout/HEAD/current-Git intent:
  add exactly `--evidence-source local` after `"<user wording>"`;
- conversation/source-session intent: add exactly
  `--evidence-source conversation-history` there; or
- unresolved scope: ask "Do you want current local Git/task evidence or prior
  conversation history?" and use zero tools.

Start exactly one installed route before repo/Git/context synthesis. Exact alias
`what has happened since my last message? use the convo tool`: run `jsonl-history-search what-happened-since "what has happened since my last message?" --cwd "$PWD" --format text`. `use the convo tool`/`convo CLI`: `jsonl-history-search`; no
connector discovery/plugin install/`convo --help` first. old-context decisions
like `before compaction`, `outside visible context`, or `earlier in the thread`
use `semantic-debug`. For explicit all-chats/agent-history audit wording, run:

```bash
jsonl-history-search semantic-debug "<user wording>" --audit-depth in-depth --cwd "$PWD" --format text
```

Use `--audit-depth exhaustive` only for scoped full coverage; fail closed.
Otherwise run:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
```

Wait for that command to exit. While it runs, do not start another `ask`,
`ask --execute`, direct `semantic-debug`, or source-checkout
fallback. Only if
the installed command exits with command-not-found (126/127) or a clear
install/import failure, and cwd contains `src/jsonl_history_search/cli.py`, run
recovery once:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask --execute "<user wording>" --cwd "$PWD" --format text
```

The source command is failure recovery, never a parallel or equivalent route.
If neither command is available, report the installation failure.

## Follow The Route

After one `ask --execute`, follow selected refs, stop rule, and any
`method_selection_packet` (`required_surfaces`, `read_order`,
`secondary_policy`, `stop_rule`, `continue_only_if`,
`cap_language_required`, `single_flight_policy`). Do not rerun `ask` or
direct `semantic-debug`. Read at most one matching file under `references/`:

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
4. Exact screenshot/OCR/source phrases: use `bulk-line-match` with
   `--phrase`/`--phrases-file`; rows are leads, not proof. Validate refs with
   `read-more`, `semantic-debug`, `follow-next-reads`, or `audit-coverage`.
5. State the searched scope and uncertainty. `not_found_in_searched_scope`
   never means the evidence does not exist anywhere.
6. Stop when inspected primary sources answer the question. Widen/escalate
   only for a named missing facet. If primary evidence is absent/capped,
   ambiguous, or contradictory, fail closed and say what remains unproved.
7. Full/deep history audits preserve CLI audit-depth coverage caveats;
   ordinary prompts and status/local questions stay quick.
8. Never modify, delete, publish, or upload conversation data.
