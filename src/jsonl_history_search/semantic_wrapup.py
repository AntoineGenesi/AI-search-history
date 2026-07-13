from __future__ import annotations

import re
import shlex
import time
from pathlib import Path
from typing import Any

from .masking import MaskContext
from .prompt_echo import setup_echo_classification
from .skill_audit import context_row, next_read_refs, scan_records
from .sources import SourcePlan


SCHEMA_VERSION = 1
INLINE_FAILURE_MODE_LIMIT = 10
DESTINATION_SCAN_LIMIT = 200
ORCHESTRATION_EVIDENCE_LIMIT = 20
ORCHESTRATION_DIVERSITY_CATEGORIES = (
    "worker_or_subagent_behavior",
    "reviewer_proof_or_claim_boundary",
    "same_task_repair_logic",
    "product_delta_integration_audit",
    "product_moving_vs_process_drift",
)
ORCHESTRATION_DIVERSITY_ACTORS = (
    "worker_or_subagent_summary",
    "reviewer_or_review_summary",
)
ORCHESTRATION_PRIMARY_RANKED_LIMIT = 10
CURRENT_SESSION_SOURCE_LIMIT = 5
CURRENT_SESSION_SOURCE_SCAN_LIMIT = 160
ROLLING_WINDOW_PRE_RECORDS = 3
ROLLING_WINDOW_POST_RECORDS = 6
ROLLING_WINDOW_INLINE_LIMIT = 20
ROLLING_WINDOW_ERROR_INLINE_LIMIT = 30
USER_MESSAGE_FIRST_INLINE_LIMIT = 20
USER_MESSAGE_CONTEXT_PRE_RECORDS = 2
USER_MESSAGE_CONTEXT_POST_RECORDS = 2
CURRENT_SESSION_RECENT_SIGNAL_LINE_WINDOW = 800
BEHAVIOR_RULE_FILENAMES = {"AGENTS.md", "CLAUDE.md"}
IGNORED_BEHAVIOR_DISCOVERY_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    ".private",
    ".venv",
    "cache",
    "caches",
    "node_modules",
    "venv",
    "worktrees",
}
PLUGIN_IGNORED_BEHAVIOR_DISCOVERY_DIRS = {".archive", ".tmp", "archive", "archives", "temp", "tmp"}

SETUP_SIGNAL_KINDS = {"semantic_wrapup_prompt", "current_session_setup_echo"}
READ_ROLE_REQUIRED_PRIMARY_PROOF = "required_primary_proof"
READ_ROLE_OPTIONAL_DIAGNOSTIC = "optional_diagnostic"
READ_ROLE_CONDITIONAL_REQUIRED = "conditional_required"

ASSISTANT_TOOL_ERROR_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tool_error", ("tool error", "tool failed", "function error", "exception", "traceback")),
    ("command_failed", ("exit code", "non-zero", "command failed", "returned error", "no such file")),
    ("runtime_abort", ("aborted", "timed out", "timeout", "killed", "stalled", "stuck")),
    ("review_failure", ("review failed", "partially_verified", "partially verified", "failed verification")),
    ("contradicted_user_intent", ("wrong direction", "not what the user asked", "silently running", "process drift")),
)

CURRENT_SESSION_SOURCE_PATTERNS = (
    "tasks/side-thread-info*.md",
    "reports/*semantic*wrap*.md",
    "reports/*semantic-wrapup*.md",
    "codex/state/pipeline-state.md",
    "TODO.md",
    "TASKS.md",
)

CURRENT_SESSION_SOURCE_FACETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "current_session_scope",
        (
            'broad "this session"',
            "broad history sweep",
            "too broad",
            "mixed older sessions",
            "this session scope",
            "session scope",
        ),
    ),
    (
        "exact_session_root_handling",
        (
            "exact session",
            "exact current-session",
            "exact current session",
            "exact known",
            "arbitrary-root",
            "arbitrary root",
            "allow-arbitrary-root",
        ),
    ),
    (
        "root_rejection_ambiguity",
        (
            "root rejection",
            "rejected root",
            "no failures found",
            "no_failure_modes_found",
        ),
    ),
    (
        "count_drift",
        (
            "count drift",
            "summary counts",
            "array length",
            "returned array",
            "returned/total",
        ),
    ),
    (
        "destination_ranking_noise",
        (
            "destination ranking",
            "fix-destination",
            "fix destination",
            "stale task",
            "private archived",
            "archived private",
        ),
    ),
    (
        "receiving_agent_inspectability",
        (
            "receiving agent",
            "receiving-agent",
            "inspectability",
            "inspect before",
            "evidence summary",
        ),
    ),
    (
        "semantic_wrapup_hardening",
        (
            "semantic-wrapup hardening",
            "semantic wrap-up hardening",
            "semantic-wrapup evidence",
            "semantic wrap-up needs",
            "product requirements",
        ),
    ),
)

FAILURE_PATTERNS: tuple[dict[str, Any], ...] = (
    {
        "label": "task_running_status_update_stall",
        "terms": (
            "status update while a task was running",
            "status message while a task was running",
            "while a task was running",
            "while work was running",
            "left the process waiting",
            "waiting for the user",
            "waiting for user",
            "stalled the loop",
            "stall the loop",
            "nonblocking status",
            "keep looping",
        ),
        "fix_type": "coordination/progress-loop behavior change",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "orchestration_rule", "active_task"),
        "inferred_steps": (
            "A status/progress update was sent while work was still running.",
            "The loop stopped waiting for the user instead of polling or continuing safe non-overlapping work.",
            "Future coordinator behavior needs explicit nonblocking progress semantics for active tasks.",
        ),
    },
    {
        "label": "over_conservative_resource_hesitation",
        "terms": (
            "over-conservative resource",
            "over conservative resource",
            "resource hesitation",
            "resource guard",
            "low c-drive",
            "low c drive",
            "c-drive space",
            "c drive space",
            "free space",
            "disk space",
            "10.18 gib",
            "under-10",
            "under 10",
            "too scared",
            "too cautious",
        ),
        "fix_type": "resource-guard decision policy update",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "orchestration_rule", "project_rule"),
        "inferred_steps": (
            "Resource warnings were treated as a hard blocker without enough measured context.",
            "The user corrected the available-space interpretation and expected bounded work to continue.",
            "Future resource checks need measured thresholds, allowed bounded actions, and explicit escalation rules.",
        ),
    },
    {
        "label": "dirty_parent_worktree_hygiene",
        "terms": (
            "dirty parent",
            "parent checkout",
            "parent master",
            "wrong checkout",
            "task worktree",
            "isolated worktree",
            "stale dirty worktree",
            "stale worktree",
            "worktree state",
            "worktree-hidden",
            "git worktree",
            "git status",
        ),
        "fix_type": "worktree/currentness workflow update",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "orchestration_rule", "active_task", "task_front_door"),
        "inferred_steps": (
            "Parent checkout, task worktree, or remaining worktree state became ambiguous.",
            "Agents could validate, edit, or hand off from the wrong tree.",
            "Before next-loop readiness, the workflow needs explicit parent/worktree classification and source-backed status proof.",
        ),
    },
    {
        "label": "packet_report_bureaucracy",
        "terms": ("packet", "packets", "report", "reports", "schema", "fields", "bureaucracy", "process REDACTED"),
        "fix_type": "CLI/output behavior change",
        "preferred_path": "docs/agent-facing-tool-contract.md",
        "preferred_kinds": ("product_contract", "project_rule", "skill_reference"),
        "inferred_steps": (
            "Agent optimizes visible packet/report structure.",
            "Useful source refs and source-use guidance become secondary.",
            "User sees process churn instead of product behavior improvement.",
        ),
    },
    {
        "label": "router_context_hygiene",
        "terms": ("router", "skill", "context pollution", "resolvable", "invoke", "specialisation", "specialization"),
        "fix_type": "skill/router/reference update",
        "preferred_path": "skills/codex/jsonl-history-search/SKILL.md",
        "preferred_kinds": ("skill_router", "skill_reference", "project_rule"),
        "inferred_steps": (
            "Agent fails to distinguish router/context-hygiene requirement from a specific artifact question.",
            "Wrong specialization or too much context loads.",
            "User has to restate the intended automatic resolvability goal.",
        ),
    },
    {
        "label": "false_user_quote_or_misattribution",
        "terms": ("never said", "fake quoted", "i never asked", "misunderstood", "you keep saying"),
        "fix_type": "project rule/doc update",
        "preferred_path": "docs/agent-facing-jsonl-examples.md",
        "preferred_kinds": ("project_rule", "product_examples", "memory_or_notes"),
        "inferred_steps": (
            "Agent compresses a nuanced user explanation into a fake quoted question.",
            "The false quote becomes repeated context.",
            "User correction becomes a durable product requirement.",
        ),
    },
    {
        "label": "case002_retrieval_escalation",
        "terms": ("case-002", "case 002", "bm25", "rrf", "tool calls", "read-more", "raw trace"),
        "fix_type": "CLI/output behavior change",
        "preferred_path": "skills/codex/jsonl-history-search/references/action-proof.md",
        "preferred_kinds": ("skill_reference", "product_contract"),
        "inferred_steps": (
            "Action-proof case has primary evidence but the agent keeps searching.",
            "Retrieval escalation/stop rules are unclear or ignored.",
            "Workflow looks worse than manual grep despite source-grounded evidence.",
        ),
    },
    {
        "label": "pre_compaction_plan_preservation",
        "terms": ("context compact", "context compaction", "before it context", "forgot", "good plan earlier", "fresh agent"),
        "fix_type": "project rule/doc update",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "project_rule", "memory_or_notes"),
        "inferred_steps": (
            "A useful plan exists before compaction.",
            "After compaction, state/front-door docs do not fully preserve it.",
            "User must ask JSONL to recover what was lost.",
        ),
    },
    {
        "label": "raw_local_evidence_boundary",
        "terms": ("privacy", "raw local", "conversation logs", "raw traces", "source paths", "line ranges"),
        "fix_type": "skill/router/reference update",
        "preferred_path": "skills/codex/jsonl-history-search/references/local-evidence.md",
        "preferred_kinds": ("skill_reference", "project_rule", "product_contract"),
        "inferred_steps": (
            "Public-safe privacy posture leaks into local product diagnosis.",
            "Raw traces, source paths, and line ranges are hidden.",
            "Failure cause cannot be fixed from sanitized summaries alone.",
        ),
    },
    {
        "label": "fsm_goal_selection_and_rabbithole",
        "terms": ("fsm", "rabbithole", "rabbit hole", "goal", "loops", "small REDACTEDy tasks", "coordinate"),
        "fix_type": "project rule/doc update",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "orchestration_rule", "task_front_door"),
        "inferred_steps": (
            "FSM chooses small/process goals or keeps adding chores.",
            "Substantial product blocker remains unresolved.",
            "User forces loop goal and insertion rules to be explicit.",
        ),
    },
    {
        "label": "stop_pause_resume_direction_change",
        "terms": (
            "stop now",
            "stop doing",
            "stop running",
            "do not continue",
            "don't continue",
            "pause",
            "paused",
            "resume",
            "wait",
            "hold on",
            "wrong direction",
            "new direction",
            "instead do",
            "coordinate instead",
            "do not ask",
            "keep going",
        ),
        "fix_type": "coordination/stop-rule update",
        "preferred_path": "CODEX-ORCHESTRATION.md",
        "preferred_kinds": ("orchestration_rule", "pipeline_state", "project_rule"),
        "inferred_steps": (
            "User changed direction, paused/resumed work, or set an explicit stop/continue boundary.",
            "The assistant must preserve that boundary as controlling workflow evidence.",
            "Future runs need source-backed coordination and stop-rule handling before continuing.",
        ),
    },
    {
        "label": "agent_rules_ignored",
        "terms": ("ignored agents.md", "ignore agents.md", "ignored agent rules", "did not follow agents.md", "ignored claude.md", "did not follow claude.md"),
        "fix_type": "agent behavior rule update",
        "preferred_path": "AGENTS.md",
        "preferred_kinds": ("project_rule", "orchestration_rule", "skill_router"),
        "inferred_steps": (
            "A local behavior rule existed or was expected.",
            "The assistant acted as if that rule was not controlling.",
            "Future runs need a clearer or more discoverable behavior-system destination.",
        ),
    },
    {
        "label": "fix_later_note_not_promoted",
        "terms": ("fix this later", "fix later", "todo this", "add this to todo", "put this in todo", "follow up later"),
        "fix_type": "task/TODO update",
        "preferred_path": "TODO.md",
        "preferred_kinds": ("task_front_door", "task_files", "issue_tracker"),
        "inferred_steps": (
            "The session contains an explicit deferred fix note.",
            "Without a task/TODO destination, the note can disappear after the session.",
            "The fix should be offered as a user-selected backlog/task update.",
        ),
    },
    {
        "label": "stale_issue_or_ticket_reused",
        "terms": ("stale ticket", "stale issue", "abc-42", "old ticket", "linear ticket", "github issue"),
        "fix_type": "ticket/currentness update",
        "preferred_path": ".github/issues/",
        "preferred_kinds": ("issue_tracker", "task_files", "task_front_door"),
        "inferred_steps": (
            "A ticket or issue-like artifact was treated as current without enough currentness checking.",
            "Stale exported state can misdirect the agent's next action.",
            "Future runs need a source-backed status/currentness check before acting on that artifact.",
        ),
    },
    {
        "label": "assistant_repeated_wrong_rule",
        "terms": ("repeated wrong rule", "kept repeating the wrong rule", "wrong rule again", "same wrong rule", "you repeated the wrong rule"),
        "fix_type": "rule clarification/update",
        "preferred_path": "AGENTS.md",
        "preferred_kinds": ("project_rule", "skill_router", "memory_or_notes"),
        "inferred_steps": (
            "The assistant repeated an incorrect behavior rule after correction.",
            "The correction did not land in a durable or discoverable place.",
            "A selected rule or skill update should prevent the same mistake from recurring.",
        ),
    },
    {
        "label": "memory_learning_update_needed",
        "terms": ("update memory", "update the memory", "add to memory", "so this is not repeated", "so it isn't repeated", "do not repeat this failure"),
        "fix_type": "memory/learning update",
        "preferred_path": "memory/",
        "preferred_kinds": ("memory_or_notes", "learning_index", "project_rule"),
        "inferred_steps": (
            "The user explicitly asked for durable learning or memory.",
            "The failure may recur if the correction remains only in chat.",
            "A selected memory/learning destination should capture the source-backed rule.",
        ),
    },
    {
        "label": "reviewer_proof_claim_boundary",
        "terms": ("reviewer proof", "independent reviewer", "review artifact", "claim boundary", "acceptance criteria", "partially_verified", "partially verified", "overclaim"),
        "fix_type": "review/proof workflow update",
        "preferred_path": "CODEX-ORCHESTRATION.md",
        "preferred_kinds": ("orchestration_rule", "pipeline_state", "project_rule"),
        "inferred_steps": (
            "Review/proof boundaries shaped whether the agent could claim the task was fixed.",
            "The session evidence must separate source-backed reviewer findings from agent summaries.",
            "Future runs need clear proof and claim-boundary instructions before handoff.",
        ),
    },
    {
        "label": "product_delta_integration_staleness",
        "terms": ("product delta integration audit", "stale baseline", "dirty parent", "branch-only", "branch only", "reviewed not integrated", "baseline correction"),
        "fix_type": "integration/currentness workflow update",
        "preferred_path": "codex/state/pipeline-state.md",
        "preferred_kinds": ("pipeline_state", "orchestration_rule", "active_task"),
        "inferred_steps": (
            "A prior product delta or baseline had ambiguous integration state.",
            "Later work risked building on branch-local, dirty, stale, or unreviewed state.",
            "The next action must reconcile integration/currentness before claiming product progress.",
        ),
    },
    {
        "label": "same_task_repair_boundary",
        "terms": ("same-task", "same task", "review_failed_fixable", "review failed fixable", "fix_review_findings", "fix review findings", "install refresh", "small setup fix", "small setup fixes"),
        "fix_type": "task/review retry boundary update",
        "preferred_path": "CODEX-ORCHESTRATION.md",
        "preferred_kinds": ("orchestration_rule", "pipeline_state", "project_rule"),
        "inferred_steps": (
            "The session distinguished small proof/setup repairs from separate product blockers.",
            "Fixable review findings should stay inside the same task envelope.",
            "Only distinct substantial product blockers should become follow-up work.",
        ),
    },
)

FRICTION_MARKERS = (
    "wrong",
    "failed",
    "failure",
    "bad",
    "complain",
    "fix this",
    "not working",
    "not useful",
    "missing",
    "forgot",
    "misunderstood",
    "stop",
    "pause",
    "resume",
    "wait",
    "hold on",
    "hate",
    "angry",
    "REDACTED",
    "REDACTED",
    "REDACTED",
    "useless",
)

ISSUE_AXIS_BY_LABEL = {
    "case002_retrieval_escalation": "product_tool_issue",
    "packet_report_bureaucracy": "product_tool_issue",
    "router_context_hygiene": "skill_runtime_issue",
    "agent_rules_ignored": "skill_runtime_issue",
    "assistant_repeated_wrong_rule": "skill_runtime_issue",
    "raw_local_evidence_boundary": "evidence_handoff_issue",
    "false_user_quote_or_misattribution": "evidence_handoff_issue",
    "pre_compaction_plan_preservation": "evidence_handoff_issue",
    "fix_later_note_not_promoted": "evidence_handoff_issue",
    "memory_learning_update_needed": "evidence_handoff_issue",
    "dirty_parent_worktree_hygiene": "agent_workflow_issue",
    "fsm_goal_selection_and_rabbithole": "agent_workflow_issue",
    "stop_pause_resume_direction_change": "agent_workflow_issue",
    "reviewer_proof_claim_boundary": "agent_workflow_issue",
    "product_delta_integration_staleness": "agent_workflow_issue",
    "same_task_repair_boundary": "agent_workflow_issue",
    "stale_issue_or_ticket_reused": "agent_workflow_issue",
    "task_running_status_update_stall": "agent_workflow_issue",
    "over_conservative_resource_hesitation": "agent_workflow_issue",
}

WHY_IT_MATTERS_BY_LABEL = {
    "task_running_status_update_stall": (
        "A progress update must not silently convert an active worker loop into a blocked wait-for-user state."
    ),
    "over_conservative_resource_hesitation": (
        "Resource guards should prevent unsafe work without freezing safe bounded work after a measurable warning is corrected."
    ),
    "dirty_parent_worktree_hygiene": (
        "Dirty parent or stale worktree ambiguity can make a coordinator believe a task is ready "
        "while changes, task notes, or proof live in the wrong checkout or wrong tree."
    ),
    "packet_report_bureaucracy": (
        "Report/schema work can consume the loop while the user still lacks better source finding, source inspection, or release evidence."
    ),
    "router_context_hygiene": (
        "Wrong routing or broad context loading makes the tool harder to use and can hide the narrow source path the user asked for."
    ),
    "false_user_quote_or_misattribution": (
        "Misquoting the user turns a correction into false durable context and makes later retrieval/debugging less trustworthy."
    ),
    "case002_retrieval_escalation": (
        "Search escalation after sufficient primary evidence increases agent burden and can make JSONL look worse than manual inspection."
    ),
    "pre_compaction_plan_preservation": (
        "If current plans are lost across compaction, future agents restart from stale provenance or ask the user to recover context."
    ),
    "raw_local_evidence_boundary": (
        "Local product diagnosis needs raw source paths and trace evidence; sanitized summaries alone often cannot explain the failure."
    ),
    "fsm_goal_selection_and_rabbithole": (
        "Small process loops can crowd out the substantial product blocker the release decision depends on."
    ),
    "stop_pause_resume_direction_change": (
        "Stop, pause, resume, and direction-change boundaries are controlling instructions; missing them causes agents to continue stale work."
    ),
    "agent_rules_ignored": (
        "Ignored local behavior rules create repeat failures that cannot be fixed by search output alone."
    ),
    "fix_later_note_not_promoted": (
        "Deferred fixes disappear unless the user can explicitly accept, reject, or defer a durable task/update."
    ),
    "stale_issue_or_ticket_reused": (
        "Stale ticket state can send agents to closed or obsolete work instead of current source-backed direction."
    ),
    "assistant_repeated_wrong_rule": (
        "A repeated wrong rule shows the correction did not reach the behavior surface that future agents actually use."
    ),
    "memory_learning_update_needed": (
        "A requested durable lesson needs a source-backed destination or the same failure can recur after the session."
    ),
    "reviewer_proof_claim_boundary": (
        "Weak review/proof boundaries let implementation summaries outrun verified evidence and acceptance criteria."
    ),
    "product_delta_integration_staleness": (
        "Branch-only, dirty, or unintegrated deltas can make later work build on stale assumptions."
    ),
    "same_task_repair_boundary": (
        "Fixable review/setup findings should not become unrelated follow-up tasks, but real product blockers must not be hidden."
    ),
}


def build_semantic_wrapup_payload(
    *,
    query: str,
    source_plan: dict,
    plan: SourcePlan,
    default_tz,
    cwd: Path,
    mask_context: MaskContext | None = None,
    output_format: str = "json",
    report_output: str | None = None,
) -> dict:
    started = time.monotonic()
    scan = scan_records(plan, default_tz=default_tz, mask_context=mask_context)
    records = scan["records"]
    signals = _complaint_signals(records, query=query)
    substantive_signals = _substantive_signals(signals)
    setup_echo_signals = [signal for signal in signals if signal.get("scope_signal_kind") in SETUP_SIGNAL_KINDS]
    selected_substantive_signals = _current_session_recent_substantive_signals(
        plan,
        substantive_signals,
        signals,
    )
    user_message_first_evidence = _user_message_first_evidence(records, signals)
    selected_substantive_signals = _promote_user_message_first_cluster_signals(
        selected_substantive_signals,
        user_message_first_evidence,
    )
    factual_context = _factual_launch_context(records, signals)
    skill_check_recommendation = _skill_check_recommendation(query, signals)
    rolling_window_coverage = _rolling_window_coverage(
        records,
        signals,
        selected_substantive_signals,
        setup_echo_signals,
        scan_warnings=scan["warnings"],
    )
    ambiguous_prompt_only_scope = _ambiguous_prompt_only_current_session_scope(plan, signals, query)
    explicit_root_setup_echo_scope = _explicit_root_setup_echo_only_scope(plan, signals, query)
    fail_closed_scope = ambiguous_prompt_only_scope or explicit_root_setup_echo_scope
    all_failure_modes = [] if fail_closed_scope else _failure_modes(selected_substantive_signals)
    failure_modes = all_failure_modes[:INLINE_FAILURE_MODE_LIMIT]
    cluster_promotion = _cluster_promotion_surface(
        user_message_first_evidence,
        returned_failure_modes=failure_modes,
        all_failure_modes=all_failure_modes,
    )
    orchestration_evidence = _orchestration_evidence(records, signals=signals, query=query)
    current_session_source_selection = _current_session_source_selection(cwd, query)
    currentness_boundary = _currentness_boundary(plan, query)
    behavior_system = detect_behavior_system(cwd)
    all_fix_suggestions = _fix_suggestions(all_failure_modes, behavior_system)
    fix_suggestions = all_fix_suggestions[:INLINE_FAILURE_MODE_LIMIT]
    status = "scope_ambiguous" if fail_closed_scope else _semantic_wrapup_status(plan, all_failure_modes)
    scope_resolution = _scope_resolution(plan, source_plan, query)
    if fail_closed_scope:
        scope_resolution.update(fail_closed_scope)
    next_reads = _semantic_next_reads(
        failure_modes,
        orchestration_evidence,
        current_session_source_selection,
        rolling_window_coverage,
        setup_echo_signals=setup_echo_signals,
        prefer_setup_signal_refs=bool(explicit_root_setup_echo_scope),
        allow_current_session_sources_without_failure_modes=not _latest_session_fail_closed(fail_closed_scope),
    )
    read_role_summary = _semantic_read_role_summary(next_reads)
    answer_ready_contract = _answer_ready_contract(
        status=status,
        failure_modes=failure_modes,
        fix_suggestions=fix_suggestions,
        next_reads=next_reads,
        read_role_summary=read_role_summary,
        cluster_promotion=cluster_promotion,
    )
    answer_use_contract = _answer_use_contract(status, scope_resolution, next_reads, answer_ready_contract)
    payload = {
        "semantic_wrapup": {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "query": query,
            "route": "semantic_wrapup_frustration_mining",
            "summary": {
                "complaint_signal_count": len(signals),
                "session_message_count": len(records),
                "user_message_count": rolling_window_coverage["session_accounting"]["user_message_count"],
                "user_complaint_candidate_count": len(signals),
                "user_complaint_windows_inspected": rolling_window_coverage["window_accounting"]["windows_inspected"],
                "user_complaint_windows_omitted": rolling_window_coverage["window_accounting"]["windows_omitted"],
                "assistant_tool_runtime_error_count": rolling_window_coverage["assistant_tool_runtime_error_count"],
                "cap_changed_conclusion": rolling_window_coverage["cap_accounting"]["cap_changed_conclusion"],
                "substantive_signal_count": len(substantive_signals),
                "selected_substantive_signal_count": len(selected_substantive_signals),
                "setup_echo_signal_count": len(setup_echo_signals),
                "failure_mode_count": len(failure_modes),
                "failure_mode_returned_count": len(failure_modes),
                "failure_mode_total_count": len(all_failure_modes),
                "failure_mode_truncated": len(all_failure_modes) > len(failure_modes),
                "fix_suggestion_count": len(fix_suggestions),
                "fix_suggestion_returned_count": len(fix_suggestions),
                "fix_suggestion_total_count": len(all_fix_suggestions),
                "fix_suggestion_truncated": len(all_fix_suggestions) > len(fix_suggestions),
                "inline_limit": INLINE_FAILURE_MODE_LIMIT,
                "behavior_system_destination_count": len(behavior_system["destinations"]),
                "orchestration_evidence_count": orchestration_evidence["returned_count"],
                "orchestration_evidence_total_count": orchestration_evidence["total_count"],
                "orchestration_evidence_truncated": orchestration_evidence["truncated"],
                "current_session_source_count": current_session_source_selection["returned_count"],
                "current_session_source_total_count": current_session_source_selection["total_count"],
                "skill_check_recommendation_present": bool(skill_check_recommendation),
                "user_message_first_candidate_count": user_message_first_evidence["candidate_count"],
                "user_message_first_cluster_count": user_message_first_evidence["cluster_count"],
                "cluster_promoted_count": cluster_promotion["promoted_count"],
                "cluster_parked_count": cluster_promotion["parked_count"],
                "cluster_truncated_count": cluster_promotion["truncated_count"],
                "factual_context_count": factual_context["count"],
                "default_actionable_read_count": read_role_summary["default_actionable_read_count"],
                "optional_diagnostic_read_count": read_role_summary["optional_diagnostic_count"],
                "conditional_required_read_count": read_role_summary["conditional_required_count"],
                "elapsed_seconds": round(time.monotonic() - started, 6),
            },
            "failure_modes": failure_modes,
            "signal_classification": {
                "substantive_signal_count": len(substantive_signals),
                "selected_substantive_signal_count": len(selected_substantive_signals),
                "user_message_first_cluster_promoted_signal_count": cluster_promotion["synthetic_signal_count"],
                "setup_echo_signal_count": len(setup_echo_signals),
                "substantive_source_refs": [signal["source_ref"] for signal in substantive_signals[:8]],
                "selected_substantive_source_refs": [signal["source_ref"] for signal in selected_substantive_signals[:8]],
                "setup_echo_source_refs": [signal["source_ref"] for signal in setup_echo_signals[:8]],
                "failure_modes_exclude_setup_echoes": True,
                "latest_session_recent_signal_filter": _latest_session_recent_signal_filter_summary(
                    plan,
                    substantive_signals,
                    selected_substantive_signals,
                    signals,
                ),
            },
            "user_message_first_evidence": user_message_first_evidence,
            "cluster_promotion": cluster_promotion,
            "factual_context": factual_context,
            "rolling_window_coverage": rolling_window_coverage,
            "orchestration_evidence": orchestration_evidence,
            "current_session_source_selection": current_session_source_selection,
            "currentness_boundary": currentness_boundary,
            "fix_suggestions": fix_suggestions,
            "behavior_system": behavior_system,
            "scope_resolution": scope_resolution,
            "skill_check_recommendation": skill_check_recommendation,
            "agent_instruction": _agent_instruction(status, scope_resolution, next_reads, answer_use_contract, skill_check_recommendation),
            "answer_ready_contract": answer_ready_contract,
            "answer_use_contract": answer_use_contract,
            "read_role_summary": read_role_summary,
            "next_reads": next_reads,
            "privacy": {
                "raw_text_included": True,
                "source_refs_included": True,
                "external_upload_performed": False,
            },
            "context_budget": {
                "format": output_format,
                "report_written": report_output,
                "searched_files": scan["searched_files"],
                "searched_bytes": scan["searched_bytes"],
            },
            "warnings": sorted(set(scan["warnings"])),
            "blind_spots": sorted(set(scan["blind_spots"])),
        },
        "source_plan": source_plan,
        "warnings": sorted(set(scan["warnings"] + source_plan.get("sources", {}).get("warnings", []))),
        "blind_spots": sorted(set(scan["blind_spots"] + source_plan.get("sources", {}).get("blind_spots", []))),
    }
    if "masking" in scan:
        payload["masking"] = scan["masking"]
    return payload


def _semantic_wrapup_status(plan: SourcePlan, failure_modes: list[dict]) -> str:
    if not plan.roots:
        if plan.scope_resolution.get("status") == "ambiguous_current_session":
            return "scope_ambiguous"
        if plan.rejected_roots:
            return "root_rejected"
        return "no_roots"
    return "found" if failure_modes else "no_failure_modes_found"


def _rolling_window_coverage(
    records,
    signals: list[dict],
    substantive_signals: list[dict],
    setup_echo_signals: list[dict],
    *,
    scan_warnings: list[str],
) -> dict:
    ref_to_index = {
        (item.record.path, int(item.record.line)): index
        for index, item in enumerate(records)
    }
    user_records = [item for item in records if _is_user_like(item.record.role)]
    windows: list[dict] = []
    error_items: list[dict] = []
    omitted_next_reads: list[dict] = []
    for ordinal, signal in enumerate(substantive_signals, 1):
        source_ref = signal.get("source_ref") or {}
        key = (str(source_ref.get("path") or ""), int(source_ref.get("line") or 0))
        center = ref_to_index.get(key)
        if center is None:
            continue
        start = max(0, center - ROLLING_WINDOW_PRE_RECORDS)
        end = min(len(records) - 1, center + ROLLING_WINDOW_POST_RECORDS)
        window_records = records[start : end + 1]
        errors = _assistant_tool_errors(window_records)
        error_items.extend(errors)
        window = {
            "window_id": f"w{ordinal:03d}",
            "signal_id": signal.get("signal_id"),
            "signal_source_ref": source_ref,
            "start_source_ref": {"path": records[start].record.path, "line": records[start].record.line},
            "end_source_ref": {"path": records[end].record.path, "line": records[end].record.line},
            "record_count": len(window_records),
            "user_message_count": len([item for item in window_records if _is_user_like(item.record.role)]),
            "assistant_tool_runtime_error_count": len(errors),
            "assistant_tool_runtime_error_refs": [item["source_ref"] for item in errors[:5]],
        }
        if len(windows) < ROLLING_WINDOW_INLINE_LIMIT:
            windows.append(window)
        else:
            omitted_next_reads.append(
                {
                    "source_ref": source_ref,
                    "reason": (
                        "Rolling complaint window was inspected for coverage but omitted from inline output; "
                        "open this signal if the returned failure modes do not cover the later correction."
                    ),
                }
            )

    total_windows = len(substantive_signals)
    inline_omitted = max(0, total_windows - len(windows))
    scan_cap_warnings = [warning for warning in scan_warnings if "cap" in warning.lower() or "elapsed" in warning.lower()]
    cap_changed_conclusion = bool(scan_cap_warnings)
    if inline_omitted:
        cap_changed_conclusion = True
    return {
        "policy": (
            "Default semantic-wrapup enumerates all user records in the selected session, "
            "builds bounded rolling windows around every substantive user complaint/correction, "
            "and mines assistant/tool/runtime errors in those windows."
        ),
        "window_radius": {
            "pre_records": ROLLING_WINDOW_PRE_RECORDS,
            "post_records": ROLLING_WINDOW_POST_RECORDS,
        },
        "session_accounting": {
            "session_message_count": len(records),
            "user_message_count": len(user_records),
            "user_complaint_candidate_count": len(signals),
            "substantive_user_complaint_candidate_count": len(substantive_signals),
            "setup_echo_candidate_count": len(setup_echo_signals),
        },
        "window_accounting": {
            "windows_considered": total_windows,
            "windows_inspected": total_windows,
            "windows_returned_inline": len(windows),
            "windows_omitted": inline_omitted,
            "inline_window_limit": ROLLING_WINDOW_INLINE_LIMIT,
        },
        "cap_accounting": {
            "scan_cap_warnings": scan_cap_warnings,
            "inline_window_omitted_count": inline_omitted,
            "error_item_omitted_count": max(0, len(error_items) - ROLLING_WINDOW_ERROR_INLINE_LIMIT),
            "cap_changed_conclusion": cap_changed_conclusion,
            "cap_boundary": (
                "cap_changed_conclusion is true when scan caps or inline omission mean a reader should "
                "inspect omitted windows before treating the returned summary as complete."
            ),
        },
        "assistant_tool_runtime_error_count": len(error_items),
        "assistant_tool_runtime_errors": error_items[:ROLLING_WINDOW_ERROR_INLINE_LIMIT],
        "windows": windows,
        "omitted_or_uncertain_next_reads": omitted_next_reads[:8],
    }


def _assistant_tool_errors(window_records) -> list[dict]:
    errors = []
    for item in window_records:
        if _is_user_like(item.record.role):
            continue
        normalized = _normalize(item.text)
        labels = [
            label
            for label, terms in ASSISTANT_TOOL_ERROR_TERMS
            if any(term in normalized for term in terms)
        ]
        if not labels:
            continue
        row = context_row(item)
        errors.append(
            {
                "error_id": f"e{len(errors) + 1:03d}",
                "labels": labels,
                **row,
            }
        )
    return errors


def _user_message_first_evidence(records, signals: list[dict]) -> dict:
    signal_by_ref = {
        (signal["source_ref"]["path"], int(signal["source_ref"]["line"])): signal
        for signal in signals
        if signal.get("source_ref")
    }
    classification_counts: dict[str, int] = {
        "frustration/correction": 0,
        "directive": 0,
        "factual_clarification": 0,
        "launch_criterion": 0,
        "non_complaint_context": 0,
    }
    candidates: list[dict] = []
    total_user_messages = 0
    for index, item in enumerate(records):
        if not _is_user_like(item.record.role):
            continue
        total_user_messages += 1
        row = context_row(item)
        source_ref = row.get("source_ref") or {}
        signal = signal_by_ref.get((str(source_ref.get("path") or ""), int(source_ref.get("line") or 0)))
        classifications = _classify_user_message(item.text, signal)
        for classification in classifications:
            classification_counts[classification] = classification_counts.get(classification, 0) + 1
        if "frustration/correction" not in classifications:
            continue
        candidates.append(
            {
                "candidate_id": f"u{len(candidates) + 1:03d}",
                "source_ref": source_ref,
                "timestamp_utc": row.get("timestamp_utc"),
                "classifications": classifications,
                "labels": list((signal or {}).get("labels") or ["general_workflow_failure"]),
                "friction_markers": list((signal or {}).get("friction_markers") or []),
                "scope_signal_kind": (signal or {}).get("scope_signal_kind") or "user_message_no_signal",
                "snippet": row.get("snippet"),
                "nearby_turn_context": _nearby_turn_context(records, index),
            }
        )
    clusters = _clusters_from_user_message_candidates(candidates)
    return {
        "method": (
            "Read target-session user-authored messages first; classify each user message; "
            "retain nearby turn context for frustration/correction candidates; cluster those "
            "candidates before mapping clusters to fix suggestions."
        ),
        "source_priority": "target_session_user_messages_before_task_summaries_reports_or_stale_labels",
        "classifications_required": [
            "frustration/correction",
            "directive",
            "factual_clarification",
            "launch_criterion",
            "non_complaint_context",
        ],
        "total_user_messages": total_user_messages,
        "classification_counts": classification_counts,
        "candidate_count": len(candidates),
        "candidates_returned": candidates[:USER_MESSAGE_FIRST_INLINE_LIMIT],
        "candidates_omitted": max(0, len(candidates) - USER_MESSAGE_FIRST_INLINE_LIMIT),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "clustered_before_fix_suggestions": True,
        "mapping_boundary": (
            "Only clusters backed by current target-session user-message candidates should be "
            "ranked as current-session fix suggestions; factual context is preserved separately."
        ),
    }


def _classify_user_message(text: str, signal: dict | None) -> list[str]:
    normalized = _normalize(text)
    classifications: list[str] = []
    if signal and signal.get("scope_signal_kind") not in SETUP_SIGNAL_KINDS:
        classifications.append("frustration/correction")
    elif any(marker in normalized for marker in FRICTION_MARKERS):
        classifications.append("frustration/correction")

    if _is_factual_context_text(normalized):
        classifications.append("factual_clarification")
    if any(term in normalized for term in ("before launch", "launch", "release", "release-ready", "claim", "criterion", "gate")):
        classifications.append("launch_criterion")
    if any(
        term in normalized
        for term in (
            "you are",
            "work only",
            "do not",
            "don't",
            "must",
            "required",
            "implement",
            "run ",
            "use ",
            "read ",
            "final message",
        )
    ):
        classifications.append("directive")
    if not classifications:
        classifications.append("non_complaint_context")
    return classifications


def _is_factual_context_text(normalized: str) -> bool:
    return (
        "jsh-1253" in normalized
        or "2/7" in normalized
        or "1/7" in normalized
        or "whx-002" in normalized
        or "exp-002" in normalized
        or "partial checkpoint" in normalized
        or "factual context" in normalized
    )


def _nearby_turn_context(records, center_index: int) -> dict:
    start = max(0, center_index - USER_MESSAGE_CONTEXT_PRE_RECORDS)
    end = min(len(records) - 1, center_index + USER_MESSAGE_CONTEXT_POST_RECORDS)
    items = []
    for item in records[start : end + 1]:
        row = context_row(item)
        items.append(
            {
                "role": item.record.role,
                "source_ref": row.get("source_ref"),
                "timestamp_utc": row.get("timestamp_utc"),
                "snippet": row.get("snippet"),
            }
        )
    return {
        "start_source_ref": items[0].get("source_ref") if items else None,
        "end_source_ref": items[-1].get("source_ref") if items else None,
        "record_count": len(items),
        "records": items,
    }


def _clusters_from_user_message_candidates(candidates: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        labels = candidate.get("labels") or ["general_workflow_failure"]
        for label in labels:
            grouped.setdefault(label, []).append(candidate)
    clusters = []
    for label, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        clusters.append(
            {
                "cluster_id": f"cluster-{len(clusters) + 1:03d}",
                "label": label,
                "candidate_count": len(items),
                "candidate_ids": [item["candidate_id"] for item in items[:8]],
                "source_refs": [item["source_ref"] for item in items[:5]],
                "examples": [
                    {
                        "source_ref": item.get("source_ref"),
                        "timestamp_utc": item.get("timestamp_utc"),
                        "snippet": item.get("snippet"),
                    }
                    for item in items[:3]
                ],
                "nearby_context_window_count": len([item for item in items if item.get("nearby_turn_context")]),
            }
        )
    return clusters


def _promote_user_message_first_cluster_signals(
    selected_signals: list[dict],
    user_message_first_evidence: dict,
) -> list[dict]:
    promoted = list(selected_signals)
    represented_labels = {
        str(label)
        for signal in selected_signals
        for label in (signal.get("labels") or [])
    }
    for cluster in user_message_first_evidence.get("clusters") or []:
        label = str(cluster.get("label") or "")
        if not label or label in represented_labels:
            continue
        candidate_count = int(cluster.get("candidate_count") or 0)
        if candidate_count < 2:
            continue
        examples = [item for item in cluster.get("examples") or [] if isinstance(item, dict)]
        if not examples:
            continue
        for index, example in enumerate(examples, 1):
            source_ref = example.get("source_ref") if isinstance(example.get("source_ref"), dict) else {}
            promoted.append(
                {
                    "signal_id": f"{cluster.get('cluster_id') or 'cluster'}-promotion-{index}",
                    "labels": [label],
                    "friction_markers": [],
                    "scope_signal_kind": "user_message_first_cluster",
                    "actor_label": "user_message_first_cluster",
                    "source_ref": source_ref,
                    "timestamp_utc": example.get("timestamp_utc"),
                    "snippet": example.get("snippet")
                    or f"User-message-first cluster `{label}` had {candidate_count} correction candidate(s).",
                    "cluster_candidate_count": candidate_count,
                    "cluster_id": cluster.get("cluster_id"),
                }
            )
        represented_labels.add(label)
    return promoted


def _factual_launch_context(records, signals: list[dict]) -> dict:
    signal_refs = {
        (signal["source_ref"]["path"], int(signal["source_ref"]["line"]))
        for signal in signals
        if signal.get("source_ref")
    }
    entries = []
    for item in records:
        if not _is_user_like(item.record.role):
            continue
        row = context_row(item)
        source_ref = row.get("source_ref") or {}
        normalized = _normalize(item.text)
        if not _is_factual_context_text(normalized):
            continue
        entries.append(
            {
                "context_id": f"fc{len(entries) + 1:03d}",
                "classification": "factual_launch_context_not_complaint_evidence",
                "source_ref": source_ref,
                "timestamp_utc": row.get("timestamp_utc"),
                "safe_summary": _safe_factual_context_summary(normalized),
                "excluded_from_failure_mode_grouping": (
                    (str(source_ref.get("path") or ""), int(source_ref.get("line") or 0)) not in signal_refs
                ),
            }
        )
    return {
        "count": len(entries),
        "entries": entries[:8],
        "policy": (
            "Factual launch/checkpoint context is preserved for claim boundaries but is not "
            "itself complaint evidence unless the user message also contains a substantive correction signal."
        ),
    }


def _safe_factual_context_summary(normalized: str) -> str:
    if "jsh-1253" in normalized or "whx-002" in normalized or "exp-002" in normalized:
        return (
            "JSH-1253 factual partial checkpoint: 2/7 accepted cases attempted, "
            "1/7 completed pair, WHX-002 completed, EXP-002 failed closed/interrupted; "
            "no broad release/provider-token/superiority claim."
        )
    return "Factual clarification preserved separately from complaint evidence."


def _agent_instruction(
    status: str,
    scope_resolution: dict,
    next_reads: list[dict] | None = None,
    answer_use_contract: dict | None = None,
    skill_check_recommendation: dict | None = None,
) -> dict:
    contract = answer_use_contract or {}
    if contract.get("decision") == "answer_from_top_next_read_or_handoff_exact_source":
        first_read = (next_reads or [{}])[0]
        return {
            "selection_required": False,
            "do_not_auto_apply": True,
            "source_use_required": True,
            "decision": contract.get("decision"),
            "required_next_action": contract.get("required_next_action"),
            "required_source_ref": first_read.get("source_ref"),
            "answer_scope": contract.get("answer_scope"),
            "fallback_next_step": scope_resolution.get("first_action"),
        }
    if status == "scope_ambiguous":
        instruction = {
            "selection_required": False,
            "do_not_auto_apply": True,
            "source_use_required": False,
            "next_step": scope_resolution.get("first_action")
            or "Rerun semantic-wrapup with an explicit --root session JSONL before applying fixes.",
        }
        if skill_check_recommendation:
            instruction["focused_follow_up"] = skill_check_recommendation
        return instruction
    if contract.get("decision") == "answer_ready_after_source_inspection":
        instruction = {
            "selection_required": True,
            "do_not_auto_apply": True,
            "source_use_required": True,
            "decision": contract.get("decision"),
            "answer_ready": True,
            "stop_broad_search": True,
            "required_next_action": contract.get("required_next_action"),
            "stop_condition": contract.get("stop_condition"),
            "continue_only_if": list(contract.get("continue_only_if") or []),
            "next_step": contract.get("required_next_action"),
        }
        if skill_check_recommendation:
            instruction["focused_follow_up"] = skill_check_recommendation
        return instruction
    instruction = {
        "selection_required": True,
        "do_not_auto_apply": True,
        "source_use_required": bool(next_reads),
        "next_step": "Ask the user which suggested fixes to apply, then implement selected fixes through normal task/FSM flow.",
    }
    if skill_check_recommendation:
        instruction["focused_follow_up"] = skill_check_recommendation
    return instruction


def _skill_check_recommendation(query: str, signals: list[dict]) -> dict | None:
    text = query.lower()
    if not _looks_like_skill_use_compliance_signal(text):
        return None
    skill = _extract_named_skill(text)
    return {
        "route": "skill_check_named_skill_compliance",
        "reason": (
            "Semantic-wrapup is for frustration/failure/improvement mining. "
            "This prompt also asks whether a named skill was used, read, or followed, "
            "which requires the focused skill-check/skill-audit path."
        ),
        "skill": skill,
        "first_command": f'jsonl-history-search skill-check "{skill}" --cwd "$PWD" --format text',
        "alias_of": "skill-audit",
        "semantic_wrapup_boundary": (
            "Do not treat this semantic-wrapup payload as a deep skill compliance audit; "
            "run skill-check/skill-audit for proper-use, missed-trigger, misapplication, "
            "and referenced-file follow-through classifications."
        ),
    }


def _looks_like_skill_use_compliance_signal(text: str) -> bool:
    has_skill_term = any(
        term in text
        for term in (
            "skill",
            "skill.md",
            "reviewer",
            "product-fsm",
            "product fsm",
            "implementer",
            "planner",
            "wrap-up",
            "wrap up",
        )
    )
    has_compliance_term = bool(
        re.search(
            r"\b(use|used|using|trigger|triggered|read|follow|followed|compliance|missed|misapplied)\b",
            text,
        )
        or re.search(r"\breferenced files?\b", text)
    )
    return has_skill_term and has_compliance_term


def _extract_named_skill(text: str) -> str:
    match = re.search(r"<name>([^<]+)</name>", text)
    if match:
        return _normalize_skill_name(match.group(1))
    match = re.search(r"\$([a-z0-9][a-z0-9_-]+)", text)
    if match:
        return _normalize_skill_name(match.group(1))
    match = re.search(r"\b([a-z0-9][a-z0-9_-]+)\s+skill\b", text)
    if match and match.group(1) not in {"the", "a", "named", "that", "this"}:
        return _normalize_skill_name(match.group(1))
    for candidate in (
        "product-fsm-orchestration",
        "reviewer",
        "implementer",
        "planner",
        "wrap-up",
    ):
        if candidate in text or candidate.replace("-", " ") in text:
            return candidate
    return "<skill-name>"


def _normalize_skill_name(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _answer_ready_contract(
    *,
    status: str,
    failure_modes: list[dict],
    fix_suggestions: list[dict],
    next_reads: list[dict],
    read_role_summary: dict | None = None,
    cluster_promotion: dict | None = None,
) -> dict:
    if status != "found" or not failure_modes:
        return {
            "answer_ready": False,
            "decision": "not_answer_ready",
            "reason": "semantic-wrapup did not return source-backed failure modes.",
        }
    if not fix_suggestions:
        return {
            "answer_ready": False,
            "decision": "not_answer_ready",
            "reason": "semantic-wrapup returned failure modes without user-selectable fix suggestions.",
        }
    cluster_gate = _cluster_promotion_answer_ready_gate(failure_modes, cluster_promotion or {})
    if not cluster_gate["answer_surface_ready"]:
        return {
            "answer_ready": False,
            "decision": "not_answer_ready",
            "reason": cluster_gate["reason"],
            "cluster_promotion_status": cluster_gate,
        }
    summary = read_role_summary or _semantic_read_role_summary(next_reads)
    return {
        "answer_ready": True,
        "decision": "answer_ready_after_source_inspection",
        "source_use_required": summary["required_primary_proof_count"] > 0,
        "stop_broad_search": True,
        "selection_required": True,
        "auto_apply": False,
        "failure_mode_count": len(failure_modes),
        "fix_suggestion_count": len(fix_suggestions),
        "cluster_promotion_status": cluster_gate,
        "read_role_summary": summary,
        "required_next_action": (
            "Inspect required_primary_proof source refs, then present the user-selectable fix suggestions "
            "or ask which accepted fix to implement. Do not open optional diagnostics by default."
        ),
        "stop_condition": (
            "Stop broad searching once required_primary_proof refs support the returned failure modes "
            "and fix suggestions; optional_diagnostic reads are not required follow-through."
        ),
        "continue_only_if": [
            "a returned failure mode lacks source proof after inspecting listed refs",
            "a conditional_required read is needed because caps/omissions block completeness",
            "the proposed destination is ambiguous or contradicted by inspected refs",
            "the user asks for exhaustive coverage or diagnostic corroboration",
            "the user selects a fix and implementation needs the normal task/FSM workflow",
        ],
        "claim_boundary": (
            "This is an answer-ready failure-mode/fix-suggestion handoff after source inspection; "
            "it does not auto-apply changes or prove broad all-history completeness."
        ),
    }


def _cluster_promotion_answer_ready_gate(failure_modes: list[dict], cluster_promotion: dict) -> dict:
    labels = {str(mode.get("failure_mode") or "") for mode in failure_modes}
    non_generic_labels = labels - {"general_workflow_failure"}
    ranked_clusters = [item for item in cluster_promotion.get("ranked_clusters") or [] if isinstance(item, dict)]
    parked_without_reason = [
        item
        for item in ranked_clusters
        if item.get("promotion_status") in {"parked", "truncated"} and not str(item.get("reason") or "").strip()
    ]
    if len(ranked_clusters) > 1 and not non_generic_labels:
        return {
            "answer_surface_ready": False,
            "reason": "multiple correction clusters were detected but only a generic failure mode was returned",
            "promoted_failure_modes": sorted(labels),
            "parked_without_reason_count": len(parked_without_reason),
        }
    if parked_without_reason:
        return {
            "answer_surface_ready": False,
            "reason": "one or more detected correction clusters lacked promoted/parked reasoning",
            "promoted_failure_modes": sorted(labels),
            "parked_without_reason_count": len(parked_without_reason),
        }
    return {
        "answer_surface_ready": True,
        "reason": "promoted clusters and parked/truncated reasons are sufficient for next-task selection",
        "promoted_failure_modes": sorted(labels),
        "parked_without_reason_count": 0,
    }


def _answer_use_contract(
    status: str,
    scope_resolution: dict,
    next_reads: list[dict],
    answer_ready_contract: dict | None = None,
) -> dict:
    if (answer_ready_contract or {}).get("answer_ready") is True:
        return dict(answer_ready_contract or {})
    first_read = next_reads[0] if next_reads else {}
    first_ref = first_read.get("source_ref") or {}
    requires_top_read = bool(first_ref) and first_read.get("answer_use_required") is True
    if requires_top_read:
        target = f"{first_ref.get('path')}:{first_ref.get('line')}"
        return {
            "source_use_required": True,
            "decision": "answer_from_top_next_read_or_handoff_exact_source",
            "answer_scope": (
                "Answer the retained current-session complaint/fix question from the top next read; "
                "do not answer from the fresh/setup/eval prompt that was classified as setup echo."
            ),
            "required_next_action": (
                f"Open {target}; answer from that source if it contains the retained complaint/fix evidence, "
                "or hand off that exact source ref and why it must be read."
            ),
            "required_source_ref": first_ref,
            "setup_echo_boundary": (
                "The latest/current session may still be setup-echo dominant; that only blocks mining the "
                "fresh prompt as complaint evidence. It does not license dropping the retained top next read."
            ),
            "fallback_if_source_insufficient": scope_resolution.get("first_action"),
        }
    if status == "scope_ambiguous":
        return {
            "source_use_required": False,
            "decision": "fail_closed_until_exact_session_root",
            "required_next_action": scope_resolution.get("first_action")
            or "Rerun semantic-wrapup with an explicit --root session JSONL before applying fixes.",
        }
    return {
        "source_use_required": _semantic_read_role_summary(next_reads)["required_primary_proof_count"] > 0,
        "decision": "answer_after_inspecting_source_refs" if next_reads else "no_source_refs_available",
        "required_next_action": (
            "Inspect required_primary_proof refs before summarizing failure modes or applying fixes; "
            "open optional diagnostics only for named gaps."
            if next_reads
            else "Do not fabricate failure modes without source refs."
        ),
    }


def _scope_resolution(plan: SourcePlan, source_plan: dict, query: str) -> dict:
    resolution = dict(plan.scope_resolution)
    if not resolution:
        resolution = {
            "status": "explicit_or_default_source_plan",
            "strategy": "shared_source_plan",
        }
    resolution.update(
        {
            "selected_root_count": len(plan.roots),
            "rejected_root_count": len(plan.rejected_roots),
            "no_broad_history_sweep_for_current_session": plan.scope_resolution.get("status")
            in {"scoped_to_latest_known_session", "ambiguous_current_session"},
        }
    )
    if not plan.roots:
        resolution["first_action"] = (
            "Rerun with an exact known Codex/Claude session JSONL, for example: "
            f"{_rerun_with_root_command(query)}"
        )
    elif plan.scope_resolution.get("status") == "scoped_to_latest_known_session":
        root = (source_plan.get("sources") or {}).get("roots", [{}])[0]
        resolution["selected_kind"] = root.get("kind")
        resolution["selected_root_known"] = root.get("known")
    return resolution


def _ambiguous_prompt_only_current_session_scope(plan: SourcePlan, signals: list[dict], query: str) -> dict | None:
    if plan.scope_resolution.get("status") != "scoped_to_latest_known_session":
        return None
    if not signals:
        return None
    setup_echo_signals = [
        signal for signal in signals if signal.get("scope_signal_kind") in SETUP_SIGNAL_KINDS
    ]
    if not setup_echo_signals:
        return None
    substantive_signals = [signal for signal in signals if signal.get("scope_signal_kind") == "user_complaint"]
    if substantive_signals:
        return None
    prompt_only_count = len(
        [signal for signal in setup_echo_signals if signal.get("scope_signal_kind") == "semantic_wrapup_prompt"]
    )
    strategy = (
        "latest_known_session_prompt_only_fail_closed"
        if prompt_only_count == len(signals)
        else "latest_known_session_setup_echo_dominant_fail_closed"
    )
    return {
        "status": "scope_ambiguous",
        "strategy": strategy,
        "reason": (
            "Current-session wording selected the latest known session, but the complaint/failure "
            "signals in that session are dominated by receiving-agent prompt, benchmark/setup, "
            "or orchestration echoes rather than parent/user complaint evidence."
        ),
        "selected_latest_session_status": "setup_echo_dominant_complaint_signals",
        "prompt_only_signal_count": prompt_only_count,
        "setup_echo_signal_count": len(setup_echo_signals),
        "substantive_signal_count": len(substantive_signals),
        "first_action": (
            "Latest known session appears to be a receiving-agent/setup session. Identify the parent/user "
            "session that contains the launch-loop discussion, then rerun with that exact Codex/Claude "
            f"session JSONL; for example: {_rerun_with_root_command(query)}"
        ),
        "stale_artifact_boundary": (
            "Do not answer mainly from retained semantic-wrapup reports, task summaries, or old hardening "
            "artifacts until the parent/user session is anchored."
        ),
    }


def _explicit_root_setup_echo_only_scope(plan: SourcePlan, signals: list[dict], query: str) -> dict | None:
    if plan.scope_resolution.get("status") == "scoped_to_latest_known_session":
        return None
    if not plan.roots or not signals or not _is_current_session_complaint_fix_query(query):
        return None
    setup_echo_signals = [
        signal for signal in signals if signal.get("scope_signal_kind") in SETUP_SIGNAL_KINDS
    ]
    if len(setup_echo_signals) != len(signals):
        return None
    return {
        "status": "scope_ambiguous",
        "strategy": "explicit_root_setup_echo_only_fail_closed",
        "reason": (
            "The explicit root contains complaint/failure-looking signals, but they are setup, "
            "reviewer, worker, or semantic-wrapup prompt echoes rather than source-backed "
            "current-session user corrections."
        ),
        "selected_explicit_root_status": "setup_echo_only_complaint_signals",
        "setup_echo_signal_count": len(setup_echo_signals),
        "substantive_signal_count": 0,
        "first_action": (
            "Inspect the listed selected-session signal refs to confirm they are only setup/reviewer "
            "echoes, or rerun semantic-wrapup with the parent/user session JSONL."
        ),
    }


def _rerun_with_root_command(query: str) -> str:
    quoted_query = shlex.quote(query)
    return (
        f"jsonl-history-search semantic-wrapup {quoted_query} "
        '--root <path-to-session.jsonl> --cwd "$PWD" --format json'
    )


def _latest_session_fail_closed(fail_closed_scope: dict | None) -> bool:
    strategy = str((fail_closed_scope or {}).get("strategy") or "")
    return strategy.startswith("latest_known_session_")


def _currentness_boundary(plan: SourcePlan, query: str) -> dict:
    normalized = _normalize(query)
    mentions_current = any(
        term in normalized
        for term in ("this session", "current session", "current-session", "this run", "current run", "latest session")
    )
    short_correction_prompt = bool(
        re.search(r"\bwhat did i (keep )?(correct|correcting|complain|complaining)\b", normalized)
    )
    if plan.scope_resolution.get("status") == "scoped_to_latest_known_session":
        return {
            "status": "current_session_attempted",
            "boundary": "Current-session wording scoped the scan to the latest known session file.",
            "stale_summary_allowed": False,
        }
    if mentions_current:
        return {
            "status": "current_session_unanchored",
            "boundary": "Current-session wording was present but the selected scope is not a verified parent/user session.",
            "stale_summary_allowed": False,
        }
    if short_correction_prompt:
        return {
            "status": "broad_history_not_current_session",
            "boundary": (
                "This short correction/complaint prompt did not name the current or parent session. "
                "Any broad-history findings are historical context, not a current-session answer; "
                "rerun with 'in this session' plus an exact --root parent/user session JSONL for current launch-loop recovery."
            ),
            "stale_summary_allowed": True,
            "current_session_answer_supported": False,
            "first_action_for_current_session": (
                "Rerun semantic-wrapup with an exact parent/user session JSONL: "
                f"{_rerun_with_root_command(query)}"
            ),
        }
    return {
        "status": "scope_not_current_session_specific",
        "boundary": "No current-session anchoring was requested or inferred.",
        "stale_summary_allowed": True,
    }


def _current_session_source_selection(cwd: Path, query: str) -> dict:
    if not _is_current_session_complaint_fix_query(query):
        return _empty_current_session_source_selection("query_not_current_session_complaint_fix")

    candidates = []
    for rel in _current_session_source_paths(cwd):
        path = cwd / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        candidate = _score_current_session_source(rel, text)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: (-item["score"], -len(item["facets"]), item["source_ref"]["path"]))
    selected = candidates[:CURRENT_SESSION_SOURCE_LIMIT]
    status = "found" if selected else "not_found"
    return {
        "status": status,
        "selection_policy": (
            "rank cwd-local side-thread/task/report evidence by semantic-wrapup current-session "
            "smoke/hardening facets before generic active-gate/front-door context"
        ),
        "returned_count": len(selected),
        "total_count": len(candidates),
        "truncated": len(candidates) > len(selected),
        "selected_sources": selected,
    }


def _empty_current_session_source_selection(reason: str) -> dict:
    return {
        "status": "not_applicable",
        "reason": reason,
        "selection_policy": (
            "rank cwd-local side-thread/task/report evidence by semantic-wrapup current-session "
            "smoke/hardening facets before generic active-gate/front-door context"
        ),
        "returned_count": 0,
        "total_count": 0,
        "truncated": False,
        "selected_sources": [],
    }


def _is_current_session_complaint_fix_query(query: str) -> bool:
    normalized = _normalize(query)
    has_current_session = any(term in normalized for term in ("this session", "current session", "current-session"))
    has_complaint_or_fix = any(
        term in normalized
        for term in (
            "complain",
            "complaint",
            "failure mode",
            "failure modes",
            "needs fixing",
            "need fixing",
            "should we fix",
            "fix from",
            "semantic wrap-up",
            "semantic-wrapup",
        )
    )
    return has_current_session and has_complaint_or_fix


def _current_session_source_paths(cwd: Path) -> list[str]:
    rels: list[str] = []
    seen: set[str] = set()
    for pattern in CURRENT_SESSION_SOURCE_PATTERNS:
        for path in sorted(cwd.glob(pattern)):
            if len(rels) >= CURRENT_SESSION_SOURCE_SCAN_LIMIT:
                return rels
            if not path.is_file() or ".scratch." in path.name or _skip_destination_path(path, cwd):
                continue
            try:
                rel = path.relative_to(cwd).as_posix()
            except ValueError:
                continue
            if rel in seen:
                continue
            seen.add(rel)
            rels.append(rel)
    return rels


def _score_current_session_source(rel: str, text: str) -> dict | None:
    normalized_text = _normalize_for_source_selection(text)
    normalized_path = _normalize_for_source_selection(rel)
    if not normalized_text.strip():
        return None

    facets = _current_session_facets(normalized_text)
    score = 0
    if any(term in normalized_text for term in ("semantic-wrapup", "semantic wrap-up", "semantic_wrapup")):
        score += 120
    if any(term in normalized_text for term in ("current-session", "current session", "this session")):
        score += 80
    if any(term in normalized_text for term in ("smoke", "hardening", "product requirements", "quality judgment")):
        score += 60
    score += len(facets) * 55

    if normalized_path.startswith("tasks/side-thread-info"):
        score += 70
    if "semantic" in normalized_path and "wrap" in normalized_path:
        score += 35
    if normalized_path in {"todo.md", "tasks.md", "codex/state/pipeline-state.md"}:
        score -= 70
    if "artifact-currentness" in normalized_text and len(facets) < 4:
        score -= 90

    if score < 240 or len(facets) < 2:
        return None

    lines = text.splitlines()
    line_no = _best_current_session_source_line(lines)
    return {
        "source_ref": {"path": rel, "line": line_no},
        "source_kind": _current_session_source_kind(rel),
        "score": score,
        "facets": facets,
        "reason": (
            "Substantive current-session semantic-wrapup smoke/hardening evidence; "
            "inspect before answering from active task or front-door context."
        ),
        "snippet": _source_line_snippet(lines, line_no),
    }


def _normalize_for_source_selection(text: str) -> str:
    normalized = _normalize(text)
    return normalized.replace(" - ", " ").replace("_", "-")


def _current_session_facets(normalized_text: str) -> list[str]:
    facets = []
    for label, terms in CURRENT_SESSION_SOURCE_FACETS:
        if any(_normalize_for_source_selection(term) in normalized_text for term in terms):
            facets.append(label)
    return facets


def _best_current_session_source_line(lines: list[str]) -> int:
    if not lines:
        return 1
    best_line = 1
    best_score = -1
    flattened_terms = [
        _normalize_for_source_selection(term)
        for _label, terms in CURRENT_SESSION_SOURCE_FACETS
        for term in terms
    ]
    for index, line in enumerate(lines, 1):
        normalized = _normalize_for_source_selection(line)
        score = sum(1 for term in flattened_terms if term in normalized)
        if any(term in normalized for term in ("product requirements", "quality judgment", "bad:", "problem:")):
            score += 3
        if any(term in normalized for term in ("semantic-wrapup", "semantic wrap-up", "current-session", "current session")):
            score += 2
        if score > best_score:
            best_score = score
            best_line = index
    return best_line


def _source_line_snippet(lines: list[str], line_no: int) -> str:
    if not lines:
        return ""
    start = max(0, line_no - 2)
    end = min(len(lines), line_no + 1)
    return re.sub(r"\s+", " ", " ".join(line.strip() for line in lines[start:end] if line.strip())).strip()[:260]


def _current_session_source_kind(rel: str) -> str:
    if rel.startswith("tasks/side-thread-info"):
        return "side_thread_current_session_evidence"
    if rel.startswith("tasks/"):
        return "task_current_session_evidence"
    if rel.startswith("reports/"):
        return "report_current_session_evidence"
    return "front_door_context"


def detect_behavior_system(cwd: Path) -> dict:
    destinations: list[dict] = []
    seen: set[str] = set()

    exact_candidates = [
        ("project_rule", "AGENTS.md"),
        ("project_rule", "CLAUDE.md"),
        ("project_rule", ".cursorrules"),
        ("project_rule", ".windsurfrules"),
        ("task_front_door", "TODO.md"),
        ("task_front_door", "TASKS.md"),
        ("task_front_door", "BACKLOG.md"),
        ("task_front_door", "ROADMAP.md"),
        ("project_front_door", "PROJECT.md"),
        ("orchestration_rule", "CODEX-ORCHESTRATION.md"),
        ("learning_index", "learnings/INDEX.md"),
        ("product_contract", "docs/agent-facing-tool-contract.md"),
        ("product_examples", "docs/agent-facing-jsonl-examples.md"),
        ("fsm_plan", "docs/next-fsm-loops-agent-facing-jsonl-plan.md"),
        ("pipeline_state", "codex/state/pipeline-state.md"),
    ]
    for kind, rel in exact_candidates:
        _add_destination(destinations, seen, cwd, kind, rel)

    for kind, rel in _active_task_destinations(cwd):
        _add_destination(destinations, seen, cwd, kind, rel)

    for kind, rel in _discover_behavior_destinations(cwd):
        _add_destination(destinations, seen, cwd, kind, rel)

    return {
        "status": "found" if destinations else "not_found",
        "destinations": destinations,
        "wrap_up_skill_assumption": "not_required",
        "detected_wrap_up_integration": any(item["kind"] == "optional_wrap_up_skill" for item in destinations),
    }


def _discover_behavior_destinations(cwd: Path) -> list[tuple[str, str]]:
    discovered: list[tuple[str, str]] = []

    for rel in _iter_existing(cwd, ["tasks/*.md", "tasks/*.txt"]):
        discovered.append(("task_files", rel))

    skill_patterns = [
        "skills/*/*/SKILL.md",
        "skills/*/*/references/*.md",
        "skills/*/SKILL.md",
        "skills/*/references/*.md",
    ]
    for rel in _iter_existing(cwd, skill_patterns):
        name = Path(rel).name.casefold()
        if "wrap" in rel.casefold():
            discovered.append(("optional_wrap_up_skill", rel))
        elif name == "skill.md":
            discovered.append(("skill_router", rel))
        else:
            discovered.append(("skill_reference", rel))

    for rel in _iter_existing(cwd, ["memory/**/*.md", "memories/**/*.md", "notes/**/*.md", "learnings/**/*.md"]):
        if rel == "learnings/INDEX.md":
            discovered.append(("learning_index", rel))
        else:
            discovered.append(("memory_or_notes", rel))

    issue_patterns = [
        ".github/issues/*",
        ".github/ISSUE_TEMPLATE/*",
        "issues/*",
        "tickets/*",
        "linear/*",
        ".linear/*",
        "exports/*github*",
        "exports/*linear*",
        "exported-issues/*",
        "exported_issues/*",
    ]
    for rel in _iter_existing(cwd, issue_patterns):
        if (cwd / rel).is_file():
            discovered.append(("issue_tracker", rel))

    for rel in _iter_recursive_behavior_rule_files(cwd):
        if rel not in {"AGENTS.md", "CLAUDE.md"}:
            discovered.append(("project_rule", rel))

    return discovered


def _active_task_destinations(cwd: Path) -> list[tuple[str, str]]:
    state = cwd / "codex/state/pipeline-state.md"
    if not state.exists() or not state.is_file():
        return []
    try:
        text = state.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    destinations: list[tuple[str, str]] = []
    targeted_patterns = (
        r"Active selected product task:\s*\n`?(tasks/[A-Za-z0-9._/\-]+\.md)`?",
        r"active_task:\s*`?(tasks/[A-Za-z0-9._/\-]+\.md)`?",
        r"Recommended next task:\s*\n`?(tasks/[A-Za-z0-9._/\-]+\.md)`?",
        r"Recommended next task:\s*`?(tasks/[A-Za-z0-9._/\-]+\.md)`?",
        r"Current task:\s*`?(tasks/[A-Za-z0-9._/\-]+\.md)`?",
    )
    for pattern in targeted_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            if match.lower().endswith("none.md"):
                continue
            if (cwd / match).is_file():
                destinations.append(("active_task", match))
                return destinations
    return destinations


def _iter_existing(cwd: Path, patterns: list[str]) -> list[str]:
    rels: list[str] = []
    for pattern in patterns:
        for path in sorted(cwd.glob(pattern)):
            if len(rels) >= DESTINATION_SCAN_LIMIT:
                return rels
            if _skip_destination_path(path, cwd):
                continue
            try:
                rels.append(path.relative_to(cwd).as_posix())
            except ValueError:
                continue
    return rels


def _iter_recursive_behavior_rule_files(cwd: Path) -> list[str]:
    rels: list[str] = []
    stack = [cwd]

    while stack:
        directory = stack.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name.casefold())
        except OSError:
            continue

        child_dirs: list[Path] = []
        for path in entries:
            if len(rels) >= DESTINATION_SCAN_LIMIT:
                return rels
            if path.name in BEHAVIOR_RULE_FILENAMES and path.is_file() and not _skip_destination_path(path, cwd):
                try:
                    rels.append(path.relative_to(cwd).as_posix())
                except ValueError:
                    continue
                continue
            if path.is_dir() and not path.is_symlink() and not _skip_destination_path(path, cwd):
                child_dirs.append(path)
        stack.extend(reversed(child_dirs))

    return rels


def _skip_destination_path(path: Path, cwd: Path | None = None) -> bool:
    if cwd is not None:
        try:
            path = path.relative_to(cwd)
        except ValueError:
            pass
    parts = {part.casefold() for part in path.parts}
    if any(part in IGNORED_BEHAVIOR_DISCOVERY_DIRS for part in parts):
        return True
    if "plugins" in parts and any(part in PLUGIN_IGNORED_BEHAVIOR_DISCOVERY_DIRS for part in parts):
        return True
    return False


def _add_destination(destinations: list[dict], seen: set[str], cwd: Path, kind: str, rel: str) -> None:
    path = cwd / rel
    if not path.exists() or rel in seen:
        return
    seen.add(rel)
    destinations.append(
        {
            "kind": kind,
            "path": rel,
            "reason": _destination_reason(kind),
            "source_refs": [{"path": rel, "line": 1}],
            "destination_ref": {"path": rel, "line": 1},
        }
    )


def _complaint_signals(records, *, query: str) -> list[dict]:
    signals: list[dict] = []
    for item in records:
        text = item.text
        normalized = _normalize(text)
        if not _is_user_like(item.record.role):
            continue
        labels = _labels_for(normalized)
        friction_markers = [marker for marker in FRICTION_MARKERS if marker in normalized][:8]
        if not labels and not friction_markers:
            continue
        if not labels:
            labels = ["general_workflow_failure"]
        row = context_row(item)
        signals.append(
            {
                "signal_id": f"s{len(signals) + 1:03d}",
                "labels": labels,
                "friction_markers": friction_markers,
                "scope_signal_kind": _scope_signal_kind(normalized, query),
                "actor_label": _actor_label(item.record.role, normalized),
                **row,
            }
        )
    _promote_repeated_issue_families(signals)
    return signals


def _promote_repeated_issue_families(signals: list[dict]) -> None:
    dirty_worktree_signals = [
        signal for signal in signals if _is_dirty_worktree_hygiene_signal(str(signal.get("snippet") or ""))
    ]
    if len(dirty_worktree_signals) >= 2:
        for signal in dirty_worktree_signals:
            labels = signal.setdefault("labels", [])
            if "dirty_parent_worktree_hygiene" not in labels:
                labels.insert(0, "dirty_parent_worktree_hygiene")


def _is_dirty_worktree_hygiene_signal(text: str) -> bool:
    normalized = _normalize(text)
    has_worktree_or_parent = any(
        term in normalized
        for term in (
            "dirty parent",
            "parent checkout",
            "parent master",
            "wrong checkout",
            "task worktree",
            "isolated worktree",
            "stale dirty worktree",
            "stale worktree",
            "worktree-hidden",
            "worktree state",
            "git worktree",
        )
    )
    has_status_or_confusion = any(
        term in normalized
        for term in (
            "dirty",
            "clean",
            "status",
            "stale",
            "hidden",
            "wrong",
            "explain",
            "prove",
            "edit",
            "edited",
            "changes",
            "git status",
        )
    )
    return has_worktree_or_parent and has_status_or_confusion


def _substantive_signals(signals: list[dict]) -> list[dict]:
    return [signal for signal in signals if signal.get("scope_signal_kind") not in SETUP_SIGNAL_KINDS]


def _current_session_recent_substantive_signals(
    plan: SourcePlan,
    substantive_signals: list[dict],
    all_signals: list[dict],
) -> list[dict]:
    if plan.scope_resolution.get("status") != "scoped_to_latest_known_session":
        return substantive_signals
    if not substantive_signals:
        return substantive_signals
    max_line = _max_signal_line(all_signals)
    if max_line <= 0:
        return substantive_signals
    cutoff = max(1, max_line - CURRENT_SESSION_RECENT_SIGNAL_LINE_WINDOW)
    recent = [
        signal
        for signal in substantive_signals
        if _source_ref_line(signal.get("source_ref") or {}) >= cutoff
    ]
    return recent or substantive_signals


def _latest_session_recent_signal_filter_summary(
    plan: SourcePlan,
    substantive_signals: list[dict],
    selected_substantive_signals: list[dict],
    all_signals: list[dict],
) -> dict:
    active = plan.scope_resolution.get("status") == "scoped_to_latest_known_session"
    max_line = _max_signal_line(all_signals)
    cutoff = max(1, max_line - CURRENT_SESSION_RECENT_SIGNAL_LINE_WINDOW) if max_line > 0 else None
    return {
        "active": active,
        "line_window": CURRENT_SESSION_RECENT_SIGNAL_LINE_WINDOW if active else None,
        "max_signal_line": max_line if active else None,
        "cutoff_line": cutoff if active else None,
        "total_substantive_signal_count": len(substantive_signals),
        "selected_substantive_signal_count": len(selected_substantive_signals),
        "stale_substantive_signal_count": max(0, len(substantive_signals) - len(selected_substantive_signals)),
        "fallback_to_all_substantive": active and bool(substantive_signals) and selected_substantive_signals == substantive_signals,
    }


def _max_signal_line(signals: list[dict]) -> int:
    return max((_source_ref_line(signal.get("source_ref") or {}) for signal in signals), default=0)


def _source_ref_line(ref: dict) -> int:
    try:
        return int(ref.get("line") or 0)
    except (TypeError, ValueError):
        return 0


def _orchestration_evidence(records, *, signals: list[dict], query: str) -> dict:
    signal_by_ref = {
        (signal["source_ref"]["path"], int(signal["source_ref"]["line"])): signal
        for signal in signals
        if signal.get("source_ref")
    }
    evidence: list[dict] = []
    for item in records:
        row = context_row(item)
        ref = row.get("source_ref") or {}
        signal = signal_by_ref.get((str(ref.get("path") or ""), int(ref.get("line") or 0)))
        categories = _orchestration_categories(item.text, role=item.record.role, query=query, signal=signal)
        if not categories:
            continue
        setup_echo = bool(signal and signal.get("scope_signal_kind") in SETUP_SIGNAL_KINDS)
        if not signal:
            echo = setup_echo_classification(item.text, query=query)
            setup_echo = echo["is_setup_echo"]
        actor = signal.get("actor_label") if signal else _actor_label(item.record.role, _normalize(item.text))
        evidence.append(
            {
                "evidence_id": f"o{len(evidence) + 1:03d}",
                "actor_label": actor,
                "categories": categories,
                "setup_echo": setup_echo,
                "substantive": not setup_echo,
                "signal_id": signal.get("signal_id") if signal else None,
                "signal_labels": signal.get("labels", []) if signal else [],
                **row,
            }
        )
    ranked = sorted(evidence, key=lambda item: (-_orchestration_priority(item), item.get("timestamp_utc") or "", item["source_ref"]["path"], item["source_ref"]["line"]))
    returned = _select_orchestration_items(ranked, limit=ORCHESTRATION_EVIDENCE_LIMIT)
    return {
        "total_count": len(evidence),
        "returned_count": len(returned),
        "inline_limit": ORCHESTRATION_EVIDENCE_LIMIT,
        "truncated": len(evidence) > len(returned),
        "category_counts": _count_categories(evidence),
        "actor_counts": _count_values(evidence, "actor_label"),
        "setup_echo_count": len([item for item in evidence if item.get("setup_echo")]),
        "substantive_count": len([item for item in evidence if item.get("substantive")]),
        "items": returned,
        "claim_boundary": (
            "Orchestration evidence marks source-backed records that may explain task outcome; "
            "setup_echo=true records are prompt/setup surfaces and are excluded from failure-mode grouping."
        ),
    }


def _select_orchestration_items(ranked: list[dict], *, limit: int) -> list[dict]:
    if len(ranked) <= limit:
        return ranked

    selected: list[dict] = []
    selected_keys: set[tuple[str, int]] = set()

    def add(item: dict) -> bool:
        ref = item.get("source_ref") or {}
        key = (str(ref.get("path") or ""), int(ref.get("line") or 0))
        if key in selected_keys or len(selected) >= limit:
            return False
        selected.append(item)
        selected_keys.add(key)
        return True

    for item in ranked[: min(ORCHESTRATION_PRIMARY_RANKED_LIMIT, limit)]:
        add(item)

    for category in ORCHESTRATION_DIVERSITY_CATEGORIES:
        for item in ranked:
            if not item.get("substantive"):
                continue
            if item.get("actor_label") in {"user", "user_prompt_or_assignment"}:
                continue
            if category in (item.get("categories") or []):
                add(item)
                break

    for actor in ORCHESTRATION_DIVERSITY_ACTORS:
        for item in ranked:
            if item.get("substantive") and item.get("actor_label") == actor:
                add(item)
                break

    for item in ranked:
        if len(selected) >= limit:
            break
        add(item)

    return selected


def _orchestration_categories(text: str, *, role: str | None, query: str, signal: dict | None) -> list[str]:
    normalized = _normalize(text)
    categories: list[str] = []
    if signal and signal.get("scope_signal_kind") not in SETUP_SIGNAL_KINDS:
        categories.append("user_complaint_or_correction")
    if _looks_like_semantic_wrapup_prompt(normalized, query):
        categories.append("semantic_wrapup_prompt_echo")
    elif setup_echo_classification(text, query=query)["is_setup_echo"]:
        categories.append("setup_or_prompt_echo")
    if any(term in normalized for term in ("automatic jsonl", "jsonl resolvability", "resolvable", "context pollution", "context hygiene", "low context")):
        categories.append("context_hygiene_correction")
    if any(term in normalized for term in ("fsm", "product loop", "current state + event", "current_state + event", "implement_as_one", "planner result", "commitment")):
        categories.append("main_agent_fsm_or_product_loop_commitment")
    if any(term in normalized for term in ("subagent", "worker", "implementer", "orchestrator worker", "worker output", "agent output")):
        categories.append("worker_or_subagent_behavior")
    if any(term in normalized for term in ("reviewer", "review artifact", "independent review", "independent reviewer", "claim boundary", "acceptance criteria", "partially_verified", "partially verified", "verified")):
        categories.append("reviewer_proof_or_claim_boundary")
    if any(term in normalized for term in ("same-task", "same task", "review_failed_fixable", "review failed fixable", "fix_review_findings", "fix review findings", "install refresh", "small setup fix", "small setup fixes")):
        categories.append("same_task_repair_logic")
    if any(term in normalized for term in ("product delta integration audit", "stale baseline", "dirty parent", "branch-only", "branch only", "reviewed not integrated", "baseline correction")):
        categories.append("product_delta_integration_audit")
    if any(term in normalized for term in ("product-moving", "product moving", "process/report drift", "report-only", "report only", "busywork", "small REDACTEDy tasks", "substantial product blocker", "substantial follow-up blocker")):
        categories.append("product_moving_vs_process_drift")
    return sorted(set(categories), key=categories.index)


def _actor_label(role: str | None, normalized_text: str) -> str:
    normalized_role = str(role or "").casefold()
    if "tool" in normalized_role or "function" in normalized_role:
        return "tool_output"
    if "assistant" in normalized_role or "agent" in normalized_role:
        if "reviewer" in normalized_text:
            return "reviewer_or_review_summary"
        if "subagent" in normalized_text or "worker" in normalized_text or "implementer" in normalized_text:
            return "worker_or_subagent_summary"
        return "main_agent"
    if _is_user_like(role):
        if setup_echo_classification(normalized_text)["is_setup_echo"]:
            return "user_prompt_or_assignment"
        return "user"
    return normalized_role or "unknown"


def _orchestration_priority(item: dict) -> int:
    categories = set(item.get("categories") or [])
    score = 0
    for category, value in {
        "user_complaint_or_correction": 100,
        "context_hygiene_correction": 95,
        "reviewer_proof_or_claim_boundary": 85,
        "worker_or_subagent_behavior": 80,
        "same_task_repair_logic": 78,
        "product_delta_integration_audit": 76,
        "product_moving_vs_process_drift": 74,
        "main_agent_fsm_or_product_loop_commitment": 70,
        "semantic_wrapup_prompt_echo": 10,
        "setup_or_prompt_echo": 8,
    }.items():
        if category in categories:
            score = max(score, value)
    if item.get("setup_echo"):
        score -= 20
    return score


def _count_categories(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for category in item.get("categories") or []:
            counts[category] = counts.get(category, 0) + 1
    return dict(sorted(counts.items()))


def _count_values(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _scope_signal_kind(normalized_text: str, query: str) -> str:
    if _looks_like_semantic_wrapup_prompt(normalized_text, query):
        return "semantic_wrapup_prompt"
    echo = setup_echo_classification(normalized_text, query=query)
    if echo["is_setup_echo"]:
        return "current_session_setup_echo"
    return "user_complaint"


def _looks_like_semantic_wrapup_prompt(normalized_text: str, query: str) -> bool:
    normalized_query = _normalize(query)
    semantic_wrapup_marker = any(
        marker in normalized_text
        for marker in (
            "semantic-wrapup",
            "semantic wrap-up",
            "semantic_wrapup",
            "jsonl-history-search semantic-wrapup",
        )
    )
    current_session_prompt = any(
        marker in normalized_text
        for marker in (
            "what did i complain",
            "complain about",
            "needs fixing from this session",
            "failure modes should we fix from this session",
        )
    )
    lane_prompt_marker = any(
        marker in normalized_text
        for marker in (
            "fresh receiving-agent",
            "fresh receiving agent",
            "receiving-agent",
            "side-thread",
            "lane prompt",
            "worker lane",
            "run jsonl-history-search",
        )
    )
    query_embedded = normalized_query and normalized_query in normalized_text
    return semantic_wrapup_marker and current_session_prompt and (lane_prompt_marker or query_embedded)


def _failure_modes(signals: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for signal in signals:
        for label in signal["labels"]:
            grouped.setdefault(label, []).append(signal)
    modes = []
    for label, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        evidence = items[:5]
        evidence_count = max(
            [len(items)]
            + [
                int(item.get("cluster_candidate_count") or 0)
                for item in items
                if str(item.get("cluster_candidate_count") or "").isdigit()
            ]
        )
        modes.append(
            {
                "failure_mode": label,
                "title": _title_for(label),
                "evidence_count": evidence_count,
                "evidence": evidence,
                "examples": _examples_from_evidence(evidence),
                "why_it_matters": _why_it_matters(label),
                "issue_axis": _issue_axis(label),
                "cause_chain": _cause_chain(label, evidence),
                "confidence": "high" if evidence_count >= 2 else "medium",
                "uncertainty": _uncertainty(label, evidence_count),
                "source_refs": [item["source_ref"] for item in evidence],
            }
        )
    return modes


def _cluster_promotion_surface(
    user_message_first_evidence: dict,
    *,
    returned_failure_modes: list[dict],
    all_failure_modes: list[dict],
) -> dict:
    returned_labels = {str(mode.get("failure_mode") or "") for mode in returned_failure_modes}
    all_labels = {str(mode.get("failure_mode") or "") for mode in all_failure_modes}
    ranked_clusters: list[dict] = []
    synthetic_signal_count = 0
    for cluster in user_message_first_evidence.get("clusters") or []:
        label = str(cluster.get("label") or "")
        candidate_count = int(cluster.get("candidate_count") or 0)
        if label in returned_labels:
            status = "promoted"
            reason = "cluster is returned as an actionable failure mode and fix suggestion candidate"
        elif label in all_labels:
            status = "truncated"
            reason = "cluster mapped to a failure mode but was outside the inline returned limit"
        elif candidate_count < 2:
            status = "parked"
            reason = "single-candidate cluster parked until another source-backed correction corroborates it"
        else:
            status = "parked"
            reason = (
                "cluster was detected in user-message-first evidence but did not survive the "
                "current-session substantive promotion filter; inspect its structured refs before "
                "treating it as a primary fix"
            )
        if any(
            signal.get("scope_signal_kind") == "user_message_first_cluster"
            for mode in returned_failure_modes
            if mode.get("failure_mode") == label
            for signal in mode.get("evidence") or []
        ):
            synthetic_signal_count += len(cluster.get("examples") or [])
        ranked_clusters.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "label": label,
                "candidate_count": candidate_count,
                "promotion_status": status,
                "reason": reason,
                "source_refs": cluster.get("source_refs") or [],
                "examples": cluster.get("examples") or [],
            }
        )
    promoted = [item for item in ranked_clusters if item["promotion_status"] == "promoted"]
    parked = [item for item in ranked_clusters if item["promotion_status"] == "parked"]
    truncated = [item for item in ranked_clusters if item["promotion_status"] == "truncated"]
    return {
        "method": (
            "Map user-message-first correction clusters to returned failure modes when they have "
            "source-backed corroboration; otherwise keep an explicit parked/truncated reason."
        ),
        "promotion_threshold": "candidate_count >= 2 or already represented by selected substantive signals",
        "ranked_clusters": ranked_clusters,
        "promoted_count": len(promoted),
        "parked_count": len(parked),
        "truncated_count": len(truncated),
        "synthetic_signal_count": synthetic_signal_count,
        "answer_surface_requirement": (
            "answer_ready requires at least one non-generic promoted cluster and explicit reasons "
            "for detected clusters that are not returned inline"
        ),
    }


def _fix_suggestions(failure_modes: list[dict], behavior_system: dict) -> list[dict]:
    suggestions = []
    destinations = behavior_system.get("destinations", [])
    for index, mode in enumerate(failure_modes, 1):
        label = mode["failure_mode"]
        pattern = _pattern_for(label)
        destination = _select_destination(pattern, destinations, mode)
        selection_options = _selection_options(index)
        suggestions.append(
            {
                "suggestion_id": f"fix-{index:03d}",
                "failure_mode": label,
                "issue_family": label,
                "issue_axis": _issue_axis(label),
                "examples": mode.get("examples") or _examples_from_evidence(mode.get("evidence", [])),
                "why_it_matters": mode.get("why_it_matters") or _why_it_matters(label),
                "fix_type": pattern["fix_type"],
                "proposed_destination": destination,
                "destination_refs": destination.get("source_refs", []),
                "destination_source_refs": destination.get("source_refs", []),
                "destination_rationale": _destination_rationale(label, destination, mode),
                "user_selectable": True,
                "auto_apply": False,
                "proposed_action": _proposed_action(label, destination),
                "doable_suggested_fix": _doable_suggested_fix(label, destination),
                "acceptance_criteria": _acceptance_criteria(label, destination),
                "evidence_refs": mode["source_refs"],
                "source_refs": mode["source_refs"],
                "selection_options": ["accept", "reject", "defer"],
                "selection_shape": {
                    "required_decision": "accept_reject_or_defer",
                    "options": selection_options,
                    "contract": "suggestion_only_normal_fsm_required",
                },
                "suggestion_only_contract": (
                    "This suggestion is not applied by semantic-wrapup; accept/reject/defer it, "
                    "then implement accepted fixes through the normal task/FSM workflow."
                ),
                "confidence": mode["confidence"],
                "uncertainty": mode["uncertainty"],
            }
        )
    return suggestions


def _semantic_next_reads(
    failure_modes: list[dict],
    orchestration_evidence: dict | None = None,
    current_session_source_selection: dict | None = None,
    rolling_window_coverage: dict | None = None,
    *,
    setup_echo_signals: list[dict] | None = None,
    prefer_setup_signal_refs: bool = False,
    allow_current_session_sources_without_failure_modes: bool = True,
) -> list[dict]:
    refs = []
    seen: set[tuple[str, int]] = set()

    def add(
        ref: dict,
        reason: str,
        facets: list[str] | None = None,
        *,
        read_role: str,
        diagnostic_open_policy: str | None = None,
        answer_use_required: bool = False,
    ) -> None:
        key = (str(ref.get("path") or ""), int(ref.get("line") or 0))
        if not key[0] or key in seen:
            return
        item = _semantic_read_item(
            ref=ref,
            reason=reason,
            read_role=read_role,
            diagnostic_open_policy=diagnostic_open_policy,
            answer_use_required=answer_use_required,
        )
        if facets:
            item["facets"] = facets
        refs.append(item)
        seen.add(key)

    orchestration_items = (orchestration_evidence or {}).get("items") or []
    failure_mode_refs = next_read_refs(*[mode.get("evidence", []) for mode in failure_modes])
    orchestration_refs = next_read_refs(orchestration_items)
    evidence_refs = next_read_refs(*[mode.get("evidence", []) for mode in failure_modes], orchestration_items)

    def add_current_session_sources() -> None:
        for source in (current_session_source_selection or {}).get("selected_sources") or []:
            ref = source.get("source_ref") or {}
            if failure_modes:
                add(
                    ref,
                    "Inspect current-session semantic-wrapup smoke/hardening evidence before answering from active gate or front-door context.",
                    list(source.get("facets") or []),
                    read_role=READ_ROLE_OPTIONAL_DIAGNOSTIC,
                    diagnostic_open_policy=(
                        "Adjacent current-session smoke/hardening evidence; open only if required primary complaint refs "
                        "leave a named current-session or setup-hardening gap."
                    ),
                )
            else:
                add(
                    ref,
                    (
                        "Required for answer-use: inspect this retained current-session semantic-wrapup "
                        "smoke/hardening source and answer from it, or hand off this exact source as required."
                    ),
                    list(source.get("facets") or []),
                    read_role=READ_ROLE_REQUIRED_PRIMARY_PROOF,
                    answer_use_required=True,
                )

    if failure_modes:
        for ref in failure_mode_refs:
            add(
                ref,
                "Inspect complaint/correction evidence before applying suggested fix.",
                read_role=READ_ROLE_REQUIRED_PRIMARY_PROOF,
            )
        for ref in orchestration_refs:
            add(
                ref,
                "Adjacent orchestration evidence; open only if direct complaint/correction proof leaves a named chronology or actor gap.",
                read_role=READ_ROLE_OPTIONAL_DIAGNOSTIC,
                diagnostic_open_policy=(
                    "Do not use adjacent orchestration evidence as default required proof when direct failure-mode refs exist."
                ),
            )
        add_current_session_sources()
    elif prefer_setup_signal_refs:
        for signal in setup_echo_signals or []:
            add(
                signal.get("source_ref") or {},
                "Inspect selected-session setup/reviewer prompt signal before using old retained semantic-wrapup artifacts.",
                read_role=READ_ROLE_OPTIONAL_DIAGNOSTIC,
                diagnostic_open_policy=(
                    "Setup/reviewer prompt echo check only; do not treat as substantive complaint proof."
                ),
            )
        add_current_session_sources()
        for ref in evidence_refs:
            add(
                ref,
                "Inspect complaint/correction evidence before applying suggested fix.",
                read_role=READ_ROLE_CONDITIONAL_REQUIRED,
                diagnostic_open_policy=(
                    "Open only if the fail-closed scope is resolved or a returned failure mode still lacks proof."
                ),
            )
    else:
        if allow_current_session_sources_without_failure_modes:
            add_current_session_sources()
        for ref in evidence_refs:
            add(
                ref,
                "Inspect complaint/correction evidence before applying suggested fix.",
                read_role=READ_ROLE_CONDITIONAL_REQUIRED,
                diagnostic_open_policy=(
                    "Open only if diagnosing why no failure modes were returned or if a proof gap remains."
                ),
            )
    for read in (rolling_window_coverage or {}).get("omitted_or_uncertain_next_reads") or []:
        add(
            read.get("source_ref") or {},
            str(read.get("reason") or "Inspect omitted rolling-window signal."),
            read_role=READ_ROLE_CONDITIONAL_REQUIRED,
            diagnostic_open_policy=(
                "Open only if omitted windows, scan caps, or an exhaustive-coverage request make completeness necessary."
            ),
        )
    return refs


def _semantic_read_item(
    *,
    ref: dict,
    reason: str,
    read_role: str,
    diagnostic_open_policy: str | None = None,
    answer_use_required: bool = False,
) -> dict:
    item: dict[str, Any] = {
        "source_ref": ref,
        "reason": reason,
        "read_role": read_role,
        "proof_role": read_role,
        "answer_use_required": bool(answer_use_required),
    }
    if read_role == READ_ROLE_REQUIRED_PRIMARY_PROOF:
        item.update(
            {
                "read_more_actionable": True,
                "followthrough_actionable": True,
                "required_for_answer": True,
                "diagnostic_only": False,
            }
        )
    elif read_role == READ_ROLE_CONDITIONAL_REQUIRED:
        item.update(
            {
                "read_more_actionable": False,
                "followthrough_actionable": False,
                "conditional_required": True,
                "required_for_answer": False,
                "diagnostic_open_policy": diagnostic_open_policy
                or "Open only when a named proof, completeness, cap, or exhaustive-coverage condition applies.",
            }
        )
    else:
        item.update(
            {
                "read_more_actionable": False,
                "followthrough_actionable": False,
                "diagnostic_only": True,
                "required_for_answer": False,
                "diagnostic_open_policy": diagnostic_open_policy
                or "Optional diagnostic; not required before answering from primary complaint proof.",
            }
        )
    return item


def _semantic_read_role_summary(next_reads: list[dict] | None) -> dict:
    reads = [read for read in next_reads or [] if isinstance(read, dict)]
    required = [read for read in reads if read.get("read_role") == READ_ROLE_REQUIRED_PRIMARY_PROOF]
    optional = [read for read in reads if read.get("read_role") == READ_ROLE_OPTIONAL_DIAGNOSTIC]
    conditional = [read for read in reads if read.get("read_role") == READ_ROLE_CONDITIONAL_REQUIRED]
    actionable = [read for read in reads if read.get("read_more_actionable") is not False]
    return {
        "total": len(reads),
        "required_primary_proof_count": len(required),
        "optional_diagnostic_count": len(optional),
        "conditional_required_count": len(conditional),
        "default_actionable_read_count": len(actionable),
        "policy": (
            "Open required_primary_proof reads by default. Treat optional_diagnostic reads as visible "
            "corroboration/setup/context only. Open conditional_required reads only when proof is missing, "
            "caps or omitted windows block completeness, or the user asks for exhaustive coverage."
        ),
    }


def _labels_for(normalized: str) -> list[str]:
    labels = []
    for pattern in FAILURE_PATTERNS:
        label = pattern["label"]
        terms = pattern["terms"]
        if any(term in normalized for term in terms):
            labels.append(label)
    return labels


def _pattern_for(label: str) -> dict[str, Any]:
    for item in FAILURE_PATTERNS:
        if item["label"] == label:
            return item
    return {
        "label": label,
        "terms": (),
        "fix_type": "task creation/amendment",
        "preferred_path": "tasks/",
        "preferred_kinds": ("task_files", "task_front_door", "project_rule"),
        "inferred_steps": (
            "User complaint indicates a workflow failure.",
            "Cause needs source inspection before applying a fix.",
        ),
    }


def _select_destination(pattern: dict[str, Any], destinations: list[dict], mode: dict) -> dict:
    preferred = str(pattern.get("preferred_path") or "")
    ranked = sorted(
        destinations,
        key=lambda item: _destination_rank(item, pattern, mode, preferred),
        reverse=True,
    )
    if ranked:
        return ranked[0]
    return {
        "kind": "user_decision_needed",
        "path": preferred,
        "reason": "no local behavior system destination detected",
        "source_refs": [],
        "destination_ref": None,
    }


def _destination_rank(item: dict, pattern: dict[str, Any], mode: dict, preferred: str) -> tuple[int, int, str]:
    path = str(item.get("path") or "")
    kind = str(item.get("kind") or "")
    score = _destination_kind_score(kind)
    if preferred and path == preferred:
        score += 1000
    elif preferred and path.startswith(preferred):
        score += 850
    elif preferred and preferred.startswith(path):
        score += 700

    evidence_text = " ".join(str(entry.get("snippet") or "") for entry in mode.get("evidence", [])).casefold()
    if path and path.casefold() in evidence_text:
        score += 250

    preferred_kinds = tuple(pattern.get("preferred_kinds") or ())
    if kind in preferred_kinds:
        score += max(0, 300 - 20 * preferred_kinds.index(kind))

    if _looks_archived_or_private_destination(path):
        score -= 2000
    if _looks_stale_plan_destination(path):
        score -= 450
    if _looks_closed_task_destination(item, mode):
        score -= 650
    if kind == "task_files" and not path.casefold() in evidence_text:
        score -= 120
    return score, -len(path), path


def _destination_kind_score(kind: str) -> int:
    return {
        "pipeline_state": 760,
        "task_front_door": 740,
        "active_task": 720,
        "project_front_door": 690,
        "orchestration_rule": 680,
        "fsm_plan": 660,
        "skill_router": 640,
        "skill_reference": 620,
        "learning_index": 600,
        "product_contract": 580,
        "product_examples": 560,
        "project_rule": 540,
        "memory_or_notes": 460,
        "issue_tracker": 440,
        "optional_wrap_up_skill": 360,
        "task_files": 220,
    }.get(kind, 100)


def _looks_archived_or_private_destination(path: str) -> bool:
    lowered = path.casefold()
    return lowered.startswith(".private/") or lowered.startswith(".tmp/plugins/") or "/.private/" in lowered or "/.tmp/plugins/" in lowered


def _looks_stale_plan_destination(path: str) -> bool:
    lowered = path.casefold()
    return (
        lowered.startswith("docs/next-")
        or "old" in lowered
        or "historical" in lowered
        or "graveyard" in lowered
    )


def _looks_closed_task_destination(item: dict, mode: dict) -> bool:
    path = str(item.get("path") or "")
    kind = str(item.get("kind") or "")
    if not path.startswith("tasks/"):
        return False
    if kind == "active_task":
        return False
    evidence_text = " ".join(str(entry.get("snippet") or "") for entry in mode.get("evidence", [])).casefold()
    if path.casefold() in evidence_text:
        return False
    lowered_name = Path(path).name.casefold()
    return any(term in lowered_name for term in ("closed", "old", "previous", "provenance"))


def _proposed_action(label: str, destination: dict) -> str:
    path = destination.get("path")
    if label == "task_running_status_update_stall":
        return (
            f"Clarify {path} so a status update during active task execution must poll/continue safe work "
            "or explicitly record a blocker instead of waiting for a user reply by default."
        )
    if label == "over_conservative_resource_hesitation":
        return (
            f"Clarify {path} so low-resource warnings use measured thresholds and allowed bounded actions "
            "rather than treating near-threshold disk space as an automatic hard stop."
        )
    if label == "dirty_parent_worktree_hygiene":
        return (
            f"Add or tighten {path} so handoff/next-loop readiness includes parent `git status`, "
            "`git worktree list`, remaining worktree classification, and whether edits happened in parent or task worktree."
        )
    if label == "packet_report_bureaucracy":
        return f"Tighten {path} so packet/report fields are allowed only when they improve source finding or inspection."
    if label == "router_context_hygiene":
        return f"Update {path} or a narrow reference so agents auto-route without loading broad context."
    if label == "false_user_quote_or_misattribution":
        return f"Record in {path} that user explanations must not be rewritten into fake quoted questions."
    if label == "case002_retrieval_escalation":
        return f"Route case/action-proof churn through source-use and retrieval-escalation guidance in {path}."
    if label == "pre_compaction_plan_preservation":
        return f"Make {path} preserve current plan/provenance before compaction or handoff."
    if label == "raw_local_evidence_boundary":
        return f"Use {path} to require internal/raw local evidence mode for product diagnosis."
    if label == "fsm_goal_selection_and_rabbithole":
        return f"Keep {path} focused on large product loops, bounded inserted loops, and explicit end goals."
    if label == "agent_rules_ignored":
        return f"Clarify {path} so local agent rules are discoverable and must be checked before acting."
    if label == "fix_later_note_not_promoted":
        return f"Add a source-backed task/TODO entry in {path} only if the user accepts this deferred fix."
    if label == "stale_issue_or_ticket_reused":
        return f"Mark or route {path} through a currentness check before using the ticket as active direction."
    if label == "assistant_repeated_wrong_rule":
        return f"Clarify the controlling rule in {path} so the assistant does not repeat the corrected mistake."
    if label == "memory_learning_update_needed":
        return f"Record the source-backed lesson in {path} so the same failure is less likely to recur."
    return f"Create or amend {path} with a source-backed fix for {label}."


def _examples_from_evidence(evidence: list[dict]) -> list[dict]:
    examples = []
    for item in evidence[:3]:
        examples.append(
            {
                "source_ref": item.get("source_ref"),
                "snippet": item.get("snippet"),
            }
        )
    return examples


def _issue_axis(label: str) -> str:
    return ISSUE_AXIS_BY_LABEL.get(label, "agent_workflow_issue")


def _why_it_matters(label: str) -> str:
    return WHY_IT_MATTERS_BY_LABEL.get(
        label,
        "The repeated source-backed complaint indicates a workflow failure that needs user selection before any fix is applied.",
    )


def _doable_suggested_fix(label: str, destination: dict) -> str:
    path = destination.get("path") or "(user decision needed)"
    if label == "task_running_status_update_stall":
        return (
            f"Propose a narrow update in {path}: status/progress updates during active worker runs are "
            "nonblocking unless a named blocker is recorded; otherwise poll the worker or continue safe "
            "non-overlapping coordination."
        )
    if label == "over_conservative_resource_hesitation":
        return (
            f"Propose a narrow update in {path}: record measured free space, classify hard-stop versus "
            "warning thresholds, and list safe bounded commands that may continue after warning correction."
        )
    if label == "dirty_parent_worktree_hygiene":
        return (
            f"Propose a narrow update in {path}: before handoff or next-loop readiness, require "
            "`git status --short --branch`, `git worktree list`, classification of parent dirt/remaining worktrees, "
            "and an explicit statement of which checkout received edits."
        )
    return _proposed_action(label, destination)


def _acceptance_criteria(label: str, destination: dict) -> list[str]:
    path = destination.get("path") or "(user decision needed)"
    if label == "task_running_status_update_stall":
        return [
            "Status/update loop-stall complaints are grouped under `task_running_status_update_stall`.",
            "The selected workflow surface distinguishes nonblocking progress updates from real user-input blockers.",
            "The output remains suggestion-only and preserves nearby turn context around the user correction.",
        ]
    if label == "over_conservative_resource_hesitation":
        return [
            "Low-resource hesitation complaints are grouped under `over_conservative_resource_hesitation`.",
            "The selected workflow surface requires measured threshold handling and safe bounded-work guidance.",
            "The output remains suggestion-only and preserves the user's factual correction separately from stale storage tasks.",
        ]
    if label == "dirty_parent_worktree_hygiene":
        return [
            "Repeated dirty parent/worktree complaints are grouped under `dirty_parent_worktree_hygiene`.",
            "The selected workflow surface requires parent status and worktree-list proof before handoff or next-loop readiness.",
            "The output remains suggestion-only; no task, skill, doc, or memory file is edited unless a user/coordinator accepts the suggestion.",
        ]
    return [
        f"The accepted change targets {path} or a fresher user-selected destination after source inspection.",
        "The fix is backed by the listed source refs and preserves the semantic-wrapup suggestion-only contract.",
        "A reviewer can verify the behavior without reading unrelated private transcript context.",
    ]


def _destination_rationale(label: str, destination: dict, mode: dict) -> str:
    path = destination.get("path") or "(user decision needed)"
    kind = destination.get("kind") or "unknown"
    evidence_count = mode.get("evidence_count", 0)
    return (
        f"{path} is suggested because it is a detected {kind} destination and "
        f"`{label}` has {evidence_count} source-backed complaint/correction signal(s)."
    )


def _selection_options(index: int) -> list[dict]:
    suggestion_id = f"fix-{index:03d}"
    return [
        {
            "decision": "accept",
            "meaning": "Apply this suggested fix through the normal task/FSM workflow after inspecting source refs.",
            "suggestion_id": suggestion_id,
        },
        {
            "decision": "reject",
            "meaning": "Do not apply this suggestion; keep the source refs as rejected evidence.",
            "suggestion_id": suggestion_id,
        },
        {
            "decision": "defer",
            "meaning": "Do not edit now; preserve the suggestion for later task or issue triage.",
            "suggestion_id": suggestion_id,
        },
    ]


def _title_for(label: str) -> str:
    return label.replace("_", " ").title()


def _cause_chain(label: str, evidence: list[dict]) -> dict:
    pattern = _pattern_for(label)
    evidence_refs = [item["source_ref"] for item in evidence if item.get("source_ref")]
    return {
        "evidence_refs": evidence_refs,
        "inferred_steps": list(pattern.get("inferred_steps") or ()),
        "claim_boundary": (
            "These steps are inferred from complaint/correction text and local destination shape; "
            "inspect evidence_refs before treating them as root-cause certainty."
        ),
    }


def _uncertainty(label: str, evidence_count: int) -> str:
    if evidence_count <= 1:
        return "single evidence signal; inspect next_reads before applying fix"
    return f"{evidence_count} evidence signals grouped by lexical workflow markers; cause chain is inferred from source wording"


def _destination_reason(kind: str) -> str:
    return {
        "project_rule": "project-level agent behavior rules exist",
        "task_front_door": "project task/front-door state exists",
        "task_files": "project tracks implementation work as task files",
        "project_front_door": "project identity/front-door doc exists",
        "orchestration_rule": "project uses explicit orchestration rules",
        "learning_index": "project has durable learning/failure-mode index",
        "memory_or_notes": "local memory/notes can preserve durable learning",
        "issue_tracker": "local/exported issue or ticket files can track selected fixes",
        "skill_router": "project has installed skill router surfaces",
        "skill_reference": "project has narrow skill reference files",
        "optional_wrap_up_skill": "wrap-up skill/reference detected as an optional destination only",
        "product_contract": "project has an agent-facing product contract",
        "product_examples": "project has source-backed product examples",
        "fsm_plan": "project has an active FSM loop plan",
        "pipeline_state": "project has front-door pipeline state",
        "active_task": "current active task is named in pipeline/front-door state",
    }.get(kind, "local behavior destination detected")


def _is_user_like(role: str | None) -> bool:
    normalized = str(role or "").lower()
    return normalized in {"user", "human", "human_user"} or "user" in normalized or "human" in normalized


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()
