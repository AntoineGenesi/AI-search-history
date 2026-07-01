# Retrieval Escalation

Use when default retrieval misses, evidence is weak, the question is hard and
semantic, or the agent keeps broad-searching after a plausible but insufficient
hit.

## Modes

Default:

- `package`
- cheap/conservative;
- start here for normal `find` and `semantic-debug`.

Escalate to fielded BM25 when:

- default returns no evidence or weak adjacent candidates;
- record-level text matching is likely useful;
- the prompt has important terms, names, files, or phrases.

```bash
jsonl-history-search semantic-debug "<same question>" \
  --retrieval-mode fielded-bm25 \
  --cwd "$PWD" \
  --format json
```

Use RRF fusion when:

- high recall matters more than local runtime;
- package and fielded modes each recover partial useful evidence;
- the case is hard enough to justify extra local work.

```bash
jsonl-history-search semantic-debug "<same question>" \
  --retrieval-mode rrf-fusion \
  --cwd "$PWD" \
  --format json
```

RRF is not blindly default because prior pressure showed higher recall but
capped/extra local work. Promote it only when full agent workflow evidence
shows better source use, less churn, and better answers for that question
family.

## Escalation Smell

If the agent runs repeated broad `rg`, `read-more`, or same-file adjacent reads
without opening primary proof, escalate or stop and inspect the source refs
already found.
