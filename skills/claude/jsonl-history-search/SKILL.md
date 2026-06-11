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

## Workflow Routing

Trigger JSONL even when the user does not name it. Route these asks first:

| User ask | Workflow | First command |
| --- | --- | --- |
| "What did I tell you before?" "Find my previous instruction/preference/feedback." | Remembered instruction retrieval | `jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format json` |
| "Did the agent actually run/edit/test/review X?" | Agent action/proof audit | `jsonl-history-search semantic-debug "did the agent actually run/edit/test/review <thing>" --cwd "$PWD" --format json` |
| "Why did this happen?" "Which conversation/task/review/commit explains this?" | Semantic debugging/provenance | `jsonl-history-search semantic-debug "why did this happen?" --task <task-slug> --path <path> --cwd "$PWD" --format json` |

Use `semantic-debug` as the first packet command for these workflows because it returns `source_locator_handoff`, source scope, route choices, candidates, timeline, structured tool/action proof, task/review/commit joins, no-evidence, uncertainty, next reads, token/byte tradeoffs, and claim boundaries. Inspect `source_locator_handoff` before broad search: it names the source family, selected/rejected roots, caps, blind spots, not-searched scope, candidate ordering, next reads, no-evidence meaning, and privacy/claim boundaries. Drop to `plan`, `find`, `structured-tool-events`, or `forensics` only for a focused follow-up.

Route quality-sensitive asks to the package before any standalone helper:

- Use `semantic-debug` first for remembered instructions, agent proof, provenance, ambiguous or semantic questions, no-evidence checks, and answer-quality-shaped investigations.
- Use `find` for a focused fuzzy, typo, synonym, or paraphrase candidate search when the full router packet is unnecessary.
- Use the helper only for fast operational recovery: latest/today/most-recent lookup, timestamp-window lookup, exact-phrase lookup, or broad-term recovery where the important words are expected to appear in the logs.

## Semantic Debugging

When the user asks why a bug was introduced, which assumption was wrong, whether an agent actually did claimed work, what prior instruction matters, or how a later symptom relates to earlier work, use the router command first.

Accepted handles are bug symptom, file/path, commit or commit range, task slug, error message, and approximate time. Route fuzzy remembered wording to `find`, provenance to `forensics`, executed tool proof to `structured-tool-events`, derived/sibling project questions to `compare-project-lineage`, skill behavior to `skill-audit`, and repeated automation opportunities to `workflow-repeat-finder`.

```bash
jsonl-history-search semantic-debug "why did this happen?" --cwd "$PWD" --format json
```

Add handles when available:

```bash
jsonl-history-search semantic-debug "did the agent actually do the claimed work?" \
  --task <task-slug> \
  --path <path> \
  --commit <commit> \
  --around "<timestamp>" \
  --cwd "$PWD" \
  --format json
```

For approved one-off private fixture roots outside the normal Codex/Claude
history locations, include hard caps in the first attempt. Do not remove the
guardrail; if the tool reports that an arbitrary root requires hard caps, retry
with both caps before changing strategy.

```bash
jsonl-history-search semantic-debug "what did I tell you before about <topic>" \
  --root /path/to/approved-jsonl-fixtures \
  --allow-arbitrary-root \
  --hard-cap-files 20 \
  --hard-cap-bytes 200000 \
  --max-files 20 \
  --max-bytes 200000 \
  --format json
```

Read `semantic_debug` fields first: `question_family`, `source_locator_handoff`, `scope`, `routes`, `strongest_candidates`, `timeline`, `tool_action_proof`, `task_review_commit_joins`, `candidate_causes`, `no_evidence`, `uncertainty`, `next_reads`, `token_byte_tradeoffs`, `sanitized_manifest_gate_evidence`, and `claim_boundaries`. Use `source_locator_handoff.next_reads` and `source_locator_handoff.candidate_ordering.top_candidates` before starting a wider scan. Legacy fixture summaries may still use `user_request`, `agent_plan`, `code_change`, `review_outcome`, `later_symptom`, `likely_wrong_assumption`, `evidence`, `inference`, `uncertainty`, and `next_reads`.

Keep evidence separate from inference. Do not claim root cause unless linked evidence supports it; otherwise use `likely contributing assumption` or `candidate cause`. Treat no-evidence as `not_found_in_searched_scope`, not all-history absence.

Do not run embeddings, timestamp sidecars, native scanner backends, private benchmark bridges, broad drive scans, or Semantic Linter runtime hooks as part of the default workflow.
