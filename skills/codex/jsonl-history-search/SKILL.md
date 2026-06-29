---
name: jsonl-history-search
description: Use JSONL for AI conversation mining across local Codex and Claude logs when the user asks to find prior things they said, debug past agent behavior, audit prior work, recover conversation history, or build semantic-debugging evidence. Trigger on phrases like "find all the times I said", "I told you before", "conversation logs", "chat history", "what happened in the past", "audit prior work", "where did we put that report", or "I remember a report".
metadata:
  short-description: Mine AI conversation JSONL
---

# JSONL

Use this when the user asks about prior conversations, repeated feedback, old decisions, reports, artifacts, semantic debugging, or "what happened". The default source of truth is local AI conversation JSONL logs, then project docs/git as secondary evidence.

## Workflow Routing

Trigger JSONL even when the user does not name it. Route these asks first:

| User ask | Workflow | First command |
| --- | --- | --- |
| "What did I tell you before?" "Find my previous instruction/preference/feedback." | Remembered instruction retrieval | `jsonl-history-search semantic-debug "what did I tell you before about <topic>" --cwd "$PWD" --format json` |
| "What did I just say?" "Find my last message." "Recent messages." "Since my last message." | Deterministic latest user-message lookup | `jsonl-history-search latest-user-messages --format json --max-matches 10` |
| "Did the agent actually run/edit/test/review X?" | Agent action/proof audit | `jsonl-history-search semantic-debug "did the agent actually run/edit/test/review <thing>" --cwd "$PWD" --format json` |
| "Why did this happen?" "Which conversation/task/review/commit explains this?" | Semantic debugging/provenance | `jsonl-history-search semantic-debug "why did this happen?" --task <task-slug> --path <path> --cwd "$PWD" --format json` |

Use `latest-user-messages` first for last/recent user-message wording. It sorts high-confidence Codex user messages by normalized timestamp and does not use semantic search, phrase matching, query expansion, or score ranking. Use `semantic-debug` as the first packet command for the other workflows because it returns `question_family`, `source_locator_handoff`, searched/not-searched scope, routes, candidates, timeline, structured tool/action proof, `source_window_action_proof`, task/review/commit joins, no-evidence, uncertainty, next reads, token/byte tradeoffs, and claim boundaries. Inspect `source_locator_handoff` before broad search: it names the source family, selected/rejected roots, caps, blind spots, not-searched scope, candidate ordering, next reads, no-evidence meaning, and privacy/claim boundaries. For claimed-action questions, inspect `semantic_debug.source_window_action_proof`; it separates user concern, command/tool call, command/tool output, final assistant claim, and adjacent artifact evidence so assistant prose or adjacent artifacts do not become primary action proof. When you need a smaller provenance map for handoff between agent turns, you may opt in with `--compact-provenance-handoff`; this adds `compact_provenance_handoff` derived from already emitted candidates, source refs, next reads, and no-evidence scope. It is not default behavior and is not proof of token savings, answer quality, product value, or default-promotion readiness. Drop to `find`, `structured-tool-events`, `forensics`, or the helper only for a focused follow-up or latest/timestamp recovery need.

Route quality-sensitive asks to the package before the helper:

- Use `semantic-debug` first for remembered instructions, agent proof, provenance, ambiguous or semantic questions, no-evidence checks, and answer-quality-shaped investigations.
- Use `find` for a focused fuzzy, typo, synonym, or paraphrase candidate search when the full router packet is unnecessary.
- Use the helper only for fast operational recovery: latest/today/most-recent lookup, timestamp-window lookup, exact-phrase lookup, or broad-term recovery where the important words are expected to appear in the logs.

Retrieval mode ladder for `find` and `semantic-debug`:

- Start with default `package` mode for normal remembered-instruction, provenance, and action-proof questions.
- Use `--retrieval-mode fielded-bm25` when default search misses or the question is a harder semantic/history search.
- Use `--retrieval-mode rrf-fusion` for high-recall hard questions when recall matters more than local runtime/cap pressure.

`fielded-bm25` and `rrf-fusion` are explicit opt-ins. Do not promote RRF to the default, and do not claim token savings, final-answer quality, or product superiority from retrieval mode choice alone.

## Search Order

1. For recent/"just now" recovery: newest modified `~/.codex/sessions/**/*.jsonl` files first.
2. `~/.codex/history.jsonl`
3. `~/.claude/projects/**/*.jsonl`
4. Project `.claude/` state, task/review files, `work/research`, `reports`, and repo docs.

Do not start with normal repo `rg` alone for history questions. The user explicitly wants JSONL-first mining because those files are the actual chat logs. Inside the JSONL shortlist, use fast raw-text search (`rg`) before JSON parsing; parse only matching lines unless a timestamp-window search requires structured timestamps.

## Tooling

Run the bundled helper only for fast operational recovery, not as the default semantic-debugging path:

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "search phrase"
```

Useful options:

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "html report" --context 220 --limit 30
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "interactive html" --roots ~/.codex ~/.claude
```

For "what did I just say", "find my last message", "recent messages", or "since my last message", use deterministic user-message recency first. Do not search for the literal phrase from the user's question.

```bash
jsonl-history-search latest-user-messages --format json --max-matches 10
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py --latest-user-messages --limit 10 --context 500
```

For other "just now", "latest", "most recent", or side-conversation recovery where you need a particular phrase, search newest-first and constrain by role when possible:

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "acceptance criteria" --latest --today --role agent_message --context 500 --limit 10
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "acceptance criteria" --latest --modified-after 2h --context 500 --limit 20
```

`--latest` scans the newest JSONL files first and defaults to the 20 newest files; pass `--recent-files 0` only when a full archive scan is really needed. The helper searches Codex session logs before history mirrors and Claude archives for recent lookups. If broad roots like `~/.codex` or `~/.claude` are passed, the helper narrows them to the actual chat-log locations instead of crawling unrelated config, skills, caches, or databases.

For incident forensics, correlate external timestamps against JSONL entries with a time window. Use the process creation time as `--around`; if the timestamp has no timezone, set the local offset explicitly.

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "" --around "2026-05-13T18:23:03" --window-minutes 5 --timezone-offset +10:00 --roots ~/.codex/sessions/2026/05/13
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "twitter-dm" --around "2026-05-13T18:23:03" --window-minutes 5 --timezone-offset +10:00
```

If the helper is insufficient, use `rg -n --glob '*.jsonl'` on the locations above, then parse nearby JSON objects for timestamps, roles, and message text.

## Relative Status Anchors

For admin/status questions such as "what happened since my last message?", "what did you do while I was asleep/away?", or "what happened after I told you to keep working?", resolve the intended anchor before answering.

Do:

- Treat "last message" as ambiguous when the immediately previous user turn is a status check, correction, or admin interruption.
- Prefer the last substantive work instruction or unattended-work boundary when the user mentions elapsed time, sleep, being away from keyboard, autonomous work, loops, or says "not the one just then".
- State the anchor used before summarizing, for example: "Anchoring to your 2026-06-29 autonomous-work instruction..."
- Reconstruct from durable evidence: git commits, task/review artifacts, pipeline state, generated reports, and JSONL logs.
- Answer both what changed and the handoff state: completed tasks, current task/loop, blockers, verification, and next work.

Do not:

- Literalize the newest chat turn if that would only summarize the last minute and miss the user's actual question.
- Summarize only the most recent task when the question is about a long unattended window.
- Hide ambiguity; if two anchors are plausible, name them and default to the wider substantive work anchor.

## Output Pattern

Return:

- locations searched;
- exact or near-exact recurring user preference wording where useful;
- grouped themes instead of raw dump;
- evidence references with file paths and line numbers when practical;
- blind spots, such as missing local logs or inaccessible project folders;
- recommended durable updates if the task is about improving skills/process.

## Semantic Debugging

Use this workflow when the user asks why a bug was introduced, which assumption was wrong, how a later symptom relates to earlier work, or what happened across a task/review/commit.

Accepted handles:

- bug symptom;
- file/path;
- commit or commit range;
- task slug;
- error message;
- approximate time.

Prefer the router command first when a compact agent-parseable packet is useful:

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

The router composes existing evidence commands:

- fuzzy symptom or remembered wording -> `jsonl-history-search find`;
- commit/path/task/review provenance -> `jsonl-history-search forensics`;
- executed actions/tool proof -> `jsonl-history-search structured-tool-events`;
- derived/sibling project relationship -> `jsonl-history-search compare-project-lineage`;
- skill behavior -> `jsonl-history-search skill-audit`;
- repeated automation opportunity -> `jsonl-history-search workflow-repeat-finder`.

Read `semantic_debug` fields first: `question_family`, `source_locator_handoff`, `scope`, `routes`, `strongest_candidates`, `timeline`, `tool_action_proof`, `task_review_commit_joins`, `candidate_causes`, `no_evidence`, `uncertainty`, `next_reads`, `token_byte_tradeoffs`, and `claim_boundaries`. Use `source_locator_handoff.next_reads` and `source_locator_handoff.candidate_ordering.top_candidates` before starting a wider scan. The legacy interpretation packet fields are `user_request`, `agent_plan`, `code_change`, `review_outcome`, `later_symptom`, `likely_wrong_assumption`, `evidence`, `inference`, `uncertainty`, and `next_reads`.

If a follow-up agent only needs provenance anchors, `semantic-debug` and `find` support `--compact-provenance-handoff`. The extra packet keeps legacy fields in place and omits snippet/source-window text; use its source refs and `next_reads` as a narrow handoff aid, not as a replacement for reading evidence or as a token-savings claim.

Keep evidence separate from inference. Never claim root cause unless linked evidence supports it; otherwise say `likely contributing assumption` or `candidate cause`. Treat no-evidence as `not_found_in_searched_scope`, not all-history absence. Treat `jsh-009`, `jsh-010`, and `jsh-011` as the lineage, skill-audit, and workflow-repeat benchmark owners rather than reimplementing those cases inside this workflow.

Do not run embeddings, timestamp sidecars, native scanner backends, private benchmark bridges, broad drive scans, or Semantic Linter runtime hooks as part of the default semantic-debug workflow.

When the output is substantial, use the `html-report` skill and build a direct-open HTML report with summary-first evidence cards, filters, timelines, and source links.
