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


ROUTES: tuple[AskRoute, ...] = (
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
            "status_anchor.value_summary / work_after_anchor",
            "next_reads for source refs that justify the status summary",
        ),
        stop_guidance=(
            "Run the status route before task/repo recovery scans. Stop once the "
            "status route and inspected refs explain the current work since the anchor."
        ),
        manual_search_guidance=(
            "Repo or task scans are follow-up only after the JSONL route "
            "identifies the anchor/window or reports that it cannot find one."
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

    if _has_any(normalized, ("overwrite", "overwrote", "clobber", "revert", "deleted", "edit")) and _has_any(
        normalized, ("agent", "edits", "edit", "changed", "action")
    ):
        return ROUTES[0]

    if _has_any(normalized, ("while i was away", "what happened", "since my last", "after i told", "keep working")):
        return ROUTES[1]

    if _has_any(normalized, ("why did i mention", "why did i say", "what was i talking about", "why was i talking about")):
        return ROUTES[2]

    if is_worktree_cleanup_currentness_query(query):
        return ROUTES[3]

    if _has_any(normalized, ("which todo", "which plan", "which task", "actually matters", "is current", "still what")):
        return ROUTES[4]

    if _looks_like_ticket_currentness(normalized):
        return ROUTES[4]

    if _has_any(normalized, ("keep yelling", "yelling about", "complain", "complained", "semantic wrap-up", "wrap up")):
        return ROUTES[5]

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
            "move for a natural history question."
        ),
    )


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


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
