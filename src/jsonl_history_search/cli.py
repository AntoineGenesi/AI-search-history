from __future__ import annotations

import argparse
import re
import sys
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
from .latest_user_messages import build_latest_user_messages_payload
from .lineage import build_lineage_payload
from .live_terminal_protocol import build_live_terminal_protocol_payload
from .manifest import build_manifest_report
from .mechanism_eval import DEFAULT_FIXTURE as DEFAULT_MECHANISM_EVAL_FIXTURE
from .mechanism_eval import build_mechanism_eval_payload
from .measured_benchmark import DEFAULT_FIXTURE as DEFAULT_MEASURED_FIXTURE
from .measured_benchmark import build_measured_benchmark_payload
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
        return {
            "answer_status": (
                "status-package-ready-after-required-reads"
                if (compact.get("answer_readiness") or {}).get("answer_allowed")
                else "not-ready/needs-more-proof"
            ),
            "promotion_status": compact.get("status"),
            "promotion_reason": (
                "The latest-status route is executable, but receiving agents should inspect "
                "required primary raw JSONL reads before turning the packet into a factual status answer."
            ),
            "read": (compact.get("follow_through_plan") or {}).get("open_first") or compact.get("next_reads", [])[:4],
            "then_confirm": (compact.get("follow_through_plan") or {}).get("then_confirm") or [],
            "diagnostics_after": (compact.get("follow_through_plan") or {}).get("do_not_open_by_default") or [],
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
        named_task_action_proof = _named_task_action_proof_handoff(
            query=query,
            cwd=cwd,
            route_family=route_family,
        )
        route_first_reads = (
            orchestration_reads
            if route_family == "orchestration_provenance" and orchestration_reads
            else expectation_mismatch_plan.get("read")
            if route_family == "expectation_mismatch" and expectation_mismatch_plan
            else named_task_action_proof.get("read")
            if named_task_action_proof
            else core_plan.get("read")
        )
        contract = {
            "answer_status": _unpromoted_semantic_debug_answer_status(internal_status),
            "promotion_status": "unpromoted_semantic_debug_execution",
            "promotion_reason": (
                "ask --execute semantic-debug remains an execution convenience; promoted scoped answers "
                "are limited to bounded_no_evidence routes executed through no-evidence-report."
            ),
            "internal_answer_status": internal_status,
            "core_convo_first_read_plan": core_plan,
            "expectation_mismatch_plan": expectation_mismatch_plan if route_family == "expectation_mismatch" else None,
            "read": route_first_reads
            or orchestration_reads
            or expectation_mismatch_plan.get("read")
            or core_plan.get("read")
            or handoff.get("primary_source_followthrough")
            or compact.get("source_locator_handoff", {}).get("next_reads")
            or compact.get("next_reads")
            or [],
            "diagnostics_after": (
                expectation_mismatch_plan.get("diagnostics_after")
                if route_family == "expectation_mismatch" and expectation_mismatch_plan
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


def _named_task_action_proof_handoff(*, query: str, cwd: str, route_family: str) -> dict:
    if route_family != "action_proof":
        return {}
    task_ids = _ask_execute_named_task_ids(query)
    if not task_ids:
        return {}
    cwd_path = Path(cwd).expanduser().resolve()
    rows: list[dict] = []
    boundaries: list[str] = []
    for task_id in task_ids[:2]:
        task_rows, task_boundaries = _current_named_task_action_proof_rows(cwd_path, task_id)
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


def _ask_execute_named_task_ids(query: str) -> list[str]:
    task_ids: list[str] = []
    for match in _ASK_EXECUTE_TASK_ID_RE.finditer(query or ""):
        prefix = match.group(1).upper()
        if prefix in _ASK_EXECUTE_TASK_ID_MONTH_PREFIXES:
            continue
        task_id = f"{prefix}-{int(match.group(2))}"
        if task_id not in task_ids:
            task_ids.append(task_id)
    return task_ids


def _current_named_task_action_proof_rows(cwd: Path, task_id: str) -> tuple[list[dict], list[str]]:
    slug = task_id.lower()
    rows: list[dict] = []
    boundaries: list[str] = []
    task_paths = _matching_named_task_artifacts(cwd / "tasks", slug)
    review_paths = _matching_named_task_artifacts(cwd / "codex" / "state" / "reviews", slug)

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
    if _positive_int(selected_route.get("explicit_source_root_count")) <= 0:
        return False
    if compact.get("status") != "no_evidence":
        return False
    no_evidence = compact.get("no_evidence") if isinstance(compact.get("no_evidence"), dict) else {}
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
        "selected_root_count": scope_counts.get("selected_root_count"),
        "excluded_root_count": scope_counts.get("excluded_root_count"),
        "missing_root_count": scope_counts.get("missing_root_count"),
        "cap_hit": cap_hit.get("hit"),
        "caps_hit": cap_hit.get("caps_hit", []),
        "not_all_history_absence": no_evidence.get("not_all_history_absence"),
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
            "follow_through_plan": compact.get("follow_through_plan"),
            "evidence_contract": compact.get("evidence_contract"),
            "fail_closed_guidance": compact.get("fail_closed_guidance"),
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
        if args.execute or auto_execute_bounded:
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
    return {
        "searched_files": int(files or 0),
        "searched_bytes": int(bytes_ or 0),
        "result_meaning": searched.get("result_meaning"),
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
