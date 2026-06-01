---
name: jsonl-history-search
description: Search local Codex and Claude JSONL conversation logs first when the user asks to find prior things they said, past instructions, repeated feedback, previous reports/artifacts, what happened in a session, audit prior work, or recover conversation history. Trigger on phrases like "find all the times I said", "I told you before", "conversation logs", "chat history", "what happened in the past", "audit prior work", "where did we put that report", or "I remember a report".
metadata:
  short-description: Search chat JSONL history first
---

# JSONL History Search

Use this when the user asks about prior conversations, repeated feedback, old decisions, reports, artifacts, or "what happened". The default source of truth is local JSONL chat logs, then project docs/git as secondary evidence.

## Search Order

1. For recent/"just now" recovery: newest modified `~/.codex/sessions/**/*.jsonl` files first.
2. `~/.codex/history.jsonl`
3. `~/.claude/projects/**/*.jsonl`
4. Project `.claude/` state, task/review files, `work/research`, `reports`, and repo docs.

Do not start with normal repo `rg` alone for history questions. The user explicitly wants JSONL-first mining because those files are the actual chat logs. Inside the JSONL shortlist, use fast raw-text search (`rg`) before JSON parsing; parse only matching lines unless a timestamp-window search requires structured timestamps.

## Tooling

Prefer the bundled helper:

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "search phrase"
```

Useful options:

```bash
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "html report" --context 220 --limit 30
python3 ~/.codex/skills/jsonl-history-search/scripts/search_jsonl_history.py "interactive html" --roots ~/.codex ~/.claude
```

For "just now", "latest", "most recent", or side-conversation recovery, search newest-first and constrain by role when possible:

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

Route to existing evidence commands instead of inventing a new `semantic-debugger` command:

- fuzzy symptom or remembered wording -> `jsonl-history-search find`;
- commit/path/task/review provenance -> `jsonl-history-search forensics`;
- executed actions/tool proof -> `jsonl-history-search structured-tool-events`;
- derived/sibling project relationship -> `jsonl-history-search compare-project-lineage`;
- skill behavior -> `jsonl-history-search skill-audit`;
- repeated automation opportunity -> `jsonl-history-search workflow-repeat-finder`.

Build a packet with `user_request`, `agent_plan`, `code_change`, `review_outcome`, `later_symptom`, `likely_wrong_assumption`, `evidence`, `inference`, `uncertainty`, and `next_reads`. The required timeline phases are user request, agent plan, code change, review outcome, later symptom, and likely wrong assumption.

Keep evidence separate from inference. Never claim root cause unless linked evidence supports it; otherwise say `likely contributing assumption` or `candidate cause`. Treat `jsh-009`, `jsh-010`, and `jsh-011` as the lineage, skill-audit, and workflow-repeat benchmark owners rather than reimplementing those cases inside this workflow.

When the output is substantial, use the `html-report` skill and build a direct-open HTML report with summary-first evidence cards, filters, timelines, and source links.
