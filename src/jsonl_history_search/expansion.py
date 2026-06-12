from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Callable


TASK_SLUG_RE = re.compile(r"\b([a-z]{1,8})[-_](\d{2,6}[a-z]?)(?:[-_]([a-z0-9]{1,20}))?\b")
SEPARATED_WORD_RE = re.compile(r"[a-z0-9]+")
MAX_EXACT_PHRASE_CHARS = 240
MAX_EXACT_PHRASE_TOKENS = 16
MAX_EXPANDED_TERMS = 110

SYNONYMS: dict[str, tuple[str, ...]] = {
    "artifact": ("artifacts", "doc", "docs", "file", "output", "report"),
    "artifacts": ("artifact", "doc", "docs", "file", "output", "report"),
    "bug": ("defect", "error", "failure", "fix", "issue", "regression"),
    "closeout": ("recap", "summary", "wrap", "wrapup"),
    "cluster": ("clustering", "group", "grouped", "grouping"),
    "clustering": ("cluster", "group", "grouped", "grouping"),
    "csv": ("spreadsheet", "table"),
    "doc": ("artifact", "docs", "document", "file", "markdown", "note", "report"),
    "docs": ("artifact", "doc", "document", "file", "markdown", "note", "report"),
    "document": ("doc", "docs", "file", "handoff", "note", "report"),
    "edit": ("changed", "edited", "modified", "patched", "refactored", "tuned", "updated"),
    "edited": ("changed", "edit", "modified", "patched", "refactored", "tuned", "updated"),
    "file": ("artifact", "doc", "docs", "output", "path", "report"),
    "handoff": ("continuation", "handover", "next-session", "note", "transfer"),
    "handover": ("continuation", "handoff", "next-session", "note", "transfer"),
    "html": ("report", "webpage"),
    "implement": ("build", "built", "implemented", "implementation"),
    "implemented": ("build", "built", "implement", "implementation"),
    "issue": ("bug", "defect", "error", "failure", "fix", "regression"),
    "json": ("jsonl", "structured"),
    "jsonl": ("json", "transcript"),
    "library": ("module", "package", "system"),
    "markdown": ("doc", "docs", "md", "report"),
    "note": ("doc", "document", "handoff", "handover", "notes"),
    "notes": ("doc", "document", "handoff", "handover", "note"),
    "preference": ("preferences", "prefer", "preferred", "style", "taste"),
    "preferences": ("preference", "prefer", "preferred", "style", "taste"),
    "prefer": ("preference", "preferences", "preferred", "style", "taste"),
    "preferred": ("preference", "preferences", "prefer", "style", "taste"),
    "report": ("artifact", "doc", "docs", "file", "html", "markdown", "reports"),
    "reports": ("artifact", "doc", "docs", "file", "html", "markdown", "report"),
    "retry": ("attempt", "attempts", "retried", "retries", "retrying"),
    "retries": ("attempt", "attempts", "retried", "retry", "retrying"),
    "search": ("find", "lookup", "retrieval", "retrieve"),
    "searched": ("find", "lookup", "retrieval", "retrieve", "search"),
    "summary": ("closeout", "recap", "wrap", "wrapup"),
    "test": ("pytest", "regression", "tests", "verification"),
    "tests": ("pytest", "regression", "test", "verification"),
    "wrap": ("closeout", "recap", "summary", "wrapup"),
    "wrapup": ("closeout", "recap", "summary", "wrap"),
}

SEMANTIC_LOCAL: dict[str, tuple[str, ...]] = {
    "edit": ("change", "patch", "refactor", "tune"),
    "edited": ("change", "patch", "refactor", "tune"),
    "library": ("codebase", "module", "source", "subsystem"),
    "physics": ("collision", "collisions", "engine", "integration", "physics.ts", "rigid-body", "rigidbody"),
    "session": ("closeout", "continuation", "handoff", "summary"),
    "skill": ("capability", "practice", "workflow"),
    "workflow": ("automation", "process", "routine", "sequence"),
}

PREFERENCE_WORDS = {
    "avoid",
    "dislike",
    "disliked",
    "like",
    "liked",
    "prefer",
    "preferred",
    "preference",
    "preferences",
    "style",
    "taste",
}

WORKTREE_CLEANUP_STATUS_TERMS = ("stale", "obsolete", "abandoned")
WORKTREE_CLEANUP_WORKTREE_TERMS = (
    "worktree",
    "worktrees",
    "checkout",
    "checkouts",
    "worker",
    "worker tree",
    "worker-tree",
    "worker_tree",
)
WORKTREE_CLEANUP_ACTION_TERMS = (
    "clean",
    "cleaned",
    "cleanup",
    "remove",
    "removed",
    "prune",
    "pruned",
)

RETRIEVAL_MISS_CONTEXT_TERMS = (
    "retrieval",
    "retrieve",
    "retrieved",
    "search",
    "searched",
    "lookup",
    "lookups",
    "no evidence",
    "no-evidence",
    "no_evidence",
)
RETRIEVAL_MISS_FAILURE_TERMS = (
    "fail",
    "failed",
    "failing",
    "failure",
    "miss",
    "missed",
    "misses",
    "missing",
    "not found",
    "not-found",
    "not_found",
)
RETRIEVAL_MISS_EXPANSION_TERMS = (
    "command",
    "evidence",
    "fail",
    "failed",
    "failure",
    "lookup",
    "miss",
    "missed",
    "missing",
    "no evidence",
    "no-evidence",
    "no_evidence",
    "not found",
    "not-found",
    "not_found",
    "retrieval",
    "search",
    "source",
)

SOURCE_SELECTION_FAILURE_EXPANSION_TERMS = (
    "candidate",
    "dropped",
    "expected",
    "file",
    "opened",
    "opening",
    "relevant",
    "retention",
    "root",
    "row",
    "selected",
)

ACTION_PROOF_AGENT_TERMS = ("agent", "assistant")
ACTION_PROOF_ACTION_TERMS = ("action", "proof")
ACTION_PROOF_EXECUTION_TERMS = (
    "command",
    "commit",
    "committed",
    "execute",
    "executed",
    "execution",
    "implement",
    "implementation",
    "pytest",
    "ran",
    "run",
    "test",
    "tested",
    "tests",
)
ACTION_PROOF_EXPANSION_TERMS = (
    "action",
    "agent",
    "command",
    "committed",
    "executed",
    "implementation",
    "pytest",
    "ran",
    "run",
    "tested",
    "tests",
)

CHANGE_SUMMARY_ADD_OR_CHANGE_ACTION_TERMS = (
    "add",
    "added",
    "change",
    "changed",
    "changes",
    "fixed",
    "implement",
    "implemented",
)
CHANGE_SUMMARY_TOOL_CONTEXT_TERMS = (
    "expansion",
    "helper",
    "jsonl",
    "output",
    "package",
    "query",
    "queries",
    "ranking",
    "retrieval",
    "scanner",
    "search",
    "tool",
    "tools",
    "wording",
)
CHANGE_SUMMARY_EXPANSION_TERMS = (
    "added",
    "fixed",
    "implemented",
    "improved",
    "output",
    "recover",
    "recovered",
    "recovers",
    "tool",
    "wording",
)

STALE_REFERENCE_STATE_STATUS_TERMS = ("stale", "old", "obsolete", "superseded")
STALE_REFERENCE_STATE_REFERENCE_TERMS = (
    "mention",
    "mentioned",
    "reference",
    "referenced",
    "referencing",
)
STALE_REFERENCE_STATE_CONTEXT_TERMS = (
    "active",
    "active-state",
    "handoff",
    "old",
    "obsolete",
    "pointer",
    "queue",
    "referenced",
    "state",
    "superseded",
)

NEXT_WORK_HANDOFF_EXPANSION_TERMS = (
    "choose",
    "cleanup",
    "handoff",
    "merge",
    "post-merge",
    "proceed",
    "queue",
    "retrieval",
    "reviewer",
    "selected",
    "worktree",
)

NEXT_CODEX_SESSION_CONTEXT_SESSION_TERMS = (
    "codex",
    "continuation",
    "continue",
    "continuing",
    "future",
    "next session",
    "next-session",
    "resume",
    "resuming",
    "session",
)
NEXT_CODEX_SESSION_CONTEXT_KNOW_TERMS = (
    "aware",
    "context",
    "know",
    "remember",
    "should know",
)
NEXT_CODEX_SESSION_CONTEXT_ACTIVE_TERMS = (
    "active",
    "active state",
    "active-state",
    "current",
    "progress",
    "state",
    "status",
    "task",
)
NEXT_CODEX_SESSION_CONTEXT_BLOCKER_TERMS = (
    "blocked",
    "blocker",
    "blockers",
    "caveat",
    "caveats",
    "constraint",
    "constraints",
    "risk",
    "risks",
)
NEXT_CODEX_SESSION_CONTEXT_DECISION_TERMS = (
    "decided",
    "decision",
    "decisions",
    "outcome",
    "resolution",
    "selected",
)
NEXT_CODEX_SESSION_CONTEXT_HANDOFF_TERMS = (
    "closeout",
    "handoff",
    "handoff note",
    "handoff notes",
    "handover",
    "notes",
    "summary",
)
NEXT_CODEX_SESSION_CONTEXT_EXPANSION_TERMS = (
    *NEXT_CODEX_SESSION_CONTEXT_SESSION_TERMS,
    *NEXT_CODEX_SESSION_CONTEXT_KNOW_TERMS,
    *NEXT_CODEX_SESSION_CONTEXT_ACTIVE_TERMS,
    *NEXT_CODEX_SESSION_CONTEXT_BLOCKER_TERMS,
    *NEXT_CODEX_SESSION_CONTEXT_DECISION_TERMS,
    *NEXT_CODEX_SESSION_CONTEXT_HANDOFF_TERMS,
)

CLAIM_BOUNDARY_CLAIM_TERMS = ("claim", "claimed", "claims", "overclaim", "overclaimed")
CLAIM_BOUNDARY_QUALITY_TERMS = (
    "answer",
    "evidence",
    "reviewed",
    "speed",
    "token efficiency",
    "token-efficiency",
    "unsupported",
    "value",
)

PROMISE_VS_COMMITTED_PROMISE_TERMS = (
    "claim",
    "claimed",
    "claims",
    "plan",
    "planned",
    "plans",
    "promise",
    "promised",
    "said",
    "say",
)
PROMISE_VS_COMMITTED_DELIVERY_TERMS = (
    "actual",
    "change",
    "changed",
    "changes",
    "commit",
    "committed",
    "diff",
    "implemented",
    "patch",
    "patched",
)
PROMISE_VS_COMMITTED_EXPANSION_TERMS = (
    "actual",
    "claim",
    "claimed",
    "commit",
    "committed",
    "changed",
    "diff",
    "gap",
    "implemented",
    "patch",
    "planned",
    "promise",
    "promised",
    "said",
    "unsupported",
)

PROCESS_DETOUR_LESSON_EXPANSION_TERMS = (
    "anchored",
    "candidate",
    "concrete",
    "coordinator",
    "hours",
    "improvement",
    "improvements",
    "jsonl",
    "lesson",
    "process",
    "process only",
    "process-only",
    "process_only",
    "product",
    "queue",
    "reseeding",
    "selection",
    "tool",
)

HANDOFF_DOCUMENT_LOCATION_DOCUMENT_TERMS = (
    "artifact",
    "artifacts",
    "doc",
    "docs",
    "document",
    "documents",
    "file",
    "handoff",
    "handover",
    "note",
    "notes",
    "report",
    "reports",
)
HANDOFF_DOCUMENT_LOCATION_CONTEXT_TERMS = (
    "branch",
    "checkout",
    "location",
    "main",
    "parent",
    "path",
    "placed",
    "placement",
    "provenance",
    "put",
    "retained",
    "stored",
    "where",
    "worktree",
    "worktrees",
    "written",
    "wrote",
)
HANDOFF_DOCUMENT_LOCATION_EXPANSION_TERMS = (
    "artifact",
    "branch",
    "checkout",
    "location",
    "main",
    "parent",
    "path",
    "retained",
    "where",
    "worktree",
    "written",
)

FILE_SESSION_PROVENANCE_TARGET_TERMS = (
    "artifact",
    "artifacts",
    "file",
    "files",
    "path",
    "paths",
)
FILE_SESSION_PROVENANCE_ACTOR_TERMS = (
    "agent",
    "agents",
    "assistant",
    "provenance",
    "responsibility",
    "responsible",
    "session",
    "sessions",
    "transcript",
    "transcripts",
)
FILE_SESSION_PROVENANCE_CREATION_TERMS = (
    "create",
    "created",
    "creates",
    "creating",
    "generate",
    "generated",
    "generates",
    "generating",
    "save",
    "saved",
    "saves",
    "saving",
    "write",
    "writes",
    "writing",
    "written",
    "wrote",
)
FILE_SESSION_PROVENANCE_EXPANSION_TERMS = (
    "artifact",
    "branch",
    "command",
    "commit",
    "created",
    "cwd",
    "diff",
    "file",
    "generated",
    "path",
    "provenance",
    "responsible",
    "saved",
    "session",
    "tool",
    "transcript",
    "worktree",
    "wrote",
)

PRE_CHANGE_FILE_PATH_MENTION_CONVERSATION_TERMS = (
    "chat",
    "chats",
    "conversation",
    "conversations",
    "session",
    "sessions",
)
PRE_CHANGE_FILE_PATH_MENTION_REFERENCE_TERMS = (
    "mention",
    "mentioned",
    "mentions",
    "reference",
    "referenced",
    "references",
    "referencing",
)
PRE_CHANGE_FILE_PATH_MENTION_TARGET_TERMS = (
    "file",
    "files",
    "path",
    "paths",
)
PRE_CHANGE_FILE_PATH_MENTION_TEMPORAL_TERMS = (
    "before",
    "prior",
    "previous",
    "earlier",
)
PRE_CHANGE_FILE_PATH_MENTION_CHANGE_TERMS = (
    "change",
    "changed",
    "changes",
    "diff",
    "edit",
    "edited",
    "git",
    "modified",
    "patch",
    "patched",
)
PRE_CHANGE_FILE_PATH_MENTION_EXPANSION_TERMS = (
    "before",
    "changed",
    "chat",
    "conversation",
    "diff",
    "edited",
    "earlier",
    "file",
    "file path",
    "git",
    "mentioned",
    "modified",
    "patch",
    "patched",
    "path",
    "previous",
    "prior",
    "provenance",
    "referenced",
    "session",
    "source",
)

COMMIT_USER_REQUEST_PROVENANCE_CHANGE_TERMS = (
    "change",
    "changed",
    "code",
    "commit",
    "committed",
    "diff",
    "patch",
)
COMMIT_USER_REQUEST_PROVENANCE_REQUEST_TERMS = (
    "ask",
    "asked",
    "instruction",
    "instructions",
    "prompt",
    "request",
    "requests",
    "user",
)
COMMIT_USER_REQUEST_PROVENANCE_CONVERSATION_TERMS = (
    "chat",
    "chats",
    "conversation",
    "conversations",
)
COMMIT_USER_REQUEST_PROVENANCE_CAUSALITY_TERMS = (
    "because",
    "behind",
    "cause",
    "caused",
    "causes",
    "causing",
    "feedback",
    "happen",
    "happened",
    "happens",
    "provenance",
    "reason",
    "reasons",
)
COMMIT_USER_REQUEST_PROVENANCE_EXPANSION_TERMS = (
    "ask",
    "cause",
    "caused",
    "change",
    "changed",
    "chat",
    "code",
    "commit",
    "committed",
    "conversation",
    "diff",
    "feedback",
    "instruction",
    "patch",
    "prompt",
    "provenance",
    "reason",
    "request",
    "source",
    "user",
    "why",
)

REVERT_FIX_FEEDBACK_SOURCE_CHANGE_TERMS = (
    "fix",
    "fixed",
    "fixes",
    "repair",
    "repaired",
    "repairs",
    "revert",
    "reverted",
    "reverts",
    "rollback",
    "rolled back",
    "rolled-back",
)
REVERT_FIX_FEEDBACK_SOURCE_USER_TERMS = (
    "feedback",
    "request",
    "requested",
    "user",
    "user feedback",
    "user request",
)
REVERT_FIX_FEEDBACK_SOURCE_AGENT_TERMS = (
    "agent",
    "agent review",
    "self review",
    "self-review",
    "self_review",
    "self-reviewing",
    "self-reviewed",
)
REVERT_FIX_FEEDBACK_SOURCE_PROVENANCE_TERMS = (
    "because",
    "based",
    "based on",
    "cause",
    "caused",
    "provenance",
    "reason",
    "source",
    "why",
)
REVERT_FIX_FEEDBACK_SOURCE_COMMIT_TERMS = (
    "change",
    "commit",
    "committed",
    "diff",
    "patch",
    "patched",
)
REVERT_FIX_FEEDBACK_SOURCE_EXPANSION_TERMS = (
    "agent",
    "agent review",
    "based on",
    "cause",
    "commit",
    "diff",
    "feedback",
    "fix",
    "fixed",
    "patch",
    "provenance",
    "reason",
    "repair",
    "repaired",
    "revert",
    "reverted",
    "self review",
    "self-review",
    "self_review",
    "source",
    "user",
    "user feedback",
    "user request",
)

BRANCH_MERGE_SURROUNDING_CONVERSATION_EXPANSION_TERMS = (
    "adjacent",
    "after",
    "around",
    "before",
    "branch",
    "chat",
    "context",
    "conversation",
    "merge",
    "merge commit",
    "merged",
    "nearby",
    "post-merge",
    "provenance",
    "review",
    "session",
    "surrounding",
    "timeline",
)

FINAL_DECISION_AFTER_DELIBERATION_FINALITY_TERMS = (
    "final",
    "last",
    "settle",
    "settled",
    "ultimate",
    "ultimately",
)
FINAL_DECISION_AFTER_DELIBERATION_DECISION_TERMS = (
    "conclusion",
    "conclusions",
    "decision",
    "decisions",
    "outcome",
    "outcomes",
    "resolution",
    "resolutions",
    "resolved",
)
FINAL_DECISION_AFTER_DELIBERATION_DISCUSSION_TERMS = (
    "back and forth",
    "back-and-forth",
    "back_and_forth",
    "debate",
    "debated",
    "debates",
    "discussion",
    "discussions",
    "iteration",
    "iterations",
)
FINAL_DECISION_AFTER_DELIBERATION_CONVERSATION_TERMS = (
    "chat",
    "chats",
    "context",
    "conversation",
    "conversations",
    "session",
    "thread",
    "threads",
    "transcript",
)
FINAL_DECISION_AFTER_DELIBERATION_EXPANSION_TERMS = (
    "back and forth",
    "back-and-forth",
    "back_and_forth",
    "chat",
    "conclusion",
    "context",
    "conversation",
    "debate",
    "decision",
    "discussion",
    "final",
    "iteration",
    "last",
    "outcome",
    "resolution",
    "resolved",
    "session",
    "settled",
    "thread",
    "transcript",
)

FINAL_ANSWER_SOURCE_HIT_QUALITY_CONTEXT_TERMS = (
    "available",
    "bad",
    "correct",
    "correctness",
    "facet",
    "facets",
    "good",
    "missing",
    "omit",
    "omits",
    "omitted",
    "partial",
    "preservation",
    "preserve",
    "preserved",
    "quality",
    "sufficient",
    "unavailable",
)
FINAL_ANSWER_SOURCE_HIT_EXPANSION_TERMS = (
    "available",
    "correctness",
    "facet",
    "facets",
    "final answer",
    "final-answer",
    "missing",
    "omitted",
    "partial",
    "preservation",
    "preserved",
    "quality",
    "source hit",
    "source-hit",
    "unavailable",
)

TASK_COMPLETION_STATE_TASK_CONTEXT_TERMS = (
    "backlog",
    "delivery",
    "deliverable",
    "deliverables",
    "queue",
    "task",
    "tasks",
    "todo",
    "todos",
)
TASK_COMPLETION_STATE_COMPLETION_TERMS = (
    "complete",
    "completed",
    "done",
    "merged",
    "reviewed",
    "verified",
)
TASK_COMPLETION_STATE_STATE_TERMS = (
    "backlog",
    "pending",
    "queue",
    "stale",
    "state",
    "status",
    "superseded",
    "todo",
    "todos",
)
TASK_COMPLETION_STATE_EXPANSION_TERMS = (
    "backlog",
    "complete",
    "completed",
    "done",
    "merged",
    "pending",
    "queue",
    "reviewed",
    "stale",
    "state",
    "status",
    "superseded",
    "todo",
    "verified",
)

REVIEW_VERDICT_STATUS_REVIEW_TERMS = (
    "review",
    "reviewed",
    "reviewer",
)
REVIEW_VERDICT_STATUS_OUTCOME_TERMS = (
    "approved",
    "blocked",
    "fail",
    "failed",
    "partial",
    "partially",
    "pass",
    "rejected",
    "stop",
    "stopped",
    "verified",
    "verdict",
)
REVIEW_VERDICT_STATUS_GATE_TERMS = (
    "gate",
    "merge",
)
REVIEW_VERDICT_STATUS_EXPANSION_TERMS = (
    "review",
    "reviewed",
    "reviewer",
    "review artifact",
    "verdict",
    "verified",
    "partially verified",
    "partially_verified",
    "partial",
    "failed",
    "blocked",
    "approved",
    "rejected",
    "pass",
    "fail",
    "status",
    "merge gate",
    "gate",
)

INTERVENTION_EFFECT_CONTEXT_TERMS = (
    "intervention",
    "interventions",
    "steering",
    "suppression",
)
INTERVENTION_EFFECT_OUTCOME_TERMS = (
    "after",
    "behavior",
    "behaviour",
    "before",
    "change",
    "changed",
    "corrected",
    "effect",
    "effects",
    "false-positive",
    "false positive",
    "help",
    "helped",
    "ignored",
    "noise",
    "noisy",
    "outcome",
    "side-effect",
    "side effect",
    "signal",
)
INTERVENTION_EFFECT_EXPANSION_TERMS = (
    "intervention",
    "interventions",
    "changed",
    "behavior",
    "behaviour",
    "steering",
    "suppression",
    "false positive",
    "false-positive",
    "noise",
    "noisy",
    "signal",
    "outcome",
    "before",
    "after",
    "corrected",
    "ignored",
    "helped",
    "side effect",
    "side-effect",
)

SUPPRESSION_HIDDEN_FAILURE_SUPPRESSION_TERMS = (
    "mask",
    "masked",
    "masking",
    "suppressed",
    "suppression",
    "suppressions",
)
SUPPRESSION_HIDDEN_FAILURE_HIDDEN_TERMS = (
    "cover up",
    "cover-up",
    "covered up",
    "covered-up",
    "hidden",
    "hide",
    "hiding",
    "miss",
    "missed",
    "misses",
)
SUPPRESSION_HIDDEN_FAILURE_TRUE_FAILURE_TERMS = (
    "actual failure",
    "false negative",
    "false-negative",
    "real failure",
    "true failure",
    "true-positive",
)
SUPPRESSION_HIDDEN_FAILURE_EVIDENCE_TERMS = (
    "evidence",
    "proof",
    "review",
    "reviewed",
    "source",
    "trace",
)
SUPPRESSION_HIDDEN_FAILURE_LINTER_TERMS = (
    "intervention",
    "interventions",
    "linter",
    "semantic linter",
    "steering",
)
SUPPRESSION_HIDDEN_FAILURE_EXPANSION_TERMS = (
    "actual failure",
    "cover up",
    "cover-up",
    "covered up",
    "covered-up",
    "evidence",
    "false negative",
    "false-negative",
    "hidden",
    "intervention",
    "linter",
    "mask",
    "masked",
    "masking",
    "missed",
    "proof",
    "real failure",
    "reviewed",
    "semantic linter",
    "source",
    "suppressed",
    "suppression",
    "trace",
    "true failure",
    "true-positive",
)

LINTER_PHASE_INAPPROPRIATE_FIRE_LINTER_TERMS = (
    "callout",
    "callouts",
    "intervention",
    "interventions",
    "linter",
    "semantic linter",
    "steering",
)
LINTER_PHASE_INAPPROPRIATE_FIRE_FIRED_TERMS = (
    "alert",
    "alerted",
    "call",
    "called",
    "fire",
    "fired",
    "flag",
    "flagged",
    "trigger",
    "triggered",
)
LINTER_PHASE_INAPPROPRIATE_FIRE_PHASE_TERMS = (
    "coordinator",
    "coordination",
    "orchestration",
    "plan",
    "planner",
    "planning",
    "set up",
    "setup",
)
LINTER_PHASE_INAPPROPRIATE_FIRE_JUDGMENT_TERMS = (
    "early",
    "inappropriate",
    "not appropriate",
    "premature",
    "prematurely",
    "should not",
    "should-not",
    "should_not",
    "shouldn't",
)
LINTER_PHASE_INAPPROPRIATE_FIRE_NOISE_TERMS = (
    "false positive",
    "false-positive",
    "false_positive",
    "noise",
    "noisy",
    "spurious",
    "unwanted",
)
LINTER_PHASE_INAPPROPRIATE_FIRE_EXPANSION_TERMS = (
    "alert",
    "callout",
    "callouts",
    "coordinator",
    "coordination",
    "early",
    "false positive",
    "false-positive",
    "fired",
    "flagged",
    "inappropriate",
    "intervention",
    "interventions",
    "linter",
    "noise",
    "noisy",
    "orchestration",
    "planning",
    "premature",
    "prematurely",
    "semantic linter",
    "set up",
    "setup",
    "should not",
    "should-not",
    "spurious",
    "steering",
    "triggered",
    "unwanted",
)

COORDINATOR_WORKFLOW_NOISY_LABEL_LABEL_TERMS = (
    "classification",
    "classifications",
    "classified",
    "label",
    "labeled",
    "labels",
    "tag",
    "tagged",
    "tags",
)
COORDINATOR_WORKFLOW_NOISY_LABEL_NOISE_TERMS = (
    "false positive",
    "false-positive",
    "false_positive",
    "noise",
    "noisy",
    "spurious",
)
COORDINATOR_WORKFLOW_NOISY_LABEL_WORKFLOW_TERMS = (
    "coordination",
    "coordinator",
    "orchestration",
    "plan",
    "planner",
    "planning",
    "workflow",
    "workflows",
)
COORDINATOR_WORKFLOW_NOISY_LABEL_LINTER_TERMS = (
    "intervention",
    "interventions",
    "linter",
    "semantic linter",
    "steering",
)
COORDINATOR_WORKFLOW_NOISY_LABEL_EXPANSION_TERMS = (
    "classification",
    "classifications",
    "classified",
    "coordination",
    "coordinator",
    "false positive",
    "false-positive",
    "intervention",
    "interventions",
    "label",
    "labels",
    "linter",
    "noise",
    "noisy",
    "orchestration",
    "planner",
    "planning",
    "semantic linter",
    "spurious",
    "steering",
    "tag",
    "tags",
    "workflow",
    "workflows",
)

PRE_CRASH_INCIDENT_CONTEXT_EXPANSION_TERMS = (
    "before",
    "crash",
    "crashed",
    "failure",
    "interrupted",
    "restart",
    "resume",
    "traceback",
    "stderr",
    "error",
    "last",
    "previous",
    "session",
    "context",
    "timeline",
)


@dataclass
class ExpansionResult:
    terms: list[str]
    reasons: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextualExpansionFamily:
    has_intent: Callable[[str, list[str]], bool]
    terms: Callable[[], list[str]]
    reason: str = "synonym_paraphrase"


def expand_query_terms(
    *,
    normalized: str,
    base_terms: list[str],
    artifact_hints: list[str],
    date_window_start: datetime | None,
    date_window_end: datetime | None,
    date_reason: str | None,
) -> ExpansionResult:
    reasons: dict[str, set[str]] = {}

    def add(term: str, reason: str) -> None:
        cleaned = " ".join(term.lower().split())
        if len(cleaned) <= 1:
            return
        reasons.setdefault(cleaned, set()).add(reason)

    for phrase in exact_phrase_terms(normalized):
        add(phrase, "exact_phrase")

    for term in base_terms:
        add(term, "normalized_terms")

    for slug in task_slug_variants(normalized):
        add(slug, "task_slug")
    for hint in artifact_hints:
        for term in artifact_variants(hint):
            add(term, "artifact_path")
        for slug in task_slug_variants(hint):
            add(slug, "task_slug")

    expansion_seeds = list(base_terms)
    for term in expansion_seeds:
        for synonym in SYNONYMS.get(term, ()):
            add(synonym, "synonym_paraphrase")
        for semantic_term in SEMANTIC_LOCAL.get(term, ()):
            add(semantic_term, "semantic_local")
        if term in PREFERENCE_WORDS:
            for preference_term in sorted(PREFERENCE_WORDS):
                add(preference_term, "preference")

    apply_contextual_expansion_families(normalized, base_terms, add)

    for term in date_window_terms(date_window_start, date_window_end, date_reason):
        add(term, "date_window")

    bounded = bounded_reasons(reasons)
    return ExpansionResult(
        terms=sorted(bounded),
        reasons={term: sorted(term_reasons) for term, term_reasons in sorted(bounded.items())},
    )


def has_worktree_cleanup_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    worktree_intent = bool(
        tokens & {"worktree", "worktrees", "checkout", "checkouts"}
        or base & {"worktree", "worktrees", "checkout", "checkouts", "worker-tree", "worker_tree"}
        or re.search(r"\bworker[\s_-]+tree\b", normalized)
    )
    cleanup_intent = bool(tokens & {"clean", "cleaned", "cleanup", "remove", "removed", "prune", "pruned"})
    return worktree_intent and cleanup_intent


def worktree_cleanup_terms() -> list[str]:
    return sorted(
        {
            *WORKTREE_CLEANUP_STATUS_TERMS,
            *WORKTREE_CLEANUP_WORKTREE_TERMS,
            *WORKTREE_CLEANUP_ACTION_TERMS,
        }
    )


def has_retrieval_miss_failure_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    context_terms = set(RETRIEVAL_MISS_CONTEXT_TERMS)
    failure_terms = set(RETRIEVAL_MISS_FAILURE_TERMS)
    context_intent = bool(
        tokens & context_terms
        or base & context_terms
        or re.search(r"\bno[\s_-]+evidence\b", normalized)
    )
    failure_intent = bool(
        tokens & failure_terms
        or base & failure_terms
        or re.search(r"\bnot[\s_-]+found\b", normalized)
    )
    return context_intent and failure_intent


def retrieval_miss_failure_terms() -> list[str]:
    return sorted(RETRIEVAL_MISS_EXPANSION_TERMS)


def has_source_selection_failure_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    source_selection_context = bool(
        re.search(r"\bsource[\s_-]+selection\b", normalized)
        or ("source" in token_context and token_context & {"selection", "selected", "selecting"})
    )
    failure_context = bool(
        token_context & {"fail", "failed", "failure", "failing"}
        or re.search(r"\bwhy\s+did\s+source[\s_-]+selection\s+fail\b", normalized)
    )
    question_context = bool(
        re.search(r"\bwhy\s+did\b", normalized)
        or re.search(r"\bwhat\s+(?:caused|made)\b", normalized)
        or "failure" in token_context
        or "fail" in token_context
    )

    return source_selection_context and failure_context and question_context


def source_selection_failure_terms() -> list[str]:
    return sorted(SOURCE_SELECTION_FAILURE_EXPANSION_TERMS)


def has_action_proof_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    if re.search(r"\b(?:did\s+it\s+)?actually\s+do\s+it\b", normalized):
        return True

    agent_context = bool(token_context & set(ACTION_PROOF_AGENT_TERMS))
    action_context = bool(token_context & set(ACTION_PROOF_ACTION_TERMS))
    execution_context = bool(token_context & set(ACTION_PROOF_EXECUTION_TERMS))
    command_execution_context = bool(
        "command" in token_context
        and (
            token_context & {"execute", "executed", "execution", "ran", "run"}
            or re.search(r"\bcommand\s+(?:was\s+)?(?:executed|run)\b", normalized)
        )
    )
    run_execution_context = bool(
        "run" in token_context
        and (
            agent_context
            or re.search(r"\bwhat\s+did\s+(?:the\s+)?agent\s+run\b", normalized)
        )
    )

    return (
        (agent_context and execution_context)
        or (action_context and execution_context)
        or command_execution_context
        or run_execution_context
    )


def action_proof_terms() -> list[str]:
    return sorted(ACTION_PROOF_EXPANSION_TERMS)


def has_change_summary_improvement_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    if re.search(r"\bwhat\s+did\s+we\s+improve\b", normalized):
        return True

    improvement_intent = bool(token_context & {"improve", "improved", "improvement", "improvements"})
    add_or_change_action_intent = bool(token_context & set(CHANGE_SUMMARY_ADD_OR_CHANGE_ACTION_TERMS))
    tool_work_context = bool(token_context & set(CHANGE_SUMMARY_TOOL_CONTEXT_TERMS))
    summary_question = bool(
        re.search(r"\bwhat\s+changed\b", normalized)
        or re.search(r"\bwhat\s+improvements?\b", normalized)
        or re.search(r"\bwhat\s+(?:was|were)\s+(?:added|implemented|fixed|improved)\b", normalized)
    )

    if summary_question and (improvement_intent or tool_work_context):
        return True
    if improvement_intent and add_or_change_action_intent:
        return True
    return improvement_intent and tool_work_context


def change_summary_improvement_terms() -> list[str]:
    return sorted(CHANGE_SUMMARY_EXPANSION_TERMS)


def has_stale_reference_state_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    stale_status_intent = bool(token_context & set(STALE_REFERENCE_STATE_STATUS_TERMS))
    reference_intent = bool(
        token_context & set(STALE_REFERENCE_STATE_REFERENCE_TERMS)
        or re.search(r"\breferenc(?:e|ed|ing)\b", normalized)
    )
    state_context = bool(
        token_context & {"active", "handoff", "pointer", "queue", "state"}
        or re.search(r"\bactive[\s_-]+state\b", normalized)
    )
    question_or_contrast = bool(
        re.search(r"\b(?:what|which)\s+(?:was|were|is|are)\b", normalized)
        or re.search(r"\bstill\s+(?:mentioned|referenced)\b", normalized)
        or re.search(r"\b(?:stale|old|obsolete|superseded)\s+but\s+(?:still\s+)?(?:mentioned|referenced)\b", normalized)
    )

    return stale_status_intent and reference_intent and (state_context or question_or_contrast)


def stale_reference_state_terms() -> list[str]:
    return sorted(
        {
            *STALE_REFERENCE_STATE_STATUS_TERMS,
            *STALE_REFERENCE_STATE_REFERENCE_TERMS,
            *STALE_REFERENCE_STATE_CONTEXT_TERMS,
        }
    )


def has_next_work_handoff_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    next_task_question = bool(
        "next" in token_context
        and (
            re.search(r"\bwhat\s+(?:is|are|was|were)\s+(?:the\s+)?next\s+(?:task|work|step|action)\b", normalized)
            or re.search(r"\bwhich\s+(?:task|work|step|action)\s+(?:is|are|was|were)\s+next\b", normalized)
        )
    )
    after_this_question = bool(
        re.search(r"\bwhat\s+(?:should\s+)?happen(?:s|ed)?\s+after\s+this\b", normalized)
        or re.search(r"\bwhat\s+(?:should\s+)?come(?:s)?\s+after\s+this\b", normalized)
    )
    what_next_question = bool(
        "next" in token_context
        and re.search(r"\bwhat\s+(?:should\s+)?(?:we|i|you)\s+do\s+next\b", normalized)
    )

    return next_task_question or after_this_question or what_next_question


def next_work_handoff_terms() -> list[str]:
    return sorted(NEXT_WORK_HANDOFF_EXPANSION_TERMS)


def has_next_codex_session_context_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    next_session_context = bool(
        re.search(r"\bnext[\s_-]+(?:codex[\s_-]+)?session\b", normalized)
        or re.search(r"\bfuture[\s_-]+(?:codex[\s_-]+)?session\b", normalized)
        or ({"codex", "session"} <= tokens and "next" in tokens)
    )
    codex_continuation_context = bool(
        "codex" in tokens
        and (
            tokens & {"continue", "continued", "continues", "continuing", "continuation", "resume", "resuming"}
            or re.search(r"\bbefore\s+continu(?:e|ing)\b", normalized)
        )
    )
    session_continuation_context = bool(
        tokens & {"session", "sessions"}
        and tokens & {"continue", "continued", "continues", "continuing", "continuation", "resume", "resuming"}
    )
    knowledge_context = bool(
        token_context & set(NEXT_CODEX_SESSION_CONTEXT_KNOW_TERMS)
        or re.search(r"\bshould\s+.*\bknow\b", normalized)
        or re.search(r"\bneed(?:s|ed)?\s+.*\bcontext\b", normalized)
        or re.search(r"\bkeep\s+in\s+mind\b", normalized)
    )
    question_or_continuation_context = bool(
        re.search(r"\b(?:what|which|where|when|how)\b", normalized)
        or re.search(r"\bbefore\s+continu(?:e|ing)\b", normalized)
        or tokens & {"continue", "continued", "continues", "continuing", "continuation", "resume", "resuming"}
    )

    return (
        (next_session_context or codex_continuation_context or session_continuation_context)
        and knowledge_context
        and question_or_continuation_context
    )


def next_codex_session_context_terms() -> list[str]:
    return sorted(set(NEXT_CODEX_SESSION_CONTEXT_EXPANSION_TERMS))


def has_claim_boundary_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    overclaim_intent = bool(
        token_context & {"overclaim", "overclaimed", "overclaims", "overclaiming"}
        or re.search(r"\bover[\s_-]*claim(?:ed|s|ing)?\b", normalized)
    )
    if overclaim_intent:
        return True

    claim_intent = bool(
        token_context & {"claim", "claimed", "claims", "claiming"}
        or re.search(r"\bclaim[\s_-]+boundar(?:y|ies)\b", normalized)
    )
    boundary_intent = bool(
        token_context & {"boundary", "boundaries", "evidence", "reviewed", "unsupported"}
        or re.search(r"\bnot[\s_-]+allowed\b", normalized)
        or re.search(r"\bwithout\s+(?:separate\s+)?reviewed\s+evidence\b", normalized)
        or re.search(r"\bnot\s+(?:backed|supported|allowed)\b", normalized)
    )
    question_context = bool(
        re.search(r"\b(?:what|which)\s+(?:claim|claims)\b", normalized)
        or re.search(r"\b(?:what|which)\s+(?:was|were|is|are)\s+.*\bclaim", normalized)
    )

    return claim_intent and boundary_intent and (question_context or "boundary" in token_context or "boundaries" in token_context)


def claim_boundary_terms() -> list[str]:
    return sorted({*CLAIM_BOUNDARY_CLAIM_TERMS, *CLAIM_BOUNDARY_QUALITY_TERMS})


def has_promise_vs_committed_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    promise_context = bool(
        token_context & set(PROMISE_VS_COMMITTED_PROMISE_TERMS)
        or re.search(r"\b(?:what\s+)?(?:did|was)\s+.*\b(?:promise|promised|claim|claimed|say|said|planned)\b", normalized)
    )
    delivery_context = bool(
        token_context & set(PROMISE_VS_COMMITTED_DELIVERY_TERMS)
        or re.search(r"\bgot[\s_-]+committed\b", normalized)
        or re.search(r"\bactual[\s_-]+(?:diff|change|changes|commit|commits|implementation|patch)\b", normalized)
    )
    contrast_context = bool(
        re.search(r"\bversus\b|\bvs\.?\b", normalized)
        or re.search(r"\bcompared\s+(?:with|to)\b", normalized)
        or re.search(r"\binstead\s+of\b", normalized)
        or re.search(r"\bgap\s+between\b", normalized)
        or re.search(r"\bgot[\s_-]+committed\b", normalized)
        or re.search(r"\bactual[\s_-]+(?:diff|change|changes|commit|commits|implementation|patch)\b", normalized)
    )

    return promise_context and delivery_context and contrast_context


def promise_vs_committed_terms() -> list[str]:
    return sorted(PROMISE_VS_COMMITTED_EXPANSION_TERMS)


def has_process_detour_lesson_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    waste_time_question = bool(
        re.search(
            r"\bwhy\s+(?:did|was|were)\s+(?:it|this|that)\s+"
            r"(?:waste|wasted|wasting)\s+(?:time|hours?)\b",
            normalized,
        )
    )
    bad_retrospective_question = bool(re.fullmatch(r"why\s+(?:was|is)\s+(?:it|this|that)\s+bad", normalized))
    bare_lesson_question = bool(
        re.fullmatch(r"what\s+did\s+(?:we|i|you)\s+learn", normalized)
        or re.fullmatch(r"what\s+was\s+the\s+lesson", normalized)
    )
    process_detour_context = bool(
        token_context & {"detour", "process", "queue", "reseeding"}
        or re.search(r"\bprocess[\s_-]+only\b", normalized)
    )
    question_context = bool(
        re.search(r"\b(?:what|why)\b", normalized)
        and token_context & {"bad", "learn", "lesson", "waste", "wasted"}
    )

    return waste_time_question or bad_retrospective_question or bare_lesson_question or (
        process_detour_context and question_context
    )


def process_detour_lesson_terms() -> list[str]:
    return sorted(PROCESS_DETOUR_LESSON_EXPANSION_TERMS)


def has_handoff_document_location_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    document_context = bool(
        token_context & set(HANDOFF_DOCUMENT_LOCATION_DOCUMENT_TERMS)
        or re.search(r"\bnext[\s_-]+session\b", normalized)
    )
    location_context = bool(
        token_context & set(HANDOFF_DOCUMENT_LOCATION_CONTEXT_TERMS)
        or re.search(r"\bwhere\s+(?:did|is|was|were)\b", normalized)
        or re.search(r"\b(?:main|parent)[\s_-]+checkout\b", normalized)
        or re.search(r"\btask[\s_-]+branch\b", normalized)
    )

    return document_context and location_context


def handoff_document_location_terms() -> list[str]:
    return sorted(HANDOFF_DOCUMENT_LOCATION_EXPANSION_TERMS)


def has_file_session_provenance_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    target_context = bool(
        token_context & set(FILE_SESSION_PROVENANCE_TARGET_TERMS)
        or re.search(r"\b[\w.-]+/[\w./-]+\b", normalized)
        or re.search(r"\b[\w.-]+\.[a-z0-9]{1,8}\b", normalized)
    )
    actor_context = bool(token_context & set(FILE_SESSION_PROVENANCE_ACTOR_TERMS))
    creation_context = bool(
        token_context & set(FILE_SESSION_PROVENANCE_CREATION_TERMS)
        or re.search(r"\b(?:creat|generat|sav|writ)(?:e|ed|es|ing)?\b", normalized)
    )
    question_context = bool(
        re.search(r"\b(?:which|what)\s+(?:session|agent|assistant|transcript)\b", normalized)
        or re.search(r"\bwho\b", normalized)
        or re.search(r"\bprovenance\b", normalized)
        or re.search(r"\bresponsib(?:le|ility)\b", normalized)
    )

    return target_context and creation_context and (actor_context or re.search(r"\bwho\b", normalized)) and question_context


def file_session_provenance_terms() -> list[str]:
    return sorted(FILE_SESSION_PROVENANCE_EXPANSION_TERMS)


def has_pre_change_file_path_mention_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    conversation_context = bool(token_context & set(PRE_CHANGE_FILE_PATH_MENTION_CONVERSATION_TERMS))
    reference_context = bool(
        token_context & set(PRE_CHANGE_FILE_PATH_MENTION_REFERENCE_TERMS)
        or re.search(r"\bmention(?:ed|s|ing)?\b", normalized)
        or re.search(r"\breferenc(?:e|ed|es|ing)\b", normalized)
    )
    target_context = bool(
        token_context & set(PRE_CHANGE_FILE_PATH_MENTION_TARGET_TERMS)
        or re.search(r"\bfile[\s_-]+paths?\b", normalized)
        or re.search(r"\b[\w.-]+/[\w./-]+\b", normalized)
        or re.search(r"\b[\w.-]+\.[a-z0-9]{1,8}\b", normalized)
    )
    temporal_context = bool(
        token_context & set(PRE_CHANGE_FILE_PATH_MENTION_TEMPORAL_TERMS)
        or re.search(r"\bearlier\s+than\b", normalized)
    )
    change_context = bool(
        token_context & set(PRE_CHANGE_FILE_PATH_MENTION_CHANGE_TERMS)
        or re.search(r"\bchang(?:e|ed|es|ing)\b", normalized)
        or re.search(r"\bmodif(?:y|ied|ies|ying)\b", normalized)
        or re.search(r"\bedit(?:ed|s|ing)?\b", normalized)
        or re.search(r"\bpatch(?:ed|es|ing)?\b", normalized)
        or re.search(r"\bgit[\s_-]+diff\b", normalized)
    )

    return conversation_context and reference_context and target_context and temporal_context and change_context


def pre_change_file_path_mention_terms() -> list[str]:
    return sorted(PRE_CHANGE_FILE_PATH_MENTION_EXPANSION_TERMS)


def has_commit_user_request_provenance_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    change_context = bool(
        token_context & set(COMMIT_USER_REQUEST_PROVENANCE_CHANGE_TERMS)
        or re.search(r"\bcode[\s_-]+chang(?:e|ed|es|ing)\b", normalized)
    )
    request_context = bool(
        token_context & set(COMMIT_USER_REQUEST_PROVENANCE_REQUEST_TERMS)
        or re.search(r"\buser[\s_-]+requests?\b", normalized)
    )
    conversation_context = bool(token_context & set(COMMIT_USER_REQUEST_PROVENANCE_CONVERSATION_TERMS))
    causality_context = bool(
        token_context & set(COMMIT_USER_REQUEST_PROVENANCE_CAUSALITY_TERMS)
        or re.search(r"\bwhy\s+did\s+(?:this\s+)?code\s+chang(?:e|ed|es|ing)?\b", normalized)
        or re.search(r"\bwhy\s+did\s+(?:this\s+)?(?:commit|diff|patch)\s+happen\b", normalized)
    )

    return change_context and (request_context or conversation_context or causality_context)


def commit_user_request_provenance_terms() -> list[str]:
    return sorted(COMMIT_USER_REQUEST_PROVENANCE_EXPANSION_TERMS)


def has_revert_fix_feedback_source_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    change_context = bool(
        token_context & set(REVERT_FIX_FEEDBACK_SOURCE_CHANGE_TERMS)
        or re.search(r"\broll(?:ed)?[\s_-]+back\b", normalized)
    )
    user_context = bool(
        token_context & set(REVERT_FIX_FEEDBACK_SOURCE_USER_TERMS)
        or re.search(r"\buser[\s_-]+(?:feedback|requests?)\b", normalized)
    )
    agent_context = bool(
        token_context & set(REVERT_FIX_FEEDBACK_SOURCE_AGENT_TERMS)
        or re.search(r"\bself[\s_-]+review(?:ed|ing)?\b", normalized)
        or re.search(r"\bagent[\s_-]+review\b", normalized)
    )
    provenance_context = bool(
        token_context & set(REVERT_FIX_FEEDBACK_SOURCE_PROVENANCE_TERMS)
        or re.search(r"\bbased\s+on\b", normalized)
    )
    commit_context = bool(token_context & set(REVERT_FIX_FEEDBACK_SOURCE_COMMIT_TERMS))
    question_context = bool(
        re.search(r"\b(?:was|were|is|are|did|does|why|what)\b", normalized)
        or provenance_context
    )

    source_side_context = user_context or agent_context
    comparison_context = user_context and agent_context
    return change_context and source_side_context and question_context and (
        provenance_context or commit_context or comparison_context
    )


def revert_fix_feedback_source_terms() -> list[str]:
    return sorted(REVERT_FIX_FEEDBACK_SOURCE_EXPANSION_TERMS)


def has_branch_merge_surrounding_conversation_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    branch_context = bool(
        token_context & {"branch", "branches"}
        or re.search(r"\btask[\s_-]+branch(?:es)?\b", normalized)
    )
    merge_commit_context = bool(re.search(r"\bmerge[\s_-]+commit\b", normalized))
    merge_context = bool(
        token_context & {"merge", "merged", "merges", "merging"}
        or merge_commit_context
        or re.search(r"\bpost[\s_-]+merge\b", normalized)
    )
    surrounding_context = bool(
        token_context & {"surrounding", "around", "nearby", "adjacent"}
        or re.search(r"\bbefore\s+and\s+after\b", normalized)
        or {"before", "after"} <= token_context
    )
    conversation_context = bool(
        token_context & {"chat", "chats", "context", "conversation", "conversations", "session", "sessions"}
    )

    return ((branch_context and merge_context) or merge_commit_context) and surrounding_context and conversation_context


def branch_merge_surrounding_conversation_terms() -> list[str]:
    return sorted(BRANCH_MERGE_SURROUNDING_CONVERSATION_EXPANSION_TERMS)


def has_final_decision_after_deliberation_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    finality_context = bool(
        token_context & set(FINAL_DECISION_AFTER_DELIBERATION_FINALITY_TERMS)
        or re.search(r"\b(?:final|last|settled|ultimate)\s+(?:decision|resolution|conclusion|outcome)s?\b", normalized)
    )
    decision_context = bool(
        token_context & set(FINAL_DECISION_AFTER_DELIBERATION_DECISION_TERMS)
        or re.search(r"\b(?:decision|resolution|conclusion|outcome)s?\b", normalized)
    )
    discussion_context = bool(
        token_context & set(FINAL_DECISION_AFTER_DELIBERATION_DISCUSSION_TERMS)
        or re.search(r"\bback[\s_-]+and[\s_-]+forth\b", normalized)
        or re.search(r"\bdebate(?:d|s)?\b", normalized)
        or re.search(r"\bdiscuss(?:ed|ion|ions)?\b", normalized)
        or re.search(r"\biterat(?:e|ed|ion|ions)\b", normalized)
    )
    conversation_context = bool(token_context & set(FINAL_DECISION_AFTER_DELIBERATION_CONVERSATION_TERMS))
    question_or_memory_context = bool(
        re.search(r"\b(?:what|which|where|when|how)\b", normalized)
        or conversation_context
    )

    return finality_context and decision_context and discussion_context and question_or_memory_context


def final_decision_after_deliberation_terms() -> list[str]:
    return sorted(FINAL_DECISION_AFTER_DELIBERATION_EXPANSION_TERMS)


def has_final_answer_source_hit_quality_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    answer_context = bool(token_context & {"answer", "answers"})
    final_answer_context = bool(
        re.search(r"\bfinal[\s_-]+answers?\b", normalized)
    )
    source_hit_context = bool(
        re.search(r"\bsource[\s_-]+hits?\b", normalized)
        or ("source" in token_context and token_context & {"hit", "hits"})
    )
    evidence_preservation_context = bool(
        "evidence" in token_context
        and (
            token_context & {"preserve", "preserved", "preservation"}
            or re.search(r"\bpreserv(?:e|ed|ation)\b", normalized)
        )
    )
    facet_or_omission_context = bool(token_context & {"facet", "facets", "omit", "omits", "omitted"})
    quality_or_preservation_context = bool(
        token_context & set(FINAL_ANSWER_SOURCE_HIT_QUALITY_CONTEXT_TERMS)
        or re.search(r"\bnot\s+(?:good|correct|sufficient)\b", normalized)
        or re.search(r"\bwasn['’]?t\s+(?:good|correct|sufficient)\b", normalized)
        or re.search(r"\bdid(?:n['’]?t| not)\s+preserve\b", normalized)
    )

    return (
        ((final_answer_context or source_hit_context) and quality_or_preservation_context)
        or evidence_preservation_context
        or (answer_context and facet_or_omission_context)
    )


def final_answer_source_hit_quality_terms() -> list[str]:
    return sorted(FINAL_ANSWER_SOURCE_HIT_EXPANSION_TERMS)


def has_task_completion_state_mismatch_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    task_delivery_context = bool(token_context & set(TASK_COMPLETION_STATE_TASK_CONTEXT_TERMS))
    completion_context = bool(token_context & set(TASK_COMPLETION_STATE_COMPLETION_TERMS))
    state_context = bool(token_context & set(TASK_COMPLETION_STATE_STATE_TERMS))
    mismatch_or_question_context = bool(
        token_context & {"already", "still", "mismatch", "mismatched"}
        or re.search(r"\b(?:why|which)\b", normalized)
        or re.search(r"\blooks?\s+complete\b", normalized)
        or re.search(r"\bif\s+it\s+looks?\s+complete\b", normalized)
        or re.search(r"\bdespite\s+(?:being\s+)?(?:done|complete|completed|reviewed|verified|merged)\b", normalized)
    )

    return task_delivery_context and completion_context and (state_context or mismatch_or_question_context)


def task_completion_state_mismatch_terms() -> list[str]:
    return sorted(TASK_COMPLETION_STATE_EXPANSION_TERMS)


def has_review_verdict_status_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    review_context = bool(
        token_context & set(REVIEW_VERDICT_STATUS_REVIEW_TERMS)
        or re.search(r"\breview[\s_-]+artifact\b", normalized)
    )
    gate_context = bool(
        token_context & set(REVIEW_VERDICT_STATUS_GATE_TERMS) == set(REVIEW_VERDICT_STATUS_GATE_TERMS)
        or re.search(r"\bmerge[\s_-]+gate\b", normalized)
    )
    outcome_context = bool(
        token_context & set(REVIEW_VERDICT_STATUS_OUTCOME_TERMS)
        or re.search(r"\bpartial(?:ly)?[\s_-]+verified\b", normalized)
    )
    question_or_verdict_context = bool(
        re.search(r"\b(?:was|were|is|are|did|does|has|had)\b", normalized)
        or re.search(r"\bmark(?:ed)?\b", normalized)
        or token_context & {"status", "verdict"}
        or gate_context
    )

    return (review_context or gate_context) and outcome_context and question_or_verdict_context


def review_verdict_status_terms() -> list[str]:
    return sorted(REVIEW_VERDICT_STATUS_EXPANSION_TERMS)


def has_intervention_effect_noise_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    intervention_context = bool(
        token_context & set(INTERVENTION_EFFECT_CONTEXT_TERMS)
        or re.search(r"\bsteer(?:ing|ed|s)?\b", normalized)
        or re.search(r"\bsuppress(?:ion|ed|es|ing)?\b", normalized)
    )
    outcome_context = bool(
        token_context & set(INTERVENTION_EFFECT_OUTCOME_TERMS)
        or re.search(r"\bfalse[\s_-]+positives?\b", normalized)
        or re.search(r"\bbefore[\s_-]+after\b", normalized)
        or re.search(r"\bside[\s_-]+effects?\b", normalized)
        or re.search(r"\bbehaviou?r\b", normalized)
        or re.search(r"\bchang(?:e|ed|es|ing)\b", normalized)
        or re.search(r"\bhelp(?:ed|s|ing)?\b", normalized)
        or re.search(r"\bignor(?:e|ed|es|ing)\b", normalized)
    )

    return intervention_context and outcome_context


def intervention_effect_noise_terms() -> list[str]:
    return sorted(INTERVENTION_EFFECT_EXPANSION_TERMS)


def has_suppression_hidden_failure_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    suppression_context = bool(
        token_context & set(SUPPRESSION_HIDDEN_FAILURE_SUPPRESSION_TERMS)
        or re.search(r"\bsuppress(?:ion|ions|ed|es|ing)?\b", normalized)
        or re.search(r"\bmask(?:ed|ing|s)?\b", normalized)
    )
    hidden_context = bool(
        token_context & set(SUPPRESSION_HIDDEN_FAILURE_HIDDEN_TERMS)
        or re.search(r"\b(?:cover|covered)[\s_-]+up\b", normalized)
        or re.search(r"\bhid(?:e|den|ing)\b", normalized)
        or re.search(r"\bmiss(?:ed|es|ing)?\b", normalized)
    )
    true_failure_context = bool(
        token_context & {"false-negative", "true-positive"}
        or re.search(r"\bfalse[\s_-]+negatives?\b", normalized)
        or re.search(r"\btrue[\s_-]+positives?\b", normalized)
        or re.search(r"\b(?:real|true|actual|legitimate)[\s_-]+fail(?:ure|ures|ed|s)?\b", normalized)
        or (
            re.search(r"\bfail(?:ure|ures|ed|s)?\b", normalized)
            and token_context & {"real", "true", "actual", "legitimate"}
        )
    )

    return suppression_context and hidden_context and true_failure_context


def suppression_hidden_failure_terms() -> list[str]:
    return sorted(
        {
            *SUPPRESSION_HIDDEN_FAILURE_SUPPRESSION_TERMS,
            *SUPPRESSION_HIDDEN_FAILURE_HIDDEN_TERMS,
            *SUPPRESSION_HIDDEN_FAILURE_TRUE_FAILURE_TERMS,
            *SUPPRESSION_HIDDEN_FAILURE_EVIDENCE_TERMS,
            *SUPPRESSION_HIDDEN_FAILURE_LINTER_TERMS,
            *SUPPRESSION_HIDDEN_FAILURE_EXPANSION_TERMS,
        }
    )


def has_linter_phase_inappropriate_fire_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    linter_context = bool(
        token_context & set(LINTER_PHASE_INAPPROPRIATE_FIRE_LINTER_TERMS)
        or re.search(r"\bsemantic[\s_-]+linter\b", normalized)
        or re.search(r"\bcall[\s_-]?outs?\b", normalized)
    )
    fired_context = bool(
        token_context & set(LINTER_PHASE_INAPPROPRIATE_FIRE_FIRED_TERMS)
        or re.search(r"\btrigger(?:ed|ing|s)?\b", normalized)
        or re.search(r"\bfir(?:e|ed|ing|es)\b", normalized)
        or re.search(r"\bflag(?:ged|ging|s)?\b", normalized)
    )
    phase_context = bool(
        token_context & set(LINTER_PHASE_INAPPROPRIATE_FIRE_PHASE_TERMS)
        or re.search(r"\bset[\s_-]+up\b", normalized)
        or re.search(r"\bcoordinat(?:or|ion|ing)\b", normalized)
        or re.search(r"\bplann(?:er|ing)\b", normalized)
    )
    judgment_or_noise_context = bool(
        token_context
        & (
            set(LINTER_PHASE_INAPPROPRIATE_FIRE_JUDGMENT_TERMS)
            | set(LINTER_PHASE_INAPPROPRIATE_FIRE_NOISE_TERMS)
        )
        or re.search(r"\bshould(?:\s+not|n't)\b", normalized)
        or re.search(r"\bnot[\s_-]+appropriate\b", normalized)
        or re.search(r"\bfalse[\s_-]+positives?\b", normalized)
    )

    return linter_context and fired_context and phase_context and judgment_or_noise_context


def linter_phase_inappropriate_fire_terms() -> list[str]:
    return sorted(
        {
            *LINTER_PHASE_INAPPROPRIATE_FIRE_LINTER_TERMS,
            *LINTER_PHASE_INAPPROPRIATE_FIRE_FIRED_TERMS,
            *LINTER_PHASE_INAPPROPRIATE_FIRE_PHASE_TERMS,
            *LINTER_PHASE_INAPPROPRIATE_FIRE_JUDGMENT_TERMS,
            *LINTER_PHASE_INAPPROPRIATE_FIRE_NOISE_TERMS,
            *LINTER_PHASE_INAPPROPRIATE_FIRE_EXPANSION_TERMS,
        }
    )


def has_coordinator_workflow_noisy_label_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    label_context = bool(
        token_context & set(COORDINATOR_WORKFLOW_NOISY_LABEL_LABEL_TERMS)
        or re.search(r"\bclassif(?:y|ied|ication|ications)\b", normalized)
        or re.search(r"\blabel(?:ed|s)?\b", normalized)
        or re.search(r"\btag(?:ged|s)?\b", normalized)
    )
    noise_context = bool(
        token_context & set(COORDINATOR_WORKFLOW_NOISY_LABEL_NOISE_TERMS)
        or re.search(r"\bfalse[\s_-]+positives?\b", normalized)
        or re.search(r"\bspurious\b", normalized)
        or re.search(r"\bnois(?:e|y)\b", normalized)
    )
    workflow_context = bool(
        token_context & set(COORDINATOR_WORKFLOW_NOISY_LABEL_WORKFLOW_TERMS)
        or re.search(r"\bcoordinat(?:or|ion|ing)\b", normalized)
        or re.search(r"\borchestrat(?:e|ed|es|ing|ion)\b", normalized)
        or re.search(r"\bplann(?:er|ing)\b", normalized)
    )
    question_context = bool(
        re.search(r"\b(?:which|what)\s+(?:label|labels|classification|classifications|tag|tags)\b", normalized)
        or re.search(r"\b(?:which|what)\b.*\b(?:noisy|noise|false[\s_-]+positive|spurious)\b", normalized)
        or re.search(r"\b(?:noisy|spurious|false[\s_-]+positive)\b.*\b(?:label|labels|classification|tag|tags)\b", normalized)
    )

    return label_context and noise_context and workflow_context and question_context


def coordinator_workflow_noisy_label_terms() -> list[str]:
    return sorted(
        {
            *COORDINATOR_WORKFLOW_NOISY_LABEL_LABEL_TERMS,
            *COORDINATOR_WORKFLOW_NOISY_LABEL_NOISE_TERMS,
            *COORDINATOR_WORKFLOW_NOISY_LABEL_WORKFLOW_TERMS,
            *COORDINATOR_WORKFLOW_NOISY_LABEL_LINTER_TERMS,
            *COORDINATOR_WORKFLOW_NOISY_LABEL_EXPANSION_TERMS,
        }
    )


def has_pre_crash_incident_context_intent(normalized: str, base_terms: list[str]) -> bool:
    tokens = set(SEPARATED_WORD_RE.findall(normalized))
    base = {term.lower() for term in base_terms}
    token_context = tokens | base

    temporal_context = bool(
        token_context & {"before", "last", "previous"}
        or re.search(r"\bpre[\s_-]+(?:crash|failure|interruption|restart)\b", normalized)
    )
    incident_context = bool(
        token_context
        & {
            "crash",
            "crashed",
            "failure",
            "interrupted",
            "restart",
            "resume",
            "traceback",
            "stderr",
            "error",
        }
        or re.search(r"\bcrash(?:ed|es|ing)?\b", normalized)
        or re.search(r"\brestart(?:ed|s|ing)?\b", normalized)
        or re.search(r"\binterruption\b|\binterrupt(?:ed|s|ing)?\b", normalized)
        or re.search(r"\bresume(?:d|s|ing)?\b", normalized)
    )
    question_or_context = bool(
        token_context & {"context", "timeline"}
        or re.search(r"\bwhat\s+happened\b", normalized)
        or re.search(r"\bwhat\s+(?:was|were)\s+(?:the\s+)?context\b", normalized)
    )

    return temporal_context and incident_context and question_or_context


def pre_crash_incident_context_terms() -> list[str]:
    return sorted(PRE_CRASH_INCIDENT_CONTEXT_EXPANSION_TERMS)


CONTEXTUAL_EXPANSION_FAMILIES: tuple[ContextualExpansionFamily, ...] = (
    ContextualExpansionFamily(has_worktree_cleanup_intent, worktree_cleanup_terms),
    ContextualExpansionFamily(has_retrieval_miss_failure_intent, retrieval_miss_failure_terms),
    ContextualExpansionFamily(has_source_selection_failure_intent, source_selection_failure_terms),
    ContextualExpansionFamily(has_action_proof_intent, action_proof_terms),
    ContextualExpansionFamily(has_change_summary_improvement_intent, change_summary_improvement_terms),
    ContextualExpansionFamily(has_stale_reference_state_intent, stale_reference_state_terms),
    ContextualExpansionFamily(has_next_work_handoff_intent, next_work_handoff_terms),
    ContextualExpansionFamily(has_next_codex_session_context_intent, next_codex_session_context_terms),
    ContextualExpansionFamily(has_claim_boundary_intent, claim_boundary_terms),
    ContextualExpansionFamily(has_promise_vs_committed_intent, promise_vs_committed_terms),
    ContextualExpansionFamily(has_process_detour_lesson_intent, process_detour_lesson_terms),
    ContextualExpansionFamily(has_handoff_document_location_intent, handoff_document_location_terms),
    ContextualExpansionFamily(has_file_session_provenance_intent, file_session_provenance_terms),
    ContextualExpansionFamily(
        has_pre_change_file_path_mention_intent,
        pre_change_file_path_mention_terms,
    ),
    ContextualExpansionFamily(
        has_commit_user_request_provenance_intent,
        commit_user_request_provenance_terms,
    ),
    ContextualExpansionFamily(
        has_revert_fix_feedback_source_intent,
        revert_fix_feedback_source_terms,
    ),
    ContextualExpansionFamily(
        has_branch_merge_surrounding_conversation_intent,
        branch_merge_surrounding_conversation_terms,
    ),
    ContextualExpansionFamily(
        has_final_decision_after_deliberation_intent,
        final_decision_after_deliberation_terms,
    ),
    ContextualExpansionFamily(
        has_final_answer_source_hit_quality_intent,
        final_answer_source_hit_quality_terms,
    ),
    ContextualExpansionFamily(
        has_task_completion_state_mismatch_intent,
        task_completion_state_mismatch_terms,
    ),
    ContextualExpansionFamily(
        has_review_verdict_status_intent,
        review_verdict_status_terms,
    ),
    ContextualExpansionFamily(
        has_intervention_effect_noise_intent,
        intervention_effect_noise_terms,
    ),
    ContextualExpansionFamily(
        has_suppression_hidden_failure_intent,
        suppression_hidden_failure_terms,
    ),
    ContextualExpansionFamily(
        has_linter_phase_inappropriate_fire_intent,
        linter_phase_inappropriate_fire_terms,
    ),
    ContextualExpansionFamily(
        has_coordinator_workflow_noisy_label_intent,
        coordinator_workflow_noisy_label_terms,
    ),
    ContextualExpansionFamily(
        has_pre_crash_incident_context_intent,
        pre_crash_incident_context_terms,
    ),
)


def apply_contextual_expansion_families(
    normalized: str,
    base_terms: list[str],
    add: Callable[[str, str], None],
) -> None:
    for family in CONTEXTUAL_EXPANSION_FAMILIES:
        if family.has_intent(normalized, base_terms):
            for term in family.terms():
                add(term, family.reason)


def exact_phrase_terms(normalized: str) -> list[str]:
    phrases: list[str] = []
    if _eligible_exact_phrase(normalized):
        phrases.append(normalized)
    for quoted in re.findall(r'"([^"]+)"|\'([^\']+)\'', normalized):
        phrase = next((item for item in quoted if item), "")
        cleaned = " ".join(phrase.split())
        if _eligible_exact_phrase(cleaned):
            phrases.append(cleaned)
    return sorted(set(phrases))


def _eligible_exact_phrase(value: str) -> bool:
    if len(value) <= 2 or len(value) > MAX_EXACT_PHRASE_CHARS:
        return False
    tokens = SEPARATED_WORD_RE.findall(value)
    return 2 <= len(tokens) <= MAX_EXACT_PHRASE_TOKENS


def bounded_reasons(reasons: dict[str, set[str]]) -> dict[str, set[str]]:
    if len(reasons) <= MAX_EXPANDED_TERMS:
        return reasons
    ordered = sorted(reasons.items(), key=lambda item: (_reason_priority(item[1]), item[0]))
    return dict(ordered[:MAX_EXPANDED_TERMS])


def _reason_priority(term_reasons: set[str]) -> tuple[int, int]:
    priority_order = (
        "exact_phrase",
        "artifact_path",
        "task_slug",
        "preference",
        "date_window",
        "normalized_terms",
        "semantic_local",
        "synonym_paraphrase",
    )
    priority = min((priority_order.index(reason) for reason in term_reasons if reason in priority_order), default=len(priority_order))
    return priority, len(term_reasons)


def task_slug_variants(text: str) -> list[str]:
    variants: set[str] = set()
    for match in TASK_SLUG_RE.finditer(text.lower()):
        prefix, number, suffix = match.groups()
        canonical = f"{prefix}-{number}"
        variants.update(
            {
                canonical,
                canonical.replace("-", "_"),
                canonical.replace("-", ""),
                canonical.replace("-", " "),
                number,
                f"task {canonical}",
            }
        )
        if suffix:
            full = f"{canonical}-{suffix}"
            variants.update({full, full.replace("-", "_"), full.replace("-", ""), full.replace("-", " ")})
    return sorted(variants)


def artifact_variants(value: str) -> list[str]:
    normalized = value.lower().strip()
    if not normalized:
        return []
    path = PurePosixPath(normalized)
    name = path.name
    stem = path.stem
    variants = {normalized, name}
    if stem:
        variants.add(stem)
        variants.add(stem.replace("-", "_"))
        variants.add(stem.replace("_", "-"))
        split = " ".join(SEPARATED_WORD_RE.findall(stem))
        if split:
            variants.add(split)
            variants.update(part for part in split.split() if len(part) > 2)
    if path.suffix:
        variants.add(path.suffix.lstrip("."))
    return sorted(variants)


def date_window_terms(
    start: datetime | None,
    end: datetime | None,
    reason: str | None,
) -> list[str]:
    terms: set[str] = set()
    if reason:
        terms.add(reason)
    if not start or not end:
        return sorted(terms)

    start_day = start.date()
    end_day = end.date()
    if end.time() == datetime.min.time() and end_day > start_day:
        end_day -= timedelta(days=1)
    span = (end_day - start_day).days
    if span < 0:
        return sorted(terms)
    for offset in range(min(span, 13) + 1):
        day = start_day + timedelta(days=offset)
        iso = day.isoformat()
        terms.add(iso)
        terms.add(iso.replace("-", "/"))
        terms.add(f"{day.strftime('%b')} {day.day}".lower())
        terms.add(f"{day.strftime('%B')} {day.day}".lower())
    return sorted(terms)
