# No Evidence

Use this reference for questions like "is there evidence for X?" or "did we ever discuss X in this bounded scope?"

## Core Rule

No evidence means `not_found_in_searched_scope`, not absent everywhere.

## Required Answer Elements

State:

- searched roots or files;
- search terms or evidence families used when useful;
- whether results were absent, weak/context-only, or secondary-only;
- caps, missing roots, unavailable exports, or omitted scopes;
- next bounded search/read if one would materially change the answer.

Useful statuses:

```text
not_found_in_searched_scope: searched scope contained no relevant primary evidence
secondary_only: only summaries/reports/assistant recaps were found
weak_candidate_only: hits were present but did not establish the requested claim
not_answerable_from_inspected_scope: required roots, anchors, or chronology were missing
capped: search stopped because a stated cap was reached
```

## Do Not Claim

Do not say:

- "never happened";
- "does not exist";
- "was deleted";
- "all history was searched";
- "there is no evidence anywhere";

unless those stronger claims are actually proven by a complete, relevant source inventory and search.

## Weak Candidates

If hits are only assistant summaries, task notes, copied prompt text, or later reports, label them as secondary or context-only. Do not turn them into proof that the original user said something or that an agent performed an action.

## Practical Search Pattern

For a bounded no-evidence answer, search at least:

```text
exact phrase or distinctive terms from the user's claim
obvious synonyms or command/tool names for the evidence family
role/tool-shape terms when the claim depends on user authorship or action proof
```

Stop after the bounded scope is enough to answer the user's actual question. If another root or date range could materially change the conclusion, name it as not searched.
