from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .masking import MaskContext
from .skill_audit import context_row, next_read_refs, scan_records
from .sources import SourcePlan


SCHEMA_VERSION = 1
INLINE_FAILURE_MODE_LIMIT = 10
DESTINATION_SCAN_LIMIT = 200

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
    signals = _complaint_signals(records)
    failure_modes = _failure_modes(signals)
    behavior_system = detect_behavior_system(cwd)
    fix_suggestions = _fix_suggestions(failure_modes, behavior_system)
    payload = {
        "semantic_wrapup": {
            "schema_version": SCHEMA_VERSION,
            "status": "found" if failure_modes else "no_failure_modes_found",
            "query": query,
            "route": "semantic_wrapup_frustration_mining",
            "summary": {
                "complaint_signal_count": len(signals),
                "failure_mode_count": len(failure_modes),
                "fix_suggestion_count": len(fix_suggestions),
                "behavior_system_destination_count": len(behavior_system["destinations"]),
                "elapsed_seconds": round(time.monotonic() - started, 6),
            },
            "failure_modes": failure_modes[:INLINE_FAILURE_MODE_LIMIT],
            "fix_suggestions": fix_suggestions[:INLINE_FAILURE_MODE_LIMIT],
            "behavior_system": behavior_system,
            "agent_instruction": {
                "selection_required": True,
                "do_not_auto_apply": True,
                "next_step": "Ask the user which suggested fixes to apply, then implement selected fixes through normal task/FSM flow.",
            },
            "next_reads": _semantic_next_reads(failure_modes),
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
    return any(part in ignored for part in path.parts)


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


def _complaint_signals(records) -> list[dict]:
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
                **row,
            }
        )
    return signals


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


def _semantic_next_reads(failure_modes: list[dict]) -> list[dict]:
    refs = []
    for ref in next_read_refs(*[mode.get("evidence", []) for mode in failure_modes]):
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
    for item in destinations:
        if item.get("path") == preferred:
            return item
    for item in destinations:
        if preferred.startswith(str(item.get("path", ""))):
            return item
    evidence_text = " ".join(str(item.get("snippet") or "") for item in mode.get("evidence", [])).casefold()
    for item in destinations:
        path = str(item.get("path") or "").casefold()
        if path and path in evidence_text:
            return item
    preferred_kinds = tuple(pattern.get("preferred_kinds") or ())
    for kind in preferred_kinds:
        for item in destinations:
            if item.get("kind") == kind:
                return item
    if destinations:
        return destinations[0]
    return {
        "kind": "user_decision_needed",
        "path": preferred,
        "reason": "no local behavior system destination detected",
        "source_refs": [],
        "destination_ref": None,
    }


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
    }.get(kind, "local behavior destination detected")


def _is_user_like(role: str | None) -> bool:
    normalized = str(role or "").lower()
    return normalized in {"user", "human", "human_user"} or "user" in normalized or "human" in normalized


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).casefold()).strip()
