from __future__ import annotations

from dataclasses import dataclass
import re

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
        return self.first_command_template.format(query=escaped)


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


ROUTES: tuple[AskRoute, ...] = (
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
        route_family="latest_status",
        chosen_route="latest-status / what-happened-since",
        why_it_applies=(
            "The wording asks what happened after the user was away or since a "
            "recent anchor, so use the status-anchor route before repo recovery."
        ),
        first_command_template='jsonl-history-search what-happened-since "{query}" --cwd "$PWD" --format text',
        read_specialization="references/latest-status.md",
        inspect_after=(
            "status_anchor.anchor_candidates and selected_anchor",
            "status_anchor.session_activity raw sessions/**/*.jsonl trace after the anchor",
            "next_reads for raw JSONL/source refs before commits, task artifacts, or repo state",
        ),
        stop_guidance=(
            "Run the status route before task/repo recovery scans. Inspect the selected "
            "anchor and raw session JSONL source refs first. Do not answer from commits, "
            "current repo state, compacted summaries, or process state alone."
        ),
        manual_search_guidance=(
            "Repo or task scans are follow-up only after the JSONL route "
            "identifies the anchor/window and raw session trace, or reports that it cannot find one. "
            "Treat commits and repo state as secondary support, never as the status anchor."
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
            "The wording asks for repeated complaints, yelling, or session-level "
            "lessons, so use the semantic wrap-up failure-mining route."
        ),
        first_command_template='jsonl-history-search semantic-wrapup "{query}" --cwd "$PWD" --format text',
        read_specialization="references/semantic-wrapup.md",
        inspect_after=(
            "semantic_wrapup.failure_modes",
            "semantic_wrapup.fix_suggestions",
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
    return {
        "ask_router": {
            "status": "matched",
            "query": query,
            "cwd": cwd,
            "chosen_route": route.chosen_route,
            "route_family": route.route_family,
            "why_it_applies": route.why_it_applies,
            "first_command": route.first_command(query),
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

    if _looks_like_local_artifact_verification_query(normalized):
        return LOCAL_ARTIFACT_VERIFICATION_ROUTE

    if _looks_like_reviewed_source_window_prompt(normalized):
        return ROUTES[0]

    if _has_any(normalized, ("overwrite", "overwrote", "clobber", "revert", "deleted", "edit")) and _has_any(
        normalized, ("agent", "edits", "edit", "changed", "action")
    ):
        return ROUTES[1]

    if _looks_like_process_actual_use_query(normalized):
        return ROUTES[2]

    if _has_any(normalized, ("while i was away", "what happened", "since my last", "after i told", "keep working")):
        return ROUTES[3]

    if _has_any(normalized, ("why did i mention", "why did i say", "what was i talking about", "why was i talking about")):
        return ROUTES[4]

    if is_worktree_cleanup_currentness_query(query):
        return ROUTES[5]

    if _has_any(normalized, ("which todo", "which plan", "which task", "actually matters", "is current", "still what")):
        return ROUTES[6]

    if _looks_like_ticket_currentness(normalized):
        return ROUTES[6]

    if _has_any(normalized, ("keep yelling", "yelling about", "complain", "complained", "semantic wrap-up", "wrap up")):
        return ROUTES[7]

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
    return local_verification and artifact_context and (explicitly_artifact_first or not asks_history)


def _looks_like_reviewed_source_window_prompt(normalized: str) -> bool:
    reviewed_source = "reviewed source case" in normalized or re.search(r"\breal-jsh[0-9a-z-]*\b", normalized)
    source_window = "source window" in normalized or "source refs" in normalized or "source case" in normalized
    dated_session = bool(re.search(r"\b20\d\d-\d\d-\d\d\b", normalized)) or "session dated" in normalized
    boundary = _has_any(normalized, ("only", "not from current docs", "not current docs", "answer from"))
    return bool(reviewed_source and source_window and (dated_session or boundary))


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
