# Remembered Instruction Or Preference

Use when the user asks what they previously told the agent or which preference
they expressed.

Open original user-authored source refs; distinguish wording from paraphrases and
state scope. Implementation prompts remain `history_answer_only`: report
imperatives; do not edit, test, delegate, or execute. If primary history is missing,
run its target-preserving JSONL continuation; if absent, fail closed without asking.
Never reconstruct from summaries or unrelated local artifacts.
