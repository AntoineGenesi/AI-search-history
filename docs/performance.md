# Performance and limits

These are historical observations from private evaluations, summarized without
publishing their conversation inputs or answers. They are not a current-version
accuracy guarantee, billed-token measurement or prediction for your histories.

## Historical 17-question comparison

The portable `history-evidence` skill was run on all 17 matched questions. Answer
adjudication gave 14 passes, 2 partials and 1 failure, with 98 of 109 substantive
facets satisfied (89.9%). Process acceptance was separate: 7 runs completed and
10 failed closed on the benchmark's hygiene or skill-observation gates. An
answer-correct run could still fail those gates.

| Mode | Model-facing byte-proxy reduction versus manual search |
| --- | ---: |
| Skill only | 42.6% |
| Skill + CLI | 52.9% |

The CLI and manual baselines were previously reviewed runs; the skill-only lane
was a later forced-use evaluation. These aggregates do not isolate every change
in model, orchestration or workflow. Some individual questions used more bytes
than manual search. Proxy bytes are not provider tokens, charges, latency or
whole-machine resource use.

## Later 23-question diagnostic

There are 69 retained observations: 23 questions each for CLI without the
optional Jev engine, CLI with Jev, and the standalone skill. Every observation
received an independent answer review, but no pooled pass rate was adjudicated.
Observation counts are not correct-answer counts.

This was an instrumented diagnostic. Clean comparative acceptance failed;
isolated Jev benefit, clean speed or token savings, and a general overall winner
were not established. Captured traces do not prove complete provider context.
The user accepted the written diagnostic and stopped further verification;
that acceptance does not turn failed gates into passes.

## Retained experimental branch

`experimental-bounded-readback` preserves a separate retained implementation.
A narrow native-history replay changed from a no-answer failure to 19 of 20
graded points. That result concerns the reconstructed replay and its own
protocol. It does not establish broad retrieval improvement, a matched gain
over the default branch, or completion of the wider evaluation bank.

Across both modes, preserve the searched scope, source failures and caps when
answering. Missing evidence and paraphrase misses remain practical limitations.
