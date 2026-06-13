from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPORT_KEY = "real_history_benchmark_drift_comparator"
DEFAULT_BEFORE_REPORT = Path("reports/jsh-754-rg-date-timeline-eligibility-tool-delta.json")
DEFAULT_AFTER_REPORT = Path("benchmarks/paired_agent/jsh-761-fresh-real-history-benchmark-after-raw-rg-route-stop.json")
DEFAULT_OUTPUT_PATH = Path("benchmarks/paired_agent/jsh-762-real-history-benchmark-drift-comparator.json")
DEFAULT_MARKDOWN_OUTPUT_PATH = Path("reports/jsh-762-real-history-benchmark-drift-comparator.md")

LOCAL_TOOL_WORK_FIELDS = (
    "bytes_scanned",
    "bytes_parsed",
    "files_opened",
    "records_parsed",
    "tool_calls",
    "subprocess_calls",
)
MODEL_FACING_FIELDS = ("output_context_bytes", "context_bytes", "output_bytes")
ROW_COST_FIELDS = LOCAL_TOOL_WORK_FIELDS + MODEL_FACING_FIELDS + ("wall_time_ms",)
EXPECTED_REF_FIELDS = (
    "expected_file_selected_by_source_plan",
    "expected_file_queued_for_scan",
    "expected_file_opened_before_scan_stop",
    "expected_record_line_reached_before_elapsed_stop",
    "expected_record_present_in_pre_final_candidate_state",
    "expected_record_present_in_final_candidate_refs",
    "expected_record_present_in_final_candidate_groups",
    "line_reach_status",
    "expected_file_records_read_before_stop",
)


def build_real_history_benchmark_drift_payload(args: Any) -> dict[str, Any]:
    before_path = Path(args.before).expanduser()
    after_path = Path(args.after).expanduser()
    before = _read_real_history_report(before_path)
    after = _read_real_history_report(after_path)
    rows = _row_movements(before, after)
    report = {
        "schema_version": 1,
        "status": "ok",
        "task": "jsh-762-real-history-benchmark-drift-comparator",
        "data_source": "committed_safe_real_history_benchmark_json_artifacts",
        "compared_artifacts": {
            "before": _artifact_view(before_path, before),
            "after": _artifact_view(after_path, after),
        },
        "privacy_contract": {
            "raw_query_text_included": False,
            "raw_private_text_included": False,
            "raw_snippets_included": False,
            "raw_paths_included": False,
            "source_lines_included": False,
            "private_upload_performed": False,
            "live_or_model_send_performed": False,
            "fresh_expensive_benchmark_replay_performed": False,
        },
        "claim_boundaries": {
            "token_savings_claimed": False,
            "product_value_claimed": False,
            "jsonl_superiority_claimed": False,
            "lane_winner_claimed": False,
            "strategy_quality_claimed": False,
            "speed_claimed": False,
            "final_answer_correctness_claimed": False,
            "default_promotion_claimed": False,
        },
        "cost_buckets": _cost_bucket_movement(before, after),
        "summary_movement": _summary_movement(before, after),
        "rg_prefilter_movement": _rg_prefilter_movement(before, after),
        "movement_counts": _movement_counts(rows),
        "selected_next_value_producing_action": _selected_next_action(rows),
        "rows": rows,
    }
    return {REPORT_KEY: report}


def render_real_history_benchmark_drift_text(payload: dict[str, Any]) -> str:
    report = payload[REPORT_KEY]
    counts = report["movement_counts"]
    lines = [
        "Real-history benchmark drift comparator",
        f"Status: {report['status']}",
        f"Before: {report['compared_artifacts']['before']['path']} ({report['compared_artifacts']['before'].get('run_id')})",
        f"After: {report['compared_artifacts']['after']['path']} ({report['compared_artifacts']['after'].get('run_id')})",
        f"First-pass hit movements: {counts['first_pass_hit_movement_count']}",
        f"Expected-ref diagnostic movements: {counts['expected_ref_diagnostic_movement_count']}",
        f"Rg-prefilter status/fallback movements: {counts['rg_prefilter_status_or_fallback_movement_count']}",
        f"Next action: {report['selected_next_value_producing_action']['action']}",
    ]
    return "\n".join(lines)


def render_real_history_benchmark_drift_markdown(payload: dict[str, Any]) -> str:
    report = payload[REPORT_KEY]
    before = report["compared_artifacts"]["before"]
    after = report["compared_artifacts"]["after"]
    counts = report["movement_counts"]
    next_action = report["selected_next_value_producing_action"]
    lines = [
        "# JSH-762 Real-History Benchmark Drift Comparator",
        "",
        "## Scope",
        "",
        f"- Before artifact: `{before['path']}` run `{before.get('run_id')}`",
        f"- After artifact: `{after['path']}` run `{after.get('run_id')}`",
        "- No live sends, model sends, private uploads, send-capable `codex exec`, raw-rg live gate, or fresh expensive benchmark replay were performed.",
        "",
        "## Movement Counts",
        "",
        "| Movement | Count |",
        "| --- | ---: |",
        f"| First-pass hit | {counts['first_pass_hit_movement_count']} |",
        f"| Outcome | {counts['outcome_movement_count']} |",
        f"| Cap labels | {counts['cap_label_movement_count']} |",
        f"| Rg-prefilter status/fallback | {counts['rg_prefilter_status_or_fallback_movement_count']} |",
        f"| Expected-ref diagnostics | {counts['expected_ref_diagnostic_movement_count']} |",
        f"| Local/model-facing row costs | {counts['cost_movement_count']} |",
        "",
        "## Baseline Summary Movement",
        "",
        "| Baseline | Before hits | After hits | Hit delta | Before cases | After cases |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["summary_movement"]:
        lines.append(
            f"| `{_esc(row['baseline'])}` | {row.get('before_first_pass_hits')} | {row.get('after_first_pass_hits')} | "
            f"{row.get('first_pass_hits_delta')} | {row.get('before_cases')} | {row.get('after_cases')} |"
        )
    lines.extend(
        [
            "",
            "## Cost Buckets",
            "",
            "| Bucket | Field | Before | After | Delta | Availability |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for bucket_name in ("local_tool_work", "model_facing_context_input_proxy", "full_agent_loop_availability"):
        bucket = report["cost_buckets"][bucket_name]
        if bucket_name == "full_agent_loop_availability":
            lines.append(
                f"| `{bucket_name}` | status |  |  |  | `{_esc(bucket['status'])}`: {_esc(bucket['reason'])} |"
            )
            continue
        for field, movement in bucket["fields"].items():
            lines.append(
                f"| `{bucket_name}` | `{field}` | {movement.get('before')} | {movement.get('after')} | "
                f"{movement.get('delta')} | `{movement.get('availability')}` |"
            )
    lines.extend(
        [
            "",
            "## Rg-Prefilter Movement",
            "",
            "| Status/fallback | Before | After | Delta |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for item in report["rg_prefilter_movement"]["status_fallback_counts"]:
        lines.append(
            f"| `{_esc(item['status_fallback'])}` | {item['before']} | {item['after']} | {item['delta']} |"
        )
    lines.extend(
        [
            "",
            "## Changed Rows",
            "",
            "| Case | Baseline | Hit | Outcome | Caps | Rg | Expected ref | Local/model cost fields |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    changed_rows = [row for row in report["rows"] if row["movement_labels"]]
    for row in changed_rows:
        cost_fields = ", ".join(f"`{_esc(item['field'])}`" for item in row.get("cost_movement", [])) or "(none)"
        lines.append(
            "| "
            f"`{_esc(row['case_id'])}` | "
            f"`{_esc(row['baseline'])}` | "
            f"{_movement_cell(row.get('first_pass_hit_movement'))} | "
            f"{_movement_cell(row.get('outcome_movement'))} | "
            f"{_movement_cell(row.get('cap_label_movement'))} | "
            f"{_movement_cell(row.get('rg_prefilter_movement'))} | "
            f"{_expected_ref_cell(row.get('expected_ref_diagnostic_movement'))} | "
            f"{cost_fields} |"
        )
    lines.extend(
        [
            "",
            "## Selected Next Action",
            "",
            f"- Action: `{next_action['action']}`",
            f"- Rationale: {next_action['rationale']}",
            f"- Not selected: {', '.join(f'`{_esc(item)}`' for item in next_action['not_selected'])}",
            "",
            "## Claim Boundaries",
            "",
        ]
    )
    for key, value in report["claim_boundaries"].items():
        lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines)


def _read_real_history_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "real_history_impact_benchmark" in payload and isinstance(payload["real_history_impact_benchmark"], dict):
        return payload["real_history_impact_benchmark"]
    if "rows" in payload and isinstance(payload["rows"], list):
        return payload
    raise ValueError(f"{path} does not contain a real_history_impact_benchmark report")


def _artifact_view(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "case_count": report.get("case_count") or report.get("manifest_case_count"),
        "row_count": len(report.get("rows") or []),
        "baselines": sorted({str(row.get("baseline")) for row in report.get("rows", []) if row.get("baseline")}),
    }


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("case_id") or ""), str(row.get("baseline") or ""))


def _row_movements(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_rows = {_row_key(row): row for row in before.get("rows", []) if _row_key(row) != ("", "")}
    after_rows = {_row_key(row): row for row in after.get("rows", []) if _row_key(row) != ("", "")}
    rows: list[dict[str, Any]] = []
    for case_id, baseline in sorted(set(before_rows) | set(after_rows)):
        before_row = before_rows.get((case_id, baseline)) or {}
        after_row = after_rows.get((case_id, baseline)) or {}
        movement = {
            "case_id": case_id,
            "baseline": baseline,
            "family": str(after_row.get("family") or before_row.get("family") or "unknown"),
            "row_presence": _movement_value(bool(before_row), bool(after_row)),
            "first_pass_hit_movement": _movement_value(before_row.get("first_pass_hit"), after_row.get("first_pass_hit")),
            "outcome_movement": _movement_value(before_row.get("expected_outcome"), after_row.get("expected_outcome")),
            "cap_label_movement": _movement_value(_sorted_strings(before_row.get("cap_hit_labels")), _sorted_strings(after_row.get("cap_hit_labels"))),
            "rg_prefilter_movement": _rg_prefilter_row_movement(before_row, after_row),
            "expected_ref_diagnostic_movement": _expected_ref_movement(before_row, after_row),
            "cost_movement": _row_cost_movement(before_row, after_row),
        }
        movement["movement_labels"] = _movement_labels(movement)
        rows.append(movement)
    return rows


def _movement_value(before: Any, after: Any) -> dict[str, Any]:
    return {"before": before, "after": after, "changed": before != after}


def _rg_prefilter_row_movement(before_row: dict[str, Any], after_row: dict[str, Any]) -> dict[str, Any]:
    before = _rg_prefilter_view(before_row)
    after = _rg_prefilter_view(after_row)
    return {
        "before": before,
        "after": after,
        "changed": before != after,
        "status_changed": before.get("status") != after.get("status"),
        "fallback_reason_changed": before.get("fallback_reason") != after.get("fallback_reason"),
    }


def _rg_prefilter_view(row: dict[str, Any]) -> dict[str, Any]:
    rg = row.get("rg_prefilter") if isinstance(row.get("rg_prefilter"), dict) else {}
    return {
        "enabled": rg.get("enabled"),
        "attempted": rg.get("attempted"),
        "status": rg.get("status"),
        "fallback_reason": rg.get("fallback_reason"),
    }


def _expected_ref_movement(before_row: dict[str, Any], after_row: dict[str, Any]) -> dict[str, Any]:
    before = _expected_ref_view(before_row)
    after = _expected_ref_view(after_row)
    fields = [
        {"field": field, "before": before.get(field), "after": after.get(field)}
        for field in EXPECTED_REF_FIELDS
        if before.get(field) != after.get(field)
    ]
    return {
        "available": bool(before or after),
        "before": before,
        "after": after,
        "changed": bool(fields),
        "changed_fields": fields,
    }


def _expected_ref_view(row: dict[str, Any]) -> dict[str, Any]:
    diagnostics = row.get("expected_ref_diagnostics") if isinstance(row.get("expected_ref_diagnostics"), dict) else {}
    refs = diagnostics.get("refs") if isinstance(diagnostics.get("refs"), list) else []
    ref = refs[0] if refs and isinstance(refs[0], dict) else {}
    return {field: ref.get(field) for field in EXPECTED_REF_FIELDS if field in ref}


def _row_cost_movement(before_row: dict[str, Any], after_row: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for field in ROW_COST_FIELDS:
        before = before_row.get(field)
        after = after_row.get(field)
        if before == after:
            continue
        rows.append(
            {
                "field": field,
                "before": before,
                "after": after,
                "delta": _numeric_delta(before, after),
                "bucket": "model_facing_context_input_proxy" if field in MODEL_FACING_FIELDS else "local_tool_work",
            }
        )
    return rows


def _movement_labels(row: dict[str, Any]) -> list[str]:
    labels = []
    if row["row_presence"]["changed"]:
        labels.append("row_presence")
    if row["first_pass_hit_movement"]["changed"]:
        labels.append("first_pass_hit")
    if row["outcome_movement"]["changed"]:
        labels.append("outcome")
    if row["cap_label_movement"]["changed"]:
        labels.append("cap_labels")
    if row["rg_prefilter_movement"]["changed"]:
        labels.append("rg_prefilter")
    if row["expected_ref_diagnostic_movement"]["changed"]:
        labels.append("expected_ref_diagnostics")
    if row["cost_movement"]:
        labels.append("cost")
    return labels


def _movement_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "row_count": len(rows),
        "changed_row_count": sum(1 for row in rows if row["movement_labels"]),
        "first_pass_hit_movement_count": sum(1 for row in rows if row["first_pass_hit_movement"]["changed"]),
        "outcome_movement_count": sum(1 for row in rows if row["outcome_movement"]["changed"]),
        "cap_label_movement_count": sum(1 for row in rows if row["cap_label_movement"]["changed"]),
        "rg_prefilter_status_or_fallback_movement_count": sum(
            1
            for row in rows
            if row["rg_prefilter_movement"]["status_changed"] or row["rg_prefilter_movement"]["fallback_reason_changed"]
        ),
        "expected_ref_diagnostic_movement_count": sum(1 for row in rows if row["expected_ref_diagnostic_movement"]["changed"]),
        "cost_movement_count": sum(1 for row in rows if row["cost_movement"]),
    }


def _summary_movement(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    before_summary = before.get("summary") if isinstance(before.get("summary"), dict) else {}
    after_summary = after.get("summary") if isinstance(after.get("summary"), dict) else {}
    rows = []
    for baseline in sorted(set(before_summary) | set(after_summary)):
        before_row = before_summary.get(baseline) or {}
        after_row = after_summary.get(baseline) or {}
        before_hits = before_row.get("first_pass_hits")
        after_hits = after_row.get("first_pass_hits")
        rows.append(
            {
                "baseline": baseline,
                "before_first_pass_hits": before_hits,
                "after_first_pass_hits": after_hits,
                "first_pass_hits_delta": _numeric_delta(before_hits, after_hits),
                "before_cases": before_row.get("cases"),
                "after_cases": after_row.get("cases"),
                "before_hit_rate": before_row.get("first_pass_hit_rate"),
                "after_hit_rate": after_row.get("first_pass_hit_rate"),
            }
        )
    return rows


def _cost_bucket_movement(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "local_tool_work": {"fields": _aggregate_field_movement(before, after, LOCAL_TOOL_WORK_FIELDS)},
        "model_facing_context_input_proxy": {"fields": _aggregate_field_movement(before, after, MODEL_FACING_FIELDS)},
        "full_agent_loop_availability": {
            "status": "unavailable",
            "reason": "compared artifacts are offline first-pass benchmark reports and do not contain full live agent-loop counters",
        },
    }


def _aggregate_field_movement(before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result = {}
    for field in fields:
        before_value = _aggregate_cost_value(before, field)
        after_value = _aggregate_cost_value(after, field)
        result[field] = {
            "before": before_value,
            "after": after_value,
            "delta": _numeric_delta(before_value, after_value),
            "availability": "available" if before_value is not None and after_value is not None else "partially_available",
        }
    return result


def _aggregate_cost_value(report: dict[str, Any], field: str) -> Any:
    aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}
    if field in aggregate:
        return aggregate.get(field)
    performance = report.get("performance_metrics") if isinstance(report.get("performance_metrics"), dict) else {}
    local = performance.get("local_tool_work") if isinstance(performance.get("local_tool_work"), dict) else {}
    handoff = performance.get("agent_model_handoff") if isinstance(performance.get("agent_model_handoff"), dict) else {}
    if field in local:
        return local.get(field)
    if field in handoff:
        return handoff.get(field)
    return None


def _rg_prefilter_movement(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_counts = _rg_prefilter_counts(before)
    after_counts = _rg_prefilter_counts(after)
    return {
        "status_fallback_counts": [
            {
                "status_fallback": key,
                "before": before_counts.get(key, 0),
                "after": after_counts.get(key, 0),
                "delta": after_counts.get(key, 0) - before_counts.get(key, 0),
            }
            for key in sorted(set(before_counts) | set(after_counts))
        ]
    }


def _rg_prefilter_counts(report: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in report.get("rows", []):
        if row.get("baseline") != "package_search_rg_prefilter":
            continue
        rg = row.get("rg_prefilter") if isinstance(row.get("rg_prefilter"), dict) else {}
        status = str(rg.get("status") or "missing")
        fallback = rg.get("fallback_reason")
        key = f"{status}:{fallback}" if fallback else status
        counts[key] = counts.get(key, 0) + 1
    return counts


def _selected_next_action(rows: list[dict[str, Any]]) -> dict[str, Any]:
    opened_not_reached_regressions = [
        row
        for row in rows
        if row["baseline"] == "package_search"
        and row["first_pass_hit_movement"] == {"before": True, "after": False, "changed": True}
        and row["expected_ref_diagnostic_movement"]["after"].get("expected_file_opened_before_scan_stop") is True
        and row["expected_ref_diagnostic_movement"]["after"].get("expected_record_line_reached_before_elapsed_stop") is False
    ]
    if opened_not_reached_regressions:
        case_ids = [row["case_id"] for row in opened_not_reached_regressions]
        return {
            "action": "investigate_package_search_opened_expected_file_record_line_reach_under_caps",
            "action_type": "tool_behavior_or_harness_diagnostic_follow_up",
            "candidate_case_ids": case_ids,
            "rationale": (
                "The comparator found a package_search first-pass regression where the expected file was still selected, "
                "queued, and opened, but the expected record line was no longer reached under caps."
            ),
            "not_selected": [
                "raw_rg_live_gate",
                "jsh_748b_c_d",
                "fresh_expensive_benchmark_replay",
                "default_promotion",
            ],
        }
    return {
        "action": "preserve_drift_comparator_and_reassess_before_tool_behavior_change",
        "action_type": "stop_or_pivot_decision",
        "candidate_case_ids": [],
        "rationale": "No package_search opened-file line-reach regression was detected in the compared artifacts.",
        "not_selected": ["raw_rg_live_gate", "jsh_748b_c_d", "fresh_expensive_benchmark_replay", "default_promotion"],
    }


def _sorted_strings(value: Any) -> list[str]:
    return sorted(str(item) for item in (value or []))


def _numeric_delta(before: Any, after: Any) -> int | float | None:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return after - before
    return None


def _esc(value: Any) -> str:
    return str(value).replace("|", "\\|")


def _movement_cell(movement: dict[str, Any] | None) -> str:
    if not movement or not movement.get("changed"):
        return "(same)"
    return f"`{_esc(movement.get('before'))}` -> `{_esc(movement.get('after'))}`"


def _expected_ref_cell(movement: dict[str, Any] | None) -> str:
    if not movement or not movement.get("changed"):
        return "(same)"
    return ", ".join(
        f"`{_esc(item['field'])}` `{_esc(item.get('before'))}` -> `{_esc(item.get('after'))}`"
        for item in movement.get("changed_fields", [])
    )
