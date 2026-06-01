---
name: jsonl-history-search
description: Use JSONL for AI conversation mining across local Codex and Claude logs when prior instructions, past decisions, reports, artifacts, vague references, or semantic-debugging evidence are needed.
---

# JSONL

Use the portable CLI as JSONL's bounded AI conversation mining interface for semantic debugging and agent memory/debug workflows.

```bash
jsonl-history-search plan "the thing I told you before" --cwd "$PWD" --format text
jsonl-history-search search "the thing I told you before" --cwd "$PWD" --format markdown
```

The `plan` command is the trust boundary and does not open transcript files. It should show interpreted terms, inferred role, date window, selected roots, rejected roots, caps, warnings, and blind spots.

Do not recursively scan arbitrary home, drive, cache, model, or project roots. If a one-off forensic root is unavoidable, require `--allow-arbitrary-root` with explicit hard caps.

## Semantic Debugging

When the user asks why a bug was introduced or which assumption was wrong, use semantic debugging as a workflow layer over existing commands, not as a separate router.

Accepted handles are bug symptom, file/path, commit or commit range, task slug, error message, and approximate time. Route fuzzy remembered wording to `find`, provenance to `forensics`, executed tool proof to `structured-tool-events`, derived/sibling project questions to `compare-project-lineage`, skill behavior to `skill-audit`, and repeated automation opportunities to `workflow-repeat-finder`.

Return `user_request`, `agent_plan`, `code_change`, `review_outcome`, `later_symptom`, `likely_wrong_assumption`, `evidence`, `inference`, `uncertainty`, and `next_reads`. Required timeline phases are user request, agent plan, code change, review outcome, later symptom, and likely wrong assumption. Do not claim root cause unless linked evidence supports it; otherwise use `likely contributing assumption` or `candidate cause`.
