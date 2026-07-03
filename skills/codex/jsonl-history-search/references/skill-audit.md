# Skill Audit / Skill Check

Use this specialization when the user asks whether a named skill was triggered,
read, followed, skipped, missed, or misapplied.

Trigger on asks such as:

- "did the agent use the reviewer skill?"
- "did it read and follow product-fsm-orchestration?"
- "was the implementer skill missed or misapplied?"
- "check whether the skill's referenced files were followed"

First command:

```bash
jsonl-history-search skill-check "<skill-name>" --cwd "$PWD" --format text
```

`skill-check` is an alias for `skill-audit`; either command reaches the same
deep audit path.

Rules:

1. Keyword mentions alone are not actual skill use.
2. Inspect `skill_audit.usage_instances`, `non_use_mentions`, and
   `workflow_classification_summary` before making compliance claims.
3. Use `properly_applied`, `partially_applied`, `missed_trigger`,
   `misapplied`, and
   `skill_read_without_required_reference_followthrough` as the audit labels.
4. Use semantic-wrapup for broad frustration, corrections, and improvement
   mining. Use skill-check/skill-audit for named-skill compliance.

After running:

- inspect evidence refs in `usage_instances[*].skill_evidence`;
- inspect `non_use_mentions` for missed triggers or suggestion-only mentions;
- answer from source-backed classifications, not from the presence of the word
  "skill" alone.
