# Remembered Topic Or Provenance

Use when the user asks why they said, mentioned, or kept talking about a topic.

Examples:

- "why did I mention X?"
- "what was I talking about with X?"
- "why did I keep bringing up X?"

This is not artifact/currentness by default. Route to currentness only when the
wording implies competing plans, stale TODOs, ticket status, or direction
conflict.

## Procedure

1. Start with:

   ```bash
   jsonl-history-search semantic-debug "why did I mention <topic>" --cwd "$PWD" --format text
   ```

   Use `--format json --report-output <path>` only for automation or when the
   text output does not expose the needed primary source followthrough command.

2. If present, run
   the rendered `Primary source followthrough` command first. It opens the most
   relevant source window and gives the stop/continue boundary.
3. Inspect top source refs before concluding.
4. Reconstruct the user's reason from surrounding turns, not only the matching
   sentence.
5. Separate the user's actual point from the agent's later paraphrase.
6. Preserve corrections where the user says the agent misunderstood the topic.

## Answer Shape

- what the user meant by mentioning the topic;
- what the agent got right/wrong if relevant;
- source refs and next reads;
- whether this is a remembered-topic, currentness, or action-proof issue.
