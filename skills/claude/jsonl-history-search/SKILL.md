---
name: jsonl-history-search
description: Use JSONL for AI conversation mining across local Codex and Claude logs when the user asks to find prior things they said, debug past agent behavior, audit prior work, recover conversation history, resolve stale/current artifacts, or build semantic-debugging evidence. Trigger even when the user does not name JSONL if the request depends on prior AI conversation history.
metadata:
  short-description: Mine AI conversation JSONL
---

# JSONL History Search

This skill is a router. Load this file first. For natural user questions that
ask what happened, why a topic appeared, whether an agent action occurred,
which artifact or ticket is current, whether old worktrees can be pruned, or what to
learn from a session, run `ask` as the
first resolver before choosing a route yourself. For explicit operational
lookups that are not natural route-choice questions, choose the narrowest route
and read only the referenced specialization needed for the user's question.

The product contract is agent-assist retrieval: find useful information in
large, messy JSONL histories, show where it was found, and give the receiving
agent enough next commands/source refs to inspect deeper and answer. The CLI
output is not the final answer by itself.

JSONL is a direct local conversation-history tool; JSON fields and reports are
implementation surfaces, not the product itself.

## Natural Prompt First Move

For natural history/status/action/currentness/wrap-up prompts, run the
lightweight front door as the first resolver before loading a route
specialization, broad `rg`, repo recovery, TODO scans, or direct JSONL
spelunking:

```bash
jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text
```

Use `ask` first for prompts like "did the agent overwrite those edits?", "what
happened while I was away?", "why did I mention the router skill?", "which TODO
actually matters?", "is ticket ABC-42 still current?", "can these old worktrees be removed?", and "what did I
keep yelling about in this session?".

The `ask` output is not a final answer and is not evidence retrieval by itself:
it does not inspect source refs, raw JSONL, tasks, reports, or repo files. It
chooses the route family, explains why, prints the exact first command to run,
names the one specialization/reference to read, says what output fields/source
refs to inspect after, and gives the stop condition for manual search.

After `ask`, read only the named reference and run the printed first command.
Do not open route references, run broad `rg`, scan TODO/repo files, or inspect
JSONL directly before `ask` for these natural prompts.

Do not combine reading this skill with `pwd`, `rg --files`, `find`, `ls -R`,
TODO scans, broad repo `rg`, or direct JSONL spelunking for natural history
prompts. If the command needs the current directory, pass `--cwd "$PWD"`; do
not dump repo file lists or workspace inventories before `ask`.

## Route Table

| User ask shape | Route | Read | First command |
| --- | --- | --- | --- |
| "What did I tell you before?" "Find my previous instruction/preference/feedback." | Remembered instruction/preference | `references/remembered-instruction.md` | `jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format json` |
| "Why did I say X?" "Why did I mention X?" "What was I talking about with X?" | Remembered topic/provenance | `ask` then `references/remembered-topic.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "What did I just say?" "Find my last message." "Recent messages." | Latest user wording | `references/latest-status.md` | `jsonl-history-search latest-user-messages --format json --max-matches 10` |
| "Since my last message." "What happened while I was away?" "What changed after I told you to keep working?" | Status anchor/value summary | `ask` then `references/latest-status.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Did the agent actually run/edit/test/review X?" "Did it overwrite those edits?" | Action-proof/source inspection | `ask` then `references/action-proof.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Why did this happen?" "Which conversation/task/review/commit explains this?" | Semantic debugging/provenance | `ask` then `references/semantic-debugging.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| Default misses, weak/no evidence, or the agent keeps searching | Retrieval escalation | `references/retrieval-escalation.md` | Retry the same command with `--retrieval-mode fielded-bm25`; reserve `rrf-fusion` for high-recall hard cases. |
| "Which plan is current?" "Is this still what we're doing?" "Which TODO matters?" "Is ticket ABC-42 still current?" "Why are there multiple next-task files?" | Artifact/currentness | `ask` then `references/status-currentness.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Can these old worktrees be removed?" "Are these branches/checkouts stale or safe to prune?" | Worktree cleanup/currentness | `ask` then `references/status-currentness.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Why did this case fail?" "Inspect the raw trace." "Save source paths/line ranges for debugging." | Local/internal evidence mode | `references/local-evidence.md` | `jsonl-history-search local-evidence-diagnostic --raw-run <raw-run.json> --evidence-mode internal --format json` |
| "Semantic wrap-up this run." "What did I complain about that needs fixing?" "What should we learn from this session?" | Semantic wrap-up/failure mining | `ask` then `references/semantic-wrapup.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| Tool returns no evidence or weak evidence | No-evidence interpretation | `references/no-evidence.md` | Treat no evidence as `not_found_in_searched_scope`, then inspect scope and escalation options. |

## Route Smoke Examples

- "what did I tell you before about release readiness?" -> `references/remembered-instruction.md`
- "why did I mention the router?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/remembered-topic.md`
- "what happened since my last message?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/latest-status.md`
- "did the agent overwrite those edits?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/action-proof.md`
- "which plan is current?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/status-currentness.md`
- "which TODO actually matters?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/status-currentness.md`
- "is ticket ABC-42 still current?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/status-currentness.md`
- "why are there three next-task files?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/status-currentness.md`
- "can these old worktrees be removed?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then inspect `artifact_currentness.worktree_currentness`
- "inspect the raw trace for this failed case" -> `references/local-evidence.md`
- "what did I complain about that needs fixing?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/semantic-wrapup.md`

## Routing Rules

- Trigger this skill even when the user does not say "use JSONL" if the answer
  depends on prior AI conversation logs or agent actions.
- For natural history/status/action/currentness/wrap-up prompts, run
  `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` before
  broad `rg`, repo recovery, TODO scans, direct JSONL inspection, or loading a
  specialization. Then follow the printed first command and read only the named
  reference.
- Do not load every reference. Read the one route reference needed now, plus
  `references/retrieval-escalation.md` only when evidence is weak, missing, or
  the route says escalation matters.
- A plain "why did I say/mention X?" question is remembered-topic/provenance,
  not artifact/currentness, unless the wording implies plan/TODO/ticket/status
  conflict.
- Artifact/currentness is for stale/current/superseded direction questions,
  local/exported ticket currentness questions, and worktree cleanup/currentness
  questions, not for every repeated-topic question.
- For worktree cleanup/currentness, use the `ask` route and the returned
  `artifact_currentness.worktree_currentness` classifications before shallow
  `git status` conclusions. Preserve worktree-local `.private` evidence under
  parent `.private/worktree-cleanup-archive/<slug>/` before physical worktree
  pruning, and keep branch refs retained unless the user explicitly scopes
  branch ref pruning.
- Use `ask` before `semantic-debug` for natural quality-sensitive remembered
  instructions, action proof, provenance, no-evidence, and ambiguous semantic
  questions. Use `semantic-debug --format text` before lower-level helper
  scripts when `ask` selects semantic-debug.
- Use `latest-user-messages` first for exact recent user-message wording.
- Use helper scripts only for operational recovery: latest/today lookup,
  timestamp-window lookup, exact phrase search, or broad-term recovery where
  important words are expected to appear.

## Source-Use Rules

After running the command:

1. Read the top source refs and `source_locator_handoff` before broad search.
2. For claimed actions, inspect `semantic_debug.source_window_action_proof`
   before relying on assistant prose.
3. Separate primary proof from adjacent leads and optional diagnostics.
4. Run targeted follow-up commands only when a required facet is missing.
5. Stop broad searching once primary evidence is sufficient to answer.
6. Answer from inspected source evidence, preserving uncertainty and searched
   scope.

JSON fields, source refs, compact outputs, and reports are navigation aids.
Do not turn them into packet bureaucracy or a required export handoff.

For local product debugging, inspect raw local JSONL, raw traces, command
events, source paths, and line ranges when needed. Sanitization is for external
publication/export, not a reason to avoid reading evidence needed to fix the
tool.

## Search Order

1. Recent/"just now" recovery: newest modified `~/.codex/sessions/**/*.jsonl`
   first.
2. `~/.codex/history.jsonl`.
3. `~/.claude/projects/**/*.jsonl`.
4. Project `.claude/` state, task/review files, `work/research`, `reports`,
   and repo docs.

Do not start with normal repo `rg` alone for history questions. Inside a JSONL
shortlist, raw `rg` is fine before parsing nearby JSON objects.
