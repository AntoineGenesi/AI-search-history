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

SETUP_SIGNAL_KINDS = {"semantic_wrapup_prompt", "current_session_setup_echo"}

FAILURE_PATTERNS: tuple[dict[str, Any], ...] = (
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
        "preferred_path": "docs/next-fsm-loops-agent-facing-jsonl-plan.md",
        "preferred_kinds": ("fsm_plan", "orchestration_rule", "pipeline_state"),
        "inferred_steps": (
            "FSM chooses small/process goals or keeps adding chores.",
            "Substantial product blocker remains unresolved.",
            "User forces loop goal and insertion rules to be explicit.",
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
    "hate",
    "angry",
    "REDACTED",
    "REDACTED",
    "REDACTED",
    "useless",
)


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
    ambiguous_prompt_only_scope = _ambiguous_prompt_only_current_session_scope(plan, signals, query)
    all_failure_modes = [] if ambiguous_prompt_only_scope else _failure_modes(substantive_signals)
    failure_modes = all_failure_modes[:INLINE_FAILURE_MODE_LIMIT]
    orchestration_evidence = _orchestration_evidence(records, signals=signals, query=query)
    behavior_system = detect_behavior_system(cwd)
    all_fix_suggestions = _fix_suggestions(all_failure_modes, behavior_system)
    fix_suggestions = all_fix_suggestions[:INLINE_FAILURE_MODE_LIMIT]
    status = "scope_ambiguous" if ambiguous_prompt_only_scope else _semantic_wrapup_status(plan, all_failure_modes)
    scope_resolution = _scope_resolution(plan, source_plan, query)
    if ambiguous_prompt_only_scope:
        scope_resolution.update(ambiguous_prompt_only_scope)
    payload = {
        "semantic_wrapup": {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "query": query,
            "route": "semantic_wrapup_frustration_mining",
            "summary": {
                "complaint_signal_count": len(signals),
                "substantive_signal_count": len(substantive_signals),
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
                "elapsed_seconds": round(time.monotonic() - started, 6),
            },
            "failure_modes": failure_modes,
            "signal_classification": {
                "substantive_signal_count": len(substantive_signals),
                "setup_echo_signal_count": len(setup_echo_signals),
                "substantive_source_refs": [signal["source_ref"] for signal in substantive_signals[:8]],
                "setup_echo_source_refs": [signal["source_ref"] for signal in setup_echo_signals[:8]],
                "failure_modes_exclude_setup_echoes": True,
            },
            "orchestration_evidence": orchestration_evidence,
            "fix_suggestions": fix_suggestions,
            "behavior_system": behavior_system,
            "scope_resolution": scope_resolution,
            "agent_instruction": _agent_instruction(status, scope_resolution),
            "next_reads": _semantic_next_reads(failure_modes, orchestration_evidence),
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


def _agent_instruction(status: str, scope_resolution: dict) -> dict:
    if status == "scope_ambiguous":
        return {
            "selection_required": False,
            "do_not_auto_apply": True,
            "next_step": scope_resolution.get("first_action")
            or "Rerun semantic-wrapup with an explicit --root session JSONL before applying fixes.",
        }
    return {
        "selection_required": True,
        "do_not_auto_apply": True,
        "next_step": "Ask the user which suggested fixes to apply, then implement selected fixes through normal task/FSM flow.",
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
    if substantive_signals and len(substantive_signals) > len(setup_echo_signals):
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
            "Rerun with the parent/user session JSONL or another exact known Codex/Claude session JSONL, "
            f"for example: {_rerun_with_root_command(query)}"
        ),
    }


def _rerun_with_root_command(query: str) -> str:
    quoted_query = shlex.quote(query)
    return (
        f"jsonl-history-search semantic-wrapup {quoted_query} "
        '--root <path-to-session.jsonl> --cwd "$PWD" --format json'
    )


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

    for rel in _iter_existing(cwd, ["**/AGENTS.md", "**/CLAUDE.md"]):
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
    destinations = []
    for match in re.findall(r"tasks/[A-Za-z0-9._/\-]+\.md", text):
        if (cwd / match).is_file():
            destinations.append(("active_task", match))
            break
    return destinations


def _iter_existing(cwd: Path, patterns: list[str]) -> list[str]:
    rels: list[str] = []
    for pattern in patterns:
        for path in sorted(cwd.glob(pattern)):
            if len(rels) >= DESTINATION_SCAN_LIMIT:
                return rels
            if _skip_destination_path(path):
                continue
            try:
                rels.append(path.relative_to(cwd).as_posix())
            except ValueError:
                continue
    return rels


def _skip_destination_path(path: Path) -> bool:
    ignored = {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", "venv"}
    parts = set(path.parts)
    if any(part in ignored for part in path.parts):
        return True
    if ".private" in parts:
        return True
    if ".tmp" in parts and "plugins" in parts:
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
    return signals


def _substantive_signals(signals: list[dict]) -> list[dict]:
    return [signal for signal in signals if signal.get("scope_signal_kind") not in SETUP_SIGNAL_KINDS]


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
        modes.append(
            {
                "failure_mode": label,
                "title": _title_for(label),
                "evidence_count": len(items),
                "evidence": evidence,
                "cause_chain": _cause_chain(label, evidence),
                "confidence": "high" if len(items) >= 2 else "medium",
                "uncertainty": _uncertainty(label, len(items)),
                "source_refs": [item["source_ref"] for item in evidence],
            }
        )
    return modes


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
                "fix_type": pattern["fix_type"],
                "proposed_destination": destination,
                "destination_refs": destination.get("source_refs", []),
                "destination_source_refs": destination.get("source_refs", []),
                "destination_rationale": _destination_rationale(label, destination, mode),
                "user_selectable": True,
                "auto_apply": False,
                "proposed_action": _proposed_action(label, destination),
                "evidence_refs": mode["source_refs"],
                "selection_options": ["accept", "reject", "defer"],
                "selection_shape": {
                    "required_decision": "accept_reject_or_defer",
                    "options": selection_options,
                },
                "confidence": mode["confidence"],
                "uncertainty": mode["uncertainty"],
            }
        )
    return suggestions


def _semantic_next_reads(failure_modes: list[dict], orchestration_evidence: dict | None = None) -> list[dict]:
    refs = []
    orchestration_items = (orchestration_evidence or {}).get("items") or []
    for ref in next_read_refs(*[mode.get("evidence", []) for mode in failure_modes], orchestration_items):
        refs.append({"source_ref": ref, "reason": "Inspect complaint/correction evidence before applying suggested fix."})
    return refs


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


def _proposed_action(label: str, destination: dict) -> str:
    path = destination.get("path")
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
