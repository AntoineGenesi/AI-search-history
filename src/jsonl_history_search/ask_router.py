from __future__ import annotations

from dataclasses import dataclass
import re
import shlex
from typing import Literal

from .agent_command_cookbook import (
    STORAGE_ROOT_INVENTORY_COMMAND,
    agent_question_cookbook,
    ask_next_commands,
    named_task_local_next_commands,
    storage_cleanup_requested,
    storage_optional_deeper_reads,
)
from .audit_depth import resolve_audit_depth
from .command_resolution import resolve_command
from .worktree_currentness import is_worktree_cleanup_currentness_query


EXECUTABLE_SEMANTIC_DEBUG_ROUTE_FAMILIES = frozenset(
    {
        "deep_history_audit",
        "source_window_semantic_debug",
        "action_proof",
        "process_actual_use",
        "expectation_mismatch",
        "orchestration_provenance",
        "provenance",
        "remembered_instruction",
        "remembered_user_wording",
        "remembered_topic",
        "causal_history",
        "topic_history",
        "long_range_history",
        "semantic_debug",
    }
)
EXECUTABLE_DEVLOG_FAILURE_ANALYSIS_ROUTE_FAMILIES = frozenset({"devlog_failure_analysis"})
EXECUTABLE_NO_EVIDENCE_ROUTE_FAMILIES = frozenset({"bounded_no_evidence"})
EXECUTABLE_COORDINATOR_CONTEXT_ROUTE_FAMILIES = frozenset({"active_status_front_door", "coordinator_context"})
EXECUTABLE_LATEST_STATUS_ROUTE_FAMILIES = frozenset({"latest_status"})
EXECUTABLE_SEMANTIC_WRAPUP_ROUTE_FAMILIES = frozenset({"semantic_wrapup"})
EXECUTABLE_ARTIFACT_CURRENTNESS_ROUTE_FAMILIES = frozenset({"artifact_currentness"})
STORAGE_RETENTION_ROUTE_FAMILY = "storage_retention_forensics"
NON_HISTORY_CLARIFICATION_ROUTE_FAMILY = "non_history_clarification"
LOCAL_ARTIFACT_VERIFICATION_ROUTE_FAMILY = "local_artifact_verification"
LOCAL_CHECKOUT_LOOKUP_ROUTE_FAMILY = "local_checkout_lookup"
NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY = "named_task_evidence_source_required"
EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY = "explicit_history_trigger_required"
RAW_PHRASE_SOURCE_SEARCH_ROUTE_FAMILY = "raw_phrase_source_search"
NON_JSONL_COMMAND_ROUTE_FAMILIES = frozenset(
    {
        NON_HISTORY_CLARIFICATION_ROUTE_FAMILY,
        LOCAL_ARTIFACT_VERIFICATION_ROUTE_FAMILY,
        LOCAL_CHECKOUT_LOOKUP_ROUTE_FAMILY,
        NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY,
        EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY,
    }
)
EvidenceSource = Literal["local", "conversation-history"]
ActivationMode = Literal["implicit", "explicit-only"]


METHOD_SELECTION_PACKET_SCHEMA_VERSION = "route-family-method-packet/v1"
_TASK_HANDLE_PATTERN = r"(?=[a-z0-9_-]*\d)(?:[a-z]+[-_][a-z0-9]+[-_][a-z0-9_-]*|[a-z]{2,}[-_]\d{2,}[a-z0-9_-]*)"
_CURRENT_TASK_RUN_HANDLE_RE = re.compile(
    rf"\b(?:current|this)\s+(?P<handle>{_TASK_HANDLE_PATTERN})\s+(?:evaluation\s+)?run\b"
)


METHOD_SELECTION_PACKET_TEMPLATES: dict[str, dict] = {
    "latest_status": {
        "method_id": "latest_status_forward_interval",
        "traversal": "forward_to_current_tail",
        "read_order": [
            "Select and inspect the named boundary Y.",
            "Traverse raw parent/session/tool/worker/review evidence forward from Y to the current tail or emitted machine continuation.",
            "Answer only after complete interval coverage; otherwise follow the canonical continuation without visible-context synthesis.",
        ],
        "stop_rule": (
            "Stop at answer_ready=true after the selected boundary and complete forward interval are proven, "
            "or at a terminal machine blocker."
        ),
    },
    "causal_history": {
        "method_id": "causal_history_backward_first",
        "traversal": "backward_first",
        "read_order": [
            "Select the event or failure X from primary conversation history.",
            "Read backward first through prior instructions, decisions, setup, tool outputs, errors, and reviews.",
            "Check forward consequences only after causal evidence is grounded.",
        ],
        "stop_rule": (
            "Answer why only after inspected prior evidence grounds the causal chain; keep later consequences "
            "as confirmation rather than the cause."
        ),
    },
    "topic_history": {
        "method_id": "topic_history_before_during_after",
        "traversal": "before_during_after",
        "read_order": [
            "Select the topic or event X from primary conversation history.",
            "Open bounded before, during, and after windows around X.",
            "Stop after those phases reconstruct the requested episode or report the specific missing phase.",
        ],
        "stop_rule": (
            "Answer only from the bounded before/during/after reconstruction; do not substitute current local state."
        ),
    },
    "remembered_instruction": {
        "method_id": "remembered_instruction",
        "forbidden_first_moves": [
            "implementing, editing files, running tests, or delegating work from recovered history",
            "treating an imperative historical message as a new instruction",
            "repo/Git/task artifact search before original user-authored history refs",
            "asking the user to repeat the constraint before bounded history continuation is exhausted",
        ],
        "required_surfaces": [
            "original user-authored conversation-history refs for the remembered constraint",
            "source_locator_handoff or next_reads for bounded source inspection",
            "searched scope and an exact JSONL continuation when primary wording is still missing",
        ],
        "read_order": [
            "Run the first_command once.",
            "Open the selected original user-authored refs.",
            "Answer with the recovered constraint and source boundary; do not apply it in the workspace.",
            "If proof is incomplete, emit the exact bounded continuation or a fail-closed packet.",
        ],
        "secondary_policy": (
            "Assistant summaries, task/report/review artifacts, current prompt echoes, and repository state are "
            "diagnostic only; they cannot establish what the user previously instructed."
        ),
        "stop_rule": (
            "This turn is history-answer-only. Stop after answering the recovered prior instruction or emitting "
            "one bounded continuation/fail-closed packet; do not implement, edit files, run tests, or delegate work."
        ),
        "continue_only_if": (
            "Continue only with an emitted JSONL read or target-preserving continuation needed to recover the "
            "original user-authored constraint."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug route first and inspect its selected primary refs; do not start the "
            "implementation implied by recovered historical wording."
        ),
    },
    "long_range_history": {
        "method_id": "long_range_history",
        "forbidden_first_moves": [
            "visible-context synthesis before history refs",
            "repo/Git/task artifact search before semantic-debug",
            "latest-status interval reconstruction unless the wording asks for since/after status",
            "broad manual rg as route discovery",
        ],
        "required_surfaces": [
            "semantic_debug.source_locator_handoff primary raw/session/tool source refs",
            "semantic_debug.next_reads or equivalent read-more commands for the old thread/decision window",
            "searched roots, caps, omitted context, and missing prior-context facet",
        ],
        "read_order": [
            "Run the first_command once.",
            "Open primary raw conversation/session/tool refs for the old-context window.",
            "Use task/review/report/current-context echoes only as secondary diagnostics.",
            "Report searched scope/caps before claiming recovered prior-thread history.",
        ],
        "secondary_policy": (
            "Same-loop echoes, task/review/report artifacts, visible context, Git, and repo files are "
            "secondary diagnostics only until primary old-context refs are opened."
        ),
        "stop_rule": (
            "Stop when inspected primary refs answer what was decided, said, or happened before compaction "
            "or outside visible context, with searched-scope/cap caveats."
        ),
        "continue_only_if": (
            "Continue only for a named missing prior-context facet, capped/ambiguous refs, unavailable roots, "
            "or an explicit user request for secondary artifacts."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug route command first; do not parallel ask/ask --execute/repo discovery "
            "for the same evidence."
        ),
    },
    "deep_history_audit": {
        "method_id": "deep_history_audit",
        "forbidden_first_moves": [
            "rerunning ask --execute after the selected audit route has executed",
            "quick semantic-debug sample for all-chats/all-agent wording",
            "latest-status or local artifact currentness substitution",
            "broad repo/Git archaeology before audit coverage",
        ],
        "required_surfaces": [
            "semantic_debug.in_depth_audit_coverage windows/slices",
            "searched roots, cap statuses, omitted windows, and trace counts",
            "source_locator_handoff and next_reads primary raw/session/tool refs",
            "audit-coverage or copyable continuation command when coverage is incomplete",
        ],
        "read_order": [
            "Run first_command with the resolved audit depth.",
            "Inspect in_depth_audit_coverage before any completeness claim.",
            "Open promoted primary raw/session/tool refs from source_locator_handoff or next_reads.",
            "Use continuation/audit-coverage only when required coverage remains incomplete.",
        ],
        "secondary_policy": (
            "Task/report/review outputs and repo artifacts are manual leads only; they cannot close all-history "
            "or every-agent completeness without audit coverage and opened primary refs."
        ),
        "stop_rule": (
            "Stop only when coverage and inspected primary refs support the scoped answer; otherwise fail closed "
            "with searched scope, caps/omissions, missing facets, and next command."
        ),
        "continue_only_if": (
            "Continue only when coverage is incomplete, a required facet is missing, caps/omissions block the "
            "answer, or the user explicitly asks for exhaustive follow-through."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug audit-depth command first; do not rerun ask, ask --execute, broad rg, "
            "or alternate discovery before inspecting audit coverage."
        ),
    },
    "provenance": {
        "method_id": "provenance",
        "forbidden_first_moves": [
            "artifact-currentness or current Git status before historical source refs",
            "TODO/task/review/report sweeps before semantic-debug",
            "opening current-loop/evaluator/benchmark/report material before original source evidence",
            "answering from current-run echoes or post-hoc answer-key artifacts",
            "broad repo rg as chronology reconstruction",
        ],
        "required_surfaces": [
            "semantic_debug.source_locator_handoff.next_reads",
            "target historical raw JSONL/session/tool refs",
            "historical task/review/branch/commit secondary refs named by the route",
            "diagnostic-only current-run/prior-final/adjudication/report rows demoted until target reads are opened",
        ],
        "read_order": [
            "Run first_command once with the original user wording.",
            "Open target original historical raw session/tool refs before repo artifacts or diagnostics.",
            "Then inspect historical task/review/branch/commit refs for chronology roles.",
            "Only after original source evidence is opened, use current-loop/evaluator/benchmark/report material as diagnostics, never final support.",
        ],
        "secondary_policy": (
            "Historical task/review/commit refs can support chronology after raw source refs; later recaps, "
            "current-loop/evaluator/benchmark/report material, same-loop output, final-answer artifacts, and "
            "answer-key-adjacent rows are diagnostics only and stay deferred until original source evidence is opened."
        ),
        "stop_rule": (
            "Stop when opened raw refs plus legitimate historical artifacts distinguish the requested task, "
            "review, branch, commit, implementation, source-session, and merge facets."
        ),
        "continue_only_if": (
            "Continue only for a named missing chronology/source-session facet, unresolved contradiction, capped "
            "history source, or user-requested current-run audit."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug provenance command first; do not run artifact-currentness, TODO scans, "
            "or broad repo searches as parallel first moves."
        ),
    },
    "orchestration_provenance": {
        "method_id": "orchestration_provenance",
        "forbidden_first_moves": [
            "what-happened-since/status anchoring for historical launched-run provenance",
            "current-run tool-output echo recovery before original raw refs",
            "task/review/report sweeps before parent/worker/reviewer source refs",
            "broad rg/listing as coordinator/worker/reviewer discovery",
        ],
        "required_surfaces": [
            "semantic_debug.source_locator_handoff.next_reads",
            "pre-existing parent raw source refs",
            "pre-existing worker/subagent raw source refs",
            "pre-existing reviewer/raw validation source refs when the prompt asks follow-through",
            "historical provenance self-echo diagnostics after target refs",
        ],
        "read_order": [
            "Run first_command once.",
            "Open pre-existing parent/coordinator refs.",
            "Open worker/subagent refs, then reviewer/validation refs when relevant.",
            "Only then use current-run command outputs or lane prompts as diagnostics.",
        ],
        "secondary_policy": (
            "Current-run command output, lane prompts, status summaries, reports, and same-run echoes are "
            "diagnostics unless the user explicitly asks about the current run/tool/lane."
        ),
        "stop_rule": (
            "Stop when opened pre-existing parent, worker/subagent, and reviewer refs prove the launched-run "
            "provenance and follow-through facets requested."
        ),
        "continue_only_if": (
            "Continue only for a named missing parent/worker/reviewer facet, capped or ambiguous refs, or an "
            "explicit request to audit the current run."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug orchestration-provenance command first; do not parallel latest-status "
            "or broad repo recovery."
        ),
    },
    "action_proof": {
        "method_id": "action_proof",
        "forbidden_first_moves": [
            "answering from assistant claims or later summaries",
            "repo grep before conversation tool/action refs",
            "task/review/report artifact proof before tool outputs",
            "opening current-loop/evaluator/benchmark/report material before original source evidence",
            "rerunning broad discovery after primary action proof answers",
        ],
        "required_surfaces": [
            "semantic_debug.source_window_action_proof",
            "tool_action_proof primary refs and required proof reads",
            "tool calls, command outputs, diffs, commits, tests, or filesystem effects tied to the claim",
            "source_locator_handoff.next_reads for exact source follow-through",
        ],
        "read_order": [
            "Run first_command once with the original user wording.",
            "Open an independently identified original raw/session/tool source ref before any diagnostic artifact.",
            "Inspect source_window_action_proof and tool_action_proof.",
            "Open primary tool/action refs and their outputs before assistant prose.",
            "Only after original source evidence is opened, use current-loop/evaluator/benchmark/report material as diagnostics, never final support.",
            "Use diffs, commits, tests, and historical reviews only after tying them to primary action evidence.",
        ],
        "secondary_policy": (
            "Assistant prose, task/review/report summaries, current-loop/evaluator/benchmark material, and later "
            "recaps are diagnostic leads only and stay deferred until original source evidence is opened; they do "
            "not prove the agent actually ran, changed, tested, read, or followed something."
        ),
        "stop_rule": (
            "Stop when inspected primary tool/action refs answer the named action claim; report weak/no evidence "
            "instead of widening by default."
        ),
        "continue_only_if": (
            "Continue only for a specific missing action facet, weak/no evidence with too-narrow searched scope, "
            "or an explicit user request for secondary confirmation."
        ),
        "cap_language_required": True,
        "single_flight_policy": (
            "Run exactly one semantic-debug action-proof command with the original user wording first; an empty "
            "query must fail closed. Do not run broad rg or artifact sweeps before primary action refs."
        ),
    },
}


def _method_selection_packet(route_family: str, first_command: str) -> dict | None:
    template = METHOD_SELECTION_PACKET_TEMPLATES.get(route_family)
    if not template:
        return None
    packet = {
        "schema_version": METHOD_SELECTION_PACKET_SCHEMA_VERSION,
        "route_family": route_family,
        "method_id": template["method_id"],
        "first_command": first_command,
    }
    packet.update(template)
    return packet


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

    def command_query(self, query: str) -> str:
        if self.route_family == "semantic_wrapup":
            return _semantic_wrapup_first_command_query(query)
        return query

    def first_command(self, query: str, *, cli: str = "jsonl-history-search") -> str:
        command_query = self.command_query(query)
        skill = _skill_name_from_query(query)
        return self.first_command_template.format(
            query=shlex.quote(command_query),
            skill=shlex.quote(skill),
            cli=cli,
        )


LOCAL_ARTIFACT_VERIFICATION_ROUTE = AskRoute(
    route_family=LOCAL_ARTIFACT_VERIFICATION_ROUTE_FAMILY,
    chosen_route="local-artifact-verification",
    why_it_applies=(
        "The wording asks what changed in a named local artifact or whether "
        "recently edited files include or saved the requested material, so "
        "verify the worktree and named files before invoking JSONL history search."
    ),
    first_command_template=(
        "git status --short --branch && "
        "git log -1 --decorate --oneline --parents HEAD"
    ),
    read_specialization="local-artifact-verification",
    inspect_after=(
        "current HEAD, branch/detached state, and HEAD parents from the first command",
        "git diff or git show for just-created/edited artifacts",
        "targeted sed/rg against named task, evidence, TODO, report, or docs files",
        "target-bound git log/branch --contains/merge-base ancestry proof for named task status claims",
        "targeted raw JSONL/source-window reads only if artifact source coverage is missing or disputed",
    ),
    stop_guidance=(
        "Start with local artifact verification. For named task status/currentness, "
        "current HEAD, merge parents, branch reachability, and reviewed-branch ancestry "
        "override contradictory task/evidence/TODO/report/pipeline status prose. Never "
        "assert active, pending-review, unmerged, incomplete, or not-finished from stale "
        "secondary prose after Git proves the reviewed work is reachable or merged; when "
        "Git does not prove reachability or merge, stay cautious and do not invent it. Do not run "
        "broad ask/semantic-debug unless the user explicitly asks about prior "
        "conversation history or a cited source window is missing/disputed."
    ),
    manual_search_guidance=(
        "Use bounded local commands first: git status, git log, git branch --contains, "
        "git merge-base --is-ancestor, git diff, git show, and sed/rg against named "
        "files. JSONL is a targeted backstop only for "
        "conversation/source-window coverage gaps."
    ),
)

NAMED_TASK_LOCAL_ROUTE = AskRoute(
    route_family=LOCAL_ARTIFACT_VERIFICATION_ROUTE_FAMILY,
    chosen_route="named-task-local / bounded-current-git-verification",
    why_it_applies=(
        "The caller declared local evidence for an explicit tasks/...md target. "
        "Use bounded current Git, that task file, its canonical review, and exact "
        "ancestry only."
    ),
    first_command_template=(
        "git status --short --branch && "
        "git log -1 --decorate --oneline --parents HEAD"
    ),
    read_specialization="local-artifact-verification",
    inspect_after=(
        "current HEAD, branch/detached state, and HEAD parents from the first command",
        "the one named tasks/...md file",
        "the matching codex/state/reviews/<task-basename>.md file when it exists",
        "git branch --contains and git merge-base --is-ancestor for exact commits named by those two files",
    ),
    stop_guidance=(
        "Stop after current HEAD/parents, the named task, its canonical review, "
        "and exact commit ancestry answer the status/currentness question. Do not "
        "run JSONL, broad or recursive rg/find, alternate review globs, TODO or "
        "pipeline/state sweeps, report scans, whole-repo archaeology, tests, or "
        "implementation review. Current Git ancestry overrides stale status prose."
    ),
    manual_search_guidance=(
        "Use only target-bound git status/log/branch --contains/merge-base checks "
        "plus bounded reads of the named task and canonical review. If those do "
        "not prove merge/reachability, report that bounded uncertainty and stop."
    ),
)

LOCAL_CHECKOUT_LOOKUP_ROUTE = AskRoute(
    route_family=LOCAL_CHECKOUT_LOOKUP_ROUTE_FAMILY,
    chosen_route="local-checkout-lookup / git-or-rg",
    why_it_applies=(
        "The wording asks about local Git, repo, file, or codebase history/search. "
        "Use bounded local Git/rg/file inspection rather than JSONL conversation "
        "history retrieval."
    ),
    first_command_template=(
        "No JSONL command; use bounded local Git/rg for the named repo, file, "
        "branch, commit, symbol, or codebase target."
    ),
    read_specialization="none",
    inspect_after=(
        "git status/log/show/diff for Git or checkout-history questions",
        "rg --files or rg -n for ordinary repo/codebase search questions",
        "targeted file reads for named files or symbols",
    ),
    stop_guidance=(
        "Do not invoke JSONL for Git history, branch history, commit history, "
        "file history, repo history, current checkout/status/cleanliness, or "
        "ordinary codebase search. Answer from bounded local evidence."
    ),
    manual_search_guidance=(
        "Use local Git/rg/file commands scoped to the named target. Conversation "
        "history is only a later explicit backstop if the user asks why an agent "
        "discussed or decided something in prior chat."
    ),
)

NAMED_TASK_CONVERSATION_HISTORY_ROUTE = AskRoute(
    route_family="semantic_debug",
    chosen_route="named-task-conversation-history / semantic-debug",
    why_it_applies=(
        "The caller declared conversation-history as the evidence source for "
        "this named task. Use JSONL semantic debugging before local task, Git, "
        "review, TODO, or state evidence."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.source_locator_handoff primary raw/session/tool source refs",
        "top primary source refs opened with read-more before local task/review/Git evidence",
        "local task, review, branch, commit, TODO, or state evidence only as secondary context",
    ),
    stop_guidance=(
        "Open and use the promoted primary JSONL source refs before consulting "
        "local task, review, branch, commit, TODO, or state evidence. Preserve "
        "the declared conversation-history scope even when the prompt also uses "
        "local-status words."
    ),
    manual_search_guidance=(
        "Use targeted local evidence only after primary conversation-history "
        "refs have been opened, and only for a named remaining secondary facet."
    ),
)

NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE = AskRoute(
    route_family=NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY,
    chosen_route="named-task evidence-source clarification",
    why_it_applies=(
        "The prompt names an explicit tasks/...md target but does not declare "
        "whether the requested evidence is local or conversation history."
    ),
    first_command_template=(
        "No tool call; ask whether the named task should be checked from local "
        "Git/task evidence or conversation history."
    ),
    read_specialization="none",
    inspect_after=(),
    stop_guidance=(
        "Ask one short clarification. After the user answers, rerun ask with "
        "--evidence-source local or --evidence-source conversation-history. "
        "Do not run Git, JSONL, repo search, TODO/task/state scans, or filesystem inspection first."
    ),
    manual_search_guidance=(
        "There is no search step until the evidence source is declared."
    ),
)

NON_HISTORY_CLARIFICATION_ROUTE = AskRoute(
    route_family=NON_HISTORY_CLARIFICATION_ROUTE_FAMILY,
    chosen_route="non-history clarification",
    why_it_applies=(
        "The wording is a bare event question without a prior-message, "
        "conversation-history, parent/main-thread, elapsed-window, or named "
        "artifact anchor. Ask what event or change the user means without "
        "invoking JSONL/history tooling."
    ),
    first_command_template="No tool call; ask which event or change the user means.",
    read_specialization="none",
    inspect_after=(),
    stop_guidance=(
        "Ask one short clarification question. Do not invoke JSONL, semantic-debug, "
        "repo search, or another tool unless the user supplies an explicit history "
        "anchor or a concrete local target."
    ),
    manual_search_guidance=(
        "There is no search step for the bare prompt. Use the user's clarification "
        "to choose local context, a named artifact, or an explicit history route."
    ),
)

EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE = AskRoute(
    route_family=EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY,
    chosen_route="explicit history trigger required",
    why_it_applies=(
        "Latest-status implicit activation is disabled. Add an explicit history-tool "
        "alias such as leading `convo` or suffix `use the convo tool` to select "
        "conversation-history routing."
    ),
    first_command_template=(
        "No tool call; ask the user to add an explicit history-tool alias such as "
        "leading `convo` or suffix `use the convo tool`."
    ),
    read_specialization="none",
    inspect_after=(),
    stop_guidance=(
        "Use zero tools in explicit-only mode until the user supplies an explicit "
        "history-tool alias. Do not run JSONL, Git, repo search, task/state scans, "
        "or filesystem inspection."
    ),
    manual_search_guidance=(
        "There is no search step until the explicit history-tool alias is present."
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
        '{cli} ask --answer {query} --cwd "$PWD" '
        "--report-output .private/jsonl/ask-answer-no-evidence-report.json --format text"
    ),
    read_specialization="references/no-evidence.md",
    inspect_after=(
        "no_evidence_report.searched_scope / searched roots, file caps, byte caps, and date windows",
        "no_evidence_report.answer_scope == not_found_in_searched_scope",
        "candidate_interpretation and weak/context-only candidate boundary",
        "sufficient_to_stop_for_searched_scope_only and read_more_guidance.required_before_scoped_no_evidence_answer",
    ),
    stop_guidance=(
        "Read references/no-evidence.md, then run ask --answer and inspect the "
        "scoped no-evidence contract: searched scope, answer_scope, cap status, "
        "missing/excluded scope, not_all_history_absence, saved report, and "
        "read_more_required_before_scoped_no_evidence_answer. Do not run "
        "`read-more` when the searched-scope stop rule is sufficient; weak/context "
        "source reads are optional diagnostics only."
    ),
    manual_search_guidance=(
        "Manual rg or read-more is follow-up only when the searched scope is "
        "wrong, capped, missing a required root, or the user asks for weak "
        "context records to be summarized. Preserve the boundary: this proves "
        "not_found_in_searched_scope, not all-history absence."
    ),
)

RAW_PHRASE_SOURCE_SEARCH_ROUTE = AskRoute(
    route_family=RAW_PHRASE_SOURCE_SEARCH_ROUTE_FAMILY,
    chosen_route="raw-phrase-source-search / bulk-line-match",
    why_it_applies=(
        "The wording asks for exact/raw/OCR/screenshot line occurrence search "
        "across Claude/Codex/config/chat-log JSONL material, so use the local "
        "bulk phrase candidate generator instead of drifting into ad hoc manual grep."
    ),
    first_command_template=(
        '{cli} bulk-line-match --phrase <exact-phrase> '
        '--cwd "$PWD" --format text'
    ),
    read_specialization="bulk-line-match command",
    inspect_after=(
        "bulk_line_match.candidates source_ref/target rows for selected exact phrase leads",
        "bulk_line_match.searched_scope selected roots, caps, warnings, and rejected roots",
        "selected high-value refs opened with read-more before final provenance claims",
        "semantic-debug, follow-next-reads, or audit-coverage when the source question needs chronology or coverage proof",
    ),
    stop_guidance=(
        "Run bulk-line-match with repeated --phrase or --phrases-file over selected JSONL roots. "
        "Treat every row as a lead, not proof; open selected source refs with read-more and use "
        "semantic-debug/follow-next-reads/audit-coverage before final provenance."
    ),
    manual_search_guidance=(
        "Do not start with broad manual grep for this intent. Manual rg is a targeted fallback only "
        "after bulk-line-match reports the searched scope, caps, and remaining missing phrase/source facet."
    ),
)

DEEP_HISTORY_AUDIT_ROUTE = AskRoute(
    route_family="deep_history_audit",
    chosen_route="deep-history-audit / semantic-debug",
    why_it_applies=(
        "The wording asks to read, search, or audit a broad AI conversation "
        "history scope such as all chats, all agent history, the whole "
        "conversation history, or events across chats/agents/history. Use an "
        "explicit semantic-debug audit-depth workflow instead of generic "
        "ask --execute, latest-status, quick rg, or a quick semantic-debug sample."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.in_depth_audit_coverage windows/slices, searched roots, caps, and omitted windows",
        "primary raw/session/tool source refs from source_locator_handoff and next_reads",
        "coverage or cap status before claiming every/all-history completeness",
        "copyable continuation, read-more, or audit-coverage command when coverage is incomplete",
    ),
    stop_guidance=(
        "Run semantic-debug with explicit --audit-depth in-depth for broad all-chats/agent-history "
        "audits, or --audit-depth exhaustive only when the wording defines a bounded every/all scope "
        "that can be treated as a real full-coverage contract. Before answering, inspect primary refs "
        "and the in_depth_audit_coverage ledger. If caps, omitted windows, unavailable roots, or weak "
        "coverage prevent completeness, fail closed: state searched scope, caps/omissions, missing "
        "facets, and the exact continuation/read command. Do not downgrade this family to ask --execute, "
        "latest-status, broad rg, or local task/status archaeology."
    ),
    manual_search_guidance=(
        "Manual rg or artifact search is follow-up only after the audit-depth route names a missing "
        "facet or continuation. Preserve searched-scope boundaries and never claim all-history "
        "completeness from capped/top-N samples."
    ),
)

LONG_RANGE_HISTORY_ROUTE = AskRoute(
    route_family="long_range_history",
    chosen_route="long-range-history / semantic-debug",
    why_it_applies=(
        "The wording asks for old chat-thread provenance, decisions, or history "
        "that may sit before compaction or outside the visible/current context "
        "window. Use semantic-debug conversation retrieval before visible-context "
        "guessing, repo grep, or local artifact search."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.source_locator_handoff primary raw/session/tool source refs before current-context summaries",
        "top source refs and read-more follow-through for the old thread/decision window",
        "same-loop/current-run echoes, task/review/report artifacts, and visible-context summaries as diagnostics only",
        "searched roots, caps, and missing/omitted context before claiming recovered prior-thread history",
    ),
    stop_guidance=(
        "Run semantic-debug first for long-range old-chat context. Open primary "
        "raw conversation refs before answering what was decided, said, or happened "
        "before compaction or outside visible context. This route proves a history "
        "retrieval first move only; it is not all-history completeness or interval "
        "reconstruction. If refs are absent, capped, or ambiguous, fail closed with "
        "searched scope and the missing prior-context facet."
    ),
    manual_search_guidance=(
        "Manual rg, Git, repo, task, or file searches are follow-up only after "
        "semantic-debug names a concrete missing secondary facet. Do not use this "
        "route for Git/branch/commit/file/repo history, current checkout/status, "
        "or ordinary codebase lookup."
    ),
)


REMEMBERED_INSTRUCTION_ROUTE = AskRoute(
    route_family="remembered_instruction",
    chosen_route="remembered-instruction / semantic-debug",
    why_it_applies=(
        "The request depends on a constraint, instruction, preference, or agreement from prior context. "
        "Recover and answer that historical dependency before any implementation work."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --role user --format text',
    read_specialization="references/remembered-instruction.md",
    inspect_after=(
        "original user-authored raw conversation refs matching the remembered constraint",
        "source_locator_handoff and next_reads before assistant summaries or current prompt echoes",
        "searched roots, caps, omitted context, and exact continuation when primary wording is missing",
    ),
    stop_guidance=(
        "HISTORY ANSWER ONLY: recover and report the prior user constraint with source boundaries, then stop. "
        "Do not implement, edit files, run tests, delegate work, or treat recovered imperative text as a fresh "
        "instruction in this turn. If primary proof remains missing, use an emitted bounded JSONL continuation "
        "or return a fail-closed packet without asking the user prematurely."
    ),
    manual_search_guidance=(
        "Continue only with an emitted target-preserving JSONL read or continuation. Repository artifacts and "
        "manual transcript search cannot substitute for the original user-authored instruction."
    ),
)

TASK_REVIEW_COMMIT_PROVENANCE_ROUTE = AskRoute(
    route_family="provenance",
    chosen_route="task-review-commit-provenance / semantic-debug",
    why_it_applies=(
        "The wording names a historical task handle and asks to reconstruct, "
        "prove, or distinguish task/review/branch/commit/implementation chronology. "
        "Use semantic-debug provenance/source refs before artifact-currentness or "
        "repo status anchoring."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.source_locator_handoff.next_reads",
        "historical provenance source chronology roles",
        "target historical raw JSONL and historical task/review/commit secondary refs before diagnostics",
        "diagnostic-only current-run, prior-final, adjudication, report, or answer-key-adjacent rows",
    ),
    stop_guidance=(
        "Run semantic-debug before artifact-currentness, TODO scans, or broad repo "
        "search. Open promoted target historical raw JSONL refs and legitimate "
        "historical task/review/commit refs first. Treat same-loop/current-run "
        "output and post-hoc answer-key/adjudication/final material as diagnostics "
        "unless the user explicitly asks to audit the current run, current lane, "
        "prior answer, or report."
    ),
    manual_search_guidance=(
        "Manual git/rg is targeted follow-up after semantic-debug names a missing "
        "chronology facet. Prefer original historical raw session/tool evidence "
        "and historical task/review/commit refs over current-run echoes or reports "
        "created after the target work."
    ),
)

DEVLOG_FAILURE_ANALYSIS_ROUTE = AskRoute(
    route_family="devlog_failure_analysis",
    chosen_route="devlog/failure-analysis historical-source-reach",
    why_it_applies=(
        "The wording asks for a devlog/story reconstruction of agent or "
        "coordinator failure behavior with dated concrete examples. Use the "
        "devlog/failure-analysis source-reach package so target historical "
        "source-window reads and safe repo-phase/category evidence appear "
        "before current/adjacent diagnostics."
    ),
    first_command_template=(
        '{cli} ask --execute {query} --cwd "$PWD" --format text '
        "--report-output .private/jsonl/ask-execute-devlog-source-reach.json"
    ),
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "ask_execute.executed_output.chronology_source_reach required roles, exact missing roles, and copyable next reads",
        "ask_execute.executed_output.target_historical_source_window_reads before diagnostic reads",
        "ask_execute.executed_output.failure_analysis.devlog_brief and linked failure-analysis report",
        "ask_execute.executed_output.repo_phase_category_evidence safe repo-phase/category packet",
        "current/adjacent/same-loop diagnostics only after target historical reads, unless the user asks to audit current output",
    ),
    stop_guidance=(
        "Run ask --execute for the devlog/failure-analysis package before broad "
        "repo archaeology. Inspect chronology_source_reach first and open its "
        "role-bound copyable next reads; answer only when primary history covers "
        "first appearance, worsening process cause, and later change. Then "
        "use the linked failure_analysis.devlog_brief and safe repo-phase/category "
        "evidence as boundaries. Treat current/adjacent/same-loop diagnostics as "
        "non-actionable by default unless the user explicitly asks to audit the "
        "current route, current output, current lane, or prior answer."
    ),
    manual_search_guidance=(
        "Manual rg or repo artifact reading is follow-up only for named missing "
        "story facets after the package. Prefer target historical source windows "
        "and safe repo-phase/category evidence; do not answer mainly from current "
        "diagnostics or post-hoc artifacts."
    ),
)

COORDINATOR_CONTEXT_ROUTE = AskRoute(
    route_family="coordinator_context",
    chosen_route="coordinator-context / current-context-next-reads",
    why_it_applies=(
        "The wording asks for a coordinator or receiving-agent current-context "
        "package across repo front-door surfaces such as TODO, pipeline/current "
        "state, plans, active tasks, goals, guiding principles, learnings, and "
        "failure modes to watch. Route to the committed-safe context package "
        "before generic semantic-wrapup complaint mining."
    ),
    first_command_template=(
        '{cli} ask --execute {query} --cwd "$PWD" --format text '
        "--report-output .private/jsonl/ask-execute-coordinator-context.json"
    ),
    read_specialization="references/coordinator-context.md",
    inspect_after=(
        "ask_execute.executed_output.current_state and active_task_handles",
        "ask_execute.executed_output.required_next_reads over TODO, pipeline/current-context state, active tasks, and learnings",
        "ask_execute.executed_output.why_goals_guiding_principles",
        "ask_execute.executed_output.learnings_failure_modes and claim_boundaries",
    ),
    stop_guidance=(
        "Run ask --execute for the coordinator-context package before broad "
        "semantic-wrapup or repo archaeology. Open required next reads first, "
        "then answer from inspected committed-safe front-door/current-state files. "
        "Treat optional diagnostics as follow-up only when a named current-context "
        "facet remains missing or contradictory."
    ),
    manual_search_guidance=(
        "Manual rg or raw JSONL inspection is follow-up only after the context "
        "package leaves a named gap. This route is a committed-safe current "
        "context/next-read package, not source-window recovery, launch readiness, "
        "all-history completeness, token savings, or a final answer generator."
    ),
)

ACTIVE_STATUS_FRONT_DOOR_ROUTE = AskRoute(
    route_family="active_status_front_door",
    chosen_route="active-status-front-door / coordinator-context",
    why_it_applies=(
        "The wording asks for the active/current repo task, coordinator state, "
        "or next legal action. Use the repo front-door state files instead of "
        "raw conversation chronology or semantic-debug diagnostics."
    ),
    first_command_template=(
        '{cli} ask --execute {query} --cwd "$PWD" --format text '
        "--report-output .private/jsonl/ask-execute-active-status-front-door.json"
    ),
    read_specialization="references/coordinator-context.md",
    inspect_after=(
        "ask_execute.executed_output.active_status_front_door.active_task_path",
        "ask_execute.executed_output.active_status_front_door.fsm_state",
        "ask_execute.executed_output.active_status_front_door.next_legal_action",
        "front-door refs only: codex/state/pipeline-state.md, codex/state/current-context-window.md, TODO.md, and active task file",
    ),
    stop_guidance=(
        "Answer current repo-status questions from inspected front-door files: "
        "pipeline state, current context window, TODO front door, and the active task. "
        "Do not use those files as raw source-session chronology, worker action chronology, "
        "or causal expectation-mismatch proof."
    ),
    manual_search_guidance=(
        "Manual search is follow-up only when the front-door files are missing, "
        "contradictory, or not this repo. Preserve the boundary between local "
        "front-door state and unproven upstream/release currentness."
    ),
)

STORAGE_RETENTION_FORENSICS_ROUTE = AskRoute(
    route_family=STORAGE_RETENTION_ROUTE_FAMILY,
    chosen_route="storage-retention-forensics / filesystem-inventory-first",
    why_it_applies=(
        "The wording asks why local private/storage artifacts are large, what "
        "created them, who uses them, or what must be retained. The decisive "
        "evidence starts with disk layout and committed repo artifact joins, not "
        "recent conversation transcript echoes."
    ),
    first_command_template=STORAGE_ROOT_INVENTORY_COMMAND,
    read_specialization="references/storage-retention.md",
    inspect_after=(
        "filesystem_inventory: bounded checkout-local and parent-retained size inventory (depth 2, top 40) with dominant storage classes and representative subtree handles",
        "generic_retention_policy: read the retention gate, reviewed ledger, state-file policy, and latest authorized outcome in one bounded command",
        "answer_ready_stop: answer the broad question from root sizes, dominant path classes, representative handles, and generic policy; mark exact creator identity unknown when it is not established",
        "optional_named_handle_join: only for one specific unresolved facet or an explicit user request, never once per inventory bucket",
        "echo_demotion: recent command-output echoes are diagnostics unless they contain primary creation or actual-use evidence",
    ),
    stop_guidance=(
        "Run only the bounded depth-2/top-40 root inventory and emitted "
        "generic_retention_policy command for a broad .private question. Then answer "
        "with total/root size, dominant storage classes, representative handles, their "
        "policy-backed retention status, and explicit uncertainty for any unverified exact "
        "creator. Stop: do not scan each bucket, serially inspect older artifacts, or "
        "require exact producer/consumer provenance for a class-level broad answer. Use "
        "one named-handle or producer/consumer join only for a specific unresolved facet "
        "material to the answer or when the user explicitly asks for that handle. Do not run "
        "uncapped recursive rg, rg --files -uu, or broad repo file dumps. Do not "
        "run storage cleanup, deletion, archiving, moves, "
        "VHDX compaction, branch pruning, or claim/release copy from this route. "
        "Use JSONL conversation search only after a named creator/history gap "
        "survives local inventory and artifact joins."
    ),
    manual_search_guidance=(
        "Deeper reads are optional, not default next reads. Do not recover generic "
        "retention policy through historical archaeology. Use a fixed-string rg join "
        "for one named subtree/task handle only when the user requested that exact facet "
        "or its unresolved classification would materially change the broad answer. If a "
        "specific subtree still lacks creator or usage evidence, run a narrowly "
        "scoped semantic-debug query for that subtree/task handle; preserve cap, "
        "root, and searched-scope caveats. Demote recent ask/command-output "
        "echoes unless they are primary creation/use records."
    ),
)

EXPECTATION_MISMATCH_ROUTE = AskRoute(
    route_family="expectation_mismatch",
    chosen_route="expectation-mismatch / semantic-debug",
    why_it_applies=(
        "The wording asks why observed AI/agent behavior diverged from an "
        "expected stop, instruction, plan, or task state. Route to "
        "semantic-debug so expectation evidence, observed/action-proof "
        "evidence, and the divergence point are inspected before diagnostics."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.expectation_mismatch_plan.expected_behavior_evidence",
        "semantic_debug.expectation_mismatch_plan.observed_behavior_or_action_proof",
        "semantic_debug.expectation_mismatch_plan.divergence_evidence",
        "semantic_debug.core_convo_first_read_plan",
        "semantic_debug.expectation_mismatch_plan.diagnostic_or_post_hoc only after target reads",
    ),
    stop_guidance=(
        "Run semantic-debug before broad diagnostics. Open expectation, observed/"
        "action-proof, and divergence reads first; use current/same-loop/post-hoc "
        "diagnostics only after those target historical reads leave a named gap."
    ),
    manual_search_guidance=(
        "Manual rg/read-more is follow-up only after the expectation-mismatch "
        "plan names a missing expected, observed, action-proof, or divergence "
        "facet. Do not answer mainly from reports, current status, or same-loop echoes."
    ),
)


REMEMBERED_USER_WORDING_ROUTE = AskRoute(
    route_family="remembered_user_wording",
    chosen_route="remembered-user-wording / semantic-debug",
    why_it_applies=(
        "The wording asks when, where, or what the user said, asked for, "
        "told, or requested about a remembered topic. Route to user-role "
        "semantic-debug so source refs prove user-authored wording instead "
        "of assistant, tool, environment, task, report, or prompt echoes."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --role user --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.question_family / query_plan detected_intents includes when_user_said",
        "user-authored source refs and timestamps before any assistant/tool/env/task/report echoes",
        "source_locator_handoff.next_reads and compact read-more follow-through for exact user message context",
        "role filtering, Codex history/session mirror dedupe, and source-window boundaries before claiming wording",
    ),
    stop_guidance=(
        "Run semantic-debug with --role user before broad manual search. Do not "
        "claim what the user said, asked, told, or requested until inspected "
        "source refs show user-authored text with timestamps/source handles; "
        "assistant, tool, environment, task, report, TODO, review, project-guidance, "
        "and current prompt echoes are diagnostics only and cannot substitute for that proof. If bounded "
        "primary user history is unavailable or yields no user-authored ref, stop "
        "with missing evidence instead of searching the repository."
    ),
    manual_search_guidance=(
        "Continue only when the user supplies a quote/timestamp or a real primary "
        "Codex/Claude history root becomes available. Do not run broad recursive "
        "repo search, TODO/task/report/review archaeology, or project-guidance "
        "substitution for a question about what the user personally said; assistant "
        "summaries, task files, reports, reviews, and echoed prompts remain non-proof."
    ),
)


LATEST_USER_MESSAGES_ROUTE = AskRoute(
    route_family="latest_user_messages",
    chosen_route="latest-user-messages / deterministic user-message lookup",
    why_it_applies=(
        "The wording asks only for the user's most recent/latest/last message "
        "or wording, without a topic or status window. Use deterministic "
        "latest-user lookup before semantic search."
    ),
    first_command_template='{cli} latest-user-messages --cwd "$PWD" --limit 1 --format text',
    read_specialization="latest-user-messages command",
    inspect_after=(
        "latest_user_messages.messages[0].timestamp_utc, text, source_ref, and openable_target",
        "latest_user_messages.lookup == deterministic_latest_user_messages",
        "deduplication_strategy and mirror_sources before treating history/session mirrors as distinct messages",
        "sources warnings, searched files/bytes, and cap boundaries before claiming global latest wording",
    ),
    stop_guidance=(
        "Run latest-user-messages first. Answer only from the newest "
        "deduplicated user-authored message and preserve timestamp/source ref; "
        "do not use semantic-debug unless the latest lookup is capped, empty, "
        "or the user asks for a topical/history explanation."
    ),
    manual_search_guidance=(
        "Manual search is follow-up only when latest-user-messages reports an "
        "empty/capped/ambiguous scope or the prompt adds a topic, date, status "
        "window, or explanation request."
    ),
)


CAUSAL_HISTORY_ROUTE = AskRoute(
    route_family="causal_history",
    chosen_route="causal-history / semantic-debug",
    why_it_applies=(
        "The caller explicitly requested conversation history for a concrete causal event. "
        "Select the event, inspect prior causal/provenance evidence backward first, then check consequences."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.directional_history_reconstruction event selection",
        "backward-first prior instructions, decisions, setup, tool outputs, errors, and reviews",
        "forward consequence check only after causal evidence is grounded",
    ),
    stop_guidance=(
        "Execute the emitted backward-first reconstruction stages in order. Do not treat a later outcome "
        "as the cause until prior source evidence grounds it."
    ),
    manual_search_guidance=(
        "Use only the emitted bounded continuation for a named missing causal facet; current local state is not a substitute."
    ),
)


TOPIC_HISTORY_ROUTE = AskRoute(
    route_family="topic_history",
    chosen_route="topic-history / semantic-debug",
    why_it_applies=(
        "The caller explicitly requested conversation history for a concrete topic or event. "
        "Reconstruct a bounded before/during/after episode around the selected source event."
    ),
    first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
    read_specialization="references/semantic-debugging.md",
    inspect_after=(
        "semantic_debug.directional_history_reconstruction selected topic/event",
        "bounded before context",
        "bounded during event evidence",
        "bounded after consequences",
    ),
    stop_guidance=(
        "Execute the emitted before/during/after stages in order and stop when the bounded episode is reconstructed."
    ),
    manual_search_guidance=(
        "Continue only for a named missing before, during, or after phase; do not switch to current local task evidence."
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
        first_command_template='{cli} skill-check {skill} --cwd "$PWD" --format text',
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
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
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
            "The wording asks whether an agent actually performed a claimed "
            "action, so the first evidence should be tool/action proof from "
            "conversation logs."
        ),
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
        read_specialization="references/action-proof.md",
        inspect_after=(
            "semantic_debug.source_window_action_proof",
            "tool_action_proof primary refs and required proof reads",
            "source_locator_handoff / next_reads for exact source follow-through",
        ),
        stop_guidance=(
            "Run the action-proof route before broad rg. If primary proof answers "
            "the claimed-action question, stop broad searching and answer from inspected refs."
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
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
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
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
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
        first_command_template='{cli} what-happened-since {query} --cwd "$PWD" --format text',
        read_specialization="none; latest-status guidance is self-contained in the status command output",
        inspect_after=(
            "the answer-ready parent/main path when emitted: selected canonical anchor, required post-anchor raw session reads, one bounded secondary confirmation batch, then answer and stop",
            "status_anchor.parent_main_anchor_shortlist and selected_anchor",
            "for main-thread wording, parent_main_anchor_shortlist must select one unique high-confidence human history.jsonl row or fail closed before answer",
            "status_anchor.session_activity raw sessions/**/*.jsonl trace after the anchor",
            "JSON-only rejected candidates, diagnostics, source_tiers, and next_reads only after naming a required facet that remains missing or contradictory",
        ),
        stop_guidance=(
            "Run the status route before task/repo recovery scans. Use "
            "the answer-ready parent/main path when emitted: open the selected canonical anchor, "
            "open the required post-anchor raw session refs, run at most the one bounded secondary "
            "confirmation batch, then answer and stop. Do not rerun JSON/JQ extraction, open rejected/"
            "diagnostic/optional refs, or broaden manual search unless a named required facet remains "
            "missing or contradictory after those reads. For "
            "main-thread/parent-thread status wording, do not answer from inherited context or a "
            "current-session prompt; inspect status_anchor.parent_main_anchor_shortlist and do not "
            "answer unless it selects one unique high-confidence human history.jsonl row."
        ),
        manual_search_guidance=(
            "Repo or task scans are follow-up only after the bounded answer-ready path reports a named "
            "required facet that is still missing or contradictory, or cannot find a canonical anchor. "
            "Treat commits and repo state as secondary support, never as the status anchor; "
            "never run broad session/state/task-family/Git archaeology after the required reads complete."
        ),
    ),
    AskRoute(
        route_family="remembered_topic",
        chosen_route="remembered-topic / semantic-debug",
        why_it_applies=(
            "The wording asks why a topic was mentioned, which is provenance for "
            "a remembered topic rather than a currentness decision."
        ),
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
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
            "current, removable, left behind, or cleanup/prune candidates, so "
            "artifact-currentness should inspect operational cleanup records, "
            "worktree state, and private-artifact preservation first."
        ),
        first_command_template='{cli} artifact-currentness {query} --cwd "$PWD" --format markdown',
        read_specialization="references/status-currentness.md",
        inspect_after=(
            "artifact_currentness.worktree_currentness.cleanup_causality.operational_records and debt_status",
            "artifact_currentness.worktree_currentness.classifications",
            "causal_source_role order for report_only_audit, deferred_handoff, cleanup_learning_policy, and cleanup_execution",
            "for a missing named target, only target_binding plus exact worktree/branch Git proof",
            "per-item source_refs and preservation guidance only after a named target is bound",
        ),
        stop_guidance=(
            "Run artifact-currentness before shallow git cleanup conclusions. Stop "
            "after the original cleanup role chain is sufficient; for a missing named target, "
            "stop after exact current Git absence without generic cleanup or fixture archaeology."
        ),
        manual_search_guidance=(
            "Manual inspection is follow-up only when the route reports a real source-role gap. "
            "Do not treat worktree cleanup as branch ref pruning or use adjacent fixtures as target proof."
        ),
    ),
    AskRoute(
        route_family="artifact_currentness",
        chosen_route="artifact-currentness",
        why_it_applies=(
            "The wording asks which TODO/plan/ticket/artifact matters or is current, so "
            "the currentness route should resolve stale versus active evidence and treat status fields as claims."
        ),
        first_command_template='{cli} artifact-currentness {query} --cwd "$PWD" --format markdown',
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
        first_command_template='{cli} semantic-wrapup {query} --cwd "$PWD" --format text',
        read_specialization="references/semantic-wrapup.md",
        inspect_after=(
            "semantic_wrapup.failure_modes",
            "semantic_wrapup.fix_suggestions",
            "semantic_wrapup.next_reads where read_role == required_primary_proof before proposing any fix",
            "optional_diagnostic and conditional_required reads only for named proof/completeness gaps",
        ),
        stop_guidance=(
            "Run semantic-wrapup before broad session/manual narrowing. Open required_primary_proof "
            "refs first, then stop when failure modes are supported; do not auto-apply fixes "
            "or open optional diagnostics by default."
        ),
        manual_search_guidance=(
            "Manual session narrowing or read-more is follow-up only for a named unsupported, "
            "incomplete, or ambiguous failure mode, for conditional_required cap/omission coverage, "
            "or when the user asks for exhaustive diagnostics after semantic-wrapup runs."
        ),
    ),
)


def build_ask_router_payload(
    query: str,
    cwd: str = ".",
    *,
    evidence_source: EvidenceSource | None = None,
    activation_mode: ActivationMode = "implicit",
    audit_depth: str | None = None,
) -> dict:
    explicit_history_tool_alias = _explicit_history_tool_alias(query)
    route_query = _explicit_history_tool_alias_query(query) or query
    route = classify_ask_route(
        query,
        evidence_source=evidence_source,
        activation_mode=activation_mode,
    )
    explicit_convo_trigger = explicit_history_tool_alias is not None
    named_task_target = _has_explicit_task_path(query)
    structured_execution_query = route.command_query(route_query)
    normalized_route_query = _normalize_for_route(route_query)
    audit_depth_payload = _route_audit_depth_payload(
        route=route,
        normalized_query=normalized_route_query,
        structured_execution_query=structured_execution_query,
        requested=audit_depth,
    )
    if route.route_family in NON_JSONL_COMMAND_ROUTE_FAMILIES:
        if route.route_family == LOCAL_ARTIFACT_VERIFICATION_ROUTE_FAMILY:
            resolution_guidance = (
                "No JSONL command resolution is needed for this local-artifact route; "
                "use its local/Git commands."
            )
        elif route.route_family == NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY:
            resolution_guidance = (
                "No command resolution is allowed until the named-task evidence source is declared."
            )
        elif route.route_family == EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY:
            resolution_guidance = (
                "No command resolution is allowed until explicit-only mode receives an explicit "
                "history-tool alias such as leading `convo` or suffix `use the convo tool`."
            )
        else:
            resolution_guidance = "No JSONL command resolution is needed for this no-tool route."
        command_resolution_payload = {
            "mode": "not_applicable",
            "command_prefix": None,
            "installed_command_available": None,
            "source_checkout_available": None,
            "source_fallback_command": None,
            "guidance": resolution_guidance,
        }
        cli_prefix = "jsonl-history-search"
    else:
        command_resolution = resolve_command(cwd)
        command_resolution_payload = command_resolution.as_dict()
        cli_prefix = command_resolution.command_prefix
    first_command = _first_command_with_audit_depth(
        route.first_command(route_query, cli=cli_prefix),
        route_family=route.route_family,
        audit_depth=audit_depth_payload,
    )
    if route.route_family == "latest_status" and _looks_like_parent_main_complete_interval_status_query(
        normalized_route_query
    ):
        first_command = _parent_main_complete_interval_status_command(
            cli_prefix=cli_prefix,
            query=structured_execution_query,
        )
    method_selection_packet = _method_selection_packet(route.route_family, first_command)
    explicit_task_match = _EXPLICIT_TASK_PATH_RE.search(query.lower())
    named_task_path = explicit_task_match.group(0) if explicit_task_match else ""
    if route.route_family == STORAGE_RETENTION_ROUTE_FAMILY:
        first_action_category = "filesystem_inventory"
    elif route.route_family in {
        NON_HISTORY_CLARIFICATION_ROUTE_FAMILY,
        NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY,
        EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY,
    }:
        first_action_category = "no_tool"
    else:
        first_action_category = "route_command"
    cleanup_requested = (
        route.route_family == STORAGE_RETENTION_ROUTE_FAMILY
        and storage_cleanup_requested(query)
    )
    answer_ready_handoff = None
    optional_deeper_reads: list[dict] = []
    if route.route_family == STORAGE_RETENTION_ROUTE_FAMILY:
        answer_ready_handoff = {
            "answer_status": "answer-after-bounded-inventory-and-policy",
            "required_evidence": ["filesystem_inventory", "generic_retention_policy"],
            "answer_facets": [
                "total and parent-retained root size",
                "dominant storage classes and representative subtree handles",
                "class-level creation/use attribution supported by paths and policy",
                "policy-backed retention status and explicit exact-producer uncertainty",
            ],
            "stop": (
                "After the two required reads, answer the broad question and stop. Exact creator/consumer "
                "identity may remain explicitly unknown; that alone does not require per-bucket joins."
            ),
            "only_continue_if": (
                "The user explicitly asks about one named handle/producer/consumer/exact artifact, or one "
                "specific unresolved facet would materially change the broad answer."
            ),
            "answer_boundary": (
                "Cleanup was explicitly requested; discuss policy without performing or authorizing mutation."
                if cleanup_requested
                else (
                    "The prompt did not request cleanup. Describe retained classes, size contributors, creation/use "
                    "at class level, and retention requirements only. Do not recommend reclaiming space or changing, "
                    "removing, relocating, archiving, or compacting stored artifacts."
                )
            ),
            "cleanup_request_status": "explicit" if cleanup_requested else "not_requested",
        }
        optional_deeper_reads = storage_optional_deeper_reads(cli_prefix=cli_prefix)
    if route.route_family in {
        NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE_FAMILY,
        EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE_FAMILY,
        LOCAL_CHECKOUT_LOOKUP_ROUTE_FAMILY,
    }:
        next_commands = []
    elif route is NAMED_TASK_LOCAL_ROUTE:
        next_commands = named_task_local_next_commands(
            selected_first_command=first_command,
            task_path=named_task_path,
        )
    else:
        next_commands = ask_next_commands(
            selected_first_command=first_command,
            route_family=route.route_family,
            cli_prefix=cli_prefix,
            query=query,
        )
    return {
        "ask_router": {
            "status": "matched",
            "query": query,
            "cwd": cwd,
            "evidence_source": evidence_source,
            "evidence_source_applies": named_task_target,
            "activation_mode": activation_mode,
            "audit_depth": audit_depth_payload,
            "explicit_convo_trigger": explicit_convo_trigger,
            "explicit_history_tool_alias": explicit_history_tool_alias,
            "chosen_route": route.chosen_route,
            "route_family": route.route_family,
            "first_action_category": first_action_category,
            "why_it_applies": route.why_it_applies,
            "structured_execution_query": structured_execution_query,
            "command_resolution": command_resolution_payload,
            "first_command": first_command,
            "first_command_contract": {
                "copy_exactly": True,
                "query_quoting": "POSIX shell quoting via shlex.quote",
                "instruction": (
                    "Copy First command exactly. Never re-wrap the original prompt in double quotes; "
                    "backticks and $(...) must remain literal query text."
                ),
            },
            "method_selection_packet": method_selection_packet,
            "next_commands": next_commands,
            "answer_ready_handoff": answer_ready_handoff,
            "optional_deeper_reads": optional_deeper_reads,
            "command_cookbook": (
                []
                if route.route_family in NON_JSONL_COMMAND_ROUTE_FAMILIES
                else agent_question_cookbook(cli_prefix=cli_prefix)
            ),
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


def execution_kind_for_route_family(route_family: str) -> str | None:
    if route_family in EXECUTABLE_ARTIFACT_CURRENTNESS_ROUTE_FAMILIES:
        return "artifact-currentness"
    if route_family in EXECUTABLE_LATEST_STATUS_ROUTE_FAMILIES:
        return "what-happened-since"
    if route_family in EXECUTABLE_DEVLOG_FAILURE_ANALYSIS_ROUTE_FAMILIES:
        return "devlog-failure-analysis"
    if route_family in EXECUTABLE_COORDINATOR_CONTEXT_ROUTE_FAMILIES:
        return "coordinator-context"
    if route_family in EXECUTABLE_SEMANTIC_DEBUG_ROUTE_FAMILIES:
        return "semantic-debug"
    if route_family in EXECUTABLE_NO_EVIDENCE_ROUTE_FAMILIES:
        return "no-evidence-report"
    if route_family in EXECUTABLE_SEMANTIC_WRAPUP_ROUTE_FAMILIES:
        return "semantic-wrapup"
    return None


def _first_command_with_audit_depth(command: str, *, route_family: str, audit_depth: dict) -> str:
    if route_family not in EXECUTABLE_SEMANTIC_DEBUG_ROUTE_FAMILIES:
        return command
    if audit_depth.get("source") == "default" and audit_depth.get("depth") == "quick":
        return command
    label = audit_depth.get("label") or "quick"
    audit_arg = f" --audit-depth {shlex.quote(str(label))}"
    if " --format " in command:
        return command.replace(" --format ", f"{audit_arg} --format ", 1)
    return f"{command}{audit_arg}"


def _looks_like_parent_main_complete_interval_status_query(normalized: str) -> bool:
    parent_main = _has_any(
        normalized,
        (
            "parent thread",
            "main thread",
            "parent/main",
            "main/parent",
        ),
    )
    if not parent_main:
        return False
    since_or_after = _has_any(
        normalized,
        (
            "what happened since",
            "what has happened since",
            "what happened after",
            "what has happened after",
            "since my last",
            "since my latest",
            "after my last",
            "after my latest",
        ),
    )
    message_anchor = _has_any(
        normalized,
        (
            "last message",
            "latest message",
            "last user message",
            "latest user message",
            "last actual message",
            "latest actual message",
            "last substantive instruction",
            "latest substantive instruction",
            "last substantive message",
            "last real instruction",
        ),
    )
    return since_or_after and message_anchor


def _parent_main_complete_interval_status_command(*, cli_prefix: str, query: str) -> str:
    return (
        f"{cli_prefix} what-happened-since {shlex.quote(query)} "
        '--cwd "$PWD" --parent-thread --complete-interval --format answer'
    )


def _route_audit_depth_payload(
    *,
    route: AskRoute,
    normalized_query: str,
    structured_execution_query: str,
    requested: str | None,
) -> dict:
    payload = resolve_audit_depth(structured_execution_query, requested)
    if route.route_family != "deep_history_audit" or payload.get("source") == "explicit":
        return payload
    if _looks_like_scoped_exhaustive_history_audit(normalized_query):
        return {
            "depth": "exhaustive",
            "label": "exhaustive",
            "contract": "exhaustive_scoped_audit",
            "source": "inferred_deep_history_audit_route",
            "requested": None,
            "reason": (
                "deep-history audit wording names a bounded every/all scope where "
                "exhaustive coverage can be treated as a real contract"
            ),
        }
    return {
        "depth": "in_depth",
        "label": "in-depth",
        "contract": "in_depth_audit",
        "source": "inferred_deep_history_audit_route",
        "requested": None,
        "reason": (
            "broad all-chats/agent-history audit wording requires explicit in-depth "
            "coverage; exhaustive is withheld until a bounded full-coverage scope is present"
        ),
    }


def _normalize_for_route(query: str) -> str:
    return " ".join(query.lower().replace("?", " ").replace("!", " ").split())


def classify_ask_route(
    query: str,
    *,
    evidence_source: EvidenceSource | None = None,
    activation_mode: ActivationMode = "implicit",
) -> AskRoute:
    explicit_query = _explicit_history_tool_alias_query(query)
    classification_query = explicit_query if explicit_query is not None else query
    normalized = " ".join(classification_query.lower().replace("?", " ").replace("!", " ").split())

    if _EXPLICIT_TASK_PATH_RE.search(normalized):
        if evidence_source == "local":
            return NAMED_TASK_LOCAL_ROUTE
        if evidence_source == "conversation-history":
            return NAMED_TASK_CONVERSATION_HISTORY_ROUTE
        return NAMED_TASK_EVIDENCE_SOURCE_REQUIRED_ROUTE

    if explicit_query is not None and _looks_like_concrete_causal_history_query(normalized):
        return CAUSAL_HISTORY_ROUTE

    if explicit_query is not None and _looks_like_concrete_topic_history_query(normalized):
        return TOPIC_HISTORY_ROUTE

    if _looks_like_skill_compliance_query(normalized) and not _looks_like_action_proof_query(normalized):
        return ROUTES[0]

    if _looks_like_reviewed_source_window_prompt(normalized):
        return ROUTES[1]

    if _looks_like_bounded_no_evidence_query(normalized):
        return BOUNDED_NO_EVIDENCE_ROUTE

    if _looks_like_raw_phrase_source_search_query(normalized):
        return RAW_PHRASE_SOURCE_SEARCH_ROUTE

    if _looks_like_explicit_named_local_task_artifact_query(normalized):
        return LOCAL_ARTIFACT_VERIFICATION_ROUTE

    if _looks_like_explicit_named_task_provenance_query(normalized):
        return TASK_REVIEW_COMMIT_PROVENANCE_ROUTE

    if _looks_like_storage_retention_forensics_query(normalized):
        return STORAGE_RETENTION_FORENSICS_ROUTE

    if _looks_like_process_actual_use_query(normalized):
        return ROUTES[3]

    if _looks_like_parent_main_status_anchor_failure_query(normalized):
        return ROUTES[5]

    if _looks_like_remembered_topic_causal_instruction_query(normalized):
        return ROUTES[6]

    if _looks_like_expectation_mismatch_artifact_status_query(normalized):
        if _looks_like_expectation_mismatch_artifact_latest_status_query(normalized):
            return ROUTES[5]
        return ROUTES[8]

    if _looks_like_expectation_mismatch_query(normalized):
        return EXPECTATION_MISMATCH_ROUTE

    if _looks_like_coordinator_context_query(normalized):
        return COORDINATOR_CONTEXT_ROUTE

    if _looks_like_historical_orchestration_episode_query(normalized):
        return ROUTES[4]

    if _looks_like_task_window_status_handoff_query(normalized):
        return ROUTES[5]

    if _looks_like_latest_status_anchor_query(normalized):
        if activation_mode == "explicit-only" and explicit_query is None:
            return EXPLICIT_HISTORY_TRIGGER_REQUIRED_ROUTE
        return ROUTES[5]

    if _looks_like_current_checkout_status_query(normalized):
        return LOCAL_CHECKOUT_LOOKUP_ROUTE

    if _looks_like_active_status_front_door_query(normalized):
        return ACTIVE_STATUS_FRONT_DOOR_ROUTE

    if _looks_like_current_local_scope_query(normalized):
        return ROUTES[8]

    if _looks_like_implicit_prior_instruction_dependency(normalized):
        return REMEMBERED_INSTRUCTION_ROUTE

    if _looks_like_deep_history_audit_query(normalized):
        return DEEP_HISTORY_AUDIT_ROUTE

    if _looks_like_long_range_history_query(normalized):
        return LONG_RANGE_HISTORY_ROUTE

    if _looks_like_devlog_failure_analysis_query(normalized):
        return DEVLOG_FAILURE_ANALYSIS_ROUTE

    if _looks_like_orchestration_provenance_query(normalized):
        return ROUTES[4]

    if _looks_like_action_proof_query(normalized):
        return ROUTES[2]

    if _looks_like_recency_only_user_wording_query(normalized):
        return LATEST_USER_MESSAGES_ROUTE

    if _looks_like_remembered_user_wording_query(normalized):
        return REMEMBERED_USER_WORDING_ROUTE

    if _has_any(normalized, ("why did i mention", "why did i say", "what was i talking about", "why was i talking about")):
        return ROUTES[6]

    if (
        not _looks_like_explicit_named_task_prior_conversation_query(normalized)
        and _looks_like_task_review_commit_provenance_query(normalized)
    ):
        return TASK_REVIEW_COMMIT_PROVENANCE_ROUTE

    if is_worktree_cleanup_currentness_query(query):
        return ROUTES[7]

    if _looks_like_local_checkout_lookup_query(normalized):
        return LOCAL_CHECKOUT_LOOKUP_ROUTE

    if _looks_like_named_ticket_causal_provenance_query(normalized):
        return TASK_REVIEW_COMMIT_PROVENANCE_ROUTE

    if _looks_like_local_artifact_verification_query(normalized):
        return LOCAL_ARTIFACT_VERIFICATION_ROUTE

    if _has_any(normalized, ("which todo", "which plan", "which task", "actually matters", "is current", "still what")):
        return ROUTES[8]

    if _looks_like_ticket_currentness(normalized):
        return ROUTES[8]

    if _looks_like_current_conversation_audit_query(normalized):
        return ROUTES[9]

    if _looks_like_repeated_complaint_or_failure_query(normalized):
        return ROUTES[9]

    if _looks_like_generic_non_history_query(normalized):
        return NON_HISTORY_CLARIFICATION_ROUTE

    if _looks_like_generic_writing_or_visible_context_query(normalized):
        return NON_HISTORY_CLARIFICATION_ROUTE

    if _looks_like_generic_history_tool_alias_non_history_query(normalized):
        return NON_HISTORY_CLARIFICATION_ROUTE

    return AskRoute(
        route_family="semantic_debug",
        chosen_route="semantic-debug",
        why_it_applies=(
            "The wording looks like a natural conversation-history or provenance "
            "question, but it did not match a narrower front-door route."
        ),
        first_command_template='{cli} semantic-debug {query} --cwd "$PWD" --format text',
        read_specialization="references/semantic-debugging.md",
        inspect_after=(
            "semantic_debug.source_locator_handoff primary raw/session/tool source refs from the first semantic-debug run",
            "top primary source refs / next_reads opened with read-more before secondary task/review/report/commit artifacts",
            "secondary task/review/report/commit artifacts only after primary refs establish chronology, action, wording, or provenance",
            "post-hoc answer-key, same-loop/current-run, prior-final, and report-created-after-target artifacts as diagnostics only",
            "no-evidence searched scope before widening",
        ),
        stop_guidance=(
            "Run semantic-debug before broad manual search. Open primary raw/"
            "session/tool source refs from that first route output before "
            "answering from task, review, report, or commit artifacts. Stop "
            "when inspected primary refs plus any necessary secondary context "
            "cover the named facets; fail closed with the searched scope and "
            "missing facet when primary refs are absent, weak, capped, or "
            "contradicted."
        ),
        manual_search_guidance=(
            "Manual rg is a targeted follow-up after route output, not the first "
            "move for a natural history question. Prefer compact text read-more "
            "commands over raw JSON source-window output. Treat repo task/review/"
            "report/commit searches as secondary joins after primary source "
            "refs are opened, unless semantic-debug explicitly returns scoped "
            "no-evidence or a named missing-primary-source blocker."
        ),
    )


def _explicit_convo_trigger_query(query: str) -> str | None:
    return _explicit_history_tool_alias_query(query)


_EXPLICIT_HISTORY_TOOL_ALIAS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("convo tool", r"\bconvo\s+tool\b"),
    ("convo cli", r"\bconvo\s+cli\b"),
    ("jsonl-search-history", r"\bjsonl[-\s]+search[-\s]+history\b"),
    ("jsonl-history-search", r"\bjsonl[-\s]+history[-\s]+search\b"),
    ("jsonl tool", r"\bjsonl\s+tool\b"),
    ("the skill", r"\bthe\s+skill\b"),
    ("convo", r"^\s*convo(?:\s*:\s*|\s+)"),
)


_EXPLICIT_HISTORY_TOOL_DIRECTIVE_RE = re.compile(
    r"""
    (?:
        (?:^|[\s,;:.!?])
        (?:
            use|using|with|via|through|run|invoke|call|ask|load|try
        )
        \s+
        (?:
            the\s+
        )?
        (?P<alias>
            convo\s+tool|
            convo\s+cli|
            jsonl[-\s]+search[-\s]+history|
            jsonl[-\s]+history[-\s]+search|
            jsonl\s+tool|
            the\s+skill
        )
        \s*\.?\s*$
    )
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


def _explicit_history_tool_alias(query: str) -> dict | None:
    normalized = _normalize_for_route(query)
    stripped = _strip_explicit_history_tool_alias(query)
    classification_normalized = _normalize_for_route(stripped or query)
    if not _looks_like_history_tool_alias_context(classification_normalized):
        return None
    for label, pattern in _EXPLICIT_HISTORY_TOOL_ALIAS_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return {
                "matched_alias": label,
                "canonical_command": "jsonl-history-search",
                "source_checkout_fallback": "PYTHONPATH=src python3 -m jsonl_history_search.cli",
                "guidance": (
                    "Resolve this explicit history-tool alias to the local "
                    "`jsonl-history-search` CLI/skill workflow. Use the installed "
                    "command first, or the source-checkout fallback when the console "
                    "script is unavailable in a package checkout. Do not start with "
                    "connector discovery, plugin install, or `convo --help`."
                ),
            }
    return None


def _explicit_history_tool_alias_query(query: str) -> str | None:
    if _explicit_history_tool_alias(query) is None:
        return None
    stripped = _strip_explicit_history_tool_alias(query)
    return stripped or None


def _strip_explicit_history_tool_alias(query: str) -> str | None:
    leading = re.match(r"^\s*convo(?:\s*:\s*|\s+)(?P<query>.+?)\s*$", query, flags=re.IGNORECASE | re.DOTALL)
    if leading:
        return leading.group("query").strip() or None
    leading_named_tool = re.match(
        (
            r"^\s*(?:convo\s+tool|convo\s+cli|jsonl[-\s]+search[-\s]+history|"
            r"jsonl[-\s]+history[-\s]+search|jsonl\s+tool)"
            r"(?:\s*:\s*|\s+)(?P<query>.+?)\s*$"
        ),
        query,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if leading_named_tool:
        return leading_named_tool.group("query").strip() or None
    stripped = _EXPLICIT_HISTORY_TOOL_DIRECTIVE_RE.sub(" ", query).strip()
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped if stripped != query.strip() else None


def _looks_like_history_tool_alias_context(normalized: str) -> bool:
    if not normalized:
        return False
    return (
        _looks_like_current_conversation_audit_query(normalized)
        or
        _looks_like_latest_status_anchor_query(normalized)
        or _looks_like_concrete_causal_history_query(normalized)
        or _looks_like_concrete_topic_history_query(normalized)
        or _looks_like_long_range_history_query(normalized)
        or _looks_like_deep_history_audit_query(normalized)
        or _looks_like_devlog_failure_analysis_query(normalized)
        or _looks_like_orchestration_provenance_query(normalized)
        or _looks_like_action_proof_query(normalized)
        or _looks_like_recency_only_user_wording_query(normalized)
        or _looks_like_remembered_user_wording_query(normalized)
        or _looks_like_remembered_topic_causal_instruction_query(normalized)
        or _looks_like_process_actual_use_query(normalized)
        or _looks_like_expectation_mismatch_query(normalized)
        or _has_any(
            normalized,
            (
                "chat history",
                "conversation history",
                "prior conversation",
                "prior message",
                "previous message",
                "source provenance",
                "source refs",
                "source sessions",
                "raw session",
                "raw history",
                "history logs",
                "action proof",
                "tool evidence",
                "what happened at",
                "what happened since",
                "what has happened since",
            ),
        )
    )


def _concrete_history_event_target(normalized: str, pattern: str) -> str | None:
    match = re.fullmatch(pattern, normalized.strip(" .?!"))
    if not match:
        return None
    target = " ".join(match.group("target").strip(" .?!").split())
    if target in {"it", "this", "that", "this event", "that event", "here"}:
        return None
    local_markers = (
        "this task file",
        "the task file",
        "this current task",
        "the current task",
        "this branch",
        "the current branch",
        "this repo",
        "the current repo",
        "this repository",
        "the current repository",
        "this checkout",
        "the current checkout",
        "this workspace",
        "the current workspace",
        "local file",
        "local task",
        "local branch",
        "local repo",
    )
    if any(marker in target for marker in local_markers):
        return None
    return target or None


def _looks_like_concrete_causal_history_query(normalized: str) -> bool:
    return _concrete_history_event_target(normalized, r"why did (?P<target>.+?) happen") is not None


def _looks_like_concrete_topic_history_query(normalized: str) -> bool:
    return (
        _concrete_history_event_target(
            normalized,
            r"what (?:has )?happened with (?P<target>.+)",
        )
        is not None
    )


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


_CURRENT_SESSION_SCOPE_TERMS = (
    "in this session",
    "this session",
    "current session",
    "current-session",
    "this run",
    "current run",
    "latest session",
    "current thread",
    "this thread",
    "this chat",
    "current chat",
    "this conversation",
    "current conversation",
)


_BROAD_HISTORY_SCOPE_TERMS = (
    "historically",
    "historical",
    "history",
    "all history",
    "all-history",
    "broad history",
    "whole history",
    "entire history",
    "across sessions",
    "cross-session",
    "cross session",
    "previous sessions",
    "prior sessions",
    "past sessions",
    "old sessions",
    "over time",
)


def _semantic_wrapup_first_command_query(query: str) -> str:
    normalized = " ".join(query.lower().replace("?", " ").replace("!", " ").split())
    if not _looks_like_bare_correction_or_complaint_prompt(normalized):
        return query
    if _has_any(normalized, _CURRENT_SESSION_SCOPE_TERMS):
        return query
    if _has_any(normalized, _BROAD_HISTORY_SCOPE_TERMS):
        return query
    return f"{query.rstrip(' ?!')} in this session?"


def _looks_like_bare_correction_or_complaint_prompt(normalized: str) -> bool:
    return _has_any(
        normalized,
        (
            "what did i keep correcting",
            "what have i kept correcting",
            "what did i keep complaining",
            "what have i kept complaining",
            "what failure modes did i keep correcting",
            "what failure modes did i keep complaining",
            "which user corrections kept recurring",
            "which corrections kept recurring",
            "what did i keep yelling",
            "what have i kept yelling",
        ),
    )


def _looks_like_current_conversation_audit_query(normalized: str) -> bool:
    """Recognize a narrow, deictic audit of the active conversation.

    A bare ``this`` remains ambiguous.  The current-chat/conversation anchor and
    an audit/failure/fix intent must both be present so explicit alias handling
    does not turn writing or local-status prompts into history searches.
    """
    current_conversation = _has_any(
        normalized,
        ("this chat", "current chat", "this conversation", "current conversation"),
    )
    audit_intent = _has_any(
        normalized,
        (
            "audit",
            "what went wrong",
            "what went badly",
            "failure",
            "failed",
            "things to fix",
            "thing to fix",
            "suggestion for improvement",
            "suggestions for improvement",
            "suggestion to improve",
            "suggestions to improve",
            "improve",
            "fix",
        ),
    )
    return current_conversation and audit_intent


def _looks_like_action_proof_query(normalized: str) -> bool:
    if _has_any(normalized, ("action proof", "source/action proof", "tool/action proof")):
        return True
    actor_terms = (
        "agent",
        "worker",
        "implementer",
        "reviewer",
        "coordinator",
        "assistant",
        "ai",
        "model",
        "codex",
        "claude",
    )
    action_terms = (
        "actually do",
        "actually did",
        "actually add",
        "actually added",
        "actually run",
        "actually ran",
        "actually edit",
        "actually edited",
        "actually render",
        "actually rendered",
        "actually test",
        "actually tested",
        "actually review",
        "actually reviewed",
        "actually merge",
        "actually merged",
        "actually commit",
        "actually committed",
        "performed",
        "execute",
        "executed",
        "run the test",
        "run tests",
        "ran tests",
        "rendered",
        "render",
        "add it",
        "added it",
        "reviewed",
        "merged",
        "committed",
        "overwrite",
        "overwrote",
        "clobber",
        "revert",
        "deleted",
        "edit",
        "source/action proof",
        "tool/action",
        "primary tool/action",
    )
    claim_contrast_terms = (
        "only claim",
        "only claimed",
        "merely claim",
        "merely claimed",
        "versus claim",
        "vs claim",
        "or claim",
        "claimed it",
        "actually do versus claim",
    )
    return _has_any(normalized, actor_terms) and (
        _has_any(normalized, action_terms) or _has_any(normalized, claim_contrast_terms)
    )


def _looks_like_storage_retention_forensics_query(normalized: str) -> bool:
    intent_text = _replace_explicit_task_paths_with_identifiers(normalized)
    compact = " ".join(re.sub(r"[_/.,;:()]+", " ", intent_text).split())
    storage_subject_terms = (
        ".private",
        " private ",
        "private file",
        "private files",
        "private artifact",
        "private artifacts",
        "private directory",
        "large file",
        "large files",
        "large subtree",
        "disk usage",
        "disk space",
        "storage",
        "gb",
        "gib",
        "vhdx",
        "c drive",
        "c:",
        "repo storage",
        "retention",
        "retained",
        "retain",
    )
    forensic_intent_terms = (
        "why is",
        "why are",
        "why does",
        "why did",
        "what created",
        "who created",
        "what uses",
        "who uses",
        "what is using",
        "who is using",
        "what must be retained",
        "what needs to be retained",
        "must retain",
        "need to retain",
        "needs to retain",
        "safe to delete",
        "can i delete",
        "can we delete",
        "can be deleted",
        "can be archived",
        "cleanup",
        "clean up",
        "archive",
        "retention",
        "retained",
        "retain",
        "claim",
        "claims",
        "examples",
        "tests",
    )
    if ".private" in normalized and _has_any(compact, forensic_intent_terms):
        return True
    if _has_any(compact, storage_subject_terms) and _has_any(compact, forensic_intent_terms):
        return True
    return False


def _looks_like_raw_phrase_source_search_query(normalized: str) -> bool:
    normalized = normalized.replace("_", " ").replace("/", " ")
    search_terms = ("search", "scan", "find", "match", "locate")
    occurrence_terms = (
        "occurrence",
        "occurrences",
        "candidate source ref",
        "candidate source refs",
        "candidate refs",
        "source ref",
        "source refs",
        "final provenance",
        "validate before",
    )
    phrase_terms = (
        "exact phrase",
        "exact phrases",
        "raw phrase",
        "raw phrases",
        "literal phrase",
        "literal phrases",
        "ocr phrase",
        "ocr phrases",
        "ocr line",
        "ocr lines",
        "extracted line",
        "extracted lines",
        "screenshot phrase",
        "screenshot phrases",
        "screenshot line",
        "screenshot lines",
    )
    source_terms = (
        "raw claude",
        "raw codex",
        "raw jsonl",
        "jsonl source",
        "jsonl sources",
        "config source",
        "config sources",
        "claude/config",
        "claude config",
        "codex/config",
        "jsonl convo",
        "convo",
        "chat log",
        "chat logs",
        "chat history",
        "chat histories",
        "conversation history",
        "conversation histories",
        "histories",
        "source-ref",
        "source refs",
    )
    has_phrase_intent = _has_any(normalized, phrase_terms) or (
        _has_any(normalized, ("exact", "literal", "ocr", "screenshot"))
        and _has_any(normalized, ("phrase", "phrases", "line", "lines"))
    )
    has_search_intent = _has_any(normalized, search_terms) or (
        _has_any(normalized, occurrence_terms)
        and _has_any(normalized, ("produce", "candidate", "validate", "provenance", "source ref", "source refs"))
    )
    return has_search_intent and has_phrase_intent and _has_any(normalized, source_terms)


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
    if len(_historical_task_handles(normalized)) != 1:
        return False
    current_run_scope = _has_any(
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
    ) or bool(
        re.search(
            r"\b(?:current|this)\s+(?:[a-z0-9_-]+\s+)?(?:evaluation\s+)?run\b",
            normalized,
        )
    )
    current_run_scope_is_negated = bool(
        re.search(
            r"\b(?:not|rather than)\s+(?:about\s+)?(?:the\s+)?current\s+(?:[a-z0-9_-]+\s+)?(?:evaluation\s+)?run\b",
            normalized,
        )
    )
    if current_run_scope and not current_run_scope_is_negated:
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


def _looks_like_historical_orchestration_episode_query(normalized: str) -> bool:
    """Reserve task-specific orchestration provenance for raw-source discovery.

    This deliberately sits ahead of generic status handoff detection.  A
    multi-task range/status handoff remains a status request, and an explicit
    current-run request remains outside historical discovery.  A separately
    named, explicitly negated current evaluation run does not turn the one
    historical episode into a two-task status handoff.
    """
    if not _looks_like_orchestration_provenance_query(normalized):
        return False
    if len(_historical_task_handles(normalized)) != 1:
        return False
    return _has_any(
        normalized,
        (
            "historical",
            "pre-existing",
            "provenance",
            "actually did",
            "what changed",
            "what was verified",
            "what remains",
        ),
    )


def _historical_task_handles(normalized: str) -> set[str]:
    handles = {
        match.group(0)
        for match in re.finditer(rf"\b{_TASK_HANDLE_PATTERN}\b", normalized)
    }
    current_run_handles = {
        match.group("handle") for match in _CURRENT_TASK_RUN_HANDLE_RE.finditer(normalized)
    }
    return handles - current_run_handles


def _looks_like_task_review_commit_provenance_query(normalized: str) -> bool:
    if _looks_like_explicit_current_or_prior_answer_audit(normalized):
        return False
    has_task_handle = bool(re.search(r"\b[a-z]{2,}[-_]\d{2,}[a-z0-9_-]*\b", normalized))
    if not has_task_handle:
        return False
    asks_for_proof_or_chronology = _has_any(
        normalized,
        (
            "reconstruct",
            "prove",
            "proof",
            "source chronology",
            "chronology",
            "provenance",
            "source chain",
            "evidence chain",
            "distinguish",
            "what actually happened",
            "what happened",
            "where did",
            "find the source",
        ),
    )
    if not asks_for_proof_or_chronology:
        return False
    if "who actually merged" in normalized or ("merged" in normalized and "what proves" in normalized):
        return True
    facet_hits = sum(
        1
        for terms in (
            ("task", "ticket"),
            ("review", "reviewer", "rereview", "rereviewer"),
            ("branch", "worktree"),
            ("commit", "merge", "sha"),
            ("implementation", "implemented", "fix", "fixed"),
            ("source", "raw evidence", "jsonl", "session", "tool evidence"),
        )
        if any(term in normalized for term in terms)
    )
    return facet_hits >= 2


def _looks_like_named_ticket_causal_provenance_query(normalized: str) -> bool:
    if _looks_like_explicit_current_or_prior_answer_audit(normalized):
        return False
    if _looks_like_action_proof_query(normalized):
        return False
    has_named_task_or_ticket = bool(
        re.search(r"\b[a-z]{2,}-\d+[a-z0-9_-]*\b", normalized)
        or re.search(r"\b[a-z]+[-_][a-z0-9]+[-_][a-z0-9_-]*\b", normalized)
    )
    if not has_named_task_or_ticket:
        return False
    causal_shape = _has_any(
        normalized,
        (
            "why did",
            "why was",
            "why were",
            "what caused",
            "what made",
            "how did",
        ),
    )
    if not causal_shape:
        return False
    causal_or_failure_terms = (
        "fail",
        "failed",
        "failure",
        "fail closed",
        "failed closed",
        "regress",
        "regressed",
        "regression",
        "blocked",
        "blocker",
        "stalled",
        "broke",
        "broken",
        "wrong",
        "bug",
        "before the fix",
        "cause",
        "caused",
        "happen",
        "happened",
    )
    return _has_any(normalized, causal_or_failure_terms)


def _looks_like_devlog_failure_analysis_query(normalized: str) -> bool:
    if _looks_like_explicit_current_or_prior_answer_audit(normalized):
        return False
    story_terms = (
        "devlog",
        "story",
        "segment",
        "reconstruct the arc",
        "reconstruct",
        "what actually happened",
        "what happened in",
        "turn the",
    )
    failure_terms = (
        "failure",
        "failure mode",
        "got stuck",
        "became stuck",
        "bogged down",
        "lost in",
        "drift",
        "drifting",
        "pulled into",
        "sounded productive",
        "away from the product",
        "process mechanics",
        "workflow/process",
        "coordinator/review overhead",
        "concrete examples",
        "separate what you can prove",
        "plausible interpretation",
    )
    recovery_terms = (
        "returning to product",
        "returned to product",
        "return to product",
        "back to product",
        "back toward product",
        "back towards product",
        "shifted to product",
        "started prioritizing product",
        "resumed product",
    )
    agent_work_terms = (
        "coordinator",
        "agent",
        "agents",
        "runner",
        "harness",
        "docker",
        "auth",
        "live-send",
        "live send",
        "review overhead",
        "product behavior",
    )
    asks_for_story = _has_any(normalized, story_terms)
    asks_for_failure = _has_any(normalized, failure_terms)
    asks_about_agent_work = _has_any(normalized, agent_work_terms)
    asks_for_dates_or_examples = _has_any(
        normalized,
        (
            "dates",
            "dated",
            "timeline",
            "arc",
            "concrete examples",
            "examples",
            "prove",
            "proof",
            "evidence",
        ),
    )
    asks_for_recovery = _has_any(normalized, recovery_terms)
    long_form_story = (
        asks_for_story
        and asks_for_failure
        and asks_about_agent_work
        and (asks_for_dates_or_examples or asks_for_recovery)
    )
    chronology_story = (
        asks_for_failure
        and asks_about_agent_work
        and _has_any(
            normalized,
            (
                "first appear",
                "first showed",
                "first show",
                "first started",
                "where it began",
                "where did it begin",
                "when it began",
                "when did it begin",
                "breakdown started",
                "failure started",
                "failure began",
            ),
        )
        and _has_any(
            normalized,
            (
                "made it worse",
                "got worse",
                "worsened",
                "compounded",
                "amplified",
                "aggravated",
            ),
        )
        and _has_any(
            normalized,
            (
                "changed later",
                "what changed",
                "changed afterward",
                "changed afterwards",
                "change afterward",
                "change afterwards",
                "fixed later",
                "shifted later",
                "later shift",
            ),
        )
    )
    return long_form_story or chronology_story


def _looks_like_deep_history_audit_query(normalized: str) -> bool:
    if not normalized:
        return False
    if _has_any(
        normalized,
        (
            "git history",
            "branch history",
            "commit history",
            "file history",
            "repo history",
            "repository history",
            "working tree",
            "worktree",
            "local status",
            "cleanliness",
        ),
    ):
        return False
    broad_history_scope = _has_any(
        normalized,
        (
            "all chats",
            "all the chats",
            "all prior chats",
            "all previous chats",
            "all earlier chats",
            "all chat history",
            "all conversation history",
            "all conversations",
            "all agent history",
            "whole conversation history",
            "entire conversation history",
            "complete conversation history",
            "across chats",
            "across agents",
            "across agent history",
            "across history",
            "across conversations",
            "across sessions",
            "through all chats",
            "through all the chats",
            "through all agent history",
            "every conversation in",
            "every session in",
        ),
    )
    outcome_audit_history_scope = (
        _has_any(
            normalized,
            (
                "chatlogs",
                "chat logs",
                "convo",
                "conversation history",
                "chat history",
                "history",
            ),
        )
        and _has_any(
            normalized,
            (
                "long-running coordinator",
                "long running coordinator",
                "coordinator session",
                "coordinator thread",
                "long-running session",
                "long running session",
            ),
        )
        and _has_any(
            normalized,
            (
                "accepted",
                "committed",
                "reviewed",
                "reviewer",
                "task outcomes",
                "task outcome",
                "claimed completed",
                "claimed complete",
                "completed work",
                "whether the agent claimed",
            ),
        )
    )
    broad_history_scope = broad_history_scope or outcome_audit_history_scope
    if not broad_history_scope:
        return False
    audit_or_search_intent = _has_any(
        normalized,
        (
            "read through",
            "go through",
            "look through",
            "search through",
            "audit",
            "inspect",
            "review",
            "find when",
            "find where",
            "find whether",
            "find if",
            "trace when",
            "trace where",
            "when did",
            "where did",
            "whether",
        ),
    )
    return audit_or_search_intent


def _looks_like_scoped_exhaustive_history_audit(normalized: str) -> bool:
    if not _has_any(normalized, ("every", "all", "fully", "full", "exhaustive")):
        return False
    if not _has_any(
        normalized,
        (
            "conversation",
            "conversations",
            "session",
            "sessions",
            "trace",
            "traces",
            "agent",
            "agents",
            "subagent",
            "subagents",
            "chat",
            "chats",
        ),
    ):
        return False
    return _has_any(
        normalized,
        (
            "bounded source scope",
            "bounded history scope",
            "selected source scope",
            "selected root",
            "specified root",
            "provided root",
            "given root",
            "within this source scope",
            "within the source scope",
            "inside this source scope",
            "inside the source scope",
            "in this bounded scope",
            "in the bounded scope",
            "in this selected root",
            "in the selected root",
            "in this provided root",
            "in the provided root",
            "in this specified root",
            "in the specified root",
            "involved in the prior failure",
            "involved in this failure",
            "from this incident",
            "from the incident",
        ),
    )


def _looks_like_current_local_scope_query(normalized: str) -> bool:
    if _has_any(
        normalized,
        (
            "current branch",
            "branch cleanliness",
            "checkout status",
            "repo status",
            "repository status",
            "local status",
            "cleanliness",
        ),
    ):
        return False
    if _has_any(
        normalized,
        (
            "conversation history",
            "prior conversation",
            "source session",
            "source proof",
            "historical source",
            "history.jsonl",
            "provenance",
            "git history",
            "branch history",
            "commit history",
            "file history",
            "repo history",
            "repository history",
        ),
    ):
        return False
    current_scope = _has_any(
        normalized,
        (
            "this current",
            "current local",
            "current checkout",
            "this checkout",
            "current workspace",
            "this workspace",
            "current repo",
            "current repository",
            "this repo",
            "this repository",
        ),
    )
    local_target = _has_any(
        normalized,
        (
            "file",
            "artifact",
            "task",
            "checkout",
            "workspace",
            "repo",
            "repository",
        ),
    )
    return current_scope and local_target


def _looks_like_local_checkout_lookup_query(normalized: str) -> bool:
    if _looks_like_non_conversation_history_scope(normalized):
        return True
    code_lookup = _has_any(
        normalized,
        (
            "search the repo",
            "search this repo",
            "search the repository",
            "grep for",
            "rg for",
            "find where",
            "find the file",
            "where is",
            "where function",
            "where class",
            "where method",
            "is defined",
            "defined in",
            "codebase",
            "source code",
        ),
    )
    code_target = bool(
        re.search(r"\b[a-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|md|toml|yaml|yml|json)\b", normalized)
        or re.search(r"\bfunction\s+[a-z_][a-z0-9_]*\b", normalized)
        or re.search(r"\bclass\s+[a-z_][a-z0-9_]*\b", normalized)
    )
    return code_lookup and code_target


def _looks_like_current_checkout_status_query(normalized: str) -> bool:
    if _looks_like_history_tool_alias_context(normalized):
        return False
    status_intent = bool(
        re.search(r"\b(?:check|status|clean|dirty|branch|repo|repository)\b", normalized)
    ) or _has_any(
        normalized,
        ("what happened", "what is", "what's", "cleanliness"),
    )
    return status_intent and _has_any(
        normalized,
        (
            "current branch",
            "branch cleanliness",
            "this branch",
            "on this branch",
            "current checkout",
            "checkout status",
            "current repo status",
            "current repository status",
            "local repo status",
            "local repository status",
            "status of local repo",
            "status of the local repo",
            "status of local repository",
            "status of the local repository",
            "repo status",
            "repository status",
            "local status",
            "current task status",
            "this task status",
            "current status of this task",
            "status of this task",
        ),
    )


def _looks_like_explicit_current_or_prior_answer_audit(normalized: str) -> bool:
    current_scope = _has_any(
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
            "current report",
            "this report",
        ),
    )
    prior_answer_or_report = _has_any(
        normalized,
        (
            "prior answer",
            "previous answer",
            "earlier answer",
            "last answer",
            "final answer",
            "this report",
            "that report",
            "prior report",
            "previous report",
            "adjudication report",
        ),
    )
    audit = _has_any(normalized, ("audit", "check", "inspect", "verify", "did the answer", "did it cite"))
    return current_scope or (prior_answer_or_report and audit)


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


def _looks_like_generic_history_tool_alias_non_history_query(normalized: str) -> bool:
    if not _has_any(normalized, ("convo", "the skill", "this skill", "that skill")):
        return False
    return not _looks_like_history_tool_alias_context(normalized)


def _looks_like_latest_status_anchor_query(normalized: str) -> bool:
    if _has_any(
        normalized,
        (
            "what happened since",
            "what has happened since",
            "what happened after compaction",
            "what happened after context compaction",
            "what happened after the context got compacted",
            "what has happened after compaction",
            "what has happened after context compaction",
            "what has happened after the context got compacted",
            "what changed since",
            "what changed while i was away",
            "what did you do while i was away",
            "while i was away",
            "since my last",
            "since my latest",
            "message before",
            "last substantive instruction",
            "latest substantive instruction",
            "last substantive message",
            "last real instruction",
            "status since",
            "latest status",
            "status update",
            "after i told",
            "keep working",
        ),
    ):
        return True
    if re.search(r"^what (?:has )?happened before\b", normalized):
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


def _looks_like_long_range_history_query(normalized: str) -> bool:
    if not normalized:
        return False
    if _looks_like_non_conversation_history_scope(
        normalized
    ) and not _looks_like_old_context_repo_history_query(normalized):
        return False
    long_range_anchor = _has_any(
        normalized,
        (
            "before compaction",
            "before context compaction",
            "before the compaction",
            "before the context got compacted",
            "before this context got compacted",
            "before current context",
            "before the current context",
            "before visible context",
            "before the visible context",
            "outside visible context",
            "outside the visible context",
            "outside current context",
            "outside the current context",
            "outside this context window",
            "outside the current context window",
            "before this context window",
            "before the current context window",
            "far back in chat history",
            "far back through chat history",
            "far back in the chat history",
            "earlier in the thread",
            "earlier in this thread",
            "earlier in the chat",
            "earlier in this chat",
            "old chat history",
            "old conversation history",
        ),
    )
    if not long_range_anchor:
        return False
    old_chat_context = _has_any(
        normalized,
        (
            "chat",
            "thread",
            "conversation",
            "history",
            "context",
            "context window",
            "visible context",
            "compaction",
            "compacted",
        ),
    )
    if not old_chat_context:
        return False
    provenance_intent = _has_any(
        normalized,
        (
            "what did we decide",
            "what did i decide",
            "what was decided",
            "what was the decision",
            "decision from",
            "recover what happened",
            "recover the decision",
            "recover what we decided",
            "what happened in the thread",
            "what happened earlier in the thread",
            "what happened earlier in this thread",
            "what happened before this context window",
            "what happened before the current context window",
            "what did we agree",
            "what did i ask",
            "what did i tell",
            "what was my ask",
            "what was the ask",
            "what was the plan",
            "what was said",
            "why did we",
            "why did i",
            "why was",
            "review",
            "trace the old",
            "trace what",
            "find the prior",
            "recover",
            "what happened",
        ),
    )
    return provenance_intent


def _looks_like_old_context_repo_history_query(normalized: str) -> bool:
    if not _has_any(
        normalized,
        (
            "before compaction",
            "before context compaction",
            "before the compaction",
            "before visible context",
            "before the visible context",
            "outside visible context",
            "outside the visible context",
            "outside current context",
            "outside the current context",
        ),
    ):
        return False
    if not _has_any(normalized, ("repo history", "repository history", "current repo history")):
        return False
    return not _has_any(
        normalized,
        (
            "git history",
            "branch history",
            "commit history",
            "file history",
            "for this branch",
            "on this branch",
            "search the repo",
            "search this repo",
            "grep for",
            "rg for",
            "where function",
        ),
    )


def _looks_like_non_conversation_history_scope(normalized: str) -> bool:
    return _has_any(
        normalized,
        (
            "git history",
            "branch history",
            "commit history",
            "file history",
            "repo history",
            "repository history",
            "current checkout",
            "current branch",
            "current repo",
            "current repository",
            "checkout status",
            "repo status",
            "repository status",
            "local status",
            "cleanliness",
            "worktree",
            "working tree",
            "codebase",
            "source code",
            "search the repo",
            "search this repo",
            "grep for",
            "rg for",
            "where function",
        ),
    )


def _looks_like_recency_only_user_wording_query(normalized: str) -> bool:
    if _has_any(
        normalized,
        (
            "what happened",
            "what has happened",
            "what changed",
            "while i was away",
            "since my last",
            "since my latest",
            "before this worker",
            "before the worker",
            "before this reviewer",
            "before the reviewer",
            "before this side",
            "before the side",
            "status",
            "catch me up",
        ),
    ):
        return False
    return any(
        re.search(pattern, normalized)
        for pattern in (
            r"\bwhat did i (?:say|ask|tell|request) (?:most recently|recently|latest|last)\b",
            r"\bwhat did i last (?:say|ask|tell|request)\b",
            r"\bwhat (?:was|is) my (?:most recent|latest|last) (?:message|ask|request|instruction)\b",
            r"\bshow (?:me )?my (?:most recent|latest|last) (?:message|ask|request|instruction)\b",
        )
    )


def _looks_like_remembered_user_wording_query(normalized: str) -> bool:
    if _looks_like_recency_only_user_wording_query(normalized):
        return False
    return any(
        re.search(pattern, normalized)
        for pattern in (
            r"\b(?:when|where) did i (?:say|tell|ask|request)\b",
            r"\bwhat exactly did i ask for(?:\b| about\b)",
            r"\bwhat did i ask for(?:\b| about\b)",
            r"\bwhat did i (?:say|tell|ask|request) (?:you )?before about\b",
            r"\bwhat did i (?:say|tell|ask|request) (?:you )?about\b",
            r"\bwhat constraint did i give (?:you )?(?:about|on|for)\b",
            r"\bwhat was my (?:ask|request|instruction) about\b",
        )
    )


def _looks_like_implicit_prior_instruction_dependency(normalized: str) -> bool:
    if not normalized:
        return False
    historical_dependency = _has_any(
        normalized,
        (
            "constraint i gave",
            "instruction i gave",
            "preference i gave",
            "requirement i gave",
            "rule i gave",
            "using what i told",
            "using what we agreed",
            "using the constraint",
            "using the instruction",
            "using the preference",
            "we agreed on",
            "i already gave",
            "i gave before",
        ),
    )
    prior_context = _has_any(
        normalized,
        (
            "before this context",
            "before current context",
            "before the current context",
            "outside visible context",
            "outside the visible context",
            "prior context",
            "previous context",
            "earlier context",
        ),
    )
    continuation_request = _has_any(
        normalized,
        ("continue", "resume", "finish", "proceed", "carry on", "apply", "use"),
    )
    return historical_dependency and (prior_context or continuation_request)


def _looks_like_remembered_topic_causal_instruction_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    if " instead of " not in normalized:
        return False
    match = re.search(
        r"\bwhy did i (?:mention|say|ask for|request|bring up|call out)\s+(.+?)\s+instead of\s+(.+)\b",
        normalized,
    )
    if not match:
        return False
    left_terms = _substantive_topic_terms(match.group(1))
    right_terms = _substantive_topic_terms(match.group(2))
    return bool(left_terms and right_terms)


def _substantive_topic_terms(text: str) -> set[str]:
    stopwords = {
        "about",
        "again",
        "agent",
        "ask",
        "asked",
        "codex",
        "did",
        "for",
        "instead",
        "main",
        "mention",
        "say",
        "that",
        "the",
        "this",
        "why",
        "you",
    }
    terms = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.casefold()):
        term = raw.replace("_", "-").strip("-")
        if term not in stopwords:
            terms.add(term)
    return terms


def _looks_like_expectation_mismatch_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    explicit_mismatch_shape = _has_any(
        normalized,
        (
            "expectation mismatch",
            "expectation-mismatch",
            "mismatch behind",
            "mismatch caused",
        ),
    )
    if explicit_mismatch_shape and _looks_like_expectation_mismatch_artifact_status_query(normalized):
        return False
    if explicit_mismatch_shape and _has_any(
        normalized,
        (
            "behind",
            "caused",
            "cause",
            "episode",
            "situation",
            "jsh-",
            "stopped",
            "not-auditing",
            "agent",
            "ai",
            "assistant",
            "codex",
            "diverged",
            "instead of",
        ),
    ):
        return True
    causal_shape = _has_any(
        normalized,
        (
            "why did",
            "why was",
            "why were",
            "why didn't",
            "why did it",
            "why did the ai",
            "why did the agent",
            "why did the assistant",
            "how did it end up",
            "how did the agent end up",
        ),
    )
    if not causal_shape:
        return False
    expectation_terms = (
        "expected",
        "expecting",
        "i expected",
        "we expected",
        "supposed to",
        "should have",
        "should've",
        "asked it to",
        "asked the agent to",
        "told it to",
        "told the agent to",
        "instructed",
        "instruction",
        "prior instruction",
        "plan was",
        "expected it to stop",
        "stop after",
        "instead of",
    )
    observed_terms = (
        "ai",
        "agent",
        "assistant",
        "codex",
        "it kept",
        "kept",
        "keep",
        "looping",
        "loop",
        "continued",
        "running",
        "searching",
        "broad search",
        "broad searches",
        "ignored",
        "diverged",
        "instead",
        "failed to stop",
    )
    divergence_terms = (
        "when i expected",
        "when we expected",
        "even though",
        "instead of",
        "rather than",
        "but i expected",
        "after the action proof",
        "after action proof",
        "expected it to stop",
    )
    return (
        _has_any(normalized, expectation_terms)
        and _has_any(normalized, observed_terms)
        and _has_any(normalized, divergence_terms)
    )


def _looks_like_expectation_mismatch_artifact_status_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    if not _has_any(
        normalized,
        (
            "expectation mismatch",
            "expectation-mismatch",
            "mismatch report",
            "mismatch artifact",
        ),
    ):
        return False
    if not _has_any(
        normalized,
        (
            "report",
            "artifact",
            "writeup",
            "write-up",
            "file",
            "ticket",
            "jsh-",
        ),
    ):
        return False
    return _has_any(
        normalized,
        (
            "latest status",
            "status of",
            "what is the status",
            "what's the status",
            "current status",
            "status update",
            "latest update",
            "where does",
            "where do",
            "stand",
            "still current",
            "is current",
            "stale",
            "superseded",
            "obsolete",
            "still active",
            "still open",
            "closed",
            "done",
        ),
    )


def _looks_like_expectation_mismatch_artifact_latest_status_query(normalized: str) -> bool:
    normalized = " ".join(re.sub(r"[_/.,;:()]+", " ", normalized).split())
    return _has_any(
        normalized,
        (
            "latest status",
            "status of",
            "what is the status",
            "what's the status",
            "current status",
            "status update",
            "latest update",
            "where does",
            "where do",
            "stand",
        ),
    )


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
    wrong_context_terms = (
        "wrong context",
        "answered the wrong context",
        "answering the wrong context",
        "wrong thread",
        "wrong conversation",
        "answered the side thread",
        "answered from the side thread",
        "answered current context",
        "used current context",
        "current context instead",
        "did not use jsonl",
        "didn't use jsonl",
        "failed to use jsonl",
        "failure to use jsonl",
        "without jsonl",
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
        "last message",
        "latest message",
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
        "wrong context",
        "nonsense",
        "made no sense",
        "jsonl",
    )
    strict_anchor_complaint = (
        _has_any(normalized, anchor_failure_terms)
        and _has_any(normalized, status_terms)
        and _has_any(normalized, parent_or_boundary_terms)
        and _has_any(normalized, complaint_terms)
    )
    wrong_context_complaint = (
        _has_any(normalized, wrong_context_terms)
        and _has_any(normalized, parent_or_boundary_terms)
        and (_has_any(normalized, status_terms) or _has_any(normalized, ("last message", "latest message")))
        and _has_any(normalized, complaint_terms)
    )
    return strict_anchor_complaint or wrong_context_complaint


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


def _looks_like_coordinator_context_query(normalized: str) -> bool:
    context_intent_terms = (
        "coordinator context",
        "current context",
        "context package",
        "context handoff",
        "handoff package",
        "handoff document",
        "handoff documents",
        "handoff docs",
        "receiving agent",
        "next reads",
        "next-read",
        "gather context",
        "gather the current",
        "orient the agent",
        "catch the agent up",
        "what should the next agent read",
        "current-state package",
    )
    if not _has_any(normalized, context_intent_terms):
        return False

    facet_groups = (
        ("plan", "plans", "planning"),
        ("handoff", "handoffs"),
        ("todo", "todos", "task front door", "front door"),
        ("pipeline", "pipeline state", "current state", "current-context"),
        ("active task", "active tasks", "task file", "task files"),
        ("why", "why the work", "purpose", "goal", "goals"),
        ("guiding principle", "guiding principles", "principles", "product contract"),
        ("learning", "learnings", "failure mode", "failure modes", "failure-mode", "failure-modes"),
    )
    facet_hits = sum(1 for group in facet_groups if _has_any(normalized, group))
    if facet_hits < 4:
        return False

    complaint_mining_terms = (
        "what did i keep",
        "what have i kept",
        "keep yelling",
        "kept yelling",
        "keep complaining",
        "kept complaining",
        "keep correcting",
        "kept correcting",
        "repeated complaints",
        "recurring complaints",
        "user corrections",
    )
    if _has_any(normalized, complaint_mining_terms) and facet_hits < 6:
        return False
    return True


def _looks_like_active_status_front_door_query(normalized: str) -> bool:
    if re.search(r"\bjsh-\d{3,4}[a-z0-9-]*\b", normalized) and "active" not in normalized:
        return False
    status_intent = _has_any(
        normalized,
        (
            "active jsonl task",
            "active task",
            "active selected product task",
            "current coordinator state",
            "current fsm state",
            "current repo status",
            "current project status",
            "current state in this project",
            "what should happen next",
            "what should happen next in this project",
            "next legal action",
            "next legal move",
            "what is active",
            "what is the active",
        ),
    )
    repo_context = _has_any(
        normalized,
        (
            "jsonl",
            "repo",
            "repository",
            "project",
            "coordinator",
            "fsm",
            "task",
            "front door",
            "front-door",
            "pipeline",
            "todo",
        ),
    )
    if status_intent and repo_context:
        return True
    return _has_any(normalized, ("what is the active jsonl task", "what's the active jsonl task"))


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
            "what happened with",
            "what happened in",
            "what happened to",
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


def _looks_like_explicit_named_local_task_artifact_query(normalized: str) -> bool:
    if not _EXPLICIT_TASK_PATH_RE.search(normalized):
        return False
    intent_text = _replace_explicit_task_paths_with_identifiers(normalized)
    return bool(
        _looks_like_local_artifact_verification_query(normalized)
        and not _looks_like_task_review_commit_provenance_query(intent_text)
    )


_EXPLICIT_TASK_PATH_RE = re.compile(
    r"(?<![a-z0-9._/-])(?:\./)?tasks/[a-z0-9._/-]+\.md\b"
)


def _has_explicit_task_path(query: str) -> bool:
    return bool(_EXPLICIT_TASK_PATH_RE.search(query.lower()))


def _replace_explicit_task_paths_with_identifiers(normalized: str) -> str:
    def replacement(match: re.Match) -> str:
        task_id = re.search(r"\b[a-z]{2,}-\d+\b", match.group(0))
        return f" {task_id.group(0) if task_id else 'named-task'}"

    return " ".join(_EXPLICIT_TASK_PATH_RE.sub(replacement, normalized).split())


def _looks_like_explicit_named_task_prior_conversation_query(normalized: str) -> bool:
    return bool(
        _EXPLICIT_TASK_PATH_RE.search(normalized)
        and _has_any(
            normalized,
            (
                "prior conversation",
                "conversation history",
                "before in the chat",
            ),
        )
    )


def _looks_like_explicit_named_task_provenance_query(normalized: str) -> bool:
    return bool(
        _EXPLICIT_TASK_PATH_RE.search(normalized)
        and not _looks_like_explicit_named_task_prior_conversation_query(normalized)
        and _looks_like_task_review_commit_provenance_query(
            _replace_explicit_task_paths_with_identifiers(normalized)
        )
    )


def _looks_like_generic_non_history_query(normalized: str) -> bool:
    return normalized in {
        "what happened",
        "what has happened",
        "what happened here",
        "what just happened",
    }


def _looks_like_generic_writing_or_visible_context_query(normalized: str) -> bool:
    if _has_any(
        normalized,
        (
            "conversation history",
            "chat history",
            "prior conversation",
            "previous conversation",
            "old conversation",
            "source provenance",
            "source refs",
            "action proof",
            "what happened since",
            "latest message",
            "last message",
        ),
    ):
        return False
    writing_or_visible_context = _has_any(
        normalized,
        (
            "make this conversation",
            "rewrite this conversation",
            "edit this conversation",
            "make this convo",
            "rewrite this convo",
            "edit this convo",
            "conversation friendlier",
            "conversation to sound",
            "convo opening",
            "summarize the visible conversation",
            "summarise the visible conversation",
            "visible conversation above",
            "conversation above",
        ),
    )
    generic_write_action = _has_any(
        normalized,
        (
            "make",
            "rewrite",
            "edit",
            "summarize",
            "summarise",
            "friendlier",
        ),
    )
    local_visible_target = _has_any(
        normalized,
        (
            "this conversation",
            "this convo",
            "visible conversation",
            "conversation above",
            "convo opening",
        ),
    )
    return writing_or_visible_context or (generic_write_action and local_visible_target)


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
        "changing behavior",
        "change agent behavior",
        "changed scope",
        "changed task selection",
        "change task selection",
        "changing task selection",
        "actually changing task selection",
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
