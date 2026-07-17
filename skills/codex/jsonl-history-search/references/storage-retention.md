# Storage Retention Forensics

Use this when `ask` selects `storage_retention_forensics` for questions such as
"why is `.private` 23GB?", "what created these large private files?", "who uses
them?", or "what must be retained for examples/tests/claims?".

This route is guidance for evidence order, not a cleanup command.

## Evidence Order

1. `filesystem_inventory`: distinguish the receiving checkout's `.private`
   from the durable parent retained-evidence root. Prefer an enclosing
   `.private` ancestor for a receiving clone nested in retained evidence;
   otherwise derive the parent from Git's common directory for a linked
   worktree. Measure each distinct root with explicit depth and row caps, then
   retain the top 40 as dominant storage classes and representative subtree
   handles. Do not list top files for every subtree.
2. `generic_retention_policy`: read the current retention gate, reviewed
   ledger, state-file policy, and latest authorized outcome in one bounded
   command.
3. `answer_ready_stop`: for a broad storage question, answer from inventory
   plus policy with total/root size, dominant path classes, representative
   handles, class-level creation/use attribution, and retention status. Mark an
   unverified exact producer/consumer as unknown and stop; that uncertainty
   does not require per-bucket joins.
4. `artifact_join`: optional only when the user explicitly asks about one
   named handle or one unresolved facet would materially change the broad
   answer. Join that one handle to committed repo
   artifacts: tasks/reports/reviews/scripts/tests/pipeline state. Prefer
   current task files, reports, review artifacts, scripts, tests, and
   `codex/state/pipeline-state.md` before conversation mining.
5. `retention_classification`: classify each large handle as required for
   current tests/examples, useful retained evidence, claim-critical raw trace,
   recomputable/cache, stale candidate for a future explicit cleanup task, or
   unknown follow-up.
6. `targeted_jsonl_follow_up`: use JSONL conversation search only for a named
   unresolved creator/history gap after inventory and artifact joins. Keep the
   query scoped to a specific subtree/task handle and preserve roots, caps,
   searched-scope limits, and uncertainty.

## Echo Demotion

Demote recent command-output echoes, ask-output echoes, same-loop transcripts,
and post-hoc reports unless they contain primary creation or actual-use
evidence for the storage handle. A recent transcript saying that a folder is
large is not creator/use proof by itself.

## Hard Boundary

Do not delete files. Do not compact VHDX files. Do not archive, move, prune
branches, or perform storage cleanup from this route. If cleanup seems useful,
write a separate explicit cleanup/archive task with retention review, privacy
review, and claim-preservation checks.

## Typical Commands

Never use uncapped recursive `rg`, `rg --files -uu`, or a broad repo file dump
for this route. Replace each placeholder with one handle selected by the bounded
inventory; do not search for the literal placeholder.

Run inventory first:

```bash
repo_root=$(git rev-parse --show-toplevel) && common_private=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.private && enclosing_private=$(python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]).resolve(); print(next((str(parent) for parent in p.parents if parent.name==".private"),""))' "$repo_root") && if [ -n "$enclosing_private" ]; then parent_private=$enclosing_private; else parent_private=$common_private; fi; for root in "$repo_root/.private" "$parent_private"; do [ -d "$root" ] || continue; if [ "$root" = "$repo_root/.private" ] && [ "$root" = "$parent_private" ]; then label=checkout-and-parent-retained; elif [ "$root" = "$repo_root/.private" ]; then label=checkout-local; else label=parent-retained; fi; printf '\n[%s] %s\n' "$label" "$root"; du -x -d 2 -B1 "$root" 2>/dev/null | python3 -c 'import heapq,sys; rows=((int(size), path.rstrip("\n")) for line in sys.stdin for size,path in [line.split("\t",1)]); [print(str(size)+"\t"+path) for size,path in heapq.nlargest(40,rows)]'; if [ "$repo_root/.private" = "$parent_private" ]; then break; fi; done; :
find <selected-large-subtree-path> -xdev -type f -printf '%s\t%p\n' 2>/dev/null | python3 -c 'import heapq,sys; rows=((int(size), path.rstrip("\n")) for line in sys.stdin for size,path in [line.split("\t",1)]); [print(str(size)+"\t"+path) for size,path in heapq.nlargest(40,rows)]'
```

Both commands keep only 40 rows in Python memory and do not use external
`sort` or writable temporary storage. The first root inventory is required.
The selected-subtree top-file command is optional only when the user asks for
exact files or one named subtree needs a representative file.

Read generic policy directly after inventory:

```bash
printf '\n--- retention gate ---\n'; nl -ba reports/jsh-067-private-artifact-retention-and-worktree-cleanup-gate.md | sed -n '8,20p'; printf '\n--- retention ledger method ---\n'; nl -ba reports/jsh-1245-private-retention-ledger.md | sed -n '13,20p'; printf '\n--- retained lane evidence contract ---\n'; nl -ba reports/jsh-1246-state5-sqlite-retention-policy.md | sed -n '13,24p'
```

For every broad `.private` size/creation/use/retention question, answer after
that read; it already includes citation-ready line numbers. State root totals,
dominant path classes, representative handles, class-level creation/use
attribution, retention status, and any exact producer uncertainty. Do not reopen those
policy files, scan each bucket, or serially inspect older tasks, reports,
reviews, TODO, or state. Only for an explicit named-handle request or one
specific unresolved facet material to the answer, join that one handle to
bounded policy and producer/consumer results:

The broad policy excerpt is intentionally retention-only. If the user did not
ask for cleanup, do not turn it into advice about reclaiming space or changing,
removing, relocating, archiving, or compacting stored artifacts. Cleanup-policy
and prior cleanup-execution reports are outside the broad answer path.

```bash
rg -n -F --max-count 20 '<large-subtree-or-task-handle>' codex/state/pipeline-state.md TODO.md PROJECT.md CODEX-ORCHESTRATION.md learnings/INDEX.md 2>/dev/null | head -n 80
rg -n -F --max-count 20 --glob '*.md' --glob '*.py' --glob '*.sh' '<large-subtree-or-task-handle>' tasks reports codex/state/reviews scripts tests 2>/dev/null | head -n 120
```

Only if a creator/history gap remains:

```bash
jsonl-history-search semantic-debug "who created or used <specific subtree/task handle>?" --cwd "$PWD" --format text
```
