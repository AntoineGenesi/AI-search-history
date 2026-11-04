"""Public command boundary for direct evidence and explicit specialists.

Ordinary ask executes locally; primary passages, not packet gates, support answers.
Rationale: docs/package-map.md#ordinary-history-workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

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
from .agent_loop_cost import DEFAULT_CASE_SELECTION_PATH as DEFAULT_AGENT_LOOP_CASE_SELECTION_PATH, DEFAULT_OUTPUT_PATH as DEFAULT_AGENT_LOOP_CONTRACT_OUTPUT_PATH, DEFAULT_RETAINED_TRACE_COUNTER_OUTPUT_PATH, DEFAULT_RETAINED_TRACE_COUNTER_REPORT_PATH, build_agent_loop_cost_contract_payload, write_agent_loop_cost_contract, write_retained_trace_counter_packet
from .archive_snapshot import build_archive_snapshot_proof_payload
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
from .providers import provider_catalog
from .impact_benchmark import DEFAULT_FIXTURE as DEFAULT_IMPACT_FIXTURE
from .impact_benchmark import build_impact_benchmark_payload
from .install_validation import validate_install
from .inspect_adapter import DEFAULT_X12G_ADJUDICATION_PATH, write_inspect_compatible_export
from .lineage import build_lineage_payload
from .live_terminal_protocol import build_live_terminal_protocol_payload
from .manifest import build_manifest_report
from .mechanism_eval import DEFAULT_FIXTURE as DEFAULT_MECHANISM_EVAL_FIXTURE
from .mechanism_eval import build_mechanism_eval_payload
from .measured_benchmark import DEFAULT_FIXTURE as DEFAULT_MEASURED_FIXTURE
from .measured_benchmark import build_measured_benchmark_payload
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
from .retrieval_modes import QUERY_PLAN_MODE_AUTO, QUERY_PLAN_MODES, RETRIEVAL_MODE_PACKAGE, RETRIEVAL_MODE_RRF_FUSION, RETRIEVAL_MODES, elapsed_label
from .semantic_debugging import DEFAULT_GOLD_MANIFEST, build_semantic_debug_payload, summarize_answer_readiness, summarize_guided_audit_flow
from .skill_audit import build_skill_audit_payload
from .source_expansion import (
    ExpansionRequest,
    build_execute_continuation_payload,
    build_follow_next_reads_payload,
    build_source_expansion_payload,
)
from .sources import Limits, SourcePlan, build_jsonl_candidate_discovery, build_source_plan, normalize_local_path
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
  ask                    Open bounded local passages for an ordinary history question.
  semantic-debug         Explicit legacy semantic specialist; not the ordinary ask path.
  bulk-line-match        Local bulk literal phrase candidate generation for selected JSONL roots.
  read-more              Open bounded context for an exact source ref.
  follow-next-reads      Expand saved next_reads from a JSON report without rerunning search.
  execute-continuation   Execute a saved canonical continuation packet and emit a successor receipt.

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
    ask, semantic-debug, semantic-wrapup,
    artifact-currentness, skill-check/skill-audit, no-evidence-report,
    bulk-line-match, read-more, follow-next-reads
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
    ask = subparsers.add_parser("ask", description="Read bounded local history evidence directly; no packets or model access.")
    ask.add_argument("query", help="Original question, including followups.")
    add_common_args(ask, retrieval=False)
    ask.set_defaults(max_files=20, max_matches=30, format="json")
    planning = subparsers.add_parser("plan", description="Inspect authorized history sources without reading evidence.")
    planning.add_argument("query", nargs="?", default="")
    add_common_args(planning)
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
    read_more.add_argument("target", help="Exact returned source target: path:line or provider-native locator.")
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
    follow_next_reads.add_argument("--from-report", required=True, help="Saved agent_find, semantic_debug, or semantic_wrapup JSON report.")
    follow_next_reads.add_argument("--kind", choices=("recovery", "required_primary_proof"), default="recovery")
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
    execute_continuation = subparsers.add_parser(
        "execute-continuation",
        description=(
            "Execute a saved canonical continuation packet in package-owned order. "
            "Only serialized bounded jsonl-history-search argv steps are allowed; "
            "fresh ask/find/bulk search, shell commands, guessed anchors, and optional diagnostics are rejected."
        ),
    )
    add_common_args(execute_continuation)
    execute_continuation.set_defaults(format="json")
    execute_continuation.add_argument(
        "--from-report",
        required=True,
        help="Saved JSON report containing a canonical_continuation_packet or next_proof_step packet.",
    )
    execute_continuation.add_argument(
        "--packet-id",
        default=None,
        help="Optional packet_id to select when a report contains multiple continuation packets.",
    )
    execute_continuation.add_argument(
        "--prior-receipt",
        default=None,
        help="Optional previous execute-continuation JSON receipt used to detect repeated bounded exhaustion.",
    )
    execute_continuation.add_argument(
        "--max-steps",
        type=int,
        default=12,
        help="Maximum serialized packet steps to execute in this run.",
    )
    execute_continuation.add_argument(
        "--max-output-chars",
        type=int,
        default=4000,
        help="Maximum raw output characters allowed in future step receipts; recorded as a receipt boundary.",
    )
    execute_continuation.add_argument(
        "--report-output",
        default=None,
        help="Optional path where the continuation successor receipt should be written as JSON.",
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


def add_common_args(parser: argparse.ArgumentParser, *, retrieval: bool = True) -> None:
    if retrieval:
        add_retrieval_args(parser)
    parser.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--codex-home", default=None)
    parser.add_argument("--claude-home", default=None)
    parser.add_argument("--gemini-home", default=None)
    parser.add_argument("--grok-home", default=None)
    parser.add_argument("--aider-root", default=None)
    parser.add_argument("--cursor-home", default=None)
    parser.add_argument("--opencode-root", default=None)
    parser.add_argument("--root", action="append", default=[])
    parser.add_argument("--role", default=None)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--max-bytes", type=int, default=100 * 1024 * 1024)
    parser.add_argument("--max-matches", type=int, default=40)
    parser.add_argument("--max-elapsed", type=float, default=15.0)
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--timezone-offset", default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument(
        "--since",
        default=None,
        help="Inclusive ISO-8601 record-time start; requires --until. Narrative recaps acquire ordered interval evidence; other questions retain search filters.",
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


def add_retrieval_args(parser: argparse.ArgumentParser) -> None:
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
    provider_roots = {
        provider: normalize_local_path(_history_home_from_args(args, provider=provider))
        for provider in ("gemini", "grok", "aider", "cursor", "opencode")
    }
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
        bm25_max_candidate_rows=getattr(args, 'bm25_max_candidate_rows', 5000),
        bm25_query_timeout=getattr(args, 'bm25_query_timeout', None),
    )
    explicit_window_start, explicit_window_end = _explicit_search_window_from_args(args, tz)
    intent = infer_query(
        args.query,
        now=now,
        role=args.role,
        date_window_start=explicit_window_start,
        date_window_end=explicit_window_end,
        date_reason="explicit_cli_window" if explicit_window_start is not None else None,
        natural_timezone_declared=bool(args.timezone or args.timezone_offset),
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
        provider_roots=provider_roots,
    )
    source_plan = {
        "query": intent.to_dict(),
        "sources": plan.to_dict(),
        "cwd": str(cwd),
        "codex_home": str(codex_home),
        "claude_home": str(claude_home),
        "provider_roots": {key: str(value) for key, value in provider_roots.items()},
        "provider_catalog": provider_catalog(),
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


# Exclusive bound that includes JSH-896's last observed event at 12:27:59.905Z.


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
            "investigation_finalization_state": packet.get("investigation_finalization_state"),
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


def _safe_relative_path(cwd: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(cwd).as_posix()
    except ValueError:
        return str(path.resolve(strict=False))


_ASK_ANSWER_MAX_LINE_CHARS = 900
# A reviewed packet authorizes one exact raw source. Inspecting that source
# past its anchor is not broad recovery, but it remains explicitly bounded.


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique = []
    for value in values:
        if value and value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


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
            and argv[module_index + 1] == "ask"
        )
    if executable == "git":
        subcommand = _named_task_source_session_git_subcommand(argv)
        return subcommand in {"commit", "apply"} or (subcommand == "diff" and "--check" in argv)
    return executable == "jsonl-history-search" and len(argv) > 1 and argv[1] == "ask"


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
        from .history_evidence import retrieve_history_evidence
        from .operation_budget import OperationBudget
        if not args.query.strip():
            print("ask requires the original question", file=sys.stderr)
            return 2
        budget = OperationBudget(limit_bytes=min(args.max_bytes, args.hard_cap_bytes) if args.hard_cap_bytes else args.max_bytes, max_elapsed=args.max_elapsed)
        try:
            _source_plan, intent, plan = payload_from_args(args)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        from .history_answer import retrieve_chronological_evidence
        default_tz = timezone_from_args(args.timezone, args.timezone_offset)
        operation = retrieve_chronological_evidence if intent.chronology_mode else retrieve_history_evidence
        extra = {'cutoff': parse_now(_source_plan['now'], default_tz), 'requested_cwd': args.cwd,
                 'live_request': args.now is None, 'explicit_roots': bool(args.root)} if operation is retrieve_chronological_evidence else {}
        payload = operation(query=args.query, intent=intent, plan=plan,
            default_tz=default_tz, budget=budget,
            mask_context=_mask_context_from_args(args), output_format=args.format, **extra)
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
    if args.command == "execute-continuation":
        args.query = str(args.from_report)
        _source_plan, _intent, plan = payload_from_args(args)
        payload = build_execute_continuation_payload(
            report_path=Path(args.from_report).expanduser(),
            packet_id=args.packet_id,
            prior_receipt_path=Path(args.prior_receipt).expanduser() if args.prior_receipt else None,
            max_steps=args.max_steps,
            max_output_chars=args.max_output_chars,
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=Path(args.cwd).expanduser().resolve(),
            receipt_output_path=Path(args.report_output).expanduser() if args.report_output else None,
        )
        print(render(payload, args.format))
        packet = payload["execute_continuation"]
        if args.report_output:
            output_path = Path(args.report_output).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(render(payload, args.format), encoding="utf-8")
        return 0 if packet["status"] == "executed" else 1
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
        raise ValueError("No evidence operation registered for " + args.command)
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
        "investigation_finalization_state": packet.get("investigation_finalization_state"),
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
                "investigation_finalization_state": packet.get("investigation_finalization_state"),
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
