---
name: jsonl-history-search
description: Use JSONL for AI conversation mining across local Codex and Claude logs when the user asks to find prior things they said, debug past agent behavior, audit prior work, recover conversation history, resolve stale/current artifacts, or build semantic-debugging evidence. Trigger even when the user does not name JSONL if the request depends on prior AI conversation history. When this skill is triggered, read this skill as a standalone command; do not chain that read with pwd, rg --files, find, ls, repo inventories, or setup probes. For installed global-skill workflows, run validate-install before ask-first routing.
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
Exception: when the user is asking whether the files/tasks/evidence packet you
just created or edited include everything, verify local artifacts first with
`git status`, `git diff`, `git show`, and targeted `sed`/`rg`; use JSONL only
as a targeted backstop for disputed source-window coverage.

The product contract is agent-assist retrieval: find useful information in
large, messy JSONL histories, show where it was found, and give the receiving
agent enough next commands/source refs to inspect deeper and answer. The CLI
output is not the final answer by itself.

JSONL is a direct local conversation-history tool; JSON fields and reports are
implementation surfaces, not the product itself.

## Natural Prompt First Move

For natural history/status/action/currentness/wrap-up prompts, first make sure
the installed global skill is fresh when this workflow relies on it, then run
the lightweight front door before loading a route specialization, broad `rg`,
repo recovery, TODO scans, or direct JSONL spelunking:

```bash
jsonl-history-search validate-install --codex-home ~/.codex --format text
python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"
jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text
```

The installed `jsonl_ask_first.py` launcher is the neutral ask-first helper. It
wraps the canonical `jsonl-history-search ask` command, adds `--cwd "$PWD"` and
`--format text` when omitted, and exists so the first command can be the
front-door resolver rather than a repo inventory/setup probe.

## Proven Route Demos

JSH-1098 free-agent traces showed sampled lower-burden source-window routing
for these workflows. This is not a release-readiness, provider-token-savings,
all-history, or universal-superiority claim.

- Date/timeline exact-ref recovery: ask first for prompts like "In the session
  dated <date>, what happened with <event>?", then open the promoted compact
  source window before broad repo search.
- Vague remembered-history retrieval: ask first for prompts like "What did I
  say before about making this clearly valuable upfront?", then use
  semantic-debug/source-window follow-through before artifact recovery.
- Agent action/proof verification: ask first for prompts like "Did the agent
  overwrite those workflow/planner edits?", then inspect tool/action proof,
  command output, diffs, commits, or review metadata.
- Decision-rationale/source-window provenance: ask first for prompts like "Why
  did we distinguish persistent coordinator from wait loops?", then answer from
  the reviewed source window and preserve corrections/uncertainty.

Before free-agent or user-facing workflows that rely on the installed global
Codex skill, run:

```bash
jsonl-history-search validate-install --codex-home ~/.codex --format text
```

If the agent preflight says the global skill is stale, refresh it from the
package checkout before relying on the route instructions:

```bash
bash install/install_codex_global.sh
```

Command-shape contract for the first commands:

- The command that reads this `SKILL.md` must be a single-purpose skill read
  only. It must not use shell chaining (`&&`, `;`, `|`) to run `pwd`,
  `rg --files`, `find`, `ls`, TODO scans, broad repo `rg`, or any other
  inventory/setup command in the same shell invocation.
- Invalid first commands include `sed -n '1,220p' .../SKILL.md && pwd`,
  `sed -n '1,220p' .../SKILL.md && rg --files`, and
  `cat .../SKILL.md && rg --files`.
- After the skill-read command completes for an installed global-skill
  workflow, the next shell command should be
  `jsonl-history-search validate-install --codex-home ~/.codex --format text`.
  If it fails, refresh the skill before relying on this route guidance.
- After the freshness preflight passes, the next shell command should be
  `python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"`
  or `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`.
- Repo inventory belongs only after `ask` selects a route or a source
  follow-through genuinely needs it.

Use `ask` first for prompts like "did the agent overwrite those edits?", "what
happened while I was away?", "why did I mention the router skill?", "which TODO
actually matters?", "is ticket ABC-42 still current?", "can these old worktrees be removed?", and "what did I
keep yelling about in this session?".
For immediate artifact-completeness prompts like "are you sure you got
everything?", "did the task file include X?", "did you save this whole thread as
evidence?", or "what changed in the files you just created?", do local artifact
verification first instead of running `ask` or `semantic-debug`.

The `ask` output is not a final answer and is not evidence retrieval by itself:
it does not inspect source refs, raw JSONL, tasks, reports, or repo files. It
chooses the route family, explains why, prints the exact first command to run,
names the one specialization/reference to read, says what output fields/source
refs to inspect after, and gives the stop condition for manual search.

After `ask`, read only the named reference and run the printed first command.
Do not open route references, run broad `rg`, scan TODO/repo files, or inspect
JSONL directly before `ask` for these natural prompts. If you have just read
this skill for an installed global-skill natural prompt, the next shell command
should be `validate-install`; after it passes, run `ask`. Do not insert `pwd`,
`rg --files`, `find`, `ls`, repo inventories, or setup probes between the skill
read, freshness preflight, and `ask`.

Do not combine reading this skill with `pwd`, `rg --files`, `find`, `ls -R`,
TODO scans, broad repo `rg`, or direct JSONL spelunking for natural history
prompts. If the command needs the current directory, pass `--cwd "$PWD"`; do
not dump repo file lists or workspace inventories before `ask`.

For prompts that name a reviewed source case, a `real-jsh...` source case ID,
an explicit date/source window, or "answer from that source scope only", do not
bypass the front door with `semantic-debug --format json`. Run `ask` first, or
run `semantic-debug ... --format text` only when the route has already selected
semantic-debug. Use compact/text `read-more` follow-through before any broad
repo/private `rg`, `rg --files`, or current-session echo recovery.
Do not run a preliminary `rg -n` for the `real-jsh...` handle, packet terms,
JSH-935, "reviewed source", or "read-more" before `ask`; that recreates the
broad-artifact/prompt-echo failure mode this skill is meant to prevent.

## Route Table

| User ask shape | Route | Read | First command |
| --- | --- | --- | --- |
| "What did I tell you before?" "Find my previous instruction/preference/feedback." | Remembered instruction/preference | `references/remembered-instruction.md` | `jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format text` |
| "Why did I say X?" "Why did I mention X?" "What was I talking about with X?" | Remembered topic/provenance | `ask` then `references/remembered-topic.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "What did I just say?" "Find my last message." "Recent messages." | Latest user wording | `references/latest-status.md` | `jsonl-history-search latest-user-messages --format json --max-matches 10` |
| "Since my last message." "What happened while I was away?" "What changed after I told you to keep working?" | Status anchor/value summary | `ask` then `references/latest-status.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Did the agent actually run/edit/test/review X?" "Did it overwrite those edits?" | Action-proof/source inspection | `ask` then `references/action-proof.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Why did this happen?" "Which conversation/task/review/commit explains this?" | Semantic debugging/provenance | `ask` then `references/semantic-debugging.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Are you sure you got everything?" "Did the task file include X?" "Did you save this whole thread as evidence?" "What changed in the files you just created?" | Local artifact verification | local files/git first | `git status --short`, then `git diff`/`git show` and targeted `sed`/`rg` against named files |
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
- "are you sure you got everything?" after just creating task/evidence files -> `git status --short`, then inspect `git diff`/named files before JSONL
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
- Do not trigger a full JSONL workflow for immediate local artifact
  completeness questions. If the user asks whether recently edited task,
  evidence, TODO, report, or doc files include the requested material, inspect
  local artifacts first; use targeted JSONL only if source-window coverage is
  missing or disputed.
- For natural history/status/action/currentness/wrap-up prompts that rely on
  the installed global skill, run
  `jsonl-history-search validate-install --codex-home ~/.codex --format text`
  after reading this skill and before ask-first routing. If it fails, refresh
  with `bash install/install_codex_global.sh` before relying on this route
  guidance.
- Then run
  `python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"`
  or its canonical wrapped command,
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
- When the user prompt includes a reviewed source case or `real-jsh...` handle,
  installed global-skill workflows still run `validate-install` after reading
  this skill. After freshness preflight passes, run `jsonl-history-search ask`
  or the already-selected `semantic-debug --format text`, not repo `rg`.
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
