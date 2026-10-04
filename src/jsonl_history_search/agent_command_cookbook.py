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
            "id": "ordinary_history",
            "question_family": "Recover remembered wording or conversation context.",
            "route": "direct local ask",
            "first_command": 'jsonl-history-search ask "<original question and followups>" --cwd "$PWD" --format text',
            "inspect_next": "Already-opened evidence with native refs; scope, caps and omissions limit the claim.",
        },
        {
            "id": "parent_anchor_status",
            "question_family": "What happened since my last message?",
            "route": "ordinary ask native chronology",
            "first_command": (
                'jsonl-history-search ask "what happened since my last message in the main thread?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "chronology.anchor, scope, temporal uncertainty, source/display coverage "
                "and the directly readable interval_artifact"
            ),
        },
        {
            "id": "complaint_mining",
            "question_family": "What did I keep correcting or complaining about?",
            "route": "semantic-wrapup",
            "first_command": 'jsonl-history-search semantic-wrapup "what did I keep correcting in this session?" --cwd "$PWD" --format text',
            "inspect_next": (
                "semantic_wrapup.failure_modes, fix_suggestions, required_primary_proof next_reads, "
                "and optional/conditional diagnostics only for named gaps"
            ),
        },
        {
            "id": "source_provenance",
            "question_family": "Find the source session behind this report/task/review.",
            "route": "task-review-commit-provenance / semantic-debug",
            "first_command": (
                'jsonl-history-search semantic-debug "find the source session behind this report task or review" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "semantic_debug.source_locator_handoff.next_reads, target historical raw JSONL/session/tool refs, "
                "then historical task/review/branch/commit refs; use linked task/review/report/commit artifacts "
                "only after those primary refs have been opened; post-hoc reports and current-run echoes are diagnostics"
            ),
        },
        {
            "id": "orchestration_provenance",
            "question_family": "Prove which coordinator, worker, and reviewer sessions handled a launched task.",
            "route": "orchestration-provenance / semantic-debug",
            "first_command": (
                'jsonl-history-search semantic-debug "which coordinator worker and reviewer sessions handled this task?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "pre-existing parent/coordinator, worker/subagent, and reviewer raw refs from "
                "source_locator_handoff.next_reads before current-run command-output echoes"
            ),
        },
        {
            "id": "long_range_history",
            "question_family": "Recover an old decision from before compaction or outside visible context.",
            "route": "long-range-history / semantic-debug",
            "first_command": (
                'jsonl-history-search semantic-debug "before compaction what did we decide about this?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "semantic_debug.source_locator_handoff primary old-context raw/session/tool refs, next_reads/read-more, "
                "searched roots, caps, and omitted context before visible-context or repo artifact synthesis"
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
            "route": "direct local ask / scoped lexical absence",
            "first_command": (
                'jsonl-history-search ask "is there evidence that <claim> was discussed in this bounded source scope?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": "scope, applied filters, coverage, omissions; lexical absence is not semantic or all-history absence",
        },
        {
            "id": "action_proof",
            "question_family": "What did the worker or agent actually do versus claim?",
            "route": "action-proof / semantic-debug",
            "first_command": (
                'jsonl-history-search semantic-debug "what did the worker actually do versus claim?" '
                '--cwd "$PWD" --format text'
            ),
            "inspect_next": (
                "semantic_debug.source_window_action_proof, tool_action_proof primary refs, command/tool outputs, "
                "diffs, commits, tests, and only then assistant claims or later summaries"
            ),
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


def _with_cli_prefix(command: str, cli_prefix: str) -> str:
    if command.startswith("jsonl-history-search "):
        return f"{cli_prefix}{command[len('jsonl-history-search'):]}"
    return command
