# No-Evidence And Weak-Evidence Handling

Use when the tool reports no evidence, weak candidates, or only adjacent
artifacts.

## First Command For Bounded Absence

For explicit searched-scope absence questions such as "is there evidence that X
was discussed in this bounded/source scope?", "did the searched scope contain
X?", or "prove there is no evidence of X in this selected root", prefer the
installed ask-first helper. It auto-executes only bounded no-evidence prompts,
so route selection, the no-evidence run, compact stdout, and the full saved
report happen from one helper invocation:

```bash
python3 "$HOME/.codex/skills/jsonl-history-search/scripts/jsonl_ask_first.py" "<user wording>"
```

The direct equivalent is:

```bash
jsonl-history-search ask --execute "<user wording>" --cwd "$PWD" --report-output .private/jsonl/ask-execute-no-evidence-report.json --format text
```

Use a relative report path. Do not include local absolute paths in reusable
route guidance.

## Rule

No evidence means `not_found_in_searched_scope`, not "does not exist anywhere".

## Procedure

1. Inspect searched roots, file caps, byte caps, date windows, and source
   family selection. In JSON output, inspect searched-scope and stop fields:
   `searched_scope`, `answer_scope`, `candidate_interpretation`,
   `sufficient_to_stop_for_searched_scope_only`, and
   `read_more_guidance.required_before_scoped_no_evidence_answer`.
   If `ask --execute` returns `answer_status:
   answer-scoped-no-evidence-now`, `promotion_status:
   promoted_scoped_no_evidence`, `answer_scope:
   not_found_in_searched_scope`, positive searched files/bytes, no missing
   selected root, no cap hit, `not_all_history_absence: true`, and
   `read_more_required_before_scoped_no_evidence_answer: false`, stop and
   answer from the scoped contract. Do not run `read-more` from weak candidate
   source refs unless the user needs those context records summarized.
2. Check whether the query is too literal, too broad, or using the wrong route.
3. Use targeted alternatives before broad scanning:
   - remembered instruction -> original user wording variants;
   - action proof -> task/path/commit/time handles;
   - status -> anchor/time-window;
   - currentness -> artifact path/topic/front-door files.
4. If default retrieval is weak, read `retrieval-escalation.md`.
5. State blind spots explicitly.

## Answer Shape

- searched scope;
- what was and was not found;
- likely next command, or state no next read is required when the weak-context
  stop rule applies;
- whether absence is meaningful or the search needs another route/mode.

## Stop Boundary

When the searched-scope stop rule is sufficient, do not run `read-more` before
answering the bounded absence question. `read-more` is an optional diagnostic
only for explaining weak/context records or recovering a specifically missing
required facet. This boundary does not suppress `read-more` for other routes.
