from __future__ import annotations

from typing import Any


CAPS: dict[str, Any] = {
    "max_cases": 3,
    "max_wall_time_seconds": 600,
    "max_commands_total": 40,
    "max_commands_per_case": 12,
    "max_bytes_read_total": 20_000_000,
    "max_bytes_read_per_case": 7_000_000,
    "max_output_tokens_total": 4_000,
    "max_output_tokens_per_case": 1_500,
    "max_private_trace_bytes": 2_000_000,
}

ALLOWLIST: list[dict[str, Any]] = [
    {
        "command": "rg",
        "allowed": True,
        "constraints": [
            "explicit sanitized search terms only",
            "explicit approved roots only",
            "respect byte/file/time caps",
            "do not print raw matching lines into committed artifacts",
        ],
    },
    {
        "command": "sed",
        "allowed": True,
        "constraints": [
            "bounded line windows only",
            "reviewer-approved private trace use only",
            "output remains private unless summarized as aggregate counters",
        ],
    },
    {
        "command": "head",
        "allowed": True,
        "constraints": [
            "bounded sanity checks only",
            "no committed raw transcript or trace text",
        ],
    },
    {
        "command": "python -m jsonl_history_search.cli",
        "allowed": True,
        "constraints": [
            "package CLI subcommands only",
            "sanitized report modes only",
            "no raw query text, snippets, paths, usernames, or session identifiers in committed output",
        ],
    },
    {
        "command": "scripts/*.py",
        "allowed": True,
        "constraints": [
            "repo helper scripts only",
            "must document command, caps, and sanitized output contract before use",
        ],
    },
]

DENYLIST: list[dict[str, str]] = [
    {"command": "codex", "reason": "spawns a live agent; not part of committed protocol generation"},
    {"command": "claude", "reason": "spawns a live agent; not part of committed protocol generation"},
    {"command": "script", "reason": "terminal capture must remain private and explicitly approved"},
    {"command": "tmux", "reason": "interactive/background sessions are out of scope"},
    {"command": "bash -c", "reason": "compound shell commands bypass allowlist review"},
    {"command": "find /", "reason": "broad filesystem traversal is prohibited"},
    {"command": "cat", "reason": "unbounded raw text dumping is prohibited"},
    {"command": "cp", "reason": "raw traces must not be copied into committed areas"},
    {"command": "rsync", "reason": "raw traces must not be copied into committed areas"},
    {"command": "curl", "reason": "network upload/download is prohibited for this protocol"},
]


def build_live_terminal_protocol_payload() -> dict[str, Any]:
    protocol = {
        "schema_version": 1,
        "status": "protocol_only",
        "data_source": "sanitized_protocol_template_only",
        "command_inert": True,
        "live_run_performed": False,
        "codex_spawned": False,
        "raw_trace_parsed": False,
        "private_logs_inspected": False,
        "opt_in_contract": {
            "future_live_run_requires_explicit_approval": True,
            "approval_scope": "per_run_and_per_case_set",
            "default_action": "emit_protocol_only",
            "this_command_can_run_simulation": False,
            "requires_reviewer_approved_case_ids_before_commit": True,
        },
        "decision": {
            "live_terminal_simulation_value": "optional_diagnostic_only",
            "adds_value_beyond_deterministic_baselines": "possible_but_not_required_for_gate_promotion",
            "promotion_rule": (
                "Live simulation cannot promote ranking, rg, embedding, timestamp, manifest, or other gates "
                "unless an independent reviewer approves sanitized aggregate proof."
            ),
            "recommended_next_step": (
                "Prefer deterministic benchmark-impact, benchmark-measured, and reviewer-approved sanitized "
                "aggregate evidence before any live terminal diagnostic run."
            ),
        },
        "caps": CAPS,
        "allowlist": ALLOWLIST,
        "denylist": DENYLIST,
        "raw_trace_rule": {
            "directory": ".private/jsh-026/",
            "git_ignored": True,
            "committed_raw_trace_allowed": False,
            "private_trace_content_allowed_in_reports": False,
            "required_cleanup_review": "git ls-files .private worktrees must return no tracked files",
        },
        "committed_output_contract": {
            "allowed_fields": [
                "protocol version",
                "approved sanitized case IDs",
                "source-set labels",
                "aggregate counters",
                "status labels",
                "gate names",
                "reviewer proof command outcomes",
            ],
            "forbidden_fields": [
                "raw query text",
                "raw transcript snippets",
                "raw filesystem paths",
                "local usernames",
                "session identifiers",
                "private trace content",
                "terminal screenshots or captures",
                "model responses copied from a private diagnostic run",
            ],
            "report_basis": "sanitized aggregate template only",
        },
        "reviewer_proof_contract": {
            "required_checks": [
                "focused tests for protocol rendering privacy",
                "CLI JSON and Markdown smoke tests",
                "git diff --check",
                "git ls-files .private worktrees",
                "privacy grep over committed task, report, docs, tests, and package code",
            ],
            "must_confirm": [
                "no raw private text entered committed artifacts",
                "no raw query text entered committed artifacts",
                "no raw snippets entered committed artifacts",
                "no raw paths or local usernames entered committed artifacts",
                "no session identifiers entered committed artifacts",
                "no private trace content entered committed artifacts",
                "no Codex, Claude, PTY, or terminal simulator process was spawned by this command",
            ],
        },
        "sanitized_aggregate_template": {
            "case_ids": [],
            "case_count": 0,
            "approved_by_reviewer": False,
            "simulation_status": "not_run",
            "diagnostic_only": True,
            "gate_promotion_allowed": False,
            "aggregate_counters": {
                "wall_time_seconds": None,
                "commands_executed": None,
                "bytes_read": None,
                "output_tokens": None,
                "first_pass_success_count": None,
                "miss_count": None,
                "overread_count": None,
            },
        },
    }
    return {"live_terminal_protocol": protocol}
