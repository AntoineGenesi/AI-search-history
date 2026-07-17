---
name: jsonl-history-search
description: Bare ambiguous event prompts such as `what happened?` are not history requests; ask one short clarification and use zero tools, including no JSONL/history, Git, repo/TODO/task/state scans, or filesystem inspection. Activate this skill for explicit history/source targets such as `what happened since my last message?`, `what happened at X?`, devlog chronology, source provenance, action proof, remembered instructions, worktree cleanup causality, or questions about where retained source sessions/private evidence are stored and how long they are kept. Explicit history/provenance/prior-conversation/source-proof wording overrides task-path local-first handling and must run the installed-or-source `ask` resolver before task/Git reads; only genuine local file, artifact, workspace, checkout, or task status/currentness checks, including a named local task-file, stay local-first. Use JSONL for AI conversation mining across local Codex and Claude logs when the user asks to find prior things they said, debug past agent behavior, audit prior work, recover conversation history, resolve stale/current artifacts, or build semantic-debugging evidence. User wording that says convo also triggers this skill, but it does not select an executable command. Trigger even when the user does not name JSONL if the request depends on prior AI conversation history. When this skill is triggered, read this skill as a standalone command; do not chain that read with pwd, rg --files, find, ls, repo inventories, or setup probes. For natural history or storage-retention prompts, run the installed-or-source resolution block before any console command or installed validation.
metadata:
  short-description: Mine AI conversation JSONL
---

# JSONL History Search

## Activation Gate

For a bare `what happened?` or equivalent ambiguous event prompt with no
concrete event, time, thread, task, path, commit, history, or source target, stop
before every tool call. Ask one short clarification about which event or change
the user means. Use zero JSONL/history commands, Git commands, repo scans,
TODO/task/state recovery, and filesystem inspection, and do not infer an event
from the current checkout.

Activate JSONL when the user supplies an explicit history/source anchor, such
as `what happened since my last message?`, `what happened at X?`, a devlog
chronology, source provenance, action proof, a remembered instruction, or
worktree cleanup causality. Also activate the storage-retention route for
questions about where retained source sessions/private evidence are stored or
how long they are kept. Explicit history/provenance intent takes precedence
over the presence of a task path: wording such as `history`, `provenance`,
`source session`, `merge proof`, `previously decided`, `prior conversation`, or
`source proof` must run the installed-or-source `ask` resolver before any local
task, report, state, or Git read. This includes questions such as `which source
session and merge prove the work in \`tasks/example.md\`?` and `what did we
previously decide about "tasks/example.md"?`.

A named task path is local-first only when the question is genuinely about the
local artifact's status or currentness, for example `what happened with
tasks/example.md?` or `is tasks/example.md merged/current?`, without explicit
history, provenance, prior-conversation, or source-proof intent. In that case,
inspect current Git and the local artifact first and use JSONL only if history
or source coverage is explicitly missing or disputed. Do not treat the mere
presence of `tasks/...` as a reason to bypass `ask`.
The named local task-file case is one example, not a limit: explicit current
local file, artifact, workspace, repository, or checkout status/currentness
wording also stays local-first unless the user explicitly asks for history,
prior conversation, provenance, or source proof.

This skill is a router. Apply the activation gate above before loading routes.
For natural user questions that
ask what happened since an explicit prior-message/history anchor, why a topic appeared, whether an agent action occurred,
which artifact or ticket is current, whether old worktrees can be pruned, or what to
learn from a session, run `ask` as the
first resolver before choosing a route yourself. For explicit operational
lookups that are not natural route-choice questions, choose the narrowest route
and read only the referenced specialization needed for the user's question.
Exception: when the user is asking whether the files/tasks/evidence packet you
just created or edited include everything, verify local artifacts first with
`git status`, `git diff`, `git show`, and targeted `sed`/`rg`; use JSONL only
as a targeted backstop for disputed source-window coverage.
Do not trigger this skill for the bare prompt `what happened?` or an equivalent
ambiguous event question with no history anchor. Ask one short clarification
question without using a tool, Git, repo scans, TODO/task/state recovery, or
filesystem inspection. For local artifact/task-file status prompts such as
`what happened with this task file?`, inspect the named local artifact first
only when none of the explicit history/provenance intents above is present.

For a named local task status/currentness question, reconcile current Git
before interpreting status prose. Inspect branch or detached state and the
current `HEAD` parents, then use target-bound `git log`, `git branch --contains`,
and `git merge-base --is-ancestor` checks to determine whether the task's
reviewed branch work is reachable or merged. Current Git proof takes precedence
over contradictory task, report, review, TODO, or pipeline status text. Do not
call work active, pending-review, unmerged, incomplete, or not finished from
stale secondary prose after Git proves the reviewed work is reachable or
merged. If Git does not provide merge or reachability proof, stay cautious and
do not infer that it merged. Keep this workflow local; do not invoke broad
JSONL history unless source coverage is explicitly missing or disputed.

The product contract is agent-assist retrieval: find useful information in
large, messy JSONL histories, show where it was found, and give the receiving
agent enough next commands/source refs to inspect deeper and answer. The CLI
output is not the final answer by itself for general routes. The exact
worktree-cleanup causality route is the narrow exception: when `ask --execute` returns an
`answer_handoff` with `answer_status: answered-from-proof`, use that answer and
its validated refs directly and stop without another command.

JSONL is a direct local conversation-history tool; JSON fields and reports are
implementation surfaces, not the product itself.

## Natural Prompt First Move

Before local task/Git inspection, run the installed-or-source block below for
any explicit history/provenance/source-session/merge-proof/previous-decision/
prior-conversation/source-proof question, even when its target is a quoted,
backticked, or plain task path. The local-first task-status exception does not
override these explicit intents.

For natural history/status/action/currentness/wrap-up prompts, use this single
installed-or-source command-resolution block as the first shell move. It keeps
the installed console behavior when available and directly executes the source
checkout fallback when it is not; do not try `validate-install` or an installed
helper before this check succeeds:

```bash
if command -v jsonl-history-search >/dev/null 2>&1; then
  jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
elif [ -f src/jsonl_history_search/cli.py ]; then
  PYTHONPATH=src python3 -m jsonl_history_search.cli ask --execute "<user wording>" --cwd "$PWD" --format text
else
  printf '%s\n' 'jsonl-history-search is not on PATH and src/jsonl_history_search/cli.py is unavailable in this cwd' >&2
  exit 127
fi
```

This block is the canonical no-fumble first move. It is command resolution,
not repo inventory or package installation. When the installed branch is
selected and the workflow depends on global-skill freshness, run the installed
preflight before later route commands:

```bash
jsonl-history-search validate-install --codex-home ~/.codex --format text
python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text
jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text
```

Use that installed sequence only after `command -v jsonl-history-search`
succeeds. When the console command is unavailable but the current package
checkout contains `src/jsonl_history_search/cli.py`, do not try an installed
helper that cannot import the package and do not inventory the repo to recover.
Run the source front door directly:

```bash
PYTHONPATH=src python3 -m jsonl_history_search.cli ask "<user wording>" --cwd "$PWD" --format text
```

The skill name and canonical installed command remain `jsonl-history-search`.
Only after the installed branch above is selected, treat `convo` as the
installed package alias/trigger for the same tool, not a separate product. Its
optional helper validation is also availability-gated:

```bash
if command -v convo >/dev/null 2>&1; then
  convo --help
fi
```

Do not run `convo`, `convo --help`, or a bare canonical console command before
the installed-or-source block selects an executable mode.

The bundled `jsonl_ask_first.py` also prepends the current checkout's `src`
directory when that package source exists. This is source-mode recovery, not
proof that the global console command is installed. If neither path is
executable, report the installed/source parity gap instead of fumbling through
unrelated commands.

For the exact wordings `why did the worktrees not clean themselves up?` and
`why did the JSON-search-history worktrees not clean themselves up?`, the
resolution block above is the only domain command needed when its cleanup
`answer_handoff` is `answered-from-proof`. The tool has already opened and
validated the bounded repo-local refs. Do not read a route reference, rerun
`artifact-currentness`, reopen those refs, inspect worktrees, or search broadly;
answer from the handoff and stop. A missing or fail-closed handoff does not
authorize cleanup or destructive recovery.

This exact cleanup fast path is fully specified in this first skill section.
When either exact wording is the user's prompt, do not read the remainder of `SKILL.md` after
this section: run the resolution block, use a validated answer handoff, and
stop. The expected trajectory is one skill-read command plus one execute
command, with no external source-read command.

For a named cleanup target, use the same resolution block once. When its
`target_binding.status` is `blocked_named_target_missing`, do not rerun
`artifact-currentness`; run only the listed target-bound Git reads. If the
worktree list and exact branch list remain empty for that target, stop and say
only that current Git proves absence and no target-specific cleanup cause can
be established. Do not add branch variants, `git show-ref`, Git-log searches,
`rg`, reports, tasks, reviews, tests, scripts, validators, fixtures, or answer
keys, and do not claim the target was not cleaned because it is absent now.

The installed `jsonl_ask_first.py` launcher is the bounded-aware ask-first
helper. It wraps the canonical `jsonl-history-search ask` command with
`--auto-execute-bounded-no-evidence`, adds `--cwd "$PWD"` and `--format text`
when omitted, and exists so the first command can be the front-door resolver rather than a repo inventory/setup probe. For non-bounded prompts it returns
normal route-only ask output; it does not auto-execute semantic-debug,
orchestration, currentness, status, skill-audit, or local-artifact routes.

Use `ask --execute` when the receiving-agent goal is to fuse route selection
with the first safe JSONL run. It executes allow-listed structured
semantic-debug, bounded no-evidence/no-evidence-report, devlog/failure-analysis,
and coordinator-context routes, writes a full
JSON report behind a report handle, and returns a compact answer/read/stop
contract. Only bounded no-evidence routes executed through `no-evidence-report`
may surface promoted scoped no-evidence answer status; semantic-debug execution
is convenience output and remains inspect-before-answer. Devlog/failure-analysis
execution is a source-reach package: open target historical source-window reads
before current/adjacent diagnostics, then use the linked
`failure_analysis.devlog_brief` and safe repo-phase/category evidence as
boundaries. Coordinator-context execution is a committed-safe current
context/next-read package from repo front-door files such as TODO, pipeline
state, current-context, active tasks, learnings, and bounded prior-result
reports; open required next reads before turning it into a substantive handoff.
Unsupported routes fail
closed with `execution_status:
unsupported_route` and the normal first command; they are not shell-executed.
Use plain `ask` when you only need route classification or when the selected
route is outside that execution allow-list.

Storage-retention forensics routes are inventory-first. Use the canonical
installed-or-source `ask --execute` block above for these natural asks; the
unsupported execution response prints the bounded route commands. For asks like "why is
`.private` 23GB?", "what created these large private files?", "who uses them?",
or "what must be retained for examples/tests/claims?", run `ask` first to get
the route contract, then follow the labeled checkout-local/parent-retained
filesystem inventory. The emitted top-N root command uses bounded in-memory selection without
external `sort` or writable temporary storage. Run the
emitted `generic_retention_policy` command immediately after inventory. For a
broad storage question, answer from those two reads with root size, dominant
path classes, representative handles, class-level creation/use attribution,
retention status, and explicit uncertainty for any exact producer not proven;
then stop. Do not scan every bucket or require producer/consumer joins before a
broad answer. When the prompt did not ask for cleanup, do not turn retention
policy into advice about reclaiming space or changing, removing, relocating,
archiving, or compacting stored artifacts. Deeper joins are optional only for
one explicit named-handle request or one unresolved facet that would materially
change the answer. This route is non-destructive:
do not delete, archive, move, compact VHDX files, prune branches, or perform
cleanup from this guidance. JSONL is a targeted follow-up only for unresolved
creator/history gaps after inventory and committed artifacts have been checked.

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

Only when the no-fumble block selected the installed branch and the workflow
relies on the installed global Codex skill, run this freshness preflight before
later installed route commands:

```bash
jsonl-history-search validate-install --codex-home ~/.codex --format text
```

If that installed-branch preflight says the global skill is stale, refresh it
from the package checkout before relying on later installed route instructions.
Never run this refresh or preflight from the source-checkout branch:

```bash
bash install/install_codex_global.sh
```

Command-shape contract for the first two shell commands:

- The command that reads this `SKILL.md` must be a single-purpose skill read
  only. It must not use shell chaining (`&&`, `;`, `|`) to run `pwd`,
  `rg --files`, `find`, `ls`, TODO scans, broad repo `rg`, or any other
  inventory/setup command in the same shell invocation.
- Invalid first commands include `sed -n '1,220p' .../SKILL.md && pwd`,
  `sed -n '1,220p' .../SKILL.md && rg --files`, and
  `cat .../SKILL.md && rg --files`.
- After the skill-read command completes for a natural prompt, the next shell
  command should be the installed-or-source command-resolution block above.
- After the skill-read command completes for an installed global-skill
  workflow where `command -v jsonl-history-search` succeeds, the freshness
  command should be
  `jsonl-history-search validate-install --codex-home ~/.codex --format text`.
  If it fails, refresh the skill before relying on this route guidance.
- After the freshness preflight passes, the next shell command should be
  `python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"`
  or `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --format text`
  for fused semantic-debug/no-evidence routing. Use
  `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` when
  you need route-only output.
- Repo inventory belongs only after `ask` selects a route or a source
  follow-through genuinely needs it.
- For exact source inspection, copy the route's source ref into
  `jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text`.
  Do not invent `read-more --context`; use `--mode window --before/--after`.
- For focused candidate search, use `jsonl-history-search find "<query>" --max-matches 20 --cwd "$PWD" --format text`.
  Do not invent `find --max-results`; the JSONL cap flag is `--max-matches`.

Use `ask` first for prompts like "did the agent overwrite those edits?", "what
happened while I was away?", "why did I mention the router skill?", "which TODO
actually matters?", "is ticket ABC-42 still current?", "can these old worktrees
be removed?", "did the agent read/follow the reviewer skill?", "is there
evidence that X was discussed in this bounded source scope?", and "what did I
keep yelling about in this session?".
For immediate artifact-completeness prompts like "are you sure you got
everything?", "did the task file include X?", "did you save this whole thread as
evidence?", or "what changed in the files you just created?", do local artifact
verification first instead of running `ask` or `semantic-debug`.
For bare prompts like `what happened?`, ask what event or change the user means;
do not run `ask`, `semantic-debug`, or any other history tool.

The `ask` output is not a final answer and is not evidence retrieval by itself:
it does not inspect source refs, raw JSONL, tasks, reports, or repo files. It
chooses the route family, explains why, prints the exact first command to run,
names the one specialization/reference to read, says what output fields/source
refs to inspect after, and gives the stop condition for manual search.

The `ask --execute` output is still not normally a final answer by itself, but for
allow-listed semantic-debug and bounded no-evidence routes it does run the
selected JSONL route and returns one compact contract with selected route
metadata, execution status, compact executed output, promotion status,
answer/read/stop guidance, and a saved report handle for drill-down. Treat
semantic-debug `ask --execute` results as unpromoted inspect-before-answer
handoffs even when the saved full report contains internal answer-readiness
fields.
The exact worktree-cleanup causality route is the exception described above:
its `answered-from-proof` handoff contains internally validated source refs and
is final-answer-ready without external source rereads.

After `ask`, read only the named reference and run the printed first command.
Latest-status is self-contained: do not open or search for a route reference.
Run its printed `what-happened-since` command directly. If that command reports
no selected anchor and no required primary refs, fail closed and ask for the
prior message wording or timestamp; do not probe skill files, repo state, or
diagnostic refs.
Do not open route references, run broad `rg`, scan TODO/repo files, or inspect
JSONL directly before `ask` for these natural prompts. If you have just read
this skill for a natural prompt, run the installed-or-source resolution block.
When its installed branch applies, run `validate-install` before relying on
later installed route guidance. Do not insert `pwd`,
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

The canonical console spellings below are installed-mode route templates, not
first-move availability probes. For every natural prompt, run the
installed-or-source block above first and use its selected command prefix.

| User ask shape | Route | Read | First command |
| --- | --- | --- | --- |
| "What happened?" with no message, history, thread, time-window, or artifact anchor | Non-history clarification | none | No tool call; ask which event or change the user means. |
| "What did I tell you before?" "Find my previous instruction/preference/feedback." | Remembered instruction/preference | `references/remembered-instruction.md` | `jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format text` |
| "Why did I say X?" "Why did I mention X?" "What was I talking about with X?" | Remembered topic/provenance | `ask` then `references/remembered-topic.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "What did I just say?" "Find my last message." "Recent messages." | Latest user wording | no route-reference read | `jsonl-history-search latest-user-messages --format json --max-matches 10` |
| "Since my last message." "What happened while I was away?" "What changed after I told you to keep working?" | Status anchor/value summary | `ask`; no route-reference read | Run the installed-or-source block above with the exact user wording, then run its printed `what-happened-since` command. |
| "Did the agent actually run/edit/test/review X?" "Did it overwrite those edits?" | Action-proof/source inspection | `ask` then `references/action-proof.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Did the agent use/read/follow the reviewer/product-fsm skill?" "Was a named skill missed or misapplied?" | Named skill compliance | `ask` then `references/skill-audit.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Turn the early coordinator experiment into a devlog segment." "Reconstruct the arc with dates and concrete examples of Docker/runner/harness/auth/live-send/workflow drift." | Devlog/failure-analysis historical source reach | `ask --execute`, then `references/semantic-debugging.md` if a source-field needs interpretation | `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-devlog-source-reach.json --format text` |
| "Gather the current coordinator context for the receiving agent: plans, handoff documents, TODO, pipeline state, active tasks, goals, guiding principles, learnings, and failure modes to watch." | Coordinator context/current-state package | `ask --execute`, then `references/coordinator-context.md` | `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-coordinator-context.json --format text` |
| "Why did this happen?" "Which conversation/task/review/commit explains this?" | Semantic debugging/provenance | `ask` then `references/semantic-debugging.md` | `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text` |
| "Is there evidence that X was discussed in this bounded/source scope?" "Did the searched scope contain X?" "Prove there is no evidence of X in this selected root." | Bounded no-evidence/searched-scope absence | helper auto-executes bounded no-evidence, then `references/no-evidence.md` only if a scope/cap field needs interpretation | `python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"` |
| "Why is `.private` 23GB?" "What created these large private files?" "Who uses them?" "What must be retained for examples/tests/claims?" | Storage-retention forensics | `ask --execute` then `references/storage-retention.md` | Run the emitted labeled checkout-local/parent-retained bounded inventory. |
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
- "what happened since my last message?" -> run the installed-or-source block above with the exact user wording as the first product command, then run the printed `what-happened-since` command directly; do not read or search for a route reference. If it reports no selected anchor and no required primary refs, fail closed and ask for the prior message wording or timestamp.
- "what happened?" with no explicit anchor -> no tool call; ask which event or change the user means
- "are you sure you got everything?" after just creating task/evidence files -> `git status --short`, then inspect `git diff`/named files before JSONL
- "did the agent overwrite those edits?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/action-proof.md`
- "did the agent read/follow the reviewer skill?" -> `jsonl-history-search ask "<user wording>" --cwd "$PWD" --format text`, then `references/skill-audit.md`
- "turn the early coordinator experiment into a devlog segment" -> `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-devlog-source-reach.json --format text`; open `target_historical_source_window_reads` before current/adjacent diagnostics, then use `failure_analysis.devlog_brief` and safe repo-phase/category evidence.
- Natural devlog wording such as an early coordinator getting stuck or bogged down in runner/harness/auth/workflow mechanics before returning to product behavior uses the same devlog source-reach route. Run only its role-bound bounded history reads first. Treat an isolated `CODEX_HOME`, `CLAUDE_HOME`, or `$HOME` without history as unavailable, use `JSONL_HISTORY_CODEX_HOME` / `JSONL_HISTORY_CLAUDE_HOME` only when they name real history roots, and otherwise stop with the exact missing roles. Never use uncapped recursive `rg`, `rg --files -uu`, broad repo dumps, or same-loop answer artifacts to fill a missing devlog role.
- "gather the current coordinator context for the receiving agent: plans, handoff documents, TODO, pipeline state, active tasks, goals, guiding principles, learnings, and failure modes to watch" -> `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-coordinator-context.json --format text`; open `required_next_reads` before answering or handing work to another agent.
- "is there evidence that X was discussed in this bounded source scope?" -> `python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"`; the helper auto-executes only bounded no-evidence and, if promoted, you can answer from the scoped no-evidence contract without a manual rerun/read-more.
- "why is .private 23GB?" -> use the installed-or-source `ask --execute` block, then `references/storage-retention.md`; run the emitted labeled checkout-local/parent-retained inventory, then the emitted `generic_retention_policy` command. The inventory and selected-subtree top-N commands use bounded Python `heapq`, not external `sort` or writable temporary storage. Stop there for generic location/lifetime policy; use named-handle joins only for an unclassified selected handle. Never use uncapped recursive `rg`, `rg --files -uu`, or a broad repo dump for this route; use JSONL only for unresolved named creator/history gaps.
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
- For worktree cleanup/currentness, use the `ask` route. For the exact cleanup
  causality prompt, stop on the validated `answered-from-proof` handoff; for
  removal/currentness decisions, inspect the returned
  `artifact_currentness.worktree_currentness` classifications before shallow
  `git status` conclusions. Preserve worktree-local `.private` evidence under
  parent `.private/worktree-cleanup-archive/<slug>/` before physical worktree
  pruning, and keep branch refs retained unless the user explicitly scopes
  branch ref pruning.
- For storage-retention forensics, use `ask` to select the
  `storage_retention_forensics` route, then start with non-destructive
  labeled checkout-local/parent-retained filesystem inventory and artifact
  joins. Use the bounded in-memory top-N commands; do not introduce an external
  `sort` or temporary-file dependency. Do not run cleanup, deletion,
  archive, move, VHDX compaction, or branch-pruning commands from this route.
  JSONL conversation history is only a targeted follow-up for a specific
  unresolved creator/history gap, with caps and searched-scope caveats.
- Use `ask` before `semantic-debug` for natural quality-sensitive remembered
  instructions, action proof, provenance, no-evidence, and ambiguous semantic
  questions. Use `semantic-debug --format text` before lower-level helper
  scripts when `ask` selects semantic-debug.
- When `ask` selects bounded no-evidence/searched-scope absence, prefer
  `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-no-evidence-report.json --format text`.
  The saved report keeps the full evidence packet while stdout shows the scoped
  no-evidence contract. Inspect searched files/bytes, missing/excluded scope,
  cap status, answer scope, not_all_history_absence, saved report, and
  read-more stop fields before deciding whether any diagnostics are needed.
- When `ask` selects devlog/failure-analysis historical source reach, prefer
  `jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-devlog-source-reach.json --format text`.
  Open `target_historical_source_window_reads` first. Treat
  current/adjacent/same-loop diagnostics as non-actionable by default unless
  the user explicitly asks to audit current output, the current route, the
  current lane, or a prior answer/report. Use the linked
  `failure_analysis.devlog_brief` and safe repo-phase/category evidence as
  source boundaries before broad repo archaeology.
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
