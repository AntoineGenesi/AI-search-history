from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import timezone
from pathlib import Path

from .agent_contract import build_agent_find_payload
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
from .ask_router import build_ask_router_payload, execution_kind_for_route_family
from .capability_diagnostic import build_capability_diagnostic
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
from .impact_benchmark import DEFAULT_FIXTURE as DEFAULT_IMPACT_FIXTURE
from .impact_benchmark import build_impact_benchmark_payload
from .install_validation import validate_install
from .inspect_adapter import DEFAULT_X12G_ADJUDICATION_PATH, write_inspect_compatible_export
from .gittools import git_root_for, last_change_for_path
from .latest_user_messages import build_latest_user_messages_payload, latest_user_messages
from .lineage import build_lineage_payload
from .live_terminal_protocol import build_live_terminal_protocol_payload
from .manifest import build_manifest_report
from .mechanism_eval import DEFAULT_FIXTURE as DEFAULT_MECHANISM_EVAL_FIXTURE
from .mechanism_eval import build_mechanism_eval_payload
from .measured_benchmark import DEFAULT_FIXTURE as DEFAULT_MEASURED_FIXTURE
from .measured_benchmark import build_measured_benchmark_payload
from .prompt_echo import setup_echo_classification
from .output import render
from .paired_agent_benchmark_protocol import build_paired_agent_benchmark_protocol_payload
from .private_benchmark_mine import build_private_benchmark_mine_payload
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
from .retrieval_modes import QUERY_PLAN_MODE_AUTO, QUERY_PLAN_MODES, RETRIEVAL_MODE_PACKAGE, RETRIEVAL_MODES, run_retrieval_mode
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
from .sources import Limits, SourcePlan, build_jsonl_candidate_discovery, build_source_plan
from .status_anchor import DEFAULT_STATUS_QUERY, build_status_anchor_payload
from .structural_hybrid import search_structural_hybrid_budgeted
from .timeutil import parse_now, timezone_from_args
from .tool_events import build_tool_events_payload
from .workflow_repeat import build_workflow_repeat_payload
from .masking import MASK_MODES, MaskContext


LOCAL_EVIDENCE_MODES = ("public", "internal")


PUBLIC_HELP_EPILOG = """\
Launch-facing commands:
  validate-install        Check that the package/console script can run.
  diagnose-capabilities  Report installed core and optional capabilities.
  ask                    Route a natural-language prompt to the first JSONL command.
  semantic-debug         Main evidence workflow for memory, action proof, provenance, and no-evidence questions.
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
    read-more, follow-next-reads, find
  Local diagnostics:
    validate-install, diagnose-capabilities, discover-jsonl,
    structured-tool-events, workflow-repeat-finder, forensics,
    local-evidence-diagnostic, failure-analysis
  Eval/benchmark/proof machinery:
    benchmark, benchmark-real-history-impact, mechanism-eval,
    eval-decision-loop, agent-use-value-*, validate-harness-artifacts,
    harness-*, real-history-*, archive, and embedding experiments
"""


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
    ask.add_argument("query", help="User wording to classify into a JSONL first route.")
    add_common_args(ask)
    ask.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Execute the selected route only when it is an allow-listed JSONL "
            "semantic-debug or no-evidence route; unsupported routes return a fail-closed contract."
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
                "fielded-bm25 explicitly opts into direct in-memory SQLite FTS5/BM25 record retrieval; "
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
    read_more.add_argument("--projection", choices=("full", "compact"), default="full")
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
            "fielded-bm25 explicitly opts into direct in-memory SQLite FTS5/BM25 record retrieval; "
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
    parser.add_argument("--timezone", default=None)
    parser.add_argument("--timezone-offset", default="+00:00")
    parser.add_argument("--now", default=None)
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
            "Opt-in local persistent segment cache directory for explicit fielded-bm25 retrieval. "
            "No cache is written unless this path is supplied."
        ),
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


def payload_from_args(args: argparse.Namespace) -> tuple[dict, object, object]:
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    cwd = Path(args.cwd).expanduser().resolve()
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
    limits = Limits(
        max_files=args.max_files,
        max_bytes=args.max_bytes,
        max_matches=args.max_matches,
        max_elapsed=args.max_elapsed,
        hard_cap_files=args.hard_cap_files,
        hard_cap_bytes=args.hard_cap_bytes,
    )
    intent = infer_query(args.query, now=now, role=args.role)
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


def _run_ask_execute(args: argparse.Namespace, *, cwd: str, route_payload: dict) -> dict:
    route = route_payload["ask_router"]
    execution_kind = execution_kind_for_route_family(route.get("route_family", ""))
    selected_route = {
        "chosen_route": route.get("chosen_route"),
        "route_family": route.get("route_family"),
        "first_action_category": route.get("first_action_category"),
        "why_it_applies": route.get("why_it_applies"),
        "structured_execution_query": route.get("structured_execution_query"),
        "first_command": route.get("first_command"),
        "explicit_source_root_count": len(getattr(args, "root", []) or []),
        "read_specialization": route.get("read_specialization"),
        "inspect_after": route.get("inspect_after", []),
        "stop_guidance": route.get("stop_guidance"),
        "manual_search_guidance": route.get("manual_search_guidance"),
    }
    contract = {
        "ask_execute": {
            "schema_version": "ask_execute_route_fusion.v1",
            "query": args.query,
            "cwd": cwd,
            "selected_route": selected_route,
            "execution_status": "unsupported_route",
            "execution": {
                "status": "unsupported_route",
                "route_family": route.get("route_family"),
                "allowlist": [
                    "what-happened-since",
                    "semantic-debug",
                    "no-evidence-report",
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
            "answer_action_contract": {
                "answer_status": "not-ready/needs-more-proof",
                "read": route.get("inspect_after", []),
                "stop": route.get("stop_guidance"),
                "only_continue_if": route.get("manual_search_guidance"),
            },
        }
    }
    if execution_kind is None:
        contract["ask_execute"]["execution"]["reason"] = (
            "Selected route is outside ask --execute initial allow-list; run the normal first_command manually if intended."
        )
        return contract

    report_output = _ask_execute_report_output(args, execution_kind)
    execution_args = _execution_args_from_ask(
        args,
        command=execution_kind,
        report_output=report_output,
        query=route.get("structured_execution_query") or args.query,
    )
    if execution_kind == "what-happened-since":
        executed_payload = _build_status_anchor_report_output_payload(execution_args)
        output_key = "status_anchor"
    elif execution_kind == "semantic-debug":
        executed_payload = _build_semantic_debug_report_output_payload(execution_args)
        output_key = "semantic_debug"
    elif execution_kind == "no-evidence-report":
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

    compact = executed_payload[output_key]
    contract["ask_execute"]["execution"].update(
        {
            "status": "executed",
            "command_family": execution_kind,
            "argv": _structured_execution_argv(execution_args),
            "report_written": compact.get("report_written"),
            "output_key": output_key,
        }
    )
    contract["ask_execute"]["execution_status"] = "executed"
    contract["ask_execute"]["executed_output"] = _compact_executed_output_for_ask(output_key=output_key, compact=compact)
    contract["ask_execute"]["answer_action_contract"] = _answer_action_contract_from_executed_output(
        output_key=output_key,
        compact=compact,
        selected_route=selected_route,
        query=args.query,
        cwd=cwd,
    )
    return contract


def _ask_execute_report_output(args: argparse.Namespace, execution_kind: str) -> str:
    if args.report_output:
        path = Path(args.report_output).expanduser()
    else:
        path = Path(args.cwd).expanduser().resolve() / ".private" / "jsonl" / f"ask-execute-{execution_kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _execution_args_from_ask(
    args: argparse.Namespace,
    *,
    command: str,
    report_output: str,
    query: str | None = None,
) -> argparse.Namespace:
    values = vars(args).copy()
    values.update(
        {
            "command": command,
            "query": query if query is not None else args.query,
            "format": "json",
            "report_output": report_output,
        }
    )
    return argparse.Namespace(**values)


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
    for root in getattr(args, "root", []) or []:
        argv.extend(["--root", root])
    if getattr(args, "allow_arbitrary_root", False):
        argv.append("--allow-arbitrary-root")
    if getattr(args, "hard_cap_files", None) is not None:
        argv.extend(["--hard-cap-files", str(args.hard_cap_files)])
    if getattr(args, "hard_cap_bytes", None) is not None:
        argv.extend(["--hard-cap-bytes", str(args.hard_cap_bytes)])
    argv.extend(["--report-output", args.report_output])
    return argv


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
            "core_convo_first_read_plan",
            "target_historical_source_window_reads",
            "repo_phase_category_evidence",
            "diagnostic_source_window_reads",
        ],
        "core_convo_first_read_plan": core_convo_first_read_plan,
        "target_historical_source_window_reads": target_reads,
        "target_historical_source_window_read_count": len(target_reads),
        "diagnostic_source_window_reads": diagnostic_reads,
        "diagnostic_source_window_read_count": len(diagnostic_reads),
        "diagnostic_policy": {
            "current_adjacent_same_loop_refs_are_default_final_support": False,
            "actionability": (
                "diagnostic-only unless the user explicitly asks to audit current output, "
                "the current route, the current lane, or a prior answer/report"
            ),
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
            "answer_status": "inspect_target_history_before_answer",
            "required_read_order": [
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
        if _devlog_read_is_invalid_default_target_artifact(row, candidate=candidate) and not artifact_audit_requested:
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


def _build_semantic_debug_report_output_payload(args: argparse.Namespace) -> dict:
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
    Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
    packet = payload["semantic_debug"]
    if _semantic_debug_is_scoped_no_evidence(packet):
        return _short_semantic_debug_no_evidence_report_written_payload(packet, args.report_output)
    return _short_semantic_debug_report_written_payload(packet, args.report_output)


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
        fielded_bm25_index_dir=getattr(args, "fielded_bm25_index_dir", None),
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
    )
    Path(args.report_output).expanduser().write_text(render(payload, "json"), encoding="utf-8")
    if _agent_find_is_no_evidence_report(payload["agent_find"]):
        return _short_no_evidence_report_written_payload(payload, args.report_output)
    return _short_report_written_payload(payload, args.report_output)


def _short_semantic_debug_report_written_payload(packet: dict, report_output: str) -> dict:
    payload = {
        "semantic_debug": {
            "schema_version": packet["schema_version"],
            "status": packet["status"],
            "query": packet["query"],
            "question_family": packet["question_family"],
            "report_written": str(Path(report_output).expanduser()),
            "candidate_count": len(packet["strongest_candidates"]),
            "event_count": packet["tool_action_proof"]["event_count"],
            "source_locator_handoff": packet["source_locator_handoff"],
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


def _answer_action_contract_from_executed_output(
    *,
    output_key: str,
    compact: dict,
    selected_route: dict,
    query: str = "",
    cwd: str = ".",
) -> dict:
    if output_key == "status_anchor":
        source_tiers = compact.get("source_tiers") or {}
        answer_readiness = compact.get("answer_readiness") or {}
        return {
            "answer_status": (
                "status-package-ready-after-required-reads"
                if answer_readiness.get("answer_allowed")
                else "not-ready/needs-more-proof"
            ),
            "promotion_status": compact.get("status"),
            "promotion_reason": (
                "The latest-status route is executable, but receiving agents should inspect "
                "required primary raw JSONL reads before turning the packet into a factual status answer."
            ),
            "read": (source_tiers.get("required_primary") or [])
            or (compact.get("follow_through_plan") or {}).get("open_first")
            or compact.get("next_reads", [])[:4],
            "then_confirm": (compact.get("follow_through_plan") or {}).get("then_confirm") or [],
            "diagnostics_after": (compact.get("follow_through_plan") or {}).get("do_not_open_by_default") or [],
            "selected_anchor": compact.get("selected_anchor"),
            "answer_readiness": answer_readiness,
            "answer_brief": compact.get("answer_brief"),
            "session_activity": compact.get("session_activity"),
            "work_products": {
                "commits": ((compact.get("work_products") or {}).get("commits") or [])[:6],
            },
            "source_tiers": {
                "required_primary": (source_tiers.get("required_primary") or [])[:6],
                "required_secondary": (source_tiers.get("required_secondary") or [])[:6],
            },
            "clarification_request": compact.get("clarification_request"),
            "parent_main_anchor_shortlist": compact.get("parent_main_anchor_shortlist"),
            "follow_through_plan": compact.get("follow_through_plan"),
            "evidence_modes": (compact.get("evidence_contract") or {}).get("source_modes"),
            "source_role_labels": (compact.get("evidence_contract") or {}).get("source_role_labels"),
            "stop": (compact.get("follow_through_plan") or {}).get("stop_condition")
            or selected_route.get("stop_guidance"),
            "only_continue_if": selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
        }
    if output_key == "coordinator_context":
        return {
            "answer_status": "context-package-ready-after-next-reads",
            "promotion_status": "committed_safe_context_package",
            "promotion_reason": (
                "The coordinator-context route is executable and committed-safe, "
                "but receiving agents should inspect required next reads before "
                "turning it into a substantive handoff or next action."
            ),
            "read": compact.get("required_next_reads") or [],
            "then_use": {
                "current_state": compact.get("current_state"),
                "active_task_handles": compact.get("active_task_handles"),
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
        repo_evidence = compact.get("repo_phase_category_evidence") or {}
        failure_analysis = compact.get("failure_analysis") or {}
        return {
            "answer_status": "inspect_target_history_before_answer",
            "promotion_status": "unpromoted_devlog_failure_analysis_source_reach_package",
            "promotion_reason": (
                "The devlog/failure-analysis route is a source-reach package, not final prose; "
                "open target historical source-window reads before answering."
            ),
            "core_convo_first_read_plan": core_plan,
            "read": core_plan.get("read") or target_reads,
            "then_use": {
                "failure_analysis_devlog_brief": failure_analysis.get("devlog_brief"),
                "repo_phase_category_evidence": repo_evidence,
            },
            "diagnostics_after": core_plan.get("diagnostics_after")
            or list(compact.get("diagnostic_source_window_reads") or []),
            "diagnostic_policy": compact.get("diagnostic_policy"),
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": (compact.get("answer_contract") or {}).get("only_continue_if")
            or selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
            "linked_reports": compact.get("linked_reports"),
        }
    if output_key == "semantic_debug":
        handoff = compact.get("answer_ready_handoff") or {}
        core_plan = compact.get("core_convo_first_read_plan") or handoff.get("core_convo_first_read_plan") or {}
        orchestration_bundle = compact.get("historical_orchestration_role_evidence_bundle") or {}
        orchestration_plan = compact.get("historical_orchestration_provenance_final_support_plan") or {}
        orchestration_reads = (
            orchestration_bundle.get("required_role_reads")
            if orchestration_bundle.get("status") == "answer_ready"
            else orchestration_bundle.get("missing_role_recovery_refs")
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
        expectation_mismatch_plan = compact.get("expectation_mismatch_plan") or {}
        target_proof_handoff = _semantic_debug_target_proof_handoff(
            compact=compact,
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
        prefer_named_task_answer = _named_task_clean_answer_surface_wanted(
            query=query,
            route_family=route_family,
        )
        named_task_action_proof = _named_task_action_proof_handoff(
            query=query,
            cwd=cwd,
            route_family=route_family,
            target_proof_available=bool(target_proof_handoff),
            allow_current_proof_with_target=prefer_named_task_answer,
        )
        if (
            target_proof_handoff
            and prefer_named_task_answer
            and named_task_action_proof.get("status") == "current_named_task_proof_refs_missing"
        ):
            named_task_action_proof = {}
        route_first_reads = (
            orchestration_reads
            if route_family == "orchestration_provenance" and orchestration_reads
            else expectation_mismatch_plan.get("read")
            if route_family == "expectation_mismatch" and expectation_mismatch_plan
            else process_actual_use_required_reads
            if route_family == "process_actual_use" and process_actual_use_required_reads
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
        if named_task_action_proof.get("status") == "current_named_task_proof_refs_available":
            answer_status = "answer-ready-after-source-inspection"
            promotion_status = "semantic_debug_current_named_task_proof_ready_after_source_inspection"
            promotion_reason = (
                "ask --execute semantic-debug found current named task/review/commit proof refs; "
                "inspect those refs before answering and use stale/session diagnostics only for named gaps."
            )
            source_inspection_boundary = {
                "decision": "answer_after_source_inspection",
                "inspect_required_primary_proof_before_answer": True,
                "required_primary_proof_source": "current_named_task_review_commit_refs",
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
        elif target_proof_handoff:
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
                if route_family in {"action_proof", "semantic_debug", "provenance"}
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
            or [],
            "diagnostic_policy": (
                "expectation mismatch diagnostics are default non-actionable until expected/observed/divergence reads leave a named gap"
                if route_family == "expectation_mismatch" and expectation_mismatch_plan
                else None
            ),
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": (
                named_task_action_proof.get("only_continue_if")
                if named_task_action_proof
                else None
            )
            or (
                (handoff.get("primary_source_followthrough") or {}).get("only_continue_if")
                or compact.get("no_evidence", {}).get("read_more_policy")
                or selected_route.get("manual_search_guidance")
            ),
            "saved_report": compact.get("report_written"),
        }
        if source_inspection_boundary:
            contract["source_inspection_boundary"] = source_inspection_boundary
        if target_proof_handoff:
            contract["target_proof_handoff"] = target_proof_handoff
            contract["diagnostics_after"] = (
                target_proof_handoff.get("secondary_context_after_source_inspection")
                or []
            ) + (target_proof_handoff.get("diagnostics_after") or [])[:4]
        if named_task_action_proof:
            contract["named_task_action_proof_handoff"] = named_task_action_proof
            contract["diagnostics_after"] = (
                named_task_action_proof.get("diagnostics_after")
                or contract.get("diagnostics_after")
                or []
            )
            contract["stop"] = named_task_action_proof.get("stop") or contract["stop"]
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
        answer_status = (
            "semantic-wrapup-ready-after-required-primary-proof"
            if answer_ready.get("answer_ready") is True
            else "not-ready/needs-more-proof"
        )
        return {
            "answer_status": answer_status,
            "promotion_status": "unpromoted_semantic_wrapup_execution",
            "promotion_reason": (
                "ask --execute semantic-wrapup is a source-backed evidence handoff, "
                "not a final answer oracle; inspect required_primary_proof refs first."
            ),
            "internal_answer_status": compact.get("status"),
            "read": required_primary_reads or next_reads[:6],
            "then_use": {
                "failure_modes": "semantic_wrapup.failure_modes",
                "fix_suggestions": "semantic_wrapup.fix_suggestions",
                "required_primary_proof": "semantic_wrapup.next_reads[read_role=required_primary_proof]",
                "answer_ready_contract": "semantic_wrapup.answer_ready_contract",
                "answer_use_contract": "semantic_wrapup.answer_use_contract",
            },
            "failure_modes": (compact.get("failure_modes") or [])[:5],
            "fix_suggestions": (compact.get("fix_suggestions") or [])[:5],
            "read_role_summary": read_role_summary,
            "source_inspection_boundary": {
                "inspect_required_primary_proof_before_answer": True,
                "optional_diagnostics_default_actionable": False,
                "source_use_required": answer_ready.get("source_use_required")
                or answer_use.get("source_use_required"),
                "stop_guidance": answer_ready.get("stop_guidance") or answer_use.get("stop_guidance"),
            },
            "diagnostics_after": [
                read
                for read in next_reads
                if isinstance(read, dict) and read.get("read_role") != "required_primary_proof"
            ][:5],
            "stop": answer_ready.get("stop_guidance")
            or answer_use.get("stop_guidance")
            or selected_route.get("stop_guidance"),
            "only_continue_if": selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written")
            or (compact.get("context_budget") or {}).get("report_written"),
        }
    if _promotable_bounded_no_evidence_contract(compact=compact, selected_route=selected_route):
        return {
            "answer_status": "answer-scoped-no-evidence-now",
            "promotion_status": "promoted_scoped_no_evidence",
            "promotion_scope": "not_found_in_searched_scope",
            "scoped_no_evidence_contract": _scoped_no_evidence_contract(compact),
            "read": compact.get("source_locator_handoff", {}).get("next_reads") or [],
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": compact.get("no_evidence", {}).get("read_more_policy")
            or selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
        }
    if _bounded_no_evidence_scoped_answer_ready(compact=compact, selected_route=selected_route):
        return {
            "answer_status": "answer-scoped-no-evidence-now",
            "promotion_status": "unpromoted",
            "promotion_reason": (
                "Scoped no-evidence is answer-ready for the searched scope, but caps "
                "or other promotion boundaries prevent stronger promotion or all-history absence."
            ),
            "promotion_scope": "not_found_in_searched_scope",
            "scoped_no_evidence_contract": _scoped_no_evidence_contract(compact),
            "read": compact.get("source_locator_handoff", {}).get("next_reads") or [],
            "stop": selected_route.get("stop_guidance"),
            "only_continue_if": compact.get("no_evidence", {}).get("read_more_policy")
            or selected_route.get("manual_search_guidance"),
            "saved_report": compact.get("report_written"),
        }
    return {
        "answer_status": "not-ready/needs-more-proof",
        "promotion_status": "unpromoted",
        "promotion_reason": (
            "Executed output did not satisfy bounded scoped no-evidence promotion requirements."
        ),
        "internal_answer_status": compact.get("status"),
        "scoped_no_evidence_contract": (
            _scoped_no_evidence_contract(compact) if isinstance(compact.get("no_evidence"), dict) else None
        ),
        "read": compact.get("source_locator_handoff", {}).get("next_reads") or [],
        "stop": selected_route.get("stop_guidance"),
        "only_continue_if": compact.get("no_evidence", {}).get("read_more_policy")
        or selected_route.get("manual_search_guidance"),
        "saved_report": compact.get("report_written"),
    }


def _unpromoted_semantic_debug_answer_status(internal_status: object) -> str:
    if internal_status in {"inspect_recovery_first", "needs_investigation", "not-ready/needs-more-proof"}:
        return str(internal_status)
    return "inspect-before-answer"


_ASK_EXECUTE_TARGET_PROOF_ROUTE_FAMILIES = frozenset(
    {"remembered_user_wording", "semantic_debug", "provenance"}
)


def _semantic_debug_target_proof_handoff(*, compact: dict, route_family: str) -> dict:
    if route_family not in _ASK_EXECUTE_TARGET_PROOF_ROUTE_FAMILIES:
        return {}
    core_plan = compact.get("core_convo_first_read_plan") or {}
    reads = [read for read in core_plan.get("read") or [] if isinstance(read, dict)]
    primary_raw = [
        read
        for read in reads
        if read.get("target_source_role") == "target_historical_raw"
        and read.get("diagnostic_only") is not True
        and read.get("read_more_actionable") is not False
        and read.get("followthrough_actionable") is not False
    ]
    if not primary_raw:
        return {}
    secondary_context = [
        read
        for read in reads
        if read.get("target_source_role") in {"target_historical_artifact", "legitimate_context"}
    ][:6]
    diagnostics_after = [
        read
        for read in (core_plan.get("diagnostics_after") or [])
        if isinstance(read, dict)
    ][:6]
    return {
        "schema_version": 1,
        "status": "target_primary_proof_refs_available",
        "route_family": route_family,
        "answer_readiness_decision": "answer_after_source_inspection",
        "required_primary_proof": primary_raw[:6],
        "secondary_context_after_source_inspection": secondary_context,
        "diagnostics_after": diagnostics_after,
        "source_boundaries": {
            "required_primary_proof": "target_historical_raw refs must be opened before answering",
            "secondary_context": "historical artifacts and legitimate context are secondary joins after raw/source inspection",
            "diagnostics": "current-run, same-loop, stale, and post-hoc diagnostics are not final support by default",
        },
    }


_ASK_EXECUTE_NAMED_TASK_PROOF_ROUTE_FAMILIES = frozenset(
    {"action_proof", "semantic_debug", "provenance"}
)


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
    for task_id in task_ids[:2]:
        task_rows, task_boundaries = _current_named_task_action_proof_rows(
            cwd_path,
            task_id,
            include_selection_rationale=include_selection_rationale,
        )
        rows.extend(task_rows)
        boundaries.extend(task_boundaries)
    rows = _dedupe_action_contract_rows(rows)
    if rows:
        return {
            "schema_version": 1,
            "status": "current_named_task_proof_refs_available",
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
    claim_boundary_non_support = no_evidence.get("candidate_interpretation") == "claim_boundary_non_support"
    if _positive_int(selected_route.get("explicit_source_root_count")) <= 0 and not claim_boundary_non_support:
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
    searched_scope = compact.get("searched_scope") if isinstance(compact.get("searched_scope"), dict) else {}
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
    return {
        "answer_scope": no_evidence.get("answer_scope"),
        "searched_files": searched_scope.get("searched_files"),
        "searched_bytes": searched_scope.get("searched_bytes"),
        "searched_root_kinds": searched_scope.get("root_kinds", []),
        "searched_root_privacy_labels": searched_scope.get("root_privacy_labels", []),
        "selected_root_count": scope_counts.get("selected_root_count"),
        "excluded_root_count": scope_counts.get("excluded_root_count"),
        "missing_root_count": scope_counts.get("missing_root_count"),
        "cap_hit": cap_hit.get("hit"),
        "caps_hit": cap_hit.get("caps_hit", []),
        "outside_selected_roots": no_evidence.get("outside_selected_roots"),
        "not_all_history_absence": no_evidence.get("not_all_history_absence"),
        "candidate_interpretation": no_evidence.get("candidate_interpretation"),
        "read_more_required_before_scoped_no_evidence_answer": read_more_policy.get(
            "required_before_scoped_no_evidence_answer"
        ),
        "read_more_actionability": read_more_policy.get("actionability"),
        "saved_report": compact.get("report_written"),
    }


def _positive_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _compact_executed_output_for_ask(*, output_key: str, compact: dict) -> dict:
    if output_key == "status_anchor":
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "query": compact.get("query"),
            "routing": compact.get("routing"),
            "summary": compact.get("summary"),
            "selected_anchor": compact.get("selected_anchor"),
            "parent_main_anchor_shortlist": compact.get("parent_main_anchor_shortlist"),
            "answer_readiness": compact.get("answer_readiness"),
            "source_tiers": {
                "tier_order": (compact.get("source_tiers") or {}).get("tier_order"),
                "counts": (compact.get("source_tiers") or {}).get("counts"),
                "required_primary": ((compact.get("source_tiers") or {}).get("required_primary") or [])[:6],
                "required_secondary": ((compact.get("source_tiers") or {}).get("required_secondary") or [])[:6],
                "diagnostic_optional": ((compact.get("source_tiers") or {}).get("diagnostic_optional") or [])[:6],
                "source_mode_order": (compact.get("source_tiers") or {}).get("source_mode_order"),
                "source_role_labels": (compact.get("source_tiers") or {}).get("source_role_labels"),
            },
            "answer_brief": compact.get("answer_brief"),
            "session_activity": compact.get("session_activity"),
            "work_products": {
                "commits": ((compact.get("work_products") or {}).get("commits") or [])[:6],
            },
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
            "current_state": compact.get("current_state"),
            "active_task_handles": compact.get("active_task_handles"),
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
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "report_written": compact.get("report_written"),
            "linked_reports": compact.get("linked_reports"),
            "source_read_order": compact.get("source_read_order"),
            "core_convo_first_read_plan": compact.get("core_convo_first_read_plan"),
            "target_historical_source_window_reads": compact.get("target_historical_source_window_reads", [])[:5],
            "target_historical_source_window_read_count": compact.get(
                "target_historical_source_window_read_count"
            ),
            "repo_phase_category_evidence": compact.get("repo_phase_category_evidence"),
            "failure_analysis": compact.get("failure_analysis"),
            "diagnostic_source_window_reads": compact.get("diagnostic_source_window_reads", [])[:5],
            "diagnostic_source_window_read_count": compact.get("diagnostic_source_window_read_count"),
            "diagnostic_policy": compact.get("diagnostic_policy"),
            "privacy_flags": compact.get("privacy_flags"),
            "message": (
                "Full devlog/failure-analysis source-reach package written to report_output; "
                "open target historical source-window reads before current/adjacent diagnostics."
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
        return {
            "schema_version": compact.get("schema_version"),
            "status": compact.get("status"),
            "report_written": compact.get("report_written"),
            "question_family": compact.get("question_family"),
            "candidate_count": compact.get("candidate_count"),
            "event_count": compact.get("event_count"),
            "answer_ready_decision": compact.get("answer_ready_decision")
            or (compact.get("answer_ready_handoff") or {}).get("decision"),
            "answer_readiness_summary": compact.get("answer_readiness_summary"),
            "historical_orchestration_provenance_final_support_plan": compact.get(
                "historical_orchestration_provenance_final_support_plan"
            ),
            "historical_orchestration_role_evidence_bundle": compact.get(
                "historical_orchestration_role_evidence_bundle"
            ),
            "core_convo_first_read_plan": compact.get("core_convo_first_read_plan"),
            "expectation_mismatch_plan": compact.get("expectation_mismatch_plan"),
            "process_actual_use_final_support_read_plan": compact.get(
                "process_actual_use_final_support_read_plan"
            ),
            "process_actual_use_bucket_evidence_bundle": compact.get(
                "process_actual_use_bucket_evidence_bundle"
            ),
            "decision_context_summary": compact.get("decision_context_summary"),
            "audit_plan_summary": compact.get("audit_plan_summary"),
            "next_reads": compact.get("next_reads", [])[:3],
            "no_evidence": compact.get("no_evidence"),
            "message": compact.get("message"),
        }
    return {
        "schema_version": compact.get("schema_version"),
        "status": compact.get("status"),
        "report_written": compact.get("report_written"),
        "searched_scope": compact.get("searched_scope"),
        "scope_counts": compact.get("scope_counts"),
        "cap_hit": compact.get("cap_hit"),
        "no_evidence": compact.get("no_evidence"),
        "message": compact.get("message"),
    }


_ASK_ANSWER_SUPPORTED_ROUTE_FAMILIES = frozenset(
    {
        "action_proof",
        "bounded_no_evidence",
        "expectation_mismatch",
        "orchestration_provenance",
        "process_actual_use",
        "provenance",
        "remembered_user_wording",
        "semantic_wrapup",
        "semantic_debug",
        "latest_status",
    }
)
_ASK_ANSWER_EXPLICIT_FAIL_STATUSES = frozenset(
    {
        "not-ready/needs-more-proof",
        "not-ready/missing-current-named-task-proof",
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
    {"action_proof", "expectation_mismatch", "provenance", "semantic_debug"}
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
    "work only in this existing worktree",
    "work only in this isolated worktree",
    "you are not alone in the codebase",
    "task: execute",
    "task branch:",
    "current branch checkpoint:",
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
    r"\byou are (?:(?:the|an?)\b.{0,160}\b)?(implementer|re-?reviewer|reviewer|worker|coordinator)\b",
    re.IGNORECASE | re.DOTALL,
)
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
        "codex_home": str(Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"),
        "max_files": int(getattr(args, "max_files", 200) or 200),
        "max_bytes": int(getattr(args, "max_bytes", 100 * 1024 * 1024) or 100 * 1024 * 1024),
        "max_elapsed": float(getattr(args, "max_elapsed", 15.0) or 15.0),
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
            },
        }
    }
    answer = base["ask_answer"]

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

    scoped = action.get("scoped_no_evidence_contract")
    if action.get("answer_status") == "answer-scoped-no-evidence-now" and isinstance(scoped, dict):
        return _ask_answer_scoped_no_evidence(answer, scoped, cwd=cwd)

    can_follow_raw_proof = _ask_answer_can_follow_raw_proof_despite_status(
        action=action,
        route_family=route_family,
    )
    if action.get("answer_status") in _ASK_ANSWER_EXPLICIT_FAIL_STATUSES and not can_follow_raw_proof:
        return _ask_answer_fail_closed(
            answer,
            f"ask --execute contract is {action.get('answer_status')}",
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
    proof_refs = _collect_ask_answer_proof_refs(action)
    if action_chronology_wanted:
        proof_refs = _dedupe_ask_answer_proof_refs(
            _action_chronology_candidate_refs(action) + proof_refs
        )
    if work_episode.get("primary_proof_refs"):
        proof_refs = _dedupe_ask_answer_proof_refs(
            list(work_episode.get("primary_proof_refs") or []) + proof_refs
        )
    opened = _open_ask_answer_proof_refs(proof_refs, cwd=cwd)
    usable = [item for item in opened if item.get("status") == "opened" and item.get("summary")]
    if not usable:
        if work_episode and work_episode.get("status") != "not_applicable":
            return _ask_answer_fail_closed(
                answer,
                "work episode resolver did not find readable primary behavior proof for the query episode",
                _work_episode_next_proof_step(work_episode, action),
            )
        return _ask_answer_fail_closed(
            answer,
            "answer contract did not expose readable local proof refs",
            _next_proof_step_from_action(action),
        )

    answer_opened = usable
    if route_family == "remembered_user_wording":
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
                answer["source_contract"]["remembered_user_wording_recovery"] = {
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
                    answer["source_contract"]["remembered_user_wording_recovery"] = {
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
                    answer["source_contract"]["remembered_user_wording_recovery"] = {
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
                        answer["source_contract"]["remembered_user_wording_recovery"] = {
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
        expectation_gate = _expectation_mismatch_answer_proof_gate(action=action, opened=answer_opened)
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
        process_gate = _process_actual_use_answer_proof_gate(action=action, opened=answer_opened)
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
    if route_family == "expectation_mismatch":
        return _expectation_mismatch_source_role_explanation(next_step)
    if route_family in {"action_proof", "orchestration_provenance", "provenance", "semantic_debug"}:
        return _action_chronology_source_role_explanation(next_step)
    if route_family == "remembered_user_wording":
        return _remembered_user_wording_source_role_explanation(next_step)
    if route_family == "latest_status":
        return _latest_status_source_role_explanation(next_step)
    return None


def _expectation_mismatch_source_role_explanation(next_step: object) -> dict | None:
    if isinstance(next_step, list):
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
    repo_boundary = _scoped_no_evidence_current_repo_boundary(scoped=scoped, cwd=cwd)
    if repo_boundary:
        text += f" {repo_boundary}"
    if scoped.get("cap_hit") is True:
        caps_text = ", ".join(str(item) for item in (scoped.get("caps_hit") or [])[:4] if str(item).strip())
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
            "searched_files": searched_files,
            "searched_bytes": searched_bytes,
            "searched_root_kinds": root_kinds,
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
    return {"ask_answer": answer}


def _scoped_no_evidence_root_label(scoped: dict) -> str:
    privacy_labels = {str(item) for item in scoped.get("searched_root_privacy_labels") or [] if str(item).strip()}
    root_kinds = {str(item) for item in scoped.get("searched_root_kinds") or [] if str(item).strip()}
    transcript_kinds = {"codex_sessions", "codex_history", "claude_projects"}
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


def _ask_answer_can_follow_raw_proof_despite_status(*, action: dict, route_family: str) -> bool:
    if route_family != "action_proof" or action.get("answer_status") != "inspect_recovery_first":
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
    if named_handoff.get("status") == "current_named_task_proof_refs_available":
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
    return _dedupe_ask_answer_proof_refs(refs)


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
        if ref.get("kind") == "current_named_task_commit_evidence":
            summary = " ".join(
                part
                for part in (
                    str(ref.get("commit_time") or ""),
                    str(ref.get("commit") or ref.get("target") or ""),
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
    elif _action_chronology_requires_repo_or_process_roles(normalized):
        roles.append("repo_or_process_state_chronology")
    return _unique_preserving_order(roles)


def _action_chronology_requires_boundary_roles(query: str) -> bool:
    normalized = str(query or "").casefold()
    if re.search(r"\b(stop|stopped|stopping|closeout|closed out)\b", normalized):
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


def _expectation_mismatch_answer_proof_gate(*, action: dict, opened: list[dict]) -> dict:
    plan = action.get("expectation_mismatch_plan") if isinstance(action.get("expectation_mismatch_plan"), dict) else {}
    clean_by_facet: dict[str, list[dict]] = {
        facet: [] for facet in _ASK_ANSWER_EXPECTATION_MISMATCH_REQUIRED_FACETS
    }
    rejected: list[dict] = []
    rejected_lines: list[str] = []

    for item in opened:
        if item.get("status") != "opened" or not item.get("summary"):
            continue
        roles = _expectation_mismatch_opened_roles(item, plan=plan)
        source = _expectation_mismatch_opened_source_role(item)
        clean_allowed = source["clean_final_support"]
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
            public["rejection_reasons"] = source["reasons"]
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


def _process_actual_use_answer_proof_gate(*, action: dict, opened: list[dict]) -> dict:
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
        reject_reasons: list[str] = []
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
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
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
        "message_role": message.get("role"),
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


def _remembered_user_latest_recovery_public(recovery: dict) -> dict:
    return {
        "status": recovery.get("status"),
        "searched_scope": recovery.get("searched_scope"),
        "searched_files": recovery.get("searched_files"),
        "candidate_user_message_count": recovery.get("candidate_user_message_count"),
        "matched_user_message_count": recovery.get("matched_user_message_count"),
        "rejected_prompt_echo_ref_count": recovery.get("rejected_prompt_echo_ref_count"),
        "query_terms": recovery.get("query_terms") or [],
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
        if _looks_like_markdown_review_failure_answer_line(line) or _looks_like_markdown_fix_rereview_answer_line(line):
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
    if _action_chronology_answer_wanted(query=query, route_family=route_family):
        action_chronology_answer = _compose_action_chronology_answer(opened=opened)
        if action_chronology_answer:
            return action_chronology_answer
    if named.get("status") == "current_named_task_proof_refs_available":
        return _compose_named_task_answer(query=query, named=named, opened=opened)
    if route_family == "remembered_user_wording":
        return f"You said this in the opened user-message proof: {_short_join(summaries[:2])}"
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
    return re.sub(r"\s{2,}", " ", sanitized).strip()


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
        summary = str(item.get("summary") or "").strip()
        target = str(item.get("target") or "").strip()
        if not roles or not summary:
            continue
        label = ",".join(roles)
        pieces.append(f"{label}: {summary}" + (f" [{target}]" if target else ""))
    if not pieces:
        return None
    return (
        "Primary action chronology shows: "
        + _short_join(pieces[:4], limit=760)
        + " Boundary: task/review/status artifacts may corroborate this chronology, but they are not primary action proof."
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


def _compose_latest_status_answer(*, query: str, action: dict, opened: list[dict]) -> str | None:
    selected_anchor = action.get("selected_anchor") if isinstance(action.get("selected_anchor"), dict) else {}
    readiness = action.get("answer_readiness") if isinstance(action.get("answer_readiness"), dict) else {}
    if not selected_anchor or readiness.get("answer_allowed") is not True:
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
    for mode in failure_modes[:7]:
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
        ref_text = ", ".join(_semantic_wrapup_source_ref_text(ref) for ref in refs[:3])
        proof_text = _short_join(proof_summaries, limit=280) if proof_summaries else "proof opened from required_primary_proof refs"
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
            return _short_join([str(example["snippet"]).strip()], limit=180)
    title = str(mode.get("title") or mode.get("failure_mode") or "Repeated correction").strip()
    return title.replace("_", " ")


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
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else Path.home() / ".codex"
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else Path.home() / ".claude"
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

    args = build_parser().parse_args(effective_argv)
    if args.command == "ask":
        cwd = str(Path(args.cwd).expanduser().resolve())
        payload = build_ask_router_payload(args.query, cwd=cwd)
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
    if args.command == "validate-install":
        payload = validate_install(
            package_root=Path(args.package_root).expanduser() if args.package_root else None,
            codex_home=Path(args.codex_home).expanduser() if args.codex_home else None,
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
        rendered = render(payload, args.format)
        if args.report_output:
            Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
            packet = payload["semantic_debug"]
            if _semantic_debug_is_scoped_no_evidence(packet):
                payload = _short_semantic_debug_no_evidence_report_written_payload(packet, args.report_output)
            else:
                payload = _short_semantic_debug_report_written_payload(packet, args.report_output)
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
        payload = build_status_anchor_payload(args)
        print(render(payload, args.format))
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
        _source_plan, _intent, plan = payload_from_args(args)
        payload = build_source_expansion_payload(
            request=ExpansionRequest(
                target=args.target,
                mode=args.mode,
                scope=args.scope,
                before=args.before,
                after=args.after,
                next_count=args.next_count,
                max_records=args.max_records,
                projection=args.projection,
                compact_text_limit=args.compact_text_limit,
            ),
            plan=plan,
            default_tz=timezone_from_args(args.timezone, args.timezone_offset),
            cwd=Path(args.cwd).expanduser().resolve(),
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
            fielded_bm25_index_dir=getattr(args, "fielded_bm25_index_dir", None),
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
            output_format=args.format,
            report_output=args.report_output,
            compact_provenance_handoff=getattr(args, "compact_provenance_handoff", False),
            evidence_context_window=getattr(args, "evidence_context_window", 0),
            no_evidence_mode=args.command == "no-evidence-report",
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
    if getattr(args, "report_output", None):
        Path(args.report_output).expanduser().write_text(rendered, encoding="utf-8")
        if "agent_find" in payload:
            if _agent_find_is_no_evidence_report(payload["agent_find"]):
                payload = _short_no_evidence_report_written_payload(payload, args.report_output)
            else:
                payload = _short_report_written_payload(payload, args.report_output)
            rendered = render(payload, args.format)
    print(rendered)
    return 0


def _agent_find_is_no_evidence_report(report: dict) -> bool:
    no_evidence = report.get("no_evidence_report") if isinstance(report.get("no_evidence_report"), dict) else {}
    return report.get("status") == "no_evidence" or bool(no_evidence.get("needed"))


def _semantic_debug_is_scoped_no_evidence(packet: dict) -> bool:
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
        answer_ready.get("decision") == "scoped_no_evidence"
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
    return {
        "semantic_debug": {
            "schema_version": packet["schema_version"],
            "status": packet["status"],
            "query": packet["query"],
            "report_written": str(Path(report_output).expanduser()),
            "answer_ready_decision": answer_ready.get("decision"),
            "searched_scope": _no_evidence_searched_scope_summary(no_evidence),
            "scope_counts": _no_evidence_scope_counts(no_evidence),
            "cap_hit": _no_evidence_cap_hit(no_evidence),
            "no_evidence": boundary,
            "message": "Full semantic-debug scoped no-evidence report written to report_output; stdout is a scoped summary.",
        }
    }


def _no_evidence_searched_scope_summary(
    no_evidence: dict,
    *,
    searched_files: int | None = None,
    searched_bytes: int | None = None,
) -> dict:
    searched = no_evidence.get("searched_scope") if isinstance(no_evidence.get("searched_scope"), dict) else {}
    files = searched.get("searched_files") if searched_files is None else searched_files
    bytes_ = searched.get("searched_bytes") if searched_bytes is None else searched_bytes
    roots = [root for root in searched.get("roots") or [] if isinstance(root, dict)]
    return {
        "searched_files": int(files or 0),
        "searched_bytes": int(bytes_ or 0),
        "result_meaning": searched.get("result_meaning"),
        "root_kinds": _unique_preserving_order([str(root.get("kind") or "") for root in roots]),
        "root_privacy_labels": _unique_preserving_order(
            [str(root.get("privacy_label") or "") for root in roots]
        ),
    }


def _no_evidence_scope_counts(no_evidence: dict) -> dict:
    searched = no_evidence.get("searched_scope") if isinstance(no_evidence.get("searched_scope"), dict) else {}
    selected_roots = no_evidence.get("selected_roots")
    if isinstance(selected_roots, list):
        selected_count = len(selected_roots)
    else:
        selected_count = len(searched.get("roots") or [])

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
