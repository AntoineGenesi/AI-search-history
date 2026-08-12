from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from .agent_contract import build_agent_find_payload
from .audit_coverage import audit_coverage_options_from_args, build_audit_coverage_ledger
from .audit_depth import AUDIT_DEPTH_CHOICES
from .agent_use_value_gate import (
    DEFAULT_JSH1043_MANIFEST_PATH,
    DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH,
    DEFAULT_JSH1045_EXPANSION_SUMMARY_PATH,
    build_agent_use_value_expansion_validation_payload,
    build_agent_use_value_manifest_validation_payload,
    build_agent_use_value_reduced_run_validation_payload,
)
from .agent_loop_cost import (
    DEFAULT_CASE_SELECTION_PATH as DEFAULT_AGENT_LOOP_CASE_SELECTION_PATH,
    DEFAULT_OUTPUT_PATH as DEFAULT_AGENT_LOOP_CONTRACT_OUTPUT_PATH,
    DEFAULT_RETAINED_TRACE_COUNTER_OUTPUT_PATH,
    DEFAULT_RETAINED_TRACE_COUNTER_REPORT_PATH,
    build_agent_loop_cost_contract_payload,
    build_retained_trace_counter_payload,
    write_agent_loop_cost_contract,
    write_retained_trace_counter_packet,
)
from .archive_snapshot import build_archive_snapshot_proof_payload
from .ask_router import (
    build_ask_router_payload,
    execution_kind_for_route_family,
    _looks_like_parent_main_complete_interval_status_query,
)
from .capability_diagnostic import build_capability_diagnostic
from .bulk_line_matcher import build_bulk_line_match_payload, load_phrase_specs
from .agent_skill_install import (
    SUPPORTED_AGENTS,
    default_agent_home,
    install_agent_skill,
    render_agent_skill_install,
)
from .codex_skill_install import default_codex_home, install_codex_skill, render_codex_skill_install
from .compact_handoff_usefulness import (
    DEFAULT_ANSWER_USE_FIXTURE as DEFAULT_COMPACT_HANDOFF_ANSWER_USE_FIXTURE,
    DEFAULT_FIXTURE as DEFAULT_COMPACT_HANDOFF_USEFULNESS_FIXTURE,
    build_compact_handoff_answer_use_eval_payload,
    build_compact_handoff_usefulness_eval_payload,
    render_compact_handoff_answer_use_markdown,
    render_compact_handoff_usefulness_markdown,
)
from .conversation_records import classify_conversation_record
from .coordinator_context import build_coordinator_context_payload
from .source_tier_answer_use import (
    DEFAULT_FIXTURE as DEFAULT_SOURCE_TIER_ANSWER_USE_FIXTURE,
    build_source_tier_answer_use_eval_payload,
    render_source_tier_answer_use_markdown,
)
from .retrieve_expand_answer_use import (
    DEFAULT_FIXTURE as DEFAULT_RETRIEVE_EXPAND_ANSWER_USE_FIXTURE,
    build_retrieve_expand_answer_use_eval_payload,
    render_retrieve_expand_answer_use_markdown,
)
from .evaluation import build_efficiency_eval
from .eval_decision_loop import build_eval_decision_loop_payload
from .embedding_experiment import DEFAULT_EMBEDDING_SPIKE_FIXTURE, FASTEMBED_MODEL_ID, build_embedding_experiment_payload
from .expansion import build_expansion_family_audit_report
from .expansion_family_probe import build_expansion_family_probe_report, supported_family_names
from .failure_analysis import build_failure_analysis_payload
from .forensics import build_forensics_payload
from .generated_metadata import build_generated_metadata_discovery_report
from .harness_artifacts import (
    DEFAULT_DRY_RUN_CASE_ID,
    DEFAULT_HARNESS_SCHEMA_PATH,
    build_harness_artifact_validation_payload,
    build_harness_dry_run_artifacts_payload,
    build_harness_offline_scorer_artifacts_payload,
)
from .harness_loop import DEFAULT_MINIMUM_LOOP_CASE_ID, build_harness_minimum_loop_payload
from .history_home import history_home_from_args
from .impact_benchmark import DEFAULT_FIXTURE as DEFAULT_IMPACT_FIXTURE
from .impact_benchmark import build_impact_benchmark_payload
from .install_validation import validate_install
from .inspect_adapter import DEFAULT_X12G_ADJUDICATION_PATH, write_inspect_compatible_export
from .gittools import (
    commit_is_reachable,
    commit_metadata,
    git_root_for,
    last_change_for_path,
    merge_commits_for_task,
)
from .latest_user_messages import build_latest_user_messages_payload, latest_user_messages
from .lineage import build_lineage_payload
from .live_terminal_protocol import build_live_terminal_protocol_payload
from .manifest import build_manifest_report
from .mechanism_eval import DEFAULT_FIXTURE as DEFAULT_MECHANISM_EVAL_FIXTURE
from .mechanism_eval import build_mechanism_eval_payload
from .measured_benchmark import DEFAULT_FIXTURE as DEFAULT_MEASURED_FIXTURE
from .measured_benchmark import build_measured_benchmark_payload
from .prompt_echo import setup_echo_classification
from .output import project_semantic_debug_continuation_step, render
from .paired_agent_benchmark_protocol import build_paired_agent_benchmark_protocol_payload
from .private_benchmark_mine import build_private_benchmark_mine_payload
from .profiling import AskExecutePhaseEmitter, finish_phase_profile, start_phase_profile
from .query import infer_query
from .redaction import RedactionContext
from .real_history_impact import DEFAULT_MANIFEST as DEFAULT_REAL_HISTORY_IMPACT_MANIFEST
from .real_history_impact import DEFAULT_PRIVATE_BRIDGE, DEFAULT_RAW_OUTPUT_DIR, build_real_history_impact_payload
from .real_history_benchmark_drift import (
    DEFAULT_AFTER_REPORT as DEFAULT_REAL_HISTORY_DRIFT_AFTER_REPORT,
    DEFAULT_BEFORE_REPORT as DEFAULT_REAL_HISTORY_DRIFT_BEFORE_REPORT,
    build_real_history_benchmark_drift_payload,
)
from .real_history_post_jsh763_date_timeline_diagnostic import (
    DEFAULT_BENCHMARK as DEFAULT_POST_JSH763_DATE_TIMELINE_BENCHMARK,
    DEFAULT_JSH711_REPORT as DEFAULT_POST_JSH763_DATE_TIMELINE_JSH711_REPORT,
    DEFAULT_JSH721_REPORT as DEFAULT_POST_JSH763_DATE_TIMELINE_JSH721_REPORT,
    DEFAULT_JSH722_REPORT as DEFAULT_POST_JSH763_DATE_TIMELINE_JSH722_REPORT,
    DEFAULT_MARKDOWN_OUTPUT as DEFAULT_POST_JSH763_DATE_TIMELINE_MARKDOWN_OUTPUT,
    DEFAULT_OUTPUT as DEFAULT_POST_JSH763_DATE_TIMELINE_OUTPUT,
    DEFAULT_RAW_RUN as DEFAULT_POST_JSH763_DATE_TIMELINE_RAW_RUN,
    build_post_jsh763_date_timeline_diagnostic_payload,
)
from .real_run_gate import (
    DEFAULT_JSH605_REVIEW_PATH,
    DEFAULT_JSH605_SUMMARY_PATH,
    build_real_run_gate_payload,
)
from .real_history_miss_audit import DEFAULT_MANIFEST as DEFAULT_REAL_HISTORY_MISS_AUDIT_MANIFEST
from .real_history_miss_audit import DEFAULT_PRIVATE_BRIDGE as DEFAULT_REAL_HISTORY_MISS_AUDIT_PRIVATE_BRIDGE
from .real_history_miss_audit import DEFAULT_RAW_RUN as DEFAULT_REAL_HISTORY_MISS_AUDIT_RAW_RUN
from .real_history_miss_audit import build_real_history_package_miss_audit_payload
from .real_history_remaining_miss_diagnostic import (
    DEFAULT_BENCHMARK_REPORT as DEFAULT_REAL_HISTORY_REMAINING_MISS_BENCHMARK_REPORT,
)
from .real_history_remaining_miss_diagnostic import (
    DEFAULT_MISS_AUDIT_REPORT as DEFAULT_REAL_HISTORY_REMAINING_MISS_AUDIT_REPORT,
)
from .real_history_remaining_miss_diagnostic import build_real_history_remaining_miss_diagnostic_payload
from .receiving_agent_trace_ledger import (
    DEFAULT_REPORT_JSON as DEFAULT_RECEIVING_AGENT_LEDGER_JSON,
    DEFAULT_REPORT_MD as DEFAULT_RECEIVING_AGENT_LEDGER_MD,
    build_receiving_agent_trace_ledger,
    parse_lane_arg,
    render_receiving_agent_trace_ledger_markdown,
)
from .repo_source_no_evidence import build_repo_source_no_evidence_payload
from .real_history_opened_file_progress_diagnostic import (
    DEFAULT_JSH706_BENCHMARK_REPORT as DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH706_BENCHMARK,
    DEFAULT_JSH706_MISS_AUDIT_REPORT as DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH706_AUDIT,
    DEFAULT_JSH707_BENCHMARK_REPORT as DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH707_BENCHMARK,
    DEFAULT_JSH707_MISS_AUDIT_REPORT as DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH707_AUDIT,
    build_real_history_opened_file_progress_diagnostic_payload,
)
from .real_history_embedding import DEFAULT_MANIFEST as DEFAULT_REAL_HISTORY_EMBEDDING_MANIFEST
from .real_history_embedding import DEFAULT_PRIVATE_BRIDGE as DEFAULT_REAL_HISTORY_EMBEDDING_PRIVATE_BRIDGE
from .real_history_embedding import build_real_history_embedding_payload
from .real_history_timestamp_seek import DEFAULT_MANIFEST as DEFAULT_REAL_HISTORY_TIMESTAMP_MANIFEST
from .real_history_timestamp_seek import DEFAULT_PRIVATE_BRIDGE as DEFAULT_REAL_HISTORY_TIMESTAMP_PRIVATE_BRIDGE
from .real_history_timestamp_seek import DEFAULT_RAW_OUTPUT_DIR as DEFAULT_REAL_HISTORY_TIMESTAMP_RAW_OUTPUT_DIR
from .real_history_timestamp_seek import build_real_history_timestamp_seek_payload
from .retrieval_modes import (
    QUERY_PLAN_MODE_AUTO,
    QUERY_PLAN_MODES,
    RETRIEVAL_MODE_FIELDED_BM25,
    RETRIEVAL_MODE_PACKAGE,
    RETRIEVAL_MODE_RRF_FUSION,
    RETRIEVAL_MODES,
    elapsed_label,
    run_retrieval_mode,
)
from .scanner import search
from .semantic_debugging import (
    DEFAULT_GOLD_MANIFEST,
    build_core_convo_first_read_plan,
    build_semantic_debug_payload,
    summarize_answer_readiness,
    summarize_guided_audit_flow,
)
from .skill_audit import build_skill_audit_payload
from .source_expansion import ExpansionRequest, build_follow_next_reads_payload, build_source_expansion_payload
from .sources import Limits, SourcePlan, build_jsonl_candidate_discovery, build_source_plan, normalize_local_path
from .status_anchor import DEFAULT_STATUS_QUERY, build_status_anchor_payload
from .structural_hybrid import search_structural_hybrid_budgeted
from .timeutil import parse_datetime, parse_now, timezone_from_args
from .tool_events import build_tool_events_payload
from .workflow_repeat import build_workflow_repeat_payload
from .masking import MASK_MODES, MaskContext


LOCAL_EVIDENCE_MODES = ("public", "internal")


PUBLIC_HELP_EPILOG = """\
Launch-facing commands:
  install-agent-skill    Install the optional Codex or Claude skill after installing the Python CLI.
  install-codex-skill    Install the optional Codex skill after installing the Python CLI.
  validate-install        Check that the package/console script can run.
  diagnose-capabilities  Report installed core and optional capabilities.
  ask                    Route a natural-language prompt to the first JSONL command.
  semantic-debug         Main evidence workflow for memory, action proof, provenance, and no-evidence questions.
  bulk-line-match        Local bulk literal phrase candidate generation for selected JSONL roots.
  read-more              Open bounded context for an exact source ref.
  follow-next-reads      Expand saved next_reads from a JSON report without rerunning search.
  find                   Focused candidate recovery when the full semantic-debug packet is unnecessary.

Developer/internal commands:
  benchmark/eval, private mining, harness/proof, real-history diagnostic,
  embedding/archive experiment, and validation-gate families are maintained for
  local product work. They are not public first-run guidance or release/readiness proof.

Use `jsonl-history-search <command> --help` for a launch command.
Use `jsonl-history-search --help-internal` to list the full developer command inventory.
"""


INTERNAL_HELP_EPILOG = """\
Developer note: this full inventory includes benchmark, harness, eval,
diagnostic, and proof commands for local product work. Do not treat their
presence here as a public first-run path or as evidence of release readiness,
token savings, all-history coverage, or broad superiority.

Internal command groups:
  Agent-facing front doors:
    ask, semantic-debug, what-happened-since, semantic-wrapup,
    artifact-currentness, skill-check/skill-audit, no-evidence-report,
    bulk-line-match, read-more, follow-next-reads, find
  Local diagnostics:
    validate-install, diagnose-capabilities, discover-jsonl,
    structured-tool-events, workflow-repeat-finder, forensics,
    local-evidence-diagnostic, failure-analysis
  Eval/benchmark/proof machinery:
    benchmark, benchmark-real-history-impact, mechanism-eval,
    eval-decision-loop, agent-use-value-*, validate-harness-artifacts,
    harness-*, real-history-*, archive, and embedding experiments
"""


def _command_shape_guidance(argv: list[str]) -> str | None:
    if not argv:
        return None
    command = argv[0]
    if command == "find" and "--max-results" in argv:
        return "\n".join(
            [
                "Invalid JSONL command shape: `find` does not accept `--max-results`.",
                "Use `--max-matches` instead.",
                'Example: jsonl-history-search find "<query>" --max-matches 20 --cwd "$PWD" --format text',
            ]
        )
    if command == "read-more" and "--context" in argv:
        return "\n".join(
            [
                "Invalid JSONL command shape: `read-more` does not accept `--context`.",
                "Use a bounded window read with `--mode window --before N --after N`.",
                'Example: jsonl-history-search read-more <source-ref> --mode window --before 2 --after 2 --cwd "$PWD" --format text',
            ]
        )
    if command == "read-more" and "--lines" in argv:
        lines_index = argv.index("--lines")
        try:
            window_size = int(argv[lines_index + 1])
        except (IndexError, ValueError):
            window_size = 2
        return "\n".join(
            [
                "Invalid JSONL command shape: `read-more` does not accept `--lines`.",
                "Use a bounded window read with `--mode window --before N --after N`.",
                "Example: jsonl-history-search read-more <source-ref> --mode window "
                f'--before {window_size} --after {window_size} --cwd "$PWD" --format text',
            ]
        )
    return None


def build_parser(*, public_help: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jsonl-history-search",
        description=(
            "JSONL: AI conversation parsing/mining for semantic debugging and "
            "agent memory/debug workflows."
        ),
        epilog=PUBLIC_HELP_EPILOG if public_help else INTERNAL_HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="COMMAND" if public_help else None,
    )
    ask = subparsers.add_parser(
        "ask",
        description=(
            "Natural-language first-command resolver for JSONL history/status/"
            "action/currentness/wrap-up prompts. It does not produce final answers."
        ),
    )
    ask.add_argument("query", help="Original user wording, or a summary paired with --original-query-file.")
    add_common_args(ask)
    ask.add_argument(
        "--evidence-source",
        choices=("local", "conversation-history"),
        default=None,
        help=(
            "Required for prompts naming tasks/...md: local selects bounded Git/task "
            "evidence; conversation-history selects JSONL semantic debugging."
        ),
    )
    ask.add_argument(
        "--activation-mode",
        choices=("implicit", "explicit-only"),
        default=os.environ.get("JSONL_HISTORY_SEARCH_ACTIVATION_MODE", "implicit"),
        help=(
            "Latest-status trigger mode. implicit (default) routes natural prior-message wording; "
            "explicit-only requires an explicit history-tool alias such as leading `convo` "
            "or suffix `use the convo tool`. The environment variable "
            "JSONL_HISTORY_SEARCH_ACTIVATION_MODE sets the default."
        ),
    )
    ask.add_argument(
        "--audit-depth",
        choices=AUDIT_DEPTH_CHOICES,
        default=None,
        help=(
            "Audit-depth contract for history routes. quick is the default; "
            "in-depth and exhaustive are explicit deeper contracts for later "
            "coverage workflows. Omit to infer only from strong audit wording."
        ),
    )
    ask.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Execute the selected route only when it is an allow-listed JSONL "
            "semantic-debug or no-evidence route; unsupported routes return a fail-closed contract."
        ),
    )
    ask.add_argument(
        "--query-origin",
        choices=("original", "summary"),
        default=None,
        help=(
            "Declare whether the positional query is exact original wording or a retrieval summary. "
            "summary requires --original-query-file or the free-running prompt-file environment contract."
        ),
    )
    ask.add_argument(
        "--original-query-file",
        default=None,
        help=(
            "UTF-8 file containing the exact original prompt. Its canonical path, byte count, and "
            "SHA-256 are recorded without copying its contents into the route report."
        ),
    )
    ask.add_argument(
        "--answer",
        action="store_true",
        help=(
            "Execute the selected safe route, inspect bounded proof refs from the "
            "answer contract, and return a concise source-backed answer when supported."
        ),
    )
    ask.add_argument(
        "--auto-execute-bounded-no-evidence",
        action="store_true",
        help=(
            "After routing, execute only high-confidence bounded no-evidence prompts "
            "through the safe no-evidence-report path. All other routes remain route-only."
        ),
    )
    ask.add_argument(
        "--report-output",
        default=None,
        help="Optional path where ask --execute writes the full selected-route JSON report.",
    )
    ask.add_argument("--task", default=None)
    ask.add_argument("--path", default=None)
    ask.add_argument("--commit", default=None)
    ask.add_argument("--branch", default=None)
    ask.add_argument("--around", default=None)
    ask.add_argument("--watchlist", action="append", default=[])
    ask.add_argument("--gold-manifest", default=str(DEFAULT_GOLD_MANIFEST))
    ask.add_argument("--evaluate-sanitized-manifest-gate", action="store_true")
    ask.add_argument("--include-manifest", action="store_true")
    ask.add_argument("--compact-provenance-handoff", action="store_true")
    ask.add_argument("--evidence-context-window", type=int, default=0)
    ask.add_argument("--retrieval-mode", choices=RETRIEVAL_MODES, default=RETRIEVAL_MODE_PACKAGE)
    ask.add_argument("--query-plan", choices=QUERY_PLAN_MODES, default=QUERY_PLAN_MODE_AUTO)
    ask.add_argument("--continuation-ledger", action="store_true")
    for name in ("plan", "search"):
        sub = subparsers.add_parser(name)
        sub.add_argument("query", nargs="?", default="")
        add_common_args(sub)
    find_descriptions = {
        "find": (
            "Focused fuzzy/typo/synonym/paraphrase candidate recovery when the "
            "full semantic-debug router packet is unnecessary."
        ),
        "no-evidence-report": (
            "Report searched-scope-only no-evidence; this is not all-history, "
            "global, or permanent absence."
        ),
    }
    for name in ("find", "plan-sources", "broaden-query", "find-candidates", "timeline", "cluster-candidates", "no-evidence-report"):
        find = subparsers.add_parser(name, description=find_descriptions.get(name))
        find.add_argument("query", nargs="?", default="")
        add_common_args(find)
        find.add_argument(
            "--include-manifest",
            action="store_true",
            help="Include optional local manifest shortlist metadata for no-evidence and source-planning sections.",
        )
        find.add_argument(
            "--report-output",
            default=None,
            help="Optional path where the full find report should be written as JSON or Markdown.",
        )
        find.add_argument(
            "--compact-provenance-handoff",
            action="store_true",
            help=(
                "Opt in to an extra compact_provenance_handoff JSON packet derived "
                "from emitted candidates, source refs, next reads, and no-evidence scope."
            ),
        )
        find.add_argument(
            "--evidence-context-window",
            type=int,
            default=0,
            help=(
                "Explicit opt-in raw evidence packet context window. 0 keeps evidence_packets metadata-only; "
                "N includes up to N same-source neighboring records around packet hit_children."
            ),
        )
        find.add_argument(
            "--retrieval-mode",
            choices=RETRIEVAL_MODES,
            default=RETRIEVAL_MODE_PACKAGE,
            help=(
                "Retrieval backend for agent-facing find output. Default package keeps existing behavior; "
                "fielded-bm25 explicitly opts into SQLite FTS5/BM25 record retrieval with durable CLI cache unless ephemeral; "
                "rrf-fusion explicitly opts into package plus fielded BM25 rank-only RRF diagnostics."
            ),
        )
        find.add_argument(
            "--query-plan",
            choices=QUERY_PLAN_MODES,
            default=QUERY_PLAN_MODE_AUTO,
            help=(
                "Controls deterministic fielded child query channels. auto enables supported intent children; "
                "off preserves the old fielded stream/channel shape; debug matches auto retrieval and emits "
                "sanitized child-plan diagnostics."
            ),
        )
        find.add_argument(
            "--continuation-ledger",
            action="store_true",
            help=(
                "Opt in to a bounded private-safe continuation candidate ledger for explicit fielded-bm25 "
                "retrieval diagnostics. Defaults stay unchanged."
            ),
        )
        find.add_argument(
            "--deep-audit-target-contract",
            type=_json_object_argument,
            default=None,
            help=argparse.SUPPRESS,
        )
    structural_hybrid = subparsers.add_parser(
        "find-structural-hybrid-budgeted",
        description=(
            "Opt-in no-send installed helper for package-first search with a "
            "budgeted structural sidecar supplement."
        ),
    )
    structural_hybrid.add_argument("query", nargs="?", default="")
    add_common_args(structural_hybrid)
    structural_hybrid.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the full budgeted structural hybrid find report should be written.",
    )
    bulk_line_match = subparsers.add_parser(
        "bulk-line-match",
        description=(
            "Local single-pass literal phrase matcher for many OCR/screenshot/source phrases. "
            "Emits candidate refs only; validate selected refs with semantic-debug/read-more/"
            "follow-next-reads/audit-coverage."
        ),
    )
    add_common_args(bulk_line_match)
    bulk_line_match.add_argument(
        "--phrase",
        action="append",
        default=[],
        help="Exact phrase to search for. Repeat for bulk batches.",
    )
    bulk_line_match.add_argument(
        "--phrases-file",
        default=None,
        help="UTF-8 file containing one exact phrase per nonblank, non-comment line.",
    )
    bulk_line_match.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Require case-sensitive literal substring matches. Default is case-insensitive literal matching.",
    )
    bulk_line_match.add_argument(
        "--snippet-chars",
        type=int,
        default=260,
        help="Maximum candidate snippet characters; 0 keeps the parsed record text.",
    )
    latest_user = subparsers.add_parser(
        "latest-user-messages",
        description=(
            "Deterministic newest-first lookup for high-confidence Codex user "
            "messages. This does not use semantic search or phrase ranking."
        ),
    )
    add_common_args(latest_user)
    latest_user.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum user messages to return; defaults to --max-matches.",
    )
    latest_user.add_argument(
        "--snippet-chars",
        type=int,
        default=500,
        help="Maximum characters of each message snippet to include; 0 keeps the full text.",
    )
    status_anchor = subparsers.add_parser(
        "what-happened-since",
        description=(
            "JSONL-first status-anchor workflow for natural questions like "
            "'what happened since my last message?' or 'what did you do while I was away?'."
        ),
    )
    status_anchor.add_argument("query", nargs="?", default=DEFAULT_STATUS_QUERY)
    add_common_args(status_anchor)
    for action in status_anchor._actions:
        if action.dest == "format":
            action.choices = ("text", "json", "markdown", "answer")
            break
    status_anchor.add_argument(
        "--parent-thread",
        action="store_true",
        help="Require a proven parent-thread anchor/boundary before answer-mode promotion.",
    )
    status_anchor.add_argument(
        "--complete-interval",
        action="store_true",
        help="Collect post-anchor interval activity up to the complete-interval hard cap before answer-mode rendering.",
    )
    status_anchor.add_argument(
        "--anchor-ref",
        help="Use an exact canonical codex_history:history.jsonl:<line> parent-thread anchor.",
    )
    status_anchor.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum recent user messages to inspect for anchor candidates; defaults to --max-matches.",
    )
    status_anchor.add_argument(
        "--snippet-chars",
        type=int,
        default=280,
        help="Maximum characters of each message snippet to include; 0 keeps the full text.",
    )
    artifact_currentness = subparsers.add_parser(
        "artifact-currentness",
        description=(
            "Resolve whether a plan, TODO, ticket, task, report, or topic should "
            "be trusted as current or treated as stale/superseded provenance."
        ),
    )
    artifact_currentness.add_argument("artifact_or_query", help="Artifact path, task/ticket id, or natural currentness query.")
    add_common_args(artifact_currentness)
    local_evidence = subparsers.add_parser(
        "local-evidence-diagnostic",
        description=(
            "Render raw-run evidence in explicit public/export or internal/local "
            "product-debugging mode."
        ),
    )
    local_evidence.add_argument("--raw-run", required=True, help="Local raw-run JSON with rows/source refs/commands.")
    local_evidence.add_argument("--evidence-mode", choices=LOCAL_EVIDENCE_MODES, default="public")
    local_evidence.add_argument("--limit", type=int, default=20)
    local_evidence.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    local_evidence.add_argument("--report-output", default=None)
    semantic_wrapup = subparsers.add_parser(
        "semantic-wrapup",
        description=(
            "Mine the selected/current session for complaints, corrections, workflow failures, "
            "assistant/tool/runtime errors, whole-session coverage, cause chains, and "
            "user-selectable fix suggestions. Use skill-audit for deep skill workflow audits."
        ),
    )
    semantic_wrapup.add_argument("query", nargs="?", default="semantic wrap-up this run")
    add_common_args(semantic_wrapup)
    semantic_wrapup.add_argument("--report-output", default=None)
    read_more = subparsers.add_parser(
        "read-more",
        description=(
            "Bounded provenance expansion for an exact source ref. Reads one "
            "record, a line window, previous/next records, or a bounded "
            "source/session/day neighborhood from the same source."
        ),
    )
    read_more.add_argument("target", help="Exact source target as path:line.")
    add_common_args(read_more)
    read_more.add_argument("--mode", choices=("record", "window", "next", "before", "neighborhood"), default="record")
    read_more.add_argument("--scope", choices=("source", "session", "day"), default="source")
    read_more.add_argument("--before", type=int, default=2)
    read_more.add_argument("--after", type=int, default=2)
    read_more.add_argument("--next-count", type=int, default=3)
    read_more.add_argument("--max-records", type=int, default=20)
    read_more.add_argument("--projection", choices=("full", "compact"), default=None)
    read_more.add_argument("--compact-text-limit", type=int, default=1200)
    follow_next_reads = subparsers.add_parser(
        "follow-next-reads",
        description=(
            "Bounded local follow-through for saved find/semantic-debug JSON "
            "next_reads. Expands exact source refs without rerunning search."
        ),
    )
    add_common_args(follow_next_reads)
    follow_next_reads.set_defaults(format="json")
    follow_next_reads.add_argument("--from-report", required=True, help="Saved agent_find or semantic_debug JSON report.")
    follow_next_reads.add_argument("--kind", choices=("recovery",), default="recovery")
    follow_next_reads.add_argument("--limit", type=int, default=3)
    follow_next_reads.add_argument(
        "--reviewed-usability-decisions",
        default=None,
        help=(
            "Optional sanitized JSON decisions file for opened recovery rows. "
            "Only status labels, counts, IDs, ranks, and hashed handles are consumed."
        ),
    )
    follow_next_reads.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the follow-next-reads recovery packet should be written as JSON.",
    )
    failure_analysis = subparsers.add_parser(
        "failure-analysis",
        description=(
            "Deterministic saved-report synthesis for workflow failure reconstruction. "
            "Consumes semantic-debug plus optional forensics/follow-next-reads JSON."
        ),
    )
    failure_analysis.add_argument("--semantic-debug-report", required=True, help="Saved semantic-debug JSON report.")
    failure_analysis.add_argument("--story-id", required=True, help="Stable caller-provided story identifier.")
    failure_analysis.add_argument("--forensics-report", default=None, help="Optional saved forensics JSON report.")
    failure_analysis.add_argument(
        "--follow-next-reads-report",
        default=None,
        help="Optional saved follow-next-reads JSON report.",
    )
    failure_analysis.add_argument(
        "--follow-through-behavior-labels-report",
        default=None,
        help=(
            "Optional sanitized follow-through behavior-label JSON report. "
            "Known case labels are classified; safe custom labels with repo-relative handles "
            "are retained as unclassified semantic-debug evidence."
        ),
    )
    failure_analysis.add_argument(
        "--repo-artifact-evidence-report",
        default=None,
        help=(
            "Optional sanitized generic repo-artifact evidence JSON report. "
            "Safe repo-relative handles, support surfaces, category candidates, phase hints, "
            "matched safe terms, counts, and claim boundaries can frame semantic-debug categories "
            "without becoming Semantic Linter behavior labels."
        ),
    )
    failure_analysis.add_argument(
        "--format",
        choices=("text", "json", "markdown"),
        default="json",
        help=(
            "Output format. All formats expose failure_analysis.devlog_brief; "
            "text and Markdown render it under a Devlog Brief heading before the full packet."
        ),
    )
    failure_analysis.add_argument("--report-output", default=None)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("query", nargs="?", default="")
    add_common_args(manifest)
    manifest.add_argument(
        "--shortlist-limit",
        type=int,
        default=None,
        help="Optional maximum manifest entries to keep in the shortlist report.",
    )
    metadata_discovery = subparsers.add_parser("metadata-discovery")
    metadata_discovery.add_argument("query", nargs="?", default="")
    add_common_args(metadata_discovery)
    metadata_discovery.add_argument("--min-count", type=int, default=1)
    metadata_discovery.add_argument("--max-candidates", type=int, default=40)
    metadata_discovery.add_argument("--report-output", default=None)
    audit_coverage = subparsers.add_parser(
        "audit-coverage",
        description=(
            "Build a committed-safe selected-model/session coverage ledger from "
            "structural session_meta and turn_context metadata."
        ),
    )
    add_common_args(audit_coverage)
    audit_coverage.add_argument("--start-date", default=None, help="Inclusive YYYY-MM-DD path-date window start.")
    audit_coverage.add_argument("--end-date", default=None, help="Inclusive YYYY-MM-DD path-date window end.")
    audit_coverage.add_argument("--window-days", type=int, default=1)
    audit_coverage.add_argument("--selected-model", action="append", default=[])
    audit_coverage.add_argument("--thread-source", action="append", default=[])
    audit_coverage.add_argument("--agent-role", action="append", default=[])
    audit_coverage.add_argument("--cwd-contains", action="append", default=[])
    audit_coverage.add_argument(
        "--trace-ledger-limit",
        type=int,
        default=200,
        help="Maximum hashed per-trace rows to emit; use 0 for aggregate-only reports or -1 for all.",
    )
    audit_coverage.add_argument("--report-output", default=None)
    expansion_family_audit = subparsers.add_parser(
        "expansion-family-audit",
        description="Report-only audit of contextual query expansion family hygiene.",
    )
    expansion_family_audit.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    expansion_family_audit.add_argument("--report-output", default=None)
    expansion_family_probe = subparsers.add_parser(
        "expansion-family-probe",
        description="Report-only aggregate retained-real-history probe for one expansion family.",
    )
    expansion_family_probe.add_argument("--family", choices=supported_family_names(), required=True)
    expansion_family_probe.add_argument("--manifest", type=Path, default=None)
    expansion_family_probe.add_argument("--private-bridge", type=Path, default=None)
    expansion_family_probe.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    expansion_family_probe.add_argument("--report-output", default=None)
    forensics = subparsers.add_parser("forensics")
    add_common_args(forensics)
    forensics.add_argument("--commit", default=None)
    forensics.add_argument("--path", default=None)
    forensics.add_argument("--task", default=None)
    forensics.add_argument("--branch", default=None)
    forensics.add_argument("--around", default=None)
    lineage = subparsers.add_parser("compare-project-lineage")
    lineage.add_argument("query", nargs="?", default="")
    add_common_args(lineage)
    lineage.add_argument("--project-root", default=None)
    lineage.add_argument("--sibling-root", action="append", default=[])
    lineage.add_argument("--max-sibling-dirs", type=int, default=40)
    lineage.add_argument("--max-project-files", type=int, default=250)
    lineage.add_argument("--max-common-files", type=int, default=40)
    lineage.add_argument("--max-projects", type=int, default=12)
    lineage.add_argument("--timeline-limit", type=int, default=20)
    lineage.add_argument("--report-output", default=None)
    skill_audit = subparsers.add_parser(
        "skill-audit",
        aliases=("skill-check",),
        description=(
            "Deep skill workflow audit: distinguish proper skill use, missed triggers, "
            "misapplication, and missing referenced-file follow-through from mere mentions."
        ),
    )
    skill_audit.set_defaults(command="skill-audit")
    skill_audit.add_argument("skill")
    add_common_args(skill_audit)
    skill_audit.add_argument("--pre-window", type=int, default=3)
    skill_audit.add_argument("--post-window", type=int, default=6)
    skill_audit.add_argument("--correction-window", type=int, default=12)
    skill_audit.add_argument("--report-output", default=None)
    structured = subparsers.add_parser("structured-tool-events")
    structured.add_argument("query", nargs="?", default="")
    add_common_args(structured)
    structured.add_argument("--event-type", action="append", default=[])
    structured.add_argument("--skill", default=None)
    structured.add_argument("--path", action="append", default=[])
    structured.add_argument("--report-output", default=None)
    workflow_repeat = subparsers.add_parser("workflow-repeat-finder")
    workflow_repeat.add_argument("query", nargs="?", default="")
    add_common_args(workflow_repeat)
    workflow_repeat.add_argument("--min-instances", type=int, default=2)
    workflow_repeat.add_argument("--window-records", type=int, default=8)
    workflow_repeat.add_argument("--cluster-limit", type=int, default=12)
    workflow_repeat.add_argument(
        "--no-suggestions",
        action="store_true",
        help="Skip bounded prose extraction of prior automation suggestions.",
    )
    workflow_repeat.add_argument("--report-output", default=None)
    semantic_debug = subparsers.add_parser(
        "semantic-debug",
        description=(
            "Front door for remembered instructions, agent proof, "
            "provenance/semantic debugging, no-evidence checks, and "
            "answer-quality investigations. Inspect source_locator_handoff, "
            "next_reads, and claim boundaries before broad search."
        ),
    )
    semantic_debug.add_argument("query", nargs="?", default="")
    add_common_args(semantic_debug)
    semantic_debug.add_argument("--task", default=None)
    semantic_debug.add_argument("--path", default=None)
    semantic_debug.add_argument("--commit", default=None)
    semantic_debug.add_argument("--branch", default=None)
    semantic_debug.add_argument("--around", default=None)
    semantic_debug.add_argument(
        "--watchlist",
        action="append",
        default=[],
        help="Repeatable semantic-debug audit predicate to preserve in audit_plan.watchlist.",
    )
    semantic_debug.add_argument(
        "--audit-depth",
        choices=AUDIT_DEPTH_CHOICES,
        default=None,
        help=(
            "Audit-depth contract: quick (default), in-depth, or exhaustive. "
            "Non-quick semantic-debug output includes coverage status, slices, "
            "cap accounting, and promotion-safety gates."
        ),
    )
    semantic_debug.add_argument(
        "--deep-audit-target-contract",
        type=_json_object_argument,
        default=None,
        help=argparse.SUPPRESS,
    )
    semantic_debug.add_argument(
        "--deep-audit-continuation-step",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    semantic_debug.add_argument(
        "--gold-manifest",
        default=str(DEFAULT_GOLD_MANIFEST),
        help="Committed sanitized real-history gold manifest for coverage-only caveats; no private bridge is required.",
    )
    semantic_debug.add_argument(
        "--evaluate-sanitized-manifest-gate",
        action="store_true",
        help=(
            "Opt in to the expensive sanitized manifest per-case evaluation. "
            "By default semantic-debug reports only cheap manifest metadata and defers this gate."
        ),
    )
    semantic_debug.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the full semantic-debug packet should be written.",
    )
    semantic_debug.add_argument(
        "--compact-provenance-handoff",
        action="store_true",
        help=(
            "Opt in to an extra compact_provenance_handoff JSON packet derived "
            "from emitted candidates, source refs, next reads, and no-evidence scope."
        ),
    )
    semantic_debug.add_argument(
        "--evidence-context-window",
        type=int,
        default=0,
        help=(
            "Explicit opt-in raw evidence packet context window. 0 keeps evidence_packets metadata-only; "
            "N includes up to N same-source neighboring records around packet hit_children."
        ),
    )
    semantic_debug.add_argument(
        "--retrieval-mode",
        choices=RETRIEVAL_MODES,
        default=RETRIEVAL_MODE_PACKAGE,
        help=(
            "Retrieval backend for semantic-debug candidate evidence. Default package keeps existing behavior; "
            "fielded-bm25 explicitly opts into SQLite FTS5/BM25 record retrieval with durable CLI cache unless ephemeral; "
            "rrf-fusion explicitly opts into package plus fielded BM25 rank-only RRF diagnostics."
        ),
    )
    semantic_debug.add_argument(
        "--query-plan",
        choices=QUERY_PLAN_MODES,
        default=QUERY_PLAN_MODE_AUTO,
        help=(
            "Controls deterministic fielded child query channels. auto enables supported intent children; "
            "off preserves the old fielded stream/channel shape; debug matches auto retrieval and emits "
            "sanitized child-plan diagnostics."
        ),
    )
    semantic_debug.add_argument(
        "--continuation-ledger",
        action="store_true",
        help=(
            "Opt in to a bounded private-safe continuation candidate ledger for explicit fielded-bm25 "
            "semantic-debug retrieval diagnostics. Defaults stay unchanged."
        ),
    )
    semantic_debug.add_argument(
        "--spawn-max-children",
        type=int,
        default=8,
        help=(
            "Maximum spawned child/session transcript refs to resolve for semantic-debug provenance. "
            "Continuation commands may raise this when the previous run hit the child cap."
        ),
    )
    private_mine = subparsers.add_parser("private-benchmark-mine")
    add_common_args(private_mine)
    private_mine.add_argument("--query-family", action="append", default=[])
    private_mine.add_argument("--query", action="append", default=[], dest="queries")
    private_mine.add_argument("--target-category", action="append", default=[])
    private_mine.add_argument("--target-stage", action="append", default=[])
    private_mine.add_argument("--target-failure-mode", action="append", default=[])
    private_mine.add_argument(
        "--fixture",
        default="benchmarks/retrieval_gold/sanitized_cases.json",
        help="Sanitized retrieval benchmark fixture to compare against.",
    )
    private_mine.add_argument(
        "--output-dir",
        default=None,
        help="Private output directory; defaults to .private/jsh-013/run-YYYYMMDD-HHMMSS.",
    )
    private_mine.add_argument("--candidate-limit", type=int, default=100)
    private_mine.add_argument("--per-query-max-matches", type=int, default=20)
    private_mine.add_argument("--context-lines", type=int, default=3)
    private_mine.add_argument("--no-tool-events", action="store_true")
    private_mine.add_argument("--no-workflow-repeat", action="store_true")
    private_mine.add_argument(
        "--write-suggestions",
        action="store_true",
        help="Compatibility flag; sanitized suggestions are always written under the private output dir.",
    )
    private_mine.add_argument("--report-output", default=None)
    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument(
        "--fixture",
        default="benchmarks/retrieval_gold/sanitized_cases.json",
        help="Sanitized retrieval benchmark fixture to evaluate.",
    )
    benchmark.add_argument("--format", choices=("json", "markdown"), default="markdown")
    benchmark.add_argument(
        "--strategy",
        action="append",
        choices=("package_search", "helper_script", "raw_rg", "naive_broad_scan"),
        default=[],
        help="Strategy to include; may be passed more than once. Defaults to all strategies.",
    )
    benchmark.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Exit non-zero when large-impact thresholds fail.",
    )
    benchmark.add_argument(
        "--include-embedding-spike",
        action="store_true",
        help="Include the deterministic local fixture vector spike report; no production embeddings are run.",
    )
    benchmark.add_argument(
        "--embedding-spike-fixture",
        default="benchmarks/retrieval_gold/sanitized_embedding_corpus.json",
        help="Synthetic sanitized fixture for --include-embedding-spike.",
    )
    measured = subparsers.add_parser("benchmark-measured")
    add_common_args(measured)
    measured.add_argument(
        "--fixture",
        default=str(DEFAULT_MEASURED_FIXTURE),
        help="Sanitized retrieval benchmark fixture whose paraphrased cases become measured probes.",
    )
    measured.add_argument(
        "--case-limit",
        type=int,
        default=6,
        help="Maximum number of sanitized fixture cases to run when --query is not supplied.",
    )
    measured.add_argument(
        "--query",
        action="append",
        default=[],
        dest="queries",
        help="Optional explicit local probe. Query text is used for retrieval but never echoed in output.",
    )
    measured.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized measured benchmark report should be written.",
    )
    measured.add_argument(
        "--manifest-shortlist",
        action="store_true",
        help="Opt in to paired manifest-shortlist planning probes; default search/find behavior is unchanged.",
    )
    measured.add_argument(
        "--timestamp-seek-prototype",
        action="store_true",
        help="Opt in to transient timestamp/byte-offset seek probes; no default or persistent sidecar is created.",
    )
    impact = subparsers.add_parser("benchmark-impact")
    add_common_args(impact)
    impact.add_argument(
        "--fixture",
        default=str(DEFAULT_IMPACT_FIXTURE),
        help="Synthetic committed-only impact benchmark fixture to evaluate.",
    )
    impact.add_argument(
        "--case-limit",
        type=int,
        default=0,
        help="Maximum number of impact cases to run; 0 means all cases.",
    )
    impact.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Deterministic repeat count per case/baseline.",
    )
    impact.add_argument(
        "--baseline",
        action="append",
        choices=("raw_rg_literal", "terminal_rg_then_open", "package_search", "package_search_rg_prefilter"),
        default=[],
        help="Baseline to include; may be passed more than once. Defaults to all deterministic baselines.",
    )
    impact.add_argument("--terminal-open-limit", type=int, default=3)
    impact.add_argument("--context-lines", type=int, default=2)
    impact.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized impact benchmark report should be written.",
    )
    mechanism_eval = subparsers.add_parser("mechanism-eval")
    add_common_args(mechanism_eval)
    mechanism_eval.add_argument(
        "--fixture",
        default=str(DEFAULT_MECHANISM_EVAL_FIXTURE),
        help="Synthetic committed-only fixture for the tiny mechanism eval.",
    )
    mechanism_eval.add_argument(
        "--case-limit",
        type=int,
        default=3,
        help="Maximum number of fixture cases to run; 0 means all cases.",
    )
    mechanism_eval.add_argument("--metadata-min-count", type=int, default=1)
    mechanism_eval.add_argument("--metadata-max-candidates", type=int, default=40)
    mechanism_eval.add_argument(
        "--include-metadata-supplement",
        action="store_true",
        help="Add an opt-in report-only baseline that supplements the query with sanitized generated metadata kind/label terms.",
    )
    mechanism_eval.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized mechanism eval report should be written.",
    )
    compact_handoff_usefulness = subparsers.add_parser("compact-handoff-usefulness-eval")
    compact_handoff_usefulness.add_argument(
        "--fixture",
        default=str(DEFAULT_COMPACT_HANDOFF_USEFULNESS_FIXTURE),
        help="Committed synthetic compact-handoff usefulness fixture.",
    )
    compact_handoff_usefulness.add_argument("--format", choices=("json", "markdown"), default="markdown")
    compact_handoff_usefulness.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the compact handoff usefulness report should be written.",
    )
    compact_handoff_answer_use = subparsers.add_parser("compact-handoff-answer-use-eval")
    compact_handoff_answer_use.add_argument(
        "--fixture",
        default=str(DEFAULT_COMPACT_HANDOFF_ANSWER_USE_FIXTURE),
        help="Committed approved-safe compact-handoff answer-use fixture.",
    )
    compact_handoff_answer_use.add_argument("--format", choices=("json", "markdown"), default="markdown")
    compact_handoff_answer_use.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the compact handoff answer-use report should be written.",
    )
    source_tier_answer_use = subparsers.add_parser("source-tier-answer-use-eval")
    source_tier_answer_use.add_argument(
        "--fixture",
        default=str(DEFAULT_SOURCE_TIER_ANSWER_USE_FIXTURE),
        help="Committed synthetic source-tier answer-use fixture.",
    )
    source_tier_answer_use.add_argument("--format", choices=("json", "markdown"), default="markdown")
    source_tier_answer_use.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the source-tier answer-use report should be written.",
    )
    retrieve_expand_answer_use = subparsers.add_parser("retrieve-expand-answer-use-eval")
    retrieve_expand_answer_use.add_argument(
        "--fixture",
        default=str(DEFAULT_RETRIEVE_EXPAND_ANSWER_USE_FIXTURE),
        help="Committed synthetic retrieve -> expand answer-use fixture.",
    )
    retrieve_expand_answer_use.add_argument("--format", choices=("json", "markdown"), default="markdown")
    retrieve_expand_answer_use.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the retrieve -> expand answer-use report should be written.",
    )
    eval_decision_loop = subparsers.add_parser(
        "eval-decision-loop",
        description=(
            "Non-live product decision packet that synthesizes deterministic "
            "answer-use evals with optional aggregate real-history pressure JSON."
        ),
    )
    eval_decision_loop.add_argument("--format", choices=("json", "markdown"), default="markdown")
    eval_decision_loop.add_argument(
        "--pressure-report",
        default=None,
        help="Optional aggregate-only real-history pressure JSON to include in the decision packet.",
    )
    eval_decision_loop.add_argument(
        "--semantic-debug-report",
        default=None,
        help="Optional semantic-debug JSON report whose answer_readiness should drive routing summary.",
    )
    eval_decision_loop.add_argument(
        "--follow-next-reads-report",
        default=None,
        help="Optional follow-next-reads JSON report whose sanitized recovery outcome should refine readiness routing.",
    )
    eval_decision_loop.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the eval decision packet should be written.",
    )
    real_history_impact = subparsers.add_parser("benchmark-real-history-impact")
    add_common_args(real_history_impact)
    real_history_impact.add_argument(
        "--manifest",
        default=str(DEFAULT_REAL_HISTORY_IMPACT_MANIFEST),
        help="Sanitized reviewed real-history gold manifest.",
    )
    real_history_impact.add_argument(
        "--private-bridge",
        default=str(DEFAULT_PRIVATE_BRIDGE),
        help="Ignored private bridge JSON produced by jsh-049a.",
    )
    real_history_impact.add_argument(
        "--case-limit",
        type=int,
        default=0,
        help="Maximum number of reviewed real-history cases to run; 0 means all cases.",
    )
    real_history_impact.add_argument(
        "--case-family",
        action="append",
        default=[],
        help="Sanitized manifest family to include; may be passed more than once. Defaults to all families.",
    )
    real_history_impact.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat count per case/baseline.",
    )
    real_history_impact.add_argument(
        "--baseline",
        action="append",
        choices=(
            "raw_rg_literal",
            "terminal_rg_then_open",
            "manual_newest_first_open",
            "package_search",
            "package_search_rg_prefilter",
            "package_search_structural_sidecar",
            "package_search_structural_hybrid",
            "package_search_structural_hybrid_budgeted",
            "package_search_fielded_bm25",
            "package_search_rrf_fusion",
        ),
        default=[],
        help="Baseline to include; may be passed more than once. Defaults to all real-history impact baselines.",
    )
    real_history_impact.add_argument("--terminal-open-limit", type=int, default=3)
    real_history_impact.add_argument("--manual-open-limit", type=int, default=6)
    real_history_impact.add_argument("--context-lines", type=int, default=2)
    real_history_impact.add_argument(
        "--raw-output-dir",
        default=str(DEFAULT_RAW_OUTPUT_DIR),
        help="Ignored directory for raw per-case private run evidence.",
    )
    real_history_impact.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    real_history_impact.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    real_history_impact.add_argument(
        "--capture-expected-ref-diagnostics",
        action="store_true",
        help="Opt in to sanitized expected-ref reach/retention diagnostics in package-search rows.",
    )
    real_history_impact.add_argument(
        "--aggregate-only-report",
        action="store_true",
        help="Omit per-case rows from the committed report payload after aggregate metrics are computed.",
    )
    real_history_impact.add_argument(
        "--package-compact-handoff",
        action="store_true",
        help=(
            "Opt in to a separate compact package-search source-locator/source-window "
            "handoff diagnostic; default search output and output_context_bytes are unchanged."
        ),
    )
    real_history_impact.add_argument(
        "--agent-compact-provenance-handoff",
        action="store_true",
        help=(
            "Opt in to aggregate pressure metrics for the agent-facing JSH-804 "
            "compact_provenance_handoff packet built through the find contract; "
            "default search output and output_context_bytes are unchanged."
        ),
    )
    real_history_impact.add_argument(
        "--source-tier-answerability-pressure",
        action="store_true",
        help=(
            "Opt in to aggregate-only source-tier answerability pressure metrics for "
            "explicit package_search_fielded_bm25 rows using the live agent compact "
            "provenance handoff builder."
        ),
    )
    real_history_impact.add_argument(
        "--continuation-ledger",
        action="store_true",
        help=(
            "Opt in to aggregate-safe continuation candidate ledger metrics for explicit "
            "package_search_fielded_bm25 benchmark rows. Defaults and other baselines stay unchanged."
        ),
    )
    real_history_impact.add_argument(
        "--checkpoint-output",
        default=None,
        help=(
            "Optional JSONL path for aggregate-safe benchmark progress checkpoints. "
            "Records contain only stage/count/RSS labels, not private paths, text, queries, sessions, or payloads."
        ),
    )
    real_history_impact.add_argument(
        "--memory-guard-rss-bytes",
        type=int,
        default=0,
        help=(
            "Soft current-RSS stop threshold for benchmark pressure runs. 0 disables; "
            "when exceeded, returns memory_guard_stop with checkpoint evidence if configured."
        ),
    )
    real_history_timestamp_seek = subparsers.add_parser("benchmark-real-history-timestamp-seek")
    add_common_args(real_history_timestamp_seek)
    real_history_timestamp_seek.add_argument(
        "--manifest",
        default=str(DEFAULT_REAL_HISTORY_TIMESTAMP_MANIFEST),
        help="Sanitized reviewed real-history gold manifest.",
    )
    real_history_timestamp_seek.add_argument(
        "--private-bridge",
        default=str(DEFAULT_REAL_HISTORY_TIMESTAMP_PRIVATE_BRIDGE),
        help="Ignored private bridge JSON produced by jsh-049a.",
    )
    real_history_timestamp_seek.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat count per selected timestamp-applicable case.",
    )
    real_history_timestamp_seek.add_argument(
        "--raw-output-dir",
        default=str(DEFAULT_REAL_HISTORY_TIMESTAMP_RAW_OUTPUT_DIR),
        help="Ignored directory for raw per-case private timestamp-seek evidence.",
    )
    real_history_timestamp_seek.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    real_history_timestamp_seek.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    real_history_embedding = subparsers.add_parser("benchmark-real-history-embedding")
    add_common_args(real_history_embedding)
    real_history_embedding.add_argument(
        "--manifest",
        default=str(DEFAULT_REAL_HISTORY_EMBEDDING_MANIFEST),
        help="Sanitized reviewed real-history gold manifest.",
    )
    real_history_embedding.add_argument(
        "--private-bridge",
        default=str(DEFAULT_REAL_HISTORY_EMBEDDING_PRIVATE_BRIDGE),
        help="Ignored private bridge JSON produced by jsh-049a.",
    )
    real_history_embedding.add_argument(
        "--case-selector",
        choices=("embedding_recall", "all_reviewed"),
        default="embedding_recall",
        help="Reviewed real-history case subset to evaluate.",
    )
    real_history_embedding.add_argument(
        "--backend",
        choices=("auto", "fastembed", "deterministic"),
        default="deterministic",
        help="Embedding backend to try. Defaults to deterministic for harness proof.",
    )
    real_history_embedding.add_argument("--fastembed-model", default=FASTEMBED_MODEL_ID)
    real_history_embedding.add_argument("--fastembed-batch-size", type=int, default=32)
    real_history_embedding.add_argument("--allow-model-download", action="store_true")
    real_history_embedding.add_argument("--fastembed-cache-dir", default=None)
    real_history_embedding.add_argument(
        "--baseline-mode",
        choices=("correctness", "skip"),
        default="correctness",
        help="Use correctness for recall comparison; skip is resource-only and remains inconclusive.",
    )
    real_history_embedding.add_argument("--case-limit", type=int, default=9)
    real_history_embedding.add_argument("--top-k", type=int, default=10)
    real_history_embedding.add_argument("--max-index-disk", type=int, default=2 * 1024 * 1024 * 1024)
    real_history_embedding.add_argument("--shard-size", type=int, default=1000)
    real_history_embedding.add_argument("--chunk-size-chars", type=int, default=0)
    real_history_embedding.add_argument("--chunk-overlap-chars", type=int, default=0)
    real_history_embedding.add_argument(
        "--output-dir",
        default=None,
        help="Ignored private output directory for vectors/indexes; must be under .private/.",
    )
    real_history_embedding.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    real_history_embedding.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    real_history_embedding.add_argument("--profile-memory", action="store_true")
    real_history_embedding.add_argument("--enable-onnx-profiling", action="store_true")
    real_history_miss_audit = subparsers.add_parser("audit-real-history-package-misses")
    real_history_miss_audit.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    real_history_miss_audit.add_argument("--cwd", default=".")
    real_history_miss_audit.add_argument("--timezone", default=None)
    real_history_miss_audit.add_argument("--timezone-offset", default="+00:00")
    real_history_miss_audit.add_argument("--now", default=None)
    real_history_miss_audit.add_argument(
        "--manifest",
        default=str(DEFAULT_REAL_HISTORY_MISS_AUDIT_MANIFEST),
        help="Sanitized reviewed real-history gold manifest.",
    )
    real_history_miss_audit.add_argument(
        "--private-bridge",
        default=str(DEFAULT_REAL_HISTORY_MISS_AUDIT_PRIVATE_BRIDGE),
        help="Ignored private bridge JSON produced by jsh-049a.",
    )
    real_history_miss_audit.add_argument(
        "--raw-run",
        default=str(DEFAULT_REAL_HISTORY_MISS_AUDIT_RAW_RUN),
        help="Ignored raw jsh-049b benchmark run JSON to audit.",
    )
    real_history_miss_audit.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    real_history_miss_audit.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    remaining_miss_diagnostic = subparsers.add_parser("diagnose-real-history-remaining-misses")
    remaining_miss_diagnostic.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    remaining_miss_diagnostic.add_argument(
        "--benchmark-report",
        default=str(DEFAULT_REAL_HISTORY_REMAINING_MISS_BENCHMARK_REPORT),
        help="Committed sanitized JSH-700 benchmark report JSON.",
    )
    remaining_miss_diagnostic.add_argument(
        "--miss-audit-report",
        default=str(DEFAULT_REAL_HISTORY_REMAINING_MISS_AUDIT_REPORT),
        help="Committed sanitized JSH-700 miss-audit report JSON.",
    )
    remaining_miss_diagnostic.add_argument(
        "--instrumented-miss-audit-report",
        default=None,
        help="Optional sanitized miss-audit report from an instrumented package-search rerun.",
    )
    remaining_miss_diagnostic.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    remaining_miss_diagnostic.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    opened_progress_diagnostic = subparsers.add_parser("diagnose-real-history-opened-file-progress")
    opened_progress_diagnostic.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    opened_progress_diagnostic.add_argument(
        "--jsh706-benchmark-report",
        default=str(DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH706_BENCHMARK),
        help="Committed sanitized JSH-706 benchmark report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh706-miss-audit-report",
        default=str(DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH706_AUDIT),
        help="Committed sanitized JSH-706 miss-audit report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh707-benchmark-report",
        default=str(DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH707_BENCHMARK),
        help="Committed sanitized JSH-707 failed-attempt benchmark report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh707-miss-audit-report",
        default=str(DEFAULT_REAL_HISTORY_OPENED_PROGRESS_JSH707_AUDIT),
        help="Committed sanitized JSH-707 failed-attempt miss-audit report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh706-raw-run",
        default=None,
        help="Optional ignored raw JSH-706 run JSON used only for safe counter fields.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh707-raw-run",
        default=None,
        help="Optional ignored raw JSH-707 failed-attempt run JSON used only for safe probe counters.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh709-benchmark-report",
        default=None,
        help="Optional committed sanitized JSH-709 benchmark report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh709-miss-audit-report",
        default=None,
        help="Optional committed sanitized JSH-709 miss-audit report JSON.",
    )
    opened_progress_diagnostic.add_argument(
        "--jsh709-raw-run",
        default=None,
        help="Optional ignored raw JSH-709 run JSON used only for safe opt-in counter fields.",
    )
    opened_progress_diagnostic.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown report should be written.",
    )
    opened_progress_diagnostic.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    benchmark_drift = subparsers.add_parser("compare-real-history-benchmark-drift")
    benchmark_drift.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    benchmark_drift.add_argument(
        "--before",
        default=str(DEFAULT_REAL_HISTORY_DRIFT_BEFORE_REPORT),
        help="Earlier committed-safe real-history impact benchmark JSON artifact.",
    )
    benchmark_drift.add_argument(
        "--after",
        default=str(DEFAULT_REAL_HISTORY_DRIFT_AFTER_REPORT),
        help="Later committed-safe real-history impact benchmark JSON artifact.",
    )
    benchmark_drift.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized JSON/Markdown comparator report should be written.",
    )
    benchmark_drift.add_argument(
        "--markdown-report-output",
        default=None,
        help="Optional path for the sanitized Markdown companion report.",
    )
    post_jsh763_date_timeline = subparsers.add_parser("diagnose-post-jsh763-date-timeline-misses")
    post_jsh763_date_timeline.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    post_jsh763_date_timeline.add_argument(
        "--benchmark",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_BENCHMARK),
        help="Committed JSH-763 real-history benchmark JSON artifact.",
    )
    post_jsh763_date_timeline.add_argument(
        "--raw-run",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_RAW_RUN),
        help="Ignored JSH-763 raw run JSON used only locally for coarse depth/query labels.",
    )
    post_jsh763_date_timeline.add_argument(
        "--jsh711-report",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_JSH711_REPORT),
        help="Committed JSH-711 prior negative-learning report.",
    )
    post_jsh763_date_timeline.add_argument(
        "--jsh721-report",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_JSH721_REPORT),
        help="Committed JSH-721 timestamp-window decision report.",
    )
    post_jsh763_date_timeline.add_argument(
        "--jsh722-report",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_JSH722_REPORT),
        help="Committed JSH-722 auditable timestamp-window decision report.",
    )
    post_jsh763_date_timeline.add_argument(
        "--report-output",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_OUTPUT),
        help="Path where the sanitized JSON report should be written.",
    )
    post_jsh763_date_timeline.add_argument(
        "--markdown-report-output",
        default=str(DEFAULT_POST_JSH763_DATE_TIMELINE_MARKDOWN_OUTPUT),
        help="Path for the sanitized Markdown companion report.",
    )
    live_protocol = subparsers.add_parser("benchmark-live-terminal-protocol")
    live_protocol.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    live_protocol.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized live-terminal protocol report should be written.",
    )
    paired_protocol = subparsers.add_parser("benchmark-paired-agent-protocol")
    paired_protocol.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    paired_protocol.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized paired-agent benchmark protocol report should be written.",
    )
    agent_loop_contract = subparsers.add_parser("benchmark-agent-loop-contract")
    agent_loop_contract.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    agent_loop_contract.add_argument(
        "--case-selection",
        default=str(DEFAULT_AGENT_LOOP_CASE_SELECTION_PATH),
        help="Committed sanitized JSH-724 held-out case selection ledger.",
    )
    agent_loop_contract.add_argument(
        "--output",
        default=None,
        help=f"Optional JSON artifact path; default for committed artifact is {DEFAULT_AGENT_LOOP_CONTRACT_OUTPUT_PATH}.",
    )
    agent_loop_contract.add_argument(
        "--report-output",
        default=None,
        help="Optional rendered report path for the no-send route/counter contract.",
    )
    retained_trace_counters = subparsers.add_parser("extract-retained-trace-counters")
    retained_trace_counters.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    retained_trace_counters.add_argument(
        "--trace-root",
        required=True,
        help="Local retained row trace directory. The path is read offline and is not written to the packet.",
    )
    retained_trace_counters.add_argument(
        "--jsh747-packet",
        default=None,
        help="Optional sanitized JSH-747 packet path used for reviewed source-ref/cost labels.",
    )
    retained_trace_counters.add_argument(
        "--output",
        default=None,
        help=f"Optional JSON artifact path; default for committed artifact is {DEFAULT_RETAINED_TRACE_COUNTER_OUTPUT_PATH}.",
    )
    retained_trace_counters.add_argument(
        "--report-output",
        default=None,
        help=f"Optional rendered report path; default for committed report is {DEFAULT_RETAINED_TRACE_COUNTER_REPORT_PATH}.",
    )
    receiving_agent_ledger = subparsers.add_parser("receiving-agent-ledger")
    receiving_agent_ledger.add_argument("--format", choices=("json", "markdown"), default="json")
    receiving_agent_ledger.add_argument(
        "--lane",
        action="append",
        required=True,
        help="Retained private lane as id=path. Path may be a lane directory or events.jsonl file; may be repeated.",
    )
    receiving_agent_ledger.add_argument(
        "--report-output",
        default=None,
        help=(
            "Optional committed-safe report path. Defaults to "
            f"{DEFAULT_RECEIVING_AGENT_LEDGER_JSON} for JSON and {DEFAULT_RECEIVING_AGENT_LEDGER_MD} for Markdown."
        ),
    )
    harness_artifacts = subparsers.add_parser("validate-harness-artifacts")
    harness_artifacts.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    harness_artifacts.add_argument(
        "--schema",
        default=str(DEFAULT_HARNESS_SCHEMA_PATH),
        help="Committed-safe harness artifact schema packet to validate.",
    )
    harness_artifacts.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Additional committed-safe harness artifact JSON file to validate; may be repeated.",
    )
    harness_artifacts.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized harness artifact validation report should be written.",
    )
    dry_run_artifacts = subparsers.add_parser("build-harness-dry-run-artifacts")
    dry_run_artifacts.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    dry_run_artifacts.add_argument(
        "--case-id",
        default=DEFAULT_DRY_RUN_CASE_ID,
        help="Reviewed case id to build; jsh-080c is scoped to the frozen one-case target.",
    )
    dry_run_artifacts.add_argument(
        "--output-dir",
        required=True,
        help="Directory where committed-safe dry-run artifact JSON files are written.",
    )
    dry_run_artifacts.add_argument(
        "--report-output",
        default=None,
        help="Optional Markdown report path for the dry-run builder handoff.",
    )
    offline_scorer_artifacts = subparsers.add_parser("build-harness-offline-scorer-artifacts")
    offline_scorer_artifacts.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    offline_scorer_artifacts.add_argument(
        "--output-dir",
        required=True,
        help="Directory where committed-safe offline scorer artifact JSON files are written.",
    )
    offline_scorer_artifacts.add_argument(
        "--report-output",
        default=None,
        help="Optional Markdown report path for the offline scorer handoff.",
    )
    inspect_adapter_export = subparsers.add_parser("export-inspect-adapter-proof")
    inspect_adapter_export.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    inspect_adapter_export.add_argument(
        "--adjudication",
        default=str(DEFAULT_X12G_ADJUDICATION_PATH),
        help="Reviewed committed-safe adjudication JSON to project into the Inspect-compatible shape.",
    )
    inspect_adapter_export.add_argument(
        "--sequence",
        type=int,
        default=3,
        help="Reviewed row sequence to project.",
    )
    inspect_adapter_export.add_argument(
        "--output",
        required=True,
        help="Path where the committed-safe Inspect-compatible proof JSON is written.",
    )
    minimum_loop = subparsers.add_parser("build-harness-minimum-loop")
    minimum_loop.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    minimum_loop.add_argument(
        "--case-id",
        default=DEFAULT_MINIMUM_LOOP_CASE_ID,
        help="Reviewed case id for the no-send readiness loop.",
    )
    minimum_loop.add_argument(
        "--output-dir",
        required=True,
        help="Directory where committed-safe loop artifacts are written.",
    )
    minimum_loop.add_argument(
        "--report-output",
        default=None,
        help="Optional Markdown report path for the user-facing readiness loop.",
    )
    real_run_gate = subparsers.add_parser("build-harness-real-run-gate")
    real_run_gate.add_argument("--format", choices=("text", "json", "markdown"), default="markdown")
    real_run_gate.add_argument(
        "--summary",
        default=str(DEFAULT_JSH605_SUMMARY_PATH),
        help="Verified JSH-605 minimum-loop summary JSON.",
    )
    real_run_gate.add_argument(
        "--review",
        default=str(DEFAULT_JSH605_REVIEW_PATH),
        help="Verified JSH-605 review artifact.",
    )
    real_run_gate.add_argument(
        "--output-dir",
        required=True,
        help="Directory where the committed-safe real-run gate summary is written.",
    )
    real_run_gate.add_argument(
        "--report-output",
        default=None,
        help="Optional Markdown report path for the real-run gate.",
    )
    agent_use_value_gate = subparsers.add_parser("validate-agent-use-value-gate")
    agent_use_value_gate.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    agent_use_value_gate.add_argument(
        "--manifest",
        default=str(DEFAULT_JSH1043_MANIFEST_PATH),
        help="Committed-safe JSH-1043 agent-use value gate manifest to validate.",
    )
    reduced_agent_use_value_gate = subparsers.add_parser("validate-agent-use-value-reduced-run")
    reduced_agent_use_value_gate.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    reduced_agent_use_value_gate.add_argument(
        "--summary",
        default=str(DEFAULT_JSH1043_REDUCED_RUN_SUMMARY_PATH),
        help="Committed-safe JSH-1043 reduced run summary to validate.",
    )
    reduced_agent_use_value_gate.add_argument(
        "--require-raw-files",
        action="store_true",
        help="Also verify that private raw artifact paths exist and JSON artifacts parse.",
    )
    reduced_agent_use_value_gate.add_argument(
        "--private-root",
        default=".",
        help="Root used to resolve .private/jsh-1043 paths when --require-raw-files is set.",
    )
    expansion_agent_use_value_gate = subparsers.add_parser("validate-agent-use-value-expansion")
    expansion_agent_use_value_gate.add_argument("--format", choices=("text", "json", "markdown"), default="json")
    expansion_agent_use_value_gate.add_argument(
        "--summary",
        default=str(DEFAULT_JSH1045_EXPANSION_SUMMARY_PATH),
        help="Committed-safe JSH-1045 expansion summary to validate.",
    )
    expansion_agent_use_value_gate.add_argument(
        "--require-raw-files",
        action="store_true",
        help="Also verify that private raw artifact paths exist and JSON artifacts parse.",
    )
    expansion_agent_use_value_gate.add_argument(
        "--private-root",
        default=".",
        help="Root used to resolve .private/jsh-1045 paths when --require-raw-files is set.",
    )
    embedding_experiment = subparsers.add_parser("embedding-experiment")
    add_common_args(embedding_experiment)
    embedding_experiment.add_argument(
        "--experiment-mode",
        choices=("selected_roots", "date_window_subset", "full_approved_local_history", "smoke_synthetic"),
        default="selected_roots",
        help="Opt-in embedding experiment scope. Full history requires --approve-full-history.",
    )
    embedding_experiment.add_argument(
        "--backend",
        choices=("auto", "fastembed", "deterministic"),
        default="auto",
        help="Embedding backend to try. auto uses local fastembed when ready, otherwise deterministic fallback.",
    )
    embedding_experiment.add_argument("--fastembed-model", default=FASTEMBED_MODEL_ID)
    embedding_experiment.add_argument(
        "--fastembed-batch-size",
        type=int,
        default=32,
        help="Maximum number of document texts passed to FastEmbed per local embed call.",
    )
    embedding_experiment.add_argument(
        "--allow-model-download",
        action="store_true",
        help="Allow the local FastEmbed library to download public model files into the ignored private cache.",
    )
    embedding_experiment.add_argument(
        "--fastembed-cache-dir",
        default=None,
        help="Ignored private cache directory for public FastEmbed model files; must be under .private/.",
    )
    embedding_experiment.add_argument("--approve-full-history", action="store_true")
    embedding_experiment.add_argument("--window-start", default=None)
    embedding_experiment.add_argument("--window-end", default=None)
    embedding_experiment.add_argument("--max-records", type=int, default=200)
    embedding_experiment.add_argument(
        "--baseline-mode",
        choices=("correctness", "skip"),
        default="correctness",
        help=(
            "Whether to materialize deterministic baseline documents for correctness comparison. "
            "Use skip only for explicit resource/scale measurements."
        ),
    )
    embedding_experiment.add_argument(
        "--shard-size",
        type=int,
        default=1000,
        help="Maximum number of embedding vectors per private binary shard.",
    )
    embedding_experiment.add_argument(
        "--chunk-size-chars",
        type=int,
        default=0,
        help="Optional per-record character chunk size before embedding. Zero disables chunking.",
    )
    embedding_experiment.add_argument(
        "--chunk-overlap-chars",
        type=int,
        default=0,
        help="Optional character overlap between chunks when --chunk-size-chars is enabled.",
    )
    embedding_experiment.add_argument("--top-k", type=int, default=10)
    embedding_experiment.add_argument("--max-index-disk", type=int, default=2 * 1024 * 1024 * 1024)
    embedding_experiment.add_argument("--case-limit", type=int, default=10)
    embedding_experiment.add_argument(
        "--query",
        action="append",
        default=[],
        dest="queries",
        help="Optional explicit probe query. Query text is used locally but not echoed in output.",
    )
    embedding_experiment.add_argument(
        "--fixture",
        default=str(DEFAULT_EMBEDDING_SPIKE_FIXTURE),
        help="Synthetic sanitized embedding fixture used for default probes or smoke_synthetic mode.",
    )
    embedding_experiment.add_argument(
        "--output-dir",
        default=None,
        help="Ignored private output directory for vectors/indexes; must be under .private/.",
    )
    embedding_experiment.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized aggregate report should be written.",
    )
    embedding_experiment.add_argument(
        "--profile-memory",
        action="store_true",
        help="Record committed-safe process RSS/max-RSS samples across embedding experiment phases.",
    )
    embedding_experiment.add_argument(
        "--enable-onnx-profiling",
        action="store_true",
        help="Request ONNX Runtime profiling when safely exposed by the selected local backend.",
    )
    archive_proof = subparsers.add_parser("archive-snapshot-proof")
    add_common_args(archive_proof)
    archive_proof.add_argument(
        "--archive-output-dir",
        required=True,
        help="Explicit local directory for synthetic archive proof manifest, ledger, and objects.",
    )
    archive_proof.add_argument(
        "--export-output-dir",
        required=True,
        help="Explicit local directory for synthetic export/restore smoke output.",
    )
    archive_proof.add_argument(
        "--include-status-probes",
        action="store_true",
        help="Write isolated synthetic status probes under the explicit archive output dir.",
    )
    archive_proof.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized archive proof report should be written.",
    )
    diagnose = subparsers.add_parser("diagnose-capabilities")
    diagnose.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    diagnose.add_argument("--cwd", default=".")
    diagnose.add_argument("--codex-home", default=None)
    diagnose.add_argument("--claude-home", default=None)
    diagnose.add_argument("--root", action="append", default=[])
    diagnose.add_argument("--max-files", type=int, default=200)
    diagnose.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    diagnose.add_argument("--max-elapsed", type=float, default=15.0)
    diagnose.add_argument("--allow-arbitrary-root", action="store_true")
    diagnose.add_argument("--hard-cap-files", type=int, default=None)
    diagnose.add_argument("--hard-cap-bytes", type=int, default=None)
    diagnose.add_argument(
        "--fixture",
        default="benchmarks/retrieval_gold/sanitized_cases.json",
        help="Sanitized benchmark fixture to check for diagnostic readiness.",
    )
    diagnose.add_argument(
        "--report-output",
        default=None,
        help="Optional path where a machine-readable diagnostic JSON report should be written.",
    )
    discovery = subparsers.add_parser("discover-jsonl")
    discovery.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    discovery.add_argument("--cwd", default=".")
    discovery.add_argument("--codex-home", default=None)
    discovery.add_argument("--claude-home", default=None)
    discovery.add_argument("--root", action="append", default=[])
    discovery.add_argument("--max-files", type=int, default=200)
    discovery.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    discovery.add_argument("--max-elapsed", type=float, default=15.0)
    discovery.add_argument("--allow-arbitrary-root", action="store_true")
    discovery.add_argument("--hard-cap-files", type=int, default=None)
    discovery.add_argument("--hard-cap-bytes", type=int, default=None)
    discovery.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the sanitized candidate discovery report should be written.",
    )
    retained_source = subparsers.add_parser(
        "retained-source-read",
        description=(
            "Open a bounded raw source-session candidate under cwd/.private from an exact "
            "same-task private: handle. Unopened or unavailable handles remain leads only."
        ),
    )
    retained_source.add_argument("handle", help="Exact private:<same-task>/... retained evidence handle.")
    retained_source.add_argument("--task", required=True, help="Named JSH task that the retained handle must match.")
    retained_source.add_argument("--cwd", default=".")
    retained_source.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    retained_source.add_argument("--max-files", type=int, default=8)
    retained_source.add_argument("--max-bytes", type=int, default=256 * 1024)
    install_skill = subparsers.add_parser(
        "install-codex-skill",
        description=(
            "Install the optional Codex skill/helper files. This command does not "
            "install the Python package or copy conversation logs."
        ),
    )
    install_skill.add_argument(
        "--codex-home",
        default=None,
        help="Codex home to update; defaults to CODEX_HOME or ~/.codex.",
    )
    install_skill.add_argument("--format", choices=("text", "json"), default="text")
    install_agent = subparsers.add_parser(
        "install-agent-skill",
        description=(
            "Install optional Codex or Claude skill/helper files. This command does not "
            "install the Python package or copy conversation logs."
        ),
    )
    install_agent.add_argument("--agent", choices=SUPPORTED_AGENTS, required=True)
    install_agent.add_argument(
        "--agent-home",
        default=None,
        help="Agent home to update; defaults to CODEX_HOME/CLAUDE_HOME or ~/.codex/~/.claude.",
    )
    install_agent.add_argument("--format", choices=("text", "json"), default="text")
    validate = subparsers.add_parser("validate-install")
    validate.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    validate.add_argument(
        "--package-root",
        default=None,
        help="Package checkout root to validate; defaults to the installed/source package root.",
    )
    validate.add_argument(
        "--codex-home",
        default=None,
        help="Optional Codex home whose global jsonl-history-search skill install should be checked read-only.",
    )
    validate.add_argument(
        "--claude-home",
        default=None,
        help="Optional Claude home whose personal jsonl-history-search skill install should be checked read-only.",
    )
    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--claude-home", default=None)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--role", default=None)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    parser.add_argument("--max-matches", type=int, default=40)
    parser.add_argument("--max-elapsed", type=float, default=15.0)
    parser.add_argument(
        "--bm25-max-candidate-rows",
        type=int,
        default=5000,
        help="Fail-closed row cap for explicit fielded-bm25/RRF SQLite FTS query materialization.",
    )
    parser.add_argument(
        "--bm25-query-timeout",
        type=float,
        default=None,
        help="Optional seconds budget for explicit fielded-bm25/RRF FTS query/materialization; defaults to --max-elapsed.",
    )
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--timezone-offset", default="+00:00")
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--since",
        default=None,
        help="Inclusive ISO-8601 start timestamp for an explicit record search window; requires --until.",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="Exclusive ISO-8601 end timestamp for an explicit record search window; requires --since.",
    )
    parser.add_argument(
        "--allow-arbitrary-root",
        action="store_true",
        help=(
            "Permit an explicit non-standard JSONL/JSON root. Requires both "
            "--hard-cap-files and --hard-cap-bytes."
        ),
    )
    parser.add_argument(
        "--hard-cap-files",
        type=int,
        default=None,
        help="Required with --allow-arbitrary-root; absolute file cap for arbitrary roots.",
    )
    parser.add_argument(
        "--hard-cap-bytes",
        type=int,
        default=None,
        help="Required with --allow-arbitrary-root; absolute byte cap for arbitrary roots.",
    )
    parser.add_argument(
        "--rg-prefilter",
        action="store_true",
        help="Optionally prefilter already-planned JSONL files with ripgrep before Python JSON parsing.",
    )
    parser.add_argument(
        "--fielded-bm25-index-dir",
        default=None,
        help=(
            "Compatibility alias for the local durable SQLite FTS5 BM25 index directory used by "
            "explicit fielded-bm25 and rrf-fusion retrieval. When omitted on CLI BM25-backed runs, "
            "a default user cache directory is used unless --fielded-bm25-ephemeral is set."
        ),
    )
    parser.add_argument(
        "--fielded-bm25-storage",
        choices=("file", "memory"),
        default="file",
        help=(
            "Storage backend for the BM25 side of explicit fielded-bm25 and rrf-fusion retrieval. "
            "Default file uses a durable SQLite FTS5 index; memory keeps the older ephemeral behavior."
        ),
    )
    parser.add_argument(
        "--rrf-fusion-storage",
        choices=("file", "memory"),
        default="file",
        help=(
            "Storage backend for explicit rrf-fusion candidate materialization. Default file writes or "
            "reuses a sanitized bounded materialization under the BM25 cache/index root; memory is an "
            "explicit small-probe opt-out."
        ),
    )
    parser.add_argument(
        "--fielded-bm25-ephemeral",
        action="store_true",
        help="Compatibility alias for --fielded-bm25-storage memory.",
    )
    parser.add_argument(
        "--mask-manifest",
        default=None,
        help="False-positive masking manifest for future copied-context search snippets",
    )
    parser.add_argument("--mask-mode", choices=sorted(MASK_MODES), default="production_collapse")
    parser.add_argument(
        "--redaction-profile",
        default=None,
        help="JSON profile for read-only snippet redaction of common PII/secrets and custom regexes",
    )


def _json_object_argument(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("expected a JSON object") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return parsed


def _history_home_from_args(
    args: argparse.Namespace,
    *,
    provider: str,
) -> Path:
    return history_home_from_args(args, provider=provider)


def payload_from_args(args: argparse.Namespace) -> tuple[dict, object, object]:
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = normalize_local_path(_history_home_from_args(args, provider="codex"))
    claude_home = normalize_local_path(_history_home_from_args(args, provider="claude"))
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
        bm25_max_candidate_rows=args.bm25_max_candidate_rows,
        bm25_query_timeout=args.bm25_query_timeout,
    )
    explicit_window_start, explicit_window_end = _explicit_search_window_from_args(args, tz)
    intent = infer_query(
        args.query,
        now=now,
        role=args.role,
        date_window_start=explicit_window_start,
        date_window_end=explicit_window_end,
        date_reason="explicit_cli_window" if explicit_window_start is not None else None,
    )
    current_session_only = (
        getattr(args, "command", None) == "semantic-wrapup"
        and not args.root
        and current_session_wording(args.query)
    )
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path(item).expanduser() for item in args.root] if args.root else None,
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
        current_session_only=current_session_only,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "now": now.isoformat(),
    }
    if args.mask_manifest:
        source_plan["masking"] = {
            "manifest_path": str(Path(args.mask_manifest).expanduser()) if args.mask_manifest else None,
            "mode": args.mask_mode,
            "scope": "future_copied_context_snippets_only",
            "canonical_transcripts_mutated": False,
        }
    if args.redaction_profile:
        redaction_path = Path(args.redaction_profile).expanduser()
        source_plan["redaction"] = _redaction_source_metadata(
            redaction_path,
            RedactionContext.from_path(redaction_path),
        )
    return source_plan, intent, plan


def _explicit_search_window_from_args(
    args: argparse.Namespace,
    default_tz,
) -> tuple[datetime | None, datetime | None]:
    since_raw = getattr(args, "since", None)
    until_raw = getattr(args, "until", None)
    if bool(since_raw) != bool(until_raw):
        raise SystemExit("--since and --until must be provided together")
    if not since_raw:
        return None, None
    start = parse_datetime(since_raw, default_tz)
    end = parse_datetime(until_raw, default_tz)
    if start is None or end is None:
        raise SystemExit("--since and --until must be valid ISO-8601 timestamps")
    if end <= start:
        raise SystemExit("--until must be later than --since")
    return start, end


def current_session_wording(query: str) -> bool:
    normalized = " ".join(str(query or "").casefold().split())
    return bool(
        re.search(
            r"\b(this|current|active|latest)\s+"
            r"(?:(?:work|working|chat|conversation|agent|codex|claude|current)\s+)?"
            r"(session|chat|thread|conversation|run)\b",
            normalized,
        )
        or re.search(r"\b(this|current)\s+conversation\b", normalized)
    )


def _redaction_source_metadata(path: Path, context: RedactionContext) -> dict:
    profiles = [name for name in context.stats.profiles if not name.startswith("custom:")]
    custom_count = sum(1 for name in context.stats.profiles if name.startswith("custom:"))
    return {
        "profile_path": str(path),
        "mode": context.stats.mode,
        "profiles": sorted(set(profiles)),
        "custom_count": custom_count,
        "warnings": list(context.stats.warnings),
        "scope": "future_copied_context_snippets_only",
        "canonical_transcripts_mutated": False,
    }


def _mask_context_from_args(args: argparse.Namespace) -> MaskContext | None:
    if not args.mask_manifest and not args.redaction_profile:
        return None
    mask_context = (
        MaskContext.from_path(Path(args.mask_manifest).expanduser(), args.mask_mode)
        if args.mask_manifest
        else MaskContext.disabled(mode=args.mask_mode)
    )
    if args.redaction_profile:
        redaction_context = RedactionContext.from_path(Path(args.redaction_profile).expanduser())
        mask_context.redaction_context = redaction_context
        if mask_context.stats is not None:
            mask_context.stats.redaction = redaction_context.stats.to_dict()
            for warning in redaction_context.stats.warnings:
                if warning not in mask_context.stats.warnings:
                    mask_context.stats.warnings.append(warning)
    return mask_context


def _configuration_warnings(source_plan: dict) -> list[str]:
    redaction = source_plan.get("redaction")
    if not isinstance(redaction, dict):
        return []
    warnings = redaction.get("warnings")
    return list(warnings) if isinstance(warnings, list) else []


def _render_for_report_output(payload: dict, report_output: str, requested_format: str) -> str:
    report_path = Path(report_output).expanduser()
    suffix = report_path.suffix.lower()
    if suffix == ".json":
        return render(payload, "json")
    if suffix in {".md", ".markdown"}:
        return render(payload, "markdown")
    return render(payload, requested_format)


def _attach_retrieval_output_report_diagnostics(
    payload: dict,
    *,
    report_key: str,
    phase: str,
    profile: dict,
    output_format: str,
    report_output: str | None,
    report_written: bool,
) -> None:
    report = payload.get(report_key)
    if not isinstance(report, dict):
        return
    diagnostics = report.get("retrieval_diagnostics")
    if not isinstance(diagnostics, dict):
        return
    if diagnostics.get("retrieval_mode") != RETRIEVAL_MODE_RRF_FUSION:
        return
    elapsed_ms = float(profile.get("elapsed_ms") or 0.0)
    output_report = {
        "phase": phase,
        "elapsed_ms": elapsed_ms,
        "elapsed_label": elapsed_label(elapsed_ms),
        "profile": profile,
        "output_format": output_format,
        "report_written": bool(report_written),
        "report_output_supplied": report_output is not None,
        "report_output_path_included": False,
        "report_output_path_category": "user_supplied_report_output" if report_output else "none",
        "raw_paths_included": False,
        "raw_text_included": False,
        "raw_query_text_included": False,
        "source_lines_included": False,
        "vectors_or_persisted_indexes_included": False,
    }
    diagnostics["output_report"] = output_report
    phase_profiles = diagnostics.setdefault("phase_profiles", {})
    if isinstance(phase_profiles, dict):
        phase_profiles[phase] = profile


def _run_ask_execute(args: argparse.Namespace, *, cwd: str, route_payload: dict) -> dict:
    route = route_payload["ask_router"]
    execution_kind = execution_kind_for_route_family(route.get("route_family", ""))
    selected_route = {
        "chosen_route": route.get("chosen_route"),
        "route_family": route.get("route_family"),
        "evidence_source": route.get("evidence_source"),
        "evidence_source_applies": route.get("evidence_source_applies"),
        "first_action_category": route.get("first_action_category"),
        "why_it_applies": route.get("why_it_applies"),
        "audit_depth": route.get("audit_depth"),
        "structured_execution_query": route.get("structured_execution_query"),
        "command_resolution": route.get("command_resolution"),
        "first_command": route.get("first_command"),
        "method_selection_packet": route.get("method_selection_packet"),
        "next_commands": route.get("next_commands", []),
        "answer_ready_handoff": route.get("answer_ready_handoff"),
        "optional_deeper_reads": route.get("optional_deeper_reads", []),
        "explicit_source_root_count": len(getattr(args, "root", []) or []),
        "read_specialization": route.get("read_specialization"),
        "inspect_after": route.get("inspect_after", []),
        "stop_guidance": route.get("stop_guidance"),
        "manual_search_guidance": route.get("manual_search_guidance"),
    }
    storage_handoff = route.get("answer_ready_handoff") or {}
    answer_action_contract = {
        "answer_status": "not-ready/needs-more-proof",
        "read": route.get("inspect_after", []),
        "stop": route.get("stop_guidance"),
        "only_continue_if": route.get("manual_search_guidance"),
    }
    if route.get("route_family") == "storage_retention_forensics":
        answer_action_contract = {
            "answer_status": storage_handoff.get("answer_status"),
            "read": route.get("next_commands", []),
            "stop": storage_handoff.get("stop"),
            "only_continue_if": storage_handoff.get("only_continue_if"),
        }
    contract = {
        "ask_execute": {
            "schema_version": "ask_execute_route_fusion.v1",
            "query": args.query,
            "original_prompt_identity": args.original_prompt_identity,
            "cwd": cwd,
            "selected_route": selected_route,
            "execution_status": "unsupported_route",
            "execution": {
                "status": "unsupported_route",
                "route_family": route.get("route_family"),
                "allowlist": [
                    "artifact-currentness",
                    "what-happened-since",
                    "semantic-debug",
                    "no-evidence-report",
                    "repo-source-no-evidence",
                    "devlog-failure-analysis",
                    "coordinator-context",
                    "semantic-wrapup",
                ],
                "command_family": None,
                "argv": [],
                "report_written": None,
                "safety": "router first_command strings are not shell-executed; only structured allow-listed JSONL routes run.",
            },
            "executed_output": None,
            "answer_action_contract": answer_action_contract,
        }
    }
    if execution_kind is None:
        contract["ask_execute"]["execution"]["reason"] = (
            "Selected route is outside ask --execute initial allow-list; run the normal first_command manually if intended."
        )
        return contract

    phase_emitter = AskExecutePhaseEmitter() if execution_kind == "semantic-debug" else None

    report_output = _ask_execute_report_output(args, execution_kind)
    execution_args = _execution_args_from_ask(
        args,
        command=execution_kind,
        report_output=report_output,
        query=route.get("structured_execution_query") or args.query,
        audit_depth_override=_selected_route_audit_depth_label(selected_route),
    )
    if route.get("route_family") == "remembered_instruction":
        execution_args.role = "user"
    if execution_kind == "what-happened-since" and _looks_like_parent_main_complete_interval_status_query(
        _normalize_ask_route_text(str(route.get("structured_execution_query") or args.query))
    ):
        execution_args.parent_thread = True
        execution_args.complete_interval = True
    try:
        Path(report_output).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        if _is_worktree_cleanup_causality_execute(args, execution_kind):
            return _run_worktree_cleanup_execute_without_report(
                contract,
                execution_args=execution_args,
                report_output=report_output,
                cwd=Path(cwd),
            )
        return _ask_execute_report_output_unavailable(
            contract,
            execution_kind=execution_kind,
            report_output=report_output,
            fallback_command=selected_route.get("first_command"),
            execution_args=execution_args,
        )

    try:
        if execution_kind == "artifact-currentness":
            executed_payload = _build_artifact_currentness_report_output_payload(execution_args)
            output_key = "artifact_currentness"
        elif execution_kind == "what-happened-since":
            executed_payload = _build_status_anchor_report_output_payload(execution_args)
            output_key = "status_anchor"
        elif execution_kind == "semantic-debug":
            executed_payload = _build_semantic_debug_report_output_payload(
                execution_args,
                phase_emitter=phase_emitter,
            )
            output_key = "semantic_debug"
        elif execution_kind == "no-evidence-report":
            executed_payload = _build_repo_source_no_evidence_report_output_payload(execution_args)
            if executed_payload is not None:
                output_key = "repo_source_no_evidence"
                execution_kind = "repo-source-no-evidence"
            else:
                executed_payload = _build_agent_find_report_output_payload(execution_args)
                output_key = "agent_find"
        elif execution_kind == "coordinator-context":
            executed_payload = _build_coordinator_context_report_output_payload(execution_args)
            output_key = "coordinator_context"
        elif execution_kind == "semantic-wrapup":
            executed_payload = _build_semantic_wrapup_report_output_payload(execution_args)
            output_key = "semantic_wrapup"
        else:
            executed_payload = _build_devlog_failure_analysis_report_output_payload(execution_args)
            output_key = "devlog_failure_analysis"
    except OSError as exc:
        if not _ask_execute_report_output_error(exc, report_output):
            raise
        if _is_worktree_cleanup_causality_execute(args, execution_kind):
            return _run_worktree_cleanup_execute_without_report(
                contract,
                execution_args=execution_args,
                report_output=report_output,
                cwd=Path(cwd),
            )
        return _ask_execute_report_output_unavailable(
            contract,
            execution_kind=execution_kind,
            report_output=report_output,
            fallback_command=selected_route.get("first_command"),
            execution_args=execution_args,
        )

    compact = executed_payload[output_key]
    argv = _structured_execution_argv(execution_args)
    if execution_kind == "repo-source-no-evidence":
        argv = [
            "jsonl-history-search",
            "ask",
            args.query,
            "--execute",
            "--cwd",
            args.cwd,
            "--format",
            "json",
        ]
        _append_structured_source_contract(argv, args)
        argv.extend(["--report-output", report_output])
    contract["ask_execute"]["execution"].update(
        {
            "status": "executed",
            "command_family": execution_kind,
            "argv": argv,
            "report_written": compact.get("report_written"),
            "output_key": output_key,
        }
    )
    contract["ask_execute"]["execution_status"] = "executed"
    contract["ask_execute"]["executed_output"] = _compact_executed_output_for_ask(output_key=output_key, compact=compact)
    action_compact = compact
    if route.get("route_family") == "remembered_instruction":
        action_compact = dict(compact)
        action_compact["_remembered_instruction_report_context"] = (
            _remembered_instruction_context_from_report(report_output)
        )
    contract["ask_execute"]["answer_action_contract"] = _answer_action_contract_from_executed_output(
        output_key=output_key,
        compact=action_compact,
        selected_route=selected_route,
        query=args.query,
        cwd=cwd,
    )
    _finalize_terminal_ask_execute_route(contract)
    if (
        output_key == "artifact_currentness"
        and compact.get("detected_intent") == "worktree_cleanup_currentness"
        and (compact.get("worktree_currentness") or {})
        .get("cleanup_causality", {})
        .get("is_cleanup_causality_query")
        and _is_answer_ready_worktree_cleanup_execute(args, execution_kind)
    ):
        _attach_worktree_cleanup_answer_handoff(
            contract,
            cwd=_artifact_currentness_answer_cwd(compact, Path(cwd)),
        )
    return contract


def _remembered_instruction_context_from_report(report_output: str) -> dict:
    try:
        payload = json.loads(Path(report_output).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    packet = payload.get("semantic_debug") if isinstance(payload, dict) else None
    if not isinstance(packet, dict):
        return {}
    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    candidates = [
        {
            "candidate_id": str(item.get("candidate_id") or ""),
            "timestamp_utc": item.get("timestamp_utc"),
            "role": item.get("role"),
            "snippet": str(item.get("snippet") or ""),
            "source_ref": item.get("source_ref") or {},
        }
        for item in packet.get("strongest_candidates") or []
        if isinstance(item, dict) and item.get("candidate_id")
    ]
    return {
        "candidate_snippets": {
            item["candidate_id"]: item["snippet"] for item in candidates
        },
        "candidates": candidates,
        "searched_scope": (
            scope.get("searched_scope")
            if isinstance(scope.get("searched_scope"), dict)
            else {}
        ),
        "selected_roots": list(scope.get("selected_roots") or []),
        "excluded_root_count": int(
            (scope.get("excluded_scope") or {}).get("rejected_root_count") or 0
        ),
        "missing_roots": list(
            (scope.get("not_searched_scope") or {}).get("missing_roots") or []
        ),
    }


def _is_answer_ready_worktree_cleanup_execute(args: argparse.Namespace, execution_kind: str) -> bool:
    if execution_kind != "artifact-currentness":
        return False

    return _is_answer_ready_worktree_cleanup_query(
        str(getattr(args, "query", "") or "")
    )


def _is_worktree_cleanup_causality_execute(args: argparse.Namespace, execution_kind: str) -> bool:
    if execution_kind != "artifact-currentness":
        return False
    from .worktree_currentness import is_worktree_cleanup_causality_query

    return is_worktree_cleanup_causality_query(str(getattr(args, "query", "") or ""))


def _is_answer_ready_worktree_cleanup_query(query: str) -> bool:
    from .worktree_currentness import is_answer_ready_worktree_cleanup_causality_query

    return is_answer_ready_worktree_cleanup_causality_query(query)


def _run_worktree_cleanup_execute_without_report(
    contract: dict,
    *,
    execution_args: argparse.Namespace,
    report_output: str,
    cwd: Path,
) -> dict:
    """Preserve bounded cleanup routing when only optional report writing fails."""
    from .artifact_currentness import build_artifact_currentness_payload

    compact = build_artifact_currentness_payload(execution_args)["artifact_currentness"]
    compact["report_written"] = None
    ask_execute = contract["ask_execute"]
    ask_execute["execution"].update(
        {
            "status": "executed",
            "command_family": "artifact-currentness",
            "argv": _structured_execution_argv(execution_args),
            "report_written": None,
            "report_output": report_output,
            "report_output_status": "unavailable",
            "output_key": "artifact_currentness",
            "reason": (
                "The optional report destination is not writable; cleanup causality was "
                "finalized from the bounded in-memory packet."
            ),
        }
    )
    ask_execute["execution_status"] = "executed"
    ask_execute["executed_output"] = _compact_executed_output_for_ask(
        output_key="artifact_currentness",
        compact=compact,
    )
    ask_execute["answer_action_contract"] = _answer_action_contract_from_executed_output(
        output_key="artifact_currentness",
        compact=compact,
        selected_route=ask_execute["selected_route"],
        query=str(getattr(execution_args, "query", "") or ""),
        cwd=str(cwd),
    )
    if _is_answer_ready_worktree_cleanup_query(
        str(getattr(execution_args, "query", "") or "")
    ):
        _attach_worktree_cleanup_answer_handoff(
            contract,
            cwd=_artifact_currentness_answer_cwd(compact, cwd),
        )
    return contract


def _artifact_currentness_answer_cwd(compact: dict, requested_cwd: Path) -> Path:
    resolution = compact.get("workspace_resolution") or {}
    if resolution.get("status") != "historical_session_workspace_recovered":
        return requested_cwd
    resolved = Path(str(resolution.get("resolved_cwd") or "")).expanduser()
    return resolved.resolve() if resolved.is_dir() else requested_cwd


def _attach_worktree_cleanup_answer_handoff(contract: dict, *, cwd: Path) -> None:
    """Finalize validated cleanup causality inside the execute handoff."""
    finalized = _ask_answer_from_execute(contract, cwd=cwd).get("ask_answer") or {}
    ask_execute = contract["ask_execute"]
    ask_execute["answer_handoff"] = {
        key: finalized.get(key)
        for key in (
            "schema_version",
            "query",
            "cwd",
            "answer_status",
            "answer",
            "refs_used",
            "proof_summary",
            "fail_closed_reason",
            "next_proof_step",
            "source_contract",
        )
    }
    ask_execute["answer_handoff"]["receiving_agent_command_contract"] = {
        "additional_commands_required": (
            0 if finalized.get("answer_status") == "answered-from-proof" else None
        ),
        "source_validation": "completed_in_process",
        "continue_only_on_fail_closed": True,
    }
    if finalized.get("answer_status") != "answered-from-proof":
        return
    action = ask_execute["answer_action_contract"]
    action["answer_status"] = "answered-from-proof"
    action["promotion_reason"] = (
        "The cleanup route opened and validated the bounded repo-local operational refs; "
        "the receiving agent can use answer_handoff directly without another command."
    )
    action["validated_read"] = action.get("read") or []
    action["read"] = []
    action["stop"] = (
        "Stop after answer_handoff when answer_status is answered-from-proof; no source reread, "
        "artifact-currentness rerun, broad search, or cleanup action is needed."
    )
    action["only_continue_if"] = (
        "Continue only if answer_handoff is absent, fail-closed, or explicitly reports rejected refs."
    )
    handoff_contract = ask_execute["answer_handoff"].get("source_contract") or {}
    handoff_contract["built_from"] = "ask_execute.validated_cleanup_answer_handoff"
    handoff_contract["answer_action_status"] = "answered-from-proof"


def _ask_execute_report_output(args: argparse.Namespace, execution_kind: str) -> str:
    if args.report_output:
        path = Path(args.report_output).expanduser()
    else:
        path = Path(args.cwd).expanduser().resolve() / ".private" / "jsonl" / f"ask-execute-{execution_kind}.json"
    return str(path)


def _ask_execute_report_output_unavailable(
    contract: dict,
    *,
    execution_kind: str,
    report_output: str,
    fallback_command: str | None,
    execution_args: argparse.Namespace | None = None,
) -> dict:
    if execution_kind == "devlog-failure-analysis":
        return _devlog_report_output_unavailable(
            contract,
            execution_kind=execution_kind,
            report_output=report_output,
            execution_args=execution_args,
        )
    if execution_kind == "semantic-debug":
        provenance = _provenance_report_output_unavailable(
            contract,
            execution_kind=execution_kind,
            report_output=report_output,
        )
        if provenance:
            return provenance
        return _semantic_debug_report_output_unavailable(
            contract,
            execution_kind=execution_kind,
            report_output=report_output,
            execution_args=execution_args,
        )
    contract["ask_execute"]["execution"].update(
        {
            "status": "report_output_unavailable",
            "command_family": execution_kind,
            "argv": [],
            "report_output": report_output,
            "report_written": None,
            "fallback_command": fallback_command,
            "reason": (
                "The report destination is not writable. No report handle is available; "
                "run the selected first command directly."
            ),
        }
    )
    contract["ask_execute"]["execution_status"] = "report_output_unavailable"
    contract["ask_execute"]["answer_action_contract"]["promotion_status"] = (
        "route_only_report_output_unavailable"
    )
    return contract


def _semantic_debug_report_output_unavailable(
    contract: dict,
    *,
    execution_kind: str,
    report_output: str,
    execution_args: argparse.Namespace | None,
) -> dict:
    ask_execute = contract["ask_execute"]
    selected_route = ask_execute.get("selected_route") or {}
    if execution_args is None:
        return {}

    compact_payload = _build_semantic_debug_in_memory_payload(execution_args)
    compact = compact_payload["semantic_debug"]
    selected_route["first_command"] = None
    selected_route["next_commands"] = []
    selected_route["manual_search_guidance"] = None
    selected_route["optional_deeper_reads"] = []
    selected_route["route_instruction_status"] = "already_executed_in_memory"
    ask_execute["execution"].update(
        {
            "status": "executed",
            "command_family": execution_kind,
            "argv": [],
            "report_output": report_output,
            "report_written": None,
            "report_output_status": "unavailable",
            "execution_mode": "in_memory",
            "route_instruction_status": "already_executed",
            "output_key": "semantic_debug",
            "reason": (
                "Semantic-debug completed in memory; only optional report persistence was unavailable."
            ),
        }
    )
    ask_execute["execution_status"] = "executed"
    ask_execute["executed_output"] = _compact_executed_output_for_ask(
        output_key="semantic_debug",
        compact=compact,
    )
    ask_execute["answer_action_contract"] = _answer_action_contract_from_executed_output(
        output_key="semantic_debug",
        compact=compact,
        selected_route=selected_route,
        query=str(ask_execute.get("query") or ""),
        cwd=str(ask_execute.get("cwd") or "."),
    )
    return contract


def _provenance_report_output_unavailable(
    contract: dict,
    *,
    execution_kind: str,
    report_output: str,
) -> dict:
    ask_execute = contract["ask_execute"]
    selected_route = ask_execute.get("selected_route") or {}
    query = str(ask_execute.get("query") or "")
    route_family = str(selected_route.get("route_family") or "")
    if not _named_task_merge_provenance_source_session_wanted(query):
        return {}
    handoff = _named_task_merge_provenance_execute_artifact_handoff(
        query=query,
        cwd=str(ask_execute.get("cwd") or "."),
        route_family=route_family,
        cli_prefix=(selected_route.get("command_resolution") or {}).get("command_prefix"),
    )
    if handoff.get("status") != "completed_task_merge_artifact_refs_available_source_session_missing":
        return {}

    retained_actions = [
        item
        for item in handoff.get("retained_source_reads") or []
        if isinstance(item, dict) and item.get("command")
    ]
    bounded_first_command = (
        str(retained_actions[0]["command"])
        if retained_actions
        else None
    )
    selected_route["first_command"] = bounded_first_command
    selected_route["next_commands"] = retained_actions

    ask_execute["execution"].update(
        {
            "status": "report_output_unavailable",
            "command_family": execution_kind,
            "argv": [],
            "report_output": report_output,
            "report_written": None,
            "fallback_command": bounded_first_command,
            "fallback_commands": retained_actions,
            "reason": (
                "The report destination is not writable. Same-task task/review/merge evidence is still "
                "available as partial proof, but no raw source-session evidence was opened by this route."
            ),
        }
    )
    ask_execute["execution_status"] = "report_output_unavailable"
    ask_execute["answer_action_contract"] = {
        "answer_status": "not-ready/missing-source-session-proof",
        "promotion_status": "completed_task_merge_only_partial_source_session_unopened",
        "promotion_reason": (
            "The report-unavailable path preserves validated task/review/true-merge leads as partial "
            "evidence. Source-session proof remains blocked until a role-valid raw/session source is opened."
        ),
        "read": handoff.get("read") or [],
        "named_task_merge_provenance_handoff": handoff,
        "source_inspection_boundary": {
            "decision": "report_merge_only_partial_and_recover_source_session",
            "answer_allowed": False,
            "inspect_required_primary_proof_before_answer": True,
            "required_primary_proof_source": "same_task_opened_raw_source_session",
            "missing_roles": ["source_session_proof"],
            "unopened_private_handles_are_leads_only": True,
            "report_output_unavailable_is_product_defect": False,
        },
        "missing_source_session_stop_contract": {
            "schema_version": 1,
            "contract_kind": "missing-source-session-stop",
            "status": "not-ready/missing-source-session-proof",
            "answer_allowed": False,
            "missing_roles": ["source_session_proof"],
            "bounded_retained_source_actions": retained_actions,
            "generic_semantic_debug_allowed": False,
            "stop": handoff.get("stop"),
            "unblock": (
                "Open a role-valid same-task raw JSONL/session ref through a bounded retained-source action; "
                "task, review, scratch, report, and summary artifacts cannot unblock this facet."
            ),
        },
        "stop": handoff.get("stop"),
        "only_continue_if": handoff.get("only_continue_if"),
        "saved_report": None,
    }
    return contract


def _devlog_report_output_unavailable(
    contract: dict,
    *,
    execution_kind: str,
    report_output: str,
    execution_args: argparse.Namespace | None = None,
) -> dict:
    ask_execute = contract["ask_execute"]
    selected_route = ask_execute.get("selected_route") or {}
    command_resolution = selected_route.get("command_resolution") or {}
    cli_prefix = command_resolution.get("command_prefix") or "jsonl-history-search"
    history_roots = _devlog_history_root_preflight(execution_args)
    if not history_roots["any_primary_history_available"]:
        missing_roles = list(DEVLOG_CHRONOLOGY_ROLE_ORDER)
        stop_condition = (
            "Stop now: no real Codex or Claude primary history root is available. Do not run the "
            "later Git read, repository searches, task/review/report archaeology, or same-loop answer-key "
            "searches. Exact missing roles: first_appearance, worsening_process_cause, later_change. "
            "Keep all chronology roles missing until a real history root is supplied."
        )
        chronology = {
            "status": "fail_closed_primary_history_roots_unavailable",
            "answer_status": "fail_closed_missing_chronology_roles",
            "answer_allowed": False,
            "required_roles": missing_roles,
            "covered_primary_roles": [],
            "missing_roles": missing_roles,
            "primary_history_blocked_roles": ["first_appearance", "worsening_process_cause"],
            "deferred_until_primary_history": ["later_change"],
            "history_root_preflight": history_roots,
            "copyable_next_read_count": 0,
            "stop_condition": stop_condition,
        }
        ask_execute["execution"].update(
            {
                "status": "report_output_unavailable",
                "command_family": execution_kind,
                "argv": [],
                "report_output": report_output,
                "report_written": None,
                "fallback_command": None,
                "fallback_commands": [],
                "reason": (
                    "The report destination is not writable and the configured runtime homes contain no "
                    "real primary history. The route fails closed without executable artifact fallback."
                ),
            }
        )
        ask_execute["execution_status"] = "report_output_unavailable"
        ask_execute["answer_action_contract"] = {
            "answer_status": "fail_closed_missing_chronology_roles",
            "promotion_status": "primary_history_roots_unavailable",
            "promotion_reason": (
                "Isolated runtime homes are not history roots. Supply a real root through "
                "JSONL_HISTORY_CODEX_HOME or JSONL_HISTORY_CLAUDE_HOME, then rerun the front door."
            ),
            "chronology_source_reach": chronology,
            "read": [],
            "stop": stop_condition,
            "only_continue_if": (
                "A real Codex or Claude history root is explicitly supplied; rerun ask --execute rather "
                "than searching repository artifacts."
            ),
            "saved_report": None,
        }
        return contract
    direct_reads = []
    for role in DEVLOG_CHRONOLOGY_ROLE_ORDER:
        direct_reads.extend(
            _devlog_primary_recovery_reads(
                role,
                write_report=False,
                cli_prefix=cli_prefix,
            )[:2]
        )

    stop_condition = (
        "Answer only after primary historical reads cover first appearance, worsening process cause, "
        "and later change. Run the role-bound stdout-only recovery commands before consulting "
        "secondary artifact leads; artifacts do not satisfy a missing primary role."
    )
    fallback_command = direct_reads[0]["command"] if direct_reads else None
    ask_execute["execution"].update(
        {
            "status": "report_output_unavailable",
            "command_family": execution_kind,
            "argv": [],
            "report_output": report_output,
            "report_written": None,
            "fallback_command": fallback_command,
            "fallback_commands": direct_reads,
            "reason": (
                "The report destination is not writable. No report handle is available; "
                "run the direct stdout-only chronology recovery commands below."
            ),
        }
    )
    ask_execute["execution_status"] = "report_output_unavailable"
    ask_execute["answer_action_contract"] = {
        "answer_status": "fail_closed_missing_chronology_roles",
        "promotion_status": "route_only_report_output_unavailable",
        "promotion_reason": (
            "The writable devlog package is unavailable. These direct commands only recover "
            "primary chronology sources; they do not make the answer ready by themselves."
        ),
        "chronology_source_reach": {
            "status": "fail_closed_missing_primary_roles",
            "answer_status": "fail_closed_missing_chronology_roles",
            "answer_allowed": False,
            "required_roles": list(DEVLOG_CHRONOLOGY_ROLE_ORDER),
            "covered_primary_roles": [],
            "missing_roles": list(DEVLOG_CHRONOLOGY_ROLE_ORDER),
            "copyable_next_read_count": len(direct_reads),
            "stop_condition": stop_condition,
        },
        "read": direct_reads,
        "stop": stop_condition,
        "only_continue_if": (
            "A required chronology role remains missing after running the bounded primary-history "
            "recovery commands; fail closed instead of broad repo or report archaeology."
        ),
        "saved_report": None,
    }
    return contract


def _ask_execute_report_output_error(exc: OSError, report_output: str) -> bool:
    if not exc.filename:
        return False

    report_path = _absolute_ask_execute_path(Path(report_output).expanduser())
    failed_path = _absolute_ask_execute_path(Path(exc.filename).expanduser())
    if failed_path in {report_path, report_path.parent}:
        return True
    return failed_path.parent == report_path.parent and failed_path.name.startswith(
        f"{report_path.stem}."
    )


def _absolute_ask_execute_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _execution_args_from_ask(
    args: argparse.Namespace,
    *,
    command: str,
    report_output: str,
    query: str | None = None,
    audit_depth_override: str | None = None,
) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(
        {
            "command": command,
            "query": query if query is not None else args.query,
            "format": "json",
            "report_output": report_output,
            "receiver_answer_mode": bool(getattr(args, "answer", False)),
        }
    )
    if audit_depth_override is not None:
        values["audit_depth"] = audit_depth_override
    if command == "artifact-currentness":
        values["artifact_or_query"] = values["query"]
    return argparse.Namespace(**values)


def _selected_route_audit_depth_label(selected_route: dict) -> str | None:
    audit_depth = selected_route.get("audit_depth") or {}
    if not isinstance(audit_depth, dict):
        return None
    if audit_depth.get("source") == "default" and audit_depth.get("depth") == "quick":
        return None
    label = audit_depth.get("label")
    return str(label) if label else None


def _args_with_bounded_auto_execute_prompt_scope(args: argparse.Namespace) -> argparse.Namespace:
    values = vars(args).copy()
    roots = list(values.get("root") or [])
    extracted_roots = _extract_bounded_auto_execute_roots_from_query(str(values.get("query") or ""))
    changed = False
    if not roots and extracted_roots:
        values["root"] = extracted_roots
        roots = extracted_roots
        changed = True
    if values.get("hard_cap_files") is None:
        hard_cap_files = _extract_bounded_auto_execute_hard_cap(str(values.get("query") or ""), "files")
        if hard_cap_files is not None:
            values["hard_cap_files"] = hard_cap_files
            changed = True
    if values.get("hard_cap_bytes") is None:
        hard_cap_bytes = _extract_bounded_auto_execute_hard_cap(str(values.get("query") or ""), "bytes")
        if hard_cap_bytes is not None:
            values["hard_cap_bytes"] = hard_cap_bytes
            changed = True
    if (
        roots
        and not values.get("allow_arbitrary_root")
        and values.get("hard_cap_files") is not None
        and values.get("hard_cap_bytes") is not None
    ):
        values["allow_arbitrary_root"] = True
        changed = True
    return argparse.Namespace(**values) if changed else args


def _extract_bounded_auto_execute_roots_from_query(query: str) -> list[str]:
    roots: list[str] = []
    for token in _path_like_tokens(query):
        cleaned = token.strip().strip("`'\"()[]{}<>.,:")
        if not _safe_bounded_fixture_root_token(cleaned):
            continue
        if cleaned not in roots:
            roots.append(cleaned)
    return roots[:2]


def _path_like_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"`([^`]+)`|'([^']+)'|\"([^\"]+)\"|(\S+)", query):
        token = next(part for part in raw if part)
        if "/" in token:
            tokens.append(token)
    return tokens


def _safe_bounded_fixture_root_token(token: str) -> bool:
    if not token or len(token) > 240:
        return False
    if any(char in token for char in ("$", "*", "?", "\\", "|", ";", "&", "=", "[", "]", "{", "}", "<", ">")):
        return False
    if "://" in token:
        return False
    path = Path(token)
    if any(part == ".." for part in path.parts):
        return False
    normalized = token.casefold().lstrip("./")
    fixture_prefixes = (
        "tests/fixtures/final_suite_bounded_no_evidence/",
        "fixtures/final_suite_bounded_no_evidence/",
        "final_suite_bounded_no_evidence/",
    )
    return normalized.startswith(fixture_prefixes)


def _extract_bounded_auto_execute_hard_cap(query: str, kind: str) -> int | None:
    patterns = (
        rf"(?:--)?hard[-_\s]?cap[-_\s]?{kind}\s*(?:=|:)?\s*([0-9][0-9_]*)",
        rf"{kind}\s+hard[-_\s]?cap\s*(?:=|:)?\s*([0-9][0-9_]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            value = int(match.group(1).replace("_", ""))
        except ValueError:
            continue
        max_value = 10_000 if kind == "files" else 2_000_000_000
        if 1 <= value <= max_value:
            return value
    return None


def _structured_execution_argv(args: argparse.Namespace) -> list[str]:
    argv = ["jsonl-history-search", args.command, args.query, "--cwd", args.cwd, "--format", "json"]
    _append_structured_source_contract(argv, args)
    if getattr(args, "role", None):
        argv.extend(["--role", str(args.role)])
    if getattr(args, "audit_depth", None):
        argv.extend(["--audit-depth", str(args.audit_depth)])
    if args.command == "what-happened-since":
        if getattr(args, "parent_thread", False):
            argv.append("--parent-thread")
        if getattr(args, "complete_interval", False):
            argv.append("--complete-interval")
        if getattr(args, "anchor_ref", None):
            argv.extend(["--anchor-ref", str(args.anchor_ref)])
    argv.extend(["--report-output", args.report_output])
    return argv


def _append_structured_source_contract(argv: list[str], args: argparse.Namespace) -> None:
    for root in getattr(args, "root", []) or []:
        argv.extend(["--root", root])
    if getattr(args, "allow_arbitrary_root", False):
        argv.append("--allow-arbitrary-root")
    for name, flag in (
        ("codex_home", "--codex-home"),
        ("claude_home", "--claude-home"),
        ("max_files", "--max-files"),
        ("max_bytes", "--max-bytes"),
        ("max_matches", "--max-matches"),
        ("max_elapsed", "--max-elapsed"),
        ("hard_cap_files", "--hard-cap-files"),
        ("hard_cap_bytes", "--hard-cap-bytes"),
        ("since", "--since"),
        ("until", "--until"),
    ):
        value = getattr(args, name, None)
        if value is not None and value != "":
            argv.extend([flag, str(value)])


def _finalize_terminal_ask_execute_route(contract: dict) -> None:
    ask_execute = contract.get("ask_execute") or {}
    action = ask_execute.get("answer_action_contract") or {}
    terminal = action.get("terminal_answer_and_stop_contract") or {}
    if terminal.get("status") != "terminal_answer_and_stop":
        return
    selected_route = ask_execute.get("selected_route") or {}
    selected_route["first_command"] = None
    selected_route["next_commands"] = []
    selected_route["manual_search_guidance"] = None
    selected_route["route_instruction_status"] = "already_executed_terminal"


def _normalize_ask_route_text(text: str) -> str:
    return " ".join(text.lower().replace("?", " ").replace("!", " ").split())


def _ask_execute_sidecar_path(report_output: str, label: str) -> Path:
    path = Path(report_output).expanduser()
    return path.with_name(f"{path.stem}.{label}.json")


def _build_coordinator_context_report_output_payload(args: argparse.Namespace) -> dict:
    compact = build_coordinator_context_payload(
        cwd=args.cwd,
        query=args.query,
        report_output=args.report_output,
    )
    Path(args.report_output).expanduser().write_text(
        render({"coordinator_context": compact}, "json"),
        encoding="utf-8",
    )
    return {"coordinator_context": compact}


def _build_status_anchor_report_output_payload(args: argparse.Namespace) -> dict:
    compact = build_status_anchor_payload(args)["status_anchor"]
    compact["report_written"] = args.report_output
    Path(args.report_output).expanduser().write_text(
        render({"status_anchor": compact}, "json"),
        encoding="utf-8",
    )
    return {"status_anchor": compact}


def _build_artifact_currentness_report_output_payload(args: argparse.Namespace) -> dict:
    from .artifact_currentness import build_artifact_currentness_payload

    payload = build_artifact_currentness_payload(args)
    compact = payload["artifact_currentness"]
    compact["report_written"] = args.report_output
    Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
    return {"artifact_currentness": compact}


def _build_semantic_wrapup_report_output_payload(args: argparse.Namespace) -> dict:
    from .semantic_wrapup import build_semantic_wrapup_payload

    source_plan, _intent, plan = payload_from_args(args)
    payload = build_semantic_wrapup_payload(
        query=args.query,
        source_plan=source_plan,
        plan=plan,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        cwd=Path(args.cwd).expanduser().resolve(),
        mask_context=_mask_context_from_args(args),
        output_format="json",
        report_output=args.report_output,
    )
    compact = payload["semantic_wrapup"]
    compact["report_written"] = args.report_output
    Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
    return {"semantic_wrapup": compact}


def _build_devlog_failure_analysis_report_output_payload(args: argparse.Namespace) -> dict:
    history_roots = _devlog_history_root_preflight(args)
    if not history_roots["any_primary_history_available"]:
        missing_roles = list(DEVLOG_CHRONOLOGY_ROLE_ORDER)
        stop_condition = (
            "Stop now: no real Codex or Claude primary history root is available. Do not run repository "
            "searches, task/review/report archaeology, later Git recovery, or same-loop answer-key searches. "
            "Exact missing roles: first_appearance, worsening_process_cause, later_change."
        )
        chronology_source_reach = {
            "status": "fail_closed_primary_history_roots_unavailable",
            "answer_status": "fail_closed_missing_chronology_roles",
            "answer_allowed": False,
            "required_roles": missing_roles,
            "covered_primary_roles": [],
            "missing_roles": missing_roles,
            "primary_history_blocked_roles": ["first_appearance", "worsening_process_cause"],
            "deferred_until_primary_history": ["later_change"],
            "history_root_preflight": history_roots,
            "primary_role_reads": {role: [] for role in DEVLOG_CHRONOLOGY_ROLE_ORDER},
            "primary_recovery_reads": {},
            "retained_primary_report_handles": [],
            "artifact_role_leads": {role: [] for role in DEVLOG_CHRONOLOGY_ROLE_ORDER},
            "dedicated_case_lead": None,
            "copyable_next_reads": [],
            "stop_condition": stop_condition,
            "claim_boundary": (
                "No primary history was available, so no repository artifact or generated output was "
                "promoted as chronology support."
            ),
        }
        compact = {
            "schema_version": 1,
            "status": "primary_history_roots_unavailable",
            "query": args.query,
            "report_written": str(Path(args.report_output).expanduser()),
            "linked_reports": {},
            "source_read_order": ["chronology_source_reach.copyable_next_reads"],
            "core_convo_first_read_plan": {
                "read": [],
                "diagnostics_after": [],
                "role_groups": {},
            },
            "target_historical_source_window_reads": [],
            "target_historical_source_window_read_count": 0,
            "chronology_source_reach": chronology_source_reach,
            "diagnostic_source_window_reads": [],
            "diagnostic_source_window_read_count": 0,
            "diagnostic_policy": {
                "current_adjacent_same_loop_refs_are_default_final_support": False,
                "actionability": "disabled while primary history roots are unavailable",
            },
            "generic_recovery_policy": {
                "primary_recovery_surface": "chronology_source_reach.copyable_next_reads",
                "broad_repo_missing_role_actions": "disabled",
                "repo_artifact_roots_are_primary_raw_recovery": False,
            },
            "output_contract": {
                "stdout": "ask prints the selected format and is not suppressed by report-output",
                "report_output_format": "json",
                "report_output": str(Path(args.report_output).expanduser()),
                "linked_report_format": None,
                "jq_rule": "The main .json report is the only sidecar for this fail-closed result.",
            },
            "repo_phase_category_evidence": {},
            "failure_analysis": {
                "status": "not_run_primary_history_roots_unavailable",
                "report_written": None,
                "devlog_brief": None,
                "story_phase_summary": None,
            },
            "answer_contract": {
                "answer_status": "fail_closed_missing_chronology_roles",
                "required_read_order": [],
                "only_continue_if": (
                    "A real history root is supplied, then the same ask --execute front door is rerun."
                ),
                "stop_condition": stop_condition,
            },
            "privacy_flags": {
                "raw_private_transcript_text_included": False,
                "raw_private_session_ids_included": False,
                "absolute_private_paths_committed": False,
                "vectors_or_indexes_included": False,
                "repo_artifact_evidence_is_committed_safe_metadata_only": False,
            },
        }
        Path(args.report_output).expanduser().write_text(
            render({"devlog_failure_analysis": compact}, "json"),
            encoding="utf-8",
        )
        return {"devlog_failure_analysis": compact}

    semantic_report = _ask_execute_sidecar_path(args.report_output, "semantic-debug")
    repo_report = _ask_execute_sidecar_path(args.report_output, "repo-artifact-evidence")
    failure_report = _ask_execute_sidecar_path(args.report_output, "failure-analysis")

    semantic_args = _execution_args_from_ask(
        args,
        command="semantic-debug",
        report_output=str(semantic_report),
    )
    source_plan, intent, plan = payload_from_args(semantic_args)
    semantic_payload = build_semantic_debug_payload(
        semantic_args,
        source_plan=source_plan,
        intent=intent,
        plan=plan,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        mask_context=_mask_context_from_args(args),
        configuration_warnings=_configuration_warnings(source_plan),
    )
    _demote_devlog_generic_repo_recovery(semantic_payload)
    semantic_report.write_text(render(semantic_payload, "json"), encoding="utf-8")
    semantic_packet = semantic_payload["semantic_debug"]

    repo_artifact_payload = {
        "repo_artifact_evidence": _build_devlog_repo_artifact_evidence_packet(
            cwd=Path(args.cwd).expanduser().resolve(),
            query=args.query,
        )
    }
    repo_report.write_text(render(repo_artifact_payload, "json"), encoding="utf-8")

    failure_payload = build_failure_analysis_payload(
        semantic_debug_report=semantic_report,
        story_id="devlog-source-reach",
        repo_artifact_evidence_report=repo_report,
    )
    failure_report.write_text(render(failure_payload, "json"), encoding="utf-8")
    failure_packet = failure_payload["failure_analysis"]

    target_reads, diagnostic_reads = _partition_devlog_source_window_reads(
        semantic_packet,
        query=args.query,
    )
    retained_reports = _discover_devlog_retained_primary_reports(
        Path(args.cwd).expanduser().resolve()
    )
    retained_target_reads, retained_diagnostic_reads = _load_devlog_retained_primary_reads(
        retained_reports,
        cwd=Path(args.cwd).expanduser().resolve(),
        query=args.query,
    )
    seen_target_reads = {str(read.get("target") or "") for read in target_reads}
    for read in retained_target_reads:
        target = str(read.get("target") or "")
        if target and target not in seen_target_reads:
            target_reads.append(read)
            seen_target_reads.add(target)
    seen_diagnostic_reads = {str(read.get("target") or "") for read in diagnostic_reads}
    for read in retained_diagnostic_reads:
        target = str(read.get("target") or "")
        if target and target not in seen_target_reads and target not in seen_diagnostic_reads:
            diagnostic_reads.append(read)
            seen_diagnostic_reads.add(target)
    if not target_reads:
        target_reads = _devlog_committed_safe_packet_fallback_reads(
            repo_artifact_payload["repo_artifact_evidence"],
            failure_packet=failure_packet,
        )
    repo_phase_category_evidence = _compact_repo_phase_category_evidence(
        repo_artifact_payload["repo_artifact_evidence"]
    )
    core_convo_first_read_plan = build_core_convo_first_read_plan(
        target_reads=target_reads,
        legitimate_context_reads=_devlog_repo_phase_context_reads(repo_phase_category_evidence),
        diagnostic_reads=diagnostic_reads,
        route_family="devlog_failure_analysis",
        plan_source="ask_execute.devlog_failure_analysis",
    )
    target_reads = (
        core_convo_first_read_plan["role_groups"]["target_historical_raw"]
        + core_convo_first_read_plan["role_groups"]["target_historical_artifact"]
    )[:8]
    diagnostic_reads = core_convo_first_read_plan["role_groups"]["diagnostic_or_post_hoc"][:8]
    chronology_source_reach = _build_devlog_chronology_source_reach(
        cwd=Path(args.cwd).expanduser().resolve(),
        target_reads=target_reads,
        retained_reports=retained_reports,
    )
    compact = {
        "schema_version": 1,
        "status": semantic_packet.get("status"),
        "query": args.query,
        "report_written": str(Path(args.report_output).expanduser()),
        "linked_reports": {
            "semantic_debug": str(semantic_report),
            "failure_analysis": str(failure_report),
            "repo_artifact_evidence": str(repo_report),
        },
        "source_read_order": [
            "chronology_source_reach.copyable_next_reads",
            "target_historical_source_window_reads",
            "core_convo_first_read_plan",
            "repo_phase_category_evidence",
            "diagnostic_source_window_reads",
        ],
        "core_convo_first_read_plan": core_convo_first_read_plan,
        "target_historical_source_window_reads": target_reads,
        "target_historical_source_window_read_count": len(target_reads),
        "chronology_source_reach": chronology_source_reach,
        "diagnostic_source_window_reads": diagnostic_reads,
        "diagnostic_source_window_read_count": len(diagnostic_reads),
        "diagnostic_policy": {
            "current_adjacent_same_loop_refs_are_default_final_support": False,
            "actionability": (
                "diagnostic-only unless the user explicitly asks to audit current output, "
                "the current route, the current lane, or a prior answer/report"
            ),
        },
        "generic_recovery_policy": {
            "primary_recovery_surface": "chronology_source_reach.copyable_next_reads",
            "broad_repo_missing_role_actions": "demoted_to_secondary_context_only",
            "repo_artifact_roots_are_primary_raw_recovery": False,
        },
        "output_contract": {
            "stdout": "ask prints the selected format and is not suppressed by report-output",
            "report_output_format": "json",
            "report_output": str(Path(args.report_output).expanduser()),
            "linked_report_format": "json",
            "jq_rule": "Run jq only against .json report paths; --format text output is terminal text.",
        },
        "repo_phase_category_evidence": repo_phase_category_evidence,
        "failure_analysis": {
            "status": failure_packet.get("status"),
            "report_written": str(failure_report),
            "devlog_brief": failure_packet.get("devlog_brief"),
            "story_phase_summary": _compact_story_phase_summary(
                failure_packet.get("story_phase_summary")
            ),
        },
        "answer_contract": {
            "answer_status": chronology_source_reach["answer_status"],
            "required_read_order": [
                "chronology_source_reach.copyable_next_reads",
                "target_historical_source_window_reads",
                "failure_analysis.devlog_brief",
                "repo_phase_category_evidence",
            ],
            "only_continue_if": (
                "A named devlog facet remains missing after target historical source-window "
                "reads and the safe repo-phase/category packet."
            ),
        },
        "privacy_flags": {
            "raw_private_transcript_text_included": False,
            "raw_private_session_ids_included": False,
            "absolute_private_paths_committed": False,
            "vectors_or_indexes_included": False,
            "repo_artifact_evidence_is_committed_safe_metadata_only": True,
        },
    }
    Path(args.report_output).expanduser().write_text(
        render({"devlog_failure_analysis": compact}, "json"),
        encoding="utf-8",
    )
    return {"devlog_failure_analysis": compact}


DEVLOG_BROAD_REPO_RECOVERY_TARGETS = {
    ".",
    "tasks reports codex/state/reviews",
    "tasks reports codex/state",
}


def _demote_devlog_generic_repo_recovery(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _demote_devlog_generic_repo_recovery(item)
        return
    if not isinstance(value, dict):
        return
    if (
        value.get("kind") == "missing_role_recovery_command"
        and str(value.get("target") or "") in DEVLOG_BROAD_REPO_RECOVERY_TARGETS
    ):
        value.update(
            {
                "diagnostic_only": True,
                "default_actionable": False,
                "final_support_allowed": False,
                "followthrough_actionable": False,
                "read_more_actionable": False,
                "source_tier": "legitimate_pre_existing_secondary_context",
                "demotion_reason": "devlog_primary_roles_require_bounded_april_raw_history",
                "command": None,
                "executable_guidance_removed": True,
                "recovery_instruction": (
                    "Secondary context only. Broad recursive repository recovery is disabled for this route; "
                    "use chronology_source_reach.copyable_next_reads and fail closed if its role remains missing."
                ),
            }
        )
    for child in value.values():
        _demote_devlog_generic_repo_recovery(child)


DEVLOG_REPO_ARTIFACT_PREFIXES = (
    "tasks/",
    "reports/",
    "docs/",
    "codex/state/reviews/",
)
DEVLOG_REPO_ARTIFACT_SUFFIXES = (".md", ".json", ".txt")
DEVLOG_REPO_ARTIFACT_SCAN_LIMIT = 400
DEVLOG_REPO_ARTIFACT_ROW_LIMIT = 20
DEVLOG_SAFE_TERM_CATEGORY_MAP = {
    "docker": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "runner": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "harness": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "auth": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "live-send": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "live send": ("infrastructure_churn", "runner_harness_auth_live_send"),
    "coordinator": ("workflow_process_overhead", "coordinator_review_process"),
    "review": ("workflow_process_overhead", "coordinator_review_process"),
    "workflow": ("workflow_process_overhead", "coordinator_review_process"),
    "process": ("workflow_process_overhead", "coordinator_review_process"),
    "product": ("product_behavior_recovery", "product_path_recovery"),
    "product-path": ("product_behavior_recovery", "product_path_recovery"),
    "graveyard": ("product_behavior_recovery", "product_path_recovery"),
    "source window": ("historical_source_reach", "historical_source_window"),
    "source-window": ("historical_source_reach", "historical_source_window"),
    "devlog": ("devlog_story_map", "story_reconstruction"),
}
DEVLOG_CHRONOLOGY_ROLE_ORDER = (
    "first_appearance",
    "worsening_process_cause",
    "later_change",
)
DEVLOG_CHRONOLOGY_ROLE_TERMS = {
    "first_appearance": (
        "first long-running coordinator",
        "first appear",
        "first started",
        "failure began",
        "period started",
        "2026-04-13",
        "2026-04-14",
    ),
    "worsening_process_cause": (
        "doing the process instead of the work",
        "doing process work instead",
        "process churn",
        "process drift",
        "docker",
        "harness",
        "live-send",
        "live send",
        "token waste",
        "token burn",
    ),
    "later_change": (
        "what changed later",
        "moving back toward product",
        "product behavior changes",
        "product path correction",
        "harness graveyard",
        "active product path",
        "paused, stale, or suspect",
    ),
}
DEVLOG_DEDICATED_CASE_PATH = "docs/devlog-agentic-workflow-failure-analysis-test-cases.md"
DEVLOG_CHRONOLOGY_ARTIFACT_PREFIX_LIMIT = 120
DEVLOG_CHRONOLOGY_ARTIFACT_KEYWORDS = (
    "coordinator",
    "devlog",
    "failure",
    "workflow",
    "product-path",
    "product_path",
    "graveyard",
    "pressure",
)
DEVLOG_RETAINED_SEMANTIC_REPORT_RE = re.compile(
    r"(?P<path>\.private/[A-Za-z0-9_+./-]+/semantic-debug\.json)"
)
DEVLOG_PRIMARY_RECOVERY_REPORT_DIR = ".private/jsonl/devlog-chronology"
DEVLOG_PRIMARY_WINDOW_START_UTC = "2026-04-13T16:00:48.508Z"
# Exclusive bound that includes JSH-896's last observed event at 12:27:59.905Z.
DEVLOG_PRIMARY_WINDOW_END_UTC = "2026-04-17T12:28:00Z"
DEVLOG_CODEX_APRIL_RELATIVE_ROOTS = (
    "sessions/2026/04/13",
    "sessions/2026/04/14",
    "sessions/2026/04/15",
    "sessions/2026/04/16",
    "sessions/2026/04/17",
    "history.jsonl",
)
DEVLOG_CLAUDE_MTIME_START = "2026-04-13 16:00:48 UTC"
DEVLOG_CLAUDE_MTIME_END = "2026-04-17 12:28:00 UTC"
DEVLOG_PRIMARY_RECOVERY_MAX_FILES = 250
DEVLOG_PRIMARY_RECOVERY_MAX_BYTES = 175_000_000
DEVLOG_PRIMARY_RECOVERY_QUERIES = {
    "first_appearance": (
        "Find the earliest primary raw user or assistant source window from "
        "2026-04-13T16:00:48Z through 2026-04-17T12:27:59Z that shows where the "
        "early JSON-search-history coordinator workflow failure first appeared. "
        "Exclude tasks, reports, reviews, current-run output, prompt echoes, and synthetic fixtures."
    ),
    "worsening_process_cause": (
        "Find primary raw user or assistant source windows from 2026-04-13T16:00:48Z "
        "through 2026-04-17T12:27:59Z that show Docker, runner, harness, auth, live-send, "
        "process edits, token waste, or process work making the early JSON-search-history "
        "coordinator failure worse. Exclude tasks, reports, reviews, current-run output, "
        "prompt echoes, and synthetic fixtures."
    ),
}
DEVLOG_LATER_CHANGE_GIT_PATH = "docs/harness-graveyard-and-product-path-correction.md"


def _devlog_history_root_preflight(args: argparse.Namespace | None) -> dict:
    explicit_roots = [
        Path(str(root)).expanduser()
        for root in list(getattr(args, "root", None) or [])
    ]
    explicit_available = any(root.exists() for root in explicit_roots)

    codex_override = os.environ.get("JSONL_HISTORY_CODEX_HOME")
    codex_arg = getattr(args, "codex_home", None) if args is not None else None
    codex_runtime = os.environ.get("CODEX_HOME")
    codex_home = Path(
        codex_override or codex_arg or codex_runtime or (Path.home() / ".codex")
    ).expanduser()
    codex_available = (codex_home / "history.jsonl").is_file() or any(
        (codex_home / relative).exists()
        for relative in DEVLOG_CODEX_APRIL_RELATIVE_ROOTS[:-1]
    )

    claude_override = os.environ.get("JSONL_HISTORY_CLAUDE_HOME")
    claude_arg = getattr(args, "claude_home", None) if args is not None else None
    claude_runtime = os.environ.get("CLAUDE_HOME")
    claude_home = Path(
        claude_override or claude_arg or claude_runtime or (Path.home() / ".claude")
    ).expanduser()
    claude_projects = claude_home / "projects"
    claude_available = _bounded_tree_has_jsonl(claude_projects)

    return {
        "status": (
            "primary_history_available"
            if explicit_available or codex_available or claude_available
            else "primary_history_roots_unavailable"
        ),
        "any_primary_history_available": explicit_available or codex_available or claude_available,
        "explicit_root_available": explicit_available,
        "codex": {
            "available": codex_available,
            "configured_by": (
                "JSONL_HISTORY_CODEX_HOME"
                if codex_override
                else "--codex-home"
                if codex_arg
                else "CODEX_HOME"
                if codex_runtime
                else "$HOME/.codex"
            ),
            "isolated_runtime_home_is_history_root": codex_available,
        },
        "claude": {
            "available": claude_available,
            "configured_by": (
                "JSONL_HISTORY_CLAUDE_HOME"
                if claude_override
                else "--claude-home"
                if claude_arg
                else "CLAUDE_HOME"
                if claude_runtime
                else "$HOME/.claude"
            ),
            "isolated_runtime_home_is_history_root": claude_available,
        },
        "override_contract": (
            "Use JSONL_HISTORY_CODEX_HOME or JSONL_HISTORY_CLAUDE_HOME only for a real history root; "
            "otherwise fail closed without repository fallback."
        ),
    }


def _bounded_tree_has_jsonl(root: Path, *, max_directories: int = 200) -> bool:
    if not root.is_dir():
        return False
    visited = 0
    for _directory, directories, files in os.walk(root):
        visited += 1
        if any(name.endswith(".jsonl") for name in files):
            return True
        if visited >= max_directories:
            directories[:] = []
            break
    return False


def _build_devlog_chronology_source_reach(
    *,
    cwd: Path,
    target_reads: list[dict],
    retained_reports: list[dict] | None = None,
) -> dict:
    primary_by_role = {role: [] for role in DEVLOG_CHRONOLOGY_ROLE_ORDER}
    for read in target_reads:
        if not isinstance(read, dict) or read.get("target_source_role") != "target_historical_raw":
            continue
        for role in list(read.get("devlog_chronology_roles") or []):
            if role in primary_by_role:
                primary_by_role[role].append(_compact_devlog_chronology_primary_read(read))

    artifact_leads = _devlog_chronology_artifact_leads(cwd)
    artifact_by_role = {role: [] for role in DEVLOG_CHRONOLOGY_ROLE_ORDER}
    for lead in artifact_leads:
        artifact_by_role[lead["role"]].append(lead)

    missing_roles = [role for role in DEVLOG_CHRONOLOGY_ROLE_ORDER if not primary_by_role[role]]
    retained_reports = list(retained_reports or [])
    primary_recovery_reads = {
        role: _devlog_primary_recovery_reads(role)
        for role in missing_roles
    }
    copyable_next_reads = []
    for role in DEVLOG_CHRONOLOGY_ROLE_ORDER:
        primary_reads = primary_by_role[role]
        if primary_reads:
            for read in primary_reads[:2]:
                if read.get("command"):
                    copyable_next_reads.append(
                        {
                            "role": role,
                            "kind": "primary_source_window",
                            "command": read["command"],
                            "target": read.get("target"),
                            "purpose": f"Open primary historical evidence for {role}.",
                        }
                    )
            continue
        copyable_next_reads.extend(primary_recovery_reads[role][:2])

    answer_allowed = not missing_roles
    return {
        "status": "primary_role_package_ready" if answer_allowed else "fail_closed_missing_primary_roles",
        "answer_status": (
            "inspect_role_complete_target_history_before_answer"
            if answer_allowed
            else "fail_closed_missing_chronology_roles"
        ),
        "answer_allowed": answer_allowed,
        "required_roles": list(DEVLOG_CHRONOLOGY_ROLE_ORDER),
        "covered_primary_roles": [role for role in DEVLOG_CHRONOLOGY_ROLE_ORDER if primary_by_role[role]],
        "missing_roles": missing_roles,
        "primary_role_reads": primary_by_role,
        "primary_recovery_reads": primary_recovery_reads,
        "retained_primary_report_handles": retained_reports,
        "artifact_role_leads": artifact_by_role,
        "dedicated_case_lead": next(
            (lead for lead in artifact_leads if lead.get("path") == DEVLOG_DEDICATED_CASE_PATH),
            None,
        ),
        "copyable_next_reads": copyable_next_reads[:6],
        "stop_condition": (
            "Answer only after primary historical reads cover first appearance, worsening process cause, "
            "and later change. Run role-bound primary recovery before consulting secondary artifact leads; "
            "artifact leads do not satisfy a missing primary role."
        ),
        "claim_boundary": (
            "This package locates first reads. It does not generate devlog prose, prove all-history "
            "completeness, or promote current-run, synthetic, task-assignment, or post-hoc answer artifacts."
        ),
    }


def _discover_devlog_retained_primary_reports(cwd: Path) -> list[dict]:
    reports = []
    seen = set()
    for source_artifact in _iter_devlog_chronology_artifact_paths(cwd):
        path = cwd / source_artifact
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        normalized = text.casefold()
        if "early coordinator" not in normalized and "first long-running coordinator" not in normalized:
            continue
        for match in DEVLOG_RETAINED_SEMANTIC_REPORT_RE.finditer(text):
            relative_path = match.group("path")
            if relative_path in seen:
                continue
            seen.add(relative_path)
            reports.append(
                {
                    "kind": "retained_primary_semantic_debug_report",
                    "safe_handle": f"private:{relative_path[len('.private/'):]}",
                    "relative_path": relative_path,
                    "source_artifact": source_artifact,
                    "available": (cwd / relative_path).is_file(),
                    "source_tier": "primary_recovery_handle",
                    "raw_text_included": False,
                }
            )
    reports.sort(
        key=lambda row: (
            not row["available"],
            -_devlog_chronology_path_priority(row["source_artifact"]),
            row["relative_path"],
        )
    )
    return reports[:6]


def _load_devlog_retained_primary_reads(
    retained_reports: list[dict],
    *,
    cwd: Path,
    query: str,
) -> tuple[list[dict], list[dict]]:
    target_reads = []
    diagnostic_reads = []
    for report in retained_reports:
        if not report.get("available"):
            continue
        try:
            payload = json.loads((cwd / report["relative_path"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        packet = payload.get("semantic_debug") if isinstance(payload, dict) else None
        if not isinstance(packet, dict):
            continue
        report_targets, report_diagnostics = _partition_devlog_source_window_reads(
            packet,
            query=query,
        )
        provenance = {
            "retained_primary_report_handle": report["safe_handle"],
            "retained_report_source_artifact": report["source_artifact"],
            "retained_report_recovery": True,
        }
        target_reads.extend({**read, **provenance} for read in report_targets)
        diagnostic_reads.extend({**read, **provenance} for read in report_diagnostics)
    return target_reads[:12], diagnostic_reads[:12]


def _devlog_primary_recovery_reads(
    role: str,
    *,
    write_report: bool = True,
    cli_prefix: str = "jsonl-history-search",
) -> list[dict]:
    if role == "later_change":
        path = DEVLOG_LATER_CHANGE_GIT_PATH
        return [
            {
                "role": role,
                "kind": "primary_git_history_recovery",
                "source_handle": f"git:path:{path}",
                "command": (
                    f'commit="$(git log --follow --diff-filter=A --format=\'%H\' -- "{path}" | tail -n 1)"; '
                    f'test -n "$commit" && git show --format=fuller --stat "$commit" -- "{path}"'
                ),
                "purpose": (
                    "Open the introducing commit for the later product-path transition as bounded git proof."
                ),
            }
        ]

    return [
        _devlog_provider_primary_recovery_read(
            role,
            provider="codex",
            write_report=write_report,
            cli_prefix=cli_prefix,
        ),
        _devlog_provider_primary_recovery_read(
            role,
            provider="claude",
            write_report=write_report,
            cli_prefix=cli_prefix,
        ),
    ]


def _devlog_provider_primary_recovery_read(
    role: str,
    *,
    provider: str,
    write_report: bool = True,
    cli_prefix: str = "jsonl-history-search",
) -> dict:
    query = DEVLOG_PRIMARY_RECOVERY_QUERIES[role]
    report_path = f"{DEVLOG_PRIMARY_RECOVERY_REPORT_DIR}/{role}-{provider}-semantic-debug.json"
    common_flags = (
        f"--allow-arbitrary-root --hard-cap-files {DEVLOG_PRIMARY_RECOVERY_MAX_FILES} "
        f"--hard-cap-bytes {DEVLOG_PRIMARY_RECOVERY_MAX_BYTES} "
        f"--max-files {DEVLOG_PRIMARY_RECOVERY_MAX_FILES} "
        f"--max-bytes {DEVLOG_PRIMARY_RECOVERY_MAX_BYTES} "
        f"--max-matches {DEVLOG_PRIMARY_RECOVERY_MAX_FILES} --max-elapsed 45 "
        f"--since {DEVLOG_PRIMARY_WINDOW_START_UTC} --until {DEVLOG_PRIMARY_WINDOW_END_UTC} "
        "--timezone-offset +00:00 --watchlist process_drift "
        "--watchlist docker_harness_auth_live_send --watchlist product_vs_process_churn "
    )
    if provider == "codex":
        root_flags = '"${CODEX_ROOT_ARGS[@]}"'
        relative_roots = " ".join(shlex.quote(root) for root in DEVLOG_CODEX_APRIL_RELATIVE_ROOTS)
        preamble = (
            'CODEX_HISTORY_HOME="${JSONL_HISTORY_CODEX_HOME:-${CODEX_HOME:-$HOME/.codex}}" && '
            'CODEX_ROOT_ARGS=() && '
            f"for relative in {relative_roots}; do path=\"$CODEX_HISTORY_HOME/$relative\"; "
            'test -e "$path" && CODEX_ROOT_ARGS+=(--root "$path"); done && '
            'test "${#CODEX_ROOT_ARGS[@]}" -gt 0 || { '
            f"printf 'Missing primary history root for role {role} (provider codex): %s has no bounded history inputs; "
            "an isolated runtime home is not a real history root. Set JSONL_HISTORY_CODEX_HOME.\\n' "
            '"$CODEX_HISTORY_HOME" >&2; exit 2; }'
        )
        if write_report:
            preamble += f' && mkdir -p "$PWD/{DEVLOG_PRIMARY_RECOVERY_REPORT_DIR}"'
        source_scope = ["codex_sessions_april_13_17", "codex_history"]
        root_contract = {
            "provider": "codex",
            "history_home_precedence": ["JSONL_HISTORY_CODEX_HOME", "CODEX_HOME", "$HOME/.codex"],
            "relative_roots": list(DEVLOG_CODEX_APRIL_RELATIVE_ROOTS),
            "scope_boundary": "April 13-17 Codex session buckets plus Codex history only",
            "runtime_home_boundary": (
                "An isolated runtime CODEX_HOME is not treated as real history unless it contains a bounded input; "
                "set JSONL_HISTORY_CODEX_HOME to the real history root or fail closed with this role missing."
            ),
        }
    else:
        root_flags = '"${CLAUDE_ROOT_ARGS[@]}"'
        preamble = (
            'CLAUDE_HISTORY_HOME="${JSONL_HISTORY_CLAUDE_HOME:-${CLAUDE_HOME:-$HOME/.claude}}" && '
            'test -d "$CLAUDE_HISTORY_HOME/projects" || { '
            f"printf 'Missing primary history root for role {role} (provider claude): %s/projects is unavailable; "
            "an isolated runtime home is not a real history root. Set JSONL_HISTORY_CLAUDE_HOME.\\n' "
            '"$CLAUDE_HISTORY_HOME" >&2; exit 2; } && '
            "CLAUDE_ROOT_ARGS=() && "
            "while IFS= read -r -d '' path; do CLAUDE_ROOT_ARGS+=(--root \"$path\"); done "
            f'< <(find "$CLAUDE_HISTORY_HOME/projects" -type f -name \'*.jsonl\' '
            f"-newermt {shlex.quote(DEVLOG_CLAUDE_MTIME_START)} "
            f"! -newermt {shlex.quote(DEVLOG_CLAUDE_MTIME_END)} -print0) && "
            'test "${#CLAUDE_ROOT_ARGS[@]}" -gt 0 || { '
            f"printf 'Missing primary history root for role {role} (provider claude): no project logs in the bounded April window.\\n' >&2; "
            'exit 2; }'
        )
        if write_report:
            preamble += f' && mkdir -p "$PWD/{DEVLOG_PRIMARY_RECOVERY_REPORT_DIR}"'
        source_scope = ["claude_project_logs_april_mtime_candidates"]
        root_contract = {
            "provider": "claude",
            "history_home_precedence": ["JSONL_HISTORY_CLAUDE_HOME", "CLAUDE_HOME", "$HOME/.claude"],
            "root_discovery": "individual Claude project JSONL files whose mtime overlaps the JSH-896 window",
            "mtime_window": {
                "start": DEVLOG_CLAUDE_MTIME_START,
                "end": DEVLOG_CLAUDE_MTIME_END,
            },
            "scope_boundary": (
                "bounded April-mtime Claude project logs only, with exact record timestamps enforced by --since/--until; "
                "fail closed if no candidate files exist"
            ),
            "runtime_home_boundary": (
                "An isolated runtime CLAUDE_HOME is not treated as real history unless projects contains a bounded input; "
                "set JSONL_HISTORY_CLAUDE_HOME to the real history root or fail closed with this role missing."
            ),
        }
    command = f"{preamble} && " if preamble else ""
    command += (
        f"{cli_prefix} semantic-debug {shlex.quote(query)} --cwd \"$PWD\" "
        f"{root_flags} {common_flags}"
    )
    if write_report:
        command += (
            f'--report-output "$PWD/{report_path}" --format text && '
            f"{cli_prefix} follow-next-reads "
            f'--from-report "$PWD/{report_path}" --kind recovery --limit 5 --format text'
        )
    else:
        command += "--format text"
    output_contract = (
        {
            "stdout_format": "text",
            "stdout_use": "readable compact semantic-debug handoff followed by bounded recovery reads",
            "report_output_format": "json",
            "report_output_path": f"$PWD/{report_path}",
            "report_output_rule": (
                "The .json report-output suffix writes a JSON sidecar; --format text controls stdout only. "
                "Run jq only against the JSON sidecar, not terminal text."
            ),
        }
        if write_report
        else {
            "stdout_format": "text",
            "stdout_use": "direct bounded semantic-debug source reach",
            "report_output_format": None,
            "report_output_path": None,
            "report_output_rule": "No report output is requested or written by this recovery command.",
        }
    )
    return {
        "role": role,
        "kind": (
            "primary_raw_history_recovery"
            if write_report
            else "primary_raw_history_recovery_stdout"
        ),
        "provider": provider,
        "source_handle": (
            f"private:{report_path[len('.private/') :]}" if write_report else None
        ),
        "recovery_output_handle": (
            f"private:{report_path[len('.private/') :]}" if write_report else None
        ),
        "historical_window_utc": {
            "start": DEVLOG_PRIMARY_WINDOW_START_UTC,
            "end": DEVLOG_PRIMARY_WINDOW_END_UTC,
            "filter_contract": "inclusive --since and exclusive --until record filter",
        },
        "source_scope": source_scope,
        "root_contract": root_contract,
        "missing_role_on_unavailable": role,
        "output_contract": output_contract,
        "command": command,
        "purpose": (
            f"Recover bounded primary user/assistant source refs for {role} from {provider} raw history; "
            "current-run, synthetic, task-assignment, post-hoc, and repo-artifact rows remain non-answering."
        ),
    }


def _compact_devlog_chronology_primary_read(read: dict) -> dict:
    target = str(read.get("target") or "")
    return {
        "target": target or None,
        "source_ref": read.get("source_ref"),
        "timestamp_utc": read.get("timestamp_utc"),
        "record_role": (read.get("devlog_target_source_eligibility") or {}).get("record_role"),
        "command": _devlog_source_window_command(target) if target else None,
        "final_support_candidate": bool(read.get("final_support_candidate")),
    }


def _devlog_source_window_command(target: str) -> str:
    escaped = target.replace('"', '\\"')
    return (
        f'jsonl-history-search read-more "{escaped}" --mode window --before 3 --after 3 '
        '--cwd "$PWD" --format text'
    )


def _devlog_chronology_artifact_leads(cwd: Path) -> list[dict]:
    candidates = []
    artifact_paths = _iter_devlog_chronology_artifact_paths(cwd)
    line_cache = {}
    for role in DEVLOG_CHRONOLOGY_ROLE_ORDER:
        terms = DEVLOG_CHRONOLOGY_ROLE_TERMS[role]
        for rel_text in artifact_paths:
            path = cwd / rel_text
            if rel_text not in line_cache:
                try:
                    line_cache[rel_text] = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    line_cache[rel_text] = []
            lines = line_cache[rel_text]
            matched_lines = [
                (index, line.casefold())
                for index, line in enumerate(lines, 1)
                if any(term in line.casefold() for term in terms)
            ]
            line_number = matched_lines[0][0] if matched_lines else None
            if line_number is None:
                continue
            start = max(1, line_number - 3)
            end = min(len(lines), line_number + 3)
            matched_terms = {term for _index, line in matched_lines for term in terms if term in line}
            path_priority = _devlog_chronology_path_priority(rel_text)
            candidates.append(
                (
                    len(matched_terms) * 10 + path_priority,
                    {
                        "role": role,
                        "kind": "repo_artifact_chronology_lead",
                        "path": rel_text,
                        "line": line_number,
                        "target": f"{rel_text}:{line_number}",
                        "command": f'sed -n \'{start},{end}p\' "$PWD/{rel_text}"',
                        "source_tier": "legitimate_pre_existing_secondary_context",
                        "final_support_allowed": False,
                        "readable": True,
                        "boundary": "artifact lead only; follow cited primary historical source refs before answering",
                    },
                )
            )
    leads = []
    for role in DEVLOG_CHRONOLOGY_ROLE_ORDER:
        role_candidates = [item for item in candidates if item[1]["role"] == role]
        role_candidates.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["line"]))
        leads.extend(item[1] for item in role_candidates[:3])
    return leads


def _iter_devlog_chronology_artifact_paths(cwd: Path) -> list[str]:
    selected = []
    for prefix in DEVLOG_REPO_ARTIFACT_PREFIXES:
        root = cwd / prefix
        if not root.exists():
            continue
        paths = []
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in DEVLOG_REPO_ARTIFACT_SUFFIXES:
                continue
            try:
                rel_text = path.relative_to(cwd).as_posix()
            except ValueError:
                continue
            if ".private/" in rel_text:
                continue
            paths.append(rel_text)
        paths.sort(key=lambda rel_text: (-_devlog_chronology_path_priority(rel_text), rel_text))
        selected.extend(paths[:DEVLOG_CHRONOLOGY_ARTIFACT_PREFIX_LIMIT])
    if (cwd / DEVLOG_DEDICATED_CASE_PATH).is_file() and DEVLOG_DEDICATED_CASE_PATH not in selected:
        selected.insert(0, DEVLOG_DEDICATED_CASE_PATH)
    return selected


def _devlog_chronology_path_priority(rel_text: str) -> int:
    normalized = rel_text.casefold()
    score = sum(1 for keyword in DEVLOG_CHRONOLOGY_ARTIFACT_KEYWORDS if keyword in normalized)
    if normalized == DEVLOG_DEDICATED_CASE_PATH:
        score += 20
    return score


def _build_devlog_repo_artifact_evidence_packet(*, cwd: Path, query: str) -> dict:
    rows = []
    for rel_path in _iter_safe_repo_artifact_paths(cwd):
        try:
            text = (cwd / rel_path).read_text(encoding="utf-8", errors="ignore")[:200_000].casefold()
        except OSError:
            continue
        matched_terms = [
            term
            for term in DEVLOG_SAFE_TERM_CATEGORY_MAP
            if term in text or term in query.casefold()
        ]
        if not matched_terms:
            continue
        category_ids = []
        phase_hints = []
        for term in matched_terms:
            category_id, phase_hint = DEVLOG_SAFE_TERM_CATEGORY_MAP[term]
            if category_id not in category_ids:
                category_ids.append(category_id)
            if phase_hint not in phase_hints:
                phase_hints.append(phase_hint)
        rows.append(
            {
                "safe_handles": [str(rel_path)],
                "support_surfaces": ["repo_artifact_evidence.safe_repo_scan"],
                "category_ids": category_ids[:4],
                "phase_hints": phase_hints[:4],
                "matched_safe_terms": matched_terms[:8],
                "evidence_strength": {
                    "safe_handle_count": 1,
                    "support_surface_count": 1,
                    "matched_safe_term_count": len(matched_terms),
                },
                "claim_boundaries": [
                    "safe repo metadata only",
                    "does not prove root cause, all-history completeness, token savings, speed, or final answer quality",
                ],
                "status": "safe_repo_phase_category_evidence",
            }
        )
        if len(rows) >= DEVLOG_REPO_ARTIFACT_ROW_LIMIT:
            break
    return {
        "schema_version": 1,
        "status": "ok",
        "rows": rows,
        "row_count": len(rows),
        "source_policy": {
            "raw_private_text_included": False,
            "absolute_private_paths_included": False,
            "session_ids_included": False,
            "vectors_or_indexes_included": False,
            "safe_repo_relative_handles_only": True,
        },
    }


def _iter_safe_repo_artifact_paths(cwd: Path) -> list[Path]:
    paths: list[Path] = []
    for prefix in DEVLOG_REPO_ARTIFACT_PREFIXES:
        root = cwd / prefix
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if len(paths) >= DEVLOG_REPO_ARTIFACT_SCAN_LIMIT:
                return paths
            if not path.is_file() or path.suffix not in DEVLOG_REPO_ARTIFACT_SUFFIXES:
                continue
            try:
                rel = path.relative_to(cwd)
            except ValueError:
                continue
            rel_text = rel.as_posix()
            if ".private/" in rel_text or not rel_text.startswith(DEVLOG_REPO_ARTIFACT_PREFIXES):
                continue
            paths.append(rel)
    return paths


def _compact_repo_phase_category_evidence(packet: dict) -> dict:
    rows = list(packet.get("rows") or [])
    categories = sorted(
        {
            category
            for row in rows
            for category in list(row.get("category_ids") or [])
        }
    )
    phase_hints = sorted(
        {
            phase
            for row in rows
            for phase in list(row.get("phase_hints") or [])
        }
    )
    return {
        "status": packet.get("status"),
        "row_count": packet.get("row_count", len(rows)),
        "category_ids": categories[:12],
        "phase_hints": phase_hints[:12],
        "rows": rows[:8],
        "source_policy": packet.get("source_policy"),
    }


def _devlog_repo_phase_context_reads(packet: dict) -> list[dict]:
    rows = []
    for index, row in enumerate(list(packet.get("rows") or [])[:4], 1):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "kind": "repo_phase_category_context",
                "packet_handle": "repo_phase_category_evidence",
                "candidate_id": f"repo-phase-context-{index:03d}",
                "safe_handles": list(row.get("safe_handles") or [])[:4],
                "category_ids": list(row.get("category_ids") or [])[:4],
                "phase_hints": list(row.get("phase_hints") or [])[:4],
                "support_surfaces": list(row.get("support_surfaces") or [])[:4],
                "target_source_role": "legitimate_context",
                "final_support_allowed": False,
                "read_more_actionable": False,
                "followthrough_actionable": True,
                "diagnostic_only": False,
                "diagnostic_open_policy": "not_diagnostic_secondary_context_after_target_history",
                "raw_text_included": False,
            }
        )
    return rows


def _compact_story_phase_summary(summary: object) -> dict:
    if not isinstance(summary, dict):
        return {}
    return {
        "status": summary.get("status"),
        "phase_count": summary.get("phase_count"),
        "usable_phase_count": summary.get("usable_phase_count"),
        "rows": list(summary.get("rows") or [])[:6],
        "gap_reasons": list(summary.get("gap_reasons") or [])[:8],
    }


def _partition_devlog_source_window_reads(packet: dict, *, query: str) -> tuple[list[dict], list[dict]]:
    handoff = packet.get("source_locator_handoff") if isinstance(packet.get("source_locator_handoff"), dict) else {}
    reads = list(handoff.get("next_reads") or packet.get("next_reads") or [])
    candidates_by_id, candidates_by_target = _devlog_source_window_candidate_lookups(packet)
    target_reads = []
    diagnostic_reads = []
    artifact_audit_requested = _devlog_source_artifact_audit_requested(query)
    for read in reads:
        if not isinstance(read, dict):
            continue
        row = dict(read)
        candidate = candidates_by_id.get(str(row.get("candidate_id") or "")) or candidates_by_target.get(
            str(row.get("target") or "")
        )
        prompt_echo = _devlog_source_window_prompt_echo_classification(
            row,
            candidate=candidate,
            query=query,
        )
        if prompt_echo.get("is_setup_echo") is True:
            reason = "assignment_or_control_prompt_echo"
            row["devlog_target_source_eligibility"] = {
                "eligible": False,
                "default_target_allowed": False,
                "reason": reason,
                "prompt_echo_reason": prompt_echo.get("reason"),
            }
            diagnostic_reads.append(
                _devlog_demote_source_window_read(
                    row,
                    reason=reason,
                )
            )
        elif _devlog_read_is_invalid_default_target_artifact(row, candidate=candidate) and not artifact_audit_requested:
            reason = _devlog_invalid_default_target_artifact_reason(row, candidate=candidate)
            row["devlog_target_source_eligibility"] = {
                "eligible": False,
                "default_target_allowed": False,
                "reason": reason,
            }
            diagnostic_reads.append(
                _devlog_demote_source_window_read(
                    row,
                    reason=reason,
                )
            )
        elif _devlog_read_is_diagnostic(row) and not artifact_audit_requested:
            row["devlog_target_source_eligibility"] = {
                "eligible": False,
                "default_target_allowed": False,
                "reason": "chronology_diagnostic",
            }
            diagnostic_reads.append(_devlog_demote_source_window_read(row, reason="chronology_diagnostic"))
        elif row.get("target"):
            eligibility = _devlog_default_target_source_eligibility(row, candidate=candidate)
            if eligibility.get("eligible") is True:
                row = _devlog_mark_real_source_target_read(row, eligibility=eligibility)
                row["devlog_chronology_roles"] = _devlog_chronology_roles_from_candidate(
                    row,
                    candidate=candidate,
                )
                row["final_support_candidate"] = True
                target_reads.append(row)
            elif artifact_audit_requested:
                row["final_support_candidate"] = True
                row["artifact_audit_promoted"] = True
                row["devlog_target_source_eligibility"] = {
                    **eligibility,
                    "artifact_audit_promoted": True,
                    "default_target_allowed": False,
                }
                target_reads.append(row)
            else:
                row["devlog_target_source_eligibility"] = eligibility
                diagnostic_reads.append(
                    _devlog_demote_source_window_read(
                        row,
                        reason=str(eligibility.get("reason") or "missing_positive_target_source_eligibility"),
                    )
                )
    return target_reads[:8], diagnostic_reads[:8]


def _devlog_source_window_prompt_echo_classification(
    read: dict,
    *,
    candidate: dict | None,
    query: str,
) -> dict:
    values = [
        read.get("summary"),
        read.get("snippet"),
        read.get("matched_text"),
        read.get("text"),
        read.get("reason"),
    ]
    if isinstance(candidate, dict):
        values.extend(
            (
                candidate.get("summary"),
                candidate.get("snippet"),
                candidate.get("matched_text"),
                candidate.get("text"),
                candidate.get("reason"),
            )
        )
    return setup_echo_classification(
        " ".join(str(value) for value in values if value not in (None, "")),
        query=query,
    )


def _devlog_chronology_roles_from_candidate(read: dict, *, candidate: dict | None) -> list[str]:
    values = [
        read.get("reason"),
        read.get("summary"),
        read.get("snippet"),
        read.get("matched_text"),
    ]
    if isinstance(candidate, dict):
        values.extend(
            (
                candidate.get("reason"),
                candidate.get("summary"),
                candidate.get("snippet"),
                candidate.get("matched_text"),
                candidate.get("text"),
            )
        )
    text = " ".join(str(value) for value in values if value not in (None, "")).casefold()
    if not text:
        return []
    return [
        role
        for role in DEVLOG_CHRONOLOGY_ROLE_ORDER
        if any(term in text for term in DEVLOG_CHRONOLOGY_ROLE_TERMS[role])
    ]


def _devlog_candidate_lookup_record(item: dict, *, source: str) -> dict:
    row = dict(item)
    row["_devlog_lookup_sources"] = [source]
    return row


def _devlog_merge_candidate_lookup(existing: dict, incoming: dict) -> dict:
    sources = list(existing.get("_devlog_lookup_sources") or [])
    for source in list(incoming.get("_devlog_lookup_sources") or []):
        if source not in sources:
            sources.append(source)
    existing["_devlog_lookup_sources"] = sources
    for key, value in incoming.items():
        if key == "_devlog_lookup_sources":
            continue
        if existing.get(key) in (None, "", [], {}):
            existing[key] = value
    return existing


def _devlog_source_window_candidate_lookups(packet: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    candidates = []
    for key in ("strongest_candidates", "find_candidates", "timeline"):
        value = packet.get(key)
        if isinstance(value, list):
            candidates.extend(
                _devlog_candidate_lookup_record(item, source=key)
                for item in value
                if isinstance(item, dict)
            )
    handoff = packet.get("source_locator_handoff") if isinstance(packet.get("source_locator_handoff"), dict) else {}
    for key in ("source_windows", "source_refs"):
        value = handoff.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                candidates.append(_devlog_candidate_lookup_record(item, source=key))
                next_read = item.get("next_read") if isinstance(item.get("next_read"), dict) else {}
                if not next_read:
                    continue
                merged = {**item, **next_read}
                if item.get("source_ref") and not next_read.get("source_ref"):
                    merged["source_ref"] = item.get("source_ref")
                if item.get("target") and not next_read.get("target"):
                    merged["target"] = item.get("target")
                if item.get("role") and not next_read.get("role"):
                    merged["role"] = item.get("role")
                if item.get("timestamp_utc") and not next_read.get("timestamp_utc"):
                    merged["timestamp_utc"] = item.get("timestamp_utc")
                candidates.append(_devlog_candidate_lookup_record(merged, source=f"{key}.next_read"))
    candidate_ordering = handoff.get("candidate_ordering") if isinstance(handoff.get("candidate_ordering"), dict) else {}
    top_candidates = (
        candidate_ordering.get("top_candidates")
        if isinstance(candidate_ordering.get("top_candidates"), list)
        else []
    )
    candidates.extend(
        _devlog_candidate_lookup_record(item, source="candidate_ordering.top_candidates")
        for item in top_candidates
        if isinstance(item, dict)
    )

    by_id: dict[str, dict] = {}
    by_target: dict[str, dict] = {}
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id and candidate_id not in by_id:
            by_id[candidate_id] = candidate
        elif candidate_id:
            by_id[candidate_id] = _devlog_merge_candidate_lookup(by_id[candidate_id], candidate)
        target = str(candidate.get("target") or "")
        if not target and isinstance(candidate.get("source_ref"), dict):
            target = _devlog_source_ref_target(candidate["source_ref"])
        if target and target not in by_target:
            by_target[target] = candidate
        elif target:
            by_target[target] = _devlog_merge_candidate_lookup(by_target[target], candidate)
    return by_id, by_target


def _devlog_source_ref_target(source_ref: dict) -> str:
    path = source_ref.get("path")
    line = source_ref.get("line")
    if not path:
        return ""
    return f"{path}:{line}" if line else str(path)


def _devlog_demote_source_window_read(read: dict, *, reason: str) -> dict:
    row = dict(read)
    selection = (
        dict(row.get("historical_provenance_selection"))
        if isinstance(row.get("historical_provenance_selection"), dict)
        else {}
    )
    if selection:
        selection["diagnostic_only"] = True
        selection["final_support_allowed"] = False
        selection["read_more_actionable"] = False
        selection["followthrough_actionable"] = False
        selection.setdefault("raw_text_included", False)
        selection.setdefault("demotion_reason", reason)
        row["historical_provenance_selection"] = selection
    row["diagnostic_only"] = True
    row["read_more_actionable"] = False
    row["followthrough_actionable"] = False
    row["devlog_target_promotion_status"] = "demoted"
    row["devlog_target_promotion_reason"] = reason
    row["diagnostic_open_policy"] = (
        "diagnostic-only for historical devlog reconstruction unless the user asks "
        "to audit tests/fixtures/smokes, current output, the current route, or a prior report"
    )
    row.pop("expansion_actions", None)
    row.pop("context_actions", None)
    row.pop("final_support_candidate", None)
    return row


def _devlog_record_role(read: dict, candidate: dict | None) -> str:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    candidate_ref = (
        candidate.get("source_ref")
        if isinstance(candidate, dict) and isinstance(candidate.get("source_ref"), dict)
        else {}
    )
    values = (
        read.get("role"),
        read.get("record_role"),
        read.get("message_role"),
        read.get("source_role"),
        source_ref.get("role"),
        candidate.get("role") if isinstance(candidate, dict) else None,
        candidate.get("record_role") if isinstance(candidate, dict) else None,
        candidate.get("message_role") if isinstance(candidate, dict) else None,
        candidate.get("source_role") if isinstance(candidate, dict) else None,
        candidate_ref.get("role"),
    )
    for value in values:
        if value not in (None, ""):
            return str(value).casefold().replace("-", "_")
    return ""


def _devlog_context_category_text(read: dict, candidate: dict | None) -> str:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    values = [
        read.get("kind"),
        read.get("source_category"),
        read.get("category"),
        read.get("record_type"),
        selection.get("source_chronology_role"),
        selection.get("selection_reason"),
    ]
    if isinstance(candidate, dict):
        values.extend(
            [
                candidate.get("kind"),
                candidate.get("source_category"),
                candidate.get("category"),
                candidate.get("record_type"),
                candidate.get("stage"),
                " ".join(str(item) for item in candidate.get("stages") or []),
                " ".join(str(item) for item in candidate.get("reason_codes") or []),
            ]
        )
    return " ".join(str(value) for value in values if value not in (None, "")).casefold().replace("-", "_")


def _devlog_role_is_historical_message(role: str) -> bool:
    return role in {
        "user",
        "assistant",
        "user_message",
        "assistant_message",
        "agent_message",
    }


def _devlog_role_or_category_is_default_denied(role: str, category_text: str) -> bool:
    denied_roles = {
        "system",
        "developer",
        "context",
        "session",
        "session_meta",
        "session_metadata",
        "turn_context",
        "tool",
        "tool_call",
        "tool_result",
        "tool_use",
        "tool_output",
        "function_call",
        "function_call_output",
        "command",
        "command_output",
        "token_count",
    }
    if role in denied_roles:
        return True
    denied_markers = (
        "developer",
        "system",
        "turn_context",
        "session_meta",
        "session_metadata",
        "tool_output",
        "tool_result",
        "tool_event",
        "function_call",
        "command_output",
    )
    return any(marker in category_text for marker in denied_markers)


def _devlog_candidate_has_source_window_provenance(candidate: dict | None) -> bool:
    if not isinstance(candidate, dict):
        return False
    sources = set(str(source) for source in candidate.get("_devlog_lookup_sources") or [])
    return (
        any(source.startswith("source_windows") for source in sources)
        or candidate.get("summary_kind") == "compact_source_window_ref"
        or bool(candidate.get("window_id"))
        or (bool(candidate.get("window_kind")) and isinstance(candidate.get("source_ref"), dict))
        or (
            isinstance(candidate.get("source_window_provenance"), dict)
            and candidate["source_window_provenance"].get("selected_source_window") is True
        )
    )


def _devlog_selection_allows_default_target(selection: dict) -> bool:
    if not selection:
        return False
    if (
        selection.get("diagnostic_only") is True
        or selection.get("final_support_allowed") is not True
        or selection.get("read_more_actionable") is False
    ):
        return False
    role = str(selection.get("source_chronology_role") or "")
    return bool(role and not role.endswith("_diagnostic") and "diagnostic" not in role)


def _devlog_default_target_source_eligibility(read: dict, *, candidate: dict | None = None) -> dict:
    base = {
        "schema_version": 1,
        "eligible": False,
        "default_target_allowed": False,
    }
    if not read.get("target"):
        return {**base, "reason": "missing_target"}
    if read.get("diagnostic_only") is True or read.get("read_more_actionable") is False:
        return {**base, "reason": "chronology_diagnostic"}
    if str(read.get("kind") or "").casefold() == "tool_event_source_ref":
        return {**base, "reason": "tool_event_not_default_devlog_target"}
    if _devlog_read_is_invalid_default_target_artifact(read, candidate=candidate):
        return {
            **base,
            "reason": _devlog_invalid_default_target_artifact_reason(read, candidate=candidate),
        }

    role = _devlog_record_role(read, candidate)
    category_text = _devlog_context_category_text(read, candidate)
    if _devlog_role_or_category_is_default_denied(role, category_text):
        return {
            **base,
            "reason": "non_message_context_not_default_devlog_target",
            "record_role": role or None,
        }

    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    chronology_role = str(selection.get("source_chronology_role") or "")
    has_source_window_provenance = _devlog_candidate_has_source_window_provenance(candidate)
    is_jsonl_target = _devlog_raw_jsonl_source_target(read)

    if (
        _devlog_selection_allows_default_target(selection)
        and chronology_role == "pre_existing_source_ref_quoted_by_current_run_echo"
    ):
        return {
            **base,
            "eligible": True,
            "default_target_allowed": True,
            "category": "pre_existing_source_ref_quoted_by_current_run_echo",
            "source_chronology_role": chronology_role,
            "record_role": role or None,
        }

    if (
        _devlog_selection_allows_default_target(selection)
        and chronology_role == "candidate_or_original_source_ref"
        and has_source_window_provenance
        and is_jsonl_target
        and _devlog_role_is_historical_message(role)
    ):
        return {
            **base,
            "eligible": True,
            "default_target_allowed": True,
            "category": "original_source_window_packet_ref",
            "source_chronology_role": chronology_role,
            "record_role": role or None,
        }

    if (
        is_jsonl_target
        and _devlog_role_is_historical_message(role)
        and (
            has_source_window_provenance
            or (
                _devlog_selection_allows_default_target(selection)
                and chronology_role == "target_historical_raw_jsonl_source_ref"
            )
        )
    ):
        return {
            **base,
            "eligible": True,
            "default_target_allowed": True,
            "category": "historical_user_assistant_jsonl_source_window",
            "record_role": role,
            "source_window_provenance": has_source_window_provenance,
            "source_chronology_role": chronology_role or None,
        }

    return {
        **base,
        "reason": "missing_positive_target_source_eligibility",
        "record_role": role or None,
        "source_window_provenance": has_source_window_provenance,
    }


def _devlog_target_read_has_real_source_provenance(read: dict, *, candidate: dict | None = None) -> bool:
    return _devlog_default_target_source_eligibility(read, candidate=candidate).get("eligible") is True


def _devlog_mark_real_source_target_read(read: dict, *, eligibility: dict | None = None) -> dict:
    row = dict(read)
    if eligibility:
        row["devlog_target_source_eligibility"] = dict(eligibility)
    if not isinstance(row.get("historical_provenance_selection"), dict):
        selection = {
            "schema_version": 1,
            "source_chronology_role": "target_historical_raw_jsonl_source_ref",
            "final_support_allowed": True,
            "read_more_actionable": row.get("read_more_actionable", True) is not False,
            "followthrough_actionable": row.get("followthrough_actionable", True) is not False,
            "diagnostic_only": False,
            "raw_text_included": False,
        }
        if eligibility and eligibility.get("category"):
            selection["devlog_target_source_eligibility_category"] = eligibility["category"]
        row["historical_provenance_selection"] = selection
    else:
        selection = dict(row["historical_provenance_selection"])
        if eligibility and eligibility.get("category"):
            selection["devlog_target_source_eligibility_category"] = eligibility["category"]
        row["historical_provenance_selection"] = selection
    return row


def _devlog_raw_jsonl_source_target(read: dict) -> bool:
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    target = str(read.get("target") or "")
    path = str(source_ref.get("path") or target.rsplit(":", 1)[0] or "")
    return target.endswith(".jsonl") or ".jsonl:" in target or path.casefold().replace("\\", "/").endswith(".jsonl")


def _devlog_committed_safe_packet_fallback_reads(packet: dict, *, failure_packet: dict) -> list[dict]:
    rows = list(packet.get("rows") or []) if isinstance(packet, dict) else []
    fallback_rows = []
    for index, row in enumerate(rows[:4], 1):
        if not isinstance(row, dict):
            continue
        fallback_rows.append(
            {
                "kind": "committed_safe_devlog_packet_fallback",
                "packet_handle": "repo_phase_category_evidence",
                "candidate_id": f"devlog-safe-packet-fallback-{index:03d}",
                "safe_handles": list(row.get("safe_handles") or [])[:4],
                "category_ids": list(row.get("category_ids") or [])[:4],
                "phase_hints": list(row.get("phase_hints") or [])[:4],
                "support_surfaces": list(row.get("support_surfaces") or [])[:4],
                "evidence_strength": row.get("evidence_strength"),
                "claim_boundaries": list(row.get("claim_boundaries") or [])[:4],
                "final_support_candidate": True,
                "fallback_reason": "no positive real-source JSONL target survived devlog source-quality gating",
                "devlog_target_source_eligibility": {
                    "schema_version": 1,
                    "eligible": True,
                    "default_target_allowed": True,
                    "category": "bounded_committed_safe_packet_fallback",
                },
                "historical_provenance_selection": {
                    "schema_version": 1,
                    "source_chronology_role": "bounded_committed_safe_packet_fallback",
                    "final_support_allowed": True,
                    "read_more_actionable": False,
                    "followthrough_actionable": True,
                    "diagnostic_only": False,
                    "raw_text_included": False,
                },
            }
        )
    if not fallback_rows:
        devlog_brief = failure_packet.get("devlog_brief") if isinstance(failure_packet, dict) else {}
        if isinstance(devlog_brief, dict) and devlog_brief:
            fallback_rows.append(
                {
                    "kind": "committed_safe_devlog_packet_fallback",
                    "packet_handle": "failure_analysis.devlog_brief",
                    "candidate_id": "devlog-safe-packet-fallback-brief-001",
                    "status": devlog_brief.get("status"),
                    "support_surfaces": ["failure_analysis.devlog_brief"],
                    "claim_boundaries": [
                        "committed-safe summary only",
                        "not a substitute for raw historical source windows when available",
                    ],
                    "final_support_candidate": True,
                    "fallback_reason": "no positive real-source JSONL target survived devlog source-quality gating",
                    "devlog_target_source_eligibility": {
                        "schema_version": 1,
                        "eligible": True,
                        "default_target_allowed": True,
                        "category": "bounded_committed_safe_packet_fallback",
                    },
                    "historical_provenance_selection": {
                        "schema_version": 1,
                        "source_chronology_role": "bounded_committed_safe_packet_fallback",
                        "final_support_allowed": True,
                        "read_more_actionable": False,
                        "followthrough_actionable": True,
                        "diagnostic_only": False,
                        "raw_text_included": False,
                    },
                }
            )
    return fallback_rows[:4]


def _devlog_source_artifact_audit_requested(query: str) -> bool:
    text = query.casefold()
    artifact_terms = (
        "current output",
        "current route",
        "current run",
        "current lane",
        "this output",
        "this route",
        "this run",
        "this lane",
        "prior answer",
        "previous answer",
        "last answer",
        "prior report",
        "previous report",
        "last report",
        "this report",
        "test",
        "tests",
        "fixture",
        "fixtures",
        "smoke",
        "smokes",
        "regression",
        "current output",
        "command output",
    )
    audit_terms = ("audit", "check", "inspect", "verify", "did it cite", "did the answer")
    return any(term in text for term in artifact_terms) and any(term in text for term in audit_terms)


def _devlog_read_is_invalid_default_target_artifact(read: dict, *, candidate: dict | None = None) -> bool:
    return _devlog_read_is_synthetic_or_test_artifact(
        read,
        candidate=candidate,
    ) or _devlog_read_is_current_generated_or_posthoc_artifact(read, candidate=candidate)


def _devlog_invalid_default_target_artifact_reason(read: dict, *, candidate: dict | None = None) -> str:
    if _devlog_read_is_synthetic_or_test_artifact(read, candidate=candidate):
        return "synthetic_test_smoke_artifact"
    return "current_generated_or_posthoc_artifact"


def _devlog_read_is_synthetic_or_test_artifact(read: dict, *, candidate: dict | None = None) -> bool:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    text = " ".join(
        str(value)
        for value in (
            read.get("kind"),
            read.get("target"),
            read.get("reason"),
            read.get("candidate_id"),
            read.get("source_category"),
            source_ref.get("path"),
            selection.get("source_chronology_role"),
            _devlog_candidate_marker_text(candidate),
        )
    ).casefold().replace("\\", "/")
    path_markers = (
        "/tests/",
        "tests/",
        "/test/",
        "/fixtures/",
        "fixtures/",
        "/fixture/",
        "/smoke/",
        "smoke/",
        "/regression/",
        "regression/",
    )
    text_markers = (
        "synthetic",
        "end-to-end smoke",
        "e2e smoke",
        "smoke output",
        "smoke test",
        "fixture output",
        "fixture jsonl",
        "regression fixture",
        "regression test",
        "golden output",
    )
    return any(marker in text for marker in path_markers + text_markers)


def _devlog_read_is_current_generated_or_posthoc_artifact(read: dict, *, candidate: dict | None = None) -> bool:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    source_ref = read.get("source_ref") if isinstance(read.get("source_ref"), dict) else {}
    text = re.sub(
        r"\s+",
        " ",
        " ".join(
            str(value)
            for value in (
                read.get("kind"),
                read.get("target"),
                read.get("reason"),
                read.get("candidate_id"),
                read.get("source_category"),
                source_ref.get("path"),
                source_ref.get("session"),
                selection.get("source_chronology_role"),
                selection.get("selection_reason"),
                _devlog_candidate_marker_text(candidate),
            )
        )
        .casefold()
        .replace("\\", "/")
        .replace("_", " "),
    ).strip()
    markers = (
        "/current/",
        "/current task/",
        "/current-task/",
        "/adjacent/",
        "/posthoc/",
        "/post-hoc/",
        "current-run",
        "current run",
        "same-loop",
        "same loop",
        "same-run",
        "same run",
        "same-lane",
        "same lane",
        "current task",
        "current route",
        "current lane",
        "ask-execute",
        "ask --execute",
        "jsonl-history-search ask",
        "jsonl history search ask",
        "devlog-source-reach",
        "devlog source reach",
        "pressure command",
        "pressure artifact",
        "command-output",
        "command output",
        "command echo",
        "output echo",
        "route echo",
        "route-echo",
        "tool output",
        "generated output",
        "source locator handoff",
        "source-locator-handoff",
        "source_locator_handoff",
        "report output",
        "report-output",
        "report_output",
        "process exited with code",
        "original token count",
        "prior report",
        "previous report",
        "last report",
        "post-hoc report",
        "post hoc report",
        "adjudication",
        "answer-key",
        "answer key",
        "prior-final",
        "prior final",
        "previous final",
    )
    return any(marker in text for marker in markers)


def _devlog_candidate_marker_text(candidate: dict | None) -> str:
    if not isinstance(candidate, dict):
        return ""
    source_ref = candidate.get("source_ref") if isinstance(candidate.get("source_ref"), dict) else {}
    return " ".join(
        str(value)
        for value in (
            candidate.get("candidate_id"),
            candidate.get("target"),
            candidate.get("path"),
            source_ref.get("path"),
            candidate.get("stage"),
            " ".join(str(item) for item in candidate.get("stages") or []),
            " ".join(str(item) for item in candidate.get("reason_codes") or []),
            " ".join(str(item) for item in candidate.get("artifacts") or []),
            " ".join(str(item) for item in candidate.get("tasks") or []),
            candidate.get("selection_reason"),
            candidate.get("snippet"),
        )
    )


def _devlog_read_is_diagnostic(read: dict) -> bool:
    selection = (
        read.get("historical_provenance_selection")
        if isinstance(read.get("historical_provenance_selection"), dict)
        else {}
    )
    if (
        read.get("diagnostic_only") is True
        or read.get("read_more_actionable") is False
        or selection.get("diagnostic_only") is True
    ):
        return True
    role = str(selection.get("source_chronology_role") or "")
    if role.endswith("_diagnostic") or "diagnostic" in role:
        return True
    text = " ".join(
        str(value)
        for value in (
            read.get("target"),
            read.get("reason"),
            read.get("candidate_id"),
            (read.get("source_ref") or {}).get("path") if isinstance(read.get("source_ref"), dict) else "",
        )
    ).casefold()
    diagnostic_markers = (
        "/current/",
        "/current_task/",
        "/adjacent/",
        "/posthoc/",
        "current-run",
        "current run",
        "same-loop",
        "same loop",
        "current task",
        "prior-final",
        "prior final",
        "previous final",
        "final answer",
        "adjudication",
        "answer-key",
        "answer key",
    )
    return any(marker in text for marker in diagnostic_markers)


def _build_semantic_debug_report_output_payload(
    args: argparse.Namespace,
    *,
    phase_emitter: AskExecutePhaseEmitter | None = None,
) -> dict:
    source_phase = phase_emitter.start("source_planning") if phase_emitter else None
    source_plan, intent, plan = payload_from_args(args)
    if phase_emitter and source_phase:
        phase_emitter.complete(source_phase)
    payload = build_semantic_debug_payload(
        args,
        source_plan=source_plan,
        intent=intent,
        plan=plan,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        mask_context=_mask_context_from_args(args),
        configuration_warnings=_configuration_warnings(source_plan),
        phase_emitter=phase_emitter,
    )
    output_profile_token = start_phase_profile("semantic_debug_output_report")
    rendering_phase = phase_emitter.start("rendering") if phase_emitter else None
    rendered = render(payload, "json")
    output_profile = finish_phase_profile(
        output_profile_token,
        stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
        report_bytes=len(rendered.encode("utf-8", errors="replace")),
        report_written=True,
    )
    _attach_retrieval_output_report_diagnostics(
        payload,
        report_key="semantic_debug",
        phase="semantic_debug_output_report",
        profile=output_profile,
        output_format="json",
        report_output=args.report_output,
        report_written=True,
    )
    report_rendered = render(payload, "json")
    if phase_emitter and rendering_phase:
        phase_emitter.complete(rendering_phase)
    report_phase = phase_emitter.start("report_writing") if phase_emitter else None
    Path(args.report_output).expanduser().write_text(report_rendered, encoding="utf-8")
    if phase_emitter and report_phase:
        phase_emitter.complete(report_phase)
    packet = payload["semantic_debug"]
    if _semantic_debug_is_scoped_no_evidence(packet):
        return _short_semantic_debug_no_evidence_report_written_payload(packet, args.report_output)
    return _short_semantic_debug_report_written_payload(packet, args.report_output)


def _build_semantic_debug_in_memory_payload(args: argparse.Namespace) -> dict:
    source_plan, intent, plan = payload_from_args(args)
    payload = build_semantic_debug_payload(
        args,
        source_plan=source_plan,
        intent=intent,
        plan=plan,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        mask_context=_mask_context_from_args(args),
        configuration_warnings=_configuration_warnings(source_plan),
    )
    packet = payload["semantic_debug"]
    packet["report_written"] = None
    packet["message"] = (
        "Semantic-debug completed in memory because the optional report destination was unavailable."
    )
    return {"semantic_debug": packet}


def _fielded_bm25_index_dir_from_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "fielded_bm25_ephemeral", False):
        return None
    if getattr(args, "fielded_bm25_storage", "file") == "memory":
        return None
    explicit = getattr(args, "fielded_bm25_index_dir", None)
    if explicit:
        return str(explicit)
    if getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE) not in {
        RETRIEVAL_MODE_FIELDED_BM25,
        RETRIEVAL_MODE_RRF_FUSION,
    }:
        return None
    return str(_default_fielded_bm25_index_dir())


def _default_fielded_bm25_index_dir() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return root / "jsonl-history-search" / "fielded-bm25"


def _rrf_fusion_materialization_dir_from_args(args: argparse.Namespace) -> str | None:
    if getattr(args, "rrf_fusion_storage", "file") == "memory":
        return None
    if getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE) != RETRIEVAL_MODE_RRF_FUSION:
        return None
    fielded_index_dir = _fielded_bm25_index_dir_from_args(args)
    root = Path(fielded_index_dir) if fielded_index_dir else _default_fielded_bm25_index_dir()
    return str(root / "rrf-materializations")


def _build_agent_find_report_output_payload(args: argparse.Namespace) -> dict:
    source_plan, intent, plan = payload_from_args(args)
    mask_context = _mask_context_from_args(args)
    result = run_retrieval_mode(
        retrieval_mode=getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE),
        plan=plan,
        intent=intent,
        default_tz=timezone_from_args(args.timezone, args.timezone_offset),
        cwd=source_plan["cwd"],
        mask_context=mask_context,
        use_rg_prefilter=args.rg_prefilter,
        fielded_bm25_index_dir=_fielded_bm25_index_dir_from_args(args),
        rrf_fusion_storage=getattr(args, "rrf_fusion_storage", "memory"),
        rrf_fusion_materialization_dir=_rrf_fusion_materialization_dir_from_args(args),
        query_plan_mode=getattr(args, "query_plan", QUERY_PLAN_MODE_AUTO),
        continuation_ledger=getattr(args, "continuation_ledger", False),
    )
    result["warnings"] = sorted(set(result["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
    manifest_report = (
        build_manifest_report(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
        )
        if args.include_manifest
        else None
    )
    payload = build_agent_find_payload(
        source_plan=source_plan,
        search_result=result,
        manifest_report=manifest_report,
        output_format="json",
        report_output=args.report_output,
        compact_provenance_handoff=getattr(args, "compact_provenance_handoff", False),
        evidence_context_window=getattr(args, "evidence_context_window", 0),
        no_evidence_mode=True,
        deep_audit_target_contract=getattr(args, "deep_audit_target_contract", None),
    )
    output_profile_token = start_phase_profile("agent_find_output_report")
    rendered = render(payload, "json")
    output_profile = finish_phase_profile(
        output_profile_token,
        stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
        report_bytes=len(rendered.encode("utf-8", errors="replace")),
        report_written=True,
    )
    _attach_retrieval_output_report_diagnostics(
        payload,
        report_key="agent_find",
        phase="agent_find_output_report",
        profile=output_profile,
        output_format="json",
        report_output=args.report_output,
        report_written=True,
    )
    Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
    if _agent_find_is_no_evidence_report(payload["agent_find"]):
        return _short_no_evidence_report_written_payload(payload, args.report_output)
    return _short_report_written_payload(payload, args.report_output)


def _build_repo_source_no_evidence_report_output_payload(args: argparse.Namespace) -> dict | None:
    if getattr(args, "root", None):
        return None
    compact = build_repo_source_no_evidence_payload(
        query=args.query,
        cwd=Path(args.cwd).expanduser().resolve(),
        report_output=args.report_output,
    )
    if compact is None:
        return None
    Path(args.report_output).expanduser().write_text(
        render({"repo_source_no_evidence": compact}, "json"),
        encoding="utf-8",
    )
    return {"repo_source_no_evidence": compact}


def _short_semantic_debug_report_written_payload(packet: dict, report_output: str) -> dict:
    payload = {
        "semantic_debug": {
            "schema_version": packet["schema_version"],
            "status": packet["status"],
            "query": packet["query"],
            "audit_depth": packet.get("audit_depth"),
            "question_family": packet["question_family"],
            "directional_history_reconstruction": packet.get(
                "directional_history_reconstruction"
            ),
            "report_written": str(Path(report_output).expanduser()),
            "candidate_count": len(packet["strongest_candidates"]),
            "event_count": packet["tool_action_proof"]["event_count"],
            "source_locator_handoff": packet["source_locator_handoff"],
            "source_handle_routing": packet.get("source_handle_routing"),
            "audit_continuation_workflow": packet.get("audit_continuation_workflow"),
            "in_depth_audit_followthrough": packet.get("in_depth_audit_followthrough"),
            "source_followthrough_plan": packet.get("source_followthrough_plan"),
            "provenance_direct_evidence_facet_ledger": packet.get(
                "provenance_direct_evidence_facet_ledger"
            ),
            "provenance_complete_proof_handoff": packet.get(
                "provenance_complete_proof_handoff"
            ),
            "investigation_state": packet["investigation_state"],
            "answer_ready_handoff": packet["answer_ready_handoff"],
            "agent_instruction": packet["agent_instruction"],
            "final_answer_source_use_contract": packet["final_answer_source_use_contract"],
            "historical_orchestration_provenance_final_support_plan": (
                packet.get("historical_orchestration_provenance_final_support_plan")
            ),
            "historical_orchestration_role_evidence_bundle": (
                packet.get("historical_orchestration_role_evidence_bundle")
            ),
            "historical_orchestration_role_coverage_source_plan": (
                packet.get("historical_orchestration_role_coverage_source_plan")
            ),
            "core_convo_first_read_plan": packet.get("core_convo_first_read_plan"),
            "action_proof_plan": packet.get("action_proof_plan"),
            "longitudinal_phase_source_plan": packet.get("longitudinal_phase_source_plan"),
            "expectation_mismatch_plan": packet.get("expectation_mismatch_plan"),
            "process_actual_use_final_support_read_plan": packet.get(
                "process_actual_use_final_support_read_plan"
            ),
            "process_actual_use_bucket_evidence_bundle": packet.get(
                "process_actual_use_bucket_evidence_bundle"
            ),
            "answer_source_discipline_summary": (
                packet["final_answer_source_use_contract"].get("answer_source_discipline")
            ),
            "claim_boundaries": packet["claim_boundaries"],
            "answer_readiness_summary": summarize_answer_readiness(packet["answer_readiness"]),
            "guided_audit_flow_summary": summarize_guided_audit_flow(packet["guided_audit_flow"]),
            "forensics_workflow_summary": packet.get("forensics_workflow", {}).get("summary"),
            "support_precision_summary": (
                packet.get("forensics_workflow", {}).get("summary", {}).get("support_summary")
            ),
            "decision_context_summary": {
                "primary_question_family": packet["decision_context"].get("primary_question_family"),
                "project_time_reconstruction_needed": packet["decision_context"].get(
                    "project_time_reconstruction_needed"
                ),
                "selected_routes": packet["decision_context"].get("selected_routes", []),
                "confidence": packet["decision_context"].get("confidence"),
            },
            "audit_plan_summary": {
                "watchlist": packet["audit_plan"].get("watchlist", [])[:8],
                "route_order": packet["audit_plan"].get("route_order", []),
                "resume_from": packet["audit_plan"].get("resume_from"),
                "cap_pressure_notes": packet["audit_plan"].get("cap_pressure_notes", []),
            },
            "next_reads": packet["next_reads"][:5],
            "message": "Full semantic-debug packet written to report_output; stdout is a compact handoff map.",
        }
    }
    if "compact_provenance_handoff" in packet:
        payload["semantic_debug"]["compact_provenance_handoff"] = packet["compact_provenance_handoff"]
    if "retrieval_diagnostics" in packet:
        payload["semantic_debug"]["retrieval_diagnostics"] = packet["retrieval_diagnostics"]
    if "retrieval_pressure" in packet:
        payload["semantic_debug"]["retrieval_pressure"] = packet["retrieval_pressure"]
    return payload


_ASK_EXECUTE_TASK_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11})-(\d{1,6})\b", re.IGNORECASE)
_ASK_EXECUTE_TASK_ID_MONTH_PREFIXES = {
    "JAN",
    "JANUARY",
    "FEB",
    "FEBRUARY",
    "MAR",
    "MARCH",
    "APR",
    "APRIL",
    "MAY",
    "JUN",
    "JUNE",
    "JUL",
    "JULY",
    "AUG",
    "AUGUST",
    "SEP",
    "SEPT",
    "SEPTEMBER",
    "OCT",
    "OCTOBER",
    "NOV",
    "NOVEMBER",
    "DEC",
    "DECEMBER",
}


def _cleanup_answer_operational_records(records: list[dict]) -> list[dict]:
    """Keep one bounded answer-leading ref for each cleanup-causality role."""
    selected: list[dict] = []
    causal_roles = (
        "report_only_audit",
        "deferred_handoff",
        "cleanup_learning_policy",
        "cleanup_execution",
        "private_retention",
    )
    for causal_role in causal_roles:
        match = next(
            (
                item
                for item in records
                if isinstance(item, dict) and item.get("causal_source_role") == causal_role
            ),
            None,
        )
        if match is not None:
            selected.append(match)
    return selected


def _compact_cleanup_facet_coverage(coverage: object) -> dict:
    if not isinstance(coverage, dict):
        return {}
    facets = coverage.get("facets") if isinstance(coverage.get("facets"), dict) else {}
    return {
        "complete": bool(coverage.get("complete")),
        "proven_count": coverage.get("proven_count"),
        "total_count": coverage.get("total_count"),
        "facets": {
            name: item.get("status")
            for name, item in facets.items()
            if isinstance(item, dict)
        },
    }


def _compact_cleanup_chronology_gate(gate: object) -> dict:
    if not isinstance(gate, dict):
        return {}
    return {
        "status": gate.get("status"),
        "current_workspace_scan": gate.get("current_workspace_scan"),
        "order": "original_chronology_then_current_workspace",
    }


def _compact_cleanup_current_state(summary: object) -> dict:
    if not isinstance(summary, dict):
        return {}
    return {
        key: summary.get(key)
        for key in (
            "phase",
            "source_role",
            "causal_proof_allowed",
            "registered_worktree_count",
            "linked_task_worktree_count",
            "dirty_worktree_count",
            "private_retention_worktree_count",
            "not_reachable_from_default_count",
            "clean_reachable_manual_closeout_debt_count",
            "default_branch",
        )
    }


def _compact_worktree_classifications(rows: object) -> list[dict]:
    if not isinstance(rows, list):
        return []
    compact: list[dict] = []
    for item in rows[:8]:
        if not isinstance(item, dict):
            continue
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        private = (
            item.get("private_artifacts")
            if isinstance(item.get("private_artifacts"), dict)
            else {}
        )
        compact.append(
            {
                "identifier": item.get("identifier"),
                "path": item.get("path"),
                "branch": item.get("branch"),
                "classification": item.get("classification"),
                "source_refs": (item.get("source_refs") or [])[:4],
                "status": {
                    "dirty": bool(status.get("dirty")),
                    "default_branch_reachability": status.get(
                        "default_branch_reachability"
                    ),
                },
                "private_artifacts": {
                    "has_private_artifacts": bool(
                        private.get("has_private_artifacts")
                    )
                },
            }
        )
    return compact


def _remembered_user_wording_missing_evidence_contract(*, compact: dict, route_family: str) -> dict:
    if route_family != "remembered_user_wording":
        return {}
    status = str(compact.get("status") or "")
    no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
    searched_scope = (
        compact.get("searched_scope")
        if isinstance(compact.get("searched_scope"), dict)
        else {}
    )
    if not searched_scope and no_evidence:
        searched_scope = _no_evidence_searched_scope_summary(no_evidence)
    scope_counts = (
        compact.get("scope_counts")
        if isinstance(compact.get("scope_counts"), dict)
        else {}
    )
    if not scope_counts and no_evidence:
        scope_counts = _no_evidence_scope_counts(no_evidence)
    if status not in {"no_evidence", "not_found_in_searched_scope"} and not no_evidence:
        return {}

    return {
        "answer_status": "fail-closed/missing-primary-user-history",
        "promotion_status": "remembered_user_wording_primary_history_unavailable",
        "promotion_reason": (
            "The bounded user-role history attempt produced no user-authored source ref. "
            "The receiving agent must report that proof gap instead of substituting project artifacts."
        ),
        "read": [],
        "diagnostics_after": [],
        "searched_scope": searched_scope,
        "no_evidence": no_evidence,
        "missing_evidence_stop_contract": {
            "answer_allowed": False,
            "missing_primary_evidence": "user-authored Codex/Claude history message with source ref or timestamp",
            "bounded_evidence_checked": {
                "searched_files": int(searched_scope.get("searched_files") or 0),
                "searched_bytes": int(searched_scope.get("searched_bytes") or 0),
                "roots": searched_scope.get("checked_roots") or [],
                "searched_roots": searched_scope.get("searched_roots") or [],
                "missing_roots": searched_scope.get("missing_roots") or [],
                "selected_root_count": int(scope_counts.get("selected_root_count") or 0),
                "searched_root_count": len(searched_scope.get("searched_roots") or []),
                "excluded_root_count": int(scope_counts.get("excluded_root_count") or 0),
                "missing_root_count": int(scope_counts.get("missing_root_count") or 0),
            },
            "excluded_as_user_statement_proof": [
                "TODO files",
                "task files",
                "reports and reviews",
                "project guidance and current-state documents",
                "assistant, tool, environment, and current-prompt echoes",
            ],
            "unblockers": [
                "a user-provided quote or approximate wording",
                "a user-provided timestamp or session anchor",
                "an available primary Codex or Claude history root",
            ],
            "broad_repo_search_allowed": False,
            "secondary_substitution_allowed": False,
        },
        "stop": (
            "Stop now and answer that the original user-authored statement was not found in the bounded "
            "available primary history. Do not run broad recursive repo search or open TODO/task/report/"
            "review/project guidance as substitute proof of what the user said."
        ),
        "only_continue_if": (
            "Continue only if the user supplies a quote/timestamp/session anchor or a real primary "
            "Codex/Claude history root becomes available."
        ),
        "saved_report": compact.get("report_written"),
    }


def _remembered_instruction_user_authored_reads(
    contract: dict,
    *,
    query: str = "",
    candidate_snippets: dict[str, str] | None = None,
    chronology_boundary: dict | None = None,
) -> list[dict]:
    reads: list[dict] = []
    selected_candidate_id = str(
        (chronology_boundary or {}).get("selected_prior_candidate_id") or ""
    )
    if not selected_candidate_id:
        return reads
    topic_terms = _remembered_user_wording_topic_terms(query) - {
        "change",
        "continue",
        "context",
        "using",
    }
    for item in contract.get("read") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("candidate_id") or "") != selected_candidate_id:
            continue
        if not _remembered_user_wording_recovery_ref_allowed(item):
            continue
        metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
        role_values = {
            str(metadata.get("top_role") or "").casefold(),
            str(metadata.get("message_role") or "").casefold(),
            str(item.get("role") or "").casefold(),
        }
        if metadata.get("is_user_authored") is True or role_values.intersection(
            {"user", "human", "human_authored_user_message"}
        ):
            candidate_text = str(
                (candidate_snippets or {}).get(str(item.get("candidate_id") or ""))
                or item.get("snippet")
                or ""
            )
            if candidate_text and _remembered_user_wording_prompt_echo_reasons(
                {"summary": candidate_text},
                query=query,
            ):
                continue
            if candidate_text and topic_terms:
                candidate_terms = {
                    _remembered_user_wording_stem(term)
                    for term in re.findall(
                        r"[a-z0-9][a-z0-9_-]{2,}",
                        candidate_text.casefold(),
                    )
                }
                if not topic_terms.intersection(candidate_terms):
                    continue
            reads.append(_conversation_message_record_read(item))
    return _dedupe_action_contract_rows(reads)[:_ASK_ANSWER_MAX_REFS]


def _remembered_instruction_chronology_boundary(
    *,
    query: str,
    candidates: list[dict],
) -> dict:
    query_text = _simple_normalize(query)
    if not query_text:
        return {"status": "missing_current_query_boundary"}

    normalized: list[dict] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        timestamp = parse_datetime(candidate.get("timestamp_utc"), timezone.utc)
        role = str(candidate.get("role") or "").casefold()
        snippet = str(candidate.get("snippet") or "")
        if not candidate_id or timestamp is None or role not in {"user", "human"}:
            continue
        normalized.append(
            {
                "candidate_id": candidate_id,
                "timestamp": timestamp.astimezone(timezone.utc),
                "snippet": snippet,
            }
        )

    current_query_boundaries = [
        candidate
        for candidate in normalized
        if query_text in _simple_normalize(candidate["snippet"])
    ]
    if len(current_query_boundaries) != 1:
        return {
            "status": (
                "missing_current_query_boundary"
                if not current_query_boundaries
                else "ambiguous_current_query_boundary"
            ),
            "current_query_boundary_count": len(current_query_boundaries),
        }

    boundary = current_query_boundaries[0]
    topic_terms = _remembered_user_wording_topic_terms(query) - {
        "change",
        "continue",
        "context",
        "using",
    }
    prior_candidates = []
    for candidate in normalized:
        if candidate["candidate_id"] == boundary["candidate_id"]:
            continue
        if candidate["timestamp"] >= boundary["timestamp"]:
            continue
        candidate_terms = {
            _remembered_user_wording_stem(term)
            for term in re.findall(
                r"[a-z0-9][a-z0-9_-]{2,}",
                candidate["snippet"].casefold(),
            )
        }
        if topic_terms and not topic_terms.intersection(candidate_terms):
            continue
        if _remembered_user_wording_prompt_echo_reasons(
            {"summary": candidate["snippet"]},
            query=query,
        ):
            continue
        prior_candidates.append(candidate)

    if len(prior_candidates) != 1:
        return {
            "status": (
                "missing_unique_prior_candidate"
                if not prior_candidates
                else "ambiguous_prior_candidates"
            ),
            "current_query_boundary_candidate_id": boundary["candidate_id"],
            "eligible_prior_candidate_count": len(prior_candidates),
        }
    return {
        "status": "unproven_original_authorship",
        "reason": (
            "Candidate-packet role, topic overlap, timestamp order, and source/session identity "
            "do not prove that the row is the original user instruction rather than evaluator "
            "or current-loop paraphrase material."
        ),
        "candidate_scope": "bounded_strongest_candidates_packet_only",
        "current_query_boundary_candidate_id": boundary["candidate_id"],
        "unproven_prior_candidate_id": prior_candidates[0]["candidate_id"],
        "eligible_prior_candidate_count": 1,
    }


def _remembered_instruction_diagnostic_reads(
    contract: dict,
    *,
    query: str = "",
    candidate_snippets: dict[str, str] | None = None,
    chronology_boundary: dict | None = None,
) -> list[dict]:
    diagnostics = list(contract.get("diagnostics_after") or [])
    for item in contract.get("read") or []:
        if not isinstance(item, dict):
            continue
        if _remembered_instruction_user_authored_reads(
            {"read": [item]},
            query=query,
            candidate_snippets=candidate_snippets,
            chronology_boundary=chronology_boundary,
        ):
            continue
        diagnostic = dict(item)
        diagnostic["diagnostic_only"] = True
        diagnostic["read_more_actionable"] = False
        diagnostic["followthrough_actionable"] = False
        diagnostic["final_support_allowed"] = False
        diagnostic["diagnostic_open_policy"] = (
            "not user-authored remembered-instruction proof; do not open by default"
        )
        diagnostics.append(diagnostic)
    return _dedupe_action_contract_rows(diagnostics)[:_ASK_ANSWER_MAX_REFS]


def _conversation_message_record_read(item: dict) -> dict:
    read = dict(item)
    record_action = next(
        (
            action
            for action in read.get("expansion_actions") or []
            if isinstance(action, dict) and action.get("mode") == "record" and action.get("command")
        ),
        None,
    )
    target = str(read.get("target") or "")
    command = record_action.get("command") if record_action else None
    if not command and target:
        command = (
            f"jsonl-history-search read-more {shlex.quote(target)} "
            '--mode record --cwd "$PWD" --format text'
        )
    if command:
        read["command"] = command
        read["primary_record_only"] = True
    return read


def _long_range_history_message_reads(contract: dict) -> list[dict]:
    allowed_roles = {
        "user",
        "human",
        "human_authored_user_message",
        "assistant",
        "agent",
        "assistant_or_agent_message",
        "agent_message",
    }
    reads: list[dict] = []
    for item in contract.get("read") or []:
        if not isinstance(item, dict) or not _remembered_user_wording_recovery_ref_allowed(item):
            continue
        metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
        roles = {
            str(metadata.get("top_role") or "").casefold(),
            str(metadata.get("message_role") or "").casefold(),
            str(item.get("role") or "").casefold(),
        }
        label = str(metadata.get("conversation_label") or "").casefold()
        if label in {"user_message", "assistant_message"} or roles.intersection(allowed_roles):
            reads.append(_conversation_message_record_read(item))
    return _dedupe_action_contract_rows(reads)[:_ASK_ANSWER_MAX_REFS]


def _long_range_history_diagnostic_reads(contract: dict, clean_reads: list[dict]) -> list[dict]:
    clean_targets = {
        str(item.get("target") or item.get("command") or "") for item in clean_reads
    }
    diagnostics = list(contract.get("diagnostics_after") or [])
    for item in contract.get("read") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("target") or item.get("command") or "") in clean_targets:
            continue
        diagnostic = dict(item)
        diagnostic.update(
            {
                "diagnostic_only": True,
                "read_more_actionable": False,
                "followthrough_actionable": False,
                "final_support_allowed": False,
                "diagnostic_open_policy": (
                    "tool/runtime/session metadata is not default old-context decision proof"
                ),
            }
        )
        diagnostic.pop("command", None)
        diagnostics.append(diagnostic)
    return _dedupe_action_contract_rows(diagnostics)[:_ASK_ANSWER_MAX_REFS]


def _continuation_first_history_contract(*, compact: dict, selected_route: dict) -> dict:
    route_family = str(selected_route.get("route_family") or "")
    handoff = compact.get("answer_ready_handoff") or {}
    if route_family == "long_range_history" and handoff.get("decision") == "missing_primary_evidence":
        guidance = handoff.get("source_action_guidance") or {}
        continuations = list(guidance.get("exact_jsonl_continuation_commands") or [])[:1]
        return {
            "answer_status": (
                "bounded-continuation-required" if continuations else "fail-closed/missing-primary-history"
            ),
            "promotion_status": "long_range_history_continuation_first",
            "promotion_reason": (
                "Primary pre-compaction history is still missing. Continue only through the emitted "
                "target-preserving JSONL command; otherwise return a searched-scope fail-closed packet."
            ),
            "read": continuations,
            "continuation_first_contract": {
                "status": "continuation_available" if continuations else "fail_closed_no_continuation",
                "ask_user_now": False,
                "searched_scope": compact.get("scope_boundary_summary") or compact.get("searched_scope") or {},
                "missing_facet": "primary pre-compaction conversation-history evidence",
            },
            "stop": (
                "Run the single emitted continuation, then answer from inspected primary refs. If no continuation "
                "is available, return the searched-scope/missing-facet fail-closed packet without asking the user."
            ),
            "only_continue_if": (
                "The emitted target-preserving JSONL continuation is present and primary old-context proof remains missing."
            ),
            "saved_report": compact.get("report_written"),
        }
    if route_family == "long_range_history":
        readiness = compact.get("answer_readiness_summary") or {}
        if readiness.get("status") == "no_evidence_scope_incomplete":
            guidance = (
                (handoff.get("source_action_guidance") if isinstance(handoff, dict) else None)
                or {}
            )
            continuations = list(guidance.get("exact_jsonl_continuation_commands") or [])[:1]
            return {
                "answer_status": (
                    "bounded-continuation-required"
                    if continuations
                    else "fail-closed/missing-primary-history"
                ),
                "promotion_status": "long_range_history_continuation_first",
                "promotion_reason": (
                    "Primary pre-compaction history is still missing. Continue only through an emitted "
                    "target-preserving JSONL command when available; otherwise return searched scope and "
                    "the missing prior-context facet."
                ),
                "read": continuations,
                "continuation_first_contract": {
                    "status": "continuation_available" if continuations else "fail_closed_no_continuation",
                    "ask_user_now": False,
                    "searched_scope": compact.get("searched_scope") or {},
                    "missing_facet": "primary pre-compaction conversation-history evidence",
                },
                "stop": (
                    "Answer only after primary old-context refs are opened. If no bounded continuation "
                    "is available, return the searched-scope/missing-facet fail-closed packet without "
                    "asking the user."
                ),
                "only_continue_if": (
                    "An emitted target-preserving JSONL continuation is present and primary old-context proof remains missing."
                ),
                "saved_report": compact.get("report_written"),
            }

    if route_family != "deep_history_audit":
        return {}
    followthrough = compact.get("in_depth_audit_followthrough") or {}
    if not followthrough.get("applies") or followthrough.get("answer_ready"):
        return {}
    disposition = followthrough.get("cap_hit_disposition") or {}
    continuation_packet = disposition.get("canonical_continuation_packet") or followthrough.get(
        "continuation_packet"
    )
    primary_reads = list(followthrough.get("ordered_primary_refs_to_open_first") or [])
    continuations = list(followthrough.get("continuation_commands") or [])[:1]
    if continuation_packet:
        return {
            "answer_status": "canonical-continuation-packet-required",
            "promotion_status": "deep_history_audit_continuation_packet",
            "promotion_reason": (
                "The capped broad-history audit is not answer-ready. Run every bounded step in the single "
                "canonical packet, then reassess its blockers before synthesizing anything."
            ),
            "read": [],
            "conditional_required": [],
            "canonical_continuation_packet": continuation_packet,
            "partial_evidence_rendering_allowed": False,
            "continuation_first_contract": {
                "status": "canonical_continuation_packet",
                "ask_user_now": False,
                "searched_scope": (followthrough.get("coverage_state") or {}).get("searched_scope") or {},
                "missing_facet": "complete target-matching deep-history coverage",
                "packet_id": continuation_packet.get("packet_id"),
                "step_count": len(continuation_packet.get("steps") or []),
            },
            "stop": (
                "Do not answer from individual step output. Run all packet steps in order; answer only if the "
                "combined coverage is complete, otherwise return the packet's remaining blocker fail closed."
            ),
            "only_continue_if": "The canonical packet still has an uncompleted step or named blocker.",
            "saved_report": compact.get("report_written"),
        }
    return {
        "answer_status": (
            "inspect-primary-then-bounded-continuation"
            if primary_reads
            else "bounded-continuation-required"
            if continuations
            else "fail-closed/incomplete-deep-history-audit"
        ),
        "promotion_status": "deep_history_audit_continuation_first",
        "promotion_reason": (
            "The broad-history audit is not answer-ready. Open target-matching primary refs first, then use the "
            "single emitted continuation when coverage or target persistence remains incomplete."
        ),
        "read": primary_reads or continuations,
        "conditional_required": continuations if primary_reads else [],
        "continuation_first_contract": {
            "status": "continuation_available" if continuations else "fail_closed_no_continuation",
            "ask_user_now": False,
            "searched_scope": (followthrough.get("coverage_state") or {}).get("searched_scope") or {},
            "missing_facet": "target-matching deep-history coverage",
        },
        "stop": (
            "Answer only after target-matching primary refs and required coverage are complete. If no bounded "
            "continuation exists, return the searched-scope/missing-facet fail-closed packet without asking the user."
        ),
        "only_continue_if": (
            "A listed primary read or the emitted target-preserving JSONL continuation remains required."
        ),
        "saved_report": compact.get("report_written"),
    }


def _answer_action_contract_from_executed_output(
    *,
    output_key: str,
    compact: dict,
    selected_route: dict,
    query: str = "",
    cwd: str = ".",
) -> dict:
    if output_key == "artifact_currentness":
        worktree = compact.get("worktree_currentness") or {}
        cleanup = worktree.get("cleanup_causality") or {}
        facet_coverage = cleanup.get("facet_coverage") or {}
        operational_records = _cleanup_answer_operational_records(
            cleanup.get("operational_records") or []
        )
        next_reads = list(compact.get("next_reads") or [])[:8]
        target_binding = cleanup.get("target_binding") or {}
        if target_binding:
            missing_target = target_binding.get("status") == "blocked_named_target_missing"
            return {
                "answer_status": "not-ready/needs-more-proof",
                "promotion_status": target_binding.get("status"),
                "promotion_reason": (
                    "Named cleanup target identity is preserved. Generic cleanup causality is "
                    "withheld until target-specific evidence supports a decisive classification."
                ),
                "read": next_reads,
                "then_use": {
                    "target_binding": target_binding,
                    "worktree_classifications": _compact_worktree_classifications(
                        worktree.get("classifications")
                    ),
                },
                "diagnostics_after": [],
                "excluded_evidence": (
                    [
                        "generic cleanup causality",
                        "task/report/review archaeology",
                        "tests, scripts, validators, fixtures, and answer keys",
                    ]
                    if missing_target
                    else []
                ),
                "source_role_order": worktree.get("source_role_order"),
                "source_roles_present": worktree.get("source_roles_present"),
                "stop": (
                    "After the listed exact Git checks, stop with current target absence and no "
                    "cleanup-causality answer; absence does not prove why cleanup did or did not happen."
                    if missing_target
                    else "Stop without a cleanup-causality answer when target-specific evidence remains inconclusive."
                ),
                "only_continue_if": (
                    "Continue only if the listed exact Git checks find the named target."
                    if missing_target
                    else target_binding.get("required_next_proof")
                ),
                "saved_report": compact.get("report_written"),
            }
        return {
            "answer_status": (
                "answer-ready-after-source-inspection"
                if compact.get("status") == "ok" and facet_coverage.get("complete")
                else "not-ready/needs-more-proof"
            ),
            "promotion_status": cleanup.get("debt_status") or compact.get("status"),
            "promotion_reason": (
                "Original cleanup chronology is answer-leading evidence. Current Git/worktree state is a "
                "separate later phase and cannot substitute for missing defer, preservation, or cleanup proof."
            ),
            "read": operational_records or next_reads,
            "then_use": {
                "cleanup_causality": {
                    "debt_status": cleanup.get("debt_status"),
                    "causal_summary": cleanup.get("causal_summary"),
                },
                "source_roles_present": worktree.get("source_roles_present"),
                "worktree_classifications": _compact_worktree_classifications(
                    worktree.get("classifications")
                ),
            },
            "diagnostics_after": (
                [
                    item
                    for item in next_reads
                    if str(item.get("phase") or "").startswith("current_state_after_chronology")
                ]
                if operational_records
                else []
            ),
            "source_role_order": worktree.get("source_role_order"),
            "source_roles_present": worktree.get("source_roles_present"),
            "stop": (
                "Stop after the operational records are inspected when debt_status and their "
                "source refs sufficiently explain why cleanup remained open or how it closed."
            ),
            "only_continue_if": (
                "Continue only if the operational records conflict, debt_status is missing, "
                "or a selected worktree still needs classification evidence."
            ),
            "saved_report": compact.get("report_written"),
        }
    if output_key == "status_anchor":
        source_tiers = compact.get("source_tiers") or {}
        answer_readiness = compact.get("answer_readiness") or {}
        latest_followthrough = _latest_status_followthrough_contract(compact)
        continuation_packet = compact.get("parent_thread_interval_continuation") or {}
        answer_allowed = bool(
            answer_readiness.get("scanner_coverage_answer_allowed", answer_readiness.get("answer_allowed"))
        )
        read_contract = list(latest_followthrough.get("required_primary_reads") or [])
        if not read_contract:
            read_contract = list(source_tiers.get("required_primary") or [])
        if not read_contract:
            read_contract = (compact.get("follow_through_plan") or {}).get("open_first") or compact.get("next_reads", [])[:4]
        return {
            "answer_status": (
                "canonical-continuation-packet-required"
                if continuation_packet
                else "follow-required-primary-refs-before-answer"
                if answer_allowed
                else "not-ready/needs-more-proof"
            ),
            "promotion_status": (
                "canonical_continuation_packet"
                if continuation_packet
                else "latest_status_navigation_only_until_required_primary_reads"
                if answer_allowed
                else compact.get("status")
            ),
            "promotion_reason": (
                "The latest-status route is executable, but a factual answer requires opened selected-anchor "
                "and post-anchor interval raw JSONL refs before any secondary task/report/git joins."
            ),
            "read": [] if continuation_packet else read_contract,
            "canonical_continuation_packet": continuation_packet or None,
            "partial_evidence_rendering_allowed": False if continuation_packet else None,
            "navigation_only": latest_followthrough.get("navigation_only"),
            "answer_from_report_allowed": False,
            "required_primary_windows_opened": False,
            "latest_status_followthrough_contract": latest_followthrough,
            "then_confirm": (compact.get("follow_through_plan") or {}).get("then_confirm") or [],
            "diagnostics_after": (compact.get("follow_through_plan") or {}).get("do_not_open_by_default") or [],
            "selected_anchor": compact.get("selected_anchor"),
            "anchor_decision": compact.get("anchor_decision"),
            "anchor_candidates": (compact.get("anchor_candidates") or [])[:8],
            "answer_readiness": answer_readiness,
            "answer_brief": [],
            "session_activity": {
                "status": (compact.get("session_activity") or {}).get("status"),
                "summary": (compact.get("session_activity") or {}).get("summary") or {},
            },
            "work_products": {"commits": []},
            "source_tiers": {
                "required_primary": (source_tiers.get("required_primary") or [])[:6],
                "required_secondary": (source_tiers.get("required_secondary") or [])[:6],
            },
            "clarification_request": None if continuation_packet else compact.get("clarification_request"),
            "parent_main_anchor_shortlist": compact.get("parent_main_anchor_shortlist"),
            "parent_thread_side_boundary": compact.get("parent_thread_side_boundary"),
            "follow_through_plan": compact.get("follow_through_plan"),
            "post_anchor_interval_reconstruction": latest_followthrough.get("post_anchor_interval_reconstruction"),
            "evidence_modes": (compact.get("evidence_contract") or {}).get("source_modes"),
            "source_role_labels": (compact.get("evidence_contract") or {}).get("source_role_labels"),
            "stop": latest_followthrough.get("stop")
            or (compact.get("follow_through_plan") or {}).get("stop_condition")
            or selected_route.get("stop_guidance"),
            "only_continue_if": selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
        }
    if output_key == "coordinator_context":
        active_status = (
            compact.get("active_status_front_door")
            if isinstance(compact.get("active_status_front_door"), dict)
            else {}
        )
        active_status_route = selected_route.get("route_family") == "active_status_front_door"
        return {
            "answer_status": (
                "active-status-front-door-ready"
                if active_status_route and active_status.get("status") == "ready"
                else "context-package-ready-after-next-reads"
            ),
            "promotion_status": "committed_safe_context_package",
            "promotion_reason": (
                "The active-status route can answer current repo state from whitelisted front-door files."
                if active_status_route
                else (
                    "The coordinator-context route is executable and committed-safe, "
                    "but receiving agents should inspect required next reads before "
                    "turning it into a substantive handoff or next action."
                )
            ),
            "read": compact.get("required_next_reads") or [],
            "then_use": {
                "active_status_front_door": active_status,
                "current_state": compact.get("current_state"),
                "active_task_handles": compact.get("active_task_handles"),
                "active_task_reconciliation": compact.get("active_task_reconciliation"),
                "why_goals_guiding_principles": compact.get("why_goals_guiding_principles"),
                "learnings_failure_modes": compact.get("learnings_failure_modes"),
            },
            "diagnostics_after": compact.get("optional_diagnostics") or [],
            "claim_boundaries": compact.get("claim_boundaries") or [],
            "privacy_flags": compact.get("source_policy"),
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
        }
    if output_key == "devlog_failure_analysis":
        target_reads = list(compact.get("target_historical_source_window_reads") or [])
        core_plan = compact.get("core_convo_first_read_plan") or {}
        chronology_source_reach = compact.get("chronology_source_reach") or {}
        repo_evidence = compact.get("repo_phase_category_evidence") or {}
        failure_analysis = compact.get("failure_analysis") or {}
        copyable_reads = chronology_source_reach.get("copyable_next_reads") or []
        return {
            "answer_status": chronology_source_reach.get("answer_status")
            or "fail_closed_missing_chronology_roles",
            "promotion_status": "unpromoted_devlog_failure_analysis_source_reach_package",
            "promotion_reason": (
                "The devlog/failure-analysis route is a source-reach package, not final prose; "
                "open role-complete target historical source-window reads before answering."
            ),
            "core_convo_first_read_plan": _compact_devlog_core_plan_for_stdout(core_plan),
            "chronology_source_reach": _compact_devlog_chronology_for_stdout(
                chronology_source_reach,
                include_reads=False,
            ),
            "read": copyable_reads or core_plan.get("read") or target_reads,
            "then_use": {
                "failure_analysis": _compact_devlog_failure_analysis_for_stdout(failure_analysis),
                "repo_phase_category_evidence": _compact_devlog_repo_evidence_for_stdout(repo_evidence),
            },
            "diagnostics_after": {
                "count": len(
                    core_plan.get("diagnostics_after")
                    or list(compact.get("diagnostic_source_window_reads") or [])
                ),
                "default_actionable": False,
                "source": "report_output diagnostic_source_window_reads",
            },
            "diagnostic_policy": compact.get("diagnostic_policy"),
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": (compact.get("answer_contract") or {}).get("only_continue_if")
            or selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
            "linked_reports": compact.get("linked_reports"),
        }
    if output_key == "semantic_debug":
        directional = compact.get("directional_history_reconstruction") or {}
        if directional.get("applies") and selected_route.get("route_family") in {
            "causal_history",
            "topic_history",
        }:
            return {
                "answer_status": directional.get("status"),
                "promotion_status": "directional_history_reads_required",
                "promotion_reason": (
                    "The selected history route requires its ordered directional source reads before synthesis."
                ),
                "directional_history_reconstruction": directional,
                "read": directional.get("stages") or [],
                "answer_gate": directional.get("answer_gate"),
                "stop": directional.get("stop_rule"),
                "only_continue_if": "A named required causal role or before/during/after phase remains missing.",
                "saved_report": compact.get("report_written"),
            }
        handoff = compact.get("answer_ready_handoff") or {}
        readiness_summary = compact.get("answer_readiness_summary") or {}
        longitudinal_phase_source_plan = compact.get("longitudinal_phase_source_plan") or {}
        source_followthrough_plan = compact.get("source_followthrough_plan") or {}
        source_followthrough_required = _source_followthrough_contract_reads(source_followthrough_plan)
        source_followthrough_conditional = _dedupe_action_contract_rows(
            [
                item
                for item in source_followthrough_plan.get("conditional_required") or []
                if isinstance(item, dict)
            ]
        )
        source_followthrough_diagnostics = list(source_followthrough_plan.get("optional_diagnostic") or [])[:5]
        provenance_ledger = compact.get("provenance_direct_evidence_facet_ledger") or {}
        provenance_handoff = compact.get("provenance_complete_proof_handoff") or {}
        provenance_applies = bool(provenance_ledger.get("applies") or provenance_handoff.get("applies"))
        selected_provenance_reads = list(provenance_handoff.get("selected_direct_reads") or [])
        continuation_provenance_reads = list(provenance_handoff.get("continuation_commands") or [])[:1]
        if provenance_handoff.get("active"):
            provenance_reads = selected_provenance_reads
        else:
            has_selected_original_request = any(
                isinstance(read, dict)
                and read.get("proof_kind") == "original_user_request_direct_human"
                and read.get("direct_human_request") is True
                for read in selected_provenance_reads
            )
            provenance_read_inputs = (
                selected_provenance_reads + continuation_provenance_reads
                if has_selected_original_request
                else continuation_provenance_reads
            )
            provenance_reads = []
            seen_provenance_targets: set[str] = set()
            for read in provenance_read_inputs:
                if not isinstance(read, dict):
                    continue
                target = str(read.get("target") or read.get("command") or "")
                if target and target in seen_provenance_targets:
                    continue
                if target:
                    seen_provenance_targets.add(target)
                provenance_reads.append(read)
        core_plan = compact.get("core_convo_first_read_plan") or handoff.get("core_convo_first_read_plan") or {}
        orchestration_bundle = compact.get("historical_orchestration_role_evidence_bundle") or {}
        orchestration_plan = compact.get("historical_orchestration_provenance_final_support_plan") or {}
        orchestration_recovery_reads = (
            orchestration_bundle.get("missing_role_recovery_refs")
            or orchestration_plan.get("missing_role_recovery_refs")
            or orchestration_plan.get("bounded_non_final_required_reads")
            or []
        )
        orchestration_reads = (
            orchestration_bundle.get("required_role_reads")
            if orchestration_bundle.get("status") == "answer_ready"
            else orchestration_recovery_reads
            or (
                orchestration_plan.get("primary_historical_final_support_reads")
                if orchestration_plan.get("status") == "answer_ready"
                else orchestration_plan.get("missing_role_recovery_refs")
                or orchestration_plan.get("bounded_non_final_required_reads")
            )
        )
        if not orchestration_reads:
            orchestration_reads = (
                orchestration_plan.get("primary_historical_final_support_reads")
                if orchestration_plan.get("status") == "answer_ready"
                else orchestration_plan.get("missing_role_recovery_refs")
                or orchestration_plan.get("bounded_non_final_required_reads")
            )
        internal_status = (
            handoff.get("answer_readiness_status")
            or compact.get("answer_readiness_summary", {}).get("status")
            or compact.get("status")
        )
        route_family = str(selected_route.get("route_family") or "")
        remembered_missing = _remembered_user_wording_missing_evidence_contract(
            compact=compact,
            route_family=route_family,
        )
        if remembered_missing:
            return remembered_missing
        continuation_first = _continuation_first_history_contract(
            compact=compact,
            selected_route=selected_route,
        )
        if continuation_first:
            return continuation_first
        expectation_mismatch_plan = compact.get("expectation_mismatch_plan") or {}
        target_proof_handoff = _semantic_debug_target_proof_handoff(
            compact=compact,
            route_family=route_family,
            query=query,
        )
        merge_artifact_handoff = _named_task_merge_provenance_execute_artifact_handoff(
            query=query,
            cwd=cwd,
            route_family=route_family,
            cli_prefix=(selected_route.get("command_resolution") or {}).get("command_prefix"),
        )
        raw_worker_chronology_handoff = _raw_worker_chronology_execute_handoff(
            query=query,
            route_family=route_family,
        )
        process_actual_use_plan = (
            compact.get("process_actual_use_final_support_read_plan")
            if route_family == "process_actual_use"
            and isinstance(compact.get("process_actual_use_final_support_read_plan"), dict)
            else {}
        )
        process_actual_use_required_reads = (
            list(process_actual_use_plan.get("required_primary_raw_final_support_reads") or [])
            if process_actual_use_plan
            else []
        )
        process_actual_use_secondary_reads = (
            list(process_actual_use_plan.get("secondary_leads") or [])
            if process_actual_use_plan
            else []
        )
        process_actual_use_diagnostic_reads = (
            list(process_actual_use_plan.get("post_hoc_answer_key_candidates") or [])
            + list(process_actual_use_plan.get("diagnostic_only_candidates") or [])
            if process_actual_use_plan
            else []
        )
        readiness_gate = _semantic_debug_readiness_source_plan_gate(
            query=query,
            route_family=route_family,
            answer_readiness_summary=readiness_summary,
            answer_ready_handoff=handoff,
            longitudinal_phase_source_plan=longitudinal_phase_source_plan,
            source_followthrough_plan=source_followthrough_plan,
            selected_route=selected_route,
        )
        if readiness_gate.get("blocked"):
            return readiness_gate["contract"]
        prefer_named_task_answer = _named_task_clean_answer_surface_wanted(
            query=query,
            route_family=route_family,
        )
        named_task_action_proof = _named_task_action_proof_handoff(
            query=query,
            cwd=cwd,
            route_family=route_family,
            target_proof_available=bool(target_proof_handoff),
            allow_current_proof_with_target=prefer_named_task_answer or bool(merge_artifact_handoff),
        )
        if raw_worker_chronology_handoff or merge_artifact_handoff:
            named_task_action_proof = {}
        if (
            target_proof_handoff
            and prefer_named_task_answer
            and named_task_action_proof.get("status") == "current_named_task_proof_refs_missing"
        ):
            named_task_action_proof = {}
        route_first_reads = (
            provenance_reads
            if provenance_applies
            else source_followthrough_required
            if route_family == "orchestration_provenance" and source_followthrough_required
            else orchestration_reads
            if route_family == "orchestration_provenance" and orchestration_reads
            else expectation_mismatch_plan.get("read")
            if route_family == "expectation_mismatch" and expectation_mismatch_plan
            else process_actual_use_required_reads
            if route_family == "process_actual_use" and process_actual_use_required_reads
            else raw_worker_chronology_handoff.get("read")
            if raw_worker_chronology_handoff
            else merge_artifact_handoff.get("read")
            if merge_artifact_handoff
            else named_task_action_proof.get("read")
            if named_task_action_proof
            else target_proof_handoff.get("required_primary_proof")
            if target_proof_handoff
            else core_plan.get("read")
        )
        answer_status = _unpromoted_semantic_debug_answer_status(internal_status)
        promotion_status = "unpromoted_semantic_debug_execution"
        promotion_reason = (
            "ask --execute semantic-debug remains an execution convenience; promoted scoped answers "
            "are limited to bounded_no_evidence routes executed through no-evidence-report."
        )
        source_inspection_boundary = None
        if provenance_applies:
            if provenance_handoff.get("active"):
                answer_status = "answer-ready-after-source-inspection"
                promotion_status = "semantic_debug_provenance_direct_proof_ready_after_source_inspection"
                promotion_reason = (
                    "ask --execute semantic-debug selected direct-human original-request proof plus correlated "
                    "action and verification proof. Open only those bounded reads before answering."
                )
                source_inspection_boundary = {
                    "decision": "answer_after_selected_direct_proof",
                    "answer_allowed": True,
                    "inspect_required_primary_proof_before_answer": True,
                    "required_primary_proof_source": (
                        "direct_human_original_request_and_correlated_action_events"
                    ),
                    "missing_facets": [],
                    "diagnostics_default_final_support": False,
                }
            else:
                missing_facets = list(
                    provenance_ledger.get("missing_required_facets")
                    or provenance_handoff.get("missing_required_facets")
                    or []
                )
                answer_status = "not-ready/missing-direct-provenance-facets"
                promotion_status = "semantic_debug_provenance_direct_proof_fail_closed"
                promotion_reason = (
                    "The source-session provenance ledger is incomplete. Correlated action rows cannot "
                    "substitute for a missing direct original user request."
                )
                source_inspection_boundary = {
                    "decision": "fail_closed_missing_direct_provenance_facets",
                    "answer_allowed": False,
                    "inspect_required_primary_proof_before_answer": True,
                    "required_primary_proof_source": (
                        "direct_human_original_request_and_correlated_action_events"
                    ),
                    "missing_facets": missing_facets,
                    "secondary_substitution_allowed": False,
                    "diagnostics_default_final_support": False,
                }
        elif raw_worker_chronology_handoff:
            answer_status = "not-ready/missing-raw-worker-chronology"
            promotion_status = "semantic_debug_raw_worker_chronology_gate_fail_closed"
            promotion_reason = (
                "The prompt explicitly restricts proof to raw worker chronology; task, review, and git "
                "artifacts are not sufficient. Recover same-task raw worker/session chronology before answering."
            )
            source_inspection_boundary = {
                "decision": "recover_raw_worker_chronology",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "raw_worker_session_chronology",
                "missing_roles": raw_worker_chronology_handoff.get("missing_roles") or [],
                "artifact_proof_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif (
            merge_artifact_handoff.get("status")
            == "completed_task_merge_artifact_refs_available_source_session_missing"
        ):
            answer_status = "not-ready/missing-source-session-proof"
            promotion_status = "semantic_debug_completed_task_merge_only_partial_source_session_unopened"
            promotion_reason = (
                "ask --execute semantic-debug found completed-task task/review/true-merge evidence, but the "
                "explicitly requested source-session facet is still missing. Report merge-only partial evidence "
                "and do not promote artifact prose or unopened private handles as source-session proof."
            )
            source_inspection_boundary = {
                "decision": "report_merge_only_partial_and_recover_source_session",
                "answer_allowed": False,
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "same_task_opened_raw_source_session",
                "missing_roles": ["source_session_proof"],
                "unopened_private_handles_are_leads_only": True,
                "diagnostics_default_final_support": False,
            }
        elif merge_artifact_handoff.get("status") == "completed_task_merge_artifact_refs_available":
            answer_status = "answer-ready-after-source-inspection"
            promotion_status = "semantic_debug_completed_task_merge_artifact_proof_ready_after_source_inspection"
            promotion_reason = (
                "ask --execute semantic-debug found completed-task task/review/git integration-or-merge proof refs; "
                "inspect those artifact refs before answering. Boundary: this is artifact provenance, "
                "not raw worker/session chronology."
            )
            source_inspection_boundary = {
                "decision": "answer_after_source_inspection",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "completed_task_task_review_git_merge_refs",
                "raw_worker_chronology_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif merge_artifact_handoff.get("status") == "completed_task_merge_artifact_refs_missing":
            answer_status = "not-ready/missing-completed-task-merge-artifact-proof"
            promotion_status = "semantic_debug_completed_task_merge_artifact_proof_missing"
            promotion_reason = (
                "A completed-task merge provenance prompt needs same-task task, review, and git integration/merge "
                "artifact refs; do not answer from unrelated raw/session diagnostics."
            )
            source_inspection_boundary = {
                "decision": "recover_completed_task_merge_artifact_proof",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "missing_completed_task_task_review_git_merge_refs",
                "diagnostics_default_final_support": False,
            }
        elif named_task_action_proof.get("status") in {
            "current_named_task_proof_refs_available",
            "current_named_task_stopped_episode_proof_refs_available",
        }:
            answer_status = "answer-ready-after-source-inspection"
            promotion_status = "semantic_debug_current_named_task_proof_ready_after_source_inspection"
            required_primary_proof_source = "current_named_task_review_commit_refs"
            if named_task_action_proof.get("status") == "current_named_task_stopped_episode_proof_refs_available":
                promotion_status = "semantic_debug_current_named_task_stopped_episode_proof_ready_after_source_inspection"
                required_primary_proof_source = "stable_task_state_report_refs"
            promotion_reason = (
                "ask --execute semantic-debug found current named task/review/commit proof refs; "
                "inspect those refs before answering and use stale/session diagnostics only for named gaps."
            )
            if named_task_action_proof.get("status") == "current_named_task_stopped_episode_proof_refs_available":
                promotion_reason = (
                    "ask --execute semantic-debug found stable local task/state/report proof for a named "
                    "stopped-before-implementation episode; inspect those refs before answering and keep "
                    "current-run transcript echoes out of final support."
                )
            source_inspection_boundary = {
                "decision": "answer_after_source_inspection",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": required_primary_proof_source,
                "diagnostics_default_final_support": False,
            }
        elif named_task_action_proof.get("status") == "current_named_task_proof_refs_missing":
            answer_status = "not-ready/missing-current-named-task-proof"
            promotion_status = "semantic_debug_named_task_proof_fail_closed"
            promotion_reason = (
                "A named task action-proof prompt needs current task/review/commit proof or explicit raw "
                "worker/reviewer/tool proof for that same task; do not answer from stale or unrelated diagnostics."
            )
            source_inspection_boundary = {
                "decision": "recover_missing_named_task_proof",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "missing_current_named_task_review_commit_refs",
                "diagnostics_default_final_support": False,
            }
        elif target_proof_handoff.get("status") == "target_primary_proof_refs_missing":
            answer_status = "not-ready/action-proof-target-identity-missing"
            promotion_status = "semantic_debug_action_proof_target_identity_fail_closed"
            promotion_reason = (
                "The action-proof plan has no primary proof refs because the target episode is not established. "
                "Do not promote generic current, evaluator, reviewer, benchmark, or diagnostic refs."
            )
            source_inspection_boundary = {
                "decision": "recover_action_proof_target_identity",
                "answer_allowed": False,
                "inspect_required_primary_proof_before_answer": False,
                "required_primary_proof_source": "explicit_target_historical_raw_action_source",
                "secondary_context_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif target_proof_handoff.get("status") == "target_primary_proof_refs_available":
            answer_status = "answer-ready-after-source-inspection"
            promotion_status = "semantic_debug_target_proof_ready_after_source_inspection"
            promotion_reason = (
                "ask --execute semantic-debug surfaced target raw/source refs as required primary proof; "
                "inspect them before answering and keep secondary artifacts or diagnostics out of final support "
                "unless a supported facet remains missing."
            )
            source_inspection_boundary = {
                "decision": "answer_after_source_inspection",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "target_historical_raw_refs",
                "secondary_context_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif target_proof_handoff.get("status") == "target_primary_proof_refs_mismatch":
            answer_status = "not-ready/target-source-role-mismatch"
            promotion_status = "semantic_debug_target_episode_source_role_gate_fail_closed"
            promotion_reason = (
                "ask --execute semantic-debug found raw/source refs, but they did not match the named "
                "target episode/entity requested by the query. Keep them as diagnostics and recover "
                "same-target primary proof before answering."
            )
            source_inspection_boundary = {
                "decision": "recover_same_target_primary_proof",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "same_target_historical_raw_refs",
                "missing_target_episode_ids": target_proof_handoff.get("required_target_ids") or [],
                "secondary_context_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif route_family == "process_actual_use" and process_actual_use_required_reads:
            answer_status = "answer-ready-after-source-inspection"
            promotion_status = "semantic_debug_process_actual_use_bucket_proof_ready_after_source_inspection"
            promotion_reason = (
                "ask --execute semantic-debug surfaced process actual-use primary raw final-support reads; "
                "inspect those reads before answering and keep artifact existence, task/review/report diffs, "
                "prior final prose, and current-run tool output out of final behavior proof."
            )
            source_inspection_boundary = {
                "decision": "answer_after_source_inspection",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "process_actual_use_primary_raw_final_support_reads",
                "secondary_context_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        elif route_family == "process_actual_use":
            answer_status = "not-ready/needs-more-proof"
            promotion_status = "semantic_debug_process_actual_use_primary_bucket_proof_missing"
            promotion_reason = (
                "Process actual-use answers need primary raw timing or reviewer-enforcement evidence for "
                "consulted-before-planning and behavior/scope/claim-boundary buckets; secondary artifacts, "
                "task/report diffs, prior final prose, and same-run tool output are not enough."
            )
            source_inspection_boundary = {
                "decision": "recover_process_actual_use_primary_bucket_proof",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "missing_process_actual_use_primary_raw_final_support_reads",
                "secondary_context_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
        contract = {
            "answer_status": answer_status,
            "promotion_status": promotion_status,
            "promotion_reason": promotion_reason,
            "internal_answer_status": internal_status,
            "core_convo_first_read_plan": core_plan,
            "action_proof_plan": (
                compact.get("action_proof_plan")
                if route_family in {
                    "action_proof",
                    "semantic_debug",
                    "provenance",
                    "source_window_semantic_debug",
                }
                and isinstance(compact.get("action_proof_plan"), dict)
                else None
            ),
            "expectation_mismatch_plan": expectation_mismatch_plan if route_family == "expectation_mismatch" else None,
            "process_actual_use_final_support_read_plan": (
                process_actual_use_plan if route_family == "process_actual_use" else None
            ),
            "read": route_first_reads
            or orchestration_reads
            or expectation_mismatch_plan.get("read")
            or (
                process_actual_use_secondary_reads + process_actual_use_diagnostic_reads
                if route_family == "process_actual_use"
                else []
            )
            or core_plan.get("read")
            or source_followthrough_required
            or handoff.get("primary_source_followthrough")
            or compact.get("source_locator_handoff", {}).get("next_reads")
            or compact.get("next_reads")
            or [],
            "diagnostics_after": (
                expectation_mismatch_plan.get("diagnostics_after")
                if route_family == "expectation_mismatch" and expectation_mismatch_plan
                else process_actual_use_secondary_reads + process_actual_use_diagnostic_reads
                if route_family == "process_actual_use"
                else core_plan.get("diagnostics_after")
            )
            or source_followthrough_diagnostics
            or [],
            "diagnostic_policy": (
                "expectation mismatch diagnostics are default non-actionable until expected/observed/divergence reads leave a named gap"
                if route_family == "expectation_mismatch" and expectation_mismatch_plan
                else None
            ),
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": (
                raw_worker_chronology_handoff.get("only_continue_if")
                if raw_worker_chronology_handoff
                else merge_artifact_handoff.get("only_continue_if")
                if merge_artifact_handoff
                else named_task_action_proof.get("only_continue_if")
                if named_task_action_proof
                else None
            )
            or (
                (handoff.get("primary_source_followthrough") or {}).get("only_continue_if")
                or source_followthrough_plan.get("only_continue_if")
                or compact.get("no_evidence", {}).get("read_more_policy")
                or selected_route.get("manual_search_guidance")
            ),
            "saved_report": compact.get("report_written"),
        }
        reviewed_provenance = (
            compact.get("final_answer_source_use_contract") or {}
        ).get("required_final_provenance") or {}
        if reviewed_provenance.get("applies"):
            contract["required_final_provenance"] = reviewed_provenance
        if source_followthrough_plan:
            contract["source_followthrough_plan"] = {
                "status": source_followthrough_plan.get("status"),
                "counts": source_followthrough_plan.get("counts"),
                "stop_condition": source_followthrough_plan.get("stop_condition"),
                "default_read_policy": source_followthrough_plan.get("default_read_policy"),
            }
            contract["stop"] = source_followthrough_plan.get("stop_condition") or contract["stop"]
        target_conditional_reads = list(
            target_proof_handoff.get("conditional_primary_after_source_inspection") or []
        )
        if route_family in {"action_proof", "source_window_semantic_debug"} and (
            target_conditional_reads or source_followthrough_conditional
        ):
            contract["conditional_required"] = _dedupe_action_contract_rows(
                target_conditional_reads + source_followthrough_conditional
            )
        if route_family == "orchestration_provenance" and source_followthrough_required:
            contract["conditional_required"] = (
                source_followthrough_conditional
                or _dedupe_action_contract_rows(list(orchestration_recovery_reads))[:_ASK_ANSWER_MAX_REFS]
            )
        if provenance_applies:
            contract["provenance_direct_evidence_facet_ledger"] = provenance_ledger
            contract["provenance_complete_proof_handoff"] = provenance_handoff
            contract["stop"] = provenance_handoff.get("stop_instruction") or contract["stop"]
        if source_inspection_boundary:
            contract["source_inspection_boundary"] = source_inspection_boundary
        if target_proof_handoff:
            contract["target_proof_handoff"] = target_proof_handoff
            if (
                target_proof_handoff.get("status")
                in {"target_primary_proof_refs_mismatch", "target_primary_proof_refs_missing"}
                and not merge_artifact_handoff
                and not raw_worker_chronology_handoff
                and not named_task_action_proof
            ):
                contract["read"] = target_proof_handoff.get("read") or []
                if target_proof_handoff.get("only_continue_if"):
                    contract["only_continue_if"] = (
                        target_proof_handoff.get("only_continue_if") or []
                    )
            contract["diagnostics_after"] = (
                target_proof_handoff.get("secondary_context_after_source_inspection")
                or []
            ) + (target_proof_handoff.get("diagnostics_after") or [])[:4]
            if (
                target_proof_handoff.get("status") == "target_primary_proof_refs_available"
                and answer_status == "answer-ready-after-source-inspection"
                and not merge_artifact_handoff
                and not raw_worker_chronology_handoff
                and not named_task_action_proof
            ):
                selected_primary = list(target_proof_handoff.get("required_primary_proof") or [])
                contract["read"] = selected_primary
                contract["single_flight_contract"] = _semantic_debug_target_single_flight_contract(
                    selected_primary=selected_primary,
                    secondary_context=target_proof_handoff.get(
                        "secondary_context_after_source_inspection"
                    )
                    or [],
                    diagnostics=target_proof_handoff.get("diagnostics_after") or [],
                    stop=contract.get("stop"),
                    suppress_broad_fallback_labels=bool(provenance_handoff.get("active")),
                )
                contract["only_continue_if"] = contract["single_flight_contract"]["continue_only_if"]
        if merge_artifact_handoff:
            contract["named_task_merge_provenance_handoff"] = merge_artifact_handoff
            contract["stop"] = merge_artifact_handoff.get("stop") or contract["stop"]
            contract["diagnostics_after"] = (
                merge_artifact_handoff.get("diagnostics_after")
                or contract.get("diagnostics_after")
                or []
            )
        if raw_worker_chronology_handoff:
            contract["raw_worker_chronology_handoff"] = raw_worker_chronology_handoff
            contract["read"] = raw_worker_chronology_handoff.get("read") or []
            contract["stop"] = raw_worker_chronology_handoff.get("stop") or contract["stop"]
            contract["diagnostics_after"] = (
                raw_worker_chronology_handoff.get("diagnostics_after")
                or contract.get("diagnostics_after")
                or []
            )
        if named_task_action_proof:
            contract["named_task_action_proof_handoff"] = named_task_action_proof
            contract["diagnostics_after"] = (
                named_task_action_proof.get("diagnostics_after")
                or contract.get("diagnostics_after")
                or []
            )
            contract["stop"] = named_task_action_proof.get("stop") or contract["stop"]
        if provenance_ledger.get("applies"):
            contract["read"] = provenance_reads
            contract["diagnostics_after"] = []
            contract["stop"] = provenance_handoff.get("stop_instruction") or contract["stop"]
            contract.pop("only_continue_if", None)
        if route_family == "long_range_history":
            message_reads = _long_range_history_message_reads(contract)
            contract["diagnostics_after"] = _long_range_history_diagnostic_reads(
                contract, message_reads
            )
            contract["read"] = message_reads
            contract["source_inspection_boundary"] = {
                "decision": (
                    "answer_after_conversation_message_inspection"
                    if message_reads
                    else "fail_closed_missing_conversation_message"
                ),
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "user_or_assistant_history_record",
                "tool_runtime_session_echoes_default_final_support": False,
                "diagnostics_default_final_support": False,
            }
            if not message_reads:
                contract["answer_status"] = "fail-closed/missing-primary-history"
                contract["promotion_status"] = "long_range_history_message_source_missing"
                contract["promotion_reason"] = (
                    "The old-context route surfaced only tool/runtime/session rows. They remain "
                    "diagnostic and are not default reads for a remembered decision."
                )
                contract["stop"] = (
                    "Return the searched-scope/missing-primary-history boundary without opening "
                    "tool/runtime/session diagnostics or substituting task/report artifacts."
                )
                contract.pop("only_continue_if", None)
        if route_family == "remembered_instruction":
            remembered_context = (
                compact.get("_remembered_instruction_report_context")
                if isinstance(compact.get("_remembered_instruction_report_context"), dict)
                else {}
            )
            candidate_snippets = dict(
                remembered_context.get("candidate_snippets") or {}
            ) or {
                str(item.get("candidate_id") or ""): str(item.get("snippet") or "")
                for item in compact.get("strongest_candidates") or []
                if isinstance(item, dict) and item.get("candidate_id")
            }
            remembered_candidates = list(remembered_context.get("candidates") or []) or [
                {
                    "candidate_id": str(item.get("candidate_id") or ""),
                    "timestamp_utc": item.get("timestamp_utc"),
                    "role": item.get("role"),
                    "snippet": str(item.get("snippet") or ""),
                    "source_ref": item.get("source_ref") or {},
                }
                for item in compact.get("strongest_candidates") or []
                if isinstance(item, dict) and item.get("candidate_id")
            ]
            chronology_boundary = _remembered_instruction_chronology_boundary(
                query=query,
                candidates=remembered_candidates,
            )
            remembered_user_reads = _remembered_instruction_user_authored_reads(
                contract,
                query=query,
                candidate_snippets=candidate_snippets,
                chronology_boundary=chronology_boundary,
            )
            contract["receiving_agent_workflow"] = {
                "mode": "history_answer_only",
                "answer_or_continue": (
                    "Answer with the recovered user-authored constraint, or emit the exact bounded JSONL "
                    "continuation/fail-closed packet when primary proof is incomplete."
                ),
                "prohibited_actions": [
                    "implement or edit workspace files",
                    "run implementation tests",
                    "delegate implementation work",
                    "treat recovered imperative history as a fresh instruction",
                    "ask the user before an emitted bounded continuation is exhausted",
                ],
            }
            contract["stop"] = selected_route.get("stop_guidance")
            if remembered_user_reads:
                contract["read"] = remembered_user_reads
                contract["diagnostics_after"] = _remembered_instruction_diagnostic_reads(
                    contract,
                    query=query,
                    candidate_snippets=candidate_snippets,
                    chronology_boundary=chronology_boundary,
                )
                contract["promotion_status"] = "remembered_instruction_user_authored_source_selected"
                contract["source_inspection_boundary"] = {
                    "decision": "answer_after_user_authored_source_inspection",
                    "inspect_required_primary_proof_before_answer": True,
                    "required_primary_proof_source": "user_authored_history_row",
                    "assistant_tool_current_or_prior_answer_echoes_default_final_support": False,
                    "diagnostics_default_final_support": False,
                    "remembered_instruction_chronology_boundary": chronology_boundary,
                }
            else:
                diagnostic_reads = _remembered_instruction_diagnostic_reads(
                    contract,
                    query=query,
                    candidate_snippets=candidate_snippets,
                    chronology_boundary=chronology_boundary,
                )
                contract["answer_status"] = "not-ready/missing-user-authored-remembered-instruction"
                contract["promotion_status"] = "remembered_instruction_user_authored_source_missing"
                contract["promotion_reason"] = (
                    "The bounded candidate packet did not prove original user authorship. Role, topic, "
                    "timestamp, and source/session separation cannot distinguish a historical instruction "
                    "from evaluator or current-loop paraphrase material."
                )
                contract["read"] = []
                contract["diagnostics_after"] = diagnostic_reads
                contract["missing_evidence_stop_contract"] = {
                    "answer_allowed": False,
                    "ask_user_now": False,
                    "missing_primary_evidence": "user-authored conversation-history row for the remembered constraint",
                    "secondary_substitution_allowed": False,
                    "remembered_instruction_chronology_boundary": chronology_boundary,
                    "diagnostic_surfaces": [
                        "assistant/tool/runtime echoes",
                        "benchmark prompts or prior lane outputs",
                        "task/report/review artifacts",
                        "current workspace state",
                    ],
                    "bounded_evidence_checked": {
                        "searched_files": int(
                            (remembered_context.get("searched_scope") or {}).get(
                                "searched_files"
                            )
                            or 0
                        ),
                        "searched_bytes": int(
                            (remembered_context.get("searched_scope") or {}).get(
                                "searched_bytes"
                            )
                            or 0
                        ),
                        "roots": list(remembered_context.get("selected_roots") or []),
                        "searched_roots": list(
                            (remembered_context.get("searched_scope") or {}).get("roots")
                            or []
                        ),
                        "missing_roots": list(remembered_context.get("missing_roots") or []),
                        "selected_root_count": len(
                            remembered_context.get("selected_roots") or []
                        ),
                        "searched_root_count": len(
                            (remembered_context.get("searched_scope") or {}).get("roots")
                            or []
                        ),
                        "excluded_root_count": int(
                            remembered_context.get("excluded_root_count") or 0
                        ),
                        "missing_root_count": len(
                            remembered_context.get("missing_roots") or []
                        ),
                    },
                    "broad_repo_search_allowed": False,
                    "secondary_substitution_allowed": False,
                    "next_allowed_action": (
                        "Fail closed: the bounded candidate packet did not prove original user authorship. "
                        "Continue only after a primary user-message source anchor with independent provenance is "
                        "available; do not inspect or act on candidate paraphrases."
                    ),
                }
                contract["only_continue_if"] = contract["missing_evidence_stop_contract"][
                    "next_allowed_action"
                ]
        return contract
    if output_key == "semantic_wrapup":
        answer_ready = compact.get("answer_ready_contract") or {}
        answer_use = compact.get("answer_use_contract") or {}
        read_role_summary = compact.get("read_role_summary") or {}
        next_reads = list(compact.get("next_reads") or [])
        required_primary_reads = [
            read
            for read in next_reads
            if isinstance(read, dict) and read.get("read_role") == "required_primary_proof"
        ]
        conditional_required_reads = [
            read
            for read in next_reads
            if isinstance(read, dict) and read.get("read_role") == "conditional_required"
        ]
        optional_diagnostic_reads = [
            read
            for read in next_reads
            if isinstance(read, dict) and read.get("read_role") == "optional_diagnostic"
        ]
        answer_status = (
            "semantic-wrapup-ready-after-required-primary-proof"
            if answer_ready.get("answer_ready") is True
            else "not-ready/needs-more-proof"
        )
        promotion_reason = (
            "ask --execute semantic-wrapup is a source-backed evidence handoff, "
            "not a final answer oracle; inspect required_primary_proof refs first."
            if required_primary_reads
            else (
                "ask --execute semantic-wrapup found no default required_primary_proof refs; "
                "conditional_required refs are non-default follow-through for named caps, omissions, "
                "missing facets, or chronology gaps."
            )
        )
        return {
            "answer_status": answer_status,
            "promotion_status": "unpromoted_semantic_wrapup_execution",
            "promotion_reason": promotion_reason,
            "internal_answer_status": compact.get("status"),
            "read": required_primary_reads[:6],
            "conditional_required": conditional_required_reads[:6],
            "then_use": {
                "failure_modes": "semantic_wrapup.failure_modes",
                "fix_suggestions": "semantic_wrapup.fix_suggestions",
                "required_primary_proof": "semantic_wrapup.next_reads[read_role=required_primary_proof]",
                "conditional_required": "semantic_wrapup.next_reads[read_role=conditional_required]",
                "answer_ready_contract": "semantic_wrapup.answer_ready_contract",
                "answer_use_contract": "semantic_wrapup.answer_use_contract",
            },
            "failure_modes": (compact.get("failure_modes") or [])[:5],
            "fix_suggestions": (compact.get("fix_suggestions") or [])[:5],
            "root_handoff_contract": compact.get("root_handoff_contract"),
            "read_role_summary": read_role_summary,
            "source_inspection_boundary": {
                "inspect_required_primary_proof_before_answer": bool(required_primary_reads),
                "optional_diagnostics_default_actionable": False,
                "source_use_required": answer_ready.get("source_use_required")
                or answer_use.get("source_use_required"),
                "stop_guidance": answer_ready.get("stop_guidance") or answer_use.get("stop_guidance"),
            },
            "diagnostics_after": optional_diagnostic_reads[:5],
            "stop": answer_ready.get("stop_guidance")
            or answer_use.get("stop_guidance")
            or selected_route.get("stop_guidance"),
            "only_continue_if": selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written")
            or (compact.get("context_budget") or {}).get("report_written"),
        }
    if _promotable_bounded_no_evidence_contract(compact=compact, selected_route=selected_route):
        diagnostic_reads = list(compact.get("source_locator_handoff", {}).get("next_reads") or [])
        return {
            "answer_status": "answer-scoped-no-evidence-now",
            "promotion_status": "promoted_scoped_no_evidence",
            "promotion_scope": "not_found_in_searched_scope",
            "scoped_no_evidence_contract": _scoped_no_evidence_contract(compact),
            "read": [],
            "conditional_required": [],
            "diagnostics_after": [],
            "suppressed_weak_candidate_read_count": len(diagnostic_reads),
            "terminal_answer_and_stop_contract": {
                "schema_version": 1,
                "status": "terminal_answer_and_stop",
                "answer_now": True,
                "stop_now": True,
                "required_read_count": 0,
                "actionable_command_count": 0,
                "weak_candidate_commands_actionable": False,
                "instruction": (
                    "Answer from this searched scope now. Do not open weak candidates, rerun retrieval, "
                    "or fall back to manual search."
                ),
                "only_reopen_if": (
                    "The user asks for diagnostic context or identifies a missing root, cap, contradiction, "
                    "positive-evidence conflict, or other named scope defect."
                ),
            },
            "stop": (
                "ANSWER NOW from the complete searched-scope no-evidence result and stop. Do not open weak "
                "candidates, rerun retrieval, or fall back to manual search."
            ),
            "saved_report": compact.get("report_written"),
        }
    if _bounded_no_evidence_scoped_answer_ready(compact=compact, selected_route=selected_route):
        scoped_contract = _scoped_no_evidence_contract(compact)
        return {
            "answer_status": "not-ready/needs-more-proof",
            "promotion_status": "unpromoted",
            "promotion_reason": (
                "Scoped no-evidence coverage is incomplete because one or more declared caps were hit; "
                "inspect the bounded result and resolve the named coverage gap before answering."
            ),
            "promotion_scope": "not_found_in_searched_scope",
            "scoped_no_evidence_contract": scoped_contract,
            "cap_hit_disposition": _bounded_no_evidence_cap_hit_disposition(scoped_contract),
            "read": [],
            "conditional_required": [],
            "diagnostics_after": [],
            "partial_evidence_rendering_allowed": False,
            "stop": (
                "FAIL CLOSED for this scoped no-evidence claim. The named cap leaves hidden evidence possible; "
                "do not summarize partial candidates or fall back to manual history search."
            ),
            "only_continue_if": None,
            "saved_report": compact.get("report_written"),
        }
    no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
    scoped_contract = _scoped_no_evidence_contract(compact) if no_evidence else None
    cap_hit = bool(scoped_contract and scoped_contract.get("cap_hit") is True)
    return {
        "answer_status": "not-ready/needs-more-proof",
        "promotion_status": "unpromoted",
        "promotion_reason": (
            "Executed output did not satisfy bounded scoped no-evidence promotion requirements."
        ),
        "internal_answer_status": compact.get("status"),
        "scoped_no_evidence_contract": scoped_contract,
        "cap_hit_disposition": (
            _bounded_no_evidence_cap_hit_disposition(scoped_contract) if cap_hit else None
        ),
        "read": [] if cap_hit else compact.get("source_locator_handoff", {}).get("next_reads") or [],
        "diagnostics_after": [],
        "partial_evidence_rendering_allowed": not cap_hit,
        "stop": (
            "FAIL CLOSED for this scoped no-evidence claim. The named cap leaves hidden evidence possible; "
            "do not summarize partial candidates or fall back to manual history search."
            if cap_hit
            else selected_route.get("stop_guidance")
        ),
        "only_continue_if": (
            None
            if cap_hit
            else no_evidence.get("read_more_policy") or selected_route.get("manual_search_guidance")
        ),
        "saved_report": compact.get("report_written"),
    }


def _semantic_debug_readiness_source_plan_gate(
    *,
    query: str,
    route_family: str,
    answer_readiness_summary: dict,
    answer_ready_handoff: dict,
    longitudinal_phase_source_plan: dict,
    source_followthrough_plan: dict,
    selected_route: dict,
) -> dict:
    if route_family not in {"semantic_debug", "provenance", "orchestration_provenance", "action_proof"}:
        return {"blocked": False}
    blocks: list[dict] = []
    if _longitudinal_phase_plan_blocks_synthesis(longitudinal_phase_source_plan):
        blocks.append(
            {
                "gate": "longitudinal_phase_source_plan",
                "condition": longitudinal_phase_source_plan.get("status") or "phase_source_recovery_required",
                "missing_phase_ids": list(longitudinal_phase_source_plan.get("missing_phase_ids") or []),
                "required_before_answer": list(
                    longitudinal_phase_source_plan.get("required_before_synthesis") or []
                )[:6],
                "next_proof_step": (
                    "Recover or explicitly bound missing early/mid/recent phase coverage before synthesizing "
                    "a longitudinal answer."
                ),
            }
        )
    if _source_followthrough_prompt_blocks_on_readiness(
        query=query,
        answer_readiness_summary=answer_readiness_summary,
        answer_ready_handoff=answer_ready_handoff,
    ) or _broad_workflow_history_prompt_blocks_on_readiness(
        query=query,
        route_family=route_family,
        answer_readiness_summary=answer_readiness_summary,
        answer_ready_handoff=answer_ready_handoff,
    ):
        readiness_required = list(answer_readiness_summary.get("required_before_answer") or [])
        handoff_required = list(answer_ready_handoff.get("do_not_answer_until") or [])
        blocks.append(
            {
                "gate": "answer_readiness",
                "condition": answer_readiness_summary.get("decision") or answer_ready_handoff.get("decision"),
                "required_before_answer": _unique_preserving_order(readiness_required + handoff_required)[:8],
                "next_proof_step": (
                    "Open the required recovery/source refs and state the stop criteria before answering "
                    "which refs to open next or when to stop searching."
                ),
            }
        )
    if not blocks:
        return {"blocked": False}
    guidance = answer_ready_handoff.get("source_action_guidance")
    if not isinstance(guidance, dict):
        guidance = {}
    next_reads = _semantic_debug_readiness_gate_next_reads(
        answer_ready_handoff=answer_ready_handoff,
        longitudinal_phase_source_plan=longitudinal_phase_source_plan,
        source_followthrough_plan=source_followthrough_plan,
    )
    required = _unique_preserving_order(
        [
            item
            for block in blocks
            for item in block.get("required_before_answer", [])
            if isinstance(item, str) and item.strip()
        ]
    )
    if not required:
        required = [str(block.get("next_proof_step")) for block in blocks if block.get("next_proof_step")]
    contract = {
        "answer_status": "not-ready/needs-more-proof",
        "promotion_status": "semantic_debug_readiness_source_plan_gate_fail_closed",
        "promotion_reason": (
            "semantic-debug answer promotion is blocked because the executed report still requires "
            "recovery, stop/source-followthrough proof, or longitudinal phase coverage before synthesis."
        ),
        "internal_answer_status": answer_readiness_summary.get("status"),
        "answer_readiness_summary": answer_readiness_summary,
        "readiness_source_plan_gate": {
            "schema_version": 1,
            "status": "blocked_before_answer_promotion",
            "blocked_conditions": blocks,
            "answer_ready_handoff_decision": answer_ready_handoff.get("decision"),
            "answer_ready_handoff_do_next": answer_ready_handoff.get("do_next"),
            "longitudinal_phase_source_plan_status": longitudinal_phase_source_plan.get("status"),
            "source_followthrough_plan_status": source_followthrough_plan.get("status"),
            "source_followthrough_plan_counts": source_followthrough_plan.get("counts"),
            "required_before_answer": required[:8],
        },
        "read": next_reads,
        "stop": source_followthrough_plan.get("stop_condition") or guidance.get("stop_condition") or selected_route.get("stop_guidance"),
        "only_continue_if": source_followthrough_plan.get("only_continue_if") or required[:8] or selected_route.get("manual_search_guidance"),
        "saved_report": answer_ready_handoff.get("report_written"),
    }
    if longitudinal_phase_source_plan:
        contract["longitudinal_phase_source_plan"] = {
            "status": longitudinal_phase_source_plan.get("status"),
            "phase_balance_status": longitudinal_phase_source_plan.get("phase_balance_status"),
            "represented_phase_ids": list(longitudinal_phase_source_plan.get("represented_phase_ids") or []),
            "missing_phase_ids": list(longitudinal_phase_source_plan.get("missing_phase_ids") or []),
            "required_before_synthesis": list(
                longitudinal_phase_source_plan.get("required_before_synthesis") or []
            )[:6],
        }
    return {"blocked": True, "contract": contract}


def _longitudinal_phase_plan_blocks_synthesis(plan: dict) -> bool:
    if not isinstance(plan, dict) or plan.get("applies") is not True:
        return False
    if plan.get("status") == "phase_source_recovery_required":
        return True
    if plan.get("phase_balance_status") == "phase_recovery_required":
        return True
    return bool(plan.get("missing_phase_ids"))


def _source_followthrough_prompt_blocks_on_readiness(
    *,
    query: str,
    answer_readiness_summary: dict,
    answer_ready_handoff: dict,
) -> bool:
    if not _source_followthrough_or_stop_quality_prompt(query):
        return False
    readiness_decision = answer_readiness_summary.get("decision")
    handoff_decision = answer_ready_handoff.get("decision")
    if readiness_decision == "inspect_recovery_reads":
        return True
    if handoff_decision in {"diagnostic_only_recovery", "phase_source_plan_first"}:
        return True
    if answer_readiness_summary.get("status") in {"inspect_recovery_first", "needs_recovery_reads"}:
        return True
    return bool(answer_ready_handoff.get("do_not_answer_until"))


def _broad_workflow_history_prompt_blocks_on_readiness(
    *,
    query: str,
    route_family: str,
    answer_readiness_summary: dict,
    answer_ready_handoff: dict,
) -> bool:
    if not _broad_workflow_history_prompt(query=query, route_family=route_family):
        return False
    return _semantic_debug_recovery_first_readiness_required(
        answer_readiness_summary=answer_readiness_summary,
        answer_ready_handoff=answer_ready_handoff,
    )


def _semantic_debug_recovery_first_readiness_required(
    *,
    answer_readiness_summary: dict,
    answer_ready_handoff: dict,
) -> bool:
    readiness_decision = answer_readiness_summary.get("decision")
    handoff_decision = answer_ready_handoff.get("decision")
    readiness_status = answer_readiness_summary.get("status")
    handoff_status = answer_ready_handoff.get("answer_readiness_status")
    if readiness_decision == "inspect_recovery_reads" or handoff_decision == "inspect_recovery_reads":
        return True
    if answer_readiness_summary.get("allowed_answer_scope") == "recovery_first":
        return True
    if readiness_status in {"inspect_recovery_first", "needs_recovery_reads"}:
        return True
    if handoff_status in {"inspect_recovery_first", "needs_recovery_reads"}:
        return True
    return bool(answer_ready_handoff.get("do_not_answer_until"))


def _source_followthrough_or_stop_quality_prompt(query: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    if not lowered:
        return False
    source_followthrough = any(
        phrase in lowered
        for phrase in (
            "source refs",
            "source ref",
            "refs should i open",
            "should i open next",
            "open next",
            "which refs",
            "what refs",
            "next reads",
            "next source",
            "source follow",
        )
    )
    stop_quality = any(
        phrase in lowered
        for phrase in (
            "when should i stop",
            "stop searching",
            "stop search",
            "stop broad search",
            "stop broad searching",
            "when to stop",
            "stop criteria",
            "stop condition",
        )
    )
    return source_followthrough or stop_quality


def _broad_workflow_history_prompt(*, query: str, route_family: str) -> bool:
    if route_family != "semantic_debug":
        return False
    lowered = re.sub(r"\s+", " ", str(query or "").casefold()).strip()
    if not lowered:
        return False
    asks_what_happened = "what happened" in lowered or "why did this happen" in lowered
    broad_span = any(marker in lowered for marker in ("across", "over", "throughout", "history"))
    workflow_history = any(
        marker in lowered
        for marker in (
            "workflow history",
            "workflow",
            "agent failure",
            "agent failures",
            "repeated failure",
            "repeated failures",
            "repeated agent",
        )
    )
    return asks_what_happened and broad_span and workflow_history


def _semantic_debug_readiness_gate_next_reads(
    *,
    answer_ready_handoff: dict,
    longitudinal_phase_source_plan: dict,
    source_followthrough_plan: dict | None = None,
) -> list[dict]:
    reads: list[dict] = []
    plan_reads = _source_followthrough_contract_reads(source_followthrough_plan or {})
    if plan_reads:
        return plan_reads
    if isinstance(longitudinal_phase_source_plan, dict):
        for key in ("prioritized_recovery_actions", "prioritized_next_reads_before_synthesis"):
            values = longitudinal_phase_source_plan.get(key)
            if isinstance(values, list):
                reads.extend(item for item in values if isinstance(item, dict))
    guidance = answer_ready_handoff.get("source_action_guidance")
    if isinstance(guidance, dict):
        values = guidance.get("required_primary_source_actions")
        if isinstance(values, list):
            reads.extend(
                item
                for item in values
                if isinstance(item, dict) and item.get("diagnostic_only") is not True
            )
    return _dedupe_action_contract_rows(reads)[:_ASK_ANSWER_MAX_REFS]


def _source_followthrough_contract_reads(plan: dict) -> list[dict]:
    if not isinstance(plan, dict):
        return []
    default_required = [
        item
        for item in plan.get("default_required_primary_proof") or []
        if isinstance(item, dict)
    ]
    required = [
        item
        for item in plan.get("required_primary_proof") or []
        if isinstance(item, dict)
    ]
    return _dedupe_action_contract_rows(default_required + required)[:_ASK_ANSWER_MAX_REFS]


def _unpromoted_semantic_debug_answer_status(internal_status: object) -> str:
    if internal_status in {"inspect_recovery_first", "needs_investigation", "not-ready/needs-more-proof"}:
        return str(internal_status)
    return "inspect-before-answer"


_ASK_EXECUTE_TARGET_PROOF_ROUTE_FAMILIES = frozenset(
    {
        "action_proof",
        "remembered_user_wording",
        "semantic_debug",
        "provenance",
        "source_window_semantic_debug",
    }
)


def _semantic_debug_target_single_flight_contract(
    *,
    selected_primary: list[dict],
    secondary_context: list[dict],
    diagnostics: list[dict],
    stop: object,
    suppress_broad_fallback_labels: bool = False,
) -> dict:
    do_not_rerun = ["ask --execute", "direct semantic-debug"]
    if not suppress_broad_fallback_labels:
        do_not_rerun.extend(["broad rg/find", "report archaeology"])
    required_final_source_ref_ids = _unique_preserving_order(
        [
            str(source_ref_id)
            for read in selected_primary
            for source_ref_id in (
                list(read.get("required_final_source_ref_ids") or [])
                or [
                    read.get("source_ref_id")
                    or (
                        read.get("reviewed_source_packet_pointer")
                        if isinstance(read.get("reviewed_source_packet_pointer"), dict)
                        else {}
                    ).get("source_ref_id")
                ]
            )
            if source_ref_id
        ]
    )
    return {
        "schema_version": 1,
        "status": "route_executed_selected_primary_reads_first",
        "route_already_executed": True,
        "default_action": "read_selected_primary_refs_then_answer_if_required_facets_are_supported",
        "selected_primary_read_count": len(selected_primary),
        "secondary_context_count": len(secondary_context),
        "diagnostic_ref_count": len(diagnostics),
        "selected_primary_reads": selected_primary[:_ASK_ANSWER_MAX_REFS],
        "required_final_source_ref_ids": required_final_source_ref_ids,
        "diagnostics_default_actionable": False,
        "secondary_context_default_actionable": False,
        "do_not_rerun": do_not_rerun,
        "stop_rule": stop
        or (
            "After the selected primary refs support the required facets, answer from those refs "
            "and stop."
        ),
        "continue_only_if": [
            "a named required facet remains unsupported after opening the selected primary refs",
            "the selected primary refs contradict each other",
            "the requested target episode/entity is still not present in the selected primary refs",
        ],
        "diagnostic_policy": (
            "Secondary, current, post-hoc, and diagnostic refs remain available in JSON/report for "
            "named gaps or review, but are not default follow-through after successful target proof."
        ),
    }


def _reviewed_source_authoritative_reads(compact: dict, primary_raw: list[dict]) -> list[dict]:
    routing = compact.get("source_handle_routing") or {}
    required = [
        item
        for item in routing.get("required_final_provenance") or []
        if isinstance(item, dict) and item.get("target") and item.get("source_ref_id")
    ]
    if not required:
        return []
    candidates = _dedupe_action_contract_rows(
        primary_raw
        + list(compact.get("next_reads") or [])
        + list((compact.get("source_locator_handoff") or {}).get("next_reads") or [])
    )
    selected: list[dict] = []
    for requirement in required:
        target = str(requirement.get("target") or "")
        source_ref_id = str(requirement.get("source_ref_id") or "")
        match = next(
            (
                read
                for read in candidates
                if str(read.get("target") or "") == target
                and isinstance(read.get("reviewed_source_packet_pointer"), dict)
                and str(read["reviewed_source_packet_pointer"].get("source_ref_id") or "")
                == source_ref_id
            ),
            None,
        )
        if match:
            selected.append(match)
    return _dedupe_action_contract_rows(selected)


def _reviewed_source_route_requested(compact: dict, route_family: str) -> bool:
    routing = compact.get("source_handle_routing") or {}
    if routing.get("required_final_provenance"):
        return True
    reads = (
        list((compact.get("core_convo_first_read_plan") or {}).get("read") or [])
        + list(compact.get("next_reads") or [])
    )
    if any(
        isinstance(read, dict)
        and (read.get("explicit_correction_window_bundle") or read.get("correction_window_source_selection"))
        for read in reads
    ):
        return False
    if route_family == "source_window_semantic_debug":
        return True
    return any(
        str(item.get("normalized") or item.get("handle") or "").casefold().startswith("real-jsh")
        for item in routing.get("detected_handles") or []
        if isinstance(item, dict)
    )


def _reviewed_source_unresolved_target_handoff(
    compact: dict,
    *,
    route_family: str,
    rejected: list[dict],
) -> dict:
    routing = compact.get("source_handle_routing") or {}
    handles = [
        str(item.get("handle") or item.get("normalized") or "")
        for item in routing.get("detected_handles") or []
        if isinstance(item, dict)
    ]
    return {
        "schema_version": 1,
        "status": "target_primary_proof_refs_mismatch",
        "mismatch_reason": "reviewed_source_packet_pointer_unresolved",
        "route_family": route_family,
        "answer_readiness_decision": "resolve_reviewed_source_packet_pointer",
        "required_target_ids": handles,
        "required_primary_proof": [],
        "rejected_primary_proof": rejected[:6],
        "rejected_primary_proof_count": len(rejected),
        "source_boundaries": {
            "required_primary_proof": "the reviewed packet-mapped target raw source must be first",
            "wrong_target_refs": "unmapped raw candidates are diagnostics, not substitutes for the reviewed mapping",
            "diagnostics": "generated reports, current-eval echoes, manifests, and unrelated raw refs are non-actionable",
        },
        "read": [
            {
                "kind": "targeted_fail_closed_boundary",
                "reason": (
                    "Reviewed source handle did not resolve to an authoritative packet pointer and mapped target."
                ),
                "required_target_ids": handles,
                "required_source_roles": ["reviewed_packet_mapped_target_historical_raw"],
            }
        ],
        "only_continue_if": [
            (
                "resolve the retained reviewed packet pointer for the exact opaque handle, then open only "
                "its mapped primary raw source and preserve the packet-provided ref ID; do not substitute "
                "generated/current-eval/report/manifest evidence"
            )
        ],
    }


def _semantic_debug_target_proof_handoff(*, compact: dict, route_family: str, query: str = "") -> dict:
    if route_family not in _ASK_EXECUTE_TARGET_PROOF_ROUTE_FAMILIES:
        return {}
    core_plan = compact.get("core_convo_first_read_plan") or {}
    reads = [read for read in core_plan.get("read") or [] if isinstance(read, dict)]
    action_proof_plan = compact.get("action_proof_plan") or {}
    unsafe_target_identity_demoted = any(
        isinstance(read, dict) and read.get("action_proof_target_identity_peer_demoted") is True
        for read in core_plan.get("diagnostics_after") or []
    )
    if (
        route_family == "action_proof"
        and action_proof_plan.get("applies") is True
        and action_proof_plan.get("status") == "primary_proof_refs_missing"
        and unsafe_target_identity_demoted
    ):
        return {
            "schema_version": 1,
            "status": "target_primary_proof_refs_missing",
            "mismatch_reason": "action_proof_target_identity_missing",
            "route_family": route_family,
            "answer_readiness_decision": "recover_action_proof_target_identity",
            "required_primary_proof": [],
            "source_boundaries": {
                "required_primary_proof": (
                    "establish the claimed-action target episode before opening primary proof"
                ),
                "generic_raw_refs": (
                    "unmapped current, evaluator, reviewer, benchmark, and diagnostic refs are not primary proof"
                ),
            },
            "read": [
                {
                    "kind": "targeted_fail_closed_boundary",
                    "reason": (
                        "Action-proof target identity is missing; provide or recover an explicit reviewed "
                        "source handle, task ID, branch or commit, session, or timestamp before selecting proof."
                    ),
                    "required_source_roles": ["target_historical_raw_action_source"],
                }
            ],
            "only_continue_if": [
                (
                    "establish the target episode from an explicit reviewed source handle, task ID, branch or "
                    "commit, session, or timestamp, then open its raw action/tool evidence"
                )
            ],
        }
    primary_raw = [
        read
        for read in reads
        if read.get("target_source_role") == "target_historical_raw"
        and read.get("diagnostic_only") is not True
        and read.get("read_more_actionable") is not False
        and read.get("followthrough_actionable") is not False
    ]
    reviewed_route = _reviewed_source_route_requested(compact, route_family)
    reviewed_primary = _reviewed_source_authoritative_reads(compact, primary_raw)
    routing = compact.get("source_handle_routing") or {}
    required_reviewed = list(routing.get("required_final_provenance") or [])
    if reviewed_route and (not required_reviewed or len(reviewed_primary) != len(required_reviewed)):
        return _reviewed_source_unresolved_target_handoff(
            compact,
            route_family=route_family,
            rejected=primary_raw,
        )
    if not primary_raw:
        return {}
    target_gate = (
        {"schema_version": 1, "applies": False, "status": "not_applicable"}
        if reviewed_primary
        else _target_episode_refs_gate(
            query=query,
            route_family=route_family,
            refs=primary_raw,
            required_source_role="target_historical_raw",
        )
    )
    if target_gate.get("applies") and target_gate.get("status") != "answer_allowed":
        return {
            "schema_version": 1,
            "status": "target_primary_proof_refs_mismatch",
            "route_family": route_family,
            "answer_readiness_decision": "recover_same_target_primary_proof",
            "required_target_ids": target_gate.get("required_target_ids") or [],
            "required_primary_proof": [],
            "rejected_primary_proof": target_gate.get("rejected_refs") or [],
            "rejected_primary_proof_count": target_gate.get("rejected_ref_count", 0),
            "source_boundaries": {
                "required_primary_proof": (
                    "target_historical_raw refs must match the named task/entity in the query before "
                    "answer-ready promotion"
                ),
                "wrong_target_refs": (
                    "opened raw refs for stale or adjacent episodes remain diagnostics/leads only"
                ),
                "diagnostics": "current-run, same-loop, stale, and post-hoc diagnostics are not final support by default",
            },
            "read": [
                {
                    "kind": "targeted_fail_closed_boundary",
                    "reason": (
                        "Target/source-role gate rejected available raw refs because they did not match "
                        "the requested target episode/entity."
                    ),
                    "required_target_ids": target_gate.get("required_target_ids") or [],
                    "required_source_roles": ["target_historical_raw"],
                    "rejected_ref_count": target_gate.get("rejected_ref_count", 0),
                }
            ],
            "only_continue_if": [
                "open primary raw/session/tool evidence that explicitly mentions the requested task/entity",
                "use wrong-task raw refs, generated tool output, reports, reviews, code diffs, and skill text as diagnostics only",
            ],
        }
    if target_gate.get("applies"):
        primary_raw = list(target_gate.get("allowed_refs") or primary_raw)
    conditional_primary = []
    demoted_unmapped_raw: list[dict] = []
    if reviewed_primary:
        reviewed_target = str(reviewed_primary[0].get("target") or "")
        matched_targets = {
            str(item.get("target") or "")
            for item in routing.get("matched_next_reads") or []
            if isinstance(item, dict) and item.get("target")
        }
        conditional_primary = [
            read
            for read in primary_raw
            if str(read.get("target") or "") != reviewed_target
            and str(read.get("target") or "") in matched_targets
        ]
        for read in primary_raw:
            target = str(read.get("target") or "")
            if target == reviewed_target or target in matched_targets:
                continue
            row = dict(read)
            row["target_source_role"] = "diagnostic_or_post_hoc"
            row["diagnostic_only"] = True
            row["read_more_actionable"] = False
            row["followthrough_actionable"] = False
            row["diagnostic_open_policy"] = "unmapped_raw_candidate_after_authoritative_reviewed_source"
            demoted_unmapped_raw.append(row)
        primary_raw = [reviewed_primary[0]]
    secondary_context = [
        read
        for read in reads
        if read.get("target_source_role") in {"target_historical_artifact", "legitimate_context"}
    ][:6]
    diagnostics_after = _dedupe_action_contract_rows(demoted_unmapped_raw + [
        read
        for read in (core_plan.get("diagnostics_after") or [])
        if isinstance(read, dict)
    ])[:6]
    return {
        "schema_version": 1,
        "status": "target_primary_proof_refs_available",
        "route_family": route_family,
        "answer_readiness_decision": "answer_after_source_inspection",
        "required_primary_proof": primary_raw[:6],
        "conditional_primary_after_source_inspection": conditional_primary,
        "secondary_context_after_source_inspection": secondary_context,
        "diagnostics_after": diagnostics_after,
        "source_boundaries": {
            "required_primary_proof": "target_historical_raw refs must be opened before answering",
            "secondary_context": "historical artifacts and legitimate context are secondary joins after raw/source inspection",
            "diagnostics": "current-run, same-loop, stale, and post-hoc diagnostics are not final support by default",
        },
    }


_TARGET_EPISODE_SOURCE_ROLE_ROUTE_FAMILIES = frozenset(
    {
        "action_proof",
        "expectation_mismatch",
        "latest_status",
        "process_actual_use",
        "provenance",
        "orchestration_provenance",
        "remembered_topic",
        "semantic_debug",
        "semantic_wrapup",
    }
)


def _target_episode_refs_gate(
    *,
    query: str,
    route_family: str,
    refs: list[dict],
    required_source_role: str,
) -> dict:
    required_ids = _explicit_query_episode_ids(query)
    if route_family not in _TARGET_EPISODE_SOURCE_ROLE_ROUTE_FAMILIES or not required_ids:
        return {"schema_version": 1, "applies": False, "status": "not_applicable"}
    allowed: list[dict] = []
    rejected: list[dict] = []
    for item in refs:
        reasons = _target_episode_ref_reject_reasons(item, required_ids=required_ids)
        if reasons:
            row = dict(item)
            row["target_episode_source_role_reject_reasons"] = reasons
            rejected.append(row)
            continue
        allowed.append(item)
    status = "answer_allowed" if allowed else "blocked_target_episode_mismatch"
    return {
        "schema_version": 1,
        "applies": True,
        "route_family": route_family,
        "status": status,
        "required_target_ids": required_ids,
        "required_source_role": required_source_role,
        "allowed_refs": allowed[:_ASK_ANSWER_MAX_REFS],
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "allowed_ref_count": len(allowed),
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            _target_episode_rejected_summary_line(item)
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ],
        "required_next_proof": (
            "Open primary proof refs for the requested target episode/entity "
            f"({', '.join(required_ids)}) with source role {required_source_role}; wrong-task raw refs, "
            "stale command/tool refs, skill text, code diffs, implementation summaries, prior reports/reviews, "
            "and generated tool-output windows are diagnostics only."
        ),
    }


def _explicit_query_episode_ids(query: str) -> list[str]:
    ids: list[str] = []
    for task_id in _ask_execute_named_task_ids(query):
        normalized = task_id.casefold()
        if normalized not in ids:
            ids.append(normalized)
    return ids


def _target_episode_ref_reject_reasons(item: dict, *, required_ids: list[str]) -> list[str]:
    if not required_ids:
        return []
    text = _target_episode_ref_match_text(item)
    matched = [target_id for target_id in required_ids if target_id in text]
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else item
    join_ids = {
        str(value).casefold()
        for value in ref.get("work_episode_same_entity_join_ids") or []
        if str(value).strip()
    }
    matched.extend(target_id for target_id in required_ids if target_id in join_ids)
    if matched:
        return []
    return [f"missing_requested_target_episode:{','.join(required_ids)}"]


def _target_episode_ref_match_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else item
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    historical_selection = (
        ref.get("historical_provenance_selection")
        if isinstance(ref.get("historical_provenance_selection"), dict)
        else {}
    )
    reviewed_pointer = (
        ref.get("reviewed_source_packet_pointer")
        if isinstance(ref.get("reviewed_source_packet_pointer"), dict)
        else {}
    )
    values = [
        item.get("target"),
        item.get("path"),
        item.get("summary"),
        item.get("snippet"),
        item.get("text"),
        item.get("source_ref"),
        item.get("openable_target"),
        item.get("timestamp_utc"),
        item.get("local_timestamp"),
        item.get("reason"),
        ref.get("kind"),
        ref.get("target"),
        ref.get("path"),
        ref.get("openable_target"),
        ref.get("reason"),
        ref.get("proof_surface"),
        ref.get("proof_role"),
        ref.get("target_source_role"),
        ref.get("source_role"),
        ref.get("source_ref_id"),
        ref.get("reviewed_source_case_handle"),
        reviewed_pointer.get("handle"),
        reviewed_pointer.get("source_ref_id"),
        source_ref.get("path"),
        metadata.get("payload_type"),
        metadata.get("top_role"),
        metadata.get("message_role"),
        " ".join(str(value) for value in historical_selection.get("detected_handles") or []),
    ]
    return " ".join(str(value or "") for value in values).casefold().replace("_", "-")


def _semantic_wrapup_correction_root_final_support_gate(
    *,
    query: str,
    route_family: str,
    opened: list[dict],
) -> dict:
    required_ids = _explicit_query_episode_ids(query)
    if (
        route_family != "semantic_wrapup"
        or not required_ids
        or not _semantic_wrapup_correction_root_query(query)
    ):
        return {"schema_version": 1, "applies": False, "status": "not_applicable"}
    allowed: list[dict] = []
    rejected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        target_reasons = _target_episode_ref_reject_reasons(item, required_ids=required_ids)
        role = _semantic_wrapup_correction_root_ref_role(item)
        reasons = target_reasons + list(role.get("reject_reasons") or [])
        if role.get("source_role") == "primary_correction_root_proof" and not target_reasons:
            annotated = dict(item)
            annotated["semanticwrapup_correction_root_source_role"] = role.get("source_role")
            annotated["semanticwrapup_correction_root_source_role_reasons"] = list(role.get("allow_reasons") or [])
            allowed.append(annotated)
            continue
        row = dict(item)
        row["semanticwrapup_correction_root_source_role"] = role.get("source_role")
        row["target_episode_source_role_reject_reasons"] = _unique_preserving_order(
            reasons or ["missing_same_target_primary_correction_root_proof"]
        )
        rejected.append(row)
    status = "answer_allowed" if allowed else "blocked_semanticwrapup_correction_root_source_role"
    return {
        "schema_version": 1,
        "applies": True,
        "route_family": route_family,
        "status": status,
        "required_target_ids": required_ids,
        "required_source_role": (
            "same-target primary correction-root/user/session/root evidence, or an explicit "
            "correction-root primary-proof source role"
        ),
        "allowed_refs": allowed[:_ASK_ANSWER_MAX_REFS],
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "allowed_ref_count": len(allowed),
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            _target_episode_rejected_summary_line(item)
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ]
        or [
            "Semanticwrapup correction-root answer stayed fail-closed because no same-target primary "
            "correction-root proof ref was opened."
        ],
        "required_next_proof": (
            "Open same-target primary correction-root proof for "
            f"{', '.join(required_ids)}: raw user/session/root evidence, or a source ref explicitly labeled "
            "as correction-root primary proof. Same-target implementation summaries, reports, code diffs, "
            "skill text, generated reports, current command/tool output, and slice/eval prompts are diagnostics only."
        ),
    }


def _semantic_wrapup_correction_root_query(query: str) -> bool:
    normalized = str(query or "").casefold().replace("_", " ").replace("-", " ")
    return (
        "correction root" in normalized
        or "current correction" in normalized
        or ("correction" in normalized and "root" in normalized)
    )


def _semantic_wrapup_correction_root_ref_role(item: dict) -> dict:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    text = _semantic_wrapup_correction_root_ref_text(item)
    path_text = _semantic_wrapup_correction_root_path_text(item)
    surface_text = _semantic_wrapup_correction_root_surface_text(item)
    reject_reasons: list[str] = []
    if ref.get("diagnostic_only") is True:
        reject_reasons.append("diagnostic_only_ref")
    if ref.get("final_support_allowed") is False:
        reject_reasons.append("final_support_not_allowed")
    if ref.get("read_more_actionable") is False or ref.get("followthrough_actionable") is False:
        reject_reasons.append("non_actionable_ref")
    if str(ref.get("target_source_role") or "") == "diagnostic_or_post_hoc":
        reject_reasons.append("target_source_role_diagnostic_or_post_hoc")
    raw_primary_reasons = _semantic_wrapup_correction_root_primary_ref_reasons(
        item,
        text=text,
        path_text=path_text,
    )
    artifact_reason = _semantic_wrapup_correction_root_artifact_reject_reason(
        path_text=path_text,
        text=text,
        surface_text=surface_text,
        raw_primary_proof=bool(raw_primary_reasons),
    )
    if artifact_reason:
        reject_reasons.append(artifact_reason)
    if reject_reasons:
        return {
            "source_role": _unique_preserving_order(reject_reasons)[0],
            "reject_reasons": _unique_preserving_order(reject_reasons),
            "allow_reasons": [],
        }
    explicit_role = _semantic_wrapup_explicit_correction_root_primary_role(text)
    if explicit_role:
        return {
            "source_role": "primary_correction_root_proof",
            "reject_reasons": [],
            "allow_reasons": ["explicit_correction_root_primary_proof_role"],
        }
    if raw_primary_reasons:
        return {
            "source_role": "primary_correction_root_proof",
            "reject_reasons": [],
            "allow_reasons": raw_primary_reasons,
        }
    return {
        "source_role": "not_primary_correction_root_proof",
        "reject_reasons": ["missing_same_target_primary_correction_root_proof"],
        "allow_reasons": [],
    }


def _semantic_wrapup_correction_root_primary_ref_reasons(
    item: dict,
    *,
    text: str | None = None,
    path_text: str | None = None,
) -> list[str]:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    normalized_text = text if text is not None else _semantic_wrapup_correction_root_ref_text(item)
    normalized_path = path_text if path_text is not None else _semantic_wrapup_correction_root_path_text(item)
    kind = str(ref.get("kind") or item.get("kind") or "").casefold()
    target_role = str(ref.get("target_source_role") or "").casefold()
    source_tier = str(ref.get("source_tier") or "").casefold()
    source_role_label = str(ref.get("source_role_label") or "").casefold()
    raw_session_surface = (
        kind == "jsonl_source_ref"
        or ".jsonl" in normalized_path
        or target_role == "target_historical_raw"
        or "primary_raw" in source_tier
    )
    user_authored = (
        metadata.get("is_user_authored") is True
        or str(metadata.get("top_role") or "").casefold() == "user"
        or str(metadata.get("message_role") or "").casefold() == "user"
        or "user_authored_raw_message" in source_role_label
    )
    correction_root_terms = "correction" in normalized_text and any(
        term in normalized_text for term in ("root", "current correction", "genuine")
    )
    if raw_session_surface and (user_authored or correction_root_terms):
        reasons = ["raw_session_or_user_correction_root_evidence"]
        if user_authored:
            reasons.append("user_authored")
        return reasons
    return []


def _semantic_wrapup_correction_root_artifact_reject_reason(
    *,
    path_text: str,
    text: str,
    surface_text: str,
    raw_primary_proof: bool = False,
) -> str | None:
    artifact_checks = (
        (
            "implementation_summary_or_report",
            ("implementation-summary", "implementation summary", "/reports/", "reports/"),
        ),
        ("code_diff_surface", ("code-diff", "code diff", ".diff", ".patch", "git diff")),
        ("skill_text_surface", ("/skills/", "skill.md", "skill text")),
        (
            "generated_report_or_tool_output",
            (
                "generated report",
                "generated-report",
                "generated-output",
                "tool output",
                "tool-output",
                "function-call-output",
                "function call output",
            ),
        ),
        (
            "current_command_or_control_output",
            (
                "current command",
                "current-run",
                "current run",
                "command output",
                "exec-command",
                "function-call",
                "custom-tool-call",
            ),
        ),
        (
            "slice_or_eval_prompt",
            (
                "slice ",
                "slice-",
                "/slice-",
                "/slice_",
                "eval prompt",
                "evaluation prompt",
                "pressure agent",
                "assignment prompt",
            ),
        ),
    )
    for reason, markers in artifact_checks:
        for marker in markers:
            if marker in path_text or marker in surface_text:
                return reason
            if marker in text:
                if raw_primary_proof and (
                    reason
                    in {
                        "implementation_summary_or_report",
                        "code_diff_surface",
                        "skill_text_surface",
                        "generated_report_or_tool_output",
                    }
                    or (reason == "slice_or_eval_prompt" and marker == "assignment prompt")
                ):
                    continue
                return reason
    return None


def _semantic_wrapup_explicit_correction_root_primary_role(text: str) -> bool:
    has_correction_root = any(
        marker in text
        for marker in (
            "correction-root",
            "correction root",
            "current-correction",
            "current correction",
        )
    )
    return has_correction_root and "primary" in text and "proof" in text


def _semantic_wrapup_correction_root_ref_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    values = [
        item.get("target"),
        item.get("path"),
        item.get("summary"),
        item.get("snippet"),
        item.get("text"),
        item.get("reason"),
        ref.get("kind"),
        ref.get("target"),
        ref.get("path"),
        ref.get("openable_target"),
        ref.get("reason"),
        ref.get("proof_surface"),
        ref.get("proof_role"),
        ref.get("target_source_role"),
        ref.get("source_role"),
        ref.get("source_role_label"),
        ref.get("source_tier"),
        ref.get("read_role"),
        source_ref.get("path"),
        metadata.get("payload_type"),
        metadata.get("top_role"),
        metadata.get("message_role"),
    ]
    return " ".join(str(value or "") for value in values).casefold().replace("_", "-")


def _semantic_wrapup_correction_root_path_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    values = [
        item.get("target"),
        item.get("path"),
        ref.get("target"),
        ref.get("path"),
        ref.get("openable_target"),
        source_ref.get("path"),
    ]
    return " ".join(str(value or "") for value in values).replace("\\", "/").casefold().replace("_", "-")


def _semantic_wrapup_correction_root_surface_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    values = [
        item.get("target"),
        item.get("path"),
        ref.get("kind"),
        ref.get("target"),
        ref.get("path"),
        ref.get("openable_target"),
        ref.get("reason"),
        ref.get("proof_surface"),
        ref.get("proof_role"),
        ref.get("target_source_role"),
        ref.get("source_role"),
        ref.get("source_role_label"),
        ref.get("source_tier"),
        ref.get("read_role"),
        source_ref.get("path"),
        metadata.get("top_type"),
        metadata.get("payload_type"),
        metadata.get("payload_role"),
        metadata.get("conversation_label"),
    ]
    return " ".join(str(value or "") for value in values).replace("\\", "/").casefold().replace("_", "-")


def _target_episode_rejected_summary_line(item: dict) -> str:
    target = str(item.get("target") or item.get("path") or "").strip()
    reasons = ", ".join(str(reason) for reason in item.get("target_episode_source_role_reject_reasons") or [])
    prefix = f"{target}: " if target else ""
    return f"{prefix}rejected_target_episode_source_role={reasons}"[:_ASK_ANSWER_MAX_LINE_CHARS]


def _target_episode_source_role_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "route_family": gate.get("route_family"),
        "required_target_ids": list(gate.get("required_target_ids") or []),
        "required_source_role": gate.get("required_source_role"),
        "allowed_ref_count": gate.get("allowed_ref_count", 0),
        "rejected_ref_count": gate.get("rejected_ref_count", 0),
        "required_next_proof": gate.get("required_next_proof"),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "reject_reasons": list(item.get("target_episode_source_role_reject_reasons") or []),
            }
            for item in list(gate.get("rejected_refs") or [])[:_ASK_ANSWER_MAX_REFS]
        ],
    }


def _semantic_wrapup_correction_root_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "route_family": gate.get("route_family"),
        "required_target_ids": list(gate.get("required_target_ids") or []),
        "required_source_role": gate.get("required_source_role"),
        "allowed_ref_count": gate.get("allowed_ref_count", 0),
        "rejected_ref_count": gate.get("rejected_ref_count", 0),
        "required_next_proof": gate.get("required_next_proof"),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "semanticwrapup_correction_root_source_role": item.get(
                    "semanticwrapup_correction_root_source_role"
                ),
                "reject_reasons": list(item.get("target_episode_source_role_reject_reasons") or []),
            }
            for item in list(gate.get("rejected_refs") or [])[:_ASK_ANSWER_MAX_REFS]
        ],
    }


def _target_episode_required_source_role(route_family: str) -> str:
    return {
        "action_proof": "primary raw action/tool proof for the requested task/action",
        "expectation_mismatch": "expected/observed/divergence proof for the requested episode",
        "latest_status": "selected anchor and required primary status refs for the requested task/window",
        "process_actual_use": "primary raw timing/effect proof for the requested process/action",
        "remembered_topic": "primary user-authored instruction wording for the requested topic",
        "semantic_wrapup": "required primary correction-root proof for the requested correction",
    }.get(route_family, "primary proof refs for the requested target episode/entity")


def _target_episode_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict):
        return None
    if next_step.get("status") not in {
        "blocked_target_episode_mismatch",
        "blocked_semanticwrapup_correction_root_source_role",
        "target_primary_proof_refs_mismatch",
        "blocked_latest_status_target_anchor_mismatch",
    }:
        return None
    required_ids = list(next_step.get("required_target_ids") or [])
    required_role = str(next_step.get("required_source_role") or "same-target primary proof")
    lines = list(next_step.get("summary_lines") or [])
    if not lines:
        lines = [
            "Opened refs were rejected because they did not match the requested target episode/entity.",
            f"Needed proof: {required_role} for {', '.join(required_ids) if required_ids else 'the named target'}.",
        ]
    return {
        "schema_version": 1,
        "kind": "target_episode_source_role_proof_required",
        "summary_lines": lines[:_ASK_ANSWER_MAX_REFS],
        "required_target_ids": required_ids,
        "required_source_role": required_role,
        "required_final_proof": next_step.get("required_next_proof"),
        "why_not_final_proof": (
            "Wrong-task raw refs, stale older command/tool refs, skill text, code diffs, implementation "
            "summaries, prior reports/reviews, and generated tool-output windows can be diagnostics or "
            "leads, but they do not support answer-ready or final answers for the named target."
        ),
    }


def _latest_status_target_episode_anchor_gate(*, query: str, action: dict, opened: list[dict]) -> dict:
    required_ids = _explicit_query_episode_ids(query)
    if not required_ids:
        return {"schema_version": 1, "applies": False, "status": "not_applicable"}
    selected_anchor = action.get("selected_anchor") if isinstance(action.get("selected_anchor"), dict) else {}
    anchor_text = _target_episode_ref_match_text(selected_anchor)
    matched_anchor_ids = [target_id for target_id in required_ids if target_id in anchor_text]
    opened_gate = _target_episode_refs_gate(
        query=query,
        route_family="latest_status",
        refs=opened,
        required_source_role=_target_episode_required_source_role("latest_status"),
    )
    completed_artifact_status_refs = _completed_task_artifact_status_opened_refs(
        opened,
        required_ids=required_ids,
    )
    if matched_anchor_ids and opened_gate.get("status") == "answer_allowed":
        return {
            "schema_version": 1,
            "applies": True,
            "status": "answer_allowed",
            "required_target_ids": required_ids,
            "matched_anchor_ids": matched_anchor_ids,
            "opened_gate": _target_episode_source_role_gate_public_contract(opened_gate),
        }
    if completed_artifact_status_refs and opened_gate.get("status") == "answer_allowed":
        return {
            "schema_version": 1,
            "applies": True,
            "status": "answer_allowed",
            "required_target_ids": required_ids,
            "matched_anchor_ids": [],
            "completed_task_artifact_status": True,
            "opened_gate": _target_episode_source_role_gate_public_contract(opened_gate),
        }
    reasons: list[str] = []
    if not matched_anchor_ids:
        reasons.append("selected_anchor_missing_requested_target_id")
    if opened_gate.get("status") != "answer_allowed":
        reasons.append("opened_required_primary_refs_missing_requested_target_id")
    return {
        "schema_version": 1,
        "applies": True,
        "status": "blocked_latest_status_target_anchor_mismatch",
        "required_target_ids": required_ids,
        "required_source_role": _target_episode_required_source_role("latest_status"),
        "matched_anchor_ids": matched_anchor_ids,
        "reasons": reasons,
        "selected_anchor": {
            "source_ref": selected_anchor.get("source_ref") or selected_anchor.get("openable_target"),
            "timestamp_utc": selected_anchor.get("timestamp_utc") or selected_anchor.get("local_timestamp"),
            "snippet": selected_anchor.get("snippet") or selected_anchor.get("text"),
        },
        "opened_gate": _target_episode_source_role_gate_public_contract(opened_gate),
        "summary_lines": [
            "Latest-status target gate rejected the selected anchor or required refs because they did not match "
            f"{', '.join(required_ids)}; reasons={','.join(reasons) if reasons else '(unknown)'}."
        ],
        "required_next_proof": (
            "Select/open a status anchor and required primary status refs that explicitly match the requested "
            f"target episode/entity ({', '.join(required_ids)}). Adjacent task anchors are diagnostics only."
        ),
    }


def _latest_status_target_episode_anchor_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "required_target_ids": list(gate.get("required_target_ids") or []),
        "required_source_role": gate.get("required_source_role"),
        "matched_anchor_ids": list(gate.get("matched_anchor_ids") or []),
        "completed_task_artifact_status": bool(gate.get("completed_task_artifact_status")),
        "reasons": list(gate.get("reasons") or []),
        "selected_anchor": gate.get("selected_anchor") or {},
        "opened_gate": gate.get("opened_gate") or {},
        "required_next_proof": gate.get("required_next_proof"),
    }


def _completed_task_artifact_status_opened_refs(opened: list[dict], *, required_ids: list[str]) -> list[dict]:
    selected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        if not _completed_task_artifact_ref(ref):
            continue
        text = _target_episode_ref_match_text(item)
        if required_ids and not any(task_id in text for task_id in required_ids):
            continue
        if str(ref.get("proof_surface") or "") in {
            "task_file",
            "review_artifact",
            "path_last_change_commit",
            "merge_commit",
            "integration_final_review_commit",
            "post_merge_task_proof",
            "post_merge_review_proof",
        }:
            selected.append(item)
    return selected


_ASK_EXECUTE_NAMED_TASK_PROOF_ROUTE_FAMILIES = frozenset(
    {"action_proof", "semantic_debug", "provenance"}
)


def _named_task_merge_provenance_execute_artifact_handoff(
    *,
    query: str,
    cwd: str,
    route_family: str,
    cli_prefix: str | None = None,
) -> dict:
    if not _named_task_merge_provenance_answer_wanted(query=query, route_family=route_family):
        return {}
    if _raw_worker_chronology_source_role_wanted(query=query, route_family=route_family):
        return {}
    cwd_path = Path(cwd).expanduser().resolve()
    refs = _completed_task_artifact_support_refs(cwd=cwd_path, query=query, route_family=route_family)
    if not refs:
        return {}

    surfaces = {str(ref.get("proof_surface") or "") for ref in refs if isinstance(ref, dict)}
    has_git_integration_proof = bool(
        surfaces.intersection({"merge_commit", "integration_final_review_commit"})
    )
    missing = [
        role
        for role, surface in (
            ("task_artifact_proof", "task_file"),
            ("review_artifact_proof", "review_artifact"),
        )
        if surface not in surfaces
    ]
    if not has_git_integration_proof:
        missing.append("merge_commit_proof")
    if missing:
        return {}
    git_proof_label = (
        "true merge commit"
        if "merge_commit" in surfaces
        else "single-parent integration/final-review commit"
    )
    source_session_requested = _named_task_merge_provenance_source_session_wanted(query)
    retained_source_reads = (
        _completed_task_retained_source_reads(
            cwd=cwd_path,
            refs=refs,
            task_ids=_ask_execute_named_task_ids(query)[:2],
            cli_prefix=cli_prefix or "jsonl-history-search",
        )
        if source_session_requested
        else []
    )
    missing_roles = ["source_session_proof"] if source_session_requested else []
    status = (
        "completed_task_merge_artifact_refs_available_source_session_missing"
        if source_session_requested
        else "completed_task_merge_artifact_refs_available"
    )
    available_roles = ["task_artifact_proof", "review_artifact_proof"]
    if "merge_commit" in surfaces:
        available_roles.append("merge_commit_proof")
    else:
        available_roles.append("integration_commit_proof")
    return {
        "schema_version": 1,
        "status": status,
        "git_proof_kind": git_proof_label,
        "task_ids": _ask_execute_named_task_ids(query)[:2],
        "read": [*retained_source_reads, *refs[:8]],
        "retained_source_reads": retained_source_reads,
        "diagnostics_after": [
            {
                "kind": "semantic_debug_fallback",
                "reason": (
                    "Inspect semantic-debug raw/session diagnostics only after task/review/git artifact "
                    "proof leaves a named merge-provenance facet missing."
                ),
                "link": "semantic_debug.core_convo_first_read_plan",
            }
        ],
        "only_continue_if": [
            "the same-task task artifact, review artifact, and git integration/merge proof do not cover the requested artifact proof",
            "open role-valid same-task raw/session evidence before claiming source-session proof; unopened private handles in artifact prose are leads only",
        ],
        "answer_allowed": not source_session_requested,
        "available_roles": available_roles,
        "missing_roles": missing_roles,
        "blocking_missing_roles": missing_roles,
        "partial_evidence_status": (
            "merge_only_partial_evidence" if source_session_requested else "artifact_proof_available"
        ),
        "source_boundary": (
            "completed task/review/git artifacts can prove completed-task integration provenance artifact roles; "
            "single-parent integration/final-review commits are not true two-parent merge commits, and artifact "
            "prose or unopened private handles do not prove raw source-session chronology"
        ),
        "stop": (
            "Report the task/review/git merge evidence as partial proof, but fail closed on source-session proof "
            "until a role-valid raw/session source is opened. Missing or unavailable private history is a proof "
            "availability boundary, not itself a product defect."
            if source_session_requested
            else (
                "Inspect same-task task/review/git artifact refs first; they satisfy completed-task "
                f"provenance artifact roles through {git_proof_label}, not raw worker/session chronology."
            )
        ),
    }


def _raw_worker_chronology_execute_handoff(*, query: str, route_family: str) -> dict:
    if not _raw_worker_chronology_source_role_wanted(query=query, route_family=route_family):
        return {}
    required_roles = _action_chronology_required_roles(query)
    if "observed_message_or_command_chronology" not in required_roles:
        required_roles = ["observed_message_or_command_chronology", *required_roles]
    required_roles = _unique_preserving_order(required_roles)
    task_ids = _ask_execute_named_task_ids(query)
    return {
        "schema_version": 1,
        "status": "missing_raw_worker_chronology",
        "task_ids": task_ids[:2],
        "required_roles": required_roles,
        "missing_roles": required_roles,
        "read": [
            {
                "kind": "targeted_fail_closed_boundary",
                "reason": (
                    "The query says to use only raw worker chronology. Task files, review artifacts, "
                    "and git commits are corroborating artifacts only and must not be promoted as sufficient proof."
                ),
                "task_ids": task_ids[:2],
                "required_source_roles": required_roles,
                "missing_roles": required_roles,
            }
        ],
        "diagnostics_after": [],
        "stop": (
            "Fail closed until same-task raw worker/session chronology is opened; name the missing "
            "source role instead of using task/review/git artifacts as proof."
        ),
        "only_continue_if": [
            "open raw worker/session messages, tool calls, or command output for the same task id",
            "do not treat task/review/git artifacts as sufficient when the query says raw worker chronology only",
        ],
        "source_boundary": (
            "raw worker chronology requires observed worker/session message, tool-call, or command-output evidence"
        ),
    }


def _raw_worker_chronology_source_role_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in _ASK_EXECUTE_NAMED_TASK_PROOF_ROUTE_FAMILIES:
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    normalized = str(query or "").casefold()
    raw_chronology = (
        "raw worker chronology" in normalized
        or "worker chronology" in normalized
        or ("using only raw" in normalized and "chronology" in normalized)
    )
    if not raw_chronology:
        return False
    return any(term in normalized for term in ("only", "fail closed", "missing source role", "source role is missing"))


def _named_task_action_proof_handoff(
    *,
    query: str,
    cwd: str,
    route_family: str,
    target_proof_available: bool = False,
    allow_current_proof_with_target: bool = False,
) -> dict:
    if route_family not in _ASK_EXECUTE_NAMED_TASK_PROOF_ROUTE_FAMILIES:
        return {}
    if route_family != "action_proof" and target_proof_available and not allow_current_proof_with_target:
        return {}
    task_ids = _ask_execute_named_task_ids(query)
    if not task_ids:
        return {}
    cwd_path = Path(cwd).expanduser().resolve()
    rows: list[dict] = []
    boundaries: list[str] = []
    include_selection_rationale = _named_task_selection_rationale_answer_wanted(
        query=query,
        route_family=route_family,
    )
    include_stopped_before_implementation = _named_task_stopped_before_implementation_answer_wanted(
        query=query,
        route_family=route_family,
    )
    for task_id in task_ids[:2]:
        task_rows, task_boundaries = _current_named_task_action_proof_rows(
            cwd_path,
            task_id,
            include_selection_rationale=include_selection_rationale,
            include_stopped_before_implementation=include_stopped_before_implementation,
        )
        rows.extend(task_rows)
        boundaries.extend(task_boundaries)
    rows = _dedupe_action_contract_rows(rows)
    if rows:
        status = "current_named_task_proof_refs_available"
        if include_stopped_before_implementation and any(
            row.get("proof_surface") == "stopped_before_implementation_causal_proof" for row in rows
        ):
            status = "current_named_task_stopped_episode_proof_refs_available"
        return {
            "schema_version": 1,
            "status": status,
            "task_ids": task_ids[:2],
            "read": rows[:6],
            "diagnostics_after": [
                {
                    "kind": "semantic_debug_fallback",
                    "reason": (
                        "Inspect semantic_debug.action_proof_plan/source_window_action_proof only after "
                        "current named task/review/commit refs leave a named proof facet missing."
                    ),
                    "link": "semantic_debug.action_proof_plan",
                }
            ],
            "stop": (
                "Inspect current named task/review/commit refs before stale raw-session refs; if they answer "
                "whether the named task file was updated, stop broad searching and cite those refs."
            ),
            "only_continue_if": [
                "the current task artifact, review artifact, and path-level commit metadata do not answer the claimed-action question",
                "the current named task artifacts are missing or conflict with inspected raw worker/reviewer/tool proof",
                "a required facet remains missing, such as task-file update, review verdict, implementation commit, or merge/closeout commit",
            ],
            "missing_current_proof_boundaries": boundaries,
        }
    return {
        "schema_version": 1,
        "status": "current_named_task_proof_refs_missing",
        "task_ids": task_ids[:2],
        "read": [
            {
                "kind": "targeted_fail_closed_boundary",
                "reason": (
                    "Named current task proof was requested, but no matching task/review artifact was found "
                    f"under cwd for {', '.join(task_ids[:2])}; do not use stale unrelated raw-session refs "
                    "as primary proof."
                ),
                "task_ids": task_ids[:2],
            }
        ],
        "stop": (
            "Fail closed for the named task until current task/review/commit proof or raw worker/reviewer/tool "
            "proof for that task is inspected."
        ),
        "only_continue_if": [
            "run a targeted check for the named task id under tasks/ and codex/state/reviews/",
            "only then inspect raw worker/reviewer/tool proof that explicitly mentions the same task id",
        ],
        "missing_current_proof_boundaries": boundaries,
    }


def _named_task_clean_answer_surface_wanted(*, query: str, route_family: str) -> bool:
    normalized = str(query or "").casefold()
    if route_family == "semantic_debug" and "what happened" in normalized:
        return bool(_ask_execute_named_task_ids(query))
    if _branch_work_episode_answer_wanted(query=query, route_family=route_family):
        return bool(_ask_execute_named_task_ids(query))
    if _named_task_selection_rationale_answer_wanted(query=query, route_family=route_family):
        return True
    if _named_task_stopped_before_implementation_answer_wanted(query=query, route_family=route_family):
        return True
    return _named_task_review_failure_status_answer_wanted(query=query, route_family=route_family)


def _branch_work_episode_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"action_proof", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    if "what happened" not in normalized:
        return False
    if "branch" not in normalized:
        return False
    return bool(re.search(r"\b[a-z0-9._-]+/[a-z0-9._/-]+\b", normalized))


def _named_task_selection_rationale_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"provenance", "semantic_debug"}:
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    normalized = str(query or "").casefold()
    if "why" not in normalized and "reason" not in normalized and "rationale" not in normalized:
        return False
    return any(
        phrase in normalized
        for phrase in (
            "get selected",
            "got selected",
            "was selected",
            "were selected",
            "selected",
            "get chosen",
            "got chosen",
            "was chosen",
            "chosen",
            "selection rationale",
            "selection reason",
        )
    )


def _named_task_review_failure_status_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"provenance", "semantic_debug"}:
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    normalized = str(query or "").casefold()
    return any(
        term in normalized
        for term in (
            "fail review",
            "failed review",
            "review fail",
            "review failure",
            "fail verification",
            "failed verification",
            "verification fail",
            "verification failure",
        )
    )


def _named_task_review_failure_causal_answer_wanted(*, query: str, route_family: str) -> bool:
    normalized = str(query or "").casefold()
    if not any(term in normalized for term in ("why", "cause", "caused", "reason")):
        return False
    return _named_task_review_failure_status_answer_wanted(query=query, route_family=route_family)


def _named_task_stopped_before_implementation_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"provenance", "semantic_debug"}:
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    normalized = str(query or "").casefold()
    if not any(term in normalized for term in ("why", "reason", "cause", "caused")):
        return False
    stopped = any(
        phrase in normalized
        for phrase in (
            "stopped before implementation",
            "stopped before implementing",
            "aborted before implementation",
            "aborted before implementing",
            "cancelled before implementation",
            "canceled before implementation",
        )
    )
    return stopped


def _ask_execute_named_task_ids(query: str) -> list[str]:
    task_ids: list[str] = []
    for match in _ASK_EXECUTE_TASK_ID_RE.finditer(query or ""):
        prefix = match.group(1).upper()
        if prefix in _ASK_EXECUTE_TASK_ID_MONTH_PREFIXES:
            continue
        if prefix != "JSH":
            continue
        task_id = f"{prefix}-{int(match.group(2))}"
        if task_id not in task_ids:
            task_ids.append(task_id)
    return task_ids


def _current_named_task_action_proof_rows(
    cwd: Path,
    task_id: str,
    *,
    include_selection_rationale: bool = False,
    include_stopped_before_implementation: bool = False,
) -> tuple[list[dict], list[str]]:
    slug = task_id.lower()
    rows: list[dict] = []
    boundaries: list[str] = []
    task_paths = _matching_named_task_artifacts(cwd / "tasks", slug)
    review_paths = _matching_named_task_artifacts(cwd / "codex" / "state" / "reviews", slug)

    if include_selection_rationale:
        selection_rows, selection_boundaries = _current_named_task_selection_rationale_rows(
            cwd,
            task_id=task_id,
            task_paths=task_paths,
        )
        rows.extend(selection_rows)
        boundaries.extend(selection_boundaries)

    if include_stopped_before_implementation:
        stopped_rows, stopped_boundaries = _current_named_task_stopped_before_implementation_rows(
            cwd,
            task_id=task_id,
        )
        rows.extend(stopped_rows)
        boundaries.extend(stopped_boundaries)

    if task_paths:
        for path in task_paths[:2]:
            rows.append(
                _named_task_artifact_read(
                    cwd,
                    path,
                    task_id=task_id,
                    proof_surface="task_file",
                    reason=f"inspect current {task_id} task artifact; verify progress log and task-file updates",
                )
            )
    else:
        boundaries.append(f"{task_id}: no matching tasks/{slug}*.md artifact found under cwd")

    if review_paths:
        for path in review_paths[:2]:
            rows.append(
                _named_task_artifact_read(
                    cwd,
                    path,
                    task_id=task_id,
                    proof_surface="review_artifact",
                    reason=f"inspect current {task_id} review artifact; verify reviewer verdict and implementation evidence",
                )
            )
    else:
        boundaries.append(f"{task_id}: no matching codex/state/reviews/{slug}*.md artifact found under cwd")

    repo = git_root_for(cwd) or cwd
    for path in [*task_paths[:1], *review_paths[:1]]:
        rel = _safe_relative_path(cwd, path)
        change = last_change_for_path(path, repo=repo)
        if change:
            rows.append(
                {
                    "kind": "current_named_task_commit_evidence",
                    "task_id": task_id,
                    "proof_surface": "path_last_change_commit",
                    "target": change.commit,
                    "commit": change.commit,
                    "commit_time": change.commit_time,
                    "subject": change.subject,
                    "path": change.path,
                    "command": f"git show --stat --oneline {change.commit} -- {change.path}",
                    "reason": f"inspect path-level commit evidence for {rel}",
                }
            )
        elif rel:
            rows.append(
                {
                    "kind": "current_named_task_commit_probe",
                    "task_id": task_id,
                    "proof_surface": "path_git_log_probe",
                    "target": rel,
                    "command": f"git log --oneline -- {rel}",
                    "reason": f"targeted commit-history probe for {rel}",
                }
            )
            boundaries.append(f"{task_id}: path-level commit metadata unavailable for {rel}")

    return rows, boundaries


_COMPLETED_TASK_ARTIFACT_ROUTE_FAMILIES = frozenset(
    {"action_proof", "latest_status", "orchestration_provenance", "provenance", "semantic_debug"}
)


def _completed_task_artifact_support_refs(*, cwd: Path, query: str, route_family: str) -> list[dict]:
    if route_family not in _COMPLETED_TASK_ARTIFACT_ROUTE_FAMILIES:
        return []
    task_ids = _ask_execute_named_task_ids(query)
    if not task_ids:
        return []
    refs: list[dict] = []
    repo = git_root_for(cwd) or cwd
    post_merge_wanted = _completed_task_post_merge_artifact_wanted(query=query, route_family=route_family)
    for task_id in task_ids[:2]:
        slug = task_id.lower()
        task_paths = _matching_named_task_artifacts(cwd / "tasks", slug)
        review_paths = _matching_named_task_artifacts(cwd / "codex" / "state" / "reviews", slug)
        for path in task_paths[:2]:
            refs.append(
                _completed_task_artifact_read(
                    cwd,
                    path,
                    task_id=task_id,
                    proof_surface="task_file",
                    reason=f"inspect completed {task_id} task artifact for status/provenance proof",
                )
            )
            if post_merge_wanted:
                post_merge = _completed_task_post_merge_summary(path, task_id=task_id)
                if post_merge:
                    refs.append(
                        _completed_task_embedded_summary_read(
                            cwd,
                            path,
                            task_id=task_id,
                            proof_surface="post_merge_task_proof",
                            reason=f"inspect completed {task_id} task post-merge proof section/handle",
                            summary=post_merge,
                        )
                    )
        for path in review_paths[:2]:
            refs.append(
                _completed_task_artifact_read(
                    cwd,
                    path,
                    task_id=task_id,
                    proof_surface="review_artifact",
                    reason=f"inspect completed {task_id} review artifact for verdict/provenance proof",
                )
            )
            if post_merge_wanted:
                post_merge = _completed_task_post_merge_summary(path, task_id=task_id)
                if post_merge:
                    refs.append(
                        _completed_task_embedded_summary_read(
                            cwd,
                            path,
                            task_id=task_id,
                            proof_surface="post_merge_review_proof",
                            reason=f"inspect completed {task_id} review/post-merge verification proof",
                            summary=post_merge,
                        )
                    )
        refs.extend(
            _completed_task_merge_commit_refs(
                repo=repo,
                task_id=task_id,
                artifact_paths=[*task_paths[:2], *review_paths[:2]],
            )
        )
        for path in [*task_paths[:1], *review_paths[:1]]:
            change = last_change_for_path(path, repo=repo)
            if change:
                refs.append(
                    {
                        "kind": "completed_named_task_commit_evidence",
                        "task_id": task_id,
                        "proof_surface": "path_last_change_commit",
                        "target_source_role": "completed_task_artifact",
                        "final_support_allowed": True,
                        "target": change.commit,
                        "commit": change.commit,
                        "commit_time": change.commit_time,
                        "subject": change.subject,
                        "path": change.path,
                        "reason": f"inspect path-level completed-task commit evidence for {_safe_relative_path(cwd, path)}",
                        "source_boundary": (
                            "completed task/review git metadata; valid for artifact status/provenance roles, "
                            "not raw source-session chronology"
                        ),
                    }
                )
        refs.extend(
            _completed_task_integration_commit_refs(
                repo=repo,
                cwd=cwd,
                task_id=task_id,
                artifact_paths=[*task_paths[:2], *review_paths[:2]],
            )
        )
    return _dedupe_ask_answer_proof_refs(refs)


_PRIVATE_HANDLE_PATTERN = re.compile(
    r"private:(?P<path>[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*/?)"
)
_RETAINED_SOURCE_REJECTED_NAME_PARTS = frozenset(
    {
        "adjudication",
        "adjudications",
        "answer",
        "answers",
        "expected",
        "expecteds",
        "final",
        "finals",
        "report",
        "reports",
        "result",
        "results",
        "review",
        "reviews",
        "rubric",
        "rubrics",
        "scratch",
        "scratches",
        "summaries",
        "summary",
    }
)
_RETAINED_SOURCE_MAX_REFS = 4
_RETAINED_HANDLE_ARTIFACT_MAX_CHARS = 64 * 1024


def _completed_task_retained_source_reads(
    *,
    cwd: Path,
    refs: list[dict],
    task_ids: list[str],
    cli_prefix: str,
) -> list[dict]:
    handles: list[tuple[str, str]] = []
    for ref in refs[:8]:
        if str(ref.get("proof_surface") or "") not in {"task_file", "review_artifact"}:
            continue
        target = str(ref.get("path") or ref.get("target") or "")
        path = Path(target)
        if not path.is_absolute():
            path = cwd / path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:_RETAINED_HANDLE_ARTIFACT_MAX_CHARS]
        except OSError:
            continue
        task_id = str(ref.get("task_id") or "").upper()
        if task_id not in task_ids:
            continue
        for match in _PRIVATE_HANDLE_PATTERN.finditer(text):
            handle = f"private:{match.group('path')}"
            if _retained_handle_matches_task(handle, task_id=task_id) and (task_id, handle) not in handles:
                handles.append((task_id, handle))
    return [
        {
            "kind": "bounded_retained_source_session_read",
            "task_id": task_id,
            "handle": handle,
            "command": (
                f"{cli_prefix} retained-source-read {shlex.quote(handle)} "
                f"--task {shlex.quote(task_id)} --cwd \"$PWD\" --format text"
            ),
            "reason": "open a bounded same-task retained raw/session lead before claiming source-session proof",
            "target_source_role": "source_session_lead",
            "final_support_allowed": False,
            "source_boundary": "the command must open role-valid raw evidence; this unopened handle remains a lead only",
        }
        for task_id, handle in handles[:2]
    ]


def _retained_handle_matches_task(handle: str, *, task_id: str) -> bool:
    match = _PRIVATE_HANDLE_PATTERN.fullmatch(str(handle or ""))
    if not match:
        return False
    first_segment = match.group("path").rstrip("/").split("/", 1)[0].casefold()
    task_token = str(task_id or "").casefold()
    return first_segment == task_token or first_segment.startswith(f"{task_token}-")


def _retained_source_candidate_paths(
    root: Path,
    *,
    retained_task_root: Path,
    max_files: int,
) -> list[Path]:
    if root.is_file():
        return (
            [root]
            if _retained_source_candidate_allowed(root, retained_task_root=retained_task_root)
            else []
        )
    if not root.is_dir():
        return []
    candidates: list[Path] = []
    directories = [root]
    directory_index = 0
    while directory_index < len(directories) and directory_index < 32:
        directory = directories[directory_index]
        directory_index += 1
        try:
            children = sorted(islice(directory.iterdir(), 64), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for child in children:
            if child.is_symlink():
                continue
            if child.is_dir() and len(directories) < 32:
                directories.append(child)
            elif child.is_file() and _retained_source_candidate_allowed(
                child,
                retained_task_root=retained_task_root,
            ):
                candidates.append(child)
                if len(candidates) >= max_files:
                    return candidates
    return candidates


def _retained_source_candidate_allowed(path: Path, *, retained_task_root: Path) -> bool:
    if path.suffix.casefold() not in {".jsonl", ".json"}:
        return False
    try:
        relative = path.relative_to(retained_task_root)
    except ValueError:
        return False
    path_tokens = {
        token
        for component in relative.parts
        for token in re.split(r"[^a-z0-9]+", component.casefold())
        if token
    }
    return not path_tokens.intersection(_RETAINED_SOURCE_REJECTED_NAME_PARTS)


def _retained_source_record(raw: str, *, fallback: str) -> tuple[dict, str] | None:
    metadata = _ask_answer_jsonl_record_metadata(raw)
    summary = _summarize_jsonl_or_text_line(raw, fallback=fallback)
    if metadata and summary and (
        metadata.get("conversation_label") != "unknown" or metadata.get("top_role")
    ):
        return metadata, summary
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    top_type = str(row.get("type") or "")
    if top_type == "thread.started" and str(row.get("thread_id") or "").strip():
        return (
            {
                "top_type": top_type,
                "conversation_label": "source_session_metadata",
                "conversation_confidence": "high",
                "is_user_authored": False,
            },
            f"source session {row['thread_id']}",
        )
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if top_type != "item.completed" or item_type not in {"agent_message", "command_execution"}:
        return None
    if item_type == "agent_message":
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        label = "assistant_message"
        summary = f"assistant: {text[:_ASK_ANSWER_MAX_LINE_CHARS]}"
    else:
        command = str(item.get("command") or "").strip()
        output = str(item.get("aggregated_output") or "").strip()
        if not command and not output:
            return None
        label = "tool_action_output"
        summary = _short_join(
            [
                f"tool call: {command[:_ASK_ANSWER_MAX_LINE_CHARS]}" if command else "",
                f"tool output: {output[:_ASK_ANSWER_MAX_LINE_CHARS]}" if output else "",
            ],
            limit=_ASK_ANSWER_MAX_LINE_CHARS,
        )
    return (
        {
            "top_type": top_type,
            "item_type": item_type,
            "conversation_label": label,
            "conversation_confidence": "high",
            "is_user_authored": False,
        },
        summary,
    )


def _retained_source_record_score(*, metadata: dict, summary: str, task_id: str) -> int:
    label = str(metadata.get("conversation_label") or "")
    normalized = summary.casefold()
    if label == "source_session_metadata":
        return 10_000
    score = 300 if label == "tool_action_output" else 250
    if task_id.casefold() in normalized:
        score += 500
    if "ask --execute" in normalized:
        score += 400
    if any(marker in normalized for marker in ("report_output_unavailable", "read-only", "read only")):
        score += 300
    if any(marker in normalized for marker in ("verification", "verified", "pytest", "git diff --check")):
        score += 150
    if "skill.md" in normalized or "skills/jsonl-history-search" in normalized:
        score -= 700
    return score


def _retained_source_tool_record_kind(metadata: dict) -> str | None:
    payload_type = str(metadata.get("payload_type") or "").casefold()
    if payload_type in {"function_call", "custom_tool_call"}:
        return "call"
    if payload_type in {"function_call_output", "custom_tool_call_output"}:
        return "output"
    return None


def _retained_source_missing_session_payload(report: dict, *, reason: str) -> dict:
    report["reason"] = reason
    report["missing_source_session_stop_contract"] = {
        "schema_version": 1,
        "contract_kind": "missing-source-session-stop",
        "status": "fail-closed/missing-source-session-proof",
        "facet": "source_session_proof",
        "answer_allowed": False,
        "terminal_for_checked_handle": True,
        "opened_role_valid_refs": 0,
        "secondary_artifact_policy": {
            surface: "lead_or_post_hoc_context_only_not_source_session_proof"
            for surface in ("task", "review", "scratch", "report", "summary")
        },
        "preserved_fact_facets": [
            "independently_verified_git_facts",
            "task_artifact_facts",
            "review_artifact_facts",
        ],
        "stop": (
            "Stop source-session recovery for this checked handle. Do not open or use task, review, "
            "scratch, report, or summary artifacts to replace the missing source-session proof. "
            "Report only independently verified Git/task/review facts as separate facets."
        ),
        "unblock": (
            "Make this retained handle available with a role-valid same-task raw JSONL/session record, "
            "or provide and open another role-valid same-task raw/session source."
        ),
    }
    return {"retained_source_read": report}


def build_retained_source_read_payload(
    *,
    handle: str,
    task_id: str,
    cwd: Path,
    max_files: int,
    max_bytes: int,
) -> dict:
    task_id = str(task_id or "").upper()
    limits = {
        "max_files": max(1, min(int(max_files), 16)),
        "max_bytes": max(1024, min(int(max_bytes), 1024 * 1024)),
        "max_refs": _RETAINED_SOURCE_MAX_REFS,
    }
    report = {
        "schema_version": 1,
        "status": "unavailable",
        "task_id": task_id,
        "handle": handle,
        "opened_refs": [],
        "limits": limits,
        "proof_boundary": "unopened or unavailable private handles are leads only, never source-session proof",
    }
    if not re.fullmatch(r"JSH-\d+", task_id) or not _retained_handle_matches_task(handle, task_id=task_id):
        return _retained_source_missing_session_payload(
            report,
            reason="invalid_or_cross_task_private_handle",
        )

    relative = _PRIVATE_HANDLE_PATTERN.fullmatch(handle).group("path").rstrip("/")
    private_root = (cwd / ".private").resolve()
    retained_root = (private_root / relative).resolve()
    if retained_root != private_root and private_root not in retained_root.parents:
        return _retained_source_missing_session_payload(
            report,
            reason="private_handle_escaped_retained_root",
        )
    retained_task_root = (private_root / relative.split("/", 1)[0]).resolve()
    candidates = _retained_source_candidate_paths(
        retained_root,
        retained_task_root=retained_task_root,
        max_files=limits["max_files"],
    )
    if not candidates:
        return _retained_source_missing_session_payload(
            report,
            reason="retained_handle_unavailable_or_no_raw_candidates",
        )

    bytes_read = 0
    candidate_files_opened = 0
    post_hoc_echo_records_rejected = 0
    ambiguous_source_session_records_rejected = 0
    source_session_outputs_rejected_without_affirmative_call = 0
    source_session_metadata_records_seen = 0
    substantive_source_paths: set[str] = set()
    candidate_refs: list[dict] = []
    for path in candidates:
        remaining = limits["max_bytes"] - bytes_read
        if remaining <= 0:
            break
        try:
            with path.open("rb") as handle_stream:
                raw_content = handle_stream.read(remaining)
        except OSError:
            continue
        candidate_files_opened += 1
        bytes_read += len(raw_content)
        content = raw_content.decode("utf-8", errors="replace")
        source_records: list[dict] = []
        for line_number, raw in enumerate(content.splitlines()[:256], start=1):
            record = _retained_source_record(raw, fallback=f"{path.name}:{line_number}")
            if not record:
                continue
            metadata, summary = record
            rel = _safe_relative_path(cwd, path)
            ref = {
                "kind": "jsonl_source_ref",
                "target": f"{rel}:{line_number}",
                "source_ref": {"path": rel, "line": line_number},
                "target_source_role": "source_session_proof",
                "final_support_allowed": True,
                "summary": summary,
                "record_metadata": metadata,
                "_retained_source_score": _retained_source_record_score(
                    metadata=metadata,
                    summary=summary,
                    task_id=task_id,
                ),
            }
            opened_item = {
                "status": "opened",
                "target": ref["target"],
                "path": rel,
                "summary": summary,
                "record_metadata": metadata,
                "ref": ref,
            }
            source_records.append(
                {
                    "metadata": metadata,
                    "ref": ref,
                    "opened_item": opened_item,
                }
            )

        affirmative_call_reasons: dict[str, str] = {}
        rejected_call_ids: set[str] = set()
        for source_record in source_records:
            metadata = source_record["metadata"]
            if _retained_source_tool_record_kind(metadata) != "call":
                continue
            call_id = str(metadata.get("tool_call_correlation_id") or "").strip()
            if not call_id:
                continue
            opened_item = source_record["opened_item"]
            original_reason = None
            if _named_task_source_session_post_hoc_echo_reason(opened_item) is not None:
                rejected_call_ids.add(call_id)
            else:
                original_reason = _named_task_source_session_original_chronology_reason(
                    opened_item
                )
            if original_reason is None:
                rejected_call_ids.add(call_id)
                affirmative_call_reasons.pop(call_id, None)
                continue
            if call_id not in rejected_call_ids:
                affirmative_call_reasons[call_id] = original_reason

        for source_record in source_records:
            metadata = source_record["metadata"]
            ref = source_record["ref"]
            opened_item = source_record["opened_item"]
            if _named_task_source_session_post_hoc_echo_reason(opened_item) is not None:
                post_hoc_echo_records_rejected += 1
                continue
            if metadata.get("conversation_label") == "source_session_metadata":
                source_session_metadata_records_seen += 1
                candidate_refs.append(ref)
                continue
            if _retained_source_tool_record_kind(metadata) == "output":
                call_id = str(metadata.get("tool_call_correlation_id") or "").strip()
                associated_reason = (
                    affirmative_call_reasons.get(call_id)
                    if call_id and call_id not in rejected_call_ids
                    else None
                )
                if associated_reason is None:
                    source_session_outputs_rejected_without_affirmative_call += 1
                    ambiguous_source_session_records_rejected += 1
                    continue
                metadata["associated_call_original_chronology_reason"] = associated_reason
            original_reason = _named_task_source_session_original_chronology_reason(opened_item)
            if original_reason is None:
                ambiguous_source_session_records_rejected += 1
                continue
            ref["source_session_original_chronology_reason"] = original_reason
            if not _opened_named_task_source_session_proof(opened_item):
                continue
            substantive_source_paths.add(rel)
            candidate_refs.append(ref)
            if len(candidate_refs) >= 64:
                break
        if len(candidate_refs) >= 64:
            break
    candidate_refs = [
        ref
        for ref in candidate_refs
        if str((ref.get("record_metadata") or {}).get("conversation_label") or "")
        != "source_session_metadata"
        or str((ref.get("source_ref") or {}).get("path") or "") in substantive_source_paths
    ]
    candidate_refs.sort(
        key=lambda ref: (
            -int(ref.get("_retained_source_score") or 0),
            str(ref.get("target") or ""),
        )
    )
    opened_refs = []
    for ref in candidate_refs[: limits["max_refs"]]:
        public = dict(ref)
        public.pop("_retained_source_score", None)
        opened_refs.append(public)
    report["candidate_files_opened"] = candidate_files_opened
    report["bytes_read"] = bytes_read
    report["post_hoc_echo_records_rejected"] = post_hoc_echo_records_rejected
    report["ambiguous_source_session_records_rejected"] = ambiguous_source_session_records_rejected
    report["source_session_outputs_rejected_without_affirmative_call"] = (
        source_session_outputs_rejected_without_affirmative_call
    )
    report["source_session_metadata_records_seen"] = source_session_metadata_records_seen
    report["opened_refs"] = opened_refs
    if opened_refs:
        report["status"] = "opened_role_valid_source_session_proof"
        report["reason"] = "bounded_same_task_raw_source_session_evidence_opened"
    else:
        return _retained_source_missing_session_payload(
            report,
            reason=(
                "raw_candidates_opened_but_no_original_chronology_records_found"
                if ambiguous_source_session_records_rejected
                else (
                    "raw_candidates_opened_but_only_post_hoc_echo_records_found"
                    if post_hoc_echo_records_rejected
                    else "raw_candidates_opened_but_no_role_valid_same_task_record_found"
                )
            ),
        )
    return {"retained_source_read": report}


def _completed_task_merge_commit_refs(
    *,
    repo: Path,
    task_id: str,
    artifact_paths: list[Path],
) -> list[dict]:
    candidates: list[str] = []
    for path in artifact_paths:
        candidates.extend(_completed_task_artifact_merge_commit_candidates(path, task_id=task_id))
    candidates.extend(
        str(commit.get("commit") or "")
        for commit in merge_commits_for_task(repo, task_id, max_commits=3)
    )

    refs: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip().lower()
        if not normalized or normalized in seen:
            continue
        metadata = commit_metadata(repo, normalized)
        if metadata is None or metadata.parent_count < 2:
            continue
        if not commit_is_reachable(repo, metadata.commit):
            continue
        seen.add(metadata.commit)
        meta = metadata.to_dict()
        refs.append(
            {
                "kind": "completed_named_task_merge_commit_evidence",
                "task_id": task_id,
                "proof_surface": "merge_commit",
                "target_source_role": "completed_task_artifact",
                "proof_role": "reachable_true_merge_commit",
                "final_support_allowed": True,
                "target": meta.get("commit"),
                "commit": meta.get("commit"),
                "commit_time": meta.get("commit_time"),
                "author": meta.get("author"),
                "committer": meta.get("committer"),
                "subject": meta.get("subject"),
                "parents": list(meta.get("parents") or []),
                "parent_count": meta.get("parent_count"),
                "contained_branches": list(meta.get("contained_branches") or []),
                "is_true_merge_commit": True,
                "reason": f"inspect reachable local git merge commit evidence for completed {task_id}",
                "source_boundary": (
                    "reachable local git commit metadata validates two or more parents; satisfies "
                    "merge-commit proof only, not raw source-session proof"
                ),
            }
        )
    return refs


def _completed_task_artifact_merge_commit_candidates(path: Path, *, task_id: str) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    task_token = task_id.casefold()
    candidates: list[str] = []
    for raw in lines:
        normalized = raw.casefold()
        if "merge" not in normalized:
            continue
        mentioned_tasks = set(re.findall(r"\bjsh-\d+\b", normalized))
        if mentioned_tasks and mentioned_tasks != {task_token}:
            continue
        candidates.extend(
            match.group(0).lower()
            for match in re.finditer(r"\b[0-9a-f]{7,40}\b", raw, flags=re.IGNORECASE)
        )
    return candidates


def _completed_task_integration_commit_refs(
    *,
    repo: Path,
    cwd: Path,
    task_id: str,
    artifact_paths: list[Path],
) -> list[dict]:
    refs: list[dict] = []
    seen: set[str] = set()
    artifact_rel_paths = {
        _safe_relative_path(repo, path)
        for path in artifact_paths
        if _safe_relative_path(repo, path)
    }
    for path in artifact_paths:
        for candidate in _completed_task_integration_commit_candidates(path, task_id=task_id):
            commit = str(candidate.get("commit") or "").lower()
            if commit in seen:
                continue
            metadata = commit_metadata(repo, commit)
            if metadata is None:
                continue
            meta = metadata.to_dict()
            parent_count = int(meta.get("parent_count") or 0)
            if parent_count != 1:
                continue
            changed_paths = {
                str(item.get("path") or "")
                for item in meta.get("changed_files") or []
                if isinstance(item, dict)
            }
            task_token = task_id.casefold()
            same_task_by_subject = task_token in str(meta.get("subject") or "").casefold()
            same_task_by_changed_file = bool(artifact_rel_paths.intersection(changed_paths))
            same_task_by_artifact_line = task_token in str(candidate.get("line_text") or "").casefold()
            if not (same_task_by_subject or same_task_by_changed_file or same_task_by_artifact_line):
                continue
            seen.add(commit)
            rel = _safe_relative_path(cwd, path)
            role = str(candidate.get("role") or "integration_commit")
            refs.append(
                {
                    "kind": "completed_named_task_integration_commit_evidence",
                    "task_id": task_id,
                    "proof_surface": "integration_final_review_commit",
                    "target_source_role": "completed_task_artifact",
                    "proof_role": "single_parent_integration_or_final_review_commit",
                    "final_support_allowed": True,
                    "target": meta.get("commit"),
                    "commit": meta.get("commit"),
                    "commit_time": meta.get("commit_time"),
                    "author": meta.get("author"),
                    "committer": meta.get("committer"),
                    "subject": meta.get("subject"),
                    "parents": list(meta.get("parents") or []),
                    "parent_count": parent_count,
                    "contained_branches": list(meta.get("contained_branches") or []),
                    "is_true_merge_commit": False,
                    "commit_role": role,
                    "artifact_path": rel,
                    "reason": (
                        f"inspect local git single-parent {_completed_task_integration_commit_role_label(role)} proof for completed {task_id}; "
                        "this is integration/final-review proof, not a true merge commit"
                    ),
                    "source_boundary": (
                        "local git metadata validates a same-task single-parent integration/final-review commit; "
                        "this does not satisfy raw source-session chronology and must not be called a true merge commit"
                    ),
                }
            )
    return refs


def _completed_task_integration_commit_role_label(role: str) -> str:
    return {
        "final_review_commit": "final-review commit",
        "integration_commit": "integration commit",
        "finalization_commit": "finalization commit",
    }.get(str(role or ""), "integration/final-review commit")


def _completed_task_integration_commit_candidates(path: Path, *, task_id: str) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    candidates: list[dict] = []
    for index, raw in enumerate(lines, start=1):
        line = raw.strip()
        normalized = line.casefold()
        role = ""
        if "final review commit" in normalized:
            role = "final_review_commit"
        elif (
            ("merged on" in normalized or "integrated on" in normalized or "integration commit" in normalized)
            and any(marker in normalized for marker in ("master", "main", "parent", "integration"))
        ):
            role = "integration_commit"
        elif "finalization commit" in normalized and task_id.casefold() in normalized:
            role = "finalization_commit"
        if not role:
            continue
        for match in re.finditer(r"\b[0-9a-f]{7,40}\b", line, flags=re.IGNORECASE):
            candidates.append(
                {
                    "commit": match.group(0).lower(),
                    "line": index,
                    "line_text": line,
                    "role": role,
                }
            )
    return candidates


def _completed_task_post_merge_artifact_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in _COMPLETED_TASK_ARTIFACT_ROUTE_FAMILIES:
        return False
    normalized = str(query or "").casefold()
    return any(marker in normalized for marker in ("post-merge", "post merge", "postmerge"))


def _completed_task_artifact_read(
    cwd: Path,
    path: Path,
    *,
    task_id: str,
    proof_surface: str,
    reason: str,
) -> dict:
    rel = _safe_relative_path(cwd, path)
    return {
        "kind": "completed_named_task_artifact",
        "task_id": task_id,
        "proof_surface": proof_surface,
        "target_source_role": "completed_task_artifact",
        "final_support_allowed": True,
        "target": rel,
        "path": rel,
        "reason": reason,
        "source_boundary": (
            "completed repo artifact; valid for completed-task status/provenance roles, "
            "not raw source-session or worker chronology proof"
        ),
    }


def _completed_task_embedded_summary_read(
    cwd: Path,
    path: Path,
    *,
    task_id: str,
    proof_surface: str,
    reason: str,
    summary: str,
) -> dict:
    rel = _safe_relative_path(cwd, path)
    return {
        "kind": "completed_named_task_embedded_summary",
        "task_id": task_id,
        "proof_surface": proof_surface,
        "target_source_role": "completed_task_artifact",
        "proof_role": "post_merge_proof_bundle",
        "final_support_allowed": True,
        "target": rel,
        "path": rel,
        "summary": summary,
        "reason": reason,
        "source_boundary": (
            "completed task/review post-merge proof text or retained private handle; "
            "valid for post-merge artifact role without exposing raw private paths"
        ),
    }


def _completed_task_post_merge_summary(path: Path, *, task_id: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    selected: list[str] = []
    in_post_merge_section = False
    section_level = 0
    for raw in lines:
        stripped = raw.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip().casefold()
            if in_post_merge_section and level <= section_level:
                in_post_merge_section = False
            if "post-merge proof" in title or "post merge proof" in title:
                in_post_merge_section = True
                section_level = level
                continue
        folded = stripped.casefold()
        if in_post_merge_section or (
            task_id.casefold() in folded
            and any(marker in folded for marker in ("post-merge", "post merge", "postmerge"))
        ):
            cleaned = stripped.lstrip("-* ").strip()
            if cleaned:
                selected.append(cleaned)
        if len(selected) >= 8:
            break
    return _short_join(selected, limit=900)


def _current_named_task_stopped_before_implementation_rows(
    cwd: Path,
    *,
    task_id: str,
) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    boundaries: list[str] = []
    candidate_paths = [
        cwd / "codex" / "state" / "pipeline-state.md",
        cwd / "TODO.md",
        cwd / "reports" / "jsh-1307-outside-user-explicit-family-discovery.md",
        cwd / "reports" / "jsh-1316-beta-usefulness-explicit-family-gate.md",
        cwd / "reports" / "jsh-1328-post-slice-fixes-beta-usefulness-regate.md",
    ]
    for path in candidate_paths:
        matches = _stopped_before_implementation_matches(path, task_id=task_id)
        if not matches:
            if path.exists():
                boundaries.append(f"{task_id}: no stopped-before-implementation causal proof found in {_safe_relative_path(cwd, path)}")
            continue
        for match in matches[:1]:
            rows.append(
                {
                    "kind": "current_named_task_artifact",
                    "task_id": task_id,
                    "proof_surface": "stopped_before_implementation_causal_proof",
                    "target": f"{_safe_relative_path(cwd, path)}:{match['line']}",
                    "path": _safe_relative_path(cwd, path),
                    "line": match["line"],
                    "reason": (
                        f"inspect stable local artifact proof explaining why {task_id} "
                        "was stopped before implementation"
                    ),
                    "summary": match["summary"],
                    "source_boundary": (
                        "stable repo task/state/report artifact; current-run tool outputs, "
                        "read-more echoes, slice prompts, and post-hoc diagnostics are not final support"
                    ),
                    "matched_terms": match["matched_terms"],
                }
            )
        if rows:
            break
    if not rows:
        boundaries.append(
            f"{task_id}: no stable task/state/report artifact contained both stopped-before-implementation "
            "wording and a clean causal reason"
        )
    return rows, boundaries


def _stopped_before_implementation_matches(path: Path, *, task_id: str) -> list[dict]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    task_lower = task_id.casefold()
    matches: list[dict] = []
    for index, raw in enumerate(lines, start=1):
        if task_lower not in raw.casefold():
            continue
        window_start = max(0, index - 3)
        window_end = min(len(lines), index + 3)
        window_text = " ".join(line.strip() for line in lines[window_start:window_end] if line.strip())
        matched_terms = _stopped_before_implementation_support_terms(window_text, task_id=task_id)
        if matched_terms:
            matches.append(
                {
                    "line": index,
                    "matched_terms": matched_terms,
                    "summary": _short_join([window_text], limit=_ASK_ANSWER_MAX_FILE_CHARS),
                }
            )
    return matches[:4]


def _stopped_before_implementation_support_terms(text: str, *, task_id: str) -> list[str]:
    normalized = str(text or "").casefold()
    if task_id.casefold() not in normalized:
        return []
    stop_terms = [
        "stopped before implementation",
        "stopped before implementing",
        "aborted before implementation",
        "aborted before implementing",
        "worker was stopped",
        "worker stopped",
    ]
    cause_terms = [
        "mostly admin/docs",
        "admin/docs",
        "currentness",
        "rather than a real product fix",
        "not a real product fix",
        "false start",
    ]
    cleanup_terms = [
        "empty worktree",
        "empty branch",
        "removed",
        "reverted",
    ]
    matched: list[str] = []
    if any(term in normalized for term in stop_terms):
        matched.append("stopped_before_implementation")
    if any(term in normalized for term in cause_terms):
        matched.append("clean_causal_reason")
    if any(term in normalized for term in cleanup_terms):
        matched.append("cleanup_outcome")
    if {"stopped_before_implementation", "clean_causal_reason"}.issubset(matched):
        return matched
    return []


def _current_named_task_selection_rationale_rows(
    cwd: Path,
    *,
    task_id: str,
    task_paths: list[Path],
) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    boundaries: list[str] = []
    if task_paths:
        task_summary = _named_task_selection_summary_from_task_file(task_paths[0])
        if task_summary:
            rel = _safe_relative_path(cwd, task_paths[0])
            rows.append(
                {
                    "kind": "current_named_task_selection_rationale",
                    "task_id": task_id,
                    "proof_surface": "task_selection_rationale",
                    "target": rel,
                    "path": rel,
                    "summary": task_summary,
                    "reason": (
                        f"inspect current {task_id} task Context/Goal/Selection Notes "
                        "before raw transcript diagnostics"
                    ),
                    "source_boundary": "current repo task selection rationale; prefer over stale/raw tool-output transcript rows",
                }
            )
        else:
            boundaries.append(f"{task_id}: task artifact did not expose Context/Goal/Selection Notes")
    pipeline_path = cwd / "codex" / "state" / "pipeline-state.md"
    pipeline_summary = _named_task_selection_summary_from_pipeline_state(pipeline_path, task_id=task_id)
    if pipeline_summary:
        rel = _safe_relative_path(cwd, pipeline_path)
        rows.append(
            {
                "kind": "current_named_task_selection_rationale",
                "task_id": task_id,
                "proof_surface": "pipeline_selection_result",
                "target": rel,
                "path": rel,
                "summary": pipeline_summary,
                "reason": f"inspect pipeline Current {task_id} selection result before raw transcript diagnostics",
                "source_boundary": "current pipeline selection result; prefer over stale/raw tool-output transcript rows",
            }
        )
    else:
        boundaries.append(f"{task_id}: no matching Current {task_id} selection result found in pipeline state")
    return rows, boundaries


def _named_task_selection_summary_from_task_file(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    parts: list[str] = []
    for heading in ("Context", "Goal", "Selection Notes"):
        section = _markdown_section_lines(lines, heading=heading)
        if not section:
            continue
        text = _compact_markdown_section(section, max_lines=5 if heading == "Selection Notes" else 3)
        if text:
            parts.append(f"{heading}: {text}")
    return _short_join(parts, limit=900)


def _named_task_selection_summary_from_pipeline_state(path: Path, *, task_id: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    section = _markdown_section_lines(lines, heading=f"Current {task_id} selection result")
    if not section:
        section = _plain_label_section_lines(lines, label=f"Current {task_id} selection result")
    if not section:
        return ""
    text = _compact_markdown_section(section, max_lines=8)
    return f"Current {task_id} selection result: {text}" if text else ""


def _markdown_section_lines(lines: list[str], *, heading: str) -> list[str]:
    wanted = heading.strip().casefold()
    in_section = False
    section_level = 0
    section: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip().rstrip(":").casefold()
            if in_section and level <= section_level:
                break
            if title == wanted:
                in_section = True
                section_level = level
                continue
        if in_section:
            section.append(raw)
    return section


def _plain_label_section_lines(lines: list[str], *, label: str) -> list[str]:
    wanted = label.strip().rstrip(":").casefold()
    in_section = False
    section: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        normalized = stripped.rstrip(":").casefold()
        if not in_section and normalized == wanted:
            in_section = True
            continue
        if not in_section:
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            break
        if re.match(r"^(Current|Prior) JSH-\d+ .+:", stripped):
            break
        section.append(raw)
    return section


def _compact_markdown_section(lines: list[str], *, max_lines: int) -> str:
    selected: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("```"):
            continue
        if re.match(r"^#{1,6}\s+", line):
            continue
        cleaned = line.lstrip("-* ").strip()
        if cleaned:
            selected.append(cleaned)
        if len(selected) >= max_lines:
            break
    return _short_join(selected, limit=700)


def _matching_named_task_artifacts(root: Path, slug: str) -> list[Path]:
    if not root.exists():
        return []
    matches = [path for path in root.glob(f"{slug}*.md") if path.is_file() and not path.name.endswith(".scratch.md")]
    return sorted(matches, key=lambda path: (len(path.name), path.name))


def _named_task_artifact_read(cwd: Path, path: Path, *, task_id: str, proof_surface: str, reason: str) -> dict:
    rel = _safe_relative_path(cwd, path)
    return {
        "kind": "current_named_task_artifact",
        "task_id": task_id,
        "proof_surface": proof_surface,
        "target": rel,
        "path": rel,
        "reason": reason,
        "source_boundary": "current repo artifact; inspect contents before treating claimed action as proven",
    }


def _safe_relative_path(cwd: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(cwd).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


def _dedupe_action_contract_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for row in rows:
        key = (
            str(row.get("kind") or ""),
            str(row.get("target") or row.get("command") or ""),
            str(row.get("proof_surface") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _promotable_bounded_no_evidence_contract(*, compact: dict, selected_route: dict) -> bool:
    if not _bounded_no_evidence_scoped_answer_ready(compact=compact, selected_route=selected_route):
        return False
    cap_hit = compact.get("cap_hit") if isinstance(compact.get("cap_hit"), dict) else {}
    return cap_hit.get("hit") is False


def _bounded_no_evidence_scoped_answer_ready(*, compact: dict, selected_route: dict) -> bool:
    if selected_route.get("route_family") != "bounded_no_evidence":
        return False
    no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
    diagnostic_non_support = no_evidence.get("candidate_interpretation") in {
        "claim_boundary_non_support",
        "control_or_generated_echo_non_support",
    }
    searched_scope = compact.get("searched_scope") if isinstance(compact.get("searched_scope"), dict) else {}
    repo_source_committed = (
        compact.get("scope_mode") == "repo_source_committed"
        or searched_scope.get("scope_mode") == "repo_source_committed"
        or "repo_source_committed" in {
            str(kind)
            for kind in searched_scope.get("root_kinds", [])
            if str(kind).strip()
        }
        or any(
            isinstance(root, dict) and root.get("kind") == "repo_source_committed"
            for root in searched_scope.get("roots") or []
        )
    )
    if (
        _positive_int(selected_route.get("explicit_source_root_count")) <= 0
        and not diagnostic_non_support
        and not repo_source_committed
    ):
        return False
    if compact.get("status") != "no_evidence" and no_evidence.get("needed") is not True:
        return False
    if no_evidence.get("answer_scope") != "not_found_in_searched_scope":
        return False
    if no_evidence.get("not_all_history_absence") is not True:
        return False
    read_more_policy = (
        no_evidence.get("read_more_policy") if isinstance(no_evidence.get("read_more_policy"), dict) else {}
    )
    if read_more_policy.get("required_before_scoped_no_evidence_answer") is not False:
        return False
    scope_counts = compact.get("scope_counts") if isinstance(compact.get("scope_counts"), dict) else {}
    searched_scope_has_work = (
        _positive_int(searched_scope.get("searched_files")) > 0
        and _positive_int(searched_scope.get("searched_bytes")) > 0
    )
    if not searched_scope_has_work:
        return False
    scoped_root_counts_present = all(
        key in scope_counts for key in ("selected_root_count", "excluded_root_count", "missing_root_count")
    )
    if not scoped_root_counts_present:
        return False
    if _positive_int(scope_counts.get("excluded_root_count")) != 0 and not repo_source_committed:
        return False
    if _positive_int(scope_counts.get("missing_root_count")) != 0:
        return False
    return True


def _scoped_no_evidence_contract(compact: dict) -> dict:
    no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
    searched_scope = compact.get("searched_scope") if isinstance(compact.get("searched_scope"), dict) else {}
    scope_counts = compact.get("scope_counts") if isinstance(compact.get("scope_counts"), dict) else {}
    cap_hit = compact.get("cap_hit") if isinstance(compact.get("cap_hit"), dict) else {}
    read_more_policy = (
        no_evidence.get("read_more_policy") if isinstance(no_evidence.get("read_more_policy"), dict) else {}
    )
    root_kinds = _searched_scope_root_kinds(searched_scope)
    root_privacy_labels = _searched_scope_root_privacy_labels(searched_scope)
    return {
        "answer_scope": no_evidence.get("answer_scope"),
        "scope_mode": compact.get("scope_mode") or searched_scope.get("scope_mode"),
        "searched_files": searched_scope.get("searched_files"),
        "searched_bytes": searched_scope.get("searched_bytes"),
        "searched_root_kinds": root_kinds,
        "searched_root_privacy_labels": root_privacy_labels,
        "excluded_roots": (compact.get("excluded_scope") or {}).get("excluded_roots", []),
        "contamination_policy": compact.get("contamination_policy")
        or no_evidence.get("contamination_policy"),
        "command_burden": compact.get("command_burden") or {},
        "selected_root_count": scope_counts.get("selected_root_count"),
        "excluded_root_count": scope_counts.get("excluded_root_count"),
        "missing_root_count": scope_counts.get("missing_root_count"),
        "cap_hit": cap_hit.get("hit"),
        "caps_hit": cap_hit.get("caps_hit", []),
        "outside_selected_roots": no_evidence.get("outside_selected_roots"),
        "source_boundary": (
            "only the selected roots and searched files/bytes were evaluated; outside selected roots "
            "and all-history absence remain unproven"
        ),
        "blind_spots": {
            "caps_hit": cap_hit.get("caps_hit", []),
            "excluded_root_count": scope_counts.get("excluded_root_count"),
            "missing_root_count": scope_counts.get("missing_root_count"),
            "outside_selected_roots": no_evidence.get("outside_selected_roots"),
        },
        "not_all_history_absence": no_evidence.get("not_all_history_absence"),
        "candidate_interpretation": no_evidence.get("candidate_interpretation"),
        "read_more_required_before_scoped_no_evidence_answer": read_more_policy.get(
            "required_before_scoped_no_evidence_answer"
        ),
        "read_more_actionability": read_more_policy.get("actionability"),
        "saved_report": compact.get("report_written"),
    }


def _bounded_no_evidence_cap_hit_disposition(scoped_contract: dict) -> dict:
    return {
        "schema_version": 1,
        "status": "scoped_fail_closed",
        "answer_ready": False,
        "canonical_continuation": None,
        "scoped_fail_closed": {
            "reason": "The declared cap prevented a complete searched-scope absence claim.",
            "searched_scope": {
                "searched_files": scoped_contract.get("searched_files"),
                "searched_bytes": scoped_contract.get("searched_bytes"),
                "searched_root_kinds": scoped_contract.get("searched_root_kinds") or [],
            },
            "blind_spots": scoped_contract.get("blind_spots") or {},
            "source_boundary": scoped_contract.get("source_boundary"),
        },
        "partial_evidence_rendering_allowed": False,
        "manual_history_fallback_allowed": False,
    }


def _positive_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _searched_scope_root_kinds(searched_scope: dict) -> list[str]:
    direct = [str(kind) for kind in searched_scope.get("root_kinds") or [] if str(kind).strip()]
    if direct:
        return direct
    return _unique_preserving_order(
        [
            str(root.get("kind") or "")
            for root in searched_scope.get("roots") or []
            if isinstance(root, dict) and str(root.get("kind") or "").strip()
        ]
    )


def _searched_scope_root_privacy_labels(searched_scope: dict) -> list[str]:
    direct = [
        str(label)
        for label in searched_scope.get("root_privacy_labels") or []
        if str(label).strip()
    ]
    if direct:
        return direct
    return _unique_preserving_order(
        [
            str(root.get("privacy_label") or "")
            for root in searched_scope.get("roots") or []
            if isinstance(root, dict) and str(root.get("privacy_label") or "").strip()
        ]
    )


def _compact_devlog_chronology_for_stdout(chronology: dict, *, include_reads: bool = True) -> dict:
    primary_role_reads = chronology.get("primary_role_reads") or {}
    artifact_role_leads = chronology.get("artifact_role_leads") or {}
    compact = {
        "status": chronology.get("status"),
        "answer_status": chronology.get("answer_status"),
        "answer_allowed": chronology.get("answer_allowed"),
        "required_roles": chronology.get("required_roles"),
        "covered_primary_roles": chronology.get("covered_primary_roles"),
        "missing_roles": chronology.get("missing_roles"),
        "primary_role_read_counts": {
            role: len(reads or []) for role, reads in primary_role_reads.items()
        },
        "artifact_role_lead_counts": {
            role: len(reads or []) for role, reads in artifact_role_leads.items()
        },
        "copyable_next_read_count": len(chronology.get("copyable_next_reads") or []),
        "retained_primary_report_handles": chronology.get("retained_primary_report_handles"),
        "stop_condition": chronology.get("stop_condition"),
        "claim_boundary": chronology.get("claim_boundary"),
    }
    if include_reads:
        compact["copyable_next_reads"] = chronology.get("copyable_next_reads") or []
    return compact


def _compact_devlog_core_plan_for_stdout(core_plan: dict) -> dict:
    reads = list(core_plan.get("read") or [])
    return {
        "plan_name": core_plan.get("plan_name"),
        "target_source_role_order": core_plan.get("target_source_role_order"),
        "role_counts": core_plan.get("role_counts"),
        "read_count": len(reads),
        "first_read_role": reads[0].get("target_source_role") if reads else None,
        "diagnostic_count": len(core_plan.get("diagnostics_after") or []),
        "secondary_context_only_after_chronology_recovery": True,
    }


def _compact_devlog_repo_evidence_for_stdout(repo_evidence: dict) -> dict:
    return {
        "status": repo_evidence.get("status"),
        "row_count": repo_evidence.get("row_count"),
        "category_ids": repo_evidence.get("category_ids"),
        "source_policy": repo_evidence.get("source_policy"),
        "use": "secondary context only; full rows are in report_output",
    }


def _compact_devlog_failure_analysis_for_stdout(failure_analysis: dict) -> dict:
    devlog_brief = failure_analysis.get("devlog_brief") or {}
    story_summary = failure_analysis.get("story_phase_summary") or {}
    return {
        "status": failure_analysis.get("status"),
        "report_written": failure_analysis.get("report_written"),
        "devlog_brief_status": devlog_brief.get("status"),
        "story_phase_status": story_summary.get("status"),
        "use": "secondary synthesis after primary role reads; full packet is in the linked report",
    }


def _semantic_debug_compact_candidate_count(compact: dict) -> int | None:
    parsed_candidate_count: int | None = None
    candidate_count = compact.get("candidate_count")
    if candidate_count is not None:
        try:
            parsed_candidate_count = int(candidate_count)
            if parsed_candidate_count > 0:
                return parsed_candidate_count
        except (TypeError, ValueError):
            pass
    strongest_candidates = compact.get("strongest_candidates")
    if isinstance(strongest_candidates, list):
        if strongest_candidates:
            return len(strongest_candidates)
        parsed_candidate_count = 0
    locator = compact.get("source_locator_handoff")
    if isinstance(locator, dict):
        ordering = locator.get("candidate_ordering")
        if isinstance(ordering, dict) and ordering.get("candidate_count") is not None:
            try:
                locator_candidate_count = int(ordering.get("candidate_count"))
                if locator_candidate_count > 0:
                    return locator_candidate_count
                parsed_candidate_count = locator_candidate_count
            except (TypeError, ValueError):
                pass
        next_reads = locator.get("next_reads")
        if isinstance(next_reads, list) and next_reads:
            return len(next_reads)
    next_reads = compact.get("next_reads")
    if isinstance(next_reads, list) and next_reads:
        return len(next_reads)
    scope_counts = compact.get("scope_counts")
    if isinstance(scope_counts, dict):
        selected_root_count = scope_counts.get("selected_root_count")
        if selected_root_count is not None:
            try:
                return int(selected_root_count)
            except (TypeError, ValueError):
                pass
    return parsed_candidate_count


def _compact_executed_output_for_ask(*, output_key: str, compact: dict) -> dict:
    if output_key == "artifact_currentness":
        worktree = compact.get("worktree_currentness") or {}
        cleanup = worktree.get("cleanup_causality") or {}
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "query": compact.get("query"),
            "detected_intent": compact.get("detected_intent"),
            "currentness_verdict": compact.get("currentness_verdict"),
            "why": compact.get("why"),
            "selected_artifact_or_topic": compact.get("selected_artifact_or_topic"),
            "workspace_resolution": compact.get("workspace_resolution"),
            "cleanup_causality": {
                "debt_status": cleanup.get("debt_status"),
                "causal_summary": cleanup.get("causal_summary"),
                "operational_records": _cleanup_answer_operational_records(
                    cleanup.get("operational_records") or []
                ),
                "git_state": cleanup.get("git_state"),
                "physical_worktree_count": cleanup.get("physical_worktree_count"),
                "target_selection_note": cleanup.get("target_selection_note"),
                "target_binding": cleanup.get("target_binding"),
                "chronology_gate": _compact_cleanup_chronology_gate(
                    cleanup.get("chronology_gate")
                ),
                "facet_coverage": _compact_cleanup_facet_coverage(
                    cleanup.get("facet_coverage")
                ),
                "current_state_summary": _compact_cleanup_current_state(
                    cleanup.get("current_state_summary")
                ),
                "causality_currentness_boundary": {
                    "status": "historical_causality_first_current_state_second",
                    "current_state_is_not_causal_proof": True,
                },
            },
            "cleanup_causal_role_order": worktree.get("cleanup_causal_role_order"),
            "source_role_order": worktree.get("source_role_order"),
            "source_roles_present": worktree.get("source_roles_present"),
            "worktree_classifications": _compact_worktree_classifications(
                worktree.get("classifications")
            ),
            "next_reads": (compact.get("next_reads") or [])[:8],
            "blind_spots": (compact.get("blind_spots") or [])[:5],
            "boundary": worktree.get("boundary"),
            "report_written": compact.get("report_written"),
            "message": "Artifact-currentness executed with a bounded cleanup-causality handoff.",
        }
    if output_key == "status_anchor":
        source_tiers = compact.get("source_tiers") or {}
        generated_contract = compact.get("generated_report_contract") or {}
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "query": compact.get("query"),
            "routing": compact.get("routing"),
            "summary": compact.get("summary"),
            "selected_anchor": compact.get("selected_anchor"),
            "parent_main_anchor_shortlist": compact.get("parent_main_anchor_shortlist"),
            "parent_thread_side_boundary": compact.get("parent_thread_side_boundary"),
            "answer_readiness": compact.get("answer_readiness"),
            "generated_report_contract": generated_contract,
            "post_anchor_interval_reconstruction": _compact_post_anchor_interval_for_ask(
                compact.get("post_anchor_interval_reconstruction") or {}
            ),
            "latest_status_followthrough_contract": _latest_status_followthrough_contract(compact),
            "source_tiers": {
                "tier_order": source_tiers.get("tier_order"),
                "counts": source_tiers.get("counts"),
                "required_primary": [],
                "required_primary_count": len(source_tiers.get("required_primary") or []),
                "required_secondary": [],
                "required_secondary_count": len(source_tiers.get("required_secondary") or []),
                "diagnostic_optional": [],
                "diagnostic_optional_count": len(source_tiers.get("diagnostic_optional") or []),
                "navigation_only": bool(generated_contract.get("navigation_only")),
                "canonical_follow_through_path": generated_contract.get("canonical_follow_through_path"),
                "source_mode_order": source_tiers.get("source_mode_order"),
                "source_role_labels": source_tiers.get("source_role_labels"),
            },
            "answer_brief": [],
            "session_activity": {
                "status": (compact.get("session_activity") or {}).get("status"),
                "summary": (compact.get("session_activity") or {}).get("summary") or {},
            },
            "work_products": {"commits": []},
            "clarification_request": compact.get("clarification_request"),
            "follow_through_plan": compact.get("follow_through_plan"),
            "evidence_contract": compact.get("evidence_contract"),
            "fail_closed_guidance": compact.get("fail_closed_guidance"),
            "report_written": compact.get("report_written"),
        }
    if output_key == "coordinator_context":
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "report_written": compact.get("report_written"),
            "source_policy": compact.get("source_policy"),
            "active_status_front_door": compact.get("active_status_front_door"),
            "current_state": compact.get("current_state"),
            "active_task_handles": compact.get("active_task_handles"),
            "active_task_reconciliation": compact.get("active_task_reconciliation"),
            "front_door_handles": compact.get("front_door_handles"),
            "why_goals_guiding_principles": compact.get("why_goals_guiding_principles"),
            "learnings_failure_modes": compact.get("learnings_failure_modes"),
            "required_next_reads": compact.get("required_next_reads", [])[:8],
            "optional_diagnostics": compact.get("optional_diagnostics", [])[:5],
            "prior_result_context": compact.get("prior_result_context", [])[:5],
            "claim_boundaries": compact.get("claim_boundaries"),
            "message": compact.get("message"),
        }
    if output_key == "devlog_failure_analysis":
        chronology_summary = _compact_devlog_chronology_for_stdout(
            compact.get("chronology_source_reach") or {},
            include_reads=True,
        )
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "report_written": compact.get("report_written"),
            "linked_reports": compact.get("linked_reports"),
            "source_read_order": compact.get("source_read_order"),
            "core_convo_first_read_plan": _compact_devlog_core_plan_for_stdout(
                compact.get("core_convo_first_read_plan") or {}
            ),
            "chronology_source_reach": chronology_summary,
            "target_historical_source_window_reads": compact.get("target_historical_source_window_reads", [])[:3],
            "target_historical_source_window_read_count": compact.get(
                "target_historical_source_window_read_count"
            ),
            "repo_phase_category_evidence": _compact_devlog_repo_evidence_for_stdout(
                compact.get("repo_phase_category_evidence") or {}
            ),
            "failure_analysis": _compact_devlog_failure_analysis_for_stdout(
                compact.get("failure_analysis") or {}
            ),
            "diagnostic_source_window_read_count": compact.get("diagnostic_source_window_read_count"),
            "diagnostic_policy": compact.get("diagnostic_policy"),
            "generic_recovery_policy": compact.get("generic_recovery_policy"),
            "output_contract": compact.get("output_contract"),
            "privacy_flags": compact.get("privacy_flags"),
            "message": (
                "Full devlog/failure-analysis source-reach package written to report_output; "
                "open chronology_source_reach copyable next reads and role-complete target history "
                "before current/adjacent diagnostics."
            ),
        }
    if output_key == "semantic_wrapup":
        next_reads = list(compact.get("next_reads") or [])
        required_primary_reads = [
            read
            for read in next_reads
            if isinstance(read, dict) and read.get("read_role") == "required_primary_proof"
        ]
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "query": compact.get("query"),
            "route": compact.get("route"),
            "report_written": compact.get("report_written")
            or (compact.get("context_budget") or {}).get("report_written"),
            "summary": compact.get("summary"),
            "scope_resolution": compact.get("scope_resolution"),
            "root_handoff_contract": compact.get("root_handoff_contract"),
            "answer_ready_contract": compact.get("answer_ready_contract"),
            "answer_use_contract": compact.get("answer_use_contract"),
            "read_role_summary": compact.get("read_role_summary"),
            "required_primary_proof": required_primary_reads[:6],
            "next_reads": next_reads[:8],
            "failure_modes": (compact.get("failure_modes") or [])[:5],
            "fix_suggestions": (compact.get("fix_suggestions") or [])[:5],
            "source_inspection_boundary": {
                "inspect_required_primary_proof_before_answer": True,
                "optional_diagnostics_default_actionable": False,
                "source_use_required": (compact.get("answer_ready_contract") or {}).get("source_use_required")
                or (compact.get("answer_use_contract") or {}).get("source_use_required"),
                "stop_guidance": (compact.get("answer_ready_contract") or {}).get("stop_guidance")
                or (compact.get("answer_use_contract") or {}).get("stop_guidance"),
            },
            "message": (
                "Semantic-wrapup executed. Inspect failure_modes, fix_suggestions, "
                "and required_primary_proof refs before answering or proposing fixes."
            ),
        }
    if output_key == "semantic_debug":
        no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
        searched_scope = (
            compact.get("searched_scope")
            if isinstance(compact.get("searched_scope"), dict)
            else _no_evidence_searched_scope_summary(no_evidence)
            if no_evidence
            else None
        )
        scope_counts = (
            compact.get("scope_counts")
            if isinstance(compact.get("scope_counts"), dict)
            else _no_evidence_scope_counts(no_evidence)
            if no_evidence
            else None
        )
        cap_hit = (
            compact.get("cap_hit")
            if isinstance(compact.get("cap_hit"), dict)
            else _no_evidence_cap_hit(no_evidence)
            if no_evidence
            else None
        )
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "report_written": compact.get("report_written"),
            "question_family": compact.get("question_family"),
            "directional_history_reconstruction": compact.get("directional_history_reconstruction"),
            "candidate_count": _semantic_debug_compact_candidate_count(compact),
            "event_count": compact.get("event_count")
            if compact.get("event_count") is not None
            else (compact.get("tool_action_proof") or {}).get("event_count"),
            "answer_ready_decision": compact.get("answer_ready_decision")
            or (compact.get("answer_ready_handoff") or {}).get("decision"),
            "answer_readiness_summary": compact.get("answer_readiness_summary")
            or summarize_answer_readiness(compact.get("answer_readiness") or {}),
            "source_handle_routing": compact.get("source_handle_routing"),
            "required_final_provenance": (
                compact.get("final_answer_source_use_contract") or {}
            ).get("required_final_provenance"),
            "historical_orchestration_provenance_final_support_plan": compact.get(
                "historical_orchestration_provenance_final_support_plan"
            ),
            "historical_orchestration_role_evidence_bundle": compact.get(
                "historical_orchestration_role_evidence_bundle"
            ),
            "core_convo_first_read_plan": compact.get("core_convo_first_read_plan"),
            "longitudinal_phase_source_plan": compact.get("longitudinal_phase_source_plan"),
            "expectation_mismatch_plan": compact.get("expectation_mismatch_plan"),
            "action_proof_plan": compact.get("action_proof_plan"),
            "process_actual_use_final_support_read_plan": compact.get(
                "process_actual_use_final_support_read_plan"
            ),
            "process_actual_use_bucket_evidence_bundle": compact.get(
                "process_actual_use_bucket_evidence_bundle"
            ),
            "source_followthrough_plan": compact.get("source_followthrough_plan"),
            "provenance_direct_evidence_facet_ledger": compact.get(
                "provenance_direct_evidence_facet_ledger"
            ),
            "provenance_complete_proof_handoff": compact.get(
                "provenance_complete_proof_handoff"
            ),
            "in_depth_audit_followthrough": compact.get("in_depth_audit_followthrough")
            or (compact.get("source_locator_handoff") or {}).get("in_depth_audit_followthrough"),
            "audit_continuation_workflow": compact.get("audit_continuation_workflow")
            or (
                compact.get("in_depth_audit_followthrough")
                or (compact.get("source_locator_handoff") or {}).get("in_depth_audit_followthrough")
                or {}
            ).get("audit_continuation_workflow"),
            "decision_context_summary": compact.get("decision_context_summary"),
            "audit_plan_summary": compact.get("audit_plan_summary"),
            "next_reads": compact.get("next_reads", [])[:3],
            "searched_scope": searched_scope,
            "scope_counts": scope_counts,
            "cap_hit": cap_hit,
            "no_evidence": no_evidence or compact.get("no_evidence"),
            "message": compact.get("message"),
        }
    return {
        "schema_version": compact.get("schema_version"),
        "status": compact.get("status"),
        "report_written": compact.get("report_written"),
        "scope_mode": compact.get("scope_mode"),
        "searched_scope": compact.get("searched_scope"),
        "excluded_scope": compact.get("excluded_scope"),
        "scope_counts": compact.get("scope_counts"),
        "cap_hit": compact.get("cap_hit"),
        "contamination_policy": compact.get("contamination_policy"),
        "command_burden": compact.get("command_burden"),
        "execution_path": compact.get("execution_path"),
        "found_evidence": compact.get("found_evidence"),
        "no_evidence": compact.get("no_evidence"),
        "message": compact.get("message"),
    }


_ASK_ANSWER_SUPPORTED_ROUTE_FAMILIES = frozenset(
    {
        "action_proof",
        "artifact_currentness",
        "bounded_no_evidence",
        "expectation_mismatch",
        "orchestration_provenance",
        "process_actual_use",
        "provenance",
        "remembered_topic",
        "remembered_user_wording",
        "semantic_wrapup",
        "semantic_debug",
        "latest_status",
        "active_status_front_door",
    }
)
_ASK_ANSWER_EXPLICIT_FAIL_STATUSES = frozenset(
    {
        "not-ready/needs-more-proof",
        "not-ready/missing-current-named-task-proof",
        "not-ready/missing-completed-task-merge-artifact-proof",
        "not-ready/missing-raw-worker-chronology",
        "canonical-continuation-packet-required",
        "inspect_recovery_first",
        "needs_investigation",
    }
)
_ASK_ANSWER_MAX_REFS = 6
_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS = (
    "expected_behavior_evidence",
    "observed_behavior_or_action_proof",
    "divergence_evidence",
)
_ASK_ANSWER_REMEMBERED_USER_RECOVERY_MAX_REFS = 16
_ASK_ANSWER_REMEMBERED_USER_LATEST_RECOVERY_LIMIT = 200
_ASK_ANSWER_MAX_LINE_CHARS = 900
_ASK_ANSWER_MAX_FILE_CHARS = 1200
_ASK_ANSWER_WORK_EPISODE_ROUTE_FAMILIES = frozenset(
    {"action_proof", "provenance", "semantic_debug"}
)
_ASK_ANSWER_WORK_EPISODE_SCAN_MAX_FILES = 80
_ASK_ANSWER_WORK_EPISODE_SCAN_MAX_BYTES = 1_000_000
_ASK_ANSWER_WORK_EPISODE_FILE_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".markdown", ".diff", ".patch", ".csv"})
_ASK_ANSWER_WORK_EPISODE_TOKEN_STOPWORDS = frozenset(
    {
        "about",
        "actually",
        "after",
        "agent",
        "claim",
        "claimed",
        "closed",
        "does",
        "done",
        "did",
        "file",
        "fix",
        "fixed",
        "happen",
        "happened",
        "issue",
        "only",
        "problem",
        "really",
        "reviewer",
        "selected",
        "task",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "whether",
        "which",
        "worker",
        "coordinator",
        "launched",
    }
)
_ASK_ANSWER_WORK_EPISODE_PRIMARY_MARKERS = (
    "changed ",
    "command_output",
    "diff --git",
    "exit_code",
    "file_edit",
    "local evidence leads",
    "pytest",
    "test_",
    "tool_call",
    "with pytest.raises",
)
_ASK_ANSWER_MONTH_NUMBERS = {
    "january": "01",
    "jan": "01",
    "february": "02",
    "feb": "02",
    "march": "03",
    "mar": "03",
    "april": "04",
    "apr": "04",
    "may": "05",
    "june": "06",
    "jun": "06",
    "july": "07",
    "jul": "07",
    "august": "08",
    "aug": "08",
    "september": "09",
    "sep": "09",
    "sept": "09",
    "october": "10",
    "oct": "10",
    "november": "11",
    "nov": "11",
    "december": "12",
    "dec": "12",
}
_REMEMBERED_USER_WORDING_PROMPT_ECHO_MARKERS = (
    "you are a read-only",
    "you are the read-only",
    "read-only pressure agent",
    "use the implementer skill",
    "work only in this existing worktree",
    "work only in this isolated worktree",
    "existing isolated worktree",
    "you are not alone in the codebase",
    "parent checkout must remain untouched",
    "branch `task/",
    "task: execute",
    "task branch:",
    "current branch checkpoint:",
    "current branch head includes",
    "product-code repair under review",
    "task file:",
    "scratch file:",
    "required proof before handoff",
    "before final response",
    "exact finding to fix",
    "coordinator live smoke",
    "command run from task worktree",
    "do not edit product code",
    "do not edit the parent checkout",
    "current branch includes implementation commit",
    "partial review commit",
    "observed command:",
    "observed answer:",
    "task note:",
    "review fixture",
    "test fixture",
)
_REMEMBERED_USER_WORDING_ASSIGNMENT_RE = re.compile(
    r"\byou are (?:(?:the|an?|a)\b.{0,160}\b)?"
    r"(implementer|re-?reviewer|reviewer|worker|coordinator|read-only\b.{0,80}\b(?:agent|slice))\b",
    re.IGNORECASE | re.DOTALL,
)
_QUOTED_STATUS_BOUNDARY_RE = re.compile(r"[\"'`]([^\"'`]{8,220})[\"'`]")
_ACTION_CHRONOLOGY_RECOVERY_MAX_FILES = 120
_ACTION_CHRONOLOGY_RECOVERY_MAX_BYTES = 3_000_000
_ACTION_CHRONOLOGY_RECOVERY_WINDOW_LINES = 36
_ACTION_CHRONOLOGY_FOLLOWUP_MARKERS = (
    "select jsh",
    "selected jsh",
    "launched jsh",
    "worktree launched",
    "next task",
    "thread_spawn",
    "task_started",
)


def _run_ask_answer(args: argparse.Namespace, *, cwd: str, route_payload: dict) -> dict:
    execute_args = args
    if route_payload["ask_router"].get("route_family") == "bounded_no_evidence":
        execute_args = _args_with_bounded_auto_execute_prompt_scope(args)
    execute_payload = _run_ask_execute(execute_args, cwd=cwd, route_payload=route_payload)
    recovery = _remembered_user_wording_latest_recovery_config(args)
    return _ask_answer_from_execute(
        execute_payload,
        cwd=Path(cwd).expanduser().resolve(),
        remembered_user_latest_recovery=recovery,
    )


def _remembered_user_wording_latest_recovery_config(args: argparse.Namespace) -> dict | None:
    explicit_roots = bool(getattr(args, "root", None))
    explicit_codex_home = bool(getattr(args, "codex_home", None))
    if explicit_roots and not explicit_codex_home:
        return None
    return {
        "codex_home": str(_history_home_from_args(args, provider="codex")),
        "max_files": int(getattr(args, "max_files", 200) or 200),
        "max_bytes": int(getattr(args, "max_bytes", 100 * 1024 * 1024) or 100 * 1024 * 1024),
        "max_elapsed": float(getattr(args, "max_elapsed", 15.0) or 15.0),
    }


def _ask_answer_artifact_currentness(
    answer: dict,
    *,
    report: dict,
    action: dict,
    cwd: Path,
) -> dict | None:
    """Finalize artifact/topic currentness and source-backed cleanup causality."""
    execution = report.get("execution") if isinstance(report.get("execution"), dict) else {}
    query = str(report.get("query") or "")
    executed_output = (
        report.get("executed_output")
        if isinstance(report.get("executed_output"), dict)
        else {}
    )
    if executed_output.get("detected_intent") == "worktree_cleanup_currentness":
        if not _is_answer_ready_worktree_cleanup_query(query):
            return _ask_answer_fail_closed(
                answer,
                "worktree cleanup direct answer is scoped to the exact generic cleanup prompt",
                {
                    "required_next_proof": (
                        "target-specific cleanup evidence and a decisive target classification"
                    ),
                    "searched_scope": "route-only artifact-currentness evidence",
                },
            )
        packet = {
            "detected_intent": "worktree_cleanup_currentness",
            "worktree_currentness": {
                "cleanup_causality": executed_output.get("cleanup_causality") or {},
            },
        }
        return _ask_answer_worktree_cleanup_currentness(
            answer,
            packet=packet,
            action=action,
            cwd=cwd,
        )
    report_path = Path(str(execution.get("report_written") or action.get("saved_report") or "")).expanduser()
    if not report_path.is_absolute():
        report_path = cwd / report_path
    try:
        saved = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return _ask_answer_fail_closed(
            answer,
            "artifact-currentness saved proof report could not be inspected",
            {
                "required_next_proof": "readable artifact-currentness report with current front-door, artifact-status, and newer-commit proof",
                "report": str(report_path),
                "inspection_error": str(exc),
            },
        )
    packet = saved.get("artifact_currentness") if isinstance(saved, dict) else {}
    if isinstance(packet, dict) and packet.get("detected_intent") == "worktree_cleanup_currentness":
        if not _is_answer_ready_worktree_cleanup_query(query):
            return _ask_answer_fail_closed(
                answer,
                "worktree cleanup direct answer is scoped to the exact generic cleanup prompt",
                {
                    "required_next_proof": (
                        "target-specific cleanup evidence and a decisive target classification"
                    ),
                    "searched_scope": "route-only artifact-currentness evidence",
                },
            )
        return _ask_answer_worktree_cleanup_currentness(
            answer,
            packet=packet,
            action=action,
            cwd=cwd,
        )
    if not isinstance(packet, dict) or packet.get("detected_intent") != "natural_direction_currentness":
        return None
    selected = packet.get("selected_artifact_or_topic") if isinstance(packet.get("selected_artifact_or_topic"), dict) else {}
    query_ids = _artifact_currentness_named_ids(query)
    selected_identity = " ".join(
        str(selected.get(field) or "") for field in ("title", "path", "source_ref")
    )
    selected_ids = _artifact_currentness_named_ids(selected_identity)
    missing_target_ids = sorted(query_ids - selected_ids)
    if missing_target_ids:
        target_gate = {
            "status": "blocked_named_target_mismatch",
            "query_identifiers": sorted(query_ids),
            "selected_artifact_identifiers": sorted(selected_ids),
            "missing_target_identifiers": missing_target_ids,
            "selected_artifact": {
                "title": selected.get("title"),
                "path": selected.get("path"),
                "source_ref": selected.get("source_ref"),
            },
            "required_next_proof": (
                f"artifact-currentness evidence whose selected artifact/topic matches {', '.join(missing_target_ids)}"
            ),
            "suggested_command": (
                f"jsonl-history-search artifact-currentness {shlex.quote(query)} "
                '--cwd "$PWD" --format text'
            ),
        }
        answer["source_contract"]["artifact_currentness_target_gate"] = target_gate
        return _ask_answer_fail_closed(
            answer,
            "artifact-currentness selected artifact does not match the named query identifier",
            target_gate,
        )
    verdict = str(packet.get("currentness_verdict") or "")
    if verdict not in {"current", "superseded"}:
        return _ask_answer_fail_closed(
            answer,
            "artifact-currentness verdict is missing or conflicting",
            {"required_next_proof": "one unambiguous current or superseded verdict", "observed_verdict": verdict or None},
        )

    chain = [item for item in packet.get("evidence_chain") or [] if isinstance(item, dict)]
    companion_status, companion_assessment = _artifact_currentness_companion_status_proof(
        packet,
        query_ids=query_ids,
        verdict=verdict,
    )
    role_candidates = {
        "current_front_door": next((item for item in chain if item.get("evidence_type") == "current_front_door"), None),
        "artifact_status_claim": next(
            (item for item in chain if item.get("evidence_type") == "artifact_status_field"),
            companion_status,
        ),
        "newer_commit_evidence": next(
            (item for item in chain if item.get("relationship") == "newer_repo_commit_after_artifact"), None
        ),
    }
    opened_roles: dict[str, dict] = {}
    for role in ("current_front_door", "artifact_status_claim"):
        candidate = role_candidates[role]
        if not candidate or not candidate.get("source_ref"):
            continue
        opened = _open_ask_answer_proof_refs(
            [{"kind": "artifact_currentness_proof", "target": candidate["source_ref"], "currentness_source_role": role}],
            cwd=cwd,
        )
        if opened and opened[0].get("status") == "opened" and opened[0].get("summary"):
            opened_roles[role] = opened[0]
    commit_candidate = role_candidates["newer_commit_evidence"]
    repo = git_root_for(cwd)
    commit = commit_metadata(repo, str(commit_candidate.get("source_ref"))) if repo and commit_candidate else None
    if commit is not None:
        opened_roles["newer_commit_evidence"] = {
            "status": "opened",
            "target": commit.commit,
            "summary": f"{commit.commit_time} {commit.commit[:12]} {commit.subject}",
            "ref": {**commit_candidate, "currentness_source_role": "newer_commit_evidence"},
        }

    missing_roles = [role for role in role_candidates if role not in opened_roles]
    assessment = packet.get("status_field_assessment") if isinstance(packet.get("status_field_assessment"), dict) else {}
    selected_status_reconciled = (
        verdict != "superseded"
        or assessment.get("assessment") == "status_claim_conflicts_with_later_evidence"
    )
    if not selected_status_reconciled and companion_assessment != "same_target_status_consistent_with_verdict":
        missing_roles.append("artifact_status_conflict_assessment")
    if missing_roles:
        answer["source_contract"]["artifact_currentness_answer_gate"] = {
            "status": "blocked_missing_or_conflicting_currentness_proof",
            "required_roles": list(role_candidates),
            "opened_roles": list(opened_roles),
            "missing_roles": missing_roles,
        }
        answer["proof_summary"] = [item["summary"] for item in opened_roles.values()]
        return _ask_answer_fail_closed(
            answer,
            "artifact-currentness required proof roles are missing, conflicting, or not inspectable",
            answer["source_contract"]["artifact_currentness_answer_gate"],
        )

    artifact_label = str(selected.get("title") or selected.get("path") or "the selected artifact/report")
    current_ref = opened_roles["current_front_door"]["target"]
    status_ref = opened_roles["artifact_status_claim"]["target"]
    commit_ref = opened_roles["newer_commit_evidence"]["target"]
    conclusion = "is not the current source of truth; it is superseded" if verdict == "superseded" else "is current"
    answer["answer_status"] = "answered-from-proof"
    answer["answer"] = (
        f"{artifact_label} {conclusion}. Current front-door/current-state evidence: {current_ref}. "
        f"The artifact status field at {status_ref} is a claim, not authority. "
        f"Newer repository history was inspected at commit {commit_ref[:12]}. "
        "This answers only the selected artifact/report currentness question; it does not establish broad release or readiness status."
    )
    answer["refs_used"] = [_ref_public_summary(item) for item in opened_roles.values()]
    answer["proof_summary"] = [
        f"{role}: {opened_roles[role]['summary']}" for role in role_candidates
    ]
    answer["source_contract"]["artifact_currentness_answer_gate"] = {
        "status": "answer_allowed",
        "verdict": verdict,
        "required_roles": list(role_candidates),
        "opened_roles": list(opened_roles),
        "boundary": "selected artifact/report currentness only; no broad release/readiness inference",
    }
    return {"ask_answer": answer}


def _artifact_currentness_companion_status_proof(
    packet: dict,
    *,
    query_ids: set[str],
    verdict: str,
) -> tuple[dict | None, str]:
    """Return one consistent status claim from exact same-ID companion artifacts."""
    if not query_ids:
        return None, "not_applicable"
    query_namespaces = {identifier.rsplit("-", 1)[0] for identifier in query_ids}
    claims: list[dict] = []
    for candidate in packet.get("artifact_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        identity = " ".join(str(candidate.get(field) or "") for field in ("title", "path", "source_ref"))
        candidate_ids = {
            identifier
            for identifier in _artifact_currentness_named_ids(identity)
            if identifier.rsplit("-", 1)[0] in query_namespaces
        }
        if query_ids != candidate_ids:
            continue
        for field in candidate.get("status_fields") or []:
            if not isinstance(field, dict) or str(field.get("name") or "").casefold() not in {
                "issue state",
                "state",
                "status",
                "ticket state",
                "workflow state",
            }:
                continue
            if not field.get("source_ref") or not field.get("value"):
                continue
            claims.append(
                {
                    "evidence_type": "same_target_companion_status_field",
                    "relationship": "exact_same_identifier_companion_status",
                    "source_ref": field["source_ref"],
                    "snippet": field.get("raw") or f"{field.get('name')}: {field['value']}",
                    "status_value": str(field["value"]),
                    "companion_path": candidate.get("path"),
                }
            )
    normalized_values = {re.sub(r"\s+", " ", claim["status_value"].strip().casefold()) for claim in claims}
    if not claims:
        return None, "same_target_status_missing"
    if len(normalized_values) != 1:
        return None, "same_target_status_conflicting"
    status_value = next(iter(normalized_values))
    terminal_markers = ("closed", "complete", "completed", "done", "merged", "resolved", "superseded")
    active_markers = ("active", "in progress", "open", "selected")
    terminal = any(re.search(rf"\b{re.escape(marker)}\b", status_value) for marker in terminal_markers)
    active = any(re.search(rf"\b{re.escape(marker)}\b", status_value) for marker in active_markers)
    positive_terminal = any(
        re.match(rf"^{re.escape(marker)}\b", status_value)
        for marker in terminal_markers
    )
    negative = bool(
        re.search(
            r"\b(?:not|never|cannot|can't|cant|won't|wont|wasn't|wasnt|weren't|werent|isn't|isnt|"
            r"aren't|arent|hasn't|hasnt|haven't|havent|hadn't|hadnt|doesn't|doesnt|don't|dont|"
            r"didn't|didnt|shouldn't|shouldnt|wouldn't|wouldnt|couldn't|couldnt|mustn't|mustnt)\b|"
            r"\bno\s+longer\b",
            status_value,
        )
    )
    if negative or terminal == active:
        return None, "same_target_status_ambiguous_or_conflicting"
    if (verdict == "superseded" and not positive_terminal) or (verdict == "current" and not active):
        return None, "same_target_status_conflicting_with_verdict"
    return claims[0], "same_target_status_consistent_with_verdict"


def _ask_answer_worktree_cleanup_currentness(
    answer: dict,
    *,
    packet: dict,
    action: dict,
    cwd: Path,
) -> dict:
    worktree = (
        packet.get("worktree_currentness")
        if isinstance(packet.get("worktree_currentness"), dict)
        else {}
    )
    cleanup = (
        worktree.get("cleanup_causality")
        if isinstance(worktree.get("cleanup_causality"), dict)
        else {}
    )
    debt_status = str(cleanup.get("debt_status") or "")
    facet_coverage = cleanup.get("facet_coverage") if isinstance(cleanup.get("facet_coverage"), dict) else {}
    action_reads = action.get("read") if isinstance(action.get("read"), list) else []
    normalized: list[dict] = []
    rejected: list[dict] = []
    repo_root = cwd.resolve()
    for item in action_reads:
        if not isinstance(item, dict) or item.get("source_role") != "operational_record":
            rejected.append(
                {
                    "source_ref": item.get("source_ref") if isinstance(item, dict) else None,
                    "reason": "invalid_source_role",
                }
            )
            continue
        source_ref = str(item.get("source_ref") or "").strip()
        target, line = _split_ref_target(source_ref)
        if not target or line is None:
            rejected.append({"source_ref": source_ref or None, "reason": "malformed_repo_ref"})
            continue
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = cwd / path
        try:
            resolved = path.resolve()
            resolved.relative_to(repo_root)
        except (OSError, ValueError):
            rejected.append({"source_ref": source_ref, "reason": "non_local_repo_ref"})
            continue
        if not resolved.is_file():
            rejected.append({"source_ref": source_ref, "reason": "unreadable_repo_ref"})
            continue
        if line < 1:
            rejected.append(
                {
                    "source_ref": source_ref,
                    "reason": "invalid_line_number",
                    "requested_line": line,
                }
            )
            continue
        try:
            with resolved.open(encoding="utf-8", errors="replace") as handle:
                line_count = sum(1 for _ in handle)
        except OSError:
            rejected.append({"source_ref": source_ref, "reason": "unreadable_repo_ref"})
            continue
        if line > line_count:
            rejected.append(
                {
                    "source_ref": source_ref,
                    "reason": "line_out_of_range",
                    "requested_line": line,
                    "available_line_count": line_count,
                }
            )
            continue
        normalized.append(
            {
                **item,
                "kind": "artifact_currentness_operational_record",
                "target": f"{resolved}:{line}",
                "target_source_role": "operational_record",
            }
        )

    opened = _open_ask_answer_proof_refs(normalized, cwd=cwd)
    usable = [item for item in opened if item.get("status") == "opened" and item.get("summary")]
    relationships = {str(item.get("relationship") or "") for item in normalized}
    causal_roles = {
        str(item.get("causal_source_role") or "") for item in normalized
    }
    required_original_roles = {
        "report_only_audit",
        "deferred_handoff",
        "cleanup_learning_policy",
        "private_retention",
    }
    has_later_outcome = bool(
        relationships & {"cleanup_execution_report", "report_only_cleanup_audit"}
    )
    if (
        rejected
        or len(usable) != len(normalized)
        or not usable
        or debt_status not in {"closed_by_cleanup_execution_report", "open_cleanup_debt"}
        or not required_original_roles <= causal_roles
        or not has_later_outcome
        or not facet_coverage.get("complete")
    ):
        gate = {
            "status": "blocked_missing_or_unreadable_worktree_cleanup_proof",
            "required_source_role": "operational_record",
            "required_causal_roles": sorted(required_original_roles),
            "required_later_outcome": "cleanup_execution_report_or_explicit_non_execution",
            "facet_coverage": {
                "complete": bool(facet_coverage.get("complete")),
                "proven_count": facet_coverage.get("proven_count"),
                "total_count": facet_coverage.get("total_count"),
            },
            "debt_status": debt_status or None,
            "opened_ref_count": len(usable),
            "rejected_refs": rejected,
        }
        answer["source_contract"]["worktree_cleanup_answer_gate"] = gate
        return _ask_answer_fail_closed(
            answer,
            "worktree cleanup operational proof refs are missing, unreadable, non-local, or incomplete",
            gate,
        )

    causal_summary = str(cleanup.get("causal_summary") or "").strip()
    if not causal_summary:
        return _ask_answer_fail_closed(
            answer,
            "worktree cleanup causal summary is missing from the inspected proof contract",
            {"required_next_proof": "cleanup causality summary backed by opened operational records"},
        )
    current_state = cleanup.get("current_state_summary") or {}
    current_state_clause = ""
    if current_state:
        default_branch = str(current_state.get("default_branch") or "the default branch")
        linked_count = int(current_state.get("linked_task_worktree_count") or 0)
        debt_count = int(
            current_state.get("clean_reachable_manual_closeout_debt_count") or 0
        )
        linked_noun = "worktree" if linked_count == 1 else "worktrees"
        linked_verb = "remains" if linked_count == 1 else "remain"
        debt_noun = "worktree" if debt_count == 1 else "worktrees"
        debt_verb = "remains" if debt_count == 1 else "remain"
        current_state_clause = (
            " Current state was inspected only after that historical chain: "
            f"{linked_count} linked task {linked_noun} {linked_verb}, "
            f"including {int(current_state.get('dirty_worktree_count') or 0)} dirty, "
            f"{int(current_state.get('private_retention_worktree_count') or 0)} with retained .private evidence, "
            f"and {int(current_state.get('not_reachable_from_default_count') or 0)} not reachable from {default_branch}; "
            f"{debt_count} clean reachable {debt_noun} {debt_verb} as manual closeout debt."
        )
    answer["answer_status"] = "answered-from-proof"
    answer["answer"] = (
        "The worktrees did not clean themselves up automatically because physical removal is a manual "
        "closeout operation, and the audit stopped at a report-only "
        "preserve-then-remove classification: private evidence still had to be retained, and later "
        "handoffs left the pre-existing worktrees unaudited instead of completing that lifecycle step. "
        "The cleanup policy required preserved evidence to be followed by physical worktree removal "
        "while retaining branch refs, so those refs were not the blocker. "
        f"{causal_summary}{current_state_clause} "
        "This is a read-only causality answer and does not authorize or perform cleanup actions."
    )
    answer["refs_used"] = [_ref_public_summary(item) for item in usable]
    answer["proof_summary"] = [
        f"{item['ref'].get('relationship')}: {item['summary']}" for item in usable
    ]
    answer["source_contract"]["worktree_cleanup_answer_gate"] = {
        "status": "answer_allowed",
        "debt_status": debt_status,
        "opened_ref_count": len(usable),
        "relationships": sorted(relationships),
        "causal_source_roles": [
            str(item.get("causal_source_role"))
            for item in normalized
            if item.get("causal_source_role")
        ],
        "facet_coverage": {
            "complete": bool(facet_coverage.get("complete")),
            "proven_count": facet_coverage.get("proven_count"),
            "total_count": facet_coverage.get("total_count"),
        },
        "current_state_summary": current_state,
        "unsupported_inferences": [
            "worktree creation is automated",
            "successful completion does not trigger cleanup",
        ],
        "boundary": "read-only cleanup causality; no cleanup, branch deletion, worktree removal, or storage action",
    }
    return {"ask_answer": answer}


def _artifact_currentness_named_ids(text: str) -> set[str]:
    return {
        f"{match.group(1).upper()}-{match.group(2)}"
        for match in _ASK_EXECUTE_TASK_ID_RE.finditer(str(text or ""))
    }


def _ask_answer_from_execute(
    execute_payload: dict,
    *,
    cwd: Path,
    remembered_user_latest_recovery: dict | None = None,
) -> dict:
    report = execute_payload.get("ask_execute") or {}
    route = report.get("selected_route") or {}
    execution = report.get("execution") or {}
    action = report.get("answer_action_contract") or {}
    route_family = str(route.get("route_family") or "")
    handoff = report.get("answer_handoff")
    if (
        route_family == "artifact_currentness"
        and execution.get("status") == "executed"
        and isinstance(handoff, dict)
        and handoff.get("answer_status") == "answered-from-proof"
        and (handoff.get("source_contract") or {})
        .get("worktree_cleanup_answer_gate", {})
        .get("status")
        == "answer_allowed"
    ):
        return {
            "ask_answer": {
                **handoff,
                "selected_route": route,
                "execution": execution,
            }
        }
    base = {
        "ask_answer": {
            "schema_version": "ask_answer_proof_followthrough.v1",
            "query": report.get("query"),
            "cwd": str(cwd),
            "selected_route": route,
            "execution": execution,
            "answer_status": "cannot-answer-directly-yet",
            "answer": None,
            "refs_used": [],
            "proof_summary": [],
            "fail_closed_reason": None,
            "next_proof_step": None,
            "source_role_explanation": None,
            "source_contract": {
                "built_from": "ask_execute.answer_action_contract",
                "answer_action_status": action.get("answer_status"),
                "promotion_status": action.get("promotion_status"),
                "saved_report": action.get("saved_report") or execution.get("report_written"),
                "parent_thread_side_boundary": action.get("parent_thread_side_boundary"),
            },
        }
    }
    answer = base["ask_answer"]

    missing_source_session = _ask_answer_missing_source_session_next_step(action)
    if execution.get("status") != "executed" and missing_source_session:
        answer["source_contract"]["missing_source_session_stop_contract"] = missing_source_session
        return _ask_answer_fail_closed(
            answer,
            (
                "source-session proof is missing; task, review, and Git facts remain separate partial proof "
                "and cannot replace an opened role-valid raw/session source"
            ),
            missing_source_session,
        )

    if execution.get("status") != "executed":
        return _ask_answer_fail_closed(
            answer,
            "selected route is not supported by the safe ask --execute allow-list",
            route.get("first_command") or route.get("stop_guidance"),
        )
    if route_family not in _ASK_ANSWER_SUPPORTED_ROUTE_FAMILIES:
        return _ask_answer_fail_closed(
            answer,
            f"route family {route_family or '(unknown)'} is not supported by ask --answer MVP",
            route.get("stop_guidance") or route.get("manual_search_guidance"),
        )
    if route_family == "artifact_currentness":
        currentness_answer = _ask_answer_artifact_currentness(answer, report=report, action=action, cwd=cwd)
        if currentness_answer is not None:
            return currentness_answer
    if route_family == "remembered_topic" and not _remembered_topic_causal_instruction_answer_wanted(
        query=str(report.get("query") or ""),
        route_family=route_family,
    ):
        return _ask_answer_fail_closed(
            answer,
            "remembered-topic causal answer recovery is scoped to coordinating-vs-implementing user-instruction prompts",
            {
                "required_next_proof": "clean user-authored coordination-vs-implementation instruction row",
                "searched_scope": "selected remembered-topic causal recovery only",
                "suggested_action": (
                    "use semantic-debug source refs as leads, then inspect raw user-authored message rows; "
                    "ask --answer will not infer a causal remembered-topic answer from task/report/tool echoes "
                    "or from unsupported remembered-topic wording."
                ),
            },
        )

    scoped = action.get("scoped_no_evidence_contract")
    if action.get("answer_status") == "answer-scoped-no-evidence-now" and isinstance(scoped, dict):
        return _ask_answer_scoped_no_evidence(answer, scoped, cwd=cwd)

    can_follow_raw_proof = _ask_answer_can_follow_raw_proof_despite_status(
        action=action,
        route_family=route_family,
    )
    if action.get("answer_status") in _ASK_ANSWER_EXPLICIT_FAIL_STATUSES and not can_follow_raw_proof:
        quote_boundary_recovery = _recover_latest_status_quoted_before_boundary(
            query=str(report.get("query") or ""),
            route_family=route_family,
            config=remembered_user_latest_recovery,
            cwd=cwd,
        )
        if quote_boundary_recovery.get("status") == "clean_user_boundary_recovered":
            recovered_opened = list(quote_boundary_recovery.get("opened") or [])
            answer["source_contract"]["latest_status_quote_boundary_recovery"] = (
                _latest_status_quote_boundary_recovery_public(quote_boundary_recovery)
            )
            answer["proof_summary"] = [
                f"Diagnostic boundary anchor only: {item['summary']}"
                for item in recovered_opened
                if item.get("summary")
            ]
            return _ask_answer_fail_closed(
                answer,
                "quoted before-boundary recovery found only the boundary anchor, not proof of the preceding status/window",
                _latest_status_quote_boundary_anchor_only_next_step(
                    recovery=quote_boundary_recovery,
                    opened=recovered_opened,
                ),
            )
        if quote_boundary_recovery.get("status") != "not_applicable":
            answer["source_contract"]["latest_status_quote_boundary_recovery"] = (
                _latest_status_quote_boundary_recovery_public(quote_boundary_recovery)
            )
        fail_reason = f"ask --execute contract is {action.get('answer_status')}"
        if action.get("promotion_status") == "semantic_debug_readiness_source_plan_gate_fail_closed":
            fail_reason = "semantic-debug recovery/source readiness gate is not satisfied"
            if isinstance(action.get("readiness_source_plan_gate"), dict):
                answer["source_contract"]["readiness_source_plan_gate"] = action["readiness_source_plan_gate"]
        return _ask_answer_fail_closed(
            answer,
            fail_reason,
            _next_proof_step_from_action(action),
        )

    work_episode = _ask_answer_work_episode_handoff(
        report=report,
        action=action,
        route_family=route_family,
        cwd=cwd,
    )
    if work_episode:
        answer["source_contract"]["work_episode_resolver"] = _work_episode_public_contract(work_episode)

    action_chronology_wanted = _action_chronology_answer_wanted(
        query=str(report.get("query") or ""),
        route_family=route_family,
    )
    post_merge_smoke_wanted = _post_merge_smoke_answer_wanted(
        query=str(report.get("query") or ""),
        route_family=route_family,
    )
    if post_merge_smoke_wanted:
        action_chronology_wanted = False
    proof_refs = _collect_ask_answer_proof_refs(action)
    if action_chronology_wanted or post_merge_smoke_wanted:
        proof_refs = _dedupe_ask_answer_proof_refs(
            _action_chronology_candidate_refs(action) + proof_refs
        )
    if work_episode.get("primary_proof_refs"):
        proof_refs = _dedupe_ask_answer_proof_refs(
            list(work_episode.get("primary_proof_refs") or []) + proof_refs
        )
    completed_task_refs = _completed_task_artifact_support_refs(
        cwd=cwd,
        query=str(report.get("query") or ""),
        route_family=route_family,
    )
    if completed_task_refs:
        proof_refs = _dedupe_ask_answer_proof_refs(completed_task_refs + proof_refs)
    if route_family == "active_status_front_door":
        proof_refs = _active_status_front_door_proof_refs(action, proof_refs)
    opened = _open_ask_answer_proof_refs(proof_refs, cwd=cwd)
    usable = [item for item in opened if item.get("status") == "opened" and item.get("summary")]
    if not usable:
        if route_family in {"remembered_user_wording", "remembered_topic"}:
            remembered_recovery_contract_key = (
                "remembered_topic_causal_user_instruction_recovery"
                if route_family == "remembered_topic"
                else "remembered_user_wording_recovery"
            )
            if route_family == "remembered_topic":
                latest_recovery = _recover_remembered_topic_causal_instruction_from_latest_user_messages(
                    query=str(report.get("query") or ""),
                    config=remembered_user_latest_recovery,
                    cwd=cwd,
                )
                latest_opened = [
                    item
                    for item in latest_recovery.get("opened") or []
                    if _remembered_topic_causal_opened_supports_answer(
                        query=str(report.get("query") or ""),
                        item=item,
                    )
                ]
            else:
                latest_recovery = _recover_remembered_user_wording_from_latest_user_messages(
                    query=str(report.get("query") or ""),
                    config=remembered_user_latest_recovery,
                    cwd=cwd,
                )
                latest_opened = list(latest_recovery.get("opened") or [])
            if latest_opened:
                answer["source_contract"][remembered_recovery_contract_key] = {
                    "schema_version": 1,
                    "status": "clean_user_proof_recovered_from_bounded_user_message_scan",
                    "searched_scope": latest_recovery.get("searched_scope"),
                    "searched_files": latest_recovery.get("searched_files"),
                    "candidate_user_message_count": latest_recovery.get("candidate_user_message_count"),
                    "matched_user_message_count": latest_recovery.get("matched_user_message_count"),
                    "rejected_prompt_echo_ref_count": latest_recovery.get("rejected_prompt_echo_ref_count"),
                    "prior_packet_recovery_status": "no_usable_primary_refs_exposed",
                }
                usable = latest_opened
            else:
                diagnostic_opened = _remembered_user_wording_rejected_diagnostic_opened(
                    report=report,
                    action=action,
                    cwd=cwd,
                )
                return _ask_answer_fail_closed(
                    answer,
                    "opened remembered-user-wording proof refs did not include user-authored JSONL message rows",
                    {
                        "required_next_proof": "raw user-authored message ref",
                        "suggested_action": (
                            "rerun or inspect remembered-user-wording proof with a role/user-message constraint; "
                            "already surfaced target reads did not yield clean user-authored non-echo proof"
                        ),
                        "bounded_recovery": {
                            "status": "no_usable_primary_refs_exposed",
                            "searched_ref_count": 0,
                            "opened_ref_count": 0,
                            "rejected_prompt_echo_ref_count": 0,
                            "rejected_non_user_ref_count": 0,
                        },
                        "user_message_recovery": _remembered_user_latest_recovery_public(latest_recovery),
                        "opened_ref_metadata": [
                            _ref_public_summary(item)
                            for item in (opened or diagnostic_opened)
                            if item.get("status") == "opened"
                        ],
                    },
                )
        if route_family == "expectation_mismatch":
            expectation_gate = _expectation_mismatch_answer_proof_gate(
                action=action,
                opened=opened,
                query=str(report.get("query") or ""),
            )
            if not expectation_gate.get("rejected_ref_count"):
                plan = (
                    action.get("expectation_mismatch_plan")
                    if isinstance(action.get("expectation_mismatch_plan"), dict)
                    else {}
                )
                rejected = [
                    item
                    for item in (
                        list(plan.get("diagnostic_or_post_hoc") or [])
                        + list(action.get("read") or [])
                    )
                    if isinstance(item, dict)
                ]
                if rejected:
                    expectation_gate["rejected_refs"] = [
                        {
                            **_ref_public_summary(item),
                            "rejected_source_role": (
                                "tool_output"
                                if any(
                                    marker
                                    in " ".join(
                                        [
                                            str(item.get("kind") or ""),
                                            str(item.get("record_metadata") or ""),
                                            str(item.get("target") or ""),
                                        ]
                                    ).casefold()
                                    for marker in ("tool", "function_call_output", "custom_tool_call_output")
                                )
                                else "diagnostic_or_non_actionable"
                            ),
                        }
                        for item in rejected[:_ASK_ANSWER_MAX_REFS]
                    ]
                    expectation_gate["rejected_ref_count"] = len(rejected)
                    expectation_gate["rejected_source_roles"] = _unique_preserving_order(
                        item["rejected_source_role"] for item in expectation_gate["rejected_refs"]
                    )
            answer["source_contract"]["expectation_mismatch_answer_gate"] = (
                _expectation_mismatch_gate_public_contract(expectation_gate)
            )
            return _ask_answer_fail_closed(
                answer,
                "expectation-mismatch proof facets missing or only diagnostic/tool-output/task-doc evidence was opened",
                expectation_gate,
            )
        if work_episode and work_episode.get("status") != "not_applicable":
            return _ask_answer_fail_closed(
                answer,
                "work episode resolver did not find readable primary behavior proof for the query episode",
                _work_episode_next_proof_step(work_episode, action),
            )
        if not usable:
            return _ask_answer_fail_closed(
                answer,
                "answer contract did not expose readable local proof refs",
                _next_proof_step_from_action(action),
            )

    answer_opened = usable
    if route_family in {"remembered_user_wording", "remembered_topic"}:
        remembered_recovery_contract_key = (
            "remembered_topic_causal_user_instruction_recovery"
            if route_family == "remembered_topic"
            else "remembered_user_wording_recovery"
        )
        user_opened = _remembered_user_wording_user_authored_opened(usable)
        if not user_opened:
            recovery = _recover_remembered_user_wording_clean_opened(
                report=report,
                action=action,
                initially_opened=usable,
                cwd=cwd,
                query=str(report.get("query") or ""),
            )
            recovered_opened = list(recovery.get("opened") or [])
            if recovered_opened:
                answer["source_contract"][remembered_recovery_contract_key] = {
                    "schema_version": 1,
                    "status": "clean_user_proof_recovered_after_non_user_refs",
                    "searched_ref_count": recovery.get("searched_ref_count"),
                    "opened_ref_count": recovery.get("opened_ref_count"),
                    "rejected_prompt_echo_ref_count": len(recovery.get("rejected_prompt_echo_refs") or []),
                }
                answer_opened = recovered_opened
            else:
                latest_recovery = _recover_remembered_user_wording_from_latest_user_messages(
                    query=str(report.get("query") or ""),
                    config=remembered_user_latest_recovery,
                    cwd=cwd,
                )
                latest_opened = list(latest_recovery.get("opened") or [])
                if latest_opened:
                    answer["source_contract"][remembered_recovery_contract_key] = {
                        "schema_version": 1,
                        "status": "clean_user_proof_recovered_from_bounded_user_message_scan",
                        "searched_scope": latest_recovery.get("searched_scope"),
                        "searched_files": latest_recovery.get("searched_files"),
                        "candidate_user_message_count": latest_recovery.get("candidate_user_message_count"),
                        "matched_user_message_count": latest_recovery.get("matched_user_message_count"),
                        "rejected_prompt_echo_ref_count": latest_recovery.get("rejected_prompt_echo_ref_count"),
                        "prior_packet_recovery_status": recovery.get("status") or "no_clean_user_proof_recovered",
                    }
                    answer_opened = latest_opened
                else:
                    answer["proof_summary"] = _non_user_opened_ref_summaries(usable)
                    return _ask_answer_fail_closed(
                        answer,
                        "opened remembered-user-wording proof refs did not include user-authored JSONL message rows",
                        {
                            "required_next_proof": "raw user-authored message ref",
                            "suggested_action": (
                                "rerun or inspect remembered-user-wording proof with a role/user-message constraint; "
                                "already surfaced target reads did not yield clean user-authored non-echo proof"
                            ),
                            "bounded_recovery": {
                                "status": recovery.get("status") or "no_clean_user_proof_recovered",
                                "searched_ref_count": recovery.get("searched_ref_count"),
                                "opened_ref_count": recovery.get("opened_ref_count"),
                                "rejected_prompt_echo_ref_count": len(recovery.get("rejected_prompt_echo_refs") or []),
                                "rejected_non_user_ref_count": len(recovery.get("rejected_non_user_refs") or []),
                            },
                            "user_message_recovery": _remembered_user_latest_recovery_public(latest_recovery),
                            "opened_ref_metadata": [_ref_public_summary(item) for item in usable],
                        },
                    )
        else:
            answer_opened = _remembered_user_wording_non_echo_user_opened(
                user_opened,
                query=str(report.get("query") or ""),
            )
            if not answer_opened:
                recovery = _recover_remembered_user_wording_clean_opened(
                    report=report,
                    action=action,
                    initially_opened=usable,
                    cwd=cwd,
                    query=str(report.get("query") or ""),
                )
                recovered_opened = list(recovery.get("opened") or [])
                if recovered_opened:
                    answer["source_contract"][remembered_recovery_contract_key] = {
                        "schema_version": 1,
                        "status": "clean_user_proof_recovered_after_prompt_echo_rejection",
                        "searched_ref_count": recovery.get("searched_ref_count"),
                        "opened_ref_count": recovery.get("opened_ref_count"),
                        "rejected_prompt_echo_ref_count": len(recovery.get("rejected_prompt_echo_refs") or []),
                    }
                    answer_opened = recovered_opened
                else:
                    latest_recovery = _recover_remembered_user_wording_from_latest_user_messages(
                        query=str(report.get("query") or ""),
                        config=remembered_user_latest_recovery,
                        cwd=cwd,
                    )
                    latest_opened = list(latest_recovery.get("opened") or [])
                    if latest_opened:
                        answer["source_contract"][remembered_recovery_contract_key] = {
                            "schema_version": 1,
                            "status": "clean_user_proof_recovered_from_bounded_user_message_scan",
                            "searched_scope": latest_recovery.get("searched_scope"),
                            "searched_files": latest_recovery.get("searched_files"),
                            "candidate_user_message_count": latest_recovery.get("candidate_user_message_count"),
                            "matched_user_message_count": latest_recovery.get("matched_user_message_count"),
                            "prior_packet_recovery_status": recovery.get("status") or "no_clean_user_proof_recovered",
                            "rejected_prompt_echo_ref_count": len(recovery.get("rejected_prompt_echo_refs") or []),
                        }
                        answer_opened = latest_opened
                    else:
                        rejected_echo_refs = [
                            _prompt_echo_ref_public_summary(item, query=str(report.get("query") or ""))
                            for item in user_opened
                        ] + list(recovery.get("rejected_prompt_echo_refs") or [])
                        answer["proof_summary"] = _prompt_echo_opened_ref_summaries(
                            user_opened,
                            query=str(report.get("query") or ""),
                        )
                        return _ask_answer_fail_closed(
                            answer,
                            "opened remembered-user-wording user refs were assignment/review/test prompt echoes, not clean remembered wording proof",
                            {
                                "required_next_proof": "raw user-authored non-echo message ref",
                                "suggested_action": (
                                    "inspect older user-message refs that state the target wording directly; already surfaced "
                                    "target reads did not yield clean user-authored non-echo proof"
                                ),
                                "bounded_recovery": {
                                    "status": recovery.get("status") or "no_clean_user_proof_recovered",
                                    "searched_ref_count": recovery.get("searched_ref_count"),
                                    "opened_ref_count": recovery.get("opened_ref_count"),
                                    "rejected_prompt_echo_ref_count": len(recovery.get("rejected_prompt_echo_refs") or []),
                                    "rejected_non_user_ref_count": len(recovery.get("rejected_non_user_refs") or []),
                                },
                                "user_message_recovery": _remembered_user_latest_recovery_public(latest_recovery),
                                "rejected_user_prompt_echo_refs": rejected_echo_refs[:_ASK_ANSWER_MAX_REFS],
                            },
                        )

    if route_family == "remembered_topic":
        supported_opened = [
            item
            for item in answer_opened
            if _remembered_topic_causal_opened_supports_answer(
                query=str(report.get("query") or ""),
                item=item,
            )
        ]
        if supported_opened:
            answer_opened = supported_opened
        else:
            latest_recovery = _recover_remembered_topic_causal_instruction_from_latest_user_messages(
                query=str(report.get("query") or ""),
                config=remembered_user_latest_recovery,
                cwd=cwd,
            )
            latest_opened = [
                item
                for item in latest_recovery.get("opened") or []
                if _remembered_topic_causal_opened_supports_answer(
                    query=str(report.get("query") or ""),
                    item=item,
                )
            ]
            answer["source_contract"]["remembered_topic_causal_user_instruction_recovery"] = {
                "schema_version": 1,
                "status": (
                    "clean_user_instruction_proof_recovered_from_bounded_user_message_scan"
                    if latest_opened
                    else "no_clean_user_instruction_proof_recovered"
                ),
                "searched_scope": latest_recovery.get("searched_scope"),
                "searched_files": latest_recovery.get("searched_files"),
                "candidate_user_message_count": latest_recovery.get("candidate_user_message_count"),
                "matched_user_message_count": latest_recovery.get("matched_user_message_count"),
                "rejected_prompt_echo_ref_count": latest_recovery.get("rejected_prompt_echo_ref_count"),
                "prior_opened_ref_count": len(answer_opened),
            }
            if latest_opened:
                answer_opened = latest_opened
            else:
                answer["proof_summary"] = [
                    f"{item.get('target')}: rejected_remembered_topic_causal_support_missing"
                    for item in answer_opened[:_ASK_ANSWER_MAX_REFS]
                ]
                return _ask_answer_fail_closed(
                    answer,
                    "opened remembered-topic user refs did not cleanly support the coordination-vs-implementation instruction",
                    {
                        "required_next_proof": "clean user-authored coordination-vs-implementation instruction row",
                        "suggested_action": (
                            "inspect raw user-message refs that explain coordinator/main Codex should coordinate "
                            "the FSM/product loop and delegate implementation to agents; do not use eval-lane, "
                            "task-assignment, report, diagnostic, tool-output, or current-query echoes as final support"
                        ),
                        "user_message_recovery": _remembered_user_latest_recovery_public(latest_recovery),
                        "opened_ref_metadata": [_ref_public_summary(item) for item in answer_opened],
                    },
                )

    episode_selection = _select_work_episode_opened_for_answer(
        query=str(report.get("query") or ""),
        route_family=route_family,
        action=action,
        opened=answer_opened,
        work_episode=work_episode,
    )
    if episode_selection.get("status") == "rejected_unrelated_episode":
        answer["proof_summary"] = list(episode_selection.get("rejected_summaries") or [])
        return _ask_answer_fail_closed(
            answer,
            "opened action-proof refs did not match the work episode implied by the query",
            episode_selection,
        )
    if episode_selection.get("status") == "selected":
        answer_opened = list(episode_selection.get("opened") or answer_opened)

    if route_family == "expectation_mismatch":
        expectation_gate = _expectation_mismatch_answer_proof_gate(
            action=action,
            opened=answer_opened,
            query=str(report.get("query") or ""),
        )
        answer["source_contract"]["expectation_mismatch_answer_gate"] = _expectation_mismatch_gate_public_contract(
            expectation_gate
        )
        if expectation_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(expectation_gate.get("rejected_summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "expectation-mismatch proof facets missing or only diagnostic/tool-output/task-doc evidence was opened",
                expectation_gate,
            )
        answer_opened = list(expectation_gate.get("opened") or answer_opened)

    if route_family == "process_actual_use":
        process_gate = _process_actual_use_answer_proof_gate(
            action=action,
            opened=answer_opened,
            query=str(report.get("query") or ""),
        )
        answer["source_contract"]["process_actual_use_answer_gate"] = (
            _process_actual_use_gate_public_contract(process_gate)
        )
        if process_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(process_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "process actual-use proof buckets missing or only secondary/tool-output/report/task evidence was opened",
                process_gate,
            )
        answer_opened = list(process_gate.get("opened") or answer_opened)

    if _named_task_repair_causality_answer_wanted(
        query=str(report.get("query") or ""),
        route_family=route_family,
    ):
        repair_gate = _named_task_repair_causality_final_proof_gate(
            query=str(report.get("query") or ""),
            opened=answer_opened,
            work_episode=work_episode,
        )
        answer["source_contract"]["named_task_repair_causality_answer_gate"] = (
            _named_task_repair_causality_gate_public_contract(repair_gate)
        )
        if repair_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(repair_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "same-task repair/review proof missing; current-run command/task/output echoes are not final support",
                repair_gate,
            )
        answer_opened = list(repair_gate.get("opened") or answer_opened)

    if post_merge_smoke_wanted:
        smoke_gate = _post_merge_smoke_answer_proof_gate(
            opened=answer_opened,
            query=str(report.get("query") or ""),
        )
        answer["source_contract"]["post_merge_smoke_answer_gate"] = (
            _post_merge_smoke_gate_public_contract(smoke_gate)
        )
        if smoke_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(smoke_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "post-merge smoke proof missing; stop/closeout chronology is not smoke execution proof",
                smoke_gate,
            )
        answer_opened = list(smoke_gate.get("opened") or answer_opened)

    if action_chronology_wanted:
        chronology_gate = _action_chronology_answer_proof_gate(
            query=str(report.get("query") or ""),
            action=action,
            opened=answer_opened,
        )
        if chronology_gate.get("status") != "answer_allowed":
            recovery = _recover_action_chronology_opened(
                query=str(report.get("query") or ""),
                action=action,
                opened=answer_opened,
                cwd=cwd,
            )
            answer["source_contract"]["action_chronology_source_recovery"] = (
                _action_chronology_recovery_public_contract(recovery)
            )
            recovered_opened = list(recovery.get("opened") or [])
            if recovered_opened:
                answer_opened = recovered_opened + list(answer_opened)
                chronology_gate = _action_chronology_answer_proof_gate(
                    query=str(report.get("query") or ""),
                    action=action,
                    opened=answer_opened,
                )
        answer["source_contract"]["action_chronology_answer_gate"] = _action_chronology_gate_public_contract(
            chronology_gate
        )
        if chronology_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(chronology_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "claim-vs-action chronology proof roles missing or only corroborating artifacts were opened",
                chronology_gate,
            )
        answer_opened = list(chronology_gate.get("opened") or answer_opened)

    if _broad_workflow_history_prompt(query=str(report.get("query") or ""), route_family=route_family):
        workflow_gate = _workflow_history_final_proof_gate(
            query=str(report.get("query") or ""),
            opened=answer_opened,
        )
        answer["source_contract"]["workflow_history_final_proof_gate"] = (
            _workflow_history_final_proof_gate_public_contract(workflow_gate)
        )
        if workflow_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(workflow_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "broad workflow-history proof contained only current-run prompt/tool echo material",
                {
                    "workflow_history_final_proof_gate": _workflow_history_final_proof_gate_public_contract(
                        workflow_gate
                    )
                },
            )
        answer_opened = list(workflow_gate.get("opened") or answer_opened)

    if route_family == "latest_status":
        interval_gate = _latest_status_post_anchor_interval_final_proof_gate(
            action=action,
            opened=answer_opened,
        )
        answer["source_contract"]["latest_status_post_anchor_interval_answer_gate"] = interval_gate
        if interval_gate.get("status") not in {"answer_allowed", "not_applied"}:
            answer["proof_summary"] = list(interval_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "latest-status post-anchor interval proof missing; selected-anchor or secondary evidence is not enough",
                interval_gate,
            )
        latest_target_gate = _latest_status_target_episode_anchor_gate(
            query=str(report.get("query") or ""),
            action=action,
            opened=answer_opened,
        )
        if latest_target_gate.get("applies"):
            answer["source_contract"]["latest_status_target_episode_anchor_gate"] = (
                _latest_status_target_episode_anchor_gate_public_contract(latest_target_gate)
            )
            if latest_target_gate.get("status") != "answer_allowed":
                answer["proof_summary"] = list(latest_target_gate.get("summary_lines") or [])
                return _ask_answer_fail_closed(
                    answer,
                    "latest-status selected anchor did not match the requested target episode/entity",
                    latest_target_gate,
                )

    semanticwrapup_correction_root_gate = _semantic_wrapup_correction_root_final_support_gate(
        query=str(report.get("query") or ""),
        route_family=route_family,
        opened=answer_opened,
    )
    if semanticwrapup_correction_root_gate.get("applies"):
        answer["source_contract"]["semanticwrapup_correction_root_final_support_gate"] = (
            _semantic_wrapup_correction_root_gate_public_contract(semanticwrapup_correction_root_gate)
        )
        if semanticwrapup_correction_root_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(semanticwrapup_correction_root_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "semanticwrapup correction-root proof missing same-target primary source-role support",
                semanticwrapup_correction_root_gate,
            )
        answer_opened = list(semanticwrapup_correction_root_gate.get("allowed_refs") or answer_opened)

    target_episode_gate = _target_episode_refs_gate(
        query=str(report.get("query") or ""),
        route_family=route_family,
        refs=answer_opened,
        required_source_role=_target_episode_required_source_role(route_family),
    )
    if target_episode_gate.get("applies"):
        answer["source_contract"]["target_episode_source_role_answer_gate"] = (
            _target_episode_source_role_gate_public_contract(target_episode_gate)
        )
        if target_episode_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(target_episode_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "opened proof refs did not match the requested target episode/entity and required source role",
                target_episode_gate,
            )
        answer_opened = list(target_episode_gate.get("allowed_refs") or answer_opened)

    current_run_gate = _current_run_final_support_gate(
        query=str(report.get("query") or ""),
        route_family=route_family,
        opened=answer_opened,
    )
    if current_run_gate.get("applies"):
        answer["source_contract"]["current_run_final_support_gate"] = (
            _current_run_final_support_gate_public_contract(current_run_gate)
        )
        if current_run_gate.get("status") != "answer_allowed":
            answer["proof_summary"] = list(current_run_gate.get("summary_lines") or [])
            return _ask_answer_fail_closed(
                answer,
                "current-run/control proof cannot support this historical answer without primary historical evidence",
                {
                    "current_run_final_support_gate": _current_run_final_support_gate_public_contract(
                        current_run_gate
                    )
                },
        )
        answer_opened = list(current_run_gate.get("opened") or answer_opened)

    final_veto = _ask_answer_finalization_veto(
        answer=answer,
        query=str(report.get("query") or ""),
        route_family=route_family,
        action=action,
        opened=answer_opened,
    )
    if final_veto.get("blocked"):
        answer["proof_summary"] = list(final_veto.get("summary_lines") or answer.get("proof_summary") or [])
        return _ask_answer_fail_closed(
            answer,
            str(final_veto.get("reason") or "answer source contract is not ready for final support"),
            final_veto.get("next_step"),
        )

    answer["refs_used"] = [_ref_public_summary(item) for item in answer_opened]
    answer["proof_summary"] = [item["summary"] for item in answer_opened]

    direct_answer = _compose_ask_answer(
        query=str(report.get("query") or ""),
        route_family=route_family,
        action=action,
        opened=answer_opened,
    )
    if not direct_answer:
        return _ask_answer_fail_closed(
            answer,
            "opened proof refs do not match a supported direct-answer family",
            _next_proof_step_from_action(action),
        )
    answer["answer_status"] = "answered-from-proof"
    answer["answer"] = _sanitize_final_answer_prose(direct_answer)
    return base


def _latest_status_followthrough_contract(compact: dict) -> dict:
    interval = _compact_post_anchor_interval_for_ask(compact.get("post_anchor_interval_reconstruction") or {})
    coverage = interval.get("coverage") or {}
    guidance = interval.get("next_read_guidance") or {}
    source_tiers = compact.get("source_tiers") or {}
    required_primary = list(source_tiers.get("required_primary") or [])
    selected_anchor_ref = str((compact.get("selected_anchor") or {}).get("source_ref") or "")
    selected_anchor_reads = [
        read
        for read in required_primary
        if str(read.get("answer_support_role") or "") == "selected_parent_thread_raw_user_row"
        or bool(read.get("canonical_authority_read"))
        or (selected_anchor_ref and str(read.get("source_ref") or "") == selected_anchor_ref)
    ]
    interval_reads = [
        read for read in required_primary if str(read.get("answer_support_role") or "") == "post_anchor_interval_activity"
    ]
    if not interval_reads:
        interval_reads = [
            read
            for read in required_primary
            if str(read.get("window_role") or "") == "session_activity"
            and not read.get("selected_anchor_raw_session_row")
        ]
    interval_reads = [
        {
            **read,
            "answer_support_role": read.get("answer_support_role") or "post_anchor_interval_activity",
        }
        for read in interval_reads
    ]
    boundary_reads = [
        read
        for read in required_primary
        if str(read.get("answer_support_role") or "")
        in {"current_user_message_boundary", "status_query_boundary"}
    ]
    complete = coverage.get("complete") is True
    generated_contract = compact.get("generated_report_contract") or {}
    scanner_ready = bool(
        (compact.get("answer_readiness") or {}).get(
            "scanner_coverage_answer_allowed",
            (compact.get("answer_readiness") or {}).get("answer_allowed"),
        )
    )
    return {
        "schema_version": "latest_status_followthrough_contract.v1",
        "status": "required_primary_followthrough" if complete else "post_anchor_interval_not_ready",
        "navigation_only": bool(generated_contract.get("navigation_only")),
        "answer_from_report_allowed": False,
        "required_primary_windows_opened": False,
        "opened_required_primary_window_count": 0,
        "required_primary_window_count": len(required_primary),
        "canonical_follow_through_path": generated_contract.get("canonical_follow_through_path")
        or "status_anchor.source_tiers.required_primary",
        "answer_promotion_requires_opened_post_anchor_interval_refs": True,
        "answer_promotion_allowed_after_contract": scanner_ready,
        "required_read_order": [
            "selected_anchor_raw_session_row",
            "post_anchor_interval_activity_raw_session_refs",
            "required_secondary_task_report_review_git_refs",
        ],
        "selected_anchor_read_count": len(selected_anchor_reads),
        "post_anchor_interval_read_count": len(interval_reads),
        "required_primary_reads": required_primary,
        "required_post_anchor_interval_reads": interval_reads,
        "selected_anchor_reads": selected_anchor_reads,
        "status_boundary_reads": boundary_reads,
        "post_anchor_interval_reconstruction": interval,
        "coverage_status": coverage.get("status"),
        "coverage_complete": coverage.get("complete"),
        "omitted_activity_entry_count": coverage.get("omitted_activity_entry_count", 0),
        "next_read_guidance": guidance,
        "stop": (
            "Do not answer from this generated report, its selected anchor, task/report/git joins, visible context, "
            "or a subset of the required windows. Open every required_primary raw ref first; if coverage is partial "
            "or missing, follow post_anchor_interval_reconstruction.next_read_guidance or fail closed."
        ),
    }


def _compact_post_anchor_interval_for_ask(interval: dict) -> dict:
    if not interval:
        return {}
    compact = dict(interval)
    compact["chronological_activity"] = list(interval.get("chronological_activity") or [])[:8]
    joins = interval.get("secondary_evidence_joins") or {}
    compact["secondary_evidence_joins"] = {
        key: list(joins.get(key) or [])[:4]
        for key in ("tasks", "reports", "reviews", "git_commits")
    }
    return compact


def _latest_status_post_anchor_interval_final_proof_gate(
    *,
    action: dict,
    opened: list[dict],
) -> dict:
    contract = (
        action.get("latest_status_followthrough_contract")
        if isinstance(action.get("latest_status_followthrough_contract"), dict)
        else {}
    )
    if not contract:
        return {
            "schema_version": 1,
            "status": "not_applied",
            "reason": "latest_status_followthrough_contract_absent",
        }
    required_reads = list(
        contract.get("required_primary_reads")
        or contract.get("required_post_anchor_interval_reads")
        or []
    )
    opened_interval = [
        item
        for item in opened
        if _latest_status_opened_answer_support_role(item) == "post_anchor_interval_activity"
    ]
    opened_anchor = [
        item
        for item in opened
        if _latest_status_opened_answer_support_role(item) == "selected_parent_thread_raw_user_row"
    ]
    required_by_identity = {
        identity: read
        for read in required_reads
        if (identity := _latest_status_required_read_identity(read))
    }
    opened_identities = {
        identity
        for item in opened
        if (identity := _latest_status_opened_read_identity(item))
    }
    missing_reads = [
        read
        for identity, read in required_by_identity.items()
        if identity not in opened_identities
    ]
    if contract.get("coverage_complete") is not True:
        return {
            "schema_version": 1,
            "status": "incomplete_post_anchor_interval_coverage",
            "selected_anchor_ref_count": len(opened_anchor),
            "post_anchor_interval_ref_count": len(opened_interval),
            "required_post_anchor_interval_read_count": len(required_reads),
            "coverage_status": contract.get("coverage_status"),
            "coverage_complete": contract.get("coverage_complete"),
            "next_read_guidance": contract.get("next_read_guidance"),
            "required_next_reads": required_reads[:6],
            "summary_lines": [
                "latest-status coverage_complete=false; opened post-anchor refs are partial leads, not final proof."
            ],
            "required_next_proof": (
                "Follow post_anchor_interval_reconstruction.next_read_guidance until coverage_complete=true, "
                "or fail closed without synthesizing status prose."
            ),
        }
    if required_by_identity and not missing_reads:
        return {
            "schema_version": 1,
            "status": "answer_allowed",
            "required_primary_windows_opened": True,
            "answer_from_report_allowed": False,
            "selected_anchor_ref_count": len(opened_anchor),
            "post_anchor_interval_ref_count": len(opened_interval),
            "required_primary_window_count": len(required_by_identity),
            "opened_required_primary_window_count": len(required_by_identity),
            "coverage_status": contract.get("coverage_status"),
            "coverage_complete": contract.get("coverage_complete"),
            "summary_lines": [
                f"{_opened_target_label(item)}: post_anchor_interval_activity"
                for item in opened_interval[:6]
            ],
        }
    return {
        "schema_version": 1,
        "status": "missing_required_primary_windows",
        "required_primary_windows_opened": False,
        "answer_from_report_allowed": False,
        "selected_anchor_ref_count": len(opened_anchor),
        "post_anchor_interval_ref_count": len(opened_interval),
        "required_primary_window_count": len(required_by_identity),
        "opened_required_primary_window_count": len(required_by_identity) - len(missing_reads),
        "missing_required_primary_window_count": len(missing_reads),
        "required_post_anchor_interval_read_count": len(
            contract.get("required_post_anchor_interval_reads") or []
        ),
        "coverage_status": contract.get("coverage_status"),
        "coverage_complete": contract.get("coverage_complete"),
        "next_read_guidance": contract.get("next_read_guidance"),
        "required_next_reads": missing_reads[:6],
        "summary_lines": [
            f"Opened {len(required_by_identity) - len(missing_reads)} of {len(required_by_identity)} required "
            "primary windows; generated report detail, selected-anchor-only evidence, and secondary "
            "task/report/git evidence cannot support latest-status prose."
        ],
        "required_next_proof": (
            "Open every remaining required primary raw window, and if coverage is partial/missing follow "
            "post_anchor_interval_reconstruction.next_read_guidance before answering."
        ),
    }


def _latest_status_opened_answer_support_role(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    for container in (ref, item):
        role = str(container.get("answer_support_role") or "")
        if role:
            return role
        if str(container.get("window_role") or "") == "session_activity":
            return "post_anchor_interval_activity"
        if str(container.get("source_role") or "") == "raw_session_activity":
            return "post_anchor_interval_activity"
    return ""


def _latest_status_required_read_identity(read: dict) -> str:
    source_ref = read.get("source_ref")
    if isinstance(source_ref, dict):
        source_ref = f"{source_ref.get('path') or ''}:{source_ref.get('line') or ''}"
    return str(
        source_ref
        or read.get("openable_target")
        or read.get("target")
        or read.get("path")
        or ""
    )


def _latest_status_opened_read_identity(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else item
    return _latest_status_required_read_identity(ref)


def _opened_target_label(item: dict) -> str:
    return str(item.get("target") or item.get("source_ref") or "(unknown target)")


def _ask_answer_finalization_veto(
    *,
    answer: dict,
    query: str,
    route_family: str,
    action: dict,
    opened: list[dict],
) -> dict:
    merge_gate_allows_artifact_finalization = False
    if _action_chronology_final_role_gate_wanted(query=query, route_family=route_family):
        chronology_gate = (
            answer.get("source_contract", {}).get("action_chronology_answer_gate")
            if isinstance(answer.get("source_contract", {}).get("action_chronology_answer_gate"), dict)
            else {}
        )
        if chronology_gate.get("status") != "answer_allowed":
            chronology_gate = _action_chronology_answer_proof_gate(
                query=query,
                action=action,
                opened=opened,
            )
            answer["source_contract"]["action_chronology_answer_gate"] = _action_chronology_gate_public_contract(
                chronology_gate
            )
        if chronology_gate.get("status") != "answer_allowed":
            return {
                "blocked": True,
                "reason": "claim-vs-action chronology proof roles missing or only corroborating artifacts were opened",
                "next_step": chronology_gate,
                "summary_lines": chronology_gate.get("summary_lines") or [],
            }

    merge_gate = _named_task_merge_provenance_final_proof_gate(
        query=query,
        route_family=route_family,
        opened=opened,
    )
    if merge_gate.get("applies"):
        answer["source_contract"]["named_task_merge_provenance_answer_gate"] = (
            _named_task_merge_provenance_gate_public_contract(merge_gate)
        )
        if merge_gate.get("status") != "answer_allowed":
            return {
                "blocked": True,
                "reason": "named-task merge provenance proof roles missing in opened refs",
                "next_step": merge_gate,
                "summary_lines": merge_gate.get("summary_lines") or [],
            }
        merge_gate_allows_artifact_finalization = True

    action_contract_blocks = _answer_action_contract_blocks_final_answer(action)
    if (
        action_contract_blocks
        and merge_gate_allows_artifact_finalization
        and _named_task_merge_provenance_artifact_gate_can_override_action_contract(
            action=action,
            merge_gate=merge_gate,
        )
    ):
        source_session_proof_reconciled = (
            str(action.get("answer_status") or "").casefold()
            == "not-ready/missing-source-session-proof"
            and str(action.get("promotion_status") or "").casefold()
            == "semantic_debug_completed_task_merge_only_partial_source_session_unopened"
        )
        answer["source_contract"]["named_task_merge_provenance_finalization_override"] = {
            "schema_version": 1,
            "status": (
                "opened_source_session_proof_overrode_stale_missing_source_session_contract"
                if source_session_proof_reconciled
                else "completed_task_artifact_gate_overrode_generic_target_source_role_mismatch"
            ),
            "overridden_answer_action_status": action.get("answer_status"),
            "overridden_promotion_status": action.get("promotion_status"),
            "boundary": (
                "the final provenance gate opened role-valid same-task source-session, task, review, and merge proof"
                if source_session_proof_reconciled
                else "task/review/git integration-or-merge artifacts are final support for completed-task provenance; "
                "raw worker/session chronology remains unproven unless separately opened"
            ),
        }
        action_contract_blocks = False

    if action_contract_blocks:
        return {
            "blocked": True,
            "reason": _answer_action_contract_veto_reason(action),
            "next_step": _answer_action_contract_veto_next_step(action, query=query),
            "summary_lines": [],
        }
    return {"blocked": False}


def _answer_action_contract_blocks_final_answer(action: dict) -> bool:
    answer_status = str(action.get("answer_status") or "").casefold()
    promotion_status = str(action.get("promotion_status") or "").casefold()
    if not answer_status and not promotion_status:
        return False
    if answer_status.startswith("not-ready"):
        return True
    if answer_status == "canonical-continuation-packet-required":
        return True
    if answer_status in {"cannot-answer-directly-yet", "needs_investigation"}:
        return True
    if "target-source-role-mismatch" in answer_status or "target-source-role-mismatch" in promotion_status:
        return True
    if "gate_fail_closed" in promotion_status or promotion_status.endswith("fail_closed"):
        return True
    return False


def _named_task_merge_provenance_artifact_gate_can_override_action_contract(
    *,
    action: dict,
    merge_gate: dict,
) -> bool:
    if merge_gate.get("status") != "answer_allowed":
        return False
    if merge_gate.get("blocking_missing_roles"):
        return False
    role_evidence = merge_gate.get("role_evidence") if isinstance(merge_gate.get("role_evidence"), dict) else {}
    if not all(
        _named_task_merge_provenance_role_satisfied(role, role_evidence)
        for role in _NAMED_TASK_MERGE_PROVENANCE_BASE_BLOCKING_ROLES
    ):
        return False
    answer_status = str(action.get("answer_status") or "").casefold()
    promotion_status = str(action.get("promotion_status") or "").casefold()
    stale_missing_source_session = (
        answer_status == "not-ready/missing-source-session-proof"
        and promotion_status
        == "semantic_debug_completed_task_merge_only_partial_source_session_unopened"
    )
    if stale_missing_source_session:
        return _named_task_merge_provenance_role_satisfied(
            "source_session_proof",
            role_evidence,
        )
    generic_target_mismatch = (
        "target-source-role-mismatch" in answer_status
        or "target-source-role-mismatch" in promotion_status
        or "target_episode_source_role_gate_fail_closed" in promotion_status
    )
    if not generic_target_mismatch:
        return False
    disallowed_failures = (
        "readiness_source_plan_gate_fail_closed",
        "expectation_mismatch",
        "process_actual_use",
        "semanticwrapup",
        "current_run",
        "no_evidence",
    )
    return not any(marker in promotion_status for marker in disallowed_failures)


def _answer_action_contract_veto_reason(action: dict) -> str:
    answer_status = str(action.get("answer_status") or "(unknown)")
    promotion_status = str(action.get("promotion_status") or "")
    if promotion_status:
        return f"ask --execute source contract is {answer_status} / {promotion_status}"
    return f"ask --execute source contract is {answer_status}"


def _answer_action_contract_veto_next_step(action: dict, *, query: str) -> object:
    if _action_chronology_requires_boundary_roles(query):
        required_roles = _action_chronology_required_roles(query)
        return {
            "schema_version": 1,
            "status": "missing_primary_action_chronology",
            "covered_roles": [],
            "missing_roles": required_roles,
            "required_roles": required_roles,
            "corroborating_refs": [],
            "rejected_refs": [],
            "summary_lines": [
                "The answer action contract says the surfaced evidence is not answer-ready for this target/source role."
            ],
            "required_next_proof": (
                "Open same-target raw stop/exhaustion chronology: session messages/tool calls, command output, "
                "repo/worktree/process state, selected/launched-task presence or absence, and explicit remaining-work "
                "or exhaustion evidence."
            ),
            "fallback_action_contract_next_step": _next_proof_step_from_action(action),
        }
    return _next_proof_step_from_action(action)


def _ask_answer_fail_closed(answer: dict, reason: str, next_step: object) -> dict:
    answer["answer_status"] = "cannot-answer-directly-yet"
    answer["fail_closed_reason"] = reason
    answer["next_proof_step"] = next_step
    explanation = _ask_answer_source_role_explanation(answer=answer, next_step=next_step)
    if explanation:
        answer["source_role_explanation"] = explanation
        summary_lines = list(explanation.get("summary_lines") or [])
        if summary_lines:
            existing = list(answer.get("proof_summary") or [])
            answer["proof_summary"] = (existing + summary_lines)[:_ASK_ANSWER_MAX_REFS]
    return {"ask_answer": answer}


def _ask_answer_source_role_explanation(*, answer: dict, next_step: object) -> dict | None:
    route = answer.get("selected_route") if isinstance(answer.get("selected_route"), dict) else {}
    route_family = str(route.get("route_family") or "")
    target_explanation = _target_episode_source_role_explanation(next_step)
    if target_explanation:
        return target_explanation
    if route_family == "expectation_mismatch":
        return _expectation_mismatch_source_role_explanation(next_step)
    if route_family in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        repair_explanation = _named_task_repair_causality_source_role_explanation(next_step)
        if repair_explanation:
            return repair_explanation
        merge_explanation = _named_task_merge_provenance_source_role_explanation(next_step)
        if merge_explanation:
            return merge_explanation
        smoke_explanation = _post_merge_smoke_source_role_explanation(next_step)
        if smoke_explanation:
            return smoke_explanation
        return _action_chronology_source_role_explanation(next_step)
    if route_family == "remembered_user_wording":
        return _remembered_user_wording_source_role_explanation(next_step)
    if route_family == "remembered_topic":
        return _remembered_topic_causal_source_role_explanation(next_step)
    if route_family == "latest_status":
        return _latest_status_source_role_explanation(next_step)
    return None


def _expectation_mismatch_source_role_explanation(next_step: object) -> dict | None:
    if isinstance(next_step, list):
        guidance = [
            item
            for item in next_step
            if isinstance(item, dict)
            and item.get("kind") == "expectation_mismatch_missing_facet_guidance"
        ]
        if guidance:
            first = guidance[0]
            missing = [
                str(item)
                for item in first.get("missing_facets") or []
                if str(item) in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
            ]
            summary = (
                "Expectation-mismatch answer stayed fail-closed because no clean actionable "
                "target historical expected/observed/divergence reads were available."
            )
            return {
                "schema_version": 1,
                "kind": "expectation_mismatch_missing_facets",
                "summary": summary,
                "summary_lines": [
                    f"Expectation-mismatch proof gap: missing clean facets {', '.join(missing) if missing else '(unknown)'}.",
                    "Why not final proof: repair/control/setup/current assignment or wrong-target reads are diagnostic leads only.",
                    "Needed proof: open clean target historical raw/session/tool evidence for expected_behavior_evidence, observed_behavior_or_action_proof, and divergence_evidence before returning a causal answer.",
                ],
                "required_facets": list(_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS),
                "covered_facets": [],
                "missing_facets": missing,
                "rejected_source_roles": _unique_preserving_order(
                    str(reason)
                    for item in guidance
                    for reason in item.get("demotion_reasons") or []
                    if str(reason).strip()
                ),
                "rejected_ref_count": int(first.get("demoted_candidate_count") or 0),
                "rejected_evidence": [],
                "required_final_proof": first.get("required_next_proof"),
            }
        planned_roles = _unique_preserving_order(
            [
                str(item.get("expectation_mismatch_role") or "")
                for item in next_step
                if isinstance(item, dict) and str(item.get("expectation_mismatch_role") or "")
            ]
        )
        missing = [
            facet
            for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
            if facet not in planned_roles
        ]
        summary = (
            "Expectation-mismatch answer stayed fail-closed because the route exposed role reads but "
            "ask --answer did not have opened clean final proof for every required facet."
        )
        return {
            "schema_version": 1,
            "kind": "expectation_mismatch_source_inspection_required",
            "summary": summary,
            "summary_lines": [
                summary,
                "Open the expectation_mismatch_plan reads for expected_behavior_evidence, observed_behavior_or_action_proof, and divergence_evidence before composing a causal answer.",
                "Treat tool-output, docs/task artifacts, diagnostic/post-hoc refs, and same-loop echoes as leads, not final facet proof.",
            ],
            "required_facets": list(_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS),
            "planned_facets": planned_roles,
            "missing_planned_facets": missing,
            "required_final_proof": (
                "Opened clean actionable refs for expected behavior, observed/action proof, and divergence."
            ),
        }
    if not isinstance(next_step, dict) or next_step.get("status") not in {
        "missing_required_facets",
        "no_clean_required_facet_refs",
    }:
        return None
    missing = [
        str(item)
        for item in next_step.get("missing_facets") or []
        if str(item) in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
    ]
    rejected_roles = [
        str(item)
        for item in next_step.get("rejected_source_roles") or []
        if str(item).strip()
    ]
    summary = (
        "Opened expectation-mismatch refs did not cover clean expected/observed/divergence proof facets"
        + (f"; missing {', '.join(missing)}" if missing else "")
        + (f"; rejected source roles included {', '.join(rejected_roles)}" if rejected_roles else "")
        + "."
    )
    return {
        "schema_version": 1,
        "kind": "expectation_mismatch_missing_facets",
        "summary": summary,
        "summary_lines": [
            f"Expectation-mismatch proof gap: missing clean facets {', '.join(missing) if missing else '(unknown)'}."
            if missing
            else "Expectation-mismatch proof gap: opened refs did not provide clean required facets.",
            "Why not final proof: tool-output, docs/task artifacts, diagnostic/post-hoc refs, and non-final support can be useful leads, but they do not prove the expected behavior, observed/action evidence, and divergence for the same episode.",
            "Needed proof: open clean actionable refs for expected_behavior_evidence, observed_behavior_or_action_proof, and divergence_evidence before returning a causal answer.",
        ],
        "required_facets": list(_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS),
        "covered_facets": list(next_step.get("covered_facets") or []),
        "missing_facets": missing,
        "rejected_source_roles": _unique_preserving_order(rejected_roles),
        "rejected_ref_count": int(next_step.get("rejected_ref_count") or 0),
        "rejected_evidence": list(next_step.get("rejected_refs") or [])[:_ASK_ANSWER_MAX_REFS],
        "required_final_proof": next_step.get("required_next_proof"),
    }


def _action_chronology_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict) or next_step.get("status") != "missing_primary_action_chronology":
        return None
    missing = [str(item) for item in next_step.get("missing_roles") or [] if str(item).strip()]
    corroborating = [item for item in next_step.get("corroborating_refs") or [] if isinstance(item, dict)]
    rejected = [item for item in next_step.get("rejected_refs") or [] if isinstance(item, dict)]
    summary = (
        "Claim-vs-action answer stayed fail-closed because opened refs did not provide primary chronological "
        "evidence for the requested stop/follow-up action"
        + (f"; missing roles: {', '.join(missing)}" if missing else "")
        + "."
    )
    return {
        "schema_version": 1,
        "kind": "action_chronology_primary_roles_required",
        "summary": summary,
        "summary_lines": [
            summary,
            "Why not final proof: task/review/status/TODO/state artifacts can corroborate what was recorded, but they do not by themselves prove what the coordinator did after the boundary.",
            "Needed proof: raw chronological session messages/tool calls, command output, git/worktree/process state, and selected/launched-task presence or absence for the same action episode.",
        ],
        "required_roles": list(next_step.get("required_roles") or []),
        "missing_roles": missing,
        "corroborating_ref_count": len(corroborating),
        "rejected_ref_count": len(rejected),
        "corroborating_evidence": corroborating[:_ASK_ANSWER_MAX_REFS],
        "rejected_evidence": rejected[:_ASK_ANSWER_MAX_REFS],
        "required_final_proof": next_step.get("required_next_proof"),
    }


def _remembered_user_wording_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict):
        return None
    refs = [item for item in next_step.get("rejected_user_prompt_echo_refs") or [] if isinstance(item, dict)]
    if not refs and isinstance(next_step.get("user_message_recovery"), dict):
        recovery = next_step["user_message_recovery"]
        status = str(recovery.get("status") or "not_run")
        searched_scope = str(recovery.get("searched_scope") or "codex_history_and_codex_sessions_only")
        searched_files = recovery.get("searched_files")
        matched = recovery.get("matched_user_message_count")
        summary = (
            "Remembered-user wording answer stayed fail-closed because surfaced proof refs were not clean "
            f"user-authored message evidence, and bounded user-message recovery over {searched_scope} "
            f"found {matched if matched is not None else 0} topic-matching user row(s)."
        )
        return {
            "schema_version": 1,
            "kind": "remembered_user_wording_user_message_recovery_empty",
            "summary": summary,
            "summary_lines": [
                summary,
                "Why not final proof: assistant/tool/task/report/current echoes may mention the topic, but they do not prove the user's original instruction.",
                "Needed proof: a raw user-authored Codex history/session message row matching the remembered instruction topic.",
            ],
            "searched_scope": searched_scope,
            "recovery_status": status,
            "searched_files": searched_files,
            "matched_user_message_count": matched,
            "required_final_proof": "Clean user-authored message row, not assistant restatement, task artifact, worker prompt, tool output, or current ask echo.",
        }
    if not refs:
        return None
    evidence = []
    role_labels = []
    for ref in refs[:_ASK_ANSWER_MAX_REFS]:
        label = _prompt_echo_source_role_label(ref.get("prompt_echo_reasons") or [])
        role_labels.append(label)
        evidence.append(
            {
                "target": ref.get("target"),
                "source_ref": ref.get("source_ref"),
                "timestamp_utc": ref.get("timestamp_utc"),
                "source_role": label,
                "prompt_echo_reasons": ref.get("prompt_echo_reasons") or [],
            }
        )
    summary = (
        f"Found {len(refs)} user-role/session row(s) containing the target wording, but the useful rejected "
        "evidence is prompt scaffolding, not clean proof that contributor said the wording."
    )
    return {
        "schema_version": 1,
        "kind": "remembered_user_wording_rejected_prompt_echo",
        "summary": summary,
        "summary_lines": [
            f"Rejected source-role evidence: {summary}",
            "Why not final proof: worker/reviewer/task/test prompt scaffolding can quote a phrase for assignment, review, or testing; it does not establish contributor authored the original remembered wording.",
            "Needed proof: a clean contributor-authored user message row that states the target wording directly and is not an assignment/review/task/test echo.",
        ],
        "rejected_source_roles": _unique_preserving_order(role_labels),
        "rejected_ref_count": len(refs),
        "rejected_evidence": evidence,
        "why_not_final_proof": (
            "These rows are real workflow data, but their role is assignment/review/task/test scaffolding "
            "or current-query echo, so they cannot answer a remembered-user wording question as final proof."
        ),
        "required_final_proof": (
            "Clean contributor-authored user message row with the target wording, not a worker/reviewer assignment, "
            "task note, review fixture, test fixture, or current ask echo."
        ),
    }


def _remembered_topic_causal_source_role_explanation(next_step: object) -> dict | None:
    base = _remembered_user_wording_source_role_explanation(next_step)
    if not base:
        return None
    explanation = dict(base)
    kind = str(explanation.get("kind") or "remembered_user_wording_user_message_recovery_empty")
    explanation["kind"] = kind.replace("remembered_user_wording", "remembered_topic_causal")
    summary = str(explanation.get("summary") or "")
    if summary:
        explanation["summary"] = summary.replace(
            "Remembered-user wording",
            "Remembered-topic causal instruction",
        ).replace(
            "remembered-user wording",
            "remembered-topic causal instruction",
        )
    summary_lines = []
    for line in explanation.get("summary_lines") or []:
        rewritten = str(line).replace(
            "Remembered-user wording",
            "Remembered-topic causal instruction",
        ).replace(
            "remembered-user wording",
            "remembered-topic causal instruction",
        )
        rewritten = rewritten.replace(
            "the user's original instruction",
            "the user's original coordination-vs-implementation instruction",
        )
        summary_lines.append(rewritten)
    if summary_lines:
        explanation["summary_lines"] = summary_lines
    explanation["required_final_proof"] = (
        "Clean contributor-authored user message row explaining the coordination-vs-implementation instruction, "
        "not a worker/reviewer assignment, task note, report, tool output, diagnostic, or current ask echo."
    )
    return explanation


def _prompt_echo_source_role_label(reasons: object) -> str:
    reason_text = " ".join(str(reason).casefold() for reason in reasons if reason)
    labels = []
    if "assignment_or_review_scaffolding" in reason_text:
        labels.append("worker/reviewer assignment or review scaffolding")
    if "task note:" in reason_text:
        labels.append("task-note scaffolding")
    if "review fixture" in reason_text:
        labels.append("review fixture scaffolding")
    if "test fixture" in reason_text:
        labels.append("test fixture scaffolding")
    if "contains_current_ask_query_text" in reason_text:
        labels.append("current ask/query echo")
    return ", ".join(_unique_preserving_order(labels)) if labels else "prompt echo scaffolding"


def _latest_status_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict):
        return None
    choices = [item for item in next_step.get("anchor_choices") or [] if isinstance(item, dict)]
    if not choices:
        return None
    evidence = []
    rejected_or_diagnostic_count = 0
    clean_parent_main_candidates = 0
    for choice in choices[:_ASK_ANSWER_MAX_REFS]:
        source_role = _latest_status_anchor_choice_source_role(choice)
        decision = str(choice.get("decision") or choice.get("confidence") or "")
        classification = str(choice.get("classification") or "")
        if decision in {"rejected", "diagnostic_rejected", "ambiguous"} or classification in {
            "worker_or_reviewer_prompt",
            "non_human_boundary_or_tool_context",
            "admin_or_status_turn",
            "post_status_context",
        }:
            rejected_or_diagnostic_count += 1
        if (
            str(choice.get("source_scope") or "") == "canonical_parent_main_history"
            and classification in {"substantive_work_anchor", "ordinary_human_turn", "short_human_turn"}
        ):
            clean_parent_main_candidates += 1
        evidence.append(
            {
                "wording": choice.get("wording"),
                "timestamp_utc": choice.get("timestamp_utc"),
                "source_ref": choice.get("source_ref"),
                "openable_target": choice.get("openable_target"),
                "source_role": source_role,
                "classification": choice.get("classification"),
                "decision": choice.get("decision"),
                "source_scope": choice.get("source_scope"),
                "ambiguity_label": choice.get("ambiguity_label"),
            }
        )
    clean_status = (
        "ambiguous_clean_parent_main_candidates_found"
        if clean_parent_main_candidates
        else "no_clean_parent_main_anchor_proven"
    )
    summary = (
        "Found candidate latest-status anchors, but the output has not proven one clean parent/main "
        "user instruction as contributor's last real instruction."
    )
    return {
        "schema_version": 1,
        "kind": "latest_status_rejected_or_ambiguous_anchor_candidates",
        "summary": summary,
        "summary_lines": [
            f"Rejected/ambiguous anchor evidence: {summary}",
            "Why not final proof: worker/reviewer/control prompt candidates are real workflow activity or control prompts, but they are not contributor's last real instruction.",
            f"Clean parent/main anchor status: {clean_status}.",
            "Needed proof: select or provide one clean parent/main user instruction by quoted message, exact timestamp, or thread/session boundary before answering what happened since it.",
        ],
        "candidate_count": len(choices),
        "rejected_or_diagnostic_candidate_count": rejected_or_diagnostic_count,
        "clean_parent_main_anchor_status": clean_status,
        "anchor_candidates": evidence,
        "why_not_final_proof": (
            "Rejected diagnostic/control candidates can describe real workflow activity after an anchor, "
            "but latest-status answers need a proven clean user anchor before summarizing work since it."
        ),
        "required_final_proof": (
            "One clean parent/main contributor-authored instruction, identified by quoted message, exact timestamp, "
            "or thread/session boundary."
        ),
    }


def _latest_status_anchor_choice_source_role(choice: dict) -> str:
    classification = str(choice.get("classification") or "")
    source_scope = str(choice.get("source_scope") or "")
    ambiguity = str(choice.get("ambiguity_label") or "")
    if classification == "worker_or_reviewer_prompt" or "worker_or_reviewer" in ambiguity:
        return "worker/reviewer control prompt"
    if classification in {"non_human_boundary_or_tool_context", "admin_or_status_turn", "post_status_context"}:
        return "workflow/control prompt or boundary context"
    if source_scope == "canonical_parent_main_history":
        return "canonical parent/main user-anchor candidate"
    if source_scope == "session_only_unproven_parent_main_candidate":
        return "session-only unproven user-anchor candidate"
    return "ambiguous latest-status anchor candidate"


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for value in values:
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _ask_answer_scoped_no_evidence(answer: dict, scoped: dict, *, cwd: Path) -> dict:
    searched_files = scoped.get("searched_files")
    searched_bytes = scoped.get("searched_bytes")
    candidate_interpretation = scoped.get("candidate_interpretation")
    root_kinds = [str(kind) for kind in scoped.get("searched_root_kinds") or [] if str(kind).strip()]
    root_kind_text = ", ".join(root_kinds[:4])
    if len(root_kinds) > 4:
        root_kind_text += f", +{len(root_kinds) - 4} more"
    root_label = _scoped_no_evidence_root_label(scoped)
    if candidate_interpretation == "claim_boundary_non_support":
        text = (
            "No searched-scope support was found for the stronger claim. The found material is "
            "classified as claim-boundary/non-support, so the answer is scoped no-support, not "
            "all-history absence."
        )
    else:
        text = (
            "No evidence was found in the searched scope. This is a searched-scope answer only, "
            "not all-history absence."
        )
    text += f" Scope searched: {searched_files} files, {searched_bytes} bytes"
    if root_kind_text:
        text += f" across {root_label} ({root_kind_text})"
    text += "."
    caps_hit = [str(item) for item in scoped.get("caps_hit") or [] if str(item).strip()]
    text += (
        " Source boundary: "
        f"{scoped.get('source_boundary') or 'selected searched roots only; all-history absence remains unproven'}."
    )
    text += (
        " Blind spots: "
        f"caps_hit={','.join(caps_hit) if caps_hit else 'none'}, "
        f"missing_roots={scoped.get('missing_root_count')}, "
        f"excluded_roots={scoped.get('excluded_root_count')}, "
        "outside_selected_roots=not searched."
    )
    if scoped.get("scope_mode"):
        text += f" Scope mode: {scoped.get('scope_mode')}."
    excluded_roots = [str(item) for item in scoped.get("excluded_roots") or [] if str(item).strip()]
    if excluded_roots:
        text += f" Excluded roots: {', '.join(excluded_roots[:8])}."
    if scoped.get("contamination_policy"):
        text += f" Contamination policy: {scoped.get('contamination_policy')}."
    burden = scoped.get("command_burden") if isinstance(scoped.get("command_burden"), dict) else {}
    if burden:
        text += (
            " Command burden: "
            f"structured_execution_count={burden.get('structured_execution_count')}, "
            f"read_more_count={burden.get('read_more_count')}, "
            f"wrong_command_count={burden.get('wrong_command_count')}, "
            f"fallback_count={burden.get('fallback_count')}."
        )
    repo_boundary = _scoped_no_evidence_current_repo_boundary(scoped=scoped, cwd=cwd)
    if repo_boundary:
        text += f" {repo_boundary}"
    if scoped.get("cap_hit") is True:
        caps_text = ", ".join(caps_hit[:4])
        text += " Cap boundary: search or retention caps were hit"
        if caps_text:
            text += f" ({caps_text})"
        text += ", so unsearched or unretained material may exist outside this answer."
    if scoped.get("read_more_actionability") == "optional_diagnostic_context":
        text += " Diagnostic candidate reads remain optional; they are not required before this scoped no-support answer."
    answer["answer_status"] = "answered-from-scoped-no-evidence-contract"
    answer["answer"] = text
    answer["refs_used"] = [
        {
            "kind": "scoped_no_evidence_contract",
            "answer_scope": scoped.get("answer_scope"),
            "scope_mode": scoped.get("scope_mode"),
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "searched_root_kinds": root_kinds,
            "excluded_roots": excluded_roots,
            "contamination_policy": scoped.get("contamination_policy"),
            "command_burden": burden,
            "current_repo_docs_tasks_searched": repo_boundary == "",
            "cap_hit": scoped.get("cap_hit"),
            "caps_hit": scoped.get("caps_hit", []),
            "candidate_interpretation": candidate_interpretation,
            "saved_report": scoped.get("saved_report"),
        }
    ]
    answer["proof_summary"] = [
        f"answer_scope={scoped.get('answer_scope')}",
        f"candidate_interpretation={candidate_interpretation}",
        f"searched_root_kinds={','.join(root_kinds) if root_kinds else '(unknown)'}",
        f"cap_hit={scoped.get('cap_hit')}",
        f"read_more_required_before_scoped_no_evidence_answer={scoped.get('read_more_required_before_scoped_no_evidence_answer')}",
    ]
    if scoped.get("contamination_policy"):
        answer["proof_summary"].append(f"contamination_policy={scoped.get('contamination_policy')}")
    return {"ask_answer": answer}


def _scoped_no_evidence_root_label(scoped: dict) -> str:
    privacy_labels = {str(item) for item in scoped.get("searched_root_privacy_labels") or [] if str(item).strip()}
    root_kinds = {str(item) for item in scoped.get("searched_root_kinds") or [] if str(item).strip()}
    transcript_kinds = {"codex_sessions", "codex_history", "claude_projects"}
    if "repo_source_committed" in root_kinds:
        return "committed repo source scope"
    if root_kinds and root_kinds <= transcript_kinds:
        return "private transcript roots" if "private_root" in privacy_labels else "transcript roots"
    if "private_root" in privacy_labels:
        return "selected private roots"
    return "selected roots"


def _scoped_no_evidence_current_repo_boundary(*, scoped: dict, cwd: Path) -> str:
    root_kinds = {str(item) for item in scoped.get("searched_root_kinds") or [] if str(item).strip()}
    if not root_kinds:
        return ""
    repo_artifact_dirs = [cwd / "tasks", cwd / "docs", cwd / "codex" / "state"]
    if not any(path.exists() for path in repo_artifact_dirs):
        return ""
    repo_root_kinds = {"cwd", "current_repo", "repo_artifacts", "task_docs", "project_docs"}
    if root_kinds & repo_root_kinds:
        return ""
    outside_selected = str(scoped.get("outside_selected_roots") or "not searched unless explicitly selected")
    return (
        "Current repo docs/tasks under the cwd were not part of this no-evidence search "
        f"({outside_selected}), so repo artifacts can still contain evidence outside this answer."
    )


def _next_proof_step_from_action(action: dict) -> object:
    continuation_packet = action.get("canonical_continuation_packet")
    if isinstance(continuation_packet, dict) and continuation_packet:
        return continuation_packet
    raw_worker_chronology = action.get("raw_worker_chronology_handoff")
    if isinstance(raw_worker_chronology, dict) and raw_worker_chronology:
        required_roles = list(raw_worker_chronology.get("required_roles") or [])
        missing_roles = list(raw_worker_chronology.get("missing_roles") or required_roles)
        return {
            "schema_version": 1,
            "status": "missing_primary_action_chronology",
            "covered_roles": [],
            "missing_roles": missing_roles,
            "required_roles": required_roles,
            "corroborating_refs": [],
            "rejected_refs": [],
            "summary_lines": [
                "The execute handoff says raw worker chronology is required; task/review/git artifacts are not enough."
            ],
            "required_next_proof": (
                "Open same-task raw worker/session chronology: worker messages, tool calls, command output, "
                "or raw transcript rows that prove the requested action."
            ),
            "fallback_action_contract_next_step": raw_worker_chronology.get("read") or [],
        }
    root_handoff = action.get("root_handoff_contract")
    if isinstance(root_handoff, dict) and root_handoff:
        return {
            "root_handoff_contract": root_handoff,
            "rerun_command": root_handoff.get("recommended_rerun_command"),
            "missing_root_guidance": root_handoff.get("missing_root_guidance"),
            "candidate_roots": root_handoff.get("candidate_roots") or [],
        }
    clarification = action.get("clarification_request")
    shortlist = action.get("parent_main_anchor_shortlist")
    if clarification or shortlist:
        choices = _latest_status_anchor_choices(action)
        return {
            "clarification_request": clarification,
            "anchor_choices": choices,
            "accepted_anchor_forms": (
                clarification.get("accepted_anchor_forms")
                if isinstance(clarification, dict)
                else ["quoted_instruction_or_message", "exact_timestamp", "thread_or_session_boundary"]
            ),
            "rerun_instruction": (
                clarification.get("rerun_instruction")
                if isinstance(clarification, dict)
                else "Rerun what-happened-since with one selected anchor before answering."
            ),
        }
    reads = action.get("read")
    if reads:
        return reads
    return action.get("only_continue_if") or action.get("stop")


def _ask_answer_missing_source_session_next_step(action: dict) -> dict:
    if action.get("answer_status") != "not-ready/missing-source-session-proof":
        return {}
    contract = (
        action.get("missing_source_session_stop_contract")
        if isinstance(action.get("missing_source_session_stop_contract"), dict)
        else {}
    )
    handoff = (
        action.get("named_task_merge_provenance_handoff")
        if isinstance(action.get("named_task_merge_provenance_handoff"), dict)
        else {}
    )
    bounded_actions = [
        item
        for item in (
            contract.get("bounded_retained_source_actions")
            or handoff.get("retained_source_reads")
            or []
        )
        if isinstance(item, dict) and item.get("command")
    ]
    stop = contract.get("stop") or action.get("stop")
    return {
        "schema_version": 1,
        "contract_kind": "missing-source-session-stop",
        "status": "not-ready/missing-source-session-proof",
        "answer_allowed": False,
        "missing_roles": ["source_session_proof"],
        "bounded_retained_source_actions": bounded_actions,
        "generic_semantic_debug_allowed": False,
        "stop": stop,
        "unblock": contract.get("unblock")
        or (
            "Open a role-valid same-task raw JSONL/session ref through a bounded retained-source action; "
            "task, review, scratch, report, and summary artifacts cannot unblock this facet."
        ),
    }


def _ask_answer_can_follow_raw_proof_despite_status(*, action: dict, route_family: str) -> bool:
    if route_family not in {"action_proof", "remembered_topic"} or action.get("answer_status") != "inspect_recovery_first":
        return False
    for item in action.get("read") or []:
        if not isinstance(item, dict):
            continue
        if item.get("target_source_role") != "target_historical_raw":
            continue
        if item.get("final_support_allowed") is not True:
            continue
        if item.get("diagnostic_only") is True:
            continue
        if item.get("source_ref") or item.get("target"):
            return True
    return False


def _ask_answer_work_episode_handoff(*, report: dict, action: dict, route_family: str, cwd: Path) -> dict:
    if route_family not in _ASK_ANSWER_WORK_EPISODE_ROUTE_FAMILIES:
        return {}
    named = (
        action.get("named_task_action_proof_handoff")
        if isinstance(action.get("named_task_action_proof_handoff"), dict)
        else {}
    )
    if named:
        return {}
    query = str(report.get("query") or "")
    hints = _work_episode_query_hints(query)
    if not _work_episode_hints_meaningful(hints):
        return {"schema_version": 1, "status": "not_applicable", "reason": "query_has_no_episode_anchors"}
    roots = _ask_answer_execution_roots(report.get("execution") or {}, cwd=cwd)
    primary_refs: list[dict] = []
    secondary_refs: list[dict] = []
    scan_notes: list[str] = []
    if roots:
        primary_refs, secondary_refs, scan_notes = _work_episode_refs_from_roots(
            roots,
            cwd=cwd,
            hints=hints,
        )
    status = (
        "primary_proof_refs_available"
        if primary_refs
        else "secondary_claim_refs_only"
        if secondary_refs
        else "no_matching_episode_refs"
    )
    return {
        "schema_version": 1,
        "status": status,
        "route_family": route_family,
        "query_episode_hints": _work_episode_public_hints(hints),
        "roots": [_safe_relative_path(cwd, root) for root in roots],
        "primary_proof_refs": primary_refs[:_ASK_ANSWER_MAX_REFS],
        "secondary_claim_refs": secondary_refs[:_ASK_ANSWER_MAX_REFS],
        "scan_notes": scan_notes[:8],
        "source_boundaries": {
            "primary_evidence": "raw chat/tool/file-edit, command output, diffs, tests, and commit/diff evidence",
            "secondary_claims": "tracker status, checklist state, issue state, labels, priority, and assistant summaries",
            "answer_rule": "answer behavior questions only from primary evidence matching the query episode",
        },
    }


def _ask_answer_execution_roots(execution: dict, *, cwd: Path) -> list[Path]:
    argv = list(execution.get("argv") or [])
    roots: list[Path] = []
    index = 0
    while index < len(argv):
        if argv[index] == "--root" and index + 1 < len(argv):
            root = Path(str(argv[index + 1])).expanduser()
            if not root.is_absolute():
                root = cwd / root
            root = root.resolve(strict=False)
            if root.exists() and root not in roots:
                roots.append(root)
            index += 2
            continue
        index += 1
    return roots[:4]


def _work_episode_refs_from_roots(roots: list[Path], *, cwd: Path, hints: dict) -> tuple[list[dict], list[dict], list[str]]:
    primary: list[dict] = []
    secondary: list[dict] = []
    notes: list[str] = []
    paths = _work_episode_scan_paths(roots)
    text_by_path: dict[Path, str] = {}
    for path in paths:
        try:
            text_by_path[path] = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            notes.append(f"skipped {path.name}: {exc}")
    for path, text in text_by_path.items():
        path_primary, path_secondary = _work_episode_refs_for_path(
            path=path,
            text=text,
            all_text=text_by_path,
            cwd=cwd,
            hints=hints,
        )
        primary.extend(path_primary)
        secondary.extend(path_secondary)
    primary = _sort_work_episode_refs(_dedupe_ask_answer_proof_refs(primary))
    secondary = _sort_work_episode_refs(_dedupe_ask_answer_proof_refs(secondary))
    notes.append(f"scanned {len(text_by_path)} local work-episode artifact files")
    return primary, secondary, notes


def _work_episode_scan_paths(roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    total_bytes = 0
    for root in roots:
        root_paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in root_paths:
            if len(paths) >= _ASK_ANSWER_WORK_EPISODE_SCAN_MAX_FILES:
                return paths
            if path.suffix.lower() not in _ASK_ANSWER_WORK_EPISODE_FILE_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if total_bytes + size > _ASK_ANSWER_WORK_EPISODE_SCAN_MAX_BYTES:
                return paths
            paths.append(path)
            total_bytes += size
    return paths


def _work_episode_refs_for_path(
    *,
    path: Path,
    text: str,
    all_text: dict[Path, str],
    cwd: Path,
    hints: dict,
) -> tuple[list[dict], list[dict]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _work_episode_jsonl_refs(path=path, text=text, cwd=cwd, hints=hints)
    if suffix in {".diff", ".patch"}:
        match = _work_episode_text_match(text + " " + path.as_posix(), hints)
        if not match["accepted"]:
            return [], []
        return [
            _work_episode_ref(
                cwd,
                path,
                kind="work_episode_primary_evidence",
                proof_surface="diff",
                reason="matching work-episode diff/file-change evidence",
                match=match,
            )
        ], []
    if suffix in {".md", ".markdown"}:
        return _work_episode_markdown_refs(path=path, text=text, cwd=cwd, hints=hints)
    if suffix == ".json":
        return _work_episode_json_refs(path=path, text=text, all_text=all_text, cwd=cwd, hints=hints)
    if suffix == ".csv":
        match = _work_episode_text_match(text + " " + path.as_posix(), hints)
        if match["accepted"]:
            return [], [
                _work_episode_ref(
                    cwd,
                    path,
                    kind="work_episode_secondary_claim",
                    proof_surface="tracker_csv_status_claim",
                    reason="matching exported tracker CSV row; status metadata is a claim until corroborated",
                    match=match,
                    final_support_allowed=False,
                )
            ]
    return [], []


def _work_episode_jsonl_refs(*, path: Path, text: str, cwd: Path, hints: dict) -> tuple[list[dict], list[dict]]:
    primary: list[dict] = []
    secondary: list[dict] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            row = {}
        row_text = _work_episode_json_row_text(row) if isinstance(row, dict) else raw
        match = _work_episode_text_match(f"{row_text} {path.as_posix()}", hints)
        if not match["accepted"]:
            continue
        kind = str(row.get("kind") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        payload_type = str(payload.get("type") or "")
        primary_kind = kind in {"tool_call", "file_edit", "command_output"} or payload_type in {
            "function_call",
            "function_call_output",
        }
        if primary_kind:
            primary.append(
                _work_episode_ref(
                    cwd,
                    path,
                    line=line_no,
                    kind="work_episode_primary_evidence",
                    proof_surface=f"raw_jsonl_{kind or payload_type}",
                    reason="matching raw conversation/tool/file-edit work-episode evidence",
                    match=match,
                )
            )
        else:
            secondary.append(
                _work_episode_ref(
                    cwd,
                    path,
                    line=line_no,
                    kind="work_episode_secondary_context",
                    proof_surface=f"raw_jsonl_{kind or payload_type or 'message'}",
                    reason="matching user/assistant episode context; inspect primary tool/file/test evidence for behavior proof",
                    match=match,
                    final_support_allowed=False,
                )
            )
    return primary, secondary


def _work_episode_json_row_text(row: dict) -> str:
    parts = [
        str(row.get("timestamp") or row.get("ts") or ""),
        str(row.get("session_id") or ""),
        str(row.get("event_id") or ""),
        str(row.get("role") or ""),
        str(row.get("kind") or ""),
        str(row.get("tool") or ""),
        str(row.get("command") or ""),
        str(row.get("path") or ""),
        str(row.get("summary") or ""),
        str(row.get("stdout") or ""),
        str(row.get("text") or ""),
        _extract_text_from_json_row(row),
    ]
    return " ".join(part for part in parts if part)


def _work_episode_markdown_refs(*, path: Path, text: str, cwd: Path, hints: dict) -> tuple[list[dict], list[dict]]:
    match = _work_episode_text_match(text + " " + path.as_posix(), hints)
    if not match["accepted"]:
        return [], []
    lower = text.casefold()
    primary_capable = (
        "checked boxes are status claims" not in lower
        and any(marker in lower for marker in _ASK_ANSWER_WORK_EPISODE_PRIMARY_MARKERS)
    )
    proof_surface = (
        "github_pr_local_evidence"
        if "pull request" in lower or "local evidence leads" in lower
        else "markdown_verification_or_checklist"
        if primary_capable
        else "markdown_status_claim"
    )
    if primary_capable:
        return [
            _work_episode_ref(
                cwd,
                path,
                kind="work_episode_primary_evidence",
                proof_surface=proof_surface,
                reason="matching markdown work-episode evidence lead; status/checklist text remains secondary",
                match=match,
            )
        ], []
    return [], [
        _work_episode_ref(
            cwd,
            path,
            kind="work_episode_secondary_claim",
            proof_surface=proof_surface,
            reason="matching markdown status/checklist artifact; not behavior proof by itself",
            match=match,
            final_support_allowed=False,
        )
    ]


def _work_episode_json_refs(
    *,
    path: Path,
    text: str,
    all_text: dict[Path, str],
    cwd: Path,
    hints: dict,
) -> tuple[list[dict], list[dict]]:
    match = _work_episode_text_match(text + " " + path.as_posix(), hints)
    if not match["accepted"]:
        return [], []
    primary: list[dict] = []
    secondary: list[dict] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    primary_join_allowed = _work_episode_json_primary_joins_allowed(data, hints=hints)
    same_entity_join_ids = _work_episode_json_primary_entity_ids(data) if primary_join_allowed else []
    if primary_join_allowed:
        for lead in _work_episode_json_primary_leads(data):
            primary.extend(
                _work_episode_refs_for_lead(
                    lead=lead,
                    origin=path,
                    all_text=all_text,
                    cwd=cwd,
                    hints=hints,
                    parent_match=match,
                    same_entity_join_ids=same_entity_join_ids,
                )
            )
        if not primary:
            primary.extend(
                _work_episode_refs_by_shared_handles(
                    handles=_work_episode_json_relation_handles(data),
                    origin=path,
                    all_text=all_text,
                    cwd=cwd,
                    hints=hints,
                    parent_match=match,
                    same_entity_join_ids=same_entity_join_ids,
                )
            )
    if primary_join_allowed and not primary and _work_episode_json_is_branch_diff_episode(data):
        diff_rel = (
            (data.get("work_episode") or {}).get("relations", {}).get("diff")
            if isinstance((data.get("work_episode") or {}).get("relations"), dict)
            else None
        )
        if diff_rel:
            primary.extend(
                _work_episode_refs_for_lead(
                    lead=str(diff_rel),
                    origin=path,
                    all_text=all_text,
                    cwd=cwd,
                    hints=hints,
                    parent_match=match,
                    same_entity_join_ids=same_entity_join_ids,
                )
            )
    secondary.append(
        _work_episode_ref(
            cwd,
            path,
            kind="work_episode_secondary_claim",
            proof_surface=_work_episode_json_proof_surface(data),
            reason="matching local tracker/task/episode JSON; status fields are joins or claims until primary evidence corroborates them",
            match=match,
            final_support_allowed=False,
        )
    )
    return primary, secondary


def _work_episode_json_primary_joins_allowed(data: dict, *, hints: dict) -> bool:
    query_ids = set(str(value).casefold() for value in hints.get("ids") or [] if value)
    if not query_ids:
        return True
    primary_ids = set(_work_episode_json_primary_entity_ids(data))
    return bool(primary_ids and query_ids.intersection(primary_ids))


def _work_episode_json_primary_entity_ids(data: dict) -> list[str]:
    ids: list[str] = []
    work_episode = data.get("work_episode") if isinstance(data.get("work_episode"), dict) else {}
    identifiers = work_episode.get("identifiers") if isinstance(work_episode.get("identifiers"), dict) else {}
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    pull_request = data.get("pull_request") if isinstance(data.get("pull_request"), dict) else {}
    for value in (
        work_episode.get("episode_id"),
        identifiers.get("task_id"),
        identifiers.get("issue_id"),
        identifiers.get("identifier"),
        issue.get("identifier"),
    ):
        if value not in (None, ""):
            ids.append(str(value).casefold())
    for value in (issue.get("number"), pull_request.get("number")):
        if value not in (None, ""):
            ids.append(f"#{value}".casefold())
    return ids


def _work_episode_json_primary_leads(data: dict) -> list[str]:
    leads: list[str] = []
    for value in data.get("primary_evidence_leads") or []:
        if isinstance(value, str):
            leads.append(value)
    work_episode = data.get("work_episode") if isinstance(data.get("work_episode"), dict) else {}
    for value in work_episode.get("primary_evidence_leads") or []:
        if isinstance(value, str):
            leads.append(value)
    relations = work_episode.get("relations") if isinstance(work_episode.get("relations"), dict) else {}
    if isinstance(relations.get("diff"), str):
        leads.append(relations["diff"])
    return leads


def _work_episode_json_relation_handles(data: dict) -> list[str]:
    handles: list[str] = []
    work_episode = data.get("work_episode") if isinstance(data.get("work_episode"), dict) else {}
    identifiers = work_episode.get("identifiers") if isinstance(work_episode.get("identifiers"), dict) else {}
    issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    pull_request = data.get("pull_request") if isinstance(data.get("pull_request"), dict) else {}
    linear_issue = data.get("issue") if isinstance(data.get("issue"), dict) else {}
    for value in [
        identifiers.get("branch"),
        identifiers.get("head_commit"),
        pull_request.get("number"),
        (pull_request.get("head") or {}).get("ref") if isinstance(pull_request.get("head"), dict) else None,
        (pull_request.get("head") or {}).get("sha") if isinstance(pull_request.get("head"), dict) else None,
        issue.get("number"),
        linear_issue.get("identifier"),
    ]:
        if value not in (None, ""):
            handles.append(str(value))
    for key in ("linkedBranches", "linkedCommits"):
        values = linear_issue.get(key)
        if isinstance(values, list):
            handles.extend(str(value) for value in values if value not in (None, ""))
    return handles


def _work_episode_json_is_branch_diff_episode(data: dict) -> bool:
    return str(data.get("fixture_id") or "") == "branch_commit_diff_only" or (
        isinstance(data.get("work_episode"), dict)
        and isinstance((data["work_episode"].get("relations") or {}), dict)
        and bool(data["work_episode"]["relations"].get("diff"))
    )


def _work_episode_json_proof_surface(data: dict) -> str:
    fixture_id = str(data.get("fixture_id") or "")
    if fixture_id == "github_export" or "pull_request" in data:
        return "github_issue_pr_status_claim"
    if fixture_id == "linear_export":
        return "linear_issue_status_claim"
    if fixture_id == "branch_commit_diff_only":
        return "branch_commit_metadata_claim"
    return "local_json_status_claim"


def _work_episode_refs_for_lead(
    *,
    lead: str,
    origin: Path,
    all_text: dict[Path, str],
    cwd: Path,
    hints: dict,
    parent_match: dict,
    same_entity_join_ids: list[str] | None = None,
) -> list[dict]:
    refs: list[dict] = []
    lead_text = lead.casefold()
    for path, text in all_text.items():
        if path == origin:
            continue
        rel_from_origin = (origin.parent / lead).resolve(strict=False)
        if path.resolve(strict=False) == rel_from_origin or path.name.casefold() in lead_text:
            child_match = _work_episode_text_match(f"{text} {path.as_posix()} {lead}", hints)
            match = child_match if child_match["accepted"] else parent_match
            if _work_episode_path_primary_capable(path, text):
                refs.append(
                    _work_episode_ref(
                        cwd,
                        path,
                        kind="work_episode_primary_evidence",
                        proof_surface=_work_episode_path_proof_surface(path, text),
                        reason=f"primary evidence lead from {origin.name}: {lead}",
                        match=match,
                        same_entity_join_ids=same_entity_join_ids,
                    )
                )
    if refs:
        return refs
    handle = _work_episode_lead_handle(lead)
    if not handle:
        return refs
    return _work_episode_refs_by_shared_handles(
        handles=[handle],
        origin=origin,
        all_text=all_text,
        cwd=cwd,
        hints=hints,
        parent_match=parent_match,
        same_entity_join_ids=same_entity_join_ids,
    )


def _work_episode_refs_by_shared_handles(
    *,
    handles: list[str],
    origin: Path,
    all_text: dict[Path, str],
    cwd: Path,
    hints: dict,
    parent_match: dict,
    same_entity_join_ids: list[str] | None = None,
) -> list[dict]:
    refs: list[dict] = []
    normalized_handles = [handle.casefold() for handle in handles if len(str(handle).strip()) >= 2]
    if not normalized_handles:
        return refs
    for path, text in all_text.items():
        if path == origin or not _work_episode_path_primary_capable(path, text):
            continue
        folded = f"{path.as_posix()} {text}".casefold()
        if not any(handle in folded for handle in normalized_handles):
            continue
        child_match = _work_episode_text_match(folded, hints)
        refs.append(
            _work_episode_ref(
                cwd,
                path,
                kind="work_episode_primary_evidence",
                proof_surface=_work_episode_path_proof_surface(path, text),
                reason=f"primary evidence joined from local artifact handles in {origin.name}",
                match=child_match if child_match["accepted"] else parent_match,
                same_entity_join_ids=same_entity_join_ids,
            )
        )
    return refs


def _work_episode_lead_handle(lead: str) -> str:
    commit = re.search(r"\b[0-9a-f]{7,40}\b", lead, flags=re.IGNORECASE)
    if commit:
        return commit.group(0)
    branch = re.search(r"\b[a-z0-9._-]+/[a-z0-9._/-]+\b", lead, flags=re.IGNORECASE)
    if branch:
        return branch.group(0)
    test_ref = re.search(r"\btests?/[^\s`'\",]+", lead, flags=re.IGNORECASE)
    if test_ref:
        return test_ref.group(0)
    return ""


def _work_episode_path_primary_capable(path: Path, text: str) -> bool:
    suffix = path.suffix.lower()
    folded = text.casefold()
    if suffix in {".diff", ".patch", ".jsonl"}:
        return True
    if suffix in {".md", ".markdown"}:
        if "checked boxes are status claims" in folded:
            return False
        return any(marker in folded for marker in _ASK_ANSWER_WORK_EPISODE_PRIMARY_MARKERS)
    return False


def _work_episode_path_proof_surface(path: Path, text: str) -> str:
    suffix = path.suffix.lower()
    folded = text.casefold()
    if suffix in {".diff", ".patch"}:
        return "diff"
    if suffix == ".jsonl":
        return "raw_jsonl_episode"
    if "pull request" in folded or "local evidence leads" in folded:
        return "github_pr_local_evidence"
    if "verification notes" in folded or "pytest" in folded:
        return "markdown_verification_or_checklist"
    return "local_primary_evidence"


def _work_episode_ref(
    cwd: Path,
    path: Path,
    *,
    kind: str,
    proof_surface: str,
    reason: str,
    match: dict,
    line: int | None = None,
    final_support_allowed: bool = True,
    same_entity_join_ids: list[str] | None = None,
) -> dict:
    rel = _safe_relative_path(cwd, path)
    target = f"{rel}:{line}" if line else rel
    return {
        "kind": kind,
        "proof_surface": proof_surface,
        "target": target,
        "path": rel,
        "source_ref": {"path": rel, **({"line": line} if line else {})},
        "target_source_role": "target_historical_raw" if final_support_allowed else "target_historical_artifact",
        "final_support_allowed": final_support_allowed,
        "diagnostic_only": False,
        "read_more_actionable": final_support_allowed,
        "followthrough_actionable": True,
        "reason": reason,
        "work_episode_match": {
            "accepted": match.get("accepted"),
            "score": match.get("score"),
            "matched_ids": match.get("matched_ids"),
            "matched_tokens": match.get("matched_tokens"),
            "matched_rare_tokens": match.get("matched_rare_tokens"),
            "matched_dates": match.get("matched_dates"),
        },
        "work_episode_same_entity_join_ids": [
            str(value).casefold()
            for value in (same_entity_join_ids or [])
            if str(value).strip()
        ],
        "source_boundary": (
            "primary behavior proof for the matched work episode"
            if final_support_allowed
            else "secondary episode context or status claim; not final behavior proof by itself"
        ),
    }


def _sort_work_episode_refs(refs: list[dict]) -> list[dict]:
    return sorted(
        refs,
        key=lambda item: (
            item.get("final_support_allowed") is not True,
            -int((item.get("work_episode_match") or {}).get("score") or 0),
            str(item.get("target") or ""),
        ),
    )


def _work_episode_query_hints(query: str) -> dict:
    text = query.casefold()
    tokens = []
    for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text):
        normalized = _work_episode_token_stem(token)
        if normalized and normalized not in _ASK_ANSWER_WORK_EPISODE_TOKEN_STOPWORDS and normalized not in tokens:
            tokens.append(normalized)
    id_candidates: list[tuple[int, str]] = []
    for pattern in (
        r"\b[A-Z][A-Z0-9]+-\d+\b",
        r"#\d+\b",
        r"\bissue\s+#?(\d+)\b",
        r"\bpr\s+#?(\d+)\b",
    ):
        for match in re.finditer(pattern, query, flags=re.IGNORECASE):
            value = match.group(0)
            if match.lastindex:
                value = f"#{match.group(1)}"
            value = value.casefold()
            id_candidates.append((match.start(), value))
    ids = []
    for _start, value in sorted(id_candidates, key=lambda item: (item[0], item[1])):
        if value not in ids:
            ids.append(value)
    branches = []
    for match in re.finditer(r"\b[a-z0-9._-]+/[a-z0-9._/-]+\b", text):
        value = match.group(0)
        if value not in branches:
            branches.append(value)
    commits = []
    for match in re.finditer(r"\b[0-9a-f]{7,40}\b", text):
        value = match.group(0)
        if value not in commits:
            commits.append(value)
    dates = _work_episode_date_hints(text)
    rare_tokens = [token for token in tokens if len(token) >= 7]
    return {
        "tokens": tokens[:16],
        "rare_tokens": rare_tokens[:8],
        "ids": ids[:8],
        "subject_id": ids[0] if ids else None,
        "branches": branches[:6],
        "commits": commits[:6],
        "dates": dates[:6],
    }


def _work_episode_token_stem(token: str) -> str:
    token = token.strip("_-").casefold()
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _work_episode_date_hints(text: str) -> list[str]:
    dates: list[str] = []
    for month, number in _ASK_ANSWER_MONTH_NUMBERS.items():
        for match in re.finditer(rf"\b{re.escape(month)}\s+([0-9]{{1,2}})\b", text):
            day = int(match.group(1))
            if 1 <= day <= 31:
                value = f"{number}-{day:02d}"
                if value not in dates:
                    dates.append(value)
    for match in re.finditer(r"\b(20\d\d)-([01]\d)-([0-3]\d)\b", text):
        value = f"{match.group(2)}-{match.group(3)}"
        if value not in dates:
            dates.append(value)
    return dates


def _work_episode_hints_meaningful(hints: dict) -> bool:
    return bool(
        hints.get("ids")
        or hints.get("branches")
        or hints.get("commits")
        or hints.get("dates")
        or len(hints.get("tokens") or []) >= 2
    )


def _work_episode_text_match(text: str, hints: dict) -> dict:
    folded = text.casefold()
    normalized = " ".join(_work_episode_token_stem(token) for token in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", folded))
    matched_ids = [value for value in hints.get("ids") or [] if value in folded]
    matched_branches = [value for value in hints.get("branches") or [] if value in folded]
    matched_commits = [value for value in hints.get("commits") or [] if value in folded]
    matched_dates = [value for value in hints.get("dates") or [] if value in folded]
    matched_tokens = [
        token
        for token in hints.get("tokens") or []
        if token in normalized or token.replace("_", "-") in folded
    ]
    matched_rare = [token for token in hints.get("rare_tokens") or [] if token in matched_tokens]
    exact_score = 10 * (len(matched_ids) + len(matched_branches) + len(matched_commits)) + 5 * len(matched_dates)
    score = exact_score + len(matched_tokens) + (2 * len(matched_rare))
    has_exact = bool(matched_ids or matched_branches or matched_commits or matched_dates)
    rare_required = bool(hints.get("rare_tokens"))
    date_required = bool(hints.get("dates"))
    accepted = bool(has_exact or len(matched_tokens) >= 2 or matched_rare) and (
        not rare_required or bool(matched_rare or has_exact)
    ) and (
        not date_required or bool(matched_dates or matched_ids or matched_branches or matched_commits)
    )
    return {
        "accepted": accepted,
        "score": score,
        "matched_ids": matched_ids,
        "matched_branches": matched_branches,
        "matched_commits": matched_commits,
        "matched_dates": matched_dates,
        "matched_tokens": matched_tokens[:8],
        "matched_rare_tokens": matched_rare[:8],
    }


def _select_work_episode_opened_for_answer(
    *,
    query: str,
    route_family: str,
    action: dict,
    opened: list[dict],
    work_episode: dict,
) -> dict:
    if route_family not in _ASK_ANSWER_WORK_EPISODE_ROUTE_FAMILIES:
        return {"status": "not_applied", "opened": opened}
    named = (
        action.get("named_task_action_proof_handoff")
        if isinstance(action.get("named_task_action_proof_handoff"), dict)
        else {}
    )
    if named:
        return {"status": "not_applied", "opened": opened}
    hints = _work_episode_query_hints(query)
    if not _work_episode_hints_meaningful(hints):
        return {"status": "not_applied", "opened": opened}
    selected: list[tuple[int, dict]] = []
    rejected: list[dict] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        explicit_id_gate = _work_episode_explicit_id_gate(item, hints)
        if not explicit_id_gate.get("allowed"):
            rejected.append(_ref_public_summary(item))
            continue
        match = ref.get("work_episode_match") if isinstance(ref.get("work_episode_match"), dict) else {}
        accepted_by_ref = bool(
            match.get("accepted")
            or match.get("matched_ids")
            or match.get("matched_tokens")
            or match.get("matched_dates")
        )
        if not accepted_by_ref:
            match = _work_episode_text_match(
                " ".join(
                    str(value)
                    for value in (
                        item.get("target"),
                        item.get("summary"),
                        ref.get("target"),
                        ref.get("reason"),
                        ref.get("proof_surface"),
                    )
                    if value
                ),
                hints,
            )
        if match.get("accepted") or accepted_by_ref:
            selected.append((int(match.get("score") or 0), item))
        else:
            rejected.append(_ref_public_summary(item))
    if selected:
        selected.sort(key=lambda row: (-row[0], str(row[1].get("target") or "")))
        return {
            "status": "selected",
            "opened": [item for _score, item in selected[:_ASK_ANSWER_MAX_REFS]],
            "work_episode_status": work_episode.get("status"),
        }
    return {
        "status": "rejected_unrelated_episode",
        "required_episode_hints": _work_episode_public_hints(hints),
        "work_episode_status": work_episode.get("status"),
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "rejected_summaries": [
            f"{item.get('target')}: did not match query episode anchors"
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ],
        "next_required_proof": _work_episode_rejected_next_required_proof(hints),
    }


def _work_episode_rejected_next_required_proof(hints: dict) -> str:
    ids = _work_episode_public_hints(hints).get("ids") or []
    subject_id = str(hints.get("subject_id") or "")
    if len(ids) > 1 and subject_id:
        explicit_id_clause = (
            f" for multiple explicit ids, the primary proof must contain or join to subject "
            f"{subject_id}, or show a same-entity relation linking {', '.join(ids)};"
        )
    else:
        explicit_id_clause = (
            f" for explicit ids, the primary proof must contain or join to {', '.join(ids)};"
            if ids
            else ""
        )
    return (
        "Open raw tool/file-edit/diff/test/commit evidence that matches the query episode anchors;"
        f"{explicit_id_clause} do not answer from unrelated action-proof refs."
    )


def _work_episode_explicit_id_gate(item: dict, hints: dict) -> dict:
    query_ids = {
        str(value).casefold()
        for value in hints.get("ids") or []
        if str(value).strip()
    }
    if not query_ids:
        return {"allowed": True, "reason": "no_explicit_query_id"}
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    join_ids = {
        str(value).casefold()
        for value in ref.get("work_episode_same_entity_join_ids") or []
        if str(value).strip()
    }
    primary_match = _work_episode_text_match(_work_episode_opened_primary_text(item, ref), hints)
    coverage_ids = join_ids.union(
        str(value).casefold()
        for value in primary_match.get("matched_ids") or []
        if str(value).strip()
    )
    subject_id = str(hints.get("subject_id") or "").casefold()
    if len(query_ids) > 1 and subject_id and subject_id not in coverage_ids:
        return {
            "allowed": False,
            "reason": "primary_proof_missing_subject_query_id",
            "required_subject_id": subject_id,
            "required_ids": sorted(query_ids),
        }
    if query_ids.intersection(coverage_ids):
        return {
            "allowed": True,
            "reason": "explicit_query_id_covered",
            "matched_ids": sorted(query_ids.intersection(coverage_ids)),
            "match": primary_match,
        }
    return {
        "allowed": False,
        "reason": "primary_proof_missing_explicit_query_id",
        "required_ids": sorted(query_ids),
    }


def _work_episode_opened_primary_text(item: dict, ref: dict) -> str:
    return " ".join(
        str(value)
        for value in (
            item.get("target"),
            item.get("summary"),
            ref.get("target"),
            ref.get("path"),
            ref.get("reason"),
            ref.get("proof_surface"),
        )
        if value
    )


def _work_episode_public_hints(hints: dict) -> dict:
    return {
        "ids": list(hints.get("ids") or [])[:4],
        "subject_id": hints.get("subject_id"),
        "branches": list(hints.get("branches") or [])[:4],
        "commits": list(hints.get("commits") or [])[:4],
        "dates": list(hints.get("dates") or [])[:4],
        "tokens": list(hints.get("tokens") or [])[:8],
        "rare_tokens": list(hints.get("rare_tokens") or [])[:4],
    }


def _work_episode_public_contract(work_episode: dict) -> dict:
    return {
        "schema_version": work_episode.get("schema_version"),
        "status": work_episode.get("status"),
        "query_episode_hints": work_episode.get("query_episode_hints"),
        "roots": work_episode.get("roots"),
        "primary_proof_ref_count": len(work_episode.get("primary_proof_refs") or []),
        "secondary_claim_ref_count": len(work_episode.get("secondary_claim_refs") or []),
        "source_boundaries": work_episode.get("source_boundaries"),
        "scan_notes": work_episode.get("scan_notes"),
    }


def _work_episode_next_proof_step(work_episode: dict, action: dict) -> dict:
    return {
        "work_episode_resolver": _work_episode_public_contract(work_episode),
        "secondary_claim_refs": list(work_episode.get("secondary_claim_refs") or [])[:3],
        "required_next_proof": (
            "matching raw behavior evidence: tool call/output, file edit, diff, test output, or commit evidence"
        ),
        "fallback_action_contract_next_step": _next_proof_step_from_action(action),
    }


def _collect_ask_answer_proof_refs(action: dict) -> list[dict]:
    refs: list[dict] = []
    target_handoff = action.get("target_proof_handoff") if isinstance(action.get("target_proof_handoff"), dict) else {}
    named_handoff = (
        action.get("named_task_action_proof_handoff")
        if isinstance(action.get("named_task_action_proof_handoff"), dict)
        else {}
    )
    if named_handoff.get("status") in {
        "current_named_task_proof_refs_available",
        "current_named_task_stopped_episode_proof_refs_available",
    }:
        return _dedupe_ask_answer_proof_refs(
            [item for item in named_handoff.get("read") or [] if isinstance(item, dict)]
        )
    for group in (
        target_handoff.get("required_primary_proof"),
        named_handoff.get("read"),
        action.get("read"),
    ):
        for item in group if isinstance(group, list) else []:
            if isinstance(item, dict):
                refs.append(item)
    latest_contract = (
        action.get("latest_status_followthrough_contract")
        if isinstance(action.get("latest_status_followthrough_contract"), dict)
        else {}
    )
    required_primary_count = len(latest_contract.get("required_primary_reads") or [])
    return _dedupe_ask_answer_proof_refs(
        refs,
        limit=max(_ASK_ANSWER_MAX_REFS, required_primary_count),
    )


def _dedupe_ask_answer_proof_refs(refs: list[dict], *, limit: int = _ASK_ANSWER_MAX_REFS) -> list[dict]:
    deduped: list[dict] = []
    seen: dict[tuple[str, str, str], dict] = {}
    for item in refs:
        source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        source_ref_text = item.get("source_ref") if isinstance(item.get("source_ref"), str) else ""
        source_target = ""
        if source_ref:
            source_target = f"{source_ref.get('path') or ''}:{source_ref.get('line') or ''}"
        key = (
            str(item.get("kind") or ""),
            str(
                item.get("target")
                or item.get("openable_target")
                or item.get("path")
                or item.get("command")
                or source_target
                or source_ref_text
            ),
            str(item.get("proof_surface") or ""),
        )
        if key in seen:
            _merge_work_episode_ref_metadata(seen[key], item)
            continue
        seen[key] = item
        deduped.append(item)
    return deduped[:limit]


def _active_status_front_door_proof_refs(action: dict, refs: list[dict]) -> list[dict]:
    then_use = action.get("then_use") if isinstance(action.get("then_use"), dict) else {}
    status = (
        then_use.get("active_status_front_door")
        if isinstance(then_use.get("active_status_front_door"), dict)
        else {}
    )
    allowed_paths = {
        str(item.get("path") or "")
        for item in status.get("source_refs") or []
        if isinstance(item, dict)
        and item.get("exists") is True
        and str(item.get("role") or "") in {"required_front_door", "active_task"}
        and str(item.get("path") or "").strip()
    }
    if not allowed_paths:
        return refs
    return _dedupe_ask_answer_proof_refs(
        [
            ref
            for ref in refs
            if str(ref.get("path") or ref.get("target") or ref.get("openable_target") or "") in allowed_paths
        ]
    )


def _merge_work_episode_ref_metadata(existing: dict, incoming: dict) -> None:
    existing_join_ids = [
        str(value).casefold()
        for value in existing.get("work_episode_same_entity_join_ids") or []
        if str(value).strip()
    ]
    for value in incoming.get("work_episode_same_entity_join_ids") or []:
        normalized = str(value).casefold()
        if normalized.strip() and normalized not in existing_join_ids:
            existing_join_ids.append(normalized)
    if existing_join_ids:
        existing["work_episode_same_entity_join_ids"] = existing_join_ids

    existing_match = existing.get("work_episode_match") if isinstance(existing.get("work_episode_match"), dict) else {}
    incoming_match = incoming.get("work_episode_match") if isinstance(incoming.get("work_episode_match"), dict) else {}
    if int(incoming_match.get("score") or 0) > int(existing_match.get("score") or 0):
        existing["work_episode_match"] = incoming_match


def _open_ask_answer_proof_refs(refs: list[dict], *, cwd: Path) -> list[dict]:
    opened: list[dict] = []
    for ref in refs:
        if ref.get("kind") in {"targeted_fail_closed_boundary", "missing_role_recovery_command"}:
            opened.append({"status": "skipped", "reason": "recovery_or_fail_closed_ref", "ref": ref})
            continue
        if (
            ref.get("kind") == "current_named_task_artifact"
            and ref.get("proof_surface") == "stopped_before_implementation_causal_proof"
        ):
            summary = str(ref.get("summary") or "").strip()
            opened.append(
                {
                    "status": "opened" if summary else "skipped",
                    "reason": "embedded_stopped_before_implementation_causal_proof" if summary else "empty_stopped_episode_proof",
                    "target": ref.get("target"),
                    "path": ref.get("path"),
                    "line": ref.get("line"),
                    "summary": summary[:_ASK_ANSWER_MAX_FILE_CHARS],
                    "ref": ref,
                }
            )
            continue
        if ref.get("kind") == "current_named_task_selection_rationale":
            summary = str(ref.get("summary") or "").strip()
            opened.append(
                {
                    "status": "opened" if summary else "skipped",
                    "reason": "embedded_current_selection_rationale" if summary else "empty_selection_rationale",
                    "target": ref.get("target"),
                    "path": ref.get("path"),
                    "line": None,
                    "summary": summary[:_ASK_ANSWER_MAX_FILE_CHARS],
                    "ref": ref,
                }
            )
            continue
        if ref.get("kind") == "completed_named_task_embedded_summary":
            summary = str(ref.get("summary") or "").strip()
            opened.append(
                {
                    "status": "opened" if summary else "skipped",
                    "reason": "embedded_completed_task_summary" if summary else "empty_completed_task_summary",
                    "target": ref.get("target"),
                    "path": ref.get("path"),
                    "line": None,
                    "summary": summary[:_ASK_ANSWER_MAX_FILE_CHARS],
                    "ref": ref,
                }
            )
            continue
        if ref.get("kind") in {
            "current_named_task_commit_evidence",
            "completed_named_task_commit_evidence",
            "completed_named_task_merge_commit_evidence",
            "completed_named_task_integration_commit_evidence",
        }:
            summary = " ".join(
                part
                for part in (
                    str(ref.get("commit_time") or ""),
                    str(ref.get("commit") or ref.get("target") or ""),
                    f"parent_count={ref.get('parent_count')}" if ref.get("parent_count") is not None else "",
                    f"author={ref.get('author')}" if ref.get("author") else "",
                    f"committer={ref.get('committer')}" if ref.get("committer") else "",
                    str(ref.get("subject") or ""),
                )
                if part
            )
            opened.append(
                {
                    "status": "opened",
                    "target": ref.get("target"),
                    "path": ref.get("path"),
                    "line": None,
                    "summary": summary[:_ASK_ANSWER_MAX_LINE_CHARS],
                    "ref": ref,
                }
            )
            continue
        source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
        openable_source_ref = (
            ref.get("openable_source_ref") if isinstance(ref.get("openable_source_ref"), dict) else {}
        )
        target = str(
            source_ref.get("path")
            or openable_source_ref.get("path")
            or ref.get("path")
            or ref.get("openable_target")
            or ref.get("target")
            or ""
        )
        if not target and isinstance(ref.get("source_ref"), str):
            target = _target_from_canonical_status_source_ref(str(ref.get("source_ref")))
        line = _positive_int(source_ref.get("line")) or None
        if not line:
            line = _positive_int(openable_source_ref.get("line")) or None
        if not line and target:
            target, line = _split_ref_target(target)
        if not target:
            opened.append({"status": "skipped", "reason": "no_local_file_target", "ref": ref})
            continue
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = cwd / path
        if not path.is_file():
            opened.append({"status": "skipped", "reason": "target_not_a_file", "target": target, "ref": ref})
            continue
        opened.append(_read_ask_answer_ref(path=path, line=line, ref=ref, cwd=cwd))
    return opened


def _split_ref_target(target: str) -> tuple[str, int | None]:
    match = re.match(r"^(.+):(\d+)$", target)
    if not match:
        return target, None
    return match.group(1), int(match.group(2))


def _target_from_canonical_status_source_ref(source_ref: str) -> str:
    match = re.match(r"^(?:codex_history|codex_sessions):(.+?)(?::(\d+))?$", source_ref)
    if not match:
        return ""
    path = match.group(1)
    line = match.group(2)
    return f"{path}:{line}" if line else path


def _read_ask_answer_ref(*, path: Path, line: int | None, ref: dict, cwd: Path) -> dict:
    rel = _safe_relative_path(cwd, path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {"status": "error", "reason": str(exc), "target": rel, "ref": ref}
    if not lines:
        return {"status": "opened", "target": rel, "line": line, "summary": "", "ref": ref}
    if line:
        index = max(0, min(line - 1, len(lines) - 1))
        raw = lines[index]
        summary = _summarize_jsonl_or_text_line(raw, fallback=f"{rel}:{index + 1}")
        record_metadata = _ask_answer_jsonl_record_metadata(raw)
        return {
            "status": "opened",
            "target": f"{rel}:{index + 1}",
            "path": rel,
            "line": index + 1,
            "summary": summary,
            "record_metadata": record_metadata,
            "ref": ref,
        }
    if path.suffix.lower() == ".jsonl":
        summaries = []
        record_summaries = []
        for index, raw in enumerate(lines[:8], start=1):
            summary = _summarize_jsonl_or_text_line(raw, fallback=f"{rel}:{index}")
            if summary:
                summaries.append(summary)
                record_summaries.append(
                    {
                        "target": f"{rel}:{index}",
                        "line": index,
                        "summary": summary,
                        "record_metadata": _ask_answer_jsonl_record_metadata(raw),
                    }
                )
        summary = " | ".join(summaries)[:_ASK_ANSWER_MAX_FILE_CHARS]
    else:
        summary = _summarize_markdown_file(lines)
        record_summaries = []
    return {
        "status": "opened",
        "target": rel,
        "path": rel,
        "line": None,
        "summary": summary,
        "record_summaries": record_summaries,
        "ref": ref,
    }


def _summarize_jsonl_or_text_line(raw: str, *, fallback: str) -> str:
    text = raw.strip()
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        return text[:_ASK_ANSWER_MAX_LINE_CHARS]
    if not isinstance(row, dict):
        return text[:_ASK_ANSWER_MAX_LINE_CHARS]
    timestamp = str(row.get("timestamp") or row.get("ts") or "")
    role = str(row.get("role") or "")
    extracted = _extract_text_from_json_row(row)
    prefix = " ".join(part for part in (timestamp, role) if part)
    if prefix and extracted:
        return f"{prefix}: {extracted[:_ASK_ANSWER_MAX_LINE_CHARS]}"
    if extracted:
        return extracted[:_ASK_ANSWER_MAX_LINE_CHARS]
    return f"{fallback}: {text[:_ASK_ANSWER_MAX_LINE_CHARS]}"


def _extract_text_from_json_row(row: dict) -> str:
    if isinstance(row.get("text"), str):
        return row["text"].strip()
    if isinstance(row.get("message"), str):
        return row["message"].strip()
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    if isinstance(payload.get("text"), str):
        return payload["text"].strip()
    if isinstance(payload.get("message"), str):
        label = str(payload.get("type") or "message")
        return f"{label}: {payload['message'].strip()}"
    if isinstance(payload.get("last_agent_message"), str):
        label = str(payload.get("type") or "last_agent_message")
        return f"{label}: {payload['last_agent_message'].strip()}"
    content = payload.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"].strip())
        if parts:
            return " ".join(parts)
    if payload.get("type") == "function_call":
        name = payload.get("name") or payload.get("function_name") or "function_call"
        arguments = payload.get("arguments")
        if isinstance(arguments, dict) and arguments.get("cmd"):
            return f"tool call {name}: {arguments.get('cmd')}"
        return f"tool call {name}: {arguments}"
    if payload.get("type") == "function_call_output":
        output = payload.get("output")
        if isinstance(output, str):
            return f"tool output: {output.strip()}"
    return ""


def _post_merge_smoke_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    post_merge = any(marker in normalized for marker in ("post-merge", "post merge", "postmerge"))
    smoke = any(marker in normalized for marker in ("smoke", "smokes", "smoke test", "smoke check"))
    claim_shape = (
        "only claim" in normalized
        or "actually" in normalized
        or "really" in normalized
        or "versus claim" in normalized
    )
    return post_merge and smoke and claim_shape


def _post_merge_smoke_answer_proof_gate(*, opened: list[dict], query: str = "") -> dict:
    direct: list[dict] = []
    rejected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        target_reasons = _target_episode_ref_reject_reasons(
            item,
            required_ids=_explicit_query_episode_ids(query),
        )
        role = _post_merge_smoke_opened_source_role(item)
        public = _ref_public_summary(item)
        public["post_merge_smoke_source_role"] = role["source_role"]
        public["post_merge_smoke_reasons"] = target_reasons + role["reasons"]
        if role["source_role"] == "direct_post_merge_smoke_proof" and not target_reasons:
            annotated = dict(item)
            annotated["post_merge_smoke_reasons"] = role["reasons"]
            direct.append(annotated)
        else:
            rejected.append(public)

    if direct:
        return {
            "schema_version": 1,
            "status": "answer_allowed",
            "opened": direct[:_ASK_ANSWER_MAX_REFS],
            "required_proof": "direct_post_merge_smoke_command_or_result",
            "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
            "summary_lines": [
                f"{item.get('target')}: direct_post_merge_smoke_proof reasons={','.join(item.get('post_merge_smoke_reasons') or [])}"
                for item in direct[:_ASK_ANSWER_MAX_REFS]
            ],
        }
    return {
        "schema_version": 1,
        "status": "missing_direct_post_merge_smoke_proof",
        "required_proof": "direct_post_merge_smoke_command_or_result",
        "missing_roles": ["direct_post_merge_smoke_command_or_result"],
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "summary_lines": [
            "Opened action-proof refs did not contain direct post-merge smoke command/result evidence; "
            "stop, closeout, merge, and generic task chronology are not smoke execution proof."
        ],
        "required_next_proof": (
            "Open raw command/tool output or a reviewed artifact that explicitly records the post-merge "
            "smoke commands/results for the named work episode."
        ),
        "acceptable_evidence": [
            "raw exec/tool call for post-merge smoke commands",
            "raw exec/tool output showing smoke pass/fail results",
            "reviewed post-merge artifact that records the exact smoke commands or results",
        ],
    }


def _post_merge_smoke_opened_source_role(item: dict) -> dict:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    target = str(item.get("target") or "")
    path = str(item.get("path") or "")
    text = " ".join(
        str(value)
        for value in (
            item.get("summary"),
            target,
            ref.get("reason"),
            ref.get("proof_surface"),
            ref.get("proof_role"),
            ref.get("target_source_role"),
            ref.get("kind"),
        )
        if value
    ).casefold()
    has_post_merge = any(marker in text for marker in ("post-merge", "post merge", "postmerge"))
    has_smoke = any(marker in text for marker in ("smoke", "smokes", "smoke test", "smoke check"))
    has_command_or_result = any(
        marker in text
        for marker in (
            "tool call",
            "tool output",
            "function_call",
            "function_call_output",
            "exec_command",
            "jsonl-history-search",
            "pytest",
            "compileall",
            "git diff --check",
            "passed",
            "failed",
            "exit code 0",
            "process exited with code 0",
            "command",
            "result",
        )
    )
    direct_surface = (
        str(ref.get("target_source_role") or "") == "target_historical_raw"
        or str(ref.get("proof_role") or "") in {"command_or_tool_call", "command_or_tool_output"}
        or str(ref.get("kind") or "") == "jsonl_source_ref"
        or path.endswith(".jsonl")
    )
    review_artifact_surface = (
        str(ref.get("proof_surface") or "") == "review_artifact"
        or "/codex/state/reviews/" in target
        or target.startswith("codex/state/reviews/")
        or "/codex/state/reviews/" in path
        or path.startswith("codex/state/reviews/")
    )
    verifies_post_merge_smokes = any(
        marker in text
        for marker in (
            "verified post-merge",
            "post-merge verified",
            "post-merge checks passed",
            "post-merge pressure passed",
            "post-merge smokes passed",
            "post-merge smoke passed",
            "post-merge installed validation and smokes passed",
            "post-merge verification passed",
        )
    )
    independent_review_surface = review_artifact_surface and verifies_post_merge_smokes
    if has_post_merge and has_smoke and has_command_or_result and (direct_surface or independent_review_surface):
        reasons = ["post_merge_smoke_terms", "command_or_result_terms"]
        reasons.append("raw_tool_or_jsonl" if direct_surface else "independent_review_artifact")
        return {"source_role": "direct_post_merge_smoke_proof", "reasons": reasons}
    task_file_surface = (
        str(ref.get("proof_surface") or "") == "task_file"
        or "/tasks/" in target
        or target.startswith("tasks/")
        or "/tasks/" in path
        or path.startswith("tasks/")
    )
    return {
        "source_role": "not_direct_post_merge_smoke_proof",
        "reasons": [
            reason
            for reason, present in (
                ("missing_post_merge_terms", not has_post_merge),
                ("missing_smoke_terms", not has_smoke),
                ("missing_command_or_result_terms", not has_command_or_result),
                ("task_closeout_not_direct_smoke_proof", task_file_surface and not direct_surface),
                (
                    "review_artifact_missing_explicit_post_merge_smoke_verification",
                    review_artifact_surface and not verifies_post_merge_smokes,
                ),
                ("unsupported_source_surface", not (direct_surface or review_artifact_surface or task_file_surface)),
            )
            if present
        ],
    }


def _post_merge_smoke_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "required_proof": gate.get("required_proof"),
        "missing_roles": gate.get("missing_roles") or [],
        "direct_ref_count": len(gate.get("opened") or []),
        "rejected_ref_count": len(gate.get("rejected_refs") or []),
    }


def _post_merge_smoke_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict) or next_step.get("status") != "missing_direct_post_merge_smoke_proof":
        return None
    return {
        "schema_version": 1,
        "kind": "post_merge_smoke_direct_proof_required",
        "summary_lines": list(next_step.get("summary_lines") or [])[:_ASK_ANSWER_MAX_REFS],
        "required_final_proof": next_step.get("required_next_proof"),
        "acceptable_evidence": next_step.get("acceptable_evidence") or [],
        "why_not_final_proof": (
            "Stop/closeout chronology, merge/status proof, and generic task summaries can establish adjacent workflow "
            "state, but they do not prove that post-merge smoke commands actually ran."
        ),
    }


def _named_task_repair_causality_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    if not re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", query, flags=re.IGNORECASE):
        return False
    causal_shape = any(marker in normalized for marker in ("why", "because", "need", "needed"))
    review_shape = any(marker in normalized for marker in ("review", "reviewer", "rereview", "re-review"))
    repair_shape = any(marker in normalized for marker in ("repair", "repairs", "fixed review", "changes requested"))
    return causal_shape and review_shape and repair_shape


def _named_task_repair_causality_final_proof_gate(
    *,
    query: str,
    opened: list[dict],
    work_episode: dict,
) -> dict:
    if not _named_task_repair_causality_answer_wanted(query=query, route_family="semantic_debug"):
        return {"schema_version": 1, "status": "not_applicable", "opened": opened}
    if work_episode.get("status") == "primary_proof_refs_available":
        return {
            "schema_version": 1,
            "status": "answer_allowed",
            "opened": opened[:_ASK_ANSWER_MAX_REFS],
            "required_roles": [
                "same_task_review_failure_evidence",
                "same_task_repair_implementation_or_test_evidence",
            ],
            "missing_roles": [],
            "rejected_ref_count": 0,
        }

    rejected = []
    for item in opened:
        reasons = _named_task_repair_causality_reject_reasons(item, query=query)
        public = _ref_public_summary(item)
        public["repair_causality_reject_reasons"] = reasons
        rejected.append(public)

    return {
        "schema_version": 1,
        "status": "missing_same_task_repair_review_proof",
        "work_episode_status": work_episode.get("status"),
        "required_roles": [
            "same_task_review_failure_evidence",
            "same_task_repair_implementation_or_test_evidence",
        ],
        "missing_roles": [
            "same_task_review_failure_evidence",
            "same_task_repair_implementation_or_test_evidence",
        ],
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            "Opened refs were not promoted because the work-episode resolver found no same-task primary "
            "proof; current command/task/output echoes can remain diagnostic but are not final support."
        ],
        "required_next_proof": (
            "Open same-task review failure evidence plus same-task repair implementation/test proof for the named "
            "task before answering why review repairs were needed."
        ),
        "acceptable_evidence": [
            "same-task independent review artifact describing the failed review finding",
            "same-task implementer fix record, diff, or command/test output addressing that finding",
            "same-task final verified review or post-fix proof that links the repair to the finding",
        ],
    }


def _named_task_repair_causality_reject_reasons(item: dict, *, query: str) -> list[str]:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    summary = str(item.get("summary") or "")
    reasons: list[str] = []
    payload_type = str(metadata.get("payload_type") or "").casefold()
    if payload_type in {"function_call", "function_call_output", "custom_tool_call"}:
        reasons.append(f"current_tool_call_or_output_echo:{payload_type}")
    if _workflow_history_current_command_echo(summary, query=query):
        reasons.append("current_command_invocation_echo")
    echo = setup_echo_classification(summary, query=query)
    if echo.get("is_setup_echo") is True:
        reasons.append(f"assignment_or_review_prompt_echo:{echo.get('reason') or 'setup_echo'}")
    if ref.get("diagnostic_only") is True or ref.get("target_source_role") == "diagnostic_or_post_hoc":
        reasons.append("diagnostic_or_post_hoc_not_final_support")
    if ref.get("final_support_allowed") is False:
        reasons.append("final_support_not_allowed")
    if not reasons:
        reasons.append("no_same_task_work_episode_primary_proof")
    return _unique_preserving_order(reasons)


def _named_task_repair_causality_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "work_episode_status": gate.get("work_episode_status"),
        "required_roles": list(gate.get("required_roles") or []),
        "missing_roles": list(gate.get("missing_roles") or []),
        "rejected_ref_count": gate.get("rejected_ref_count"),
        "required_next_proof": gate.get("required_next_proof"),
        "acceptable_evidence": list(gate.get("acceptable_evidence") or []),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "repair_causality_reject_reasons": list(item.get("repair_causality_reject_reasons") or []),
            }
            for item in list(gate.get("rejected_refs") or [])[:_ASK_ANSWER_MAX_REFS]
        ],
    }


def _named_task_repair_causality_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict) or next_step.get("status") != "missing_same_task_repair_review_proof":
        return None
    return {
        "schema_version": 1,
        "kind": "named_task_repair_causality_same_task_proof_required",
        "summary_lines": list(next_step.get("summary_lines") or [])[:_ASK_ANSWER_MAX_REFS],
        "required_final_proof": next_step.get("required_next_proof"),
        "required_roles": list(next_step.get("required_roles") or []),
        "missing_roles": list(next_step.get("missing_roles") or []),
        "acceptable_evidence": list(next_step.get("acceptable_evidence") or []),
        "why_not_final_proof": (
            "A current command, assignment prompt, task output, or same-loop report can diagnose why the search failed, "
            "but it does not prove the historical same-task review failures or repairs."
        ),
    }


def _action_chronology_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    claim_shape = (
        "only claim" in normalized
        or "actually" in normalized
        or "really" in normalized
        or "versus claim" in normalized
    )
    review_verification_shape = any(
        term in normalized
        for term in ("reviewer", "verify", "verified", "review verdict", "review artifact")
    )
    if review_verification_shape and not _action_chronology_requires_boundary_roles(query):
        return False
    if route_family == "action_proof":
        return claim_shape and (
            "coordinator" in normalized or _action_chronology_requires_boundary_roles(query)
        )
    return claim_shape and _action_chronology_requires_boundary_roles(query)


def _action_chronology_final_role_gate_wanted(*, query: str, route_family: str) -> bool:
    if _action_chronology_answer_wanted(query=query, route_family=route_family):
        return True
    if route_family not in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    review_verification_shape = any(
        term in normalized
        for term in ("reviewer", "verify", "verified", "review verdict", "review artifact")
    )
    if review_verification_shape and not _action_chronology_requires_boundary_roles(query):
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    action_vs_claim = (
        "versus claim" in normalized
        or "only claim" in normalized
        or ("actually" in normalized and any(term in normalized for term in ("worker", "agent", "did ")))
    )
    return action_vs_claim and any(term in normalized for term in ("worker", "agent", "did ", "ran", "run"))


def _action_chronology_candidate_refs(action: dict) -> list[dict]:
    refs: list[dict] = []
    action_plan = action.get("action_proof_plan") if isinstance(action.get("action_proof_plan"), dict) else {}
    core_plan = action.get("core_convo_first_read_plan") if isinstance(action.get("core_convo_first_read_plan"), dict) else {}
    for group in (
        action_plan.get("required_proof_reads"),
        action_plan.get("primary_proof_refs"),
    ):
        for item in group if isinstance(group, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("diagnostic_only") is True:
                continue
            if item.get("target_source_role") == "diagnostic_or_post_hoc":
                continue
            if item.get("read_more_actionable") is False or item.get("followthrough_actionable") is False:
                continue
            refs.append(item)
    for item in core_plan.get("read") if isinstance(core_plan.get("read"), list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("target_source_role") != "target_historical_raw":
            continue
        if item.get("diagnostic_only") is True:
            continue
        if item.get("read_more_actionable") is False or item.get("followthrough_actionable") is False:
            continue
        refs.append(item)
    return _dedupe_ask_answer_proof_refs(refs)


def _action_chronology_answer_proof_gate(*, query: str, action: dict, opened: list[dict]) -> dict:
    query_ids = {match.group(0).casefold() for match in _ASK_EXECUTE_TASK_ID_RE.finditer(query or "")}
    primary: list[dict] = []
    corroborating: list[dict] = []
    rejected: list[dict] = []
    covered_roles: list[str] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        source = _action_chronology_opened_source_role(item)
        public = _ref_public_summary(item)
        public["action_chronology_source_role"] = source["source_role"]
        public["action_chronology_roles"] = source["roles"]
        if source["source_role"] == "primary_action_chronology":
            if not _action_chronology_opened_matches_query(item, query_ids=query_ids):
                public["rejection_reason"] = "primary chronology ref did not match requested task/action handle"
                rejected.append(public)
                continue
            annotated = dict(item)
            annotated["action_chronology_roles"] = source["roles"]
            primary.append(annotated)
            covered_roles.extend(source["roles"])
        elif source["source_role"] == "corroborating_artifact":
            corroborating.append(public)
        else:
            rejected.append(public)

    required_roles = _action_chronology_required_roles(query)
    covered = _unique_preserving_order(covered_roles)
    missing = [role for role in required_roles if role not in covered]
    summary_lines = []
    if primary:
        summary_lines.extend(
            f"{item.get('target')}: primary_action_chronology roles={','.join(item.get('action_chronology_roles') or [])}"
            for item in primary[:_ASK_ANSWER_MAX_REFS]
        )
    if corroborating:
        summary_lines.append(
            "Corroborating artifacts opened but not sufficient as primary chronology proof: "
            + _short_join([str(item.get("target") or "") for item in corroborating[:3]], limit=320)
        )
    if rejected:
        summary_lines.append(
            "Rejected non-matching or non-primary action refs: "
            + _short_join([str(item.get("target") or "") for item in rejected[:3]], limit=320)
        )
    if primary and not missing:
        return {
            "schema_version": 1,
            "status": "answer_allowed",
            "opened": primary[:_ASK_ANSWER_MAX_REFS],
            "covered_roles": covered,
            "required_roles": required_roles,
            "corroborating_refs": corroborating[:_ASK_ANSWER_MAX_REFS],
            "summary_lines": summary_lines,
        }
    return {
        "schema_version": 1,
        "status": "missing_primary_action_chronology",
        "covered_roles": covered,
        "missing_roles": missing or required_roles,
        "required_roles": required_roles,
        "corroborating_refs": corroborating[:_ASK_ANSWER_MAX_REFS],
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "summary_lines": summary_lines
        or [
            "Only task/review/status/commit artifacts or non-matching diagnostics were opened; primary chronological action proof is still required."
        ],
        "required_next_proof": (
            "Open raw chronological evidence for the same action: session messages/tool calls, command output, "
            "git/worktree/process state, and selected/launched-task presence or absence when the prompt asks whether work stopped."
            + (
                " Include explicit stop/exhaustion chronology and remaining-work or exhaustion state proof."
                if "exhaustion_or_remaining_work_state_chronology" in required_roles
                else ""
            )
        ),
        "fallback_action_contract_next_step": _next_proof_step_from_action(action),
    }


def _recover_action_chronology_opened(*, query: str, action: dict, opened: list[dict], cwd: Path) -> dict:
    query_ids = [match.group(0).casefold() for match in _ASK_EXECUTE_TASK_ID_RE.finditer(query or "")]
    if not query_ids:
        return {"schema_version": 1, "status": "not_applicable_no_named_task", "opened": []}
    if not _action_chronology_requires_boundary_roles(query):
        return {"schema_version": 1, "status": "not_applicable_not_boundary_prompt", "opened": []}

    date_hints = _unique_preserving_order(
        _action_chronology_date_hints(opened, action)
        + _action_chronology_named_task_repo_date_hints(cwd=cwd, query_ids=query_ids)
    )
    roots = _action_chronology_recovery_roots(cwd=cwd, date_hints=date_hints)
    searched_files = 0
    matched_files = 0
    recovered: list[dict] = []
    for path in _action_chronology_recovery_files(roots):
        searched_files += 1
        item = _action_chronology_recovered_window_for_file(path=path, query_ids=query_ids, cwd=cwd)
        if not item:
            continue
        matched_files += 1
        recovered.append(item)
    recovered.sort(
        key=lambda item: (
            ((item.get("ref") or {}).get("action_chronology_recovery") or {}).get("score") or 0,
            str(item.get("target") or ""),
        ),
        reverse=True,
    )
    recovered = recovered[:2]
    return {
        "schema_version": 1,
        "status": "primary_chronology_recovered" if recovered else "no_primary_chronology_recovered",
        "opened": recovered,
        "searched_roots": [str(root) for root in roots],
        "searched_file_count": searched_files,
        "matched_file_count": matched_files,
        "query_ids": query_ids,
        "date_hints": date_hints,
    }


def _action_chronology_recovery_public_contract(recovery: dict) -> dict:
    return {
        "schema_version": recovery.get("schema_version"),
        "status": recovery.get("status"),
        "searched_roots": recovery.get("searched_roots") or [],
        "searched_file_count": recovery.get("searched_file_count") or 0,
        "matched_file_count": recovery.get("matched_file_count") or 0,
        "opened_ref_count": len(recovery.get("opened") or []),
        "query_ids": recovery.get("query_ids") or [],
        "date_hints": recovery.get("date_hints") or [],
    }


def _action_chronology_date_hints(opened: list[dict], action: dict) -> list[str]:
    texts: list[str] = []
    for item in opened:
        if isinstance(item, dict):
            texts.extend(str(value) for value in (item.get("summary"), item.get("target")) if value)
            ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
            texts.extend(
                str(value)
                for value in (
                    ref.get("commit_time"),
                    ref.get("timestamp_utc"),
                    ref.get("target"),
                    ref.get("reason"),
                )
                if value
            )
    named = action.get("named_task_action_proof_handoff") if isinstance(action.get("named_task_action_proof_handoff"), dict) else {}
    for ref in named.get("read") if isinstance(named.get("read"), list) else []:
        if isinstance(ref, dict):
            texts.extend(str(value) for value in (ref.get("commit_time"), ref.get("timestamp_utc")) if value)
    hints: list[str] = []
    for text in texts:
        for match in re.finditer(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text):
            date = "-".join(match.groups())
            if date not in hints:
                hints.append(date)
    return hints[:4]


def _action_chronology_named_task_repo_date_hints(*, cwd: Path, query_ids: list[str]) -> list[str]:
    repo = git_root_for(cwd) or cwd
    hints: list[str] = []
    for task_id in query_ids:
        slug = task_id.casefold()
        candidates = list((cwd / "tasks").glob(f"{slug}*.md")) + list(
            (cwd / "codex" / "state" / "reviews").glob(f"{slug}*.md")
        )
        for path in candidates[:4]:
            change = last_change_for_path(path, repo=repo)
            if not change or not change.commit_time:
                continue
            match = re.match(r"(20\d\d-\d\d-\d\d)", change.commit_time)
            if match and match.group(1) not in hints:
                hints.append(match.group(1))
    return hints[:4]


def _action_chronology_recovery_roots(*, cwd: Path, date_hints: list[str]) -> list[Path]:
    home_sessions = Path.home() / ".codex" / "sessions"
    base_roots = [cwd / "sessions", cwd / ".codex" / "sessions", home_sessions]
    roots: list[Path] = []
    for root in base_roots:
        if not root.is_dir():
            continue
        if root == home_sessions:
            if not date_hints:
                continue
            for date in date_hints:
                year, month, day = date.split("-")
                dated = root / year / month / day
                if dated.is_dir() and dated not in roots:
                    roots.append(dated)
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _action_chronology_recovery_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            candidates = sorted(root.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            try:
                if path.stat().st_size > _ACTION_CHRONOLOGY_RECOVERY_MAX_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) >= _ACTION_CHRONOLOGY_RECOVERY_MAX_FILES:
                return files
    return files


def _action_chronology_recovered_window_for_file(*, path: Path, query_ids: list[str], cwd: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    lowered = [line.casefold() for line in lines]
    if not any(any(task_id in line for task_id in query_ids) for line in lowered):
        return None

    boundary_index = _action_chronology_boundary_index(lowered, query_ids)
    if boundary_index is None:
        return None
    start = max(0, boundary_index - _ACTION_CHRONOLOGY_RECOVERY_WINDOW_LINES + 1)
    window_lines = lines[start : boundary_index + 1]
    window_text = "\n".join(window_lines).casefold()
    if not _action_chronology_window_has_primary_events(window_text):
        return None
    post_boundary_text = "\n".join(lines[boundary_index + 1 :]).casefold()
    followup_markers = [marker for marker in _ACTION_CHRONOLOGY_FOLLOWUP_MARKERS if marker in post_boundary_text]
    if followup_markers:
        return None

    rel = _safe_relative_path(cwd, path)
    summary = _action_chronology_recovered_window_summary(
        rel=rel,
        start_line=start + 1,
        boundary_line=boundary_index + 1,
        line_count=len(lines),
        window_lines=window_lines,
        no_followup_markers=not followup_markers,
        query_ids=query_ids,
    )
    score = _action_chronology_recovered_window_score(window_text)
    return {
        "status": "opened",
        "target": f"{rel}:{start + 1}-{boundary_index + 1}",
        "path": rel,
        "line": boundary_index + 1,
        "summary": summary[:_ASK_ANSWER_MAX_FILE_CHARS],
        "record_metadata": {
            "conversation_label": "action_chronology_recovered_window",
            "conversation_confidence": "high",
            "top_role": "assistant",
        },
        "ref": {
            "kind": "jsonl_source_ref",
            "target": f"{rel}:{start + 1}-{boundary_index + 1}",
            "source_ref": {"path": str(path), "line": start + 1},
            "openable_target": f"{path}:{start + 1}",
            "proof_surface": "raw_jsonl_action_chronology_window",
            "target_source_role": "target_historical_raw",
            "proof_role": "command_or_tool_call",
            "reason": (
                "bounded action chronology recovery for named stop-boundary prompt: "
                + ", ".join(query_ids)
            ),
            "action_chronology_recovery": {
                "boundary_line": boundary_index + 1,
                "line_count": len(lines),
                "post_boundary_followup_markers_found": followup_markers,
                "score": score,
            },
        },
    }


def _action_chronology_boundary_index(lines: list[str], query_ids: list[str]) -> int | None:
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if not any(task_id in line for task_id in query_ids):
            continue
        if any(term in line for term in ("task_complete", "final answer", "implemented and committed", "closeout")):
            return index
    return None


def _action_chronology_window_has_primary_events(text: str) -> bool:
    return (
        ("function_call" in text or "tool call" in text)
        and ("function_call_output" in text or "tool output" in text)
        and any(term in text for term in ("git status", "git show", "git commit", "git add", "commit:"))
        and any(term in text for term in ("task_complete", "final answer", "implemented and committed", "closeout"))
    )


def _action_chronology_recovered_window_score(text: str) -> int:
    score = 0
    if "implemented and committed" in text:
        score += 8
    if "on the task branch" in text:
        score += 4
    if "git add" in text or "git commit" in text:
        score += 3
    if "git status" in text:
        score += 2
    if "task_complete" in text:
        score += 2
    if "review artifact committed" in text or "verdict: `verified`" in text or "verdict: verified" in text:
        score -= 4
    return score


def _action_chronology_recovered_window_summary(
    *,
    rel: str,
    start_line: int,
    boundary_line: int,
    line_count: int,
    window_lines: list[str],
    no_followup_markers: bool,
    query_ids: list[str],
) -> str:
    line_summaries: list[str] = []
    for offset, raw in enumerate(window_lines, start=start_line):
        summary = _summarize_jsonl_or_text_line(raw, fallback=f"{rel}:{offset}")
        lower = summary.casefold()
        if any(
            marker in lower
            for marker in (
                "tool call",
                "tool output",
                "git status",
                "git show",
                "git add",
                "commit",
                "task_complete",
                "final answer",
                "implemented and committed",
            )
        ):
            line_summaries.append(f"line {offset}: {summary}")
        if len(line_summaries) >= 6:
            break
    absence = (
        f"no next task selected or launched after boundary: source file ends at line {line_count}; "
        "post-boundary marker scan found no next task, selected, launched, thread_spawn, or task_started markers"
        if no_followup_markers and boundary_line == line_count
        else "post-boundary marker scan found no next task, selected, launched, thread_spawn, or task_started markers"
    )
    return (
        f"Recovered primary action chronology window for {', '.join(query_ids)} "
        f"{rel}:{start_line}-{boundary_line}: "
        "tool call/tool output chronology; final answer and task_complete closeout chronology; "
        f"git status/worktree/commit repo process chronology; {absence}. "
        + " | ".join(line_summaries)
        + f" | boundary line {boundary_line}."
    )


def _action_chronology_required_roles(query: str) -> list[str]:
    normalized = str(query or "").casefold()
    roles = ["observed_message_or_command_chronology"]
    if _action_chronology_requires_boundary_roles(query):
        roles.extend(
            [
                "stop_or_closeout_chronology",
                "followup_selection_or_absence_chronology",
                "repo_or_process_state_chronology",
            ]
        )
        if any(term in normalized for term in ("exhaust", "exhausted", "exhaustion", "remaining work")):
            roles.append("exhaustion_or_remaining_work_state_chronology")
    elif _action_chronology_requires_repo_or_process_roles(normalized):
        roles.append("repo_or_process_state_chronology")
    return _unique_preserving_order(roles)


def _action_chronology_requires_boundary_roles(query: str) -> bool:
    normalized = str(query or "").casefold()
    if re.search(r"\b(stop|stopped|stopping|closeout|closed out)\b", normalized):
        return True
    if re.search(r"\b(exhaust|exhausted|exhaustion|remaining work)\b", normalized):
        return True
    if re.search(r"\b(selected|launched)\b", normalized) and any(
        term in normalized for term in ("next", "task", "after", "follow-up", "follow up")
    ):
        return True
    return any(
        term in normalized
        for term in (
            "stop after",
            "stopped after",
            "stop boundary",
            "after boundary",
            "after jsh-",
            "after task",
            "no next task",
            "next task",
            "selected next",
            "selected/launched",
            "selected or launched",
            "launched next",
            "follow-up",
            "follow up",
            "closeout",
            "closed out",
        )
    )


def _action_chronology_requires_repo_or_process_roles(normalized_query: str) -> bool:
    return any(
        term in normalized_query
        for term in (
            "git status",
            "git worktree",
            "repo state",
            "repository state",
            "worktree state",
            "process state",
            "running process",
            "background process",
            "dirty",
            "clean",
            "committed",
            "commit",
        )
    )


def _action_chronology_opened_source_role(item: dict) -> dict:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    surface = str(ref.get("proof_surface") or "")
    target_role = str(ref.get("target_source_role") or "")
    proof_role = str(ref.get("proof_role") or "")
    kind = str(ref.get("kind") or "")
    summary = str(item.get("summary") or "")
    target = str(item.get("target") or "")
    source_text = " ".join([summary, target, surface, target_role, proof_role, kind]).casefold()
    if surface in {
        "task_file",
        "review_artifact",
        "path_last_change_commit",
        "merge_commit",
        "post_merge_task_proof",
        "post_merge_review_proof",
        "task_selection_rationale",
        "pipeline_selection_result",
    } or target_role in {"target_historical_artifact", "legitimate_context"}:
        return {
            "source_role": "corroborating_artifact",
            "roles": _action_chronology_text_roles(source_text),
        }
    primary_markers = {
        "target_historical_raw",
        "command_or_tool_call",
        "command_or_tool_output",
        "changed_check_or_commit_evidence",
    }
    if (
        target_role in primary_markers
        or proof_role in primary_markers
        or kind == "jsonl_source_ref"
        or str(item.get("path") or "").endswith(".jsonl")
    ):
        return {
            "source_role": "primary_action_chronology",
            "roles": _action_chronology_text_roles(source_text),
        }
    return {
        "source_role": "diagnostic_or_unknown",
        "roles": _action_chronology_text_roles(source_text),
    }


def _action_chronology_text_roles(text: str) -> list[str]:
    roles: list[str] = []
    if any(
        term in text
        for term in (
            "tool call",
            "tool output",
            "function_call",
            "agent_message",
            "user_message",
            " assistant:",
            " user:",
            "system:",
        )
    ):
        roles.append("observed_message_or_command_chronology")
    if any(term in text for term in ("stop", "stopped", "closeout", "closed", "task_complete", "final answer")):
        roles.append("stop_or_closeout_chronology")
    if any(term in text for term in ("no next", "next task", "selected", "launched", "thread_spawn", "task_started")):
        roles.append("followup_selection_or_absence_chronology")
    if any(term in text for term in ("git status", "git worktree", "process", "worktree", "dirty", "clean", "commit")):
        roles.append("repo_or_process_state_chronology")
    if any(term in text for term in ("exhaust", "exhausted", "exhaustion", "remaining work", "nothing left")):
        roles.append("exhaustion_or_remaining_work_state_chronology")
    return _unique_preserving_order(roles)


def _action_chronology_opened_matches_query(item: dict, *, query_ids: set[str]) -> bool:
    if not query_ids:
        return True
    text = " ".join(
        str(value)
        for value in (
            item.get("target"),
            item.get("summary"),
            (item.get("ref") or {}).get("target") if isinstance(item.get("ref"), dict) else "",
            (item.get("ref") or {}).get("reason") if isinstance(item.get("ref"), dict) else "",
        )
        if value
    ).casefold()
    return bool(query_ids.intersection(text.split()) or any(task_id in text for task_id in query_ids))


def _action_chronology_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "covered_roles": gate.get("covered_roles") or [],
        "missing_roles": gate.get("missing_roles") or [],
        "required_roles": gate.get("required_roles") or [],
        "corroborating_ref_count": len(gate.get("corroborating_refs") or []),
        "rejected_ref_count": len(gate.get("rejected_refs") or []),
    }


def _expectation_mismatch_answer_proof_gate(*, action: dict, opened: list[dict], query: str = "") -> dict:
    plan = action.get("expectation_mismatch_plan") if isinstance(action.get("expectation_mismatch_plan"), dict) else {}
    clean_by_facet: dict[str, list[dict]] = {
        facet: [] for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
    }
    rejected: list[dict] = []
    rejected_lines: list[str] = []

    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        target_reasons = _target_episode_ref_reject_reasons(
            item,
            required_ids=_explicit_query_episode_ids(query),
        )
        roles = _expectation_mismatch_opened_roles(item, plan=plan)
        source = _expectation_mismatch_opened_source_role(item)
        clean_allowed = source["clean_final_support"] and not target_reasons
        applicable_roles = [
            role for role in roles if role in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
        ]
        if clean_allowed and applicable_roles:
            annotated = dict(item)
            annotated["expectation_mismatch_answer_roles"] = applicable_roles
            for role in applicable_roles:
                clean_by_facet[role].append(annotated)
            continue
        if applicable_roles or not clean_allowed:
            public = _ref_public_summary(item)
            public["expectation_mismatch_roles"] = applicable_roles
            public["rejected_source_role"] = source["source_role"]
            public["rejection_reasons"] = target_reasons + source["reasons"]
            rejected.append(public)
            rejected_lines.append(
                f"{item.get('target')}: rejected_source_role={source['source_role']}; "
                f"roles={','.join(applicable_roles) if applicable_roles else '(none)'}"
            )

    covered = [
        facet
        for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
        if clean_by_facet.get(facet)
    ]
    missing = [
        facet
        for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
        if facet not in covered
    ]
    if not missing:
        selected: list[dict] = []
        for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS:
            for item in clean_by_facet[facet]:
                if not any(_opened_ref_same_target(item, existing) for existing in selected):
                    selected.append(item)
                    break
        for item in opened:
            if len(selected) >= _ASK_ANSWER_MAX_REFS:
                break
            source = _expectation_mismatch_opened_source_role(item)
            if source["clean_final_support"] and not any(_opened_ref_same_target(item, existing) for existing in selected):
                selected.append(item)
        return {
            "schema_version": 1,
            "status": "answer_allowed",
            "required_facets": list(_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS),
            "covered_facets": covered,
            "missing_facets": [],
            "opened": selected[:_ASK_ANSWER_MAX_REFS],
            "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
            "rejected_source_roles": _unique_preserving_order(
                str(item.get("rejected_source_role") or "") for item in rejected
            ),
            "plan_status": plan.get("status"),
            "plan_counts": plan.get("counts"),
        }

    return {
        "schema_version": 1,
        "status": "missing_required_facets" if covered else "no_clean_required_facet_refs",
        "required_facets": list(_ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS),
        "covered_facets": covered,
        "missing_facets": missing,
        "rejected_refs": rejected[:_ASK_ANSWER_MAX_REFS],
        "rejected_ref_count": len(rejected),
        "rejected_source_roles": _unique_preserving_order(
            str(item.get("rejected_source_role") or "") for item in rejected
        ),
        "rejected_summary_lines": rejected_lines[:_ASK_ANSWER_MAX_REFS],
        "required_next_proof": (
            "Open clean actionable expectation-mismatch proof for expected_behavior_evidence, "
            "observed_behavior_or_action_proof, and divergence_evidence. Treat tool-output, docs/tasks, "
            "diagnostics, and post-hoc artifacts as leads only until a facet remains explicitly missing."
        ),
        "suggested_action": (
            "Use the existing expectation_mismatch_plan role buckets, but inspect raw user/assistant/action "
            "conversation rows or other primary behavior proof for each missing facet before composing a final answer."
        ),
        "plan_status": plan.get("status"),
        "plan_counts": plan.get("counts"),
    }


def _process_actual_use_answer_proof_gate(*, action: dict, opened: list[dict], query: str = "") -> dict:
    plan = (
        action.get("process_actual_use_final_support_read_plan")
        if isinstance(action.get("process_actual_use_final_support_read_plan"), dict)
        else {}
    )
    bundle = (
        plan.get("process_actual_use_bucket_evidence_bundle")
        if isinstance(plan.get("process_actual_use_bucket_evidence_bundle"), dict)
        else {}
    )
    summary = bundle.get("summary") if isinstance(bundle.get("summary"), dict) else {}
    required_targets = {
        str(item.get("target") or "")
        for item in plan.get("required_primary_raw_final_support_reads") or []
        if isinstance(item, dict) and str(item.get("target") or "").strip()
    }
    allowed: list[dict] = []
    rejected: list[dict] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        selection = (
            ref.get("process_actual_use_selection")
            if isinstance(ref.get("process_actual_use_selection"), dict)
            else {}
        )
        metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
        payload_type = str(metadata.get("payload_type") or "").casefold()
        target = str(ref.get("target") or item.get("target") or "")
        source_tier = str(ref.get("actual_use_source_tier") or selection.get("source_tier") or "")
        chronology_role = str(
            ref.get("source_chronology_role")
            or selection.get("source_chronology_role")
            or selection.get("anti_contamination_role")
            or ""
        )
        selected_buckets = [
            str(bucket) for bucket in selection.get("selected_buckets") or ref.get("selected_buckets") or []
        ]
        final_support_allowed = bool(ref.get("final_support_allowed") or selection.get("final_support_allowed"))
        reject_reasons: list[str] = _target_episode_ref_reject_reasons(
            item,
            required_ids=_explicit_query_episode_ids(query),
        )
        if required_targets and target not in required_targets and str(item.get("target") or "") not in required_targets:
            reject_reasons.append("not_in_required_primary_raw_final_support_reads")
        if source_tier != "primary_raw_timing_evidence":
            reject_reasons.append("not_primary_raw_timing_evidence")
        if chronology_role and chronology_role != "primary_raw_evidence":
            reject_reasons.append(f"not_primary_raw_chronology:{chronology_role}")
        if not final_support_allowed:
            reject_reasons.append("final_support_not_allowed")
        if payload_type in {"function_call_output", "custom_tool_call", "function_call"}:
            reject_reasons.append(f"tool_output_or_call_row:{payload_type}")
        if not set(selected_buckets).intersection(
            {"consulted_before_planning", "changed_behavior_scope_or_claim_boundaries"}
        ):
            reject_reasons.append("missing_behavior_change_or_before_planning_bucket")
        if reject_reasons:
            row = dict(item)
            row["process_actual_use_reject_reasons"] = reject_reasons
            rejected.append(row)
            continue
        row = dict(item)
        row["process_actual_use_selected_buckets"] = selected_buckets
        allowed.append(row)

    covered_buckets = sorted(
        {
            bucket
            for item in allowed
            for bucket in item.get("process_actual_use_selected_buckets") or []
            if str(bucket).strip()
        }
    )
    return {
        "schema_version": 1,
        "status": "answer_allowed" if allowed else "blocked",
        "opened": allowed[:_ASK_ANSWER_MAX_REFS],
        "rejected": rejected[:_ASK_ANSWER_MAX_REFS],
        "covered_buckets": covered_buckets,
        "missing_or_excluded_buckets": list(summary.get("missing_or_excluded_buckets") or []),
        "secondary_only_buckets": list(summary.get("secondary_only_buckets") or []),
        "required_primary_raw_final_support_read_count": len(required_targets),
        "allowed_ref_count": len(allowed),
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            _process_actual_use_rejected_summary_line(item)
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ],
        "required_next_proof": (
            "primary raw timing or reviewer-enforcement evidence for consulted-before-planning "
            "and behavior/scope/claim-boundary buckets"
        ),
    }


def _workflow_history_final_proof_gate(*, query: str, opened: list[dict]) -> dict:
    allowed: list[dict] = []
    rejected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        reasons = _workflow_history_final_proof_reject_reasons(item, query=query)
        if reasons:
            row = dict(item)
            row["workflow_history_reject_reasons"] = reasons
            rejected.append(row)
            continue
        allowed.append(item)
    status = "answer_allowed" if allowed else "blocked_current_run_echo_only"
    return {
        "schema_version": 1,
        "status": status,
        "opened": allowed[:_ASK_ANSWER_MAX_REFS],
        "rejected": rejected[:_ASK_ANSWER_MAX_REFS],
        "allowed_ref_count": len(allowed),
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            _workflow_history_rejected_summary_line(item)
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ],
        "required_next_proof": (
            "clean historical workflow evidence after recovery reads are opened; do not use current-run "
            "assignment prompts, current command/tool-call rows, or action-proof-looking self material as "
            "final support for broad workflow-history synthesis"
        ),
    }


def _workflow_history_final_proof_reject_reasons(item: dict, *, query: str) -> list[str]:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    summary = str(item.get("summary") or "")
    folded = summary.casefold()
    reasons: list[str] = []
    payload_type = str(metadata.get("payload_type") or "").casefold()
    if payload_type in {"function_call", "function_call_output", "custom_tool_call"}:
        reasons.append(f"current_tool_call_or_output_echo:{payload_type}")
    if str(ref.get("kind") or "") == "tool_event_source_ref":
        reasons.append("action_proof_tool_event_self_material")
    echo = setup_echo_classification(summary, query=query)
    if echo.get("is_setup_echo") is True:
        reasons.append(f"assignment_or_review_prompt_echo:{echo.get('reason') or 'setup_echo'}")
    if _workflow_history_current_command_echo(summary, query=query):
        reasons.append("current_command_invocation_echo")
    if ref.get("diagnostic_only") is True or ref.get("target_source_role") == "diagnostic_or_post_hoc":
        reasons.append("diagnostic_or_post_hoc_not_final_support")
    if ref.get("final_support_allowed") is False:
        reasons.append("final_support_not_allowed")
    if any(marker in folded for marker in ("current-run diagnostic", "same-loop recap", "current ask --execute")):
        reasons.append("current_run_self_echo")
    return _unique_preserving_order(reasons)


def _workflow_history_current_command_echo(summary: str, *, query: str) -> bool:
    folded = str(summary or "").casefold()
    normalized_query = " ".join(str(query or "").casefold().split())
    command_marker = any(
        marker in folded
        for marker in (
            "ask --answer",
            "jsonl_history_search.cli ask",
            "jsonl-history-search ask",
            "python3 -m jsonl_history_search.cli",
            "pythonpath=src",
        )
    )
    query_echo = bool(normalized_query and normalized_query in " ".join(folded.split()))
    return command_marker and query_echo


def _workflow_history_rejected_summary_line(item: dict) -> str:
    target = str(item.get("target") or "").strip()
    reasons = ", ".join(str(reason) for reason in item.get("workflow_history_reject_reasons") or [])
    prefix = f"{target}: " if target else ""
    return f"{prefix}rejected_broad_workflow_history_final_support={reasons}"[:_ASK_ANSWER_MAX_LINE_CHARS]


CURRENT_RUN_FINAL_SUPPORT_ROUTE_FAMILIES = frozenset(
    {
        "latest_status",
        "semantic_debug",
        "expectation_mismatch",
        "remembered_user_wording",
        "remembered_topic",
        "semantic_wrapup",
        "action_proof",
        "provenance",
        "orchestration_provenance",
    }
)


def _current_run_final_support_gate(*, query: str, route_family: str, opened: list[dict]) -> dict:
    if route_family not in CURRENT_RUN_FINAL_SUPPORT_ROUTE_FAMILIES:
        return {"schema_version": 1, "applies": False, "status": "not_applicable"}
    allowed: list[dict] = []
    rejected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        reasons = _current_run_final_support_reject_reasons(item, query=query)
        if reasons:
            row = dict(item)
            row["current_run_final_support_reject_reasons"] = reasons
            rejected.append(row)
            continue
        allowed.append(item)
    if allowed:
        status = "answer_allowed"
    elif rejected:
        status = "blocked_current_run_control_only"
    else:
        status = "blocked_no_opened_final_support"
    return {
        "schema_version": 1,
        "applies": True,
        "route_family": route_family,
        "status": status,
        "opened": allowed[:_ASK_ANSWER_MAX_REFS],
        "rejected": rejected[:_ASK_ANSWER_MAX_REFS],
        "allowed_ref_count": len(allowed),
        "rejected_ref_count": len(rejected),
        "summary_lines": [
            _current_run_rejected_summary_line(item)
            for item in rejected[:_ASK_ANSWER_MAX_REFS]
        ],
        "required_next_proof": (
            "primary historical/user/task/review/commit evidence for the requested episode; "
            "current-run assignment, control, probe, generated-report, and tool-output refs are diagnostics only"
        ),
    }


def _current_run_final_support_reject_reasons(item: dict, *, query: str) -> list[str]:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    if _completed_task_artifact_ref(ref):
        return []
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    summary = str(item.get("summary") or "")
    text = _current_run_final_support_text(item)
    folded = re.sub(r"\s+", " ", text.casefold().replace("_", " ")).strip()
    surface_text = _current_run_final_support_hard_surface_text(item)
    surface_folded = re.sub(r"\s+", " ", surface_text.casefold().replace("_", " ")).strip()
    reasons: list[str] = []
    payload_type = str(metadata.get("payload_type") or "").casefold()
    has_current_markers = _current_run_ref_markers_present(folded)
    has_hard_current_markers = _current_run_ref_hard_markers_present(surface_folded)
    has_query_or_command_echo = _current_run_query_or_command_echo(folded, query=query)
    historical_raw_correction_root_proof = bool(
        _semantic_wrapup_correction_root_query(query)
        and _semantic_wrapup_correction_root_primary_ref_reasons(item)
        and not has_hard_current_markers
    )
    if (
        payload_type in {"function_call", "function_call_output", "custom_tool_call"}
        and (has_current_markers or has_query_or_command_echo)
    ):
        reasons.append(f"current_tool_call_or_output_echo:{payload_type}")
    echo = setup_echo_classification(summary, query=query)
    if echo.get("is_setup_echo") is True and not historical_raw_correction_root_proof:
        reasons.append(f"assignment_or_control_prompt_echo:{echo.get('reason') or 'setup_echo'}")
    if has_current_markers and not historical_raw_correction_root_proof:
        reasons.append("current_run_control_or_generated_artifact")
    if has_query_or_command_echo:
        reasons.append("current_query_or_command_echo")
    if ref.get("diagnostic_only") is True or ref.get("target_source_role") == "diagnostic_or_post_hoc":
        reasons.append("diagnostic_or_post_hoc_not_final_support")
    if ref.get("final_support_allowed") is False:
        reasons.append("final_support_not_allowed")
    return _unique_preserving_order(reasons)


def _completed_task_artifact_ref(ref: dict) -> bool:
    kind = str(ref.get("kind") or "")
    target_role = str(ref.get("target_source_role") or "")
    return kind.startswith("completed_named_task_") or target_role == "completed_task_artifact"


def _current_run_final_support_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            item.get("target"),
            item.get("path"),
            item.get("summary"),
            item.get("reason"),
            ref.get("kind"),
            ref.get("target"),
            ref.get("path"),
            ref.get("openable_target"),
            ref.get("reason"),
            ref.get("target_source_role"),
            ref.get("source_tier"),
            ref.get("read_role"),
            ref.get("proof_surface"),
            source_ref.get("path"),
            metadata.get("payload_type"),
            metadata.get("top_role"),
            metadata.get("message_role"),
        )
    )


def _current_run_final_support_hard_surface_text(item: dict) -> str:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    return " ".join(
        str(value or "")
        for value in (
            item.get("target"),
            item.get("path"),
            item.get("reason"),
            ref.get("kind"),
            ref.get("target"),
            ref.get("path"),
            ref.get("openable_target"),
            ref.get("reason"),
            ref.get("target_source_role"),
            ref.get("source_tier"),
            ref.get("read_role"),
            ref.get("proof_surface"),
            source_ref.get("path"),
            metadata.get("top_type"),
            metadata.get("payload_type"),
            metadata.get("payload_role"),
            metadata.get("conversation_label"),
        )
    )


def _current_run_ref_markers_present(folded: str) -> bool:
    markers = (
        "/sessions/current/",
        "/current/",
        "/current run/",
        "/current-run/",
        "/current task/",
        "/current-task/",
        "/same-loop/",
        "/same loop/",
        "/same-run/",
        "/same run/",
        "/same-lane/",
        "/same lane/",
        "/slice-a/",
        "/slice-b/",
        "/slice-c/",
        "/slice-d/",
        "current-run",
        "current run",
        "same-loop",
        "same loop",
        "same-run",
        "same run",
        "same-lane",
        "same lane",
        "control prompt",
        "current prompt",
        "current assignment",
        "assignment prompt",
        "read-only slice",
        "pressure agent",
        "run live cli",
        "run live current-master",
        "save artifacts",
        "generated report",
        "current generated report",
        "tool output window",
        "semantic-debug output",
        "semantic debug output",
        "source locator handoff",
        "answer ready handoff",
        "original token count",
        "process exited with code",
    )
    return any(marker in folded for marker in markers)


def _current_run_ref_hard_markers_present(folded: str) -> bool:
    markers = (
        "/sessions/current/",
        "/current/",
        "/current run/",
        "/current-run/",
        "/current task/",
        "/current-task/",
        "/same-loop/",
        "/same loop/",
        "/same-run/",
        "/same run/",
        "/same-lane/",
        "/same lane/",
        "/slice-a/",
        "/slice-b/",
        "/slice-c/",
        "/slice-d/",
        "current-run",
        "current run",
        "same-loop",
        "same loop",
        "same-run",
        "same run",
        "same-lane",
        "same lane",
        "read-only slice",
        "pressure agent",
        "run live cli",
        "run live current-master",
        "save artifacts",
        "generated report",
        "current generated report",
        "tool output window",
        "semantic-debug output",
        "semantic debug output",
        "source locator handoff",
        "answer ready handoff",
        "original token count",
        "process exited with code",
    )
    return any(marker in folded for marker in markers)


def _current_run_query_or_command_echo(folded: str, *, query: str) -> bool:
    normalized_query = re.sub(r"\s+", " ", str(query or "").casefold().replace("_", " ")).strip()
    query_echo = bool(normalized_query and normalized_query in folded)
    command_marker = any(
        marker in folded
        for marker in (
            "jsonl-history-search",
            "jsonl history search",
            "python3 -m jsonl_history_search",
            "ask --answer",
            "ask --execute",
            "semantic-debug",
            "semantic wrapup",
            "semantic-wrapup",
            "what-happened-since",
        )
    )
    return query_echo and (command_marker or "prompt:" in folded or "selected prompt" in folded)


def _current_run_rejected_summary_line(item: dict) -> str:
    target = str(item.get("target") or "").strip()
    reasons = ", ".join(str(reason) for reason in item.get("current_run_final_support_reject_reasons") or [])
    prefix = f"{target}: " if target else ""
    return f"{prefix}rejected_current_run_final_support={reasons}"[:_ASK_ANSWER_MAX_LINE_CHARS]


def _current_run_final_support_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "route_family": gate.get("route_family"),
        "allowed_ref_count": gate.get("allowed_ref_count", 0),
        "rejected_ref_count": gate.get("rejected_ref_count", 0),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "reject_reasons": list(item.get("current_run_final_support_reject_reasons") or []),
            }
            for item in gate.get("rejected", [])[:_ASK_ANSWER_MAX_REFS]
        ],
        "required_next_proof": gate.get("required_next_proof"),
    }


def _workflow_history_final_proof_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "allowed_ref_count": gate.get("allowed_ref_count"),
        "rejected_ref_count": gate.get("rejected_ref_count"),
        "required_next_proof": gate.get("required_next_proof"),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "workflow_history_reject_reasons": list(item.get("workflow_history_reject_reasons") or []),
            }
            for item in list(gate.get("rejected") or [])[:_ASK_ANSWER_MAX_REFS]
        ],
    }


def _process_actual_use_rejected_summary_line(item: dict) -> str:
    target = str(item.get("target") or "").strip()
    reasons = ", ".join(str(reason) for reason in item.get("process_actual_use_reject_reasons") or [])
    summary = str(item.get("summary") or "").strip()
    prefix = f"{target}: " if target else ""
    suffix = f" ({reasons})" if reasons else ""
    return f"{prefix}{summary}{suffix}"[:_ASK_ANSWER_MAX_LINE_CHARS]


def _process_actual_use_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "covered_buckets": list(gate.get("covered_buckets") or []),
        "missing_or_excluded_buckets": list(gate.get("missing_or_excluded_buckets") or []),
        "secondary_only_buckets": list(gate.get("secondary_only_buckets") or []),
        "required_primary_raw_final_support_read_count": gate.get("required_primary_raw_final_support_read_count"),
        "allowed_ref_count": gate.get("allowed_ref_count"),
        "rejected_ref_count": gate.get("rejected_ref_count"),
        "required_next_proof": gate.get("required_next_proof"),
        "rejected_refs": [
            {
                **_ref_public_summary(item),
                "process_actual_use_reject_reasons": list(item.get("process_actual_use_reject_reasons") or []),
                "actual_use_source_tier": (
                    (item.get("ref") or {}).get("actual_use_source_tier")
                    if isinstance(item.get("ref"), dict)
                    else None
                ),
            }
            for item in list(gate.get("rejected") or [])[:_ASK_ANSWER_MAX_REFS]
        ],
    }


def _expectation_mismatch_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "status": gate.get("status"),
        "required_facets": gate.get("required_facets"),
        "covered_facets": gate.get("covered_facets"),
        "missing_facets": gate.get("missing_facets"),
        "rejected_ref_count": gate.get("rejected_ref_count", len(gate.get("rejected_refs") or [])),
        "rejected_source_roles": gate.get("rejected_source_roles"),
        "plan_status": gate.get("plan_status"),
        "plan_counts": gate.get("plan_counts"),
    }


def _expectation_mismatch_opened_roles(item: dict, *, plan: dict) -> list[str]:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    roles: list[str] = []
    role = str(ref.get("expectation_mismatch_role") or "")
    if role:
        roles.append(role)
    for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS:
        for planned in plan.get(facet) or []:
            if isinstance(planned, dict) and _expectation_mismatch_refs_same_source(ref, planned):
                roles.append(facet)
                break
    return [
        role
        for role in _unique_preserving_order(roles)
        if role in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
    ]


def _expectation_mismatch_refs_same_source(left: dict, right: dict) -> bool:
    left_source = left.get("source_ref") if isinstance(left.get("source_ref"), dict) else {}
    right_source = right.get("source_ref") if isinstance(right.get("source_ref"), dict) else {}
    left_target = str(left.get("target") or left.get("openable_target") or "")
    right_target = str(right.get("target") or right.get("openable_target") or "")
    left_line = _positive_int(left_source.get("line")) or _line_from_target(left_target)
    right_line = _positive_int(right_source.get("line")) or _line_from_target(right_target)
    if left_line and right_line and left_line != right_line:
        return False
    left_paths = _expectation_mismatch_ref_paths(left)
    right_paths = _expectation_mismatch_ref_paths(right)
    for left_path in left_paths:
        for right_path in right_paths:
            if _path_strings_same_or_suffix(left_path, right_path):
                return True
    return bool(left_target and right_target and _path_strings_same_or_suffix(left_target, right_target))


def _expectation_mismatch_ref_paths(ref: dict) -> list[str]:
    paths: list[str] = []
    source = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    openable = ref.get("openable_source_ref") if isinstance(ref.get("openable_source_ref"), dict) else {}
    for value in (
        source.get("path"),
        openable.get("path"),
        ref.get("path"),
        ref.get("openable_target"),
        ref.get("target"),
    ):
        if not value:
            continue
        target, _line = _split_ref_target(str(value))
        paths.append(target)
    return _unique_preserving_order(paths)


def _path_strings_same_or_suffix(left: str, right: str) -> bool:
    left_norm = str(left or "").replace("\\", "/").casefold().strip()
    right_norm = str(right or "").replace("\\", "/").casefold().strip()
    if not left_norm or not right_norm:
        return False
    return (
        left_norm == right_norm
        or left_norm.endswith(f"/{right_norm}")
        or right_norm.endswith(f"/{left_norm}")
    )


def _line_from_target(target: str) -> int | None:
    if not target:
        return None
    _path, line = _split_ref_target(target)
    return line


def _expectation_mismatch_opened_source_role(item: dict) -> dict:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    summary = str(item.get("summary") or "")
    reasons: list[str] = []
    if ref.get("diagnostic_only") is True or ref.get("read_more_actionable") is False:
        reasons.append("diagnostic_or_non_actionable")
    if ref.get("followthrough_actionable") is False:
        reasons.append("not_followthrough_actionable")
    if str(ref.get("target_source_role") or "") == "diagnostic_or_post_hoc":
        reasons.append("target_source_role_diagnostic_or_post_hoc")
    if ref.get("final_support_allowed") is False:
        reasons.append("final_support_not_allowed")
    payload_type = str(metadata.get("payload_type") or "")
    if payload_type == "function_call_output" or summary.casefold().startswith("tool output:") or ": tool output:" in summary.casefold():
        reasons.append("tool_output")
    path_role = _expectation_mismatch_path_source_role(
        str(item.get("target") or ""),
        str(item.get("path") or ""),
        ref,
    )
    if path_role:
        reasons.append(path_role)
    if reasons:
        source_role = _expectation_mismatch_rejection_source_role(reasons)
        return {"clean_final_support": False, "source_role": source_role, "reasons": _unique_preserving_order(reasons)}
    return {"clean_final_support": True, "source_role": "clean_actionable_primary", "reasons": []}


def _expectation_mismatch_path_source_role(target: str, path: str, ref: dict) -> str | None:
    values = [target, path]
    source = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    values.extend(
        str(value or "")
        for value in (
            source.get("path"),
            ref.get("path"),
            ref.get("target"),
            ref.get("openable_target"),
        )
    )
    for value in values:
        normalized = str(value or "").replace("\\", "/").casefold()
        if not normalized:
            continue
        if "/tasks/" in normalized or normalized.startswith("tasks/"):
            return "task_artifact"
        if "/docs/" in normalized or normalized.startswith("docs/"):
            return "docs_artifact"
    return None


def _expectation_mismatch_rejection_source_role(reasons: list[str]) -> str:
    priority = (
        "tool_output",
        "task_artifact",
        "docs_artifact",
        "target_source_role_diagnostic_or_post_hoc",
        "diagnostic_or_non_actionable",
        "not_followthrough_actionable",
        "final_support_not_allowed",
    )
    for reason in priority:
        if reason in reasons:
            return reason
    return reasons[0] if reasons else "unknown_rejected_source"


def _opened_ref_same_target(left: dict, right: dict) -> bool:
    return str(left.get("target") or "") == str(right.get("target") or "") or _expectation_mismatch_refs_same_source(
        left.get("ref") if isinstance(left.get("ref"), dict) else {},
        right.get("ref") if isinstance(right.get("ref"), dict) else {},
    )


def _ask_answer_jsonl_record_metadata(raw: str) -> dict | None:
    try:
        row = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    item = row.get("item") if isinstance(row.get("item"), dict) else {}
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    tool_call_correlation_id = None
    for key in ("call_id", "callId", "item_id", "itemId", "id"):
        for container in (payload, item, message, row):
            value = str(container.get(key) or "").strip()
            if value:
                tool_call_correlation_id = value
                break
        if tool_call_correlation_id:
            break
    classification = classify_conversation_record(row)
    simple_top_level_user_text = (
        classification.label == "unknown"
        and str(row.get("role") or "").strip().lower() == "user"
        and bool(str(row.get("text") or row.get("content") or row.get("message") or "").strip())
        and not isinstance(row.get("payload"), dict)
        and not isinstance(row.get("message"), dict)
    )
    is_user_authored = classification.is_human_authored or simple_top_level_user_text
    return {
        "top_type": row.get("type"),
        "top_role": row.get("role"),
        "payload_type": payload.get("type"),
        "payload_role": payload.get("role"),
        "item_type": item.get("type"),
        "message_role": message.get("role"),
        "tool_call_correlation_id": tool_call_correlation_id,
        "conversation_label": classification.label,
        "conversation_confidence": classification.confidence,
        "conversation_reasons": list(classification.reasons),
        "is_user_authored": is_user_authored,
        "user_authorship_basis": (
            "conversation_record_classifier"
            if classification.is_human_authored
            else "simple_top_level_role_user_text"
            if simple_top_level_user_text
            else None
        ),
    }


def _recover_remembered_user_wording_clean_opened(
    *,
    report: dict,
    action: dict,
    initially_opened: list[dict],
    cwd: Path,
    query: str,
) -> dict:
    refs = _remembered_user_wording_recovery_refs(report=report, action=action)
    if not refs:
        return {
            "status": "no_bounded_recovery_refs_available",
            "opened": [],
            "searched_ref_count": 0,
            "opened_ref_count": 0,
            "rejected_prompt_echo_refs": [],
            "rejected_non_user_refs": [],
        }
    initial_targets = {str(item.get("target") or "") for item in initially_opened if item.get("target")}
    opened = [
        item
        for item in _open_ask_answer_proof_refs(refs, cwd=cwd)
        if item.get("status") == "opened" and item.get("summary") and str(item.get("target") or "") not in initial_targets
    ]
    user_opened = _remembered_user_wording_user_authored_opened(opened)
    clean_opened = _remembered_user_wording_non_echo_user_opened(user_opened, query=query)
    rejected_echo = [
        _prompt_echo_ref_public_summary(item, query=query)
        for item in user_opened
        if _remembered_user_wording_prompt_echo_reasons(item, query=query)
    ]
    user_targets = {str(item.get("target") or "") for item in user_opened if item.get("target")}
    rejected_non_user = [
        _ref_public_summary(item)
        for item in opened
        if str(item.get("target") or "") not in user_targets
    ]
    return {
        "status": "clean_user_proof_recovered" if clean_opened else "no_clean_user_proof_recovered",
        "opened": clean_opened[:_ASK_ANSWER_MAX_REFS],
        "searched_ref_count": len(refs),
        "opened_ref_count": len(opened),
        "rejected_prompt_echo_refs": rejected_echo[:_ASK_ANSWER_MAX_REFS],
        "rejected_non_user_refs": rejected_non_user[:_ASK_ANSWER_MAX_REFS],
    }


def _remembered_user_wording_rejected_diagnostic_opened(
    *,
    report: dict,
    action: dict,
    cwd: Path,
) -> list[dict]:
    refs: list[dict] = []

    def add_group(group: object) -> None:
        for item in group if isinstance(group, list) else []:
            if isinstance(item, dict):
                refs.append(item)

    compact = report.get("executed_output") if isinstance(report.get("executed_output"), dict) else {}
    core_plan = compact.get("core_convo_first_read_plan") if isinstance(compact.get("core_convo_first_read_plan"), dict) else {}
    locator = compact.get("source_locator_handoff") if isinstance(compact.get("source_locator_handoff"), dict) else {}
    add_group(action.get("read"))
    add_group(action.get("diagnostics_after"))
    add_group(locator.get("next_reads"))
    add_group(core_plan.get("read"))
    add_group(core_plan.get("diagnostics_after"))
    add_group(compact.get("next_reads"))
    diagnostic_refs = [
        ref
        for ref in _dedupe_ask_answer_proof_refs(refs)
        if ref.get("diagnostic_only") is True
        or ref.get("target_source_role") == "diagnostic_or_post_hoc"
        or ref.get("final_support_allowed") is False
    ]
    return [
        item
        for item in _open_ask_answer_proof_refs(diagnostic_refs, cwd=cwd)
        if item.get("status") == "opened" and item.get("summary")
    ]


def _recover_remembered_user_wording_from_latest_user_messages(
    *,
    query: str,
    config: dict | None,
    cwd: Path,
) -> dict:
    if not config:
        return {
            "status": "not_run_explicit_root_without_codex_home",
            "opened": [],
            "searched_scope": "not_searched",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "query_terms": [],
        }
    terms = _remembered_user_wording_topic_terms(query)
    if not terms:
        return {
            "status": "not_run_no_topic_terms",
            "opened": [],
            "searched_scope": "codex_history_and_codex_sessions_only",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "query_terms": [],
        }
    try:
        result = latest_user_messages(
            codex_home=Path(str(config.get("codex_home") or "")).expanduser(),
            default_tz=timezone.utc,
            limit=_ASK_ANSWER_REMEMBERED_USER_LATEST_RECOVERY_LIMIT,
            max_files=max(1, int(config.get("max_files") or 200)),
            max_bytes=max(1, int(config.get("max_bytes") or 100 * 1024 * 1024)),
            max_elapsed=max(0.1, float(config.get("max_elapsed") or 15.0)),
            snippet_chars=0,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI recovery surface
        return {
            "status": "error",
            "error": str(exc),
            "opened": [],
            "searched_scope": "codex_history_and_codex_sessions_only",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "query_terms": sorted(terms),
        }
    messages = [item for item in result.get("messages") or [] if isinstance(item, dict)]
    scored = [
        (score, item)
        for item in messages
        if (score := _remembered_user_message_topic_score(item, terms)) >= min(2, len(terms))
    ]
    scored.sort(
        key=lambda pair: (
            pair[0],
            str(pair[1].get("timestamp_utc") or ""),
            1 if pair[1].get("source_kind") == "codex_history" else 0,
        ),
        reverse=True,
    )
    opened: list[dict] = []
    rejected_prompt_echo: list[dict] = []
    for score, message in scored:
        item = _latest_user_message_recovery_opened_item(message, score=score, cwd=cwd)
        if _remembered_user_wording_prompt_echo_reasons(item, query=query):
            rejected_prompt_echo.append(_prompt_echo_ref_public_summary(item, query=query))
            continue
        opened.append(item)
        if len(opened) >= 1:
            break
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "status": "clean_user_proof_recovered" if opened else "no_matching_user_message_found",
        "opened": opened,
        "searched_scope": result.get("source_scope") or "codex_history_and_codex_sessions_only",
        "searched_files": summary.get("searched_files"),
        "candidate_user_message_count": summary.get("candidate_user_message_count"),
        "matched_user_message_count": len(scored),
        "rejected_prompt_echo_ref_count": len(rejected_prompt_echo),
        "rejected_prompt_echo_refs": rejected_prompt_echo[:_ASK_ANSWER_MAX_REFS],
        "query_terms": sorted(terms),
        "warnings": (result.get("sources") or {}).get("warnings") if isinstance(result.get("sources"), dict) else [],
    }


def _recover_remembered_topic_causal_instruction_from_latest_user_messages(
    *,
    query: str,
    config: dict | None,
    cwd: Path,
) -> dict:
    if not config:
        return {
            "status": "not_run_explicit_root_without_codex_home",
            "opened": [],
            "searched_scope": "not_searched",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "rejected_prompt_echo_ref_count": 0,
            "rejected_non_support_ref_count": 0,
            "query_terms": [],
        }
    terms = _remembered_user_wording_topic_terms(query)
    if not terms:
        return {
            "status": "not_run_no_topic_terms",
            "opened": [],
            "searched_scope": "codex_history_and_codex_sessions_only",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "rejected_prompt_echo_ref_count": 0,
            "rejected_non_support_ref_count": 0,
            "query_terms": [],
        }
    try:
        result = latest_user_messages(
            codex_home=Path(str(config.get("codex_home") or "")).expanduser(),
            default_tz=timezone.utc,
            limit=_ASK_ANSWER_REMEMBERED_USER_LATEST_RECOVERY_LIMIT,
            max_files=max(1, int(config.get("max_files") or 200)),
            max_bytes=max(1, int(config.get("max_bytes") or 100 * 1024 * 1024)),
            max_elapsed=max(0.1, float(config.get("max_elapsed") or 15.0)),
            snippet_chars=0,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI recovery surface
        return {
            "status": "error",
            "error": str(exc),
            "opened": [],
            "searched_scope": "codex_history_and_codex_sessions_only",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "rejected_prompt_echo_ref_count": 0,
            "rejected_non_support_ref_count": 0,
            "query_terms": sorted(terms),
        }
    messages = [item for item in result.get("messages") or [] if isinstance(item, dict)]
    scored = [
        (score, item)
        for item in messages
        if (score := _remembered_user_message_topic_score(item, terms)) >= min(2, len(terms))
    ]
    scored.sort(
        key=lambda pair: (
            pair[0],
            str(pair[1].get("timestamp_utc") or ""),
            1 if pair[1].get("source_kind") == "codex_history" else 0,
        ),
        reverse=True,
    )
    opened: list[dict] = []
    rejected_prompt_echo: list[dict] = []
    rejected_non_support = 0
    for score, message in scored:
        item = _latest_user_message_recovery_opened_item(message, score=score, cwd=cwd)
        if _remembered_user_wording_prompt_echo_reasons(item, query=query):
            rejected_prompt_echo.append(_prompt_echo_ref_public_summary(item, query=query))
            continue
        if not _remembered_topic_causal_opened_supports_answer(query=query, item=item):
            rejected_non_support += 1
            continue
        opened.append(item)
        break
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "status": "clean_user_instruction_proof_recovered" if opened else "no_matching_user_instruction_proof_found",
        "opened": opened,
        "searched_scope": result.get("source_scope") or "codex_history_and_codex_sessions_only",
        "searched_files": summary.get("searched_files"),
        "candidate_user_message_count": summary.get("candidate_user_message_count"),
        "matched_user_message_count": len(scored),
        "rejected_prompt_echo_ref_count": len(rejected_prompt_echo),
        "rejected_prompt_echo_refs": rejected_prompt_echo[:_ASK_ANSWER_MAX_REFS],
        "rejected_non_support_ref_count": rejected_non_support,
        "query_terms": sorted(terms),
        "warnings": (result.get("sources") or {}).get("warnings") if isinstance(result.get("sources"), dict) else [],
    }


def _remembered_user_latest_recovery_public(recovery: dict) -> dict:
    return {
        "status": recovery.get("status"),
        "searched_scope": recovery.get("searched_scope"),
        "searched_files": recovery.get("searched_files"),
        "candidate_user_message_count": recovery.get("candidate_user_message_count"),
        "matched_user_message_count": recovery.get("matched_user_message_count"),
        "rejected_prompt_echo_ref_count": recovery.get("rejected_prompt_echo_ref_count"),
        "rejected_non_support_ref_count": recovery.get("rejected_non_support_ref_count"),
        "query_terms": recovery.get("query_terms") or [],
    }


def _recover_latest_status_quoted_before_boundary(
    *,
    query: str,
    route_family: str,
    config: dict | None,
    cwd: Path,
) -> dict:
    phrases = _latest_status_quoted_before_boundary_phrases(query)
    if route_family != "latest_status" or not phrases:
        return {"status": "not_applicable", "opened": []}
    if not config:
        return {
            "status": "not_run_explicit_root_without_codex_home",
            "opened": [],
            "quoted_boundary_phrases": phrases,
            "searched_scope": "not_searched",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "rejected_prompt_echo_ref_count": 0,
        }
    try:
        result = latest_user_messages(
            codex_home=Path(str(config.get("codex_home") or "")).expanduser(),
            default_tz=timezone.utc,
            limit=_ASK_ANSWER_REMEMBERED_USER_LATEST_RECOVERY_LIMIT,
            max_files=max(1, int(config.get("max_files") or 200)),
            max_bytes=max(1, int(config.get("max_bytes") or 100 * 1024 * 1024)),
            max_elapsed=max(0.1, float(config.get("max_elapsed") or 15.0)),
            snippet_chars=0,
        )
    except Exception as exc:  # pragma: no cover - defensive CLI recovery surface
        return {
            "status": "error",
            "error": str(exc),
            "opened": [],
            "quoted_boundary_phrases": phrases,
            "searched_scope": "codex_history_and_codex_sessions_only",
            "searched_files": 0,
            "candidate_user_message_count": 0,
            "matched_user_message_count": 0,
            "rejected_prompt_echo_ref_count": 0,
        }
    messages = [item for item in result.get("messages") or [] if isinstance(item, dict)]
    normalized_phrases = [_simple_normalize(phrase) for phrase in phrases if _simple_normalize(phrase)]
    matched_messages = []
    rejected_prompt_echo: list[dict] = []
    for message in messages:
        snippet = _simple_normalize(str(message.get("snippet") or ""))
        if not any(phrase and phrase in snippet for phrase in normalized_phrases):
            continue
        matched_messages.append(message)
        score = max(1, len(snippet.split()))
        item = _latest_user_message_recovery_opened_item(message, score=score, cwd=cwd)
        if _remembered_user_wording_prompt_echo_reasons(item, query=query):
            rejected_prompt_echo.append(_prompt_echo_ref_public_summary(item, query=query))
            continue
        summary = str(item.get("summary") or "")
        if not any(phrase and phrase in _simple_normalize(summary) for phrase in normalized_phrases):
            continue
        summary_lc = summary.casefold()
        if _REMEMBERED_USER_WORDING_ASSIGNMENT_RE.search(summary) or "do not edit product code" in summary_lc:
            rejected_prompt_echo.append(_prompt_echo_ref_public_summary(item, query=query))
            continue
        opened = [item]
        summary_packet = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        return {
            "status": "clean_user_boundary_recovered",
            "opened": opened,
            "quoted_boundary_phrases": phrases,
            "recovered_boundary_phrase": phrases[0],
            "searched_scope": result.get("source_scope") or "codex_history_and_codex_sessions_only",
            "searched_files": summary_packet.get("searched_files"),
            "candidate_user_message_count": summary_packet.get("candidate_user_message_count"),
            "matched_user_message_count": len(matched_messages),
            "rejected_prompt_echo_ref_count": len(rejected_prompt_echo),
            "rejected_prompt_echo_refs": rejected_prompt_echo[:_ASK_ANSWER_MAX_REFS],
        }
    summary_packet = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "status": "no_matching_quoted_user_message_found",
        "opened": [],
        "quoted_boundary_phrases": phrases,
        "searched_scope": result.get("source_scope") or "codex_history_and_codex_sessions_only",
        "searched_files": summary_packet.get("searched_files"),
        "candidate_user_message_count": summary_packet.get("candidate_user_message_count"),
        "matched_user_message_count": len(matched_messages),
        "rejected_prompt_echo_ref_count": len(rejected_prompt_echo),
        "rejected_prompt_echo_refs": rejected_prompt_echo[:_ASK_ANSWER_MAX_REFS],
    }


def _latest_status_quoted_before_boundary_phrases(query: str) -> list[str]:
    normalized = _simple_normalize(query)
    if "before" not in normalized or "not after" not in normalized:
        return []
    phrases = []
    for match in _QUOTED_STATUS_BOUNDARY_RE.finditer(str(query or "")):
        phrase = _simple_normalize(match.group(1))
        if len(phrase.split()) >= 3 and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def _simple_normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _latest_status_quote_boundary_recovery_public(recovery: dict) -> dict:
    return {
        "schema_version": 1,
        "status": recovery.get("status"),
        "searched_scope": recovery.get("searched_scope"),
        "searched_files": recovery.get("searched_files"),
        "candidate_user_message_count": recovery.get("candidate_user_message_count"),
        "matched_user_message_count": recovery.get("matched_user_message_count"),
        "quoted_boundary_phrases": recovery.get("quoted_boundary_phrases") or [],
        "recovered_boundary_phrase": recovery.get("recovered_boundary_phrase"),
        "rejected_prompt_echo_ref_count": recovery.get("rejected_prompt_echo_ref_count"),
    }


def _latest_status_quote_boundary_anchor_only_next_step(*, recovery: dict, opened: list[dict]) -> dict:
    diagnostic_refs = [_ref_public_summary(item) for item in opened[:_ASK_ANSWER_MAX_REFS]]
    boundary_target = ""
    if diagnostic_refs:
        boundary_target = str(diagnostic_refs[0].get("target") or diagnostic_refs[0].get("openable_target") or "")
    read_more_command = None
    if boundary_target:
        read_more_command = (
            "jsonl-history-search read-more "
            f"{shlex.quote(boundary_target)} --mode window --before 10 --after 0 --cwd \"$PWD\" --format json"
        )
    return {
        "status": "boundary_anchor_only_not_final_proof",
        "required_next_proof": "source-role appropriate raw evidence at or before the recovered boundary that proves the requested preceding status/window",
        "diagnostic_boundary_refs": diagnostic_refs,
        "quoted_boundary_phrases": recovery.get("quoted_boundary_phrases") or [],
        "suggested_action": (
            "Treat the recovered quote as an anchor/diagnostic ref only. Open the preceding raw history/session "
            "window and answer from status/window evidence at or before that boundary; do not finalize from the "
            "quoted boundary line alone or from later task/slice prompt echoes."
        ),
        "suggested_read_more_command": read_more_command,
    }


def _remembered_user_wording_topic_terms(query: str) -> set[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "asked",
        "before",
        "constraint",
        "constraints",
        "did",
        "exactly",
        "for",
        "give",
        "gave",
        "instruction",
        "instructions",
        "into",
        "message",
        "request",
        "requested",
        "say",
        "said",
        "tell",
        "that",
        "the",
        "this",
        "what",
        "when",
        "where",
        "with",
        "you",
        "your",
    }
    terms = set()
    for raw in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", query.casefold()):
        term = raw.replace("_", "-").strip("-")
        if term in stopwords:
            continue
        stem = _remembered_user_wording_stem(term)
        if len(stem) >= 3 and stem not in stopwords:
            terms.add(stem)
    return terms


def _remembered_user_wording_stem(term: str) -> str:
    for suffix in ("ing", "ingly", "edly", "ed", "es", "s"):
        if len(term) > len(suffix) + 3 and term.endswith(suffix):
            return term[: -len(suffix)]
    return term


def _remembered_user_message_topic_score(message: dict, terms: set[str]) -> int:
    text_terms = {
        _remembered_user_wording_stem(raw)
        for raw in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(message.get("snippet") or "").casefold())
    }
    return len(terms & text_terms)


def _latest_user_message_recovery_opened_item(message: dict, *, score: int, cwd: Path) -> dict:
    target = str(message.get("openable_target") or message.get("source_ref") or "")
    path_text, line = _split_ref_target(target) if target else ("", None)
    rel_target = target
    if path_text:
        rel_path = _safe_relative_path(cwd, Path(path_text).expanduser())
        rel_target = f"{rel_path}:{line}" if line else rel_path
    ref = {
        "kind": "jsonl_source_ref",
        "target_source_role": "target_historical_raw",
        "reason": "bounded user-message recovery matched remembered-instruction topic terms",
        "source_ref": message.get("openable_source_ref"),
        "openable_target": message.get("openable_target"),
        "source_tier": "primary_raw_chat_log",
        "source_role_label": "user_authored_raw_message",
        "timestamp_utc": message.get("timestamp_utc"),
        "remembered_user_recovery": {
            "searched_scope": "codex_history_and_codex_sessions_only",
            "topic_score": score,
        },
    }
    summary = " ".join(
        part
        for part in (str(message.get("timestamp_utc") or ""), "user:", str(message.get("snippet") or ""))
        if part
    )
    return {
        "status": "opened",
        "target": rel_target,
        "path": rel_target.rsplit(":", 1)[0] if ":" in rel_target else rel_target,
        "line": line,
        "summary": summary[:_ASK_ANSWER_MAX_LINE_CHARS],
        "record_metadata": {
            "conversation_label": "user_message",
            "conversation_confidence": "high",
            "is_user_authored": True,
            "user_authorship_basis": "latest_user_messages_human_filter",
            "top_role": "user",
            "payload_type": None,
            "payload_role": None,
            "message_role": "user",
        },
        "ref": ref,
    }


def _remembered_user_wording_recovery_refs(*, report: dict, action: dict) -> list[dict]:
    refs: list[dict] = []

    def add_group(group: object) -> None:
        for item in group if isinstance(group, list) else []:
            if isinstance(item, dict) and _remembered_user_wording_recovery_ref_allowed(item):
                refs.append(item)

    target_handoff = action.get("target_proof_handoff") if isinstance(action.get("target_proof_handoff"), dict) else {}
    core_plan = action.get("core_convo_first_read_plan") if isinstance(action.get("core_convo_first_read_plan"), dict) else {}
    compact = report.get("executed_output") if isinstance(report.get("executed_output"), dict) else {}
    compact_core = (
        compact.get("core_convo_first_read_plan")
        if isinstance(compact.get("core_convo_first_read_plan"), dict)
        else {}
    )

    add_group(target_handoff.get("required_primary_proof"))
    add_group(action.get("read"))
    add_group(core_plan.get("read"))
    role_groups = core_plan.get("role_groups") if isinstance(core_plan.get("role_groups"), dict) else {}
    add_group(role_groups.get("target_historical_raw"))
    add_group(compact_core.get("read"))
    compact_role_groups = compact_core.get("role_groups") if isinstance(compact_core.get("role_groups"), dict) else {}
    add_group(compact_role_groups.get("target_historical_raw"))
    add_group(compact.get("next_reads"))
    add_group(target_handoff.get("secondary_context_after_source_inspection"))

    return _dedupe_ask_answer_proof_refs(
        refs,
        limit=_ASK_ANSWER_REMEMBERED_USER_RECOVERY_MAX_REFS,
    )


def _remembered_user_wording_recovery_ref_allowed(ref: dict) -> bool:
    if ref.get("diagnostic_only") is True:
        return False
    if ref.get("read_more_actionable") is False or ref.get("followthrough_actionable") is False:
        return False
    if ref.get("kind") in {"targeted_fail_closed_boundary", "missing_role_recovery_command"}:
        return False
    target_role = str(ref.get("target_source_role") or "")
    if target_role and target_role == "diagnostic_or_post_hoc":
        return False
    return bool(ref.get("source_ref") or ref.get("target") or ref.get("openable_target") or ref.get("path"))


def _remembered_user_wording_user_authored_opened(opened: list[dict]) -> list[dict]:
    user_authored: list[dict] = []
    for item in opened:
        metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
        if metadata.get("is_user_authored") is True:
            user_authored.append(item)
            continue
        for record in item.get("record_summaries") or []:
            if not isinstance(record, dict):
                continue
            record_metadata = (
                record.get("record_metadata") if isinstance(record.get("record_metadata"), dict) else {}
            )
            if record_metadata.get("is_user_authored") is True and record.get("summary"):
                cloned = dict(item)
                cloned["target"] = record.get("target") or item.get("target")
                cloned["line"] = record.get("line")
                cloned["summary"] = record.get("summary")
                cloned["record_metadata"] = record_metadata
                user_authored.append(cloned)
    return user_authored


def _remembered_user_wording_non_echo_user_opened(opened: list[dict], *, query: str) -> list[dict]:
    return [
        item
        for item in opened
        if not _remembered_user_wording_prompt_echo_reasons(item, query=query)
    ]


def _remembered_user_wording_prompt_echo_reasons(item: dict, *, query: str) -> list[str]:
    raw_text = str(item.get("summary") or "")
    text = raw_text.casefold()
    shared_echo = setup_echo_classification(raw_text, query=query)
    if shared_echo.get("explicit_prompt_scaffold_query"):
        return []
    reasons: list[str] = []
    query_text = str(query or "").strip().casefold()
    if query_text and query_text in text:
        reasons.append("contains_current_ask_query_text")
    if _REMEMBERED_USER_WORDING_ASSIGNMENT_RE.search(raw_text):
        reasons.append("assignment_or_review_scaffolding")
    if shared_echo.get("is_setup_echo"):
        reason = str(shared_echo.get("reason") or "setup_echo")
        reasons.append(f"setup_echo:{reason}")
    for marker in _REMEMBERED_USER_WORDING_PROMPT_ECHO_MARKERS:
        if marker in text:
            reasons.append(f"prompt_echo_marker:{marker}")
    return _unique_preserving_order(reasons)


def _prompt_echo_ref_public_summary(item: dict, *, query: str) -> dict:
    summary = _ref_public_summary(item)
    summary["prompt_echo_reasons"] = _remembered_user_wording_prompt_echo_reasons(item, query=query)
    return summary


def _prompt_echo_opened_ref_summaries(opened: list[dict], *, query: str) -> list[str]:
    summaries: list[str] = []
    for item in opened[:_ASK_ANSWER_MAX_REFS]:
        reasons = ",".join(_remembered_user_wording_prompt_echo_reasons(item, query=query))
        summaries.append(f"{item.get('target')}: rejected_prompt_echo={reasons}")
    return summaries


def _non_user_opened_ref_summaries(opened: list[dict]) -> list[str]:
    summaries: list[str] = []
    for item in opened[:_ASK_ANSWER_MAX_REFS]:
        metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
        if not metadata and item.get("record_summaries"):
            labels = []
            for record in item.get("record_summaries") or []:
                if not isinstance(record, dict):
                    continue
                record_metadata = (
                    record.get("record_metadata") if isinstance(record.get("record_metadata"), dict) else {}
                )
                label = record_metadata.get("conversation_label")
                confidence = record_metadata.get("conversation_confidence")
                if label:
                    labels.append(f"{record.get('target')}: {label}/{confidence}")
            if labels:
                summaries.append("; ".join(labels))
                continue
        label = metadata.get("conversation_label") or "non-jsonl-or-unclassified"
        confidence = metadata.get("conversation_confidence") or "unknown"
        summaries.append(f"{item.get('target')}: {label}/{confidence}")
    return summaries


def _summarize_markdown_file(lines: list[str]) -> str:
    selected: list[str] = []
    high_signal: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("# ") or _looks_like_markdown_proof_summary_line(line):
            selected.append(line)
        if (
            _looks_like_markdown_review_failure_answer_line(line)
            or _looks_like_markdown_fix_rereview_answer_line(line)
            or _looks_like_markdown_merge_commit_answer_line(line)
        ):
            high_signal.append(line)
        if len(selected) >= 5 and len(high_signal) >= 4:
            break
    if not selected:
        selected = [line.strip() for line in lines if line.strip()][:5]
    combined = selected[:5]
    for line in high_signal:
        if line not in combined:
            combined.append(line)
        if len(combined) >= 9:
            break
    return " | ".join(combined)[:_ASK_ANSWER_MAX_FILE_CHARS]


def _looks_like_markdown_proof_summary_line(line: str) -> bool:
    normalized = line.lstrip("-*` ").lower()
    if normalized in {"proof:", "regression test:", "commit:", "review:"}:
        return False
    return normalized.startswith(
        (
            "status:",
            "verdict:",
            "proof:",
            "regression test:",
            "commit:",
            "review:",
            "merge commit:",
            "merge proof:",
            "parent merge commit:",
            "implementation commit:",
            "review commit:",
            "close commit:",
            "closeout commit:",
        )
    )


def _looks_like_markdown_merge_commit_answer_line(line: str) -> bool:
    normalized = line.lstrip("-*` ").casefold()
    if not re.search(r"\b[0-9a-f]{7,40}\b", normalized):
        return False
    return any(
        marker in normalized
        for marker in (
            "merge commit",
            "parent merge",
            "merge proof",
            "merged",
            "integrated on parent",
        )
    )


def _looks_like_markdown_review_failure_answer_line(line: str) -> bool:
    normalized = line.lstrip("-*` ").casefold()
    return (
        any(term in normalized for term in ("failed-review", "review failure", "failed review"))
        or (
            any(term in normalized for term in ("failed", "failure", "`failed`"))
            and any(term in normalized for term in ("because", "boundary", "direct support", "overclassified"))
        )
    )


def _looks_like_markdown_fix_rereview_answer_line(line: str) -> bool:
    normalized = line.lstrip("-*` ").casefold()
    return any(term in normalized for term in ("rereview", "re-review", "after fix", "fix/rereview")) and any(
        term in normalized for term in ("verified", "`verified`", "verdict", "outcome", "fixed")
    )


def _ref_public_summary(item: dict) -> dict:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    source_ref = ref.get("source_ref")
    summary = {
        "target": item.get("target"),
        "kind": ref.get("kind"),
        "proof_surface": ref.get("proof_surface"),
        "target_source_role": ref.get("target_source_role"),
        "reason": ref.get("reason"),
        "source_ref": source_ref,
        "openable_target": ref.get("openable_target"),
        "source_tier": ref.get("source_tier"),
        "source_role_label": ref.get("source_role_label"),
        "timestamp_utc": ref.get("timestamp_utc"),
        "expectation_mismatch_role": ref.get("expectation_mismatch_role"),
        "expectation_mismatch_inferred_role": ref.get("expectation_mismatch_inferred_role"),
    }
    for key in (
        "commit",
        "commit_time",
        "author",
        "committer",
        "subject",
        "parent_count",
        "parents",
        "contained_branches",
        "is_true_merge_commit",
        "commit_role",
    ):
        if key in ref:
            summary[key] = ref.get(key)
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    if metadata:
        summary["record_metadata"] = {
            "conversation_label": metadata.get("conversation_label"),
            "conversation_confidence": metadata.get("conversation_confidence"),
            "is_user_authored": metadata.get("is_user_authored"),
            "user_authorship_basis": metadata.get("user_authorship_basis"),
            "top_role": metadata.get("top_role"),
            "payload_type": metadata.get("payload_type"),
            "payload_role": metadata.get("payload_role"),
            "message_role": metadata.get("message_role"),
        }
    return summary


def _compose_ask_answer(*, query: str, route_family: str, action: dict, opened: list[dict]) -> str | None:
    summaries = [str(item.get("summary") or "").strip() for item in opened if item.get("summary")]
    if not summaries:
        return None
    named = (
        action.get("named_task_action_proof_handoff")
        if isinstance(action.get("named_task_action_proof_handoff"), dict)
        else {}
    )
    if _post_merge_smoke_answer_wanted(query=query, route_family=route_family):
        return _compose_post_merge_smoke_answer(opened=opened)
    if _action_chronology_answer_wanted(query=query, route_family=route_family):
        action_chronology_answer = _compose_action_chronology_answer(opened=opened)
        if action_chronology_answer:
            return action_chronology_answer
    completed_task_answer = _compose_completed_task_artifact_answer(
        query=query,
        route_family=route_family,
        opened=opened,
    )
    if completed_task_answer:
        return completed_task_answer
    if named.get("status") in {
        "current_named_task_proof_refs_available",
        "current_named_task_stopped_episode_proof_refs_available",
    }:
        return _compose_named_task_answer(query=query, named=named, opened=opened)
    if route_family == "remembered_user_wording":
        return f"You said this in the opened user-message proof: {_short_join(summaries[:2])}"
    if route_family == "remembered_topic":
        return _compose_remembered_topic_causal_instruction_answer(query=query, opened=opened)
    if route_family == "active_status_front_door":
        return _compose_active_status_front_door_answer(action=action, opened=opened)
    if route_family == "latest_status":
        return _compose_latest_status_answer(query=query, action=action, opened=opened)
    if route_family == "semantic_wrapup":
        return _compose_semantic_wrapup_corrections_answer(query=query, action=action, opened=opened)
    if route_family == "expectation_mismatch":
        return _compose_expectation_mismatch_answer(opened=opened)
    if route_family == "process_actual_use":
        return _compose_process_actual_use_answer(action=action, opened=opened)
    if _branch_work_episode_answer_wanted(query=query, route_family=route_family):
        return _compose_branch_work_episode_answer(query=query, opened=opened)
    if route_family in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return f"The opened proof refs show: {_short_join(summaries[:4])}"
    return None


def _sanitize_final_answer_prose(text: str) -> str:
    sanitized = str(text or "")
    sanitized = re.sub(r"tool output:\s*Chunk ID[^\n.;]*", "opened tool-output proof ref", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r'"type"\s*:\s*"event_msg"', "event message record", sanitized)
    sanitized = re.sub(r'"timestamp"\s*:\s*"[^"]*"', "event timestamp", sanitized)
    sanitized = sanitized.replace('"timestamp"', "event timestamp")
    sanitized = sanitized.replace('"payload"', "event payload")
    sanitized = sanitized.replace('"type":"event_msg"', "event message record")
    sanitized = sanitized.replace('"type": "event_msg"', "event message record")
    sanitized = _redact_local_paths_for_final_answer(sanitized)
    return re.sub(r"\s{2,}", " ", sanitized).strip()


def _remembered_topic_causal_instruction_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family != "remembered_topic":
        return False
    normalized = _simple_normalize(query)
    if "instead of" not in normalized:
        return False
    if not re.search(r"\bwhy did i (?:mention|say|ask for|request|bring up|call out)\b", normalized):
        return False
    terms = _remembered_user_wording_topic_terms(query)
    has_coordination = any(term.startswith("coordinat") for term in terms)
    has_implementation = any(term.startswith("implement") for term in terms)
    return has_coordination and has_implementation


def _remembered_topic_causal_opened_supports_answer(*, query: str, item: dict) -> bool:
    if not _remembered_topic_causal_instruction_answer_wanted(
        query=query,
        route_family="remembered_topic",
    ):
        return False
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    if metadata.get("is_user_authored") is not True:
        return False
    if _remembered_user_wording_prompt_echo_reasons(item, query=query):
        return False
    text = str(item.get("summary") or "").casefold()
    has_coordination = any(term in text for term in ("coordinating", "coordinate", "coordinator"))
    has_implementation = any(
        term in text
        for term in ("implementing", "implementation", "implementer", "implementers", "agents")
    )
    has_instructional_reason = any(
        term in text
        for term in (
            "fsm",
            "orchestration loop",
            "product loop",
            "main codex",
            "delegate",
            "agents are for",
            "broken down",
            "split",
            "rabbithole",
            "impactful thing next",
        )
    )
    return has_coordination and has_implementation and has_instructional_reason


def _compose_remembered_topic_causal_instruction_answer(*, query: str, opened: list[dict]) -> str | None:
    if not _remembered_topic_causal_instruction_answer_wanted(
        query=query,
        route_family="remembered_topic",
    ):
        return None
    summaries = [str(item.get("summary") or "").strip() for item in opened if item.get("summary")]
    if not summaries:
        return None
    joined = " ".join(summaries).casefold()
    if not (
        any(term in joined for term in ("coordinating", "coordinate", "coordinator"))
        and any(term in joined for term in ("implementing", "implementation", "implementer", "agents"))
    ):
        return None
    source_summary = _short_join(summaries[:2], limit=520)
    return (
        "You mentioned coordinating instead of implementing because the opened user-authored instruction "
        "says the main/coordinator Codex should coordinate the FSM/product loop, select and split the work, "
        "and delegate implementation to implementer agents instead of doing the product-code implementation "
        f"itself. Opened proof: {source_summary}"
    )


def _compose_active_status_front_door_answer(*, action: dict, opened: list[dict]) -> str | None:
    then_use = action.get("then_use") if isinstance(action.get("then_use"), dict) else {}
    status = (
        then_use.get("active_status_front_door")
        if isinstance(then_use.get("active_status_front_door"), dict)
        else {}
    )
    if status.get("status") != "ready":
        return None
    active_task = str(status.get("active_task_path") or "").strip()
    required_paths = {
        "TODO.md",
        "codex/state/pipeline-state.md",
        "codex/state/current-context-window.md",
        active_task,
    }
    opened_front_door = [
        item
        for item in opened
        if item.get("status") == "opened"
        and str((item.get("ref") or {}).get("read_role") or "") == "required_context"
        and str((item.get("ref") or {}).get("path") or "") in required_paths
    ]
    if len(opened_front_door) < 2:
        return None
    active_id = str(status.get("active_task_id") or "").strip()
    fsm_state = str(status.get("fsm_state") or "").strip()
    substate = str(status.get("current_substate") or "").strip()
    next_action = str(status.get("next_legal_action") or "").strip()
    bottleneck = str(status.get("product_bottleneck") or "").strip()
    currentness = str(status.get("local_currentness") or "").strip()
    boundary = str(status.get("currentness_boundary") or "").strip()
    pieces = [
        "Active repo front-door state:",
        _active_status_sentence(f"active task {active_id + ' ' if active_id else ''}{active_task}"),
        _active_status_sentence(f"Current FSM state: {fsm_state}" + (f" / {substate}" if substate else "")),
        _active_status_sentence(f"Next legal action: {next_action}"),
    ]
    if bottleneck:
        pieces.append(_active_status_sentence(f"Product bottleneck: {bottleneck}"))
    if currentness:
        pieces.append(_active_status_sentence(f"Local currentness: {currentness}"))
    if boundary:
        pieces.append(f"Boundary: {boundary}")
    pieces.append(
        "Refs are repo front-door files only; they are not raw source-session chronology, worker action chronology, or causal expectation-mismatch proof."
    )
    return " ".join(pieces)


def _active_status_sentence(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return text if text.endswith((".", "!", "?")) else text + "."


def _compose_process_actual_use_answer(*, action: dict, opened: list[dict]) -> str | None:
    summaries = [str(item.get("summary") or "").strip() for item in opened if item.get("summary")]
    if not summaries:
        return None
    covered = sorted(
        {
            bucket
            for item in opened
            for bucket in item.get("process_actual_use_selected_buckets") or []
            if str(bucket).strip()
        }
    )
    plan = (
        action.get("process_actual_use_final_support_read_plan")
        if isinstance(action.get("process_actual_use_final_support_read_plan"), dict)
        else {}
    )
    bundle = (
        plan.get("process_actual_use_bucket_evidence_bundle")
        if isinstance(plan.get("process_actual_use_bucket_evidence_bundle"), dict)
        else {}
    )
    bundle_summary = bundle.get("summary") if isinstance(bundle.get("summary"), dict) else {}
    missing = list(bundle_summary.get("missing_or_excluded_buckets") or [])
    secondary_only = list(bundle_summary.get("secondary_only_buckets") or [])
    pieces = [
        "Primary process actual-use proof covers buckets: "
        + (", ".join(covered) if covered else "(none)"),
        "Opened primary refs show: " + _short_join(summaries[:3], limit=620),
    ]
    if secondary_only:
        pieces.append("Secondary-only buckets not counted as behavior proof: " + ", ".join(secondary_only))
    if missing:
        pieces.append("Missing or excluded buckets: " + ", ".join(missing))
    pieces.append(
        "Boundary: artifact existence/citation, task/review/report diffs, prior final prose, and current-run tool output are not behavior-changing proof."
    )
    return " ".join(pieces)


def _compose_action_chronology_answer(*, opened: list[dict]) -> str | None:
    pieces = []
    for item in opened:
        roles = [str(role) for role in item.get("action_chronology_roles") or [] if str(role).strip()]
        summary = _action_chronology_public_answer_summary(str(item.get("summary") or "").strip())
        target = str(item.get("target") or "").strip()
        if not roles or not summary:
            continue
        label = ",".join(roles)
        target_label = _action_chronology_public_ref_label(target)
        pieces.append(f"{label}: {summary}" + (f" [{target_label}]" if target_label else ""))
    if not pieces:
        return None
    return (
        "Primary action chronology shows: "
        + _short_join(pieces[:4], limit=760)
        + " Boundary: task/review/status artifacts may corroborate this chronology, but they are not primary action proof."
    )


def _action_chronology_public_answer_summary(summary: str) -> str:
    if not summary:
        return ""
    return _redact_jsonl_path_targets_for_final_answer(summary)


def _action_chronology_public_ref_label(target: str) -> str:
    if not target:
        return ""
    return _redact_jsonl_path_targets_for_final_answer(target)


def _redact_jsonl_path_targets_for_final_answer(text: str) -> str:
    sanitized = str(text or "")

    def _line_label(match: re.Match[str]) -> str:
        start = match.group("start")
        end = match.groupdict().get("end")
        if end:
            return f"source window lines {start}-{end}"
        return f"source window line {start}"

    sanitized = re.sub(
        r"(?P<path>(?:/|~)[^\n\]|;]*?\.jsonl):(?P<start>\d+)(?:-(?P<end>\d+))?",
        _line_label,
        sanitized,
    )
    sanitized = re.sub(
        r"\b(?P<path>[A-Za-z0-9._/-]+\.jsonl):(?P<start>\d+)(?:-(?P<end>\d+))?",
        _line_label,
        sanitized,
    )
    return re.sub(r"\s{2,}", " ", sanitized).strip()


def _compose_post_merge_smoke_answer(*, opened: list[dict]) -> str | None:
    pieces = []
    for item in opened:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        reasons = [
            str(reason)
            for reason in item.get("post_merge_smoke_reasons") or []
            if str(reason).strip()
        ]
        label = "direct post-merge smoke proof"
        if reasons:
            label += f" ({','.join(reasons)})"
        pieces.append(f"{label}: {summary}")
    if not pieces:
        return None
    return (
        "Direct post-merge smoke proof shows: "
        + _short_join(pieces[:4], limit=760)
        + " Boundary: stop/closeout chronology and generic merge/status proof are not used as smoke execution proof."
    )


def _compose_branch_work_episode_answer(*, query: str, opened: list[dict]) -> str | None:
    branch = _branch_work_episode_label(query)
    proof_lines = _work_episode_answer_prose_summaries(opened)
    if not proof_lines:
        return None
    return (
        f"Branch {branch} is supported by opened work-episode proof: "
        f"{_short_join(proof_lines[:4], limit=760)} "
        "Boundary: this summarizes branch/work-episode proof and leaves raw refs and proof_summary available for inspection; "
        "it does not paste raw JSONL event payloads into the final answer."
    )


def _branch_work_episode_label(query: str) -> str:
    match = re.search(r"\b[a-z0-9._-]+/[a-z0-9._/-]+\b", str(query or ""), flags=re.IGNORECASE)
    return match.group(0) if match else "the requested branch"


def _work_episode_answer_prose_summaries(opened: list[dict]) -> list[str]:
    cleaned: list[str] = []
    for item in opened:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        fragments = []
        for fragment in _summary_fragments([summary]):
            prose = _work_episode_answer_prose_fragment(fragment, item=item)
            if prose:
                fragments.append(prose)
        if fragments:
            cleaned.append(_short_join(fragments[:4], limit=420))
    return cleaned


def _work_episode_answer_prose_fragment(fragment: str, *, item: dict) -> str:
    text = str(fragment or "").strip().lstrip("-* ").strip()
    if not text:
        return ""
    normalized = text.casefold()
    raw_markers = (
        '"timestamp"',
        '"payload"',
        '"type":"event_msg"',
        '"type": "event_msg"',
        "rollout-2026-",
        "custom_tool_call",
        "function_call_output",
    )
    if any(marker in normalized for marker in raw_markers):
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        surface = str(ref.get("proof_surface") or ref.get("kind") or "opened proof").replace("_", " ")
        target = str(item.get("target") or ref.get("target") or "").strip()
        return f"{surface} at {target}" if target else surface
    text = re.sub(r"^\d{4}-\d{2}-\d{2}T[^\s:]+:?\s*", "", text)
    text = re.sub(r"^(tool output|tool call)\s*:\s*", "", text, flags=re.IGNORECASE)
    return text[:_ASK_ANSWER_MAX_LINE_CHARS]


def _compose_expectation_mismatch_answer(*, opened: list[dict]) -> str | None:
    by_role: dict[str, list[str]] = {
        role: [] for role in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
    }
    for item in opened:
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        roles = [
            str(role)
            for role in item.get("expectation_mismatch_answer_roles") or []
            if str(role) in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
        ]
        for role in roles:
            by_role[role].append(summary)
    if any(not by_role[role] for role in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS):
        return None
    pieces = []
    for role in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS:
        pieces.append(f"{role}: {_short_join(by_role[role][:2], limit=360)}")
    return (
        "The opened expectation-mismatch proof refs cover expected_behavior_evidence, "
        "observed_behavior_or_action_proof, and divergence_evidence: "
        + " ".join(pieces)
    )


def _compose_completed_task_artifact_answer(*, query: str, route_family: str, opened: list[dict]) -> str | None:
    task_ids = _ask_execute_named_task_ids(query)
    if not task_ids:
        return None
    task_id = task_ids[0]
    refs = _completed_task_artifact_opened_refs(opened, task_id=task_id)
    if not refs:
        return None
    task_summaries = _completed_task_role_summaries(refs, "task_file", task_id=task_id)
    review_summaries = _completed_task_role_summaries(refs, "review_artifact", task_id=task_id)
    path_commit_summaries = _completed_task_role_summaries(refs, "path_last_change_commit", task_id=task_id)
    merge_commits = _named_task_merge_commit_hashes(refs, task_id=task_id)
    merge_commit_labels = _named_task_merge_commit_labels(refs, task_id=task_id)
    integration_commit_labels = _named_task_integration_commit_labels(refs, task_id=task_id)
    post_merge_summaries = [
        _completed_task_clean_summary(str(item.get("summary") or ""), task_id=task_id)
        for item in refs
        if str((item.get("ref") or {}).get("proof_surface") or "") in {"post_merge_task_proof", "post_merge_review_proof"}
    ]
    post_merge_summaries = [summary for summary in post_merge_summaries if summary]

    if route_family == "latest_status":
        status_bits = []
        status = _completed_task_status_label(task_summaries + review_summaries)
        if status:
            status_bits.append(status)
        if review_summaries:
            status_bits.append("reviewed/verified")
        if merge_commits:
            status_bits.append("merged")
        elif integration_commit_labels:
            status_bits.append("integrated/final-reviewed")
        if post_merge_summaries:
            status_bits.append("post-merge checked")
        if not status_bits:
            status_bits.append("completed-task artifact proof found")
        proof_bits = []
        if task_summaries:
            proof_bits.append(f"task: {_short_join(task_summaries[:1], limit=260)}")
        if review_summaries:
            proof_bits.append(f"review: {_short_join(review_summaries[:1], limit=220)}")
        if merge_commits:
            proof_bits.append(f"merge commit: {', '.join(commit[:12] for commit in merge_commits[:2])}")
        if integration_commit_labels:
            proof_bits.append(f"integration/final-review commit: {', '.join(integration_commit_labels[:2])}")
        if post_merge_summaries:
            proof_bits.append(f"post-merge proof: {_short_join(post_merge_summaries[:1], limit=220)}")
        return (
            f"{task_id} latest completed-task status: {', '.join(_unique_preserving_order(status_bits))}. "
            f"Proof: {'; '.join(proof_bits)}. "
            "Boundary: this is task/review/git artifact status proof, not raw source-session chronology."
        )

    if _named_task_merge_provenance_answer_wanted(query=query, route_family=route_family):
        source_sessions = _named_task_source_session_proof_summaries(opened)
        source_session_requested = _named_task_merge_provenance_source_session_wanted(query)
        pieces = []
        if source_sessions:
            pieces.append(f"source-session proof: {_short_join(source_sessions[:1], limit=220)}")
        elif not source_session_requested and merge_commits:
            pieces.append("source-session proof: not required for this git merge-actor answer")
        else:
            pieces.append("source-session proof: missing/not opened")
        pieces.append(
            "task artifact proof: "
            + (_short_join(task_summaries[:1], limit=240) if task_summaries else "missing/not opened")
        )
        pieces.append(
            "review artifact proof: "
            + (_short_join(review_summaries[:1], limit=240) if review_summaries else "missing/not opened")
        )
        pieces.append(
            "merge commit proof: "
            + (", ".join(merge_commit_labels[:3]) if merge_commit_labels else "missing/not opened")
        )
        pieces.append(
            "integration/final-review commit proof: "
            + (", ".join(integration_commit_labels[:3]) if integration_commit_labels else "missing/not opened")
        )
        if post_merge_summaries:
            pieces.append(f"post-merge proof: {_short_join(post_merge_summaries[:1], limit=240)}")
        return (
            f"Completed-task merge/provenance proof for {task_id}: "
            + "; ".join(pieces)
            + ". Boundary: task/review/git/post-merge artifacts satisfy only their artifact roles; "
            "a single-parent integration/final-review commit is not a true two-parent merge commit; "
            "they are not raw source-session proof unless a raw session ref is opened."
        )

    if _completed_task_artifact_provenance_answer_wanted(query=query, route_family=route_family):
        proof_bits = []
        if task_summaries:
            proof_bits.append(f"task: {_short_join(task_summaries[:1], limit=260)}")
        if review_summaries:
            proof_bits.append(f"review: {_short_join(review_summaries[:1], limit=240)}")
        if path_commit_summaries:
            proof_bits.append(f"commit evidence: {_short_join(path_commit_summaries[:2], limit=240)}")
        if merge_commits:
            proof_bits.append(f"merge commit: {', '.join(commit[:12] for commit in merge_commits[:2])}")
        if integration_commit_labels:
            proof_bits.append(f"integration/final-review commit: {', '.join(integration_commit_labels[:2])}")
        if post_merge_summaries:
            proof_bits.append(f"post-merge proof: {_short_join(post_merge_summaries[:1], limit=220)}")
        if proof_bits:
            return (
                f"Completed-task artifact proof for {task_id}: "
                + "; ".join(proof_bits)
                + ". Boundary: this proves recorded task/review/git artifact status/provenance, not raw worker-session chronology."
            )
    return None


def _completed_task_artifact_provenance_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return False
    normalized = str(query or "").casefold()
    if _named_task_review_failure_status_answer_wanted(query=query, route_family=route_family):
        return False
    if _named_task_selection_rationale_answer_wanted(query=query, route_family=route_family):
        return False
    if _branch_work_episode_answer_wanted(query=query, route_family=route_family):
        return False
    if re.search(r"\bwhat happened\b", normalized):
        return False
    return (
        "prove" in normalized
        and any(term in normalized for term in ("task", "review", "commit"))
        and any(term in normalized for term in ("action", "taken", "provenance", "status"))
    )


def _completed_task_artifact_opened_refs(opened: list[dict], *, task_id: str) -> list[dict]:
    normalized_task = task_id.casefold()
    selected: list[dict] = []
    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        if not _completed_task_artifact_ref(ref):
            continue
        text = _target_episode_ref_match_text(item)
        if normalized_task not in text:
            continue
        selected.append(item)
    return selected


def _completed_task_role_summaries(refs: list[dict], proof_surface: str, *, task_id: str) -> list[str]:
    summaries: list[str] = []
    for item in refs:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        if str(ref.get("proof_surface") or "") != proof_surface:
            continue
        summary = _completed_task_clean_summary(str(item.get("summary") or ""), task_id=task_id)
        if summary:
            summaries.append(summary)
    return summaries


def _completed_task_clean_summary(summary: str, *, task_id: str) -> str:
    fragments: list[str] = []
    for fragment in _summary_fragments([summary]):
        prose = _named_task_current_proof_prose_fragment(fragment, task_id=task_id)
        if not prose:
            continue
        prose = _redact_local_paths_for_final_answer(prose)
        if ".private/" in prose:
            prose = re.sub(r"\.private/[A-Za-z0-9._/-]+", "retained private proof handle", prose)
        fragments.append(prose)
    return _short_join(fragments[:4], limit=420)


def _completed_task_status_label(summaries: list[str]) -> str:
    text = " ".join(summaries).casefold()
    if "post-merge verified" in text:
        return "post-merge verified"
    if "closed - merged" in text or "closed/merged" in text:
        return "closed/merged"
    if "verified - merged" in text or "reviewed merged" in text:
        return "verified/merged"
    if "status: closed" in text:
        return "closed"
    if "status: verified" in text:
        return "verified"
    if "verdict: verified" in text:
        return "verified"
    return ""


_FINAL_ANSWER_LOCAL_PATH_RE = re.compile(
    r"""
    (?<![\w.-])
    (?:
        /home/[A-Za-z0-9_.-]+
        | /Users/[A-Za-z0-9_.-]+
        | /mnt/[A-Za-z]
    )
    (?:/[^\s`'"<>|;:,)]*)*
    """,
    re.VERBOSE,
)


def _redact_local_paths_for_final_answer(text: str) -> str:
    sanitized = str(text or "")
    sanitized = _FINAL_ANSWER_LOCAL_PATH_RE.sub("[local path]", sanitized)
    return re.sub(r"\s{2,}", " ", sanitized).strip()


def _compose_latest_status_answer(*, query: str, action: dict, opened: list[dict]) -> str | None:
    selected_anchor = action.get("selected_anchor") if isinstance(action.get("selected_anchor"), dict) else {}
    readiness = action.get("answer_readiness") if isinstance(action.get("answer_readiness"), dict) else {}
    scanner_ready = readiness.get("scanner_coverage_answer_allowed", readiness.get("answer_allowed"))
    if not selected_anchor or scanner_ready is not True:
        return None
    anchor_text = str(selected_anchor.get("snippet") or selected_anchor.get("text") or "").strip()
    anchor_time = str(selected_anchor.get("timestamp_utc") or selected_anchor.get("local_timestamp") or "").strip()
    anchor_ref = str(selected_anchor.get("source_ref") or selected_anchor.get("openable_target") or "").strip()
    confidence = str(
        selected_anchor.get("confidence")
        or selected_anchor.get("decision")
        or selected_anchor.get("classification")
        or readiness.get("status")
        or ""
    ).strip()
    boundary = str(
        selected_anchor.get("anchor_scope")
        or selected_anchor.get("context_boundary")
        or (action.get("selected_route") or {}).get("route_family")
        or "latest_status"
    ).strip()
    primary_summaries = [
        str(item.get("summary") or "").strip()
        for item in opened
        if item.get("summary") and _latest_status_opened_is_primary(item)
    ]
    if not primary_summaries:
        primary_summaries = [str(item.get("summary") or "").strip() for item in opened if item.get("summary")]
    if not primary_summaries:
        return None
    source_tiers = action.get("source_tiers") if isinstance(action.get("source_tiers"), dict) else {}
    secondary_refs = _latest_status_secondary_ref_labels(source_tiers.get("required_secondary") or [])
    commits = _latest_status_commit_labels(((action.get("work_products") or {}).get("commits") or []))
    answer_brief = action.get("answer_brief")
    brief_text = _latest_status_brief_text(answer_brief)
    session_activity = action.get("session_activity") if isinstance(action.get("session_activity"), dict) else {}
    session_summary = session_activity.get("summary") if isinstance(session_activity.get("summary"), dict) else {}
    activity_text = _latest_status_activity_text(session_summary)
    pieces = [
        "Selected anchor: "
        + _short_join(
            [
                f'wording="{anchor_text}"' if anchor_text else "",
                f"timestamp={anchor_time}" if anchor_time else "",
                f"source_ref={anchor_ref}" if anchor_ref else "",
                f"confidence={confidence}" if confidence else "",
                f"boundary={boundary}" if boundary else "",
            ],
            limit=520,
        )
        + ".",
        f"Work after that anchor from opened required primary refs: {_short_join(primary_summaries[:4], limit=720)}",
    ]
    if brief_text:
        pieces.append(f"Status-anchor brief: {brief_text}")
    if activity_text:
        pieces.append(f"Raw activity summary: {activity_text}")
    if commits:
        pieces.append(f"Secondary commit refs: {_short_join(commits[:4], limit=420)}")
    if secondary_refs:
        pieces.append(f"Secondary task/report/review refs: {_short_join(secondary_refs[:4], limit=420)}")
    pieces.append(
        "Boundary: this is a latest-status answer from selected-anchor and required-primary proof only, "
        "not an all-history completeness claim."
    )
    return " ".join(part for part in pieces if part)


def _latest_status_opened_is_primary(item: dict) -> bool:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    return str(ref.get("source_tier") or "") == "required_primary" or bool(ref.get("answer_use_required"))


def _latest_status_brief_text(answer_brief: object) -> str:
    if isinstance(answer_brief, str):
        return answer_brief.strip()
    if isinstance(answer_brief, list):
        values = [str(value).strip() for value in answer_brief if str(value).strip()]
        return _short_join(values[:3], limit=520)
    if not isinstance(answer_brief, dict):
        return ""
    for key in ("summary", "brief", "answer", "status_summary"):
        value = answer_brief.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    values = [
        str(value).strip()
        for value in answer_brief.values()
        if isinstance(value, (str, int, float)) and str(value).strip()
    ]
    return _short_join(values[:4], limit=420)


def _latest_status_activity_text(summary: dict) -> str:
    fields = [
        ("session_files", summary.get("session_file_count")),
        ("records_after_anchor", summary.get("records_after_anchor")),
        ("spawned_session_calls", summary.get("spawned_session_call_count")),
    ]
    parts = [f"{label}={value}" for label, value in fields if value not in (None, "")]
    return ", ".join(parts)


def _latest_status_secondary_ref_labels(reads: list[dict]) -> list[str]:
    labels = []
    for read in reads:
        if not isinstance(read, dict):
            continue
        label = str(read.get("source_ref") or read.get("openable_target") or read.get("path") or "").strip()
        reason = str(read.get("reason") or read.get("source_role_label") or "").strip()
        if label and reason:
            labels.append(f"{label} ({reason})")
        elif label:
            labels.append(label)
    return labels


def _latest_status_commit_labels(commits: list[dict]) -> list[str]:
    labels = []
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        label = str(commit.get("commit") or commit.get("hash") or commit.get("source_ref") or "").strip()
        subject = str(commit.get("subject") or commit.get("summary") or commit.get("message") or "").strip()
        if label and subject:
            labels.append(f"{label} {subject}")
        elif label:
            labels.append(label)
        elif subject:
            labels.append(subject)
    return labels


def _latest_status_anchor_choices(action: dict) -> list[dict]:
    candidates: list[dict] = []
    anchor_decision = action.get("anchor_decision")
    if isinstance(anchor_decision, dict):
        candidates.extend(
            item
            for item in anchor_decision.get("rejected_anchor_alternatives") or []
            if isinstance(item, dict)
        )
        chronology = anchor_decision.get("current_user_message_chronology")
        if isinstance(chronology, dict):
            candidates.extend(
                item
                for item in chronology.get("rejected_anchor_alternatives") or []
                if isinstance(item, dict)
            )
    candidates.extend(item for item in action.get("anchor_candidates") or [] if isinstance(item, dict))
    shortlist = action.get("parent_main_anchor_shortlist")
    if isinstance(shortlist, dict):
        for key in ("candidates", "shortlist", "top_candidates", "canonical_candidates"):
            values = shortlist.get(key)
            if isinstance(values, list):
                candidates.extend(item for item in values if isinstance(item, dict))
    for read in action.get("read") or []:
        if isinstance(read, dict) and (
            read.get("parent_main_anchor_recovery_candidate") or read.get("prior_request_occurrence_candidate")
        ):
            candidates.append(read)
    choices: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item.get("source_ref") or item.get("openable_target") or item.get("snippet") or item)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            {
                "wording": item.get("snippet") or item.get("text") or item.get("reason"),
                "timestamp_utc": item.get("timestamp_utc"),
                "source_ref": item.get("source_ref"),
                "openable_target": item.get("openable_target"),
                "confidence": item.get("confidence") or item.get("decision") or item.get("source_role_label"),
                "decision": item.get("decision"),
                "classification": item.get("classification"),
                "source_scope": item.get("source_scope"),
                "ambiguity_label": item.get("ambiguity_label"),
            }
        )
        if len(choices) >= 3:
            break
    return choices


def _compose_semantic_wrapup_corrections_answer(*, query: str, action: dict, opened: list[dict]) -> str | None:
    failure_modes = [item for item in action.get("failure_modes") or [] if isinstance(item, dict)]
    suggestions = {
        str(item.get("failure_mode") or ""): item
        for item in action.get("fix_suggestions") or []
        if isinstance(item, dict)
    }
    if not failure_modes or not suggestions:
        return None
    opened_by_ref = _semantic_wrapup_opened_by_source_ref(opened)
    clusters: list[str] = []
    for mode in failure_modes[:5]:
        key = str(mode.get("failure_mode") or "")
        suggestion = suggestions.get(key) or {}
        refs = _semantic_wrapup_cluster_source_refs(mode=mode, suggestion=suggestion)
        proof_summaries = [
            opened_by_ref[ref_key]
            for ref in refs
            for ref_key in [_semantic_wrapup_ref_key(ref)]
            if ref_key in opened_by_ref
        ][:2]
        correction = _semantic_wrapup_human_correction(mode=mode, suggestion=suggestion)
        family = _semantic_wrapup_affected_family(mode=mode, suggestion=suggestion)
        fix = str(
            suggestion.get("doable_suggested_fix")
            or suggestion.get("proposed_action")
            or mode.get("why_it_matters")
            or "Preserve this correction as a source-backed product-fix candidate."
        ).strip()
        ref_text = f"{len(refs)} structured source ref(s) in the semantic_wrapup payload"
        proof_text = (
            "required proof refs were opened; inspect refs_used for exact source locations"
            if proof_summaries
            else "proof is available from required_primary_proof refs"
        )
        clusters.append(
            f"{len(clusters) + 1}. Correction: {correction} "
            f"Affects: {family}. Likely product fix: {fix} "
            f"Sources: {ref_text}. Proof: {proof_text}"
        )
    if not clusters:
        return None
    scope = "historical" if "histor" in query.casefold() else "current-session"
    return (
        f"Semantic-wrapup found {len(clusters)} source-backed {scope} correction cluster(s). "
        "These are product-fix candidates, not auto-applied tasks or all-history completeness claims. "
        + " ".join(clusters)
    )


def _semantic_wrapup_opened_by_source_ref(opened: list[dict]) -> dict[str, str]:
    by_ref: dict[str, str] = {}
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
        key = _semantic_wrapup_ref_key(source_ref)
        summary = str(item.get("summary") or "").strip()
        if key and summary and key not in by_ref:
            by_ref[key] = summary
    return by_ref


def _semantic_wrapup_cluster_source_refs(*, mode: dict, suggestion: dict) -> list[dict]:
    refs: list[dict] = []
    for group in (
        mode.get("source_refs"),
        (mode.get("cause_chain") or {}).get("evidence_refs") if isinstance(mode.get("cause_chain"), dict) else None,
        suggestion.get("source_refs"),
        suggestion.get("evidence_refs"),
    ):
        for item in group if isinstance(group, list) else []:
            if isinstance(item, dict) and _semantic_wrapup_ref_key(item):
                refs.append(item)
    deduped: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        key = _semantic_wrapup_ref_key(ref)
        if key and key not in seen:
            seen.add(key)
            deduped.append(ref)
    return deduped


def _semantic_wrapup_human_correction(*, mode: dict, suggestion: dict) -> str:
    examples = mode.get("examples") or suggestion.get("examples") or []
    for example in examples:
        if isinstance(example, dict) and str(example.get("snippet") or "").strip():
            snippet = str(example["snippet"]).strip()
            if _semantic_wrapup_snippet_has_raw_local_path(snippet):
                continue
            return _short_join([snippet], limit=180)
    title = str(mode.get("title") or mode.get("failure_mode") or "Repeated correction").strip()
    return title.replace("_", " ")


def _semantic_wrapup_snippet_has_raw_local_path(snippet: str) -> bool:
    text = snippet.casefold()
    return any(
        marker in text
        for marker in (
            "/home/",
            "/users/",
            "/tmp/",
            "worktree:",
            "branch:",
            "isolated implementer",
            "work on exactly one task",
            "product-code changes must stay",
        )
    )


def _semantic_wrapup_affected_family(*, mode: dict, suggestion: dict) -> str:
    failure_mode = str(mode.get("failure_mode") or suggestion.get("failure_mode") or "").casefold()
    issue_axis = str(mode.get("issue_axis") or suggestion.get("issue_axis") or "").casefold()
    fix_type = str(suggestion.get("fix_type") or "").casefold()
    text = " ".join([failure_mode, issue_axis, fix_type])
    if "false_user_quote" in text or "misattribution" in text:
        return "When did I say X? / remembered-user-wording proof"
    if "retrieval" in text or "raw_trace" in text or "action-proof" in text:
        return "Did the agent actually do X or only claim it? / semantic-debug action proof"
    if "pre_compaction" in text or "handoff" in text or "memory" in text:
        return "What happened since my last real instruction / work-episode handoff"
    if "router" in text:
        return "Which work episode/source route proves this? / ask routing"
    if "raw_local" in text or "source" in text or "privacy" in text:
        return "Which source session/task/review/commit proves this? / source refs"
    if "agent_rules" in text or "skill" in text:
        return "What did I keep correcting? / semantic-wrapup agent-rule fixes"
    return "What did I keep correcting? / semantic-wrapup correction mining"


def _semantic_wrapup_ref_key(ref: dict) -> str:
    path = str(ref.get("path") or "").strip()
    line = ref.get("line")
    if not path:
        return ""
    return f"{path}:{line or ''}"


def _semantic_wrapup_source_ref_text(ref: dict) -> str:
    path = str(ref.get("path") or "").strip()
    line = ref.get("line")
    if not path:
        return "(unknown source)"
    return f"{path}:{line}" if line else path


def _compose_named_task_answer(*, query: str, named: dict, opened: list[dict]) -> str | None:
    task_ids = named.get("task_ids") or []
    task_id = str(task_ids[0]) if task_ids else "the named task"
    task_summaries: list[str] = []
    review_summaries: list[str] = []
    other_summaries: list[str] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        proof_surface = ref.get("proof_surface")
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        if proof_surface == "task_file":
            task_summaries.append(summary)
        elif proof_surface == "review_artifact":
            review_summaries.append(summary)
        else:
            other_summaries.append(summary)
    clean_task_summaries = _named_task_current_proof_prose_summaries(task_summaries, task_id=task_id)
    clean_review_summaries = _named_task_current_proof_prose_summaries(review_summaries, task_id=task_id)
    clean_other_summaries = _named_task_current_proof_prose_summaries(other_summaries, task_id=task_id)
    query_lower = query.lower()
    if _branch_work_episode_answer_wanted(query=query, route_family="provenance"):
        branch_answer = _compose_named_task_branch_work_episode_answer(
            query=query,
            task_id=task_id,
            clean_task_summaries=clean_task_summaries,
            clean_review_summaries=clean_review_summaries,
            clean_other_summaries=clean_other_summaries,
            opened=opened,
        )
        if branch_answer:
            return branch_answer
    if _named_task_selection_rationale_answer_wanted(query=query, route_family="semantic_debug"):
        selection_answer = _compose_named_task_selection_rationale_answer(
            task_id=task_id,
            opened=opened,
        )
        if selection_answer:
            return selection_answer
    if _named_task_stopped_before_implementation_answer_wanted(query=query, route_family="semantic_debug"):
        stopped_answer = _compose_named_task_stopped_before_implementation_answer(
            task_id=task_id,
            opened=opened,
        )
        if stopped_answer:
            return stopped_answer
    if _named_task_merge_provenance_answer_wanted(query=query, route_family="provenance"):
        merge_answer = _compose_named_task_merge_provenance_answer(
            task_id=task_id,
            clean_task_summaries=clean_task_summaries,
            clean_review_summaries=clean_review_summaries,
            opened=opened,
        )
        if merge_answer:
            return merge_answer
    review_failure_status_wanted = _named_task_review_failure_status_answer_wanted(
        query=query,
        route_family="provenance",
    )
    review_failure_causal_wanted = _named_task_review_failure_causal_answer_wanted(
        query=query,
        route_family="provenance",
    )
    if review_failure_status_wanted or review_failure_causal_wanted:
        false_premise_answer = _named_task_verified_not_failed_review_answer(
            task_id=task_id,
            task_summaries=task_summaries,
            review_summaries=review_summaries,
        )
        if false_premise_answer:
            return false_premise_answer
    if review_failure_causal_wanted:
        cause = _named_task_review_failure_cause(task_summaries + review_summaries, task_id=task_id)
        outcome = _named_task_review_failure_fix_outcome(review_summaries + task_summaries, task_id=task_id)
        if cause and outcome:
            return (
                f"The current review/fix proof for {task_id} says the review failed because {cause} "
                f"The current fix/rereview outcome proof says {outcome} "
                "Boundary: this answers from current task/review/commit proof only, not an all-history root-cause claim."
            )
    if review_failure_status_wanted:
        return None
    review_verification_wanted = any(
        term in query_lower for term in ("reviewer", "verify", "verified", "only claim")
    )
    if review_verification_wanted:
        if clean_review_summaries:
            return (
                f"The current review proof for {task_id} supports verification: "
                f"{_short_join(clean_review_summaries[:2])}"
                + (f" Task proof: {_short_join(clean_task_summaries[:1])}" if clean_task_summaries else "")
            )
        return f"I found current {task_id} proof refs, but no review-artifact summary was readable."
    pieces = []
    if clean_task_summaries:
        pieces.append(f"task proof: {_short_join(clean_task_summaries[:2])}")
    if clean_review_summaries:
        pieces.append(f"review proof: {_short_join(clean_review_summaries[:2])}")
    if clean_other_summaries:
        pieces.append(f"commit/probe proof: {_short_join(clean_other_summaries[:1])}")
    return f"Current proof for {task_id} shows " + "; ".join(pieces) + "."


def _named_task_merge_provenance_answer_wanted(*, query: str, route_family: str) -> bool:
    if route_family not in {"provenance", "semantic_debug"}:
        return False
    if not _ask_execute_named_task_ids(query):
        return False
    normalized = str(query or "").casefold()
    if not any(term in normalized for term in ("merge", "merged")):
        return False
    return any(term in normalized for term in ("source", "session", "task", "review", "commit", "prove"))


def _compose_named_task_merge_provenance_answer(
    *,
    task_id: str,
    clean_task_summaries: list[str],
    clean_review_summaries: list[str],
    opened: list[dict],
) -> str | None:
    task_proof = _named_task_merge_provenance_role_summary(clean_task_summaries)
    review_proof = _named_task_merge_provenance_role_summary(clean_review_summaries)
    merge_commits = _named_task_merge_commit_hashes(opened, task_id=task_id)
    merge_commit_labels = _named_task_merge_commit_labels(opened, task_id=task_id)
    integration_commit_labels = _named_task_integration_commit_labels(opened, task_id=task_id)
    source_sessions = _named_task_source_session_proof_summaries(opened)
    if not (task_proof or review_proof or merge_commits or integration_commit_labels or source_sessions):
        return None
    pieces = []
    if source_sessions:
        pieces.append(f"source-session proof: {_short_join(source_sessions[:2], limit=260)}")
    else:
        pieces.append("source-session proof: missing/unresolved in the opened proof refs")
    if task_proof:
        pieces.append(f"task artifact proof: {task_proof}")
    else:
        pieces.append("task artifact proof: missing/unresolved in the opened proof refs")
    if review_proof:
        pieces.append(f"review artifact proof: {review_proof}")
    else:
        pieces.append("review artifact proof: missing/unresolved in the opened proof refs")
    if merge_commits:
        pieces.append(f"merge commit proof: {', '.join(merge_commit_labels[:3])}")
    else:
        pieces.append("merge commit proof: missing/unresolved in the opened proof refs")
    if integration_commit_labels:
        pieces.append(f"integration/final-review commit proof: {', '.join(integration_commit_labels[:3])}")
    return (
        f"Current merge provenance proof for {task_id}: "
        + "; ".join(pieces)
        + ". Boundary: task and review artifacts corroborate task/review roles; true merge commits and single-parent integration/final-review commits are separate git proof surfaces."
    )


_NAMED_TASK_MERGE_PROVENANCE_REQUIRED_ROLES = (
    "source_session_proof",
    "task_artifact_proof",
    "review_artifact_proof",
    "merge_commit_proof",
)

_NAMED_TASK_MERGE_PROVENANCE_BASE_BLOCKING_ROLES = (
    "task_artifact_proof",
    "review_artifact_proof",
    "merge_commit_proof",
)


def _named_task_merge_provenance_final_proof_gate(
    *,
    query: str,
    route_family: str,
    opened: list[dict],
) -> dict:
    if not _named_task_merge_provenance_answer_wanted(query=query, route_family=route_family):
        return {"schema_version": 1, "applies": False, "status": "not_applicable"}
    task_ids = _ask_execute_named_task_ids(query)
    task_id = str(task_ids[0]).casefold() if task_ids else ""
    role_evidence = _named_task_merge_provenance_role_evidence(opened, task_id=task_id)
    blocking_roles = list(_NAMED_TASK_MERGE_PROVENANCE_BASE_BLOCKING_ROLES)
    if _named_task_merge_provenance_source_session_wanted(query):
        blocking_roles.insert(0, "source_session_proof")
    missing = [
        role
        for role in _NAMED_TASK_MERGE_PROVENANCE_REQUIRED_ROLES
        if not _named_task_merge_provenance_role_satisfied(role, role_evidence)
    ]
    blocking_missing = [
        role
        for role in blocking_roles
        if not _named_task_merge_provenance_role_satisfied(role, role_evidence)
    ]
    display_missing = list(missing)
    status = "answer_allowed" if not blocking_missing else "missing_required_merge_provenance_roles"
    summary_lines = []
    if blocking_missing:
        summary_lines.append(
            "Named-task merge provenance proof is missing required roles: " + ", ".join(blocking_missing) + "."
        )
        merge_commits = _unique_preserving_order(
            str(commit)
            for item in role_evidence.get("merge_commit_proof") or []
            for commit in item.get("merge_commits") or [item.get("commit") or item.get("target")]
            if str(commit or "").strip()
        )
        if merge_commits:
            summary_lines.append(
                "Merge facet partial proof retained: reachable true merge commit "
                + ", ".join(commit[:12] for commit in merge_commits[:2])
                + "; this does not satisfy source-session proof."
            )
    elif missing:
        summary_lines.append(
            "Named-task merge provenance proof has task/review/merge roles; missing non-fabricated roles: "
            + ", ".join(display_missing)
            + "."
        )
    else:
        summary_lines.append(
            "Named-task merge provenance proof has source-session, task, review, and git integration/merge roles."
        )
    return {
        "schema_version": 1,
        "applies": True,
        "status": status,
        "task_id": task_id,
        "required_roles": list(_NAMED_TASK_MERGE_PROVENANCE_REQUIRED_ROLES),
        "missing_roles": display_missing,
        "blocking_missing_roles": blocking_missing,
        "role_evidence": role_evidence,
        "summary_lines": summary_lines,
        "required_next_proof": (
            "Open a role-valid same-task original-chronology raw JSONL/session ref before claiming "
            "source-session proof; an unopened private handle or later answer/report echo is only a lead."
            if blocking_missing == ["source_session_proof"]
            else (
                "Open same-task task artifact, review artifact, and an actual parent merge commit; also open "
                "same-task source-session proof when the question requests raw/source-session chronology "
                f"for {task_id or 'the named task'} before answering merge provenance."
            )
        ),
        "acceptable_evidence": [
            "original source-session row or raw transcript reference for the task implementation/review/merge episode, not a later answer/report echo",
            "same-task task artifact recording implementation/review/merge state",
            "same-task independent review artifact or review proof",
            "actual parent merge commit hash for the merge facet; single-parent integration/final-review proof remains a separate closeout surface",
        ],
    }


def _named_task_merge_provenance_role_satisfied(role: str, role_evidence: dict) -> bool:
    return bool(role_evidence.get(role))


def _named_task_merge_provenance_source_session_wanted(query: str) -> bool:
    normalized = str(query or "").casefold()
    return any(term in normalized for term in ("source session", "source-session", "raw session", "raw worker"))


def _named_task_merge_provenance_role_evidence(opened: list[dict], *, task_id: str) -> dict:
    evidence: dict[str, list[dict]] = {
        role: [] for role in _NAMED_TASK_MERGE_PROVENANCE_REQUIRED_ROLES
    }
    evidence["integration_commit_proof"] = []
    evidence["source_session_diagnostic_context"] = []
    for item in opened:
        if item.get("status") != "opened":
            continue
        summary = str(item.get("summary") or "")
        if not summary:
            continue
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        target = str(item.get("target") or item.get("path") or ref.get("target") or ref.get("path") or "")
        text = " ".join(
            str(value or "")
            for value in (
                summary,
                target,
                ref.get("kind"),
                ref.get("proof_surface"),
                ref.get("target_source_role"),
                ref.get("proof_role"),
            )
        ).casefold()
        if task_id and _named_task_fragment_mentions_other_task_id(text, task_id):
            continue
        public = _ref_public_summary(item)
        if _opened_named_task_source_session_proof(item):
            public["source_session_original_chronology_reason"] = (
                _named_task_source_session_original_chronology_reason(item)
            )
            evidence["source_session_proof"].append(public)
        elif (reason := _named_task_source_session_rejection_reason(item)) is not None:
            diagnostic = dict(public)
            diagnostic["final_support_allowed"] = False
            diagnostic["diagnostic_only"] = True
            diagnostic["source_session_demotion_reason"] = reason
            evidence["source_session_diagnostic_context"].append(diagnostic)
        if ref.get("proof_surface") == "task_file" or "/tasks/" in f"/{target}".casefold():
            evidence["task_artifact_proof"].append(public)
        if ref.get("proof_surface") == "review_artifact" or "codex/state/reviews" in target.casefold():
            evidence["review_artifact_proof"].append(public)
        if _named_task_merge_commit_hashes([item], task_id=task_id or "the named task"):
            public = dict(public)
            public["merge_commits"] = _named_task_merge_commit_hashes([item], task_id=task_id or "the named task")
            evidence["merge_commit_proof"].append(public)
        if _named_task_integration_commit_hashes([item], task_id=task_id or "the named task"):
            public = dict(public)
            public["integration_commits"] = _named_task_integration_commit_hashes(
                [item], task_id=task_id or "the named task"
            )
            evidence["integration_commit_proof"].append(public)
    return {
        role: values[:_ASK_ANSWER_MAX_REFS]
        for role, values in evidence.items()
        if values
    }


def _named_task_merge_provenance_gate_public_contract(gate: dict) -> dict:
    return {
        "schema_version": gate.get("schema_version"),
        "applies": gate.get("applies"),
        "status": gate.get("status"),
        "task_id": gate.get("task_id"),
        "required_roles": list(gate.get("required_roles") or []),
        "missing_roles": list(gate.get("missing_roles") or []),
        "blocking_missing_roles": list(gate.get("blocking_missing_roles") or []),
        "role_evidence": gate.get("role_evidence") or {},
        "summary_lines": list(gate.get("summary_lines") or []),
        "required_next_proof": gate.get("required_next_proof"),
        "acceptable_evidence": list(gate.get("acceptable_evidence") or []),
    }


def _named_task_merge_provenance_source_role_explanation(next_step: object) -> dict | None:
    if not isinstance(next_step, dict) or next_step.get("status") != "missing_required_merge_provenance_roles":
        return None
    missing = [str(item) for item in next_step.get("missing_roles") or [] if str(item).strip()]
    summary = (
        "Named-task merge provenance stayed fail-closed because opened refs did not cover every requested "
        f"source role; missing {', '.join(missing) if missing else '(unknown)'}."
    )
    return {
        "schema_version": 1,
        "kind": "named_task_merge_provenance_required_roles_missing",
        "summary": summary,
        "summary_lines": [
            summary,
            "Why not final proof: task/review/closeout artifacts can corroborate adjacent workflow state, but a prompt asking for source session/task/review/commit proof needs each requested role.",
            "Needed proof: same-task source-session evidence, task artifact, review artifact, and actual merge-commit proof.",
        ],
        "required_roles": list(next_step.get("required_roles") or []),
        "missing_roles": missing,
        "role_evidence": next_step.get("role_evidence") or {},
        "required_final_proof": next_step.get("required_next_proof"),
        "acceptable_evidence": list(next_step.get("acceptable_evidence") or []),
    }


def _named_task_merge_provenance_role_summary(summaries: list[str]) -> str:
    fragments: list[str] = []
    for fragment in _summary_fragments(summaries):
        normalized = fragment.casefold()
        if any(marker in normalized for marker in ("close commit", "closeout commit", "close jsh")) and not any(
            marker in normalized for marker in ("merge commit", "parent merge", "merge proof")
        ):
            continue
        fragments.append(fragment)
    return _short_join(fragments[:4], limit=360)


def _named_task_source_session_proof_summaries(opened: list[dict]) -> list[str]:
    summaries: list[str] = []
    for item in opened:
        if not _opened_named_task_source_session_proof(item):
            continue
        summary = str(item.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
    return summaries


_NAMED_TASK_SOURCE_SESSION_ROLES = frozenset(
    {"target_historical_raw", "raw_worker_chronology", "source_session_proof"}
)
_NAMED_TASK_SOURCE_SESSION_KINDS = frozenset(
    {
        "jsonl_source_ref",
        "tool_event_source_ref",
        "primary_source_window",
        "source_window",
    }
)


def _named_task_source_session_post_hoc_echo_reason(item: dict) -> str | None:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    if str(ref.get("target_source_role") or "") not in _NAMED_TASK_SOURCE_SESSION_ROLES:
        return None
    selection = (
        ref.get("historical_provenance_selection")
        if isinstance(ref.get("historical_provenance_selection"), dict)
        else {}
    )
    if ref.get("diagnostic_only") is True or selection.get("diagnostic_only") is True:
        return "explicit_diagnostic_source_ref"
    source_role = str(selection.get("source_chronology_role") or "").casefold()
    if "diagnostic" in source_role or "post_hoc" in source_role or "post-hoc" in source_role:
        return "diagnostic_or_post_hoc_chronology_role"

    summary = re.sub(r"\s+", " ", str(item.get("summary") or "").casefold()).strip()
    if not summary:
        return None
    explicit_echo_markers = (
        "answer-key",
        "answer key",
        "prior final answer",
        "previous final answer",
        "prior answer says",
        "previous answer says",
        "retained-source-read output",
        "retained source read output",
        "retained_source_read output",
        "eval answer",
        "evaluation answer",
        "post-hoc report",
        "post hoc report",
        "post-hoc review",
        "post hoc review",
        "post-hoc task summary",
        "post hoc task summary",
        "post-hoc summary",
        "post hoc summary",
        "eval report says",
        "evaluation report says",
        "review summary says",
        "task summary says",
        "report summary says",
        "review artifact says",
        "task artifact says",
        "report artifact says",
        "prior report says",
        "previous report says",
        "current merge provenance proof for",
        "completed-task merge/provenance proof for",
        "named-task merge provenance proof is",
    )
    if any(marker in summary for marker in explicit_echo_markers):
        return "post_hoc_answer_report_or_retained_read_echo"

    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    compact_summary = summary.replace(" ", "")
    output_record = (
        metadata.get("conversation_label") == "tool_action_output"
        or str(metadata.get("payload_type") or "") in {"function_call_output", "custom_tool_call_output"}
        or '"type":"function_call_output"' in compact_summary
        or '"type":"custom_tool_call_output"' in compact_summary
    )
    if output_record and any(
        marker in summary
        for marker in (
            "tool output: # review ",
            "tool output: # jsh-",
            "tool output: ## progress log",
            "tool output: verdict: verified",
            "tool output: status: closed - merged",
            "tool output: status: closed - integrated",
            "# review ",
            "## progress log",
            "verdict: verified",
            "status: closed - merged",
            "status: closed - integrated",
            "retained source read",
            "source-session proof:",
            "implementation evidence",
        )
    ):
        return "post_hoc_task_review_or_report_tool_output"
    return None


_NAMED_TASK_SOURCE_SESSION_READ_ONLY_EXECUTABLES = frozenset(
    {"rg", "grep", "sed", "cat", "find", "ls", "head", "tail", "jq"}
)
_NAMED_TASK_SOURCE_SESSION_READ_ONLY_GIT_SUBCOMMANDS = frozenset({"show", "log", "status"})
_NAMED_TASK_SOURCE_SESSION_SHELL_SEPARATORS = frozenset({"&&", ";", "||", "|"})
_NAMED_TASK_SOURCE_SESSION_SETUP_EXECUTABLES = frozenset(
    {"cd", "export", "source", ".", "pwd", "true", ":", "unset", "set", "umask", "pushd", "popd"}
)
_NAMED_TASK_SOURCE_SESSION_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)


def _named_task_source_session_command_text(call_text: str) -> str:
    normalized = str(call_text or "").strip()
    marker_index = normalized.find("tool call:")
    if marker_index >= 0:
        return normalized[marker_index + len("tool call:") :].strip()
    match = re.search(r"(?:^|\s)tool call [^:]+:\s*(.*)$", normalized)
    if match:
        payload = match.group(1).strip()
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return payload
        if isinstance(decoded, dict):
            return str(decoded.get("cmd") or decoded.get("command") or "").strip()
        return str(decoded).strip() if isinstance(decoded, str) else payload
    match = re.search(r'"(?:command|cmd)"\s*:\s*("(?:\\.|[^"\\])*")', normalized)
    if match:
        try:
            return str(json.loads(match.group(1))).strip()
        except (json.JSONDecodeError, TypeError):
            return ""
    return normalized


def _named_task_source_session_shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _named_task_source_session_effective_argv(argv: list[str]) -> list[str]:
    argv = list(argv)
    while argv and _NAMED_TASK_SOURCE_SESSION_ASSIGNMENT_RE.fullmatch(argv[0]):
        argv = argv[1:]
    while argv:
        executable = os.path.basename(argv[0])
        if executable == "env":
            index = 1
            while index < len(argv):
                token = argv[index]
                if token == "--":
                    index += 1
                    break
                if token in {"-i", "--ignore-environment", "-0", "--null"}:
                    index += 1
                    continue
                if token in {"-u", "--unset"}:
                    if index + 1 >= len(argv):
                        return []
                    index += 2
                    continue
                if token.startswith("--unset="):
                    index += 1
                    continue
                break
            argv = argv[index:]
        elif executable in {"command", "exec", "sudo"}:
            if len(argv) > 1 and argv[1] == "--":
                argv = argv[2:]
            elif len(argv) > 1 and argv[1].startswith("-"):
                return []
            else:
                argv = argv[1:]
        elif executable == "time":
            index = 1
            while index < len(argv) and argv[index] in {"-p", "--portability"}:
                index += 1
            if index < len(argv) and argv[index] == "--":
                index += 1
            elif index < len(argv) and argv[index].startswith("-"):
                return []
            argv = argv[index:]
        elif executable == "nice":
            index = 1
            if index < len(argv) and argv[index] == "-n":
                if index + 1 >= len(argv):
                    return []
                index += 2
            elif index < len(argv) and (
                re.fullmatch(r"-\d+", argv[index]) or argv[index].startswith("--adjustment=")
            ):
                index += 1
            if index < len(argv) and argv[index] == "--":
                index += 1
            elif index < len(argv) and argv[index].startswith("-"):
                return []
            argv = argv[index:]
        elif executable == "timeout":
            index = 1
            if index < len(argv) and argv[index] == "--":
                index += 1
            elif index < len(argv) and argv[index].startswith("-"):
                return []
            if index >= len(argv):
                return []
            argv = argv[index + 1 :]
        elif executable == "stdbuf":
            index = 1
            while index < len(argv) and argv[index].startswith("-"):
                token = argv[index]
                if token in {"-i", "-o", "-e"}:
                    if index + 1 >= len(argv):
                        return []
                    index += 2
                    continue
                if re.fullmatch(r"-[ioe].+", token) or re.fullmatch(
                    r"--(?:input|output|error)=.+", token
                ):
                    index += 1
                    continue
                return []
            argv = argv[index:]
        else:
            return argv
        while argv and _NAMED_TASK_SOURCE_SESSION_ASSIGNMENT_RE.fullmatch(argv[0]):
            argv = argv[1:]
    return []


def _named_task_source_session_unwrapped_command_details(
    command: str,
) -> tuple[list[str], bool]:
    argv = _named_task_source_session_shell_tokens(command)
    shell_wrapped = False
    while argv:
        argv = _named_task_source_session_effective_argv(argv)
        if not argv:
            return [], shell_wrapped
        executable = os.path.basename(argv[0])
        if executable in {"bash", "sh", "zsh"}:
            for index, token in enumerate(argv[1:], start=1):
                if token.startswith("-") and "c" in token[1:] and index + 1 < len(argv):
                    argv = _named_task_source_session_shell_tokens(argv[index + 1])
                    shell_wrapped = True
                    break
            else:
                return argv, shell_wrapped
            continue
        return argv, shell_wrapped
    return [], shell_wrapped


def _named_task_source_session_command_segments(
    argv: list[str],
) -> tuple[list[list[str]], list[str], bool]:
    segments: list[list[str]] = []
    separators: list[str] = []
    current: list[str] = []
    for token in argv:
        if token in _NAMED_TASK_SOURCE_SESSION_SHELL_SEPARATORS:
            if not current:
                return [], [], False
            segments.append(current)
            separators.append(token)
            current = []
            continue
        if token and set(token).issubset({";", "&", "|"}):
            return [], [], False
        current.append(token)
    if not current:
        return [], [], False
    segments.append(current)
    return segments, separators, True


def _named_task_source_session_git_subcommand(argv: list[str]) -> str:
    index = 1
    options_with_values = {"-c", "-C", "--git-dir", "--work-tree", "--namespace"}
    while index < len(argv):
        token = argv[index]
        if token in options_with_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return ""


def _named_task_source_session_reader_argv(argv: list[str]) -> bool:
    argv = _named_task_source_session_effective_argv(argv)
    if not argv:
        return False
    executable = os.path.basename(argv[0])
    if executable in _NAMED_TASK_SOURCE_SESSION_READ_ONLY_EXECUTABLES:
        return True
    return (
        executable == "git"
        and _named_task_source_session_git_subcommand(argv)
        in _NAMED_TASK_SOURCE_SESSION_READ_ONLY_GIT_SUBCOMMANDS
    )


def _named_task_source_session_action_argv(argv: list[str]) -> bool:
    argv = _named_task_source_session_effective_argv(argv)
    if not argv:
        return False
    executable = os.path.basename(argv[0])
    if executable in {"pytest", "compileall", "py_compile", "apply_patch"}:
        return True
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        try:
            module_index = argv.index("-m") + 1
        except (ValueError, IndexError):
            return False
        if module_index >= len(argv):
            return False
        module = argv[module_index]
        if module in {"pytest", "compileall", "py_compile"}:
            return True
        return (
            module == "jsonl_history_search.cli"
            and module_index + 1 < len(argv)
            and argv[module_index + 1] in {"ask", "what-happened-since"}
        )
    if executable == "git":
        subcommand = _named_task_source_session_git_subcommand(argv)
        return subcommand in {"commit", "apply"} or (subcommand == "diff" and "--check" in argv)
    return executable == "jsonl-history-search" and len(argv) > 1 and argv[1] in {
        "ask",
        "what-happened-since",
    }


def _named_task_source_session_setup_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    if all(_NAMED_TASK_SOURCE_SESSION_ASSIGNMENT_RE.fullmatch(token) for token in argv):
        return True
    effective = _named_task_source_session_effective_argv(argv)
    return bool(
        effective
        and os.path.basename(effective[0]) in _NAMED_TASK_SOURCE_SESSION_SETUP_EXECUTABLES
    )


def _named_task_source_session_command_structure(call_text: str) -> str:
    command = _named_task_source_session_command_text(call_text)
    argv, shell_wrapped = _named_task_source_session_unwrapped_command_details(command)
    if not argv:
        return (
            "ambiguous_compound"
            if re.search(r"(?:^|\s)(?:bash|sh|zsh)\b|[;&|]", command)
            else "unavailable"
        )
    segments, separators, valid = _named_task_source_session_command_segments(argv)
    if not valid:
        return "ambiguous_compound"
    if any(_named_task_source_session_reader_argv(segment) for segment in segments):
        return "read_only"
    compound = bool(separators)
    if shell_wrapped or compound:
        if any(separator in {"|", "||"} for separator in separators):
            return "ambiguous_compound"
        action_count = sum(_named_task_source_session_action_argv(segment) for segment in segments)
        safely_reducible = all(
            _named_task_source_session_action_argv(segment)
            or _named_task_source_session_setup_argv(segment)
            for segment in segments
        )
        return "affirmative_action" if safely_reducible and action_count == 1 else "ambiguous_compound"
    return "affirmative_action" if _named_task_source_session_action_argv(segments[0]) else "simple_other"


def _named_task_source_session_read_only_command(call_text: str) -> bool:
    return _named_task_source_session_command_structure(call_text) == "read_only"


def _named_task_source_session_original_chronology_reason(item: dict) -> str | None:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    if str(ref.get("target_source_role") or "") not in _NAMED_TASK_SOURCE_SESSION_ROLES:
        return None
    if _named_task_source_session_post_hoc_echo_reason(item) is not None:
        return None
    metadata = item.get("record_metadata") if isinstance(item.get("record_metadata"), dict) else {}
    if metadata.get("conversation_label") == "source_session_metadata":
        return None
    summary = re.sub(r"\s+", " ", str(item.get("summary") or "").casefold()).strip()
    if not summary:
        return None

    payload_type = str(metadata.get("payload_type") or "").casefold()
    item_type = str(metadata.get("item_type") or "").casefold()
    conversation_label = str(metadata.get("conversation_label") or "").casefold()
    structured_tool_record = (
        item_type == "command_execution"
        or conversation_label == "tool_action_output"
        or payload_type
        in {"function_call", "function_call_output", "custom_tool_call", "custom_tool_call_output"}
        or summary.startswith("tool call:")
        or summary.startswith("tool output:")
        or '"type":"command_execution"' in summary.replace(" ", "")
    )
    if structured_tool_record:
        call_text, separator, output_text = summary.partition("tool output:")
        call_text = call_text.rstrip()
        if separator and call_text.endswith("|"):
            call_text = call_text[:-1].rstrip()
        command_structure = _named_task_source_session_command_structure(call_text)
        output_record = payload_type in {"function_call_output", "custom_tool_call_output"} or (
            item_type != "command_execution"
            and bool(re.search(r"(?:^|\s)tool output:\s*", summary))
        )
        clear_output_record = output_record and "tool call" not in call_text
        if command_structure in {"read_only", "ambiguous_compound"}:
            return None
        if command_structure == "simple_other" and not clear_output_record:
            return None
        if command_structure == "affirmative_action":
            return "structured_change_verification_or_runtime_command"
        result_text = output_text if separator else summary
        has_execution_result = any(
            marker in result_text
            for marker in (
                "report_output_unavailable",
                "process exited with code 0",
                '"exit_code":0',
                '"exit_code": 0',
                "tests passed",
                "test passed",
                " passed in ",
                "answer status:",
                "execution status:",
            )
        )
        associated_call_reason = str(
            metadata.get("associated_call_original_chronology_reason") or ""
        )
        if (
            has_execution_result
            and clear_output_record
            and associated_call_reason
            == "structured_change_verification_or_runtime_command"
        ):
            return "structured_tool_execution_result_with_affirmative_call"
        return None
    return None


def _named_task_source_session_rejection_reason(item: dict) -> str | None:
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    if str(ref.get("target_source_role") or "") not in _NAMED_TASK_SOURCE_SESSION_ROLES:
        return None
    post_hoc_reason = _named_task_source_session_post_hoc_echo_reason(item)
    if post_hoc_reason is not None:
        return post_hoc_reason
    if _named_task_source_session_original_chronology_reason(item) is None:
        return "ambiguous_raw_record_without_original_chronology_signal"
    return None


def _opened_named_task_source_session_proof(item: dict) -> bool:
    if item.get("status") != "opened" or not str(item.get("summary") or "").strip():
        return False
    ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
    if ref.get("final_support_allowed") is False or ref.get("diagnostic_only") is True:
        return False
    if _named_task_source_session_post_hoc_echo_reason(item) is not None:
        return False
    if _named_task_source_session_original_chronology_reason(item) is None:
        return False
    target_role = str(ref.get("target_source_role") or "")
    if target_role not in _NAMED_TASK_SOURCE_SESSION_ROLES:
        return False
    kind = str(ref.get("kind") or "")
    source_ref = ref.get("source_ref") if isinstance(ref.get("source_ref"), dict) else {}
    source_path = str(
        source_ref.get("path")
        or item.get("path")
        or item.get("target")
        or ref.get("path")
        or ref.get("target")
        or ""
    ).casefold()
    raw_file_surface = bool(re.search(r"\.(?:jsonl|json)(?::\d+)?$", source_path))
    return raw_file_surface and (kind in _NAMED_TASK_SOURCE_SESSION_KINDS or source_path.endswith(".jsonl"))


def _named_task_merge_commit_hashes(opened: list[dict], *, task_id: str) -> list[str]:
    commits: list[str] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        proof_surface = str(ref.get("proof_surface") or "")
        if proof_surface == "merge_commit":
            direct_commit = str(ref.get("commit") or item.get("target") or "").strip().lower()
            if re.fullmatch(r"[0-9a-f]{7,40}", direct_commit):
                if direct_commit not in commits:
                    commits.append(direct_commit)
                continue
        if not _opened_named_task_source_session_proof(item):
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        for fragment in _summary_fragments([summary]):
            if _named_task_fragment_mentions_other_task_id(fragment, task_id):
                continue
            if not _named_task_fragment_is_merge_commit_proof(fragment):
                continue
            for commit in _named_task_merge_commit_hashes_from_fragment(fragment):
                normalized = commit.lower()
                if normalized not in commits:
                    commits.append(normalized)
    return commits


def _named_task_merge_commit_labels(opened: list[dict], *, task_id: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        proof_surface = str(ref.get("proof_surface") or "")
        if proof_surface != "merge_commit":
            continue
        direct_commit = str(ref.get("commit") or item.get("target") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,40}", direct_commit):
            continue
        if task_id and _named_task_fragment_mentions_other_task_id(_target_episode_ref_match_text(item), task_id):
            continue
        if direct_commit in seen:
            continue
        seen.add(direct_commit)
        author = str(ref.get("author") or "").strip()
        label = direct_commit[:12]
        if author:
            label += f" (author: {author})"
        labels.append(label)
    for commit in _named_task_merge_commit_hashes(opened, task_id=task_id):
        if commit in seen:
            continue
        seen.add(commit)
        labels.append(commit[:12])
    return labels


def _named_task_integration_commit_hashes(opened: list[dict], *, task_id: str) -> list[str]:
    commits: list[str] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        if str(ref.get("proof_surface") or "") != "integration_final_review_commit":
            continue
        if task_id and _named_task_fragment_mentions_other_task_id(_target_episode_ref_match_text(item), task_id):
            continue
        direct_commit = str(ref.get("commit") or item.get("target") or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{7,40}", direct_commit) and direct_commit not in commits:
            commits.append(direct_commit)
    return commits


def _named_task_integration_commit_labels(opened: list[dict], *, task_id: str) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        if str(ref.get("proof_surface") or "") != "integration_final_review_commit":
            continue
        direct_commit = str(ref.get("commit") or item.get("target") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{7,40}", direct_commit):
            continue
        if task_id and _named_task_fragment_mentions_other_task_id(_target_episode_ref_match_text(item), task_id):
            continue
        if direct_commit in seen:
            continue
        seen.add(direct_commit)
        label = f"{direct_commit[:12]} (single-parent"
        author = str(ref.get("author") or "").strip()
        committer = str(ref.get("committer") or "").strip()
        subject = str(ref.get("subject") or "").strip()
        if author:
            label += f"; author: {author}"
        if committer:
            label += f"; committer: {committer}"
        if subject:
            label += f"; subject: {subject}"
        label += ")"
        labels.append(label)
    return labels


def _named_task_merge_commit_hashes_from_fragment(fragment: str) -> list[str]:
    text = str(fragment or "")
    hashes: list[str] = []
    patterns = (
        r"(?:parent\s+)?merge\s+commit\s*[:` ]+\s*`?([0-9a-f]{7,40})\b",
        r"merge\s+proof\s*[:` ]+\s*`?([0-9a-f]{7,40})\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            commit = match.group(1).lower()
            if commit not in hashes:
                hashes.append(commit)
    return hashes


def _named_task_fragment_is_merge_commit_proof(fragment: str) -> bool:
    normalized = str(fragment or "").casefold()
    if not any(
        marker in normalized
        for marker in (
            "merge commit",
            "parent merge",
            "merge proof",
            "merged",
            "integrated on parent",
        )
    ):
        return False
    if any(marker in normalized for marker in ("close commit", "closeout commit", "close jsh")) and not any(
        marker in normalized for marker in ("merge commit", "parent merge", "merge proof")
    ):
        return False
    return True


def _compose_named_task_branch_work_episode_answer(
    *,
    query: str,
    task_id: str,
    clean_task_summaries: list[str],
    clean_review_summaries: list[str],
    clean_other_summaries: list[str],
    opened: list[dict],
) -> str | None:
    pieces = []
    if clean_task_summaries:
        pieces.append(f"task proof: {_short_join(clean_task_summaries[:2], limit=420)}")
    if clean_review_summaries:
        pieces.append(f"review proof: {_short_join(clean_review_summaries[:2], limit=420)}")
    if clean_other_summaries:
        pieces.append(f"commit/probe proof: {_short_join(clean_other_summaries[:1], limit=260)}")
    if not pieces:
        fallback = _work_episode_answer_prose_summaries(opened)
        if not fallback:
            return None
        pieces.append(_short_join(fallback[:3], limit=520))
    branch = _branch_work_episode_label(query)
    return (
        f"Branch {branch} maps to {task_id}. Current task/review/commit proof says "
        f"{'; '.join(pieces)}. "
        "Boundary: this branch/work-episode answer is from current proof refs, with raw refs and proof_summary "
        "kept for inspection, not from pasted raw JSONL event payload."
    )


def _named_task_current_proof_prose_summaries(summaries: list[str], *, task_id: str) -> list[str]:
    cleaned: list[str] = []
    for summary in summaries:
        fragments = []
        for fragment in _summary_fragments([summary]):
            prose = _named_task_current_proof_prose_fragment(fragment, task_id=task_id)
            if prose:
                fragments.append(prose)
        if fragments:
            cleaned.append(_short_join(fragments[:5], limit=520))
    return cleaned


def _named_task_current_proof_prose_fragment(fragment: str, *, task_id: str) -> str:
    text = str(fragment or "").strip()
    if not text:
        return ""
    text = text.lstrip("-* ").strip()
    normalized = text.casefold()
    if _named_task_fragment_mentions_other_task_id(text, task_id):
        return ""
    if any(
        marker in normalized
        for marker in (
            '"timestamp"',
            '"payload"',
            '"type":"event_msg"',
            '"type": "event_msg"',
            "tool output: chunk id",
            "raw prior tool-output",
            "raw tool-output",
            "raw tool output",
            "rollout-2026-",
            "event_msg",
            "diagnostic/section-paste",
            "diagnostic marker",
        )
    ):
        return ""
    preservation_terms = (
        "review-proof",
        "failed-review",
        "expectation-mismatch",
        "latest-status",
        "remembered-user",
    )
    if sum(1 for term in preservation_terms if term in normalized) >= 2:
        return ""
    label_match = re.match(r"^`?(context|proof)`?:\s*(.*)$", text, flags=re.IGNORECASE)
    if label_match:
        label = label_match.group(1).casefold()
        rest = label_match.group(2).strip().strip("` .")
        if not rest:
            return ""
        if label == "proof":
            return f"proof noted {rest}"
        return rest
    return text


def _compose_named_task_selection_rationale_answer(*, task_id: str, opened: list[dict]) -> str | None:
    selection_summaries: list[str] = []
    proof_ref_labels: list[str] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        summary = str(item.get("summary") or "").strip()
        if ref.get("proof_surface") in {"task_selection_rationale", "pipeline_selection_result"}:
            if summary:
                selection_summaries.append(summary)
            label = _named_task_selection_proof_ref_label(ref)
            if label and label not in proof_ref_labels:
                proof_ref_labels.append(label)
    if not selection_summaries:
        return None
    reason_source = " ".join(selection_summaries)
    reason = _named_task_selection_reason_clause(reason_source, task_id=task_id)
    proof_refs = _short_join(proof_ref_labels[:3], limit=220) or "current task/state selection proof"
    return (
        f"Current selection proof for {task_id} says it was selected because {reason} "
        f"Refs used: {proof_refs}."
        + " Boundary: this answers from current task/state selection proof, not raw tool-output transcript text or an all-history claim."
    )


def _compose_named_task_stopped_before_implementation_answer(*, task_id: str, opened: list[dict]) -> str | None:
    proof_summaries: list[str] = []
    proof_ref_labels: list[str] = []
    rejected: list[str] = []
    for item in opened:
        ref = item.get("ref") if isinstance(item.get("ref"), dict) else {}
        summary = str(item.get("summary") or "").strip()
        if ref.get("proof_surface") != "stopped_before_implementation_causal_proof":
            continue
        if _named_task_stopped_before_implementation_summary_supported(summary, task_id=task_id):
            proof_summaries.append(summary)
            target = str(item.get("target") or ref.get("target") or "").strip()
            if target:
                proof_ref_labels.append(target)
        else:
            rejected.append(str(item.get("target") or ref.get("target") or "unlabeled ref"))
    if not proof_summaries:
        return None
    cause = _named_task_stopped_before_implementation_cause_clause(
        _short_join(proof_summaries[:2], limit=900),
        task_id=task_id,
    )
    refs = _short_join(proof_ref_labels[:3], limit=240) or "stable local task/state/report artifact proof"
    boundary = (
        "Boundary: this answers only the named stopped-before-implementation episode from stable "
        "local task/state/report artifacts; current-run transcript echoes, nested read-more output, "
        "slice prompts, and post-hoc diagnostics are not final support."
    )
    if rejected:
        boundary += f" Rejected unsupported stopped-task refs: {_short_join(rejected[:2], limit=160)}."
    return f"{task_id} was stopped before implementation because {cause} Refs used: {refs}. {boundary}"


def _named_task_stopped_before_implementation_summary_supported(summary: str, *, task_id: str) -> bool:
    return bool(_stopped_before_implementation_support_terms(summary, task_id=task_id))


def _named_task_stopped_before_implementation_cause_clause(proof_text: str, *, task_id: str) -> str:
    normalized = str(proof_text or "").casefold()
    cause_parts: list[str] = []
    if "mostly admin/docs" in normalized:
        cause_parts.append("it was mostly admin/docs work")
    elif "admin/docs" in normalized:
        cause_parts.append("it was admin/docs work")
    if "currentness" in normalized:
        cause_parts.append("currentness work")
    if "rather than a real product fix" in normalized or "not a real product fix" in normalized:
        cause_parts.append("rather than a real product fix")
    if not cause_parts and "false start" in normalized:
        cause_parts.append("it was a false start")
    cleanup_parts: list[str] = []
    if "empty worktree" in normalized or "empty branch" in normalized:
        cleanup_parts.append("the empty branch/worktree were removed")
    elif "removed" in normalized:
        cleanup_parts.append("the false-start artifacts were removed")
    if "reverted" in normalized:
        cleanup_parts.append("the selection/handoff commits were reverted")
    cause = _short_join(cause_parts, limit=260) or _short_join([proof_text], limit=360)
    cleanup = _short_join(cleanup_parts, limit=220)
    if cleanup:
        return f"{cause}; {cleanup}."
    return f"{cause}."


def _named_task_selection_proof_ref_label(ref: dict) -> str:
    surface = str(ref.get("proof_surface") or "")
    target = str(ref.get("target") or ref.get("path") or "").strip()
    if surface == "task_selection_rationale":
        return f"task selection rationale ({target})" if target else "task selection rationale"
    if surface == "pipeline_selection_result":
        return f"pipeline selection result ({target})" if target else "pipeline selection result"
    return target


def _named_task_selection_reason_clause(proof_text: str, *, task_id: str) -> str:
    normalized = proof_text.casefold()
    false_positive = any(term in normalized for term in ("false-positive", "false positive", "false causal"))
    verified = "verified" in normalized
    failed_review_prompt_match = re.search(r"why did\s+JSH-\d+\s+fail review\?", proof_text, flags=re.IGNORECASE)
    failed_review_prompt = failed_review_prompt_match.group(0) if failed_review_prompt_match else ""
    if false_positive and verified and failed_review_prompt:
        return (
            f"fresh installed probes showed `{failed_review_prompt}` was being answered as a false-positive "
            "failed-review causal premise even though current review proof was `VERIFIED`, not failed."
        )
    if false_positive and verified:
        return (
            "fresh installed probes found a false-positive failed-review causal answer even though current "
            "review proof was `VERIFIED`, not failed."
        )
    selection_prompt_match = re.search(
        r"why did\s+JSH-\d+\s+(?:get|got)\s+selected\?",
        proof_text,
        flags=re.IGNORECASE,
    )
    selection_prompt = selection_prompt_match.group(0) if selection_prompt_match else ""
    raw_tool_output_issue = (
        "raw prior tool-output" in normalized
        or "raw tool-output" in normalized
        or "raw tool output" in normalized
    )
    clean_task_state_proof = (
        "clean task/state" in normalized
        or "task/state proof" in normalized
        or "task/state selection rationale" in normalized
    )
    if selection_prompt and raw_tool_output_issue and clean_task_state_proof:
        return (
            f"fresh installed probes showed `{selection_prompt}` was answering from raw prior tool-output "
            "text instead of clean task/state selection proof."
        )
    return _short_join([proof_text], limit=420)


def _named_task_verified_not_failed_review_answer(
    *,
    task_id: str,
    task_summaries: list[str],
    review_summaries: list[str],
) -> str | None:
    review_fragments = [
        fragment
        for fragment in _summary_fragments(review_summaries)
        if not _named_task_fragment_mentions_other_task_id(fragment, task_id)
    ]
    relevant_fragments = [
        fragment
        for fragment in _summary_fragments(review_summaries + task_summaries)
        if not _named_task_fragment_mentions_other_task_id(fragment, task_id)
    ]
    verified_review = [
        fragment
        for fragment in review_fragments
        if _named_task_fragment_has_verified_review_verdict(fragment)
    ]
    if not verified_review:
        return None
    if any(_named_task_fragment_has_failed_review_proof(fragment) for fragment in relevant_fragments):
        return None
    if any(_named_task_fragment_has_fix_rereview_proof(fragment) for fragment in relevant_fragments):
        return None
    proof = _short_join(verified_review[:2])
    task_proof = _short_join(
        [
            fragment
            for fragment in _summary_fragments(task_summaries)
            if not _named_task_fragment_mentions_other_task_id(fragment, task_id)
        ][:2]
    )
    return (
        f"The current review proof for {task_id} does not support the failed-review premise; "
        f"it shows the review was VERIFIED, not failed: {proof}"
        + (f" Task proof: {task_proof}" if task_proof else "")
        + " Boundary: this answers from current task/review/commit proof only, not an all-history claim that no review ever failed."
    )


def _named_task_fragment_mentions_other_task_id(fragment: str, task_id: str) -> bool:
    task_id_normalized = str(task_id or "").casefold()
    mentioned_ids = {match.group(0).casefold() for match in _ASK_EXECUTE_TASK_ID_RE.finditer(fragment or "")}
    return bool(mentioned_ids and task_id_normalized not in mentioned_ids)


def _named_task_fragment_has_verified_review_verdict(fragment: str) -> bool:
    normalized = str(fragment or "").casefold()
    return "verdict" in normalized and "verified" in normalized


def _named_task_fragment_has_failed_review_proof(fragment: str) -> bool:
    normalized = str(fragment or "").casefold()
    if _named_task_fragment_negates_failed_review_proof(fragment):
        return False
    if "verdict" in normalized and "failed" in normalized:
        return True
    if "review:" in normalized and "failed because" in normalized:
        return True
    if "review artifact" in normalized and "failed" in normalized and "because" in normalized:
        return True
    return False


def _named_task_fragment_has_fix_rereview_proof(fragment: str) -> bool:
    normalized = str(fragment or "").casefold()
    if _named_task_fragment_negates_failed_review_proof(fragment):
        return False
    if any(term in normalized for term in ("fix/rereview", "rereview", "re-review")) and any(
        term in normalized for term in ("verified", "verification", "outcome", "fixed")
    ):
        return True
    if "after fix" in normalized and any(term in normalized for term in ("verified", "fixed")):
        return True
    return False


def _named_task_fragment_negates_failed_review_proof(fragment: str) -> bool:
    normalized = str(fragment or "").casefold()
    if any(
        phrase in normalized
        for phrase in (
            "no failed-review",
            "no current failed-review",
            "no failed review",
            "failed-review/fix proof is absent",
            "does not support the failed-review premise",
            "premise is unsupported",
            "verified, not failed",
            "verified review and no",
            "not failed",
        )
    ):
        return True
    return False


def _named_task_failure_proof_fragments(summaries: list[str], *, task_id: str) -> list[str]:
    fragments: list[str] = []
    for fragment in _summary_fragments(summaries):
        if _named_task_fragment_mentions_other_task_id(fragment, task_id):
            continue
        if _named_task_fragment_negates_failed_review_proof(fragment):
            continue
        fragments.append(fragment)
    return fragments


def _named_task_review_failure_cause(summaries: list[str], *, task_id: str) -> str:
    fragments = _named_task_failure_proof_fragments(summaries, task_id=task_id)
    for fragment in fragments:
        normalized = fragment.casefold()
        if (
            any(term in normalized for term in ("fail", "failed", "failure"))
            and any(term in normalized for term in ("review", "verification"))
            and any(term in normalized for term in ("because", "cause", "overclassified", "violat"))
        ):
            return _clean_named_task_review_failure_cause(fragment)
        if (
            any(term in normalized for term in ("fail", "failed", "failure"))
            and "because" in normalized
            and any(term in normalized for term in ("scope", "boundary", "direct support", "overclassified", "true-support"))
        ):
            return _clean_named_task_review_failure_cause(fragment)
    for fragment in fragments:
        normalized = fragment.casefold()
        if (
            any(term in normalized for term in ("failed-review", "review failure"))
            and any(term in normalized for term in ("because", "boundary", "direct support", "mixed", "overclassified"))
        ):
            return _clean_named_task_review_failure_cause(fragment)
    return ""


def _clean_named_task_review_failure_cause(fragment: str) -> str:
    cleaned = str(fragment or "").strip().lstrip("-* ").strip()
    cleaned = re.sub(r"^review:\s*failed\s+because\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^review artifact\s+was\s+`?failed`?\s+because\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^was\s+`?failed`?\s+because\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _named_task_review_failure_fix_outcome(summaries: list[str], *, task_id: str) -> str:
    fragments = _named_task_failure_proof_fragments(summaries, task_id=task_id)
    verified_verdict = ""
    for fragment in fragments:
        normalized = fragment.casefold()
        if not verified_verdict and "verdict" in normalized and "verified" in normalized:
            verified_verdict = fragment
        if any(term in normalized for term in ("rereview", "re-review", "fix/rereview")) and any(
            term in normalized for term in ("verified", "verification", "outcome")
        ):
            return fragment
        if "fix" in normalized and any(term in normalized for term in ("verified", "verification", "outcome")):
            return fragment
        if "failed-review" in normalized and "fixed" in normalized:
            return f"{verified_verdict}; {fragment}" if verified_verdict else fragment
    for fragment in fragments:
        normalized = fragment.casefold()
        if any(term in normalized for term in ("rereview", "re-review", "after fix", "fix/rereview")):
            return fragment
        if verified_verdict and any(term in normalized for term in ("fixed", "source-inspection-first", "no_evidence_report")):
            return f"{verified_verdict}; {fragment}"
    return ""


def _summary_fragments(summaries: list[str]) -> list[str]:
    fragments: list[str] = []
    for summary in summaries:
        for fragment in str(summary or "").split(" | "):
            cleaned = fragment.strip()
            if cleaned:
                fragments.append(cleaned)
    return fragments


def _short_join(parts: list[str], *, limit: int = 520) -> str:
    text = " | ".join(part.strip() for part in parts if part.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def discovery_payload_from_args(args: argparse.Namespace) -> dict:
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = normalize_local_path(_history_home_from_args(args, provider="codex"))
    claude_home = normalize_local_path(_history_home_from_args(args, provider="claude"))
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=0,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    if not args.root:
        return build_jsonl_candidate_discovery(SourcePlan(limits=limits), explicit_roots_provided=False)
    plan = build_source_plan(
        cwd=cwd,
        codex_home=codex_home,
        claude_home=claude_home,
        roots=[Path(item).expanduser() for item in args.root],
        allow_arbitrary_root=args.allow_arbitrary_root,
        limits=limits,
    )
    return build_jsonl_candidate_discovery(plan, explicit_roots_provided=True)


def _resolve_ask_original_prompt_identity(args: argparse.Namespace) -> dict:
    query = str(args.query or "")
    query_bytes = query.encode("utf-8")
    explicit_source_path = getattr(args, "original_query_file", None)
    environment_source_path = os.environ.get("JSONL_HISTORY_SEARCH_ORIGINAL_PROMPT_FILE")
    source_path_value = explicit_source_path or environment_source_path
    declared_query_origin = getattr(args, "query_origin", None)

    if declared_query_origin == "summary" and not source_path_value:
        raise ValueError(
            "--query-origin summary requires --original-query-file PATH; "
            "a summarized query cannot stand in for the original prompt"
        )

    if source_path_value:
        source_path = Path(source_path_value).expanduser().resolve()
        original_bytes = source_path.read_bytes()
        original_text = original_bytes.decode("utf-8")
        if not original_text.strip():
            raise ValueError("--original-query-file must contain nonblank UTF-8 prompt text")
        matches_query = original_bytes == query_bytes
        if declared_query_origin == "original" and not matches_query:
            raise ValueError(
                "query declared original does not exactly match --original-query-file; "
                "use --query-origin summary for a retrieval summary"
            )
        query_origin = declared_query_origin or ("original" if matches_query else "summary")
        digest = hashlib.sha256(original_bytes).hexdigest()
        source = {
            "kind": "original_query_file",
            "path": str(source_path),
            "byte_count": len(original_bytes),
            "sha256": digest,
            "source_reference": f"sha256:{digest}",
            "attachment_method": (
                "explicit_cli" if explicit_source_path else "free_running_prompt_environment"
            ),
            "content_included_in_route": False,
        }
        status = "original_file_exact_match" if matches_query else "summary_with_original_file"
    else:
        query_origin = declared_query_origin or "undeclared"
        matches_query = True if declared_query_origin == "original" else None
        digest = hashlib.sha256(query_bytes).hexdigest()
        source = {
            "kind": "inline_query",
            "path": None,
            "byte_count": len(query_bytes),
            "sha256": digest,
            "source_reference": f"sha256:{digest}",
            "attachment_method": "inline_query",
            "content_included_in_route": True,
        }
        status = "original_inline" if declared_query_origin == "original" else "inline_origin_undeclared"

    return {
        "schema_version": "ask_original_prompt_identity.v1",
        "status": status,
        "original_wording_contract_satisfied": status != "inline_origin_undeclared",
        "query_origin": query_origin,
        "query_origin_declaration": "explicit" if declared_query_origin else "inferred",
        "query_matches_original_prompt": matches_query,
        "original_prompt_source": source,
        "identity_audit_read_required": False,
        "identity_audit_read_policy": (
            "Do not reopen the original prompt file after routing unless its recorded identity is disputed."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv == ["--help-internal"]:
        print(build_parser(public_help=False).format_help())
        return 0

    command_shape_guidance = _command_shape_guidance(effective_argv)
    if command_shape_guidance:
        print(command_shape_guidance, file=sys.stderr)
        return 2

    args = build_parser().parse_args(effective_argv)
    if args.command == "ask":
        try:
            args.original_prompt_identity = _resolve_ask_original_prompt_identity(args)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"ask original prompt identity error: {exc}", file=sys.stderr)
            return 2
        if not args.query.strip():
            print(
                "ask requires the original user wording; retry: "
                'jsonl-history-search ask --execute "<original-user-wording>" '
                '--cwd "$PWD" --format text',
                file=sys.stderr,
            )
            return 2
        cwd = str(Path(args.cwd).expanduser().resolve())
        payload = build_ask_router_payload(
            args.query,
            cwd=cwd,
            evidence_source=args.evidence_source,
            activation_mode=args.activation_mode,
            audit_depth=args.audit_depth,
        )
        payload["ask_router"]["original_prompt_identity"] = args.original_prompt_identity
        auto_execute_bounded = (
            args.auto_execute_bounded_no_evidence
            and payload["ask_router"].get("route_family") == "bounded_no_evidence"
        )
        if args.answer:
            payload = _run_ask_answer(args, cwd=cwd, route_payload=payload)
        elif args.execute or auto_execute_bounded:
            if auto_execute_bounded and not args.execute:
                args = _args_with_bounded_auto_execute_prompt_scope(args)
            payload = _run_ask_execute(args, cwd=cwd, route_payload=payload)
        print(render(payload, args.format))
        return 0
    if args.command == "retained-source-read":
        payload = build_retained_source_read_payload(
            handle=args.handle,
            task_id=args.task,
            cwd=Path(args.cwd).expanduser().resolve(),
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
        print(render(payload, args.format))
        return 0
    if args.command == "install-codex-skill":
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else default_codex_home()
        try:
            payload = install_codex_skill(codex_home=codex_home)
        except (FileNotFoundError, OSError) as exc:
            print(f"Codex skill install failed: {exc}", file=sys.stderr)
            return 1
        print(render_codex_skill_install(payload, args.format))
        return 0
    if args.command == "install-agent-skill":
        agent_home = Path(args.agent_home).expanduser() if args.agent_home else default_agent_home(args.agent)
        try:
            payload = install_agent_skill(agent=args.agent, agent_home=agent_home)
        except (FileNotFoundError, OSError, ValueError) as exc:
            print(f"{args.agent.title()} skill install failed: {exc}", file=sys.stderr)
            return 1
        print(render_agent_skill_install(payload, args.format))
        return 0
    if args.command == "validate-install":
        payload = validate_install(
            package_root=Path(args.package_root).expanduser() if args.package_root else None,
            codex_home=Path(args.codex_home).expanduser() if args.codex_home else None,
            claude_home=Path(args.claude_home).expanduser() if args.claude_home else None,
        )
        print(render(payload, args.format))
        return 1 if payload["install_validation"]["overall_status"] == "fail" else 0
    if args.command == "diagnose-capabilities":
        payload = build_capability_diagnostic(args)
        if args.report_output:
            payload["capability_diagnostic"]["report_written"] = str(Path(args.report_output).expanduser())
            Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
        print(render(payload, args.format))
        statuses = payload["capability_diagnostic"]["summary"]
        return 1 if statuses.get("blocked", 0) else 0
    if args.command == "discover-jsonl":
        payload = discovery_payload_from_args(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["jsonl_candidate_discovery"]["status"]
        return 1 if status in {"no_roots", "root_rejected"} else 0
    if args.command == "bulk-line-match":
        cwd = Path(args.cwd).expanduser().resolve()
        codex_home = normalize_local_path(_history_home_from_args(args, provider="codex"))
        claude_home = normalize_local_path(_history_home_from_args(args, provider="claude"))
        limits = Limits(
            max_files=args.max_files,
            max_bytes=args.max_bytes,
            max_matches=args.max_matches,
            max_elapsed=args.max_elapsed,
            hard_cap_files=args.hard_cap_files,
            hard_cap_bytes=args.hard_cap_bytes,
        )
        plan = build_source_plan(
            cwd=cwd,
            codex_home=codex_home,
            claude_home=claude_home,
            roots=[Path(item).expanduser() for item in args.root] if args.root else None,
            allow_arbitrary_root=args.allow_arbitrary_root,
            limits=limits,
        )
        try:
            phrases = load_phrase_specs(
                phrases=args.phrase,
                phrases_file=Path(args.phrases_file).expanduser() if args.phrases_file else None,
            )
        except OSError as exc:
            print(f"bulk-line-match failed: {exc}", file=sys.stderr)
            return 2
        payload = build_bulk_line_match_payload(
            plan=plan,
            phrases=phrases,
            case_sensitive=args.case_sensitive,
            snippet_chars=args.snippet_chars,
        )
        print(render(payload, args.format))
        return 2 if payload["bulk_line_match"]["status"] == "blocked" else 0
    if args.command == "metadata-discovery":
        _source_plan, _intent, plan = payload_from_args(args)
        payload = build_generated_metadata_discovery_report(
            plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            min_count=args.min_count,
            max_candidates=args.max_candidates,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["generated_metadata_discovery"]["status"]
        return 1 if status in {"root_rejected"} else 0
    if args.command == "audit-coverage":
        try:
            payload = build_audit_coverage_ledger(audit_coverage_options_from_args(args))
        except ValueError as exc:
            print(f"audit-coverage failed: {exc}", file=sys.stderr)
            return 2
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(
                _render_for_report_output(payload, args.report_output, args.format),
                encoding="utf-8",
            )
        print(rendered)
        return 0 if payload["audit_coverage_ledger"].get("status") != "blocked" else 1
    if args.command == "expansion-family-audit":
        payload = build_expansion_family_audit_report()
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "expansion-family-probe":
        payload = build_expansion_family_probe_report(
            family=args.family,
            manifest_path=args.manifest,
            private_bridge_path=args.private_bridge,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "forensics":
        print(render(build_forensics_payload(args), args.format))
        return 0
    if args.command == "compare-project-lineage":
        payload = build_lineage_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
            rendered = render(_short_lineage_report_written_payload(payload, args.report_output), args.format)
        print(rendered)
        return 0
    if args.command == "skill-audit":
        args.query = args.skill
        source_plan, _intent, plan = payload_from_args(args)
        payload = build_skill_audit_payload(
            skill=args.skill,
            source_plan=source_plan,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            pre_window=args.pre_window,
            post_window=args.post_window,
            correction_window=args.correction_window,
            mask_context=_mask_context_from_args(args),
            output_format=args.format,
            report_output=args.report_output,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
            rendered = render(_short_skill_audit_report_written_payload(payload, args.report_output, args.format), args.format)
        print(rendered)
        return 0
    if args.command == "structured-tool-events":
        source_plan, _intent, plan = payload_from_args(args)
        payload = build_tool_events_payload(
            source_plan=source_plan,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            mask_context=_mask_context_from_args(args),
            event_types=args.event_type,
            skill=args.skill,
            path_globs=args.path,
            output_format=args.format,
            report_output=args.report_output,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
            rendered = render(_short_tool_events_report_written_payload(payload, args.report_output), args.format)
        print(rendered)
        return 0
    if args.command == "workflow-repeat-finder":
        source_plan, _intent, plan = payload_from_args(args)
        payload = build_workflow_repeat_payload(
            source_plan=source_plan,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            query=args.query,
            min_instances=args.min_instances,
            window_records=args.window_records,
            cluster_limit=args.cluster_limit,
            include_suggestions=not args.no_suggestions,
            mask_context=_mask_context_from_args(args),
            output_format=args.format,
            report_output=args.report_output,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
            rendered = render(_short_workflow_repeat_report_written_payload(payload, args.report_output), args.format)
        print(rendered)
        return 0
    if args.command == "semantic-debug":
        source_plan, intent, plan = payload_from_args(args)
        payload = build_semantic_debug_payload(
            args,
            source_plan=source_plan,
            intent=intent,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            mask_context=_mask_context_from_args(args),
            configuration_warnings=_configuration_warnings(source_plan),
        )
        if args.deep_audit_continuation_step:
            payload = project_semantic_debug_continuation_step(payload)
        output_profile_token = start_phase_profile("semantic_debug_output_report")
        rendered = render(payload, args.format)
        report_bytes = 0
        if args.report_output:
            report_path = Path(args.report_output).expanduser()
            report_rendered = _render_for_report_output(payload, args.report_output, args.format)
            report_bytes = len(report_rendered.encode("utf-8", errors="replace"))
            output_profile = finish_phase_profile(
                output_profile_token,
                stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
                report_bytes=report_bytes,
                report_written=True,
            )
            _attach_retrieval_output_report_diagnostics(
                payload,
                report_key="semantic_debug",
                phase="semantic_debug_output_report",
                profile=output_profile,
                output_format=args.format,
                report_output=args.report_output,
                report_written=True,
            )
            report_path.write_text(
                _render_for_report_output(payload, args.report_output, args.format),
                encoding="utf-8",
            )
            packet = payload["semantic_debug"]
            if _semantic_debug_is_scoped_no_evidence(packet):
                payload = _short_semantic_debug_no_evidence_report_written_payload(packet, args.report_output)
            else:
                payload = _short_semantic_debug_report_written_payload(packet, args.report_output)
            rendered = render(payload, args.format)
        else:
            output_profile = finish_phase_profile(
                output_profile_token,
                stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
                report_bytes=report_bytes,
                report_written=False,
            )
            _attach_retrieval_output_report_diagnostics(
                payload,
                report_key="semantic_debug",
                phase="semantic_debug_output_report",
                profile=output_profile,
                output_format=args.format,
                report_output=None,
                report_written=False,
            )
            rendered = render(payload, args.format)
        print(rendered)
        return 0
    if args.command == "private-benchmark-mine":
        args.query = " ".join(args.queries)
        source_plan, _intent, plan = payload_from_args(args)
        output_dir = (
            Path(args.output_dir).expanduser()
            if args.output_dir
            else Path(".private") / "jsh-013" / f"run-{parse_now(args.now, timezone_from_args(args.timezone, args.timezone_offset)).strftime('%Y%m%d-%H%M%S')}"
        )
        payload = build_private_benchmark_mine_payload(
            source_plan=source_plan,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            fixture_path=Path(args.fixture).expanduser(),
            output_dir=output_dir,
            queries=args.queries,
            query_families=args.query_family,
            target_categories=args.target_category,
            target_stages=args.target_stage,
            target_failure_modes=args.target_failure_mode,
            candidate_limit=args.candidate_limit,
            per_query_max_matches=args.per_query_max_matches,
            context_lines=args.context_lines,
            include_tool_events=not args.no_tool_events,
            include_workflow_repeat=not args.no_workflow_repeat,
            mask_context=_mask_context_from_args(args),
            output_format=args.format,
            report_output=args.report_output,
        )
        print(render(payload, args.format))
        return 0
    if args.command == "benchmark":
        payload = build_efficiency_eval(
            Path(args.fixture).expanduser(),
            strategies=tuple(args.strategy) if args.strategy else None,
            include_embedding_spike=args.include_embedding_spike,
            embedding_fixture_path=Path(args.embedding_spike_fixture).expanduser(),
        )
        print(render(payload, args.format))
        eval_payload = payload["retrieval_efficiency_eval"]
        thresholds = eval_payload.get("large_impact_thresholds", {})
        if args.fail_on_threshold and thresholds.get("status") == "fail":
            return 1
        return 1 if eval_payload.get("status") == "invalid_fixture" else 0
    if args.command == "benchmark-measured":
        payload = build_measured_benchmark_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["measured_benchmark"]["status"]
        return 1 if status in {"no_roots", "root_rejected", "invalid_fixture"} else 0
    if args.command == "benchmark-impact":
        payload = build_impact_benchmark_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["impact_benchmark"]["status"]
        return 1 if status in {"root_rejected", "invalid_fixture"} else 0
    if args.command == "mechanism-eval":
        payload = build_mechanism_eval_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["mechanism_eval"]["status"]
        return 1 if status in {"root_rejected", "invalid_fixture"} else 0
    if args.command == "compact-handoff-usefulness-eval":
        payload = build_compact_handoff_usefulness_eval_payload(Path(args.fixture).expanduser())
        rendered = (
            render(payload, "json")
            if args.format == "json"
            else render_compact_handoff_usefulness_markdown(payload)
        )
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["compact_handoff_usefulness_eval"]["status"]
        return 1 if status == "invalid_fixture" else 0
    if args.command == "compact-handoff-answer-use-eval":
        payload = build_compact_handoff_answer_use_eval_payload(Path(args.fixture).expanduser())
        rendered = (
            render(payload, "json")
            if args.format == "json"
            else render_compact_handoff_answer_use_markdown(payload)
        )
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["compact_handoff_answer_use_eval"]["status"]
        return 1 if status == "invalid_fixture" else 0
    if args.command == "source-tier-answer-use-eval":
        payload = build_source_tier_answer_use_eval_payload(Path(args.fixture).expanduser())
        rendered = (
            render(payload, "json")
            if args.format == "json"
            else render_source_tier_answer_use_markdown(payload)
        )
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["source_tier_answer_use_eval"]["status"]
        return 1 if status == "invalid_fixture" else 0
    if args.command == "retrieve-expand-answer-use-eval":
        payload = build_retrieve_expand_answer_use_eval_payload(Path(args.fixture).expanduser())
        rendered = (
            render(payload, "json")
            if args.format == "json"
            else render_retrieve_expand_answer_use_markdown(payload)
        )
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["retrieve_expand_answer_use_eval"]["status"]
        return 1 if status == "invalid_fixture" else 0
    if args.command == "eval-decision-loop":
        pressure_report = Path(args.pressure_report).expanduser() if args.pressure_report else None
        semantic_debug_report = (
            Path(args.semantic_debug_report).expanduser() if args.semantic_debug_report else None
        )
        follow_next_reads_report = (
            Path(args.follow_next_reads_report).expanduser() if args.follow_next_reads_report else None
        )
        payload = build_eval_decision_loop_payload(
            pressure_report=pressure_report,
            semantic_debug_report=semantic_debug_report,
            follow_next_reads_report=follow_next_reads_report,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "failure-analysis":
        payload = build_failure_analysis_payload(
            semantic_debug_report=Path(args.semantic_debug_report).expanduser(),
            story_id=args.story_id,
            forensics_report=Path(args.forensics_report).expanduser() if args.forensics_report else None,
            follow_next_reads_report=Path(args.follow_next_reads_report).expanduser()
            if args.follow_next_reads_report
            else None,
            follow_through_behavior_labels_report=Path(args.follow_through_behavior_labels_report).expanduser()
            if args.follow_through_behavior_labels_report
            else None,
            repo_artifact_evidence_report=Path(args.repo_artifact_evidence_report).expanduser()
            if args.repo_artifact_evidence_report
            else None,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0 if payload["failure_analysis"]["status"] == "ok" else 1
    if args.command == "benchmark-real-history-impact":
        payload = build_real_history_impact_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render(payload, "json"), encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_impact_benchmark"]["status"]
        return 1 if status in {"root_rejected", "invalid_fixture", "invalid_bridge"} else 0
    if args.command == "benchmark-real-history-timestamp-seek":
        payload = build_real_history_timestamp_seek_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_timestamp_seek_benchmark"]["status"]
        return 1 if status in {"root_rejected", "invalid_fixture", "invalid_bridge"} else 0
    if args.command == "benchmark-real-history-embedding":
        payload = build_real_history_embedding_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_embedding_benchmark"]["status"]
        return 1 if status in {"invalid_fixture", "invalid_bridge", "private_output_rejected"} else 0
    if args.command == "audit-real-history-package-misses":
        payload = build_real_history_package_miss_audit_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_package_search_miss_audit"]["status"]
        return 1 if status in {"invalid_fixture"} else 0
    if args.command == "diagnose-real-history-remaining-misses":
        payload = build_real_history_remaining_miss_diagnostic_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_remaining_miss_loss_point_diagnostic"]["status"]
        return 1 if status in {"incomplete"} else 0
    if args.command == "diagnose-real-history-opened-file-progress":
        payload = build_real_history_opened_file_progress_diagnostic_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_opened_file_progress_displacement_diagnostic"]["status"]
        return 1 if status in {"incomplete"} else 0
    if args.command == "compare-real-history-benchmark-drift":
        payload = build_real_history_benchmark_drift_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            markdown_path = (
                Path(args.markdown_report_output).expanduser()
                if args.markdown_report_output
                else output_path.with_suffix(".md")
            )
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        elif args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["real_history_benchmark_drift_comparator"]["status"]
        return 1 if status in {"incomplete", "invalid_artifact"} else 0
    if args.command == "diagnose-post-jsh763-date-timeline-misses":
        payload = build_post_jsh763_date_timeline_diagnostic_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render(payload, "json"), encoding="utf-8")
        if args.markdown_report_output:
            markdown_path = Path(args.markdown_report_output).expanduser()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        status = payload["post_jsh763_date_timeline_miss_depth_diagnostic"]["status"]
        return 1 if status in {"incomplete"} else 0
    if args.command == "benchmark-live-terminal-protocol":
        payload = build_live_terminal_protocol_payload()
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "benchmark-paired-agent-protocol":
        payload = build_paired_agent_benchmark_protocol_payload()
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "benchmark-agent-loop-contract":
        case_selection = Path(args.case_selection).expanduser()
        if args.output:
            payload = write_agent_loop_cost_contract(Path(args.output).expanduser(), case_selection)
        else:
            payload = build_agent_loop_cost_contract_payload(case_selection)
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "extract-retained-trace-counters":
        trace_root = Path(args.trace_root).expanduser()
        jsh747_packet = Path(args.jsh747_packet).expanduser() if args.jsh747_packet else None
        output = Path(args.output).expanduser() if args.output else DEFAULT_RETAINED_TRACE_COUNTER_OUTPUT_PATH
        if jsh747_packet is None:
            payload = write_retained_trace_counter_packet(output, trace_root)
        else:
            payload = write_retained_trace_counter_packet(output, trace_root, jsh747_packet)
        rendered = render(payload, args.format)
        report_output = Path(args.report_output).expanduser() if args.report_output else DEFAULT_RETAINED_TRACE_COUNTER_REPORT_PATH
        report_output.parent.mkdir(parents=True, exist_ok=True)
        report_output.write_text(render(payload, "markdown"), encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "receiving-agent-ledger":
        lanes = [parse_lane_arg(item) for item in args.lane]
        payload = build_receiving_agent_trace_ledger(lanes, repo_root=Path.cwd())
        rendered = (
            render(payload, "json")
            if args.format == "json"
            else render_receiving_agent_trace_ledger_markdown(payload)
        )
        if args.report_output:
            report_output = Path(args.report_output).expanduser()
        elif args.format == "markdown":
            report_output = DEFAULT_RECEIVING_AGENT_LEDGER_MD
        else:
            report_output = DEFAULT_RECEIVING_AGENT_LEDGER_JSON
        report_output.parent.mkdir(parents=True, exist_ok=True)
        report_output.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    if args.command == "validate-harness-artifacts":
        payload = build_harness_artifact_validation_payload(
            schema_path=Path(args.schema).expanduser(),
            artifact_paths=tuple(Path(item).expanduser() for item in args.artifact),
        )
        rendered = render(payload, args.format)
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 1 if payload["harness_artifact_validation"]["status"] == "fail" else 0
    if args.command == "build-harness-dry-run-artifacts":
        payload = build_harness_dry_run_artifacts_payload(
            case_id=args.case_id,
            output_dir=Path(args.output_dir).expanduser(),
            report_output=Path(args.report_output).expanduser() if args.report_output else None,
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["harness_dry_run_artifacts"]["status"] == "fail" else 0
    if args.command == "build-harness-offline-scorer-artifacts":
        payload = build_harness_offline_scorer_artifacts_payload(
            output_dir=Path(args.output_dir).expanduser(),
            report_output=Path(args.report_output).expanduser() if args.report_output else None,
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["harness_offline_scorer_artifacts"]["status"] == "fail" else 0
    if args.command == "export-inspect-adapter-proof":
        payload = write_inspect_compatible_export(
            output_path=Path(args.output).expanduser(),
            adjudication_path=Path(args.adjudication).expanduser(),
            sequence=args.sequence,
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["status"] != "pass" else 0
    if args.command == "build-harness-minimum-loop":
        payload = build_harness_minimum_loop_payload(
            output_dir=Path(args.output_dir).expanduser(),
            case_id=args.case_id,
            report_output=Path(args.report_output).expanduser() if args.report_output else None,
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["harness_minimum_user_facing_loop"]["status"] != "pass" else 0
    if args.command == "build-harness-real-run-gate":
        payload = build_real_run_gate_payload(
            summary_path=Path(args.summary).expanduser(),
            review_path=Path(args.review).expanduser(),
            output_dir=Path(args.output_dir).expanduser(),
            report_output=Path(args.report_output).expanduser() if args.report_output else None,
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["harness_real_run_gate"]["status"] != "pass" else 0
    if args.command == "validate-agent-use-value-gate":
        payload = build_agent_use_value_manifest_validation_payload(Path(args.manifest).expanduser())
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["agent_use_value_gate_manifest_validation"]["status"] != "pass" else 0
    if args.command == "validate-agent-use-value-reduced-run":
        payload = build_agent_use_value_reduced_run_validation_payload(
            Path(args.summary).expanduser(),
            require_raw_files=args.require_raw_files,
            private_root=Path(args.private_root).expanduser(),
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["agent_use_value_reduced_run_validation"]["status"] != "pass" else 0
    if args.command == "validate-agent-use-value-expansion":
        payload = build_agent_use_value_expansion_validation_payload(
            Path(args.summary).expanduser(),
            require_raw_files=args.require_raw_files,
            private_root=Path(args.private_root).expanduser(),
        )
        rendered = render(payload, args.format)
        print(rendered)
        return 1 if payload["agent_use_value_expansion_validation"]["status"] != "pass" else 0
    if args.command == "embedding-experiment":
        payload = build_embedding_experiment_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["embedding_experiment"]["status"]
        return 1 if status in {"root_rejected", "invalid_fixture", "private_output_rejected"} else 0
    if args.command == "archive-snapshot-proof":
        payload = build_archive_snapshot_proof_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        status = payload["archive_snapshot_proof"]["status"]
        return 1 if status in {"invalid_request", "proof_failed"} else 0
    if args.command == "latest-user-messages":
        payload = build_latest_user_messages_payload(args)
        print(render(payload, args.format))
        return 0
    if args.command == "what-happened-since":
        phase_emitter = (
            AskExecutePhaseEmitter(command=args.command)
            if args.parent_thread and args.complete_interval
            else None
        )
        source_phase = phase_emitter.start("source_planning") if phase_emitter else None
        if phase_emitter and source_phase:
            phase_emitter.complete(source_phase)
        retrieval_phase = phase_emitter.start("retrieval") if phase_emitter else None
        payload = build_status_anchor_payload(args)
        if phase_emitter and retrieval_phase:
            phase_emitter.complete(retrieval_phase)
        rendering_phase = phase_emitter.start("rendering") if phase_emitter else None
        rendered = render(payload, args.format)
        if phase_emitter and rendering_phase:
            phase_emitter.complete(rendering_phase)
        print(rendered)
        return 0 if payload["status_anchor"]["status"] == "ok" else 1
    if args.command == "artifact-currentness":
        from .artifact_currentness import build_artifact_currentness_payload

        payload = build_artifact_currentness_payload(args)
        print(render(payload, args.format))
        return 0 if payload["artifact_currentness"]["status"] == "ok" else 1
    if args.command == "local-evidence-diagnostic":
        from .local_evidence import build_local_evidence_diagnostic_payload

        payload = build_local_evidence_diagnostic_payload(args)
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0 if payload["local_evidence_diagnostic"]["status"] == "ok" else 1
    if args.command == "semantic-wrapup":
        from .semantic_wrapup import build_semantic_wrapup_payload

        source_plan, _intent, plan = payload_from_args(args)
        mask_context = _mask_context_from_args(args)
        payload = build_semantic_wrapup_payload(
            query=args.query,
            source_plan=source_plan,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=Path(args.cwd).expanduser().resolve(),
            mask_context=mask_context,
            output_format=args.format,
            report_output=args.report_output,
        )
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0 if payload["semantic_wrapup"]["status"] in {"found", "no_failure_modes_found"} else 1
    if args.command == "read-more":
        args.query = args.target
        source_plan, _intent, plan = payload_from_args(args)
        projection = args.projection or (
            "compact" if args.format == "text" and args.mode == "window" else "full"
        )
        payload = build_source_expansion_payload(
            request=ExpansionRequest(
                target=args.target,
                mode=args.mode,
                scope=args.scope,
                before=args.before,
                after=args.after,
                next_count=args.next_count,
                max_records=args.max_records,
                projection=projection,
                compact_text_limit=args.compact_text_limit,
            ),
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=Path(args.cwd).expanduser().resolve(),
            now_utc=parse_datetime(source_plan.get("now"), timezone.utc),
        )
        print(render(payload, args.format))
        return 0 if payload["source_expansion"]["status"] == "expanded" else 1
    if args.command == "follow-next-reads":
        args.query = str(args.from_report)
        _source_plan, _intent, plan = payload_from_args(args)
        payload = build_follow_next_reads_payload(
            report_path=Path(args.from_report).expanduser(),
            kind=args.kind,
            limit=args.limit,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=Path(args.cwd).expanduser().resolve(),
            reviewed_usability_decisions_path=Path(args.reviewed_usability_decisions).expanduser()
            if args.reviewed_usability_decisions
            else None,
            recovery_report_output_path=Path(args.report_output).expanduser() if args.report_output else None,
        )
        print(render(payload, args.format))
        packet = payload["follow_next_reads"]
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render(payload, args.format), encoding="utf-8")
        if packet["status"] not in {"expanded", "partial_expanded", "no_next_reads"}:
            return 1
        return 0
    source_plan, intent, plan = payload_from_args(args)
    if args.command == "manifest":
        payload = build_manifest_report(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            shortlist_limit=args.shortlist_limit,
        )
        payload["source_plan"] = source_plan
    elif args.command == "find-structural-hybrid-budgeted":
        mask_context = _mask_context_from_args(args)
        result = search_structural_hybrid_budgeted(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            mask_context=mask_context,
        )
        result["warnings"] = sorted(set(result["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
        output_profile_token = start_phase_profile("agent_find_output_report")
        payload = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            manifest_report=None,
            output_format=args.format,
            report_output=args.report_output,
        )
        payload["agent_find"]["structural_hybrid_budgeted"] = {
            "command_surface": result["command_surface"],
            "status": result["status"],
            "strategy": result["strategy"],
            "gate": result["gate"],
            "package_candidate_count": result["package_candidate_count"],
            "structural_supplement_candidate_count": result["structural_supplement_candidate_count"],
            "supplement_limit": result["supplement_limit"],
            "candidate_selection_profile": result["candidate_selection_profile"],
            "structural_candidate_read_schedule_profile": result["structural_candidate_read_schedule_profile"],
            "structural_candidate_read_schedule_entry_count": result["structural_candidate_read_schedule_entry_count"],
            "package_searched_bytes": result["package_searched_bytes"],
            "structural_searched_bytes": result["structural_searched_bytes"],
            "structural_records_considered": result["structural_records_considered"],
            "record_window": result["record_window"],
            "record_window_enabled": result["record_window_enabled"],
            "record_window_radius": result["record_window_radius"],
            "record_window_seed_entry_count": result["record_window_seed_entry_count"],
            "record_window_extra_entry_count": result["record_window_extra_entry_count"],
            "record_window_entry_count": result["record_window_entry_count"],
            "record_window_max_extra_entries_per_file": result["record_window_max_extra_entries_per_file"],
            "record_window_max_extra_entries_total": result["record_window_max_extra_entries_total"],
            "oracle_inputs_used": result["oracle_inputs_used"],
            "privacy": result["privacy"],
        }
    elif args.command in {"find", "plan-sources", "broaden-query", "find-candidates", "timeline", "cluster-candidates", "no-evidence-report"}:
        mask_context = _mask_context_from_args(args)
        result = run_retrieval_mode(
            retrieval_mode=getattr(args, "retrieval_mode", RETRIEVAL_MODE_PACKAGE),
            plan=plan,
            intent=intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            mask_context=mask_context,
            use_rg_prefilter=args.rg_prefilter,
            fielded_bm25_index_dir=_fielded_bm25_index_dir_from_args(args),
            rrf_fusion_storage=getattr(args, "rrf_fusion_storage", "memory"),
            rrf_fusion_materialization_dir=_rrf_fusion_materialization_dir_from_args(args),
            query_plan_mode=getattr(args, "query_plan", QUERY_PLAN_MODE_AUTO),
            continuation_ledger=getattr(args, "continuation_ledger", False),
        )
        result["warnings"] = sorted(set(result["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
        manifest_report = (
            build_manifest_report(
                plan,
                intent,
                default_tz=timezone_from_args(args.timezone, args.timezone_offset),
                cwd=source_plan["cwd"],
            )
            if args.include_manifest
            else None
        )
        output_profile_token = start_phase_profile("agent_find_output_report")
        payload = build_agent_find_payload(
            source_plan=source_plan,
            search_result=result,
            manifest_report=manifest_report,
            output_format=args.format,
            report_output=args.report_output,
            compact_provenance_handoff=getattr(args, "compact_provenance_handoff", False),
            evidence_context_window=getattr(args, "evidence_context_window", 0),
            no_evidence_mode=args.command == "no-evidence-report",
            deep_audit_target_contract=getattr(args, "deep_audit_target_contract", None),
        )
    elif args.command == "plan":
        payload = {
            "source_plan": source_plan,
            "searched_files": 0,
            "searched_bytes": 0,
            "groups": [],
            "warnings": sorted(set(intent.warnings + plan.warnings + _configuration_warnings(source_plan))),
            "blind_spots": plan.blind_spots,
        }
    else:
        mask_context = _mask_context_from_args(args)
        result = search(
            plan,
            intent,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=source_plan["cwd"],
            mask_context=mask_context,
            use_rg_prefilter=args.rg_prefilter,
        )
        payload = {"source_plan": source_plan, **result}
        payload["warnings"] = sorted(set(payload["warnings"] + intent.warnings + _configuration_warnings(source_plan)))
    rendered = render(payload, args.format)
    if getattr(args, "report_output", None) and "agent_find" in payload:
        report_rendered = _render_for_report_output(payload, args.report_output, args.format)
        output_profile = finish_phase_profile(
            output_profile_token,
            stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
            report_bytes=len(report_rendered.encode("utf-8", errors="replace")),
            report_written=True,
        )
        _attach_retrieval_output_report_diagnostics(
            payload,
            report_key="agent_find",
            phase="agent_find_output_report",
            profile=output_profile,
            output_format=args.format,
            report_output=args.report_output,
            report_written=True,
        )
        Path(args.report_output).expanduser().write_text(
            _render_for_report_output(payload, args.report_output, args.format),
            encoding="utf-8",
        )
        if "agent_find" in payload:
            if _agent_find_is_no_evidence_report(payload["agent_find"]):
                payload = _short_no_evidence_report_written_payload(payload, args.report_output)
            else:
                payload = _short_report_written_payload(payload, args.report_output)
            rendered = render(payload, args.format)
    elif getattr(args, "report_output", None):
        Path(args.report_output).expanduser().write_text(
            _render_for_report_output(payload, args.report_output, args.format),
            encoding="utf-8",
        )
    elif "agent_find" in payload:
        output_profile = finish_phase_profile(
            output_profile_token,
            stdout_bytes=len(rendered.encode("utf-8", errors="replace")),
            report_bytes=0,
            report_written=False,
        )
        _attach_retrieval_output_report_diagnostics(
            payload,
            report_key="agent_find",
            phase="agent_find_output_report",
            profile=output_profile,
            output_format=args.format,
            report_output=None,
            report_written=False,
        )
        rendered = render(payload, args.format)
    print(rendered)
    return 0


def _agent_find_is_no_evidence_report(report: dict) -> bool:
    no_evidence = report.get("no_evidence_report") if isinstance(report.get("no_evidence_report"), dict) else {}
    return report.get("status") == "no_evidence" or bool(no_evidence.get("needed"))


def _semantic_debug_is_scoped_no_evidence(packet: dict) -> bool:
    workflow = (
        packet.get("scope_aware_no_evidence_workflow")
        if isinstance(packet.get("scope_aware_no_evidence_workflow"), dict)
        else {}
    )
    answer_ready = packet.get("answer_ready_handoff") if isinstance(packet.get("answer_ready_handoff"), dict) else {}
    agent_instruction = packet.get("agent_instruction") if isinstance(packet.get("agent_instruction"), dict) else {}
    source_action = (
        packet.get("source_action_guidance")
        if isinstance(packet.get("source_action_guidance"), dict)
        else answer_ready.get("source_action_guidance")
        if isinstance(answer_ready.get("source_action_guidance"), dict)
        else agent_instruction.get("source_action_guidance")
        if isinstance(agent_instruction.get("source_action_guidance"), dict)
        else {}
    )
    return (
        bool(workflow.get("applies"))
        or packet.get("status") == "no_evidence"
        or answer_ready.get("decision") == "scoped_no_evidence"
        or source_action.get("decision") == "answer_scoped_no_evidence_now"
    )


def _short_no_evidence_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["agent_find"]
    no_evidence = report.get("no_evidence_report") if isinstance(report.get("no_evidence_report"), dict) else {}
    return {
        "agent_find": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "query": report["query"],
            "report_written": str(Path(report_output).expanduser()),
            "searched_scope": _no_evidence_searched_scope_summary(
                no_evidence,
                searched_files=report["token_efficiency"]["searched_files"],
                searched_bytes=report["token_efficiency"]["searched_bytes"],
            ),
            "scope_counts": _no_evidence_scope_counts(no_evidence),
            "cap_hit": _no_evidence_cap_hit(no_evidence),
            "no_evidence": _no_evidence_boundary_summary(no_evidence),
            "message": "Full no-evidence find report written to report_output; stdout is a scoped summary.",
        }
    }


def _short_semantic_debug_no_evidence_report_written_payload(packet: dict, report_output: str) -> dict:
    no_evidence = packet.get("no_evidence") if isinstance(packet.get("no_evidence"), dict) else {}
    answer_ready = packet.get("answer_ready_handoff") if isinstance(packet.get("answer_ready_handoff"), dict) else {}
    workflow = (
        packet.get("scope_aware_no_evidence_workflow")
        if isinstance(packet.get("scope_aware_no_evidence_workflow"), dict)
        else {}
    )
    agent_instruction = packet.get("agent_instruction") if isinstance(packet.get("agent_instruction"), dict) else {}
    source_action = (
        packet.get("source_action_guidance")
        if isinstance(packet.get("source_action_guidance"), dict)
        else answer_ready.get("source_action_guidance")
        if isinstance(answer_ready.get("source_action_guidance"), dict)
        else agent_instruction.get("source_action_guidance")
        if isinstance(agent_instruction.get("source_action_guidance"), dict)
        else {}
    )
    boundary = _no_evidence_boundary_summary(
        no_evidence,
        locator_meaning=(packet.get("source_locator_handoff") or {}).get("no_evidence_meaning", {}),
    )
    boundary["source_action_guidance_decision"] = source_action.get("decision")
    boundary["read_more_policy"] = _semantic_debug_scoped_no_evidence_read_more_policy(
        answer_ready=answer_ready,
        source_action=source_action,
    )
    if workflow and not workflow.get("answer_ready"):
        boundary["read_more_policy"] = {
            "required_before_scoped_no_evidence_answer": True,
            "actionability": "scope_refinement_or_continuation_required",
            "diagnostic_policy": (
                "Do not answer no-evidence from optional diagnostics; resolve the workflow blockers "
                "with its exact continuation or required scope refinement."
            ),
        }
    compact = {
        "schema_version": packet["schema_version"],
        "status": packet["status"],
        "query": packet["query"],
        "audit_depth": packet.get("audit_depth"),
        "question_family": packet.get("question_family"),
        "report_written": str(Path(report_output).expanduser()),
        "candidate_count": len(packet.get("strongest_candidates") or []),
        "event_count": (packet.get("tool_action_proof") or {}).get("event_count"),
        "answer_ready_decision": answer_ready.get("decision"),
        "scope_aware_no_evidence_workflow": _compact_scope_aware_no_evidence_workflow(
            workflow
        ),
        "answer_readiness_summary": summarize_answer_readiness(packet.get("answer_readiness") or {}),
        "core_convo_first_read_plan": packet.get("core_convo_first_read_plan"),
        "searched_scope": _no_evidence_searched_scope_summary(no_evidence),
        "scope_counts": _no_evidence_scope_counts(no_evidence),
        "cap_hit": _no_evidence_cap_hit(no_evidence),
        "no_evidence": boundary,
        "message": "Full semantic-debug scoped no-evidence report written to report_output; stdout is a scoped summary.",
    }
    compact["answer_ready_handoff"] = {
        "decision": answer_ready.get("decision"),
        "source_action_guidance": {
            "exact_jsonl_continuation_commands": list(
                (answer_ready.get("source_action_guidance") or {}).get(
                    "exact_jsonl_continuation_commands"
                )
                or []
            )[:1],
        },
    }
    followthrough = packet.get("in_depth_audit_followthrough") or {}
    if followthrough:
        compact["in_depth_audit_followthrough"] = {
            "applies": bool(followthrough.get("applies")),
            "answer_ready": bool(followthrough.get("answer_ready")),
            "ordered_primary_refs_to_open_first": list(
                followthrough.get("ordered_primary_refs_to_open_first") or []
            )[:4],
            "continuation_commands": list(followthrough.get("continuation_commands") or [])[:1],
            "coverage_state": {
                "searched_scope": (followthrough.get("coverage_state") or {}).get(
                    "searched_scope"
                )
                or {},
            },
        }
    routing = packet.get("source_handle_routing") or {}
    if routing.get("required_final_provenance"):
        compact.update(
            {
                "source_locator_handoff": packet.get("source_locator_handoff"),
                "source_handle_routing": routing,
                "source_followthrough_plan": packet.get("source_followthrough_plan"),
                "answer_ready_handoff": packet.get("answer_ready_handoff"),
                "final_answer_source_use_contract": packet.get("final_answer_source_use_contract"),
                "action_proof_plan": packet.get("action_proof_plan"),
                "next_reads": packet.get("next_reads"),
            }
        )
    return {
        "semantic_debug": compact,
    }


def _compact_scope_aware_no_evidence_workflow(workflow: dict) -> dict:
    if not workflow:
        return {}
    compact = {
        "status": workflow.get("status"),
        "answer_ready": bool(workflow.get("answer_ready")),
        "hard_stop": bool(workflow.get("hard_stop")),
        "answer_scope": workflow.get("answer_scope"),
        "searched_scope_names": list(workflow.get("searched_scope_names") or []),
        "searched_file_count": int(workflow.get("searched_file_count") or 0),
        "global_absence_allowed": False,
        "blockers": list(workflow.get("blockers") or []),
        "optional_diagnostic_suppressed_count": int(
            workflow.get("optional_diagnostic_next_reads_suppressed") or 0
        ),
    }
    continuation = workflow.get("continuation") or {}
    if continuation.get("required"):
        compact["continuation"] = {
            "required": True,
            "exact_continuation": [
                item.get("command")
                for item in continuation.get("exact_commands") or []
                if isinstance(item, dict) and item.get("command")
            ],
            "required_scope_refinement": continuation.get("required_scope_refinement"),
            "missing_root_precondition": continuation.get("missing_root_precondition"),
        }
    return compact


def _no_evidence_searched_scope_summary(
    no_evidence: dict,
    *,
    searched_files: int | None = None,
    searched_bytes: int | None = None,
) -> dict:
    searched = no_evidence.get("searched_scope") if isinstance(no_evidence.get("searched_scope"), dict) else {}
    not_searched = (
        no_evidence.get("not_searched_scope")
        if isinstance(no_evidence.get("not_searched_scope"), dict)
        else {}
    )
    files = searched.get("searched_files") if searched_files is None else searched_files
    bytes_ = searched.get("searched_bytes") if searched_bytes is None else searched_bytes
    searched_roots = _compact_no_evidence_root_rows(searched.get("roots") or [])
    missing_roots = _compact_no_evidence_root_rows(
        no_evidence.get("missing_scope")
        if isinstance(no_evidence.get("missing_scope"), list)
        else not_searched.get("missing_roots") or []
    )
    checked_roots = _dedupe_compact_no_evidence_root_rows(searched_roots + missing_roots)
    return {
        "searched_files": int(files or 0),
        "searched_bytes": int(bytes_ or 0),
        "result_meaning": searched.get("result_meaning"),
        "checked_roots": checked_roots,
        "searched_roots": searched_roots,
        "missing_roots": missing_roots,
        "root_kinds": _unique_preserving_order([str(root.get("kind") or "") for root in checked_roots]),
        "root_privacy_labels": _unique_preserving_order(
            [str(root.get("privacy_label") or "") for root in checked_roots]
        ),
    }


def _compact_no_evidence_root_rows(rows: object) -> list[dict]:
    compact: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        compact.append(
            {
                "kind": row.get("kind"),
                "scope_label": row.get("scope_label"),
                "privacy_label": row.get("privacy_label"),
                "reason": row.get("reason"),
            }
        )
    return compact


def _dedupe_compact_no_evidence_root_rows(rows: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[object, object, object]] = set()
    for row in rows:
        key = (row.get("kind"), row.get("scope_label"), row.get("privacy_label"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _no_evidence_scope_counts(no_evidence: dict) -> dict:
    searched = no_evidence.get("searched_scope") if isinstance(no_evidence.get("searched_scope"), dict) else {}
    selected_roots = no_evidence.get("selected_roots")
    if isinstance(selected_roots, list):
        selected_count = len(selected_roots)
    else:
        not_searched = (
            no_evidence.get("not_searched_scope")
            if isinstance(no_evidence.get("not_searched_scope"), dict)
            else {}
        )
        missing_roots = (
            no_evidence.get("missing_scope")
            if isinstance(no_evidence.get("missing_scope"), list)
            else not_searched.get("missing_roots") or []
        )
        selected_count = len(
            _dedupe_compact_no_evidence_root_rows(
                _compact_no_evidence_root_rows(searched.get("roots") or [])
                + _compact_no_evidence_root_rows(missing_roots)
            )
        )

    excluded_summary = (
        no_evidence.get("excluded_scope_summary")
        if isinstance(no_evidence.get("excluded_scope_summary"), dict)
        else no_evidence.get("excluded_scope")
        if isinstance(no_evidence.get("excluded_scope"), dict)
        else {}
    )
    excluded_roots = no_evidence.get("excluded_scope")
    excluded_count = int(excluded_summary.get("rejected_root_count") or 0)
    if not excluded_count and isinstance(excluded_roots, list):
        excluded_count = len(excluded_roots)

    not_searched = no_evidence.get("not_searched_scope") if isinstance(no_evidence.get("not_searched_scope"), dict) else {}
    missing_roots = no_evidence.get("missing_scope") if isinstance(no_evidence.get("missing_scope"), list) else None
    missing_count = int(not_searched.get("missing_root_count") or 0)
    if not missing_count:
        missing_count = len(missing_roots or not_searched.get("missing_roots") or [])

    return {
        "selected_root_count": selected_count,
        "excluded_root_count": excluded_count,
        "missing_root_count": missing_count,
    }


def _no_evidence_cap_hit(no_evidence: dict) -> dict:
    cap_hit = no_evidence.get("cap_hit") if isinstance(no_evidence.get("cap_hit"), dict) else {}
    if cap_hit:
        return {
            "hit": bool(cap_hit.get("hit")),
            "caps_hit": list(cap_hit.get("caps_hit") or []),
            "normalized_warnings": list(cap_hit.get("normalized_warnings") or []),
        }
    not_searched = no_evidence.get("not_searched_scope") if isinstance(no_evidence.get("not_searched_scope"), dict) else {}
    cap_limited = not_searched.get("cap_limited") if isinstance(not_searched.get("cap_limited"), dict) else {}
    return {
        "hit": bool(cap_limited.get("hit")),
        "caps_hit": list(cap_limited.get("caps_hit") or []),
        "normalized_warnings": list(cap_limited.get("normalized_warnings") or []),
    }


def _no_evidence_boundary_summary(no_evidence: dict, *, locator_meaning: dict | None = None) -> dict:
    locator_meaning = locator_meaning or {}
    not_searched = no_evidence.get("not_searched_scope") if isinstance(no_evidence.get("not_searched_scope"), dict) else {}
    summary = {
        "needed": bool(no_evidence.get("needed")),
        "answer_scope": no_evidence.get("answer_scope")
        or locator_meaning.get("answer_scope")
        or "not_found_in_searched_scope",
        "not_all_history_absence": bool(
            no_evidence.get("not_all_history_absence", locator_meaning.get("not_all_history_absence", True))
        ),
        "read_more_policy": _no_evidence_read_more_policy(no_evidence),
    }
    if not_searched.get("outside_selected_roots"):
        summary["outside_selected_roots"] = not_searched.get("outside_selected_roots")
    if no_evidence.get("candidate_interpretation"):
        summary["candidate_interpretation"] = no_evidence.get("candidate_interpretation")
    coverage = no_evidence.get("query_term_coverage") if isinstance(no_evidence.get("query_term_coverage"), dict) else {}
    missing_terms = list(coverage.get("missing_terms_any_candidate") or [])
    if missing_terms:
        summary["missing_query_terms"] = missing_terms
    if no_evidence.get("weak_candidate_count") is not None:
        summary["weak_candidate_count"] = no_evidence.get("weak_candidate_count")
    elif no_evidence.get("candidate_rows_found") is not None:
        summary["candidate_rows_found"] = no_evidence.get("candidate_rows_found")
    return summary


def _no_evidence_read_more_policy(no_evidence: dict) -> dict:
    guidance = no_evidence.get("read_more_guidance") if isinstance(no_evidence.get("read_more_guidance"), dict) else {}
    required = bool(guidance.get("required_before_scoped_no_evidence_answer", False))
    policy = {
        "required_before_scoped_no_evidence_answer": required,
        "actionability": guidance.get("actionability")
        or ("required_before_answer" if required else "optional_diagnostic_context"),
    }
    if guidance.get("diagnostic_next_read_count_suppressed") is not None:
        policy["diagnostic_next_read_count_suppressed"] = guidance.get("diagnostic_next_read_count_suppressed")
    if guidance.get("source_refs_available_in"):
        policy["source_refs_available_in"] = guidance.get("source_refs_available_in")
    if guidance.get("reason"):
        policy["reason"] = guidance.get("reason")
    return policy


def _semantic_debug_scoped_no_evidence_read_more_policy(*, answer_ready: dict, source_action: dict) -> dict:
    source_use = answer_ready.get("source_use_summary") if isinstance(answer_ready.get("source_use_summary"), dict) else {}
    optional_reads = source_action.get("optional_diagnostic_reads") if isinstance(source_action, dict) else []
    return {
        "required_before_scoped_no_evidence_answer": False,
        "actionability": "optional_diagnostic_context",
        "diagnostic_policy": "Diagnostic reads may be inspected for audit/context, but are not required before the scoped no-evidence answer.",
        "optional_diagnostic_read_count": len(optional_reads or []),
        "diagnostic_only_excluded_next_read_count": int(source_use.get("diagnostic_only_excluded_next_read_count") or 0),
    }


def _short_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["agent_find"]
    short = {
        "agent_find": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "query": report["query"],
            "report_written": str(Path(report_output).expanduser()),
            "candidate_count": report["token_efficiency"]["candidate_count"],
            "searched_files": report["token_efficiency"]["searched_files"],
            "searched_bytes": report["token_efficiency"]["searched_bytes"],
            "command_surface": report["command_surface"],
            "source_locator_handoff": report["source_locator_handoff"],
            "top_candidates": report["find_candidates"][:3],
            "next_search_suggestions": report["next_search_suggestions"],
            "message": "Full find report written to report_output; stdout is a compact handoff map.",
        }
    }
    if "compact_provenance_handoff" in report:
        short["agent_find"]["compact_provenance_handoff"] = report["compact_provenance_handoff"]
    if "retrieval_diagnostics" in report:
        short["agent_find"]["retrieval_diagnostics"] = report["retrieval_diagnostics"]
    if "retrieval_pressure" in report:
        short["agent_find"]["retrieval_pressure"] = report["retrieval_pressure"]
    return short


def _short_lineage_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["project_lineage"]
    return {
        "project_lineage": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "report_written": str(Path(report_output).expanduser()),
            "candidate_count": len(report.get("candidate_projects", [])),
            "compact_ai_handoff": report.get("compact_ai_handoff", {}),
            "warnings": payload.get("warnings", []),
            "next_read": report.get("next_read", [])[:5],
            "message": "Full lineage report written to report_output; stdout is a compact handoff map.",
        }
    }


def _short_skill_audit_report_written_payload(payload: dict, report_output: str, output_format: str = "json") -> dict:
    report = payload["skill_audit"]
    efficiency = report.get("token_efficiency", {})
    compact = {
        "schema_version": report["schema_version"],
        "status": report["status"],
        "skill": report["skill"],
        "report_written": str(Path(report_output).expanduser()),
        "token_efficiency": {
            "usage_instance_count": efficiency.get("usage_instance_count", 0),
            "non_use_mention_count": efficiency.get("non_use_mention_count", 0),
            "searched_files": efficiency.get("searched_files", 0),
            "searched_bytes": efficiency.get("searched_bytes", 0),
        },
        "warnings": payload.get("warnings", []),
        "message": "Full skill audit report written to report_output; stdout is a compact handoff map.",
    }
    if output_format == "json":
        compact["ai_handoff_packet"] = report.get("ai_handoff_packet", {})
    else:
        usage_instances = report.get("usage_instances", [])[:3]
        non_use_mentions = report.get("non_use_mentions", [])[:3]
        compact.update(
            {
                "usage_instances": usage_instances,
                "non_use_mentions": non_use_mentions,
                "top_usage_instances": usage_instances,
                "top_non_use_mentions": non_use_mentions,
            }
        )
    return {
        "skill_audit": compact
    }


def _short_tool_events_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["structured_tool_events"]
    efficiency = report.get("token_efficiency", {})
    return {
        "structured_tool_events": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "report_written": str(Path(report_output).expanduser()),
            "token_efficiency": {
                "event_count": efficiency.get("event_count", 0),
                "excluded_mention_count": efficiency.get("excluded_mention_count", 0),
                "searched_files": efficiency.get("searched_files", 0),
                "searched_bytes": efficiency.get("searched_bytes", 0),
            },
            "ai_handoff_packet": report.get("ai_handoff_packet", {}),
            "detector_schema_gap": report.get("detector_schema_gap"),
            "detector_observations": report.get("detector_observations"),
            "warnings": payload.get("warnings", []),
            "message": "Full structured tool-events report written to report_output; stdout is a compact handoff map.",
        }
    }


def _short_workflow_repeat_report_written_payload(payload: dict, report_output: str) -> dict:
    report = payload["workflow_repeat_finder"]
    efficiency = report.get("token_efficiency", {})
    return {
        "workflow_repeat_finder": {
            "schema_version": report["schema_version"],
            "status": report["status"],
            "query": report.get("query", ""),
            "report_written": str(Path(report_output).expanduser()),
            "token_efficiency": {
                "cluster_count": efficiency.get("cluster_count", 0),
                "suggestion_count": efficiency.get("suggestion_count", 0),
                "rejected_one_off_count": efficiency.get("rejected_one_off_count", 0),
                "searched_files": efficiency.get("searched_files", 0),
                "searched_bytes": efficiency.get("searched_bytes", 0),
            },
            "ai_handoff_packet": report.get("ai_handoff_packet", {}),
            "warnings": payload.get("warnings", []),
            "message": "Full workflow-repeat-finder report written to report_output; stdout is a compact handoff map.",
        }
    }


if __name__ == "__main__":
    raise SystemExit(main())
