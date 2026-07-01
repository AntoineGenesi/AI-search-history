# Status And Artifact Currentness

Use this reference when a user asks a natural question that suggests confusion
about current direction, stale plans, repeated topics, task status, or whether
an artifact, local ticket, or exported issue should be trusted.

Examples:

- "what's the next real task here?"
- "why are there so many plans?"
- "is this still what we're doing?"
- "what happened to the case-002 work?"
- "which of these TODOs actually matters?"
- "is ticket ABC-42 still current?"
- "is this exported GitHub/Linear issue state still trustworthy?"
- "why did we switch direction?"
- "can these old worktrees be removed?"
- "are these checkouts stale or safe to prune?"

Do not require the user to ask "is this artifact stale?" Infer currentness
resolution when the question implies competing artifacts or unclear direction.

## Agent Procedure

1. Run the live currentness route first from the relevant project directory:
   `jsonl-history-search artifact-currentness "<artifact-or-question>" --cwd "$PWD" --format json`
   Use an explicit path when the user names one; otherwise pass the natural
   wording such as "which plan is current?"
2. Read the returned `currentness_verdict`, `why`, `evidence_chain`,
   `status_field_assessment`, `current_front_door`, `next_reads`, and
   `blind_spots` before answering.
   For worktree cleanup/currentness prompts, also read
   `worktree_currentness.classifications`, each item's `source_refs`,
   `next_commands`, and `preservation_guidance`.
3. Start with JSONL/history evidence, not only repo `git status`, when the
   route reports missing or ambiguous local evidence.
4. Identify topic and artifact candidates:
   - task/TODO/PLAN/ROADMAP docs;
   - tickets/issues if locally/exported available;
   - generic status/state docs;
   - exported GitHub/Linear-style files with state, labels, milestone, project,
     workflow state, assignee, closed/updated, or issue fields;
   - reviews/reports/state files;
   - commits, branches, worktrees;
   - conversation turns around creation and later correction.
5. Check current front doors:
   - pipeline/state files when present;
   - TODO or roadmap pointers;
   - latest relevant task/review/report references.
6. Treat artifact status fields as claims, not truth. This includes `Status`,
   `state`, `phase`, issue state, labels, milestone, project fields, workflow
   state, and exported ticket fields.
7. Look for later evidence:
   - commits after artifact creation;
   - tasks/reviews/reports that completed or superseded it;
   - user corrections;
   - newer front-door pointers;
   - newer local references to the same ticket/plan/status artifact;
   - exported ticket state, interpreted as a local claim rather than a live
     source of truth;
   - branch/worktree ownership.
8. For worktree cleanup/currentness, inspect or run the targeted commands for:
   - `git worktree list --porcelain`;
   - `git -C <worktree> status --short --branch`;
   - `git branch --contains <head>`;
   - `git log --all --grep=<id-or-slug>`;
   - targeted `rg` over `codex/state`, `tasks`, `reports`, `reviews`, `TODO.md`, and `TASKS.md`;
   - `find <worktree>/.private -maxdepth 4 -type f -print`.
9. Preserve worktree-local `.private` artifacts first:
   copy/archive them under parent
   `.private/worktree-cleanup-archive/<slug>/...` before physical worktree
   pruning. Save local diffs there too when the worktree is dirty.
10. Keep branch refs retained unless the user explicitly scopes branch ref
   pruning.
11. Answer with a currentness verdict and source refs.

## Verdicts

Use one of:

- `current`
- `stale`
- `superseded`
- `contradicted`
- `historical_provenance`
- `unknown`

The route may classify `status_field_assessment.assessment` as a supported,
conflicting, or claim-only status. Do not answer from `Status: READY`,
`state: closed`, labels, milestones, projects, or workflow fields alone.
Inspect `evidence_chain` and `next_reads` first.

For worktree cleanup/currentness, use the per-item
`worktree_currentness.classifications` values:

- `KEEP_ACTIVE`
- `SAFE_TO_PRUNE`
- `PRESERVE_THEN_PRUNE`
- `DEBUG_ONLY_NO_MERGE`
- `SUPERSEDED`
- `NEEDS_USER_DECISION`
- `UNKNOWN`

## Answer Shape

Lead with the practical answer:

- whether to trust the artifact/topic as current;
- why;
- what later evidence changes interpretation;
- what source refs or commands to inspect next;
- blind spots.

Avoid process chronology unless it explains the currentness verdict.
