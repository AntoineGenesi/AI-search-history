from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .query import infer_query


REPORT_KEY = "post_jsh763_date_timeline_miss_depth_diagnostic"
TASK_ID = "jsh-764-post-jsh-763-remaining-date-timeline-miss-depth-diagnostic"
DEFAULT_BENCHMARK = Path("benchmarks/paired_agent/jsh-763-package-search-opened-file-record-line-reach-under-caps.json")
DEFAULT_RAW_RUN = Path(".private/jsh-763/raw-runs/b29e1e1b6506.json")
DEFAULT_OUTPUT = Path("benchmarks/paired_agent/jsh-764-post-jsh-763-remaining-date-timeline-miss-depth-diagnostic.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/jsh-764-post-jsh-763-remaining-date-timeline-miss-depth-diagnostic.md")
DEFAULT_JSH711_REPORT = Path("reports/jsh-711-post-quantum-negative-learning-within-file-navigation-decision.json")
DEFAULT_JSH721_REPORT = Path("reports/jsh-721-date-timeline-runtime-anchor-timestamp-window-same-caps-decision.json")
DEFAULT_JSH722_REPORT = Path("reports/jsh-722-auditable-date-timeline-runtime-anchor-timestamp-window-decision.json")

CASE_IDS = (
    "real-jsh047c2-date-timeline-001",
    "real-jsh047c2-date-timeline-002",
    "real-jsh047c2-date-timeline-004",
    "real-jsh047c2-date-timeline-006",
)


def build_post_jsh763_date_timeline_diagnostic_payload(args: Any) -> dict[str, Any]:
    benchmark_path = Path(getattr(args, "benchmark", DEFAULT_BENCHMARK)).expanduser()
    raw_run_path = _resolve_optional_path(Path(getattr(args, "raw_run", DEFAULT_RAW_RUN)).expanduser())
    benchmark = _read_real_history_report(benchmark_path)
    raw_run = _read_json(raw_run_path) if raw_run_path else {}
    prior_reports = _prior_evidence(args)
    rows = [_case_row(case_id, benchmark, raw_run) for case_id in CASE_IDS]
    report = {
        "schema_version": 1,
        "status": "ok" if len(rows) == len(CASE_IDS) else "incomplete",
        "task": TASK_ID,
        "data_source": "committed_jsh763_benchmark_plus_ignored_raw_run_used_only_for_coarse_depth_and_query_labels",
        "input_artifacts": {
            "benchmark_label": benchmark_path.name,
            "benchmark_run_id": _safe_string(benchmark.get("run_id")),
            "raw_run_label": _safe_string(raw_run.get("run_id")) if raw_run else "unavailable",
            "raw_run_used": bool(raw_run),
            "prior_evidence_report_labels": [item["report_label"] for item in prior_reports],
        },
        "privacy_contract": _privacy_contract(),
        "behavior_boundary": {
            "scanner_search_behavior_changed": False,
            "timestamp_seek_behavior_changed": False,
            "sidecar_embedding_rg_behavior_changed": False,
            "live_or_model_send_performed": False,
            "fresh_benchmark_replay_performed": False,
            "raw_rg_live_gate_performed": False,
            "jsh_748_work_performed": False,
        },
        "claim_boundaries": {
            "token_savings_claimed": False,
            "final_answer_correctness_claimed": False,
            "lane_winner_claimed": False,
            "default_promotion_claimed": False,
            "broad_product_value_claimed": False,
        },
        "summary": _summary(rows, benchmark),
        "cost_buckets": _cost_buckets(benchmark, rows),
        "prior_negative_evidence": prior_reports,
        "rows": rows,
        "selected_next_value_producing_action": _selected_next_action(rows),
    }
    return {REPORT_KEY: report}


def render_post_jsh763_date_timeline_diagnostic_text(payload: dict[str, Any]) -> str:
    report = payload[REPORT_KEY]
    action = report["selected_next_value_producing_action"]
    return "\n".join(
        [
            "Post-JSH-763 date-timeline miss depth diagnostic",
            f"Status: {report['status']}",
            f"Benchmark run: {report['input_artifacts']['benchmark_run_id']}",
            f"Rows diagnosed: {report['summary']['case_count']}",
            f"Opened line-not-reached: {report['summary']['opened_line_not_reached_count']}",
            f"Source not opened: {report['summary']['source_not_opened_count']}",
            f"Next action: {action['action']}",
        ]
    )


def render_post_jsh763_date_timeline_diagnostic_markdown(payload: dict[str, Any]) -> str:
    report = payload[REPORT_KEY]
    summary = report["summary"]
    action = report["selected_next_value_producing_action"]
    lines = [
        "# JSH-764 Post-JSH-763 Date-Timeline Miss Depth Diagnostic",
        "",
        "## Scope",
        "",
        f"- Benchmark artifact: `{report['input_artifacts']['benchmark_label']}` run `{report['input_artifacts']['benchmark_run_id']}`",
        "- Ignored raw run was used only locally for coarse depth buckets, timestamp presence, and query-inference labels.",
        "- No scanner/search, timestamp seek, sidecar, embedding, rg, live-send, model-send, raw-rg live gate, JSH-748, or benchmark replay behavior changed.",
        "",
        "## Summary",
        "",
        "| Measure | Value |",
        "| --- | ---: |",
        f"| Cases diagnosed | {summary['case_count']} |",
        f"| Opened but record line not reached | {summary['opened_line_not_reached_count']} |",
        f"| Selected/queued but not opened | {summary['source_not_opened_count']} |",
        f"| Query date window present | {summary['query_date_window_present_count']} |",
        f"| Query date window usable as current filter | {summary['query_date_window_filter_usable_count']} |",
        f"| Expected record timestamp available | {summary['expected_record_timestamp_available_count']} |",
        "",
        "## Loss-Point Table",
        "",
        "| Case | Selected | Queued | Opened | Line reached | Final present | Opened order | Records read bucket | Target depth bucket | Additional needed bucket | Mechanism labels |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["rows"]:
        scope = row["committed_benchmark_diagnostics"]
        depth = row["depth_buckets"]
        lines.append(
            "| "
            f"`{_esc(row['case_id'])}` | "
            f"`{scope['selected']}` | `{scope['queued']}` | `{scope['opened']}` | "
            f"`{scope['line_reached']}` | `{scope['final_present']}` | "
            f"`{scope['opened_order_label']}` | `{scope['records_read_bucket']}` | "
            f"`{depth['target_depth_bucket']}` | `{depth['additional_records_needed_bucket']}` | "
            f"{', '.join(f'`{_esc(label)}`' for label in row['mechanism_classifications'])} |"
        )
    lines.extend(
        [
            "",
            "## Date Handle And Timestamp Diagnostics",
            "",
            "| Case | Date window present | Current filter usable | Date reason label | Timestamped expected record | Timestamp/index usable without expected-ref steering | Reason |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in report["rows"]:
        query = row["query_inference_labels"]
        ts = row["timestamp_seek_diagnostic"]
        lines.append(
            "| "
            f"`{_esc(row['case_id'])}` | `{query['date_window_present']}` | "
            f"`{query['date_window_filter_usable']}` | `{_esc(query['date_reason_label'])}` | "
            f"`{ts['expected_record_timestamp_available']}` | "
            f"`{ts['existing_timestamp_index_usable_without_expected_ref_steering']}` | "
            f"{_esc(ts['reason'])} |"
        )
    lines.extend(
        [
            "",
            "## Cost Buckets",
            "",
            f"- Local tool work: package baselines opened `{report['cost_buckets']['local_tool_work']['package_baseline_files_opened']}` files and parsed/scanned `{report['cost_buckets']['local_tool_work']['package_baseline_bytes_scanned_parsed_bucket']}` bytes in the JSH-763 committed run.",
            f"- Model-facing context/input proxy: package baselines emitted `{report['cost_buckets']['model_facing_context_input_proxy']['package_baseline_output_context_bytes_bucket']}` output/context bytes.",
            f"- Full agent-loop cost: `{report['cost_buckets']['full_agent_loop_availability']['status']}` because this offline diagnostic does not include retained live turn traces.",
            "",
            "## Prior Negative Evidence",
            "",
            "| Report | Scoped learning | Blocks current next action |",
            "| --- | --- | --- |",
        ]
    )
    for item in report["prior_negative_evidence"]:
        lines.append(
            f"| `{_esc(item['report_label'])}` | {_esc(item['scoped_learning'])} | `{item['blocks_selected_next_action']}` |"
        )
    lines.extend(
        [
            "",
            "## Selected Next Action",
            "",
            f"- Action: `{action['action']}`",
            f"- Rationale: {action['rationale']}",
            f"- Not selected: {', '.join(f'`{_esc(item)}`' for item in action['not_selected'])}",
            "",
            "## Privacy Assertions",
            "",
        ]
    )
    for key, value in report["privacy_contract"].items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines)


def _case_row(case_id: str, benchmark: dict[str, Any], raw_run: dict[str, Any]) -> dict[str, Any]:
    row = _benchmark_row(benchmark, case_id)
    raw_row = _raw_row(raw_run, case_id)
    ref_diag = _first_expected_ref_diag(row)
    raw_ref = _first_raw_expected_ref(raw_row)
    query_labels = _query_labels(raw_row)
    timestamp_diag = _timestamp_seek_diagnostic(raw_ref, query_labels)
    scope = _committed_scope(row, ref_diag)
    depth = _depth_buckets(ref_diag, raw_ref)
    labels = _mechanism_labels(scope, depth, query_labels, timestamp_diag)
    return {
        "case_id": case_id,
        "family": _safe_string(row.get("family")) or "unknown",
        "baseline": "package_search",
        "first_pass_hit": bool(row.get("first_pass_hit")),
        "expected_outcome": _safe_string(row.get("expected_outcome")),
        "committed_benchmark_diagnostics": scope,
        "depth_buckets": depth,
        "query_inference_labels": query_labels,
        "timestamp_seek_diagnostic": timestamp_diag,
        "mechanism_classifications": labels,
        "cost_bucket_view": {
            "local_tool_work": {
                "files_opened": _int(row.get("files_opened")),
                "records_parsed": _int(row.get("records_parsed")),
                "bytes_scanned_parsed_bucket": _bucket(_int(row.get("bytes_scanned")) + _int(row.get("bytes_parsed"))),
            },
            "model_facing_context_input_proxy": {
                "output_context_bytes_bucket": _bucket(_int(row.get("output_context_bytes") or row.get("context_bytes"))),
            },
            "full_agent_loop_availability": "unavailable_in_offline_committed_benchmark_report",
        },
    }


def _committed_scope(row: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    final_present = bool(ref.get("expected_record_present_in_final_candidate_refs")) or bool(
        ref.get("expected_record_present_in_final_candidate_groups")
    )
    opened_order = ref.get("expected_file_open_order")
    return {
        "selected": bool(ref.get("expected_file_selected_by_source_plan")),
        "queued": bool(ref.get("expected_file_queued_for_scan")),
        "opened": bool(ref.get("expected_file_opened_before_scan_stop")),
        "line_reached": bool(ref.get("expected_record_line_reached_before_elapsed_stop")),
        "final_present": final_present,
        "opened_order_label": f"opened_file_{int(opened_order):03d}" if opened_order else "not_opened",
        "records_read_bucket": _safe_string(ref.get("expected_file_progress_bucket")) or _bucket(_int(ref.get("expected_file_records_read_before_stop"))),
        "line_reach_status": _safe_string(ref.get("line_reach_status")) or "unknown",
        "records_parsed_bucket": _bucket(_int(row.get("records_parsed"))),
    }


def _depth_buckets(ref: dict[str, Any], raw_ref: dict[str, Any]) -> dict[str, Any]:
    reached = _int(ref.get("expected_file_records_read_before_stop"))
    target_depth = _int(raw_ref.get("line"))
    additional = max(target_depth - reached, 0) if target_depth else 0
    return {
        "target_depth_bucket": _bucket(target_depth) if target_depth else "unknown",
        "records_read_before_stop_bucket": _bucket(reached),
        "additional_records_needed_bucket": _bucket(additional) if additional else "unknown",
        "target_depth_source": "ignored_raw_run_expected_ref_line_used_locally_bucket_only" if target_depth else "unavailable",
        "exact_depth_values_included": False,
    }


def _query_labels(raw_row: dict[str, Any]) -> dict[str, Any]:
    query_text = _safe_string(raw_row.get("query_text"))
    intent = infer_query(query_text, now=datetime(2026, 6, 14, tzinfo=timezone.utc))
    reason = intent.date_reason or "none"
    return {
        "raw_query_text_included": False,
        "role_label": intent.role or "any",
        "date_window_present": bool(intent.date_window_start and intent.date_window_end),
        "date_window_filter_usable": bool(intent.date_window_filter),
        "date_reason_label": reason,
        "term_count_bucket": _bucket(len(intent.terms)),
        "artifact_hint_present": bool(intent.artifact_hints),
        "warning_labels": [str(item) for item in intent.warnings],
    }


def _timestamp_seek_diagnostic(raw_ref: dict[str, Any], query_labels: dict[str, Any]) -> dict[str, Any]:
    timestamp_available = _expected_record_has_timestamp(raw_ref)
    date_filter = bool(query_labels["date_window_filter_usable"])
    return {
        "expected_record_timestamp_available": timestamp_available,
        "existing_timestamp_index_usable_without_expected_ref_steering": False,
        "timestamp_sidecar_or_index_payload_included": False,
        "reason": (
            "expected_record_timestamp_exists_but_current_query_has_no_usable_non_oracle_date_filter"
            if timestamp_available and not date_filter
            else "date_filter_signal_exists_but_prior_runtime_anchor_timestamp_window_did_not_improve_target_cases"
        ),
    }


def _mechanism_labels(
    scope: dict[str, Any],
    depth: dict[str, Any],
    query_labels: dict[str, Any],
    timestamp_diag: dict[str, Any],
) -> list[str]:
    labels: list[str] = []
    if scope["selected"] and scope["queued"] and not scope["opened"]:
        labels.append("source_not_opened")
    if scope["opened"] and not scope["line_reached"]:
        labels.append("opened_line_not_reached")
    if not query_labels["date_window_filter_usable"]:
        labels.append("date_handle_missing_or_not_filter_usable")
    if timestamp_diag["expected_record_timestamp_available"]:
        labels.append("timestamped_record_not_enough_without_non_oracle_date_handle")
    if depth["additional_records_needed_bucket"] != "unknown":
        labels.append("within_file_depth_miss")
    if not labels:
        labels.append("not_enough_evidence")
    return labels


def _summary(rows: list[dict[str, Any]], benchmark: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_count": len(rows),
        "benchmark_package_search_hits": _int(((benchmark.get("summary") or {}).get("package_search") or {}).get("first_pass_hits")),
        "benchmark_package_search_cases": _int(((benchmark.get("summary") or {}).get("package_search") or {}).get("cases")),
        "opened_line_not_reached_count": sum(
            1 for row in rows if "opened_line_not_reached" in row["mechanism_classifications"]
        ),
        "source_not_opened_count": sum(1 for row in rows if "source_not_opened" in row["mechanism_classifications"]),
        "within_file_depth_miss_count": sum(1 for row in rows if "within_file_depth_miss" in row["mechanism_classifications"]),
        "query_date_window_present_count": sum(1 for row in rows if row["query_inference_labels"]["date_window_present"]),
        "query_date_window_filter_usable_count": sum(1 for row in rows if row["query_inference_labels"]["date_window_filter_usable"]),
        "expected_record_timestamp_available_count": sum(
            1 for row in rows if row["timestamp_seek_diagnostic"]["expected_record_timestamp_available"]
        ),
    }


def _cost_buckets(benchmark: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    package_rows = [row for row in benchmark.get("rows", []) if row.get("baseline") in {"package_search", "package_search_rg_prefilter"}]
    local_bytes = sum(_int(row.get("bytes_scanned")) + _int(row.get("bytes_parsed")) for row in package_rows)
    output_context = sum(_int(row.get("output_context_bytes") or row.get("context_bytes")) for row in package_rows)
    return {
        "local_tool_work": {
            "package_baseline_files_opened": sum(_int(row.get("files_opened")) for row in package_rows),
            "package_baseline_records_parsed": sum(_int(row.get("records_parsed")) for row in package_rows),
            "package_baseline_bytes_scanned_parsed_bucket": _bucket(local_bytes),
            "next_mechanism_risk": "within_file_depth_strategy_may_raise_local_records_or_bytes_if_not_targeted",
        },
        "model_facing_context_input_proxy": {
            "package_baseline_output_context_bytes_bucket": _bucket(output_context),
            "next_mechanism_risk": "deeper_reads_must_not_emit_raw_snippets_or_broad_context_by_default",
        },
        "full_agent_loop_availability": {
            "status": "unavailable",
            "reason": "JSH-763 committed artifact is an offline benchmark report, not a retained full agent-loop trace.",
        },
        "target_rows_cost_bucket_view": [row["cost_bucket_view"] for row in rows],
    }


def _prior_evidence(args: Any) -> list[dict[str, Any]]:
    paths = [
        Path(getattr(args, "jsh711_report", DEFAULT_JSH711_REPORT)).expanduser(),
        Path(getattr(args, "jsh721_report", DEFAULT_JSH721_REPORT)).expanduser(),
        Path(getattr(args, "jsh722_report", DEFAULT_JSH722_REPORT)).expanduser(),
    ]
    reports = []
    for path in paths:
        payload = _read_json(path)
        report = next((value for value in payload.values() if isinstance(value, dict)), {})
        reports.append(_prior_report_summary(path, report))
    return reports


def _prior_report_summary(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    name = path.name
    if "jsh-711" in name:
        return {
            "report_label": name,
            "scoped_learning": "blind_quantum_escalation_and_blind_midpoint_or_positional_line_probe_are_not_supported_without_a_non_oracle_handle",
            "blocks_selected_next_action": False,
            "why_not_blocking": "selected action is design/test of a non-oracle date-timeline within-file-depth strategy, not a blind retry.",
            "behavior_changed_in_final_branch": bool((report.get("decision") or {}).get("product_behavior_changed")),
        }
    if "jsh-721" in name:
        return {
            "report_label": name,
            "scoped_learning": "runtime_anchor_timestamp_window_candidate_stayed_at_16_of_20_and_had_an_auditability_downgrade",
            "blocks_selected_next_action": False,
            "why_not_blocking": "negative evidence is scoped to that timestamp-window attempt, not to every non-oracle within-file-depth design.",
            "promotion_bar_met": bool(report.get("promotion_bar_met")),
        }
    return {
        "report_label": name,
        "scoped_learning": "auditable_timestamp_window_rerun_stayed_at_16_of_20_with_zero_target_gains",
        "blocks_selected_next_action": False,
        "why_not_blocking": "selected action must avoid treating timestamp presence as enough and must test a materially different mechanism.",
        "promotion_bar_met": bool(report.get("promotion_bar_met")),
    }


def _selected_next_action(rows: list[dict[str, Any]]) -> dict[str, Any]:
    has_depth_miss = any("within_file_depth_miss" in row["mechanism_classifications"] for row in rows)
    has_date_misalignment = any(
        not row["query_inference_labels"]["date_window_filter_usable"] for row in rows
    )
    action = (
        "design_and_test_non_oracle_date_timeline_within_file_depth_strategy"
        if has_depth_miss or has_date_misalignment
        else "stop_no_action_until_more_evidence"
    )
    return {
        "action": action,
        "rationale": (
            "The four remaining package-search misses are selected/queued scope hits, with three opened files stopping before the target depth and one selected/queued file not opened; current query inference does not expose a usable date filter for these rows, so a next task must test a non-oracle within-file-depth strategy rather than raw-rg or timestamp-window repetition."
        ),
        "not_selected": [
            "raw_rg_live_gate",
            "fresh_expensive_benchmark_replay",
            "timestamp_window_retry_without_new_mechanism",
            "blind_quantum_escalation",
            "expected_ref_steering",
            "default_promotion",
        ],
    }


def _privacy_contract() -> dict[str, bool]:
    return {
        "raw_query_text_included": False,
        "raw_private_text_included": False,
        "raw_snippets_included": False,
        "raw_private_paths_included": False,
        "exact_line_numbers_included": False,
        "raw_source_refs_included": False,
        "raw_session_ids_included": False,
        "local_usernames_included": False,
        "vectors_or_indexes_included": False,
        "private_jsonl_paths_included": False,
    }


def _read_real_history_report(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    if "real_history_impact_benchmark" in payload and isinstance(payload["real_history_impact_benchmark"], dict):
        return payload["real_history_impact_benchmark"]
    if "rows" in payload and isinstance(payload["rows"], list):
        return payload
    raise ValueError(f"{path} does not contain a real-history benchmark report")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _benchmark_row(report: dict[str, Any], case_id: str) -> dict[str, Any]:
    for row in report.get("rows", []):
        if row.get("case_id") == case_id and row.get("baseline") == "package_search":
            return row
    raise ValueError(f"missing package_search benchmark row for {case_id}")


def _raw_row(raw_run: dict[str, Any], case_id: str) -> dict[str, Any]:
    for row in raw_run.get("rows", []):
        if row.get("case_id") == case_id and row.get("baseline") == "package_search":
            return row
    return {}


def _first_expected_ref_diag(row: dict[str, Any]) -> dict[str, Any]:
    refs = ((row.get("expected_ref_diagnostics") or {}).get("refs") or [])
    return refs[0] if refs and isinstance(refs[0], dict) else {}


def _first_raw_expected_ref(row: dict[str, Any]) -> dict[str, Any]:
    refs = row.get("expected_refs") or []
    return refs[0] if refs and isinstance(refs[0], dict) else {}


def _expected_record_has_timestamp(raw_ref: dict[str, Any]) -> bool:
    path_value = _safe_string(raw_ref.get("path"))
    line_number = _int(raw_ref.get("line"))
    if not path_value or not line_number:
        return False
    path = Path(path_value).expanduser()
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle, start=1):
                if index == line_number:
                    record = json.loads(line)
                    return isinstance(record, dict) and bool(record.get("timestamp"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return False


def _resolve_optional_path(path: Path) -> Path | None:
    if path.exists():
        return path
    if not path.is_absolute():
        parent_private = Path("..") / ".." / path
        if parent_private.exists():
            return parent_private
    return None


def _bucket(value: int) -> str:
    if value <= 0:
        return "0"
    ranges = (
        (1, 128),
        (129, 256),
        (257, 512),
        (513, 1024),
        (1025, 2048),
        (2049, 4096),
        (4097, 8192),
        (8193, 16384),
        (16385, 32768),
        (32769, 65536),
        (65537, 131072),
        (131073, 262144),
        (262145, 524288),
        (524289, 1048576),
    )
    for low, high in ranges:
        if low <= value <= high:
            return f"{low}-{high}"
    return ">1048576"


def _safe_string(value: Any) -> str:
    return str(value) if value is not None else ""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _esc(value: str) -> str:
    return str(value).replace("|", "\\|")
