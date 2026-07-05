from __future__ import annotations

from dataclasses import dataclass
import re

from .agent_command_cookbook import agent_question_cookbook, ask_next_commands
from .worktree_currentness import is_worktree_cleanup_currentness_query


@dataclass(frozen=True)
class AskRoute:
    route_family: str
    chosen_route: str
    why_it_applies: str
    first_command_template: str
    read_specialization: str
    inspect_after: tuple[str, ...]
    stop_guidance: str
    manual_search_guidance: str

    def first_command(self, query: str) -> str:
        escaped = query.replace('"', '\\"')
        skill = _skill_name_from_query(query)
        return self.first_command_template.format(query=escaped, skill=skill)


LOCAL_ARTIFACT_VERIFICATION_ROUTE = AskRoute(
    route_family="local_artifact_verification",
    chosen_route="local-artifact-verification",
    why_it_applies=(
        "The wording asks whether recently edited local artifacts include or "
        "saved the requested material, so verify the worktree and named files "
        "before invoking JSONL history search."
    ),
    first_command_template="git status --short",
    read_specialization="local-artifact-verification",
    inspect_after=(
        "git status --short for changed/untracked files",
        "git diff or git show for just-created/edited artifacts",
        "targeted sed/rg against named task, evidence, TODO, report, or docs files",
        "targeted raw JSONL/source-window reads only if artifact source coverage is missing or disputed",
    ),
    stop_guidance=(
        "Start with local artifact verification. Answer from the inspected task/"
        "evidence/TODO/report files when they resolve completeness. Do not run "
        "broad ask/semantic-debug unless the user explicitly asks about prior "
        "conversation history or a cited source window is missing/disputed."
    ),
    manual_search_guidance=(
        "Use bounded local commands first: git status, git diff, git show, and "
        "sed/rg against named files. JSONL is a targeted backstop only for "
        "conversation/source-window coverage gaps."
    ),
)

BOUNDED_NO_EVIDENCE_ROUTE = AskRoute(
    route_family="bounded_no_evidence",
    chosen_route="bounded-no-evidence / no-evidence-report",
    why_it_applies=(
        "The wording asks whether evidence exists inside an explicit bounded "
        "or searched source scope. Use the no-evidence report surface so the "
        "answer is scoped to searched roots and weak/context-only candidates "
        "instead of becoming a generic semantic-debug question."
    ),
    first_command_template=(
        'jsonl-history-search no-evidence-report "{query}" --cwd "$PWD" '
        "--report-output .private/jsonl/no-evidence-report.json --format json"
    ),
    read_specialization="references/no-evidence.md",
    inspect_after=(
        "no_evidence_report.searched_scope / searched roots, file caps, byte caps, and date windows",
        "no_evidence_report.answer_scope == not_found_in_searched_scope",
        "candidate_interpretation and weak/context-only candidate boundary",
        "sufficient_to_stop_for_searched_scope_only and read_more_guidance.required_before_scoped_no_evidence_answer",
    ),
    stop_guidance=(
        "Read references/no-evidence.md, then inspect the report's searched "
        "scope, answer_scope, candidate_interpretation, and read_more_guidance "
        "stop fields. Do not run `read-more` when the searched-scope stop rule "
        "is sufficient; weak/context source reads are optional diagnostics only."
    ),
    manual_search_guidance=(
        "Manual rg or read-more is follow-up only when the searched scope is "
        "wrong, capped, missing a required root, or the user asks for weak "
        "context records to be summarized. Preserve the boundary: this proves "
        "not_found_in_searched_scope, not all-history absence."
    ),
)


ROUTES: tuple[AskRoute, ...] = (
    AskRoute(
        route_family="skill_check",
        chosen_route="skill-check / skill-audit",
        why_it_applies=(
            "The wording asks whether a named skill was used, read, triggered, "
            "followed, skipped, or misapplied, so route to the focused skill "
            "audit/check surface instead of semantic-wrapup frustration mining."
        ),
        first_command_template='jsonl-history-search skill-check "{skill}" --cwd "$PWD" --format text',
        read_specialization="references/skill-audit.md",
        inspect_after=(
            "skill_audit.usage_instances",
            "skill_audit.non_use_mentions",
            "skill_audit.workflow_classification_summary",
            "ai_handoff_packet evidence refs before making compliance claims",
        ),
        stop_guidance=(
            "Run skill-check/skill-audit before semantic-wrapup for named-skill "
            "compliance questions. Stop when the audit classifies proper use, "
            "missed trigger, misapplication, or missing referenced-file follow-through "
            "from inspected source refs."
        ),
        manual_search_guidance=(
            "Manual rg is a targeted follow-up only after the skill audit names "
            "a missing skill-use facet or source ref. Keyword mentions alone are "
            "not proof of actual skill use."
        ),
    ),
    AskRoute(
        route_family="source_window_semantic_debug",
        chosen_route="source-window / semantic-debug",
        why_it_applies=(
            "The wording names a reviewed source case, specific session/date, "
            "or source-window-only boundary, so route directly to semantic-debug "
            "source refs before artifact currentness or broad repo search."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-debugging.md",
        inspect_after=(
            "semantic_debug.answer_ready_handoff.primary_source_followthrough",
            "semantic_debug.source_locator_handoff / top source refs",
            "compact text read-more follow-through; avoid JSON source-window output",
        ),
        stop_guidance=(
            "Run text-first semantic-debug before artifact currentness or broad "
            "rg, then open the promoted source-window reads. Stop broad searching "
            "once the reviewed source window answers the prompt. Do not switch to "
            "JSON output, `rg --files`, broad repo/private `rg`, or current-session "
            "echo recovery unless a named required facet is still missing after the "
            "compact source-window read."
        ),
        manual_search_guidance=(
            "Manual rg is a follow-up only for a named missing facet after "
            "semantic-debug compact text source-window follow-through, not before it. "
            "Do not run `rg --files` as reviewed-source recovery."
        ),
    ),
    AskRoute(
        route_family="action_proof",
        chosen_route="action-proof / semantic-debug",
        why_it_applies=(
            "The wording asks whether an agent action changed or overwrote edits, "
            "so the first evidence should be tool/action proof from conversation logs."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/action-proof.md",
        inspect_after=(
            "semantic_debug.source_window_action_proof",
            "tool_action_proof primary refs and required proof reads",
            "source_locator_handoff / next_reads for exact source follow-through",
        ),
        stop_guidance=(
            "Run the action-proof route before broad rg. If primary proof answers "
            "the overwrite/action question, stop broad searching and answer from inspected refs."
        ),
        manual_search_guidance=(
            "Manual rg is only for targeted follow-up after the route, or if the "
            "route returns weak/no evidence and the searched scope is too narrow."
        ),
    ),
    AskRoute(
        route_family="process_actual_use",
        chosen_route="process-actual-use / semantic-debug",
        why_it_applies=(
            "The wording asks whether a process, artifact, checklist, learning, "
            "or front-door is actually used in work, not merely current or cited. "
            "Route to semantic-debug so raw session timing and behavior-change "
            "evidence are primary."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-debugging.md",
        inspect_after=(
            "semantic_debug.question_family.primary == process_actual_use",
            "semantic_debug.guided_audit_flow.answer_gate.process_actual_use_buckets",
            "primary raw conversation/session windows for before-planning consultation, behavior/scope/claim-boundary changes, and reviewer enforcement timing",
            "repo task/review artifacts only as secondary context unless linked to raw timing or reviewer enforcement evidence",
        ),
        stop_guidance=(
            "Run semantic-debug text-first before artifact-currentness, TODO scans, "
            "or repo-only grep. Answer behavior claims from inspected raw session "
            "windows and actual-use buckets: exists, cited, consulted before "
            "planning, changed behavior/scope/claim boundaries, only updated "
            "after the fact, stale/performative/no-evidence gap."
        ),
        manual_search_guidance=(
            "Manual rg is a targeted follow-up only after semantic-debug names a "
            "missing bucket or source window. File existence, task status, or "
            "mentions are not actual-use proof without raw timing or reviewer "
            "enforcement evidence."
        ),
    ),
    AskRoute(
        route_family="orchestration_provenance",
        chosen_route="orchestration-provenance / semantic-debug",
        why_it_applies=(
            "The wording names a historical task/source handle and asks about "
            "coordinator, worker, reviewer, subagent, or launched-run provenance. "
            "Use semantic-debug source refs rather than status anchoring to the "
            "current run."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-debugging.md",
        inspect_after=(
            "semantic_debug.source_locator_handoff.next_reads",
            "historical provenance self-echo diagnostics",
            "pre-existing parent/worker/reviewer raw source refs before current-run tool-output echoes",
        ),
        stop_guidance=(
            "Run semantic-debug before what-happened-since or broad repo recovery. "
            "Open promoted pre-existing raw parent/worker/reviewer source refs first. "
            "Treat current-run command outputs, lane prompts, and same-run tool echoes "
            "as diagnostics unless the user explicitly asks about the current run/tool/lane."
        ),
        manual_search_guidance=(
            "Manual rg is a targeted follow-up only after semantic-debug source refs "
            "leave a named provenance facet missing. Prefer original raw session refs "
            "over current-run outputs that merely quote those refs."
        ),
    ),
    AskRoute(
        route_family="latest_status",
        chosen_route="latest-status / what-happened-since",
        why_it_applies=(
            "The wording asks what happened after the user was away, since a "
            "recent anchor, or across an explicit task status/handoff window, "
            "so use the status-anchor route before repo recovery."
        ),
        first_command_template='jsonl-history-search what-happened-since "{query}" --cwd "$PWD" --format text',
        read_specialization="references/latest-status.md",
        inspect_after=(
            "status_anchor.follow_through_plan open_first / then_confirm / do_not_open_by_default before JSON or JQ reruns",
            "status_anchor.anchor_candidates and selected_anchor",
            "for main-thread wording, selected_anchor should be the human history.jsonl row, with session/current-context candidates only as follow-up or rejected detail",
            "status_anchor.session_activity raw sessions/**/*.jsonl trace after the anchor",
            "source_tiers and next_reads only as debugging detail after the follow-through plan leaves a named gap",
        ),
        stop_guidance=(
            "Run the status route before task/repo recovery scans. Use "
            "status_anchor.follow_through_plan first: open_first required primary refs, then_confirm "
            "target-window secondary refs, and do_not_open_by_default diagnostics. Do not rerun "
            "JSON/JQ extraction, open diagnostic/post-window refs, or broaden manual search unless "
            "a named status facet remains missing or contradictory after the plan reads. For "
            "main-thread/parent-thread status wording, do not answer from inherited context or a "
            "current-session prompt when the selected anchor is not a human history.jsonl row."
        ),
        manual_search_guidance=(
            "Repo or task scans are follow-up only after the follow-through plan identifies "
            "the anchor/window and required raw/session refs, or reports that it cannot find one. "
            "Treat commits and repo state as secondary support, never as the status anchor; "
            "diagnostic/post-window refs are not default answer support."
        ),
    ),
    AskRoute(
        route_family="remembered_topic",
        chosen_route="remembered-topic / semantic-debug",
        why_it_applies=(
            "The wording asks why a topic was mentioned, which is provenance for "
            "a remembered topic rather than a currentness decision."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/remembered-topic.md",
        inspect_after=(
            "semantic_debug.source_locator_handoff",
            "top source refs and read-more follow-through commands",
            "answer readiness / no-evidence scope before widening",
        ),
        stop_guidance=(
            "Run semantic-debug before exact grep or direct JSON inspection. Stop "
            "when inspected source refs explain why the topic was mentioned."
        ),
        manual_search_guidance=(
            "Exact phrase rg is a follow-up for missing facets, not the first move "
            "for a natural remembered-topic question."
        ),
    ),
    AskRoute(
        route_family="artifact_currentness",
        chosen_route="artifact-currentness / worktree cleanup currentness",
        why_it_applies=(
            "The wording asks whether worktrees/checkouts/branches are old, stale, "
            "current, removable, or prune candidates, so artifact-currentness "
            "should classify worktree state and private-artifact preservation first."
        ),
        first_command_template='jsonl-history-search artifact-currentness "{query}" --cwd "$PWD" --format markdown',
        read_specialization="references/status-currentness.md",
        inspect_after=(
            "artifact_currentness.worktree_currentness.classifications",
            "per-item source_refs and preservation_guidance",
            "next_commands for worktree list, status, branch contains, git log grep, targeted rg, and private artifact find",
        ),
        stop_guidance=(
            "Run artifact-currentness before shallow git cleanup conclusions. Stop "
            "only after source refs and private-artifact preservation requirements are inspected."
        ),
        manual_search_guidance=(
            "Manual git/rg inspection is follow-up after the route's targeted commands. "
            "Do not treat worktree cleanup as branch ref pruning."
        ),
    ),
    AskRoute(
        route_family="artifact_currentness",
        chosen_route="artifact-currentness",
        why_it_applies=(
            "The wording asks which TODO/plan/ticket/artifact matters or is current, so "
            "the currentness route should resolve stale versus active evidence and treat status fields as claims."
        ),
        first_command_template='jsonl-history-search artifact-currentness "{query}" --cwd "$PWD" --format markdown',
        read_specialization="references/status-currentness.md",
        inspect_after=(
            "artifact_currentness.currentness_verdict",
            "current_front_door and status_field_assessment claim fields",
            "evidence_chain and next_reads for current vs stale proof",
        ),
        stop_guidance=(
            "Run artifact-currentness before TODO/front-door scans. Stop broad "
            "searching once current front-door evidence and inspected refs answer which artifact matters."
        ),
        manual_search_guidance=(
            "Manual TODO scans are follow-up only when the currentness route names "
            "a specific missing front-door source or cannot resolve the artifact."
        ),
    ),
    AskRoute(
        route_family="semantic_wrapup",
        chosen_route="semantic-wrapup",
        why_it_applies=(
            "The wording asks for repeated complaints, recurring failure modes, "
            "or user corrections, so use the semantic wrap-up failure-mining route."
        ),
        first_command_template='jsonl-history-search semantic-wrapup "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-wrapup.md",
        inspect_after=(
            "semantic_wrapup.failure_modes",
            "semantic_wrapup.fix_suggestions",
            "complaint/correction source refs before proposing any fix",
            "next_reads source refs before proposing any fix",
        ),
        stop_guidance=(
            "Run semantic-wrapup before broad session/manual narrowing. Stop when "
            "failure modes are supported by inspected source refs; do not auto-apply fixes."
        ),
        manual_search_guidance=(
            "Manual session narrowing is follow-up only for a missing source ref "
            "or ambiguous failure mode after semantic-wrapup runs."
        ),
    ),
)


def build_ask_router_payload(query: str, cwd: str = ".") -> dict:
    route = classify_ask_route(query)
    first_command = route.first_command(query)
    return {
        "ask_router": {
            "status": "matched",
            "query": query,
            "cwd": cwd,
            "chosen_route": route.chosen_route,
            "route_family": route.route_family,
            "why_it_applies": route.why_it_applies,
            "first_command": first_command,
            "next_commands": ask_next_commands(
                selected_first_command=first_command,
                route_family=route.route_family,
            ),
            "command_cookbook": agent_question_cookbook(),
            "read_specialization": route.read_specialization,
            "inspect_after": list(route.inspect_after),
            "stop_guidance": route.stop_guidance,
            "manual_search_guidance": route.manual_search_guidance,
            "boundary": (
                "This is a first-command resolver for agents. It is not a final "
                "answer generator and does not inspect evidence by itself."
            ),
        }
    }


def classify_ask_route(query: str) -> AskRoute:
    normalized = " ".join(query.lower().replace("?", " ").replace("!", " ").split())

    if _looks_like_skill_compliance_query(normalized):
        return ROUTES[0]

    if _looks_like_reviewed_source_window_prompt(normalized):
        return ROUTES[1]

    if _looks_like_local_artifact_verification_query(normalized):
        return LOCAL_ARTIFACT_VERIFICATION_ROUTE

    if _looks_like_bounded_no_evidence_query(normalized):
        return BOUNDED_NO_EVIDENCE_ROUTE

    if _has_any(normalized, ("overwrite", "overwrote", "clobber", "revert", "deleted", "edit")) and _has_any(
        normalized, ("agent", "edits", "edit", "changed", "action")
    ):
        return ROUTES[2]

    if _looks_like_process_actual_use_query(normalized):
        return ROUTES[3]

    if _looks_like_task_window_status_handoff_query(normalized):
        return ROUTES[5]

    if _looks_like_parent_main_status_anchor_failure_query(normalized):
        return ROUTES[5]

    if _looks_like_orchestration_provenance_query(normalized):
        return ROUTES[4]

    if _looks_like_latest_status_anchor_query(normalized):
        return ROUTES[5]

    if _has_any(normalized, ("why did i mention", "why did i say", "what was i talking about", "why was i talking about")):
        return ROUTES[6]

    if is_worktree_cleanup_currentness_query(query):
        return ROUTES[7]

    if _has_any(normalized, ("which todo", "which plan", "which task", "actually matters", "is current", "still what")):
        return ROUTES[8]

    if _looks_like_ticket_currentness(normalized):
        return ROUTES[8]

    if _looks_like_repeated_complaint_or_failure_query(normalized):
        return ROUTES[9]

    return AskRoute(
        route_family="semantic_debug",
        chosen_route="semantic-debug",
        why_it_applies=(
            "The wording looks like a natural conversation-history or provenance "
            "question, but it did not match a narrower front-door route."
        ),
        first_command_template='jsonl-history-search semantic-debug "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-debugging.md",
        inspect_after=(
            "semantic_debug.source_locator_handoff",
            "top source refs / next_reads",
            "no-evidence searched scope before widening",
        ),
        stop_guidance=(
            "Run semantic-debug before broad manual search. Stop when inspected "
            "source refs are sufficient, or escalate only from explicit weak/no-evidence output."
        ),
        manual_search_guidance=(
            "Manual rg is a targeted follow-up after route output, not the first "
            "move for a natural history question. Prefer compact text read-more "
            "commands over raw JSON source-window output."
        ),
    )


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _looks_like_skill_compliance_query(normalized: str) -> bool:
    if not _has_any(
        normalized,
        (
            "skill",
            "$",
            "skill.md",
            "reviewer",
            "product-fsm",
            "product fsm",
            "implementer",
            "planner",
            "wrap-up",
            "wrap up",
        ),
    ):
        return False
    compliance_terms = (
        r"\buse\b",
        r"\bused\b",
        r"\busing\b",
        r"\btrigger\b",
        r"\btriggered\b",
        r"\bread\b",
        r"\bfollow\b",
        r"\bfollowed\b",
        r"\bcomply\b",
        r"\bcompliance\b",
        r"\bskip\b",
        r"\bskipped\b",
        r"\bmissed\b",
        r"\bmisapplied\b",
        r"\bproperly applied\b",
        r"\breferenced files?\b",
        r"\bskill\.md\b",
    )
    question_terms = (
        "did the agent",
        "did it",
        "did they",
        "whether",
        "was the",
        "was ",
        "were the",
        "were ",
        "was it",
        "check",
        "audit",
        "prove",
        "actually",
    )
    return any(re.search(term, normalized) for term in compliance_terms) and _has_any(normalized, question_terms)


def _skill_name_from_query(query: str) -> str:
    normalized = query.lower()
    match = re.search(r"<name>([^<]+)</name>", normalized)
    if match:
        return _normalize_skill_name(match.group(1))
    match = re.search(r"\$([a-z0-9][a-z0-9_-]+)", normalized)
    if match:
        return _normalize_skill_name(match.group(1))
    match = re.search(r"\b([a-z0-9][a-z0-9_-]+)\s+skill\b", normalized)
    if match and match.group(1) not in {"a", "the", "this", "that", "named"}:
        return _normalize_skill_name(match.group(1))
    for candidate in (
        "product-fsm-orchestration",
        "reviewer",
        "implementer",
        "planner",
        "wrap-up",
    ):
        if candidate in normalized or candidate.replace("-", " ") in normalized:
            return candidate
    return "<skill-name>"


def _normalize_skill_name(value: str) -> str:
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _looks_like_orchestration_provenance_query(normalized: str) -> bool:
    has_explicit_handle = bool(
        re.search(r"\b[a-z]+[-_][a-z0-9]+[-_][a-z0-9_-]*\b", normalized)
        or re.search(r"\b[a-z]{2,}[-_]\d{2,}[a-z0-9_-]*\b", normalized)
    )
    if not has_explicit_handle:
        return False
    if _has_any(
        normalized,
        (
            "current run",
            "this run",
            "current lane",
            "this lane",
            "current tool",
            "this tool",
            "current route",
            "this route",
        ),
    ):
        return False
    return _has_any(
        normalized,
        (
            "coordinator",
            "worker",
            "reviewer",
            "rereviewer",
            "subagent",
            "spawned",
            "launched",
            "implementation worker",
            "parent",
            "orchestration",
        ),
    ) and _has_any(
        normalized,
        (
            "what happened",
            "what changed",
            "actually did",
            "verified",
            "proven",
            "unresolved",
            "caveat",
            "provenance",
        ),
    )


def _looks_like_task_window_status_handoff_query(normalized: str) -> bool:
    task_handles = re.findall(r"\b[a-z]{2,}[-_]\d{2,}[a-z0-9_-]*\b", normalized)
    if len(set(task_handles)) < 2:
        return False
    if not _has_any(
        normalized,
        (
            "what happened",
            "status",
            "handoff",
            "next action",
            "next serious action",
        ),
    ):
        return False
    has_range_connector = (
        "->" in normalized
        or _has_any(normalized, (" across ", " between ", " through "))
        or (" from " in normalized and " to " in normalized)
    )
    if not has_range_connector:
        return False
    return _has_any(
        normalized,
        (
            "coordinator",
            "worker",
            "workers",
            "reviewer",
            "reviewers",
            "commit",
            "commits",
            "report",
            "reports",
            "validation",
            "validations",
            "caveat",
            "caveats",
            "blocker",
            "blockers",
            "next action",
            "next serious action",
        ),
    )


def _looks_like_latest_status_anchor_query(normalized: str) -> bool:
    if _has_any(
        normalized,
        (
            "what happened since",
            "what has happened since",
            "what changed since",
            "what changed while i was away",
            "what did you do while i was away",
            "while i was away",
            "since my last",
            "since my latest",
            "status since",
            "latest status",
            "status update",
            "after i told",
            "keep working",
        ),
    ):
        return True
    anchor_terms = (
        "last message",
        "latest message",
        "last actual message",
        "latest actual message",
        "main thread",
        "parent thread",
        "parent/main",
        "before this worker",
        "before the worker",
        "before this reviewer",
        "before the reviewer",
        "before this side conversation",
        "before the side conversation",
        "before this side thread",
        "before the side thread",
    )
    status_verbs = (
        "what happened",
        "what changed",
        "what did i ask",
        "what did i tell",
        "what was my ask",
        "what was the ask",
        "what did you do",
        "status",
        "catch me up",
    )
    return _has_any(normalized, anchor_terms) and _has_any(normalized, status_verbs)


def _looks_like_parent_main_status_anchor_failure_query(normalized: str) -> bool:
    anchor_failure_terms = (
        "wrong anchor",
        "wrong status anchor",
        "wrong selected anchor",
        "bad anchor",
        "selected the wrong",
        "used the wrong",
        "ignored the anchor",
        "made no sense",
    )
    status_terms = (
        "status",
        "what happened since",
        "what has happened since",
        "while i was away",
        "answer",
        "side-thread answer",
        "side thread answer",
    )
    parent_or_boundary_terms = (
        "parent/main",
        "parent thread",
        "main thread",
        "side-thread",
        "side thread",
        "last actual message",
        "previous relevant time window",
        "before the last answer",
    )
    complaint_terms = (
        "complaint",
        "complaining",
        "correction",
        "correcting",
        "failed",
        "failure",
        "wrong",
        "nonsense",
        "made no sense",
    )
    return (
        _has_any(normalized, anchor_failure_terms)
        and _has_any(normalized, status_terms)
        and _has_any(normalized, parent_or_boundary_terms)
        and _has_any(normalized, complaint_terms)
    )


def _looks_like_repeated_complaint_or_failure_query(normalized: str) -> bool:
    if _has_any(normalized, ("semantic wrap-up", "semantic wrapup", "wrap up", "keep yelling", "yelling about")):
        return True
    complaint_terms = (
        "complain",
        "complained",
        "complaining",
        "complaints",
        "failure mode",
        "failure modes",
        "failure-mode",
        "failure-modes",
        "kept correcting",
        "keep correcting",
        "kept correcting you",
        "keep correcting you",
        "user corrections",
        "same correction",
        "same failure",
        "recurring failure",
        "repeated failure",
        "keeps happening",
        "keep happening",
        "keep doing",
        "kept doing",
    )
    repeated_terms = (
        "keep",
        "kept",
        "repeated",
        "recurring",
        "again",
        "same",
        "correcting",
        "correction",
        "failure modes",
        "complaining",
    )
    if _has_any(normalized, complaint_terms) and _has_any(normalized, repeated_terms):
        return True
    return _has_any(normalized, ("why does this keep happening", "why do you keep doing this", "why did this keep happening"))


def _looks_like_local_artifact_verification_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    local_verification = _has_any(
        normalized,
        (
            "are you sure",
            "got everything",
            "save",
            "saved",
            "include",
            "included",
            "did you get",
            "did you add",
            "did you write",
            "did you save",
            "what changed",
            "files you just",
            "just created",
            "just made",
            "just edited",
            "whole thread as evidence",
            "thread as evidence",
            "evidence packet complete",
            "task file include",
        ),
    )
    artifact_context = _has_any(
        normalized,
        (
            "task file",
            "task",
            "evidence packet",
            "evidence file",
            "todo",
            "report",
            "review",
            "doc",
            "docs",
            "file",
            "files",
            "artifact",
            "artifacts",
            "diff",
            "commit",
            "anti contamination",
            "source refs",
            "source windows",
            "thread",
        ),
    )
    asks_history = _has_any(
        normalized,
        (
            "what happened",
            "what did i say",
            "what did i tell",
            "why did i mention",
            "why did i say",
            "prior conversation",
            "conversation history",
            "before in the chat",
            "last message",
            "while i was away",
        ),
    )
    explicitly_artifact_first = _has_any(
        normalized,
        (
            "task file include",
            "evidence packet",
            "whole thread as evidence",
            "thread as evidence",
            "files you just",
            "just created",
            "just edited",
            "what changed in the files",
            "anti contamination rule",
        ),
    )
    ambiguous_recent_work_followup = _has_any(
        normalized,
        (
            "are you sure",
            "got everything",
            "did you get everything",
            "did you get it all",
            "did you include everything",
            "did you save everything",
        ),
    )
    return (
        local_verification
        and not asks_history
        and (artifact_context or explicitly_artifact_first or ambiguous_recent_work_followup)
    )


def _looks_like_reviewed_source_window_prompt(normalized: str) -> bool:
    real_jsh_handle = bool(re.search(r"\breal-jsh[0-9a-z-]*\b", normalized))
    reviewed_source = "reviewed source case" in normalized or real_jsh_handle
    source_window = "source window" in normalized or "source refs" in normalized or "source case" in normalized
    dated_session = bool(re.search(r"\b20\d\d-\d\d-\d\d\b", normalized)) or "session dated" in normalized
    boundary = _has_any(normalized, ("only", "not from current docs", "not current docs", "answer from"))
    return bool(reviewed_source and (real_jsh_handle or (source_window and (dated_session or boundary))))


def _looks_like_bounded_no_evidence_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    scoped_terms = (
        "bounded scope",
        "bounded source scope",
        "bounded source",
        "bounded root",
        "bounded search",
        "bounded fixture",
        "searched scope",
        "searched source",
        "searched root",
        "selected scope",
        "selected source",
        "selected root",
        "source scope",
        "source-scoped",
        "scoped search",
        "scoped no evidence",
        "searched-scope",
        "within the searched",
        "within this root",
        "within this source",
        "within this scope",
        "in this root",
        "in this source scope",
        "in the selected root",
        "in the selected source",
        "in the searched scope",
    )
    if not _has_any(normalized, scoped_terms):
        return False

    explicit_absence_terms = (
        "no evidence",
        "no direct evidence",
        "not found",
        "nothing found",
        "absent",
        "absence",
        "was not discussed",
        "wasn't discussed",
        "were not discussed",
        "weren't discussed",
        "not discussed",
        "not mentioned",
        "missing evidence",
    )
    evidence_question_terms = (
        "is there evidence",
        "any evidence",
        "evidence that",
        "prove there is no",
        "prove no evidence",
        "did the searched scope contain",
        "did the selected scope contain",
        "did the searched root contain",
        "did the selected root contain",
        "does the searched scope contain",
        "does the selected root contain",
    )
    scoped_discussion_terms = (
        "discussed",
        "mentioned",
        "contain",
        "contains",
        "contained",
        "covered",
        "appear",
        "appears",
        "appeared",
        "show",
        "shows",
    )
    weak_terms = (
        "weak evidence",
        "weak/no evidence",
        "weak candidates",
        "weak context",
        "context only",
        "context-only",
    )
    return (
        _has_any(normalized, explicit_absence_terms)
        or _has_any(normalized, evidence_question_terms)
        or (_has_any(normalized, scoped_discussion_terms) and _has_any(normalized, ("was ", "were ", "did ", "does ", "is ")))
        or _has_any(normalized, weak_terms)
    )


def _looks_like_ticket_currentness(normalized: str) -> bool:
    ticketish = _has_any(normalized, ("ticket", "issue", "linear", "github")) or bool(
        re.search(r"\b[a-z]{2,}-\d+\b", normalized)
    )
    currentish = _has_any(
        normalized,
        (
            "still current",
            "current",
            "stale",
            "superseded",
            "obsolete",
            "still open",
            "still active",
            "closed",
            "done",
            "matters",
        ),
    )
    return ticketish and currentish


def _looks_like_process_actual_use_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    actual_use_phrases = (
        "actually being used",
        "actually consulted",
        "actually used",
        "actual use",
        "being used",
        "consulted before planning",
        "consulted before work",
        "consulted before",
        "used to change agent behavior",
        "used to change behavior",
        "used before planning",
        "read before planning",
        "process for its own sake",
        "performative",
        "merely exist",
        "get cited",
        "merely cited",
        "write-only ritual",
        "only updated after the fact",
        "updated after the fact",
        "get updated after the fact",
        "changed behavior",
        "change agent behavior",
        "changed scope",
        "changed task selection",
        "claim boundaries",
        "reviewer enforced",
        "review enforced",
        "artifact exists",
        "artifact is cited",
        "consulted before planning",
        "changed task selection scope or claim boundaries",
        "only updated after the fact",
        "stale performative or no evidence gap",
    )
    process_targets = (
        "process",
        "artifact",
        "document",
        "doc",
        "rule",
        "checklist",
        "front-door",
        "front door",
        "fsm",
        "learning",
        "learnings",
        "learning entry",
        "ledger",
        "memory",
        "workflow",
        "procedure",
        "rubric",
    )
    use_audit_verbs = (
        "is ",
        "is this",
        "is the",
        "are the",
        "are these",
        "was this",
        "were these",
        "which ",
        "whether",
        "prove",
        "verify",
        "audit",
        "classify",
        "distinguish",
        "does it",
        "did it",
        "did they",
    )
    has_actual_use_phrase = _has_any(normalized, actual_use_phrases)
    has_target = _has_any(normalized, process_targets)
    has_audit_shape = _has_any(normalized, use_audit_verbs)
    return has_target and has_actual_use_phrase and has_audit_shape
