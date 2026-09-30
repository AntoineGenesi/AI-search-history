# Latest Status

Use this reference for "what happened since my last message?", parent-thread status, unattended-work intervals, and similar questions.

## Required Steps

1. Identify the user-authored anchor message.
2. Confirm the relevant thread/session when possible.
3. Inspect records after that anchor in chronological order within the relevant session/thread.
4. Separate raw session/tool activity from commits, task files, and summaries.
5. Report incomplete intervals, missing roots, or ambiguity instead of guessing.

## Anchor Rules

Do not treat the current side-thread as the parent/main thread unless evidence says it is. Do not treat injected context, system reminders, or assistant restatements as the user's anchor.

When several candidate anchors exist, choose the one with the strongest human-authored source ref and relevant session/thread metadata. If the anchor cannot be identified, ask for the wording/time or fail closed with the ambiguity.

## Evidence Priority

Primary:

- anchor user row;
- post-anchor raw assistant/session/tool records;
- tool outputs, edits, tests, commits tied to the interval.

Secondary:

- task reports, reviews, summaries, final assistant recaps.

Do not claim "nothing happened" from a fresh side-thread, current Git state, or a partial interval. Say `not answerable from inspected interval` when coverage is incomplete.

## Parent/Side Thread Caution

If the user asks about a "parent thread" from a side thread, do not assume the currently visible chat is the parent. Look for raw session metadata such as `parent_thread_id`, conversation id, session id, cwd/workspace continuity, or an exact side-thread ask that can identify the boundary. If multiple plausible parent sessions remain, answer `ambiguous`.

## Answer Shape

Use a compact timeline:

```text
Anchor: source_ref and short paraphrase
Searched interval: session/root and line/time range
What happened: primary events in order
Not proven / missing: gaps, missing roots, ambiguous threads, secondary-only items
Status: confirmed | not_answerable_from_inspected_scope | ambiguous | capped
```
