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
   select one large subtree from the top 40 before listing its top 40 files.
2. `generic_retention_policy`: read the current retention gate, reviewed
   ledger, state-file policy, and latest authorized outcome in one bounded
   command. For a generic question about where retained sessions live or how
   long they are kept, answer from this chain and stop.
3. `artifact_join`: only when the generic policy leaves one selected large
   subtree or task handle unclassified, join it to committed repo
   artifacts: tasks/reports/reviews/scripts/tests/pipeline state. Prefer
   current task files, reports, review artifacts, scripts, tests, and
   `codex/state/pipeline-state.md` before conversation mining.
4. `retention_classification`: classify each large handle as required for
   current tests/examples, useful retained evidence, claim-critical raw trace,
   recomputable/cache, stale candidate for a future explicit cleanup task, or
   unknown follow-up.
5. `targeted_jsonl_follow_up`: use JSONL conversation search only for a named
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
`sort` or writable temporary storage. Replace the selected-subtree placeholder
with one path printed by the labeled inventory.

Read generic policy directly after inventory:

```bash
for f in reports/jsh-067-private-artifact-retention-and-worktree-cleanup-gate.md reports/jsh-1245-private-retention-ledger.md reports/jsh-1246-state5-sqlite-retention-policy.md reports/jsh-1257-private-storage-cleanup-executed.md; do printf '\n--- %s ---\n' "$f"; nl -ba "$f" | sed -n '1,28p'; done
```

For generic `.private` location, lifetime, or policy questions, stop after that
read; it already includes citation-ready line numbers. Do not reopen those
policy files or serially inspect older tasks, reports, reviews, TODO, or state.
Only when one selected inventory handle remains unclassified, join that handle
to bounded policy and producer/consumer results:

```bash
rg -n -F --max-count 20 '<large-subtree-or-task-handle>' codex/state/pipeline-state.md TODO.md PROJECT.md CODEX-ORCHESTRATION.md learnings/INDEX.md 2>/dev/null | head -n 80
rg -n -F --max-count 20 --glob '*.md' --glob '*.py' --glob '*.sh' '<large-subtree-or-task-handle>' tasks reports codex/state/reviews scripts tests 2>/dev/null | head -n 120
```

Only if a creator/history gap remains:

```bash
jsonl-history-search semantic-debug "who created or used <specific subtree/task handle>?" --cwd "$PWD" --format text
```
