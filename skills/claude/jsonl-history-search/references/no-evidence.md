# No-Evidence And Weak-Evidence Handling

Use when the tool reports no evidence, weak candidates, or only adjacent
artifacts.

## Rule

No evidence means `not_found_in_searched_scope`, not "does not exist anywhere".

## Procedure

1. Inspect searched roots, file caps, byte caps, date windows, and source
   family selection.
   If an explicit `no-evidence-report` returns
   `candidate_interpretation: weak_context_only`,
   `sufficient_to_stop_for_searched_scope_only: true`, and no `next_reads`,
   stop and answer from the report. Do not run `read-more` from weak candidate
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
