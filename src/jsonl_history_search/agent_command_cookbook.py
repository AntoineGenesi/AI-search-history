from __future__ import annotations

from typing import Any


def agent_question_cookbook() -> list[dict[str, str]]:
    return [
        {
            "id": "parent_anchor_status",
            "question_family": "What happened since my last message?",
            "route": "latest-status / what-happened-since",
            "first_command": (
                'jsonl-history-search ask "what happened since my last message in the main thread?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "status_anchor.parent_main_anchor_shortlist, follow_through_plan open_first, "
                "selected_anchor, and raw sessions/**/*.jsonl activity after the anchor"
            ),
        },
        {
            "id": "complaint_mining",
            "question_family": "What did I keep correcting or complaining about?",
            "route": "semantic-wrapup",
            "first_command": 'jsonl-history-search ask "what did I keep correcting?" --cwd "$PWD" --format text',
            "inspect_next": (
                "semantic_wrapup.failure_modes, fix_suggestions, required_primary_proof next_reads, "
                "and optional/conditional diagnostics only for named gaps"
            ),
        },
        {
            "id": "source_provenance",
            "question_family": "Find the source session behind this report/task/review.",
            "route": "semantic-debug",
            "first_command": (
                'jsonl-history-search ask "find the source session behind this report task or review" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": "semantic_debug.source_locator_handoff, source refs, next_reads, and linked task/review/git artifacts",
        },
        {
            "id": "bounded_no_evidence",
            "question_family": "Is there evidence for this inside the searched source scope?",
            "route": "bounded-no-evidence / no-evidence-report",
            "first_command": (
                'jsonl-history-search ask "is there evidence that <claim> was discussed in this bounded source scope?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": "no_evidence_report.searched_scope, answer_scope, candidate_interpretation, and read_more_guidance",
        },
        {
            "id": "action_proof",
            "question_family": "What did the worker or agent actually do versus claim?",
            "route": "action-proof / semantic-debug",
            "first_command": (
                'jsonl-history-search ask "what did the worker actually do versus claim?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": "tool_action_proof primary refs, command/tool events, diffs, commits, and review metadata",
        },
        {
            "id": "storage_retention_forensics",
            "question_family": "Why is .private large, who created/uses it, and what must be retained?",
            "route": "storage-retention-forensics / filesystem-inventory-first",
            "first_command": "du -sh .private .private/* 2>/dev/null",
            "inspect_next": (
                "filesystem_inventory first, then artifact_join over tasks/reports/reviews/scripts/tests/"
                "codex state, then targeted JSONL only for unresolved creator/history gaps"
            ),
        },
        {
            "id": "read_more",
            "question_family": "Read the exact source evidence returned by a route.",
            "route": "read-more",
            "first_command": 'jsonl-history-search read-more <source-ref> --cwd "$PWD" --format text',
            "inspect_next": "the concrete path:line source ref named by source_locator_handoff, next_reads, or follow_through_plan",
        },
    ]


def ask_next_commands(*, selected_first_command: str, route_family: str) -> list[dict[str, Any]]:
    if route_family == "storage_retention_forensics":
        return [
            {
                "purpose": "filesystem_inventory",
                "command": selected_first_command,
                "inspect_next": "local size inventory, largest subtrees, top files, and file-type/extension summary",
            },
            {
                "purpose": "artifact_join",
                "command": "rg -n \"<large-subtree-or-task-handle>\" tasks reports reviews scripts tests codex/state",
                "inspect_next": "map each large subtree to committed task/report/review/script/test/pipeline-state use or retention evidence",
            },
            {
                "purpose": "targeted_jsonl_follow_up_only_if_gap_remains",
                "command": (
                    'jsonl-history-search semantic-debug "who created or used <specific subtree/task handle>?" '
                    '--cwd "$PWD" --format text'
                ),
                "inspect_next": (
                    "run only after inventory/artifact joins leave a named creator/history gap; keep roots, caps, "
                    "searched-scope caveats, and echo demotion explicit"
                ),
            },
        ]

    commands: list[dict[str, Any]] = [
        {
            "purpose": "run selected first route",
            "command": selected_first_command,
            "inspect_next": "the route output's source refs, source locator handoff, follow-through plan, or no-evidence scope",
        }
    ]
    for entry in agent_question_cookbook():
        if _entry_applies_to_route(entry, route_family):
            commands.append(
                {
                    "purpose": f"same-family example: {entry['question_family']}",
                    "command": entry["first_command"],
                    "inspect_next": entry["inspect_next"],
                }
            )
            break
    if route_family == "semantic_wrapup":
        commands.append(
            {
                "purpose": "open required semantic-wrapup proof ref only",
                "command": 'jsonl-history-search read-more <required_primary_proof source-ref> --cwd "$PWD" --format text',
                "inspect_next": (
                    "replace the placeholder only with a next_reads item where read_role is required_primary_proof; "
                    "optional_diagnostic and conditional_required refs are not default follow-through"
                ),
            }
        )
    else:
        commands.append(
            {
                "purpose": "open exact source ref returned by the route",
                "command": 'jsonl-history-search read-more <source-ref> --cwd "$PWD" --format text',
                "inspect_next": "replace <source-ref> with the concrete path:line from the preceding route output",
            }
        )
    return commands


def _entry_applies_to_route(entry: dict[str, str], route_family: str) -> bool:
    entry_id = entry.get("id")
    if route_family == "latest_status":
        return entry_id == "parent_anchor_status"
    if route_family == "semantic_wrapup":
        return entry_id == "complaint_mining"
    if route_family == "bounded_no_evidence":
        return entry_id == "bounded_no_evidence"
    if route_family == "action_proof":
        return entry_id == "action_proof"
    if route_family == "storage_retention_forensics":
        return entry_id == "storage_retention_forensics"
    if route_family in {"orchestration_provenance", "semantic_debug", "remembered_topic", "process_actual_use"}:
        return entry_id == "source_provenance"
    return False
