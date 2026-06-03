from __future__ import annotations

import json
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .conversation_records import extract_human_ask
from .embedding_experiment import (
    FASTEMBED_MODEL_ID,
    DocumentPlan,
    ExperimentDocument,
    ExperimentProbe,
    MemoryProfiler,
    _aggregate_rows,
    _build_index,
    _content_fingerprint,
    _elapsed_ms,
    _is_ignored_private_path,
    _onnx_profile_status,
    _privacy_contract,
    _private_output_dir,
    _run_id,
    _run_queries,
    _select_backend,
)
from .metric_contract import add_metric_contract, real_history_embedding_metrics
from .real_history_bridge import _read_one_based_ref, load_json, validate_bridge
from .timeutil import parse_now, timezone_from_args


DEFAULT_MANIFEST = Path("benchmarks/real_history_gold/sanitized_cases.json")
DEFAULT_PRIVATE_BRIDGE = Path(".private/jsh-049/bridge.json")


@dataclass(frozen=True)
class BridgeEmbeddingCase:
    case_id: str
    family: str
    expected_ref_id: str
    query_text: str
    document_text: str
    difficulty: str


def build_real_history_embedding_payload(args) -> dict[str, Any]:
    started = time.perf_counter()
    profiler = MemoryProfiler(enabled=bool(getattr(args, "profile_memory", False)), started=started)
    profiler.mark("experiment", "start")
    tz = timezone_from_args(args.timezone, args.timezone_offset)
    now = parse_now(args.now, tz)
    output_dir = _private_output_dir(args.output_dir, now)
    run_id = _run_id(now)
    report = _base_report(args, run_id=run_id, started=started)

    if not _is_ignored_private_path(output_dir):
        return {"real_history_embedding_benchmark": _finish_without_scan(report, "private_output_rejected", ["private_output_dir_must_be_under_dot_private"], started)}

    manifest_path = Path(args.manifest).expanduser()
    bridge_path = Path(args.private_bridge).expanduser()
    try:
        manifest = load_json(manifest_path)
        bridge = load_json(bridge_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {"real_history_embedding_benchmark": _finish_without_scan(report, "invalid_fixture", ["manifest_or_bridge_unreadable"], started)}

    bridge_validation = validate_bridge(manifest, bridge)
    bridge_report = bridge_validation.report.get("real_history_benchmark_bridge", {})
    report["bridge_validation"] = _safe_bridge_validation(bridge_report)
    if bridge_validation.errors:
        return {"real_history_embedding_benchmark": _finish_without_scan(report, "invalid_bridge", ["private_bridge_validation_failed"], started)}

    cases, case_errors = _load_embedding_cases(manifest, bridge, selector=args.case_selector, case_limit=args.case_limit)
    if case_errors:
        return {"real_history_embedding_benchmark": _finish_without_scan(report, "invalid_fixture", case_errors, started)}
    if not cases:
        return {"real_history_embedding_benchmark": _finish_without_scan(report, "no_cases", ["no_embedding_recall_cases_selected"], started)}

    documents = [
        ExperimentDocument(
            doc_id=case.expected_ref_id,
            text=case.document_text,
            timestamp=None,
            role="user",
            cwd=None,
            root_label="private_bridge_ref",
        )
        for case in cases
    ]
    probes = [
        ExperimentProbe(
            query_text=case.query_text,
            case_id=case.case_id,
            case_family=case.family,
            query_source="reviewed_private_bridge_case",
            gold_doc_id=case.expected_ref_id,
        )
        for case in cases
    ]
    source_metrics = {
        "status": "ok",
        "data_source": "private_real_history_bridge_refs",
        "selected_root_count": 0,
        "candidate_file_count": len(cases),
        "candidate_bytes": sum(len(case.document_text.encode("utf-8", errors="replace")) for case in cases),
        "record_count": len(cases),
        "embedding_document_count": len(documents),
        "chunk_count": 0,
        "skipped_records": 0,
        "records_cap_hit": False,
        "cap_hit_labels": [],
        "warnings": [],
        "baseline_mode": args.baseline_mode,
        "baseline_documents_materialized": args.baseline_mode == "correctness",
        "correctness_comparison_available": args.baseline_mode == "correctness",
        "document_enumeration_mode": "private_bridge_materialized_gold_refs",
        "documents_retained_for_index_build": True,
    }
    report["source_metrics"] = source_metrics
    report["case_selection"] = {
        "selector": args.case_selector,
        "selected_case_count": len(cases),
        "expected_embedding_recall_case_count": _embedding_case_count(manifest),
        "below_floor_case_count_caveat": "9 embedding-applicable reviewed cases is below the old 20-50 target floor.",
        "families": _family_counts(cases),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    profiler.mark("backend_select", "start", backend_requested=args.backend)
    backend_state = _select_backend(args=args, output_dir=output_dir, profiler=profiler)
    profiler.mark("backend_select", "end", backend_selected=backend_state["public"]["selected"])
    onnx_profile = _onnx_profile_status(args=args, backend_state=backend_state)

    document_plan = DocumentPlan(
        content_fingerprint=_content_fingerprint(documents),
        document_count=len(documents),
        source_record_count=len(cases),
        root_labels=("private_bridge_ref",),
        expected_doc_ids=tuple(doc.doc_id for doc in documents),
        source_metrics=source_metrics,
    )
    build_started = time.perf_counter()
    tracemalloc.start()
    profiler.mark("index_build", "start", document_count=len(documents))
    index, index_private_payload = _build_index(
        iter(documents),
        document_plan=document_plan,
        backend_state=backend_state,
        output_dir=output_dir,
        args=args,
        profiler=profiler,
    )
    _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    profiler.mark("index_build", "end", document_count=len(documents), tracemalloc_peak_bytes=int(peak_bytes))
    tracemalloc.stop()
    index_build_ms = _elapsed_ms(build_started)

    profiler.mark("query_scan", "start", query_case_count=len(probes))
    query_rows, query_latencies = _run_queries(
        probes=probes,
        documents=documents,
        index=index,
        top_k=args.top_k,
        baseline_mode=args.baseline_mode,
        profiler=profiler,
    )
    profiler.mark("query_scan", "end", query_case_count=len(probes))
    refresh_metrics = index_private_payload["refresh_metrics"]
    aggregate = _aggregate_rows(
        rows=query_rows,
        started=started,
        source_metrics=source_metrics,
        index_build_ms=index_build_ms,
        peak_memory_bytes=int(peak_bytes),
        index_size_bytes=int(index_private_payload["storage_metrics"].get("total_bytes", 0)),
        query_latencies=query_latencies,
        cap_labels=[],
        refresh_metrics=refresh_metrics,
    )
    report.update(
        {
            "status": aggregate["overall_status"],
            "aggregate": aggregate,
            "backend": backend_state["public"],
            "index_metrics": aggregate["index_metrics"],
            "query_metrics": aggregate["query_metrics"],
            "refresh_metrics": refresh_metrics["public"],
            "storage_metrics": index_private_payload["storage_metrics"],
            "batching": index_private_payload["batching"],
            "chunking": index_private_payload["chunking"],
            "baseline_comparison": _baseline_comparison_payload(args.baseline_mode),
            "streaming_enumeration": {
                "enabled": False,
                "mode": "private_bridge_materialized_gold_refs",
                "documents_retained_for_index_build": True,
                "baseline_documents_materialized": args.baseline_mode == "correctness",
                "scope": "reviewed_private_bridge_refs_only",
            },
            "rows": query_rows,
            "memory_profile": profiler.public(python_peak_bytes=int(peak_bytes)),
            "onnx_profile": onnx_profile,
            "embedding_recall_gate": _real_history_embedding_gate(query_rows, aggregate),
            "privacy_contract": _privacy_contract(),
            "private_artifacts": {
                "scope": "ignored_private_output",
                "raw_text_written": False,
                "binary_vector_shards_written": True,
                "private_vectors_committed": False,
                "private_indexes_committed": False,
                "vector_bytes": index_private_payload["storage_metrics"].get("vector_bytes", 0),
                "total_bytes": index_private_payload["storage_metrics"].get("total_bytes", 0),
            },
        }
    )
    return {"real_history_embedding_benchmark": _add_metric_sections(report)}


def _load_embedding_cases(manifest: dict[str, Any], bridge: dict[str, Any], *, selector: str, case_limit: int) -> tuple[list[BridgeEmbeddingCase], list[str]]:
    manifest_cases = [case for case in manifest.get("cases", []) if isinstance(case, dict)]
    if selector == "embedding_recall":
        manifest_cases = [
            case
            for case in manifest_cases
            if (case.get("downstream_applicability") or {}).get("embedding_recall") is True
        ]
    if case_limit > 0:
        manifest_cases = manifest_cases[:case_limit]
    bridge_by_case = {
        case.get("case_id"): case
        for case in bridge.get("cases", [])
        if isinstance(case, dict) and isinstance(case.get("case_id"), str)
    }
    cases: list[BridgeEmbeddingCase] = []
    errors: list[str] = []
    for manifest_case in manifest_cases:
        case_id = str(manifest_case.get("case_id"))
        bridge_case = bridge_by_case.get(case_id)
        if not bridge_case:
            errors.append(f"{case_id}: missing_bridge_case")
            continue
        refs = [ref for ref in bridge_case.get("refs", []) if isinstance(ref, dict)]
        must_ref_ids = [
            ref.get("ref_id")
            for ref in manifest_case.get("must_find_refs", [])
            if isinstance(ref, dict) and isinstance(ref.get("ref_id"), str)
        ]
        expected_ref_id = str(must_ref_ids[0]) if must_ref_ids else ""
        ref = next((item for item in refs if item.get("ref_id") == expected_ref_id), refs[0] if refs else None)
        if not ref:
            errors.append(f"{case_id}: missing_bridge_ref")
            continue
        try:
            raw_record = _read_one_based_ref(ref)
        except (OSError, json.JSONDecodeError):
            errors.append(f"{case_id}: unreadable_bridge_ref")
            continue
        extraction = extract_human_ask(raw_record)
        if extraction.ask_text is None:
            errors.append(f"{case_id}: missing_human_ask_text")
            continue
        query_text = ref.get("query_text", bridge_case.get("query_text"))
        if not isinstance(query_text, str) or not query_text:
            query_text = extraction.ask_text
        cases.append(
            BridgeEmbeddingCase(
                case_id=case_id,
                family=str(manifest_case.get("family") or "unknown"),
                expected_ref_id=str(ref.get("ref_id") or expected_ref_id),
                query_text=query_text,
                document_text=extraction.ask_text,
                difficulty=str(manifest_case.get("difficulty") or "unknown"),
            )
        )
    return cases, errors


def _real_history_embedding_gate(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    metrics = aggregate["query_metrics"]
    if not metrics.get("baseline_correctness_available", True):
        return _gate("inconclusive", ["baseline_comparison_skipped_resource_measurement_only"], metrics)
    if not metrics.get("gold_case_count"):
        return _gate("not_evaluated", ["no_gold_cases_available"], metrics)
    return _gate("inconclusive", ["real_history_correctness_harness_only", "resource_scale_evidence_not_included"], metrics)


def _gate(status: str, reasons: list[str], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "reasons": sorted(set(reasons)),
        "metrics": {
            "case_count": metrics.get("case_count", 0),
            "gold_case_count": metrics.get("gold_case_count", 0),
            "embedding_first_pass_hits": metrics.get("embedding_first_pass_hits", 0),
            "baseline_first_pass_hits": metrics.get("baseline_first_pass_hits", 0),
            "embedding_mean_recall_at_3": metrics.get("embedding_mean_recall_at_3"),
            "baseline_mean_recall_at_3": metrics.get("baseline_mean_recall_at_3"),
            "protected_regressions": metrics.get("protected_regressions", 0),
        },
    }


def _base_report(args, *, run_id: str, started: float) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "pending",
        "data_source": "private_real_history_bridge_refs",
        "created_elapsed_ms": _elapsed_ms(started),
        "default_live_search_changed": False,
        "production_status": "experimental_unpromoted_not_default",
        "limits": {
            "case_limit": args.case_limit,
            "top_k": args.top_k,
            "backend": args.backend,
            "baseline_mode": args.baseline_mode,
            "fastembed_batch_size": args.fastembed_batch_size,
            "shard_size": args.shard_size,
            "max_index_disk": args.max_index_disk,
        },
        "scope": {
            "manifest_label": "real_history_gold_manifest",
            "private_bridge_label": "ignored_private_real_history_bridge",
            "selected_cases": args.case_selector,
            "not_searched_scope": [
                "Only reviewed manifest cases selected by case_selector were considered.",
                "Raw local paths, session IDs, query text, source snippets, and vectors are omitted from committed-safe output.",
            ],
        },
    }


def _finish_without_scan(report: dict[str, Any], status: str, warnings: list[str], started: float) -> dict[str, Any]:
    report["status"] = status
    report["aggregate"] = {
        "overall_status": status,
        "total_elapsed_ms": _elapsed_ms(started),
        "caps_hit": False,
        "cap_hit_labels": [],
        "failure_modes": warnings,
        "query_metrics": {
            "case_count": 0,
            "gold_case_count": 0,
            "baseline_correctness_available": False,
            "candidate_count_total": 0,
            "baseline_candidate_count_total": 0,
            "embedding_first_pass_hits": 0,
            "baseline_first_pass_hits": 0,
            "embedding_mean_recall_at_3": None,
            "baseline_mean_recall_at_3": None,
            "protected_regressions": 0,
        },
    }
    report["rows"] = []
    report["warnings"] = warnings
    report["embedding_recall_gate"] = {"status": "not_evaluated", "reasons": [f"benchmark_status_{status}"]}
    report["privacy_contract"] = _privacy_contract()
    return _add_metric_sections(report)


def _baseline_comparison_payload(baseline_mode: str) -> dict[str, Any]:
    if baseline_mode == "skip":
        return {
            "mode": "skip",
            "status": "skipped_for_resource_measurement",
            "baseline_documents_materialized": False,
            "correctness_comparison_available": False,
            "gate_claim_allowed": False,
            "reason": "explicit_baseline_mode_skip",
        }
    return {
        "mode": "correctness",
        "status": "deterministic_baseline_materialized",
        "baseline_documents_materialized": True,
        "correctness_comparison_available": True,
        "gate_claim_allowed": True,
        "reason": "default_correctness_mode",
    }


def _safe_bridge_validation(payload: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "schema_version",
        "status",
        "manifest_case_count",
        "benchmarkable_reviewed_case_count",
        "expected_case_count",
        "bridge_case_count",
        "accepted_case_count",
        "quarantined_case_count",
        "rejected_case_count",
        "resolved_ref_count",
        "source_structure_counts",
        "classifier_confidence_counts",
        "query_text_check_counts",
        "rejection_counts",
        "error_count",
        "below_floor_case_count_caveat",
        "privacy_contract",
    }
    return {key: payload.get(key) for key in sorted(keys) if key in payload}


def _embedding_case_count(manifest: dict[str, Any]) -> int:
    return sum(
        1
        for case in manifest.get("cases", [])
        if isinstance(case, dict) and (case.get("downstream_applicability") or {}).get("embedding_recall") is True
    )


def _family_counts(cases: list[BridgeEmbeddingCase]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.family] = counts.get(case.family, 0) + 1
    return dict(sorted(counts.items()))


def _add_metric_sections(report: dict[str, Any]) -> dict[str, Any]:
    return add_metric_contract(report, real_history_embedding_metrics(report))
