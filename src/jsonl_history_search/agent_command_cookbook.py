from __future__ import annotations

import re
import shlex
from typing import Any


_BOUNDED_TOP_N_FILTER = (
    "python3 -c 'import heapq,sys; "
    'rows=((int(size), path.rstrip("\\n")) for line in sys.stdin '
    'for size,path in [line.split("\\t",1)]); '
    '[print(str(size)+"\\t"+path) for size,path in heapq.nlargest(40,rows)]\''
)

STORAGE_ROOT_INVENTORY_COMMAND = (
    'repo_root=$(git rev-parse --show-toplevel) && '
    'common_private=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/.private && '
    'enclosing_private=$(python3 -c \'from pathlib import Path; import sys; '
    'p=Path(sys.argv[1]).resolve(); '
    'print(next((str(parent) for parent in p.parents if parent.name==".private"),""))\' "$repo_root") && '
    'if [ -n "$enclosing_private" ]; then parent_private=$enclosing_private; '
    'else parent_private=$common_private; fi; '
    'for root in "$repo_root/.private" "$parent_private"; do '
    '[ -d "$root" ] || continue; '
    'if [ "$root" = "$repo_root/.private" ] && [ "$root" = "$parent_private" ]; then '
    'label=checkout-and-parent-retained; '
    'elif [ "$root" = "$repo_root/.private" ]; then label=checkout-local; '
    'else label=parent-retained; fi; '
    'printf \'\\n[%s] %s\\n\' "$label" "$root"; '
    f'du -x -d 2 -B1 "$root" 2>/dev/null | {_BOUNDED_TOP_N_FILTER}; '
    'if [ "$repo_root/.private" = "$parent_private" ]; then break; fi; '
    'done; :'
)

STORAGE_RETENTION_ONLY_POLICY_COMMAND = (
    "printf '\\n--- retention gate ---\\n'; "
    "nl -ba reports/jsh-067-private-artifact-retention-and-worktree-cleanup-gate.md | sed -n '8,20p'; "
    "printf '\\n--- retention ledger method ---\\n'; "
    "nl -ba reports/jsh-1245-private-retention-ledger.md | sed -n '13,20p'; "
    "printf '\\n--- retained lane evidence contract ---\\n'; "
    "nl -ba reports/jsh-1246-state5-sqlite-retention-policy.md | sed -n '13,24p'"
)

STORAGE_EXPLICIT_CLEANUP_POLICY_COMMAND = (
    "for f in reports/jsh-067-private-artifact-retention-and-worktree-cleanup-gate.md "
    "reports/jsh-1245-private-retention-ledger.md "
    "reports/jsh-1246-state5-sqlite-retention-policy.md "
    "reports/jsh-1257-private-storage-cleanup-executed.md; do "
    "printf '\\n--- %s ---\\n' \"$f\"; nl -ba \"$f\" | sed -n '1,28p'; done"
)


def storage_cleanup_requested(query: str) -> bool:
    normalized = " ".join(query.lower().replace("_", " ").split())
    return bool(
        re.search(
            r"\b(delete|deleted|deleting|deletion|cleanup|archive|archived|archiving|move|moved|moving|"
            r"compact|compacted|compacting|compaction|prune|pruned|pruning|remove|removed|removing|"
            r"removal|relocate|relocated|relocating|relocation|reclaim|reclaimed|reclaiming|reclamation)\b|"
            r"\bclean\s+up\b",
            normalized,
        )
    )


def agent_question_cookbook(*, cli_prefix: str = "jsonl-history-search") -> list[dict[str, str]]:
    cookbook = [
        {
            "id": "parent_anchor_status",
            "question_family": "What happened since my last message?",
            "route": "latest-status / what-happened-since",
            "first_command": (
                'jsonl-history-search what-happened-since "what happened since my last message in the main thread?" '
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
            "first_command": 'jsonl-history-search ask "what did I keep correcting in this session?" --cwd "$PWD" --format text',
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
            "inspect_next": (
                "semantic_debug.source_locator_handoff primary raw/session/tool source refs and next_reads first; "
                "use linked task/review/report/commit artifacts only after those primary refs have been opened"
            ),
        },
        {
            "id": "bulk_ocr_provenance",
            "question_family": "Find OCR or screenshot line occurrences in chat-log histories.",
            "route": "raw-phrase-source-search / bulk-line-match",
            "first_command": (
                'jsonl-history-search bulk-line-match --phrases-file <ocr-lines.txt> '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "bulk_line_match candidate source_ref/target rows as leads only; validate selected refs "
                "with read-more windows and semantic-debug/follow-next-reads/audit-coverage before final provenance"
            ),
        },
        {
            "id": "deep_history_audit",
            "question_family": "Read all chats/agent history and find when X or Y happened.",
            "route": "deep-history-audit / semantic-debug",
            "first_command": (
                'jsonl-history-search semantic-debug "<question>" --audit-depth in-depth '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "in_depth_audit_coverage, rolling/sliced windows, caps/omitted windows, "
                "primary raw/session/tool refs, and continuation/read guidance when coverage is incomplete"
            ),
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
            "first_command": STORAGE_ROOT_INVENTORY_COMMAND,
            "inspect_next": (
                "checkout-local versus parent-retained filesystem_inventory, then generic retention policy; "
                "answer the broad question and stop, with one named-handle join only for an explicit or "
                "materially unresolved facet"
            ),
        },
        {
            "id": "read_more",
            "question_family": "Read the exact source evidence returned by a route.",
            "route": "read-more",
            "first_command": 'jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text',
            "inspect_next": (
                "the concrete path:line source ref named by source_locator_handoff, next_reads, or follow_through_plan; "
                "use --mode window --before/--after for surrounding context, not a --context flag"
            ),
        },
    ]
    for entry in cookbook:
        entry["first_command"] = _with_cli_prefix(entry["first_command"], cli_prefix)
    return cookbook


def storage_optional_deeper_reads(*, cli_prefix: str = "jsonl-history-search") -> list[dict[str, Any]]:
    """Return storage drill-downs that are not required for a broad answer."""
    return [
        {
            "purpose": "target_top_files",
            "command": (
                "find <selected-large-subtree-path> -xdev -type f -printf '%s\\t%p\\n' 2>/dev/null "
                f"| {_BOUNDED_TOP_N_FILTER}"
            ),
            "inspect_next": (
                "only when the user asks for exact files or one inventory subtree needs a representative file; "
                "do not enumerate the whole repo"
            ),
        },
        {
            "purpose": "named_handle_policy_join",
            "command": (
                "rg -n -F --max-count 20 '<large-subtree-or-task-handle>' "
                "codex/state/pipeline-state.md TODO.md PROJECT.md CODEX-ORCHESTRATION.md "
                "learnings/INDEX.md 2>/dev/null | head -n 80"
            ),
            "inspect_next": (
                "only for one named handle when the user requests its exact policy or the broad answer would "
                "otherwise contain a material unresolved policy facet"
            ),
        },
        {
            "purpose": "producer_consumer_join",
            "command": (
                "rg -n -F --max-count 20 --glob '*.md' --glob '*.py' --glob '*.sh' "
                "'<large-subtree-or-task-handle>' tasks reports codex/state/reviews scripts tests "
                "2>/dev/null | head -n 120"
            ),
            "inspect_next": (
                "bounded producer or consumer references for one explicitly requested facet; "
                "never run this once per inventory bucket"
            ),
        },
        {
            "purpose": "targeted_jsonl_follow_up_only_if_gap_remains",
            "command": (
                f'{cli_prefix} semantic-debug "who created or used <specific subtree/task handle>?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "only after a specific requested creator/history facet remains unresolved; keep roots, caps, "
                "searched-scope caveats, and echo demotion explicit"
            ),
        },
    ]


def ask_next_commands(
    *,
    selected_first_command: str,
    route_family: str,
    cli_prefix: str = "jsonl-history-search",
    query: str = "",
) -> list[dict[str, Any]]:
    if route_family == "non_history_clarification":
        return []

    if route_family == "local_artifact_verification":
        return [
            {
                "purpose": "inspect local artifact state",
                "command": selected_first_command,
                "inspect_next": "changed and untracked files relevant to the user's local target",
            },
            {
                "purpose": "inspect the named local changes",
                "command": "git diff -- <named-file>",
                "inspect_next": "the named task, evidence, report, docs, or other artifact before any history lookup",
            },
            {
                "purpose": "inspect the latest committed change when relevant",
                "command": "git show --stat --oneline HEAD",
                "inspect_next": "whether the named artifact change is committed and what that commit touched",
            },
        ]

    if route_family == "storage_retention_forensics":
        cleanup_requested = storage_cleanup_requested(query)
        policy_command = (
            STORAGE_EXPLICIT_CLEANUP_POLICY_COMMAND
            if cleanup_requested
            else STORAGE_RETENTION_ONLY_POLICY_COMMAND
        )
        return [
            {
                "purpose": "filesystem_inventory",
                "command": selected_first_command,
                "inspect_next": (
                    "bounded depth-2 size inventory for distinct checkout-local and parent-retained roots; "
                    "use the top rows as dominant classes and representative handles; do not open a subtree "
                    "for a broad answer"
                ),
            },
            {
                "purpose": "generic_retention_policy",
                "command": policy_command,
                "inspect_next": (
                    (
                        "explicitly requested cleanup policy and latest authorized outcome with citation-ready "
                        "line numbers; do not perform any mutation"
                    )
                    if cleanup_requested
                    else (
                        "retention-only parent .private location, artifact-use method, and lane evidence contract "
                        "with citation-ready line numbers; describe retained classes and requirements, do not turn "
                        "this evidence into space-reclamation advice, then stop"
                    )
                ),
            },
        ]

    if route_family == "raw_phrase_source_search":
        return [
            {
                "purpose": "generate bulk phrase candidates",
                "command": selected_first_command,
                "inspect_next": (
                    "bulk_line_match.candidates, phrase_id/phrase_text, source_ref/target, match_strength, "
                    "and searched_scope caps; rows are leads only"
                ),
            },
            {
                "purpose": "open selected candidate source ref",
                "command": (
                    f'{cli_prefix} read-more <source-ref> '
                    '--mode window --before 2 --after 2 --cwd "$PWD" --format text'
                ),
                "inspect_next": (
                    "bounded raw context around a selected phrase candidate before using it as provenance"
                ),
            },
            {
                "purpose": "validate chronology or provenance when needed",
                "command": f'{cli_prefix} semantic-debug "<question>" --cwd "$PWD" --format text',
                "inspect_next": (
                    "source_locator_handoff, next_reads, and audit/coverage warnings before final provenance"
                ),
            },
            {
                "purpose": "check remaining coverage gaps",
                "command": f'{cli_prefix} audit-coverage --cwd "$PWD" --format text',
                "inspect_next": "searched-scope metadata and omitted/capped windows before declaring absence or completeness",
            },
        ]

    commands: list[dict[str, Any]] = [
        {
            "purpose": "run selected first route",
            "command": selected_first_command,
            "inspect_next": (
                "the route output's primary raw/session/tool source refs, source locator handoff, "
                "follow-through plan, or no-evidence scope before secondary artifacts"
            ),
        }
    ]
    for entry in agent_question_cookbook(cli_prefix=cli_prefix):
        if _entry_applies_to_route(entry, route_family):
            commands.append(
                {
                    "purpose": f"same-family example: {entry['question_family']}",
                    "command": entry["first_command"],
                    "inspect_next": entry["inspect_next"],
                }
            )
            break
    if route_family == "bounded_no_evidence":
        commands.append(
            {
                "purpose": "scoped no-evidence answer contract",
                "command": (
                    "No read-more required when ask --execute reports "
                    "answer_scope=not_found_in_searched_scope and "
                    "read_more_required_before_scoped_no_evidence_answer=false"
                ),
                "inspect_next": (
                    "answer from searched_scope, candidate_interpretation, cap/missing-scope fields, "
                    "and not_all_history_absence; read weak/context refs only as optional diagnostics "
                    "or on explicit user request"
                ),
            }
        )
        return commands
    if route_family == "semantic_wrapup":
        commands.append(
            {
                "purpose": "open required semantic-wrapup proof ref only",
                "command": (
                    f'{cli_prefix} read-more <required_primary_proof source-ref> '
                    '--mode window --before 2 --after 2 --cwd "$PWD" --format text'
                ),
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
                "command": (
                    f'{cli_prefix} read-more <source-ref> '
                    '--mode window --before 2 --after 2 --cwd "$PWD" --format text'
                ),
                "inspect_next": (
                    "replace <source-ref> with a concrete primary raw/session/tool path:line from the "
                    "preceding route output before relying on task/review/report/commit artifacts; "
                    "for context use --mode window --before/--after, not --context"
                ),
            }
        )
    return commands


def named_task_local_next_commands(
    *,
    selected_first_command: str,
    task_path: str,
) -> list[dict[str, Any]]:
    task_basename = task_path.rsplit("/", 1)[-1]
    review_path = f"codex/state/reviews/{task_basename}"
    quoted_task = shlex.quote(task_path)
    quoted_review = shlex.quote(review_path)
    return [
        {
            "purpose": "inspect current HEAD and parents",
            "command": selected_first_command,
            "inspect_next": "current branch or detached state, HEAD, and direct HEAD parents only",
        },
        {
            "purpose": "read the named task",
            "command": f"sed -n '1,320p' -- {quoted_task}",
            "inspect_next": "status and exact commit identifiers stated by this named task only",
        },
        {
            "purpose": "read the canonical review when present",
            "command": (
                f"if [ -f {quoted_review} ]; then sed -n '1,320p' -- {quoted_review}; "
                "else printf '%s\\n' 'canonical review is absent'; fi"
            ),
            "inspect_next": "verdict and exact reviewed or merge commit identifiers from this canonical review only",
        },
        {
            "purpose": "verify exact target-bound ancestry",
            "command": (
                "commit=EXACT_COMMIT_FROM_NAMED_TASK_OR_CANONICAL_REVIEW; "
                'git branch --contains "$commit" && '
                'git merge-base --is-ancestor "$commit" HEAD'
            ),
            "inspect_next": (
                "replace the token with one exact commit read from the named task or canonical review; "
                "repeat only for another exact governing commit from those same files"
            ),
        },
    ]


def _with_cli_prefix(command: str, cli_prefix: str) -> str:
    if command.startswith("jsonl-history-search "):
        return f"{cli_prefix}{command[len('jsonl-history-search'):]}"
    return command


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
    if route_family == "deep_history_audit":
        return entry_id == "deep_history_audit"
    if route_family in {"orchestration_provenance", "semantic_debug", "remembered_topic", "process_actual_use"}:
        return entry_id == "source_provenance"
    return False
