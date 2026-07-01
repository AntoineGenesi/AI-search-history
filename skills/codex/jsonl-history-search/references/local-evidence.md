# Local/Internal Evidence Mode

Use this specialization when the agent is diagnosing why JSONL or another
agent workflow failed and needs raw local source detail.

Trigger on asks such as:

- "why did this case fail?"
- "inspect the raw trace"
- "what commands did the agent keep running?"
- "save the source paths/line ranges for debugging"
- "do not let privacy summaries hide what happened"

First command for a retained or generated raw-run artifact:

```bash
jsonl-history-search local-evidence-diagnostic \
  --raw-run <raw-run.json> \
  --evidence-mode internal \
  --format json
```

Use `--evidence-mode public` for public/export-safe output.

Rules:

1. Internal/local mode may preserve raw local paths, exact line ranges, command
   traces, final-answer evidence, source-window excerpts/summaries, and
   pass/fail reasoning.
2. Public/export mode must suppress raw private paths, exact line numbers, raw
   query text, snippets, final-answer text, and pass/fail prose.
3. Do not upload raw transcripts, trace payloads, secrets, vectors, or indexes
   to external services.
4. Do not publish internal/local reports as public release artifacts.
5. Treat sanitized/public summaries as export surfaces, not as the authority
   for product debugging.

Agent use:

- Run internal mode when you need to diagnose a local product failure.
- Inspect the returned `source_refs`, `line_ranges`, `command_family_trace`,
  `final_answer`, and `pass_fail_reasoning`.
- Use public mode when writing committed/public release evidence or when a
  user explicitly asks for sanitized output.
