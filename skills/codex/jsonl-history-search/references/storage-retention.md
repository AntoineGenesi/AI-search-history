# Storage Retention Forensics

Use this when `ask` selects `storage_retention_forensics` for questions such as
"why is `.private` 23GB?", "what created these large private files?", "who uses
them?", or "what must be retained for examples/tests/claims?".

This route is guidance for evidence order, not a cleanup command.

## Evidence Order

1. `filesystem_inventory`: measure local disk layout first. Start with
   non-destructive size inventory, largest subtrees, top files, and file-type or
   extension summaries.
2. `artifact_join`: join large subtree or task handles to committed repo
   artifacts: tasks/reports/reviews/scripts/tests/pipeline state. Prefer
   current task files, reports, review artifacts, scripts, tests, and
   `codex/state/pipeline-state.md` before conversation mining.
3. `retention_classification`: classify each large handle as required for
   current tests/examples, useful retained evidence, claim-critical raw trace,
   recomputable/cache, stale candidate for a future explicit cleanup task, or
   unknown follow-up.
4. `targeted_jsonl_follow_up`: use JSONL conversation search only for a named
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

Run inventory first:

```bash
du -sh .private .private/* 2>/dev/null
```

Then join large handles to committed artifacts:

```bash
rg -n "<large-subtree-or-task-handle>" tasks reports reviews scripts tests codex/state
```

Only if a creator/history gap remains:

```bash
jsonl-history-search semantic-debug "who created or used <specific subtree/task handle>?" --cwd "$PWD" --format text
```
