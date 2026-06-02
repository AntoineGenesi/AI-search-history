from __future__ import annotations

import json
import math
from typing import Any


TOKEN_FORMULA = "ceil(bytes / 4)"
TOKEN_METHOD = "approximate_bytes_div_4_ceiling"
TOKEN_WARNING = (
    "Approximate token estimates use ceil(bytes / 4) with no tokenizer. Parsed/scanned bytes are tool-side "
    "local-work proxies, not direct model token savings; model-token impact is represented by agent/model "
    "handoff bytes and their approximate token estimates."
)
MEMORY_THRESHOLDS_BYTES = (256 * 1024 * 1024, 512 * 1024 * 1024, 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024)


def approximate_tokens_from_bytes(byte_count: Any) -> int:
    try:
        value = int(byte_count or 0)
    except (TypeError, ValueError):
        value = 0
    return int(math.ceil(max(0, value) / 4))


def metric_contract_section() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "vocabulary": {
            "timing": "Wall-clock and phase durations measured in milliseconds.",
            "local_tool_work": "Files, records, bytes scanned/parsed, tool calls, subprocess calls, and candidates handled by JSONL locally.",
            "agent_model_handoff": "Bytes and approximate tokens emitted to the agent/model as report rows, snippets, candidates, or context.",
            "memory": "RSS and duration fields when sampled; otherwise explicit not_measured status.",
            "correctness": "Gate status, source-ref equivalence, no-evidence scope equivalence, recall, MRR, and regression counters.",
            "tradeoffs": "Human-readable context required before turning speed, memory, or byte numbers into claims.",
        },
        "byte_to_token_estimation": _token_metadata(),
        "parsed_byte_warning": TOKEN_WARNING,
    }


def build_performance_metrics(
    *,
    workload: str,
    wall_time_ms: Any,
    phase_durations_ms: dict[str, Any] | None = None,
    local_work: dict[str, Any] | None = None,
    handoff: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
    correctness: dict[str, Any] | None = None,
    tradeoffs: list[str] | None = None,
) -> dict[str, Any]:
    local = _with_byte_token_estimates(local_work or {}, local_byte_keys())
    model_handoff = _with_byte_token_estimates(handoff or {}, handoff_byte_keys())
    return {
        "schema_version": 1,
        "workload": workload,
        "timing": {
            "wall_time_ms": _int_or_zero(wall_time_ms),
            "phase_durations_ms": {key: _int_or_zero(value) for key, value in (phase_durations_ms or {}).items()},
        },
        "local_tool_work": local,
        "agent_model_handoff": model_handoff,
        "memory": memory or not_measured_memory("rss_not_sampled_for_this_command"),
        "correctness": correctness or {},
        "tradeoffs": tradeoffs or [],
        "byte_to_token_estimation": _token_metadata(),
    }


def add_metric_contract(report: dict[str, Any], performance_metrics: dict[str, Any]) -> dict[str, Any]:
    report["metric_contract"] = metric_contract_section()
    report["performance_metrics"] = performance_metrics
    return report


def not_measured_memory(reason: str) -> dict[str, Any]:
    return {
        "status": "not_measured",
        "reason": reason,
        "peak_rss_bytes": None,
        "process_max_rss_bytes": None,
        "sample_count": 0,
        "observed_duration_ms": None,
        "rss_duration_above_thresholds_ms": {},
    }


def memory_from_profile(memory_profile: dict[str, Any] | None) -> dict[str, Any]:
    profile = memory_profile or {}
    if not profile.get("enabled"):
        return not_measured_memory("memory_profile_not_enabled")
    samples = [sample for sample in profile.get("samples", []) if isinstance(sample, dict)]
    if not samples:
        return {
            **not_measured_memory("memory_profile_enabled_but_no_samples"),
            "status": "not_measured",
            "sample_count": 0,
        }
    durations = _rss_durations(samples)
    elapsed_values = [_int_or_zero(sample.get("elapsed_ms")) for sample in samples]
    return {
        "status": "measured",
        "sample_count": len(samples),
        "peak_rss_bytes": _int_or_zero(profile.get("peak_observed_rss_bytes")),
        "process_max_rss_bytes": _int_or_zero(profile.get("process_max_rss_bytes")),
        "tracemalloc_python_peak_bytes": profile.get("tracemalloc_python_peak_bytes"),
        "observed_duration_ms": max(elapsed_values) - min(elapsed_values),
        "rss_duration_above_thresholds_ms": durations,
        "rss_duration_method": "lower_bound_sum_of_intervals_between_existing_samples",
        "thresholds_bytes": list(MEMORY_THRESHOLDS_BYTES),
        "native_memory_note": profile.get("native_memory_note"),
    }


def measured_benchmark_metrics(report: dict[str, Any]) -> dict[str, Any]:
    aggregate = report.get("aggregate") or {}
    manifest = aggregate.get("manifest_shortlist") or {}
    timestamp_seek = aggregate.get("timestamp_seek_prototype") or {}
    phase_durations = {
        "total": aggregate.get("total_elapsed_ms", 0),
        "manifest_build": manifest.get("build_elapsed_ms", 0),
        "manifest_refresh": manifest.get("refresh_elapsed_ms", 0),
        "manifest_planned_scan": manifest.get("elapsed_ms", 0),
        "timestamp_seek_query": timestamp_seek.get("elapsed_ms_median", 0),
        "timestamp_seek_build": timestamp_seek.get("build_elapsed_ms", 0),
        "timestamp_seek_no_change_refresh": timestamp_seek.get("no_change_refresh_elapsed_ms", 0),
        "timestamp_seek_append_refresh": timestamp_seek.get("append_refresh_elapsed_ms", 0),
    }
    return build_performance_metrics(
        workload="benchmark_measured",
        wall_time_ms=aggregate.get("wall_time_ms", aggregate.get("total_elapsed_ms", 0)),
        phase_durations_ms=phase_durations,
        local_work={
            "files_opened": aggregate.get("files_opened", 0),
            "python_opened_files": aggregate.get("python_opened_files", 0),
            "records_parsed": None,
            "bytes_scanned": aggregate.get("bytes_scanned", aggregate.get("searched_bytes", 0)),
            "bytes_parsed": aggregate.get("bytes_parsed", 0),
            "tool_calls": aggregate.get("tool_calls", 0),
            "subprocess_calls": aggregate.get("subprocess_calls", 0),
            "candidate_count": aggregate.get("candidate_count", 0),
        },
        handoff={
            "output_bytes": aggregate.get("output_bytes", 0),
            "context_bytes": aggregate.get("context_bytes", 0),
            "output_context_bytes": aggregate.get("output_context_bytes", 0),
        },
        memory=not_measured_memory("rss_not_measured_by_benchmark_measured"),
        correctness={
            "ranking_quality_gate_status": (report.get("ranking_quality_gate") or {}).get("status"),
            "rg_prefilter_gate_status": (report.get("rg_prefilter_gate") or {}).get("status"),
            "manifest_shortlist_gate_status": (report.get("manifest_shortlist_gate") or {}).get("status"),
            "timestamp_sidecar_gate_status": (report.get("timestamp_sidecar_gate") or {}).get("status"),
            "source_ref_equivalence": manifest.get("source_ref_equivalence") or timestamp_seek.get("source_ref_equivalence"),
            "no_evidence_scope_equivalence": manifest.get("no_evidence_scope_equivalence"),
            "required_counters": aggregate.get("required_counters"),
        },
        tradeoffs=[
            "Parsed bytes are tool-side work proxies for local JSONL parsing, not direct LLM token savings.",
            "Agent/model handoff bytes and approximate handoff tokens are the token-impact proxy for benchmark reports.",
            "Promotion still requires correctness/source-ref equivalence, no-evidence scope context, caps, and gate status.",
        ],
    )


def impact_benchmark_metrics(report: dict[str, Any]) -> dict[str, Any]:
    rows = report.get("rows") or []
    summary = report.get("summary") or {}
    return build_performance_metrics(
        workload="benchmark_impact",
        wall_time_ms=report.get("total_wall_time_ms", 0),
        phase_durations_ms={
            f"{name}_median_wall_time": row.get("median_wall_time_ms", 0)
            for name, row in summary.items()
            if isinstance(row, dict)
        },
        local_work={
            "files_opened": sum(_int_or_zero(row.get("files_opened")) for row in rows),
            "records_parsed": None,
            "bytes_scanned": sum(_int_or_zero(row.get("bytes_scanned")) for row in rows),
            "bytes_parsed": sum(_int_or_zero(row.get("bytes_parsed")) for row in rows),
            "tool_calls": sum(_int_or_zero(row.get("tool_calls")) for row in rows),
            "subprocess_calls": sum(_int_or_zero(row.get("subprocess_calls")) for row in rows),
            "candidate_count": sum(_int_or_zero(row.get("candidate_count")) for row in rows),
        },
        handoff={
            "output_bytes": sum(_int_or_zero(row.get("output_bytes")) for row in rows),
            "context_bytes": sum(_int_or_zero(row.get("context_bytes")) for row in rows),
            "output_context_bytes": sum(_int_or_zero(row.get("output_context_bytes")) for row in rows),
        },
        memory=not_measured_memory("rss_not_measured_by_benchmark_impact"),
        correctness={
            "ranking_quality_gate_status": (report.get("ranking_quality_gate") or {}).get("status"),
            "rg_prefilter_gate_status": (report.get("rg_prefilter_gate") or {}).get("status"),
            "required_counters": {
                "ranking_quality_gate": (report.get("ranking_quality_gate") or {}).get("required_counters"),
                "rg_prefilter_gate": (report.get("rg_prefilter_gate") or {}).get("required_counters"),
            },
            "source_ref_equivalence": (
                (summary.get("package_search_rg_prefilter") or {}).get("equivalence_vs_package_search")
            ),
        },
        tradeoffs=[
            "No-extension and package rows compare local work and handoff size; parsed-byte reductions are not model-token savings.",
            "Synthetic-only impact evidence proves mechanics but cannot promote ranking or rg gates without legitimate correctness evidence.",
            "source-ref equivalence and no-evidence correctness must be preserved before speed or byte reductions count as improvements.",
        ],
    )


def embedding_experiment_metrics(report: dict[str, Any]) -> dict[str, Any]:
    aggregate = report.get("aggregate") or {}
    source_metrics = report.get("source_metrics") or aggregate.get("source_metrics") or {}
    index_metrics = report.get("index_metrics") or aggregate.get("index_metrics") or {}
    query_metrics = report.get("query_metrics") or aggregate.get("query_metrics") or {}
    rows = report.get("rows") or []
    output_context_bytes = sum(len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")) for row in rows)
    return build_performance_metrics(
        workload="embedding_experiment",
        wall_time_ms=aggregate.get("total_elapsed_ms", 0),
        phase_durations_ms={
            "index_build": index_metrics.get("index_build_time_ms", 0),
            "refresh_no_change": index_metrics.get("refresh_no_change_cost_ms", 0),
            "median_query": query_metrics.get("median_query_latency_ms", 0),
            "p90_query": query_metrics.get("p90_query_latency_ms", 0),
        },
        local_work={
            "files_opened": source_metrics.get("candidate_file_count", 0),
            "records_parsed": source_metrics.get("record_count", 0),
            "bytes_scanned": source_metrics.get("candidate_bytes", 0),
            "bytes_parsed": source_metrics.get("candidate_bytes", 0),
            "tool_calls": query_metrics.get("case_count", 0),
            "subprocess_calls": 0,
            "candidate_count": query_metrics.get("candidate_count_total", 0),
            "embedding_document_count": source_metrics.get("embedding_document_count", 0),
        },
        handoff={
            "output_bytes": output_context_bytes,
            "context_bytes": output_context_bytes,
            "output_context_bytes": output_context_bytes,
        },
        memory=memory_from_profile(report.get("memory_profile")),
        correctness={
            "embedding_recall_gate_status": (report.get("embedding_recall_gate") or {}).get("status"),
            "gold_case_count": query_metrics.get("gold_case_count", 0),
            "embedding_first_pass_hits": query_metrics.get("embedding_first_pass_hits", 0),
            "baseline_first_pass_hits": query_metrics.get("baseline_first_pass_hits", 0),
            "protected_regressions": query_metrics.get("protected_regressions", 0),
            "source_ref_recovery": (report.get("comparison") or {}).get("source_ref_recovery"),
        },
        tradeoffs=[
            "Embedding handoff bytes are sanitized report-row bytes; vector/index bytes are local storage and not model-token savings.",
            "RSS peaks must be interpreted with observed duration and sample coverage, not as a standalone memory claim.",
            "Embedding promotion requires recall/correctness gains without exact/path/slug/command/date/no-evidence regressions.",
        ],
    )


def _with_byte_token_estimates(values: dict[str, Any], byte_keys: tuple[str, ...]) -> dict[str, Any]:
    output = dict(values)
    token_estimates: dict[str, int] = {}
    for key in byte_keys:
        if key in output and output.get(key) is not None:
            token_estimates[f"{key}_approx_tokens"] = approximate_tokens_from_bytes(output.get(key))
    output["approximate_token_estimates"] = token_estimates
    output["token_estimation_warning"] = TOKEN_WARNING
    return output


def _token_metadata() -> dict[str, str]:
    return {
        "method": TOKEN_METHOD,
        "formula": TOKEN_FORMULA,
        "tokenizer": "none",
        "status": "approximate",
        "warning": TOKEN_WARNING,
    }


def _rss_durations(samples: list[dict[str, Any]]) -> dict[str, int]:
    durations = {str(threshold): 0 for threshold in MEMORY_THRESHOLDS_BYTES}
    ordered = sorted(samples, key=lambda sample: _int_or_zero(sample.get("elapsed_ms")))
    for current, next_sample in zip(ordered, ordered[1:]):
        delta = max(0, _int_or_zero(next_sample.get("elapsed_ms")) - _int_or_zero(current.get("elapsed_ms")))
        rss = _int_or_zero(current.get("rss_bytes"))
        for threshold in MEMORY_THRESHOLDS_BYTES:
            if rss >= threshold:
                durations[str(threshold)] += delta
    return durations


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def local_byte_keys() -> tuple[str, ...]:
    return ("bytes_scanned", "bytes_parsed")


def handoff_byte_keys() -> tuple[str, ...]:
    return ("output_bytes", "context_bytes", "output_context_bytes")
