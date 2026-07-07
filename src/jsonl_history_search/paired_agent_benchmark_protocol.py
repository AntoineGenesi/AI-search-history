from __future__ import annotations

from typing import Any

from .metric_contract import TOKEN_FORMULA, TOKEN_WARNING, approximate_tokens_from_bytes


PROTOCOL_CAPS: dict[str, Any] = {
    "max_cases_per_run": 6,
    "max_wall_time_seconds_per_side": 900,
    "max_commands_or_tool_calls_per_side": 16,
    "max_files_opened_per_side": 24,
    "max_files_searched_per_side": 120,
    "max_bytes_scanned_per_side": 25_000_000,
    "max_bytes_read_per_side": 12_000_000,
    "max_output_bytes_per_side": 32_000,
    "max_context_bytes_per_side": 32_000,
    "max_private_trace_bytes_per_run": 4_000_000,
}

GENERIC_ALLOWED_TOOLS: list[dict[str, str]] = [
    {
        "tool": "rg",
        "purpose": "bounded text search over approved local roots",
    },
    {
        "tool": "sed",
        "purpose": "bounded line-window inspection after a source hit",
    },
    {
        "tool": "head",
        "purpose": "small file/header sanity checks",
    },
    {
        "tool": "git",
        "purpose": "repo-local file discovery and metadata checks",
    },
    {
        "tool": "python3",
        "purpose": "bounded local parsing or counting helpers without package-specific shortcuts",
    },
]

JSONL_ASSISTED_TOOLS: list[dict[str, str]] = [
    *GENERIC_ALLOWED_TOOLS,
    {
        "tool": "python3 -m jsonl_history_search.cli",
        "purpose": "approved package CLI workflows for the JSONL-assisted side",
    },
    {
        "tool": "jsonl-history-search Codex skill",
        "purpose": "approved package skill guidance for the JSONL-assisted side",
    },
    {
        "tool": "repo helper scripts",
        "purpose": "approved scripts documented in the benchmark case packet",
    },
]

DEFAULT_MANUAL_FORBIDDEN_MATERIAL: list[str] = [
    "JSONL CLI help",
    "JSONL skill help",
    "package docs",
    "transcript-root cheat sheets",
    "JSONL schema guides",
    "source-location hints",
    "package-derived query plans",
    "prior search results",
    "private bridge refs",
    "reviewer notes",
    "rejected-ledger clues",
    "package-internal task discoveries",
    "private case-packet files",
    "private source-map files",
    "expected-facet material",
    "private adjudication files",
    "task/review/report answer-key artifacts",
    "same-loop artifacts that directly answer the case",
]

CONTAMINATION_LABELS: list[dict[str, Any]] = [
    {
        "label": "none",
        "severity": "clean",
        "counts_as_default_condition": True,
        "description": "No cross-side or post hoc hints leaked into the evaluated condition.",
    },
    {
        "label": "leaked_hint",
        "severity": "major",
        "counts_as_default_condition": False,
        "description": "Prompt or operator leaked source-location hints, search plans, or package guidance.",
    },
    {
        "label": "post_hoc_rooting",
        "severity": "major",
        "counts_as_default_condition": False,
        "description": "A side received roots, files, or locations discovered after another side already searched.",
    },
    {
        "label": "prior_results_shared",
        "severity": "major",
        "counts_as_default_condition": False,
        "description": "A side saw another side's hits, misses, counters, or draft answer before finishing.",
    },
    {
        "label": "shared_knowledge_variant",
        "severity": "alternate_condition",
        "counts_as_default_condition": False,
        "description": "Intentional shared-knowledge experiment; keep separate from the default/manual blind baseline.",
    },
    {
        "label": "counter_unavailable",
        "severity": "minor",
        "counts_as_default_condition": True,
        "description": "A required counter could not be measured and was recorded as unavailable, not zero.",
    },
]


def build_paired_agent_benchmark_protocol_payload() -> dict[str, Any]:
    protocol = {
        "schema_version": 1,
        "status": "protocol_only",
        "data_source": "sanitized_protocol_template_only",
        "command_inert": True,
        "dry_validation_only": True,
        "live_paired_benchmark_run_performed": False,
        "live_agents_spawned": False,
        "private_logs_inspected": False,
        "raw_trace_parsed": False,
        "protocol_identity": {
            "command": "benchmark-paired-agent-protocol",
            "purpose": "paired_evaluation_contract_and_dry_validation",
            "this_task": "jsh-059b1-paired-agent-benchmark-protocol-and-harness",
            "pilot_task": "jsh-059b2-paired-agent-retrieval-pilot-run-and-result-packet",
            "case_manifest_dependency": "jsh-060f-reviewed-example-manifest-and-benchmark-integration",
        },
        "complements_live_terminal_protocol": {
            "related_command": "benchmark-live-terminal-protocol",
            "related_command_role": "inert_generic_safety_protocol",
            "this_command_role": "paired_evaluation_specific_contract_metrics_and_dry_validation",
            "related_command_remains_inert": True,
            "this_command_runs_pilot_by_default": False,
            "difference_summary": (
                "The live-terminal command stays a generic no-spawn safety contract; this paired command adds "
                "per-side knowledge boundaries, contamination labels, result schema, trace rules, and dry "
                "reviewer validation for a later paired run."
            ),
        },
        "run_contract": {
            "case_ids_must_be_sanitized": True,
            "reviewer_approved_case_ids_required_before_commit": True,
            "run_id_format": "paired-<sanitized-label>-<yyyymmdd>-<attempt>",
            "default_condition": "manual_default_blind",
            "alternate_conditions_must_be_labeled": True,
            "dry_validation_mode": {
                "enabled_by_default": True,
                "writes_private_traces": False,
                "writes_committed_aggregates_only_when_requested": True,
                "validates": [
                    "prompt-view boundaries",
                    "counter schema completeness",
                    "contamination label vocabulary",
                    "trace path and cleanup contract",
                    "committed-safe output contract",
                ],
            },
            "cleanup_rules": [
                "Keep raw traces only under ignored .private/jsh-059b1/.",
                "Do not copy raw prompt text, transcripts, source lines, or query payloads into committed artifacts.",
                "Use git ls-files .private worktrees to verify no private trace files were tracked.",
            ],
        },
        "prompt_views": {
            "shared_case_packet": {
                "allowed_sections": [
                    "benchmark_question",
                    "time_and_command_caps",
                    "output_schema",
                    "privacy_rules",
                ],
                "case_packet_template": [
                    "Question: <sanitized benchmark question>",
                    "Caps: follow the published time, command, file, and byte limits.",
                    "Output schema: answer, supporting refs, confidence, counters, uncertainty, contamination labels.",
                    "Privacy: raw traces remain private; committed outputs stay aggregate and sanitized.",
                ],
            },
            "default_manual": {
                "allowed_sections": [
                    "benchmark_question",
                    "reviewed_case_identity",
                    "source_search_boundary",
                    "generic_allowed_tools",
                    "time_and_command_caps",
                    "output_schema",
                    "privacy_rules",
                ],
                "allowed_tools": GENERIC_ALLOWED_TOOLS,
                "prompt_template": [
                    "Question: <sanitized benchmark question>",
                    "Reviewed case identity: <enough non-answer-key identity to find the relevant primary source, such as project/date/ask shape/event family>.",
                    "Source search boundary: <where ordinary shell/file search may look, without expected facets, source maps, or evaluator refs>.",
                    "Allowed tools: rg, sed, head, git, python3.",
                    "Do not open private case packets, source maps, expected-facet material, adjudication files, task/review/report answer keys, or same-loop artifacts that directly answer this case.",
                    "Caps: stay within the published time, command, file, byte, and output limits.",
                    "Return: answer, source refs, confidence, counters, uncertainty, contamination labels.",
                    "Privacy: keep raw traces private and keep committed output aggregate and sanitized.",
                ],
                "forbidden_material": DEFAULT_MANUAL_FORBIDDEN_MATERIAL,
                "exp_style_private_safe_materialization": {
                    "allowed": [
                        "benchmark_question",
                        "reviewed_case_identity",
                        "source_search_boundary",
                        "caps",
                        "output_schema",
                        "privacy_rules",
                    ],
                    "forbidden": [
                        "expected facets",
                        "source maps",
                        "case packet paths",
                        "adjudication checklists",
                        "raw adjudication",
                        "same-loop answer-key reports/tasks/reviews",
                    ],
                    "purpose": (
                        "Give the manual lane enough reviewed case identity to find the primary source "
                        "without exposing expected answers or source-location maps."
                    ),
                },
            },
            "jsonl_assisted": {
                "allowed_sections": [
                    "benchmark_question",
                    "approved_jsonl_tools",
                    "time_and_command_caps",
                    "output_schema",
                    "privacy_rules",
                ],
                "allowed_tools": JSONL_ASSISTED_TOOLS,
                "prompt_template": [
                    "Question: <sanitized benchmark question>",
                    "Allowed tools: generic shell tools plus approved JSONL package CLI, JSONL skill guidance, and documented repo helper scripts.",
                    "Caps: stay within the published time, command, file, byte, and output limits.",
                    "Return: answer, supporting refs, confidence, counters, uncertainty, contamination labels.",
                    "Privacy: raw traces remain private and committed output stays aggregate and sanitized.",
                ],
            },
        },
        "caps": PROTOCOL_CAPS,
        "trace_contract": {
            "private_root": ".private/jsh-059b1/",
            "git_ignored": True,
            "per_run_directory_pattern": ".private/jsh-059b1/run-<sanitized-label>/",
            "trace_files": [
                "prompt-jsonl-assisted.txt",
                "prompt-default-manual.txt",
                "stdout-jsonl-assisted.txt",
                "stdout-default-manual.txt",
                "counter-log.json",
                "review-notes.md",
            ],
            "committed_raw_trace_allowed": False,
            "committed_trace_summary_allowed": "sanitized_aggregate_only",
        },
        "contamination_rules": {
            "default_condition_requires_clean_manual_blindness": True,
            "labels": CONTAMINATION_LABELS,
            "notes": [
                "If the default/manual side sees package guidance or source hints, mark the row contaminated.",
                "If the default/manual side opens private case packets, source maps, expected facets, adjudication files, task/review/report answer keys, or same-loop answer-key artifacts, mark the row failed closed.",
                "If roots or source locations were learned after another side searched, mark post_hoc_rooting.",
                "Shared-knowledge experiments are allowed only as explicitly labeled alternate conditions.",
                "Unavailable counters stay unavailable; they never become zero for convenience.",
            ],
        },
        "metric_contract": _metric_contract(),
        "result_schema": {
            "required_case_fields": [
                "case_id",
                "condition_label",
                "benchmark_question_label",
                "question_family",
                "jsonl_assisted",
                "default_manual",
                "comparison",
                "review",
            ],
            "required_side_metrics": list(_side_metric_template().keys()),
            "required_comparison_fields": list(_comparison_template().keys()),
            "required_review_fields": list(_review_template().keys()),
        },
        "committed_output_contract": {
            "allowed_fields": [
                "sanitized case and run labels",
                "condition labels",
                "aggregate counters and explicit unavailable markers",
                "contamination labels",
                "correctness and reviewer outcome summaries",
                "caps and trace-path rules",
                "protocol and dry-validation status",
            ],
            "forbidden_fields": [
                "raw query text",
                "raw transcript snippets",
                "raw filesystem paths",
                "local usernames",
                "session identifiers",
                "prompt captures",
                "private bridge refs",
                "query_text payloads",
                "raw_line payloads",
                "source_lines payloads",
                "bridge_ref values",
            ],
            "report_basis": "sanitized aggregate protocol and dry-validation output only",
        },
        "reviewer_proof_contract": {
            "required_checks": [
                "focused tests for payload/rendering/privacy boundaries",
                "benchmark-live-terminal-protocol JSON smoke",
                "benchmark-paired-agent-protocol JSON smoke",
                "benchmark-paired-agent-protocol Markdown report smoke",
                "git check-ignore -v .private/jsh-059b1/probe.json",
                "git ls-files .private worktrees",
                "git diff --check",
                "privacy grep over committed protocol, report, task, and tests",
            ],
            "must_confirm": [
                "default/manual prompt view contains only the benchmark question, generic tools, caps, output schema, and privacy rules",
                "JSONL-specific help is absent from the default/manual prompt view unless a case labels a shared-knowledge alternate condition",
                "missing counters are represented as unavailable, not zero",
                "live paired benchmark was not run by this command",
                "raw traces remain under ignored .private/jsh-059b1/ only",
                "benchmark-live-terminal-protocol remains inert and separate in purpose",
            ],
        },
        "sanitized_aggregate_template": {
            "run_id": "paired-<sanitized-label>-<yyyymmdd>-<attempt>",
            "case_count": 0,
            "live_run_performed": False,
            "pilot_claim_allowed": False,
            "default_condition": "manual_default_blind",
            "cases": [],
            "aggregate_by_side": {
                "jsonl_assisted": _side_metric_template(),
                "default_manual": _side_metric_template(),
            },
            "aggregate_comparison": _comparison_template(),
            "review": _review_template(),
        },
    }
    return {"paired_agent_benchmark_protocol": protocol}


def _metric_contract() -> dict[str, Any]:
    example_output_bytes = 1600
    example_context_bytes = 2400
    return {
        "counter_rule": "Missing counters must use the literal value unavailable; never use 0 as a placeholder.",
        "required_metrics": [
            "wall_time_seconds",
            "commands_or_tool_calls",
            "files_opened",
            "files_searched",
            "bytes_scanned",
            "bytes_read",
            "output_bytes",
            "context_bytes",
            "output_bytes_estimated_tokens",
            "context_bytes_estimated_tokens",
            "correctness",
            "first_pass_source_hit",
            "reviewer_confirmed_outcome",
            "contamination_notes",
        ],
        "estimated_metric_rule": {
            "formula": TOKEN_FORMULA,
            "tokenizer": "none",
            "status": "approximate",
            "warning": TOKEN_WARNING,
        },
        "example_estimates": {
            "output_bytes": example_output_bytes,
            "output_bytes_estimated_tokens": approximate_tokens_from_bytes(example_output_bytes),
            "context_bytes": example_context_bytes,
            "context_bytes_estimated_tokens": approximate_tokens_from_bytes(example_context_bytes),
        },
    }


def _side_metric_template() -> dict[str, Any]:
    return {
        "wall_time_seconds": "unavailable",
        "commands_or_tool_calls": "unavailable",
        "files_opened": "unavailable",
        "files_searched": "unavailable",
        "bytes_scanned": "unavailable",
        "bytes_read": "unavailable",
        "output_bytes": "unavailable",
        "context_bytes": "unavailable",
        "output_bytes_estimated_tokens": "unavailable",
        "context_bytes_estimated_tokens": "unavailable",
        "correctness": "unavailable",
        "first_pass_source_hit": "unavailable",
        "reviewer_confirmed_outcome": "unavailable",
        "contamination_notes": [],
    }


def _comparison_template() -> dict[str, Any]:
    return {
        "correctness_winner": "unavailable",
        "faster_side": "unavailable",
        "lower_command_count_side": "unavailable",
        "lower_file_open_count_side": "unavailable",
        "lower_bytes_read_side": "unavailable",
        "first_pass_advantage_side": "unavailable",
        "comparison_notes": [],
    }


def _review_template() -> dict[str, Any]:
    return {
        "reviewer_confirmed_outcome": "unavailable",
        "contamination_labels": [],
        "residual_uncertainty": [],
    }
